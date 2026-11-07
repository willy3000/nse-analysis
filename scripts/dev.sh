#!/usr/bin/env bash
# Start the full local stack: embedded Postgres, migrations, API (:8000), worker, web (:3000).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/services/api/.venv/Scripts/python"; [ -x "$PY" ] || PY="$ROOT/services/api/.venv/bin/python"
export DATABASE_URL="$("$PY" "$ROOT/scripts/dev_postgres.py")"
cd "$ROOT/services/api"
"$PY" -m alembic upgrade head
"$PY" -m uvicorn nse_platform.main:app --port 8000 > "$ROOT/.localdata/api.log" 2>&1 &
"$PY" -m nse_platform.worker.main > "$ROOT/.localdata/worker.log" 2>&1 &
cd "$ROOT/apps/web" && npx next dev -p 3000
