# NSE Intelligence Platform

A research and decision-support terminal for the **Nairobi Securities Exchange**. It provides market overview, scanner, opportunity signals, multi-timeframe trends, fundamentals, a strategy backtester, a paper portfolio, watchlists, alerts, news, and a Data Centre that shows exactly how fresh the data is.

**The platform never executes trades.** Prices from the connected provider are 15-minute delayed or end-of-day, and are never labelled "live".

## Quick start (no Docker)

Requirements: Python 3.10+, Node 22, Git Bash on Windows (or any POSIX shell).

```bash
# one-time setup
python -m venv services/api/.venv
services/api/.venv/Scripts/python -m pip install -e "services/api[dev]"   # .venv/bin/python on Linux/macOS
(cd apps/web && npm ci)
cp .env.example .env            # then set MYSTOCKS_API_KEY

# every time
bash scripts/dev.sh             # Postgres + migrations + API :8080 + worker + web :3000
bash scripts/dev.sh stop        # stop API, worker and Postgres
```

Open http://localhost:3000 and create an account. First data load:

```bash
cd services/api
.venv/Scripts/python -m nse_platform.cli sync-instruments --provider mystocks
.venv/Scripts/python -m nse_platform.cli backfill --provider mystocks --years 11
.venv/Scripts/python -m nse_platform.cli ingest-corporate --provider mystocks
.venv/Scripts/python -m nse_platform.cli analytics --replay
```

## Quick start (Docker)

```bash
cp .env.example .env    # set MYSTOCKS_API_KEY, POSTGRES_PASSWORD, SESSION_SECRET
docker compose up --build
docker compose run --rm api python -m nse_platform.cli sync-instruments --provider mystocks
```

## Repository layout

| Path | Contents |
|---|---|
| `services/api/` | Python package `nse_platform`: FastAPI API, worker, CLI, analytics, backtester, tests |
| `apps/web/` | Next.js + TypeScript + Tailwind frontend (13 sections) |
| `infrastructure/docker/` | Container images |
| `docs/` | User guide, architecture, data sources, schema, API, operations, security, ADRs |
| `scripts/` | Local launcher, embedded Postgres, API smoke test |

## Documentation

- **[User guide](docs/USER_GUIDE.md):** how it works, refresh schedule, reading signals, making decisions
- [Architecture](docs/architecture/overview.md) · [Data flow](docs/architecture/data-flow.md) · [Decisions (ADRs)](docs/decisions/)
- [Data providers research](docs/data-sources/provider-research.md) · [CSV import formats](docs/data-sources/csv-import.md)
- [Database schema](docs/database/schema.md) · [API](docs/api/README.md)
- [Deployment & operations](docs/deployment/operations.md) · [Security](docs/security/security.md)
- [Project status](PROJECT_STATUS.md) · [Tasks](TASKS.md) · [Roadmap](docs/roadmap.md)

## Tests

```bash
cd services/api && .venv/Scripts/python -m pytest -q   # needs Postgres: scripts/dev_postgres.py or TEST_DATABASE_URL
cd apps/web && npx vitest run && npx tsc --noEmit && npm run build
```
