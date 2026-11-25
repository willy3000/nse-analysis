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
import time
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


def _db_ready(job: str) -> bool:
    """False (with one short log line) when the database is unreachable, so a job can skip instead of crash.

    Without this gate a job started during a database outage does no useful work but still opens a
    connection and logs a full traceback on every tick - the 5-second backtest poll turned one outage into
    hundreds of identical stack traces. The gate caches its probe, so the skip is cheap.
    """
    from nse_platform.core.db import db_gate

    if db_gate.confirm():
        return True
    log.warning("job_skipped_db_unavailable", extra={"job": job, "error": db_gate.last_error})
    return False


def job_quotes() -> None:
    from nse_platform.ingestion import jobs

    if not _db_ready("poll_quotes"):
        return
    _guard("poll_quotes", lambda: asyncio.run(jobs.poll_quotes(_providers())))


def job_eod() -> None:
    from nse_platform.alerts.engine import evaluate_all
    from nse_platform.analytics.pipeline import run_analytics
    from nse_platform.ingestion import jobs

    if not _db_ready("eod"):
        return
    _guard("ingest_history", lambda: asyncio.run(jobs.ingest_history(_providers())))
    _guard("analytics", run_analytics)
    _guard("alerts", evaluate_all)


def job_corporate() -> None:
    from nse_platform.ingestion import jobs

    if not _db_ready("corporate"):
        return
    _guard("ingest_corporate", lambda: asyncio.run(jobs.ingest_corporate(_providers())))


def job_weekly() -> None:
    from nse_platform.analytics.pipeline import run_analytics
    from nse_platform.analytics.validation import run_score_validation
    from nse_platform.ingestion import jobs

    if not _db_ready("weekly"):
        return
    _guard("sync_instruments", lambda: asyncio.run(jobs.sync_instruments(_providers())))
    _guard("analytics_replay", lambda: run_analytics(replay=True))
    _guard("score_validation", run_score_validation)


def job_backtests() -> None:
    from nse_platform.backtesting.runner import process_queue
    from nse_platform.core.db import db_gate, is_transient_db_error

    if not _db_ready("backtests"):
        return
    try:
        done = process_queue()
        if done:
            log.info("backtests_processed", extra={"runs": done})
    except Exception as exc:  # noqa: BLE001
        if is_transient_db_error(exc):
            # Connectivity, not a queue bug. Still reported, but as one line rather than a stack trace every
            # BACKTEST_POLL_SECONDS, and the gate suppresses the next ticks until the database answers again.
            log.warning("backtest_queue_db_unavailable", extra={"error": f"{type(exc).__name__}: {exc}"})
            db_gate.confirm(force=True)
            return
        log.exception("backtest_queue_error")


def _bootstrap_gaps() -> tuple[bool, bool]:
    """(needs_history, needs_analytics) for the real universe.

    Checked separately on purpose. A part-finished earlier load can leave bars behind with no analytics, and
    Overview reads the snapshots, not the bars - so "some bars exist" must not be taken to mean "done".
    """
    from sqlalchemy import select

    from nse_platform.core.db import session_scope, with_db_retry
    from nse_platform.models import Instrument, InstrumentSnapshot, PriceBar

    def _probe() -> tuple[bool, bool]:
        with session_scope() as s:
            bars = s.scalar(select(PriceBar.instrument_id)
                            .join(Instrument, Instrument.id == PriceBar.instrument_id)
                            .where(Instrument.is_synthetic.is_(False)).limit(1))
            snaps = s.scalar(select(InstrumentSnapshot.instrument_id)
                             .join(Instrument, Instrument.id == InstrumentSnapshot.instrument_id)
                             .where(Instrument.is_synthetic.is_(False)).limit(1))
        return bars is None, snaps is None

    # Read-only, so it is safe to run again from the start after a dropped connection.
    return with_db_retry("bootstrap_gaps", _probe)


