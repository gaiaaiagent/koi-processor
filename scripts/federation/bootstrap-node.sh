#!/usr/bin/env bash
# bootstrap-node.sh — Blank-host bootstrap for KOI-net peer nodes
#
# Usage:
#   ./bootstrap-node.sh [--dry-run] [--yes] [--skip-join-request] \
#     [--repo-url <url>] [--ref <git-ref>] <node-name> <wireguard-ip> [koi-processor-path]
#
# Example:
#   ./bootstrap-node.sh --yes nuc-personal 10.100.0.22 ~/projects/RegenAI/koi-processor
#
# What it does (idempotent):
#   1) Installs OS prerequisites (WireGuard, Python venv/pip, PostgreSQL, git, curl, jq)
#   2) Ensures local PostgreSQL role/database (personal_koi)
#   3) Clones/updates koi-processor and checks out requested ref
#   4) Creates venv and installs Python dependencies
#   5) Generates KOI/WireGuard join request (unless --skip-join-request)
#   6) Runs readiness validation checks

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# ============================================
# ARGUMENTS
# ============================================

YES=false
SKIP_JOIN=false
SKIP_MCP=false
INVITE_TOKEN=""
REPO_URL="https://github.com/gaiaaiagent/koi-processor.git"
REPO_REF="regen-prod"

usage() {
    cat <<USAGE
Usage:
  $0 [--dry-run] [--yes] [--skip-join-request] [--repo-url <url>] [--ref <git-ref>] <node-name> <wireguard-ip> [koi-processor-path]
  $0 [--dry-run] [--yes] [--skip-mcp] --invite <token> [--repo-url <url>] [--ref <git-ref>] [koi-processor-path]

Examples:
  $0 --yes nuc-personal 10.100.0.22 ~/projects/RegenAI/koi-processor
  $0 --dry-run --ref main shawn-personal 10.100.0.3
  $0 --invite "KOI-INVITE-1:..." --yes
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --yes|-y)
            YES=true
            shift
            ;;
        --skip-join-request)
            SKIP_JOIN=true
            shift
            ;;
        --skip-mcp)
            SKIP_MCP=true
            shift
            ;;
        --invite)
            INVITE_TOKEN="${2:-}"
            [[ -z "$INVITE_TOKEN" ]] && log_fatal "--invite requires a token argument"
            shift 2
            ;;
        --repo-url)
            REPO_URL="${2:-}"
            shift 2
            ;;
        --ref)
            REPO_REF="${2:-}"
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

if [[ -n "$INVITE_TOKEN" ]]; then
    # Invite mode: extract NODE_NAME and WG_IP from token
    INVITE_PAYLOAD=$(decode_invite_token "$INVITE_TOKEN") || \
        log_fatal "Failed to decode invite token"
    NODE_NAME=$(echo "$INVITE_PAYLOAD" | python3 -c "import sys,json; print(json.load(sys.stdin)['peer_name'])")
    INVITE_PEER_NUMBER=$(echo "$INVITE_PAYLOAD" | python3 -c "import sys,json; print(json.load(sys.stdin)['peer_number'])")
    WG_IP="10.100.0.${INVITE_PEER_NUMBER}"
    INVITE_RELAY_PUBKEY=$(echo "$INVITE_PAYLOAD" | python3 -c "import sys,json; print(json.load(sys.stdin)['relay_pubkey'])")
    INVITE_RELAY_ENDPOINT=$(echo "$INVITE_PAYLOAD" | python3 -c "import sys,json; print(json.load(sys.stdin)['relay_endpoint'])")
    INVITE_ADMIN_BASE_URL=$(echo "$INVITE_PAYLOAD" | python3 -c "import sys,json; print(json.load(sys.stdin)['admin_base_url'])")
    INVITE_ADMIN_WG_IP=$(echo "$INVITE_PAYLOAD" | python3 -c "import sys,json; print(json.load(sys.stdin)['admin_wg_ip'])")
    INVITE_VAULT_SYNC_FOLDER=$(echo "$INVITE_PAYLOAD" | python3 -c "import sys,json; print(json.load(sys.stdin).get('vault_sync_folder','Shared'))")
    KOI_PATH="${1:-$HOME/projects/RegenAI/koi-processor}"
