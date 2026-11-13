# Deployment & operations

## Process topology

| Process | Command | Scaling |
|---|---|---|
| API | `uvicorn nse_platform.main:app` (image default) | Any number of replicas (stateless) |
| Worker | `python -m nse_platform.worker.main` | **One** recommended; advisory locks make extra replicas safe (jobs skip) |
| Migrations | `python -m alembic upgrade head` | Run once per deploy, before API/worker |
| Web | `node server.js` (Next standalone) | Any number of replicas |
| PostgreSQL 14+ (TimescaleDB optional) | managed service recommended | — |
| Redis (optional) | cache and rate-limit counters | The app degrades gracefully without it |

Do **not** run the worker on serverless platforms; it needs a persistent process.

## Recommended hosting (single user)

| Component | Option |
|---|---|
| Web | Vercel (set `API_ORIGIN` to the public API URL at build time) or the web container |
| API + worker | One small VPS or container host (e.g. 1 vCPU / 2 GB) running `docker compose` |
| Database | Managed Postgres (e.g. Timescale Cloud, Neon, RDS) or the compose `db` service with backups |

Production deploy (compose on a VPS behind a TLS reverse proxy such as Caddy or Nginx):

1. `cp .env.example .env` and set:
   - `APP_ENV=production`
   - a strong `SESSION_SECRET` and `POSTGRES_PASSWORD`
   - `COOKIE_SECURE=true`
   - `CORS_ORIGINS=["https://your-domain"]`
   - `MYSTOCKS_API_KEY`, and `MYSTOCKS_ENV=production` once your KYB is approved
   - `ENABLE_SYNTHETIC_PROVIDER=false`
2. `docker compose up -d --build`. Migrations run automatically through the `migrate` service.
3. Put HTTPS in front of port 3000 (web) only. Keep 8000 and 5432 private; compose binds them to 127.0.0.1.
4. Create your account, then set `ALLOW_REGISTRATION=false` and restart `api`.
5. Load data: `docker compose run --rm api python -m nse_platform.cli sync-instruments --provider mystocks`, then `backfill`, `ingest-corporate` and `analytics --replay`.

The API refuses to start in production if synthetic instruments exist (`nse-cli purge-synthetic`). It also refuses if `SESSION_SECRET` is the dev default or `COOKIE_SECURE` is false.

## Secrets
Secrets live only in `.env` on the host or in the platform's secret store, never in git. The frontend never receives provider keys or database credentials; it only talks to the API through the same-origin proxy.

## Backups & restore

```bash
bash scripts/backup.sh                 # writes .localdata/backups/nse-YYYYmmdd-HHMM.dump (pg_dump custom format)
bash scripts/backup.sh restore FILE    # pg_restore --clean into $DATABASE_URL (asks for confirmation)
```
- Run the backup daily (cron or Task Scheduler) and copy the dumps off the host. Keep at least 14 daily and 8 weekly copies.
- With compose: `docker compose exec db pg_dump -U nse -Fc nse > nse.dump`, and `docker compose exec -T db pg_restore -U nse -d nse --clean < nse.dump` to restore.
- Market data can be re-downloaded (`backfill`). **User data (portfolios, watchlists, strategies, alerts) cannot**, which is why backups matter.
- Test a restore into a scratch database quarterly.

## Monitoring

| Signal | Where |
|---|---|
| Liveness / readiness | `GET /health/live`, `GET /health/ready` (database, migration version, Redis) |
| Data freshness, provider status, rate limits | Data Centre page or `GET /api/v1/data/health`, `/data/providers` |
| Job history and failures | `GET /api/v1/data/runs?status=failed`, table `ingestion_runs` |
| Data-quality issues | `GET /api/v1/data/issues` |
| Logs | JSON lines on stdout (`LOG_JSON=true`), one per request, with `request_id`; worker logs `job_finished` / `job_crashed` |

Alert yourself, for example with an uptime monitor, when `/health/ready` fails or `data/health.overall` is not COMPLETE after 17:30 EAT on a trading day.

## Local development without Docker
`bash scripts/dev.sh` (start) and `bash scripts/dev.sh stop`. It uses embedded PostgreSQL in `.localdata/pg`. After an unclean shutdown, the first start replays the WAL, which can take about a minute.
