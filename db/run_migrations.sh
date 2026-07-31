#!/usr/bin/env bash
# Backward-compatible entrypoint; the tracked runner is safe to rerun.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/run_migrations_tracked.sh"
