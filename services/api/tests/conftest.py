"""Test configuration.

Pure unit tests need nothing. Tests marked `db` need PostgreSQL: set TEST_DATABASE_URL, or have
scripts/dev_postgres.py running (its URL file is used, with database `nse_test`). The test database is wiped,
migrated with Alembic and loaded with a small deterministic synthetic universe once per session.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
URL_FILE = ROOT / ".localdata" / "pg_url.txt"


def _test_url() -> str | None:
    if os.environ.get("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]
    if URL_FILE.exists():
        return URL_FILE.read_text().strip().rsplit("/", 1)[0] + "/nse_test"
    return None


TEST_URL = _test_url()
if TEST_URL:
    os.environ["DATABASE_URL"] = TEST_URL
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("RATE_LIMIT_PER_MINUTE", "100000")
os.environ.setdefault("LOGIN_RATE_LIMIT_PER_MINUTE", "100000")
os.environ["MYSTOCKS_API_KEY"] = ""

SYNTH_END = date(2026, 6, 30)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "db: requires PostgreSQL")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if TEST_URL:
        return
    skip = pytest.mark.skip(reason="no PostgreSQL available (set TEST_DATABASE_URL)")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def database() -> str:
    assert TEST_URL
    import sqlalchemy as sa
    from alembic.config import Config

    from alembic import command

    eng = sa.create_engine(TEST_URL, isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(sa.text("DROP SCHEMA public CASCADE"))
        c.execute(sa.text("CREATE SCHEMA public"))
    eng.dispose()
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    cfg.attributes["database_url"] = TEST_URL
    command.upgrade(cfg, "head")
    return TEST_URL


@pytest.fixture(scope="session")
def seeded(database: str) -> dict[str, object]:
    """Synthetic universe from 2023 to SYNTH_END, ingested through the real jobs and analysed."""
    from nse_platform.analytics.pipeline import run_analytics
    from nse_platform.ingestion import jobs
    from nse_platform.providers.synthetic import SyntheticProvider

    providers = {"synthetic": SyntheticProvider(end_date=SYNTH_END)}
    asyncio.run(jobs.sync_instruments(providers))
    hist = asyncio.run(jobs.ingest_history(providers, start=date(2023, 1, 1), end=SYNTH_END, incremental=False))
    corp = asyncio.run(jobs.ingest_corporate(providers))
    analytics = run_analytics(replay=True)
    return {"history": hist, "corporate": corp, "analytics": analytics}


@pytest.fixture
def client(seeded):  # noqa: ANN001, ANN201
    from fastapi.testclient import TestClient

    from nse_platform.main import create_app

    return TestClient(create_app())


def register(client, email: str, password: str = "Test-passw0rd") -> dict[str, str]:  # noqa: ANN001
    client.get("/api/v1/auth/csrf")
    r = client.post("/api/v1/auth/register", json={"email": email, "password": password},
                    headers={"X-CSRF-Token": client.cookies.get("nse_csrf")})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": client.cookies.get("nse_csrf")}
