#!/bin/bash

# KOI Processor Database Migration Runner
# Runs all migrations in order for initial setup

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default database URL
DEFAULT_DB_URL="postgresql://postgres:postgres@localhost:5433/eliza"
DB_URL="${DATABASE_URL:-$DEFAULT_DB_URL}"

# Migration directory
MIGRATION_DIR="$(dirname "$0")/../migrations"

echo -e "${GREEN}KOI Processor Database Migration Runner${NC}"
echo "========================================"
echo ""

# Check if psql is installed
if ! command -v psql &> /dev/null; then
    echo -e "${RED}Error: psql command not found. Please install PostgreSQL client.${NC}"
    echo "Ubuntu/Debian: sudo apt-get install postgresql-client"
    echo "macOS: brew install postgresql"
    exit 1
fi

# Test database connection
echo -e "${YELLOW}Testing database connection...${NC}"
if ! psql "$DB_URL" -c "SELECT version();" &> /dev/null; then
    echo -e "${RED}Error: Cannot connect to database.${NC}"
    echo "Database URL: $DB_URL"
    echo ""
    echo "Please ensure:"
    echo "1. PostgreSQL is running"
    echo "2. Database 'eliza' exists"
    echo "3. Credentials are correct"
    echo ""
    echo "To use a different database, set DATABASE_URL environment variable:"
    echo "  DATABASE_URL=postgresql://user:pass@host:port/dbname $0"
    exit 1
fi

echo -e "${GREEN}✓ Database connection successful${NC}"
echo ""

# --- Safety guard (added 2026-05-29) ----------------------------------------
# Refuse to run on a DB that uses the koi_migrations baseline system. This
# legacy runner tracks applied state in the DEPRECATED schema_migrations ledger,
# which is stale on baseline-system DBs — so it would RE-RUN already-applied
# migrations against the live database. New migrations are tracked in
# koi_migrations + baseline manifests (scripts/stamp_baseline.py); apply scoped:
#   psql "$DATABASE_URL" -f migrations/NNN_x.sql   # then record in koi_migrations
# Override (expert / genuinely-fresh DB only): KOI_ALLOW_LEGACY_RUNNER=1
if [ "${KOI_ALLOW_LEGACY_RUNNER:-0}" != "1" ]; then
    USES_KOI_MIGRATIONS=$(psql "$DB_URL" -t -c "SELECT to_regclass('public.koi_migrations') IS NOT NULL;" 2>/dev/null | tr -d '[:space:]')
    if [ "$USES_KOI_MIGRATIONS" = "t" ]; then
        echo -e "${RED}ABORT: this database uses the koi_migrations baseline system.${NC}"
        echo "This legacy schema_migrations-based runner is UNSAFE here: its ledger is"
        echo "stale, so it would re-run already-applied migrations against the live DB."
        echo "Apply a new migration scoped instead:  psql \"\$DATABASE_URL\" -f migrations/NNN_name.sql"
        echo "(then record it in koi_migrations; baseline: scripts/stamp_baseline.py)"
        echo "Override (fresh/empty DB only): KOI_ALLOW_LEGACY_RUNNER=1 $0"
        exit 1
    fi
fi
# ---------------------------------------------------------------------------

# Check for pgvector extension
echo -e "${YELLOW}Checking for required extensions...${NC}"
EXTENSIONS_SQL="
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        CREATE EXTENSION vector;
        RAISE NOTICE 'Created extension: vector';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'uuid-ossp') THEN
        CREATE EXTENSION \"uuid-ossp\";
        RAISE NOTICE 'Created extension: uuid-ossp';
    END IF;
END
\$\$;
"

if ! psql "$DB_URL" -c "$EXTENSIONS_SQL" 2>&1 | grep -v NOTICE; then
    echo -e "${RED}Error: Failed to create required extensions.${NC}"
    echo "Please ensure pgvector is installed and you have CREATE EXTENSION permission."
    exit 1
fi

echo -e "${GREEN}✓ Required extensions verified${NC}"
echo ""

# Create migration tracking table
echo -e "${YELLOW}Setting up migration tracking...${NC}"
TRACKING_SQL="
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(255) PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"

if ! psql "$DB_URL" -c "$TRACKING_SQL" &> /dev/null; then
    echo -e "${RED}Error: Failed to create migration tracking table.${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Migration tracking ready${NC}"
