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
- `tests/test_vault_sync.py` — 17 unit tests
- `scripts/federation/smoke-vault-sync.sh` — two-peer smoke test script

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

## Phase Sync-1.5 (Hardening)

Scope:
1. Reliability and observability improvements without changing sync semantics.

Planned work:
1. Add a low-latency watcher hook that triggers early scan cycles (scanner remains correctness backstop).
2. Add per-cycle backpressure caps (max files/events/bytes) and explicit overflow logging.
3. Add structured metrics and health counters:
   - queued/applied/skipped_dedup/conflicts/rejected_invalid_payload/stale_delete_ignored
4. Add reconciliation tooling and runbook:
   - dry-run drift report
   - optional repair mode
5. Expand operator UX in `/koi-net/vault-sync/status`:
   - last successful trigger time
   - last apply time
   - rolling counters by rejection reason

Execution order:
1. Observability first (`/status` + counters + logging consistency).
2. Reconciliation dry-run, then repair.
3. Backpressure controls.
4. Watcher hook.

Definition of done:
1. Stable operation on sustained edits with bounded queue growth.
2. Drift detection and repair path validated.
3. Metrics visible and actionable for troubleshooting.

Exit criteria to start Sync-2:
1. Sync-1.5 checks pass for at least 7 days across two active peers.
2. No unresolved data-loss bugs in create/update/delete/conflict flows.
3. Reconciliation run shows zero unexplained drift on both peers.

## Phase Sync-2 (Feature Expansion)

Scope:
1. Multi-peer sync model.
2. Attachment support.

Planned work:
1. Move from global file sync state to per-(file, peer) state.
2. Attachment handling (size limits, optional chunking/compression).
3. More explicit rename/move tracking (optional).
4. Policy controls per peer/folder (limits, inclusion rules).

Definition of done:
1. One node can sync to multiple peers without state ambiguity.
2. Attachments replicate safely with bounded resource usage.
3. Peer-level policy controls enforced.

## Phase Sync-3 (Advanced Collaboration/Security)

Scope:
1. Optional collaborative merge and stronger confidentiality guarantees.

Candidates:
1. App-layer E2EE for payloads (in addition to WireGuard).
2. Optional CRDT/OT-based merge mode for concurrent edits.
3. Key rotation and recovery workflows for encrypted payload mode.

Notes:
1. CRDT is intentionally deferred. Sync-1 uses conflict copies for simplicity and correctness.
2. Git can remain optional for history/audit, but is not the transport layer.
3. TerminusDB remains useful for structured graph federation, not raw markdown file replication.

## Open Questions

1. Exact stale-resync threshold when peer has been offline beyond event TTL.
2. Default conflict copy naming and retention policy.
3. Attachment policy in Sync-2 (size and format boundaries).
4. Whether app-layer E2EE becomes default or optional in Sync-3.

## Related Documents

1. `scripts/federation/README.md` (operator setup/runbook)
2. `docs/planning/KOI_NET_FEDERATION_NEXT_SESSION_2026-02-25.md` (session task list)
3. `README.md` (project-level federation and traversal overview)
