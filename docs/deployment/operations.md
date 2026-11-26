# Deployment & operations

Host-specific runbook: **[railway.md](railway.md)** (the current target — four services, private
networking, moving the data across). This page is the host-neutral half: topology, pooling, the data load,
backups and monitoring.

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
Hosts that charge for background workers, or offer only a web service, can run the schedule inside the API
instead. This is the deployed default on Railway, where a worker is a second billable service:
set `RUN_SCHEDULER_IN_API=true` on the API service. The same jobs then run on one background thread in the
API process.

- The API instance must stay awake. On a host that sleeps idle instances (Render's free web service), the
  schedule stops while it sleeps.
- A long analytics run shares the process with request handling, so responses can slow for a few minutes.
- Extra API replicas are safe: each job takes a Postgres advisory lock, so replicas skip rather than duplicate.
- Confirm it is live at `GET /health/ready` -> `checks.scheduler`, which lists each job's next run time.
- Do not also run `nse-worker` unless you want the redundancy; locks make it safe either way.

**When to stop sharing the API process.** `RUN_SCHEDULER_IN_API=true` is a cost concession, not the better
topology, and it has real costs: the jobs and the request handlers share one connection pool, so a backfill
competes with requests for connections; a CPU-bound analytics replay slows responses for minutes; the whole
schedule dies with the web process (and stops entirely while a free instance sleeps); and job failures and
request failures land in the same log stream. Separate `nse-worker` as soon as the host allows a background
process — in particular once any of these is true: more than one API replica, a full analytics replay that
takes longer than a minute, backtests that users wait on, or a paid Postgres plan where the connection
budget no longer forces one process. Nothing in the code needs to change: the same `register_jobs` schedule
and the same advisory locks apply, so flipping the flag off and starting the worker is the whole migration.

### Database connectivity, pooling and retries
The pool is **per process** and shared by request handlers, the scheduler thread and the bootstrap thread.
Managed plans cap *total* connections low — Render's smallest Postgres allows roughly 22 across every client
— so the budget is `(DB_POOL_SIZE + DB_MAX_OVERFLOW) x processes x replicas`, and it must stay under that
cap with room for `psql` and migrations. Defaults are 5 + 5, i.e. 10 per process.

An over-sized pool is a cause of outage, not a cushion: once the server hits `max_connections` it refuses
or terminates connections, which surfaces in the log as `SSL error: unexpected eof while reading` rather
than as anything that names connection limits.

- `pool_pre_ping` validates a connection on checkout; `DB_POOL_RECYCLE` (300 s) retires it before the host
  drops it while idle. Neither helps a connection already held open by a running job — hence the TCP
  keepalives in `core/db.py`.
- `DB_CONNECT_TIMEOUT` bounds connect and DNS; without it a resolution failure can block a thread.
- `BIND_HOST` (default `0.0.0.0`) and `PORT` (default 8080) set the API's listening interface and port.
  Hosts that inject `PORT` override the default; `BIND_HOST=::` is required on a private network that
  resolves over IPv6.
- `with_db_retry()` retries a whole unit of work on transient connectivity failures (DNS, TLS teardown,
  dropped connections) with bounded exponential backoff and jitter, then re-raises. Query, data and
  integrity errors are never retried.
- Background jobs run only once one `SELECT 1` has succeeded (`core/db.DatabaseGate`). While the database is
  unreachable, jobs log a single `job_skipped_db_unavailable` line and re-probe at most every
  `DB_GATE_RECHECK_SECONDS` instead of reconnecting — and logging a traceback — on every tick.
- `GET /health/ready` reports `checks.database.jobs_gate_open` (and `jobs_gate_error`) plus
  `checks.scheduler.waiting_for_database`. The gate is only populated once a job or the scheduler starter
  has looked, so `false` means "not asked yet" as well as "unreachable" — read it with `checks.database.ok`,
  which is this endpoint's own live probe.
- `alembic upgrade head` in the entrypoint retries with backoff (`MIGRATION_MAX_ATTEMPTS`, default 6), so a
  database that is not reachable in the first seconds of a deploy no longer aborts it.

The **backtest queue polls every `BACKTEST_POLL_SECONDS` (default 5)**, which is the heaviest source of
connection churn in the process — roughly 17k checkouts a day, and it was what filled the log during the
outage. Raise it if the database is remote and backtest latency does not matter.

If the database host **fails to resolve** (`failed to resolve host 'dpg-...-a': Name or service not known`),
that is infrastructure, not application code, and no retry policy will fix it. On Render the `dpg-xxxx-a`
short name is the *internal* hostname, which resolves only from a service in the same account **and
region** while the database is live. Check, in order: that the database is not suspended, expired or
deleted (free Postgres expires); that the web service and the database are in the same region; and that
`DATABASE_URL` uses the internal hostname only from inside Render — otherwise use the external
`dpg-xxxx-a.<region>-postgres.render.com` form, which resolves publicly and requires TLS.

On Railway the equivalent failure is `postgres.railway.internal` not resolving, which means the client is
not on that project's private network (a local shell, or another provider) — use
`railway connect postgres --tunnel-only` from outside, not the billed public proxy. A *connection* that
hangs rather than failing to resolve is usually the IPv6 one: private DNS answers with an IPv6 address, so
the API must bind `::` (`BIND_HOST=::`) rather than `0.0.0.0` or nothing reaches it.

### Loading data on a host with no shell access
The first data load — instruments, history, corporate actions, analytics — runs automatically on a
background thread just after start-up. It is **on by default** (`BOOTSTRAP_ON_START`) and needs no other
setting; set it false to load the data yourself instead.

- It deliberately does **not** run in the container's start command. A host that waits for the port to be
  bound (Render does) fails the deploy if a multi-year backfill runs before the web server starts.
- It is inert unless `MYSTOCKS_API_KEY` is set **and** no non-synthetic price bar exists, so redeploys do
  not reload. To force a reload, clear `price_bars` first.
- It is not an APScheduler job, so it runs whether the schedule lives in this process or in a worker.
- Confirm it started at `GET /health/ready` -> `checks.bootstrap.running`.
- `BOOTSTRAP_BACKFILL_YEARS` (default 3) bounds the history. The longest indicator lookback is ~120
  sessions, so 3 years is ample; 10 years is a much larger download and will not fit free-tier storage.
- Each step is logged and guarded: one failing step does not stop the others. Watch progress at
  `GET /api/v1/data/runs`, and the result at `GET /api/v1/data/health`.
- It holds the single job thread while it runs, so the first quote poll may be skipped. The schedule resumes
  normally afterwards.

### Finishing a load on a database that restarts
A small managed Postgres can restart every few minutes under load (a 256 MB Render instance running this
analytics workload was observed restarting every 7-11 minutes, with `shared_buffers` at 64 MB). An
all-or-nothing backfill cannot finish in that window, so the loader is built to make partial progress and
resume:

- **`catchup`** (every `CATCHUP_POLL_MINUTES`, default 15) asks which instruments still have no price bars
  and which have bars but no 1d analytics snapshot, and loads only those. Passes never redo completed work,
  so the gap shrinks monotonically. Once the universe is complete it is one cheap query.
- `ingest_history` recovers **per instrument**: a dropped connection waits for the server to return and
  then continues with the next instrument, instead of aborting the job. Previously one restart ended the
  pass and every instrument after it was never fetched - 66 instruments synced, 2 loaded.
- `GET /api/v1/data/gaps` shows exactly what is missing. Overview inner-joins `instrument_snapshots`, so an
  instrument without a snapshot does not appear there at all, however many bars it has.
- `POST /api/v1/admin/data/catchup` (admin) runs a pass now instead of waiting for the schedule. It returns
  immediately and takes the same advisory lock, so it cannot duplicate a scheduled run.

If restarts are frequent, the real fix is a larger database plan: the loader now survives them, but every
restart still costs an in-flight instrument and a few seconds of waiting.

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
| Web | The web container (Railway), or Vercel with `API_ORIGIN` set to the public API URL at build time |
| API + worker | Two services from the one image (Railway), or a small VPS (1 vCPU / 2 GB) running `docker compose` |
| Database | Managed Postgres with at least 1-2 GB of memory for this workload — Railway Postgres alongside the app services, Timescale Cloud, Neon, RDS, or the compose `db` service with backups |

Keep the app and its database on the same provider and in the same region. Splitting them means every
query crosses the public internet: on Railway specifically that is billed egress through a TCP proxy whose
certificate cannot be verified, and the backtest poll alone checks out a connection roughly 17k times a
day. See [railway.md](railway.md).

Production deploy (compose on a VPS behind a TLS reverse proxy such as Caddy or Nginx):

1. `cp .env.example .env` and set:
   - `APP_ENV=production`
   - a strong `SESSION_SECRET` and `POSTGRES_PASSWORD`
   - `COOKIE_SECURE=true`
   - `CORS_ORIGINS=["https://your-domain"]`
   - `MYSTOCKS_API_KEY`, and `MYSTOCKS_ENV=production` once your KYB is approved
   - `ENABLE_SYNTHETIC_PROVIDER=false`
2. `docker compose up -d --build`. Migrations run automatically through the `migrate` service.
3. Put HTTPS in front of port 3000 (web) only. Keep 8080 and 5432 private; compose binds them to 127.0.0.1.
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
