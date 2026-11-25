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

# Jobs whose "database unavailable" skip has already been logged for the current outage.
_SKIP_LOGGED: set[str] = set()


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
        _SKIP_LOGGED.discard(job)
        return True
    # One line per job per outage, not one per tick: a 5-second poll through a 10-minute restart would
    # otherwise write 120 identical lines and bury whatever else happened.
    if job not in _SKIP_LOGGED:
        _SKIP_LOGGED.add(job)
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


def data_gaps() -> dict[str, list]:
    """Which real instruments still lack price bars, and which lack an analytics snapshot.

    This is what makes the load resumable. The old check was a single "do any bars exist at all?" probe,
    so once one instrument had bars the loader believed it was finished - which is how a universe of 66
    instruments ended up with 2 of them loaded and Overview showing one stock. Overview inner-joins
    instrument_snapshots, so an instrument without a snapshot is invisible there however many bars it has.
    """
    from sqlalchemy import select

    from nse_platform.core.db import session_scope, with_db_retry
    from nse_platform.models import Instrument, InstrumentIdentifier, InstrumentSnapshot, PriceBar
    from nse_platform.models.enums import SecurityType

    priced = {SecurityType.EQUITY.value, SecurityType.PREFERENCE.value, SecurityType.ETF.value}

    def _probe() -> dict[str, list]:
        with session_scope() as s:
            # Both symbols: the platform one ("ABSA") for humans, and the provider one ("ABSA.KE") because
            # that is what ingest_history filters on. They differ for every instrument here, and matching
            # them only via its `symbol.split(".")[0]` fallback would break the moment a provider symbol
            # stopped being the platform symbol plus a suffix - silently loading nothing.
            targets = s.execute(
                select(Instrument.id, Instrument.symbol, InstrumentIdentifier.provider_symbol)
                .join(InstrumentIdentifier, InstrumentIdentifier.instrument_id == Instrument.id)
                .where(Instrument.is_synthetic.is_(False), Instrument.security_type.in_(priced),
                       Instrument.primary_provider_id.is_not(None),
                       InstrumentIdentifier.provider_id == Instrument.primary_provider_id)).all()
            with_bars = {r[0] for r in s.execute(select(PriceBar.instrument_id).distinct()).all()}
            with_snaps = {r[0] for r in s.execute(
                select(InstrumentSnapshot.instrument_id).distinct().where(
                    InstrumentSnapshot.timeframe == "1d")).all()}
        missing = [t for t in targets if t[0] not in with_bars]
        return {
            "instruments": [t[1] for t in targets],
            "missing_bars": [t[1] for t in missing],
            "missing_bars_provider_symbols": [t[2] for t in missing],
            "missing_snapshot_ids": [t[0] for t in targets if t[0] in with_bars and t[0] not in with_snaps],
        }

    return with_db_retry("data_gaps", _probe)  # read-only, so safe to run again from the start


def job_catchup() -> bool:
    """Fill whatever the universe is still missing. A no-op once every instrument has bars and a snapshot.

    Runs on a schedule as well as at start-up, because a managed database that restarts every few minutes
    cannot be relied on to let any single pass finish. Each pass loads only the instruments that are still
    missing and uses the stored high-water mark, so passes never redo completed work and the gap shrinks
    monotonically. Returns True when nothing is left to do.

    Never raises: it is called from the scheduler, from the bootstrap thread and from an admin request, and
    an escaping exception in any of those is either invisible or kills the caller.
    """
    try:
        return _catchup_pass()
    except Exception:  # noqa: BLE001 - reported with its traceback; the next pass tries again
        log.exception("catchup_failed")
        return False


