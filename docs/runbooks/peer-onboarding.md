# Peer Onboarding Runbook

## Overview

This runbook walks through onboarding a new peer into the KOI-net E2EE vault sync mesh. By the end, the new peer will have:

- A WireGuard tunnel to the relay (and thus reachability to all peers)
- A running KOI-net node with PostgreSQL, migrations, and API
- Bidirectional handshake with fingerprint-verified peers
- Vault sync configured and tested

**Architecture:** Star topology over WireGuard — all peers connect to a central relay (`37.27.48.12:51820`), which forwards UDP packets between peers. KOI-net vault sync runs over this mesh, with E2EE (X25519 + ChaCha20-Poly1305) so the relay cannot read file contents.

```
                    ┌─────────────┐
                    │  WG Relay   │
                    │ 37.27.48.12 │
                    │ 10.100.0.1  │
                    └──┬───┬───┬──┘
                       │   │   │
            ┌──────────┘   │   └──────────┐
            │              │              │
   ┌────────┴───┐  ┌──────┴─────┐  ┌─────┴────────┐
   │  darren    │  │    nuc     │  │    shawn     │
   │ 10.100.0.2 │  │ 10.100.0.22│  │ 10.100.0.3  │
   │  :8351     │  │  :8351     │  │  :8351      │
   └────────────┘  └────────────┘  └─────────────┘
```

**Time estimate:** ~45 min for full setup (assuming prerequisites installed).

## Prerequisites

| Requirement | Details |
|-------------|---------|
| Machine | macOS or Linux with sudo access |
| Network | Outbound UDP to relay `37.27.48.12:51820` |
| Software | git, Python 3.11+, PostgreSQL 14+ (`bootstrap-node.sh` installs these if missing) |
| Out-of-band channel | Signal or phone call for fingerprint verification |
| Admin coordination | Admin must be available to run `approve-peer.sh` and `connect-peers.sh` |

## Phase 1: Bootstrap (Peer's Machine)

The bootstrap script handles everything from OS dependencies to key generation in one command.

```bash
cd ~
git clone https://github.com/gaiaaiagent/koi-processor.git projects/RegenAI/koi-processor
cd projects/RegenAI/koi-processor

./scripts/federation/bootstrap-node.sh shawn-personal 10.100.0.3
```

**What it does (6 steps):**

| Step | Action | What to expect |
|------|--------|----------------|
| 1 | Install OS prerequisites (WireGuard, Python, PostgreSQL, git, curl, jq) | May prompt for sudo password |
| 2 | Create PostgreSQL role + `personal_koi` database | `[INFO] PostgreSQL already ready` if DB exists |
| 3 | Clone/update koi-processor repo to `regen-prod` | Git checkout output |
| 4 | Create Python venv + install dependencies | pip install output |
| 5 | Generate WireGuard + KOI keypairs, write join request | Calls `join-request.sh` internally |
| 6 | Run validation checks | Calls `validate-node.sh` |

**Expected success output:**

```
===================================
  Bootstrap Complete
===================================

  Join request file:
    $HOME/.config/personal-koi/wireguard/join-request.txt

  Next (admin side):
    1) Approve peer with approve-peer.sh --from-file <join-request>
    2) Provide wg-koi.conf template to peer

  Next (peer side after approval):
    1) Activate WireGuard (wg-quick up wg-koi)
    2) Run setup-node.sh --yes shawn-personal 10.100.0.3
    3) Run connect-peers.sh http://10.100.0.2:8351 darren
===================================
```

**Verify join request was generated:**

```bash
cat ~/.config/personal-koi/wireguard/join-request.txt
```

Should show `peer_name`, `wg_public_key`, `koi_public_key`, `node_rid`, `key_fingerprint`, and expiration (24h).

**Send the join request content to admin via Signal.**

### Troubleshooting

