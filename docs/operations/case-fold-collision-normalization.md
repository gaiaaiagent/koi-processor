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
| queued event | **1 × `UPDATE`** for the lowercase path, queued 2026-08-26 22:02:21Z **during the soak**, target `darren-personal`, `delivered=1` (unapplied — MacBook sync is off), expires 2026-09-02 |

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

### Step 0 — pre-flight export and assertions

```bash
OUT=~/backups/casefold-$(date +%Y%m%d-%H%M%S); mkdir -p "$OUT"

# MacBook rows
psql personal_koi -Atc "COPY (SELECT row_to_json(t) FROM (
  SELECT * FROM vault_sync_state
   WHERE lower(relative_path)='organizations/regenerate cascadia.md') t)
  TO STDOUT" > "$OUT/mac_rows.json"

# NUC rows + the queued event
ssh dobby@192.168.1.69 "psql personal_koi -Atc \"COPY (SELECT row_to_json(t) FROM (
  SELECT * FROM vault_sync_state
   WHERE lower(relative_path)='organizations/regenerate cascadia.md') t) TO STDOUT\"" > "$OUT/nuc_rows.json"
ssh dobby@192.168.1.69 "psql personal_koi -Atc \"COPY (SELECT row_to_json(t) FROM (
  SELECT * FROM koi_net_events
   WHERE rid LIKE '%Regenerate cascadia.md') t) TO STDOUT\"" > "$OUT/nuc_events.json"

# byte-level copy of the file being quarantined
scp "dobby@192.168.1.69:/home/dobby/Documents/Notes/Organizations/Regenerate cascadia.md" \
    "$OUT/Regenerate cascadia.md"

wc -l "$OUT"/*.json          # expect 2, 2, >=1
```

**Assert before proceeding:** `mac_rows.json` 2 lines, `nuc_rows.json` 2 lines,
and the copied file's sha256 is `38ecc42b…`. Any other value → stop.

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

### Step 3 — NUC: delete the stale row and the queued event

```sql
BEGIN;
DO $$
DECLARE n int;
BEGIN
  DELETE FROM vault_sync_state
   WHERE relative_path = 'Organizations/Regenerate cascadia.md';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'vault_sync_state: expected 1 row, got %', n; END IF;

  DELETE FROM koi_net_events
   WHERE rid LIKE '%Regenerate cascadia.md';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'koi_net_events: expected 1 row, got %', n; END IF;

  RAISE NOTICE 'NUC: 1 state row + 1 event deleted';
END $$;
COMMIT;
```

The uppercase row (seq 6/6) is untouched and keeps tracking the surviving file.

### Step 4 — MacBook: delete the stale row only

```sql
BEGIN;
DO $$
DECLARE n int; h text;
BEGIN
  SELECT left(content_hash,16) INTO h FROM vault_sync_state
   WHERE relative_path = 'Organizations/Regenerate cascadia.md';
  IF h IS DISTINCT FROM 'ed88466c2fd079' THEN
    RAISE EXCEPTION 'refusing: expected the stale hash, found %', h;
  END IF;

  DELETE FROM vault_sync_state
   WHERE relative_path = 'Organizations/Regenerate cascadia.md';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'expected 1 row, got %', n; END IF;
  RAISE NOTICE 'MacBook: 1 stale row deleted';
END $$;
COMMIT;
```

> The hash guard is deliberately a **prefix of the stale value**, so if the row has
> changed since this was written the delete refuses rather than proceeding.
> Re-read the live value before running; adjust only after understanding why it moved.

Nothing on the MacBook filesystem is touched. The canonical row (hash `38ecc42b…`)
continues to track inode `181086141`.

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

# 3. no event anywhere still references the lowercase path — expect 0 and 0
psql personal_koi -tAc "SELECT count(*) FROM koi_net_events WHERE rid LIKE '%Regenerate cascadia.md';"
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

### Step 8 — post-enable verification

```bash
# watch one full scan, then:
ssh dobby@192.168.1.69 'journalctl -u dobby-koi-processor --since "-10 min" --no-pager \
  | grep -c vault_sync.tombstone_blocked'      # expect 0
ssh dobby@192.168.1.69 "psql personal_koi -tAc \"SELECT count(*) FROM koi_net_events
  WHERE event_type='FORGET' AND queued_at > now() - interval '15 minutes';\""   # expect 0
```
Note: `journalctl --since` reads **local** time. Do not pass a UTC timestamp — that
silently returns an empty journal, which reads as "nothing happened."

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

## Rollback

Reverse order. Each step is independently reversible.

```bash
# 4'/3'. restore the deleted rows from the export
python3 - "$OUT/mac_rows.json" <<'PY'
import json,subprocess,sys
for line in open(sys.argv[1]):
    r=json.loads(line)
    if r['relative_path']!='Organizations/Regenerate cascadia.md': continue
    cols=','.join(r); vals=','.join('NULL' if v is None else "'"+str(v).replace("'","''")+"'" for v in r.values())
    print(f"INSERT INTO vault_sync_state ({cols}) VALUES ({vals});")
PY
# review the emitted SQL, then apply. Same for nuc_rows.json / nuc_events.json.

# 2'. restore the quarantined file
ssh dobby@192.168.1.69 'mv ~/.vault-casefold-trash/<TS>/"Regenerate cascadia.md" \
     ~/Documents/Notes/Organizations/'

# 1'. re-enable NUC sync if rolling back mid-procedure
```

The quarantine directory is never emptied by this procedure. Delete it only after
the MacBook has been enabled and soaked clean.

## Explicitly out of scope

* The CIE stub row `Meetings/Civic Intelligence Engine/2026-08-05 - CIE Reconvene.md`
  (241 bytes, hash `b9845ff3…`, seq 5/5). The canonical 60,262-byte note without the
  hyphen exists on both nodes; the NUC already tombstoned the hyphenated path and
  emitted its one legitimate FORGET during the soak. **Left deliberately** as the
  expected single FORGET of the controlled first MacBook scan.
* Enabling MacBook vault sync. Separate, explicit approval.
* Any other rename, tombstone, event purge, or edge change.
