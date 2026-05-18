-- Migration 081: Dead-letter queue for koi_net_events.
-- Captures events that exhausted retry budget or TTL.

BEGIN;

CREATE TABLE IF NOT EXISTS koi_net_events_dead (
    id BIGSERIAL PRIMARY KEY,
    original_event_id BIGINT NOT NULL,
    rid TEXT NOT NULL,
    event_type TEXT NOT NULL,
    target_node TEXT,
    payload JSONB NOT NULL,
    delivery_attempts INT NOT NULL,
    original_expires_at TIMESTAMPTZ NOT NULL,
    moved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    dlq_reason TEXT NOT NULL CHECK (dlq_reason IN ('max_attempts', 'ttl_expired', 'manual'))
);

CREATE INDEX IF NOT EXISTS idx_dlq_target_node ON koi_net_events_dead(target_node);
CREATE INDEX IF NOT EXISTS idx_dlq_moved_at ON koi_net_events_dead(moved_at);
CREATE INDEX IF NOT EXISTS idx_dlq_reason ON koi_net_events_dead(dlq_reason);

COMMIT;
