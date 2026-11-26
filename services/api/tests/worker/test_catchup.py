"""The catch-up loader: fills the real gap, resumably, on a database that restarts mid-pass.

The production failure this exists for: 66 instruments were synced, the history job started, loaded two of
them, lost its connection to a restarting Postgres, and aborted - so the remaining 64 were never fetched at
all and Overview (which inner-joins instrument_snapshots) showed one stock. The old check asked only "do
any bars exist?", so once one instrument had bars the loader believed it was finished.
"""

from __future__ import annotations

import pytest

from nse_platform.core.config import Settings
from nse_platform.worker import jobs as wjobs


def _settings(**kw: object) -> Settings:
    kw.setdefault("mystocks_api_key", "sk_test_key")
    return Settings(session_secret="x" * 40, **kw)  # type: ignore[arg-type]


def _gaps(instruments: list[str], missing: list[str], snapshot_ids: list[int] | None = None) -> dict:
    return {
        "instruments": instruments,
        "missing_bars": missing,
        "missing_bars_provider_symbols": [f"{m}.KE" for m in missing],
        "missing_snapshot_ids": snapshot_ids or [],
    }


@pytest.fixture
def ready(monkeypatch):
    monkeypatch.setattr(wjobs, "_db_ready", lambda _job: True)
    monkeypatch.setattr(wjobs, "get_settings", lambda: _settings())
    monkeypatch.setattr(wjobs, "_providers", lambda: {})


class TestGapTargeting:
    def test_loads_only_what_is_missing_using_provider_symbols(self, ready, monkeypatch):
        """A pass must not re-download the instruments that are already done."""
        calls: dict[str, object] = {}
        universe = [f"S{i}" for i in range(10)]
        missing = ["S3", "S7"]
        seq = iter([_gaps(universe, missing), _gaps(universe, []), _gaps(universe, [])])
        monkeypatch.setattr(wjobs, "data_gaps", lambda: next(seq))

        def guard(name, fn):  # noqa: ANN001
            calls[name] = True
            if name == "catchup_history":
                fn()

        monkeypatch.setattr(wjobs, "_guard", guard)

        async def history(_p, start=None, incremental=True, symbols=None):  # noqa: ANN001
            calls["symbols"] = symbols
            calls["incremental"] = incremental

        from nse_platform.ingestion import jobs as ijobs

        monkeypatch.setattr(ijobs, "ingest_history", history)
        assert wjobs.job_catchup() is True
        # Provider symbols, not platform symbols: ingest_history filters on the provider's spelling.
        assert calls["symbols"] == ["S3.KE", "S7.KE"]
        assert calls["incremental"] is True  # resume from the stored high-water mark

    def test_complete_universe_is_a_no_op(self, ready, monkeypatch):
        monkeypatch.setattr(wjobs, "data_gaps", lambda: _gaps(["A", "B"], []))
        ran: list[str] = []
        monkeypatch.setattr(wjobs, "_guard", lambda name, fn: ran.append(name))
        assert wjobs.job_catchup() is True
        assert ran == []  # one cheap query and nothing else once the data is in

    def test_reports_incomplete_when_a_gap_remains(self, ready, monkeypatch):
        """A pass that could not finish must not claim success, or the catch-up would stop early."""
        universe = [f"S{i}" for i in range(10)]
        seq = iter([_gaps(universe, ["S1", "S2", "S3"]), _gaps(universe, ["S2", "S3"]),
                    _gaps(universe, ["S2", "S3"])])
        monkeypatch.setattr(wjobs, "data_gaps", lambda: next(seq))
        monkeypatch.setattr(wjobs, "_guard", lambda name, fn: None)
        assert wjobs.job_catchup() is False

    def test_analytics_runs_only_for_instruments_without_a_snapshot(self, ready, monkeypatch):
        """Overview reads snapshots, so bars without a snapshot are still invisible - but replaying the
        whole universe each pass would waste the window between restarts."""
        universe = ["A", "B", "C"]
        seq = iter([_gaps(universe, [], [7, 9]), _gaps(universe, [], [7, 9]), _gaps(universe, [], [])])
        monkeypatch.setattr(wjobs, "data_gaps", lambda: next(seq))
        got: dict[str, object] = {}

        def guard(name, fn):  # noqa: ANN001
            if name == "catchup_analytics":
                fn()

        monkeypatch.setattr(wjobs, "_guard", guard)
        from nse_platform.analytics import pipeline

        monkeypatch.setattr(pipeline, "run_analytics",
                            lambda replay=False, instrument_ids=None: got.update(ids=instrument_ids))
        assert wjobs.job_catchup() is True
        assert got["ids"] == [7, 9]

    def test_syncs_instruments_when_the_universe_is_empty(self, ready, monkeypatch):
        """Nothing can be loaded before sync_instruments has run at least once."""
        monkeypatch.setattr(wjobs, "data_gaps", lambda: _gaps([], []))
        ran: list[str] = []
        monkeypatch.setattr(wjobs, "_guard", lambda name, fn: ran.append(name))
        assert wjobs.job_catchup() is False  # not complete: come back after the sync
        assert ran == ["catchup_instruments"]


