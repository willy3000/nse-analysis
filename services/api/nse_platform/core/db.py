"""Database engine, session factory, transient-failure retry and advisory-lock helper."""

from __future__ import annotations

import hashlib
import logging
import random
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import TypeVar

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from nse_platform.core.config import get_settings

log = logging.getLogger("nse_platform.db")

T = TypeVar("T")

# Failures a later attempt may survive: DNS resolution, TCP connect, TLS teardown ("SSL error: unexpected eof
# while reading"), server-side termination, pool exhaustion. Deliberately narrow: IntegrityError,
# ProgrammingError and DataError are bugs or bad data and must never be retried or hidden.
_TRANSIENT_DB_ERRORS = (OperationalError, InterfaceError)


def is_transient_db_error(exc: BaseException) -> bool:
    """True when `exc` is a connectivity-level database failure rather than a query or data error."""
    if isinstance(exc, _TRANSIENT_DB_ERRORS):
        return True
    # A DBAPIError whose connection was invalidated is a dropped connection whatever its subclass.
    return isinstance(exc, DBAPIError) and bool(getattr(exc, "connection_invalidated", False))


@lru_cache
def get_engine() -> Engine:
    s = get_settings()
    return create_engine(
        s.database_url,
        # Pool sizing is per process, and the scheduler thread, the bootstrap thread and every request
        # handler draw from this one pool. Managed Postgres plans cap total connections low (Render's
        # smallest allows roughly 22 across *all* clients), so an over-generous pool here is what exhausts
        # the server and gets connections terminated underneath us.
        pool_size=s.db_pool_size,
        max_overflow=s.db_max_overflow,
        pool_timeout=s.db_pool_timeout,
        pool_pre_ping=True,
        # Managed hosts drop idle connections well inside the old 30-minute recycle, which surfaced as
        # "unexpected eof while reading" on the next use. Recycle ahead of the host.
        pool_recycle=s.db_pool_recycle,
        connect_args={
            # Without this, a DNS or routing failure can block the calling thread until the OS gives up.
            "connect_timeout": s.db_connect_timeout,
            # A job holds its advisory-lock connection open but idle for its whole run, which is minutes of
            # provider HTTP calls. Managed networks drop an idle socket, and the closing unlock then fails.
            # pool_pre_ping cannot help: it only validates on checkout, not a connection already held.
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        },
        future=True,
    )


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on any exception (and re-raise)."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def with_db_retry(name: str, fn: Callable[[], T], attempts: int | None = None) -> T:
    """Run `fn`, retrying transient database connectivity failures with bounded exponential backoff.

    Retries the whole unit of work, not a single statement: a dropped connection invalidates the session, so
    the transaction has to start over. `fn` must therefore be safe to run again from the beginning.

    Query, data and integrity errors are never retried, and the last transient failure is re-raised once the
    attempts are used up - nothing is swallowed here.
    """
    s = get_settings()
    total = attempts if attempts is not None else s.db_retry_attempts
    for attempt in range(1, total + 1):
        try:
            return fn()
        except BaseException as exc:
            if attempt >= total or not is_transient_db_error(exc):
                raise
            delay = min(s.db_retry_base_seconds * 2 ** (attempt - 1), s.db_retry_max_seconds)
            delay *= 0.5 + random.random()  # noqa: S311 - jitter, so replicas do not retry in lockstep
            log.warning("db_retry", extra={"op": name, "attempt": attempt, "of": total,
                                           "sleep": round(delay, 2), "error": f"{type(exc).__name__}: {exc}"})
            time.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def check_db_connection() -> None:
    """Open a connection and run SELECT 1. Raises the underlying error if the database is unreachable."""
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))


class DatabaseGate:
    """Tracks whether database connectivity is confirmed, so background jobs can refuse to start without it.

    A scheduled job that runs while the database is unreachable does no work, marks nothing done, and emits a
    full traceback on every tick - a five-second backtest poll turns one outage into a flood of identical
    stack traces. The gate gives jobs a cheap, quiet way to skip, and re-probes at most once every
    `db_gate_recheck_seconds`, so recovery is picked up without hammering a host that is already struggling.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready = False
        self._checked_at = 0.0
        self._probed = False
        self.last_error: str | None = None

    @property
    def ready(self) -> bool:
        """Last known state, without probing."""
        return self._ready

    def confirm(self, *, force: bool = False) -> bool:
        """True if the database answers. The result is cached for `db_gate_recheck_seconds`."""
        recheck = get_settings().db_gate_recheck_seconds
        with self._lock:
            if not force and self._probed and time.monotonic() - self._checked_at < recheck:
                return self._ready
            try:
                check_db_connection()
            except Exception as exc:  # noqa: BLE001 - recorded in last_error and logged, never discarded
                self.last_error = f"{type(exc).__name__}: {exc}"
                if self._ready or not self._probed:
                    log.warning("db_gate_unavailable", extra={"error": self.last_error})
                self._ready = False
            else:
                if not self._ready:
                    log.info("db_gate_ready")
                self.last_error = None
                self._ready = True
            self._probed = True
            self._checked_at = time.monotonic()
            return self._ready

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the database answers or `timeout` elapses. Returns whether it became reachable."""
        s = get_settings()
        limit = s.db_ready_timeout_seconds if timeout is None else timeout
        deadline = time.monotonic() + limit
        delay = s.db_retry_base_seconds
        while True:
            if self.confirm(force=True):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                log.error("db_wait_timeout", extra={"seconds": limit, "error": self.last_error})
                return False
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, s.db_retry_max_seconds)


db_gate = DatabaseGate()


def _lock_key(name: str) -> int:
    # Stable signed 64-bit key derived from the job name.
    return int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)


@contextmanager
def advisory_lock(name: str) -> Iterator[bool]:
    """Session-level Postgres advisory lock. Yields True if acquired, False if another holder exists."""
    engine = get_engine()
    key = _lock_key(name)

    def _acquire() -> tuple[Connection, bool]:
        conn = engine.connect()
        try:
            return conn, bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar())
        except BaseException:
            conn.close()
            raise

    conn, acquired = with_db_retry(f"advisory_lock:{name}", _acquire)
    try:
        yield acquired
    finally:
        try:
            if acquired:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                conn.commit()
        except Exception as unlock_exc:  # noqa: BLE001
            # The unlock runs while the job's own exception may be propagating. Raising from here would
            # replace that exception with this one and hide the real failure, so it is logged instead. A
            # session-level lock is released by Postgres when the connection goes away - which is exactly the
            # case that makes this unlock fail - so the lock is not leaked.
            log.warning("advisory_unlock_failed",
                        extra={"lock": name, "error": f"{type(unlock_exc).__name__}: {unlock_exc}"})
        finally:
            try:
                conn.close()
            except Exception as close_exc:  # noqa: BLE001 - same reasoning as the unlock above
                log.warning("advisory_lock_close_failed", extra={"lock": name, "error": repr(close_exc)})


__all__ = ["DatabaseGate", "advisory_lock", "check_db_connection", "db_gate", "get_engine", "get_sessionmaker",
           "is_transient_db_error", "session_scope", "with_db_retry"]
