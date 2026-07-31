-- Migration: Add tenant_id for ingest-time tenant attribution
-- Date: 2026-07-31
-- Purpose: Capture WHICH TENANT a document belongs to at write time, so that
--          per-tenant isolation remains possible later. This migration adds
--          CAPTURE ONLY — it does not enforce anything. See the note at the end.
-- Related: 015_add_privacy_column.sql (same shape, same sticky-merge semantics)
--
-- WHY NOW, before any tenancy decision is made:
--   Provenance not recorded at write time cannot be reconstructed afterwards.
--   Empirically, on prod 2026-07-31:
--     SELECT count(*) FROM koi_entity_chunk_links l
--       LEFT JOIN koi_memories m ON l.document_rid = m.rid WHERE m.rid IS NULL;
--     -> 200,751 of 3,841,436 rows (5.22%) whose origin is now undecidable.
--   Every document ingested without a tenant key joins that category.
--
-- SCOPE: koi_memories only. koi_memory_chunks inherits tenancy through
--   document_rid and does not need its own key until read paths filter on it.
--   entity_registry (41 insert sites) and entity_relationships (30) are
--   DELIBERATELY EXCLUDED — entity_registry is a global namespace
--   (UNIQUE (normalized_text, entity_type), resolved with no scope), so adding a
--   tenant key there forks every entity per tenant and destroys cross-tenant
--   aggregation. That is a product decision, not a migration.

ALTER TABLE koi_memories ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(100);

-- Empty string must never be storable. Because attribution is made sticky with
-- COALESCE(existing, new), an empty-string tenant would count as a real owner and
-- permanently block the correct tenant from ever being recorded. The Python writers
-- normalise '' -> None, but only 2 of the 8 koi_memories writers are patched in this
-- phase, so the invariant is enforced in the schema where every writer must obey it.
-- Verified: without this, upserting '' then 'secondmuse' leaves the row owned by ''.
DO $$
BEGIN
    ALTER TABLE koi_memories
        ADD CONSTRAINT koi_memories_tenant_id_not_blank
        CHECK (tenant_id IS NULL OR length(btrim(tenant_id)) > 0);
EXCEPTION
    WHEN duplicate_object THEN NULL;  -- idempotent re-run
END $$;

COMMENT ON COLUMN koi_memories.tenant_id IS
  'Owning tenant for this document, captured at ingest. NULL = pre-tenancy or '
  'first-party Regen data. Capture only: no read path filters on this column '
  'as of migration 108. Set once and sticky (COALESCE on conflict) — a later '
  'ingest of the same rid must not silently re-attribute an existing document.';

-- Partial index: the overwhelming majority of existing rows are NULL, so a
-- partial index stays small and only pays for rows that actually carry a tenant.
CREATE INDEX IF NOT EXISTS idx_koi_memories_tenant_id
  ON koi_memories(tenant_id)
  WHERE tenant_id IS NOT NULL;

-- Composite for the eventual "active rows for tenant X" read pattern, mirroring
-- the idx_koi_memories_active_privacy shape introduced in 015.
CREATE INDEX IF NOT EXISTS idx_koi_memories_active_tenant
  ON koi_memories(superseded_at, tenant_id)
  WHERE superseded_at IS NULL AND tenant_id IS NOT NULL;

-- NOTE ON LOCKING: koi_memories is ~68,900 rows / ~577 MB on prod. ADD COLUMN
-- with no default is a catalog-only change in PG11+ and does not rewrite the
-- table. The two CREATE INDEX statements take a brief ACCESS EXCLUSIVE lock;
-- at this row count that is sub-second. If this is ever applied to a much larger
-- table, or if the runner does NOT wrap migrations in a transaction, prefer:
--   CREATE INDEX CONCURRENTLY ... (cannot run inside a transaction block).
--
-- NOTE ON ENFORCEMENT: this migration does NOT make it safe to admit an external
-- user. koi-query-api.ts buildPrivacyFilter() still returns '' for any
-- authenticated caller ("Authenticated users see all data"), and access is gated
-- solely by a hardcoded @regen.network email check. Capture is not enforcement.
