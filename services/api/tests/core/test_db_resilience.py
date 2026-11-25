"""Transient-failure handling in the database layer.

These are the paths the Render logs exercised: a database hostname that fails to resolve, a connection the
server tears down mid-use ("SSL error: unexpected eof while reading"), and the advisory-lock unlock that
then fails while the job's own exception is on its way out.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError, ProgrammingError

from nse_platform.core import db as dbmod


def _dns_failure() -> OperationalError:
    """The exact shape of the production failure: psycopg could not resolve the database host."""
    return OperationalError("SELECT 1", {}, Exception("failed to resolve host 'dpg-xxxx-a': Name or service not known"))


def _eof_failure() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("SSL error: unexpected eof while reading"))


class TestClassification:
    def test_connectivity_failures_are_transient(self):
        assert dbmod.is_transient_db_error(_dns_failure())
        assert dbmod.is_transient_db_error(_eof_failure())
        assert dbmod.is_transient_db_error(InterfaceError("x", {}, Exception("connection already closed")))

    def test_query_and_data_errors_are_not(self):
        """These will fail identically on every attempt, and retrying one would hide a bug."""
        assert not dbmod.is_transient_db_error(ProgrammingError("SELECT nope", {}, Exception("no such column")))
        assert not dbmod.is_transient_db_error(IntegrityError("INSERT", {}, Exception("duplicate key")))
        assert not dbmod.is_transient_db_error(ValueError("not a database error at all"))

    def test_invalidated_connection_is_transient_whatever_the_subclass(self):
        exc = ProgrammingError("SELECT 1", {}, Exception("boom"))
        exc.connection_invalidated = True
        assert dbmod.is_transient_db_error(exc)


class TestWithDbRetry:
    def test_retries_a_transient_failure_and_returns(self, monkeypatch):
        monkeypatch.setattr(dbmod.time, "sleep", lambda _s: None)
        attempts = []

        def flaky() -> str:
            attempts.append(1)
            if len(attempts) < 3:
                raise _dns_failure()
            return "ok"

        assert dbmod.with_db_retry("test", flaky) == "ok"
        assert len(attempts) == 3

    def test_gives_up_and_re_raises_the_real_error(self, monkeypatch):
        """Bounded: the caller still sees the database failure, never a silent success."""
        monkeypatch.setattr(dbmod.time, "sleep", lambda _s: None)
        calls = []

        def always_down() -> None:
            calls.append(1)
            raise _eof_failure()

        with pytest.raises(OperationalError, match="unexpected eof"):
            dbmod.with_db_retry("test", always_down, attempts=3)
        assert len(calls) == 3

    def test_does_not_retry_a_query_error(self, monkeypatch):
        monkeypatch.setattr(dbmod.time, "sleep", lambda _s: None)
        calls = []

        def broken() -> None:
            calls.append(1)
            raise ProgrammingError("SELECT nope", {}, Exception("no such column"))

        with pytest.raises(ProgrammingError):
            dbmod.with_db_retry("test", broken, attempts=5)
        assert len(calls) == 1

    def test_backoff_is_exponential_and_capped(self, monkeypatch):
        slept: list[float] = []
        monkeypatch.setattr(dbmod.time, "sleep", slept.append)
        monkeypatch.setattr(dbmod.random, "random", lambda: 0.5)  # no jitter: delay * 1.0

        with pytest.raises(OperationalError):
            dbmod.with_db_retry("test", lambda: (_ for _ in ()).throw(_dns_failure()), attempts=6)

        s = dbmod.get_settings()
        assert slept == pytest.approx([min(s.db_retry_base_seconds * 2 ** i, s.db_retry_max_seconds)
                                       for i in range(5)])
        assert max(slept) <= s.db_retry_max_seconds


class TestDatabaseGate:
    def test_starts_closed_and_opens_once_the_database_answers(self, monkeypatch):
        gate = dbmod.DatabaseGate()
        assert gate.ready is False
        monkeypatch.setattr(dbmod, "check_db_connection", lambda: None)
        assert gate.confirm() is True
        assert gate.ready is True and gate.last_error is None

    def test_records_the_error_without_raising(self, monkeypatch):
        """A job needs to skip quietly, so the gate reports rather than throws - but keeps the message."""
        gate = dbmod.DatabaseGate()
        monkeypatch.setattr(dbmod, "check_db_connection", lambda: (_ for _ in ()).throw(_dns_failure()))
        assert gate.confirm() is False
        assert "failed to resolve host" in (gate.last_error or "")

    def test_result_is_cached_so_a_five_second_poll_does_not_probe_every_tick(self, monkeypatch):
        probes = []
        gate = dbmod.DatabaseGate()
        monkeypatch.setattr(dbmod, "check_db_connection", lambda: probes.append(1))
        for _ in range(10):
            gate.confirm()
        assert len(probes) == 1
        gate.confirm(force=True)
        assert len(probes) == 2

    def test_failure_is_cached_too(self, monkeypatch):
        probes = []
        gate = dbmod.DatabaseGate()

        def down() -> None:
            probes.append(1)
            raise _dns_failure()

        monkeypatch.setattr(dbmod, "check_db_connection", down)
        for _ in range(10):
            assert gate.confirm() is False
        assert len(probes) == 1

    def test_wait_polls_until_the_database_comes_back(self, monkeypatch):
        monkeypatch.setattr(dbmod.time, "sleep", lambda _s: None)
        gate = dbmod.DatabaseGate()
        calls = []

        def eventually() -> None:
            calls.append(1)
            if len(calls) < 4:
                raise _dns_failure()

        monkeypatch.setattr(dbmod, "check_db_connection", eventually)
        assert gate.wait(timeout=60) is True
        assert len(calls) == 4

    def test_wait_gives_up_at_the_deadline(self, monkeypatch):
        monkeypatch.setattr(dbmod.time, "sleep", lambda _s: None)
        gate = dbmod.DatabaseGate()
        monkeypatch.setattr(dbmod, "check_db_connection", lambda: (_ for _ in ()).throw(_dns_failure()))
        assert gate.wait(timeout=0) is False
        assert gate.ready is False


class _FakeResult:
    def __init__(self, value: object):
        self._value = value

    def scalar(self) -> object:
        return self._value


class _FakeConn:
    """Acquires the lock, then fails every later statement - an idle connection the network dropped."""

    def __init__(self, *, fail_unlock: bool, acquired: bool = True):
        self.fail_unlock = fail_unlock
        self.acquired = acquired
        self.statements: list[str] = []
        self.closed = False

    def execute(self, stmt, _params=None):  # noqa: ANN001
        sql = str(stmt)
        self.statements.append(sql)
        if "pg_advisory_unlock" in sql:
            if self.fail_unlock:
                raise InterfaceError("unlock", {}, Exception("connection already closed"))
            return _FakeResult(True)
        return _FakeResult(self.acquired)

    def commit(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _FakeEngine:
    def __init__(self, conn: _FakeConn):
        self._conn = conn

    def connect(self) -> _FakeConn:
        return self._conn


class TestAdvisoryLock:
    def test_failed_unlock_does_not_mask_the_job_failure(self, monkeypatch):
        """The whole point: the job's exception must reach the caller, not be replaced by the unlock error."""
        conn = _FakeConn(fail_unlock=True)
        monkeypatch.setattr(dbmod, "get_engine", lambda: _FakeEngine(conn))

        with pytest.raises(RuntimeError, match="provider down"), dbmod.advisory_lock("eod") as got:
            assert got is True
            raise RuntimeError("provider down")

        assert any("pg_advisory_unlock" in s for s in conn.statements)  # it did try
        assert conn.closed

    def test_failed_unlock_alone_does_not_fail_the_job(self, monkeypatch):
        """A session-level lock dies with its connection, so a failed unlock leaks nothing worth crashing for."""
        conn = _FakeConn(fail_unlock=True)
        monkeypatch.setattr(dbmod, "get_engine", lambda: _FakeEngine(conn))
        with dbmod.advisory_lock("eod") as got:
            assert got is True
        assert conn.closed

    def test_no_unlock_attempted_when_the_lock_was_not_acquired(self, monkeypatch):
        conn = _FakeConn(fail_unlock=False, acquired=False)
        monkeypatch.setattr(dbmod, "get_engine", lambda: _FakeEngine(conn))
        with dbmod.advisory_lock("eod") as got:
            assert got is False
        assert not any("pg_advisory_unlock" in s for s in conn.statements)

    def test_acquisition_retries_a_transient_failure(self, monkeypatch):
        monkeypatch.setattr(dbmod.time, "sleep", lambda _s: None)
        conn = _FakeConn(fail_unlock=False)
        tries = []

        class Flaky:
            def connect(self):  # noqa: ANN202
                tries.append(1)
                if len(tries) < 3:
                    raise _dns_failure()
                return conn

        monkeypatch.setattr(dbmod, "get_engine", lambda: Flaky())
        with dbmod.advisory_lock("eod") as got:
            assert got is True
        assert len(tries) == 3


def test_engine_configuration_is_pooled_conservatively():
    """A small pool on purpose: managed Postgres plans cap total connections across all clients."""
    s = dbmod.get_settings()
    assert s.db_pool_size + s.db_max_overflow <= 12
    assert s.db_pool_recycle <= 600  # must recycle before the host drops an idle connection
    assert s.db_connect_timeout > 0  # a DNS failure must not block a thread indefinitely
