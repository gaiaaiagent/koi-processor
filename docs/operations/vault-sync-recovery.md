# Vault Sync Recovery Runbook

Use this when two KOI-net peers stop exchanging shared vault files after runtime
or migration version skew. The goal is to recover without corrupting a large
production database or silently dropping vault-file events.

## Safety Rules

- Do not run broad migrations against a production DB under time pressure.
- Pause vault sync before structural DB changes.
- Prefer structural DB checks over `schema_migrations`; some deployed nodes may
  have the right table shape without complete migration ledger rows.
- Back up federation and vault-sync tables before any migration.
- Replay a scoped path first. Do not release the whole backlog until the scoped
  replay is clean.

## Phase 0: Pause

On each node:

```bash
TOKEN=$(cat ~/.config/personal-koi/koi-state/admin_token)
curl -sS -X POST -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8351/koi-net/vault-sync/pause | jq .
```

## Phase 1: Diagnostics

Run on each node from the `koi-processor` checkout:

```bash
python3 scripts/federation/vault-sync-doctor.py \
  --path-prefix "Shared/Regen AI/Meetings" \
  --peer-rid "orn:koi-net.node:PEER_NAME_OR_HASH" \
  --include-file-list \
  --output "/tmp/vault-sync-doctor-$(hostname)-$(date -u +%Y%m%dT%H%M%SZ).json"
```

For Darren -> Shawn recovery, Darren's peer RID is:

```text
orn:koi-net.node:shawn+135d478ecc2d7107c0159d6235440519da948e047cb976349e4bb0ce307c8328
```

Compare these fields between nodes:

- Git branch and SHA
- `http.koi_net_health.body.node`
- `db.structural_status.value`
- `db.indexes.value`
- `db.vault_sync_peers.value`
- `db.vault_event_summary.value`
- `db.path_events_for_peer.value`
- `files.count`, `files.total_bytes`, and per-file `sha256`

## Expected Post-080 Vault-Sync Shape

The current `regen-prod` vault-sync substrate expects:

- `koi_net_events.target_node` column exists
- `koi_net_nodes.encryption_key` column exists
- `vault_sync_state.local_edit_seq` column exists and is NOT NULL
- `vault_sync_peers` primary key is `(peer_node_rid, shared_folder)`
- `vault_sync_peers.id` column is absent
- event dedup index includes `(source_node, event_id, COALESCE(target_node, ''))`

The relevant migration files are:

- `049_vault_sync.sql`
- `057_encryption_key.sql`
- `060_multi_peer_sync.sql`
- `080_multi_folder_sync.sql`

Do not assume all intermediate non-vault migrations are safe or required for a
vault-sync recovery.

## Phase 2: Back Up

Minimum table backup before migration or event repair:

```bash
mkdir -p ~/koi-backups
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
pg_dump -d personal_koi \
  -t koi_net_events \
  -t koi_net_edges \
  -t koi_net_nodes \
  -t vault_sync_state \
  -t vault_sync_peers \
  -t vault_sync_applied_events \
  -t vault_sync_metrics \
  -Fc -f "$HOME/koi-backups/vault-sync-$STAMP.dump"
```

For a high-risk DB, also take a full DB backup or snapshot before proceeding.

## Phase 3: Migrate Only Missing Vault-Sync Shape

Use the doctor report to decide what is missing. Apply only the smallest needed
patches, preferably first against a restored clone of the DB.

Examples:

- If `target_node` is missing, apply `043_event_target_node.sql` or
  `048_event_target_node.sql` depending on lineage.
- If `encryption_key` is missing, apply `057_encryption_key.sql`.
- If `local_edit_seq` is missing or event dedup is still `(source_node,event_id)`,
  apply the corresponding parts of `060_multi_peer_sync.sql`.
- If `vault_sync_peers` still has singleton `id` or primary key only
  `peer_node_rid`, apply `080_multi_folder_sync.sql` after verifying duplicate
  rows will not violate the new composite key.

After migration, re-run `vault-sync-doctor.py` and confirm the expected shape.

## Phase 4: Scoped Replay

After both nodes pass the expected shape checks:

1. Keep broad sync paused.
2. Reconcile or replay only the target path, for example:

```bash
TOKEN=$(cat ~/.config/personal-koi/koi-state/admin_token)
cd ~/Documents/Notes
find "Shared/Regen AI/Meetings" -maxdepth 1 -type f -name '*.md' \
  ! -name '*Transcript.md' -print | sort |
  jq -R -s '{mode:"repair", confirm:true, max_actions:100, paths:(split("\n")[:-1])}' \
  > /tmp/regen-ai-meetings-repair.json

curl -sS -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary @/tmp/regen-ai-meetings-repair.json \
  http://127.0.0.1:8351/koi-net/vault-sync/reconcile | jq .
```

3. Resume polling only after confirming the receiver can apply a small canary.
4. Re-run the doctor on both nodes and compare file count + hashes.

## Durable Code Fixes To Land

The recovery above handles one incident. The code should also be hardened so the
same class of break is visible and recoverable:

- Add explicit vault-sync protocol/version fields to `/koi-net/health`.
- Add a DB shape hash to `/koi-net/health` or `/koi-net/vault-sync/status`.
- Refuse or downgrade vault-file delivery to incompatible peers.
- Make vault `apply_event()` return `applied`, `rejected`, `deferred`, or
  `quarantined`.
- Confirm only successfully applied events. Do not confirm locally rejected
  events just because rejection was logged.
- Persist rejection records per event ID, source, target, reason, path, and
  detail.
- Add an operator replay endpoint with peer + path filters and dry-run output.
- Add a two-node version-skew test that proves rejected vault-file events remain
  retryable or quarantined, not silently confirmed.

## Go / No-Go

Proceed to broad vault-sync resume only when:

- Both nodes report the expected post-080 shape.
- A scoped canary file syncs both directions with no rejection-counter increase.
- `reconcile detect` shows zero drift for the scoped path on both nodes.
- The active queue for the peer is understood and does not include unexpected
  destructive `FORGET` events.
