#!/bin/bash
# Wrapper for backfill_chunks_by_sensor.py invoked by the
# com.personal-koi.chunk-embedder LaunchAgent.
#
# Sources secrets from config/personal.env (POSTGRES_URL + OPENAI_API_KEY)
# without baking them into the plist file.
#
# 2026-08-03: REPO_DIR used to be HARDCODED to the volatile dev checkout
# (~/projects/regenai/koi-processor). That is a working tree other sessions
# freely switch branches in, and this script + its Python target live only on
# some branches — so on 2026-07-31 a routine branch switch left them absent, the
# job exited 78 (EX_CONFIG) on every 5-minute tick, and 268 chunks across
# email-sensor and mediawiki-sensor accumulated with NO embedding at all
# (invisible to semantic search) over two days before anyone noticed.
#
# It now resolves its OWN location, so the job runs against whatever checkout the
# plist points at and cannot be orphaned by someone else's branch switch.
# Override with CHUNK_EMBEDDER_REPO_DIR to force a different tree.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${CHUNK_EMBEDDER_REPO_DIR:-$(dirname "$SCRIPT_DIR")}"
SECRETS_FILE="$REPO_DIR/config/personal.env"
PY="$REPO_DIR/venv/bin/python"
TARGET="$REPO_DIR/scripts/backfill_chunks_by_sensor.py"

# Fail with a SPECIFIC message per missing piece. The old script had a single
# generic exit 2, which is why a missing Python target surfaced only as an opaque
# launchd exit code with nothing in the log to say what was wrong.
[ -f "$SECRETS_FILE" ] || { echo "ERROR: missing $SECRETS_FILE (expected POSTGRES_URL + OPENAI_API_KEY)" >&2; exit 2; }
[ -x "$PY" ]           || { echo "ERROR: missing venv interpreter $PY" >&2; exit 3; }
[ -f "$TARGET" ]       || { echo "ERROR: missing $TARGET — this file and its wrapper must be on the DEPLOYED branch, not only a feature branch" >&2; exit 4; }

set -a
# shellcheck disable=SC1090
source "$SECRETS_FILE"
set +a

cd "$REPO_DIR"
exec "$PY" "$TARGET" \
    --all-pending \
    --limit 500 \
    "$@"
