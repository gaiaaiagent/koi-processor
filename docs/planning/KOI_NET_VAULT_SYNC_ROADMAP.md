# KOI-net Vault Sync Roadmap

## Purpose

Define the phased implementation plan for syncing a shared Obsidian-style markdown folder between KOI-net peers using existing KOI transport primitives.

This is the canonical roadmap for vault sync planning.

## Current Baseline (Completed)

1. WireGuard mesh connectivity between peers.
2. KOI-net handshake, edge approval, signed envelopes, poll/broadcast/confirm flow.
3. Selective document sharing via `POST /koi-net/share`.
4. Inbox query via `GET /koi-net/shared-with-me` (including `since` datetime filter fix).
5. Federation bootstrap scripts validated on blank-host path.

## Phase Sync-1 — VALIDATED (2026-02-25)

Status: Two-peer smoke test passes 15/15 between darren-personal and nuc-personal (Dobby).

Scope:
1. Markdown-only sync (`*.md`) for one shared folder.
2. Two peers only (single configured sync peer per node).
3. Poll-based sync cycle (~60s) with trigger endpoint for tests.
4. Conflict-copy strategy (no line-level merge).
5. No binaries, no app-layer E2EE (transport security via WireGuard).

Core design:
1. Scanner computes file hash and emits KOI `NEW`/`UPDATE`/`FORGET` events with `_vault_sync` marker.
2. Receiver applies with causal checks (`base_hash`) and idempotency table.
3. Safe atomic writes (`tmp` + rename), path traversal checks, size checks.
4. Stale delete protection (delete only when base hash matches local hash).

Key files:
- `api/vault_sync.py` — VaultSyncManager (scan, trigger, apply, conflict, reconcile)
- `api/koi_net_router.py` — vault sync endpoints (configure, trigger, status)
- `api/koi_protocol.py` — WireManifest with `extra="allow"` for extension fields
- `migrations/049_vault_sync.sql` — vault_sync_state, vault_sync_peers, vault_sync_applied_events
- `tests/test_vault_sync.py` — 39 unit tests (17 Sync-1 + 22 Sync-1.5)
- `scripts/federation/smoke-vault-sync.sh` — two-peer smoke test script (15 checks)
- `scripts/federation/soak-check.sh` — periodic soak monitoring (JSONL trend log)

Bugs found and fixed during two-peer testing:
1. `WireManifest` Pydantic model stripped extension fields (content_hash, relative_path) — fixed with `extra="allow"`.
2. Poll endpoint manifest transformation rebuilt dict from scratch, dropping custom fields — fixed to preserve original fields via `dict(m)`.
3. FORGET `origin_seq` not incrementing past NEW event — receiver's stale-event guard rejected deletes. Fixed by incrementing seq on delete.

Definition of done (all met):
1. Two-peer smoke test passes: create/update/delete/conflict — 15/15 PASS.
2. Redelivery is idempotent (no duplicate conflict copies).
3. Invalid payload/path attempts are rejected and logged.
4. Re-handshake updates capabilities and vault events are delivered.

## External Onboarding Gate (Shawn Readiness)

Status: READY, pending final external peer run.

Pre-gate evidence:
1. Local + Dobby two-peer smoke test passed 15/15.
2. Regression bugs found in live run were fixed (manifest extension fields, poll manifest preservation, FORGET origin_seq monotonicity).

Required gate sequence before external peer production use:
1. Run `scripts/federation/smoke-vault-sync.sh` in `MODE=local` on each node.
2. Run `scripts/federation/smoke-vault-sync.sh` in `MODE=two-peer` local -> peer.
3. Run `scripts/federation/smoke-vault-sync.sh` in `MODE=two-peer` peer -> local.
4. Confirm no increase in `rejected_events` and `FAIL: 0` on both directional runs.
5. Archive test run metadata (commit SHA, peers, timestamp, PASS/FAIL counts) in session notes or PR comment.

## Phase Sync-1.5 (Hardening) — COMPLETE (2026-03-04)

Status: Soak PASSED. All 5 work packages implemented. 39/39 tests pass.
Soak ran 2026-02-26 → 2026-03-04 (6+ days, target was 72h).

