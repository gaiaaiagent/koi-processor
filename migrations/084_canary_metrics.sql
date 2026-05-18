-- Migration 084: Time-series canary metrics for KOI federation round-trip tests.
--
-- The existing `vault_sync_metrics` table is a singleton (id=1) holding a
-- JSONB blob of vault-sync counters. It is not suited for time-series rows.
--
-- This migration adds a dedicated table `koi_canary_metrics` for daily
-- federation canary results (one row per canary run).
--
-- Columns:
--   id            — surrogate primary key
--   canary_id     — UUID v7 of the canary file written to the shared vault
--   status        — 'ok' | 'timeout' | 'error'
--   duration_seconds — round-trip seconds, NULL on timeout
--   recorded_at   — when the result was persisted
--   detail        — free-form JSONB context (canary_id, error msgs, etc.)
BEGIN;

CREATE TABLE IF NOT EXISTS koi_canary_metrics (
    id BIGSERIAL PRIMARY KEY,
    canary_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'timeout', 'error')),
    duration_seconds DOUBLE PRECISION,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    detail JSONB
);

CREATE INDEX IF NOT EXISTS idx_koi_canary_metrics_recorded_at
    ON koi_canary_metrics(recorded_at DESC);

CREATE INDEX IF NOT EXISTS idx_koi_canary_metrics_status
    ON koi_canary_metrics(status);

COMMIT;
