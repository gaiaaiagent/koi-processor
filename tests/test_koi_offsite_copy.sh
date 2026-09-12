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
#   bash tests/test_koi_offsite_copy.sh      # needs ssh to gaia; ~2 minutes
#
# RUN IT ALONE. T7 interrupts a transfer with `pkill -9 -x rsync`, which kills
# EVERY rsync on this machine, not just this suite's -- including the nightly
# backup's off-host copy if it happens to be running. Per-run remote directories
# make the suite safe against sharing STATE with a second run; they do not make
# it safe against that kill. Do not run two copies concurrently, and do not run
# it while koi_backup.sh is in its off-host step.
set -uo pipefail
S=~/projects/koi-processor-service/scripts/koi_offsite_copy.sh
# Unique per run. A fixed remote directory meant two concurrent runs of this
# suite shared state on the far end AND each deleted it on exit, so one run's
# cleanup pulled the ground out from under the other. The symptom was a failure
# in an unrelated test (T5) that had nothing to do with the code under test --
# which is the worst kind, because it sends you looking in the wrong place.
W=$(mktemp -d); export KOI_OFFSITE_DIR="koi-offsite-selftest-$$"
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

echo "== T2: a RESUMED partial with correct size+mtime but WRONG BYTES =="
# The transfer now lands on <name>.inprogress and is promoted only after the
# checksum passes, so corrupting the FINAL name proves nothing -- the next run
# writes a fresh .inprogress and renames over it. The risk moved with the design:
# --partial --inplace RESUMES an existing .inprogress, and rsync's default
# size+mtime quick check will SKIP a file whose size and mtime match the source.
# So a corrupt resume is the case that can still silently produce a bad backup,
# and the checksum is the only thing standing in front of it. That is what T2
# must test now.
ssh -o BatchMode=yes gaia "cd $KOI_OFFSITE_DIR && head -c 3000000 /dev/urandom > $(basename "$D").inprogress && touch -r $(basename "$D") $(basename "$D").inprogress"
out=$(bash "$S" "$D" 2>&1); rc=$?
echo "$out" | sed 's/^/     /'
ck "exit 1 on mismatch" "$rc" "1"
ck "says checksum mismatch" "$(echo "$out"|grep -c 'checksum mismatch')" "1"
ck "the corrupt partial was REMOVED" "$(ssh -o BatchMode=yes gaia "test -f $KOI_OFFSITE_DIR/$(basename "$D").inprogress && echo y || echo n")" "n"
ck "corrupt bytes were NOT promoted to the backup name" \
   "$(ssh -o BatchMode=yes gaia "sha256sum $KOI_OFFSITE_DIR/$(basename "$D") 2>/dev/null | awk '{print \$1}'")" \
   "$(shasum -a 256 "$D"|awk '{print $1}')"
ck "local dump still readable and unchanged size" "$(wc -c < "$D" | tr -d ' ')" "3000000"

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

echo "== T7: an INTERRUPTED transfer must not leave anything named like a backup =="
# The 2026-09-12 failure: rsync was SIGKILLed at 10.5GB of 12.5GB and left that
# partial under the dump's REAL name, where `ls` could not distinguish it from a
# finished backup and retention would have COUNTED it -- possibly pruning a good
# backup in its favour. Reproduce the interrupt and assert the invariant.
BIG="$W/personal_koi-20260930-000000.dump"
head -c 60000000 /dev/urandom > "$BIG"          # 60MB: long enough to interrupt
# ATTEMPTS=1 so a single kill actually ENDS the run. With the default 3 the
# retry loop resumes and finishes the transfer -- which is correct behaviour and
# is what happened on the first version of this test, but it means the run never
# reaches the state this test is about. The invariant under test is what an
# UNRECOVERED interruption leaves behind.
KOI_OFFSITE_ATTEMPTS=1 bash "$S" "$BIG" >/dev/null 2>&1 &
SPID=$!
# Wait until bytes are actually moving, then kill rsync out from under it.
# -x (exact process NAME), never -f (full command line). `pkill -f rsync...`
# matches any process whose ARGUMENTS mention rsync -- including the shell
# running this very test, whose command line contains this line. That is not
# hypothetical: it silently killed the harness mid-run on the first attempt,
# which looked like the suite passing quietly rather than like a kill.
# Wait for BYTES ON THE FAR END, not for a local process to exist. An rsync
# process is alive during ssh handshake and protocol negotiation, before it has
# opened anything remotely -- killing at that point leaves no partial at all,
# which is why this test failed while the code was correct. Poll the remote.
for _ in $(seq 1 100); do
  sz=$(ssh -o BatchMode=yes gaia "stat -c %s $KOI_OFFSITE_DIR/personal_koi-20260930-000000.dump.inprogress 2>/dev/null" || true)
  [ -n "${sz:-}" ] && [ "${sz:-0}" -gt 1000000 ] && break
  sleep 0.5
