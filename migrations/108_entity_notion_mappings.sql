-- Migration 106: entity_notion_mappings + projection_checkpoints (KG → Notion projection, Phase 0)
--
-- The Notion analogue of entity_rid_mappings (the vault projection table). Projects a
-- canonical personal-KOI entity onto a Notion page with independent sync state. Keyed on
-- (canonical_uri, notion_database_id) because a single entity may legitimately have one
-- page per Notion DB (Team Members DB vs the unified Entities DB). One LIVE page per
-- (canonical_uri, db) is enforced by a partial unique index; merged losers are archived,
-- not deleted, so history survives and the invariant holds.
--
-- Spec: ~/.claude/plans/kg-notion-entity-projection-spec.md §7.1. Additive; touches no
-- existing table. Safe to re-run (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS entity_notion_mappings (
  notion_page_id     TEXT UNIQUE NOT NULL,
  canonical_uri      TEXT NOT NULL,               -- → entity_registry.fuseki_uri
  notion_database_id TEXT NOT NULL,
  entity_type        TEXT,
  name               TEXT,
  content_hash       TEXT,
  sync_status        TEXT DEFAULT 'linked'
                     CHECK (sync_status IN ('linked','local_only','pending_sync','conflict','archived')),
  relation_truncated BOOLEAN DEFAULT FALSE,        -- degree > 100 (spec §5.5)
  last_synced        TIMESTAMPTZ,
  created_at         TIMESTAMPTZ DEFAULT NOW(),
  visibility_scope   TEXT DEFAULT 'public'
);

-- One LIVE page per (canonical_uri, db). Archived rows excluded so a merged loser can
-- coexist historically without colliding with the survivor.
CREATE UNIQUE INDEX IF NOT EXISTS uq_notion_live_uri_db
  ON entity_notion_mappings (canonical_uri, notion_database_id)
  WHERE sync_status <> 'archived';

CREATE INDEX IF NOT EXISTS idx_notion_mappings_canonical
  ON entity_notion_mappings (canonical_uri);

-- Merge-poller checkpoint (Phase 3): monotonic high-water mark on entity_merge_log.id,
-- committed in the SAME transaction as the loser-row retire.
CREATE TABLE IF NOT EXISTS projection_checkpoints (
  name              TEXT PRIMARY KEY,             -- e.g. 'notion_entity_merge_poller'
  last_processed_id BIGINT NOT NULL DEFAULT 0,
  updated_at        TIMESTAMPTZ DEFAULT NOW()
);
