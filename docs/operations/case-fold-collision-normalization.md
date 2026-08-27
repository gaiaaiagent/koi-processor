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
2. The existing queued lowercase `UPDATE` must be removed, and removing it while
   the emitter can regenerate it is pointless.

It is re-enabled in step 7 and verified in step 8.

---

## Procedure

### Step 0 — pre-flight: durable paths, type-safe export, mechanical assertions

Paths are recorded to a manifest so every later step and the rollback reference the
same location instead of a remembered one.

```bash
set -euo pipefail
RUN=$(date +%Y%m%d-%H%M%S)
OUT="$HOME/backups/casefold-$RUN"
QUAR="/home/dobby/.vault-casefold-trash/$RUN"          # created in step 2
mkdir -p "$OUT"
cat > "$OUT/MANIFEST.env" <<EOF
RUN=$RUN
OUT=$OUT
QUAR=$QUAR
LOWER='Organizations/Regenerate cascadia.md'
UPPER='Organizations/Regenerate Cascadia.md'
MAC_STALE_ID=3354
NUC_LOWER_ID=3335
CANON_SHA=38ecc42bc96f46b6767eff248dc09fc0ed0e4e57ca1f882c05153b8603d2c9be
STALE_SHA=ed88466c2fd07942e6b6056a6392b6c4f025b8f1760b81e81109142a0d05c9b4
EOF
echo "manifest: $OUT/MANIFEST.env"
```

**Export with `COPY`, not `row_to_json`.** `COPY` text format round-trips `jsonb`,
`text[]`, `uuid` and `timestamptz` exactly; a JSON dump reconstructed through
Python `str()` does not (correction 3).

```bash
. "$OUT/MANIFEST.env"

psql personal_koi -c "\copy (SELECT * FROM vault_sync_state WHERE id = $MAC_STALE_ID) TO '$OUT/mac_stale_row.tsv'"

ssh dobby@192.168.1.69 "psql personal_koi -c \"\\copy (SELECT * FROM vault_sync_state WHERE id = $NUC_LOWER_ID) TO '/tmp/nuc_lower_row.tsv'\""
scp dobby@192.168.1.69:/tmp/nuc_lower_row.tsv "$OUT/nuc_lower_row.tsv"

# full 47-row event history, exported for the record even though none are deleted
ssh dobby@192.168.1.69 "psql personal_koi -c \"\\copy (SELECT * FROM koi_net_events WHERE rid LIKE '%Regenerate cascadia.md') TO '/tmp/nuc_events.tsv'\""
scp dobby@192.168.1.69:/tmp/nuc_events.tsv "$OUT/nuc_events.tsv"

scp "dobby@192.168.1.69:/home/dobby/Documents/Notes/$LOWER" "$OUT/lower.md"
```

**Mechanical assertions — the script must exit non-zero on any failure.**

```bash
. "$OUT/MANIFEST.env"
fail() { echo "PREFLIGHT FAIL: $*" >&2; exit 1; }

[ "$(wc -l < "$OUT/mac_stale_row.tsv")"  -eq 1 ]  || fail "mac_stale_row.tsv != 1 line"
[ "$(wc -l < "$OUT/nuc_lower_row.tsv")"  -eq 1 ]  || fail "nuc_lower_row.tsv != 1 line"
[ "$(wc -l < "$OUT/nuc_events.tsv")"     -eq 47 ] || fail "event history != 47 rows"
[ "$(shasum -a 256 "$OUT/lower.md" | cut -d' ' -f1)" = "$CANON_SHA" ] || fail "exported file sha != canonical"

# the two NUC files must be byte-identical BEFORE anything is moved
NUC_SHAS=$(ssh dobby@192.168.1.69 "cd '/home/dobby/Documents/Notes/Organizations' && sha256sum 'Regenerate cascadia.md' 'Regenerate Cascadia.md' | cut -d' ' -f1 | sort -u | wc -l")
[ "$NUC_SHAS" -eq 1 ] || fail "NUC duplicates are NOT byte-identical — stop, this procedure assumes they are"

# the safety property, not a historical-row count
PEND=$(ssh dobby@192.168.1.69 "psql personal_koi -tAc \"SELECT count(*) FROM koi_net_events WHERE rid LIKE '%Regenerate cascadia.md' AND expires_at>now() AND NOT (COALESCE(target_node,'') = ANY(COALESCE(delivered_to,ARRAY[]::text[])));\"" | tr -d ' ')
[ "$PEND" -eq 0 ] || fail "there are $PEND unexpired events pending delivery for this RID"

echo "PREFLIGHT OK — run $RUN"
```

