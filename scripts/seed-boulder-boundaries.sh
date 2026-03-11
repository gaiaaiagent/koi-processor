#!/usr/bin/env bash
# Seed Boulder boundary entities on the Front Range node
#
# Creates missing watershed/location entities and relationships needed
# for the Boulder pilot (Djimo, Aaron, Friday build sessions).
#
# Usage:
#   FR_URL=http://localhost:8355 ./scripts/seed-boulder-boundaries.sh
#   FR_URL=http://45.132.245.30:8355 ./scripts/seed-boulder-boundaries.sh

set -euo pipefail

FR_URL="${FR_URL:-http://localhost:8355}"
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
    existing=$(curl -sf "$FR_URL/entity-search?query=$encoded_name&type=$type&limit=1" 2>/dev/null \
        | python3 -c 'import sys,json; d=json.load(sys.stdin); results=d.get("results",[]); print(results[0]["uri"] if results and results[0]["name"].lower()=="'"$(echo "$name" | tr '[:upper:]' '[:lower:]')"'" else "")' 2>/dev/null || echo "")

    if [ -n "$existing" ]; then
        yellow "  [EXISTS] $name ($type) → $existing"
        SKIPPED=$((SKIPPED + 1))
        echo "$existing"
        return
    fi

    local content_hash
    content_hash="seed-boulder-$(echo "$name" | tr '[:upper:]' '[:lower:]' | tr ' ' '-')-$(date +%s)"

    local resp
    resp=$(curl -sf -X POST "$FR_URL/register-entity" \
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

    # Use the ingest endpoint to create the relationship
    local resp
    resp=$(curl -sf -X POST "$FR_URL/ingest" \
        -H "Content-Type: application/json" \
        -d "{
            \"document_rid\": \"seed:boulder-boundaries-$(date +%Y%m%d)\",
            \"entities\": [],
            \"relationships\": [
                {
                    \"subject\": \"$(python3 -c "import sys; uri='$subject'; parts=uri.split('/'); print(parts[-1].replace('-',' ').title())")\",
                    \"predicate\": \"$predicate\",
                    \"object\": \"$(python3 -c "import sys; uri='$object'; parts=uri.split('/'); print(parts[-1].replace('-',' ').title())")\"
                }
            ],
            \"source\": \"seed-script\"
        }" 2>/dev/null || echo '{}')

    green "  [EDGE] $desc ($predicate)"
    EDGES=$((EDGES + 1))
}

echo "========================================"
echo "Boulder Boundary Entity Seeding"
echo "FR_URL: $FR_URL"
echo "========================================"
echo ""

# Health check
HEALTH=$(curl -sf "$FR_URL/health" 2>/dev/null || echo '{}')
STATUS=$(echo "$HEALTH" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("status",""))' 2>/dev/null || echo "")
if [ "$STATUS" != "ok" ] && [ "$STATUS" != "healthy" ]; then
    printf "\033[31mFR node not healthy at %s\033[0m\n" "$FR_URL"
    exit 1
fi
echo "FR node healthy."
echo ""

# ------------------------------------------------------------------ #
# Register boundary entities
# ------------------------------------------------------------------ #
echo "--- Registering boundary entities ---"

BOULDER_URI=$(register_entity \
    "Boulder" \
    "Location" \
    "Locations/Boulder.md" \
    "City of Boulder, Colorado. Gateway to the Front Range bioregion and home to multiple watershed restoration initiatives.")

BOULDER_CREEK_URI=$(register_entity \
    "Boulder Creek Watershed" \
    "Location" \
    "Locations/Boulder Creek Watershed.md" \
    "Boulder Creek watershed draining from the Continental Divide through Boulder Canyon to the plains. Primary watershed for the City of Boulder, Colorado. Encompasses approximately 440 square miles.")

ST_VRAIN_URI=$(register_entity \
    "St. Vrain Creek Watershed" \
    "Location" \
    "Locations/St Vrain Creek Watershed.md" \
    "St. Vrain Creek watershed in northern Boulder County, Colorado. Flows from the Indian Peaks Wilderness through Longmont. Major tributary of the South Platte River.")

