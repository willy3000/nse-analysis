"""Self-ping keeps a free-tier instance awake without touching the database."""

from __future__ import annotations

import httpx
import pytest
import respx

from nse_platform.core.config import Settings
from nse_platform.worker.jobs import job_self_ping, register_jobs, resolve_self_ping_url


def settings(**kw: object) -> Settings:
    return Settings(session_secret="x" * 40, **kw)  # type: ignore[arg-type]


def test_url_resolution_order_and_normalisation(monkeypatch):
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    assert resolve_self_ping_url(settings()) is None                      # disabled by default
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://api.onrender.com/")
    assert resolve_self_ping_url(settings()) == "https://api.onrender.com"  # trailing slash trimmed
    # An explicit setting wins over the host variable, and a bare host gets https.
    assert resolve_self_ping_url(settings(self_ping_url="my-api.example.com")) == "https://my-api.example.com"


@respx.mock
def test_ping_hits_public_health_live_only(monkeypatch):
    monkeypatch.setenv("SELF_PING_URL", "https://api.example.com")
    from nse_platform.core.config import get_settings

    get_settings.cache_clear()
    route = respx.get("https://api.example.com/health/live").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    try:
        job_self_ping()
    finally:
        monkeypatch.delenv("SELF_PING_URL")
        get_settings.cache_clear()
    assert route.called and route.call_count == 1
    # /health/live is a static response: no database or Redis call is made by the ping.
    assert str(route.calls[0].request.url).endswith("/health/live")


@respx.mock
def test_ping_failure_is_swallowed(monkeypatch):
    monkeypatch.setenv("SELF_PING_URL", "https://down.example.com")
    from nse_platform.core.config import get_settings

    get_settings.cache_clear()
    respx.get("https://down.example.com/health/live").mock(side_effect=httpx.ConnectError("refused"))
    try:
        job_self_ping()  # must not raise: a failed ping may not break the schedule
    finally:
        monkeypatch.delenv("SELF_PING_URL")
        get_settings.cache_clear()


def test_job_registered_only_when_url_configured(monkeypatch):
    from apscheduler.schedulers.background import BackgroundScheduler

    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    off = register_jobs(BackgroundScheduler(timezone="Africa/Nairobi"), settings())
    assert "self_ping" not in {j.id for j in off.get_jobs()}
    on = register_jobs(BackgroundScheduler(timezone="Africa/Nairobi"),
                       settings(self_ping_url="https://api.example.com", self_ping_minutes=10))
    job = next(j for j in on.get_jobs() if j.id == "self_ping")
    assert job.trigger.interval.total_seconds() == pytest.approx(600)
    assert job.next_run_time is not None  # fires immediately, not only after the first interval
