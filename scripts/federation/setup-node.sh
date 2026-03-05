#!/usr/bin/env bash
# setup-node.sh — KOI-net node setup on peer's machine
#
# Usage: ./setup-node.sh [--yes] [--force] [--skip-firewall] <node-name> <wireguard-ip> [koi-processor-path]
# Example: ./setup-node.sh --yes shawn-personal 10.100.0.3 /home/shawn/koi-processor
#
# Run on the peer's machine after WireGuard tunnel is verified.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# ============================================
# PARSE ARGS
# ============================================

NON_INTERACTIVE=false
FORCE_OVERWRITE=false
SKIP_FIREWALL=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes|-y)
            NON_INTERACTIVE=true
            FORCE_OVERWRITE=true
            shift
            ;;
        --force)
            FORCE_OVERWRITE=true
            shift
            ;;
        --skip-firewall)
            SKIP_FIREWALL=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [--yes] [--force] [--skip-firewall] <node-name> <wireguard-ip> [koi-processor-path]"
            echo "Example: $0 --yes shawn-personal 10.100.0.3 /home/shawn/koi-processor"
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
    echo "Usage: $0 [--yes] [--force] [--skip-firewall] <node-name> <wireguard-ip> [koi-processor-path]"
    echo "Example: $0 --yes shawn-personal 10.100.0.3 /home/shawn/koi-processor"
    exit 1
fi

NODE_NAME="$1"
WG_IP="$2"
KOI_PATH="${3:-$KOI_PROCESSOR_DIR}"

validate_peer_name "$NODE_NAME"

STATE_DIR="$HOME/.config/personal-koi"
KOI_STATE="$STATE_DIR/koi-state"
DB_USER="${USER:-$(whoami)}"

log_info "Setting up KOI-net node: $NODE_NAME"
log_info "WireGuard IP: $WG_IP"
log_info "KOI processor: $KOI_PATH"
log_info "Non-interactive: $NON_INTERACTIVE"
log_info "Force overwrite: $FORCE_OVERWRITE"
log_info "Skip firewall: $SKIP_FIREWALL"

# ============================================
# STEP 1: Verify WireGuard tunnel
# ============================================

log_info "Step 1: Verifying WireGuard tunnel..."

if ! ping -c 1 -W 3 10.100.0.1 &>/dev/null; then
    log_fatal "Cannot reach relay (10.100.0.1). Is WireGuard active?"
fi
log_info "Relay reachable at 10.100.0.1"

# ============================================
# STEP 2: Create directories
# ============================================

log_info "Step 2: Creating state directories..."

mkdir -p "$KOI_STATE"
mkdir -p "$STATE_DIR"
chmod 700 "$KOI_STATE"

# ============================================
# STEP 3: Generate personal.env from template
# ============================================

log_info "Step 3: Generating config/personal.env..."

TEMPLATE="$SCRIPT_DIR/personal-env.template"
TARGET="$KOI_PATH/config/personal.env"
WRITE_ENV=true

if [[ -f "$TARGET" ]]; then
    log_warn "personal.env already exists at $TARGET"
    if $FORCE_OVERWRITE; then
        cp "$TARGET" "${TARGET}.bak.$(date +%s)"
        log_info "Backed up existing config (force overwrite enabled)"
    else
        read -rp "Overwrite? [y/N]: " confirm
        if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
            log_info "Keeping existing personal.env"
            WRITE_ENV=false
        else
            cp "$TARGET" "${TARGET}.bak.$(date +%s)"
            log_info "Backed up existing config"
        fi
    fi
fi

if $WRITE_ENV && [[ -f "$TEMPLATE" ]]; then
    sed \
        -e "s|{{NODE_NAME}}|$NODE_NAME|g" \
        -e "s|{{WG_IP}}|$WG_IP|g" \
        -e "s|{{STATE_DIR}}|$KOI_STATE|g" \
        -e "s|{{DB_USER}}|$DB_USER|g" \
        -e "s|{{OBSIDIAN_VAULT_PATH}}|~/Documents/Notes|g" \
        -e "s|{{EMBEDDING_PROVIDER}}||g" \
        -e "s|{{EMBEDDING_MODEL}}||g" \
        -e "s|{{OPENAI_API_KEY}}||g" \
        "$TEMPLATE" > "$TARGET"
    log_info "Generated personal.env"