def _catchup_pass() -> bool:
    from nse_platform.analytics.pipeline import run_analytics
    from nse_platform.ingestion import jobs

    s = get_settings()
    if not _provider_key(s):
        log.info("catchup_skipped_no_provider_key")
        return True
    if not _db_ready("catchup"):
        return False
    gaps = data_gaps()
    if not gaps["instruments"]:
        log.info("catchup_no_instruments_yet")  # sync_instruments has not run or found nothing
        _guard("catchup_instruments", lambda: asyncio.run(jobs.sync_instruments(_providers())))
        return False
    missing_bars, missing_snaps = gaps["missing_bars"], gaps["missing_snapshot_ids"]
    if not missing_bars and not missing_snaps:
        log.info("catchup_complete", extra={"instruments": len(gaps["instruments"])})
        return True
    log.info("catchup_started", extra={"instruments": len(gaps["instruments"]),
                                       "missing_bars": len(missing_bars),
                                       "missing_snapshots": len(missing_snaps)})
    provs = _providers()
    if missing_bars:
        # `symbols` restricts the pass to what is missing; incremental=True resumes from the stored
        # high-water mark, so an instrument interrupted half way is continued rather than re-downloaded.
        want = gaps["missing_bars_provider_symbols"]
        _guard("catchup_history", lambda: asyncio.run(
            jobs.ingest_history(provs, symbols=want, incremental=True)))
        _guard("catchup_corporate", lambda: asyncio.run(jobs.ingest_corporate(provs)))
    after = data_gaps()
    need_analytics = after["missing_snapshot_ids"]
    if need_analytics:
        _guard("catchup_analytics", lambda: run_analytics(replay=True, instrument_ids=need_analytics))
    final = data_gaps()
    remaining = len(final["missing_bars"]) + len(final["missing_snapshot_ids"])
    if remaining:
        log.info("catchup_progress", extra={"missing_bars": len(final["missing_bars"]),
                                            "missing_snapshots": len(final["missing_snapshot_ids"]),
                                            "was_missing": len(missing_bars) + len(missing_snaps)})
        return False
    log.info("catchup_complete", extra={"instruments": len(final["instruments"])})
    return True


def _provider_key(s: Settings) -> str:
    """The provider key, treating a blank value as absent (hosts pass an unset variable through as "")."""
    return s.mystocks_api_key.get_secret_value().strip() if s.mystocks_api_key is not None else ""


def job_bootstrap() -> bool:
    """First-run load: instruments, then whatever the universe is still missing.

    Runs on a background thread just after start-up, never in the container's start command: a host that
    waits for the port to be bound fails the deploy if a multi-year backfill runs first.

    The load itself is `job_catchup`, which works instrument by instrument against the real gap, so this is
    cheap to repeat and resumes rather than restarts. Returns True when nothing is left to do. Never
    raises: this runs as the only call on a one-shot thread, and an escaping exception killed that thread
    for the life of the process, so the first-run load never happened at all.
    """
    from nse_platform.ingestion import jobs

    s = get_settings()
    if not _provider_key(s):
        log.info("bootstrap_skipped_no_provider_key")
        return True
    try:
        gaps = data_gaps()
    except Exception as exc:  # noqa: BLE001 - reported, then retried by the caller
        log.exception("bootstrap_gaps_failed")
        from nse_platform.core.db import is_transient_db_error

        # A query or data error will not fix itself on the next attempt; connectivity may.
        return not is_transient_db_error(exc)
    if gaps["instruments"] and not gaps["missing_bars"] and not gaps["missing_snapshot_ids"]:
        log.info("bootstrap_skipped_data_present", extra={"instruments": len(gaps["instruments"])})
        return True
    if not gaps["instruments"]:
        try:
            provs = _providers()
        except Exception:  # noqa: BLE001 - a bad provider configuration is not retryable
            log.exception("bootstrap_providers_failed")
            return True
        _guard("bootstrap_instruments", lambda: asyncio.run(jobs.sync_instruments(provs)))
    log.info("bootstrap_started", extra={"missing_bars": len(gaps["missing_bars"]),
                                         "missing_snapshots": len(gaps["missing_snapshot_ids"])})
    done = job_catchup()
    log.info("bootstrap_finished", extra={"complete": done})
    return done


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
    # Keeps filling whatever the universe is still missing, and costs one cheap query once it is
    # complete. On a database that restarts every few minutes, no single pass can be relied on to
    # finish, so the loader has to be something that runs again rather than a one-off at start-up.
    sched.add_job(job_catchup, IntervalTrigger(minutes=s.catchup_poll_minutes, timezone=tz), id="catchup")
    if resolve_self_ping_url(s):
        sched.add_job(job_self_ping, IntervalTrigger(minutes=s.self_ping_minutes, timezone=tz),
                      id="self_ping", next_run_time=datetime.now(ZoneInfo(tz)))
    return sched


# One job at a time: analytics is CPU-bound, so overlapping runs would compete for the process.
JOB_DEFAULTS = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 600}
