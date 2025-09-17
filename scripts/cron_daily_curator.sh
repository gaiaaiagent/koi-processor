#!/bin/bash
# Daily Curator Cron Script
# Runs at 12:00 ET Monday-Friday

# Set up environment
export PATH="/usr/local/bin:/usr/bin:/bin"
cd /opt/projects/koi-processor

# Activate virtual environment
source venv/bin/activate

# Set timestamp
echo "=========================="
echo "Daily Curator Run: $(date)"
echo "=========================="

# Run the daily curator
python scripts/run_daily_curator.py daily \
  --output "output/daily_threads/thread_$(date +%Y%m%d).json" \
  --verbose

# Check exit code
if [ $? -eq 0 ]; then
    echo "✅ Daily curator completed successfully"

    # Submit for review
    OUTPUT_FILE="output/daily_threads/thread_$(date +%Y%m%d).json"
    if [ -f "$OUTPUT_FILE" ]; then
        echo "Submitting content for review..."
        python scripts/submit_for_review.py "$OUTPUT_FILE" --type daily --auto-review

        if [ $? -eq 0 ]; then
            echo "✅ Content submitted for review"
        else
            echo "⚠️ Review submission failed"
        fi
    fi
else
    echo "❌ Daily curator failed with exit code $?"
fi

echo ""