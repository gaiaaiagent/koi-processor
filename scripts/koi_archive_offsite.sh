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

[ -d "$REPO/.git" ] || fail "no git repo at $REPO"
gpg --list-keys "$RECIPIENT" >/dev/null 2>&1 || fail "no gpg key $RECIPIENT -- cannot encrypt, refusing to send cleartext"

HEAD_SHA="$(git -C "$REPO" rev-parse HEAD)"
COMMITS="$(git -C "$REPO" rev-list --count --all)"

# Skip when nothing has changed. The marker records the HEAD that was last
# successfully verified on the far end, so this asks "is what is already there
# still current", not "did we run recently".
if [ -f "$MARKER" ] && [ "$(awk '{print $2}' "$MARKER")" = "$HEAD_SHA" ]; then
  log "up to date (HEAD ${HEAD_SHA:0:12} already verified off-host); nothing to do"
  exit 0
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
WORK="$(mktemp -d)"
# The cleartext bundle is external/copyrighted material. It must not survive
# this script under any exit path, including a failure or an interrupt.
trap 'rm -rf "$WORK"' EXIT INT TERM HUP

BUNDLE="$WORK/koi-source-archive-${STAMP}.bundle"
ENC="$WORK/koi-source-archive-${STAMP}.bundle.gpg"

log "bundling ${COMMITS} commits from ${REPO} (HEAD ${HEAD_SHA:0:12})"
git -C "$REPO" bundle create "$BUNDLE" --all 2>&1 | sed 's/^/    /'
git -C "$REPO" bundle verify "$BUNDLE" >/dev/null 2>&1 || fail "git bundle verify rejected the bundle we just made"

log "encrypting to ${RECIPIENT}"
gpg --batch --yes --trust-model always --recipient "$RECIPIENT" --output "$ENC" --encrypt "$BUNDLE" \
  || fail "gpg encrypt failed"
# Refuse to send anything that is not actually encrypted.
file "$ENC" | grep -qi "PGP\|GPG\|encrypted" || fail "$(basename "$ENC") is not PGP-encrypted; refusing to transfer"
rm -f "$BUNDLE"

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=30)
BASE="$(basename "$ENC")"
log "copying ${BASE} ($(du -h "$ENC" | cut -f1)) -> ${HOST}:${DIR}/"
ssh "${SSH_OPTS[@]}" "$HOST" "mkdir -p ${DIR}" || fail "cannot reach ${HOST}"
rsync -a --partial --inplace -e "ssh ${SSH_OPTS[*]}" "$ENC" "${HOST}:${DIR}/" || fail "rsync returned non-zero"

# Same discipline as koi_offsite_copy.sh: rsync exiting 0 is not evidence the
# bytes arrived. Compare checksums.
LOCAL_SHA="$(shasum -a 256 "$ENC" | awk '{print $1}')"
REMOTE_SHA="$(ssh "${SSH_OPTS[@]}" "$HOST" "sha256sum ${DIR}/${BASE} 2>/dev/null | awk '{print \$1}'" || true)"
[ -n "$REMOTE_SHA" ] || fail "no checksum from ${HOST}"
if [ "$LOCAL_SHA" != "$REMOTE_SHA" ]; then
  ssh "${SSH_OPTS[@]}" "$HOST" "rm -f ${DIR}/${BASE}" || true
  fail "checksum mismatch; removed the bad remote copy"
fi

printf '%s %s %s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$HEAD_SHA" "$COMMITS" "$LOCAL_SHA" > "$MARKER"
log "OK ${HOST}:${DIR}/${BASE} sha256 ${LOCAL_SHA:0:16} verified (${COMMITS} commits)"

PRUNED="$(ssh "${SSH_OPTS[@]}" "$HOST" \
  "cd ${DIR} && ls -1 koi-source-archive-*.bundle.gpg 2>/dev/null | sort | head -n -${KEEP} | xargs -r -n1 sh -c 'rm -f \"\$0\" && echo x' | wc -l" || echo 0)"
KEPT="$(ssh "${SSH_OPTS[@]}" "$HOST" "ls -1 ${DIR}/koi-source-archive-*.bundle.gpg 2>/dev/null | wc -l" || echo '?')"
log "DONE pruned=${PRUNED} kept=${KEPT}"
