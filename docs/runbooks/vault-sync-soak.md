# Vault Sync Soak Runbook

## Overview

72-hour soak test for Vault Sync Phase Sync-1.5 hardening. Both peers run the same runtime SHA with `VAULT_SYNC_REPAIR_ENABLED=false` while we observe stability under normal use.

## Current Soak

| Field | Value |
|-------|-------|
| Start | 2026-02-26T04:31:19Z |
| Go/no-go | 2026-03-01T04:31:19Z (72h) |
| Runtime SHA | `5ddd839e` |
| Local peer | darren-personal |
| Remote peer | nuc-personal (dobby@192.168.1.69) |
| Soak log | `/tmp/vault-sync-soak.jsonl` |

## Periodic Checks (every 6-12h)

```bash
bash scripts/federation/soak-check.sh
```

This captures from both peers:
- `pending_events` — should stay < 100, no sustained upward trend
- `scans_completed` — should be increasing
- `rejected_total` — no unexplained increase
- `reconcile_drift` — should be 0
- `watcher_enabled` — should be true

Results append to `/tmp/vault-sync-soak.jsonl` with a trend table.

### Manual checks

```bash
# Local status
curl -s -H "Authorization: Bearer $TOKEN" \
  localhost:8351/koi-net/vault-sync/status | jq .metrics

# Local reconcile
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode":"detect"}' localhost:8351/koi-net/vault-sync/reconcile

# Peer status (via SSH)
ssh dobby@192.168.1.69 "curl -sf -H 'Authorization: Bearer <peer-token>' \
  localhost:8351/koi-net/vault-sync/status" | jq .metrics
```

Admin tokens are auto-read from `~/.config/personal-koi/koi-state/admin_token` by `soak-check.sh`.

## Go/No-Go Criteria

All must pass before enabling repair mode or proceeding to Sync-2:

| Criterion | Threshold |
|-----------|-----------|
| Soak duration | >= 72h on both peers |
| Rejected events | No unexplained increase vs baseline |
| Reconcile detect drift | 0 for 2 consecutive runs >= 1h apart |
| Pending event queue | < 100, no sustained upward trend across checks |
| Smoke test | 15/15 on both nodes (watcher off + on) at end of soak |
| No manual intervention | No forced restarts, DB fixes, or queue purges |

### Final smoke test (at 72h)

```bash
# Pass 1: watcher disabled
KOI_ADMIN_TOKEN=<local-token> PEER_KOI_ADMIN_TOKEN=<peer-token> \
  VAULT_SYNC_WATCHER=false MODE=two-peer \
  PEER_SSH=dobby@192.168.1.69 PEER_NAME=nuc-personal \
  bash scripts/federation/smoke-vault-sync.sh

# Pass 2: watcher enabled
KOI_ADMIN_TOKEN=<local-token> PEER_KOI_ADMIN_TOKEN=<peer-token> \
  MODE=two-peer \
  PEER_SSH=dobby@192.168.1.69 PEER_NAME=nuc-personal \
  bash scripts/federation/smoke-vault-sync.sh
```

## Rollback Procedure

If soak fails or anomalies are observed:

```bash
# 1. Disable watcher and repair on affected peer
export VAULT_SYNC_REPAIR_ENABLED=false
export VAULT_SYNC_WATCHER=false
# Restart service

# 2. Capture diagnostics before further action
curl -s -H "Authorization: Bearer $TOKEN" \
  localhost:8351/koi-net/vault-sync/status | jq . > soak-fail-status.json

curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode":"detect"}' localhost:8351/koi-net/vault-sync/reconcile > soak-fail-reconcile.json

# 3. Investigate from captured data before making any manual DB changes
```

## Post-Soak: Enable Repair Mode (Progressive)

After soak passes, enable repair on one peer at a time:

### Peer 1 (local) first

```bash
VAULT_SYNC_REPAIR_ENABLED=true  # add to env
# Restart

# Scoped repair on a small set
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode":"repair","confirm":true,"paths":["Shared/test-file.md"],"max_actions":5}' \
  localhost:8351/koi-net/vault-sync/reconcile

# Verify: detect should show 0 drift for repaired paths
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode":"detect"}' localhost:8351/koi-net/vault-sync/reconcile
```

### Peer 2 (Dobby) — only after Peer 1 verified

Repeat the same scoped repair + verify sequence on Dobby.

## Peer Onboarding

For onboarding a new peer (e.g., Shawn), see the dedicated runbook:

**[Peer Onboarding Runbook](peer-onboarding.md)**

It covers the full flow: bootstrap → approval → WireGuard → node setup → handshake → vault sync configure → smoke test → soak.

Requires migration `060_multi_peer_sync.sql` (included in `setup-node.sh`).

## 3-Peer Soak Test

After onboarding a third peer and completing the initial 2-peer soak, run the 3-peer chain forwarding smoke test to validate mesh forwarding:

```bash
THREE_PEER=1 \
  PEER_SSH=dobby@192.168.1.69 PEER_NAME=nuc-personal \
  THIRD_PEER_SSH=shawn@10.100.0.3 THIRD_PEER_NAME=shawn \
  KOI_ADMIN_TOKEN=$(cat ~/.config/personal-koi/koi-state/admin_token) \
  PEER_KOI_ADMIN_TOKEN=<nuc-token> \
  THIRD_PEER_ADMIN_TOKEN=<shawn-token> \
  bash scripts/federation/smoke-vault-sync.sh
```

This tests:
- **Chain topology**: Temporarily removes the A↔C link, forcing forwarding through B
- **Forward path** (A → B → C): File created on A arrives on C via B's `_forward_to_peers()`
- **Reverse path** (C → B → A): File created on C arrives on A via B
- **Loop detection**: Verifies B applies each event exactly once (dedup prevents re-forwarding)
- **Mesh restore**: Automatically re-establishes full mesh (A↔B, A↔C, B↔C) even if tests fail

After the 3-peer smoke passes, run `soak-check.sh` on all three nodes every 6-12h for 24h before enabling repair mode.