Record `$OUT/MANIFEST.env` in the execution log. Every subsequent step begins with
`. "$OUT/MANIFEST.env"`.

### Step 1 — disable NUC vault sync

```bash
ssh dobby@192.168.1.69 '
  cd ~/projects/RegenAI/koi-processor
  cp config/personal.env config/personal.env.bak-casefold-$(date +%Y%m%d-%H%M%S)
  sed -i "s|^VAULT_SYNC_ENABLED=true|VAULT_SYNC_ENABLED=false|" config/personal.env
  sudo systemctl restart dobby-koi-processor && sleep 12
  PID=$(systemctl show dobby-koi-processor -p MainPID --value)
  tr "\0" "\n" < /proc/$PID/environ | grep ^VAULT_SYNC_ENABLED='
```
**Assert:** live env reads `VAULT_SYNC_ENABLED=false`. Not the file — the process.

### Step 2 — quarantine the NUC duplicate (move, never delete)

```bash
ssh dobby@192.168.1.69 '
  Q=~/.vault-casefold-trash/$(date +%Y%m%d-%H%M%S); mkdir -p "$Q"
  cd ~/Documents/Notes/Organizations
  sha256sum "Regenerate cascadia.md" "Regenerate Cascadia.md"    # must be identical
  mv "Regenerate cascadia.md" "$Q/"
  ls -la | grep -i "regenerate cascadia"                          # expect ONE file
  echo "$Q"'
```
**Assert:** the two sha256 values are identical **before** the move, and exactly
one file matches afterwards. `Regenerate Cascadia.md` (inode `6470157`) survives.

### Step 3 — NUC: delete the lowercase state row (events untouched)

Guards on the **complete** 64-character hash plus row id, path, both sequences and
size. Any drift matches zero rows and aborts.

```sql
BEGIN;
DO $$
DECLARE n int;
BEGIN
  DELETE FROM vault_sync_state
   WHERE id                = 3335
     AND relative_path     = 'Organizations/Regenerate cascadia.md'
     AND content_hash      = '38ecc42bc96f46b6767eff248dc09fc0ed0e4e57ca1f882c05153b8603d2c9be'
     AND origin_seq        = 129
     AND local_edit_seq    = 129
     AND file_size_bytes   = 6262
     AND is_deleted        = FALSE;
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN
    RAISE EXCEPTION 'NUC vault_sync_state: expected 1 row, got % — state moved since the dry run, abort', n;
  END IF;
  RAISE NOTICE 'NUC: 1 state row deleted (id=3335)';
END $$;
COMMIT;
```

**No event rows are deleted.** See "Events: leave all 47 to age out".

The uppercase row (`id=123692`, seq 6/6) is untouched and continues to track the
surviving file.

### Step 4 — MacBook: delete the stale row only

**Corrected.** The earlier draft guarded on `left(content_hash,16) = 'ed88466c2fd079'`
— a **14-character** literal against a 16-character value. It could never match, so
the delete would have aborted on every run. Guard on the full hash and the row's
complete identity instead.

```sql
BEGIN;
DO $$
DECLARE n int;
BEGIN
  DELETE FROM vault_sync_state
   WHERE id                = 3354
     AND relative_path     = 'Organizations/Regenerate cascadia.md'
     AND content_hash      = 'ed88466c2fd07942e6b6056a6392b6c4f025b8f1760b81e81109142a0d05c9b4'
     AND origin_seq        = 229
     AND local_edit_seq    = 229
     AND file_size_bytes   = 5171
     AND is_deleted        = FALSE;
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN
    RAISE EXCEPTION 'MacBook vault_sync_state: expected 1 row, got % — state moved since the dry run, abort', n;
  END IF;
  RAISE NOTICE 'MacBook: 1 stale row deleted (id=3354)';
END $$;
COMMIT;
```

