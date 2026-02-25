#!/usr/bin/env bash
# lib.sh — Shared functions for KOI-net federation scripts
#
# Source this file: source "$(dirname "$0")/lib.sh"

set -euo pipefail

# ============================================
# CONSTANTS
# ============================================

PEER_REGISTRY="${PEER_REGISTRY:-$HOME/.config/personal-koi/peer-registry.json}"
KOI_STATE_DIR="${KOI_STATE_DIR:-$HOME/.config/personal-koi/koi-state}"
WG_STATE_DIR="${WG_STATE_DIR:-$HOME/.config/personal-koi/wireguard}"
KOI_PROCESSOR_DIR="${KOI_PROCESSOR_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

# ============================================
# LOGGING
# ============================================

log_info()  { echo "[INFO]  $*"; }
log_warn()  { echo "[WARN]  $*" >&2; }
log_error() { echo "[ERROR] $*" >&2; }
log_fatal() { echo "[FATAL] $*" >&2; exit 1; }

# ============================================
# DRY-RUN SUPPORT
# ============================================

DRY_RUN=false

parse_dry_run() {
    if [[ "${1:-}" == "--dry-run" ]]; then
        DRY_RUN=true
        shift
    fi
    echo "$@"
}

run_cmd() {
    # Execute a command, or print it if --dry-run
    if $DRY_RUN; then
        echo "[DRY-RUN] $*"
    else
        "$@"
    fi
}

run_ssh() {
    # Execute a command via SSH, or print it if --dry-run
    local target="$1"
    shift
    if $DRY_RUN; then
        echo "[DRY-RUN] ssh $target: $*"
    else
        ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "$target" "$@"
    fi
}

# ============================================
# INPUT VALIDATION
# ============================================

validate_peer_name() {
    local name="$1"
    if [[ ! "$name" =~ ^[a-z][a-z0-9-]{1,30}$ ]]; then
        log_fatal "Invalid peer name '$name': must be lowercase alphanumeric + hyphens, 2-31 chars, start with letter"
    fi
}

validate_peer_number() {
    local num="$1"
    if ! [[ "$num" =~ ^[0-9]+$ ]] || (( num < 2 || num > 254 )); then
        log_fatal "Invalid peer number '$num': must be 2-254"
    fi
}