| Problem | Fix |
|---------|-----|
| `apt-get` / `brew` fails | Run manually: `sudo apt install wireguard-tools python3-venv postgresql` (Linux) or `brew install wireguard-tools python postgresql` (macOS) |
| `psql: FATAL: role "shawn" does not exist` | `sudo -u postgres createuser --superuser shawn` then re-run |
| `psql: could not connect to server` | Start PostgreSQL: `sudo systemctl start postgresql` (Linux) or `brew services start postgresql` (macOS) |
| Python deps fail | The script falls back to `requirements-bootstrap.txt`. If that also fails, check Python version: `python3 --version` (need 3.11+) |
| Join request expired | Re-run `./scripts/federation/join-request.sh shawn-personal` to regenerate (24h expiry) |

## Phase 2: Approval (Admin's Machine)

Admin receives the join request content via Signal and saves it to a file.

```bash
cd ~/projects/regenai/koi-processor

# Save the join request content to a file
cat > /tmp/shawn-join-request.txt << 'EOF'
<paste join request content here>
EOF

# Approve: peer number 3 → WireGuard IP 10.100.0.3
./scripts/federation/approve-peer.sh --from-file /tmp/shawn-join-request.txt 3
```

**What it does:**

1. Records peer in `~/.config/personal-koi/peer-registry.json` (peer_name, wg_ip, wg_pubkey, koi_pubkey, node_rid)
2. SSHs to relay (`poly@37.27.48.12`) and adds peer to `/etc/wireguard/wg-koi.conf`
3. Fetches relay public key
4. Generates WireGuard config template at `federation-invite-shawn-personal/wg-koi.conf`

**Expected output:**

```
===================================
  Peer Approved: shawn-personal
===================================

  WireGuard IP:   10.100.0.3
  Relay endpoint: 37.27.48.12:51820

  Config template: federation-invite-shawn-personal/wg-koi.conf
    (peer must insert their private key)
===================================
```

**Verify relay config updated:**

```bash
ssh poly@37.27.48.12 "sudo wg show wg-koi"
```

Should show a new peer entry with `allowed-ips: 10.100.0.3/32`.

**Send the WireGuard config template to peer via Signal.**

## Phase 3: Activate Tunnel (Peer's Machine)

Peer receives the `wg-koi.conf` template from admin.

### Linux

```bash
# Insert your private key (from ~/.config/personal-koi/wireguard/private.key)
PRIVATE_KEY=$(cat ~/.config/personal-koi/wireguard/private.key)
sed -i "s|<PASTE_YOUR_PRIVATE_KEY_HERE>|${PRIVATE_KEY}|" wg-koi.conf

# Install config
sudo cp wg-koi.conf /etc/wireguard/wg-koi.conf
sudo chmod 600 /etc/wireguard/wg-koi.conf

# Activate
sudo wg-quick up wg-koi
```

### macOS

1. Open WireGuard.app
2. Import tunnel from file → select `wg-koi.conf`
3. Edit: replace `<PASTE_YOUR_PRIVATE_KEY_HERE>` with contents of `~/.config/personal-koi/wireguard/private.key`
4. Activate the tunnel

### Verify connectivity

```bash
# Relay
ping -c 3 10.100.0.1
# Expected: 3 packets transmitted, 3 received

# Admin node (darren)
ping -c 3 10.100.0.2
# Expected: 3 packets transmitted, 3 received
```

### Troubleshooting

| Problem | Fix |
|---------|-----|
| `ping 10.100.0.1` fails | Check firewall allows outbound UDP 51820. On corporate networks, try a different port or hotspot. |
| `ping 10.100.0.2` fails but relay works | Admin's WireGuard may be down. Have admin check `sudo wg show wg-koi` and `wg-quick up wg-koi`. |
| `RTNETLINK answers: Operation not permitted` | Need sudo for `wg-quick up`. |
| macOS: "Unable to import tunnel" | Ensure WireGuard.app is installed from App Store or `brew install --cask wireguard-go`. |

