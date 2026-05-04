-- 079_requirements.sql
-- Normative expectations with cadence — what a pool/org/federation constitution
-- says should be true. Generic across scopes; pool requirements are the first
-- use case but the table serves any normative artifact.
--
-- Part of the Claims × Spore protocol layer (additive, no rewrites).

CREATE TABLE IF NOT EXISTS requirements (
    id                      SERIAL PRIMARY KEY,
    requirement_rid         TEXT UNIQUE NOT NULL,          -- orn:koi-net.requirement:<hash>
    scope                   TEXT NOT NULL,                 -- personal | team | pool | org | federation | on_chain_group
    scope_ref               TEXT,                          -- URI of the scoped entity (pool_rid, org_uri, etc.)
    policy_source           TEXT NOT NULL,                 -- URI of the policy/constitution that declares this
    requirement_type        TEXT NOT NULL
        CHECK (requirement_type IN ('monitoring', 'reporting', 'stewardship', 'governance', 'contribution')),
    statement               TEXT NOT NULL,                 -- Human-readable requirement
    subject_uri             TEXT,                          -- entity_registry.fuseki_uri of the entity this concerns
    frequency               TEXT
        CHECK (frequency IS NULL OR frequency IN ('once', 'weekly', 'monthly', 'quarterly', 'annual')),
    freshness_window_days   INTEGER,                       -- How many days before coverage becomes stale
    severity                TEXT NOT NULL DEFAULT 'medium'
        CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    active                  BOOLEAN NOT NULL DEFAULT TRUE,
    metadata                JSONB DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Predicates for requirement entities
INSERT INTO allowed_predicates (predicate, description, subject_types, object_types)
VALUES
    ('requires', 'Pool, org, or policy declares a requirement',
        ARRAY['CommitmentPool', 'Organization', 'Policy', 'SpecDoc'],
        ARRAY['Requirement'])
ON CONFLICT (predicate) DO NOTHING;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_requirements_scope      ON requirements(scope);
CREATE INDEX IF NOT EXISTS idx_requirements_scope_ref   ON requirements(scope_ref);
CREATE INDEX IF NOT EXISTS idx_requirements_type        ON requirements(requirement_type);
CREATE INDEX IF NOT EXISTS idx_requirements_active      ON requirements(active) WHERE active = TRUE;
CREATE INDEX IF NOT EXISTS idx_requirements_severity    ON requirements(severity);
CREATE INDEX IF NOT EXISTS idx_requirements_subject     ON requirements(subject_uri);

-- Register migration
INSERT INTO koi_migrations (migration_id, checksum)
VALUES ('personal:079_requirements', 'v1_protocol_layer')
ON CONFLICT (migration_id) DO NOTHING;
