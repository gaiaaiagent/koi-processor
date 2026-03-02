#!/usr/bin/env bash
# approve-peer.sh — Admin adds peer to relay mesh (no private keys)
#
# Usage: ./approve-peer.sh [--dry-run] <peer-name> <peer-number> <wg-public-key> [relay-ssh]
# Example: ./approve-peer.sh shawn-personal 3 "abc123..." poly@37.27.48.12
#
# Can also read from join request file:
#   ./approve-peer.sh --from-file <join-request.txt> <peer-number> [relay-ssh]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# ============================================
# PARSE ARGS
# ============================================

if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
    shift
fi

FROM_FILE=""
if [[ "${1:-}" == "--from-file" ]]; then
    FROM_FILE="$2"
    shift 2
fi

if [[ -n "$FROM_FILE" ]]; then
    # Parse from join request file
    if [[ $# -lt 1 ]]; then
        echo "Usage: $0 [--dry-run] --from-file <join-request.txt> <peer-number> [relay-ssh]"
        exit 1
    fi
    PEER_NUMBER="$1"
    RELAY_SSH="${2:-poly@37.27.48.12}"

    # Parse join request
    FIELDS=$(parse_join_request "$FROM_FILE")
    if [[ $? -ne 0 ]]; then
        log_fatal "Failed to parse join request"
    fi

    PEER_NAME=$(echo "$FIELDS" | grep '^peer_name=' | cut -d= -f2-)
    WG_PUBKEY=$(echo "$FIELDS" | grep '^wg_public_key=' | cut -d= -f2-)
    KOI_PUBKEY=$(echo "$FIELDS" | grep '^koi_public_key=' | cut -d= -f2-)
    NODE_RID=$(echo "$FIELDS" | grep '^node_rid=' | cut -d= -f2-)
else
    if [[ $# -lt 3 ]]; then
        echo "Usage: $0 [--dry-run] <peer-name> <peer-number> <wg-public-key> [relay-ssh]"
        echo "   or: $0 [--dry-run] --from-file <join-request.txt> <peer-number> [relay-ssh]"
        exit 1
    fi
    PEER_NAME="$1"
    PEER_NUMBER="$2"
    WG_PUBKEY="$3"
    RELAY_SSH="${4:-poly@37.27.48.12}"
    KOI_PUBKEY=""
    NODE_RID=""
fi

# ============================================
# VALIDATE INPUTS
# ============================================

validate_peer_name "$PEER_NAME"
validate_peer_number "$PEER_NUMBER"
validate_wg_pubkey "$WG_PUBKEY"
validate_ssh_target "$RELAY_SSH"

PEER_IP="10.100.0.${PEER_NUMBER}"

log_info "Approving peer: $PEER_NAME (IP: $PEER_IP)"
log_info "Relay: $RELAY_SSH"
log_info "Dry run: $DRY_RUN"

# ============================================
# STEP 1: Record in peer registry
# ============================================

log_info "Step 1: Recording in peer registry..."

ADDED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)

REGISTRY_ENTRY=$(python3 -c "
import json
entry = {
    'peer_name': '$PEER_NAME',
    'peer_number': $PEER_NUMBER,
    'wg_ip': '$PEER_IP',
    'wg_pubkey': '$WG_PUBKEY',
    'koi_pubkey': '${KOI_PUBKEY:-}',
    'node_rid': '${NODE_RID:-}',
    'added_at': '$ADDED_AT',
    'status': 'active'
}
# Remove empty optional fields
entry = {k: v for k, v in entry.items() if v}
if 'status' not in entry:
    entry['status'] = 'active'
print(json.dumps(entry))
")

if $DRY_RUN; then
    echo "[DRY-RUN] Would add to peer registry:"
    echo "$REGISTRY_ENTRY" | python3 -m json.tool
else
    peer_registry_add "$REGISTRY_ENTRY"
    log_info "Added to peer registry: $PEER_REGISTRY"
fi

# ============================================
# STEP 2: Transactional relay config edit
# ============================================

log_info "Step 2: Adding peer to relay WireGuard config..."

relay_config_edit "$RELAY_SSH" add-peer "$WG_PUBKEY" "$PEER_IP"

# ============================================
# STEP 3: Get relay public key
# ============================================

log_info "Step 3: Fetching relay public key..."

RELAY_PUBKEY=""
RELAY_ENDPOINT="${RELAY_SSH#*@}"
if ! $DRY_RUN; then
    RELAY_PUBKEY=$(ssh -o ConnectTimeout=10 "$RELAY_SSH" "sudo cat /etc/wireguard/koi_private.key | wg pubkey")
else
    RELAY_PUBKEY="<relay-public-key>"
fi

# ============================================
# STEP 4: Generate peer's WireGuard config template
# ============================================

log_info "Step 4: Generating WireGuard config template for peer..."

INVITE_DIR="federation-invite-${PEER_NAME}"
mkdir -p "$INVITE_DIR"

cat > "$INVITE_DIR/wg-koi.conf" <<CONF
[Interface]
Address = ${PEER_IP}/24
PrivateKey = <PASTE_YOUR_PRIVATE_KEY_HERE>

[Peer]
PublicKey = ${RELAY_PUBKEY}
Endpoint = ${RELAY_ENDPOINT}:51820
AllowedIPs = 10.100.0.0/24
PersistentKeepalive = 25
CONF

# ============================================
# STEP 5: Print setup instructions
# ============================================

echo ""
echo "==================================="
echo "  Peer Approved: $PEER_NAME"
echo "==================================="
echo ""
echo "  WireGuard IP:   $PEER_IP"
echo "  Relay endpoint: ${RELAY_ENDPOINT}:51820"
echo ""
echo "  Config template: $INVITE_DIR/wg-koi.conf"
echo "    (peer must insert their private key)"
echo ""
echo "  Send to peer:"
echo "    1. The config template ($INVITE_DIR/wg-koi.conf)"
echo "    2. Instructions to:"
echo "       a. Replace <PASTE_YOUR_PRIVATE_KEY_HERE> with their WG private key"
echo "       b. Copy to /etc/wireguard/wg-koi.conf (Linux) or use WireGuard app (macOS)"
echo "       c. Activate: wg-quick up wg-koi (Linux) or toggle in WireGuard app"
echo "       d. Test: ping 10.100.0.1"
echo "       e. Run: ./setup-node.sh $PEER_NAME $PEER_IP"
echo "       f. Run: ./connect-peers.sh http://10.100.0.2:8351 darren"
echo ""
if [[ -n "${KOI_PUBKEY:-}" ]]; then
    FINGERPRINT=$(compute_koi_fingerprint "$KOI_PUBKEY")
    echo "  KOI key fingerprint: $FINGERPRINT"
    echo "    (verify with peer during connect-peers.sh)"
fi
echo "==================================="