elif ! $WRITE_ENV; then
    log_info "Skipped personal.env generation (using existing file)"
else
    log_warn "Template not found at $TEMPLATE, skipping personal.env generation"
fi

# ============================================
# STEP 4: Verify config settings
# ============================================

log_info "Step 4: Verifying config settings..."

if [[ -f "$TARGET" ]]; then
    # Source the config to check values
    set -a
    source "$TARGET"
    set +a

    # Check non-localhost base URL
    if [[ "$KOI_BASE_URL" == *"localhost"* || "$KOI_BASE_URL" == *"127.0.0.1"* ]]; then
        log_fatal "KOI_BASE_URL must be the WireGuard IP, not localhost. Got: $KOI_BASE_URL"
    fi

    # Check policy settings
    if [[ "${KOI_NET_REQUIRE_APPROVED_EDGE_FOR_POLL:-}" != "true" ]]; then
        log_warn "KOI_NET_REQUIRE_APPROVED_EDGE_FOR_POLL should be 'true' for federation security"
    fi
    if [[ "${KOI_ENFORCE_SOURCE_KEY_RID_BINDING:-}" != "true" ]]; then
        log_warn "KOI_ENFORCE_SOURCE_KEY_RID_BINDING should be 'true' for federation security"
    fi

    log_info "Config verified: base URL=$KOI_BASE_URL"
fi

# ============================================
# STEP 5: Restrict API to WireGuard + loopback
# ============================================

log_info "Step 5: Restricting API to WireGuard + loopback..."

KOI_PORT="${KOI_API_PORT:-8351}"

if $SKIP_FIREWALL; then
    log_warn "Skipping firewall setup (--skip-firewall)"
elif [[ "$(uname)" == "Darwin" ]]; then
    # macOS: use pf (packet filter)
    PF_ANCHOR="/etc/pf.anchors/koi-net"
    PF_CONF="/etc/pf.conf"

    if [[ -f "$PF_ANCHOR" ]]; then
        log_info "pf anchor already exists, skipping"
    else
        log_info "Setting up macOS pf rules for port $KOI_PORT..."
        echo ""
        echo "  This requires sudo to modify /etc/pf.anchors/koi-net"
        echo "  Rules: allow port $KOI_PORT on lo0 and utun* only, block elsewhere"
        echo ""
        if $NON_INTERACTIVE; then
            confirm="y"
        else
            read -rp "  Proceed with sudo? [y/N]: " confirm
        fi
        if [[ "$confirm" == "y" || "$confirm" == "Y" ]]; then
            # Backup current state
            sudo pfctl -sa > "$STATE_DIR/pf-backup-$(date +%s).txt" 2>/dev/null || true

            # Create anchor rules
            sudo tee "$PF_ANCHOR" > /dev/null <<PF_RULES
# KOI-net: restrict port $KOI_PORT to WireGuard + loopback
pass in quick on lo0 proto tcp from any to any port $KOI_PORT
pass in quick on utun0 proto tcp from any to any port $KOI_PORT
pass in quick on utun1 proto tcp from any to any port $KOI_PORT
pass in quick on utun2 proto tcp from any to any port $KOI_PORT
pass in quick on utun3 proto tcp from any to any port $KOI_PORT
block in quick proto tcp from any to any port $KOI_PORT
PF_RULES

            # Add anchor reference if not present
            if ! grep -q 'anchor "koi-net"' "$PF_CONF" 2>/dev/null; then
                sudo cp "$PF_CONF" "${PF_CONF}.bak.$(date +%s)"
                echo 'anchor "koi-net"' | sudo tee -a "$PF_CONF" > /dev/null
                echo 'load anchor "koi-net" from "/etc/pf.anchors/koi-net"' | sudo tee -a "$PF_CONF" > /dev/null
            fi

            # Load rules
            sudo pfctl -a koi-net -f "$PF_ANCHOR" 2>/dev/null || true
            sudo pfctl -e 2>/dev/null || true

            log_info "pf rules applied"
        else
            log_warn "Skipped firewall setup. Manual steps:"
            echo "  sudo tee /etc/pf.anchors/koi-net <<'EOF'"
            echo "  pass in quick on lo0 proto tcp from any to any port $KOI_PORT"
            echo "  pass in quick on utun0 proto tcp from any to any port $KOI_PORT"
            echo "  block in quick proto tcp from any to any port $KOI_PORT"
            echo "  EOF"
        fi
    fi
