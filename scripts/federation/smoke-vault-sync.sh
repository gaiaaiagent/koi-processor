#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

# ============================================================
# Vault Sync Two-Peer Smoke Test
# ============================================================
# Usage:
#   bash scripts/federation/smoke-vault-sync.sh                           # local mode
#   MODE=two-peer PEER_SSH=shawn@10.100.0.3 bash scripts/federation/smoke-vault-sync.sh
#   KOI_ADMIN_TOKEN=mytoken bash scripts/federation/smoke-vault-sync.sh
#   KEEP_ARTIFACTS_ON_FAIL=1 bash scripts/federation/smoke-vault-sync.sh
# ============================================================

# --- Parameters (env overridable) ---
MODE="${MODE:-local}"
API_PORT="${API_PORT:-8351}"
PEER_NAME="${PEER_NAME:-shawn}"
PEER_SSH="${PEER_SSH:-}"
PEER_VAULT_PATH="${PEER_VAULT_PATH:-~/Documents/Notes}"
SHARED_FOLDER="${SHARED_FOLDER:-Shared}"
VAULT_PATH="${VAULT_PATH:-$HOME/Documents/Notes}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-120}"
KEEP_ARTIFACTS_ON_FAIL="${KEEP_ARTIFACTS_ON_FAIL:-0}"
PEER_KOI_ADMIN_TOKEN="${PEER_KOI_ADMIN_TOKEN:-}"

# --- Validate mode ---
if [[ "$MODE" != "local" && "$MODE" != "two-peer" ]]; then
    echo "FATAL: MODE must be 'local' or 'two-peer' (got '$MODE')"
    exit 1
fi
if [[ "$MODE" == "two-peer" && -z "$PEER_SSH" ]]; then
    echo "FATAL: PEER_SSH is required for two-peer mode (e.g. PEER_SSH=shawn@10.100.0.3)"
    exit 1
fi

# --- Dependency preflight ---
for cmd in curl python3 psql; do
    command -v "$cmd" >/dev/null 2>&1 || { echo "FATAL: $cmd not found"; exit 1; }
done
if [[ "$MODE" == "two-peer" ]]; then
    command -v ssh >/dev/null 2>&1 || { echo "FATAL: ssh required for two-peer mode"; exit 1; }
fi

# --- Run ID + test file paths ---
RUN_ID=$(date +%s)
SMOKE_FILE="${SHARED_FOLDER}/_smoke-test-${RUN_ID}.md"
SMOKE_CONFLICT="${SHARED_FOLDER}/_smoke-conflict-${RUN_ID}.md"

# --- Counters ---
PASS=0
FAIL=0

# --- Admin token support ---
CURL_OPTS=(-sf)
if [[ -n "${KOI_ADMIN_TOKEN:-}" ]]; then
    CURL_OPTS+=(-H "Authorization: Bearer ${KOI_ADMIN_TOKEN}")
fi

BASE_URL="http://localhost:${API_PORT}"

# Build peer curl command (used in SSH calls for two-peer mode)
PEER_CURL="curl -sf"
if [[ -n "$PEER_KOI_ADMIN_TOKEN" ]]; then
    PEER_CURL="curl -sf -H 'Authorization: Bearer ${PEER_KOI_ADMIN_TOKEN}'"
fi

# --- Helpers ---
step() { echo -e "\n=== $1 ==="; }
pass() { echo "  PASS: $1"; (( PASS++ )) || true; }
fail() { echo "  FAIL: $1"; (( FAIL++ )) || true; }

json_get() {
    python3 -c "import sys,json; d=json.load(sys.stdin); print(d$1)"
}

wait_for() {
    local desc="$1" check_cmd="$2" timeout="${3:-$WAIT_TIMEOUT}"
    local deadline=$((SECONDS + timeout))
    while (( SECONDS < deadline )); do
        if eval "$check_cmd"; then return 0; fi
        sleep 5
    done
    return 1
}

