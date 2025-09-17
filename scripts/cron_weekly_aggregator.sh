#!/bin/bash
# Weekly Aggregator Cron Script
# Runs at 14:00 ET on Fridays

# Set up environment
export PATH="/usr/local/bin:/usr/bin:/bin"
cd /opt/projects/koi-processor

# Activate virtual environment
source venv/bin/activate

# Set timestamp
echo "=========================="
echo "Weekly Aggregator Run: $(date)"
echo "=========================="

# Run the weekly aggregator
python scripts/run_weekly_aggregator.py \
  --output "output/weekly_digests/"

# Check exit code
if [ $? -eq 0 ]; then
    echo "✅ Weekly aggregator completed successfully"

    # Submit for review
    DIGEST_FILE="output/weekly_digests/weekly_digest_$(date +%Y-%m-%d).json"
    if [ -f "$DIGEST_FILE" ]; then
        echo "Submitting weekly digest for review..."
        python scripts/submit_for_review.py "$DIGEST_FILE" --type weekly --auto-review

        if [ $? -eq 0 ]; then
            echo "✅ Weekly digest submitted for review"
        else
            echo "⚠️ Review submission failed"
        fi
    fi

    # Also generate the podcast if configured
    if [ -f "scripts/generate_weekly_podcast.py" ]; then
        echo "Generating weekly podcast..."
        python scripts/generate_weekly_podcast.py \
          --input "output/weekly_digests/weekly_digest_$(date +%Y-%m-%d).md"
    fi
else
    echo "❌ Weekly aggregator failed with exit code $?"
fi

echo ""