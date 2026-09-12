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
W=$(mktemp -d); export KOI_ARCHIVE_OFFSITE_DIR=koi-archive-selftest KOI_ARCHIVE_MARKER="$W/.marker"
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
ck "and it is PGP-encrypted, per file(1)" \
   "$(file "$W/fetched.gpg" | grep -ciE 'PGP|GPG|encrypted')" "1"

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

echo "== G1: refuses to send if the key is missing =="
out=$(KOI_ARCHIVE_GPG_KEY=DEADBEEFDEADBEEF KOI_ARCHIVE_MARKER="$W/.m2" bash "$S" 2>&1); rc=$?
ck "exit 1" "$rc" "1"
ck "says it refuses cleartext" "$(echo "$out"|grep -c 'refusing to send cleartext')" "1"

echo; echo "RESULT: pass=$pass fail=$fail"
