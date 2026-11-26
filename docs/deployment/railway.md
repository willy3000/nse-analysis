# Deploying to Railway

Written for whoever operates the deployment. It assumes the database and the API are both moving to
Railway — the arrangement this app wants, because every query then stays on Railway's private network
instead of crossing the public internet. See [operations.md](operations.md) for how the app behaves once it
is running (pooling, retries, the first-run data load, backups, monitoring).

**Two** services in one Railway project, in the **same region**. The frontend is deployed separately on
Netlify, so Railway hosts the backend only:

| Service | Image | Command | Public domain |
|---|---|---|---|
| `postgres` | Railway Postgres | — | no |
| `api` | `infrastructure/docker/api.Dockerfile` | image default (migrate, then serve) | **yes** |

The API's public domain is **required** in this layout, not optional: Netlify's Next server is what proxies
the browser's `/api` calls, and it reaches the API over the public internet. `web.json` stays in the repo
for the alternative where the frontend moves to Railway too, and is unused as things stand.

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

| Setting | `api` | `worker`, if ever added |
|---|---|---|
| Root Directory | *empty* (`/`) | *empty* (`/`) |
| Config as code | `infrastructure/railway/api.json` | `infrastructure/railway/worker.json` |
| Build command | *empty* (Dockerfile builder) | *empty* |
| Start command | *empty* (image default) | from the config file |
| Healthcheck path | `/health/live` (config file) | none |
| Public domain | **yes**, target port 8000 | no |
| Watch paths | `services/api/**`, `infrastructure/docker/api.Dockerfile` | same as `api` |

**Root Directory must stay at the repo root, and this is where Railway differs from Netlify.** Netlify
builds the frontend with its own Node toolchain, so pointing its base directory at `apps/web` is right
there. Railway builds a *Docker image*, and Root Directory is the build context: the Dockerfile copies
`services/api/pyproject.toml` and the rest by repo-relative path, so a context of `services/api` makes
those resolve to `services/api/services/api/...` and the build fails with `not found`. The same instinct
that is correct on Netlify breaks the build here. A service that will not build for want of a directory usually needs its *builder* set instead:
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
CORS_ORIGINS=["https://<your-site>.netlify.app"]
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

`CORS_ORIGINS` takes the Netlify domain as a literal. A `${{web.RAILWAY_PUBLIC_DOMAIN}}` reference cannot
work here - there is no `web` service on Railway to reference - and the proxied calls arrive from Netlify's
server rather than the browser, so CORS is barely exercised; it is set correctly for any direct call.

A Railway variable *reference* keeps the password out of the variable list, and reaches the private host
rather than the proxy. `Postgres.` is the database **service's name** as the sidebar shows it, case
included - rename the service and these references must follow. A bare `${{PGUSER}}` resolves only inside
the Postgres service itself; from `api` or `worker` it silently yields an empty user and host, so the
namespace is not optional. Railway's variable picker autocompletes the correct form.

Referencing `${{Postgres.DATABASE_URL}}` directly would not work: the
`postgresql+psycopg://` prefix is **required** — a bare `postgresql://` URL routes SQLAlchemy to psycopg2,
which is not installed here, and the app will not start — and Railway's own `DATABASE_URL` points at the
public proxy. Composing it from the parts above fixes both.

`BIND_HOST=::` is Railway's documented value for a Python service and covers both stacks on a dual-stack
container, which is why it stays. It is load-bearing only for *inbound private* traffic: Railway resolves
`*.railway.internal` over IPv6 (and, in environments created after 16 October 2025, IPv4 as well), so a
service that another Railway service calls privately must not bind `0.0.0.0` alone. With the frontend on
Netlify nothing on Railway calls the API privately, so the `0.0.0.0` default would also serve public
ingress - keep `::` anyway, and it is the first thing to flip if public requests ever fail to arrive.
The API's own outbound connections to Postgres use the private network regardless of what it binds.

`RUN_SCHEDULER_IN_API=true` is what makes the three-service layout work: the quote poll, end-of-day
analytics, alerts, backtests and `catchup` run on one background thread in the API. Run that flag *or* a
`worker`, not both - advisory locks make a double-run safe, but there is no reason to pay for it.

### On Netlify, not Railway

The frontend needs one variable, set in **Netlify's** environment:

```
API_ORIGIN=https://<api public domain>.up.railway.app
```

It is consumed at **build** time — Next.js bakes `rewrites()` into its routes manifest — so changing it
requires a Netlify **rebuild**, not a restart or a cache-cleared redeploy of the same build.

That proxy is not a convenience, it is what makes authentication work. The session and CSRF cookies are set
`httponly`, `samesite=lax` and without a `domain`, so they are host-only cookies for whichever origin the
browser sees. With the Next rewrite in place the browser only ever talks to the Netlify origin, the proxy
forwards `Set-Cookie` from the API, and the cookies attach to that origin. If the frontend were changed to
call the Railway domain directly from the browser, those calls become cross-site, `samesite=lax` stops
sending the session cookie, and logins break - fixing that properly means `SameSite=None`, credentialed
CORS and revisiting CSRF, which is a code change and a security decision, not configuration (ADR-0005).

This also requires that Netlify is running the Next server, rather than serving a static export - rewrites
to an external origin need the server to be there to perform them.

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
4. Generate the API's public domain (target port 8000), put it in Netlify's `API_ORIGIN`, and trigger a
   Netlify rebuild so the new value is baked in.
5. Create your account, then set `ALLOW_REGISTRATION=false` on `api` and redeploy.

## 5. Verify

- `GET /health/ready` on the API — `checks.database.ok`, the migration version, and
  `checks.scheduler.waiting_for_database`.
- `GET /api/v1/data/runs` for job history, `GET /api/v1/data/health` for freshness,
  `GET /api/v1/data/gaps` for anything the loader still owes.
- `https://<api public domain>/health/live` directly: a Railway 404 means no service owns that hostname, a
  502 means the domain is registered but nothing is serving HTTP behind it.
- The Netlify domain in a browser, then log in. The dashboard renders only if Netlify's `API_ORIGIN` points
  at the live Railway domain, and logging in succeeds only if the calls go through Netlify's proxy rather
  than straight to Railway - the two things this layout can get wrong.

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
- With the frontend on Netlify (or Vercel), the browser's proxy hop leaves Railway and arrives at the API's
  public domain. That traffic is ordinary public ingress, not the billed database proxy, and the API's own
  queries still stay on the private network - which is the hop that mattered.
