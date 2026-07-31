-- Rollback for 108_tenant_id.sql
--
-- WARNING: dropping tenant_id DESTROYS attribution that cannot be recomputed.
-- That is the whole point of the forward migration. If you are rolling back
-- because a read path misbehaved, prefer fixing the read path — the column is
-- inert on its own (nothing filters on it as of 108).
--
-- If you genuinely need to roll back after any tenant-attributed ingest has run,
-- capture the mapping first so it can be restored:
--   CREATE TABLE koi_memories_tenant_backup_108 AS
--     SELECT rid, tenant_id FROM koi_memories WHERE tenant_id IS NOT NULL;

DROP INDEX IF EXISTS idx_koi_memories_active_tenant;
DROP INDEX IF EXISTS idx_koi_memories_tenant_id;
ALTER TABLE koi_memories DROP COLUMN IF EXISTS tenant_id;
