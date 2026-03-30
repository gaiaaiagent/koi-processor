-- 081_coverage_links.sql
-- Explicit relational primitive for gap computation.
-- Coverage links connect artifacts that satisfy requirements:
--   commitment covers requirement
--   claim covers condition
--   evidence covers commitment
--
-- Gap computation: for each active requirement, query coverage_links where
-- target_rid = requirement_rid AND valid_until > now(). No valid coverage → gap.
--
-- Part of the Claims × Spore protocol layer (additive, no rewrites).

CREATE TABLE IF NOT EXISTS coverage_links (
    id              SERIAL PRIMARY KEY,
    coverage_rid    TEXT UNIQUE NOT NULL,               -- deterministic RID
    coverage_type   TEXT NOT NULL
        CHECK (coverage_type IN (
            'commitment_covers_requirement',
            'claim_covers_condition',
            'evidence_covers_commitment'
        )),
    source_rid      TEXT NOT NULL,                      -- RID of the covering artifact
    target_rid      TEXT NOT NULL,                      -- RID of the covered artifact
    valid_from      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_until     TIMESTAMPTZ,                        -- NULL = open-ended; computed from freshness_window if recurrent
    confidence      FLOAT,                              -- 0–1
    provenance      TEXT                                -- manual | ai_inferred | policy_rule
        CHECK (provenance IS NULL OR provenance IN ('manual', 'ai_inferred', 'policy_rule')),
    metadata        JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Predicates for coverage relationships
INSERT INTO allowed_predicates (predicate, description, subject_types, object_types)
VALUES
    ('covers', 'Artifact satisfies or fulfills a requirement, condition, or commitment',
        ARRAY['Commitment', 'Claim', 'Evidence'],
        ARRAY['Requirement', 'Commitment', 'Condition'])
ON CONFLICT (predicate) DO NOTHING;

-- Indexes for gap computation
CREATE INDEX IF NOT EXISTS idx_coverage_target     ON coverage_links(target_rid);
CREATE INDEX IF NOT EXISTS idx_coverage_source     ON coverage_links(source_rid);
CREATE INDEX IF NOT EXISTS idx_coverage_type       ON coverage_links(coverage_type);
-- Note: partial indexes with NOW() are not allowed (must be immutable).
-- Use a plain composite index instead; gap queries filter at runtime.
CREATE INDEX IF NOT EXISTS idx_coverage_valid      ON coverage_links(target_rid, valid_until);

-- Composite for gap queries: "find all valid coverage for this requirement"
CREATE INDEX IF NOT EXISTS idx_coverage_gap_check
    ON coverage_links(target_rid, coverage_type, valid_until);

-- Register migration
INSERT INTO koi_migrations (migration_id, checksum)
VALUES ('personal:081_coverage_links', 'v1_protocol_layer')
ON CONFLICT (migration_id) DO NOTHING;
