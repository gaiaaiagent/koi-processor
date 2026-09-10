-- 107_entity_visibility_scope.sql
-- Phase 1, KG two-viewport plan (~/.claude/plans/design-the-indigenomics-twinkly-dijkstra.md):
-- add the 4-value AUDIENCE visibility axis to entity_registry.
--
-- CORRECTED per read-only recon (B1): entity_registry ONLY. Do NOT add a CHECK to
-- entity_rid_mappings.visibility_scope — that is a SEPARATE, pre-existing 2-value axis
-- (public / node_private = Notion-projection privacy). A 4-value CHECK there would
-- CheckViolation-kill vault->registry sync on the first node_private write.
--
-- Additive + metadata-only (constant DEFAULT, no row rewrite on ~21k rows). No code reads
-- entity_registry.visibility_scope yet (recon: 0 readers), so the 'unclassified' default
-- cannot blank any read path. Reversible via 107_entity_visibility_scope_down.sql.
-- Apply directly: psql personal_koi -v ON_ERROR_STOP=1 -f 107_entity_visibility_scope.sql
BEGIN;

ALTER TABLE entity_registry
  ADD COLUMN IF NOT EXISTS visibility_scope TEXT NOT NULL DEFAULT 'unclassified',
  ADD COLUMN IF NOT EXISTS source_context   TEXT,
  ADD COLUMN IF NOT EXISTS expires_at       TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS revoked_at       TIMESTAMPTZ;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'entity_registry_visibility_scope_check'
      AND conrelid = 'public.entity_registry'::regclass
  ) THEN
    ALTER TABLE entity_registry
      ADD CONSTRAINT entity_registry_visibility_scope_check
      CHECK (visibility_scope IN ('public', 'team', 'confidential', 'unclassified'));
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_entity_registry_visibility_scope
  ON entity_registry (visibility_scope);

COMMIT;
