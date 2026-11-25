"""A restarting database must cost one instrument, not the rest of the universe.

The production failure: `ingest_history` loaded two instruments, lost its connection to a restarting
Postgres, and the generic handler in `_run_job` marked the run failed and ended the job - so the remaining
64 instruments were never fetched at all, on that pass or any later one.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from nse_platform.ingestion import jobs as ijobs


def _db_loss() -> OperationalError:
    return OperationalError("INSERT", {}, Exception("server closed the connection unexpectedly"))


class _Run:
    """Stand-in for the IngestionRun ledger row."""

    def __init__(self) -> None:
        self.items_requested = 0
        self.items_succeeded = 0
        self.items_failed = 0
        self.rows_written = 0
        self.details: dict | None = None


class _Session:
    def __init__(self, *, recover: bool = True) -> None:
        self.rollbacks = 0
        self.merges = 0
        self.recover = recover

    def rollback(self) -> None:
        self.rollbacks += 1
        if not self.recover:
            raise _db_loss()  # a rollback on a dead socket fails too; that must not mask anything

    def merge(self, obj):  # noqa: ANN001, ANN202
        self.merges += 1
        return obj


class TestResumeAfterDbLoss:
    def test_rolls_back_waits_and_reattaches_the_run(self, monkeypatch):
        from nse_platform.core import db as dbmod

        gate = dbmod.DatabaseGate()
        monkeypatch.setattr(dbmod, "db_gate", gate)
        monkeypatch.setattr(dbmod, "check_db_connection", lambda: None)  # server is back
        s, run = _Session(), _Run()
        got = ijobs._resume_after_db_loss(s, run, _db_loss())
        assert got is run and s.rollbacks == 1 and s.merges == 1

    def test_a_failing_rollback_does_not_mask_the_recovery(self, monkeypatch):
        """The rollback almost always fails against a dead socket - reconnecting is what matters."""
        from nse_platform.core import db as dbmod

        monkeypatch.setattr(dbmod, "db_gate", dbmod.DatabaseGate())
        monkeypatch.setattr(dbmod, "check_db_connection", lambda: None)
        s = _Session(recover=False)
        assert ijobs._resume_after_db_loss(s, _Run(), _db_loss()) is not None

    def test_re_raises_the_original_when_the_database_stays_down(self, monkeypatch):
        """Nothing is swallowed: the ledger records the same failure it always did."""
        from nse_platform.core import db as dbmod

        monkeypatch.setattr(dbmod, "db_gate", dbmod.DatabaseGate())
        monkeypatch.setattr(dbmod, "check_db_connection", lambda: (_ for _ in ()).throw(_db_loss()))
        monkeypatch.setattr(dbmod.time, "sleep", lambda _s: None)
        monkeypatch.setattr(ijobs, "get_settings",
                            lambda: __import__("nse_platform.core.config", fromlist=["Settings"]).Settings(
                                session_secret="x" * 40, db_ready_timeout_seconds=0))  # type: ignore[arg-type]
        original = _db_loss()
        with pytest.raises(OperationalError) as caught:
            ijobs._resume_after_db_loss(_Session(), _Run(), original)
        assert caught.value is original  # the real cause, not a rollback artefact


class TestOnlyConnectivityIsRecovered:
    def test_a_data_error_still_aborts_the_job(self):
        """Recovery must not become a way to paper over a genuine bug: only connectivity is transient."""
        from nse_platform.core.db import is_transient_db_error

        assert is_transient_db_error(_db_loss())
        assert not is_transient_db_error(IntegrityError("INSERT", {}, Exception("duplicate key")))


@pytest.mark.db
def test_default_history_window_follows_the_configured_backfill_years(database, monkeypatch):
    """A hard-coded decade per instrument made a pass too long to finish between restarts.

    Instrument 41 in production held 2,637 bars back to 2016 because the window fell back to ten years,
    while BOOTSTRAP_BACKFILL_YEARS said three. Asserted on the date actually requested of the provider.
    """
    import asyncio
    from datetime import date

    from nse_platform.ingestion import jobs as real
    from nse_platform.providers.synthetic import SyntheticProvider

    asked: list[date] = []

    class Recording(SyntheticProvider):
        async def get_historical_prices(self, symbol, start, end, timeframe):  # noqa: ANN001, ANN202
            asked.append(start)
            return await super().get_historical_prices(symbol, start, end, timeframe)

    providers = {"synthetic": Recording()}
    asyncio.run(real.sync_instruments(providers))
    asyncio.run(real.ingest_history(providers, incremental=False))  # no explicit start: uses the default
    assert asked, "no instrument was requested"
    years = max(date.today().year - a.year for a in asked)
    assert years == 3, f"requested {years} years of history, expected the configured 3"
