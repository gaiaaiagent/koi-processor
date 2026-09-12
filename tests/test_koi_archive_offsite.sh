#!/usr/bin/env bash
# Tests for scripts/koi_archive_offsite.sh.
#
# R1 is the one that matters and the only one that proves anything: fetch the
# ciphertext back from the remote host, decrypt it, clone it, and check that the
# commit count AND the actual file content survive the round trip. A backup that
# has never been restored is a hypothesis, not a backup.
#
# C1 is the contract check: what rests on the far end must not be readable. It
# greps the transferred bytes for a string that exists in the cleartext archive
# and requires zero hits, then proves the control works by finding that same
# string in the decrypted copy.
#
#   bash tests/test_koi_archive_offsite.sh     # needs gpg key + ssh to gaia
set -uo pipefail
S=~/projects/koi-processor-service/scripts/koi_archive_offsite.sh
# Unique remote dir per run (see the note in test_koi_offsite_copy.sh).
# Keep the marker override on its OWN line: a trailing comment on the export
# swallowed `KOI_ARCHIVE_MARKER=...`, so the suite both read an unset variable
# AND let the script write to the PRODUCTION marker at
# ~/koi-backups/.last-archive-offsite, pointing it at a selftest bundle in a
# directory the suite then deleted.
W=$(mktemp -d)
export KOI_ARCHIVE_OFFSITE_DIR="koi-archive-selftest-$$"
export KOI_ARCHIVE_MARKER="$W/.marker"
pass=0; fail=0
ck(){ if [ "$2" = "$3" ]; then echo "  PASS $1"; pass=$((pass+1)); else echo "  FAIL $1 (got '$2' want '$3')"; fail=$((fail+1)); fi; }
cleanup(){ ssh -o BatchMode=yes gaia "rm -rf $KOI_ARCHIVE_OFFSITE_DIR" 2>/dev/null; rm -rf "$W"; }
trap cleanup EXIT

REPO=~/Documents/koi-source-archive
WANT_COMMITS=$(git -C "$REPO" rev-list --count --all)
WANT_HEAD=$(git -C "$REPO" rev-parse HEAD)

echo "== B1: bundle, encrypt, transfer, verify =="
out=$(bash "$S" 2>&1); rc=$?
echo "$out" | sed 's/^/     /'
ck "exit 0" "$rc" "0"
ck "marker records the real HEAD" "$(awk '{print $2}' "$KOI_ARCHIVE_MARKER")" "$WANT_HEAD"
BASE=$(ssh -o BatchMode=yes gaia "ls -1 $KOI_ARCHIVE_OFFSITE_DIR/*.bundle.gpg 2>/dev/null | head -1")
ck "a bundle landed" "$([ -n "$BASE" ] && echo y || echo n)" "y"

echo "== B2: second run is a no-op while HEAD is unchanged =="
out=$(bash "$S" 2>&1); rc=$?
ck "exit 0" "$rc" "0"
ck "skipped as up to date" "$(echo "$out"|grep -c 'up to date')" "1"

echo "== C1: what rests off-host must NOT be readable =="
ssh -o BatchMode=yes gaia "cat $BASE" > "$W/fetched.gpg"
ck "fetched bytes match what we sent" "$(shasum -a 256 "$W/fetched.gpg"|awk '{print $1}')" "$(awk '{print $4}' "$KOI_ARCHIVE_MARKER")"
# The readability test is "is this a usable git bundle", NOT "does it contain a
# known string". A first version grepped the ciphertext for a phrase from the
# README and found none -- but a bundle stores objects zlib-compressed, so that
# phrase is not in the CLEARTEXT bundle either. The check would have passed with
# encryption switched off entirely: a control that could not fail.
#
# `git bundle verify` can fail, and does: it must REJECT what rests off-host and
# ACCEPT the same bytes after decryption. That pair is the whole property.
ck "off-host artifact is NOT a usable git bundle" \
   "$(git bundle verify "$W/fetched.gpg" >/dev/null 2>&1 && echo readable || echo unreadable)" "unreadable"
# file -b, NOT file. Without -b the path is echoed, and this temp file is named
# fetched.gpg, so grep -i matched "gpg" in the NAME -- this assertion returned 1
# for ASCII text, an empty file, random bytes and a real cleartext bundle. It was
# one of the 18 green assertions and could not fail.
ck "and it is PGP data, per file -b" \
   "$(file -b "$W/fetched.gpg" | grep -ciE '^PGP')" "1"

