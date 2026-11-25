"""Scheduled job definitions and their schedule, shared by the standalone worker and the in-process scheduler.

Schedule (Africa/Nairobi):
  every QUOTE_POLL_MINUTES, Mon-Fri 09:00-15:45   poll_quotes (delayed feed; faster polling adds nothing)
  Mon-Fri 16:30                                   ingest_history (EOD bars) -> analytics -> alerts
  Mon-Fri 17:00                                   ingest_corporate (actions, profiles, news, financials)
  Sun 02:00                                       sync_instruments, full signal replay, score validation
  every BACKTEST_POLL_SECONDS                     backtest queue

Every job takes a Postgres advisory lock (ADR-0002), so running these in several processes or replicas is
safe: a second runner skips the job instead of duplicating it.
"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from nse_platform.core.config import Settings

log = logging.getLogger("nse_platform.worker")


def _providers():  # noqa: ANN202
    from nse_platform.providers.registry import build_providers

    return build_providers()


def _guard(name: str, fn) -> None:  # noqa: ANN001
    try:
        result = fn()
        log.info("job_finished", extra={"job": name, "result": str(result)[:2000]})
    except Exception:  # noqa: BLE001 - logged with traceback; the scheduler keeps running other jobs
        log.exception("job_crashed", extra={"job": name})


def job_quotes() -> None:
    from nse_platform.ingestion import jobs

    _guard("poll_quotes", lambda: asyncio.run(jobs.poll_quotes(_providers())))


def job_eod() -> None:
    from nse_platform.alerts.engine import evaluate_all
    from nse_platform.analytics.pipeline import run_analytics
    from nse_platform.ingestion import jobs

    _guard("ingest_history", lambda: asyncio.run(jobs.ingest_history(_providers())))
    _guard("analytics", run_analytics)
    _guard("alerts", evaluate_all)


def job_corporate() -> None:
    from nse_platform.ingestion import jobs

    _guard("ingest_corporate", lambda: asyncio.run(jobs.ingest_corporate(_providers())))


def job_weekly() -> None:
    from nse_platform.analytics.pipeline import run_analytics
    from nse_platform.analytics.validation import run_score_validation
    from nse_platform.ingestion import jobs

    _guard("sync_instruments", lambda: asyncio.run(jobs.sync_instruments(_providers())))
    _guard("analytics_replay", lambda: run_analytics(replay=True))
    _guard("score_validation", run_score_validation)


def job_backtests() -> None:
    from nse_platform.backtesting.runner import process_queue

    try:
        done = process_queue()
        if done:
            log.info("backtests_processed", extra={"runs": done})
    except Exception:  # noqa: BLE001
        log.exception("backtest_queue_error")


def register_jobs(sched: BaseScheduler, s: Settings) -> BaseScheduler:
    """Attach every scheduled job to `sched`. Used by both worker processes and the API's in-process scheduler."""
    tz = s.market_timezone
    sched.add_job(job_quotes, CronTrigger(day_of_week="mon-fri", hour="9-15", minute=f"*/{s.quote_poll_minutes}",
                                          timezone=tz), id="poll_quotes")
    sched.add_job(job_eod, CronTrigger(day_of_week="mon-fri", hour=16, minute=30, timezone=tz), id="eod")
    sched.add_job(job_corporate, CronTrigger(day_of_week="mon-fri", hour=17, minute=0, timezone=tz), id="corporate")
    sched.add_job(job_weekly, CronTrigger(day_of_week="sun", hour=2, minute=0, timezone=tz), id="weekly")
    sched.add_job(job_backtests, IntervalTrigger(seconds=s.backtest_poll_seconds, timezone=tz), id="backtests")
    return sched


# One job at a time: analytics is CPU-bound, so overlapping runs would compete for the process.
JOB_DEFAULTS = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 600}