else
    if [[ $# -lt 2 ]]; then
        usage
        exit 1
    fi
    NODE_NAME="$1"
    WG_IP="$2"
    KOI_PATH="${3:-$HOME/projects/RegenAI/koi-processor}"
fi

STATE_DIR="${STATE_DIR:-$HOME/.config/personal-koi}"
DB_USER="${USER:-$(whoami)}"

# --dry-run is incompatible with --invite (too many interactive/stateful steps)
if [[ -n "$INVITE_TOKEN" ]] && $DRY_RUN; then
    log_fatal "--dry-run is not supported with --invite (invite flow requires live WG, handshakes, and SAS verification)"
fi

validate_peer_name "$NODE_NAME"

if [[ ! "$WG_IP" =~ ^10\.100\.0\.([0-9]{1,3})$ ]]; then
    log_fatal "Invalid WireGuard IP '$WG_IP' (expected 10.100.0.X)"
fi
WG_OCTET="${BASH_REMATCH[1]}"
if (( WG_OCTET < 2 || WG_OCTET > 254 )); then
    log_fatal "Invalid WireGuard IP '$WG_IP': last octet must be 2-254"
fi

log_info "Bootstrap target node: $NODE_NAME"
log_info "WireGuard IP: $WG_IP"
log_info "KOI path: $KOI_PATH"
log_info "Repo: $REPO_URL @ $REPO_REF"
log_info "Dry run: $DRY_RUN"
log_info "Non-interactive: $YES"
if [[ -n "$INVITE_TOKEN" ]]; then
    log_info "Mode: invite-driven bootstrap"
fi

# ============================================
# HELPERS
# ============================================

confirm_or_exit() {
    local prompt="$1"
    if $YES; then
        return 0
    fi
    read -rp "$prompt [y/N]: " answer
    [[ "$answer" == "y" || "$answer" == "Y" ]]
}

run_or_print() {
    if $DRY_RUN; then
        echo "[DRY-RUN] $*"
    else
        eval "$@"
    fi
}

install_linux_prereqs() {
    if command -v wg >/dev/null 2>&1 \
       && command -v psql >/dev/null 2>&1 \
       && command -v jq >/dev/null 2>&1 \
       && command -v git >/dev/null 2>&1 \
       && command -v curl >/dev/null 2>&1 \
       && python3 -m venv --help >/dev/null 2>&1 \
       && python3 -m pip --version >/dev/null 2>&1; then
        log_info "Linux prerequisites already present; skipping install"
        return 0
    fi

    log_info "Installing Linux prerequisites..."

    if command -v pacman >/dev/null 2>&1; then
        # Arch / CachyOS / Manjaro
        local packages=(wireguard-tools python python-pip postgresql git curl jq)
        if ! confirm_or_exit "Install missing packages with sudo pacman?"; then
            log_fatal "Aborted by user (missing prerequisites)"
        fi
        run_or_print "sudo pacman -S --needed --noconfirm ${packages[*]}"
    elif command -v apt-get >/dev/null 2>&1; then
        # Debian / Ubuntu
        local packages=(wireguard-tools python3-venv python3-pip postgresql postgresql-contrib git curl jq)
        if ! confirm_or_exit "Install missing packages with sudo apt-get?"; then
            log_fatal "Aborted by user (missing prerequisites)"
        fi
        run_or_print "sudo apt-get update -qq"
        run_or_print "sudo apt-get install -y -qq ${packages[*]}"
    elif command -v dnf >/dev/null 2>&1; then
        # Fedora / RHEL
        local packages=(wireguard-tools python3 python3-pip postgresql-server postgresql git curl jq)
        if ! confirm_or_exit "Install missing packages with sudo dnf?"; then
            log_fatal "Aborted by user (missing prerequisites)"
        fi
        run_or_print "sudo dnf install -y -q ${packages[*]}"
    else
        log_fatal "Unsupported package manager. Install manually: wireguard-tools python3 python3-pip postgresql git curl jq"
    fi
}

install_macos_prereqs() {
    require_cmd brew
    local packages=(wireguard-tools postgresql jq)
    log_info "Installing macOS prerequisites..."
    if ! confirm_or_exit "Install packages with brew?"; then
        log_fatal "Aborted by user"
    fi
    run_or_print "brew install ${packages[*]}"
}

ensure_postgres() {
    log_info "Ensuring PostgreSQL role/db exist..."
    if [[ "$(uname)" == "Linux" ]]; then
        # Fast path: already usable with local socket auth.
        if psql -d personal_koi -Atc "SELECT 1" >/dev/null 2>&1; then
            log_info "PostgreSQL already ready (personal_koi reachable via local socket)"
            return 0
        fi
        # Arch/CachyOS: PostgreSQL requires manual initdb before first start
        local pgdata="/var/lib/postgres/data"
        if command -v pacman >/dev/null 2>&1 && [[ ! -f "$pgdata/PG_VERSION" ]]; then
            log_info "Arch Linux detected — initializing PostgreSQL data directory..."
            run_or_print "sudo -u postgres initdb -D '$pgdata'"
        fi
        run_or_print "sudo systemctl enable --now postgresql"
        run_or_print "sudo -u postgres psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'\" | grep -q 1 || sudo -u postgres createuser -s '${DB_USER}'"
        run_or_print "sudo -u postgres psql -tAc \"SELECT 1 FROM pg_database WHERE datname='personal_koi'\" | grep -q 1 || sudo -u postgres createdb -O '${DB_USER}' personal_koi"
    else
        run_or_print "brew services start postgresql >/dev/null 2>&1 || true"
        run_or_print "createuser -s '${DB_USER}' >/dev/null 2>&1 || true"
        run_or_print "createdb personal_koi >/dev/null 2>&1 || true"
    fi
}

ensure_repo() {
    log_info "Ensuring koi-processor repository..."
    if [[ -d "$KOI_PATH/.git" ]]; then
        log_info "Repository exists at $KOI_PATH"
    else
        run_or_print "mkdir -p \"$(dirname "$KOI_PATH")\""
        run_or_print "git clone \"$REPO_URL\" \"$KOI_PATH\""
    fi

    run_or_print "git -C \"$KOI_PATH\" fetch origin --prune"
    run_or_print "git -C \"$KOI_PATH\" checkout \"$REPO_REF\""
    run_or_print "git -C \"$KOI_PATH\" pull --ff-only origin \"$REPO_REF\" || true"
}

setup_python_env() {
    log_info "Setting up Python virtual environment..."
    if [[ -d "$KOI_PATH/venv" && ! -f "$KOI_PATH/venv/bin/activate" ]]; then
        log_warn "Detected broken virtualenv at $KOI_PATH/venv (missing bin/activate), recreating..."
        run_or_print "rm -rf \"$KOI_PATH/venv\""
    fi
    if [[ -x "$KOI_PATH/venv/bin/python" ]]; then
        local venv_py_version
        venv_py_version="$("$KOI_PATH/venv/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")"
        local venv_major="${venv_py_version%%.*}"
        local venv_minor="${venv_py_version##*.}"
        if (( venv_major < 3 || (venv_major == 3 && venv_minor < 11) )); then
            log_warn "Existing virtualenv uses Python ${venv_py_version}; recreating with Python 3.11+"
            run_or_print "rm -rf \"$KOI_PATH/venv\""
        fi
    fi
    if [[ ! -f "$KOI_PATH/venv/bin/activate" ]]; then
        run_or_print "python3 -m venv \"$KOI_PATH/venv\""
    fi
    # Ensure pip exists even on minimal python installs.
    run_or_print "bash -lc '\"$KOI_PATH/venv/bin/python\" -m ensurepip --upgrade >/dev/null 2>&1 || true'"
    run_or_print "bash -lc 'source \"$KOI_PATH/venv/bin/activate\" && pip install -q --upgrade pip'"
    if $DRY_RUN; then
        run_or_print "bash -lc 'source \"$KOI_PATH/venv/bin/activate\" && pip install -q -r \"$KOI_PATH/requirements.txt\"'"
        return 0
    fi

    if ! bash -lc "source \"$KOI_PATH/venv/bin/activate\" && pip install -q -r \"$KOI_PATH/requirements.txt\""; then
        local fallback_req="$KOI_PATH/scripts/federation/requirements-bootstrap.txt"
        if [[ -f "$fallback_req" ]]; then
            log_warn "Full requirements install failed. Falling back to federation bootstrap requirements."
            bash -lc "source \"$KOI_PATH/venv/bin/activate\" && pip install -q -r \"$fallback_req\""
        else
            log_fatal "requirements install failed and fallback file missing: $fallback_req"
        fi
    fi
}

generate_join_request() {
    local join_script="$KOI_PATH/scripts/federation/join-request.sh"
    if [[ ! -x "$join_script" ]]; then
        log_fatal "join-request.sh not found at $join_script"
    fi
    run_or_print "bash \"$join_script\" \"$NODE_NAME\" \"$STATE_DIR\""
}

run_validation() {
    local validate_script="$KOI_PATH/scripts/federation/validate-node.sh"
    if [[ -x "$validate_script" ]]; then
        run_or_print "bash \"$validate_script\" --expect-wg-ip \"$WG_IP\" --koi-path \"$KOI_PATH\""
    else
        log_warn "Validation script not found at $validate_script"
    fi
}

ensure_node_runtime() {
    # Check if node >= 18 is available
    if command -v node >/dev/null 2>&1; then
        local node_major
        node_major=$(node -v | sed 's/v//' | cut -d. -f1)
        if (( node_major >= 18 )); then
            return 0
        fi
        log_warn "Node.js $(node -v) found but >= 18 required for MCP server"
    fi

    case "$(uname -s)" in
        Linux)
            if ! confirm_or_exit "Install Node.js 20.x for MCP server?"; then
                log_warn "Skipping Node.js install — MCP server won't be set up"
                return 1
            fi
            if ! curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - 2>/dev/null; then
                log_warn "Node.js repo setup failed — skipping MCP server"
                return 1
            fi
            if ! sudo apt-get install -y -qq nodejs 2>/dev/null; then
                log_warn "Node.js install failed — skipping MCP server"
                return 1
            fi
            ;;
        Darwin)
            if command -v brew >/dev/null 2>&1; then
                if ! confirm_or_exit "Install Node.js via brew for MCP server?"; then
                    log_warn "Skipping Node.js install — MCP server won't be set up"
                    return 1
                fi
                if ! brew install node 2>/dev/null; then
                    log_warn "Node.js install failed — skipping MCP server"
                    return 1
                fi
            else
                log_warn "brew not found — install Node.js >= 18 manually for MCP server"
                return 1
            fi
            ;;
    esac
}