Note `file_size_bytes = 5171` — the stale row does not merely have the wrong hash,
it describes a **different, smaller** file than the 6,262 bytes on disk. It is a
leftover from older content, not a duplicate of the current row.

Nothing on the MacBook filesystem is touched. The canonical row (`id=25958`, hash
`38ecc42b…`) continues to track inode `181086141`.

### Step 5 — verify before re-enabling

```bash
# 1. DB case-fold audit, both nodes — expect ZERO rows
psql personal_koi -tAc "SELECT lower(relative_path), count(*) FROM vault_sync_state
  WHERE is_deleted=FALSE GROUP BY 1 HAVING count(*)>1;"
ssh dobby@192.168.1.69 "psql personal_koi -tAc \"SELECT lower(relative_path), count(*)
  FROM vault_sync_state WHERE is_deleted=FALSE GROUP BY 1 HAVING count(*)>1;\""

# 2. NUC filesystem case-fold audit across all 7 folders — expect ZERO
ssh dobby@192.168.1.69 'cd ~/Documents/Notes && for d in Shared Meetings People \
  Organizations Projects Locations Bridges; do [ -d "$d" ] || continue
  find "$d" -type f -name "*.md" | awk "{print tolower(\$0)\"\t\"\$0}" | sort \
   | awk -F"\t" "{if(\$1==p) print \"COLLISION \"po\" <> \"\$2; p=\$1; po=\$2}"; done'

# 3. THE SAFETY PROPERTY — zero unexpired events PENDING DELIVERY for that RID.
#    NOT "zero historical rows": there are 47 and they are audit history that stays.
#    Expect 0 on the NUC. The MacBook is not the emitter for this RID.
PEND=$(ssh dobby@192.168.1.69 "psql personal_koi -tAc \"SELECT count(*) FROM koi_net_events \
  WHERE rid LIKE '%Regenerate cascadia.md' AND expires_at>now() \
    AND NOT (COALESCE(target_node,'') = ANY(COALESCE(delivered_to,ARRAY[]::text[])));\"" | tr -d ' ')
[ "$PEND" -eq 0 ] || { echo "FAIL: $PEND unexpired events pending delivery"; exit 1; }

#    For the record only — expect 47, unchanged. A DROP here means something
#    deleted audit history that this procedure never touches.
ssh dobby@192.168.1.69 "psql personal_koi -tAc \"SELECT count(*) FROM koi_net_events WHERE rid LIKE '%Regenerate cascadia.md';\""

# 4. the canonical file is intact on both nodes — expect 38ecc42b…, 6262 bytes
shasum -a 256 "$HOME/Documents/Notes/Organizations/Regenerate Cascadia.md"
ssh dobby@192.168.1.69 'sha256sum ~/Documents/Notes/Organizations/"Regenerate Cascadia.md"'

# 5. both detectors — expect clear
python3 ~/projects/dobby/scripts/vault_sync_detectors.py
ssh dobby@192.168.1.69 'python3 ~/bin/vault_sync_detectors.py --vault ~/Documents/Notes'
```

### Step 6 — MacBook reconcile, read-only

Re-run the MacBook reconcile in **detect** mode. Expected `missing_on_disk`:

```
Meetings/Civic Intelligence Engine/2026-08-05 - CIE Reconvene.md
```

**Exactly one entry, and it must be that one.** Two entries means step 4 did not
take. A different entry means something else changed and this procedure is stale —
stop and re-derive.

### Step 7 — re-enable NUC vault sync

Reverse of step 1, then assert the live process env reads `true`.

### Step 8 — post-enable verification (positive control first)

**An empty journal must FAIL, not look clean.** Earlier today a UTC marker passed to
`journalctl --since`, which reads **local** time, returned an empty journal that was
briefly read as "nothing went wrong". Every counter below is a `grep -c` and would
have reported a reassuring `0` against that same empty result.

