#!/usr/bin/env bash
# Encrypted off-host copy of ~/Documents/koi-source-archive.
#
# THE CONSTRAINT THIS RESPECTS. That repo's README line 4 says: "Local-only git
# repo -- no remote, never pushed. External/copyrighted material stays here."
# That is a rule about what MATERIAL may leave this machine, not a preference
# about durability, so "push it to another machine for safety" does not satisfy
# it -- a push puts readable copyrighted content on a second host. On 2026-09-12
# a plain push to the NUC was started here and reversed for exactly that reason.
#
# THE PROBLEM IT STILL LEAVES. The archive, the database, and the database dumps
# all live on /dev/disk3s5, and `tmutil destinationinfo` says "No destinations
# configured". One disk loss takes all three. The archive is the durable history
# of every external source ingested -- KOI's pipeline is content-addressed, so a
# changed page mints a new document and the OLD bytes exist nowhere else.
#
# HOW BOTH HOLD AT ONCE. What leaves is ciphertext. `git bundle --all` collapses
# the whole repo to one file; that file is encrypted to a dedicated key before it
# touches the network. The clause is satisfied in substance: no readable external
# material rests on another host.
#
# WHY PUBLIC-KEY AND NOT A PASSPHRASE. A symmetric job must hold its secret on
# disk to encrypt, so compromising this laptop yields the key. Encrypting to a
# public key means this script holds NO secret -- the private key can live only
# where the ciphertext does not (the NUC, and the operator's password manager),
# so neither host alone can read the archive.
#
# Restore: fetch the .gpg, `gpg -d` with the private key, `git clone` the bundle.
set -euo pipefail

REPO="${KOI_ARCHIVE_REPO:-$HOME/Documents/koi-source-archive}"
RECIPIENT="${KOI_ARCHIVE_GPG_KEY:-F5EE933A8DC407E4}"
HOST="${KOI_OFFSITE_HOST:-gaia}"
DIR="${KOI_ARCHIVE_OFFSITE_DIR:-koi-archive-offsite}"
KEEP="${KOI_ARCHIVE_KEEP:-4}"
MARKER="${KOI_ARCHIVE_MARKER:-$HOME/koi-backups/.last-archive-offsite}"

log() { echo "[$(date '+%F %T')] ARCHIVE-OFFSITE: $*"; }
fail() { log "FAIL: $*"; exit 1; }

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

[ -d "$REPO/.git" ] || fail "no git repo at $REPO"
gpg --list-keys "$RECIPIENT" >/dev/null 2>&1 || fail "no gpg key $RECIPIENT -- cannot encrypt, refusing to send cleartext"

HEAD_SHA="$(bounded 120 git -C "$REPO" rev-parse HEAD)" || fail "git rev-parse timed out or failed (is the disk starved?)"
COMMITS="$(bounded 120 git -C "$REPO" rev-list --count --all)" || fail "git rev-list timed out or failed"

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=30 -o ServerAliveCountMax=3)

# Skip when nothing has changed -- but ASK THE REMOTE, do not infer it.
#
# The first version compared the marker's HEAD to `git rev-parse HEAD` and
# exited 0 if they matched. Both operands were local, so the "skip" was a
# statement about this laptop only: delete the bundle on gaia and this script
# would report "up to date" forever and never send another one. The marker is
# evidence of what happened once, not of what is currently there.
if [ -f "$MARKER" ]; then
  M_HEAD="$(awk '{print $2}' "$MARKER")"
  M_NAME="$(awk '{print $5}' "$MARKER")"
  M_SIZE="$(awk '{print $6}' "$MARKER")"
  if [ "$M_HEAD" = "$HEAD_SHA" ] && [ -n "$M_NAME" ] && [ -n "$M_SIZE" ]; then
    R_SIZE="$(bounded 60 ssh "${SSH_OPTS[@]}" "$HOST" "stat -c %s ${DIR}/${M_NAME} 2>/dev/null" || true)"
    if [ "$R_SIZE" = "$M_SIZE" ]; then
      log "up to date (HEAD ${HEAD_SHA:0:12}; ${HOST}:${DIR}/${M_NAME} present at ${M_SIZE} bytes)"
      exit 0
    fi
    log "HEAD unchanged but the off-host copy is gone or wrong size (remote='${R_SIZE:-absent}' expected=${M_SIZE}); re-sending"
  fi