## Phase 4: Node Setup (Peer's Machine)

With the WireGuard tunnel active, configure the KOI-net node:

```bash
cd ~/projects/RegenAI/koi-processor

./scripts/federation/setup-node.sh --yes shawn-personal 10.100.0.3
```

**What it does (11 steps):**

| Step | Action | What to expect |
|------|--------|----------------|
| 1 | Verify WireGuard tunnel (ping relay) | `[INFO] Relay reachable at 10.100.0.1` |
| 2 | Create state directories | `~/.config/personal-koi/koi-state/` |
| 3 | Generate `personal.env` from template | Substitutes node name, WG IP, state dir |
| 4 | Verify config settings | Checks `KOI_BASE_URL` is non-localhost, security flags enabled |
| 5 | Configure API firewall rules | Port 8351 only on loopback + WireGuard interface |
| 6 | Run database migrations (through 060) | `[INFO] Applying migration 040...` through `060` |
| 7 | Install Python crypto deps | `pip install cryptography` |
| 8 | Generate `start.sh` | At `~/.config/personal-koi/start.sh` |
| 9 | Start service + wait for health | Polls `/health` for up to 30s |
| 10 | Verify KOI key continuity | Compares loaded identity against peer registry |
| 11 | Print summary | Node RID, fingerprint, WG IP, API endpoint |

**Expected success output:**

```
===================================
  KOI-net Node Setup Complete
===================================

  Node name:       shawn-personal
  Node RID:        orn:koi-net.node:shawn-personal-<hash>
  Key fingerprint: SHA256:...
  WireGuard IP:    10.100.0.3
  KOI API:         http://10.100.0.3:8351
  Start script:    ~/.config/personal-koi/start.sh
===================================
```

**Run validation:**

```bash
./scripts/federation/validate-node.sh --expect-wg-ip 10.100.0.3
```

Expected: all PASS, 0 WARN, 0 FAIL.

### Troubleshooting

| Problem | Fix |
|---------|-----|
| `[FATAL] Cannot reach relay (10.100.0.1)` | WireGuard tunnel is down — activate it first (Phase 3) |
| `[FATAL] KOI_BASE_URL must be the WireGuard IP` | The generated `personal.env` has wrong `KOI_BASE_URL`. Edit `<koi-processor-path>/config/personal.env` and set `KOI_BASE_URL=http://10.100.0.3:8351` |
| `[FATAL] Service did not become healthy within 30s` | Check logs: `tail -50 /tmp/koi-processor.log`. Common: port 8351 in use (`lsof -ti tcp:8351`) or missing env vars. |
| Migration fails | Check PostgreSQL is running and `personal_koi` DB exists: `psql -l \| grep personal_koi` |

## Phase 5: Handshake (Both Sides)

The handshake establishes trust via TOFU (Trust On First Use) fingerprint verification over an out-of-band channel (Signal call).

### Peer runs first:

```bash
cd ~/projects/RegenAI/koi-processor

./scripts/federation/connect-peers.sh http://10.100.0.2:8351 darren
```

**Interactive prompts:**

1. **Fingerprint verification:** The script displays the remote node's key fingerprint. Read it aloud to admin over Signal. Admin confirms it matches their node's fingerprint.

   ```
   Remote node fingerprint: SHA256:abc123...
   Does the fingerprint match? [y/N]: y
   ```

2. **Edge approval:** Approve the outbound edge to allow the remote peer to poll your events.

   ```
   Approve outbound edge to darren? This allows them to poll your events. [y/N]: y
   ```

### Admin runs second:

```bash
cd ~/projects/regenai/koi-processor

./scripts/federation/connect-peers.sh http://10.100.0.3:8351 shawn
```

Same two prompts — verify fingerprint (peer reads theirs over Signal), approve edge.

### Verify: both sides show APPROVED edges

