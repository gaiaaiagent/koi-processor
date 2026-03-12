#!/usr/bin/env bash
# Log a Hub Cultivator flow funding decision as Evidence in the knowledge graph
#
# Creates an Evidence entity + related Project/Organization entities via /ingest
# with Hub Cultivator conventions. Returns a real CAT receipt_id for provenance.
#
# Usage:
#   ./scripts/log-hub-decision.sh \
#       --steward "Darren Zal" \
#       --recipient "Gorge Tillicum Community Garden" \
#       --recipient-type "Project" \
#       --amount "500" \
#       --currency "CAD" \
#       --rationale "Community food sovereignty initiative supporting Indigenous land stewardship" \
#       --bioregion "Greater Victoria" \
#       --landscape-hub "Victoria Landscape Hub"
#
#   KOI_URL=http://localhost:8351 ./scripts/log-hub-decision.sh ...
#
# Optional flags:
#   --parent-receipt <receipt_id>   Chain to a parent receipt for multi-step provenance
#   --dry-run                      Print the payload without sending

set -euo pipefail

KOI_URL="${KOI_URL:-http://localhost:8351}"

# Defaults
STEWARD=""
RECIPIENT=""
RECIPIENT_TYPE="Project"
AMOUNT=""
CURRENCY="CAD"
RATIONALE=""
BIOREGION=""
LANDSCAPE_HUB=""
PARENT_RECEIPT=""
DRY_RUN=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --steward)       STEWARD="$2"; shift 2 ;;
        --recipient)     RECIPIENT="$2"; shift 2 ;;
        --recipient-type) RECIPIENT_TYPE="$2"; shift 2 ;;
        --amount)        AMOUNT="$2"; shift 2 ;;
        --currency)      CURRENCY="$2"; shift 2 ;;
        --rationale)     RATIONALE="$2"; shift 2 ;;
        --bioregion)     BIOREGION="$2"; shift 2 ;;
        --landscape-hub) LANDSCAPE_HUB="$2"; shift 2 ;;
        --parent-receipt) PARENT_RECEIPT="$2"; shift 2 ;;
        --dry-run)       DRY_RUN=true; shift ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

# Validate required fields
if [ -z "$STEWARD" ] || [ -z "$RECIPIENT" ] || [ -z "$AMOUNT" ] || [ -z "$RATIONALE" ]; then
    echo "Required: --steward, --recipient, --amount, --rationale"
    echo "Optional: --recipient-type (default: Project), --currency (default: CAD),"
    echo "          --bioregion, --landscape-hub, --parent-receipt, --dry-run"
    exit 1
fi

# Generate deterministic document_rid
SLUG=$(echo "$RECIPIENT" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd '[:alnum:]-')
DATE=$(date +%Y-%m-%d)
DOCUMENT_RID="hub-cultivator:${DATE}:${SLUG}"

# Build evidence description
EVIDENCE_DESC="Hub Cultivator flow funding decision: ${STEWARD} allocated \$${AMOUNT} ${CURRENCY} to ${RECIPIENT}. Rationale: ${RATIONALE}"
if [ -n "$LANDSCAPE_HUB" ]; then
    EVIDENCE_DESC="${EVIDENCE_DESC}. Via ${LANDSCAPE_HUB}."
fi

# Build entities array
ENTITIES='['
# Evidence entity
ENTITIES="${ENTITIES}{\"name\": \"Hub Cultivator Decision — ${RECIPIENT} — ${DATE}\", \"type\": \"Evidence\", \"context\": \"${EVIDENCE_DESC}\"}"
# Steward (Person)
ENTITIES="${ENTITIES}, {\"name\": \"${STEWARD}\", \"type\": \"Person\", \"context\": \"Hub Cultivator steward in the Regenerate Cascadia Landscape Hub Cultivator program\"}"
# Recipient
ENTITIES="${ENTITIES}, {\"name\": \"${RECIPIENT}\", \"type\": \"${RECIPIENT_TYPE}\", \"context\": \"Recipient of Hub Cultivator flow funding grant of \$${AMOUNT} ${CURRENCY}\"}"
# Landscape Hub (if provided)
if [ -n "$LANDSCAPE_HUB" ]; then
    ENTITIES="${ENTITIES}, {\"name\": \"${LANDSCAPE_HUB}\", \"type\": \"Organization\", \"context\": \"Landscape Hub in the Regenerate Cascadia Hub Cultivator program\"}"
fi
ENTITIES="${ENTITIES}]"

