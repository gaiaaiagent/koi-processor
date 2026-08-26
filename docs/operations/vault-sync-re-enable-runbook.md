# Runbook: re-enabling vault sync after the 2026-08 FORGET storm

**Status:** NOT EXECUTED. Written 2026-08-26. `VAULT_SYNC_ENABLED=false` on both
nodes and must stay that way until every gate below is closed **by the operator**.

This is deliberately a runbook and not a script. Every remaining step is a policy
decision about who may delete whose files, and none of them should be taken by an
agent on its own judgement.

---

## What actually broke, in one paragraph

`MAX_FILES_PER_SCAN = 100` and the deletion pass shipped in the same commit
(`87c1ea2`, 2026-02-25). `_stat_cache` is in-memory, so after **any process
restart** every file is a cache miss, gets hashed, and is charged against the cap
— the scan truncates inside the first 100 files, and the deletion pass then runs
against a file list it already knows is partial. Everything not reached looked
absent. The fingerprint is visible in the data: `Shared` rows in
`vault_sync_state` were created `2026-08-17 13:31` in a batch of **exactly 100**.

The system had no way to distinguish *"I did not look"* from *"it is not there."*

## Code gates — all closed

| id | fix | commit |
|---|---|---|
| A1 | a truncated scan skips the deletion pass entirely | `3bd94d0` |
| A2 | unchanged files no longer spend scan budget, so a cold cache stops truncating | `68dcf90` |
| A3 | path exclusion applied symmetrically (read side as well as write side) | `0f568d1` |
| A5 | a file that cannot be READ is UNKNOWN, not absent | `fd09fa2` |
| A4 | never tombstone a path whose file is on disk at the moment of the write | `3cc4b88` |

A4 is the one that does not depend on the others being right. It would have
blocked **1,139 of the 1,339** bad tombstones on its own.

**Still open by design:** A6 (a scan coverage ledger — refuse deletion unless
coverage is computed, not inferred) and A7 (reconcile reports drift pages rather
than silently skipping). Neither blocks re-enable; both would make the guarantee
structural instead of a stack of guards.

## Detection — armed

`dobby/scripts/vault_sync_detectors.py`, both nodes:
* **false tombstones** — rows marked deleted whose file exists.
* **armed deletions** — queued FORGETs whose path exists. This is the one that
  matters: each is a pending delete of a live file on a peer.

Verified by positive control on 2026-08-26 (injected an armed FORGET, confirmed
`TRIPPED`, removed it, confirmed `clear`), so a `clear` result means the query
ran. Both nodes read clear. **Run this before and after every step below.**

---

## GATE 1 — `friend-e2e` (BLOCKING)

**Do not re-enable until this is decided.** The earlier characterisation of this
peer as "a dead March dry-run" was wrong, corrected 2026-08-26:

| fact | value |
|---|---|
| WireGuard handshake | **~2 minutes ago** — the host is live |
| ping `10.100.0.24` | up, 228 ms |
| port 80 | **401** — an authenticated service is running |
| KOI ports 8351 / 8355 / 8100 | no response — its KOI node is down, not its machine |
| edge `darren-personal -> friend-e2e` | **APPROVED, includes `Vault-file`**, on both nodes |
| `vault_sync_peers` | has a `Shared` row on both nodes, **already `enabled=f`** |
| vault files ever delivered to it | **0** |
| unexpired `Shared/` events targeted at it | **1,226**, expiring by 2026-09-01 |
| last event of any kind delivered | 2026-08-14 |

So the exposure has never been realised, is self-expiring, **and becomes live the
moment its KOI node restarts while vault sync is on.** A live host with an
approved vault-file edge is not a theoretical risk.

### State correction, 2026-08-26 — half of this gate is already closed

Re-read from both databases rather than from this document:

```
MAC  friend-e2e  Shared  enabled=f        NUC  friend-e2e  Shared  enabled=f
MAC  shawn       Shared  enabled=f        NUC  shawn       Shared  enabled=f
MAC  nuc-personal <7 folders> enabled=t   NUC  darren-personal <7 folders> enabled=t
```

**Every peer lookup in `api/vault_sync.py` filters `enabled=TRUE`** — the emitter
(`_get_all_peers`), the per-source lookup (`_get_peer_by_source`) and the
folder-scoped variant all do. So a disabled peering already blocks *both*
directions: no new events are targeted at that peer, and inbound vault events from
it are rejected.

Two consequences, and they materially shrink this gate:

