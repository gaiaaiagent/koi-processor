-- Migration 085: extend koi_canary_metrics.status CHECK to include 'awaiting_echo'.
--
-- Task 527 (canary echo handshake) introduces a tolerant-mode status used while
-- Darren's echo watcher is being rolled out on Dobby. 'awaiting_echo' is set when:
--   - no echo file landed within timeout window AND
--   - our local original canary file still exists (proves write path is fine)
--
-- Once handshake is shipped + first green round-trip observed, ops can set
-- KOI_CANARY_AWAIT_ECHO=strict and this status becomes effectively unused, but
-- keeping it in the CHECK is forward-compatible.

BEGIN;

ALTER TABLE koi_canary_metrics DROP CONSTRAINT IF EXISTS koi_canary_metrics_status_check;
ALTER TABLE koi_canary_metrics ADD CONSTRAINT koi_canary_metrics_status_check
  CHECK (status = ANY (ARRAY['ok'::text, 'timeout'::text, 'awaiting_echo'::text, 'error'::text]));

COMMIT;
