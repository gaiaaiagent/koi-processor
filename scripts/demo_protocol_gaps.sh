#!/usr/bin/env bash
# demo_protocol_gaps.sh — Reproduce the negative-space intelligence demo path.
#
# Requires: koi-processor running locally with migrations 079-082 applied.
# Usage:    bash scripts/demo_protocol_gaps.sh [BASE_URL]
#
# Default BASE_URL: http://localhost:8351

set -euo pipefail

BASE="${1:-http://localhost:8351}"
POOL_RID="orn:koi-net.pool:demo-herring-$(date +%s)"

echo "=== Protocol Layer Demo: Negative-Space Intelligence ==="
echo "Base URL: $BASE"
echo ""

# Step 0: Create a pool (via commitment pool API)
echo "--- Step 0: Create pool ---"
POOL=$(curl -sf -X POST "$BASE/pools/create" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"Demo Herring Pool\",
    \"description\": \"Victoria Landscape Hub — herring restoration\",
    \"activation_threshold_pct\": 80,
    \"metadata\": {}
  }")
POOL_RID=$(echo "$POOL" | python3 -c "import sys,json; print(json.load(sys.stdin)['pool_rid'])")
echo "Pool created: $POOL_RID"
echo ""

# Step 1: Create a requirement
echo "--- Step 1: Create requirement ---"
REQ=$(curl -sf -X POST "$BASE/protocol/requirements/create" \
  -H "Content-Type: application/json" \
  -d "{
    \"scope\": \"pool\",
    \"scope_ref\": \"$POOL_RID\",
    \"policy_source\": \"demo-constitution\",
    \"requirement_type\": \"monitoring\",
    \"statement\": \"Quarterly herring population monitoring\",
    \"frequency\": \"quarterly\",
    \"freshness_window_days\": 90,
    \"severity\": \"high\"
  }")
REQ_RID=$(echo "$REQ" | python3 -c "import sys,json; print(json.load(sys.stdin)['requirement_rid'])")
echo "Requirement created: $REQ_RID"
echo "$REQ" | python3 -m json.tool
echo ""

# Step 2: Compute gaps — should find 1 unmet
echo "--- Step 2: Compute gaps (expect 1 unmet) ---"
GAPS=$(curl -sf "$BASE/protocol/pools/$POOL_RID/gaps")
echo "$GAPS" | python3 -m json.tool
echo ""

UNMET=$(echo "$GAPS" | python3 -c "import sys,json; print(json.load(sys.stdin)['unmet_count'])")
NEXT=$(echo "$GAPS" | python3 -c "import sys,json; g=json.load(sys.stdin)['gaps']; print(g[0]['next_move'] if g else 'none')")
echo "Result: $UNMET unmet gap(s), next_move=$NEXT"
echo ""

# Step 3: Check emitted signal
echo "--- Step 3: Verify gap_computed signal ---"
SIGS=$(curl -sf "$BASE/protocol/signals/?source_ref=$POOL_RID&signal_type=gap_computed")
echo "$SIGS" | python3 -m json.tool
echo ""

# Step 4: Create a commitment and link coverage
echo "--- Step 4: Add commitment + coverage ---"
COMMIT=$(curl -sf -X POST "$BASE/commitments/create" \
  -H "Content-Type: application/json" \
  -d "{
    \"pledger_uri\": \"urn:demo:marine-biologist\",
    \"title\": \"Herring population survey Q2 2026\",
    \"offer_type\": \"stewardship\",
    \"pool_rid\": \"$POOL_RID\",
    \"metadata\": {}
  }")
COMMIT_RID=$(echo "$COMMIT" | python3 -c "import sys,json; print(json.load(sys.stdin)['commitment_rid'])")
echo "Commitment created: $COMMIT_RID"

COV=$(curl -sf -X POST "$BASE/protocol/coverage/link" \
  -H "Content-Type: application/json" \
  -d "{
    \"coverage_type\": \"commitment_covers_requirement\",
    \"source_rid\": \"$COMMIT_RID\",
    \"target_rid\": \"$REQ_RID\",
    \"confidence\": 0.95,
    \"provenance\": \"manual\"
  }")
COV_RID=$(echo "$COV" | python3 -c "import sys,json; print(json.load(sys.stdin)['coverage_rid'])")
echo "Coverage linked: $COV_RID"
echo ""

# Step 5: Recompute gaps — should resolve
echo "--- Step 5: Recompute gaps (expect 0 gaps, 1 covered) ---"
GAPS2=$(curl -sf "$BASE/protocol/pools/$POOL_RID/gaps")
echo "$GAPS2" | python3 -m json.tool
echo ""

COVERED=$(echo "$GAPS2" | python3 -c "import sys,json; print(json.load(sys.stdin)['covered_count'])")
UNMET2=$(echo "$GAPS2" | python3 -c "import sys,json; print(json.load(sys.stdin)['unmet_count'])")
echo "Result: $COVERED covered, $UNMET2 unmet"
echo ""

if [ "$UNMET2" = "0" ] && [ "$COVERED" = "1" ]; then
  echo "=== DEMO PASSED: Requirement → Gap → Signal → Coverage → Resolved ==="
else
  echo "=== DEMO FAILED: Expected 0 unmet / 1 covered ==="
  exit 1
fi
