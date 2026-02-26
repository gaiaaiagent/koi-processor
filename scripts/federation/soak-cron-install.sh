#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Install soak-check cron job (every 2 hours)
# ============================================================
# Runs soak-check.sh on the local Mac. The NUC peer doesn't
# need its own cron — soak-check.sh SSHes into it.
#
# Install:  bash scripts/federation/soak-cron-install.sh
# Remove:   bash scripts/federation/soak-cron-remove.sh
#           (or: crontab -e and delete the koi-soak line)
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
CRON_CMD="0 */2 * * * /bin/bash -lc 'cd ${PROJECT_DIR} && . config/personal.env && bash scripts/federation/soak-check.sh' >> /tmp/soak-cron.log 2>&1 # koi-soak"

# Check if already installed
if crontab -l 2>/dev/null | grep -q "koi-soak"; then
    echo "Soak cron already installed:"
    crontab -l | grep "koi-soak"
else
    (crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab -
    echo "Installed: $CRON_CMD"
fi
