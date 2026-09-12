#!/usr/bin/env bash
# Copy ONE verified dump off this machine and prove it arrived intact.
#
# Split out of koi_backup.sh (2026-09-12) so it can be tested against a small
# file without running a two-hour pg_dump. koi_backup.sh calls it as the last
# step, after the local dump is verified and retention has run.
#
# WHY THIS EXISTS. Everything in koi_backup.sh protects against LOGICAL loss --
# a bad DELETE, a dropped table -- and nothing against DISK loss. `tmutil
# destinationinfo` returns "No destinations configured", so before this the
# 115 GB of dumps and the database they came from lived on exactly one disk.
# A sole-copy incident is on record from 2026-08-05, and on 2026-09-12 six
# commits were found existing only on the NUC's disk for the same reason.
#
# THROUGHPUT, measured 2026-09-12 with a 300MB incompressible file: 11 MB/s,
# so a 12.5GB dump takes roughly 18 minutes. The nightly job starts at 03:15,
# so the off-host copy finishes well before morning. If a future dump grows
# past ~40GB, or the link degrades, re-measure before assuming that still holds.
#
# WHY gaia AND NOT THE NUC: the NUC has 18 GB free of 98 GB (82% used) against
# 12.5 GB per dump -- it would hold one with no headroom. gaia has 602 GB.
#
# THE VERIFICATION IS THE POINT. rsync exiting 0 does not mean the bytes are
# there: it can exit 0 on a truncated source, and an interrupted stream leaves
# a partial that `ls` cannot distinguish from a backup. gaia has no pg_restore,
# so the integrity check koi_backup.sh runs locally cannot run there -- a
# checksum is what is available, and it is stronger than a size comparison.
# The rule this encodes: verify the WORK happened, not that the command exited 0.
set -euo pipefail

DUMP="${1:?usage: koi_offsite_copy.sh <dump-path>}"
[ -f "$DUMP" ] || { echo "FAIL: no such dump: $DUMP" >&2; exit 2; }

HOST="${KOI_OFFSITE_HOST:-gaia}"
DIR="${KOI_OFFSITE_DIR:-koi-offsite}"
KEEP="${KOI_OFFSITE_KEEP:-7}"
MARKER="${KOI_OFFSITE_MARKER:-$(dirname "$DUMP")/.last-offhost-sync}"
BASE="$(basename "$DUMP")"

log() { echo "[$(date '+%F %T')] OFFSITE: $*"; }
fail() { log "FAIL: $*"; log "the local dump at ${DUMP} is intact; only the off-host copy failed"; exit 1; }

# BatchMode: launchd has no terminal, so a key wanting a passphrase must fail
# immediately and loudly rather than hang the job until the next run.
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=30)

log "copying ${BASE} -> ${HOST}:${DIR}/"
ssh "${SSH_OPTS[@]}" "$HOST" "mkdir -p ${DIR}" || fail "cannot reach ${HOST} or create ${DIR}"

# --partial leaves a resumable remainder; the checksum below rejects it, so a
# partial is never mistaken for a backup.
rsync -a --partial --inplace -e "ssh ${SSH_OPTS[*]}" "$DUMP" "${HOST}:${DIR}/" \
  || fail "rsync returned non-zero"

LOCAL_SHA="$(shasum -a 256 "$DUMP" | awk '{print $1}')"
REMOTE_SHA="$(ssh "${SSH_OPTS[@]}" "$HOST" "sha256sum ${DIR}/${BASE} 2>/dev/null | awk '{print \$1}'" || true)"

[ -n "$REMOTE_SHA" ] || fail "no checksum from ${HOST} (file absent after a successful-looking transfer)"
if [ "$LOCAL_SHA" != "$REMOTE_SHA" ]; then
  ssh "${SSH_OPTS[@]}" "$HOST" "rm -f ${DIR}/${BASE}" || true
  fail "checksum mismatch (local ${LOCAL_SHA:0:16} remote ${REMOTE_SHA:0:16}); removed the bad remote copy"
fi
log "OK ${HOST}:${DIR}/${BASE} sha256 ${LOCAL_SHA:0:16} verified"

# Durable answer to "when did a dump last leave this machine" -- one `cat`,
# no log grepping, and a staleness check becomes trivial. Space-free ISO-8601 so
# the three fields stay unambiguous to awk (a '+%F %T' stamp splits into two).
printf '%s %s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$BASE" "$LOCAL_SHA" > "$MARKER"

# Remote retention: keep the newest N. Filename stamps sort chronologically.
PRUNED="$(ssh "${SSH_OPTS[@]}" "$HOST" \
  "cd ${DIR} && ls -1 personal_koi-*.dump 2>/dev/null | sort | head -n -${KEEP} | xargs -r -n1 sh -c 'rm -f \"\$0\" && echo \"\$0\"' | wc -l" || echo 0)"
KEPT="$(ssh "${SSH_OPTS[@]}" "$HOST" "ls -1 ${DIR}/personal_koi-*.dump 2>/dev/null | wc -l" || echo '?')"
FREE="$(ssh "${SSH_OPTS[@]}" "$HOST" "df -Ph ${DIR} | awk 'NR==2{print \$4}'" || echo '?')"
log "DONE pruned=${PRUNED} kept=${KEPT} free=${FREE} on ${HOST}"
