-- =============================================================================
-- Migration 127: knowledge-fact retraction ledger + per-peer delivery states
-- =============================================================================
-- Date:     2026-09-15 (state vocabulary + shape assertion revised 2026-09-17,
--           before any application; this file has never been applied anywhere)
-- Issue:    #67 — federated fact retractions and recipient-application proof
-- Database: personal_koi (PostgreSQL 14.15)
--
-- Apply:    psql -d personal_koi -v ON_ERROR_STOP=1 -f migrations/127_fact_retraction_ledger.sql
-- Rollback: psql -d personal_koi -v ON_ERROR_STOP=1 -f migrations/127_fact_retraction_ledger_down.sql
--
-- WHY A MIGRATION, AND WHY NOT koi_net_events ALONE
-- -----------------------------------------------------------------------------
-- `koi_net_events` CAN carry the outbound intent transactionally: `EventQueue.add`
-- now accepts a caller-supplied connection, so the event row commits with the
-- `knowledge_facts.valid_to` write or not at all. What it cannot do, read out of
-- api/event_queue.py and proved in tests/test_fact_retraction_boundary.py:
--
--   * `delivered_to` is appended for events the poll filter EXCLUDED as well as
--     for events it handed over (the starvation fix), so it is not receipt.
--   * `confirmed_by` is receipt (`EventQueue.confirm` docstring), not application.
--     There is no column that could hold an application result.
--   * A polled-but-unconfirmed event is never redelivered: poll() excludes on
--     `delivered_to`, which it set at hand-over. The "will re-deliver on next
--     poll" comment in koi_poller is false. Retry needs its own record.
--   * Rows expire (24h default, 72h remote) and `cleanup()` deletes them. The
--     obligation to a peer that was offline for three days must outlive the row.
--
-- So the transport row stays what it is, and the OBLIGATION lives here.
--
-- TWO TABLES, TWO ROLES
-- -----------------------------------------------------------------------------
-- knowledge_fact_retractions — the tombstone ledger. One row per (fact, valid_to,
--   origin). Written by BOTH roles:
--     publisher: in the retraction transaction, origin_node = this node.
--     recipient: on receipt of a retraction event, origin_node = the sender.
--       `applied_at` NULL means the tombstone is PENDING — the fact is not
--       present locally yet (UPDATE-before-NEW). The episode/fact apply paths
--       consult pending rows so a late NEW lands already tombstoned.
--   `valid_to` is the committed knowledge_facts.valid_to, copied exactly. It is
--   never constructed.
--
-- knowledge_fact_retraction_deliveries — publisher-only, one row per
--   (retraction, target peer). The state column is the operator's evidence and
--   is deliberately finer than koi_net_events can express:
--     queued        outbox row + unicast koi_net_events row committed together
--     delivered     the publisher's poll() handed the event to this peer
--     received      the peer confirmed receipt (koi-net confirm) — NOT application
--     applied       the peer REPORTED application (confirm payload `applications`)
--     pending       the peer reported the fact is ABSENT there and recorded a
--                   pending tombstone (its apply paths land it when the fact
--                   arrives). Not application, not terminal: a later signed
--                   report may advance it. The sweep leaves it alone.
--     rejected      the peer reported it could not apply (report retained verbatim)
--     retrying      the prior event expired without an application report; a
--                   fresh event was queued (attempt incremented)
--     failed        terminal: attempts exhausted, or the peer's edge was revoked
--                   while an obligation was open
--     unverifiable  received, but the event expired without any application
--                   report — an older peer, or a lost report. Cannot be told
--                   apart from "applied" from here without the lookup slice.
--     unauthorized  a peer that held (or may hold) the original fact but whose
--                   edge no longer admits knowledge_fact; nothing was sent.
--   `event_id` is the CURRENT koi_net_events row for this delivery (NULL for
--   unauthorized). Earlier attempts' ids are kept in `attempt_history`.
--
-- Nothing here touches knowledge_facts, koi_net_events or any graph table.
-- Dry run + negative controls: tests/test_migration_127.py (scratch DB, rolled back).
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS knowledge_fact_retractions (
    id               BIGSERIAL PRIMARY KEY,
    fact_id          UUID        NOT NULL,
    valid_to         TIMESTAMPTZ NOT NULL,
    origin_node      TEXT        NOT NULL,
    episode_id       UUID,
    reason           TEXT,
    retracted_by     TEXT,
    source_document  TEXT,
    source_node_rid  TEXT,
    fact_snapshot    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    applied_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT knowledge_fact_retractions_fact_valid_origin_key
        UNIQUE (fact_id, valid_to, origin_node)
);

