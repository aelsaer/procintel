#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
mkdir -p "$BACKUP_DIR"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$BACKUP_DIR/procintel-$timestamp.dump"
pg_dump --format=custom --no-owner --file="$target" "$DATABASE_URL"
sha256sum "$target" > "$target.sha256"
find "$BACKUP_DIR" -type f -name 'procintel-*.dump*' -mtime "+$RETENTION_DAYS" -delete
printf 'backup=%s\n' "$target"