class TestGuards:
    def test_skipped_without_a_provider_key(self, monkeypatch):
        monkeypatch.setattr(wjobs, "get_settings", lambda: _settings(mystocks_api_key=""))
        probed: list[str] = []
        monkeypatch.setattr(wjobs, "data_gaps", lambda: probed.append("x") or _gaps([], []))
        assert wjobs.job_catchup() is True
        assert probed == []

    def test_does_not_run_while_the_database_is_unreachable(self, monkeypatch):
        monkeypatch.setattr(wjobs, "get_settings", lambda: _settings())
        monkeypatch.setattr(wjobs, "_db_ready", lambda _job: False)
        probed: list[str] = []
        monkeypatch.setattr(wjobs, "data_gaps", lambda: probed.append("x") or _gaps([], []))
        assert wjobs.job_catchup() is False
        assert probed == []


class TestScheduled:
    def test_catchup_is_on_the_schedule(self, monkeypatch):
        """It cannot be a start-up-only task: no single pass is guaranteed to finish between restarts."""
        from apscheduler.schedulers.background import BackgroundScheduler

        monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
        monkeypatch.delenv("RAILWAY_PUBLIC_DOMAIN", raising=False)
        sched = wjobs.register_jobs(BackgroundScheduler(timezone="Africa/Nairobi"), _settings())
        assert "catchup" in {j.id for j in sched.get_jobs()}


class TestSkipLogIsNotPerTick:
    def test_one_line_per_outage_not_one_per_poll(self, monkeypatch):
        """A 5-second poll through a 10-minute restart wrote 120 identical lines."""
        from sqlalchemy.exc import OperationalError

        from nse_platform.core import db as dbmod

        gate = dbmod.DatabaseGate()
        monkeypatch.setattr(dbmod, "db_gate", gate)
        monkeypatch.setattr(dbmod, "check_db_connection",
                            lambda: (_ for _ in ()).throw(OperationalError("x", {}, Exception("down"))))
        wjobs._SKIP_LOGGED.clear()
        logged = [wjobs._db_ready("backtests") for _ in range(50)]
        assert logged == [False] * 50
        assert sorted(wjobs._SKIP_LOGGED) == ["backtests"]  # logged once, then suppressed

        # ...and the flag clears on recovery, so the next outage is not silent. The gate caches a failure
        # for db_gate_recheck_seconds, so the probe has to come due first - that caching is the point.
        monkeypatch.setattr(dbmod, "check_db_connection", lambda: None)
        assert wjobs._db_ready("backtests") is False  # still within the cached window
        gate._checked_at -= dbmod.get_settings().db_gate_recheck_seconds + 1  # the interval elapses
        assert wjobs._db_ready("backtests") is True
        assert not wjobs._SKIP_LOGGED  # ready for the next outage to be reported
