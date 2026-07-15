#!/bin/bash
# Wrapper for com.personal-koi.substack-gmail-bridge launchd agent.
#
# Captures full-content PAID Substack posts (Will Ruddick, Michel Bauwens) that
# the API-based substack_sensor.py skips. Reads them from Gmail over IMAP and
# feeds them into the same substack-corpus pipeline. Scheduled ~07:25 — after
# substack_sensor.py (~07:15) and before the deep-extract job (~07:45), so any
# new paid post is ingested AND graphed the same morning.
#
# Sources personal.env (vars are NOT exported there, so use `set -a`) for
# OPENAI_API_KEY + POSTGRES_URL. IMAP auth reuses ~/.gmail-app-password (same
# credential mbsync uses); no browser/OAuth, so it is launchd-safe.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1   # -> koi-processor repo root
set -a
# shellcheck disable=SC1091
source config/personal.env
set +a
# Look back a few days each run so a missed run self-heals; ingest is idempotent
# (ingest_substack_corpus.py dedups by canonical slug).
exec venv/bin/python scripts/ingest_substack_from_gmail.py --apply --since-days "${1:-4}"
