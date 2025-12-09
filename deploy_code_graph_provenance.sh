#!/bin/bash
# Deploy Code Graph Provenance Update
# This script updates the Code Graph Service to use provenance-enabled processor

set -e

echo "🌱 Deploying Code Graph Provenance Update"
echo "=========================================="
echo ""

# Configuration
SERVER="darren@202.61.196.119"
PROJECT_DIR="/opt/projects/koi-processor"
BACKUP_DIR="/opt/projects/koi-processor/backups/$(date +%Y%m%d_%H%M%S)"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Step 1: Backup existing files
echo -e "${YELLOW}Step 1: Creating backup...${NC}"
ssh $SERVER "mkdir -p $BACKUP_DIR"
ssh $SERVER "cp $PROJECT_DIR/src/core/code_graph_processor.py $BACKUP_DIR/ 2>/dev/null || echo 'No existing processor'"
ssh $SERVER "cp $PROJECT_DIR/src/core/code_graph_service.py $BACKUP_DIR/ 2>/dev/null || echo 'No existing service'"
echo -e "${GREEN}✓ Backup created at $BACKUP_DIR${NC}"
echo ""

# Step 2: Upload new files
echo -e "${YELLOW}Step 2: Uploading new files...${NC}"
scp src/core/code_graph_processor_v2.py $SERVER:$PROJECT_DIR/src/core/code_graph_processor.py
echo -e "${GREEN}✓ Uploaded code_graph_processor.py (v2 with provenance)${NC}"
echo ""

# Step 3: Run database migration
echo -e "${YELLOW}Step 3: Running database migration...${NC}"
scp migrations/add_code_graph_provenance.sql $SERVER:/tmp/
ssh $SERVER "cd $PROJECT_DIR && psql postgresql://postgres:postgres@localhost:5433/eliza -f /tmp/add_code_graph_provenance.sql"
echo -e "${GREEN}✓ Database migration completed${NC}"
echo ""

# Step 4: Restart Code Graph Service
echo -e "${YELLOW}Step 4: Restarting Code Graph Service...${NC}"
ssh $SERVER "systemctl restart code-graph-service || (cd $PROJECT_DIR && pkill -f code_graph_service && python3 src/core/code_graph_service.py &)"
sleep 3
echo -e "${GREEN}✓ Service restarted${NC}"
echo ""

# Step 5: Verify deployment
echo -e "${YELLOW}Step 5: Verifying deployment...${NC}"
HEALTH_CHECK=$(ssh $SERVER "curl -s http://localhost:8350/ | jq -r '.status' 2>/dev/null || echo 'error'")

if [ "$HEALTH_CHECK" = "running" ]; then
    echo -e "${GREEN}✓ Code Graph Service is running${NC}"
else
    echo -e "${RED}✗ Service health check failed${NC}"
    echo "Check logs: ssh $SERVER 'journalctl -u code-graph-service -n 50'"
    exit 1
fi

# Check stats
echo ""
echo "Current stats:"
ssh $SERVER "curl -s http://localhost:8350/stats | jq '{events_received, events_processed, entities_extracted, cat_receipts_created: \"will_be_in_db\"}'"

echo ""
echo -e "${GREEN}=========================================="
echo "✓ Deployment Complete!"
echo "==========================================${NC}"
echo ""
echo "Next steps:"
echo "1. Test with sample event:"
echo "   ssh $SERVER"
echo "   curl -X POST http://localhost:8350/process-koi-event \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d @test_event.json"
echo ""
echo "2. Verify provenance:"
echo "   psql postgresql://postgres:postgres@localhost:5433/eliza"
echo "   SELECT * FROM code_entity_provenance LIMIT 5;"
echo ""
echo "3. Trigger reindexing:"
echo "   python3 trigger_graph_indexing.py"
echo ""
echo "Backup location: $BACKUP_DIR"