setup_mcp_server() {
    local koi_path="$1"
    local vault_path="$2"
    local mcp_path="${MCP_PATH:-$HOME/projects/personal-koi-mcp}"
    local mcp_repo="https://github.com/DarrenZal/personal-koi-mcp.git"

    (
        set +e  # Non-fatal — MCP setup failure should not abort onboarding
        trap 'log_warn "MCP setup failed — see manual steps below"; exit 0' ERR

        log_info "Setting up personal-koi MCP server..."

        # Clone or update
        if [[ -d "$mcp_path/.git" ]]; then
            log_info "personal-koi-mcp exists, pulling latest..."
            git -C "$mcp_path" pull --ff-only 2>/dev/null || log_warn "git pull failed, using existing"
        else
            mkdir -p "$(dirname "$mcp_path")"
            git clone "$mcp_repo" "$mcp_path" || { log_warn "git clone failed"; exit 1; }
        fi

        # Install deps + build
        cd "$mcp_path"
        npm install --no-audit --no-fund 2>&1 | tail -3
        npm run build 2>&1 | tail -3

        if [[ ! -f "$mcp_path/dist/index.js" ]]; then
            log_warn "MCP build failed — dist/index.js not found"
            exit 1
        fi

        # Upsert KOI_API_ENDPOINT in .env (create if missing, update if present)
        if [[ ! -f "$mcp_path/.env" ]]; then
            cat > "$mcp_path/.env" <<EOF
KOI_API_ENDPOINT=http://localhost:8351
EOF
        elif grep -q '^KOI_API_ENDPOINT=' "$mcp_path/.env"; then
            sed -i.bak 's|^KOI_API_ENDPOINT=.*|KOI_API_ENDPOINT=http://localhost:8351|' "$mcp_path/.env"
            rm -f "$mcp_path/.env.bak"
            log_info ".env exists — updated KOI_API_ENDPOINT to localhost:8351"
        else
            echo "KOI_API_ENDPOINT=http://localhost:8351" >> "$mcp_path/.env"
            log_info ".env exists — appended KOI_API_ENDPOINT"
        fi

        # Register with Claude Code if CLI available
        local mcp_entry="$mcp_path/dist/index.js"
        if command -v claude >/dev/null 2>&1; then
            log_info "Registering MCP server with Claude Code..."
            # Remove stale entries across all scopes, then add fresh in user scope
            for scope in user local project; do
                claude mcp remove --scope "$scope" personal-koi 2>/dev/null || true
            done
            claude mcp add --scope user \
                -e OBSIDIAN_VAULT_PATH="$vault_path" \
                -e KOI_API_ENDPOINT="http://localhost:8351" \
                personal-koi -- node "$mcp_entry" \
                2>/dev/null && log_info "MCP registered via 'claude mcp add --scope user'" \
                || log_warn "claude mcp add failed — paste config manually"
            # Verify registration (scope-aware)
            if claude mcp get --scope user personal-koi 2>/dev/null | grep -q personal-koi; then
                log_info "MCP server 'personal-koi' confirmed (user scope)"
                log_info "Note: restart Claude Code to pick up the new MCP server"
            else
                log_warn "MCP server not confirmed — may need manual registration (see config below)"
            fi
        fi

        log_info "MCP server built at $mcp_path"
    ) || true  # Ensure non-fatal

    # Always print config (even if setup failed — user can retry manually)
    local mcp_path="${MCP_PATH:-$HOME/projects/personal-koi-mcp}"
    local mcp_entry="$mcp_path/dist/index.js"
    local vault_path="$2"
    cat <<MCPCONFIG

===================================
  Claude Code MCP Configuration
===================================

To use your KOI knowledge base in Claude Code, ensure this MCP server is registered.

Option A (CLI):
  claude mcp add --scope user -e OBSIDIAN_VAULT_PATH="$vault_path" -e KOI_API_ENDPOINT="http://localhost:8351" personal-koi -- node "$mcp_entry"

Option B (JSON — add to ~/.mcp.json):
  {
    "mcpServers": {
      "personal-koi": {
        "command": "node",
        "args": ["$mcp_entry"],
        "env": {
          "OBSIDIAN_VAULT_PATH": "$vault_path",
          "KOI_API_ENDPOINT": "http://localhost:8351"
        }
      }
    }
  }

If MCP setup failed, retry manually:
  cd $mcp_path && npm install && npm run build
  Then register with one of the options above.

===================================
MCPCONFIG
}

