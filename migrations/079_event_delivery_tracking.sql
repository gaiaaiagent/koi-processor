-- Migration 079: Delivery tracking for store-and-forward relay
-- Adds columns to track delivery attempts and DLQ reason on koi_net_events.
-- Additive only — safe to run on live service.

BEGIN;

ALTER TABLE koi_net_events
  ADD COLUMN IF NOT EXISTS delivery_attempts INT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_attempt_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS dlq_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_koi_events_delivery_attempts
  ON koi_net_events(delivery_attempts)
  WHERE delivery_attempts > 0;

COMMIT;
