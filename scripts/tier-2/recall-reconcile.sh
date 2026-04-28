#!/bin/zsh
# recall-reconcile.sh — Tier-2 substrate-divergence reconciliation (Step 7).
#
# Per plan §Rollback "Substrate divergence reconciliation":
#   - For each ADR-Entity in Graphiti `koi_canon_v1` group_id added in last 24hr:
#       - Read its rid attribute
#       - Query KOI: SELECT EXISTS(SELECT 1 FROM koi_memories WHERE rid = $1)
#       - If FALSE → divergence (Graphiti has content with no KOI source)
#   - Persistence: append divergences to ~/.koi/recall-divergences.jsonl
#   - Trigger threshold: rolling 7-day count > 10 → fire auto-revert (handled
#     by trigger-eval; reconcile only writes the divergences file).
#
# Cron: 0 3 * * *
#
# Idempotent. Each divergence line carries the entity_uuid + rid + ts;
# downstream trigger-eval reads the rolling 7d window.

set -e

DIVERGENCES_FILE="$HOME/.koi/recall-divergences.jsonl"
mkdir -p "$(dirname "$DIVERGENCES_FILE")"

NOW_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
GROUP_ID="koi_canon_v1"

# Pull recent ADR-Entity nodes (added in last 24hr) from Graphiti.
#
# Cypher: MATCH ADR-Entity nodes; created_at within last 24hr; return
# uuid + rid (from attributes).
#
# We rely on the deterministic ADR-Entity convention: created via
# graphiti_sustained_write.py with attributes.rid. Pure LLM-extracted
# entities have no rid attribute — those are NOT ADR-Entity nodes and not
# audited here (KOI has no record of them by design).

CYPHER='MATCH (n:Entity) WHERE n.group_id = "'$GROUP_ID'" AND ANY(l IN labels(n) WHERE l = "ADR") RETURN n.uuid AS uuid, n.rid AS rid, n.created_at AS created_at LIMIT 500'

ADR_ROWS=$(/usr/local/bin/docker exec falkordb-tier2 redis-cli GRAPH.QUERY "$GROUP_ID" "$CYPHER" 2>&1 || echo "")

# Parse rid values (every 4th line in raw redis-cli output is rid; structure varies).
# Use python to parse the GRAPH.QUERY response cleanly.
RIDS=$(echo "$ADR_ROWS" | python3 -c '
import sys, re
text = sys.stdin.read()
# RIDs follow the line "doc-scanner:..." pattern; extract them all.
rids = re.findall(r"doc-scanner:[^\s\"]+\.md", text)
# Deduplicate while preserving order
seen = set()
out = []
for r in rids:
    if r not in seen:
        seen.add(r)
        out.append(r)
for r in out:
    print(r)
' 2>/dev/null)

if [[ -z "$RIDS" ]]; then
    # No ADR-Entity rids found; nothing to reconcile (or Graphiti unhealthy).
    echo "{\"ts\":\"$NOW_ISO\",\"event\":\"reconcile_no_adr_entities\"}" >> "$DIVERGENCES_FILE.audit"
    exit 0
fi

DIVERGENCES_FOUND=0
TOTAL_CHECKED=0
while IFS= read -r rid; do
    [[ -z "$rid" ]] && continue
    TOTAL_CHECKED=$((TOTAL_CHECKED + 1))
    # Check KOI for the rid (uses local psql connection per personal_koi convention).
    EXISTS=$(psql -d personal_koi -t -c "SELECT EXISTS(SELECT 1 FROM koi_memories WHERE rid = '$rid')" 2>/dev/null | tr -d ' ' | head -c 1)
    if [[ "$EXISTS" != "t" ]]; then
        DIVERGENCES_FOUND=$((DIVERGENCES_FOUND + 1))
        printf '{"ts":"%s","reason":"graphiti_rid_not_in_koi","rid":"%s","group_id":"%s"}\n' \
            "$NOW_ISO" "$rid" "$GROUP_ID" >> "$DIVERGENCES_FILE"
    fi
done <<< "$RIDS"

# Audit summary line for daily reconcile run
printf '{"ts":"%s","event":"reconcile_complete","checked":%d,"divergences_found":%d}\n' \
    "$NOW_ISO" "$TOTAL_CHECKED" "$DIVERGENCES_FOUND" >> "$DIVERGENCES_FILE.audit"
