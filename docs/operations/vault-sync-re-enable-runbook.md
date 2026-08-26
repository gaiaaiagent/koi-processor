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
| `vault_sync_peers` | has a **`Shared`** row on both nodes |
| vault files ever delivered to it | **0** |
| unexpired `Shared/` events targeted at it | **1,226**, expiring by 2026-09-01 |
| last event of any kind delivered | 2026-08-14 |

So the exposure has never been realised, is self-expiring, **and becomes live the
moment its KOI node restarts while vault sync is on.** A live host with an
approved vault-file edge is not a theoretical risk.

Pick one, then re-run the detector:

- **(a) Narrow the edge.** Remove `Vault-file` from `rid_types` on the
  `darren-personal -> friend-e2e` edge, both nodes. Reversible, no WireGuard
  change, keeps whatever the peer was legitimately for.
- **(b) Drop the vault peering.** Delete the `friend-e2e` / `Shared` row from
  `vault_sync_peers` on both nodes. Stops events being targeted at it at the
  emitter.
- **(c) Remove the peer entirely**, per the original plan — also edits the relay's
  `/etc/wireguard/wg-koi.conf` over `ssh poly@37.27.48.12`. Most thorough, least
  reversible, and it disconnects a host that is currently up.
- **(d) Deliberately keep it.** Only if you know what that machine is and intend
  it to hold a copy of `Shared/`. Write down which, so this stops resurfacing.

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

Confirm that `Locations/` and `Bridges/` really are Mac-owned entity folders
before applying.

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
