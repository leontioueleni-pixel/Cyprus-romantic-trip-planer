#!/bin/sh
set -eu

export SQLITE_DB_PATH="${SQLITE_DB_PATH:-/app/data/planner_cloud.sqlite3}"

mkdir -p "$(dirname "$SQLITE_DB_PATH")"

python scripts/init_sqlite.py

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
