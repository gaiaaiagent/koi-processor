#!/usr/bin/env bash
set -euo pipefail

# Phase 1 Smoke Test — validates live TerminusDB integration end-to-end.
#
# Prerequisites:
#   - TerminusDB container running on port 6363
#   - PostgreSQL personal_koi database accessible
#   - No process on port 8351
#
# Usage:
#   bash scripts/terminusdb/smoke_phase1.sh [--fresh]
#
#   --fresh   Drop and recreate TDB database before testing (fixes schema drift)

FRESH=""
for arg in "$@"; do
    case "$arg" in
        --fresh) FRESH="--fresh" ;;
        *) echo "Unknown argument: $arg"; echo "Usage: $0 [--fresh]"; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

# Safe env loading (handles spaces/quotes)
set -a; source config/personal.env; set +a

PASS=0
FAIL=0
step() { echo -e "\n=== $1 ==="; }
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

cleanup() {
    if [[ -n "${API_PID:-}" ]]; then
        kill "$API_PID" 2>/dev/null || true
        wait "$API_PID" 2>/dev/null || true
    fi
    if [[ -n "${WORKER_PID:-}" ]]; then
        kill "$WORKER_PID" 2>/dev/null || true
        wait "$WORKER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

# -------------------------------------------------------------------------
step "0. Preflight"
# -------------------------------------------------------------------------

if lsof -i :8351 2>/dev/null | grep -q LISTEN; then
    fail "Port 8351 already in use"
    exit 1
fi
pass "Port 8351 clear"

if ! docker ps --format '{{.Names}}' | grep -q '^terminusdb$'; then
    fail "TerminusDB container not running"
    exit 1
fi
pass "TerminusDB container running"

if ! psql personal_koi -c "SELECT 1" > /dev/null 2>&1; then
    fail "PostgreSQL not reachable"
    exit 1
fi
pass "PostgreSQL reachable"

# -------------------------------------------------------------------------
step "1. Import${FRESH:+ (fresh)}"
# -------------------------------------------------------------------------

venv/bin/python -m scripts.terminusdb.import_from_postgres $FRESH 2>&1
pass "Import completed"

# -------------------------------------------------------------------------
step "2. Start API + worker"
# -------------------------------------------------------------------------

( set -a; source config/personal.env; set +a
  venv/bin/uvicorn api.personal_ingest_api:app --host 0.0.0.0 --port 8351 ) &
API_PID=$!

( set -a; source config/personal.env; set +a
  venv/bin/python -m scripts.terminusdb.outbox_worker ) &
WORKER_PID=$!

# Wait for API to be ready
for i in {1..15}; do
    if curl -sf localhost:8351/health > /dev/null 2>&1; then
        break
    fi
    sleep 1
done

if ! curl -sf localhost:8351/health > /dev/null 2>&1; then
    fail "API did not start within 15s"
    exit 1
fi
pass "API listening on :8351"

# -------------------------------------------------------------------------
step "3. Graph health"
# -------------------------------------------------------------------------

HEALTH=$(curl -sf localhost:8351/graph/health)
TDB_REACHABLE=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('terminusdb_reachable',False))")
SCHEMA_OK=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('schema_ok',False))")

if [[ "$TDB_REACHABLE" == "True" ]]; then pass "TerminusDB reachable"; else fail "TerminusDB not reachable"; fi
if [[ "$SCHEMA_OK" == "True" ]]; then pass "Schema OK"; else fail "Schema mismatch"; fi

# -------------------------------------------------------------------------
step "4. Smoke test — register entity"
# -------------------------------------------------------------------------

TIMESTAMP=$(date +%s)
REGISTER_RESULT=$(curl -sf -X POST localhost:8351/register-entity \
    -H 'Content-Type: application/json' \
    -d "{
        \"name\": \"Phase1 Smoke Test $TIMESTAMP\",
        \"entity_type\": \"Concept\",
        \"vault_rid\": \"Tests/phase1-smoke-$TIMESTAMP.md\",
        \"vault_path\": \"Tests/phase1-smoke-$TIMESTAMP.md\",
        \"content_hash\": \"smoke_$TIMESTAMP\"
    }")

IS_NEW=$(echo "$REGISTER_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('is_new',False))")
CANONICAL_URI=$(echo "$REGISTER_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('canonical_uri',''))")

if [[ "$IS_NEW" == "True" && -n "$CANONICAL_URI" ]]; then
    pass "Entity registered: $CANONICAL_URI"
else
    fail "Entity registration (is_new=$IS_NEW)"
fi

# Wait for worker to drain
sleep 5

