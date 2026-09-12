#!/usr/bin/env bash
# Nightly backup of the laptop's personal_koi (27GB primary, previously unbacked).
#
# Adapted from the NUC's ~/bin/koi-backup.sh (created there 2026-07-14 after an
# over-broad DELETE destroyed live rows and the most recent dump turned out to
# be 6 weeks stale). Same shape: compressed custom-format dump, integrity check
# via pg_restore --list (catches a 0-byte/corrupt dump — "fail loudly" per the
# 2026-08-31 pipeline hardening audit), retention (7 daily + Sunday kept 28d),
# then a checksum-verified off-host copy (koi_offsite_copy.sh, added 2026-09-12).
#
# personal-koi-pipeline-hardening-audit-2026-08-31.md, Phase 0 item 2.
# pipefail is load-bearing, not decoration: the off-host step at the bottom
# pipes through `tee`, and WITHOUT pipefail a failed off-host copy reports
# tee's exit code instead of its own and the run looks clean. Verified by
# negative control on 2026-09-12 (pipefail off -> failure masked).
set -euo pipefail

DB="${KOI_BACKUP_DB:-personal_koi}"
DEST="${KOI_BACKUP_DEST:-${HOME}/koi-backups}"
LOG="${DEST}/backup.log"
# NOTE ON THE TWO TIMESTAMPS a dump carries, because they differ by hours and
# the difference looks like a contradiction to anyone who finds it later:
#   filename stamp  = when the dump STARTED   (this variable)
#   file mtime      = when the dump COMPLETED (last write by pg_dump)
# The 2026-08-31 dump is stamped 19:42:31 and has mtime 21:40:06 -- a ~2h run
# for 12.18GB, not a discrepancy. backup.log records both events explicitly;
# a directory listing shows only the second. When citing "when the backup was
# taken", say which event you mean.
STAMP="$(date +%Y%m%d-%H%M%S)"
DOW="$(date +%u)"          # 7 = Sunday
# Name the dump after the database it came from. It was hardcoded to the literal
# `personal_koi-` regardless of $DB, which meant a scratch-database run produced
# a file indistinguishable from a production dump -- and could be promoted into
# the production retention set. Deriving it from $DB closes that and lets this
# one engine back up more than one database.
OUT="${DEST}/${DB}-${STAMP}.dump"

# Homebrew Postgres binaries aren't on launchd's default PATH.
export PATH="/opt/homebrew/bin:${PATH}"

mkdir -p "$DEST"
log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# Refuse the production directory BEFORE announcing a START. A refused
# self-test used to log "START backup ..." into the PRODUCTION backup.log and
# then bail, leaving a START with no matching OK/FAIL -- which is exactly the
# signature koi_backup_check.sh treats as "a run died without reaching a guard".
# Test runs were manufacturing false staleness alarms in the real log.
if [ "${KOI_BACKUP_SELFTEST:-0}" = "1" ] && [ "$DEST" = "${HOME}/koi-backups" ]; then
  log "ABORT: KOI_BACKUP_SELFTEST=1 refuses the production backup directory ${DEST}; set KOI_BACKUP_DEST"
  exit 3
fi

log "START backup ${DB} -> ${OUT}"

