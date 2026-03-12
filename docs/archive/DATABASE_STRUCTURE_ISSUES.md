# Database Structure Issues - September 2025

## Problem Summary

The production database has diverged from the migration files in the codebase, creating issues for new deployments.

## Issues Found

### 1. Untracked Column Additions
Production has several columns that were added manually or through failed migrations:
- `chunk_rid`, `chunk_index`, `total_chunks`, `free_chunks`, `chunks_created` - Chunking system columns
- `jena_chunk_uri` - Additional Jena integration
- Duplicate `source_content_rid` columns (appears twice in production)

### 2. Migration Tracking Inconsistency
- **Production applied**: Migrations 001-007
- **Production NOT applied**: Migration 010 (jena_integration) - but columns exist anyway
- **Codebase has**: Migrations 001-012

### 3. Failed Migration Application
Migration 007 (`improved_storage_architecture`) shows as applied but:
- References tables that don't exist (`koi_content`, `koi_memory_chunks`)
- Some ALTER statements failed
- Columns were likely added manually afterward

## Root Causes

1. **Migrations referencing non-existent tables**: Migration 007 tries to create foreign keys to tables that were never created
2. **Manual database modifications**: Columns added directly without updating migrations
3. **No rollback on partial failure**: When migrations partially fail, they're still marked as applied
4. **Missing migration validation**: No checks to ensure migrations match actual database state

## Impact on New Deployments

Anyone cloning the repo and running migrations will get:
1. **Different schema than production**
2. **Missing columns** that production code expects
3. **Failed migrations** due to missing referenced tables
4. **Data import failures** when trying to restore production backups

## Solution Implemented

### 1. Created Migration 012
`migrations/012_fix_production_alignment.sql` - Adds all missing columns with IF NOT EXISTS clauses

### 2. Created Database Structure Script
`scripts/ensure_db_structure.sh` - Ensures correct structure regardless of current state

### 3. Updated Setup Process
The script handles:
- Partial migration failures
- Already-existing columns
- Missing schema_migrations table
- Verification of final structure

## Recommendations for Future

1. **Always test migrations on a fresh database** before deploying
2. **Use IF NOT EXISTS** clauses for safety
3. **Don't reference tables without checking they exist**
4. **Track manual changes** in migration files immediately
5. **Use the ensure_db_structure.sh script** in CI/CD pipelines

## How to Fix Existing Deployments

For anyone who has already deployed:

```bash
# 1. Run the structure alignment script
cd /path/to/koi-processor
./scripts/ensure_db_structure.sh

# 2. If you have data issues, export and reimport using CSV:
# On production:
psql $DB_URL -c "COPY koi_memories TO STDOUT WITH CSV HEADER;" > koi_memories.csv
psql $DB_URL -c "COPY koi_embeddings TO STDOUT WITH CSV HEADER;" > koi_embeddings.csv

# On local:
psql $DB_URL -c "\COPY koi_memories FROM 'koi_memories.csv' WITH CSV HEADER;"
psql $DB_URL -c "\COPY koi_embeddings FROM 'koi_embeddings.csv' WITH CSV HEADER;"
```

## Verification

To verify your database structure is correct:

```sql
-- Check all expected columns exist
SELECT column_name 
FROM information_schema.columns 
WHERE table_name = 'koi_memories' 
ORDER BY ordinal_position;

-- Should include:
-- id, rid, cid, version, previous_version_id, event_type, source_sensor,
-- agent_id, content, metadata, superseded_at, created_at, updated_at,
-- published_at, published_confidence, content_hash, last_seen_at,
-- source_content_rid, is_chunk, parent_document_rid,
-- jena_uri, jena_graph, jena_sync_status, jena_synced_at
```

## Lessons Learned

1. **Production divergence is dangerous** - Keep migrations in sync
2. **Partial failures need handling** - Don't assume all-or-nothing
3. **Column existence checks are essential** - Use IF NOT EXISTS
4. **Documentation prevents confusion** - Track all database changes

This issue has been resolved with Migration 012 and the ensure_db_structure.sh script.