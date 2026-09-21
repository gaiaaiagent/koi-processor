#!/usr/bin/env bash
# Shim for sensor/LaunchAgent invocation of extract_deep_sessions.py.
# Per Decision 71: sources deep_extract.env, then execs the orchestrator.
# Used by sensor subprocess invocation per Decision 75/112.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Source runtime config (DEEP_EXTRACT_CONFIRM, TELUS_API_TOKEN, etc.)
ENV_FILE="$REPO_ROOT/config/deep_extract.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "ABORT: $ENV_FILE missing" >&2
  exit 20
fi
# shellcheck source=/dev/null
source "$ENV_FILE"

# Use the regenai shared venv which has asyncpg/httpx/jsonschema installed
# (see Decision 91 — deps live in the koi-processor venv).
VENV_PY="$HOME/projects/regenai/venv/bin/python"
if [ -x "$VENV_PY" ]; then
  PY="$VENV_PY"
else
  PY="$(command -v python3)"
fi

exec "$PY" "$SCRIPT_DIR/extract_deep_sessions.py" "$@"
