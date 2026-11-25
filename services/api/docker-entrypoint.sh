#!/bin/sh
# Migrate, then hand the process straight to the web server.
#
# The first data load deliberately does NOT run here. A host that waits for the port to be bound fails the
# deploy while a multi-year backfill runs. Set BOOTSTRAP_ON_START=true (with RUN_SCHEDULER_IN_API=true) and
# the load runs on the scheduler thread shortly after start-up, with the API already serving.
set -e

echo "Running migrations..."
python -m alembic upgrade head

echo "Starting API..."
exec python -m uvicorn nse_platform.main:app --host 0.0.0.0 --port "${PORT:-10000}" \
  --proxy-headers --forwarded-allow-ips='*'
