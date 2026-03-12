#!/usr/bin/env bash
# Claims pipeline regression gate
# Usage: ./scripts/check_claims_regression.sh [--runs 3]
# Requires: koi-server running on :8351
# Exit 0 = pass, Exit 1 = regression detected
set -euo pipefail

RUNS="${1:-3}"
BASELINE="docs/eval/claims-baseline.json"
OUTPUT="/tmp/claims-eval-latest.json"

echo "=== Claims Regression Gate ==="
echo "Runs: $RUNS | Baseline: $BASELINE"
echo ""

# Health check — fail fast if server not running
if ! curl -sf http://localhost:8351/health > /dev/null 2>&1; then
    echo "ERROR: koi-server not reachable on :8351"
    echo "Start with: ~/.config/personal-koi/start.sh"
    exit 1
fi

# Check baseline exists
if [ ! -f "$BASELINE" ]; then
    echo "ERROR: Baseline not found at $BASELINE"
    echo "Generate one with: python -m scripts.eval_claims_pipeline --skip-anchor --runs 3 --save $BASELINE"
    exit 1
fi

# Run eval harness with comparison and latency gate
exec python -m scripts.eval_claims_pipeline \
    --skip-anchor \
    --runs "$RUNS" \
    --compare "$BASELINE" \
    --fail-on-latency-regression 2.0 \
    --save "$OUTPUT"