else
    # Linux: use iptables
    if sudo iptables -C INPUT -p tcp --dport "$KOI_PORT" -i lo -j ACCEPT 2>/dev/null; then
        log_info "iptables rules already exist, skipping"
    else
        log_info "Setting up Linux iptables rules for port $KOI_PORT..."
        if $NON_INTERACTIVE; then
            confirm="y"
        else
            read -rp "  Proceed with sudo? [y/N]: " confirm
        fi
        if [[ "$confirm" == "y" || "$confirm" == "Y" ]]; then
            # Backup
            sudo iptables-save > "$STATE_DIR/iptables-backup-$(date +%s).txt"

            sudo iptables -A INPUT -p tcp --dport "$KOI_PORT" -i lo -j ACCEPT
            sudo iptables -A INPUT -p tcp --dport "$KOI_PORT" -i wg-koi -j ACCEPT
            sudo iptables -A INPUT -p tcp --dport "$KOI_PORT" -j DROP

            # Persist
            if command -v iptables-save &>/dev/null; then
                sudo sh -c 'iptables-save > /etc/iptables/rules.v4' 2>/dev/null || \
                sudo sh -c 'iptables-save > /etc/iptables.rules' 2>/dev/null || true
            fi

            log_info "iptables rules applied"
        else
            log_warn "Skipped firewall setup. Run manually:"
            echo "  sudo iptables -A INPUT -p tcp --dport $KOI_PORT -i lo -j ACCEPT"
            echo "  sudo iptables -A INPUT -p tcp --dport $KOI_PORT -i wg-koi -j ACCEPT"
            echo "  sudo iptables -A INPUT -p tcp --dport $KOI_PORT -j DROP"
        fi
    fi
fi

# Verify firewall (non-blocking)
log_info "Verifying firewall..."
# We can only verify localhost works if the service is running
# LAN check would need to know the LAN IP
log_info "Firewall verification will be checked after service starts"

# ============================================
# STEP 6: Run pending migrations
# ============================================

log_info "Step 6: Running pending migrations..."

POSTGRES_URL="${POSTGRES_URL:-postgresql:///personal_koi}"
MIGRATIONS_DIR="$KOI_PATH/migrations"

# Federation setup defaults to migration 040+ unless explicitly overridden.
# This avoids legacy non-federation migrations that rely on historical tables.
if [[ -z "${KOI_MIGRATION_MIN_NUM:-}" ]]; then
    export KOI_MIGRATION_MIN_NUM=40
    log_info "Using federation migration baseline: KOI_MIGRATION_MIN_NUM=$KOI_MIGRATION_MIN_NUM"
fi

if [[ -d "$MIGRATIONS_DIR" ]]; then
    require_cmd python3
    # Prefer repo venv for migration execution if available.
    if [[ -f "$KOI_PATH/venv/bin/activate" ]]; then
        # shellcheck source=/dev/null
        source "$KOI_PATH/venv/bin/activate"
    fi
    ensure_python_module "psycopg2" "psycopg2-binary"
    run_migrations "$POSTGRES_URL" "$MIGRATIONS_DIR"
else
    log_warn "Migrations directory not found: $MIGRATIONS_DIR"
fi

# ============================================
# STEP 7: Install Python deps
# ============================================

log_info "Step 7: Checking Python dependencies..."

cd "$KOI_PATH"
if [[ -f venv/bin/activate ]]; then
    source venv/bin/activate
fi

pip install -q cryptography 2>/dev/null || log_warn "Could not install cryptography package"

# ============================================
# STEP 8: Generate start.sh from template
# ============================================

log_info "Step 8: Generating start.sh..."

START_TEMPLATE="$SCRIPT_DIR/start-sh.template"
START_TARGET="$STATE_DIR/start.sh"

