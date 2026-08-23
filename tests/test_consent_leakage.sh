#!/usr/bin/env bash
# Consent Leakage Smoke Test — Data Classification Matrix 1C verification
#
# Creates a node_private Evidence entity, then verifies it does NOT appear
# on any of the 15 public endpoints that filter by node_private.
#
# Usage:
#   BASE_URL=http://localhost:8351 ./tests/test_consent_leakage.sh
#   BASE_URL=http://45.132.245.30:8351 ./tests/test_consent_leakage.sh

set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/live_write_shell_guard.sh"
live_write_begin

BASE_URL="${BASE_URL:-http://localhost:8351}"
PASS=0
FAIL=0

green()  { printf "\033[32m%s\033[0m\n" "$1"; }
red()    { printf "\033[31m%s\033[0m\n" "$1"; }

check() {
    local desc="$1" ok="$2"
    if [ "$ok" = "true" ]; then
        green "  [PASS] $desc"
        PASS=$((PASS + 1))
    else
        red "  [FAIL] $desc"
        FAIL=$((FAIL + 1))
    fi
}

echo "================================================"
echo "Consent Leakage Smoke Test — node_private filter"
echo "BASE_URL: $BASE_URL"
echo "================================================"
echo ""

# ------------------------------------------------------------------ #
# Step 0: Health check
# ------------------------------------------------------------------ #
echo "--- Step 0: Health check ---"
HEALTH=$(curl -sf "$BASE_URL/health" 2>/dev/null || echo '{}')
check "API health" "$(echo "$HEALTH" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("true" if d.get("status") in ("ok","healthy") else "false")' 2>/dev/null || echo false)"
echo ""

# ------------------------------------------------------------------ #
# Step 1: Create a node_private Evidence entity
# ------------------------------------------------------------------ #
echo "--- Step 1: Create node_private Evidence entity ---"

TS="$KOI_TEST_RUN_ID"
PRIVATE_NAME="CONSENT-LEAKAGE-TEST-$TS"
PRIVATE_VAULT_RID="orn:obsidian.entity:Evidence/consent-leakage-test-$TS"
PRIVATE_VAULT_PATH="Evidence/consent-leakage-test-$TS.md"

REGISTER_RESP=$(curl -sf -X POST "$BASE_URL/register-entity" \
    -H "Content-Type: application/json" \
    -d "{
        \"vault_rid\": \"$PRIVATE_VAULT_RID\",
        \"vault_path\": \"$PRIVATE_VAULT_PATH\",
        \"entity_type\": \"Evidence\",
        \"name\": \"$PRIVATE_NAME\",
        \"content_hash\": \"consent-leak-test-$TS\",
        \"visibility_scope\": \"node_private\",
        \"frontmatter\": {
            \"@type\": \"Evidence\",
            \"name\": \"$PRIVATE_NAME\",
            \"description\": \"This entity should NEVER appear on public endpoints\"
        }
    }" 2>/dev/null || echo '{"success":false}')

PRIVATE_URI=$(echo "$REGISTER_RESP" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("canonical_uri",""))' 2>/dev/null || echo "")
live_write_record entity_uri "$PRIVATE_URI"
check "node_private Evidence registered" "$([ -n "$PRIVATE_URI" ] && echo true || echo false)"
echo "  URI: $PRIVATE_URI"

if [ -z "$PRIVATE_URI" ]; then
    red "Cannot proceed without entity. Response: $REGISTER_RESP"
    exit 1
fi
echo ""

# Helper: check if a JSON response contains the private entity name or URI
contains_private() {
    local resp="$1"
    echo "$resp" | python3 -c "
import sys
data = sys.stdin.read()
name = '$PRIVATE_NAME'
uri = '$PRIVATE_URI'
found = name in data or uri in data
print('true' if found else 'false')
" 2>/dev/null || echo "false"
}

# ------------------------------------------------------------------ #
# Step 2: Verify entity is invisible on all public endpoints
# ------------------------------------------------------------------ #
echo "--- Step 2: Verify invisibility on public endpoints ---"

# 2a. /entity-search
SEARCH_RESP=$(curl -sf "$BASE_URL/entity-search?query=CONSENT-LEAKAGE-TEST&limit=50" 2>/dev/null || echo '{}')
LEAKED=$(contains_private "$SEARCH_RESP")
check "/entity-search: private entity NOT found" "$([ "$LEAKED" = "false" ] && echo true || echo false)"

# 2b. /entity/{uri} — should return 404 or empty for private entity
ENTITY_RESP=$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/entity/$PRIVATE_URI" 2>/dev/null || echo "000")
check "/entity/{uri}: returns 404 for private entity" "$([ "$ENTITY_RESP" = "404" ] && echo true || echo false)"

# 2c. /entities (list)
ENTITIES_RESP=$(curl -sf "$BASE_URL/entities?entity_type=Evidence&limit=500" 2>/dev/null || echo '[]')
LEAKED=$(contains_private "$ENTITIES_RESP")
check "/entities: private entity NOT in list" "$([ "$LEAKED" = "false" ] && echo true || echo false)"

# 2d. /entity/{uri}/mentioned-in
MENTION_RESP=$(curl -s "$BASE_URL/entity/$PRIVATE_URI/mentioned-in" 2>/dev/null || echo '{}')
MENTION_CODE=$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/entity/$PRIVATE_URI/mentioned-in" 2>/dev/null || echo "000")
check "/entity/{uri}/mentioned-in: rejects or 404" "$([ "$MENTION_CODE" = "404" ] || [ "$MENTION_CODE" = "403" ] && echo true || echo false)"