**On peer:**
```bash
psql personal_koi -c "SELECT source_node, target_node, status FROM koi_net_edges WHERE status='APPROVED';"
```

**On admin:**
```bash
psql personal_koi -c "SELECT source_node, target_node, status FROM koi_net_edges WHERE status='APPROVED';"
```

Both should show two APPROVED edges (one inbound, one outbound) for the peer relationship.

## Phase 6: Configure Vault Sync

Both sides must configure which folders to sync with the new peer.

### Admin configures sync to new peer:

```bash
TOKEN=$(cat ~/.config/personal-koi/koi-state/admin_token)

curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"peer": "shawn", "shared_folder": "Shared"}' \
  http://localhost:8351/koi-net/vault-sync/configure
```

Expected response: `{"peer_node_rid": "orn:koi-net.node:shawn-personal-...", ...}`

### Peer configures sync to admin:

```bash
TOKEN=$(cat ~/.config/personal-koi/koi-state/admin_token)

curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"peer": "darren", "shared_folder": "Shared"}' \
  http://localhost:8351/koi-net/vault-sync/configure
```

### Verify vault sync status

On both sides:

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8351/koi-net/vault-sync/status | python3 -m json.tool
```

Check:
- `peer_count` includes the new peer
- `peers[]` array lists the new peer with correct `shared_folder`

## Phase 7: Smoke Test

### Quick manual test

```bash
# Admin creates test file
echo "# Hello from darren $(date)" > ~/Documents/Notes/Shared/_onboarding-test.md

# Trigger sync
TOKEN=$(cat ~/.config/personal-koi/koi-state/admin_token)
curl -sf -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8351/koi-net/vault-sync/trigger

# Peer triggers sync (so it polls immediately)
# On peer's machine:
TOKEN=$(cat ~/.config/personal-koi/koi-state/admin_token)
curl -sf -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8351/koi-net/vault-sync/trigger

# Peer checks file arrived
cat ~/Documents/Notes/Shared/_onboarding-test.md
```

### Reverse direction

```bash
# Peer creates test file
echo "# Hello from shawn $(date)" > ~/Documents/Notes/Shared/_onboarding-test-reverse.md

# Trigger sync on peer, then admin triggers sync
# Admin checks file arrived:
cat ~/Documents/Notes/Shared/_onboarding-test-reverse.md
```

### Delete propagation

```bash
# Admin deletes test files
rm ~/Documents/Notes/Shared/_onboarding-test.md
rm ~/Documents/Notes/Shared/_onboarding-test-reverse.md

# Trigger sync, verify peer's copies are also removed
```

### Full automated smoke test

```bash
MODE=two-peer \
  PEER_SSH=shawn@10.100.0.3 \
  PEER_NAME=shawn \
  KOI_ADMIN_TOKEN=$(cat ~/.config/personal-koi/koi-state/admin_token) \
  PEER_KOI_ADMIN_TOKEN=<peer-admin-token> \
  bash scripts/federation/smoke-vault-sync.sh
