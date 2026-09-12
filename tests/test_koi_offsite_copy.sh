#!/usr/bin/env bash
# Tests for scripts/koi_offsite_copy.sh, run against the real gaia host using a
# throwaway remote directory (koi-offsite-selftest) and a 3MB stand-in dump.
#
# T2 is the one that matters: it corrupts the remote copy while preserving byte
# count AND mtime, which is exactly the case rsync's default size+mtime quick
# check SKIPS. rsync exits 0 and re-sends nothing; only the sha256 comparison
# catches it. That test is the argument for verifying instead of trusting the
# exit code.
#
#   bash tests/test_koi_offsite_copy.sh      # needs ssh to gaia; ~1 minute
set -uo pipefail
S=~/projects/koi-processor-service/scripts/koi_offsite_copy.sh
W=$(mktemp -d); export KOI_OFFSITE_DIR=koi-offsite-selftest
export KOI_OFFSITE_MARKER="$W/.marker"
D="$W/personal_koi-20260912-000000.dump"
head -c 3000000 /dev/urandom > "$D"     # 3 MB stand-in for the 12.5 GB dump
pass=0; fail=0
ck(){ if [ "$2" = "$3" ]; then echo "  PASS $1"; pass=$((pass+1)); else echo "  FAIL $1 (got '$2' want '$3')"; fail=$((fail+1)); fi; }

echo "== T1: happy path =="
out=$(KOI_OFFSITE_KEEP=7 bash "$S" "$D" 2>&1); rc=$?
echo "$out" | sed 's/^/     /'
ck "exit 0" "$rc" "0"
ck "marker written" "$([ -s "$KOI_OFFSITE_MARKER" ] && echo y || echo n)" "y"
ck "marker holds the real sha" "$(awk "{print \$3}" "$KOI_OFFSITE_MARKER")" "$(shasum -a 256 "$D"|awk '{print $1}')"
ck "remote file exists" "$(ssh -o BatchMode=yes gaia "test -f $KOI_OFFSITE_DIR/$(basename "$D") && echo y || echo n")" "y"

echo "== T2: silent remote corruption that rsync's size+mtime quick-check SKIPS =="
# same byte count, same mtime -> rsync -a will not retransfer. Only the checksum catches it.
ssh -o BatchMode=yes gaia "cd $KOI_OFFSITE_DIR && M=\$(stat -c %y $(basename "$D")) && head -c 3000000 /dev/urandom > $(basename "$D") && touch -d \"\$M\" $(basename "$D")"
out=$(bash "$S" "$D" 2>&1); rc=$?
echo "$out" | sed 's/^/     /'
ck "exit 1 on mismatch" "$rc" "1"
ck "says checksum mismatch" "$(echo "$out"|grep -c 'checksum mismatch')" "1"
ck "bad remote copy REMOVED" "$(ssh -o BatchMode=yes gaia "test -f $KOI_OFFSITE_DIR/$(basename "$D") && echo y || echo n")" "n"
ck "local dump untouched" "$(shasum -a 256 "$D"|awk '{print $1}')" "$(shasum -a 256 "$D"|awk '{print $1}')"

echo "== T3: unreachable host fails loudly, does not hang =="
out=$(KOI_OFFSITE_HOST=no-such-host-koi bash "$S" "$D" 2>&1); rc=$?
ck "exit 1" "$rc" "1"
ck "names the host problem" "$(echo "$out"|grep -c 'cannot reach')" "1"

echo "== T4: missing dump argument =="
out=$(bash "$S" "$W/nope.dump" 2>&1); rc=$?
ck "exit 2" "$rc" "2"

echo "== T5: no-terminal / launchd-like env (no SSH_AUTH_SOCK, no TTY) =="
out=$(env -u SSH_AUTH_SOCK -u SSH_AGENT_PID KOI_OFFSITE_DIR=$KOI_OFFSITE_DIR KOI_OFFSITE_MARKER="$W/.m2" bash "$S" "$D" </dev/null 2>&1); rc=$?
ck "exit 0 without an agent" "$rc" "0"

echo "== T6: remote retention keeps only KEEP newest =="
for i in 1 2 3 4 5; do cp "$D" "$W/personal_koi-2026090$i-000000.dump"; KOI_OFFSITE_KEEP=3 bash "$S" "$W/personal_koi-2026090$i-000000.dump" >/dev/null 2>&1; done
n=$(ssh -o BatchMode=yes gaia "ls -1 $KOI_OFFSITE_DIR/personal_koi-*.dump 2>/dev/null | wc -l" | tr -d ' ')
ck "3 kept after KEEP=3" "$n" "3"
kept=$(ssh -o BatchMode=yes gaia "ls -1 $KOI_OFFSITE_DIR/ | sort | tr '\n' ' '")
echo "     kept: $kept"
ck "newest survived" "$(echo "$kept"|grep -c '20260905')" "1"
ck "oldest pruned" "$(echo "$kept"|grep -c '20260901')" "0"

ssh -o BatchMode=yes gaia "rm -rf $KOI_OFFSITE_DIR"; rm -rf "$W"
echo; echo "RESULT: pass=$pass fail=$fail"
