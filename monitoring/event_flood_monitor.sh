#!/bin/bash
# Event Flood Detection Monitor
# Detects abnormally high CONTENT CHANGED event rates
# Runs every 5 minutes via systemd timer
#
# Thresholds:
#   Normal:   < 100 events/5min - No action
#   Warning:  100-500 events/5min - Log warning
#   Critical: > 500 events/5min - Send email alert
#
# Created: 2026-01-09
# Context: Response to Jan 9-10 flood incident (57,116 false events in 24h)

set -e

# Configuration
THRESHOLD_WARNING=100
THRESHOLD_CRITICAL=500
TIME_WINDOW="5 minutes ago"
LOG_FILE="/opt/projects/koi-processor/logs/event_flood.log"
ALERT_CONFIG="/opt/projects/koi-sensors/.alert-config"

# Load alert config (contains ALERT_EMAIL)
if [ -f "$ALERT_CONFIG" ]; then
    source "$ALERT_CONFIG"
fi

# Also check processor location
if [ -z "$ALERT_EMAIL" ] && [ -f /opt/projects/koi-processor/.alert-config ]; then
    source /opt/projects/koi-processor/.alert-config
fi

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
HOSTNAME=$(hostname)

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

# Count CONTENT CHANGED events in time window
# Use wc -l for more reliable counting, strip whitespace
EVENT_COUNT=$(sudo journalctl -u koi-coordinator --since "$TIME_WINDOW" --no-pager 2>/dev/null | grep "CONTENT CHANGED" | wc -l | tr -d ' ')
EVENT_COUNT=${EVENT_COUNT:-0}

# Get top event sources for diagnostics
TOP_SOURCES=$(sudo journalctl -u koi-coordinator --since "$TIME_WINDOW" --no-pager 2>/dev/null | grep "CONTENT CHANGED" | grep -oE 'ORN:[^,[:space:]]+|regen\.[a-z_]+:' | sort | uniq -c | sort -rn | head -5 || echo "none")

echo "[$TIMESTAMP] Event count: $EVENT_COUNT (5 min window)" >> "$LOG_FILE"

# Check thresholds
if [ "$EVENT_COUNT" -gt "$THRESHOLD_CRITICAL" ]; then
    SEVERITY="critical"
    MESSAGE="CRITICAL: $EVENT_COUNT CONTENT CHANGED events in 5 minutes (threshold: $THRESHOLD_CRITICAL)"
elif [ "$EVENT_COUNT" -gt "$THRESHOLD_WARNING" ]; then
    SEVERITY="warning"
    MESSAGE="WARNING: $EVENT_COUNT CONTENT CHANGED events in 5 minutes (threshold: $THRESHOLD_WARNING)"
else
    echo "[$TIMESTAMP] Event rate normal ($EVENT_COUNT events)" >> "$LOG_FILE"
    exit 0
fi

# Log alert
echo "[$TIMESTAMP] ALERT [$SEVERITY]: $MESSAGE" >> "$LOG_FILE"

# Send email if configured
if command -v msmtp &> /dev/null && [ -n "$ALERT_EMAIL" ]; then
    cat << EOF | msmtp "$ALERT_EMAIL"
Subject: [KOI ALERT] Event Flood [$SEVERITY] on $HOSTNAME
From: zaldarren@gmail.com
To: $ALERT_EMAIL

========================================
   KOI EVENT FLOOD ALERT
========================================

Severity:   $SEVERITY
Time:       $TIMESTAMP
Host:       $HOSTNAME

$MESSAGE

Normal rate: < $THRESHOLD_WARNING events/5min
Current:     $EVENT_COUNT events/5min

----------------------------------------
TOP EVENT SOURCES:
----------------------------------------
$TOP_SOURCES

----------------------------------------
RECOMMENDED ACTIONS:
----------------------------------------
1. Check which sensor is flooding:
   sudo journalctl -u koi-coordinator --since '10 minutes ago' | grep 'CONTENT CHANGED' | grep -oE 'ORN:[^,[:space:]]+|regen\.[a-z_]+:' | sort | uniq -c | sort -rn

2. Check sensor status:
   for s in github github_activity discourse websites; do
     echo "=== \$s ==="
     sudo systemctl status koi-sensor@\$s | head -5
   done

3. If a sensor is flooding, stop it:
   sudo systemctl stop koi-sensor@<sensor_name>

4. Check coordinator logs for patterns:
   sudo journalctl -u koi-coordinator --since '30 minutes ago' -f

----------------------------------------
This is an automated alert from KOI Pipeline Monitor
EOF
    echo "[$TIMESTAMP] Alert email sent to $ALERT_EMAIL" >> "$LOG_FILE"
else
    echo "[$TIMESTAMP] No email configured (ALERT_EMAIL not set or msmtp unavailable)" >> "$LOG_FILE"
fi
