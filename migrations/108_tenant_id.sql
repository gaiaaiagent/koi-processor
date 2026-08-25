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

-- Fail fast rather than queue: an ACCESS EXCLUSIVE request that stacks behind an
-- in-flight query on koi_memories would put every subsequent query behind it.
SET lock_timeout = '3s';

ALTER TABLE koi_memories ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(100);

-- Empty string must never be storable. Because attribution is made sticky with
-- COALESCE(existing, new), an empty-string tenant would count as a real owner and
-- permanently block the correct tenant from ever being recorded. The Python writers
-- normalise '' -> None, but only 3 of the koi_memories writers are patched in this
-- phase, so THIS invariant is enforced in the schema where every writer must obey it.
-- Verified: without this, upserting '' then 'secondmuse' leaves the row owned by ''.
--
-- SCOPE OF THIS CONSTRAINT — it enforces BLANKNESS ONLY, not stickiness. Set-once
-- attribution is implemented per-writer via COALESCE(koi_memories.tenant_id,
-- EXCLUDED.tenant_id), which means an unpatched writer that later adds tenant_id to
-- its upsert list with plain EXCLUDED.tenant_id would silently re-attribute rows,
-- with no error and no way to detect it afterwards. If stickiness needs to be a real
-- database invariant rather than a convention, it belongs in a BEFORE UPDATE trigger.
-- Not done here: at present exactly one writer set exists and all of it is patched.
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

-- Composite for the eventual "active rows for tenant X" read pattern. NOTE the column
-- order: 015's idx_koi_memories_active_privacy leads with superseded_at, but under this
-- index's own partial predicate superseded_at is constant-NULL, so leading with it would
-- be informationally dead. tenant_id leads instead — verified with EXPLAIN, which puts
-- the Index Cond on tenant_id and none on superseded_at.
CREATE INDEX IF NOT EXISTS idx_koi_memories_active_tenant
  ON koi_memories(tenant_id, superseded_at)
  WHERE superseded_at IS NULL AND tenant_id IS NOT NULL;

-- NOTE ON LOCKING (measured on a same-size replica, PG 15; an earlier version of this
-- comment had the lock model inverted):
--   ALTER TABLE ADD COLUMN        -> AccessExclusiveLock, catalog-only in PG11+,
--                                    no table rewrite. ~5 ms.
--   ALTER TABLE ADD CONSTRAINT    -> AccessExclusiveLock, and it BLOCKS READERS
--     ... CHECK                      during a full heap validation scan. ~60-70 ms.
--                                    This is the statement that actually blocks queries.
--   CREATE INDEX (non-concurrent) -> ShareLock: blocks WRITERS only. SELECTs run fine.
--                                    ~50 ms each.
-- Sizing: the relevant figure is the HEAP (~178 MB), not pg_total_relation_size
-- (~577 MB, which includes ~192 MB of indexes and ~207 MB of TOAST). tenant_id is
-- not TOASTed, so validation scans the heap only.
-- On a much larger table, or outside a transaction, prefer CREATE INDEX CONCURRENTLY
-- (which cannot run inside a transaction block).
--
-- NOTE ON DEPLOY ORDER — this is load-bearing, not advisory:
--   RUN THIS MIGRATION BEFORE DEPLOYING THE CODE THAT WRITES tenant_id.
--   The patched writers name tenant_id in their INSERT column list, so against a
--   pre-108 database every write raises
--     asyncpg.exceptions.UndefinedColumnError: column "tenant_id" ... does not exist
--   and the row is NOT written. Verified by reproduction on a pre-108 scratch DB:
--   rows written = 0. It fails loudly rather than silently, but it fails closed —
--   an ingest run against an unmigrated DB loses the documents for that run.
--   The same applies in reverse: do not roll this migration back while the patched
--   code is live. Roll back the code first.
--
-- NOTE ON ENFORCEMENT: this migration does NOT make it safe to admit an external
-- user. koi-query-api.ts buildPrivacyFilter() still returns '' for any
-- authenticated caller ("Authenticated users see all data"), and access is gated
-- solely by a hardcoded @regen.network email check. Capture is not enforcement.
