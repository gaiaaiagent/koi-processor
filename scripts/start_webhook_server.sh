#!/bin/bash
cd /opt/projects/koi-processor
source .venv/bin/activate
set -a && source .env && set +a

# Optional: Set webhook secret for security
# export GITHUB_WEBHOOK_SECRET="your-secret-here"

exec python api/github_webhook.py
