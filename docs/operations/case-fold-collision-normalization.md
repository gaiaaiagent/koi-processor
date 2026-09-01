# DRY RUN — normalize the `Regenerate cascadia.md` case collision

**Status: NOT EXECUTED.** Written 2026-08-26. Prepared while NUC vault sync is ON
and MacBook vault sync is OFF. Nothing below has been run.

Scope is exactly one file. This is a prerequisite for the controlled first MacBook
scan, because A4 will predictably fire `tombstone_blocked` on it and the re-enable
runbook requires a full stop on that signal.

---

## Verified state

Everything here was read from both machines on 2026-08-26; none of it is recalled.

### The MacBook (APFS, case-insensitive)

| | |
|---|---|
| files on disk | **one**, inode `181086141`, 6,262 bytes, sha `38ecc42b…` |
| both spellings resolve to it | yes — same inode for `Regenerate Cascadia.md` and `Regenerate cascadia.md` |
| real on-disk name | `Regenerate Cascadia.md` |
| DB rows | **two** |
| … `Organizations/Regenerate Cascadia.md` | hash `38ecc42b…`, seq 33/33 — **matches disk** |
| … `Organizations/Regenerate cascadia.md` | hash `ed88466c…`, seq 229/229 — **stale; matches nothing on disk** |

### The NUC (ext4, case-sensitive)

| | |
|---|---|
| files on disk | **two distinct inodes**, `6440465` (lowercase, Aug 19) and `6470157` (uppercase, Aug 24) |
| bytes | 6,262 each |
| sha256 | **identical**, `38ecc42b…` — the content is the same, only the name differs |
| DB rows | two, **both** hash `38ecc42b…`; lowercase seq 129/129, uppercase seq 6/6 |
| event history | **47 rows** for this exact RID — 23 `FORGET`, 23 `NEW`, 1 `UPDATE`. An earlier draft said "1"; that query filtered on `expires_at>now()` and hid 46. |
| … unexpired | **1**: `2f759ee7-2872-4ab7-b4ad-60de2509d150`, `UPDATE`, queued 2026-08-26 22:02:21Z during the soak, expires 2026-09-02 |
| … its state | `target`, `delivered_to` **and** `confirmed_by` all = `darren-personal` |
| … MacBook applied row | **none** for that UUID |
| **pending delivery for this RID** | **0** ← this is the safety property that matters |

### It is the only one

| audit | result |
|---|---|
| DB case-fold collisions, MacBook, all 7 folders | **1** — this one |
| DB case-fold collisions, NUC, all 7 folders | **1** — this one |
| NUC filesystem case-fold collisions, all 7 folders | **1** — this one |

### Why the two reconciles disagreed on the count

A read-only MacBook reconcile reported **two** `missing_on_disk`; an independent
check reported **one**. Both are correct, and the difference *is* the defect:

* `reconcile` builds `disk_files` from `rglob`, which yields **real** filenames, so
  the lowercase DB row has no match → `missing_on_disk`.
* A direct `os.path.exists()` check on APFS is **case-insensitive**, so the
  lowercase path resolves to the uppercase file → not missing.

A4 uses `(vault_path / rel_path).exists()`, i.e. the second behaviour. So on the
first MacBook scan the lowercase row becomes a deletion candidate and **A4 blocks
it** → `vault_sync.tombstone_blocked` → the runbook stops. That is A4 working
correctly on genuinely ambiguous input, not a fault.

---

## Events: leave all 47 to age out

**Corrected.** The earlier draft deleted events with the predicate
`rid LIKE '%Regenerate cascadia.md'` and asserted `ROW_COUNT = 1`. Against 47 real
rows that assertion would have aborted the transaction — it failed safe, but the
design was wrong twice over: a broad RID predicate on an audit trail, guarded by a
count derived from a query that had silently hidden 46 rows.

**The 46 expired rows are audit history. Do not touch them.**

The single unexpired event is already `delivered_to` **and** `confirmed_by`
`darren-personal`, with no MacBook `vault_sync_applied_events` row. It was
acknowledged without being applied, and it cannot be re-delivered, because the
poller confirms vault events whether or not the vault manager is running:

```python
# api/koi_poller.py:652-658 — _process_event
if isinstance(contents, dict) and contents.get("_vault_sync"):
    if self.vault_sync:          # falsy while VAULT_SYNC_ENABLED=false
        ...                      # branch skipped, NO exception raised
# ...so _process_event returns normally and the caller does:
        confirm_batch.append(event_id)      # :623
```

A skipped branch is not a failure, so the event is confirmed and never re-sent. It
expires 2026-09-02 on its own.