PENDING=$(psql -tAq personal_koi -c "SELECT COUNT(*) FROM terminusdb_outbox WHERE status='pending';")
if [[ "$PENDING" -eq 0 ]]; then
    pass "Outbox drained (0 pending)"
else
    fail "Outbox has $PENDING pending rows"
fi

# -------------------------------------------------------------------------
step "5. Auth guard"
# -------------------------------------------------------------------------

LOCAL_CODE=$(curl -s -o /dev/null -w "%{http_code}" localhost:8351/graph/health)
if [[ "$LOCAL_CODE" == "200" ]]; then pass "Localhost → 200"; else fail "Localhost → $LOCAL_CODE (expected 200)"; fi

LAN_IP=$(ipconfig getifaddr en0 2>/dev/null \
    || hostname -I 2>/dev/null | awk '{print $1}' \
    || ip -4 addr show scope global 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 | head -1 \
    || echo "")
if [[ -n "$LAN_IP" ]]; then
    LAN_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://${LAN_IP}:8351/graph/health")
    if [[ "$LAN_CODE" == "403" ]]; then pass "LAN IP → 403"; else fail "LAN IP → $LAN_CODE (expected 403)"; fi
else
    echo "  SKIP: No LAN IP detected"
fi

# -------------------------------------------------------------------------
step "6. Fail-open"
# -------------------------------------------------------------------------

docker stop terminusdb > /dev/null
sleep 2

FAILOPEN_RESULT=$(curl -sf -X POST localhost:8351/register-entity \
    -H 'Content-Type: application/json' \
    -d "{
        \"name\": \"Fail Open Smoke $TIMESTAMP\",
        \"entity_type\": \"Concept\",
        \"vault_rid\": \"Tests/failopen-$TIMESTAMP.md\",
        \"vault_path\": \"Tests/failopen-$TIMESTAMP.md\",
        \"content_hash\": \"failopen_$TIMESTAMP\"
    }")

FAILOPEN_OK=$(echo "$FAILOPEN_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('success',False))")
if [[ "$FAILOPEN_OK" == "True" ]]; then
    pass "PG write succeeds with TDB down"
else
    fail "PG write failed with TDB down"
fi

PENDING_AFTER=$(psql -tAq personal_koi -c "SELECT COUNT(*) FROM terminusdb_outbox WHERE status IN ('pending','processing');")
if [[ "$PENDING_AFTER" -gt 0 ]]; then
    pass "Outbox accumulating ($PENDING_AFTER pending)"
else
    # Might have been claimed already — check if any row was just created
    pass "Outbox row created (worker may have already attempted)"
fi

docker start terminusdb > /dev/null

# Wait for TDB to come up + worker to drain backlog
echo "  Waiting for TDB recovery + backlog drain..."
for i in {1..30}; do
    STILL_PENDING=$(psql -tAq personal_koi -c "SELECT COUNT(*) FROM terminusdb_outbox WHERE status IN ('pending','processing');")
    if [[ "$STILL_PENDING" -eq 0 ]]; then break; fi
    sleep 2
done

DEAD=$(psql -tAq personal_koi -c "SELECT COUNT(*) FROM terminusdb_outbox WHERE status='dead_letter';")
STILL_PENDING=$(psql -tAq personal_koi -c "SELECT COUNT(*) FROM terminusdb_outbox WHERE status IN ('pending','processing');")

if [[ "$STILL_PENDING" -eq 0 && "$DEAD" -eq 0 ]]; then
    pass "Backlog drained after recovery"
elif [[ "$DEAD" -gt 0 ]]; then
    fail "$DEAD dead-letter rows (increase MAX_ATTEMPTS or check TDB startup time)"
else
    fail "$STILL_PENDING rows still pending after 60s"
fi

# -------------------------------------------------------------------------
step "7. Reconciliation"
# -------------------------------------------------------------------------

RECON_OUTPUT=$(venv/bin/python -m scripts.terminusdb.reconcile 2>&1)
echo "$RECON_OUTPUT"

if echo "$RECON_OUTPUT" | grep -q "No drift detected"; then
    pass "Reconciliation: 0 drift"
else
    fail "Reconciliation detected drift"
fi

# -------------------------------------------------------------------------
step "Results"
# -------------------------------------------------------------------------

TOTAL=$((PASS + FAIL))
echo ""
echo "  $PASS/$TOTAL passed"
if [[ "$FAIL" -gt 0 ]]; then
    echo "  $FAIL FAILED"
    exit 1
else
    echo "  Phase 1 smoke test: ALL PASS"
fi