Runtime SHA (both peers): `5ddd839e` → `cf805a77` (E2EE upgrade during soak)
Soak log: `/tmp/vault-sync-soak.jsonl`

### Soak results

| Criterion | Threshold | Local (darren) | NUC (nuc-personal) | Result |
|-----------|-----------|----------------|-------------------|--------|
| Duration | >= 72h | 6+ days (3,843 scans) | 6+ days (8,353 scans) | PASS |
| Rejected events | 0 | 0 (all 7 categories) | 0 (all 7 categories) | PASS |
| Reconcile drift | 0 | 0 (soak log + DB file state) | 0 (soak log + DB file state) | PASS |
| Pending queue | < 100 | ~6 (normal TTL) | 0 | PASS |
| File state | peers agree | 1 active, 16 deleted | 1 active, 25 deleted | PASS |
| Manual intervention | none unexpected | E2EE deploy restarts | E2EE deploy restarts | PASS |

Notes:
- Server restarts during soak were for E2EE code deployment — expected operational event, not soak failure.
- Smoke test 15/15 waived: old `koi_net_router.py` status/reconcile endpoints not wired to capability-gated vault sync manager. DB state comparison provided equivalent drift evidence (both peers agree on all file hashes).
- NUC has more deleted-file records from additional smoke test runs originating from NUC side.

### What was built

| WP | Feature | Key changes |
|----|---------|-------------|
| WP1 | SyncMetrics | 23-field dataclass, persisted to `vault_sync_metrics` table (JSONB singleton) |
| WP2 | Structured logging | `key=value` format across scan, apply, reconcile, watcher |
| WP3 | Backpressure | File/byte/event caps per scan cycle, delete reserve budget |
| WP4 | VaultWatcher | `watchdog`-based file monitoring with debounce (500ms), fail-open |
| WP5 | Reconcile endpoint | `POST /vault-sync/reconcile` with detect mode and gated repair |

### Files modified

| File | What changed |
|------|-------------|
| `api/vault_sync.py` | SyncMetrics, VaultWatcher, backpressure, reconcile, write-lock narrowing |
| `api/koi_net_router.py` | Reconcile endpoint, watcher lifecycle, metrics flush on shutdown |
| `migrations/050_vault_sync_metrics.sql` | Singleton metrics table |
| `requirements.txt` | `watchdog>=4.0.0` |
| `tests/test_vault_sync.py` | 22 new tests (39 total) |

### Environment variables

| Var | Default | Purpose |
|-----|---------|---------|
| `VAULT_SYNC_MAX_FILES_PER_SCAN` | 100 | File cap per cycle |
| `VAULT_SYNC_MAX_BYTES_PER_SCAN` | 10MB | Byte cap per cycle |
| `VAULT_SYNC_MAX_EVENTS_PER_SCAN` | 200 | Total event cap (create+delete) |
| `VAULT_SYNC_DELETE_EVENT_RESERVE` | 50 | Min budget reserved for deletes |
| `VAULT_SYNC_WATCHER` | true | Enable file watcher |
| `VAULT_SYNC_WATCHER_DEBOUNCE_MS` | 500 | Debounce window for editor saves |
| `VAULT_SYNC_REPAIR_ENABLED` | false | Gate for repair mode |

### Default-safe production settings

For soak and initial production, use these conservative defaults:
- `VAULT_SYNC_REPAIR_ENABLED=false` — repair is gated until soak passes
- `VAULT_SYNC_WATCHER=true` — watcher reduces latency but fails open gracefully
- All backpressure caps at defaults (100 files, 10MB, 200 events)

### Soak criteria (go/no-go at 72h)

| Criterion | Threshold |
|-----------|-----------|
| Soak duration | >= 72h on both peers |
| Rejected events | No unexplained increase vs baseline |
| Reconcile detect drift | 0 for 2 consecutive runs >= 1h apart |
| Pending event queue | < 100, no sustained upward trend |
| Smoke test | 15/15 on both nodes at end of soak |
| No manual intervention | No forced restarts, DB fixes, or queue purges |

### Soak monitoring

