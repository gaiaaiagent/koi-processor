#!/usr/bin/env bash
# Seed commitment pooling demo data for Celo hackathon.
#
# Creates 2 CommitmentPools and 3 Commitments via API.
# Requires Victoria Landscape Hub entities to be seeded first
# (run seed-victoria-landscape-hub.sh if not already done).
#
# Usage:
#   KOI_URL=http://localhost:8351 ./scripts/seed-commitment-demo.sh
#   KOI_URL=http://45.132.245.30:8351 ./scripts/seed-commitment-demo.sh

set -euo pipefail

KOI_URL="${KOI_URL:-http://localhost:8351}"
POOLS_CREATED=0
COMMITMENTS_CREATED=0

green()  { printf "\033[32m%s\033[0m\n" "$1"; }
yellow() { printf "\033[33m%s\033[0m\n" "$1"; }
red()    { printf "\033[31m%s\033[0m\n" "$1"; }

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
echo "Checking $KOI_URL/health ..."
if ! curl -sf "$KOI_URL/health" >/dev/null 2>&1; then
    red "Node at $KOI_URL is not reachable"
    exit 1
fi
green "Node healthy"

# ---------------------------------------------------------------------------
# Resolve entity URIs (must already exist)
# ---------------------------------------------------------------------------
resolve_entity() {
    local name="$1" type="$2"
    local encoded_name
    encoded_name=$(python3 -c "import urllib.parse; print(urllib.parse.quote('''$name'''))")
    local uri
    # Try with type filter first, then without
    for search_url in \
        "$KOI_URL/entity-search?query=$encoded_name&type=$type&limit=5" \
        "$KOI_URL/entity-search?query=$encoded_name&limit=5"; do
        uri=$(curl -sf "$search_url" 2>/dev/null \
            | python3 -c "
import sys, json
target = sys.argv[1].lower()
d = json.load(sys.stdin)
for r in d.get('results', []):
    if r['name'].lower() == target:
        print(r.get('uri') or r.get('fuseki_uri', ''))
        break
" "$name" 2>/dev/null || echo "")
        if [ -n "$uri" ]; then
            echo "$uri"
            return
        fi
    done
    echo ""
}

resolve_entity_fallback() {
    # Try multiple names in order, return first found
    local type="$1"
    shift
    for name in "$@"; do
        local uri
        uri=$(resolve_entity "$name" "$type")
        if [ -n "$uri" ]; then
            echo "$uri"
            return
        fi
    done
    echo ""
}

echo ""
echo "Resolving existing entities..."

VLH_URI=$(resolve_entity_fallback "Organization" "Victoria Landscape Hub" "Victoria Landscape Group")
RC_URI=$(resolve_entity_fallback "Organization" "Regenerate Cascadia")
KE_URI=$(resolve_entity_fallback "Organization" "Kinship Earth")
MP_URI=$(resolve_entity_fallback "Organization" "Mycopunks")
SS_URI=$(resolve_entity_fallback "Bioregion" "Salish Sea")
CASCADIA_URI=$(resolve_entity_fallback "Bioregion" "Cascadia")

# Salish Sea might not be seeded — try Location type too
if [ -z "$SS_URI" ]; then
    SS_URI=$(resolve_entity "Salish Sea" "Location")
fi

echo "  Victoria Landscape Hub: ${VLH_URI:-NOT FOUND}"
echo "  Regenerate Cascadia:    ${RC_URI:-NOT FOUND}"
echo "  Kinship Earth:          ${KE_URI:-NOT FOUND}"
echo "  Mycopunks:              ${MP_URI:-NOT FOUND}"
echo "  Salish Sea:             ${SS_URI:-NOT FOUND}"
echo "  Cascadia:               ${CASCADIA_URI:-NOT FOUND}"

if [ -z "$VLH_URI" ] || [ -z "$RC_URI" ]; then
    red "Required entities not found. Run seed-victoria-landscape-hub.sh first."
    exit 1
fi

# Use Cascadia as fallback bioregion if Salish Sea not found
BIOREGION_URI="${SS_URI:-$CASCADIA_URI}"
if [ -z "$BIOREGION_URI" ]; then
    yellow "Warning: No bioregion entity found. Pools will have empty bioregion_uri."
fi

# Ensure broader relationship between Salish Sea and Cascadia (for umbrella scoring)
if [ -n "$SS_URI" ] && [ -n "$CASCADIA_URI" ] && [ "$SS_URI" != "$CASCADIA_URI" ]; then
    echo ""
    echo "Ensuring Salish Sea → broader → Cascadia edge..."
    # Use ingest endpoint which resolves by entity name
    curl -sf -X POST "$KOI_URL/ingest" \
        -H "Content-Type: application/json" \
        -d "{
            \"document_rid\": \"seed:commitment-demo-bioregion-edges\",
            \"entities\": [],
            \"relationships\": [{
                \"subject\": \"Salish Sea\",
                \"predicate\": \"broader\",
                \"object\": \"Cascadia\"
            }],
            \"source\": \"seed-script\"
        }" >/dev/null 2>&1 && green "  Edge created via ingest" || {
        # Fallback: broader may not be in allowed_predicates on fresh installs
        yellow "  Ingest failed (broader predicate may need to be added to allowed_predicates)"
    }
