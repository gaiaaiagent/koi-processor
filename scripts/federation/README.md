# KOI-net Federation Scripts

These scripts automate peer onboarding, relay updates, security checks, and KOI-net handshake.

## Blank Host Bootstrap (Recommended)

Use this on a fresh Ubuntu/macOS peer host to install prerequisites, prepare PostgreSQL + Python environment, and generate a join request:

```bash
cd ~/projects/RegenAI/koi-processor/scripts/federation
./bootstrap-node.sh --yes <node-name> <wireguard-ip> [koi-processor-path]
```

Example:

```bash
./bootstrap-node.sh --yes nuc-personal 10.100.0.22 ~/projects/RegenAI/koi-processor
```

What `bootstrap-node.sh` does:

1. Installs prerequisites (`wireguard-tools`, Python venv/pip, PostgreSQL, `git`, `curl`, `jq`).
2. Ensures local DB role/user + `personal_koi` database exist.
3. Clones/updates `koi-processor` and checks out a target git ref.
4. Creates `venv` and installs `requirements.txt`.
   - If full dependency resolution fails on a blank host, it falls back to
     `scripts/federation/requirements-bootstrap.txt` (runtime subset).
5. Runs `join-request.sh` (unless `--skip-join-request`).
6. Runs `validate-node.sh` checks.

## Secure Peer Onboarding Flow

1. Peer machine:
   - `./join-request.sh <node-name>`
2. Admin machine:
   - `./approve-peer.sh --from-file <join-request.txt> <peer-number> [relay-ssh]`
3. Peer machine:
   - Insert private key into generated `wg-koi.conf`, then `wg-quick up wg-koi`
4. Peer machine:
   - `./setup-node.sh --yes <node-name> <wireguard-ip> <koi-processor-path>`
5. Either side:
   - `./connect-peers.sh http://<other-peer-wg-ip>:8351 <alias>`

## Validation

Run post-setup validation:

```bash
./validate-node.sh --expect-wg-ip 10.100.0.22
```

Checks include:

- Required commands available (`wg`, `psql`, `curl`, `python3`)
- Security policy flags enabled in `config/personal.env`
- `KOI_BASE_URL` uses WireGuard address (not localhost)
- WireGuard tunnel reachability (`10.100.0.1`)
- PostgreSQL connectivity (`personal_koi`)
- Local API and `/koi-net/health` status

Use `--strict` to fail on warnings.

## Federation Smoke Test (Recommended)

After both peers are approved and connected, run a bidirectional share smoke test.

```bash
# From peer A: queue a test share to peer B
curl -sS -X POST http://127.0.0.1:8351/koi-net/share \
  -H 'Content-Type: application/json' \
  -d '{
    "document_rid":"orn:personal-koi.testdoc:smoke-a-to-b-<ts>",
    "recipient":"<alias-for-peer-b>",
    "message":"smoke a->b",
    "contents":{"title":"smoke","body":"federation test"}
  }'

# On peer B: verify receipt
curl -sS "http://127.0.0.1:8351/koi-net/shared-with-me?from_peer=<alias-for-peer-a>&limit=10"
```

`/koi-net/shared-with-me` supports `since` as ISO-8601 datetime:

```bash
curl -sS "http://127.0.0.1:8351/koi-net/shared-with-me?since=2026-02-25T20:22:00Z&limit=10"
```

## Non-Interactive Setup

`setup-node.sh` supports automation flags:

```bash
./setup-node.sh --yes --force <node-name> <wireguard-ip> [koi-processor-path]
```

- `--yes`: auto-accepts firewall sudo prompts
- `--force`: overwrite `config/personal.env` (with backup)
- `--skip-firewall`: leaves firewall unchanged

Migration note:

- `setup-node.sh` defaults `KOI_MIGRATION_MIN_NUM=40` for federation setup, so
  federation-relevant migrations run without legacy pre-federation table
  dependencies.
- Override by exporting `KOI_MIGRATION_MIN_NUM` explicitly.

## Vault Sync Smoke Test

After both peers have vault sync enabled (`VAULT_SYNC_ENABLED=true` in `config/personal.env`) and the `Shared/` folder exists, run the two-mode smoke test:

```bash
# Local mode (single node, no peer needed):
KOI_ADMIN_TOKEN=<token> PEER_NAME=<peer-alias> \
  bash scripts/federation/smoke-vault-sync.sh

# Two-peer mode (requires SSH to peer):
KOI_ADMIN_TOKEN=<local-token> \
  MODE=two-peer \
  PEER_NAME=<peer-alias> \
  PEER_SSH=<user>@<peer-ip> \
  PEER_VAULT_PATH=<peer-vault-path> \
  PEER_KOI_ADMIN_TOKEN=<peer-token> \
  bash scripts/federation/smoke-vault-sync.sh
```

Prerequisites:
- Migration 049 applied on both nodes
- `VAULT_SYNC_ENABLED=true` in both nodes' `config/personal.env`
- `Shared/` folder exists in both vaults
- Handshake refreshed so edge `rid_types` includes `Vault-file`
- API running on both nodes

The smoke test validates: configure, file create/track, peer arrival, conflict detection, delete/tombstone, peer delete propagation, and no rejected events.

## Dry Run

Use `--dry-run` on bootstrap/approval/removal scripts before mutating:

```bash
./bootstrap-node.sh --dry-run --yes nuc-personal 10.100.0.22
./approve-peer.sh --dry-run --from-file ./join-request.txt 22
./remove-peer.sh --dry-run nuc-personal
```
