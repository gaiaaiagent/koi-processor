#!/usr/bin/env bash
# Seed Victoria Landscape Hub entities on the local/GV node
#
# Creates Victoria Landscape Hub (Organization) and Regenerate Cascadia
# (Organization) with affiliated_with relationship.
#
# Usage:
#   KOI_URL=http://localhost:8351 ./scripts/seed-victoria-landscape-hub.sh
#   KOI_URL=http://37.27.48.12:8351 ./scripts/seed-victoria-landscape-hub.sh

set -euo pipefail

KOI_URL="${KOI_URL:-http://localhost:8351}"
CREATED=0
SKIPPED=0
EDGES=0

green()  { printf "\033[32m%s\033[0m\n" "$1"; }
yellow() { printf "\033[33m%s\033[0m\n" "$1"; }

register_entity() {
    local name="$1" type="$2" vault_path="$3" description="$4"
    local vault_rid="orn:obsidian.entity:${vault_path}"

    # Check if entity already exists
    local encoded_name
    encoded_name=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$name'))")
    local existing
    existing=$(curl -sf "$KOI_URL/entity-search?query=$encoded_name&type=$type&limit=1" 2>/dev/null \
        | python3 -c 'import sys,json; d=json.load(sys.stdin); results=d.get("results",[]); print(results[0]["uri"] if results and results[0]["name"].lower()=="'"$(echo "$name" | tr '[:upper:]' '[:lower:]')"'" else "")' 2>/dev/null || echo "")

    if [ -n "$existing" ]; then
        yellow "  [EXISTS] $name ($type) → $existing"
        SKIPPED=$((SKIPPED + 1))
        echo "$existing"
        return
    fi

    local content_hash
    content_hash="seed-victoria-$(echo "$name" | tr '[:upper:]' '[:lower:]' | tr ' ' '-')-$(date +%s)"

    local resp
    resp=$(curl -sf -X POST "$KOI_URL/register-entity" \
        -H "Content-Type: application/json" \
        -d "{
            \"vault_rid\": \"$vault_rid\",
            \"vault_path\": \"$vault_path\",
            \"entity_type\": \"$type\",
            \"name\": \"$name\",
            \"content_hash\": \"$content_hash\",
            \"visibility_scope\": \"public\",
            \"frontmatter\": {
                \"@type\": \"$type\",
                \"name\": \"$name\",
                \"description\": \"$description\"
            }
        }" 2>/dev/null || echo '{"success":false}')

    local uri
    uri=$(echo "$resp" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("canonical_uri",""))' 2>/dev/null || echo "")

    if [ -n "$uri" ]; then
        green "  [CREATED] $name ($type) → $uri"
        CREATED=$((CREATED + 1))
        echo "$uri"
    else
        printf "\033[31m  [FAILED] %s (%s)\033[0m\n" "$name" "$type"
        echo ""
    fi
}

create_edge() {
    local subject="$1" predicate="$2" object="$3" desc="$4"

    if [ -z "$subject" ] || [ -z "$object" ]; then
        yellow "  [SKIP EDGE] $desc — missing URI"
        return
    fi

    curl -sf -X POST "$KOI_URL/ingest" \
        -H "Content-Type: application/json" \
        -d "{
            \"document_rid\": \"seed:victoria-landscape-hub-$(date +%Y%m%d)\",
            \"entities\": [],
            \"relationships\": [
                {
                    \"subject\": \"$(python3 -c "import sys; uri='$subject'; parts=uri.split('/'); print(parts[-1].replace('-',' ').title())")\",
                    \"predicate\": \"$predicate\",
                    \"object\": \"$(python3 -c "import sys; uri='$object'; parts=uri.split('/'); print(parts[-1].replace('-',' ').title())")\"
                }
            ],
            \"source\": \"seed-script\"
        }" > /dev/null 2>&1 || true

    green "  [EDGE] $desc ($predicate)"
    EDGES=$((EDGES + 1))
}

echo "========================================"
echo "Victoria Landscape Hub Entity Seeding"
echo "KOI_URL: $KOI_URL"
echo "========================================"
echo ""

# Health check
HEALTH=$(curl -sf "$KOI_URL/health" 2>/dev/null || echo '{}')
STATUS=$(echo "$HEALTH" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("status",""))' 2>/dev/null || echo "")
if [ "$STATUS" != "ok" ] && [ "$STATUS" != "healthy" ]; then
    printf "\033[31mNode not healthy at %s\033[0m\n" "$KOI_URL"
    exit 1
fi
echo "Node healthy."
echo ""

# ------------------------------------------------------------------ #
# Register organizations
# ------------------------------------------------------------------ #
echo "--- Registering organizations ---"

VICTORIA_HUB_URI=$(register_entity \
    "Victoria Landscape Hub" \
    "Organization" \
    "Organizations/Victoria Landscape Hub.md" \
    "Victoria Landscape Hub in the Regenerate Cascadia Hub Cultivator program. One of up to 10 landscape groups across the Cascadia bioregion. Stewards place-based regeneration strategies, bioregional mapping, and community funding flows for the Greater Victoria area.")

REGEN_CASCADIA_URI=$(register_entity \
    "Regenerate Cascadia" \
    "Organization" \
    "Organizations/Regenerate Cascadia.md" \
    "Bioregional organizing body for the Cascadia bioregion. Runs the Landscape Hub Cultivator program supporting up to 10 landscape groups. Develops BioFi (bioregional finance) infrastructure and participatory governance structures for bioregional regeneration.")

