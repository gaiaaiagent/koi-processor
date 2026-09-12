#!/usr/bin/env bash
# End-to-end tests for scripts/koi_backup.sh, run against a scratch PostgreSQL
# database via KOI_BACKUP_SELFTEST=1 so the full path -- pg_dump, pg_restore
# integrity check, size guard, retention, off-host copy -- executes in seconds
# rather than in the nightly two-hour 12.5GB run.
#
# E2 is the load-bearing one: when the off-host copy fails, the run must exit
# non-zero AND leave the local dump in place and restorable. A backup script
# that deletes a good dump because the network was down would be worse than no
# off-host copy at all.
#
#   bash tests/test_koi_backup_e2e.sh        # needs local postgres + ssh to gaia
set -uo pipefail
export PATH="/opt/homebrew/bin:$PATH"
S=~/projects/koi-processor-service/scripts/koi_backup.sh
W=$(mktemp -d); TDB=koi_backup_selftest_$$
export KOI_BACKUP_DB="$TDB" KOI_BACKUP_DEST="$W" KOI_BACKUP_SELFTEST=1 KOI_OFFSITE_DIR="koi-offsite-e2e-$$"
pass=0; fail=0
ck(){ if [ "$2" = "$3" ]; then echo "  PASS $1"; pass=$((pass+1)); else echo "  FAIL $1 (got '$2' want '$3')"; fail=$((fail+1)); fi; }
cleanup(){ dropdb --if-exists "$TDB" 2>/dev/null; ssh -o BatchMode=yes gaia "rm -rf $KOI_OFFSITE_DIR" 2>/dev/null; rm -rf "$W"; }
trap cleanup EXIT

createdb "$TDB" || { echo "cannot create scratch db"; exit 1; }
psql -q "$TDB" -c "create table t as select g, md5(g::text) from generate_series(1,20000) g" || exit 1

echo "== E1: full run, real script, scratch DB =="
out=$(bash "$S" 2>&1); rc=$?
echo "$out" | sed 's/^/     /'
ck "exit 0" "$rc" "0"
D=$(ls "$W"/personal_koi-*.dump 2>/dev/null | head -1)
ck "a dump exists" "$([ -n "$D" ] && echo y || echo n)" "y"
ck "dump passes pg_restore --list" "$(pg_restore --list "$D" >/dev/null 2>&1 && echo y || echo n)" "y"
ck "OFFSITE line reached" "$(echo "$out" | grep -c 'OFFSITE: OK')" "1"
ck "marker written" "$([ -s "$W/.last-offhost-sync" ] && echo y || echo n)" "y"
ck "marker sha == local dump sha" "$(awk '{print $3}' "$W/.last-offhost-sync")" "$(shasum -a 256 "$D"|awk '{print $1}')"
R=$(ssh -o BatchMode=yes gaia "sha256sum $KOI_OFFSITE_DIR/$(basename "$D") 2>/dev/null | awk '{print \$1}'")
ck "REMOTE bytes match local (the whole point)" "$R" "$(shasum -a 256 "$D"|awk '{print $1}')"

echo "== E2: off-host unreachable -> exit 1 but the LOCAL dump survives =="
psql -q "$TDB" -c "insert into t select g, md5(g::text) from generate_series(20001,21000) g"
before=$(ls "$W"/personal_koi-*.dump | wc -l | tr -d ' ')
out=$(KOI_OFFSITE_HOST=no-such-host-koi bash "$S" 2>&1); rc=$?
ck "exit 1" "$rc" "1"
ck "says the local dump is kept" "$(echo "$out"|grep -c 'is intact; only the off-host copy failed')" "1"
after=$(ls "$W"/personal_koi-*.dump | wc -l | tr -d ' ')
ck "the new local dump was NOT deleted" "$after" "$((before+1))"
ck "and it is still a valid dump" "$(pg_restore --list "$(ls -t "$W"/personal_koi-*.dump|head -1)" >/dev/null 2>&1 && echo y || echo n)" "y"

echo "== E3: selftest mode is refused against the real database =="
out=$(KOI_BACKUP_DB=personal_koi KOI_BACKUP_SELFTEST=1 KOI_BACKUP_DEST="$W" bash "$S" 2>&1); rc=$?
ck "exit 3" "$rc" "3"
ck "names the refusal" "$(echo "$out"|grep -c 'refused against the real database')" "1"

