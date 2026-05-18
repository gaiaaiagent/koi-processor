-- Migration 082: Preserve manifest + source_node across DLQ round-trip.
-- Lossless replay requires these fields — _process_event reads manifest for
-- share/commons routing and source_node feeds koi_net_cross_refs.

BEGIN;

ALTER TABLE koi_net_events_dead
  ADD COLUMN IF NOT EXISTS manifest JSONB,
  ADD COLUMN IF NOT EXISTS source_node TEXT;

COMMIT;