fi

STAMP="$(date +%Y%m%d-%H%M%S)"

# WORK LIVES AT A KNOWN PATH, not in mktemp's /var/folders, so an orphan is
# FINDABLE and gets swept by the next run.
#
# The previous comment here claimed the cleartext bundle "must not survive this
# script under any exit path, including a failure or an interrupt". That was
# false. bash does not run a trap while a foreground child is executing, and
# `launchctl print` shows these jobs have exit timeout = 5 -- so a logout or
# restart during the ~156s `git bundle create` gets SIGKILLed before the trap
# can fire, and a ~464MB cleartext bundle is left behind. The machine really did
# restart mid-backup on 2026-09-11, and koi_backup.sh's trap likewise never ran.
#
# On the confidentiality of that orphan, stated plainly rather than alarmingly:
# it is mode-700 under $HOME on the same disk as ~/Documents/koi-source-archive
# itself, so anyone who can read it can already read the archive directly. The
# cost is 464MB nobody accounts for, not a new disclosure. The off-host property
# -- that what leaves this machine is ciphertext -- is unaffected either way.
WORKROOT="${KOI_ARCHIVE_WORKROOT:-${HOME}/koi-backups}"
mkdir -p "$WORKROOT"

# Sweep orphans from previous runs BEFORE making a new one, since the trap is
# not guaranteed to have run. A directory whose PID is no longer alive is dead.
for d in "$WORKROOT"/.archive-work.*; do
  [ -d "$d" ] || continue
  opid="${d##*.}"
  case "$opid" in (*[!0-9]*|"") continue ;; esac
  if ! kill -0 "$opid" 2>/dev/null; then
    log "sweeping orphaned work dir from a killed run: $(basename "$d") ($(du -sh "$d" 2>/dev/null | cut -f1))"
    rm -rf "$d"
  fi
done

WORK="${WORKROOT}/.archive-work.$$"
rm -rf "$WORK"; mkdir -p "$WORK"; chmod 700 "$WORK"
# Best effort, and it does cover the ordinary paths (success, `fail`, Ctrl-C
# between children). It cannot cover SIGKILL; the sweep above is what covers that.
trap 'rm -rf "$WORK"' EXIT INT TERM HUP

BUNDLE="$WORK/koi-source-archive-${STAMP}.bundle"
ENC="$WORK/koi-source-archive-${STAMP}.bundle.gpg"

log "bundling ${COMMITS} commits from ${REPO} (HEAD ${HEAD_SHA:0:12})"
bounded 1800 git -C "$REPO" bundle create "$BUNDLE" --all 2>&1 | sed 's/^/    /' || fail "git bundle create timed out (30m) or failed"
git -C "$REPO" bundle verify "$BUNDLE" >/dev/null 2>&1 || fail "git bundle verify rejected the bundle we just made"

log "encrypting to ${RECIPIENT}"
gpg --batch --yes --trust-model always --recipient "$RECIPIENT" --output "$ENC" --encrypt "$BUNDLE" \
  || fail "gpg encrypt failed"
# REFUSE TO SEND ANYTHING THAT IS NOT ACTUALLY ENCRYPTED.
#
# This is the only runtime check standing between the cleartext archive and the
# network, and the first version of it could not fail. It was:
#     file "$ENC" | grep -qi "PGP\|GPG\|encrypted"
# `file` without -b prints "<path>: <description>", and $ENC is ALWAYS named
# ...bundle.gpg, so grep -i matched the literal "gpg" IN THE FILENAME whatever
# the bytes were. Demonstrated end to end: with a gpg stub whose --encrypt was
# `cp`, the script passed this guard, transferred a plain `git bundle`,
# checksum-verified the cleartext against itself, logged "OK ... verified" and
# exited 0. A clone of what landed recovered the repo with no key at all.
#
# So the check is now a PAIR, each half of which can fail on its own:
#   1. file -b (brief -- no filename) must report PGP data;
#   2. git bundle verify must REJECT it. A cleartext bundle is a VALID bundle,
#      so this half fails loudly in exactly the case that matters, and it does
#      not depend on file(1)'s magic database being right about PGP.
# Agreeing means the bytes are unreadable as a repository and identify as PGP.
FILETYPE="$(file -b "$ENC")"
case "$FILETYPE" in
  PGP*) : ;;
  *) fail "$(basename "$ENC") is not PGP data (file says: ${FILETYPE}); refusing to transfer" ;;