echo "== E4: KOI_OFFSITE=0 opt-out still completes locally =="
out=$(KOI_OFFSITE=0 bash "$S" 2>&1); rc=$?
ck "exit 0" "$rc" "0"
ck "skipped, not silently done" "$(echo "$out"|grep -c 'OFFSITE: skipped')" "1"

echo "== E5: an INTERRUPTED run must not report success =="
# There was no interrupt coverage at all, which is why the trap could delete the
# dump and still `exit 0` through the very session that hardened its disarm. The
# property: a signalled run exits non-zero AND leaves no partial behind.
# The scratch table must be big enough that pg_dump is still running when the
# signal lands, and the kill must be triggered by EVIDENCE (the dump file has
# grown) rather than by a sleep. An earlier version used `sleep 0.5` after
# pg_dump appeared: it reported exit 143 while the machine was loaded and
# therefore slow, then reported exit 0 once the machine was idle, because the
# whole script finished before the kill arrived. It was green by accident.
psql -q "$TDB" -c "insert into t select g, md5(g::text)||md5((g*7)::text)||md5((g*13)::text) from generate_series(30000,900000) g" >/dev/null 2>&1
before=$(ls "$W"/personal_koi-*.dump 2>/dev/null | wc -l | tr -d ' ')
KOI_OFFSITE=0 bash "$S" >/dev/null 2>&1 &
BPID=$!
# Wait for the dump file to actually be accumulating bytes, then signal.
killed=no
for _ in $(seq 1 200); do
  newest=$(ls -t "$W"/personal_koi-*.dump 2>/dev/null | head -1)
  if [ -n "$newest" ] && [ "$(wc -c < "$newest" | tr -d ' ')" -gt 2000000 ]; then
    kill -TERM "$BPID" 2>/dev/null; killed=yes; break
  fi
  kill -0 "$BPID" 2>/dev/null || break     # script exited before we could signal
  sleep 0.1
done
ck "the signal was actually delivered mid-dump" "$killed" "yes"
wait "$BPID" 2>/dev/null; rc=$?
ck "exit is NON-zero after SIGTERM" "$([ "$rc" -ne 0 ] && echo nonzero || echo "zero($rc)")" "nonzero"
ck "conventional 128+15 for SIGTERM" "$rc" "143"
after=$(ls "$W"/personal_koi-*.dump 2>/dev/null | wc -l | tr -d ' ')
ck "no partial dump left behind" "$after" "$before"
# `grep -c` exits 1 on zero matches, so `... || echo 0` appends a SECOND line
# and the comparison sees "0\n0" -- which is a plumbing artifact, not a result.
# grep -c already prints 0; suppress its exit status instead of adding output.
ck "the log says it aborted" "$(grep -c 'ABORTED (SIGTERM)' "$W/backup.log" 2>/dev/null; true)" "1"

echo "== E6: selftest refuses the PRODUCTION backup directory =="
# Without this, a scratch dump named exactly like a production dump reached
# gaia:koi-offsite/ and counted toward remote retention.
out=$(KOI_BACKUP_DEST="$HOME/koi-backups" bash "$S" 2>&1); rc=$?
ck "exit 3" "$rc" "3"
ck "names the refusal" "$(echo "$out"|grep -c 'refuses the production backup directory')" "1"
ck "off-host dir defaulted away from production" \
   "$(KOI_BACKUP_DEST="$W" bash -c 'set -a; KOI_BACKUP_SELFTEST=1; KOI_BACKUP_DB='"$TDB"'; set +a; grep -c "KOI_OFFSITE_DIR:=koi-offsite-selftest" '"$S"'')" "1"

echo; echo "RESULT: pass=$pass fail=$fail"

# EXIT NON-ZERO ON FAILURE. Without this the suite prints "fail=3" and exits 0,
# so it reports success to every automated consumer and only a human reading the
# RESULT line by eye would notice. That is not theoretical: a run of this file
# was killed mid-suite and exited 0 with no RESULT line at all, which is
# indistinguishable from a clean pass unless you are looking for the line that
# is missing. A suite that cannot fail its caller is not a gate.
[ "$fail" -eq 0 ] || exit 1
