#!/bin/zsh
# Tier-2 sustained-write nightly cron wrapper.
#
# Per plan §Step 8 Phase 3:
#   - Sources OPENAI_API_KEY from personal.env
#   - Uses graphiti-poc venv (has graphiti-core 0.29.0 + falkordb 1.6.1)
#   - Runs full batch-1 doc_kinds: decision-record + foundation + architecture
#   - Idempotent dedup ensures Step-3 + Step-5 already-ingested items skip
#   - batch_id stamped per-run for rollback scoping
#
# Cron: 0 4 * * * (04:00 daily; runs after nightly KOI sync, before morning brief)

set -e

# Source env (OPENAI_API_KEY)
set -a
source "$HOME/projects/regenai/koi-processor/config/personal.env"
set +a

BATCH_ID="tier_2_nightly_$(date -u +%Y_%m_%d)"

# Run sustained-write against batch-1 allowlist.
# group_id is hardcoded "koi_canon_v1" inside the script (production scheme).
exec "$HOME/.venv/graphiti-poc/bin/python3" \
    "$HOME/projects/regenai/koi-processor/scripts/graphiti_sustained_write.py" \
    --doc-kinds "decision-record,foundation,architecture" \
    --batch-id "$BATCH_ID"
