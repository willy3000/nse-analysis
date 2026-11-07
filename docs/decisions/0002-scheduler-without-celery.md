# ADR-0002: APScheduler + Postgres job ledger instead of Celery

Status: Accepted (2026-11-07)

## Context
The default proposal was Celery and Celery Beat with a Redis broker. The real workloads are:
- Polling quotes every 15 minutes during the session. The upstream data is 15-minute delayed and refreshes every 900 s, so polling faster adds nothing.
- Ingesting end-of-day bars once per trading day, plus backfills.
- Recomputing indicators, trends, opportunities and scores after each ingestion.
- Evaluating alerts after each recomputation.

That is a handful of low-frequency jobs that depend on each other in sequence. A message broker would add a stateful dependency and its own failure modes (broker loss, visibility timeouts, duplicate delivery) with no throughput benefit. The development machine also has no Redis and no Docker. A design that only works with Redis present would hide its failure modes until deployment.

## Decision
- `nse-worker` runs APScheduler 3.x (`BlockingScheduler`) with cron triggers in `Africa/Nairobi` time.
- Every job execution:
  1. Takes a Postgres **advisory lock** keyed by job name (`pg_try_advisory_lock`). An overlapping run or second replica skips instead of running twice.
  2. Writes an `ingestion_runs` row (`running` → `succeeded` / `partial` / `failed`) with counts and error text.
  3. Is **idempotent**: writes are `INSERT … ON CONFLICT` upserts on natural keys.
- Retries use exponential backoff with jitter inside the provider HTTP client (`nse_platform/providers/http.py`) and honour `Retry-After`.
- Redis is **optional**. It provides response caching and rate-limit counters. When Redis is unreachable, the cache is bypassed and rate limiting falls back to in-process counters, and `/api/v1/data/health` reports the degraded state.

## Consequences
- The local environment needs nothing but PostgreSQL.
- If workloads become high-volume or fan-out heavy, for example streaming intraday data after a real-time licence, move to Celery or Arq. Job functions are plain callables, so migrating means re-registering them, not rewriting them.