fi

# ---------------------------------------------------------------------------
# Create pools
# ---------------------------------------------------------------------------
create_pool() {
    local name="$1" description="$2" steward_uri="$3" bioregion_uri="$4"
    local threshold_pct="$5" metadata="$6"

    echo ""
    echo "Creating pool: $name"

    local response
    response=$(curl -sf -X POST "$KOI_URL/pools/create" \
        -H "Content-Type: application/json" \
        -d "{
            \"name\": \"$name\",
            \"description\": \"$description\",
            \"steward_uri\": \"$steward_uri\",
            \"bioregion_uri\": \"$bioregion_uri\",
            \"activation_threshold_pct\": $threshold_pct,
            \"metadata\": $metadata,
            \"created_by\": \"seed-commitment-demo\"
        }" 2>/dev/null) || true

    if [ -n "$response" ]; then
        local rid
        rid=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('pool_rid',''))" 2>/dev/null || echo "")
        if [ -n "$rid" ]; then
            green "  Created: $rid"
            POOLS_CREATED=$((POOLS_CREATED + 1))
            echo "$rid"
            return
        fi
    fi
    yellow "  Skipped (may already exist)"
    # Try to find existing pool by listing
    local existing_rid
    existing_rid=$(curl -sf "$KOI_URL/pools/create" -X POST \
        -H "Content-Type: application/json" \
        -d "{
            \"name\": \"$name\",
            \"description\": \"$description\",
            \"steward_uri\": \"$steward_uri\",
            \"bioregion_uri\": \"$bioregion_uri\",
            \"activation_threshold_pct\": $threshold_pct,
            \"metadata\": $metadata,
            \"created_by\": \"seed-commitment-demo\"
        }" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('pool_rid',''))" 2>/dev/null || echo "")
    echo "$existing_rid"
}

echo ""
echo "=== Creating Commitment Pools ==="

POOL1_RID=$(create_pool \
    "Victoria Landscape Hub Restoration Pool" \
    "Aggregates restoration commitments for the Victoria / Salish Sea bioregion. Steward: Victoria Landscape Hub." \
    "$VLH_URI" \
    "$BIOREGION_URI" \
    80 \
    '{"need_tags": ["restoration", "native-plants", "monitoring", "soil-health", "mycoremediation"], "capacity_usd": 50000, "remaining_capacity_usd": 50000, "activation_threshold_usd": 15000}')

POOL2_RID=$(create_pool \
    "Cascadia Bioregion Stewardship Pool" \
    "Umbrella pool for stewardship commitments across the broader Cascadia bioregion." \
    "${RC_URI:-$VLH_URI}" \
    "${CASCADIA_URI:-$BIOREGION_URI}" \
    80 \
    '{"need_tags": ["stewardship", "watershed", "community", "restoration", "education"], "capacity_usd": 100000, "remaining_capacity_usd": 100000, "activation_threshold_usd": 25000}')

# ---------------------------------------------------------------------------
# Create commitments
# ---------------------------------------------------------------------------
create_commitment() {
    local pledger_uri="$1" title="$2" description="$3" offer_type="$4"
    local quantity="$5" unit="$6" start="$7" end="$8" metadata="$9"

    echo ""
    echo "Creating commitment: $title"

    local response
    response=$(curl -sf -X POST "$KOI_URL/commitments/create" \
        -H "Content-Type: application/json" \
        -d "{
            \"pledger_uri\": \"$pledger_uri\",
            \"title\": \"$title\",
            \"description\": \"$description\",
            \"offer_type\": \"$offer_type\",
            \"quantity\": $quantity,
            \"unit\": \"$unit\",
            \"validity_start\": \"${start}T00:00:00Z\",
            \"validity_end\": \"${end}T00:00:00Z\",
            \"metadata\": $metadata,
            \"created_by\": \"seed-commitment-demo\"
        }" 2>/dev/null) || true

    if [ -n "$response" ]; then
        local rid
        rid=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('commitment_rid',''))" 2>/dev/null || echo "")
        if [ -n "$rid" ]; then
            green "  Created: $rid"
            COMMITMENTS_CREATED=$((COMMITMENTS_CREATED + 1))
            echo "$rid"
            return
        fi
    fi
    yellow "  Skipped (may already exist)"
}

echo ""
echo "=== Creating Commitments ==="