So the first assertion is a **positive control**: a scan must be proven to have run
before any zero-count is allowed to mean anything.

```bash
set -euo pipefail
. "$OUT/MANIFEST.env"
fail() { echo "VERIFY FAIL: $*" >&2; exit 1; }

# MARK is recorded in the NUC's LOCAL time, immediately before the step-7 restart.
MARK=$(ssh dobby@192.168.1.69 'date +"%Y-%m-%d %H:%M:%S"')
# ... step 7 restart happens here ...

jc() { ssh dobby@192.168.1.69 "journalctl -u dobby-koi-processor --since '$MARK' --no-pager"; }

# 1. POSITIVE CONTROL — wait up to 10 min for a completed scan after the marker.
for i in $(seq 1 60); do
  SCANS=$(jc | grep -c 'vault_sync.scan_complete' || true)
  [ "${SCANS:-0}" -ge 1 ] && break
  sleep 10
done
[ "${SCANS:-0}" -ge 1 ] || fail "no vault_sync.scan_complete after $MARK — the scan never ran, or the journal window is wrong. Every count below would be a false zero."
echo "positive control OK: $SCANS scan_complete since $MARK"

# 2. Only now are zero-counts meaningful.
[ "$(jc | grep -c 'vault_sync.scan_capped')"      -eq 0 ] || fail "scan_capped > 0"
[ "$(jc | grep -c 'vault_sync.tombstone_blocked')" -eq 0 ] || fail "tombstone_blocked > 0 — STOP"
[ "$(jc | grep -c 'vault_sync.absence_unverifiable')" -eq 0 ] || fail "absence_unverifiable > 0"

# 3. Detector on both nodes (its own exit code is the assertion).
ssh dobby@192.168.1.69 'python3 ~/bin/vault_sync_detectors.py --vault ~/Documents/Notes --quiet' || fail "NUC detector tripped"
python3 ~/projects/dobby/scripts/vault_sync_detectors.py --quiet || fail "MacBook detector tripped"

# 4. Zero unexpired events PENDING DELIVERY for the lowercase RID (not "zero rows").
PEND=$(ssh dobby@192.168.1.69 "psql personal_koi -tAc \"SELECT count(*) FROM koi_net_events WHERE rid LIKE '%Regenerate cascadia.md' AND expires_at>now() AND NOT (COALESCE(target_node,'') = ANY(COALESCE(delivered_to,ARRAY[]::text[])));\"" | tr -d ' ')
[ "$PEND" -eq 0 ] || fail "$PEND unexpired events pending delivery for the lowercase RID"

# 5. No NEW FORGET emitted since the marker.
NEWFG=$(ssh dobby@192.168.1.69 "psql personal_koi -tAc \"SELECT count(*) FROM koi_net_events WHERE event_type='FORGET' AND queued_at > timestamp '$MARK';\"" | tr -d ' ')
[ "$NEWFG" -eq 0 ] || fail "$NEWFG new FORGET(s) since $MARK"

echo "POST-ENABLE VERIFY OK"
```

`vault_sync.scan_complete` is emitted at `api/vault_sync.py:1524` with
`folder=… files_changed=… events_queued=… duration_ms=…`, so it is a real
completion signal and not an inferred one.

### Step 9 — fresh snapshot, followed through all three copies

Immediately before any MacBook enable:

```bash
ssh dobby@192.168.1.69 'systemctl start dobby-vault-autocommit.service; sleep 4
  cd ~/Documents/Notes && git log --oneline -1
  cd ~/backups/git/vault-nuc.git && git log --oneline -1 refs/heads/main'
~/.local/bin/mirror-nuc-vault.sh
cd ~/backups/git/vault-nuc-mirror.git && git log --oneline -1 refs/heads/main
```
**Assert the same SHA in all three:** NUC working tree → NUC bare repo → MacBook
off-machine mirror. The mirror had no schedule until 2026-08-26 and went 3.6 h
stale within a day; confirm it, do not assume it.

