#!/bin/sh
# Migrate, then hand the process straight to the web server.
#
# The first data load deliberately does NOT run here. A host that waits for the port to be bound fails the
# deploy while a multi-year backfill runs. Set BOOTSTRAP_ON_START=true (with RUN_SCHEDULER_IN_API=true) and
# the load runs on its own thread shortly after start-up, with the API already serving.
set -e

# `alembic upgrade head` is the first thing to touch the database, and a managed database is often not
# reachable the instant a deploy starts - a DNS entry that has not propagated, or an instance still waking.
# A single failure here aborts the whole deploy, so retry with bounded exponential backoff before giving up.
# This only retries the command; the migrations themselves are untouched and still run exactly once.
attempt=1
max_attempts="${MIGRATION_MAX_ATTEMPTS:-6}"
delay="${MIGRATION_RETRY_SECONDS:-2}"
while : ; do
  echo "Running migrations (attempt ${attempt}/${max_attempts})..."
  if python -m alembic upgrade head; then
    break
  fi
  if [ "${attempt}" -ge "${max_attempts}" ]; then
    echo "Migrations failed after ${max_attempts} attempts; aborting deploy." >&2
    exit 1
  fi
  echo "Migration attempt ${attempt} failed; retrying in ${delay}s." >&2
  sleep "${delay}"
  attempt=$((attempt + 1))
  delay=$((delay * 2))
  if [ "${delay}" -gt 60 ]; then delay=60; fi  # explicit `if`, so `set -e` cannot act on a false test
done

echo "Starting API..."
# BIND_HOST is the interface, not a preference. Railway's private network resolves `*.railway.internal` to
# an IPv6 address (and, outside legacy environments, an IPv4 one too), so a process bound only to 0.0.0.0
# never receives private traffic and `web` cannot reach `api` at all. Set BIND_HOST=:: there, which covers
# both stacks on a dual-stack container. 0.0.0.0 stays the default, for compose and for hosts that route
# over IPv4.
# PORT defaults to 8000 to match EXPOSE, the image healthcheck, docker-compose's published port and the web
# proxy's fallback. Render and Railway both inject their own PORT, which wins.
exec python -m uvicorn nse_platform.main:app --host "${BIND_HOST:-0.0.0.0}" --port "${PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips='*'
