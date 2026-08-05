-- 107_entity_visibility_scope_down.sql — reverses 107 (entity_registry only).
-- Does NOT touch entity_rid_mappings.visibility_scope (a separate pre-existing column).
-- Apply: psql personal_koi -v ON_ERROR_STOP=1 -f 107_entity_visibility_scope_down.sql
BEGIN;

DROP INDEX IF EXISTS idx_entity_registry_visibility_scope;

ALTER TABLE entity_registry
  DROP CONSTRAINT IF EXISTS entity_registry_visibility_scope_check;

ALTER TABLE entity_registry
  DROP COLUMN IF EXISTS visibility_scope,
  DROP COLUMN IF EXISTS source_context,
  DROP COLUMN IF EXISTS expires_at,
  DROP COLUMN IF EXISTS revoked_at;

COMMIT;
