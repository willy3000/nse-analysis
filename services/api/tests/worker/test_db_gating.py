"""Scheduled jobs must not run - or log a traceback per tick - while the database is unreachable.

The production symptom: `backtest_queue_error` with a full stack trace every BACKTEST_POLL_SECONDS (5) for
as long as the database host failed to resolve, and a bootstrap thread that died on its first attempt and
so never loaded any data at all.
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy.exc import OperationalError

from nse_platform.core import db as dbmod
from nse_platform.core.config import Settings
from nse_platform.worker import jobs as wjobs


def _dns_failure() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("failed to resolve host 'dpg-xxxx-a': Name or service not known"))


@pytest.fixture
def db_down(monkeypatch):
    monkeypatch.setattr(dbmod, "check_db_connection", lambda: (_ for _ in ()).throw(_dns_failure()))
    monkeypatch.setattr(dbmod, "db_gate", dbmod.DatabaseGate())
    monkeypatch.setattr(dbmod.time, "sleep", lambda _s: None)


@pytest.fixture
def db_up(monkeypatch):
    monkeypatch.setattr(dbmod, "check_db_connection", lambda: None)
    monkeypatch.setattr(dbmod, "db_gate", dbmod.DatabaseGate())


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def messages(self) -> list[str]:
        # getMessage(), not `.message`: that attribute is only set when a formatter runs, and
        # JsonFormatter never sets it - so `.message` is missing once configure_logging() has run.
        return [r.getMessage() for r in self.records]


@pytest.fixture
def worker_log():
    """Capture the worker logger directly.

    Not `caplog`: `configure_logging()` replaces the root logger's handlers, so once any earlier test has
    built the app, caplog's handler is gone and these assertions would silently pass on an empty list.
    """
    logger = logging.getLogger("nse_platform.worker")
    handler = _Collect()
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    yield handler
    logger.removeHandler(handler)
    logger.setLevel(previous)


class TestJobsSkipWhenTheDatabaseIsDown:
    @pytest.mark.parametrize("job", ["job_quotes", "job_eod", "job_corporate", "job_weekly", "job_backtests"])
    def test_job_does_no_work_and_does_not_raise(self, job, db_down, monkeypatch):
        """Each job returns immediately. Nothing is swallowed: the gate logged why."""
        touched: list[str] = []
        monkeypatch.setattr(wjobs, "_providers", lambda: touched.append("providers"))
        monkeypatch.setattr(wjobs, "_guard", lambda name, fn: touched.append(name))
        getattr(wjobs, job)()
        assert touched == []
        assert "failed to resolve host" in (dbmod.db_gate.last_error or "")

    def test_the_gate_probes_once_not_once_per_tick(self, db_down, monkeypatch):
        """A 5-second poll during a 10-minute outage must not mean 120 connection attempts."""
        probes: list[int] = []
        monkeypatch.setattr(dbmod, "check_db_connection",
                            lambda: (probes.append(1), (_ for _ in ()).throw(_dns_failure()))[1])
        for _ in range(50):
            wjobs.job_backtests()
        assert len(probes) == 1

    def test_jobs_run_normally_once_the_database_answers(self, db_up, monkeypatch):
        ran: list[str] = []
        monkeypatch.setattr(wjobs, "_providers", lambda: {})
        monkeypatch.setattr(wjobs, "_guard", lambda name, fn: ran.append(name))
        wjobs.job_eod()
        assert ran == ["ingest_history", "analytics", "alerts"]


class TestBacktestQueueErrors:
    def test_a_dropped_connection_is_one_line_not_a_stack_trace(self, db_up, monkeypatch, worker_log):
        from nse_platform.backtesting import runner

        monkeypatch.setattr(runner, "process_queue", lambda: (_ for _ in ()).throw(_dns_failure()))
        wjobs.job_backtests()  # must not raise
        assert "backtest_queue_db_unavailable" in worker_log.messages()
        assert "backtest_queue_error" not in worker_log.messages()
        assert not any(r.exc_info for r in worker_log.records)

    def test_a_real_queue_bug_still_gets_its_traceback(self, db_up, monkeypatch, worker_log):
        """Connectivity handling must not become a way to hide genuine failures."""
        from nse_platform.backtesting import runner

        monkeypatch.setattr(runner, "process_queue", lambda: (_ for _ in ()).throw(ValueError("bad spec")))
        wjobs.job_backtests()
        errors = [r for r in worker_log.records if r.getMessage() == "backtest_queue_error"]
        assert len(errors) == 1 and errors[0].exc_info  # full traceback preserved


def _settings(**kw: object) -> Settings:
    kw.setdefault("mystocks_api_key", "sk_test_key")
    return Settings(session_secret="x" * 40, **kw)  # type: ignore[arg-type]


class TestBootstrapThreadSurvivesFailure:
    def test_a_failing_gap_check_does_not_kill_the_thread_or_the_load(self, db_up, monkeypatch):
        """The bug: the gap check raised, the one-shot thread died, and no data was ever loaded."""
        monkeypatch.setattr(wjobs, "get_settings", lambda: _settings(bootstrap_max_attempts=3))
        monkeypatch.setattr(wjobs.time, "sleep", lambda _s: None)
        calls: list[str] = []
        gaps = iter([_dns_failure(), _dns_failure(), None])

        def flaky_gaps() -> dict:
            exc = next(gaps)
            if exc is not None:
                raise exc
            # By now the data is in (another replica, or an earlier pass).
            return {"instruments": ["A"], "missing_bars": [], "missing_bars_provider_symbols": [],
                    "missing_snapshot_ids": []}

        monkeypatch.setattr(wjobs, "data_gaps", flaky_gaps)
        monkeypatch.setattr(wjobs, "_providers", lambda: calls.append("providers") or {})

        wjobs.bootstrap_thread_target()  # must not raise, and must keep trying
        assert next(gaps, "exhausted") == "exhausted"  # all three attempts were made

    def test_it_waits_for_the_database_before_loading_anything(self, db_down, monkeypatch):
        """Start-up is exactly when a managed database is least likely to answer."""
        monkeypatch.setattr(wjobs, "get_settings", lambda: _settings(db_ready_timeout_seconds=0))
        touched: list[str] = []
        monkeypatch.setattr(wjobs, "data_gaps", lambda: touched.append("gaps") or {})
        wjobs.bootstrap_thread_target()
        assert touched == []  # never attempted against a database known to be unreachable

    def test_an_unexpected_error_is_logged_and_retried_not_fatal(self, db_up, monkeypatch, worker_log):
        monkeypatch.setattr(wjobs, "get_settings", lambda: _settings(bootstrap_max_attempts=2))
        monkeypatch.setattr(wjobs.time, "sleep", lambda _s: None)
        monkeypatch.setattr(wjobs, "job_bootstrap", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        wjobs.bootstrap_thread_target()  # must not raise out of the thread
        messages = worker_log.messages()
        assert messages.count("bootstrap_unexpected_error") == 2  # both attempts tried and reported
        assert "bootstrap_gave_up" in messages

    @pytest.mark.parametrize("key", [None, "", "   "])
    def test_no_provider_key_is_not_retried(self, key, db_up, monkeypatch):
        """Nothing to load and nothing to fix: it must settle, not spin.

        A blank string counts as absent - hosts pass an unset variable through as "", which is not None.
        """
        monkeypatch.setattr(wjobs, "get_settings", lambda: _settings(mystocks_api_key=key))
        probed: list[str] = []
        monkeypatch.setattr(wjobs, "data_gaps", lambda: probed.append("gaps") or {})
        slept: list[float] = []
        monkeypatch.setattr(wjobs.time, "sleep", slept.append)
        wjobs.bootstrap_thread_target()
        assert slept == [] and probed == []