check_file_tracked() {
    local count
    count=$(psql -tAq personal_koi -v smoke_file="'${SMOKE_FILE}'" -c "
        SELECT COUNT(*) FROM vault_sync_state
        WHERE relative_path = :smoke_file AND is_deleted = FALSE;
    ")
    [[ "$count" -ge 1 ]]
}

check_file_tombstoned() {
    local count
    count=$(psql -tAq personal_koi -v smoke_file="'${SMOKE_FILE}'" -c "
        SELECT COUNT(*) FROM vault_sync_state
        WHERE relative_path = :smoke_file AND is_deleted = TRUE;
    ")
    [[ "$count" -ge 1 ]]
}

get_rejected_total() {
    local status
    status=$(curl "${CURL_OPTS[@]}" "${BASE_URL}/koi-net/vault-sync/status")
    echo "$status" | python3 -c "import sys,json; d=json.load(sys.stdin); print(sum(d.get('rejected_events',{}).values()))" 2>/dev/null || echo "0"
}

# --- Cleanup trap ---
cleanup() {
    step "10. Cleanup"
    if (( FAIL > 0 )) && [[ "$KEEP_ARTIFACTS_ON_FAIL" == "1" ]]; then
        echo "  KEEP_ARTIFACTS_ON_FAIL=1 — leaving smoke test files for debugging"
    else
        rm -f "${VAULT_PATH}/${SHARED_FOLDER}/_smoke-test-${RUN_ID}"*.md
        rm -f "${VAULT_PATH}/${SHARED_FOLDER}/_smoke-conflict-${RUN_ID}"*.md
        if [[ "$MODE" == "two-peer" ]] && [[ -n "${PEER_SSH:-}" ]]; then
            ssh "$PEER_SSH" "rm -f \"${PEER_VAULT_PATH}/${SHARED_FOLDER}\"/_smoke-test-${RUN_ID}*.md \"${PEER_VAULT_PATH}/${SHARED_FOLDER}\"/_smoke-conflict-${RUN_ID}*.md" 2>/dev/null || true
        fi
        pass "smoke test files removed"
    fi

    echo ""
    echo "==============================="
    echo "  Vault Sync Smoke Test ($MODE)"
    echo "==============================="
    echo "  PASS: $PASS"
    echo "  FAIL: $FAIL"
    echo "==============================="
}
trap cleanup EXIT

# ============================================================
# Step 0: Preflight
# ============================================================
step "0. Preflight"

# API health
if curl "${CURL_OPTS[@]}" "${BASE_URL}/health" >/dev/null 2>&1; then
    pass "API healthy on localhost:${API_PORT}"
else
    fail "API not reachable on localhost:${API_PORT}"
    exit 1
fi

# vault_sync_state table reachable
if psql -tAq personal_koi -c "SELECT 1 FROM vault_sync_state LIMIT 1;" >/dev/null 2>&1; then
    pass "vault_sync_state table reachable"
else
    fail "vault_sync_state table not reachable"
    exit 1
fi

# Sync enabled
STATUS=$(curl "${CURL_OPTS[@]}" "${BASE_URL}/koi-net/vault-sync/status")
ENABLED=$(echo "$STATUS" | json_get "['enabled']" 2>/dev/null || echo "")
if [[ "$ENABLED" == "True" || "$ENABLED" == "true" ]]; then
    pass "vault sync enabled"
else
    fail "vault sync not enabled (got: $ENABLED)"
fi

# Peer RID resolution
PEER_RID=$(psql -tAq personal_koi -v pname="'${PEER_NAME}'" -c "
    SELECT COALESCE(
        (SELECT node_rid FROM koi_net_peer_aliases WHERE LOWER(alias) = LOWER(:pname)),
        (SELECT node_rid FROM koi_net_nodes WHERE LOWER(node_name) = LOWER(:pname) AND status = 'active')
    );
")
if [[ -n "$PEER_RID" ]]; then
    pass "peer '${PEER_NAME}' resolved to ${PEER_RID}"
else
    fail "peer '${PEER_NAME}' not found in node registry"
fi

# Resolve local node RID from health endpoint
LOCAL_RID=$(curl "${CURL_OPTS[@]}" "${BASE_URL}/koi-net/health" 2>/dev/null \
    | json_get "['node']['node_rid']" 2>/dev/null || echo "")

# Edge rid_types check (scoped to peer→local edge)
if [[ -n "$PEER_RID" ]]; then
    EDGE_WHERE="source_node = :prid AND status = 'APPROVED'"
    if [[ -n "$LOCAL_RID" ]]; then
        EDGE_WHERE="source_node = :prid AND target_node = :lrid AND status = 'APPROVED'"
    fi
    RID_TYPES=$(psql -tAq personal_koi \
        -v prid="'${PEER_RID}'" \
        -v lrid="'${LOCAL_RID}'" \
        -c "SELECT array_to_string(rid_types, ',') FROM koi_net_edges WHERE ${EDGE_WHERE} LIMIT 1;")
    if echo "$RID_TYPES" | grep -iq "vault-file"; then
        pass "peer edge includes Vault-file in rid_types"
    else
        fail "peer edge missing Vault-file — re-run handshake to refresh"
    fi
fi

# ============================================================
# Step 1: Configure Vault Sync
# ============================================================
step "1. Configure Vault Sync"

if ! CONFIGURE_RESP=$(curl "${CURL_OPTS[@]}" -X POST \
    -H "Content-Type: application/json" \
    -d "{\"peer\": \"${PEER_NAME}\", \"shared_folder\": \"${SHARED_FOLDER}\"}" \
    "${BASE_URL}/koi-net/vault-sync/configure" 2>&1); then
    fail "configure HTTP call failed"; exit 1
fi

CONFIGURED_RID=$(echo "$CONFIGURE_RESP" | json_get "['peer_node_rid']" 2>/dev/null || echo "")
if [[ -n "$CONFIGURED_RID" ]]; then
    pass "configured for ${CONFIGURED_RID}"
else
    fail "configure returned unexpected response: ${CONFIGURE_RESP}"
fi

# ============================================================
# Step 2: Baseline
# ============================================================
step "2. Baseline"

BASELINE_REJECTED=$(get_rejected_total)
pass "baseline captured (rejected=${BASELINE_REJECTED})"

# ============================================================
# Step 3: Create Test File
# ============================================================
step "3. Create Test File"

mkdir -p "${VAULT_PATH}/${SHARED_FOLDER}"
cat > "${VAULT_PATH}/${SMOKE_FILE}" <<EOF
---
title: Smoke Test ${RUN_ID}
---
# Smoke Test

Created by smoke-vault-sync.sh at $(date -u +%Y-%m-%dT%H:%M:%SZ)
Run ID: ${RUN_ID}
EOF

if [[ -f "${VAULT_PATH}/${SMOKE_FILE}" ]]; then
    pass "wrote ${SMOKE_FILE}"
else
    fail "failed to create ${SMOKE_FILE}"
fi

# ============================================================
# Step 4: Trigger + Wait
# ============================================================
step "4. Trigger + Wait"

# Trigger a sync
if ! curl "${CURL_OPTS[@]}" -X POST "${BASE_URL}/koi-net/vault-sync/trigger" >/dev/null 2>&1; then
    fail "trigger call failed"; exit 1
fi

if wait_for "file tracked" check_file_tracked; then
    pass "file tracked in vault_sync_state"
else
    fail "file not tracked after ${WAIT_TIMEOUT}s"
fi

# ============================================================
# Step 5: Peer Arrival (two-peer only)
# ============================================================
if [[ "$MODE" == "two-peer" ]]; then
    step "5. Peer Arrival"

    PEER_FILE="${PEER_VAULT_PATH}/${SMOKE_FILE}"
    if wait_for "peer file arrival" "ssh \"$PEER_SSH\" 'test -f \"${PEER_FILE}\"'" ; then
        pass "file arrived on peer"
    else
        fail "file not found on peer after ${WAIT_TIMEOUT}s"
    fi
fi

# ============================================================
# Step 6: Conflict Test (two-peer only)
# ============================================================
if [[ "$MODE" == "two-peer" ]]; then
    step "6. Conflict Test"

    # Local writes content A
    cat > "${VAULT_PATH}/${SMOKE_CONFLICT}" <<EOF
---
title: Conflict Test ${RUN_ID}
---
Content A (local) — written at $(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF

    # Peer writes content B
    ssh "$PEER_SSH" "mkdir -p \"${PEER_VAULT_PATH}/${SHARED_FOLDER}\" && cat > \"${PEER_VAULT_PATH}/${SMOKE_CONFLICT}\"" <<EOF
---
title: Conflict Test ${RUN_ID}
---
Content B (peer) — written at $(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF

    # Both trigger sync
    if ! curl "${CURL_OPTS[@]}" -X POST "${BASE_URL}/koi-net/vault-sync/trigger" >/dev/null 2>&1; then
        fail "local trigger call failed"; exit 1
    fi
    if ! ssh "$PEER_SSH" "${PEER_CURL} -X POST http://localhost:${API_PORT}/koi-net/vault-sync/trigger" >/dev/null 2>&1; then
        fail "peer trigger call failed"; exit 1
    fi

    # Poll for conflict copy on either side
    check_conflict_copy() {
        ls "${VAULT_PATH}/${SHARED_FOLDER}/"*conflict*"${RUN_ID}"*"(conflict"* 2>/dev/null && return 0
        ssh "$PEER_SSH" "ls \"${PEER_VAULT_PATH}/${SHARED_FOLDER}/\"*conflict*${RUN_ID}*'(conflict'* 2>/dev/null" && return 0
        return 1
    }

    if wait_for "conflict copy" check_conflict_copy; then
        pass "conflict copy found"
    else
        fail "no conflict copy found after ${WAIT_TIMEOUT}s"
    fi
fi

# ============================================================
# Step 7: Delete Test
# ============================================================
step "7. Delete Test"

rm -f "${VAULT_PATH}/${SMOKE_FILE}"

# Trigger to detect deletion
if ! curl "${CURL_OPTS[@]}" -X POST "${BASE_URL}/koi-net/vault-sync/trigger" >/dev/null 2>&1; then
    fail "trigger call failed"; exit 1
fi

if wait_for "tombstone" check_file_tombstoned; then
    pass "tombstone created for _smoke-test-${RUN_ID}.md"
else
    fail "tombstone not created after ${WAIT_TIMEOUT}s"
fi

# ============================================================
# Step 8: Peer Delete Propagation (two-peer only)
# ============================================================
if [[ "$MODE" == "two-peer" ]]; then
    step "8. Peer Delete Propagation"

    PEER_FILE="${PEER_VAULT_PATH}/${SMOKE_FILE}"
    if wait_for "peer file deletion" "ssh \"$PEER_SSH\" '! test -f \"${PEER_FILE}\"'" ; then
        pass "file removed on peer"
    else
        fail "file still exists on peer after ${WAIT_TIMEOUT}s"
    fi
fi

# ============================================================
# Step 9: Final Status
# ============================================================
step "9. Final Status"

FINAL_REJECTED=$(get_rejected_total)
if [[ "$FINAL_REJECTED" -le "$BASELINE_REJECTED" ]]; then
    pass "no new rejected events"
else
    fail "rejected events increased: ${BASELINE_REJECTED} -> ${FINAL_REJECTED}"
fi

# Cleanup runs via EXIT trap; exit non-zero if any test failed
[[ $FAIL -eq 0 ]] || exit 1