def job_bootstrap() -> bool:
    """One-off first-run load: instruments, history, corporate actions, then analytics.

    Runs on a background thread just after start-up, never in the container's start command: a host that
    waits for the port to be bound fails the deploy if a multi-year backfill runs first. Skips itself once
    real bars exist, so a redeploy does not repeat it. Each step is guarded, so one failure does not stop
    the rest.

    Returns True when there is nothing left to do (loaded, or legitimately skipped) and False when it should
    be tried again - see `bootstrap_thread_target`, which owns the retrying. Never raises: this runs as the
    only call on a one-shot thread, and an escaping exception killed that thread for the life of the
    process, so the first-run load never happened at all.
    """
    from datetime import date

    from nse_platform.analytics.pipeline import run_analytics
    from nse_platform.ingestion import jobs

    s = get_settings()
    # A blank value counts as absent. Hosts hand an unset variable through as "", which is not None, so the
    # load used to start with an unconfigured provider, fetch nothing, and - now that it retries - do it
    # repeatedly.
    key = s.mystocks_api_key.get_secret_value().strip() if s.mystocks_api_key is not None else ""
    if not key:
        log.info("bootstrap_skipped_no_provider_key")
        return True
    try:
        needs_history, needs_analytics = _bootstrap_gaps()
    except Exception as exc:  # noqa: BLE001 - reported, then retried by the caller
        log.exception("bootstrap_gaps_failed")
        from nse_platform.core.db import is_transient_db_error

        # A query or data error will not fix itself on the next attempt; connectivity may.
        return not is_transient_db_error(exc)
    if not needs_history and not needs_analytics:
        log.info("bootstrap_skipped_data_present")
        return True
    start = date(date.today().year - s.bootstrap_backfill_years, 1, 1)
    try:
        provs = _providers()
    except Exception:  # noqa: BLE001 - a bad provider configuration is not retryable
        log.exception("bootstrap_providers_failed")
        return True
    log.info("bootstrap_started", extra={"history": needs_history, "analytics": needs_analytics,
                                         "history_from": start.isoformat()})
    if needs_history:
        _guard("bootstrap_instruments", lambda: asyncio.run(jobs.sync_instruments(provs)))
        _guard("bootstrap_history", lambda: asyncio.run(jobs.ingest_history(provs, start=start, incremental=False)))
        _guard("bootstrap_corporate", lambda: asyncio.run(jobs.ingest_corporate(provs)))
    _guard("bootstrap_analytics", lambda: run_analytics(replay=True))
    log.info("bootstrap_finished")
    # Each step is individually guarded, so "finished" does not mean "complete". Re-check, and let the
    # caller try again if the analytics snapshots that Overview reads are still missing.
    try:
        still_missing = any(_bootstrap_gaps())
    except Exception:  # noqa: BLE001 - the load itself ran; do not spin on a failing probe
        log.exception("bootstrap_verify_failed")
        return True
    if still_missing:
        log.warning("bootstrap_incomplete")
    return not still_missing


def bootstrap_thread_target() -> None:
    """Thread body for the first-run load: wait for the database, then retry the load until it sticks.

    The load is the only way a fresh deployment gets price bars and analytics snapshots, and it ran exactly
    once - during start-up, which is also when a managed database is least likely to be reachable. One DNS
    failure therefore left the deployment permanently empty. So: confirm connectivity first, then retry a
    load that reports itself incomplete, with bounded exponential backoff.
    """
    from nse_platform.core.db import db_gate

    s = get_settings()
    if not db_gate.wait(s.db_ready_timeout_seconds):
        log.error("bootstrap_abandoned_db_unreachable", extra={"error": db_gate.last_error})
        return
    delay = max(s.db_retry_base_seconds, 5.0)
    for attempt in range(1, s.bootstrap_max_attempts + 1):
        try:
            if job_bootstrap():
                return
        except Exception:  # noqa: BLE001 - job_bootstrap should not raise; belt and braces for the thread
            log.exception("bootstrap_unexpected_error", extra={"attempt": attempt})
        if attempt == s.bootstrap_max_attempts:
            log.error("bootstrap_gave_up", extra={"attempts": attempt})
            return
        log.info("bootstrap_retry_scheduled", extra={"attempt": attempt, "sleep": round(delay, 1)})
        time.sleep(delay)
        delay = min(delay * 2, s.bootstrap_retry_max_seconds)


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