1. **The "disable the peering" step is already in effect** for `friend-e2e`. The
   1,226 queued events are historical residue from before it was disabled. They
   **cannot grow**, including after vault sync is re-enabled.
2. **Shawn's vault peering is also already disabled**, which is consistent with
   what he was told on 2026-08-26 ("I'm not going to run vault-sync/configure for
   Legion2 until the drift is diagnosed").

So when vault sync is re-enabled, **only Mac↔NUC will sync.** No third party
receives anything new. What remains of this gate is narrower than written above:

* the **edge** still lists `Vault-file`, so the 1,226 already-targeted events would
  be *served* if that node polled before they expire (last expiry **2026-09-02**);
* and nobody can yet say what `10.100.0.24` is.

### The four options were a false either/or — corrected 2026-08-26

The original framing offered (a) narrow the edge *or* (b) drop the vault peering.
That is wrong, because **they are different mechanisms guarding different halves**,
and neither substitutes for the other:

| control | what it stops | what it does NOT stop |
|---|---|---|
| **(a)** remove `Vault-file` from the edge `rid_types` | *delivery* — the poll gate refuses to serve vault events | the emitter keeps generating and targeting them, and inbound vault events from that peer are still applied |
| **(b)** disable the `friend-e2e`/`Shared` peering row | *emission* — no new events are targeted at it, and its inbound vault writes stop | the 1,226 already-queued events, which are already targeted |

`rid_types` is the protocol's authorization boundary; `vault_sync_peers` is our
emitter's configuration. Doing only (b) leaves a queue behind an edge that would
still serve it. Doing only (a) keeps manufacturing data that can never be
delivered. **The already-targeted events are a third decision, not covered by
either.**

**RECOMMENDED — all three, in this order:**

1. **Narrow the edge.** Remove `Vault-file` from `rid_types` on
   `darren-personal -> friend-e2e`, **both nodes**. Reversible, no WireGuard change.
2. **Disable, do not delete, the peering.** Set `enabled=false` on the
   `friend-e2e`/`Shared` row in `vault_sync_peers`, both nodes. Deleting it loses
   the record that this peering ever existed, which is how the peer became
   unexplained in the first place.
3. **Let the queue expire behind the narrowed edge.** Do **not** purge. Recount
   first (the figure below ages), confirm zero remain after the last expiry, and
   only then consider deletion as a separate, explicitly approved step.

**Preserve the node identity and the WireGuard peer.** The host is up. Removing it
from the relay disconnects a live machine whose purpose is not yet established, and
that is not a decision to take as a side effect of a vault-sync fix.

**Still open regardless of which controls are applied:** *what is `10.100.0.24`?* It
answers 401 on port 80. Until someone can say what that machine is and who runs it,
"keep it deliberately" is not available as an option, because nobody can state what
would be kept.

## GATE 2 — `Locations/` and `Bridges/` are unguarded (BLOCKING)

The Mac/NUC pair implements "Mac owns, NUC mirrors" for exactly four folders:

```
Mac : KOI_VAULT_READONLY_PATHS=Meetings/,People/,Organizations/,Projects/
NUC : KOI_VAULT_MIRROR_PATHS=Meetings/,People/,Organizations/,Projects/
```

`Locations/` (162 tracked) and `Bridges/` (6) are synced Mac↔NUC exactly like the
other four but appear in **neither** list, so the Mac does not reject incoming
UPDATE/FORGET for them and the NUC does not mirror them. Their
`vault_sync_peers` rows were created 2026-04-18, *before* either list was
written (2026-04-22 / 04-27) — an omission at authoring time, not later drift.

This is not hypothetical: **51 `Locations/` FORGETs were generated on 2026-08-25**,
and nothing on the Mac would have rejected them.

Proposed, but **not applied** — it changes replication semantics for 168 files:

```
Mac : KOI_VAULT_READONLY_PATHS=Meetings/,People/,Organizations/,Projects/,Locations/,Bridges/
NUC : KOI_VAULT_MIRROR_PATHS=Meetings/,People/,Organizations/,Projects/,Locations/,Bridges/
```

**RECOMMENDED: apply.** The justification is ownership topology, not activity
level — both folders are peered with `nuc-personal` and nothing else, exactly like
the four already governed:

```sql
SELECT shared_folder, count(*) FROM vault_sync_peers GROUP BY 1;
-- Bridges / Locations: nuc-personal only
```

**A characterisation offered during review, and why it was not used:** that
`Bridges/` is "a legacy/archive folder, superseded by `StagedBridgeNotes/`". Checked
2026-08-26 and **both halves are wrong**:

* `StagedBridgeNotes/` **does not exist** on the MacBook or the NUC. It is an
  aspirational Dobby-side staging convention in `dobby/config/identity.md` that has
  never been exercised.
* `Bridges/` is not dormant — `Sheaf-Coordination-x-Spore.md` was modified
  **2026-08-14**, twelve days ago. The other five are April.

The conclusion survives; the reason does not. Ownership policy should not depend on
how recently a folder was written, or it will need re-litigating every time activity
changes.

## GATE 3 — `Shared/` is protected on neither node (ACCEPT, do not "fix")

`Shared/` is in no readonly or mirror list anywhere, which is **correct**: it is
the genuinely bidirectional folder, and that is the point of sharing it with
Shawn. Marking it readonly would break the federation it exists for.

The consequence is that `Shared/` has **no** ownership guard behind the emitter
fixes — which is exactly why the loss landed there and not in the four guarded
folders. Its whole protection is A1–A5 plus the detectors. Treat the first soak
window as load-bearing.

## GATE 4 — node status disagreement (NON-BLOCKING, fix before Legion returns)

`octo-salish-sea` (`10.100.0.20:8351`) is **`rejected` on the Mac and `active` on
the NUC**. `legion-koi` has the same split. A `rejected` status is not
enforceable by identity because `koi_net_nodes` is unique on `node_rid`, not on
`base_url` (divergence 8) — `10.100.0.20` already hosts both `front-range:8355`
and `octo-salish-sea:8351`.

Reconcile the two nodes so a peer cannot handshake on one and not the other.

---

## Re-enable sequence

Only after Gates 1 and 2 are closed.

1. **Baseline.** Run the detector on both nodes; both must read `clear`. Record
   `SELECT count(*) FROM vault_sync_state WHERE is_deleted=TRUE` on each.
2. **Confirm the git safety net.** The NUC vault is a git repo with a 30-minute
   autocommit timer and a Mac mirror (`dobby-vault-autocommit.timer`). Confirm
   the last autocommit is recent. This is the pre-delete snapshot that made the
   FORGET-carries-payload fork unnecessary; do not re-enable without it.
3. **One node first.** Set `VAULT_SYNC_ENABLED=true` on the **NUC only**, restart
   via `systemctl restart dobby-koi-processor`, and leave the Mac off. A
   one-sided sync cannot produce a cross-node deletion wave.
4. **Watch the first scan after restart** — this is the exact condition that
   triggered the storm (cold `_stat_cache`). Expect in the log:
   * `vault_sync.scan_capped` should now be **rare**; sustained capping means A2
     is not doing its job and deletions are being suppressed — safe, but wrong.
   * `vault_sync.deletions_skipped reason=scan_truncated` — A1 firing.
   * `vault_sync.tombstone_blocked ... reason=file_present_on_disk` — **A4
     firing. Any occurrence means something upstream still selects present files
     as deletion candidates. Investigate before going further.**
   * `vault_sync.reconcile_unreadable` / `reconcile_unknown_paths` — A5.
5. **Detector after 1 scan cycle, then after 1 hour.** Any trip: set
   `VAULT_SYNC_ENABLED=false`, restart, investigate. Do not debug with it on.
6. **Then the Mac**, same watch.
7. **Only then Shawn.** His `Shared` peering is a third decision and needs the
   namespace question settled first (below).

## Before Shawn's node returns

* His two old Legion identities are pinned to `rid_types={SpecDoc}`; `Vault-file`
  is not in scope, so nothing can be served to them today.
* **Divergence 1 is in its expand phase.** Both vault-file namespaces are
  accepted inbound; emission is still the legacy `koi-net.vault-file` behind
  `KOI_VAULT_FILE_NAMESPACE`. **Do not flip it until his node accepts both** — he
  has been unreachable since June and cannot have been upgraded.
* His `{SpecDoc}` canary channel carries **none** of the 236 SpecDoc entities
  (divergence 9). Told to him 2026-08-26.

## Rollback

```bash
# Both nodes, in this order.
sed -i 's/^VAULT_SYNC_ENABLED=true/VAULT_SYNC_ENABLED=false/' config/personal.env
sudo systemctl restart dobby-koi-processor        # NUC
~/.config/personal-koi/restart.sh                 # Mac
python3 scripts/vault_sync_detectors.py           # must return clear
```

Disabling stops new events. It does **not** disarm FORGETs already queued — those
live until their TTL. If the detector shows armed deletions, delete those rows
before any peer polls:

```sql
DELETE FROM koi_net_events WHERE event_type='FORGET' AND expires_at > now()
  AND (rid LIKE 'orn:koi-net.vault-file:%' OR rid LIKE 'orn:personal-koi.vault-file:%');
```

## Related

* `docs/architecture/koi-protocol-conformance.md` — the 13-divergence register
* `dobby/scripts/vault_sync_detectors.py` — the tripwires

---

# DRY RUN — 2026-08-26

Nothing below has been executed. Every count was read live from both databases at
the time of writing; recount before applying, because two of them age.

## Change 1 — narrow the `friend-e2e` edge (Gate 1, remaining half)

**Affected rows: 2** (one per node). The inbound edge
`friend-e2e -> darren-personal` is *their* authorization to us and is enforced by
their node, not ours; leave it alone.

```sql
-- MacBook AND NUC, same statement on each.
UPDATE koi_net_edges
   SET rid_types = array_remove(rid_types, 'Vault-file')
 WHERE source_node = 'orn:koi-net.node:darren-personal+80e26aab6b59178cd605c93b1aa0b903e61a283ee2a4ace07da3d1fabdd779f6'
   AND target_node = 'orn:koi-net.node:friend-e2e+ccca71d04bde1a77eebb4c941b542c6e2edf222c50cfb10f17f41ee499469002';
```

Before → after: `{Organization,Person,Project,Concept,Location,Vault-file}` →
`{Organization,Person,Project,Concept,Location}`.

**Verify (expect `f` on both nodes):**
```sql
SELECT 'Vault-file' = ANY(rid_types) AS still_scoped
  FROM koi_net_edges
 WHERE source_node LIKE '%darren-personal%' AND target_node LIKE '%friend-e2e%';
```

**Rollback:** `SET rid_types = array_append(rid_types, 'Vault-file')` with the same
WHERE clause.

**Not doing:** deleting the 1,226 / 413 queued events. They expire by
**2026-09-02** and deleting them is a separate decision. After that date:
```sql
SELECT count(*) FROM koi_net_events
 WHERE expires_at > now() AND target_node LIKE '%friend-e2e%';   -- expect 0
```

## Change 2 — Gate 2 ownership config

**Affected: 2 config lines, 168 tracked files** (Locations 162, Bridges 6).
Config only; no SQL, no data migration.

```diff
 # MacBook  config/personal.env:115
-KOI_VAULT_READONLY_PATHS=Meetings/,People/,Organizations/,Projects/
+KOI_VAULT_READONLY_PATHS=Meetings/,People/,Organizations/,Projects/,Locations/,Bridges/

 # NUC  config/personal.env:95
-KOI_VAULT_MIRROR_PATHS=Meetings/,People/,Organizations/,Projects/
+KOI_VAULT_MIRROR_PATHS=Meetings/,People/,Organizations/,Projects/,Locations/,Bridges/
```

Both require a service restart to take effect (read via `os.getenv` at import).

**Verify:** with vault sync still OFF, restart and confirm the process picked them
up, then check that an inbound UPDATE for `Locations/…` is rejected on the Mac and
mirrored on the NUC during the soak.

**Rollback:** restore the four-folder value and restart.

## Change 3 — nothing (Gate 3)

`Shared/` stays out of both lists, deliberately. It is the bidirectional folder.

## What is explicitly NOT in this dry run

* re-enabling `VAULT_SYNC_ENABLED` anywhere
* purging any events
* deleting or de-peering any node, or touching the relay's WireGuard config
* flipping `KOI_VAULT_FILE_NAMESPACE` (divergence 1 cutover)
* messaging Shawn

## Order of operations, once approved

1. Recount Change 1's queue figures; apply Change 1 on both nodes; verify.
2. Apply Change 2 on both nodes; restart both; confirm the env is live.
3. Detector on both nodes → must read `clear`.
4. Confirm the NUC vault autocommit is recent **and** the Mac mirror matches its
   HEAD (the mirror had no schedule until 2026-08-26 and went 3.6h stale in a day).
5. `VAULT_SYNC_ENABLED=true` on the **NUC only**; restart; watch the first
   post-restart scan — that is the exact condition that produced the storm.
6. Detector after one scan cycle, then after an hour. Any trip → disable, restart,
   investigate. Do not debug with it on.
7. Only then the MacBook, same watch.

Only Mac↔NUC will sync: `friend-e2e` and `shawn` are both `enabled=f`.
