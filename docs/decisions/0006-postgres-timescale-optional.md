# ADR-0006: PostgreSQL is authoritative; TimescaleDB is optional

Status: Accepted (2026-11-07)

## Decision
- PostgreSQL ≥ 14 is the only supported database. The schema is managed by Alembic.
- Migration `0002_timescale` turns `price_bars` and `quotes` into hypertables **only if** the `timescaledb` extension is installable. Otherwise it is a no-op and the composite B-tree indexes on `(instrument_id, timeframe, ts)` apply.
  - At NSE scale (about 70 equities × 250 bars/year × a few timeframes) plain Postgres is ample.
- Local development and the test suite run against a real PostgreSQL 16 started by `pgserver` (embedded binaries) when Docker is unavailable.
  - Tests do **not** use SQLite, so Postgres-specific behaviour (ON CONFLICT upserts, advisory locks, JSONB) is actually exercised.
- The schema uses no extensions. pgcrypto, pg_trgm and timescaledb are unavailable in the embedded build, and managed hosts differ in what they offer.
