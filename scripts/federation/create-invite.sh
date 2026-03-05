#!/usr/bin/env bash
# create-invite.sh — Admin generates an invite token for a new peer
#
# Usage: ./create-invite.sh [--dry-run] [--ttl <hours>] [--vault-sync-folder <folder>] [--relay-ssh <user@host>] <peer-name> <peer-number>
# Example: ./create-invite.sh shawn-personal 3 --ttl 24 --vault-sync-folder Shared

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# ============================================
# ARGUMENTS
# ============================================

TTL_HOURS=24
VAULT_SYNC_FOLDER="Shared"
RELAY_SSH="${RELAY_SSH:-poly@37.27.48.12}"

usage() {
    cat <<USAGE
Usage:
  $0 [--dry-run] [--ttl <hours>] [--vault-sync-folder <folder>] [--relay-ssh <user@host>] <peer-name> <peer-number>

Examples:
  $0 shawn-personal 3
  $0 --ttl 48 --vault-sync-folder Shared shawn-personal 3
  $0 --dry-run shawn-personal 3
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --ttl)
            TTL_HOURS="${2:-24}"
            shift 2
            ;;
        --vault-sync-folder)
            VAULT_SYNC_FOLDER="${2:-Shared}"
            shift 2
            ;;
        --relay-ssh)
            RELAY_SSH="${2:-}"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        --*)
            log_fatal "Unknown option: $1"
            ;;
        *)
            break
            ;;
    esac
done

