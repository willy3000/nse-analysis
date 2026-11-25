"""First-run bootstrap load: runs on the scheduler thread, not in the container's start command."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from nse_platform.core.config import Settings
from nse_platform.worker import jobs as wjobs


def settings(**kw: object) -> Settings:
    return Settings(session_secret="x" * 40, **kw)  # type: ignore[arg-type]


def test_registered_only_when_enabled(monkeypatch):
    from apscheduler.schedulers.background import BackgroundScheduler

    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    off = wjobs.register_jobs(BackgroundScheduler(timezone="Africa/Nairobi"), settings())
    assert "bootstrap" not in {j.id for j in off.get_jobs()}

    on = wjobs.register_jobs(BackgroundScheduler(timezone="Africa/Nairobi"), settings(bootstrap_on_start=True))
    job = next(j for j in on.get_jobs() if j.id == "bootstrap")
    # A one-shot, shortly after start-up so the web server has bound its port first.
    delay = job.trigger.run_date - datetime.now(ZoneInfo("Africa/Nairobi"))
    assert timedelta(0) < delay <= timedelta(seconds=30)


def _record(calls: list[str], monkeypatch) -> None:
    from nse_platform.analytics import pipeline
    from nse_platform.ingestion import jobs as ijobs

    monkeypatch.setattr(wjobs, "_providers", lambda: {})

    async def instruments(_p):  # noqa: ANN001
        calls.append("instruments")

    async def history(_p, start=None, incremental=True, symbols=None):  # noqa: ANN001
        calls.append(f"history:{start}:{incremental}")

    async def corporate(_p):  # noqa: ANN001
        calls.append("corporate")

    monkeypatch.setattr(ijobs, "sync_instruments", instruments)
    monkeypatch.setattr(ijobs, "ingest_history", history)
    monkeypatch.setattr(ijobs, "ingest_corporate", corporate)
    monkeypatch.setattr(pipeline, "run_analytics", lambda replay=False: calls.append(f"analytics:{replay}"))


def test_skipped_when_real_bars_exist(monkeypatch):
    calls: list[str] = []
    _record(calls, monkeypatch)
    monkeypatch.setattr(wjobs, "_has_real_bars", lambda: True)
    wjobs.job_bootstrap()
    assert calls == []  # a redeploy must not reload everything


def test_loads_in_order_over_the_configured_window(monkeypatch):
    calls: list[str] = []
    _record(calls, monkeypatch)
    monkeypatch.setattr(wjobs, "_has_real_bars", lambda: False)
    monkeypatch.setattr(wjobs, "get_settings", lambda: settings(bootstrap_backfill_years=3))
    wjobs.job_bootstrap()
    expect_start = date(date.today().year - 3, 1, 1)
    assert calls == ["instruments", f"history:{expect_start}:False", "corporate", "analytics:True"]


def test_one_failing_step_does_not_stop_the_rest(monkeypatch):
    calls: list[str] = []
    _record(calls, monkeypatch)
    monkeypatch.setattr(wjobs, "_has_real_bars", lambda: False)
    monkeypatch.setattr(wjobs, "get_settings", lambda: settings())
    from nse_platform.ingestion import jobs as ijobs

    async def boom(_p, start=None, incremental=True, symbols=None):  # noqa: ANN001
        raise RuntimeError("provider down")

    monkeypatch.setattr(ijobs, "ingest_history", boom)
    wjobs.job_bootstrap()  # must not raise
    assert "corporate" in calls and "analytics:True" in calls
