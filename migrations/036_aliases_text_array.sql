-- =============================================================================
-- Migration 036: Ensure aliases column is TEXT[] (not JSONB)
-- =============================================================================
-- Date: 2026-02-02
-- Purpose: Align new environments with production database where aliases is TEXT[]
--          Migration 030 defined aliases as JSONB but live DB uses TEXT[]
-- Database: personal_koi
-- =============================================================================

-- Only alter if column exists and is JSONB
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'entity_registry'
        AND column_name = 'aliases'
        AND data_type = 'jsonb'
    ) THEN
        -- Convert JSONB to TEXT[] (handle empty/null cases)
        ALTER TABLE entity_registry
        ALTER COLUMN aliases TYPE TEXT[]
        USING CASE
            WHEN aliases IS NULL THEN '{}'::TEXT[]
            WHEN aliases = 'null'::jsonb THEN '{}'::TEXT[]
            ELSE (SELECT ARRAY(SELECT jsonb_array_elements_text(aliases)))
        END;

        RAISE NOTICE 'Converted aliases column from JSONB to TEXT[]';
    ELSE
        RAISE NOTICE 'aliases column is already TEXT[] or does not exist';
    END IF;
END $$;

-- Set default to empty array (avoid nulls, makes COALESCE unnecessary)
ALTER TABLE entity_registry
ALTER COLUMN aliases SET DEFAULT '{}';

-- Backfill any existing nulls
UPDATE entity_registry SET aliases = '{}' WHERE aliases IS NULL;

-- Ensure GIN index exists for TEXT[] (different syntax than JSONB)
-- Drop JSONB GIN index if exists, create TEXT[] GIN index
DROP INDEX IF EXISTS idx_entity_registry_aliases_gin;
CREATE INDEX IF NOT EXISTS idx_entity_aliases_gin
ON entity_registry USING GIN (aliases);

-- =============================================================================
-- Comments
-- =============================================================================

COMMENT ON COLUMN entity_registry.aliases IS 'Array of alternative names for alias matching (TEXT[] for efficient ANY() queries)';