# SELF-TEST MODE, checked here and not further down for a reason. Without it
# the script could only be exercised end-to-end by the nightly run itself -- a
# 2-hour, 12.5GB dump -- so changes to it were in practice verified by reading,
# with `bash -n` standing in for a test. It lowers the 4GB size floor (set near
# ACTUAL_BYTES below) so the whole path -- dump, integrity check, size guard,
# retention, off-host copy -- runs in seconds against a scratch database.
#
# It REFUSES the real database, which is the safety property: a stray
# KOI_BACKUP_SELFTEST cannot quietly lower the production floor, since doing so
# would also require having pointed $DB elsewhere.
#
# The refusal is FIRST because a guard placed after the dump is not a guard.
# Written here initially at the size-check, it would have had to pg_dump the
# whole production database -- the exact two-hour operation the mode exists to
# avoid -- before it could decline to run. Caught by trying to run the test.
SELFTEST=0
if [ "${KOI_BACKUP_SELFTEST:-0}" = "1" ]; then
  if [ "$DB" = "personal_koi" ]; then
    log "ABORT: KOI_BACKUP_SELFTEST=1 refused against the real database"
    exit 3
  fi
  SELFTEST=1
  # Scope the off-host destination the way the size floor is scoped. The dump
  # FILENAME is hardcoded to `personal_koi-` regardless of $DB, so a scratch run
  # produces a file named exactly like a production dump -- and without this it
  # went to the production `gaia:koi-offsite/`, passed its checksum (it is a
  # faithful copy of a scratch dump), was promoted to the production name, and
  # then COUNTED toward remote retention, where a ~400KB impostor can evict a
  # real 12.5GB backup. Defaulting these means a self-test cannot touch
  # production state even when the caller forgets to redirect it.
  : "${KOI_OFFSITE_DIR:=koi-offsite-selftest}"
  : "${KOI_OFFSITE_MARKER:=${DEST}/.last-offhost-sync}"
  export KOI_OFFSITE_DIR KOI_OFFSITE_MARKER
  log "SELFTEST: scratch DB ${DB} -> ${DEST}, off-host dir ${KOI_OFFSITE_DIR} (size floor will be lowered)"
fi


# Clean up after an interrupted run. Twice on 2026-08-31 a dump was killed
# mid-write (once by a harness background-task teardown, once unexplained),
# each time leaving a partial .dump on disk with no FAIL line in the log --
# i.e. a stub that looks exactly like a backup to anyone listing the
# directory, and that the guards below never got a chance to reject because
# the script died before reaching them. A partial is worse than no file:
# nothing downstream distinguishes it from a real dump until a restore fails.
# The exit status must reflect THE SIGNAL, not `$?`.
#
# This was `rc=$?; ...; exit $rc`. `$?` is the status of the last COMPLETED
# command, which is 0 whenever the signal lands just after something succeeded
# -- so the trap deleted the in-progress dump and then exited 0, and launchd's
# LastExitStatus, an `&&` chain, or `if koi_backup.sh; then` all recorded
# SUCCESS for a night that produced no backup. That is this project's own
# standard ("a command exiting 0 is not evidence the work happened") being
# violated by the first trap in the nightly job. The rc=0 path fired in
# production on 2026-09-12 (backup.log). Re-raising the signal gives the caller
# the conventional 128+N and cannot be mistaken for success.
_abort() {
  sig="$1"
  if [ -f "$OUT" ]; then
    rm -f "$OUT"; log "ABORTED (SIG${sig}): removed partial ${OUT}"
  else
    log "ABORTED (SIG${sig}): no partial to remove"
  fi
  trap - "$sig"
  kill -s "$sig" $$
}
trap '_abort INT' INT
trap '_abort TERM' TERM
trap '_abort HUP' HUP

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

# Minimum-size guard. pg_restore --list only reads the TOC, so a dump
# truncated mid-write (disk full partway, or the process killed -- which is
# exactly how the 2026-08-31 14:44 manual run died) still lists CLEANLY while
# missing most of its data. Verified against that dump: it passes --list.
#
# The threshold is derived from this machine's real dump history, not guessed.
# Complete personal_koi dumps here have been 8.6 GB (2026-05-13), 8.6 GB
# (2026-05-14) and 9.4 GB (2026-07-14). The dead partial was 1.29 GB -- 14% of
# a real dump. An earlier version of this guard used a flat 500 MB floor, which
# that partial would have passed; the floor was sized on the NUC's 2.6-2.8 GB
# dumps (smaller DB) rather than this host's.
#
# Primary check is RELATIVE to the newest previous good dump, so it tracks DB
# growth instead of going stale. ABS_FLOOR only covers the first-ever run.
# NOTE: deliberately uses `wc -c`, never `stat`. On this machine
# /usr/local/bin/stat SHADOWS /usr/bin/stat and silently emits NOTHING for
# every form tried (-f%z, -c%s, --version). A guard written with bare `stat`
# reads an empty string, the [ ] comparison errors, and under `set -e` a
# perfectly good dump reports as a failed run having never checked a size --
# i.e. the guard is inert while looking present. `wc -c` needs no such luck.
# Absolute floor, per database. 4GB is correct for personal_koi (< half the
# smallest real dump) and nonsense for a 62MB one, so the caller sets it. The
# 70%-of-previous rule below does the real work once a history exists; this is
# the cold-start backstop.
ABS_FLOOR="${KOI_BACKUP_FLOOR:-$((4 * 1024 * 1024 * 1024))}"
[ "$SELFTEST" = "1" ] && ABS_FLOOR=1024   # scratch DBs are kilobytes, not gigabytes

