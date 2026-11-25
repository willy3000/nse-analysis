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

### Running without a separate worker
Hosts that charge for background workers (Render, for example) can run the schedule inside the API instead:
set `RUN_SCHEDULER_IN_API=true` on the API service. The same jobs then run on one background thread in the
API process.

- The API instance must stay awake. On a host that sleeps idle instances (Render's free web service), the
  schedule stops while it sleeps.
- A long analytics run shares the process with request handling, so responses can slow for a few minutes.
- Extra API replicas are safe: each job takes a Postgres advisory lock, so replicas skip rather than duplicate.
- Confirm it is live at `GET /health/ready` -> `checks.scheduler`, which lists each job's next run time.
- Do not also run `nse-worker` unless you want the redundancy; locks make it safe either way.

### Loading data on a host with no shell access
`BOOTSTRAP_ON_START=true` runs the first data load — instruments, history, corporate actions, analytics — on
the scheduler thread about 30 seconds after start-up. It needs `RUN_SCHEDULER_IN_API=true` (or a worker) to
have somewhere to run, and `MYSTOCKS_API_KEY` to have anything to fetch.

- It deliberately does **not** run in the container's start command. A host that waits for the port to be
  bound (Render does) fails the deploy if a multi-year backfill runs before the web server starts.
- It skips itself once any non-synthetic price bar exists, so redeploys do not reload everything. To force a
  reload, clear `price_bars` first.
- `BOOTSTRAP_BACKFILL_YEARS` (default 3) bounds the history. The longest indicator lookback is ~120
  sessions, so 3 years is ample; 10 years is a much larger download and will not fit free-tier storage.
- Each step is logged and guarded: one failing step does not stop the others. Watch progress at
  `GET /api/v1/data/runs`, and the result at `GET /api/v1/data/health`.
- It holds the single job thread while it runs, so the first quote poll may be skipped. The schedule resumes
  normally afterwards.

### Keeping a free-tier instance awake
Hosts that sleep idle instances stop the schedule while asleep. `SELF_PING_MINUTES` (default 10) makes the
app request its own `/health/live` on that interval, which returns a static response and touches neither
the database nor Redis, so it costs no database quota.

- The ping must reach the **public** URL; a loopback request never reaches the host's router and would not
  count as activity. On Render, `RENDER_EXTERNAL_URL` is picked up automatically; elsewhere set
  `SELF_PING_URL=https://your-api-host`. Leave both unset to disable (the default).
- It registers only when a URL resolves, and runs once at startup and then on the interval.
- A failed ping is logged (`self_ping_failed`) and never interrupts the other jobs.
- **Free-tier hours:** staying awake all month uses roughly 730 instance-hours. Render's free web-service
  allowance is 750 hours per month *across the account*, so one always-awake service consumes nearly all
  of it. A paid Starter instance avoids both the sleeping and the quota.

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