```

Expected: all PASS, 0 FAIL.

## Phase 8: Soak

### Install soak monitoring on both sides

On both admin and peer machines:

```bash
# Run soak check every 6-12 hours
bash scripts/federation/soak-check.sh
```

Results append to `/tmp/vault-sync-soak.jsonl`.

### Monitor for 24 hours

Check for:
- `pending_events` stays < 100, no sustained upward trend
- `rejected_total` — no unexplained increase
- `reconcile_drift` — should be 0
- `scans_completed` — should be increasing

### After 24h: enable repair

On each peer (one at a time):

1. Edit `<koi-processor-path>/config/personal.env`:
   ```bash
   VAULT_SYNC_REPAIR_ENABLED=true    # gates manual POST /vault-sync/reconcile with repair mode
   VAULT_SYNC_AUTO_REPAIR=true       # controls whether scheduled reconcile auto-repairs drift
   ```

2. Restart service (required — `VAULT_SYNC_AUTO_REPAIR` is read at startup):
   ```bash
   # Kill existing process, then restart
   kill $(lsof -ti tcp:8351) 2>/dev/null; sleep 2
   ~/.config/personal-koi/start.sh
   ```

3. Test scoped repair:
   ```bash
   TOKEN=$(cat ~/.config/personal-koi/koi-state/admin_token)
   curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"mode":"repair","confirm":true,"paths":["Shared/test-file.md"],"max_actions":5}' \
     http://localhost:8351/koi-net/vault-sync/reconcile
   ```

See `docs/runbooks/vault-sync-soak.md` for detailed soak procedures and go/no-go criteria.

## Appendix A: Pre-staged Shawn Payloads

These values are reserved for Shawn's onboarding:

| Field | Value |
|-------|-------|
| Node name | `shawn-personal` |
| Peer number | 3 |
| WireGuard IP | `10.100.0.3` |
| Expected node RID format | `orn:koi-net.node:shawn-personal-<hash>` |
| Admin WG IP (darren) | `10.100.0.2` |
| NUC WG IP | `10.100.0.22` |
| Relay endpoint | `37.27.48.12:51820` |

### Configure commands (post-handshake)

**On Darren's machine:**
```bash
TOKEN=$(cat ~/.config/personal-koi/koi-state/admin_token)
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"peer": "shawn", "shared_folder": "Shared"}' \
  http://localhost:8351/koi-net/vault-sync/configure
```

**On Shawn's machine:**
```bash
TOKEN=$(cat ~/.config/personal-koi/koi-state/admin_token)
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"peer": "darren", "shared_folder": "Shared"}' \
  http://localhost:8351/koi-net/vault-sync/configure
```

**On NUC (if Shawn syncs with NUC too):**
```bash
# On NUC:
TOKEN=$(cat ~/.config/personal-koi/koi-state/admin_token)
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"peer": "shawn", "shared_folder": "Shared"}' \
  http://localhost:8351/koi-net/vault-sync/configure

# On Shawn:
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"peer": "nuc", "shared_folder": "Shared"}' \
  http://localhost:8351/koi-net/vault-sync/configure
```

### Fingerprint verification checklist

During the Signal call for `connect-peers.sh`:

- [ ] Peer reads their node fingerprint aloud → Admin confirms match
- [ ] Admin reads their node fingerprint aloud → Peer confirms match
- [ ] Both approve outbound edges when prompted
- [ ] Verify: `psql personal_koi -c "SELECT status FROM koi_net_edges;"` shows APPROVED on both sides

## Appendix B: Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Handshake succeeded but no sync | Vault sync not configured on one or both sides | Run `configure` on both sides (Phase 6) |
| `pending_events` growing | Peer unreachable or not polling | Check peer WireGuard: `ping <peer-wg-ip>`. Check peer service: `curl -sf http://<peer-wg-ip>:8351/health` |
| `unauthorized_source` rejections | Peer not configured as vault sync partner | Run `configure` for that peer on the rejecting node |
| Drift detected in reconcile | Files diverged between peers | Run `reconcile` with `{"mode":"detect"}` first, then `{"mode":"repair","confirm":true}` if `VAULT_SYNC_REPAIR_ENABLED=true` |
| Key rotation needed | Compromised key or node rebuild | Run `remove-peer.sh` on admin, then re-onboard from Phase 1 |
| `events_skipped_dedup` increasing | Normal in multi-peer mesh — forwarded events hitting dedup | No action needed unless files aren't arriving |
| Service won't start (port in use) | Stale process on port 8351 | `kill $(lsof -ti tcp:8351)` then restart |
| Peer shows in status but files don't sync | Shared folder mismatch | Verify both sides configured same `shared_folder` value |
| `[FATAL] Key mismatch` during setup | Node identity changed since join request | Re-run `join-request.sh` and have admin re-approve |
