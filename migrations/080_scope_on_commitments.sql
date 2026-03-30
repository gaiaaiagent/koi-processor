-- 080_scope_on_commitments.sql
-- Add first-class scope column to commitments and commitment_pools.
-- Scope determines governance path — too important for metadata.
--
-- Part of the Claims × Spore protocol layer (additive, no rewrites).

-- Add scope to commitments (default 'pool' — backward compatible)
DO $$ BEGIN
    ALTER TABLE commitments ADD COLUMN scope TEXT DEFAULT 'pool';
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

-- Add scope to commitment_pools (default 'pool' — backward compatible)
DO $$ BEGIN
    ALTER TABLE commitment_pools ADD COLUMN scope TEXT DEFAULT 'pool';
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

-- Index for scope queries
CREATE INDEX IF NOT EXISTS idx_commitments_scope ON commitments(scope);
CREATE INDEX IF NOT EXISTS idx_pools_scope       ON commitment_pools(scope);

-- Register migration
INSERT INTO koi_migrations (migration_id, checksum)
VALUES ('personal:080_scope_on_commitments', 'v1_protocol_layer')
ON CONFLICT (migration_id) DO NOTHING;
