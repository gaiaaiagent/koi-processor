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
# T1-T9 test TRANSFER MECHANICS -- staging under .inprogress, atomic promotion,
# retention, resume, checksum rejection. Those are orthogonal to encryption, so
# they run with it OFF and compare remote bytes to the local dump directly,
# which keeps the assertions readable. The DEFAULT path (encryption on) gets its
# own end-to-end section, X1-X5, at the bottom -- including the round trip that
# is the only thing proving the ciphertext is still a usable backup.
export KOI_OFFSITE_ENCRYPT=0
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

echo "== X1-X5: the DEFAULT path -- encrypted =="
unset KOI_OFFSITE_ENCRYPT          # back to the shipped default (on)
XD="$W/personal_koi-20261001-000000.dump"
cp "$D" "$XD"
out=$(bash "$S" "$XD" 2>&1); rc=$?
echo "$out" | sed 's/^/     /'
RB="personal_koi-20261001-000000.dump.gpg"
ck "X1 exit 0" "$rc" "0"
ck "X1 remote artifact carries .gpg" "$(ssh -o BatchMode=yes gaia "ls -1 $KOI_OFFSITE_DIR/$RB 2>/dev/null | wc -l" | tr -d ' ')" "1"
ck "X1 the PLAINTEXT name is absent off-host" "$(ssh -o BatchMode=yes gaia "ls -1 $KOI_OFFSITE_DIR/personal_koi-20261001-000000.dump 2>/dev/null | wc -l" | tr -d ' ')" "0"

echo "  X2: what rests off-host must not be a readable dump"
ssh -o BatchMode=yes gaia "cat $KOI_OFFSITE_DIR/$RB" > "$W/fetched.gpg"
ck "X2 pg_restore REJECTS the off-host bytes" \
   "$(pg_restore --list "$W/fetched.gpg" >/dev/null 2>&1 && echo readable || echo unreadable)" "unreadable"
ck "X2 file -b says PGP" "$(file -b "$W/fetched.gpg" | grep -c '^PGP')" "1"
ck "X2 encrypted to the intended key" \
   "$(head -c 2000000 "$W/fetched.gpg" | gpg --list-packets 2>&1 | grep -c 'F5EE933A8DC407E4')" "1"

echo "  X3: RESTORE -- the only test that proves it is still a backup"
gpg --batch --yes --pinentry-mode loopback --passphrase '' -o "$W/restored.dump" -d "$W/fetched.gpg" 2>/dev/null
ck "X3 decrypts to the ORIGINAL dump, byte for byte" \
   "$(shasum -a 256 "$W/restored.dump" 2>/dev/null | awk '{print $1}')" "$(shasum -a 256 "$XD" | awk '{print $1}')"
ck "X3 positive control: the decrypted dump IS readable by pg_restore" \
   "$(pg_restore --list "$W/restored.dump" >/dev/null 2>&1 && echo readable || echo unreadable)" "readable"

echo "  X4: refuses to send cleartext when encryption silently does not happen"
STUB="$W/xstub"; mkdir -p "$STUB"
REAL_GPG="$(command -v gpg)"
cat > "$STUB/gpg" <<STUBEOF
#!/bin/bash
args=("\$@"); out=""; inp=""
for ((i=0; i<\${#args[@]}; i++)); do
  [ "\${args[\$i]}" = "--output" ] && out="\${args[\$((i+1))]}"
  [ "\${args[\$i]}" = "--encrypt" ] && inp="\${args[\$((i+1))]}"
done
if [ -n "\$out" ] && [ -n "\$inp" ]; then cp "\$inp" "\$out"; exit 0; fi
exec ${REAL_GPG} "\$@"
STUBEOF
chmod +x "$STUB/gpg"
cp "$D" "$W/personal_koi-20261002-000000.dump"
out=$(PATH="$STUB:$PATH" KOI_OFFSITE_MARKER="$W/.mx" bash "$S" "$W/personal_koi-20261002-000000.dump" 2>&1); rc=$?
ck "X4 exit 1" "$rc" "1"
ck "X4 names the specific refusal" "$(echo "$out" | grep -c 'still a readable pg_dump')" "1"
ck "X4 nothing for that dump reached the remote" \
   "$(ssh -o BatchMode=yes gaia "ls -1 $KOI_OFFSITE_DIR/personal_koi-20261002* 2>/dev/null | wc -l" | tr -d ' ')" "0"

echo "  X5: retention is scoped to ONE database"
cp "$D" "$W/eliza-20261001-000000.dump"
KOI_OFFSITE_KEEP=1 KOI_OFFSITE_MARKER="$W/.me" bash "$S" "$W/eliza-20261001-000000.dump" >/dev/null 2>&1
ck "X5 eliza kept" "$(ssh -o BatchMode=yes gaia "ls -1 $KOI_OFFSITE_DIR/eliza-*.dump.gpg 2>/dev/null | wc -l" | tr -d ' ')" "1"
ck "X5 eliza's KEEP=1 did NOT prune personal_koi" \
   "$(ssh -o BatchMode=yes gaia "ls -1 $KOI_OFFSITE_DIR/personal_koi-20261001-000000.dump.gpg 2>/dev/null | wc -l" | tr -d ' ')" "1"

ssh -o BatchMode=yes gaia "rm -rf $KOI_OFFSITE_DIR"; rm -rf "$W"
echo; echo "RESULT: pass=$pass fail=$fail"

# EXIT NON-ZERO ON FAILURE. Without this the suite prints "fail=3" and exits 0,
# so it reports success to every automated consumer and only a human reading the
# RESULT line by eye would notice. That is not theoretical: a run of this file
# was killed mid-suite and exited 0 with no RESULT line at all, which is
# indistinguishable from a clean pass unless you are looking for the line that
# is missing. A suite that cannot fail its caller is not a gate.
[ "$fail" -eq 0 ] || exit 1