done
echo "     (remote partial reached ${sz:-0} bytes before the kill)"
pkill -9 -x rsync 2>/dev/null; wait $SPID 2>/dev/null
FINAL=$(ssh -o BatchMode=yes gaia "ls -1 $KOI_OFFSITE_DIR/personal_koi-20260930-000000.dump 2>/dev/null | wc -l" | tr -d ' ')
PART=$(ssh -o BatchMode=yes gaia "ls -1 $KOI_OFFSITE_DIR/personal_koi-20260930-000000.dump.inprogress 2>/dev/null | wc -l" | tr -d ' ')
ck "nothing under the real backup name" "$FINAL" "0"
ck "the partial exists, under .inprogress" "$PART" "1"

echo "== T8: retention must not count a partial as a backup =="
# Give retention a reason to prune: KEEP=1 with one real dump plus the partial.
cp "$D" "$W/personal_koi-20260929-000000.dump"
KOI_OFFSITE_KEEP=1 bash "$S" "$W/personal_koi-20260929-000000.dump" >/dev/null 2>&1
KEPT=$(ssh -o BatchMode=yes gaia "ls -1 $KOI_OFFSITE_DIR/personal_koi-*.dump 2>/dev/null | wc -l" | tr -d ' ')
STILL=$(ssh -o BatchMode=yes gaia "ls -1 $KOI_OFFSITE_DIR/*.inprogress 2>/dev/null | wc -l" | tr -d ' ')
ck "the real dump survived retention" "$KEPT" "1"
ck "the partial was not promoted into a backup" "$STILL" "1"

echo "== T9: a retry RESUMES the partial rather than restarting =="
BEFORE=$(ssh -o BatchMode=yes gaia "stat -c %s $KOI_OFFSITE_DIR/personal_koi-20260930-000000.dump.inprogress 2>/dev/null" || echo 0)
out=$(bash "$S" "$BIG" 2>&1); rc=$?
ck "completes on retry" "$rc" "0"
ck "now present under the real name" "$(ssh -o BatchMode=yes gaia "ls -1 $KOI_OFFSITE_DIR/personal_koi-20260930-000000.dump 2>/dev/null | wc -l" | tr -d ' ')" "1"
ck "remote bytes match local" "$(ssh -o BatchMode=yes gaia "sha256sum $KOI_OFFSITE_DIR/personal_koi-20260930-000000.dump | awk '{print \$1}'")" "$(shasum -a 256 "$BIG"|awk '{print $1}')"
ck "the .inprogress name is gone (renamed, not copied)" "$(ssh -o BatchMode=yes gaia "ls -1 $KOI_OFFSITE_DIR/personal_koi-20260930-000000.dump.inprogress 2>/dev/null | wc -l" | tr -d ' ')" "0"
echo "     (partial was ${BEFORE} bytes before the retry; full file is $(wc -c < "$BIG"))"

ssh -o BatchMode=yes gaia "rm -rf $KOI_OFFSITE_DIR"; rm -rf "$W"
echo; echo "RESULT: pass=$pass fail=$fail"

# EXIT NON-ZERO ON FAILURE. Without this the suite prints "fail=3" and exits 0,
# so it reports success to every automated consumer and only a human reading the
# RESULT line by eye would notice. That is not theoretical: a run of this file
# was killed mid-suite and exited 0 with no RESULT line at all, which is
# indistinguishable from a clean pass unless you are looking for the line that
# is missing. A suite that cannot fail its caller is not a gate.
[ "$fail" -eq 0 ] || exit 1
