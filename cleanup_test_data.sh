#!/bin/bash
# KOI System Test Data Cleanup Script
# Removes all test and verification data before production launch

echo "========================================"
echo "    KOI TEST DATA CLEANUP SCRIPT       "
echo "========================================"
echo "Starting at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Backup before cleanup
echo "📦 Creating backup before cleanup..."
BACKUP_FILE="/opt/projects/backups/koi_memories_backup_$(date +%Y%m%d_%H%M%S).sql"
mkdir -p /opt/projects/backups

docker exec gaia-postgres-1 pg_dump -U postgres -d eliza \
    -t koi_memories -t koi_embeddings \
    > "$BACKUP_FILE" 2>/dev/null

if [ -f "$BACKUP_FILE" ]; then
    echo -e "${GREEN}✓${NC} Backup created: $BACKUP_FILE"
else
    echo -e "${RED}✗${NC} Backup failed - aborting cleanup"
    exit 1
fi

echo ""
echo "🔍 Identifying test data to remove..."
echo "------------------------------------"

# Count test entries
TEST_COUNT=$(docker exec gaia-postgres-1 psql -U postgres -d eliza -t -c "
    SELECT COUNT(*) FROM koi_memories 
    WHERE rid LIKE '%test%' 
       OR rid LIKE '%trace%' 
       OR rid LIKE '%load_test%'
       OR rid LIKE '%resilience%'
       OR rid LIKE '%monitor%'
       OR rid LIKE '%startup%'
       OR rid LIKE '%validation%'
       OR content::text LIKE '%QUANTUM_BLOCKCHAIN_AGRICULTURE%'
       OR content::text LIKE '%UNIQUE TEST%'
       OR content::text LIKE '%TRACE TEST%'
       OR content::text LIKE '%Load test event%'
       OR content::text LIKE '%Pipeline test%'
       OR content::text LIKE '%Testing fixed coordinator%'
" 2>/dev/null | xargs)

echo "Found $TEST_COUNT test entries to remove"

# Show sample of what will be deleted
echo ""
echo "📋 Sample of entries to be removed:"
docker exec gaia-postgres-1 psql -U postgres -d eliza -c "
    SELECT rid, LEFT(content::text, 60) as content_preview
    FROM koi_memories 
    WHERE rid LIKE '%test%' 
       OR rid LIKE '%trace%' 
       OR content::text LIKE '%QUANTUM_BLOCKCHAIN_AGRICULTURE%'
       OR content::text LIKE '%UNIQUE TEST%'
    LIMIT 5;
" 2>/dev/null

echo ""
read -p "⚠️  Proceed with cleanup of $TEST_COUNT test entries? (yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    echo "Cleanup cancelled."
    exit 0
fi

echo ""
echo "🧹 Cleaning test data..."
echo "------------------------"

# Delete test embeddings first (foreign key constraint)
echo -n "Removing test embeddings..."
EMBEDDINGS_DELETED=$(docker exec gaia-postgres-1 psql -U postgres -d eliza -t -c "
    DELETE FROM koi_embeddings 
    WHERE rid IN (
        SELECT rid FROM koi_memories 
        WHERE rid LIKE '%test%' 
           OR rid LIKE '%trace%' 
           OR rid LIKE '%load_test%'
           OR rid LIKE '%resilience%'
           OR rid LIKE '%monitor%'
           OR rid LIKE '%startup%'
           OR rid LIKE '%validation%'
           OR content::text LIKE '%QUANTUM_BLOCKCHAIN_AGRICULTURE%'
           OR content::text LIKE '%UNIQUE TEST%'
           OR content::text LIKE '%TRACE TEST%'
           OR content::text LIKE '%Load test event%'
           OR content::text LIKE '%Pipeline test%'
           OR content::text LIKE '%Testing fixed coordinator%'
    );
    SELECT COUNT(*) FROM koi_embeddings WHERE rid LIKE '%test%';
" 2>/dev/null | tail -1 | xargs)

echo -e " ${GREEN}✓${NC} Removed embeddings"

# Delete test memories
echo -n "Removing test memories..."
MEMORIES_DELETED=$(docker exec gaia-postgres-1 psql -U postgres -d eliza -t -c "
    DELETE FROM koi_memories 
    WHERE rid LIKE '%test%' 
       OR rid LIKE '%trace%' 
       OR rid LIKE '%load_test%'
       OR rid LIKE '%resilience%'
       OR rid LIKE '%monitor%'
       OR rid LIKE '%startup%'
       OR rid LIKE '%validation%'
       OR content::text LIKE '%QUANTUM_BLOCKCHAIN_AGRICULTURE%'
       OR content::text LIKE '%UNIQUE TEST%'
       OR content::text LIKE '%TRACE TEST%'
       OR content::text LIKE '%Load test event%'
       OR content::text LIKE '%Pipeline test%'
       OR content::text LIKE '%Testing fixed coordinator%';
" 2>/dev/null | xargs)

echo -e " ${GREEN}✓${NC} Removed test memories"

# Verify cleanup
echo ""
echo "📊 Cleanup Results:"
echo "------------------"

# Get remaining counts
REMAINING_MEMORIES=$(docker exec gaia-postgres-1 psql -U postgres -d eliza -t -c "
    SELECT COUNT(*) FROM koi_memories;
" 2>/dev/null | xargs)

REMAINING_EMBEDDINGS=$(docker exec gaia-postgres-1 psql -U postgres -d eliza -t -c "
    SELECT COUNT(*) FROM koi_embeddings;
" 2>/dev/null | xargs)

echo "Remaining KOI memories: $REMAINING_MEMORIES"
echo "Remaining embeddings: $REMAINING_EMBEDDINGS"

# Show what's left (should be real content only)
echo ""
echo "✨ Remaining content (real data only):"
docker exec gaia-postgres-1 psql -U postgres -d eliza -c "
    SELECT rid, LEFT(content::text, 80) as content_preview
    FROM koi_memories
    ORDER BY created_at DESC
    LIMIT 5;
" 2>/dev/null

# Clean up test receipts and logs
echo ""
echo "🗑️  Cleaning test receipts and logs..."

# Clean test receipts
if [ -d "/opt/projects/koi-processor/output/receipts" ]; then
    find /opt/projects/koi-processor/output/receipts -name "*test*" -delete 2>/dev/null
    find /opt/projects/koi-processor/output/receipts -name "*trace*" -delete 2>/dev/null
    echo -e "${GREEN}✓${NC} Cleaned test receipts"
fi

# Rotate logs
for log in /opt/projects/koi-processor/logs/*.log; do
    if [ -f "$log" ]; then
        cp "$log" "${log}.$(date +%Y%m%d_%H%M%S).bak"
        > "$log"
    fi
done
echo -e "${GREEN}✓${NC} Rotated log files"

echo ""
echo "========================================"
echo "         CLEANUP COMPLETE               "
echo "========================================"
echo ""
echo "Summary:"
echo "--------"
echo "✅ Test data removed: ~$TEST_COUNT entries"
echo "✅ Production data preserved: $REMAINING_MEMORIES memories"
echo "✅ Backup saved: $BACKUP_FILE"
echo "✅ Logs rotated with timestamps"
echo ""
echo "The system is now clean and ready for production!"
echo ""
echo "Next steps:"
echo "1. Verify remaining data: docker exec gaia-postgres-1 psql -U postgres -d eliza -c 'SELECT * FROM koi_memories;'"
echo "2. Start production monitoring: /opt/projects/monitor_production.sh"
echo "3. Begin real data collection!"
echo ""