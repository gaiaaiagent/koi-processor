#!/bin/bash
# Scheduled Draft Generator for Milestone B
# Runs daily and weekly content generation in DRAFT-ONLY mode

# Load environment variables
source /opt/projects/koi-sensors/.env 2>/dev/null || true
source /opt/projects/koi-processor/.env 2>/dev/null || true

# Set paths
PROCESSOR_DIR="/opt/projects/koi-processor"
LOG_DIR="$PROCESSOR_DIR/logs/scheduled"
DRAFTS_DIR="$PROCESSOR_DIR/output/drafts"

# Create directories if they don't exist
mkdir -p "$LOG_DIR"
mkdir -p "$DRAFTS_DIR"

# Timestamp for logs
TIMESTAMP=$(date '+%Y-%m-%d_%H-%M-%S')

# Function to run daily curator
run_daily_curator() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Running Daily Curator (Draft Mode)..."

    cd "$PROCESSOR_DIR"

    # Activate virtual environment if available
    if [ -f "venv/bin/activate" ]; then
        source venv/bin/activate
    fi

    # Run the daily curator and save to drafts
    python3 scripts/run_daily_curator.py daily \
        --output "$DRAFTS_DIR/daily_draft_${TIMESTAMP}.json" \
        2>&1 | tee -a "$LOG_DIR/daily_curator_${TIMESTAMP}.log"

    if [ $? -eq 0 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Daily draft generated successfully"

        # Also save to the web interface location
        cp "$DRAFTS_DIR/daily_draft_${TIMESTAMP}.json" \
           "$PROCESSOR_DIR/output/web/daily_latest.json" 2>/dev/null || true
    else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Daily draft generation failed"
    fi
}

# Function to run weekly aggregator
run_weekly_aggregator() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Running Weekly Aggregator (Draft Mode)..."

    cd "$PROCESSOR_DIR"

    # Activate virtual environment if available
    if [ -f "venv/bin/activate" ]; then
        source venv/bin/activate
    fi

    # Run the weekly aggregator and save to drafts
    python3 scripts/run_weekly_aggregator.py weekly \
        --output "$DRAFTS_DIR/weekly_draft_${TIMESTAMP}.json" \
        2>&1 | tee -a "$LOG_DIR/weekly_aggregator_${TIMESTAMP}.log"

    if [ $? -eq 0 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Weekly draft generated successfully"

        # Generate podcast audio for the weekly digest
        python3 scripts/generate_podcast.py \
            --input "$DRAFTS_DIR/weekly_draft_${TIMESTAMP}.json" \
            --output "$DRAFTS_DIR/weekly_podcast_${TIMESTAMP}.mp3" \
            2>&1 | tee -a "$LOG_DIR/weekly_podcast_${TIMESTAMP}.log"

        # Save to web interface location
        cp "$DRAFTS_DIR/weekly_draft_${TIMESTAMP}.json" \
           "$PROCESSOR_DIR/output/web/weekly_latest.json" 2>/dev/null || true
    else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Weekly draft generation failed"
    fi
}

# Main execution based on argument
case "$1" in
    daily)
        run_daily_curator
        ;;
    weekly)
        run_weekly_aggregator
        ;;
    both)
        run_daily_curator
        run_weekly_aggregator
        ;;
    *)
        echo "Usage: $0 {daily|weekly|both}"
        echo "  daily  - Generate daily thread draft"
        echo "  weekly - Generate weekly digest draft"
        echo "  both   - Generate both drafts"
        exit 1
        ;;
esac

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Draft generation complete"