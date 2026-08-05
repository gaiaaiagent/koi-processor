#!/bin/bash
# Wrapper for com.personal-koi.research-author-sensor launchd agent.
# Sources personal.env for POSTGRES_URL and embedding credentials, then runs
# the author-paper sensor in apply mode.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1
set -a
# shellcheck disable=SC1091
source config/personal.env
set +a
exec venv/bin/python scripts/research_author_sensor.py --apply --download-pdfs "$@"
