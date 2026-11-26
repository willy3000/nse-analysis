# Deploying to Railway

Written for whoever operates the deployment. It assumes the database and the API are both moving to
Railway — the arrangement this app wants, because every query then stays on Railway's private network
instead of crossing the public internet. See [operations.md](operations.md) for how the app behaves once it
is running (pooling, retries, the first-run data load, backups, monitoring).

**Three** services in one Railway project, all in the **same region**:

| Service | Image | Command | Public domain |
|---|---|---|---|
| `postgres` | Railway Postgres | — | no |
| `api` | `infrastructure/docker/api.Dockerfile` | image default (migrate, then serve) | optional |
| `web` | `infrastructure/docker/web.Dockerfile` | image default | **yes** |

The schedule runs inside the API (`RUN_SCHEDULER_IN_API=true`), not in a separate `worker`. On Railway a
worker is a second billable service, and for a single-user deployment the in-process scheduler is the
cheaper, simpler default - the connection budget that made it awkward on a small Render Postgres is gone.
`worker.json` is kept ready for when splitting is worth it: more than one API replica, an analytics replay
over a minute, or backtests users wait on (see [operations.md](operations.md), "When to stop sharing the
API process"). Flipping the flag and deploying that config is the whole migration - no code changes.

Config-as-code lives in [infrastructure/railway/](../../infrastructure/railway/): point each service's
**Settings → Config as code** at `infrastructure/railway/api.json`, `worker.json` or `web.json`. That file
carries the Dockerfile path, the start command, the healthcheck and the restart policy, so those are not
re-entered by hand per service.

### Console settings

Every service builds from the **repo root**. The Dockerfiles copy by repo-relative path
(`COPY services/api/...`, `COPY apps/web ...`), so a service whose Root Directory points at a subfolder
fails to build.

| Setting | `api` | `web` | `worker`, if ever added |
|---|---|---|---|
| Root Directory | *empty* (`/`) | *empty* (`/`) | *empty* (`/`) |
| Config as code | `infrastructure/railway/api.json` | `infrastructure/railway/web.json` | `infrastructure/railway/worker.json` |
| Build command | *empty* (Dockerfile builder) | *empty* | *empty* |
| Start command | *empty* (image default) | *empty* (image default) | from the config file |
| Healthcheck path | `/health/live` (config file) | `/` (config file) | none |
| Public domain | optional | **yes** | no |
| Target port | 8000 | 3000 | — |
| Watch paths | `services/api/**`, `infrastructure/docker/api.Dockerfile` | `apps/web/**`, `infrastructure/docker/web.Dockerfile` | same as `api` |

Root Directory must stay at the repo root. Railway's Root Directory is the Docker build context, and
pointing it at `services/api` makes every `COPY services/api/...` resolve to `services/api/services/api/...`
and fail. A service that will not build for want of a directory usually needs its *builder* set instead:
with no Dockerfile at the repo root, Railway falls back to Nixpacks and cannot plan a monorepo. The
config-as-code file pins `"builder": "DOCKERFILE"` and the Dockerfile path, which is what stops the
guessing.

Watch paths are optional but stop a web-only change from rebuilding and restarting the API.

## 1. Database

Add a Postgres service, then **give it memory before loading anything**. The Render outage was a 256 MB
instance with `shared_buffers` at 64 MB restarting every 7–11 minutes under the analytics workload; the
loader survives that now, but every restart still costs an in-flight instrument. 1–2 GB is the point of
this move.

Railway exposes two hostnames. Use the private one everywhere in the app:

- `postgres.railway.internal:5432` — private, free, what `DATABASE_URL` must use.
- `<name>.proxy.rlwy.net:<port>` — public TCP proxy, **billed as network egress**, and its certificate does
  not cover that hostname so `sslmode=verify-full` cannot work through it. Use it only for one-off dumps
  and restores, and prefer `railway connect postgres --tunnel-only` even for those.

Timescale is not needed: migration `0002` creates hypertables only when the extension is installable and is
otherwise a no-op, with the composite B-tree indexes carrying the load (ADR-0006). Vanilla Railway Postgres
is a supported target.

## 2. Variables

On `api`:

```
APP_ENV=production
DATABASE_URL=postgresql+psycopg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.RAILWAY_PRIVATE_DOMAIN}}:5432/${{Postgres.PGDATABASE}}
SESSION_SECRET=<python -c "import secrets;print(secrets.token_urlsafe(48))">
COOKIE_SECURE=true
CORS_ORIGINS=["https://<web public domain>"]
MYSTOCKS_API_KEY=<key>
MYSTOCKS_ENV=production          # sandbox until KYB is approved
ENABLE_SYNTHETIC_PROVIDER=false
ALLOW_REGISTRATION=true          # set false once your account exists
BIND_HOST=::
PORT=8000
RUN_SCHEDULER_IN_API=true
BOOTSTRAP_ON_START=true
```

If you later split the worker out, everything above except `BIND_HOST`, `PORT`, `RUN_SCHEDULER_IN_API` and
`BOOTSTRAP_ON_START` belongs on both services - a Railway shared variable group is the tidy way, since the
two must not drift. The first-run load always runs in the API process (`main.py` starts it on its own
thread whenever `BOOTSTRAP_ON_START` is true, independently of the scheduler flag), so a worker would take
`BOOTSTRAP_ON_START=false`.

A Railway variable *reference* keeps the password out of the variable list, and reaches the private host
rather than the proxy. `Postgres.` is the database **service's name** as the sidebar shows it, case
included - rename the service and these references must follow. A bare `${{PGUSER}}` resolves only inside
the Postgres service itself; from `api` or `worker` it silently yields an empty user and host, so the
namespace is not optional. Railway's variable picker autocompletes the correct form.

Referencing `${{Postgres.DATABASE_URL}}` directly would not work: the
`postgresql+psycopg://` prefix is **required** — a bare `postgresql://` URL routes SQLAlchemy to psycopg2,
which is not installed here, and the app will not start — and Railway's own `DATABASE_URL` points at the
public proxy. Composing it from the parts above fixes both.

`BIND_HOST=::` is not cosmetic. Railway's private network resolves `*.railway.internal` over IPv6 (and, in
environments created after 16 October 2025, IPv4 as well), so a process bound only to `0.0.0.0` receives no
private traffic at all and `web` cannot reach `api`. `::` covers both stacks on a dual-stack container.