validate_wg_pubkey() {
    local key="$1"
    # WireGuard public keys are 44-char base64 (32 bytes + padding)
    if [[ ${#key} -ne 44 ]] || ! echo "$key" | base64 -d &>/dev/null; then
        log_fatal "Invalid WireGuard public key: must be 44-char base64"
    fi
}

validate_ssh_target() {
    local target="$1"
    if [[ ! "$target" =~ ^[a-zA-Z0-9._-]+@[a-zA-Z0-9._-]+$ ]]; then
        log_fatal "Invalid SSH target '$target': expected user@host format"
    fi
}

# ============================================
# PEER REGISTRY (atomic read/write)
# ============================================

peer_registry_init() {
    local dir
    dir="$(dirname "$PEER_REGISTRY")"
    mkdir -p "$dir"
    if [[ ! -f "$PEER_REGISTRY" ]]; then
        echo '[]' > "$PEER_REGISTRY"
    fi
}

peer_registry_read() {
    # Output the full peer registry JSON
    peer_registry_init
    cat "$PEER_REGISTRY"
}

peer_registry_add() {
    # Atomic add: flock + write to temp + mv
    local entry_json="$1"
    peer_registry_init

    local lockfile="${PEER_REGISTRY}.lock"
    local tmpfile="${PEER_REGISTRY}.tmp.$$"

    (
        flock -w 5 200 || log_fatal "Failed to acquire peer registry lock"

        # Check for duplicate peer_name
        local peer_name
        peer_name=$(echo "$entry_json" | python3 -c "import sys,json; print(json.load(sys.stdin)['peer_name'])")
        local existing
        existing=$(python3 -c "
import json, sys
reg = json.load(open('$PEER_REGISTRY'))
for p in reg:
    if p['peer_name'] == '$peer_name' and p.get('status') == 'active':
        print('exists')
        sys.exit(0)
print('ok')
")
        if [[ "$existing" == "exists" ]]; then
            log_fatal "Peer '$peer_name' already exists in registry with status=active"
        fi

        # Append entry
        python3 -c "
import json, sys
reg = json.load(open('$PEER_REGISTRY'))
entry = json.loads('''$entry_json''')
reg.append(entry)
with open('$tmpfile', 'w') as f:
    json.dump(reg, f, indent=2)
"
        mv "$tmpfile" "$PEER_REGISTRY"

    ) 200>"$lockfile"
}

peer_registry_update_status() {
    # Atomic status update by peer_name
    local peer_name="$1"
    local new_status="$2"
    local extra_field="${3:-}"  # e.g. "removed_at" for timestamp
    local extra_value="${4:-}"

    peer_registry_init
    local lockfile="${PEER_REGISTRY}.lock"
    local tmpfile="${PEER_REGISTRY}.tmp.$$"

    (
        flock -w 5 200 || log_fatal "Failed to acquire peer registry lock"

        python3 -c "
import json, sys
reg = json.load(open('$PEER_REGISTRY'))
found = False
for p in reg:
    if p['peer_name'] == '$peer_name':
        p['status'] = '$new_status'
        if '$extra_field' and '$extra_value':
            p['$extra_field'] = '$extra_value'
        found = True
        break
if not found:
    print('Peer not found: $peer_name', file=sys.stderr)
    sys.exit(1)
with open('$tmpfile', 'w') as f:
    json.dump(reg, f, indent=2)
"
        mv "$tmpfile" "$PEER_REGISTRY"

    ) 200>"$lockfile"
}

peer_registry_lookup() {
    # Look up a peer by name or node_rid, output JSON entry
    local query="$1"
    peer_registry_init
    python3 -c "
import json, sys
reg = json.load(open('$PEER_REGISTRY'))
for p in reg:
    if p['peer_name'] == '$query' or p.get('node_rid') == '$query':
        json.dump(p, sys.stdout, indent=2)
        sys.exit(0)
print('', file=sys.stderr)
sys.exit(1)
" 2>/dev/null
}

# ============================================
# SSH HELPERS
# ============================================

ssh_file_exists() {
    local target="$1" path="$2"
    ssh -o ConnectTimeout=10 "$target" "test -f '$path'" 2>/dev/null
}

ssh_cmd_exists() {
    local target="$1" cmd="$2"
    ssh -o ConnectTimeout=10 "$target" "command -v '$cmd'" &>/dev/null
}

# ============================================
# TRANSACTIONAL RELAY CONFIG EDIT
# ============================================

relay_config_edit() {
    # Transactional edit of WireGuard config on relay VPS
    # Args: <ssh-target> <action> [action-args...]
    # Actions: add-peer <pubkey> <ip>, remove-peer <pubkey>
    local ssh_target="$1"
    local action="$2"
    shift 2

    local remote_script
    case "$action" in
        add-peer)
            local pubkey="$1" peer_ip="$2"
            remote_script="$(cat <<'REMOTE_EOF'
set -euo pipefail
LOCKFILE="/tmp/wg-koi.lock"
CONF="/etc/wireguard/wg-koi.conf"

(
    flock -w 10 200 || { echo "FAIL: Could not acquire lock"; exit 1; }

    # Backup
    BACKUP="${CONF}.bak.$(date +%s)"
    cp "$CONF" "$BACKUP"

    # Check for duplicate
    if grep -q "PUBKEY_PLACEHOLDER" "$CONF"; then
        echo "FAIL: Peer with this public key already exists"
        exit 1
    fi

    # Append peer to temp copy
    TMPCONF="${CONF}.tmp.$$"
    cp "$CONF" "$TMPCONF"
    cat >> "$TMPCONF" <<PEER

[Peer]
PublicKey = PUBKEY_PLACEHOLDER
AllowedIPs = IP_PLACEHOLDER/32
PEER

    # Syntax check
    if ! wg-quick strip wg-koi < "$TMPCONF" > /dev/null 2>&1; then
        echo "FAIL: Syntax check failed, restoring backup"
        rm -f "$TMPCONF"
        exit 1
    fi

    # Atomic replace
    mv "$TMPCONF" "$CONF"

    # Live reload
    if wg show wg-koi > /dev/null 2>&1; then
        wg syncconf wg-koi <(wg-quick strip wg-koi)
    fi

    echo "OK: Peer added successfully"

) 200>"$LOCKFILE"
REMOTE_EOF
)"
            # Substitute placeholders
            remote_script="${remote_script//PUBKEY_PLACEHOLDER/$pubkey}"
            remote_script="${remote_script//IP_PLACEHOLDER/$peer_ip}"
            ;;

        remove-peer)
            local pubkey="$1"
            remote_script="$(cat <<'REMOTE_EOF'
set -euo pipefail
LOCKFILE="/tmp/wg-koi.lock"
CONF="/etc/wireguard/wg-koi.conf"

(
    flock -w 10 200 || { echo "FAIL: Could not acquire lock"; exit 1; }

    # Backup
    BACKUP="${CONF}.bak.$(date +%s)"
    cp "$CONF" "$BACKUP"

    # Remove peer block matching pubkey
    TMPCONF="${CONF}.tmp.$$"
    python3 -c "
import re, sys
conf = open('$CONF').read()
# Split into blocks, remove the one with matching pubkey
blocks = re.split(r'(?=\[Peer\])', conf)
filtered = []
removed = False
for b in blocks:
    if 'PUBKEY_PLACEHOLDER' in b:
        removed = True
    else:
        filtered.append(b)
if not removed:
    print('FAIL: Peer not found in config', file=sys.stderr)
    sys.exit(1)
with open('$TMPCONF', 'w') as f:
    f.write(''.join(filtered).rstrip() + '\n')
"

    # Syntax check
    if ! wg-quick strip wg-koi < "$TMPCONF" > /dev/null 2>&1; then
        echo "FAIL: Syntax check failed, restoring from $BACKUP"
        cp "$BACKUP" "$CONF"
        rm -f "$TMPCONF"
        exit 1
    fi

    # Atomic replace
    mv "$TMPCONF" "$CONF"

    # Live reload
    if wg show wg-koi > /dev/null 2>&1; then
        wg syncconf wg-koi <(wg-quick strip wg-koi)
    fi

    echo "OK: Peer removed successfully"

) 200>"$LOCKFILE"
REMOTE_EOF
)"
            remote_script="${remote_script//PUBKEY_PLACEHOLDER/$pubkey}"
            ;;

        *)
            log_fatal "Unknown relay config action: $action"
            ;;
    esac

    if $DRY_RUN; then
        echo "[DRY-RUN] Would execute transactional config edit on $ssh_target:"
        echo "[DRY-RUN]   Action: $action"
        return 0
    fi

    local result
    result=$(ssh -o ConnectTimeout=10 "$ssh_target" "sudo bash -s" <<< "$remote_script" 2>&1) || true

    if [[ "$result" == OK:* ]]; then
        log_info "$result"
        return 0
    else
        log_error "Relay config edit failed: $result"
        return 1
    fi
}

