#!/bin/bash
# Setup script for Milestone B cron jobs

echo "================================================"
echo "Milestone B: Cron Job Setup (Draft-Only Mode)"
echo "================================================"

# Check if running as appropriate user
if [[ $EUID -eq 0 ]]; then
   echo "Warning: Running as root. Cron jobs will be installed for root user."
   echo "Consider running as the application user instead."
   read -p "Continue? (y/n) " -n 1 -r
   echo
   if [[ ! $REPLY =~ ^[Yy]$ ]]; then
       exit 1
   fi
fi

CRONTAB_FILE="/opt/projects/koi-processor/config/milestone_b_crontab"
PROCESSOR_DIR="/opt/projects/koi-processor"

# Create necessary directories
echo "Creating directories..."
mkdir -p "$PROCESSOR_DIR/logs/scheduled"
mkdir -p "$PROCESSOR_DIR/output/drafts"
mkdir -p "$PROCESSOR_DIR/output/web"

# Make scripts executable
echo "Setting script permissions..."
chmod +x "$PROCESSOR_DIR/scripts/scheduled_draft_generator.sh"
chmod +x "$PROCESSOR_DIR/scripts/run_daily_curator.py"
chmod +x "$PROCESSOR_DIR/scripts/run_weekly_aggregator.py"

# Backup existing crontab
echo "Backing up existing crontab..."
crontab -l > "$PROCESSOR_DIR/config/crontab_backup_$(date +%Y%m%d_%H%M%S).txt" 2>/dev/null || true

# Check system timezone
echo ""
echo "Current system time: $(date)"
echo "System timezone: $(timedatectl | grep -i 'time zone' || echo 'Unable to detect')"
echo ""
echo "NOTE: Cron jobs are configured for ET (Eastern Time)."
echo "If your server is not in ET, you may need to adjust the times."
echo ""

# Display what will be installed
echo "The following cron jobs will be installed:"
echo "-------------------------------------------"
cat "$CRONTAB_FILE" | grep -E "^[0-9]" | while read -r line; do
    echo "  $line"
done
echo ""

# Ask for confirmation
read -p "Install these cron jobs? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cron job installation cancelled."
    exit 0
fi

# Install cron jobs
echo "Installing cron jobs..."

# Get existing crontab (excluding our Milestone B jobs to avoid duplicates)
(crontab -l 2>/dev/null | grep -v "milestone_b\|Regen Daily\|Regen Weekly\|scheduled_draft_generator.sh" || true) > /tmp/current_cron

# Add our Milestone B jobs
cat "$CRONTAB_FILE" >> /tmp/current_cron

# Install the new crontab
crontab /tmp/current_cron
rm /tmp/current_cron

echo "✅ Cron jobs installed successfully!"
echo ""

# Verify installation
echo "Current cron jobs for Milestone B:"
echo "-----------------------------------"
crontab -l | grep -E "scheduled_draft_generator.sh" || echo "No Milestone B jobs found"

echo ""
echo "================================================"
echo "Setup Complete!"
echo "================================================"
echo ""
echo "📝 IMPORTANT NOTES:"
echo ""
echo "1. DRAFT-ONLY MODE: All generated content will be saved as drafts"
echo "   - Daily drafts: $PROCESSOR_DIR/output/drafts/daily_draft_*.json"
echo "   - Weekly drafts: $PROCESSOR_DIR/output/drafts/weekly_draft_*.json"
echo ""
echo "2. SCHEDULE:"
echo "   - Daily: 12:00 ET on weekdays (Mon-Fri)"
echo "   - Weekly: 10:00 ET on Fridays"
echo ""
echo "3. MANUAL GENERATION: You can still generate drafts manually:"
echo "   - Daily: python3 $PROCESSOR_DIR/scripts/run_daily_curator.py"
echo "   - Weekly: python3 $PROCESSOR_DIR/scripts/run_weekly_aggregator.py"
echo ""
echo "4. VIEW LOGS:"
echo "   - Cron log: tail -f $PROCESSOR_DIR/logs/cron.log"
echo "   - Daily logs: ls $PROCESSOR_DIR/logs/scheduled/daily_*.log"
echo "   - Weekly logs: ls $PROCESSOR_DIR/logs/scheduled/weekly_*.log"
echo ""
echo "5. MANAGE CRON JOBS:"
echo "   - View: crontab -l"
echo "   - Edit: crontab -e"
echo "   - Remove: crontab -l | grep -v 'scheduled_draft_generator' | crontab -"
echo ""
echo "6. WEB INTERFACE:"
echo "   - Access drafts at: https://regen.gaiaai.xyz/digests/"
echo "   - Latest daily: $PROCESSOR_DIR/output/web/daily_latest.json"
echo "   - Latest weekly: $PROCESSOR_DIR/output/web/weekly_latest.json"
echo ""