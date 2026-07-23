#!/usr/bin/env bash
# Applies db/migrations/*.sql in filename order, then db/marts/*.sql, then
# db/seeds/*.sql (reference-data inserts — safe to re-run, seeds use
# ON CONFLICT DO NOTHING). Requires DATABASE_URL (see
# infra/docker/.env.example) and a running `psql` client. Stops on the
# first error.
set -euo pipefail

if [ -z "${DATABASE_URL:-}" ]; then
    echo "DATABASE_URL is not set. Example:" >&2
    echo '  export DATABASE_URL=postgresql://procintel:procintel@localhost:5432/procintel' >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "== migrations =="
for f in "$SCRIPT_DIR"/migrations/*.sql; do
    echo "-- applying $(basename "$f")"
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
done

echo "== marts =="
for f in "$SCRIPT_DIR"/marts/*.sql; do
    echo "-- applying $(basename "$f")"
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
done

echo "== seeds =="
for f in "$SCRIPT_DIR"/seeds/*.sql; do
    echo "-- applying $(basename "$f")"
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
done

echo "done."
