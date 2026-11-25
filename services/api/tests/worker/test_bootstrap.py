"""First-run bootstrap load: runs on its own thread, and delegates the actual loading to the catch-up pass.

Bootstrap exists only to get the universe started and kick the first pass. The gap-filling itself is
`job_catchup` (see test_catchup.py), because a single start-up pass cannot be relied on to finish against a
managed database that restarts every few minutes.
"""

from __future__ import annotations

import pytest

from nse_platform.core.config import Settings
from nse_platform.worker import jobs as wjobs


def settings(**kw: object) -> Settings:
    kw.setdefault("mystocks_api_key", "sk_test_key")
    return Settings(session_secret="x" * 40, **kw)  # type: ignore[arg-type]


def gaps(instruments: list[str], missing: list[str], snapshot_ids: list[int] | None = None) -> dict:
    return {
        "instruments": instruments,
        "missing_bars": missing,
        "missing_bars_provider_symbols": [f"{m}.KE" for m in missing],
        "missing_snapshot_ids": snapshot_ids or [],
    }


def test_not_an_apscheduler_job(monkeypatch):
    """It runs on its own thread from the API lifespan, so it works without an in-process scheduler too."""
    from apscheduler.schedulers.background import BackgroundScheduler

    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    sched = wjobs.register_jobs(BackgroundScheduler(timezone="Africa/Nairobi"), settings())
    assert "bootstrap" not in {j.id for j in sched.get_jobs()}


def test_skipped_when_the_universe_is_already_complete(monkeypatch):
    """A redeploy must not reload anything."""
    monkeypatch.setattr(wjobs, "get_settings", lambda: settings())
    monkeypatch.setattr(wjobs, "data_gaps", lambda: gaps(["A", "B"], []))
    ran: list[str] = []
    monkeypatch.setattr(wjobs, "_guard", lambda name, fn: ran.append(name))
    monkeypatch.setattr(wjobs, "job_catchup", lambda: ran.append("catchup") or True)
    assert wjobs.job_bootstrap() is True
    assert ran == []


def test_syncs_the_universe_first_when_there_are_no_instruments(monkeypatch):
    """Nothing can be loaded before sync_instruments has run, so bootstrap does that one step itself."""
    monkeypatch.setattr(wjobs, "get_settings", lambda: settings())
    monkeypatch.setattr(wjobs, "data_gaps", lambda: gaps([], []))
    monkeypatch.setattr(wjobs, "_providers", lambda: {})
    ran: list[str] = []
    monkeypatch.setattr(wjobs, "_guard", lambda name, fn: ran.append(name))
    monkeypatch.setattr(wjobs, "job_catchup", lambda: False)
    assert wjobs.job_bootstrap() is False
    assert ran == ["bootstrap_instruments"]


def test_delegates_the_load_to_the_catchup_pass(monkeypatch):
    monkeypatch.setattr(wjobs, "get_settings", lambda: settings())
    monkeypatch.setattr(wjobs, "data_gaps", lambda: gaps(["A", "B", "C"], ["B", "C"]))
    monkeypatch.setattr(wjobs, "_guard", lambda name, fn: None)
    called: list[str] = []
    monkeypatch.setattr(wjobs, "job_catchup", lambda: called.append("catchup") or True)
    assert wjobs.job_bootstrap() is True
    assert called == ["catchup"]


def test_reports_incomplete_so_the_thread_tries_again(monkeypatch):
    monkeypatch.setattr(wjobs, "get_settings", lambda: settings())
    monkeypatch.setattr(wjobs, "data_gaps", lambda: gaps(["A", "B"], ["B"]))
    monkeypatch.setattr(wjobs, "_guard", lambda name, fn: None)
    monkeypatch.setattr(wjobs, "job_catchup", lambda: False)
    assert wjobs.job_bootstrap() is False


@pytest.mark.parametrize("key", [None, "", "   "])
def test_skipped_without_a_provider_key(key, monkeypatch):
    """On by default, so it must stay inert on an installation with no provider configured.

    A blank string counts as absent: hosts pass an unset variable through as "", which is not None.
    """
    monkeypatch.setattr(wjobs, "get_settings", lambda: settings(mystocks_api_key=key))
    probed: list[str] = []
    monkeypatch.setattr(wjobs, "data_gaps", lambda: probed.append("gaps") or gaps([], []))
    assert wjobs.job_bootstrap() is True
    assert probed == []
