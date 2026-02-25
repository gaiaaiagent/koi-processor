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

## Non-Interactive Setup

`setup-node.sh` supports automation flags:

```bash
./setup-node.sh --yes --force <node-name> <wireguard-ip> [koi-processor-path]
```

- `--yes`: auto-accepts firewall sudo prompts
- `--force`: overwrite `config/personal.env` (with backup)
- `--skip-firewall`: leaves firewall unchanged

Fresh DB note:

- On blank hosts, `setup-node.sh` auto-sets `KOI_MIGRATION_MIN_NUM=40` when
  `koi_net_events` does not exist, so federation-relevant migrations can run
  without legacy pre-federation table dependencies.
- Override by exporting `KOI_MIGRATION_MIN_NUM` explicitly.

## Dry Run

Use `--dry-run` on bootstrap/approval/removal scripts before mutating:

```bash
./bootstrap-node.sh --dry-run --yes nuc-personal 10.100.0.22
./approve-peer.sh --dry-run --from-file ./join-request.txt 22
./remove-peer.sh --dry-run nuc-personal
```