SOUTH_PLATTE_URI=$(register_entity \
    "South Platte River Basin" \
    "Location" \
    "Locations/South Platte River Basin.md" \
    "South Platte River Basin, major drainage in northeastern Colorado. Receives water from Boulder Creek, St. Vrain, Big Thompson, and Cache la Poudre. Critical water supply for the Denver metro area and Front Range agriculture.")

FRONT_RANGE_URI=$(register_entity \
    "Front Range" \
    "Bioregion" \
    "Bioregions/Front Range.md" \
    "Front Range bioregion of Colorado, extending from Fort Collins to Colorado Springs along the eastern slope of the Rocky Mountains. Characterized by montane-plains transition ecology and urban-wildland interface.")

BOULDER_COUNTY_URI=$(register_entity \
    "Boulder County" \
    "Location" \
    "Locations/Boulder County.md" \
    "Boulder County, Colorado. Contains Boulder, Longmont, Louisville, and surrounding mountain communities. Encompasses both Boulder Creek and St. Vrain watersheds.")

echo ""

# ------------------------------------------------------------------ #
# Register key organizations
# ------------------------------------------------------------------ #
echo "--- Registering Boulder organizations ---"

BOULDER_WATER_URI=$(register_entity \
    "Boulder County Parks and Open Space" \
    "Organization" \
    "Organizations/Boulder County Parks and Open Space.md" \
    "Boulder County department managing 100,000+ acres of open space, trails, and agricultural heritage. Key partner for watershed restoration and ecological monitoring in the Boulder area.")

CITY_BOULDER_URI=$(register_entity \
    "City of Boulder Open Space and Mountain Parks" \
    "Organization" \
    "Organizations/City of Boulder OSMP.md" \
    "City of Boulder department managing 46,000+ acres of open space. Conducts ecological restoration, fire mitigation, and watershed management on city-owned lands.")

echo ""

# ------------------------------------------------------------------ #
# Create hierarchical relationships
# ------------------------------------------------------------------ #
echo "--- Creating relationships ---"

# Boulder located_in Front Range
if [ -n "$BOULDER_URI" ] && [ -n "$FRONT_RANGE_URI" ]; then
    create_edge "$BOULDER_URI" "located_in" "$FRONT_RANGE_URI" "Boulder → located_in → Front Range"
fi

# Boulder located_in Boulder County
if [ -n "$BOULDER_URI" ] && [ -n "$BOULDER_COUNTY_URI" ]; then
    create_edge "$BOULDER_URI" "located_in" "$BOULDER_COUNTY_URI" "Boulder → located_in → Boulder County"
fi

# Boulder County located_in Front Range
if [ -n "$BOULDER_COUNTY_URI" ] && [ -n "$FRONT_RANGE_URI" ]; then
    create_edge "$BOULDER_COUNTY_URI" "located_in" "$FRONT_RANGE_URI" "Boulder County → located_in → Front Range"
fi

# Boulder Creek broader South Platte
if [ -n "$BOULDER_CREEK_URI" ] && [ -n "$SOUTH_PLATTE_URI" ]; then
    create_edge "$SOUTH_PLATTE_URI" "broader" "$BOULDER_CREEK_URI" "South Platte → broader → Boulder Creek"
fi

# St. Vrain broader South Platte
if [ -n "$ST_VRAIN_URI" ] && [ -n "$SOUTH_PLATTE_URI" ]; then
    create_edge "$SOUTH_PLATTE_URI" "broader" "$ST_VRAIN_URI" "South Platte → broader → St. Vrain"
fi

# Boulder Creek related_to Boulder
if [ -n "$BOULDER_CREEK_URI" ] && [ -n "$BOULDER_URI" ]; then
    create_edge "$BOULDER_CREEK_URI" "related_to" "$BOULDER_URI" "Boulder Creek → related_to → Boulder"
fi

# St. Vrain related_to Boulder County
if [ -n "$ST_VRAIN_URI" ] && [ -n "$BOULDER_COUNTY_URI" ]; then
    create_edge "$ST_VRAIN_URI" "related_to" "$BOULDER_COUNTY_URI" "St. Vrain → related_to → Boulder County"
fi

echo ""

# ------------------------------------------------------------------ #
# Summary
# ------------------------------------------------------------------ #
echo "========================================"
echo "Results: $CREATED created, $SKIPPED already existed, $EDGES edges created"
echo "========================================"