# ============================================
# FINGERPRINT COMPUTATION
# ============================================

compute_koi_fingerprint() {
    # Compute SHA256 fingerprint of the actual DER public key bytes
    # Input: base64-encoded DER public key string
    # Output: SHA256:<base64-hash>
    local der_b64="$1"
    local hash
    # Decode base64 to get raw DER bytes, then SHA256 those bytes
    hash=$(echo -n "$der_b64" | base64 -d | openssl dgst -sha256 -binary | base64 | tr -d '=')
    echo "SHA256:${hash}"
}

# ============================================
# MIGRATION RUNNER
# ============================================

run_migrations() {
    # Run pending SQL migrations with tracking
    # Args: <postgres-url> <migrations-dir>
    local pg_url="$1"
    local mig_dir="$2"

    if $DRY_RUN; then
        echo "[DRY-RUN] Would run migrations from $mig_dir"
        return 0
    fi

    ensure_python_module "psycopg2" "psycopg2-binary"

    python3 -c "
import os, sys, glob, psycopg2

pg_url = '$pg_url'
mig_dir = '$mig_dir'

conn = psycopg2.connect(pg_url)
conn.autocommit = True
cur = conn.cursor()

# Create tracking table if not exists
cur.execute('''
    CREATE TABLE IF NOT EXISTS koi_schema_migrations (
        filename TEXT PRIMARY KEY,
        applied_at TIMESTAMPTZ DEFAULT NOW()
    )
''')

# Baseline detection: if table was just created but schema tables exist
cur.execute(\"SELECT COUNT(*) FROM koi_schema_migrations\")
count = cur.fetchone()[0]
if count == 0:
    # Check if this is a pre-tracking database
    cur.execute(\"SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name='koi_net_events')\")
    if cur.fetchone()[0]:
        print('[INFO] Pre-tracking database detected, recording baseline...')
        # Only mark migrations as baseline if their target table already exists
        # This prevents marking NEW migrations (like 045) as already applied
        import re
        mig_files = sorted(glob.glob(os.path.join(mig_dir, '*.sql')))

        # First pass: find highest migration number whose CREATE TABLEs all exist
        highest_existing_num = 0
        for mf in mig_files:
            fname = os.path.basename(mf)
            sql_content = open(mf).read()
            tables = re.findall(r'CREATE TABLE(?:\s+IF NOT EXISTS)?\s+(\w+)', sql_content, re.IGNORECASE)
            if tables:
                all_exist = True
                for tbl in tables:
                    cur.execute(\"SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name=%s)\", (tbl,))
                    if not cur.fetchone()[0]:
                        all_exist = False
                        break
                if all_exist:
                    try:
                        num = int(re.match(r'(\d+)', fname).group(1))
                        highest_existing_num = max(highest_existing_num, num)
                    except (AttributeError, ValueError):
                        pass
        print(f'[INFO] Baseline cutoff: migration number {highest_existing_num}')

        # Second pass: mark baselines
        baseline_count = 0
        for mf in mig_files:
            fname = os.path.basename(mf)
            # Read the SQL to check if its CREATE TABLE target already exists
            sql_content = open(mf).read()
            tables = re.findall(r'CREATE TABLE(?:\s+IF NOT EXISTS)?\s+(\w+)', sql_content, re.IGNORECASE)
            if tables:
                # Check if ALL tables in this migration already exist
                all_exist = True
                for tbl in tables:
                    cur.execute(\"SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name=%s)\", (tbl,))
                    if not cur.fetchone()[0]:
                        all_exist = False
                        break
                if all_exist:
                    cur.execute(
                        \"INSERT INTO koi_schema_migrations (filename, applied_at) VALUES (%s, 'epoch') ON CONFLICT DO NOTHING\",
                        (fname,)
                    )
                    baseline_count += 1
                else:
                    print(f'[INFO] Migration {fname} has new tables, will be applied')
            else:
                # Non-CREATE-TABLE migrations (ALTER, INDEX, etc.)
                # Only mark as baseline if their number is <= the highest CREATE-TABLE
                # migration whose tables already exist. This avoids skipping new ALTERs.
                try:
                    file_num = int(re.match(r'(\d+)', fname).group(1))
                except (AttributeError, ValueError):
                    file_num = 999999
                if file_num <= highest_existing_num:
                    cur.execute(
                        \"INSERT INTO koi_schema_migrations (filename, applied_at) VALUES (%s, 'epoch') ON CONFLICT DO NOTHING\",
                        (fname,)
                    )
                    baseline_count += 1
                else:
                    print(f'[INFO] Migration {fname} (non-CREATE, num={file_num}) is newer than baseline cutoff ({highest_existing_num}), will be applied')
        print(f'[INFO] Marked {baseline_count} existing migrations as baseline')

# Get already-applied migrations
cur.execute('SELECT filename FROM koi_schema_migrations')
applied = {row[0] for row in cur.fetchall()}

# Find and run pending migrations
mig_files = sorted(glob.glob(os.path.join(mig_dir, '*.sql')))
pending = [f for f in mig_files if os.path.basename(f) not in applied]

if not pending:
    print('[INFO] All migrations up to date')
else:
    for mf in pending:
        fname = os.path.basename(mf)
        print(f'[INFO] Applying migration: {fname}')
        try:
            sql = open(mf).read()
            cur.execute(sql)
            cur.execute(
                'INSERT INTO koi_schema_migrations (filename) VALUES (%s)',
                (fname,)
            )
            print(f'[INFO] Applied: {fname}')
        except Exception as e:
            print(f'[ERROR] Migration {fname} failed: {e}', file=sys.stderr)
            sys.exit(1)

conn.close()
print('[INFO] Migration check complete')
"
}

# ============================================
# JOIN REQUEST PARSING
# ============================================

parse_join_request() {
    # Parse a join request block, output key=value pairs
    # Args: <file-or-stdin>
    local input="${1:--}"
    python3 -c "
import sys, re
from datetime import datetime, timezone

text = open('$input').read() if '$input' != '-' else sys.stdin.read()

# Extract fields between markers
match = re.search(r'=== KOI-net Join Request ===(.*?)==========================', text, re.DOTALL)
if not match:
    print('FAIL: No join request block found', file=sys.stderr)
    sys.exit(1)

fields = {}
for line in match.group(1).strip().split('\n'):
    if ':' in line:
        key, val = line.split(':', 1)
        fields[key.strip()] = val.strip()

# Check expiry
expires = fields.get('expires', '')
if expires:
    try:
        exp_dt = datetime.fromisoformat(expires.replace('Z', '+00:00'))
        if exp_dt < datetime.now(timezone.utc):
            print('FAIL: Join request has expired', file=sys.stderr)
            sys.exit(1)
    except ValueError:
        print(f'WARN: Could not parse expires: {expires}', file=sys.stderr)

for k, v in fields.items():
    print(f'{k}={v}')
"
}

# ============================================
# PREREQUISITE CHECKS
# ============================================

require_cmd() {
    local cmd="$1"
    if ! command -v "$cmd" &>/dev/null; then
        log_fatal "Required command not found: $cmd"
    fi
}

ensure_python_module() {
    # Ensure a Python module is importable by python3.
    # Attempts installation if missing.
    # Args: <module-name> [pip-package-name]
    local module="$1"
    local pip_package="${2:-$module}"

    if python3 -c "import $module" 2>/dev/null; then
        return 0
    fi

    log_warn "Python module '$module' not found; attempting install of '$pip_package'..."

    # Try pip associated with python3 first (best chance to match runtime interpreter).
    if python3 -m pip --version >/dev/null 2>&1; then
        if python3 -m pip install -q "$pip_package" >/dev/null 2>&1; then
            :
        elif python3 -m pip install -q --user "$pip_package" >/dev/null 2>&1; then
            :
        fi
    fi

    # Fallback: project venv pip if available.
    if ! python3 -c "import $module" 2>/dev/null; then
        if [[ -f "$KOI_PROCESSOR_DIR/venv/bin/pip" ]]; then
            "$KOI_PROCESSOR_DIR/venv/bin/pip" install -q "$pip_package" >/dev/null 2>&1 || true
        fi
    fi

    # Final fallback: pip3 if available.
    if ! python3 -c "import $module" 2>/dev/null; then
        if command -v pip3 >/dev/null 2>&1; then
            pip3 install -q "$pip_package" >/dev/null 2>&1 || true
        fi
    fi

    if ! python3 -c "import $module" 2>/dev/null; then
        log_fatal "Required Python module not found: $module (install with: python3 -m pip install $pip_package)"
    fi
}

require_python_module() {
    local module="$1"
    local pip_package="${2:-$module}"
    ensure_python_module "$module" "$pip_package"
}

check_wg_tools() {
    if ! command -v wg &>/dev/null; then
        log_warn "wireguard-tools not found"
        if [[ "$(uname)" == "Darwin" ]]; then
            log_info "Install with: brew install wireguard-tools"
        else
            log_info "Install with: sudo apt install wireguard-tools"
        fi
        return 1
    fi
    return 0
}