**Verification changes accordingly.** The old check ("zero historical rows for this
RID") asserted something neither true nor necessary. The real property is:

```sql
-- zero UNEXPIRED events PENDING DELIVERY for this RID — currently 0
SELECT count(*) FROM koi_net_events
 WHERE rid LIKE '%Regenerate cascadia.md'
   AND expires_at > now()
   AND NOT (COALESCE(target_node,'') = ANY(COALESCE(delivered_to, ARRAY[]::text[])));
```

**If** there is later a deliberate reason to remove the current event, delete that
one UUID with complete state guards and never the RID predicate:

```sql
BEGIN;
DO $$
DECLARE n int;
BEGIN
  DELETE FROM koi_net_events
   WHERE event_id = '2f759ee7-2872-4ab7-b4ad-60de2509d150'::uuid
     AND rid = 'orn:koi-net.vault-file:Organizations/Regenerate cascadia.md'
     AND event_type = 'UPDATE'
     AND target_node = 'orn:koi-net.node:darren-personal+80e26aab6b59178cd605c93b1aa0b903e61a283ee2a4ace07da3d1fabdd779f6'
     AND target_node = ANY(delivered_to)
     AND target_node = ANY(confirmed_by);
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'expected exactly 1 row, got % — state moved, abort', n; END IF;
END $$;
COMMIT;
```

Every conjunct is a state assertion: wrong type, wrong target, or not-yet-confirmed
all make it match zero rows and abort. **Default is to do nothing and let it expire.**

## The hazard that dictates the order

**A FORGET for the lowercase path is the thing to avoid.** On the NUC that path is
a distinct file; on the MacBook it case-folds onto the **canonical** file. If a
lowercase FORGET were ever applied on the MacBook it would unlink inode
`181086141` — the file we are trying to preserve.

`KOI_VAULT_READONLY_PATHS` on the MacBook now includes `Organizations/`, so an
inbound FORGET for it is rejected. **That guard is not sufficient reason to
generate the event.** This session exists because single guards failed.

Therefore: **rows are `DELETE`d, never tombstoned.** Tombstoning is what emits a
FORGET. Deleting the tracking row emits nothing, and the row is not recreated —
on the NUC because the file is quarantined, on the MacBook because `rglob` never
yields that spelling.

## Must NUC sync be disabled?

**Yes, for the duration.** Two independent reasons:

1. With sync ON, quarantining the lowercase file causes the next scan to observe a
   genuine absence on ext4 → A4 confirms it → tombstone → **FORGET emitted**. That
   is exactly the event that must never exist.
2. It keeps the window free of new emissions while the filesystem is changed. No
   events are removed by this procedure — all 47 deliberately remain.

It is re-enabled in step 7 and verified in step 8.

---

## Procedure

Every step begins by sourcing the manifest written in step 0, so no path is ever
retyped or remembered. Every step is `set -euo pipefail` and exits non-zero on the
first failed assertion.

> **Every `ssh` here is `ssh -n`, and it is not optional.** `ssh` reads stdin, and
> inside a `bash <<EOS` block **stdin is the script itself** — so an un-`-n` ssh
> consumes the remaining steps and the shell exits **0**. Demonstrated during the
> step-0 run: without `-n`, the line after an ssh never executes; with `-n`, it
> does. A procedure that silently skips its own assertions and reports success is
> the exact failure mode this document exists to prevent. This is the same defect
> class as the `stdin=DEVNULL` fix in `dobby/agent_runner.py` (2026-04-24).

### Step 0 — pre-flight: durable paths, type-safe export, mechanical assertions

```bash
set -euo pipefail
RUN=$(date +%Y%m%d-%H%M%S)
OUT="$HOME/backups/casefold-$RUN"
mkdir -p "$OUT"
cat > "$OUT/MANIFEST.env" <<EOF
RUN=$RUN
OUT=$OUT
QUAR=/home/dobby/.vault-casefold-trash/$RUN
NUC=dobby@192.168.1.69
NUC_VAULT=/home/dobby/Documents/Notes
MAC_VAULT=$HOME/Documents/Notes
LOWER='Organizations/Regenerate cascadia.md'
UPPER='Organizations/Regenerate Cascadia.md'
LOWER_RID='orn:koi-net.vault-file:Organizations/Regenerate cascadia.md'
MAC_STALE_ID=3354
NUC_LOWER_ID=3335
CANON_SHA=38ecc42bc96f46b6767eff248dc09fc0ed0e4e57ca1f882c05153b8603d2c9be
STALE_SHA=ed88466c2fd07942e6b6056a6392b6c4f025b8f1760b81e81109142a0d05c9b4
CANON_SIZE=6262
STALE_SIZE=5171
EVENT_HISTORY_EXPECTED=47
EOF
echo "manifest: $OUT/MANIFEST.env"
```

`QUAR` is fixed here and used verbatim by step 2 and by the rollback. **Neither may
compute its own timestamp** — an earlier draft did, and the rollback then pointed at
a directory that step 2 had never created.

**Export with `COPY`, not `row_to_json`.** `COPY` text format round-trips `jsonb`,
`text[]`, `uuid` and `timestamptz` exactly; a JSON dump rebuilt through Python
`str()` does not (see Rollback).

```bash
set -euo pipefail
. "$OUT/MANIFEST.env"

psql personal_koi -v ON_ERROR_STOP=1 \
  -c "\copy (SELECT * FROM vault_sync_state WHERE id = $MAC_STALE_ID) TO '$OUT/mac_stale_row.tsv'"

ssh -n "$NUC" "psql personal_koi -v ON_ERROR_STOP=1 -c \"\\\\copy (SELECT * FROM vault_sync_state WHERE id = $NUC_LOWER_ID) TO '/tmp/nuc_lower_row.tsv'\""
scp "$NUC:/tmp/nuc_lower_row.tsv" "$OUT/nuc_lower_row.tsv"

# all 47 event rows, exported as a record even though none are deleted
ssh -n "$NUC" "psql personal_koi -v ON_ERROR_STOP=1 -c \"\\\\copy (SELECT * FROM koi_net_events WHERE rid = '$LOWER_RID') TO '/tmp/nuc_events.tsv'\""
scp "$NUC:/tmp/nuc_events.tsv" "$OUT/nuc_events.tsv"

scp "$NUC:$NUC_VAULT/$LOWER" "$OUT/lower.md"
```

**Assertions — every one fails the run.**

```bash
set -euo pipefail
. "$OUT/MANIFEST.env"
fail() { echo "PREFLIGHT FAIL: $*" >&2; exit 1; }

[ "$(wc -l < "$OUT/mac_stale_row.tsv")" -eq 1 ] || fail "mac_stale_row.tsv != 1 line"
[ "$(wc -l < "$OUT/nuc_lower_row.tsv")" -eq 1 ] || fail "nuc_lower_row.tsv != 1 line"
[ "$(wc -l < "$OUT/nuc_events.tsv")" -eq "$EVENT_HISTORY_EXPECTED" ] \
  || fail "event history != $EVENT_HISTORY_EXPECTED rows"
[ "$(shasum -a 256 "$OUT/lower.md" | cut -d' ' -f1)" = "$CANON_SHA" ] \
  || fail "exported file sha != CANON_SHA"

# the two NUC files must be byte-identical BEFORE anything moves
NUC_UNIQ=$(ssh -n "$NUC" "cd '$NUC_VAULT/Organizations' && sha256sum 'Regenerate cascadia.md' 'Regenerate Cascadia.md' | cut -d' ' -f1 | sort -u | wc -l")
[ "$NUC_UNIQ" -eq 1 ] || fail "NUC duplicates are NOT byte-identical; this procedure assumes they are"

# the safety property, not a historical-row count
PEND=$(ssh -n "$NUC" "psql personal_koi -tAc \"SELECT count(*) FROM koi_net_events WHERE rid = '$LOWER_RID' AND expires_at>now() AND NOT (COALESCE(target_node,'') = ANY(COALESCE(delivered_to,ARRAY[]::text[])));\"" | tr -d ' ')
[ "$PEND" -eq 0 ] || fail "$PEND unexpired events pending delivery for the lowercase RID"

echo "PREFLIGHT OK — run $RUN"
```

Then run **both** restore proofs below (MacBook and NUC) before touching anything.

### Step 1 — disable NUC vault sync

```bash
set -euo pipefail
. "$OUT/MANIFEST.env"
ssh -n "$NUC" 'set -euo pipefail
  cd ~/projects/RegenAI/koi-processor
  cp config/personal.env "config/personal.env.bak-casefold-'"$RUN"'"
  sed -i "s|^VAULT_SYNC_ENABLED=true|VAULT_SYNC_ENABLED=false|" config/personal.env
  sudo systemctl restart dobby-koi-processor
  sleep 12
  PID=$(systemctl show dobby-koi-processor -p MainPID --value)
  LIVE=$(tr "\0" "\n" < /proc/$PID/environ | grep "^VAULT_SYNC_ENABLED=" | cut -d= -f2)
  [ "$LIVE" = "false" ] || { echo "FAIL: live env is $LIVE"; exit 1; }
  echo "NUC sync disabled (live env verified)"'
```

Asserts the **process** environment, not the file.

### Step 2 — quarantine the NUC duplicate (fail-fast, move never delete)

```bash
set -euo pipefail
. "$OUT/MANIFEST.env"

ssh -n "$NUC" "set -euo pipefail
  QUAR='$QUAR'; V='$NUC_VAULT'; CANON='$CANON_SHA'; SIZE=$CANON_SIZE
  cd \"\$V/Organizations\"

  # 1. BOTH files must hash to CANON before anything moves.
  L=\$(sha256sum 'Regenerate cascadia.md' | cut -d' ' -f1)
  U=\$(sha256sum 'Regenerate Cascadia.md' | cut -d' ' -f1)
  [ \"\$L\" = \"\$CANON\" ] || { echo \"FAIL lowercase sha \$L != \$CANON\"; exit 1; }
  [ \"\$U\" = \"\$CANON\" ] || { echo \"FAIL uppercase sha \$U != \$CANON\"; exit 1; }

  # 2. move into the EXACT manifest path
  mkdir -p \"\$QUAR\"
  mv 'Regenerate cascadia.md' \"\$QUAR/\"

  # 3. post-conditions
  [ ! -e 'Regenerate cascadia.md' ] || { echo 'FAIL lowercase still present'; exit 1; }
  [ -e 'Regenerate Cascadia.md' ]   || { echo 'FAIL uppercase missing'; exit 1; }
  U2=\$(sha256sum 'Regenerate Cascadia.md' | cut -d' ' -f1)
  [ \"\$U2\" = \"\$CANON\" ] || { echo \"FAIL uppercase sha changed: \$U2\"; exit 1; }
  S2=\$(stat -c %s 'Regenerate Cascadia.md')
  [ \"\$S2\" -eq \"\$SIZE\" ] || { echo \"FAIL uppercase size \$S2 != \$SIZE\"; exit 1; }
  N=\$(ls -1 | grep -ic '^regenerate cascadia\.md\$')
  [ \"\$N\" -eq 1 ] || { echo \"FAIL case-insensitive matches = \$N, expected 1\"; exit 1; }
  [ -f \"\$QUAR/Regenerate cascadia.md\" ] || { echo 'FAIL quarantined copy missing'; exit 1; }

  echo \"STEP2 OK — quarantined to \$QUAR\""
```

`set -euo pipefail` plus an explicit exit on each check; the closing `echo` is
reached only if every assertion passed. The case-insensitive count is asserted as
**exactly 1** rather than eyeballed from `grep` output (it is **2** today).

### Step 3 — NUC: delete the lowercase state row (events untouched)

```sql
BEGIN;
DO $$
DECLARE n int;
BEGIN
  DELETE FROM vault_sync_state
   WHERE id              = 3335
     AND relative_path   = 'Organizations/Regenerate cascadia.md'
     AND content_hash    = '38ecc42bc96f46b6767eff248dc09fc0ed0e4e57ca1f882c05153b8603d2c9be'
     AND origin_seq      = 129
     AND local_edit_seq  = 129
     AND file_size_bytes = 6262
     AND is_deleted      = FALSE;
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN
    RAISE EXCEPTION 'NUC vault_sync_state: expected 1 row, got % — state moved, abort', n;
  END IF;
  RAISE NOTICE 'NUC: 1 state row deleted (id=3335)';
END $$;
COMMIT;
```

**No event rows are deleted.** The uppercase row (`id=123692`, seq 6/6) is untouched.

### Step 4 — MacBook: delete the stale row only

```sql
BEGIN;
DO $$
DECLARE n int;
BEGIN
  DELETE FROM vault_sync_state
   WHERE id              = 3354
     AND relative_path   = 'Organizations/Regenerate cascadia.md'
     AND content_hash    = 'ed88466c2fd07942e6b6056a6392b6c4f025b8f1760b81e81109142a0d05c9b4'
     AND origin_seq      = 229
     AND local_edit_seq  = 229
     AND file_size_bytes = 5171
     AND is_deleted      = FALSE;
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN
    RAISE EXCEPTION 'MacBook vault_sync_state: expected 1 row, got % — state moved, abort', n;
  END IF;
  RAISE NOTICE 'MacBook: 1 stale row deleted (id=3354)';
END $$;
COMMIT;
```

`file_size_bytes = 5171` against 6,262 on disk: the stale row describes a
**different, smaller** file. It is leftover content, not a duplicate of the current
row. Nothing on the MacBook filesystem is touched.

### Step 5 — verify before re-enabling (mechanical)

```bash
set -euo pipefail
. "$OUT/MANIFEST.env"
fail() { echo "VERIFY FAIL: $*" >&2; exit 1; }

# 1. DB case-fold collisions — output must be EMPTY on both nodes
Q="SELECT lower(relative_path) FROM vault_sync_state WHERE is_deleted=FALSE GROUP BY 1 HAVING count(*)>1;"
# Capture separately: a psql that FAILS prints nothing, and `[ -z "$(...)" ]`
# swallows its exit code — an error would read as "no collisions".
MACCOL=$(psql personal_koi -v ON_ERROR_STOP=1 -tAc "$Q") || fail "MacBook collision query failed"
NUCCOL=$(ssh -n "$NUC" "psql personal_koi -v ON_ERROR_STOP=1 -tAc \"$Q\"") || fail "NUC collision query failed"
[ -z "$(printf %s "$MACCOL" | tr -d '[:space:]')" ] || fail "MacBook DB collision remains: $MACCOL"
[ -z "$(printf %s "$NUCCOL" | tr -d '[:space:]')" ] || fail "NUC DB collision remains: $NUCCOL"

# 2. NUC filesystem collisions across all 7 folders — output must be EMPTY
FS=$(ssh -n "$NUC" 'cd ~/Documents/Notes && for d in Shared Meetings People Organizations Projects Locations Bridges; do
  [ -d "$d" ] || continue
  find "$d" -type f -name "*.md" | awk "{print tolower(\$0)\"\t\"\$0}" | sort |
    awk -F"\t" "{if(\$1==p) print \"COLLISION \"po\" <> \"\$2; p=\$1; po=\$2}"
done') || fail "NUC filesystem audit failed to run"
[ -z "$(printf %s "$FS" | tr -d '[:space:]')" ] || fail "NUC filesystem collision remains: $FS"

# 3. safety property: zero unexpired events PENDING DELIVERY for the lowercase RID
PEND=$(ssh -n "$NUC" "psql personal_koi -tAc \"SELECT count(*) FROM koi_net_events WHERE rid = '$LOWER_RID' AND expires_at>now() AND NOT (COALESCE(target_node,'') = ANY(COALESCE(delivered_to,ARRAY[]::text[])));\"" | tr -d ' ')
[ "$PEND" -eq 0 ] || fail "$PEND unexpired events pending delivery"

# 4. audit history must still be exactly 47 — a DROP means something deleted history
HIST=$(ssh -n "$NUC" "psql personal_koi -tAc \"SELECT count(*) FROM koi_net_events WHERE rid = '$LOWER_RID';\"" | tr -d ' ')
[ "$HIST" -eq "$EVENT_HISTORY_EXPECTED" ] || fail "event history is $HIST, expected $EVENT_HISTORY_EXPECTED"

# 5. canonical file intact on BOTH nodes — asserted, not printed
MS=$(shasum -a 256 "$MAC_VAULT/$UPPER" | cut -d' ' -f1)
MZ=$(wc -c < "$MAC_VAULT/$UPPER" | tr -d ' ')   # NOT stat — see note below
[ "$MS" = "$CANON_SHA" ]   || fail "MacBook canonical sha $MS"
[ "$MZ" -eq "$CANON_SIZE" ] || fail "MacBook canonical size $MZ"
NS=$(ssh -n "$NUC" "sha256sum '$NUC_VAULT/$UPPER' | cut -d' ' -f1")
NZ=$(ssh -n "$NUC" "stat -c %s '$NUC_VAULT/$UPPER'")
[ "$NS" = "$CANON_SHA" ]   || fail "NUC canonical sha $NS"
[ "$NZ" -eq "$CANON_SIZE" ] || fail "NUC canonical size $NZ"

# 6. detectors — their exit codes are the assertion
ssh -n "$NUC" 'python3 ~/bin/vault_sync_detectors.py --vault ~/Documents/Notes --quiet' || fail "NUC detector tripped"
python3 ~/projects/dobby/scripts/vault_sync_detectors.py --quiet || fail "MacBook detector tripped"

echo "STEP5 VERIFY OK"
```

> **Do not use `stat` for the MacBook size check.** `/usr/local/bin/stat` (GNU
> coreutils) shadows `/usr/bin/stat` on this machine, and **both** `stat -f %z`
> (BSD form) and `stat -c %s` (GNU form) return an **empty string** here. During the
> live run this made check 5a report FAIL with the sha matching exactly and
> `size=` blank — a broken measurement masquerading as a data fault, which is
> precisely the kind of false signal that gets a healthy state rolled back.
> `wc -c` and `ls -l` both return 6262. The NUC side keeps `stat -c %s`, which works
> correctly on Linux.

> **This step makes many SSH round-trips and can outlive a command timeout.** The
> live run was killed with **exit 137 (SIGKILL)** after four of six check groups.
> That is an execution artifact, **not** a failed assertion, and the two are not
> interchangeable: `set -e` aborts on a failed guard and prints which one, whereas a
> SIGKILL simply stops with no verdict. **Distinguish them before rolling anything
> back** — re-run the remaining groups individually and let them return their own
> verdicts. Rolling back a healthy state because the harness ran out of time is its
> own failure. Prefer running the six groups as separate invocations.

### Step 6 — MacBook reconcile, read-only

**The HTTP endpoint cannot be used here.** `POST /koi-net/vault-sync/reconcile`
returns `{"error": "Vault sync is not enabled"}` while the MacBook is off, and
enabling it to run the check is exactly what this step exists to gate. Instead,
instantiate the real `VaultSyncManager` and call the real `reconcile(mode="detect")`
— the actual code path, not a reimplementation of it — with an event queue that
**raises** if anything attempts to queue, which proves the run emitted nothing:

```bash
. "$OUT/MANIFEST.env"
cd ~/projects/koi-processor-service
NODE_RID='orn:koi-net.node:darren-personal+80e26aab6b59178cd605c93b1aa0b903e61a283ee2a4ace07da3d1fabdd779f6'

KOI_VAULT_READONLY_PATHS="$(grep '^KOI_VAULT_READONLY_PATHS' config/personal.env | cut -d= -f2-)" \
KOI_VAULT_EXCLUDE_PATHS="$(grep '^KOI_VAULT_EXCLUDE_PATHS' config/personal.env | cut -d= -f2- | tr -d "'")" \
~/venvs/koi-server/bin/python - "$NODE_RID" "$MAC_VAULT" > "$OUT/step6_reconcile_detect.json" <<'PY'
import asyncio, json, sys
sys.path.insert(0, '.')
import asyncpg
from api.vault_sync import VaultSyncManager

node_rid, vault_path = sys.argv[1], sys.argv[2]

class _NullQueue:                      # detect mode must not emit
    async def add(self, *a, **k):
        raise AssertionError("detect mode must not queue events")

async def main():
    pool = await asyncpg.create_pool(database="personal_koi", min_size=1, max_size=2)
    mgr = VaultSyncManager(pool=pool, node_rid=node_rid, event_queue=_NullQueue(), vault_path=vault_path)
    print(json.dumps(await mgr.reconcile(mode="detect"), indent=2, default=str))
    await pool.close()

asyncio.run(main())
PY
```

The environment variables must be passed explicitly — the exclusion and readonly
path lists are read via `os.getenv` at import, and a bare interpreter has neither,
which would silently widen the comparison. `node_rid` is hardcoded because the
MacBook has **no self-row** in `koi_net_nodes`; a `SELECT` for it returns empty.

Expected `missing_on_disk`:

```
Meetings/Civic Intelligence Engine/2026-08-05 - CIE Reconvene.md
```

**Exactly one entry, and it must be that one.** Two entries means step 4 did not
take. A different entry means something else changed and this procedure is stale —
stop and re-derive.

### Step 7 — re-enable NUC vault sync (exact, guarded)

```bash
set -euo pipefail
. "$OUT/MANIFEST.env"

# Marker as an EPOCH, taken on the NUC, immediately before the restart. See step 8.
MARK_EPOCH=$(ssh -n "$NUC" 'date +%s')
echo "MARK_EPOCH=$MARK_EPOCH" >> "$OUT/MANIFEST.env"

ssh -n "$NUC" 'set -euo pipefail
  cd ~/projects/RegenAI/koi-processor
  sed -i "s|^VAULT_SYNC_ENABLED=false|VAULT_SYNC_ENABLED=true|" config/personal.env
  sudo systemctl restart dobby-koi-processor
  sleep 12
  systemctl is-active dobby-koi-processor >/dev/null || { echo "FAIL: service not active"; exit 1; }
  PID=$(systemctl show dobby-koi-processor -p MainPID --value)
  LIVE=$(tr "\0" "\n" < /proc/$PID/environ | grep "^VAULT_SYNC_ENABLED=" | cut -d= -f2)
  [ "$LIVE" = "true" ] || { echo "FAIL: live env is $LIVE"; exit 1; }
  echo "NUC sync re-enabled (live env verified)"'
```

### Step 8 — post-enable verification (epoch-correct, positive control first)

**Two timezone traps, both hit for real today.**

* `journalctl --since` reads **OS-local** time. A UTC marker returned an empty
  journal that read as healthy — every `grep -c` below would have said a reassuring
  `0`.
* PostgreSQL on the NUC runs `TimeZone = Etc/UTC` while the OS is **PDT**. A
  NUC-local marker string in SQL is read as UTC, i.e. **7 hours early**. Measured
  live: with a marker of `2026-08-26 18:14:25`, `FORGETs > local-string` = **1**
  and `FORGETs > to_timestamp(epoch)` = **0**. The string form would have failed a
  healthy run on the legitimate CIE FORGET from hours earlier.

**One epoch value drives both.** `journalctl --since "@$MARK_EPOCH"` and
`to_timestamp($MARK_EPOCH)` are unambiguous everywhere.

```bash
set -euo pipefail
. "$OUT/MANIFEST.env"          # provides MARK_EPOCH from step 7
fail() { echo "VERIFY FAIL: $*" >&2; exit 1; }
jc() { ssh -n "$NUC" "journalctl -u dobby-koi-processor --since '@$MARK_EPOCH' --no-pager"; }

# 1. POSITIVE CONTROL — a scan must be PROVEN to have run before any zero counts.
SCANS=0
for _ in $(seq 1 60); do
  PROBE=$(jc) || fail "journal read failed during positive control"
  SCANS=$(printf %s "$PROBE" | grep -c 'vault_sync.scan_complete' || true)
  [ "$SCANS" -ge 1 ] && break
  sleep 10
done
[ "$SCANS" -ge 1 ] || fail "no vault_sync.scan_complete after @$MARK_EPOCH — the scan never ran, or the window is wrong. Every count below would be a false zero."
echo "positive control OK: $SCANS scan_complete"

# 2. Capture the journal ONCE and make a read failure fatal. Counting straight
#    off `jc |` would turn a dropped SSH connection into three reassuring zeros.
LOG=$(jc) || fail "journal read failed — cannot evaluate any signal below"
[ -n "$LOG" ] || fail "journal returned empty after a proven scan_complete — window is wrong"

[ "$(printf %s "$LOG" | grep -c 'vault_sync.scan_capped')"         -eq 0 ] || fail "scan_capped > 0"
[ "$(printf %s "$LOG" | grep -c 'vault_sync.tombstone_blocked')"    -eq 0 ] || fail "tombstone_blocked > 0 — STOP"
[ "$(printf %s "$LOG" | grep -c 'vault_sync.absence_unverifiable')" -eq 0 ] || fail "absence_unverifiable > 0"

# 3. THE forbidden event: a FORGET for the exact lowercase Vault-file RID. Scoped
#    deliberately — "any FORGET" was already proven the wrong discriminator, since a
#    genuine deletion must produce one.
BAD=$(ssh -n "$NUC" "psql personal_koi -tAc \"SELECT count(*) FROM koi_net_events WHERE event_type='FORGET' AND rid = '$LOWER_RID' AND queued_at > to_timestamp($MARK_EPOCH);\"" | tr -d ' ')
[ "$BAD" -eq 0 ] || fail "$BAD FORGET(s) emitted for the lowercase RID — this is the event the whole procedure exists to prevent"

# 4. Other FORGETs are RECORDED for review, not failed on. Armed deletions are the
#    detectors' job: a FORGET whose path still exists is what actually matters.
OTHER=$(ssh -n "$NUC" "psql personal_koi -tAc \"SELECT count(*) FROM koi_net_events WHERE event_type='FORGET' AND rid <> '$LOWER_RID' AND queued_at > to_timestamp($MARK_EPOCH);\"" | tr -d ' ')
echo "NOTE: $OTHER other FORGET(s) since the marker — review, do not auto-fail:"
ssh -n "$NUC" "psql personal_koi -tAc \"SELECT rid FROM koi_net_events WHERE event_type='FORGET' AND rid <> '$LOWER_RID' AND queued_at > to_timestamp($MARK_EPOCH);\""

# 5. Detectors decide whether any of those are armed.
ssh -n "$NUC" 'python3 ~/bin/vault_sync_detectors.py --vault ~/Documents/Notes' || fail "NUC detector tripped"
python3 ~/projects/dobby/scripts/vault_sync_detectors.py || fail "MacBook detector tripped"

# 6. Safety property again, post-enable.
PEND=$(ssh -n "$NUC" "psql personal_koi -tAc \"SELECT count(*) FROM koi_net_events WHERE rid = '$LOWER_RID' AND expires_at>now() AND NOT (COALESCE(target_node,'') = ANY(COALESCE(delivered_to,ARRAY[]::text[])));\"" | tr -d ' ')
[ "$PEND" -eq 0 ] || fail "$PEND unexpired events pending delivery"

echo "POST-ENABLE VERIFY OK"
```

`vault_sync.scan_complete` is emitted at `api/vault_sync.py:1524` with
`folder=… files_changed=… events_queued=… duration_ms=…` — a real completion line.
Observed 175 times in a 30-minute window, so the control is frequent, not rare.

### Step 9 — fresh snapshot, followed through all three copies

Immediately before any MacBook enable.

```bash
set -euo pipefail
. "$OUT/MANIFEST.env"
fail() { echo "SNAPSHOT FAIL: $*" >&2; exit 1; }

ssh -n "$NUC" 'systemctl start dobby-vault-autocommit.service || sudo systemctl start dobby-vault-autocommit.service; sleep 5'

# The snapshot is worthless if the tree still holds uncommitted changes — those
# are exactly the bytes not protected by the bare repo or the mirror.
DIRTY=$(ssh -n "$NUC" 'cd ~/Documents/Notes && git status --porcelain') || fail "could not read NUC git status"
[ -z "$DIRTY" ] || fail "NUC working tree is NOT clean; uncommitted paths are unprotected:
$DIRTY"

# FULL 40-char SHAs. Abbreviations can collide and, worse, differ in length
# between repos, so a string compare of short forms can pass on unequal commits.
WORK=$(ssh -n "$NUC" 'cd ~/Documents/Notes && git rev-parse HEAD')                    || fail "working tree rev-parse failed"
BARE=$(ssh -n "$NUC" 'cd ~/backups/git/vault-nuc.git && git rev-parse refs/heads/main') || fail "bare repo rev-parse failed"
~/.local/bin/mirror-nuc-vault.sh || fail "mirror pull failed"
MIRR=$(cd ~/backups/git/vault-nuc-mirror.git && git rev-parse refs/heads/main)     || fail "mirror rev-parse failed"
for v in "$WORK" "$BARE" "$MIRR"; do
  [ ${#v} -eq 40 ] || fail "expected a full 40-char SHA, got '$v'"
done

echo "  working tree : $WORK"
echo "  bare repo    : $BARE"
echo "  off-machine  : $MIRR"
[ "$WORK" = "$BARE" ] && [ "$BARE" = "$MIRR" ] || fail "snapshot SHAs diverge"
echo "SNAPSHOT OK — same SHA in all three"
```

The mirror had no schedule until 2026-08-26 and went 3.6 h stale within a day.
Assert it; do not assume it.

## Rollback — type-safe, and proven before it is needed

An earlier draft emitted `INSERT`s from `row_to_json` by stringifying each value in
Python. That cannot reconstruct this schema: `koi_net_events` has `manifest jsonb`,
`contents jsonb`, `delivered_to text[]`, `confirmed_by text[]`, `event_id uuid`,
`queued_at`/`expires_at timestamptz`. Python `str()` on a parsed list yields
`['a', 'b']`, which is not valid Postgres array input. The restore would have
failed, or silently written malformed values.

**`COPY` round-trips all of those.** The step-0 exports are already in that format.

### Proof 1 — MacBook (run during step 0, before anything is deleted)

```bash
set -euo pipefail
. "$OUT/MANIFEST.env"
psql personal_koi -v ON_ERROR_STOP=1 <<SQL
BEGIN;
CREATE TEMP TABLE _orig AS SELECT * FROM vault_sync_state WHERE id = $MAC_STALE_ID;
DO $$
BEGIN
  IF (SELECT count(*) FROM _orig) <> 1 THEN
    RAISE EXCEPTION 'snapshot: expected exactly 1 row';
  END IF;
END $$;

DELETE FROM vault_sync_state WHERE id = $MAC_STALE_ID;
\copy vault_sync_state FROM '$OUT/mac_stale_row.tsv'

DO $$
DECLARE d int;
BEGIN
  SELECT count(*) INTO d FROM (
      (SELECT * FROM vault_sync_state WHERE id = $MAC_STALE_ID EXCEPT SELECT * FROM _orig)
      UNION ALL
      (SELECT * FROM _orig EXCEPT SELECT * FROM vault_sync_state WHERE id = $MAC_STALE_ID)
  ) t;
  IF d <> 0 THEN
    RAISE EXCEPTION 'restore is NOT exact: % differing row(s)', d;
  END IF;
  RAISE NOTICE 'MAC RESTORE PROVEN: round-trip exact';
END $$;
ROLLBACK;
SQL
```

### Proof 2 — NUC (written out in full; do not improvise it)

```bash
set -euo pipefail
. "$OUT/MANIFEST.env"
scp "$OUT/nuc_lower_row.tsv" "$NUC:/tmp/nuc_lower_row.tsv"
ssh -n "$NUC" "psql personal_koi -v ON_ERROR_STOP=1 <<SQL
BEGIN;
CREATE TEMP TABLE _orig AS SELECT * FROM vault_sync_state WHERE id = $NUC_LOWER_ID;
DO $$
BEGIN
  IF (SELECT count(*) FROM _orig) <> 1 THEN
    RAISE EXCEPTION 'snapshot: expected exactly 1 row';
  END IF;
END $$;

DELETE FROM vault_sync_state WHERE id = $NUC_LOWER_ID;
\\copy vault_sync_state FROM '/tmp/nuc_lower_row.tsv'

DO $$
DECLARE d int;
BEGIN
  SELECT count(*) INTO d FROM (
      (SELECT * FROM vault_sync_state WHERE id = $NUC_LOWER_ID EXCEPT SELECT * FROM _orig)
      UNION ALL
      (SELECT * FROM _orig EXCEPT SELECT * FROM vault_sync_state WHERE id = $NUC_LOWER_ID)
  ) t;
  IF d <> 0 THEN
    RAISE EXCEPTION 'restore is NOT exact: % differing row(s)', d;
  END IF;
  RAISE NOTICE 'NUC RESTORE PROVEN: round-trip exact';
END $$;
ROLLBACK;
SQL"
```

Either proof failing raises an exception and rolls back. **Both must print PROVEN
before step 1.**

> **Why `DO … RAISE EXCEPTION` and not `CASE WHEN … ELSE (SELECT 1/0)`.** The first
> version of these proofs used the `CASE` form and **failed on a healthy row**:
> PostgreSQL folds the constant subquery `(SELECT 1/0)` at plan time, so the ELSE
> branch raises even when the condition is true. Observed live — the MacBook proof
> errored `division by zero` immediately after `SELECT 1` confirmed the snapshot had
> exactly one row. This is the same trap that broke the Change-1 guard earlier the
> same day; `GET DIAGNOSTICS` / `RAISE EXCEPTION` inside a `DO` block is the form
> that actually works.

### Actual rollback, in reverse order

```bash
set -euo pipefail
. "$OUT/MANIFEST.env"

# 4'. MacBook stale row
psql personal_koi -v ON_ERROR_STOP=1 -c "\copy vault_sync_state FROM '$OUT/mac_stale_row.tsv'"

# 3'. NUC lowercase row
scp "$OUT/nuc_lower_row.tsv" "$NUC:/tmp/nuc_lower_row.tsv"
ssh -n "$NUC" "psql personal_koi -v ON_ERROR_STOP=1 -c \"\\\\copy vault_sync_state FROM '/tmp/nuc_lower_row.tsv'\""

# 2'. the quarantined file, from the manifest's exact QUAR
ssh -n "$NUC" "mv '$QUAR/Regenerate cascadia.md' '$NUC_VAULT/$LOWER'"

# 1'. re-enable NUC sync if rolling back mid-procedure (step 7)
```

No event rows are ever deleted, so there is nothing to restore in `koi_net_events`;
`$OUT/nuc_events.tsv` is retained as a record only.

The quarantine directory is never emptied by this procedure. Remove it only after
the MacBook has been enabled and soaked clean.

## Explicitly out of scope

* The CIE stub row `Meetings/Civic Intelligence Engine/2026-08-05 - CIE Reconvene.md`
  (241 bytes, hash `b9845ff3…`, seq 5/5). The canonical 60,262-byte note without the
  hyphen exists on both nodes; the NUC already tombstoned the hyphenated path and
  emitted its one legitimate FORGET during the soak. **Left deliberately** as the
  expected single FORGET of the controlled first MacBook scan.
* Enabling MacBook vault sync. Separate, explicit approval.
* Deleting any event row. All 47 remain.
* Any other rename, tombstone, event purge, or edge change.
