#!/usr/bin/env bash
# Nightly driver: back up EVERY database that matters, not just personal_koi.
#
# WHY THIS EXISTS. koi_backup.sh is a careful single-database engine -- integrity
# check, size guard, retention, verified off-host copy -- and it was pointed at
# exactly one database. A 2026-09-12 review found four others with real content
# and no backup of any kind: eliza, salishsee_public, indigenomics_koi and
# infinite_regen. Together ~320MB, about 0.4% of one personal_koi dump, i.e. the
# gap was never about cost. They were simply never added.
#
# The engine stays single-database and this drives it once per entry, so the
# guards that protect the 12.5GB dump are the same ones protecting the 8MB one
# and there is no second implementation to drift.
#
# FLOOR, per database, is the cold-start backstop only: once a database has one
# good dump, koi_backup.sh uses 70% of the previous size, which is a far better
# truncation detector than any constant. The floors here are deliberately
# generous-downward -- they exist to catch a 0-byte or obviously-truncated dump
# on the very first run, not to assert an expected size.
set -uo pipefail

DEST="${KOI_BACKUP_DEST:-${HOME}/koi-backups}"
LOG="${DEST}/backup.log"
HERE="$(cd "$(dirname "$0")" && pwd)"

# name:floor_bytes   -- floor is ~25% of the current dump size, rounded down hard
# Floors set from MEASURED dump sizes on 2026-09-12, at roughly 25-30% of the
# observed dump -- not from pg_database_size, which counts catalog overhead and
# free pages. Estimating from it put infinite_regen's floor at 500000 when the
# dump is 7559 bytes, and the guard correctly refused the backup. The database
# turned out to be EMPTY: 3 tables, 0 rows, so the dump is pure schema. Kept in
# the set anyway -- 7KB costs nothing and it is covered the day someone fills it.
#
#   db                 dump observed    floor
#   personal_koi        12.5 GB          4 GB
#   eliza               34 MB            10 MB
#   salishsee_public    32 MB             8 MB
#   indigenomics_koi    21 MB             6 MB
#   infinite_regen      7.5 KB (schema)   4 KB
DATABASES="${KOI_BACKUP_DATABASES:-\
personal_koi:4294967296 \
eliza:10000000 \
salishsee_public:8000000 \
indigenomics_koi:6000000 \
infinite_regen:4000}"

log() { echo "[$(date '+%F %T')] ALL: $*" | tee -a "$LOG"; }

log "START multi-database backup"
failed=""; ok=""
for entry in $DATABASES; do
  db="${entry%%:*}"; floor="${entry##*:}"
  # Skip cleanly rather than fail the whole night if a database was dropped.
  if ! psql -lqt 2>/dev/null | cut -d'|' -f1 | tr -d ' ' | grep -qxF "$db"; then
    log "SKIP ${db}: no such database"
    continue
  fi
  log "--- ${db} (floor ${floor} bytes) ---"
  if KOI_BACKUP_DB="$db" KOI_BACKUP_FLOOR="$floor" "${HERE}/koi_backup.sh"; then
    ok="${ok} ${db}"
  else
    rc=$?
    log "FAILED ${db} (exit ${rc})"
    failed="${failed} ${db}"
  fi
done

if [ -n "$failed" ]; then
  log "DONE with FAILURES -- ok:${ok:- none} failed:${failed}"
  exit 1
fi
log "DONE -- all succeeded:${ok}"