---

## Rollback — type-safe, and proven before it is needed

**Corrected.** The earlier draft emitted `INSERT` statements from `row_to_json` by
stringifying each value in Python. That cannot reconstruct this schema:
`koi_net_events` has `manifest jsonb`, `contents jsonb`, `delivered_to text[]`,
`confirmed_by text[]`, `event_id uuid`, `queued_at`/`expires_at timestamptz`.
Python `str()` on a parsed list yields `['a', 'b']`, which is not valid Postgres
array input, and on a dict yields single-quoted pseudo-JSON. The restore would
have failed, or worse, silently written malformed values.

**Use `COPY`**, which round-trips every one of those types exactly. The exports in
step 0 are already in that format.

### Prove the restore before relying on it

Run this **during step 0**, before anything is deleted. It restores into a
transaction and asserts exact row equality in both directions, then rolls back so
nothing is changed.

```bash
. "$OUT/MANIFEST.env"

psql personal_koi -v ON_ERROR_STOP=1 <<SQL
BEGIN;
CREATE TEMP TABLE _orig AS SELECT * FROM vault_sync_state WHERE id = $MAC_STALE_ID;
SELECT CASE WHEN count(*) = 1 THEN 'snapshot ok'
            ELSE (SELECT 1/0)::text END FROM _orig;

DELETE FROM vault_sync_state WHERE id = $MAC_STALE_ID;
\copy vault_sync_state FROM '$OUT/mac_stale_row.tsv'

-- exact equality, BOTH directions: a missing column or coerced type shows up here
SELECT CASE WHEN (
    (SELECT count(*) FROM (
        (SELECT * FROM vault_sync_state WHERE id = $MAC_STALE_ID EXCEPT SELECT * FROM _orig)
        UNION ALL
        (SELECT * FROM _orig EXCEPT SELECT * FROM vault_sync_state WHERE id = $MAC_STALE_ID)
    ) d)
  ) = 0 THEN 'RESTORE PROVEN: round-trip is byte-exact'
       ELSE (SELECT 1/0)::text END;
ROLLBACK;
SQL
```

A non-zero difference raises `division by zero` and the whole thing rolls back.
**Repeat the identical proof on the NUC** for `id = $NUC_LOWER_ID` against
`nuc_lower_row.tsv` before proceeding.

### Actual rollback, in reverse order

```bash
. "$OUT/MANIFEST.env"

# 4'. MacBook stale row
psql personal_koi -v ON_ERROR_STOP=1 -c "\copy vault_sync_state FROM '$OUT/mac_stale_row.tsv'"

# 3'. NUC lowercase row
scp "$OUT/nuc_lower_row.tsv" dobby@192.168.1.69:/tmp/
ssh dobby@192.168.1.69 "psql personal_koi -v ON_ERROR_STOP=1 -c \"\\copy vault_sync_state FROM '/tmp/nuc_lower_row.tsv'\""

# 2'. the quarantined file, from the path recorded in the manifest
ssh dobby@192.168.1.69 "mv '$QUAR/Regenerate cascadia.md' '/home/dobby/Documents/Notes/$LOWER'"

# 1'. re-enable NUC sync if rolling back mid-procedure
```

No event rows are ever deleted, so there is nothing to restore in
`koi_net_events`; `$OUT/nuc_events.tsv` is retained as a record only.

The quarantine directory is never emptied by this procedure. Remove it only after
the MacBook has been enabled and soaked clean.

## Explicitly out of scope

* The CIE stub row `Meetings/Civic Intelligence Engine/2026-08-05 - CIE Reconvene.md`
  (241 bytes, hash `b9845ff3…`, seq 5/5). The canonical 60,262-byte note without the
  hyphen exists on both nodes; the NUC already tombstoned the hyphenated path and
  emitted its one legitimate FORGET during the soak. **Left deliberately** as the
  expected single FORGET of the controlled first MacBook scan.
* Enabling MacBook vault sync. Separate, explicit approval.
* Any other rename, tombstone, event purge, or edge change.