CREATE INDEX IF NOT EXISTS idx_kfr_fact_id
    ON knowledge_fact_retractions (fact_id);

-- Pending tombstones: consulted by the recipient apply paths on every fact insert.
CREATE INDEX IF NOT EXISTS idx_kfr_pending
    ON knowledge_fact_retractions (fact_id)
    WHERE applied_at IS NULL;

CREATE TABLE IF NOT EXISTS knowledge_fact_retraction_deliveries (
    id               BIGSERIAL PRIMARY KEY,
    retraction_id    BIGINT      NOT NULL
        REFERENCES knowledge_fact_retractions (id) ON DELETE CASCADE,
    target_node      TEXT        NOT NULL,
    event_id         UUID,
    attempt          INTEGER     NOT NULL DEFAULT 1 CHECK (attempt >= 0),
    attempt_history  JSONB       NOT NULL DEFAULT '[]'::jsonb,
    state            TEXT        NOT NULL,
    state_reason     TEXT,
    application      JSONB,
    queued_at        TIMESTAMPTZ,
    delivered_at     TIMESTAMPTZ,
    received_at      TIMESTAMPTZ,
    applied_at       TIMESTAMPTZ,
    state_changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT knowledge_fact_retraction_deliveries_retraction_target_key
        UNIQUE (retraction_id, target_node),
    CONSTRAINT knowledge_fact_retraction_deliveries_state_check CHECK (
        state IN ('queued', 'delivered', 'received', 'applied', 'pending', 'rejected',
                  'retrying', 'failed', 'unverifiable', 'unauthorized')
    ),
    -- An obligation that was never queued cannot have an event; one that was
    -- must. `unauthorized` is the only state minted without a transport row.
    CONSTRAINT knowledge_fact_retraction_deliveries_event_presence_check CHECK (
        (state = 'unauthorized' AND event_id IS NULL)
        OR (state <> 'unauthorized' AND event_id IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_kfrd_event_id
    ON knowledge_fact_retraction_deliveries (event_id)
    WHERE event_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_kfrd_state
    ON knowledge_fact_retraction_deliveries (state);

CREATE INDEX IF NOT EXISTS idx_kfrd_target_state
    ON knowledge_fact_retraction_deliveries (target_node, state);

COMMENT ON TABLE knowledge_fact_retractions IS
    'Tombstone ledger for knowledge_facts (issue #67). Publisher writes in the retraction transaction; recipients write on receipt. applied_at NULL = pending (fact not present yet).';
COMMENT ON TABLE knowledge_fact_retraction_deliveries IS
    'Per-peer delivery/application state for a retraction (issue #67). Finer than koi_net_events.delivered_to/confirmed_by, which conflate scope-exclusion with delivery and receipt with application.';
COMMENT ON COLUMN knowledge_fact_retractions.valid_to IS
    'The committed knowledge_facts.valid_to, copied exactly. Never constructed.';
COMMENT ON COLUMN knowledge_fact_retraction_deliveries.state IS
    'queued | delivered | received | applied | pending | rejected | retrying | failed | unverifiable | unauthorized';

-- -----------------------------------------------------------------------------
-- Assertion. Exact shape, not "the tables exist". `CREATE TABLE IF NOT EXISTS`
-- keeps a pre-existing table AS IT IS, so a drifted table (a hand-made one, a
-- half-applied earlier draft) would pass a mere existence check and then fail
-- at runtime as a CheckViolation or a missing column. Asserted here:
--   * the state vocabulary is EXACTLY the ten states the ledger module names —
--     no missing state, no extra one (an extra state is drift too);
--   * the two UNIQUE keys the code upserts on have exactly these columns, in
--     this order;
--   * the event-presence CHECK exists;
--   * every column the module reads or writes exists on each table.
-- Each failure names what is wrong.
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    expected_states TEXT[] := ARRAY['applied', 'delivered', 'failed', 'pending', 'queued',
                                    'received', 'rejected', 'retrying',
                                    'unauthorized', 'unverifiable'];
    found_states    TEXT[];
    state_check     TEXT;
    have_presence   BOOLEAN;
    unique_cols     TEXT[];
    expected_cols   TEXT[];
    missing_cols    TEXT[];
BEGIN
    -- 1. state vocabulary, exactly
    SELECT pg_get_constraintdef(oid) INTO state_check
      FROM pg_constraint
     WHERE conname = 'knowledge_fact_retraction_deliveries_state_check'
       AND conrelid = 'knowledge_fact_retraction_deliveries'::regclass;
    IF state_check IS NULL THEN
        RAISE EXCEPTION '127: state CHECK constraint is missing';
    END IF;
    SELECT array_agg(m[1] ORDER BY m[1]) INTO found_states
      FROM regexp_matches(state_check, $re$'([a-z_]+)'$re$, 'g') AS m;
    IF found_states IS DISTINCT FROM expected_states THEN
        RAISE EXCEPTION '127: state CHECK vocabulary is % but must be exactly % (definition: %)',
            found_states, expected_states, state_check;
    END IF;

    -- 2. unique keys, exact columns in order
    SELECT array_agg(a.attname ORDER BY k.ord) INTO unique_cols
      FROM pg_constraint c
      CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
      JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
     WHERE c.conname = 'knowledge_fact_retractions_fact_valid_origin_key'
       AND c.conrelid = 'knowledge_fact_retractions'::regclass
       AND c.contype = 'u';
    IF unique_cols IS DISTINCT FROM ARRAY['fact_id', 'valid_to', 'origin_node'] THEN
        RAISE EXCEPTION '127: knowledge_fact_retractions unique key is % but must be (fact_id, valid_to, origin_node)',
            unique_cols;
    END IF;
    SELECT array_agg(a.attname ORDER BY k.ord) INTO unique_cols
      FROM pg_constraint c
      CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
      JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
     WHERE c.conname = 'knowledge_fact_retraction_deliveries_retraction_target_key'
       AND c.conrelid = 'knowledge_fact_retraction_deliveries'::regclass
       AND c.contype = 'u';
    IF unique_cols IS DISTINCT FROM ARRAY['retraction_id', 'target_node'] THEN
        RAISE EXCEPTION '127: knowledge_fact_retraction_deliveries unique key is % but must be (retraction_id, target_node)',
            unique_cols;
    END IF;

    -- 3. event-presence check
    SELECT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'knowledge_fact_retraction_deliveries_event_presence_check'
                      AND conrelid = 'knowledge_fact_retraction_deliveries'::regclass)
      INTO have_presence;
    IF NOT have_presence THEN
        RAISE EXCEPTION '127: knowledge_fact_retraction_deliveries_event_presence_check is missing';
    END IF;

    -- 4. columns the module reads/writes
    expected_cols := ARRAY['id', 'fact_id', 'valid_to', 'origin_node', 'episode_id', 'reason',
                           'retracted_by', 'source_document', 'source_node_rid', 'fact_snapshot',
                           'applied_at', 'created_at'];
    SELECT array_agg(c ORDER BY c) INTO missing_cols
      FROM unnest(expected_cols) AS c
     WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'knowledge_fact_retractions'
                          AND column_name = c);
    IF missing_cols IS NOT NULL THEN
        RAISE EXCEPTION '127: knowledge_fact_retractions lacks column(s) %', missing_cols;
    END IF;
    expected_cols := ARRAY['id', 'retraction_id', 'target_node', 'event_id', 'attempt',
                           'attempt_history', 'state', 'state_reason', 'application', 'queued_at',
                           'delivered_at', 'received_at', 'applied_at', 'state_changed_at',
                           'created_at'];
    SELECT array_agg(c ORDER BY c) INTO missing_cols
      FROM unnest(expected_cols) AS c
     WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'knowledge_fact_retraction_deliveries'
                          AND column_name = c);
    IF missing_cols IS NOT NULL THEN
        RAISE EXCEPTION '127: knowledge_fact_retraction_deliveries lacks column(s) %', missing_cols;
    END IF;

    RAISE NOTICE '127: assertion PASSED (ledger + deliveries, % states, both unique keys, all columns)',
        cardinality(expected_states);
END $$;

INSERT INTO koi_migrations (migration_id, checksum)
VALUES ('personal:127_fact_retraction_ledger', 'v2_retraction_ledger_pending_state')
ON CONFLICT (migration_id) DO NOTHING;

COMMIT;
