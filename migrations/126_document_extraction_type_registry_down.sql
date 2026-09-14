-- =============================================================================
-- Migration 126 DOWN: un-designate Document and Event as extractable
-- =============================================================================
-- Date:     2026-09-14
-- Issue:    #68
--
-- Apply:    psql -d personal_koi -v ON_ERROR_STOP=1 -1 -f migrations/126_document_extraction_type_registry_down.sql
--
-- =============================================================================
-- READ THIS BEFORE RUNNING IT
-- =============================================================================
-- This does NOT delete the Document or Event rows, and must not: 390 live
-- entities carry those two types, with 598 relationship edges and 498 document
-- links between them (tests/test_canonical_entity_types.py records the
-- measurement). Deleting the rows would also break allowed_facets' foreign key.
--
-- It clears only the `extractable` designation, returning the registry to
-- migration 111's seven. That is the correct inverse of what 126 asserts.
--
-- Running this while the extractor still admits nine types puts the registry and
-- the code back into exactly the disagreement issue #68 was filed about, and
-- tests/test_document_extraction_type_contract.py will say so. Roll the code back
-- too, or do not roll this back.
-- =============================================================================

UPDATE allowed_entity_types
   SET extractable = false
 WHERE entity_type IN ('Document', 'Event');

DO $$
DECLARE
    n INTEGER;
BEGIN
    SELECT count(*) INTO n
      FROM allowed_entity_types
     WHERE extractable AND deprecated_at IS NULL;

    IF n <> 7 THEN
        RAISE EXCEPTION
            'expected 7 extractable entity types after rollback, found %. The registry '
            'held a designation this migration did not create; inspect before proceeding.', n;
    END IF;

    RAISE NOTICE 'rollback assertion PASSED (extractable entity types = 7)';
END $$;