# Build relationships array
RELATIONSHIPS='['
# Evidence documents recipient
RELATIONSHIPS="${RELATIONSHIPS}{\"subject\": \"Hub Cultivator Decision — ${RECIPIENT} — ${DATE}\", \"predicate\": \"documents\", \"object\": \"${RECIPIENT}\"}"
# Evidence informs steward decision-making
RELATIONSHIPS="${RELATIONSHIPS}, {\"subject\": \"Hub Cultivator Decision — ${RECIPIENT} — ${DATE}\", \"predicate\": \"informs\", \"object\": \"${STEWARD}\"}"
# Landscape Hub relationship
if [ -n "$LANDSCAPE_HUB" ]; then
    RELATIONSHIPS="${RELATIONSHIPS}, {\"subject\": \"${STEWARD}\", \"predicate\": \"affiliated_with\", \"object\": \"${LANDSCAPE_HUB}\"}"
fi
RELATIONSHIPS="${RELATIONSHIPS}]"

# Build parent_receipt_id field
PARENT_FIELD=""
if [ -n "$PARENT_RECEIPT" ]; then
    PARENT_FIELD="\"parent_receipt_id\": \"${PARENT_RECEIPT}\","
fi

# Assemble full payload
PAYLOAD=$(cat <<JSONEOF
{
    "document_rid": "${DOCUMENT_RID}",
    "entities": ${ENTITIES},
    "relationships": ${RELATIONSHIPS},
    "source": "hub-cultivator",
    ${PARENT_FIELD}
    "context": {
        "associated_people": ["${STEWARD}"],
        "organizations": [$([ -n "$LANDSCAPE_HUB" ] && echo "\"${LANDSCAPE_HUB}\"" || echo "")],
        "topics": ["flow funding", "Hub Cultivator", "bioregional funding"]
    }
}
JSONEOF
)

if [ "$DRY_RUN" = true ]; then
    echo "=== DRY RUN — would POST to ${KOI_URL}/ingest ==="
    echo "$PAYLOAD" | python3 -m json.tool
    exit 0
fi

# Health check
HEALTH=$(curl -sf "$KOI_URL/health" 2>/dev/null || echo '{}')
STATUS=$(echo "$HEALTH" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("status",""))' 2>/dev/null || echo "")
if [ "$STATUS" != "ok" ] && [ "$STATUS" != "healthy" ]; then
    printf "\033[31mNode not healthy at %s\033[0m\n" "$KOI_URL"
    exit 1
fi

# Send ingest request
echo "Logging Hub Cultivator decision..."
echo "  Steward:   ${STEWARD}"
echo "  Recipient: ${RECIPIENT} (${RECIPIENT_TYPE})"
echo "  Amount:    \$${AMOUNT} ${CURRENCY}"
echo "  Rationale: ${RATIONALE}"
echo "  Doc RID:   ${DOCUMENT_RID}"
echo ""

RESPONSE=$(curl -sf -X POST "$KOI_URL/ingest" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" 2>/dev/null || echo '{"success":false,"error":"request failed"}')

SUCCESS=$(echo "$RESPONSE" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("success",False))' 2>/dev/null || echo "False")
RECEIPT_ID=$(echo "$RESPONSE" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("receipt_id", d.get("receipt_rid","")))' 2>/dev/null || echo "")

if [ "$SUCCESS" = "True" ]; then
    printf "\033[32m✓ Decision logged successfully\033[0m\n"
    echo "  Receipt ID: ${RECEIPT_ID}"

    # Show receipt chain if available
    if [ -n "$RECEIPT_ID" ]; then
        echo ""
        echo "  Receipt chain:"
        CHAIN=$(curl -sf "$KOI_URL/receipts/${RECEIPT_ID}/chain" 2>/dev/null || echo '{}')
        CHAIN_LEN=$(echo "$CHAIN" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("chain_length",0))' 2>/dev/null || echo "0")
        echo "    Chain length: ${CHAIN_LEN}"
    fi

    # Show created entities
    echo ""
    echo "  Entities:"
    echo "$RESPONSE" | python3 -c '
import sys, json
d = json.load(sys.stdin)
for e in d.get("canonical_entities", []):
    status = "NEW" if e.get("is_new") else "RESOLVED"
    print(f"    [{status}] {e[\"name\"]} ({e[\"type\"]}) → {e[\"uri\"]}")
' 2>/dev/null || true
else
    printf "\033[31m✗ Decision logging failed\033[0m\n"
    echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
    exit 1
fi
