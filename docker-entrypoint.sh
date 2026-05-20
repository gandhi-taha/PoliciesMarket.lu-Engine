#!/usr/bin/env bash
# Engine container entrypoint.
#
# Waits for Postgres, runs migrations + seed (both idempotent), then
# execs into the command passed as arguments (uvicorn in production,
# anything else in dev / tests).

set -euo pipefail

DB_HOST="${POSTGRES_HOST:-db}"
DB_PORT="${POSTGRES_PORT:-5432}"
DB_USER="${POSTGRES_USER:-postgres}"

echo "[entrypoint] waiting for ${DB_HOST}:${DB_PORT}..."
RETRIES=30
until pg_isready -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" >/dev/null 2>&1; do
  RETRIES=$((RETRIES - 1))
  if [ "${RETRIES}" -le 0 ]; then
    echo "[entrypoint] gave up waiting for ${DB_HOST}:${DB_PORT}" >&2
    exit 1
  fi
  sleep 2
done
echo "[entrypoint] db is reachable, running migrations + seed..."

alembic upgrade head
python -m app.db.seed
echo "[entrypoint] startup tasks complete, handing off to: $*"

exec "$@"