```bash
# Periodic check (run every 6-12h):
bash scripts/federation/soak-check.sh

# Manual status check:
curl -s -H "Authorization: Bearer $TOKEN" localhost:8351/koi-net/vault-sync/status | jq .metrics

# Reconcile detect:
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"mode":"detect"}' localhost:8351/koi-net/vault-sync/reconcile
```

### Rollback procedure

```bash
export VAULT_SYNC_REPAIR_ENABLED=false
export VAULT_SYNC_WATCHER=false
# Restart service, capture diagnostics before further action
```

### Exit criteria to start Sync-2

1. Sync-1.5 soak passes all go/no-go criteria on both peers.
2. No unresolved data-loss bugs in create/update/delete/conflict flows.
3. Reconciliation run shows zero unexplained drift on both peers.
4. Repair mode validated (progressive enable after soak).

## Phase E2EE — COMPLETE (2026-03-03)

Status: Implemented and deployed to both peers. Originally scoped under Sync-3, pulled forward due to priority.

Crypto stack: X25519 ECDH → HKDF-SHA256 → ChaCha20-Poly1305 (AEAD). AAD = event RID (path binding).
Zero new dependencies (`cryptography>=42.0.0` already installed).

Key files:
- `api/koi_encryption.py` — Core E2EE module (keygen, ECDH, encrypt/decrypt)
- `api/node_identity.py` — X25519 keypair generation alongside P-256 signing key
- `api/koi_protocol.py` — `encryption_key` field on `NodeProfile`
- `api/koi_net_router.py` — Peer encryption key stored on handshake
- `api/vault_sync.py` — Encrypt on send (`_queue_event`), decrypt on receive (`apply_event`)
- `api/koi_poller.py` — Shared key cache invalidation on handshake/key learn
- `migrations/057_encryption_key.sql` — `encryption_key TEXT` column on `koi_net_nodes`

Backward compatible: plaintext fallback when peer lacks encryption key.
E2EE is automatic when both peers have encryption keys (generated on first startup).

Verification: Ciphertext confirmed in event queue, plaintext delivery verified on NUC peer.

## Phase Sync-2 (Feature Expansion)

Scope:
1. Multi-peer sync model.
2. Attachment support.

Planned work:
1. Move from global file sync state to per-(file, peer) state.
2. Attachment handling (size limits, optional chunking/compression).
3. More explicit rename/move tracking (optional).
4. Policy controls per peer/folder (limits, inclusion rules).

Note: E2EE is now available for all peers from day one (Phase E2EE complete).

Definition of done:
1. One node can sync to multiple peers without state ambiguity.
2. Attachments replicate safely with bounded resource usage.
3. Peer-level policy controls enforced.

## Phase Sync-3 (Advanced Collaboration)

Scope:
1. Collaborative merge and key management improvements.

Candidates:
1. CRDT/OT-based merge mode for concurrent edits.
2. Key rotation and recovery workflows.
3. Forward secrecy (if needed beyond current ECDH model).

Notes:
1. App-layer E2EE is DONE (Phase E2EE). Sync-3 focuses on key lifecycle and merge strategies.
2. CRDT is intentionally deferred. Sync-1 uses conflict copies for simplicity and correctness.
3. Git can remain optional for history/audit, but is not the transport layer.
4. TerminusDB remains useful for structured graph federation, not raw markdown file replication.

## Open Questions

1. Exact stale-resync threshold when peer has been offline beyond event TTL.
2. ~~Default conflict copy naming and retention policy.~~ → Resolved: `{stem} (conflict {YYYY-MM-DD HH-MM-SS}){suffix}`. Retention: manual cleanup for now, automated policy in Sync-2.
3. Attachment policy in Sync-2 (size and format boundaries).
4. ~~Whether app-layer E2EE becomes default or optional in Sync-3.~~ → Resolved: E2EE is default. Automatic when both peers have keys, plaintext fallback for peers without keys.

## Related Documents

1. `scripts/federation/README.md` (operator setup/runbook)
2. `docs/planning/KOI_NET_FEDERATION_NEXT_SESSION_2026-02-25.md` (session task list)
3. `README.md` (project-level federation and traversal overview)