if [[ -f "$START_TEMPLATE" ]]; then
    sed \
        -e "s|{{NODE_NAME}}|$NODE_NAME|g" \
        -e "s|{{KOI_PROCESSOR_PATH}}|$KOI_PATH|g" \
        -e "s|{{DB_USER}}|$DB_USER|g" \
        -e "s|{{STATE_DIR}}|$KOI_STATE|g" \
        "$START_TEMPLATE" > "$START_TARGET"
    chmod +x "$START_TARGET"
    log_info "Generated start.sh at $START_TARGET"
else
    log_warn "Template not found at $START_TEMPLATE"
fi

# ============================================
# STEP 9: Start service and wait for health
# ============================================

log_info "Step 9: Starting KOI-net service..."

if [[ -f "$START_TARGET" ]]; then
    "$START_TARGET" &
    KOI_PID=$!

    # Wait for health
    log_info "Waiting for service to become healthy..."
    for i in $(seq 1 30); do
        if curl -sf "http://127.0.0.1:${KOI_PORT}/health" &>/dev/null; then
            log_info "Service healthy after ${i}s"
            break
        fi
        if ! kill -0 "$KOI_PID" 2>/dev/null; then
            log_fatal "Service process exited unexpectedly"
        fi
        sleep 1
    done

    if ! curl -sf "http://127.0.0.1:${KOI_PORT}/health" &>/dev/null; then
        log_fatal "Service did not become healthy within 30s"
    fi
else
    log_warn "No start.sh found, skipping service start"
    log_warn "Start manually: cd $KOI_PATH && source config/personal.env && uvicorn api.personal_ingest_api:app --host 0.0.0.0 --port 8351"
fi

# ============================================
# STEP 10: Verify KOI key continuity
# ============================================

log_info "Step 10: Verifying KOI key continuity..."

KEY_INFO=$(KOI_STATE_DIR="$KOI_STATE" python3 -c "
import sys, os, json
sys.path.insert(0, '$KOI_PATH')
os.environ['KOI_STATE_DIR'] = '$KOI_STATE'

from api.node_identity import load_or_create_identity, get_public_key_der_b64
from base64 import b64encode, b64decode
import hashlib

private_key, profile = load_or_create_identity('$NODE_NAME')
koi_pubkey = get_public_key_der_b64(private_key)

# Compute fingerprint: SHA256 of the actual DER bytes (not the base64 text)
der_bytes = b64decode(koi_pubkey)
fp_hash = hashlib.sha256(der_bytes).digest()
fingerprint = 'SHA256:' + b64encode(fp_hash).decode().rstrip('=')

print(f'node_rid={profile.node_rid}')
print(f'koi_public_key={koi_pubkey}')
print(f'fingerprint={fingerprint}')
")

LOADED_RID=$(echo "$KEY_INFO" | grep '^node_rid=' | cut -d= -f2-)
LOADED_PUBKEY=$(echo "$KEY_INFO" | grep '^koi_public_key=' | cut -d= -f2-)
LOADED_FP=$(echo "$KEY_INFO" | grep '^fingerprint=' | cut -d= -f2-)

# Check against peer registry if available locally
if [[ -f "$PEER_REGISTRY" ]]; then
    REGISTRY_RID=$(peer_registry_lookup "$NODE_NAME" 2>/dev/null | python3 -c "
import json, sys
try:
    p = json.load(sys.stdin)
    print(p.get('node_rid', ''))
except:
    print('')
" 2>/dev/null || echo "")

    if [[ -n "$REGISTRY_RID" && "$REGISTRY_RID" != "$LOADED_RID" ]]; then
        log_fatal "Key mismatch! Registry RID: $REGISTRY_RID, Loaded RID: $LOADED_RID"
    fi
fi

# ============================================
# STEP 11: Print node identity
# ============================================

echo ""
echo "==================================="
echo "  KOI-net Node Setup Complete"
echo "==================================="
echo ""
echo "  Node name:       $NODE_NAME"
echo "  Node RID:        $LOADED_RID"
echo "  Key fingerprint: $LOADED_FP"
echo "  WireGuard IP:    $WG_IP"
echo "  KOI API:         http://$WG_IP:$KOI_PORT"
echo "  Start script:    $START_TARGET"
echo ""
echo "  Next step: Run connect-peers.sh to establish federation"
echo "    ./connect-peers.sh http://10.100.0.2:8351 darren"
echo "==================================="
