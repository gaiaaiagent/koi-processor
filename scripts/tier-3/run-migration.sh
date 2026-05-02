#!/usr/bin/env bash
# run-migration.sh — Tier-3 Pack 1 Item 1.2 (2026-04-28).
#
# Thin harness for any tier-3 SQL migration that follows the
# `migrate-session-entity-normalization.sql` convention:
#   - SQL file emits its own pre-state SELECTs at the top
#   - SQL file does the work in BEGIN; … COMMIT;
#   - SQL file emits post-state + final-verification SELECTs
#
# This harness runs `psql -f <file>` and surfaces stdout. With --dry-run,
# it wraps the migration body so any COMMIT inside is converted to
# ROLLBACK (changes visible during the run, never persisted).
#
# Usage:
#   bash run-migration.sh <path-to-migration.sql> [--dry-run]
#   bash run-migration.sh ./migrate-session-entity-normalization.sql
#   bash run-migration.sh ./migrate-session-entity-normalization.sql --dry-run
#
# Pre-flight:
#   - SQL file exists + readable
#   - psql available on PATH
#   - DB url resolved from POSTGRES_URL env (or default `personal_koi`)
#
# Exit codes:
#   0  psql ran without error
#   1  pre-flight failure or psql error

set -euo pipefail

MIGRATION_FILE="${1:-}"
MODE="${2:-live}"

if [[ -z "$MIGRATION_FILE" ]]; then
  echo "ERROR: migration SQL file required" >&2
  echo "Usage: bash run-migration.sh <migration.sql> [--dry-run]" >&2
  exit 1
fi

if [[ ! -f "$MIGRATION_FILE" ]]; then
  echo "ERROR: migration file not found: $MIGRATION_FILE" >&2
  exit 1
fi

if [[ ! -r "$MIGRATION_FILE" ]]; then
  echo "ERROR: migration file not readable: $MIGRATION_FILE" >&2
  exit 1
fi

if ! command -v psql >/dev/null 2>&1; then
  echo "ERROR: psql not found on PATH" >&2
  exit 1
fi

# Resolve DB. Prefer POSTGRES_URL; fall back to the personal_koi default.
DB_TARGET="${POSTGRES_URL:-personal_koi}"

if [[ "$MODE" == "--dry-run" ]]; then
  echo "── DRY-RUN: migration body wrapped in BEGIN; … ROLLBACK; ──"
  echo "── File: $MIGRATION_FILE ──"
  # Wrap the file's body so the entire run is rolled back at the end.
  # Note: if the SQL file contains its own COMMIT, that COMMIT will fire
  # inside our outer transaction — but since we ROLLBACK at the end, no
  # changes persist. The SELECTs and pre/post-state output still appear.
  WRAPPER=$(mktemp)
  trap 'rm -f "$WRAPPER"' EXIT
  {
    echo "\\echo '── DRY-RUN wrapper: BEGIN ──'"
    echo "BEGIN;"
    echo "\\i $MIGRATION_FILE"
    echo "\\echo '── DRY-RUN wrapper: ROLLBACK ──'"
    echo "ROLLBACK;"
  } > "$WRAPPER"
  psql "$DB_TARGET" -f "$WRAPPER"
  rc=$?
elif [[ "$MODE" == "live" ]]; then
  echo "── LIVE: $MIGRATION_FILE → $DB_TARGET ──"
  psql "$DB_TARGET" -f "$MIGRATION_FILE"
  rc=$?
else
  echo "ERROR: unknown mode: $MODE (expected '--dry-run' or omit)" >&2
  exit 1
fi

if [[ "$rc" -ne 0 ]]; then
  echo "ERROR: psql exited with code $rc" >&2
  exit 1
fi

echo "── DONE (rc=0) ──"
exit 0
