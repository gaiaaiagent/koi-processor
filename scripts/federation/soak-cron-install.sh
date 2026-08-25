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

# PROJECT_DIR is wherever this script happened to be run FROM, pinned into the cron command
# line forever after. Running it from the shared dev checkout (~/projects/RegenAI/koi-processor,
# which sessions branch-switch freely) silently baked that path into a cron job that then kept
# running whatever was left on disk there, regardless of later branch switches — discovered
# 2026-08-24 as a live, currently-running instance of exactly this. Refuse instead of silently
# reproducing it; the caller should run this from koi-processor-service (or koi-processor-runtime).
case "$PROJECT_DIR" in
    */projects/regenai/koi-processor|*/projects/RegenAI/koi-processor)
        echo "Refusing to install from the shared dev checkout: $PROJECT_DIR" >&2
        echo "Run this from ~/projects/koi-processor-service instead (see CLAUDE.md DEPLOY TOPOLOGY)." >&2
        exit 1
        ;;
esac

CRON_CMD="0 */2 * * * /bin/bash -lc 'cd ${PROJECT_DIR} && . config/personal.env && bash scripts/federation/soak-check.sh' >> /tmp/soak-cron.log 2>&1 # koi-soak"

# Check if already installed
if crontab -l 2>/dev/null | grep -q "koi-soak"; then
    echo "Soak cron already installed:"
    crontab -l | grep "koi-soak"
else
    (crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab -
    echo "Installed: $CRON_CMD"
fi
