#!/bin/bash
# =============================================================================
# Deployment Script: Ledger Sensor Entity Indexing
# =============================================================================
# This script deploys the Ledger Sensor entity indexing feature to production.
# Run on production server: darren@202.61.196.119
#
# What this deploys:
# - Database migration for ledger entity fields
# - Enhanced entity resolution API
# - Ledger sensor with entity indexing
# =============================================================================

set -e  # Exit on any error

echo "============================================="
echo "  Ledger Sensor Deployment"
echo "============================================="
echo ""

# Configuration
PROD_SERVER="darren@202.61.196.119"
KOI_PROCESSOR_PATH="/opt/projects/koi-processor"
KOI_SENSORS_PATH="/opt/projects/koi-sensors"
MCP_PATH="/opt/projects/regen-koi-mcp"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Function to run commands on production
run_prod() {
    ssh $PROD_SERVER "$1"
}

echo -e "${YELLOW}Step 1: Running database migration...${NC}"
run_prod "cd $KOI_PROCESSOR_PATH && psql -h localhost -p 5433 -U eliza -d eliza -f migrations/030_ledger_entity_fields.sql"
echo -e "${GREEN}✓ Migration complete${NC}"
echo ""

echo -e "${YELLOW}Step 2: Restarting Event Bridge v2...${NC}"
run_prod "sudo systemctl restart koi-event-bridge || (cd $KOI_PROCESSOR_PATH && source venv/bin/activate && pkill -f 'koi_event_bridge_v2' || true && nohup python3 src/core/koi_event_bridge_v2.py > logs/event_bridge.log 2>&1 &)"
echo -e "${GREEN}✓ Event Bridge restarted${NC}"
echo ""

echo -e "${YELLOW}Step 3: Restarting Query API...${NC}"
run_prod "cd $KOI_PROCESSOR_PATH && pm2 restart hybrid 2>/dev/null || pm2 restart koi-query-api 2>/dev/null || echo 'Query API restart via pm2 - check manually'"
echo -e "${GREEN}✓ Query API restarted${NC}"
echo ""

echo -e "${YELLOW}Step 4: Building MCP server...${NC}"
run_prod "cd $MCP_PATH && npm run build"
echo -e "${GREEN}✓ MCP server built${NC}"
echo ""

echo -e "${YELLOW}Step 5: Setting up Ledger sensor...${NC}"
run_prod "cd $KOI_SENSORS_PATH/sensors/ledger && ./setup.sh"
echo -e "${GREEN}✓ Ledger sensor setup complete${NC}"
echo ""

echo -e "${YELLOW}Step 6: Enabling and starting Ledger sensor...${NC}"
run_prod "sudo systemctl enable koi-sensor@ledger && sudo systemctl start koi-sensor@ledger"
echo -e "${GREEN}✓ Ledger sensor started${NC}"
echo ""

echo "============================================="
echo -e "${GREEN}  Deployment Complete!${NC}"
echo "============================================="
echo ""
echo "Verification commands:"
echo "  Check sensor status:  ssh $PROD_SERVER 'sudo systemctl status koi-sensor@ledger'"
echo "  View sensor logs:     ssh $PROD_SERVER 'journalctl -u koi-sensor@ledger -f'"
echo "  Test entity resolve:  curl 'https://regen.gaiaai.xyz/api/koi/entity/resolve?label=C02'"
echo ""
