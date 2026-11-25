"""First-run bootstrap load: runs on the scheduler thread, not in the container's start command."""

from __future__ import annotations

from datetime import date

from nse_platform.core.config import Settings
from nse_platform.worker import jobs as wjobs


def settings(**kw: object) -> Settings:
    kw.setdefault("mystocks_api_key", "sk_test_key")
    return Settings(session_secret="x" * 40, **kw)  # type: ignore[arg-type]


def test_not_an_apscheduler_job(monkeypatch):
    """It runs on its own thread from the API lifespan, so it works without an in-process scheduler too."""
    from apscheduler.schedulers.background import BackgroundScheduler

    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    sched = wjobs.register_jobs(BackgroundScheduler(timezone="Africa/Nairobi"), settings())
    assert "bootstrap" not in {j.id for j in sched.get_jobs()}


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
    monkeypatch.setattr(wjobs, "get_settings", lambda: settings())
    monkeypatch.setattr(wjobs, "_bootstrap_gaps", lambda: (False, False))
    wjobs.job_bootstrap()
    assert calls == []  # a redeploy must not reload everything


def test_bars_without_analytics_runs_analytics_only(monkeypatch):
    """A part-finished earlier load leaves bars but no snapshots, and Overview reads the snapshots."""
    calls: list[str] = []
    _record(calls, monkeypatch)
    monkeypatch.setattr(wjobs, "get_settings", lambda: settings())
    monkeypatch.setattr(wjobs, "_bootstrap_gaps", lambda: (False, True))
    wjobs.job_bootstrap()
    assert calls == ["analytics:True"]  # no pointless re-download of history


def test_skipped_without_a_provider_key(monkeypatch):
    """On by default, so it must stay inert on an installation with no provider configured."""
    calls: list[str] = []
    _record(calls, monkeypatch)
    monkeypatch.setattr(wjobs, "get_settings", lambda: settings(mystocks_api_key=None))
    monkeypatch.setattr(wjobs, "_bootstrap_gaps", lambda: (True, True))
    wjobs.job_bootstrap()
    assert calls == []


def test_loads_in_order_over_the_configured_window(monkeypatch):
    calls: list[str] = []
    _record(calls, monkeypatch)
    monkeypatch.setattr(wjobs, "_bootstrap_gaps", lambda: (True, True))
    monkeypatch.setattr(wjobs, "get_settings", lambda: settings(bootstrap_backfill_years=3))
    wjobs.job_bootstrap()
    expect_start = date(date.today().year - 3, 1, 1)
    assert calls == ["instruments", f"history:{expect_start}:False", "corporate", "analytics:True"]


def test_one_failing_step_does_not_stop_the_rest(monkeypatch):
    calls: list[str] = []
    _record(calls, monkeypatch)
    monkeypatch.setattr(wjobs, "_bootstrap_gaps", lambda: (True, True))
    monkeypatch.setattr(wjobs, "get_settings", lambda: settings())
    from nse_platform.ingestion import jobs as ijobs

    async def boom(_p, start=None, incremental=True, symbols=None):  # noqa: ANN001
        raise RuntimeError("provider down")

    monkeypatch.setattr(ijobs, "ingest_history", boom)
    wjobs.job_bootstrap()  # must not raise
    assert "corporate" in calls and "analytics:True" in calls
