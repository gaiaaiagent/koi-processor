#!/bin/bash
# Wrapper for com.personal-koi.website-sensor launchd agent.
# Sources personal.env (vars are NOT exported there, so use `set -a`) for
# POSTGRES_URL + OPENAI_API_KEY + extractor settings, then runs the website
# sensor with the koi venv. Pass-through args: --dry-run, --archive-only,
# --site <id>, --max-ingest N, --status.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1   # -> koi-processor repo root
set -a
# shellcheck disable=SC1091
source config/personal.env
set +a
mkdir -p logs
exec venv/bin/python scripts/website_sensor.py "$@"
