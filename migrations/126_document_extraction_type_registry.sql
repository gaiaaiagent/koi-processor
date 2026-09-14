-- =============================================================================
-- Migration 126: register Document and Event as document-extractable types
-- =============================================================================
-- Date:     2026-09-14
-- Issue:    #68 — align the deep-document extraction type contract
-- Database: personal_koi (PostgreSQL 14.15)
--
-- Apply:    psql -d personal_koi -v ON_ERROR_STOP=1 -1 -f migrations/126_document_extraction_type_registry.sql
-- Rollback: psql -d personal_koi -v ON_ERROR_STOP=1 -1 -f migrations/126_document_extraction_type_registry_down.sql
--
-- =============================================================================
-- THIS IS A NO-OP ON THE LAPTOP AND THAT IS THE POINT
-- =============================================================================
-- Verified read-only 2026-09-14 against live personal_koi:
--
--   SELECT entity_type, extractable FROM allowed_entity_types
--    WHERE entity_type IN ('Document','Event');
--   -- Document | t
--   -- Event    | t
--
-- Both rows already exist with extractable = true. They were INSERTed directly,
-- not by any migration — `docs/operations/two-node-topology.md` records that the
-- laptop's allowed_entity_types holds 32 rows against the NUC's 28, and that the
-- four extra ones appear in no migration file anywhere.
--
-- So this migration does not change the laptop. It exists because the REGISTRY
-- was right and the CODE was wrong, and a database rebuilt from migrations/ would
-- reproduce the wrong half: migration 111 seeds 28 types with `extractable = true`
-- for exactly seven, and its own comment says "exactly the 7 the deep-extraction
-- enum can emit". That enum is now nine. Without this file, a fresh personal_koi
-- would fail tests/test_document_extraction_type_contract.py
-- ::test_the_live_registry_and_the_contract_designate_the_same_extractable_set —
-- correctly, because it would genuinely disagree with the extractor.
--
-- Written as an idempotent upsert so it is safe on a node that already has the
-- rows (no-op) and correct on one that does not (insert). It touches nothing in
-- entity_registry, knowledge_facts, or any other graph table.
-- =============================================================================

INSERT INTO allowed_entity_types (entity_type, description, extractable) VALUES
    ('Document',
     'A discrete written work ingested from a corpus: paper, guide, report, or article.',
     true),
    ('Event',
     'A dated happening that is not a Meeting: a conference, launch, incident, or milestone occurrence.',
     true)
ON CONFLICT (entity_type) DO UPDATE
    SET extractable = true,
        deprecated_at = NULL;


-- -----------------------------------------------------------------------------
-- Assertion. A migration that reports success without establishing its
-- postcondition is the failure mode this repository keeps re-learning.
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    n INTEGER;
    names TEXT;
BEGIN
    SELECT count(*), string_agg(entity_type, ', ' ORDER BY entity_type)
      INTO n, names
      FROM allowed_entity_types
     WHERE extractable AND deprecated_at IS NULL;

    IF n <> 9 THEN
        RAISE EXCEPTION
            'expected exactly 9 extractable entity types after this migration, found % (%). '
            'The extractor''s contract (api/document_extraction_contract.py, '
            'doc-entity-types-v2-2026-09-14) admits: Person, Organization, Project, Concept, '
            'Location, Protocol, CaseStudy, Document, Event.', n, names;
    END IF;

    RAISE NOTICE 'assertion PASSED (extractable entity types = 9: %)', names;
END $$;
