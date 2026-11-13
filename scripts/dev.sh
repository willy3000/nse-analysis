#!/usr/bin/env bash
# Local stack without Docker: embedded Postgres + migrations + API (:8000) + worker + web (:3000).
#   bash scripts/dev.sh          start everything (Ctrl+C stops the web app; API/worker keep running)
#   bash scripts/dev.sh stop     stop API, worker and Postgres
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/services/api/.venv/Scripts/python"; [ -x "$PY" ] || PY="$ROOT/services/api/.venv/bin/python"
PIDS="$ROOT/.localdata/pids"
mkdir -p "$ROOT/.localdata"

kill_procs() {
  if [ -f "$PIDS" ]; then while read -r p; do kill "$p" 2>/dev/null || true; done < "$PIDS"; rm -f "$PIDS"; fi
}
if [ "${1:-}" = "stop" ]; then kill_procs; "$PY" "$ROOT/scripts/dev_postgres.py" --stop || true; exit 0; fi

kill_procs  # stale API/worker from a previous run
# Postgres may need time for crash recovery after an unclean shutdown: retry.
for i in 1 2 3 4 5 6; do
  if URL="$("$PY" "$ROOT/scripts/dev_postgres.py" 2>/dev/null | tail -1)" && [ -n "$URL" ]; then break; fi
  echo "waiting for Postgres to start/recover ($i)..."; sleep 15
done
export DATABASE_URL="$URL"
cd "$ROOT/services/api"
"$PY" -m alembic upgrade head
"$PY" -m uvicorn nse_platform.main:app --port 8000 > "$ROOT/.localdata/api.log" 2>&1 & echo $! >> "$PIDS"
"$PY" -m nse_platform.worker.main > "$ROOT/.localdata/worker.log" 2>&1 & echo $! >> "$PIDS"
echo "API :8000 and worker started (logs in .localdata/). Starting web on http://localhost:3000 ..."
cd "$ROOT/apps/web" && npx next dev -p 3000
