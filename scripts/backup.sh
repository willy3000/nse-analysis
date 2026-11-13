#!/usr/bin/env bash
# Backup / restore the database in pg_dump custom format.
#   bash scripts/backup.sh                 -> .localdata/backups/nse-<timestamp>.dump
#   bash scripts/backup.sh restore FILE    -> restore FILE into $DATABASE_URL (destructive, asks first)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
URL="${DATABASE_URL:-$(cat "$ROOT/.localdata/pg_url.txt" 2>/dev/null || true)}"
[ -n "$URL" ] || { echo "Set DATABASE_URL"; exit 1; }
URL="${URL/postgresql+psycopg:/postgresql:}"
# Prefer system tools; fall back to the binaries bundled with the embedded dev Postgres.
BIN="$(dirname "$(command -v pg_dump 2>/dev/null || echo "$ROOT/services/api/.venv/Lib/site-packages/pgserver/pginstall/bin/pg_dump")")"
if [ "${1:-}" = "restore" ]; then
  FILE="${2:?usage: backup.sh restore FILE}"
  read -r -p "This REPLACES the contents of the target database. Type 'restore' to continue: " ok
  [ "$ok" = "restore" ] || { echo "aborted"; exit 1; }
  "$BIN/pg_restore" --clean --if-exists --no-owner -d "$URL" "$FILE"
  echo "restored $FILE"
else
  mkdir -p "$ROOT/.localdata/backups"
  OUT="$ROOT/.localdata/backups/nse-$(date +%Y%m%d-%H%M).dump"
  "$BIN/pg_dump" -Fc --no-owner -f "$OUT" "$URL"
  echo "backup written: $OUT ($(du -h "$OUT" | cut -f1))"
fi