KINSHIP_EARTH_URI=$(register_entity \
    "Kinship Earth" \
    "Organization" \
    "Organizations/Kinship Earth.md" \
    "Organization behind Bioregional Earth and the Earth Regeneration Fund. Launched flow funding in September 2024 across six pilot bioregions: Barichara, Cascadia, Forests of the NE, Greater Tkaronto, Northern Andes, and Ogallala. Fundraises to resource bioregional core teams.")

MYCOPUNKS_URI=$(register_entity \
    "Mycopunks" \
    "Organization" \
    "Organizations/Mycopunks.md" \
    "Collective experimenting with Threshold-Based Flow Funding (TBFF) — algorithmic capital redistribution where overflow above individual thresholds flows to allocation preferences. Developing the tbff-protocol on Base/Superfluid.")

echo ""

# ------------------------------------------------------------------ #
# Register key concepts
# ------------------------------------------------------------------ #
echo "--- Registering concepts ---"

FLOW_FUNDING_URI=$(register_entity \
    "Flow Funding" \
    "Concept" \
    "Concepts/Flow Funding.md" \
    "Trust-based philanthropic model where capital flows through community like water through a watershed. Funder provides capital to trusted Hub Cultivator who distributes smaller grants to ground-level projects. Minimal reporting — 4 reflective questions annually.")

HUB_CULTIVATOR_URI=$(register_entity \
    "Hub Cultivator" \
    "Concept" \
    "Concepts/Hub Cultivator.md" \
    "Role in the Regenerate Cascadia Landscape Hub Cultivator program. A trusted community member who allocates flow funding to local projects based on landscape knowledge and relational trust. Two-phase program: strategy development then core team building.")

TBFF_URI=$(register_entity \
    "Threshold-Based Flow Funding" \
    "Concept" \
    "Concepts/Threshold-Based Flow Funding.md" \
    "Algorithmic flow funding mechanism. Each participant sets a maximum threshold; overflow above threshold redistributes according to weighted allocation preferences. Equation: x' = min(x, t) + P^T * max(0, x - t). Converges in 1-4 iterations. On-chain via Superfluid CFA streaming on Base.")

echo ""

# ------------------------------------------------------------------ #
# Register bioregion
# ------------------------------------------------------------------ #
echo "--- Registering bioregion ---"

CASCADIA_URI=$(register_entity \
    "Cascadia" \
    "Bioregion" \
    "Bioregions/Cascadia.md" \
    "Cascadia bioregion spanning the Pacific Northwest from northern California through British Columbia. One of six pilot bioregions in Kinship Earth's flow funding program. Home to Regenerate Cascadia and the Landscape Hub Cultivator program.")

VICTORIA_URI=$(register_entity \
    "Greater Victoria" \
    "Location" \
    "Locations/Greater Victoria.md" \
    "Greater Victoria area on southern Vancouver Island, British Columbia. Part of the Cascadia bioregion and the Salish Sea watershed. Home to the Victoria Landscape Hub in the Hub Cultivator program.")

echo ""

# ------------------------------------------------------------------ #
# Create relationships
# ------------------------------------------------------------------ #
echo "--- Creating relationships ---"

# Victoria Landscape Hub affiliated_with Regenerate Cascadia
if [ -n "$VICTORIA_HUB_URI" ] && [ -n "$REGEN_CASCADIA_URI" ]; then
    create_edge "$VICTORIA_HUB_URI" "affiliated_with" "$REGEN_CASCADIA_URI" \
        "Victoria Landscape Hub → affiliated_with → Regenerate Cascadia"
fi

# Regenerate Cascadia affiliated_with Kinship Earth
if [ -n "$REGEN_CASCADIA_URI" ] && [ -n "$KINSHIP_EARTH_URI" ]; then
    create_edge "$REGEN_CASCADIA_URI" "affiliated_with" "$KINSHIP_EARTH_URI" \
        "Regenerate Cascadia → affiliated_with → Kinship Earth"
fi

# Victoria Landscape Hub located_in Greater Victoria
if [ -n "$VICTORIA_HUB_URI" ] && [ -n "$VICTORIA_URI" ]; then
    create_edge "$VICTORIA_HUB_URI" "located_in" "$VICTORIA_URI" \
        "Victoria Landscape Hub → located_in → Greater Victoria"
fi

# Greater Victoria located_in Cascadia
if [ -n "$VICTORIA_URI" ] && [ -n "$CASCADIA_URI" ]; then
    create_edge "$VICTORIA_URI" "located_in" "$CASCADIA_URI" \
        "Greater Victoria → located_in → Cascadia"
fi

# Regenerate Cascadia located_in Cascadia
if [ -n "$REGEN_CASCADIA_URI" ] && [ -n "$CASCADIA_URI" ]; then
    create_edge "$REGEN_CASCADIA_URI" "located_in" "$CASCADIA_URI" \
        "Regenerate Cascadia → located_in → Cascadia"
fi

echo ""

# ------------------------------------------------------------------ #
# Summary
# ------------------------------------------------------------------ #
echo "========================================"
echo "Results: $CREATED created, $SKIPPED already existed, $EDGES edges created"
echo "========================================"
