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
REPO_URL="https://github.com/gaiaaiagent/koi-processor.git"
REPO_REF="regen-prod"

usage() {
    cat <<USAGE
Usage:
  $0 [--dry-run] [--yes] [--skip-join-request] [--repo-url <url>] [--ref <git-ref>] <node-name> <wireguard-ip> [koi-processor-path]

Examples:
  $0 --yes nuc-personal 10.100.0.22 ~/projects/RegenAI/koi-processor
  $0 --dry-run --ref main shawn-personal 10.100.0.3
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

if [[ $# -lt 2 ]]; then
    usage
    exit 1
fi

NODE_NAME="$1"
WG_IP="$2"
KOI_PATH="${3:-$HOME/projects/RegenAI/koi-processor}"
STATE_DIR="${STATE_DIR:-$HOME/.config/personal-koi}"
DB_USER="${USER:-$(whoami)}"

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
    local packages=(
        wireguard-tools
        python3-venv
        python3-pip
        postgresql
        postgresql-contrib
        git
        curl
        jq
    )
    log_info "Installing Linux prerequisites..."
    if ! confirm_or_exit "Install packages with sudo apt-get?"; then
        log_fatal "Aborted by user"
    fi
    run_or_print "sudo apt-get update -qq"
    run_or_print "sudo apt-get install -y -qq ${packages[*]}"
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
    if [[ ! -f "$KOI_PATH/venv/bin/activate" ]]; then
        run_or_print "python3 -m venv \"$KOI_PATH/venv\""
    fi
    # Ensure pip exists even on minimal python installs.
    run_or_print "bash -lc '\"$KOI_PATH/venv/bin/python\" -m ensurepip --upgrade >/dev/null 2>&1 || true'"
    run_or_print "bash -lc 'source \"$KOI_PATH/venv/bin/activate\" && pip install -q --upgrade pip'"
    run_or_print "bash -lc 'source \"$KOI_PATH/venv/bin/activate\" && pip install -q -r \"$KOI_PATH/requirements.txt\"'"
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

log_info "Step 2: PostgreSQL bootstrap..."
ensure_postgres

log_info "Step 3: Repository bootstrap..."
ensure_repo

log_info "Step 4: Python environment bootstrap..."
setup_python_env

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
echo "==================================="