echo ""

# Get list of migration files. Exclude down/rollback files (*_down.sql,
# *.rollback.sql, *.down.sql) — a forward runner must NEVER auto-execute them.
MIGRATIONS=($(ls -1 "$MIGRATION_DIR"/*.sql 2>/dev/null | grep -viE '(\.rollback|_down|\.down)\.sql$' | sort))

if [ ${#MIGRATIONS[@]} -eq 0 ]; then
    echo -e "${RED}Error: No migration files found in $MIGRATION_DIR${NC}"
    exit 1
fi

echo "Found ${#MIGRATIONS[@]} migration files"
echo ""

# Run each migration
APPLIED=0
SKIPPED=0
FAILED=0

for MIGRATION_FILE in "${MIGRATIONS[@]}"; do
    MIGRATION_NAME=$(basename "$MIGRATION_FILE" .sql)
    
    # Check if already applied
    ALREADY_APPLIED=$(psql "$DB_URL" -t -c "SELECT COUNT(*) FROM schema_migrations WHERE version = '$MIGRATION_NAME';" 2>/dev/null | tr -d ' ')
    
    if [ "$ALREADY_APPLIED" = "1" ]; then
        echo -e "${YELLOW}⊘ Skipping $MIGRATION_NAME (already applied)${NC}"
        ((SKIPPED++))
        continue
    fi
    
    echo -e "${YELLOW}→ Applying $MIGRATION_NAME...${NC}"
    
    # Run the migration
    if psql "$DB_URL" -f "$MIGRATION_FILE" > /tmp/migration_output.txt 2>&1; then
        # Record successful migration
        psql "$DB_URL" -c "INSERT INTO schema_migrations (version) VALUES ('$MIGRATION_NAME');" &> /dev/null
        echo -e "${GREEN}✓ Applied $MIGRATION_NAME${NC}"
        ((APPLIED++))
    else
        echo -e "${RED}✗ Failed to apply $MIGRATION_NAME${NC}"
        echo "Error output:"
        cat /tmp/migration_output.txt
        ((FAILED++))
        
        # Ask if user wants to continue
        read -p "Continue with remaining migrations? (y/n) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            break
        fi
    fi
done

# Clean up
rm -f /tmp/migration_output.txt

echo ""
echo "========================================"
echo -e "${GREEN}Migration Summary${NC}"
echo "========================================"
echo "Applied: $APPLIED"
echo "Skipped: $SKIPPED"
echo "Failed:  $FAILED"
echo ""

# Verify final state
if [ $FAILED -eq 0 ]; then
    echo -e "${YELLOW}Verifying database schema...${NC}"
    
    # Check for expected tables
    TABLES=$(psql "$DB_URL" -t -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name IN ('transformation_receipts', 'agent_knowledge_permissions', 'koi_memories', 'koi_embeddings', 'koi_entities');" | tr -d ' ')
    
    VIEWS=$(psql "$DB_URL" -t -c "SELECT COUNT(*) FROM information_schema.views WHERE table_schema = 'public' AND table_name = 'v_daily_content';" | tr -d ' ')
    
    if [ "$TABLES" -ge "5" ] && [ "$VIEWS" -ge "1" ]; then
        echo -e "${GREEN}✓ All expected tables and views are present${NC}"
        echo ""
        
        # Show table summary
        echo "Database tables:"
        psql "$DB_URL" -c "\dt koi_*" 2>/dev/null | grep -E "koi_|table"
        
        echo ""
        echo -e "${GREEN}✓ Migration setup complete!${NC}"
        echo ""
        echo "You can now run the Daily Curator:"
        echo "  python scripts/run_daily_curator.py status"
        exit 0
    else
        echo -e "${RED}Warning: Some expected tables/views may be missing${NC}"
        echo "Found $TABLES tables and $VIEWS views"
        echo ""
        echo "Please check the migration output above for errors"
        exit 1
    fi
else
    echo -e "${RED}Some migrations failed. Please fix the issues and re-run.${NC}"
    echo ""
    echo "To reset and start over:"
    echo "  psql $DB_URL -c 'DROP TABLE IF EXISTS koi_memories, koi_embeddings, koi_entities, transformation_receipts, agent_knowledge_permissions, schema_migrations CASCADE;'"
    echo "  $0"
    exit 1
fi