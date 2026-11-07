"""`nse-worker`: scheduled ingestion, analytics, alerts and the backtest queue (ADR-0002).

Schedule (Africa/Nairobi):
  every 15 min, Mon-Fri 09:00-15:45   poll_quotes (delayed feed; faster polling adds nothing)
  Mon-Fri 16:30                       ingest_history (EOD bars, incremental) -> analytics -> alerts
  Mon-Fri 17:00                       ingest_corporate (actions, profiles, news, financials)
  Sun 02:00                           sync_instruments, score validation, full signal replay
  every BACKTEST_POLL_SECONDS          backtest queue
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from nse_platform.core.config import get_settings
from nse_platform.core.logging import configure_logging

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


def build_scheduler() -> BlockingScheduler:
    s = get_settings()
    tz = s.market_timezone
    sched = BlockingScheduler(timezone=tz, job_defaults={"coalesce": True, "max_instances": 1,
                                                         "misfire_grace_time": 600})
    sched.add_job(job_quotes, CronTrigger(day_of_week="mon-fri", hour="9-15", minute=f"*/{s.quote_poll_minutes}",
                                          timezone=tz), id="poll_quotes")
    sched.add_job(job_eod, CronTrigger(day_of_week="mon-fri", hour=16, minute=30, timezone=tz), id="eod")
    sched.add_job(job_corporate, CronTrigger(day_of_week="mon-fri", hour=17, minute=0, timezone=tz), id="corporate")
    sched.add_job(job_weekly, CronTrigger(day_of_week="sun", hour=2, minute=0, timezone=tz), id="weekly")
    sched.add_job(job_backtests, IntervalTrigger(seconds=s.backtest_poll_seconds), id="backtests")
    return sched


def run() -> None:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    sched = build_scheduler()

    def _stop(*_: object) -> None:
        log.info("worker_shutdown_requested")
        sched.shutdown(wait=True)  # graceful: let running jobs finish
        sys.exit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("worker_started", extra={"jobs": [j.id for j in sched.get_jobs()]})
    if "--once" in sys.argv:
        job_eod()
        job_backtests()
        return
    sched.start()


if __name__ == "__main__":
    run()
