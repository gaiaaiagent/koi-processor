-- Migration 083: Key-value runtime state (for health endpoint, sweep timestamps).
BEGIN;
CREATE TABLE IF NOT EXISTS koi_net_runtime_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMIT;
