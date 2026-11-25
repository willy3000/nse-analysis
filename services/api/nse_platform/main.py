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


def _start_in_process_scheduler():  # noqa: ANN202
    """Start the scheduled jobs inside this API process (RUN_SCHEDULER_IN_API=true).

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
    sched.start()
    log.info("in_process_scheduler_started", extra={"jobs": [j.id for j in sched.get_jobs()]})
    return sched


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    s = get_settings()
    if s.app_env == "production" and not s.allow_synthetic_in_production:
        with get_sessionmaker()() as db:
            n = db.scalar(select(func.count()).select_from(Instrument).where(Instrument.is_synthetic.is_(True)))
        if n:
            raise RuntimeError(f"{n} synthetic instruments present in a production database. Run "
                               "`nse-cli purge-synthetic` (ADR-0007).")
    scheduler = _start_in_process_scheduler() if s.run_scheduler_in_api else None
    app.state.scheduler = scheduler
    yield
    if scheduler is not None:
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
        sched = getattr(app.state, "scheduler", None)
        checks["scheduler"] = {
            "in_process": get_settings().run_scheduler_in_api,
            "running": bool(sched and sched.running),
            "next_runs": {j.id: j.next_run_time.isoformat() if j.next_run_time else None
                          for j in sched.get_jobs()} if sched else None,
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