ACTUAL_BYTES=$(wc -c < "$OUT" | tr -d '[:space:]')

# Newest previous dump by mtime (ls -t), excluding the one just written.
# `|| true` is load-bearing: on the first run $OUT is the ONLY match, grep
# filters it out, exits 1, and `set -euo pipefail` would abort the script
# immediately after a perfectly good dump. Verified by test.
PREV_FILE=$(ls -t "$DEST"/"${DB}"-*.dump 2>/dev/null | grep -vxF "$OUT" | head -1 || true)
PREV=""
if [ -n "$PREV_FILE" ] && [ -f "$PREV_FILE" ]; then
  PREV=$(wc -c < "$PREV_FILE" | tr -d '[:space:]')
fi

if [ -n "$PREV" ] && [ "$PREV" -gt 0 ]; then
  # 70% of the last good dump: absorbs normal compression variance and modest
  # shrinkage, still catches any serious truncation.
  MIN_BYTES=$(( PREV * 70 / 100 ))
  [ "$MIN_BYTES" -lt "$ABS_FLOOR" ] && MIN_BYTES=$ABS_FLOOR
else
  MIN_BYTES=$ABS_FLOOR
fi

if [ "$ACTUAL_BYTES" -lt "$MIN_BYTES" ]; then
  log "FAIL: dump is ${ACTUAL_BYTES} bytes, below ${MIN_BYTES}-byte minimum (prev good: ${PREV:-none}); removing"
  rm -f "$OUT"
  exit 1
fi

SIZE=$(du -h "$OUT" | cut -f1)
log "OK: ${OUT} (${SIZE}) verified"

# DISARM the partial-cleanup trap. Past this line $OUT is a verified dump, not
# a partial, and the trap's rm would destroy a good backup rather than a stub.
# This matters more now than it did: everything below (retention, and the
# off-host copy) can run for minutes, and the off-host copy is network-bound,
# so the window in which an interrupt reaches an armed trap is no longer
# instantaneous. The trap's whole purpose ends where verification succeeds.
trap - INT TERM HUP

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
done < <(find "$DEST" -maxdepth 1 -name "${DB}-*.dump" -print0)

KEPT=$(find "$DEST" -maxdepth 1 -name "${DB}-*.dump" | wc -l | tr -d ' ')
log "DONE: pruned=${PRUNED} kept=${KEPT} free=$(df -Ph "$DEST" | awk 'NR==2{print $4}')"

# --- OFF-HOST COPY -----------------------------------------------------------
# Until 2026-09-12 every dump this script produced stayed on the same physical
# disk as the database it was dumping, so the whole 115GB protected against a
# bad DELETE and against nothing else. `tmutil destinationinfo` reports "No
# destinations configured", so there was no second copy anywhere.
#
# Runs LAST, deliberately: a network failure at this point cannot leave the
# local backup half-done, because the local backup is already finished,
# verified and pruned. It exits non-zero so a silent off-host failure is not
# possible -- but the local dump is kept either way, which is why this is the
# final step rather than an early one.
#
# Verification and the choice of host are argued in koi_offsite_copy.sh.
OFFSITE="$(dirname "$0")/koi_offsite_copy.sh"
if [ "${KOI_OFFSITE:-1}" = "0" ]; then
  log "OFFSITE: skipped (KOI_OFFSITE=0)"
elif [ ! -x "$OFFSITE" ]; then
  log "OFFSITE FAIL: ${OFFSITE} missing or not executable"
  exit 1
elif "$OFFSITE" "$OUT" 2>&1 | tee -a "$LOG"; then
  :
else
  log "OFFSITE FAIL: ${OUT} exists locally but has no off-host copy"
  exit 1
fi