# Commitment 1: Regenerate Cascadia — restoration labor
if [ -n "$RC_URI" ]; then
    C1_RID=$(create_commitment \
        "$RC_URI" \
        "Native plant restoration — 200 hours" \
        "Regenerate Cascadia offers 200 hours of native plant restoration across the Salish Sea bioregion. Includes site assessment, planting, and 6-month monitoring." \
        "stewardship" \
        200 "hours" "2026-04-01" "2026-09-30" \
        "{\"wants\": [\"soil testing equipment access\", \"volunteer coordination support\"], \"limits\": [\"max 3 concurrent restoration sites\"], \"bioregion_uri\": \"$BIOREGION_URI\", \"estimated_value_usd\": 8000, \"routing_tags\": [\"restoration\", \"native-plants\", \"labor\"]}")
fi

# Commitment 2: Kinship Earth — soil monitoring equipment
if [ -n "$KE_URI" ]; then
    C2_RID=$(create_commitment \
        "$KE_URI" \
        "Soil monitoring equipment loan — 6 months" \
        "Kinship Earth offers soil monitoring equipment (pH meters, moisture sensors, microbial test kits) on 6-month loan to restoration projects." \
        "goods" \
        1 "equipment-kit" "2026-04-01" "2026-10-31" \
        "{\"wants\": [\"restoration site access for data collection\", \"shared soil health data\"], \"limits\": [\"equipment must be returned in working condition\", \"max 2 concurrent loans\"], \"bioregion_uri\": \"$BIOREGION_URI\", \"estimated_value_usd\": 3000, \"routing_tags\": [\"monitoring\", \"equipment\", \"soil-health\"]}")
else
    yellow "Skipping Kinship Earth commitment (entity not found)"
fi

# Commitment 3: Mycopunks — mycoremediation pilot
if [ -n "$MP_URI" ]; then
    C3_RID=$(create_commitment \
        "$MP_URI" \
        "Mycoremediation pilot — 40 hours" \
        "Mycopunks offers a 40-hour mycoremediation pilot: site assessment, substrate preparation, fungal inoculation, and monitoring for one contaminated site." \
        "service" \
        40 "hours" "2026-05-01" "2026-08-31" \
        "{\"wants\": [\"contaminated site access\", \"baseline soil testing\"], \"limits\": [\"single site only\", \"requires minimum 0.5 acre area\"], \"bioregion_uri\": \"$BIOREGION_URI\", \"estimated_value_usd\": 2000, \"routing_tags\": [\"mycoremediation\", \"restoration\", \"fungi\"]}")
else
    yellow "Skipping Mycopunks commitment (entity not found)"
fi

# ---------------------------------------------------------------------------
# Test routing suggestions
# ---------------------------------------------------------------------------
echo ""
echo "=== Testing Routing Suggestions ==="

echo "Scoring Regenerate Cascadia commitment against pools..."
ROUTING=$(curl -sf -X POST "$KOI_URL/commitments/routing-suggestions" \
    -H "Content-Type: application/json" \
    -d "{
        \"pledger_uri\": \"$RC_URI\",
        \"title\": \"Native plant restoration — 200 hours\",
        \"offer_type\": \"stewardship\",
        \"quantity\": 200,
        \"unit\": \"hours\",
        \"validity_start\": \"2026-04-01T00:00:00Z\",
        \"validity_end\": \"2026-09-30T00:00:00Z\",
        \"metadata\": {
            \"wants\": [\"soil testing equipment access\"],
            \"limits\": [\"max 3 concurrent sites\"],
            \"bioregion_uri\": \"$BIOREGION_URI\",
            \"estimated_value_usd\": 8000,
            \"routing_tags\": [\"restoration\", \"native-plants\", \"labor\"]
        }
    }" 2>/dev/null) || true

if [ -n "$ROUTING" ]; then
    echo "$ROUTING" | python3 -m json.tool 2>/dev/null || echo "$ROUTING"
    SUGGESTION_COUNT=$(echo "$ROUTING" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('suggestions',[])))" 2>/dev/null || echo "0")
    green "Got $SUGGESTION_COUNT routing suggestions"
else
    red "Routing suggestions endpoint failed"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=== Summary ==="
green "Pools created:      $POOLS_CREATED"
green "Commitments created: $COMMITMENTS_CREATED"
echo ""
echo "Pool RIDs:"
echo "  Victoria:  ${POOL1_RID:-unknown}"
echo "  Cascadia:  ${POOL2_RID:-unknown}"
echo ""
echo "Next steps:"
echo "  1. Test routing: curl -X POST $KOI_URL/commitments/routing-suggestions -H 'Content-Type: application/json' -d '{...}'"
echo "  2. Approve:      curl -X PATCH $KOI_URL/commitments/<rid>/state -H 'Content-Type: application/json' -d '{\"new_state\":\"VERIFIED\",\"actor\":\"steward\"}'"
echo "  3. Pledge:       curl -X POST $KOI_URL/pools/<pool_rid>/pledge -H 'Content-Type: application/json' -d '{\"commitment_rid\":\"<rid>\"}'"