echo "== R1: RESTORE -- decrypt, clone, verify content =="
gpg --batch --yes --pinentry-mode loopback --passphrase '' -o "$W/restored.bundle" -d "$W/fetched.gpg" 2>/dev/null
ck "decrypts" "$([ -s "$W/restored.bundle" ] && echo y || echo n)" "y"
# POSITIVE CONTROL for the check above: the same bytes, decrypted, must be a
# bundle git accepts. Without this pair, "unreadable" proves nothing -- a
# truncated or empty file is also unreadable.
ck "positive control: decrypted bytes ARE a valid git bundle" \
   "$(git bundle verify "$W/restored.bundle" >/dev/null 2>&1 && echo readable || echo unreadable)" "readable"
git clone -q "$W/restored.bundle" "$W/restored" 2>/dev/null
ck "clones" "$([ -d "$W/restored/.git" ] && echo y || echo n)" "y"
ck "commit count survives" "$(git -C "$W/restored" rev-list --count --all 2>/dev/null)" "$WANT_COMMITS"
ck "HEAD survives" "$(git -C "$W/restored" rev-parse HEAD 2>/dev/null)" "$WANT_HEAD"
ck "README content survives byte-for-byte" "$(shasum -a 256 "$W/restored/README.md" 2>/dev/null|awk '{print $1}')" "$(shasum -a 256 "$REPO/README.md"|awk '{print $1}')"
NF_SRC=$(git -C "$REPO" ls-tree -r HEAD --name-only | wc -l | tr -d ' ')
NF_DST=$(git -C "$W/restored" ls-tree -r HEAD --name-only | wc -l | tr -d ' ')
ck "every tracked file present ($NF_SRC)" "$NF_DST" "$NF_SRC"
ck "full tree hash identical" "$(git -C "$W/restored" rev-parse HEAD^{tree} 2>/dev/null)" "$(git -C "$REPO" rev-parse HEAD^{tree})"

echo "== G0: the encryption guard must REFUSE a cleartext bundle named *.gpg =="
# The guard's whole purpose is to catch the case where encryption silently did
# not happen. Prove it can: hand the running script a gpg that is `cp`.
STUB="$W/stub"; mkdir -p "$STUB"
cat > "$STUB/gpg" <<'STUBEOF'
#!/bin/bash
# passthrough "encryption": copy the input to --output, exit 0
args=("$@"); out=""; inp=""
for ((i=0; i<${#args[@]}; i++)); do
  [ "${args[$i]}" = "--output" ] && out="${args[$((i+1))]}"
  [ "${args[$i]}" = "--encrypt" ] && inp="${args[$((i+1))]}"
done
if [ -n "$out" ] && [ -n "$inp" ]; then cp "$inp" "$out"; exit 0; fi
exec /usr/bin/gpg "$@"     # let --list-keys etc. behave normally
STUBEOF
chmod +x "$STUB/gpg"
out=$(PATH="$STUB:$PATH" KOI_ARCHIVE_MARKER="$W/.m0" bash "$S" 2>&1); rc=$?
ck "exit 1 when encryption silently did not happen" "$rc" "1"
ck "says it refuses to transfer" "$(echo "$out"|grep -c 'refusing to transfer')" "1"
ck "nothing cleartext reached the remote" \
   "$(ssh -o BatchMode=yes gaia "ls -1 $KOI_ARCHIVE_OFFSITE_DIR/*.gpg 2>/dev/null | wc -l" | tr -d ' ')" "1"
echo "     (the 1 remaining file is B1's genuine ciphertext, not the stub's cleartext)"

echo "== G1: refuses to send if the key is missing =="
out=$(KOI_ARCHIVE_GPG_KEY=DEADBEEFDEADBEEF KOI_ARCHIVE_MARKER="$W/.m2" bash "$S" 2>&1); rc=$?
ck "exit 1" "$rc" "1"
ck "says it refuses cleartext" "$(echo "$out"|grep -c 'refusing to send cleartext')" "1"

echo; echo "RESULT: pass=$pass fail=$fail"

# EXIT NON-ZERO ON FAILURE. Without this the suite prints "fail=3" and exits 0,
# so it reports success to every automated consumer and only a human reading the
# RESULT line by eye would notice. That is not theoretical: a run of this file
# was killed mid-suite and exited 0 with no RESULT line at all, which is
# indistinguishable from a clean pass unless you are looking for the line that
# is missing. A suite that cannot fail its caller is not a gate.
[ "$fail" -eq 0 ] || exit 1
