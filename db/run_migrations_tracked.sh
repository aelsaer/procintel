#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 <<'SQL'
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
SQL

applied_count="$(psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -tAc \
  "SELECT COUNT(*) FROM schema_migrations")"
has_legacy_schema="$(psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -tAc \
  "SELECT CASE WHEN to_regclass('public.procurement_acts') IS NULL THEN 0 ELSE 1 END")"

if [[ "$applied_count" == "0" && "$has_legacy_schema" == "1" ]]; then
    legacy_baseline_complete="$(psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -tAc \
      "SELECT CASE WHEN
          to_regclass('public.business_profiles') IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM information_schema.columns
              WHERE table_schema='public' AND table_name='procurement_acts'
                AND column_name='source_details'
          )
          AND to_regprocedure('public.procintel_taxonomy_match(uuid,text,text[],text[],boolean)') IS NOT NULL
        THEN 1 ELSE 0 END")"
    if [[ "$legacy_baseline_complete" != "1" ]]; then
        printf >&2 '%s\n' \
          "Existing schema is not at the verified migration-24 baseline." \
          "Apply the missing legacy SQL files individually, then rerun this command."
        exit 1
    fi

    for file in "$SCRIPT_DIR"/migrations/*.sql; do
        filename="$(basename "$file")"
        migration_number="${filename%%_*}"
        if (( 10#$migration_number > 24 )); then
            continue
        fi
        psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -c \
          "INSERT INTO schema_migrations(filename) VALUES ('$filename')"
        printf 'migration=%s status=adopted_existing\n' "$filename"
    done
fi

for file in "$SCRIPT_DIR"/migrations/*.sql; do
    filename="$(basename "$file")"
    applied="$(psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -tAc \
      "SELECT 1 FROM schema_migrations WHERE filename = '$filename'")"
    if [[ "$applied" == "1" ]]; then
        printf 'migration=%s status=already_applied\n' "$filename"
        continue
    fi
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 --single-transaction \
      -f "$file" \
      -c "INSERT INTO schema_migrations(filename) VALUES ('$filename')"
    printf 'migration=%s status=applied\n' "$filename"
done

for file in "$SCRIPT_DIR"/marts/*.sql "$SCRIPT_DIR"/seeds/*.sql; do
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 --single-transaction -f "$file"
done
