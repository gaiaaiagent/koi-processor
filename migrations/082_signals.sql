-- 082_signals.sql
-- Raw observations, declarations, discourse tensions, or computed gaps.
-- Signals are pre-intent: not yet interpreted into directional action.
-- The gap_computed type is emitted by the negative-space intelligence engine
-- when a requirement has no valid coverage.
--
-- Part of the Claims × Spore protocol layer (additive, no rewrites).

CREATE TABLE IF NOT EXISTS signals (
    id              SERIAL PRIMARY KEY,
    signal_rid      TEXT UNIQUE NOT NULL,               -- orn:koi-net.signal:<hash>
    signal_type     TEXT NOT NULL
        CHECK (signal_type IN ('declaration', 'discourse', 'gap_computed', 'sensor', 'document_extract')),
    source_kind     TEXT NOT NULL,                      -- What produced it (transcript, gap_computation, sensor, etc.)
    source_ref      TEXT,                               -- URI of source document/pool/computation
    statement       TEXT NOT NULL,                      -- Human-readable description
    scope           TEXT NOT NULL,                      -- personal | team | pool | org | federation | on_chain_group
    subject_uri     TEXT,                               -- entity_registry.fuseki_uri
    metadata        JSONB DEFAULT '{}'::jsonb,
    confidence      FLOAT,                              -- 0–1, NULL if not applicable
    fresh_until     TIMESTAMPTZ,                        -- When this signal becomes stale
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Junction table: signals → intents (many-to-many)
-- Links to the existing intent_registry (migration 074)
CREATE TABLE IF NOT EXISTS signal_intents (
    id              SERIAL PRIMARY KEY,
    signal_rid      TEXT NOT NULL,                      -- signals.signal_rid
    intent_rid      TEXT NOT NULL,                      -- intent_registry.intent_rid
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (signal_rid, intent_rid)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_signals_type        ON signals(signal_type);
CREATE INDEX IF NOT EXISTS idx_signals_scope       ON signals(scope);
CREATE INDEX IF NOT EXISTS idx_signals_source_ref  ON signals(source_ref);
CREATE INDEX IF NOT EXISTS idx_signals_subject     ON signals(subject_uri);
CREATE INDEX IF NOT EXISTS idx_signals_fresh       ON signals(fresh_until)
    WHERE fresh_until IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_signal_intents_sig  ON signal_intents(signal_rid);
CREATE INDEX IF NOT EXISTS idx_signal_intents_int  ON signal_intents(intent_rid);

-- Register migration
INSERT INTO koi_migrations (migration_id, checksum)
VALUES ('personal:082_signals', 'v1_protocol_layer')
ON CONFLICT (migration_id) DO NOTHING;
