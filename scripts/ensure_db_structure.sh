#!/bin/bash

# Ensure Database Structure Script
# This script ensures the database has the correct structure by applying all migrations
# It handles the case where some migrations might have been partially applied

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Database connection
DB_URL="${POSTGRES_URL:-postgresql://postgres:postgres@localhost:5433/eliza}"

echo -e "${YELLOW}=== Ensuring KOI Database Structure ===${NC}"
echo "Database: $DB_URL"
echo

# Check if schema_migrations table exists
echo -e "${YELLOW}Checking schema_migrations table...${NC}"
psql "$DB_URL" -c "
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(255) PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT NOW()
);" > /dev/null 2>&1

# Get list of applied migrations
APPLIED_MIGRATIONS=$(psql "$DB_URL" -t -c "SELECT version FROM schema_migrations;" 2>/dev/null | tr -d ' ')

# Apply migrations in order
MIGRATION_FILES=(
    "001_create_transformation_receipts.sql"
    "002_create_agent_knowledge_permissions.sql"
    "003_create_isolated_koi_tables.sql"
    "004_add_publication_dates.sql"
    "005_create_dashboard_tables.sql"
    "006_fix_cat_receipts.sql"
    "007_improved_storage_architecture.sql"
    "010_jena_integration.sql"
    "011_adaptive_knowledge_query_log.sql"
    "012_fix_production_alignment.sql"
)

for migration in "${MIGRATION_FILES[@]}"; do
    VERSION="${migration%.sql}"
    
    # Check if already applied
    if echo "$APPLIED_MIGRATIONS" | grep -q "^$VERSION$"; then
        echo -e "${GREEN}✓${NC} Migration $VERSION already applied"
    else
        if [ -f "migrations/$migration" ]; then
            echo -e "${YELLOW}Applying migration: $VERSION${NC}"
            
            # Apply migration with error handling
            if psql "$DB_URL" -f "migrations/$migration" > /dev/null 2>&1; then
                # Record successful migration
                psql "$DB_URL" -c "INSERT INTO schema_migrations (version) VALUES ('$VERSION') ON CONFLICT DO NOTHING;" > /dev/null 2>&1
                echo -e "${GREEN}✓${NC} Migration $VERSION applied successfully"
            else
                echo -e "${YELLOW}⚠${NC} Migration $VERSION had errors (may be partial - columns might already exist)"
                # Still record it to avoid re-running
                psql "$DB_URL" -c "INSERT INTO schema_migrations (version) VALUES ('$VERSION') ON CONFLICT DO NOTHING;" > /dev/null 2>&1
            fi
        else
            echo -e "${RED}✗${NC} Migration file not found: migrations/$migration"
        fi
    fi
done

echo
echo -e "${YELLOW}Verifying database structure...${NC}"

# Check critical tables exist
TABLES=("koi_memories" "koi_embeddings" "koi_query_log" "transformation_receipts")
MISSING_TABLES=()

for table in "${TABLES[@]}"; do
    if psql "$DB_URL" -t -c "SELECT 1 FROM pg_tables WHERE tablename = '$table';" | grep -q 1; then
        echo -e "${GREEN}✓${NC} Table $table exists"
    else
        echo -e "${RED}✗${NC} Table $table is missing"
        MISSING_TABLES+=("$table")
    fi
done

# Check critical columns in koi_memories
echo
echo -e "${YELLOW}Checking koi_memories columns...${NC}"
EXPECTED_COLUMNS=(
    "id" "rid" "cid" "version" "event_type" "source_sensor"
    "content" "metadata" "created_at" "updated_at"
    "is_chunk" "parent_document_rid" "source_content_rid"
    "jena_uri" "jena_graph" "jena_sync_status"
)

for column in "${EXPECTED_COLUMNS[@]}"; do
    if psql "$DB_URL" -t -c "SELECT 1 FROM information_schema.columns WHERE table_name = 'koi_memories' AND column_name = '$column';" | grep -q 1; then
        echo -e "${GREEN}✓${NC} Column $column exists"
    else
        echo -e "${RED}✗${NC} Column $column is missing"
    fi
done

# Summary
echo
echo -e "${YELLOW}=== Database Structure Summary ===${NC}"

# Count records
MEMORY_COUNT=$(psql "$DB_URL" -t -c "SELECT COUNT(*) FROM koi_memories;" 2>/dev/null | tr -d ' ')
EMBEDDING_COUNT=$(psql "$DB_URL" -t -c "SELECT COUNT(*) FROM koi_embeddings WHERE dim_1024 IS NOT NULL;" 2>/dev/null | tr -d ' ')

echo "KOI Memories: $MEMORY_COUNT records"
echo "BGE Embeddings: $EMBEDDING_COUNT records"

if [ ${#MISSING_TABLES[@]} -eq 0 ]; then
    echo -e "${GREEN}✓ All critical tables exist${NC}"
else
    echo -e "${RED}✗ Missing tables: ${MISSING_TABLES[*]}${NC}"
    echo -e "${YELLOW}Run './scripts/setup.sh' to create missing tables${NC}"
fi

echo
echo -e "${GREEN}Database structure check complete!${NC}"