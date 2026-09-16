-- =============================================================================
-- Migration 127 DOWN: drop the fact-retraction ledger and delivery states
-- =============================================================================
-- Date:     2026-09-15
-- Issue:    #67
--
-- Apply:    psql -d personal_koi -v ON_ERROR_STOP=1 -f migrations/127_fact_retraction_ledger_down.sql
--
-- READ THIS BEFORE RUNNING IT
-- -----------------------------------------------------------------------------
-- These two tables are the ONLY record of (a) which peers were sent which
-- tombstones and what each reported, and (b) PENDING tombstones on a recipient —
-- retractions that arrived before the fact they retract. Dropping (b) on a
-- recipient means a late-arriving NEW for such a fact lands LIVE. Dropping (a)
-- on the publisher makes every open obligation invisible to the audit and the
-- retry sweep.
--
-- The rows live nowhere else, so this file exports both tables before dropping
-- anything — placed BELOW ON_ERROR_STOP so a failed export aborts the rollback
-- (house pattern of 125; paths are LITERAL because `\copy` does not interpolate
-- :'var' inside a filename). knowledge_facts.valid_to is untouched: rolling
-- back the LEDGER does not un-retract anything.
-- =============================================================================

\set ON_ERROR_STOP on

-- Export first. If either export fails, ON_ERROR_STOP aborts before any DROP.
-- Guarded on table existence so a re-run after a half-finished rollback (tables
-- already gone, ledger row still present) can complete instead of aborting on
-- the export (review finding 17). Paths are fixed: a re-run overwrites them.
SELECT to_regclass('knowledge_fact_retractions') IS NOT NULL AS has_retractions, to_regclass('knowledge_fact_retraction_deliveries') IS NOT NULL AS has_deliveries \gset
\if :has_retractions
\copy (SELECT * FROM knowledge_fact_retractions ORDER BY id) TO '/tmp/koi_127_down_retractions.csv' WITH CSV HEADER
\endif
\if :has_deliveries
\copy (SELECT * FROM knowledge_fact_retraction_deliveries ORDER BY id) TO '/tmp/koi_127_down_deliveries.csv' WITH CSV HEADER
\endif

BEGIN;

DROP TABLE IF EXISTS knowledge_fact_retraction_deliveries;
DROP TABLE IF EXISTS knowledge_fact_retractions;

DO $$
BEGIN
    IF to_regclass('knowledge_fact_retraction_deliveries') IS NOT NULL
       OR to_regclass('knowledge_fact_retractions') IS NOT NULL THEN
        RAISE EXCEPTION '127 rollback: a ledger table still exists';
    END IF;
    RAISE NOTICE '127 rollback: assertion PASSED (ledger tables dropped)';
END $$;

DELETE FROM koi_migrations
 WHERE migration_id = 'personal:127_fact_retraction_ledger';

COMMIT;