esac
if git -C "$REPO" bundle verify "$ENC" >/dev/null 2>&1; then
  fail "$(basename "$ENC") is still a readable git bundle after encryption; refusing to transfer"
fi
# 3. And it must be encrypted TO THE INTENDED KEY. file(1) and bundle-verify
#    together prove "PGP data that is not a repository"; neither proves the
#    recipient. gpg reads the pubkey-enc packet without needing any secret key,
#    so this is cheap and it is the only check that would notice the bundle
#    being encrypted to somebody else's key. Only the header is read.
PKT="$(head -c 2000000 "$ENC" | gpg --list-packets 2>&1 | head -5 || true)"
case "$PKT" in
  *"$RECIPIENT"*) : ;;
  *) fail "$(basename "$ENC") is not encrypted to ${RECIPIENT} (packets said: $(echo "$PKT" | head -1)); refusing to transfer" ;;
esac
rm -f "$BUNDLE"

BASE="$(basename "$ENC")"
# Stage under .inprogress and promote by rename only after the checksum passes,
# for the same reason koi_offsite_copy.sh does. An interrupted transfer here was
# proven to leave a partial under the FINAL name, where it matched the retention
# glob, counted toward KEEP, and caused the oldest GOOD bundle to be pruned in
# its favour -- i.e. an interruption did not merely fail, it destroyed a backup.
REMOTE_TMP="${BASE}.inprogress"
log "copying ${BASE} ($(du -h "$ENC" | cut -f1)) -> ${HOST}:${DIR}/"
ssh "${SSH_OPTS[@]}" "$HOST" "mkdir -p ${DIR}" || fail "cannot reach ${HOST}"
rsync -a --partial --inplace -e "ssh ${SSH_OPTS[*]}" "$ENC" "${HOST}:${DIR}/${REMOTE_TMP}" \
  || fail "rsync returned non-zero; ${REMOTE_TMP} kept on ${HOST} so a retry resumes"

# Same discipline as koi_offsite_copy.sh: rsync exiting 0 is not evidence the
# bytes arrived. Compare checksums.
LOCAL_SHA="$(shasum -a 256 "$ENC" | awk '{print $1}')"
REMOTE_SHA="$(ssh "${SSH_OPTS[@]}" "$HOST" "sha256sum ${DIR}/${REMOTE_TMP} 2>/dev/null | awk '{print \$1}'" || true)"
[ -n "$REMOTE_SHA" ] || fail "no checksum from ${HOST}"
if [ "$LOCAL_SHA" != "$REMOTE_SHA" ]; then
  ssh "${SSH_OPTS[@]}" "$HOST" "rm -f ${DIR}/${REMOTE_TMP}" || true
  fail "checksum mismatch; removed the bad remote copy"
fi
ssh "${SSH_OPTS[@]}" "$HOST" "mv -f ${DIR}/${REMOTE_TMP} ${DIR}/${BASE}" \
  || fail "could not promote ${REMOTE_TMP} to ${BASE}"

# Marker carries the remote FILENAME and SIZE so the skip above can ask the
# remote whether that exact object is still there.
printf '%s %s %s %s %s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$HEAD_SHA" "$COMMITS" "$LOCAL_SHA" \
  "$BASE" "$(wc -c < "$ENC" | tr -d ' ')" > "$MARKER"
log "OK ${HOST}:${DIR}/${BASE} sha256 ${LOCAL_SHA:0:16} verified (${COMMITS} commits)"

ssh "${SSH_OPTS[@]}" "$HOST" \
  "find ${DIR} -maxdepth 1 -name '*.bundle.gpg.inprogress' -mtime +2 -delete 2>/dev/null" || true

PRUNED="$(ssh "${SSH_OPTS[@]}" "$HOST" \
  "cd ${DIR} && ls -1 koi-source-archive-*.bundle.gpg 2>/dev/null | sort | head -n -${KEEP} | xargs -r -n1 sh -c 'rm -f \"\$0\" && echo x' | wc -l" || echo 0)"
KEPT="$(ssh "${SSH_OPTS[@]}" "$HOST" "ls -1 ${DIR}/koi-source-archive-*.bundle.gpg 2>/dev/null | wc -l" || echo '?')"
log "DONE pruned=${PRUNED} kept=${KEPT}"
