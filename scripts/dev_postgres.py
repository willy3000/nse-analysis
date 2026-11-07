"""Start a local PostgreSQL for development/tests when Docker is unavailable (uses `pgserver` binaries).

Usage:  services/api/.venv/Scripts/python scripts/dev_postgres.py
Creates databases `nse` and `nse_test`, and writes the connection URL to .localdata/pg_url.txt.
The server keeps running after this script exits (cleanup_mode=None). Stop it with --stop.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pgserver

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / ".localdata" / "pg"


def main() -> None:
    DATA.parent.mkdir(parents=True, exist_ok=True)
    srv = pgserver.get_server(str(DATA), cleanup_mode=None)
    if "--stop" in sys.argv:
        srv.cleanup()
        print("stopped")
        return
    existing = srv.psql("SELECT datname FROM pg_database;")
    for db in ("nse", "nse_test"):
        if f" {db}\n" not in existing and not any(line.strip() == db for line in existing.splitlines()):
            srv.psql(f"CREATE DATABASE {db};")
    base = srv.get_uri()  # postgresql://postgres:@127.0.0.1:PORT/postgres
    url = base.rsplit("/", 1)[0].replace("postgresql://", "postgresql+psycopg://") + "/nse"
    (ROOT / ".localdata" / "pg_url.txt").write_text(url)
    print(url)


if __name__ == "__main__":
    main()