if [[ $# -lt 2 ]]; then
    usage
    exit 1
fi

PEER_NAME="$1"
PEER_NUMBER="$2"

# ============================================
# VALIDATE
# ============================================

validate_peer_name "$PEER_NAME"
validate_peer_number "$PEER_NUMBER"
validate_ssh_target "$RELAY_SSH"

PEER_IP="10.100.0.${PEER_NUMBER}"

# Check peer_name not already in a live state
peer_registry_init
EXISTING=$(python3 -c "
import json, sys
reg = json.load(open('$PEER_REGISTRY'))
for p in reg:
    if p['peer_name'] == '$PEER_NAME' and p.get('status') in ('active', 'approving'):
        print(p['status'])
        sys.exit(0)
    if p['peer_name'] == '$PEER_NAME' and p.get('status') == 'invited':
        print('invited')
        sys.exit(0)
print('ok')
" 2>/dev/null || echo "ok")

if [[ "$EXISTING" == "active" ]]; then
    log_fatal "Peer '$PEER_NAME' already active in registry"
fi

if [[ "$EXISTING" == "approving" ]]; then
    log_fatal "Peer '$PEER_NAME' is mid-approval (status=approving). Complete or cancel that flow first."
fi

if [[ "$EXISTING" == "invited" ]]; then
    if $DRY_RUN; then
        log_warn "[DRY-RUN] Stale invite exists for '$PEER_NAME' — would cancel it"
    else
        log_warn "Stale invite exists for '$PEER_NAME'"
        read -rp "Overwrite stale invite? [y/N]: " confirm
        if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
            log_fatal "Aborted — remove stale entry first or choose a different name"
        fi
        # Remove stale entry
        peer_registry_update_status "$PEER_NAME" "cancelled" "cancelled_at" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    fi
fi

# Check peer_number not already assigned
EXISTING_NUM=$(python3 -c "
import json, sys
reg = json.load(open('$PEER_REGISTRY'))
for p in reg:
    if p.get('peer_number') == $PEER_NUMBER and p.get('status') in ('active', 'invited', 'approving'):
        print(p['peer_name'])
        sys.exit(0)
print('')
" 2>/dev/null || echo "")

if [[ -n "$EXISTING_NUM" ]]; then
    log_fatal "IP $PEER_IP already assigned to '$EXISTING_NUM'"
fi

# ============================================
# FETCH RELAY PUBLIC KEY
# ============================================

log_info "Fetching relay public key from $RELAY_SSH..."

RELAY_PUBKEY=""
RELAY_ENDPOINT="${RELAY_SSH#*@}"

if ! $DRY_RUN; then
    # Try reading from running interface first
    if RELAY_PUBKEY=$(run_ssh "$RELAY_SSH" "sudo wg show wg-koi public-key" 2>/dev/null); then
        log_info "Got relay pubkey from running WG interface"
    else
        # Fallback: derive from private key
        log_warn "Relay WG interface is down — deriving pubkey from private key"
        log_warn "Verify relay is running before peer connects"
        RELAY_PUBKEY=$(run_ssh "$RELAY_SSH" "sudo cat /etc/wireguard/koi_private.key | wg pubkey" 2>/dev/null) || \
            log_fatal "Cannot fetch relay public key via SSH"
    fi
else
    RELAY_PUBKEY="<relay-public-key-dry-run>"
fi

# ============================================
# READ ADMIN NODE INFO
# ============================================

log_info "Reading admin node info..."

ADMIN_WG_IP=""
ADMIN_BASE_URL=""

if ! $DRY_RUN; then
    HEALTH=$(curl -sf "http://127.0.0.1:8351/koi-net/health" 2>/dev/null) || \
        log_fatal "Cannot reach local KOI-net API at http://127.0.0.1:8351/koi-net/health"
    ADMIN_BASE_URL=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin)['profile']['base_url'])" 2>/dev/null) || \
        log_fatal "Cannot extract base_url from health response"
    # Extract WG IP from base_url (http://10.100.0.X:8351)
    ADMIN_WG_IP=$(echo "$ADMIN_BASE_URL" | python3 -c "
import sys
from urllib.parse import urlparse
url = sys.stdin.read().strip()
print(urlparse(url).hostname)
" 2>/dev/null) || log_fatal "Cannot extract admin WG IP from base_url"
else
    ADMIN_BASE_URL="http://10.100.0.2:8351"
    ADMIN_WG_IP="10.100.0.2"
fi

# ============================================
# BUILD TOKEN
# ============================================

log_info "Generating invite token..."

STATE_DIR="${KOI_STATE_DIR:-$HOME/.config/personal-koi/koi-state}"

if $DRY_RUN; then
    # Dry-run: generate token with ephemeral secret (no disk writes)
    TOKEN=$(python3 -c "
import sys, os, time, json, secrets
sys.path.insert(0, '$SCRIPT_DIR')
from invite_token import create_token, generate_jti

secret = secrets.token_bytes(32)  # ephemeral, not persisted
now = int(time.time())
payload = {
    'v': 1,
    'jti': generate_jti(),
    'peer_name': '$PEER_NAME',
    'peer_number': $PEER_NUMBER,
    'relay_pubkey': '$RELAY_PUBKEY',
    'relay_endpoint': '${RELAY_ENDPOINT}:51820',
    'admin_base_url': '$ADMIN_BASE_URL',
    'admin_wg_ip': '$ADMIN_WG_IP',
    'vault_sync_folder': '$VAULT_SYNC_FOLDER',
    'issued_at': now,
    'expires_at': now + ($TTL_HOURS * 3600),
}
print(create_token(payload, secret))
") || log_fatal "Failed to generate invite token"
    JTI="<dry-run-jti>"
else
    TOKEN=$(python3 -c "
import sys, os, time, json
sys.path.insert(0, '$SCRIPT_DIR')
from invite_token import create_token, verify_token, load_or_create_secret, generate_jti

secret = load_or_create_secret('$STATE_DIR')
now = int(time.time())
payload = {
    'v': 1,
    'jti': generate_jti(),
    'peer_name': '$PEER_NAME',
    'peer_number': $PEER_NUMBER,
    'relay_pubkey': '$RELAY_PUBKEY',
    'relay_endpoint': '${RELAY_ENDPOINT}:51820',
    'admin_base_url': '$ADMIN_BASE_URL',
    'admin_wg_ip': '$ADMIN_WG_IP',
    'vault_sync_folder': '$VAULT_SYNC_FOLDER',
    'issued_at': now,
    'expires_at': now + ($TTL_HOURS * 3600),
}
token = create_token(payload, secret)

# Round-trip verify
verified = verify_token(token, secret)
assert verified['peer_name'] == '$PEER_NAME', 'Round-trip verification failed'

print(token)
") || log_fatal "Failed to generate invite token"

    # Extract JTI for registry
    JTI=$(python3 -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')
from invite_token import decode_token
payload = decode_token('$TOKEN')
print(payload['jti'])
")
fi

# ============================================
# REGISTER INVITED ENTRY
# ============================================

log_info "Recording invite in peer registry..."

ADDED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)

REGISTRY_ENTRY=$(python3 -c "
import json
entry = {
    'peer_name': '$PEER_NAME',
    'peer_number': $PEER_NUMBER,
    'wg_ip': '$PEER_IP',
    'jti': '$JTI',
    'status': 'invited',
    'consumed': False,
    'added_at': '$ADDED_AT',
}
print(json.dumps(entry))
")

if $DRY_RUN; then
    echo "[DRY-RUN] Would add to peer registry:"
    echo "$REGISTRY_ENTRY" | python3 -m json.tool
    echo ""
    echo "[DRY-RUN] Token would be:"
    echo "$TOKEN"
else
    peer_registry_add "$REGISTRY_ENTRY"
    log_info "Registered invite for $PEER_NAME (peer_number=$PEER_NUMBER)"
fi

# ============================================
# PRINT TOKEN + INSTRUCTIONS
# ============================================

echo ""
echo "==================================="
echo "  Invite Token Created"
echo "==================================="
echo ""
echo "  Peer:    $PEER_NAME"
echo "  IP:      $PEER_IP"
echo "  TTL:     ${TTL_HOURS}h"
echo "  JTI:     $JTI"
echo ""
echo "  Token (send via Signal — single line, safe to paste):"
echo ""
echo "  $TOKEN"
echo ""
echo "  Peer runs:"
echo "    ./bootstrap-node.sh --invite \"$TOKEN\" --yes"
echo ""
echo "  After peer sends WG public key, admin runs:"
echo "    ./approve-peer.sh --pubkey-only <pubkey> $PEER_NUMBER"
echo ""
echo "  After WG tunnel is up + handshake completes:"
echo "    1. Both sides verify SAS code over Signal"
echo "    2. Admin runs: ./approve-peer-edges.sh $PEER_NAME"
echo "==================================="