# 2e. /entities/mentioned-in (batch)
BATCH_RESP=$(curl -sf -X POST "$BASE_URL/entities/mentioned-in" \
    -H "Content-Type: application/json" \
    -d "{\"entity_uris\": [\"$PRIVATE_URI\"]}" 2>/dev/null || echo '{}')
LEAKED=$(contains_private "$BATCH_RESP")
check "/entities/mentioned-in: private entity filtered out" "$([ "$LEAKED" = "false" ] && echo true || echo false)"

# 2f. /stats
STATS_RESP=$(curl -sf "$BASE_URL/stats" 2>/dev/null || echo '{}')
LEAKED=$(contains_private "$STATS_RESP")
check "/stats: private entity NOT in response" "$([ "$LEAKED" = "false" ] && echo true || echo false)"

# 2g. /entity/{uri}/evidence
EV_RESP=$(curl -s "$BASE_URL/entity/$PRIVATE_URI/evidence" 2>/dev/null || echo '{}')
EV_CODE=$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/entity/$PRIVATE_URI/evidence" 2>/dev/null || echo "000")
check "/entity/{uri}/evidence: rejects or 404" "$([ "$EV_CODE" = "404" ] || [ "$EV_CODE" = "403" ] && echo true || echo false)"

# 2h. /relationships/{uri}
REL_CODE=$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/relationships/$PRIVATE_URI" 2>/dev/null || echo "000")
check "/relationships/{uri}: rejects or 404" "$([ "$REL_CODE" = "404" ] || [ "$REL_CODE" = "403" ] && echo true || echo false)"

# 2i. /vault-entities
VAULT_RESP=$(curl -sf "$BASE_URL/vault-entities?entity_type=Evidence" 2>/dev/null || echo '[]')
LEAKED=$(contains_private "$VAULT_RESP")
check "/vault-entities: private entity NOT in list" "$([ "$LEAKED" = "false" ] && echo true || echo false)"

# 2j. /vault-entity/{vault_rid}
VAULT_SINGLE_CODE=$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/vault-entity/$PRIVATE_VAULT_RID" 2>/dev/null || echo "000")
check "/vault-entity/{rid}: returns 404 for private" "$([ "$VAULT_SINGLE_CODE" = "404" ] && echo true || echo false)"

# 2k. /chat — search for the private entity name
CHAT_RESP=$(curl -sf -X POST "$BASE_URL/chat" \
    -H "Content-Type: application/json" \
    -d "{\"message\": \"Tell me about $PRIVATE_NAME\", \"session_id\": \"consent-test-$TS\"}" 2>/dev/null || echo '{}')
LEAKED=$(contains_private "$CHAT_RESP")
check "/chat: private entity NOT in retrieval context" "$([ "$LEAKED" = "false" ] && echo true || echo false)"

# 2l. /entity/resolve (GET)
RESOLVE_RESP=$(curl -sf "$BASE_URL/entity/resolve?name=CONSENT-LEAKAGE-TEST&type=Evidence" 2>/dev/null || echo '{}')
LEAKED=$(contains_private "$RESOLVE_RESP")
check "/entity/resolve (GET): private entity NOT resolved" "$([ "$LEAKED" = "false" ] && echo true || echo false)"

# 2m. /graph-version — entity count should NOT include private entity
GV_RESP=$(curl -sf "$BASE_URL/graph-version" 2>/dev/null || echo '{}')
LEAKED=$(contains_private "$GV_RESP")
check "/graph-version: private entity NOT in response" "$([ "$LEAKED" = "false" ] && echo true || echo false)"

# 2n. /web/evaluate — entity context for LLM should exclude private
WE_RESP=$(curl -s -X POST "$BASE_URL/web/evaluate" \
    -H "Content-Type: application/json" \
    -d "{\"url\": \"https://example.com\", \"title\": \"Test\", \"content\": \"$PRIVATE_NAME\"}" 2>/dev/null || echo '{}')
LEAKED=$(contains_private "$WE_RESP")
check "/web/evaluate: private entity NOT in LLM context" "$([ "$LEAKED" = "false" ] && echo true || echo false)"

# 2o. /web/process — entity context for LLM should exclude private
WP_RESP=$(curl -s -X POST "$BASE_URL/web/process" \
    -H "Content-Type: application/json" \
    -d "{\"url\": \"https://example.com\", \"title\": \"Test\", \"content\": \"$PRIVATE_NAME\", \"submission_id\": \"consent-test-$TS\"}" 2>/dev/null || echo '{}')
LEAKED=$(contains_private "$WP_RESP")
check "/web/process: private entity NOT in LLM context" "$([ "$LEAKED" = "false" ] && echo true || echo false)"

echo ""

# ------------------------------------------------------------------ #
# Step 3: Verify entity IS visible via direct DB (sanity check)
# We can't query DB directly from bash, but we can verify
# the entity was actually created by checking the register response
# ------------------------------------------------------------------ #
echo "--- Step 3: Sanity check ---"
check "Entity was actually registered (URI exists)" "$([ -n "$PRIVATE_URI" ] && echo true || echo false)"
echo ""

# ------------------------------------------------------------------ #
# Summary
# ------------------------------------------------------------------ #
echo "================================================"
TOTAL=$((PASS + FAIL))
echo "Results: $PASS passed, $FAIL failed ($TOTAL total)"
echo "Private entity URI: $PRIVATE_URI"
echo "================================================"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
