"""FastAPI application factory and `nse-api` entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text

from nse_platform.api.middleware import RateLimitMiddleware, RequestContextMiddleware, install_error_handlers
from nse_platform.api.v1 import analysis, auth, data, instruments, market, research, user_data
from nse_platform.core.cache import get_cache
from nse_platform.core.config import get_settings
from nse_platform.core.db import get_engine, get_sessionmaker
from nse_platform.core.logging import configure_logging
from nse_platform.models import Instrument

VERSION = "0.1.0"
log = logging.getLogger("nse_platform.api")


def _build_in_process_scheduler():  # noqa: ANN202
    """Build the in-process scheduler (RUN_SCHEDULER_IN_API=true), without starting it.

    For hosts without a background worker. Jobs run on a single background thread, and each one takes a
    Postgres advisory lock, so extra API replicas skip rather than duplicate work. A long analytics run shares
    this process with request handling, so responses can slow down while it runs; prefer a separate
    `nse-worker` process when the host allows one.
    """
    from apscheduler.schedulers.background import BackgroundScheduler

    from nse_platform.worker.jobs import JOB_DEFAULTS, register_jobs

    s = get_settings()
    sched = BackgroundScheduler(timezone=s.market_timezone, job_defaults=JOB_DEFAULTS,
                                executors={"default": {"type": "threadpool", "max_workers": 1}})
    register_jobs(sched, s)
    return sched


def _start_background_thread(name: str, target):  # noqa: ANN001, ANN202
    """Daemon thread, so the port is bound immediately whatever the database is doing.

    A host that waits for the port fails the deploy if a multi-year backfill - or a wait on an unreachable
    database - runs first, so none of this may happen inline in `lifespan`.
    """
    import threading

    t = threading.Thread(target=target, name=name, daemon=True)
    t.start()
    return t


def _scheduler_starter(sched):  # noqa: ANN001, ANN202
    """Hold the scheduled jobs back until database connectivity is confirmed.

    Started immediately, every job fired against a database that was not yet reachable: no work done, a
    traceback per tick, and - for the 5-second backtest poll - a new connection attempt every 5 seconds
    against a host that was already failing. Waiting for one successful `SELECT 1` costs nothing when the
    database is healthy and removes the whole flood when it is not. Individual jobs still re-check, because
    connectivity can be lost again after this point.
    """
    from nse_platform.core.db import db_gate

    def _run() -> None:
        if not db_gate.wait(get_settings().db_ready_timeout_seconds):
            log.error("scheduler_not_started_db_unreachable", extra={"error": db_gate.last_error})
            return
        # Connectivity is confirmed, so the deferred ADR-0007 guard can finally be answered. A violation
        # must stop the jobs from running, but this thread is not start-up any more, so it cannot abort the
        # process: refuse to start the scheduler instead, loudly.
        try:
            _assert_no_synthetic_in_production()
        except Exception:  # noqa: BLE001 - logged with its traceback; the API keeps serving
            log.exception("scheduler_not_started_synthetic_guard_failed")
            return
        try:
            sched.start()
        except Exception:  # noqa: BLE001 - the API must keep serving even if the scheduler cannot start
            log.exception("scheduler_start_failed")
            return
        log.info("in_process_scheduler_started", extra={"jobs": [j.id for j in sched.get_jobs()]})

    return _run


def _next_run(job) -> str | None:  # noqa: ANN001
    """A job added before its scheduler starts has no `next_run_time` attribute at all."""
    nxt = getattr(job, "next_run_time", None)
    return nxt.isoformat() if nxt else None


def _assert_no_synthetic_in_production() -> None:
    """ADR-0007: synthetic data must never silently appear in a production database."""
    s = get_settings()
    if s.app_env != "production" or s.allow_synthetic_in_production:
        return
    with get_sessionmaker()() as db:
        n = db.scalar(select(func.count()).select_from(Instrument).where(Instrument.is_synthetic.is_(True)))
    if n:
        raise RuntimeError(f"{n} synthetic instruments present in a production database. Run "
                           "`nse-cli purge-synthetic` (ADR-0007).")


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    from nse_platform.core.db import is_transient_db_error, with_db_retry

    s = get_settings()
    # The guard is a data check, not a connectivity check. Retry it, but do not fail the deploy because the
    # database happened to be unreachable this second: the check is run again, before any job does work,
    # from the scheduler-starter thread below. A real violation still aborts start-up.
    try:
        with_db_retry("synthetic_guard", _assert_no_synthetic_in_production)
    except Exception as exc:
        if not is_transient_db_error(exc):
            raise
        log.error("synthetic_guard_deferred_db_unreachable", extra={"error": f"{type(exc).__name__}: {exc}"})

    scheduler = _build_in_process_scheduler() if s.run_scheduler_in_api else None
    app.state.scheduler = scheduler
    app.state.scheduler_starter = None
    if scheduler is not None:
        # Started from a thread that first waits for the database, so an unreachable database delays the
        # jobs instead of flooding the log with per-tick tracebacks - and never delays binding the port.
        app.state.scheduler_starter = _start_background_thread("scheduler-start", _scheduler_starter(scheduler))
    if s.bootstrap_on_start:
        from nse_platform.worker.jobs import bootstrap_thread_target

        app.state.bootstrap = _start_background_thread("bootstrap", bootstrap_thread_target)
    else:
        app.state.bootstrap = None
    yield
    if scheduler is not None and scheduler.running:
        scheduler.shutdown(wait=True)  # let a running job finish before the process exits
    get_engine().dispose()  # graceful shutdown: close pooled connections


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    app = FastAPI(
        title="NSE Intelligence Platform API", version=VERSION, lifespan=lifespan,
        description="Decision-support research API for the Nairobi Securities Exchange. No trade execution. "
                    "Market data freshness is reported on every price; delayed data is never labelled live.",
        docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None,
    )
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                       allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID"])
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
    for r in (auth.router, market.router, instruments.router, analysis.router, research.router, user_data.router,
              data.router):
        app.include_router(r, prefix="/api/v1")

    @app.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    def ready() -> Any:
        checks: dict[str, Any] = {}
        ok = True
        try:
            with get_engine().connect() as c:
                c.execute(text("SELECT 1"))
                rev = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
            checks["database"] = {"ok": True, "migration": rev}
        except Exception as exc:  # noqa: BLE001 - reported in the health payload
            ok = False
            checks["database"] = {"ok": False, "error": type(exc).__name__}
        cache = get_cache()
        checks["redis"] = {"configured": cache.configured, "ok": cache.ping(),
                           "required": False}
        boot = getattr(app.state, "bootstrap", None)
        checks["bootstrap"] = {"enabled": get_settings().bootstrap_on_start,
                               "running": bool(boot and boot.is_alive())}
        from nse_platform.core.db import db_gate

        # The gate background jobs consult, not this endpoint's own probe above: it is only populated once a
        # job or the scheduler starter has looked, so `false` here means "not asked yet" as well as "down".
        checks["database"]["jobs_gate_open"] = db_gate.ready
        checks["database"]["jobs_gate_error"] = db_gate.last_error
        sched = getattr(app.state, "scheduler", None)
        running = bool(sched and sched.running)
        starter = getattr(app.state, "scheduler_starter", None)
        checks["scheduler"] = {
            "in_process": get_settings().run_scheduler_in_api,
            "running": running,
            # Jobs are held until one SELECT 1 succeeds, so a scheduler that is not yet running is waiting
            # for the database rather than broken.
            "waiting_for_database": bool(sched and not running and starter and starter.is_alive()),
            # `next_run_time` is not set on jobs added before the scheduler starts.
            "next_runs": {j.id: _next_run(j) for j in sched.get_jobs()} if sched else None,
            "note": None if get_settings().run_scheduler_in_api
            else "Scheduled jobs run in the separate `nse-worker` process.",
        }
        return JSONResponse(status_code=200 if ok else 503,
                            content={"status": "ok" if ok else "unavailable", "version": VERSION, "checks": checks})

    return app


app = create_app()


def run() -> None:
    import uvicorn

    s = get_settings()
    uvicorn.run("nse_platform.main:app", host="0.0.0.0", port=8000, proxy_headers=True,  # noqa: S104
                forwarded_allow_ips="*" if s.app_env != "production" else "127.0.0.1", log_config=None)
