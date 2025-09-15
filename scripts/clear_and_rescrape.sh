#!/bin/bash
# Clear specific sensors from database and trigger re-scraping

echo "🔄 KOI Memory Re-scraping Tool"
echo "================================"
echo ""
echo "This will clear data from specific sensors and re-scrape with proper dates."
echo ""

# Database connection
export PGPASSWORD=postgres
DB_CMD="psql -h localhost -p 5433 -U postgres -d eliza"

# Show current status
echo "📊 Current Status:"
$DB_CMD -c "SELECT source_sensor, COUNT(*) as total, COUNT(published_at) as with_dates FROM koi_memories GROUP BY source_sensor ORDER BY total DESC LIMIT 10;"

echo ""
echo "Which sensors would you like to re-scrape?"
echo "1) Discourse forums (high value for digests)"
echo "2) Medium articles (high value for digests)"
echo "3) GitHub repos (medium value)"
echo "4) All of the above"
echo "5) Cancel"
echo ""
read -p "Enter choice (1-5): " choice

case $choice in
    1)
        echo "Clearing discourse sensor data..."
        $DB_CMD -c "DELETE FROM koi_memories WHERE source_sensor LIKE 'discourse-sensor%';"
        echo "✅ Cleared. Now restart discourse sensor to re-scrape."
        ;;
    2)
        echo "Clearing medium sensor data..."
        $DB_CMD -c "DELETE FROM koi_memories WHERE source_sensor LIKE 'medium-sensor%';"
        echo "✅ Cleared. Now restart medium sensor to re-scrape."
        ;;
    3)
        echo "Clearing GitHub sensor data..."
        $DB_CMD -c "DELETE FROM koi_memories WHERE source_sensor LIKE 'github-sensor%';"
        echo "✅ Cleared. Now restart GitHub sensor to re-scrape."
        ;;
    4)
        echo "Clearing discourse, medium, and GitHub sensor data..."
        $DB_CMD -c "DELETE FROM koi_memories WHERE source_sensor LIKE 'discourse-sensor%' OR source_sensor LIKE 'medium-sensor%' OR source_sensor LIKE 'github-sensor%';"
        echo "✅ Cleared. Now restart these sensors to re-scrape."
        ;;
    5)
        echo "Cancelled."
        exit 0
        ;;
    *)
        echo "Invalid choice."
        exit 1
        ;;
esac

echo ""
echo "📝 Next steps:"
echo "1. Stop sensors: cd /opt/projects/koi-sensors && ./stop_all.sh"
echo "2. Start sensors: ./start_all.sh"
echo "3. Wait for re-scraping to complete (check logs)"
echo "4. Run daily curator to test: cd /opt/projects/koi-processor && python scripts/run_daily_curator.py daily"