-- Migration 080: Persisted backoff state per peer.
-- Replaces KoiPoller._backoff in-memory dict so backoff survives restart.
--
-- Note: Spec referenced table `koi_net_peers` which does not exist in this
-- schema. Backoff state is keyed by node_rid (see api/koi_poller.py:91), so
-- we attach the columns to koi_net_nodes (the canonical node table keyed by
-- node_rid).

BEGIN;

ALTER TABLE koi_net_nodes
  ADD COLUMN IF NOT EXISTS backoff_until TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS backoff_streak INT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_success_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_koi_nodes_backoff_until
  ON koi_net_nodes(backoff_until)
  WHERE backoff_until IS NOT NULL;

COMMIT;
