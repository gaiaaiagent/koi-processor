#!/usr/bin/env bash
# Nightly backup of the laptop's personal_koi (27GB primary, previously unbacked).
#
# Adapted from the NUC's ~/bin/koi-backup.sh (created there 2026-07-14 after an
# over-broad DELETE destroyed live rows and the most recent dump turned out to
# be 6 weeks stale). Same shape: compressed custom-format dump, integrity check
# via pg_restore --list (catches a 0-byte/corrupt dump — "fail loudly" per the
# 2026-08-31 pipeline hardening audit), retention (7 daily + Sunday kept 28d).
#
# personal-koi-pipeline-hardening-audit-2026-08-31.md, Phase 0 item 2.
set -euo pipefail

DB="personal_koi"
DEST="${HOME}/koi-backups"
LOG="${DEST}/backup.log"
STAMP="$(date +%Y%m%d-%H%M%S)"
DOW="$(date +%u)"          # 7 = Sunday
OUT="${DEST}/personal_koi-${STAMP}.dump"

# Homebrew Postgres binaries aren't on launchd's default PATH.
export PATH="/opt/homebrew/bin:${PATH}"

mkdir -p "$DEST"
log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

log "START backup ${DB} -> ${OUT}"

# Guard: need room for roughly the DB size (dump is smaller, but be safe).
AVAIL_MB=$(df -Pm "$DEST" | awk 'NR==2{print $4}')
if [ "$AVAIL_MB" -lt 8000 ]; then
  log "ABORT: only ${AVAIL_MB} MB free in $DEST (need >= 8000)"
  exit 1
fi

# -Fc = custom format (compressed, restorable selectively with pg_restore)
if ! pg_dump -Fc "$DB" -f "$OUT"; then
  log "FAIL: pg_dump errored"
  rm -f "$OUT"
  exit 1
fi

# Integrity check: a dump we can't list is a dump we can't restore. This is
# what makes a 0-byte or truncated dump fail loudly instead of sitting on
# disk looking like a backup until the day it's needed.
if ! pg_restore --list "$OUT" > /dev/null 2>&1; then
  log "FAIL: dump failed pg_restore --list integrity check; removing"
  rm -f "$OUT"
  exit 1
fi

# Minimum-size guard: pg_restore --list only reads the TOC, so a dump
# truncated mid-write (disk full partway through, killed process) can still
# list cleanly while missing most of its data. 500MB is well under the
# smallest healthy dump we've seen (~1GB+ compressed on a 27GB DB) and well
# above a truncated stub.
MIN_BYTES=$((500 * 1024 * 1024))
ACTUAL_BYTES=$(stat -f%z "$OUT")
if [ "$ACTUAL_BYTES" -lt "$MIN_BYTES" ]; then
  log "FAIL: dump is ${ACTUAL_BYTES} bytes, below ${MIN_BYTES}-byte minimum; removing"
  rm -f "$OUT"
  exit 1
fi

SIZE=$(du -h "$OUT" | cut -f1)
log "OK: ${OUT} (${SIZE}) verified"

# Mark Sunday dumps so retention can keep them longer.
if [ "$DOW" = "7" ]; then
  touch "${OUT}.weekly"
  log "tagged weekly: ${OUT}.weekly"
fi

# Retention:
#   - non-weekly dumps older than 7 days  -> delete
#   - weekly dumps older than 28 days     -> delete
PRUNED=0
while IFS= read -r -d '' f; do
  if [ -e "${f}.weekly" ]; then
    # weekly: keep 28 days
    if [ -n "$(find "$f" -mtime +28 -print -quit)" ]; then
      rm -f "$f" "${f}.weekly"; log "pruned (weekly>28d): $(basename "$f")"; PRUNED=$((PRUNED+1))
    fi
  else
    if [ -n "$(find "$f" -mtime +7 -print -quit)" ]; then
      rm -f "$f"; log "pruned (daily>7d): $(basename "$f")"; PRUNED=$((PRUNED+1))
    fi
  fi
done < <(find "$DEST" -maxdepth 1 -name 'personal_koi-*.dump' -print0)

KEPT=$(find "$DEST" -maxdepth 1 -name 'personal_koi-*.dump' | wc -l | tr -d ' ')
log "DONE: pruned=${PRUNED} kept=${KEPT} free=$(df -Ph "$DEST" | awk 'NR==2{print $4}')"
