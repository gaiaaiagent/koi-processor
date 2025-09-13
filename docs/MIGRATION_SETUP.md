# Database Migration Setup Guide

## Overview
This guide provides clear instructions for setting up the database schema when cloning the KOI processor repository. All migrations must be run in order to properly initialize the database.

## Prerequisites
- PostgreSQL 14+ with pgvector extension
- Database named `eliza` (or configured in your environment)
- Database user with CREATE/ALTER permissions

## Quick Setup (Recommended)

### Option 1: Automated Migration Script
```bash
# Clone the repository
git clone https://github.com/RegenAI/koi-processor.git
cd koi-processor

# Run all migrations in order
./scripts/run_migrations.sh

# Or with custom database URL
DATABASE_URL=postgresql://user:pass@host:port/dbname ./scripts/run_migrations.sh
```

### Option 2: Using Python CLI
```bash
# Install dependencies
pip install -r requirements.txt

# Run all migrations
python scripts/run_daily_curator.py migrate

# Check migration status
python scripts/run_daily_curator.py migrate --status
```

## Manual Setup (Step by Step)

If you prefer to run migrations manually or need to troubleshoot:

### 1. Check Database Connection
```bash
# Test connection (default)
psql postgresql://postgres:postgres@localhost:5433/eliza -c "SELECT version();"

# Or with your custom URL
psql $DATABASE_URL -c "SELECT version();"
```

### 2. Enable pgvector Extension
```sql
-- Connect to your database
psql postgresql://postgres:postgres@localhost:5433/eliza

-- Enable pgvector if not already enabled
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Verify extensions
\dx
```

### 3. Run Migrations in Order

**IMPORTANT**: Migrations must be run in numerical order!

```bash
# Set your database URL (or use default)
export DATABASE_URL=postgresql://postgres:postgres@localhost:5433/eliza

# Run each migration in order
psql $DATABASE_URL -f migrations/001_create_transformation_receipts.sql
psql $DATABASE_URL -f migrations/002_create_agent_knowledge_permissions.sql
psql $DATABASE_URL -f migrations/003_create_isolated_koi_tables.sql
psql $DATABASE_URL -f migrations/004_add_publication_dates.sql

# Verify all migrations completed
psql $DATABASE_URL -c "\dt koi_*"
```

## Migration Overview

| Migration | Purpose | Dependencies |
|-----------|---------|--------------|
| 001_create_transformation_receipts.sql | CAT receipt tracking for content transformations | pgvector |
| 002_create_agent_knowledge_permissions.sql | Agent permission system for knowledge access | 001 |
| 003_create_isolated_koi_tables.sql | Main KOI tables with versioning support | 001, 002 |
| 004_add_publication_dates.sql | Publication date tracking for content curation | 003 |

## Verification

After running migrations, verify the setup:

```bash
# Check all tables were created
psql $DATABASE_URL -c "\dt"

# Expected tables:
# - transformation_receipts
# - agent_knowledge_permissions  
# - koi_memories
# - koi_embeddings
# - koi_entities
# - v_daily_content (view)

# Check specific KOI tables
psql $DATABASE_URL -c "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'koi_memories' ORDER BY ordinal_position;"

# Test the daily content view
psql $DATABASE_URL -c "SELECT COUNT(*) FROM v_daily_content;"
```

## Troubleshooting

### Error: "permission denied to create extension"
```sql
-- Connect as superuser
psql -U postgres -d eliza
ALTER USER your_user CREATEDB;
GRANT CREATE ON DATABASE eliza TO your_user;
```

### Error: "extension 'vector' does not exist"
```bash
# Install pgvector (Ubuntu/Debian)
sudo apt-get install postgresql-14-pgvector

# Install pgvector (macOS with Homebrew)
brew install pgvector

# Or build from source
git clone https://github.com/pgvector/pgvector.git
cd pgvector
make
make install
```

### Error: "relation already exists"
This means a migration was partially applied. To reset:
```sql
-- BE CAREFUL: This drops all KOI tables
DROP TABLE IF EXISTS koi_memories CASCADE;
DROP TABLE IF EXISTS koi_embeddings CASCADE;
DROP TABLE IF EXISTS koi_entities CASCADE;
DROP TABLE IF EXISTS transformation_receipts CASCADE;
DROP TABLE IF EXISTS agent_knowledge_permissions CASCADE;
DROP VIEW IF EXISTS v_daily_content CASCADE;

-- Then re-run migrations from start
```

### Checking Migration History
```sql
-- Create migration tracking table (optional but recommended)
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(255) PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- After each migration, record it
INSERT INTO schema_migrations (version) VALUES ('001_create_transformation_receipts');
INSERT INTO schema_migrations (version) VALUES ('002_create_agent_knowledge_permissions');
INSERT INTO schema_migrations (version) VALUES ('003_create_isolated_koi_tables');
INSERT INTO schema_migrations (version) VALUES ('004_add_publication_dates');

-- Check applied migrations
SELECT * FROM schema_migrations ORDER BY version;
```

## Docker Setup

If using Docker for PostgreSQL:

```yaml
# docker-compose.yml
version: '3.8'
services:
  postgres:
    image: pgvector/pgvector:pg14
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: eliza
    ports:
      - "5433:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./migrations:/migrations:ro

volumes:
  postgres_data:
```

```bash
# Start PostgreSQL
docker-compose up -d postgres

# Run migrations
docker-compose exec postgres bash -c "for f in /migrations/*.sql; do psql -U postgres -d eliza -f \$f; done"
```

## Environment Variables

Set these in your `.env` file:
```bash
# Database connection
DATABASE_URL=postgresql://postgres:postgres@localhost:5433/eliza

# Or individual components
POSTGRES_HOST=localhost
POSTGRES_PORT=5433
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=eliza
```

## Fresh Install Checklist

- [ ] PostgreSQL 14+ installed
- [ ] pgvector extension available
- [ ] Database created (`createdb eliza`)
- [ ] User has proper permissions
- [ ] All 4 migrations run in order
- [ ] Tables verified with `\dt`
- [ ] Test query on v_daily_content view works

## Rollback Instructions

To rollback a specific migration:

```bash
# Rollback 004 (publication dates)
psql $DATABASE_URL -c "ALTER TABLE koi_memories DROP COLUMN IF EXISTS published_at, DROP COLUMN IF EXISTS published_confidence, DROP COLUMN IF EXISTS content_hash, DROP COLUMN IF EXISTS last_seen_at CASCADE;"

# Record rollback
psql $DATABASE_URL -c "DELETE FROM schema_migrations WHERE version = '004_add_publication_dates';"
```

## Support

If you encounter issues:
1. Check the [Troubleshooting](#troubleshooting) section
2. Verify PostgreSQL version: `psql -c "SELECT version();"`
3. Check pgvector is installed: `psql -c "\dx vector;"`
4. Review migration output for specific errors
5. Open an issue with error messages and environment details