`RUN_SCHEDULER_IN_API=true` is what makes the three-service layout work: the quote poll, end-of-day
analytics, alerts, backtests and `catchup` run on one background thread in the API. Run that flag *or* a
`worker`, not both - advisory locks make a double-run safe, but there is no reason to pay for it.

`web` only:

```
API_ORIGIN=http://api.railway.internal:8000
PORT=3000
```

This one is consumed at **build** time: Next.js bakes `rewrites()` into the routes manifest, so the value
must be present before the web image is built, and a later change needs a redeploy, not a restart. Railway
makes service variables available as Docker build arguments, and `web.Dockerfile` already declares
`ARG API_ORIGIN`. Verify after the first deploy by loading a page that fetches data; if the dashboard is
empty and the web logs show connections to `http://api:8000`, the build did not pick the variable up — set
it and redeploy rather than restart.

Self-ping (`SELF_PING_URL`) stays empty: Railway does not sleep instances by default. `RAILWAY_PUBLIC_DOMAIN`
is read automatically if you ever do want it.

## 3. Moving the data

Market data can be re-downloaded, but **portfolios, watchlists, strategies and alerts cannot** — take the
dump before tearing anything down.

```bash
# From Render's external database URL
DATABASE_URL='postgresql://...render.com/...' bash scripts/backup.sh

# Into Railway, through a tunnel rather than the billed public proxy
railway connect postgres --tunnel-only          # in another terminal; note the local port
DATABASE_URL='postgresql+psycopg://postgres:<password>@127.0.0.1:<local port>/railway' \
  bash scripts/backup.sh restore .localdata/backups/nse-<timestamp>.dump
```

`scripts/backup.sh` strips the `+psycopg` prefix for `pg_dump`/`pg_restore` itself, so either form of the
URL works. The restore is destructive and asks for confirmation first.

Alternatively, skip the dump and let the app refill itself: `api` runs migrations on start, and the
first-run load plus the `catchup` job will fetch instruments, history, corporate actions and analytics from
the provider. That loses only user data, which is exactly the part worth keeping — so dump first anyway.

## 4. Order of operations

1. Deploy `postgres` and size it.
2. Restore the dump (or skip, per above).
3. Deploy `api`. Its entrypoint runs `alembic upgrade head` with bounded retries before binding the port, so
   a database that is briefly unreachable at deploy time no longer aborts the deploy.
   Jobs do not start until one `SELECT 1` has succeeded, so a slow database delays the schedule rather
   than filling the log with tracebacks.
4. Deploy `web` with `API_ORIGIN` already set, and give it the public domain.
5. Create your account, then set `ALLOW_REGISTRATION=false` on `api` and redeploy.

## 5. Verify

- `GET /health/ready` on the API — `checks.database.ok`, the migration version, and
  `checks.scheduler.waiting_for_database`.
- `GET /api/v1/data/runs` for job history, `GET /api/v1/data/health` for freshness,
  `GET /api/v1/data/gaps` for anything the loader still owes.
- The web domain in a browser: the dashboard renders only if the private hop to `api` works, which is the
  one thing this layout can get wrong.

## Pooling, now that the database is not the bottleneck

Defaults stay 5 + 5 per process, so the single `api` service holds 10 connections against a Railway
Postgres that allows roughly 100. There is room to raise `DB_POOL_SIZE` and `DB_MAX_OVERFLOW` to 10 + 10
once the database has the RAM to match - and the jobs share this pool with request handlers, which is the
main cost of not running a separate worker. The budget is
`(DB_POOL_SIZE + DB_MAX_OVERFLOW) × processes × replicas`, and it must stay under `max_connections` with
room for `psql` and migrations — an over-sized pool is a cause of outage, not a cushion.

`BACKTEST_POLL_SECONDS` (default 5) is the heaviest source of connection churn, roughly 17k checkouts a
day. On the private network that is cheap; it was only expensive when every checkout crossed the internet.

## Cost notes

- Private networking is free. The public TCP proxy is billed egress, which is why `DATABASE_URL` uses the
  internal host.
- A `web` on Vercel instead of Railway is also fine, but then the browser's proxy hop leaves Railway: give
  `api` a public domain, set `API_ORIGIN` to it, and put that domain in `CORS_ORIGINS`.
