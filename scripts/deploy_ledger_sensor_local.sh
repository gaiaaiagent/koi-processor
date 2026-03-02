#!/bin/bash
# =============================================================================
# Local Deployment Script: Ledger Sensor Entity Indexing
# =============================================================================
# Run this script DIRECTLY ON the production server (202.61.196.119)
#
# Usage: ./deploy_ledger_sensor_local.sh
# =============================================================================

set -e  # Exit on any error

echo "============================================="
echo "  Ledger Sensor Deployment (Local)"
echo "============================================="
echo ""

# Configuration
KOI_PROCESSOR_PATH="/opt/projects/koi-processor"
KOI_SENSORS_PATH="/opt/projects/koi-sensors"
MCP_PATH="/opt/projects/regen-koi-mcp"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

cd $KOI_PROCESSOR_PATH
set -a; source .env; set +a

echo -e "${YELLOW}Step 1: Running database migration...${NC}"
psql -h localhost -p 5433 -U eliza -d eliza -f migrations/030_ledger_entity_fields.sql
echo -e "${GREEN}✓ Migration complete${NC}"
echo ""

echo -e "${YELLOW}Step 2: Restarting Event Bridge v2...${NC}"
pkill -f 'koi_event_bridge_v2.py' || true
sleep 2
source venv/bin/activate
nohup python3 src/core/koi_event_bridge_v2.py > logs/event_bridge_v2.log 2>&1 &
echo -e "${GREEN}✓ Event Bridge v2 restarted (PID: $!)${NC}"
echo ""

echo -e "${YELLOW}Step 3: Restarting Query API (pm2)...${NC}"
pm2 restart hybrid 2>/dev/null || pm2 restart koi-query-api 2>/dev/null || echo "Check pm2 status manually"
echo -e "${GREEN}✓ Query API restarted${NC}"
echo ""

echo -e "${YELLOW}Step 4: Building MCP server...${NC}"
cd $MCP_PATH
npm run build
echo -e "${GREEN}✓ MCP server built${NC}"
echo ""

echo -e "${YELLOW}Step 5: Setting up Ledger sensor...${NC}"
cd $KOI_SENSORS_PATH/sensors/ledger
if [ ! -d "venv" ]; then
    ./setup.sh
fi
echo -e "${GREEN}✓ Ledger sensor setup complete${NC}"
echo ""

echo -e "${YELLOW}Step 6: Enabling and starting Ledger sensor via systemd...${NC}"
sudo systemctl enable koi-sensor@ledger
sudo systemctl start koi-sensor@ledger
echo -e "${GREEN}✓ Ledger sensor started${NC}"
echo ""

echo "============================================="
echo -e "${GREEN}  Deployment Complete!${NC}"
echo "============================================="
echo ""
echo "Verification:"
echo "  sudo systemctl status koi-sensor@ledger"
echo "  journalctl -u koi-sensor@ledger -f"
echo "  curl 'http://localhost:8301/api/koi/entity/resolve?label=C02'"
echo ""
