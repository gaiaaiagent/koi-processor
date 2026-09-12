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
export KOI_BACKUP_DB="$TDB" KOI_BACKUP_DEST="$W" KOI_BACKUP_SELFTEST=1 KOI_OFFSITE_DIR=koi-offsite-e2e
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

echo; echo "RESULT: pass=$pass fail=$fail"