# ============================================
# EXECUTION
# ============================================

log_info "Step 1: Installing prerequisites..."
case "$(uname)" in
    Linux) install_linux_prereqs ;;
    Darwin) install_macos_prereqs ;;
    *)
        log_fatal "Unsupported OS: $(uname)"
        ;;
esac

require_python_3_11

log_info "Step 2: PostgreSQL bootstrap..."
ensure_postgres

log_info "Step 3: Repository bootstrap..."
ensure_repo

log_info "Step 4: Python environment bootstrap..."
setup_python_env

if [[ -n "$INVITE_TOKEN" ]]; then
    # ============================================
    # INVITE-DRIVEN FLOW (Steps 5-13)
    # ============================================

    # Activate venv for Python imports
    if [[ -f "$KOI_PATH/venv/bin/activate" ]]; then
        source "$KOI_PATH/venv/bin/activate"
    fi

    KOI_STATE="$STATE_DIR/koi-state"
    WG_DIR="$STATE_DIR/wireguard"
    mkdir -p "$KOI_STATE" "$WG_DIR"
    chmod 700 "$KOI_STATE" "$WG_DIR"

    # --- Step 6: Generate WG keypair ---
    log_info "Step 6: Generating WireGuard keypair..."

    WG_PRIVKEY_FILE="$WG_DIR/private.key"
    WG_PUBKEY_FILE="$WG_DIR/public.key"

    if [[ -f "$WG_PRIVKEY_FILE" ]]; then
        log_info "Using existing WireGuard keypair"
        WG_PUBKEY=$(cat "$WG_PUBKEY_FILE")
    else
        umask 077
        wg genkey > "$WG_PRIVKEY_FILE"
        wg pubkey < "$WG_PRIVKEY_FILE" > "$WG_PUBKEY_FILE"
        chmod 600 "$WG_PRIVKEY_FILE"
        WG_PUBKEY=$(cat "$WG_PUBKEY_FILE")
        log_info "Generated new WireGuard keypair"
    fi

    # --- Step 7: Generate KOI node identity ---
    log_info "Step 7: Generating KOI node identity..."

    require_cmd python3
    if ! python3 -c "import cryptography" 2>/dev/null; then
        pip install -q cryptography 2>/dev/null || \
            log_fatal "Cannot install 'cryptography'. Run: pip install cryptography"
    fi

    KOI_IDENTITY=$(KOI_STATE_DIR="$KOI_STATE" python3 -c "
import sys, os
sys.path.insert(0, '$KOI_PATH')
os.environ['KOI_STATE_DIR'] = '$KOI_STATE'
from api.node_identity import load_or_create_identity, get_public_key_der_b64
# load_or_create_identity may return (private_key, profile) or
# (private_key, profile, encryption_private_key) depending on version.
identity = load_or_create_identity('$NODE_NAME')
if not isinstance(identity, tuple) or len(identity) < 2:
    raise RuntimeError(f'Unexpected identity return shape: {type(identity)}')
private_key, profile = identity[0], identity[1]
koi_pubkey = get_public_key_der_b64(private_key)
print(f'node_rid={profile.node_rid}')
print(f'koi_public_key={koi_pubkey}')
")
    LOCAL_NODE_RID=$(echo "$KOI_IDENTITY" | grep '^node_rid=' | cut -d= -f2-)
    LOCAL_KOI_PUBKEY=$(echo "$KOI_IDENTITY" | grep '^koi_public_key=' | cut -d= -f2-)
    log_info "Node RID: $LOCAL_NODE_RID"

    # --- Step 8: Auto-generate wg-koi.conf + activate WireGuard ---
    log_info "Step 8: Configuring WireGuard..."

    if [[ "$(uname)" != "Linux" ]]; then
        log_fatal "Invite flow only supports Linux peers. Use manual flow for macOS."
    fi

    WG_PRIVKEY=$(cat "$WG_PRIVKEY_FILE")

    cat > /tmp/wg-koi.conf.tmp <<WGCONF
[Interface]
Address = ${WG_IP}/24
PrivateKey = ${WG_PRIVKEY}

[Peer]
PublicKey = ${INVITE_RELAY_PUBKEY}
Endpoint = ${INVITE_RELAY_ENDPOINT}
AllowedIPs = 10.100.0.0/24
PersistentKeepalive = 25
WGCONF

    run_or_print "sudo cp /tmp/wg-koi.conf.tmp /etc/wireguard/wg-koi.conf"
    run_or_print "sudo chmod 600 /etc/wireguard/wg-koi.conf"
    rm -f /tmp/wg-koi.conf.tmp

    # Activate or sync WG interface
    if wg show wg-koi &>/dev/null; then
        log_info "WG interface exists, syncing config..."
        run_or_print "sudo bash -c 'wg syncconf wg-koi <(wg-quick strip wg-koi)'"
    else
        run_or_print "sudo wg-quick up wg-koi"
    fi
    log_info "WireGuard interface activated"

    # --- Step 9: Print WG pubkey + wait for admin approval ---
    echo ""
    echo "==================================="
    echo "  Your WireGuard public key:"
    echo "  $WG_PUBKEY"
    echo ""
    echo "  Send to admin. Admin runs:"
    echo "    ./approve-peer.sh --pubkey-only $WG_PUBKEY $INVITE_PEER_NUMBER"
    echo "==================================="
    echo ""

    log_info "Waiting for admin to approve WG key (pinging relay 10.100.0.1)..."
    WAIT_START=$(date +%s)
    WAIT_TIMEOUT=600  # 10 minutes
    while ! ping -c 1 -W 2 10.100.0.1 &>/dev/null; do
        ELAPSED=$(( $(date +%s) - WAIT_START ))
        if (( ELAPSED > WAIT_TIMEOUT )); then
            log_fatal "Timeout (${WAIT_TIMEOUT}s) waiting for relay. Admin must run approve-peer.sh first."
        fi
        echo -ne "\r  Waiting for admin to approve WG key... (${ELAPSED}s)"
        sleep 5
    done
    echo ""
    log_info "Relay reachable! WG tunnel is up."

    # --- Step 10: Run setup-node.sh logic ---
    log_info "Step 10: Running node setup (env, migrations, firewall, service)..."

    SETUP_SCRIPT="$KOI_PATH/scripts/federation/setup-node.sh"
    if [[ -x "$SETUP_SCRIPT" ]]; then
        bash "$SETUP_SCRIPT" --yes "$NODE_NAME" "$WG_IP" "$KOI_PATH"
    else
        log_fatal "setup-node.sh not found at $SETUP_SCRIPT"
    fi

    # --- Step 10b: Set up MCP server for Claude Code ---
    if ! $SKIP_MCP; then
        VAULT_PATH="${VAULT_PATH:-$HOME/Documents/Notes}"
        if ensure_node_runtime; then
            setup_mcp_server "$KOI_PATH" "$VAULT_PATH"
        else
            log_warn "Skipping MCP setup (no Node.js). Install Node >= 18 and run manually:"
            log_warn "  cd ~/projects/personal-koi-mcp && npm install && npm run build"
        fi
    fi

    KOI_PORT="${KOI_API_PORT:-8351}"

    # Wait for local health
    log_info "Waiting for local KOI-net service..."
    for i in $(seq 1 30); do
        if curl -sf "http://127.0.0.1:${KOI_PORT}/health" &>/dev/null; then
            log_info "Local service healthy"
            break
        fi
        sleep 1
    done
    if ! curl -sf "http://127.0.0.1:${KOI_PORT}/health" &>/dev/null; then
        log_fatal "Local KOI-net service did not become healthy"
    fi

    # --- Step 11: Handshake with defer_approval ---
    log_info "Step 11: Initiating handshake with admin (defer_approval=true)..."

    # Get admin's profile
    ADMIN_HEALTH=$(curl -sf "${INVITE_ADMIN_BASE_URL}/koi-net/health") || \
        log_fatal "Cannot reach admin at ${INVITE_ADMIN_BASE_URL}/koi-net/health"
    ADMIN_PROFILE=$(echo "$ADMIN_HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d.get('node', d.get('profile', {}))))")
    ADMIN_NODE_RID=$(echo "$ADMIN_PROFILE" | python3 -c "import sys,json; print(json.load(sys.stdin)['node_rid'])")
    ADMIN_KOI_PUBKEY=$(echo "$ADMIN_PROFILE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('public_key',''))")

    # Get local profile
    LOCAL_HEALTH=$(curl -sf "http://127.0.0.1:${KOI_PORT}/koi-net/health")
    LOCAL_PROFILE=$(echo "$LOCAL_HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d.get('node', d.get('profile', {}))))")

    # Handshake: local → admin (with defer_approval)
    log_info "Sending handshake to admin..."
    HANDSHAKE_PAYLOAD=$(python3 -c "
import json
profile = json.loads('$LOCAL_PROFILE')
payload = {'type': 'handshake', 'profile': profile, 'defer_approval': True}
print(json.dumps(payload))
")

    ADMIN_RESPONSE_CODE=$(curl -s -o /tmp/admin-handshake-response.json -w "%{http_code}" \
        -X POST "${INVITE_ADMIN_BASE_URL}/koi-net/handshake" \
        -H "Content-Type: application/json" \
        -d "$HANDSHAKE_PAYLOAD")

    if [[ "$ADMIN_RESPONSE_CODE" == "400" ]]; then
        log_fatal "ERROR: Admin node does not support deferred edge approval. Admin must upgrade koi-server and restart before using invite flow."
    fi
    if [[ "$ADMIN_RESPONSE_CODE" != "200" ]]; then
        log_error "Admin handshake returned HTTP $ADMIN_RESPONSE_CODE"
        cat /tmp/admin-handshake-response.json 2>/dev/null
        # Retry with backoff
        for retry in 1 2 3; do
            sleep $((retry * 5))
            log_info "Retry $retry/3..."
            ADMIN_RESPONSE_CODE=$(curl -s -o /tmp/admin-handshake-response.json -w "%{http_code}" \
                -X POST "${INVITE_ADMIN_BASE_URL}/koi-net/handshake" \
                -H "Content-Type: application/json" \
                -d "$HANDSHAKE_PAYLOAD")
            [[ "$ADMIN_RESPONSE_CODE" == "200" ]] && break
        done
        if [[ "$ADMIN_RESPONSE_CODE" != "200" ]]; then
            log_fatal "Admin node unreachable after 3 retries — restart admin koi-server and re-run bootstrap"
        fi
    fi
    log_info "Admin handshake succeeded (edges PROPOSED)"

    # Handshake: admin → local (with defer_approval)
    log_info "Sending admin profile to local node..."
    LOCAL_HANDSHAKE_PAYLOAD=$(python3 -c "
import json
profile = json.loads('$ADMIN_PROFILE')
payload = {'type': 'handshake', 'profile': profile, 'defer_approval': True}
print(json.dumps(payload))
")

    curl -sf -X POST "http://127.0.0.1:${KOI_PORT}/koi-net/handshake" \
        -H "Content-Type: application/json" \
        -d "$LOCAL_HANDSHAKE_PAYLOAD" > /dev/null || \
        log_fatal "Local handshake failed"
    log_info "Local handshake succeeded (edges PROPOSED)"

    # --- Step 12: SAS verification gate (mandatory) ---
    log_info "Step 12: SAS verification..."

    SAS_CODE=$(compute_sas "$LOCAL_KOI_PUBKEY" "$ADMIN_KOI_PUBKEY")

    echo ""
    echo "==================================="
    echo "  VERIFY THIS CODE WITH ADMIN"
    echo "  SAS: $SAS_CODE"
    echo "==================================="
    echo ""
    echo "  Admin: run ./compute-sas.sh $NODE_NAME and compare"
    echo ""

    # SAS is always interactive — --yes does NOT skip this
    read -rp "Does admin confirm this code? [y/N]: " sas_confirm

    if [[ "$sas_confirm" != "y" && "$sas_confirm" != "Y" ]]; then
        log_error "IDENTITY MISMATCH — possible MITM. Cleaning up local state..."

        # Delete all edges between local and admin
        psql -d personal_koi -c "
            DELETE FROM koi_net_edges WHERE
                (source_node='$LOCAL_NODE_RID' AND target_node='$ADMIN_NODE_RID')
                OR (source_node='$ADMIN_NODE_RID' AND target_node='$LOCAL_NODE_RID')
        " 2>/dev/null || true

        # Remove admin node record
        psql -d personal_koi -c "
            DELETE FROM koi_net_nodes WHERE node_rid='$ADMIN_NODE_RID'
        " 2>/dev/null || true

        # Remove alias
        psql -d personal_koi -c "
            DELETE FROM koi_net_peer_aliases WHERE node_rid='$ADMIN_NODE_RID'
        " 2>/dev/null || true

        echo ""
        echo "  Local state cleaned up."
        echo "  Admin must run: ./remove-peer.sh $NODE_NAME"
        echo ""
        exit 1
    fi

    # --- Step 13: Post-SAS: approve local edges + configure vault sync ---
    log_info "Step 13: Approving local edges and configuring vault sync..."

    # Read admin token
    ADMIN_TOKEN_FILE="$KOI_STATE/admin_token"
    if [[ -f "$ADMIN_TOKEN_FILE" ]]; then
        LOCAL_ADMIN_TOKEN=$(cat "$ADMIN_TOKEN_FILE")
    else
        log_warn "No admin token found — cannot approve local edges automatically"
        LOCAL_ADMIN_TOKEN=""
    fi

    if [[ -n "$LOCAL_ADMIN_TOKEN" ]]; then
        # Find all PROPOSED edges involving admin
        PROPOSED_EDGES=$(psql -d personal_koi -Atc "
            SELECT edge_rid FROM koi_net_edges WHERE status='PROPOSED'
              AND ((source_node='$LOCAL_NODE_RID' AND target_node='$ADMIN_NODE_RID')
                OR (source_node='$ADMIN_NODE_RID' AND target_node='$LOCAL_NODE_RID'))
        ")

        APPROVED_COUNT=0
        while IFS= read -r edge_rid; do
            [[ -z "$edge_rid" ]] && continue
            curl -sf -X POST "http://127.0.0.1:${KOI_PORT}/koi-net/edges/approve" \
                -H "Content-Type: application/json" \
                -H "Authorization: Bearer $LOCAL_ADMIN_TOKEN" \
                -d "{\"edge_rid\": \"$edge_rid\"}" > /dev/null 2>&1 && \
                APPROVED_COUNT=$((APPROVED_COUNT + 1)) || \
                log_warn "Failed to approve edge: $edge_rid"
        done <<< "$PROPOSED_EDGES"
        log_info "Approved $APPROVED_COUNT local edges"
    fi

    echo ""
    echo "  Admin: approve edges to this peer by running:"
    echo "    ./approve-peer-edges.sh $NODE_NAME"
    echo "  (peer node_rid: $LOCAL_NODE_RID)"
    echo ""

    # Configure vault sync (non-fatal)
    if [[ -n "$INVITE_VAULT_SYNC_FOLDER" && "$INVITE_VAULT_SYNC_FOLDER" != "None" ]]; then
        log_info "Configuring vault sync (folder: $INVITE_VAULT_SYNC_FOLDER)..."
        curl -sf -X POST "http://127.0.0.1:${KOI_PORT}/koi-net/vault-sync/configure" \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${LOCAL_ADMIN_TOKEN:-}" \
            -d "{\"folder\": \"$INVITE_VAULT_SYNC_FOLDER\"}" > /dev/null 2>&1 || \
            log_warn "Vault sync configuration failed — configure manually later"
    fi

    # --- Summary ---
    echo ""
    echo "==================================="
    echo "  Bootstrap Complete (invite flow)"
    echo "==================================="
    echo ""
    echo "  Node:     $NODE_NAME"
    echo "  RID:      $LOCAL_NODE_RID"
    echo "  WG IP:    $WG_IP"
    echo "  SAS:      $SAS_CODE (verified)"
    echo ""
    echo "  Local edges:  APPROVED"
    echo "  Admin edges:  PROPOSED (admin must approve)"
    echo ""
    echo "  Admin runs: ./approve-peer-edges.sh $NODE_NAME"
    echo "==================================="

else
    # ============================================
    # MANUAL FLOW (original behavior)
    # ============================================

    if ! $SKIP_JOIN; then
        log_info "Step 5: Generating join request..."
        generate_join_request
    else
        log_warn "Skipping join-request generation (--skip-join-request)"
    fi

    log_info "Step 6: Running validation checks..."
    run_validation

    echo ""
    echo "==================================="
    echo "  Bootstrap Complete"
    echo "==================================="
    echo ""
    if ! $SKIP_JOIN; then
        echo "  Join request file:"
        echo "    $STATE_DIR/wireguard/join-request.txt"
        echo ""
    fi
    echo "  Next (admin side):"
    echo "    1) Approve peer with approve-peer.sh --from-file <join-request>"
    echo "    2) Provide wg-koi.conf template to peer"
    echo ""
    echo "  Next (peer side after approval):"
    echo "    1) Activate WireGuard (wg-quick up wg-koi)"
    echo "    2) Run setup-node.sh --yes $NODE_NAME $WG_IP \"$KOI_PATH\""
    echo "    3) Run connect-peers.sh http://10.100.0.2:8351 darren"
    echo ""
    _vault_hint="${VAULT_PATH:-\$HOME/Documents/Notes}"
    echo "  MCP server (for Claude Code users):"
    echo "    1) Install Node.js >= 18"
    echo "    2) git clone https://github.com/DarrenZal/personal-koi-mcp.git ~/projects/personal-koi-mcp"
    echo "    3) cd ~/projects/personal-koi-mcp && npm install && npm run build"
    echo "    4) claude mcp add --scope user -e OBSIDIAN_VAULT_PATH=$_vault_hint -e KOI_API_ENDPOINT=http://localhost:8351 personal-koi -- node ~/projects/personal-koi-mcp/dist/index.js"
    echo "==================================="
fi
