#!/bin/bash
# Wrapper for backfill_chunks_by_sensor.py invoked by the
# com.personal-koi.chunk-embedder LaunchAgent.
#
# Sources secrets from config/personal.env (POSTGRES_URL + OPENAI_API_KEY)
# without baking them into the plist file.

set -euo pipefail

REPO_DIR="/Users/darrenzal/projects/regenai/koi-processor"
SECRETS_FILE="$REPO_DIR/config/personal.env"

if [ ! -f "$SECRETS_FILE" ]; then
    echo "ERROR: missing $SECRETS_FILE (expected POSTGRES_URL + OPENAI_API_KEY)" >&2
    exit 2
fi

set -a
# shellcheck disable=SC1090
source "$SECRETS_FILE"
set +a

cd "$REPO_DIR"
exec "$REPO_DIR/venv/bin/python" "$REPO_DIR/scripts/backfill_chunks_by_sensor.py" \
    --all-pending \
    --limit 500 \
    "$@"
