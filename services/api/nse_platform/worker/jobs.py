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
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from nse_platform.core.config import Settings, get_settings

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


def _bootstrap_gaps() -> tuple[bool, bool]:
    """(needs_history, needs_analytics) for the real universe.

    Checked separately on purpose. A part-finished earlier load can leave bars behind with no analytics, and
    Overview reads the snapshots, not the bars - so "some bars exist" must not be taken to mean "done".
    """
    from sqlalchemy import select

    from nse_platform.core.db import session_scope
    from nse_platform.models import Instrument, InstrumentSnapshot, PriceBar

    with session_scope() as s:
        bars = s.scalar(select(PriceBar.instrument_id)
                        .join(Instrument, Instrument.id == PriceBar.instrument_id)
                        .where(Instrument.is_synthetic.is_(False)).limit(1))
        snaps = s.scalar(select(InstrumentSnapshot.instrument_id)
                         .join(Instrument, Instrument.id == InstrumentSnapshot.instrument_id)
                         .where(Instrument.is_synthetic.is_(False)).limit(1))
    return bars is None, snaps is None


def job_bootstrap() -> None:
    """One-off first-run load: instruments, history, corporate actions, then analytics.

    Runs on a background thread just after start-up, never in the container's start command: a host that
    waits for the port to be bound fails the deploy if a multi-year backfill runs first. Skips itself once
    real bars exist, so a redeploy does not repeat it. Each step is guarded, so one failure does not stop
    the rest.
    """
    from datetime import date

    from nse_platform.analytics.pipeline import run_analytics
    from nse_platform.ingestion import jobs

    s = get_settings()
    if s.mystocks_api_key is None:
        log.info("bootstrap_skipped_no_provider_key")
        return
    needs_history, needs_analytics = _bootstrap_gaps()
    if not needs_history and not needs_analytics:
        log.info("bootstrap_skipped_data_present")
        return
    start = date(date.today().year - s.bootstrap_backfill_years, 1, 1)
    provs = _providers()
    log.info("bootstrap_started", extra={"history": needs_history, "analytics": needs_analytics,
                                         "history_from": start.isoformat()})
    if needs_history:
        _guard("bootstrap_instruments", lambda: asyncio.run(jobs.sync_instruments(provs)))
        _guard("bootstrap_history", lambda: asyncio.run(jobs.ingest_history(provs, start=start, incremental=False)))
        _guard("bootstrap_corporate", lambda: asyncio.run(jobs.ingest_corporate(provs)))
    _guard("bootstrap_analytics", lambda: run_analytics(replay=True))
    log.info("bootstrap_finished")


def resolve_self_ping_url(s: Settings) -> str | None:
    """Public base URL to ping, from SELF_PING_URL or the host's own variable (Render sets this)."""
    raw = s.self_ping_url or os.environ.get("RENDER_EXTERNAL_URL") or ""
    raw = raw.strip().rstrip("/")
    if not raw:
        return None
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    return raw


def job_self_ping() -> None:
    """GET our own /health/live so an idle free-tier instance is not put to sleep.

    Deliberately hits /health/live, which returns a static response and touches neither the database nor
    Redis. The request must go to the public URL: a loopback call never reaches the host's router and so
    would not count as activity.
    """
    import httpx

    base = resolve_self_ping_url(get_settings())
    if not base:
        return
    url = f"{base}/health/live"
    try:
        r = httpx.get(url, timeout=10.0, headers={"User-Agent": "nse-platform-selfping/1"})
        log.debug("self_ping", extra={"url": url, "status": r.status_code})
    except Exception as exc:  # noqa: BLE001 - never let a failed ping affect the schedule
        log.warning("self_ping_failed", extra={"url": url, "error": repr(exc)})


def register_jobs(sched: BaseScheduler, s: Settings) -> BaseScheduler:
    """Attach every scheduled job to `sched`. Used by both worker processes and the API's in-process scheduler."""
    tz = s.market_timezone
    sched.add_job(job_quotes, CronTrigger(day_of_week="mon-fri", hour="9-15", minute=f"*/{s.quote_poll_minutes}",
                                          timezone=tz), id="poll_quotes")
    sched.add_job(job_eod, CronTrigger(day_of_week="mon-fri", hour=16, minute=30, timezone=tz), id="eod")
    sched.add_job(job_corporate, CronTrigger(day_of_week="mon-fri", hour=17, minute=0, timezone=tz), id="corporate")
    sched.add_job(job_weekly, CronTrigger(day_of_week="sun", hour=2, minute=0, timezone=tz), id="weekly")
    sched.add_job(job_backtests, IntervalTrigger(seconds=s.backtest_poll_seconds, timezone=tz), id="backtests")
    if resolve_self_ping_url(s):
        sched.add_job(job_self_ping, IntervalTrigger(minutes=s.self_ping_minutes, timezone=tz),
                      id="self_ping", next_run_time=datetime.now(ZoneInfo(tz)))
    return sched


# One job at a time: analytics is CPU-bound, so overlapping runs would compete for the process.
JOB_DEFAULTS = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 600}
