#!/usr/bin/env bash
set -euo pipefail

# One-shot pipeline: export published map -> refine graph -> load into Jena
# Env:
#  POSTGRES_URL (postgresql://user:pass@host:port/db)
#  CONSOLIDATION_PATH (defaults to src/core/final_consolidation_all_t0.25.json)
#  OUTPUT_PATH (defaults to src/core/published_map.json)
#  JENA_DATA_ENDPOINT (defaults to http://localhost:3030/koi/data?default)

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CORE_DIR="$ROOT_DIR/src/core"

POSTGRES_URL="${POSTGRES_URL:-postgresql://postgres:postgres@localhost:5433/eliza}"
OUTPUT_PATH="${OUTPUT_PATH:-$CORE_DIR/published_map.json}"
CONSOLIDATION_PATH="${CONSOLIDATION_PATH:-$CORE_DIR/final_consolidation_all_t0.25.json}"
JENA_DATA_ENDPOINT="${JENA_DATA_ENDPOINT:-http://localhost:3030/koi/data?default}"

echo "📤 Exporting published_at map → $OUTPUT_PATH"
export POSTGRES_URL
export OUTPUT_PATH
node "$ROOT_DIR/scripts/export_published_map.js"

echo "🧹 Refining graph with publishedAt and consolidation"
cd "$CORE_DIR"
export CONSOLIDATION_PATH
export PUBLISHED_MAP_PATH="$OUTPUT_PATH"
python3 refine_graph.py

# Pick the latest refined_graph_*.ttl in this directory
TTL_FILE=$(ls -1t refined_graph_*.ttl | head -n1)
if [[ -z "$TTL_FILE" ]]; then
  echo "❌ No refined_graph_*.ttl produced"
  exit 1
fi
echo "📄 Latest TTL: $TTL_FILE"

echo "⬆️  Loading TTL into Jena at $JENA_DATA_ENDPOINT"
if curl -fsS -X POST "$JENA_DATA_ENDPOINT" -H 'Content-Type: text/turtle' --data-binary @"$TTL_FILE" > /dev/null; then
  echo "✅ Jena load complete"
else
  echo "⚠️  Jena load failed. Please verify endpoint and credentials, or load $TTL_FILE manually."
fi

echo "✨ Done"

