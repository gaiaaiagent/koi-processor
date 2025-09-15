#!/bin/bash
# Clear and re-scrape all important sources with proper date extraction

echo "🔄 Comprehensive KOI Memory Re-scraping"
echo "========================================"
echo ""
echo "This will clear and re-scrape:"
echo "  • Discourse forums"
echo "  • Medium articles"
echo "  • Twitter/X posts"
echo "  • Websites"
echo "  • GitHub repositories"
echo "  • GitLab repositories"
echo ""
read -p "⚠️  This will DELETE existing data. Continue? (y/N): " confirm

if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 0
fi

# Database connection
export PGPASSWORD=postgres
DB_CMD="psql -h localhost -p 5433 -U postgres -d eliza"

# Show current status
echo ""
echo "📊 Current Status:"
$DB_CMD -c "SELECT
    CASE
        WHEN source_sensor LIKE 'discourse%' THEN 'Discourse'
        WHEN source_sensor LIKE 'medium%' THEN 'Medium'
        WHEN source_sensor LIKE 'twitter%' THEN 'Twitter'
        WHEN source_sensor LIKE 'website%' THEN 'Website'
        WHEN source_sensor LIKE 'github%' THEN 'GitHub'
        WHEN source_sensor LIKE 'gitlab%' THEN 'GitLab'
        ELSE 'Other'
    END as source_type,
    COUNT(*) as total,
    COUNT(published_at) as with_dates,
    ROUND(100.0 * COUNT(published_at) / COUNT(*), 1) as percent_with_dates
FROM koi_memories
GROUP BY source_type
ORDER BY total DESC;"

# Stop running sensors
echo ""
echo "🛑 Stopping sensors..."
cd /opt/projects/koi-sensors
./stop_all.sh

# Clear data for specified sensors
echo ""
echo "🗑️  Clearing old data..."
$DB_CMD -c "DELETE FROM koi_memories WHERE
    source_sensor LIKE 'discourse-sensor%' OR
    source_sensor LIKE 'medium-sensor%' OR
    source_sensor LIKE 'twitter%sensor%' OR
    source_sensor LIKE 'website-sensor%' OR
    source_sensor LIKE 'github-sensor%' OR
    source_sensor LIKE 'gitlab-sensor%';"

# Get deletion count
DELETED=$($DB_CMD -t -c "SELECT ROW_COUNT();")
echo "✅ Deleted $DELETED memories"

# Show remaining data
echo ""
echo "📊 Remaining data:"
$DB_CMD -c "SELECT source_sensor, COUNT(*) FROM koi_memories GROUP BY source_sensor ORDER BY COUNT(*) DESC LIMIT 5;"

# Start sensors for re-scraping
echo ""
echo "🚀 Starting sensors for re-scraping..."
echo "   This will collect fresh data with proper publication dates."
echo ""

# Start individual sensors with proper setup
cd /opt/projects/koi-sensors

# Discourse
if [ -d "sensors/discourse" ]; then
    echo "Starting Discourse sensor..."
    cd sensors/discourse
    if [ -f "start.sh" ]; then
        ./start.sh -b
        echo "✅ Discourse sensor started"
    fi
    cd ../..
fi

# Medium
if [ -d "sensors/medium" ]; then
    echo "Starting Medium sensor..."
    cd sensors/medium
    if [ -f "start.sh" ]; then
        ./start.sh -b
        echo "✅ Medium sensor started"
    fi
    cd ../..
fi

# Twitter
if [ -d "sensors/twitter" ]; then
    echo "Starting Twitter sensor..."
    cd sensors/twitter
    if [ -f "start.sh" ]; then
        ./start.sh -b
        echo "✅ Twitter sensor started"
    fi
    cd ../..
fi

# Website
if [ -d "sensors/websites" ]; then
    echo "Starting Website sensor..."
    cd sensors/websites
    if [ -f "start.sh" ]; then
        ./start.sh -b
        echo "✅ Website sensor started"
    fi
    cd ../..
fi

# GitHub
if [ -d "sensors/github" ]; then
    echo "Starting GitHub sensor..."
    cd sensors/github
    if [ -f "start.sh" ]; then
        ./start.sh -b
        echo "✅ GitHub sensor started"
    fi
    cd ../..
fi

# GitLab
if [ -d "sensors/gitlab" ]; then
    echo "Starting GitLab sensor..."
    cd sensors/gitlab
    if [ -f "start.sh" ]; then
        ./start.sh -b
        echo "✅ GitLab sensor started"
    fi
    cd ../..
fi

echo ""
echo "✅ Re-scraping initiated!"
echo ""
echo "📝 Monitor progress:"
echo "   tail -f sensors/*/\*.log"
echo ""
echo "📊 Check date coverage:"
echo "   psql -h localhost -p 5433 -U postgres -d eliza -c \"SELECT source_sensor, COUNT(*), COUNT(published_at) FROM koi_memories GROUP BY source_sensor;\""
echo ""
echo "🎯 Test digests after scraping completes:"
echo "   cd /opt/projects/koi-processor"
echo "   python scripts/run_daily_curator.py daily"
echo "   python scripts/run_weekly_aggregator.py --preview"