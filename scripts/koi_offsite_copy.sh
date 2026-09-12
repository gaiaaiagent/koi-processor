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
# THROUGHPUT. A 300MB probe gave 11 MB/s, but a real 12.5GB transfer sustained
# about 7 MB/s -- roughly 30 minutes, not the 18 the small probe implied. Short
# transfers are dominated by a fast start; size the window from the large number.
# Re-measure if a dump grows past ~40GB or the link changes.
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

# --- watchdog -------------------------------------------------------------
# macOS ships no timeout(1) and coreutils' gtimeout is not installed here.
# Without a wall-clock bound, a hung `git`, `ssh` or `rsync` blocks the nightly
# job forever: no output, no failure, no alarm -- it simply never returns, and
# the next launchd invocation finds it still sitting there.
#
# Not hypothetical. On 2026-09-12 `git rev-parse HEAD` -- which reads a 41-byte
# file -- hung for over two minutes on an I/O-starved machine (15-minute load
# average 33.75, StorageManagementService pinning two cores); plain `cat` on the
# same file hung too. A bound turns that into a loud failure instead of silence.
#
# perl is always present on macOS, and unlike a background-and-kill helper this
# works inside $( ) command substitution, which is where most of these calls
# live. On timeout the child dies of SIGALRM and the exit status is non-zero.
bounded() { perl -e 'alarm shift; exec @ARGV' "$@"; }

# BatchMode: launchd has no terminal, so a key wanting a passphrase must fail
# immediately and loudly rather than hang the job until the next run.
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=30)

log "copying ${BASE} -> ${HOST}:${DIR}/"
bounded 60 ssh "${SSH_OPTS[@]}" "$HOST" "mkdir -p ${DIR}" || fail "cannot reach ${HOST} or create ${DIR}"

# SWEEP ABANDONED PARTIALS FIRST, not at the end.
#
# Every failure path in this script calls fail(), which exits immediately -- so
# with the sweep at the bottom it ran only on nights that SUCCEEDED, i.e. never
# on the nights that create orphans. And orphans are not self-healing: each
# night's dump carries a new timestamp, so tomorrow cannot resume today's
# partial, it simply leaves it and starts a fresh 12.5GB push. At ~10GB an
# interrupted night and 591GB free, that is roughly 59 nights of silent
# accumulation whose first symptom is transfers failing for a new reason.
# Running it here means a run cleans up after its predecessors even if it is
# itself about to fail.
STALE="$(ssh "${SSH_OPTS[@]}" "$HOST" \
  "find ${DIR} -maxdepth 1 -name 'personal_koi-*.dump.inprogress' -mtime +2 -print -delete 2>/dev/null | wc -l" || echo 0)"
[ "${STALE:-0}" -gt 0 ] && log "swept ${STALE} abandoned .inprogress file(s) older than 2 days"

# TRANSFER UNDER A TEMPORARY NAME, and only rename after the checksum passes.
#
# The first real 12.5GB run proved why this is necessary. rsync was killed at
# 10.5GB (SIGKILL, ~25 minutes in). The script correctly failed and kept the
# local dump -- but it left 10.5GB on the far end under the dump's REAL name,
# and `ls -l` cannot tell that from a finished backup. Worse, the retention
# block below keeps the newest N files matching personal_koi-*.dump, so that
# partial would have been KEPT and could have pruned a genuine backup in its
# favour. An earlier comment here claimed "the checksum rejects it, so a partial
# is never mistaken for a backup"; that was true only within a single run, and
# false for every run afterwards, which is the case that matters.
#
# Under .inprogress a partial is structurally ineligible: retention never
# matches it, nothing counts it as a backup, and --partial --inplace means the
# next attempt RESUMES it rather than restarting from zero.
REMOTE_TMP="${BASE}.inprogress"

# Retry, because the failure that exposed this was transient. Each attempt
# resumes from whatever arrived last time.
ATTEMPTS="${KOI_OFFSITE_ATTEMPTS:-3}"
n=1
while : ; do
  if rsync -a --partial --inplace -e "ssh ${SSH_OPTS[*]}" "$DUMP" "${HOST}:${DIR}/${REMOTE_TMP}"; then
    break
  fi
  if [ "$n" -ge "$ATTEMPTS" ]; then
    fail "rsync failed on attempt ${n}/${ATTEMPTS}; ${DIR}/${REMOTE_TMP} kept on ${HOST} so a retry resumes"
  fi
  log "rsync attempt ${n}/${ATTEMPTS} failed; retrying in 30s (resuming from the partial)"
  sleep 30
  n=$((n+1))
done

LOCAL_SHA="$(shasum -a 256 "$DUMP" | awk '{print $1}')"
REMOTE_SHA="$(bounded 1800 ssh "${SSH_OPTS[@]}" "$HOST" "sha256sum ${DIR}/${REMOTE_TMP} 2>/dev/null | awk '{print \$1}'" || true)"

[ -n "$REMOTE_SHA" ] || fail "no checksum from ${HOST} (file absent after a successful-looking transfer)"
if [ "$LOCAL_SHA" != "$REMOTE_SHA" ]; then
  ssh "${SSH_OPTS[@]}" "$HOST" "rm -f ${DIR}/${REMOTE_TMP}" || true
  fail "checksum mismatch (local ${LOCAL_SHA:0:16} remote ${REMOTE_SHA:0:16}); removed the bad remote copy"
fi

# Only now does it become a backup. Rename is atomic within the directory, so
# the name personal_koi-*.dump never refers to unverified bytes.
ssh "${SSH_OPTS[@]}" "$HOST" "mv -f ${DIR}/${REMOTE_TMP} ${DIR}/${BASE}" \
  || fail "could not promote ${REMOTE_TMP} to ${BASE}"
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
