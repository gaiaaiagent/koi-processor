-- ============================================================================
-- FIX-015b: Cleanup Type-Invalid Relationships
-- ============================================================================
-- Purpose: Delete relationships that violate predicate-type constraints
-- Date: 2025-12-25
--
-- This script matches the PREDICATE_TYPE_CONSTRAINTS in:
--   src/extraction/predicate_guard.py
--
-- IMPORTANT: Run on PRODUCTION server (darren@202.61.196.119)
--   cd /opt/projects/koi-processor
--   set -a; source .env; set +a
--   psql -d eliza -f scripts/fix015b_cleanup_type_violations.sql
--
-- POST-CLEANUP: Fuseki must be rebuilt to reflect deletions in RDF store
--   python scripts/regenerate_fuseki_graph.py
-- ============================================================================

\echo '=============================================='
\echo 'FIX-015b: Type-Invalid Relationships Cleanup'
\echo '=============================================='
\echo ''

-- ============================================================================
-- STEP 1: CREATE BACKUP TABLE
-- ============================================================================
\echo 'STEP 1: Creating backup table for rollback...'

DROP TABLE IF EXISTS koi_relationships_backup_fix015b;

CREATE TABLE koi_relationships_backup_fix015b AS
SELECT r.*,
       subj.entity_type AS subject_type,
       obj.entity_type AS object_type
FROM koi_relationships r
JOIN entity_registry subj ON r.subject_entity_id = subj.id
JOIN entity_registry obj ON r.object_entity_id = obj.id
WHERE
    -- operates: blocked subjects CONCEPT, EVENT
    (r.predicate = 'operates' AND subj.entity_type IN ('CONCEPT', 'EVENT'))
    OR
    -- operates: blocked objects CONCEPT, MATERIAL, LOCATION, EVENT
    (r.predicate = 'operates' AND obj.entity_type IN ('CONCEPT', 'MATERIAL', 'LOCATION', 'EVENT'))
    OR
    -- founded: valid subjects PERSON, ORGANIZATION only
    (r.predicate = 'founded' AND subj.entity_type NOT IN ('PERSON', 'ORGANIZATION'))
    OR
    -- founded: valid objects ORGANIZATION, PROJECT, TECHNOLOGY only
    (r.predicate = 'founded' AND obj.entity_type NOT IN ('ORGANIZATION', 'PROJECT', 'TECHNOLOGY'))
    OR
    -- works_at: valid subjects PERSON only
    (r.predicate = 'works_at' AND subj.entity_type NOT IN ('PERSON'))
    OR
    -- works_at: valid objects ORGANIZATION, PROJECT, VALIDATOR only
    (r.predicate = 'works_at' AND obj.entity_type NOT IN ('ORGANIZATION', 'PROJECT', 'VALIDATOR'))
    OR
    -- employs: valid subjects ORGANIZATION only
    (r.predicate = 'employs' AND subj.entity_type NOT IN ('ORGANIZATION'))
    OR
    -- employs: valid objects PERSON only
    (r.predicate = 'employs' AND obj.entity_type NOT IN ('PERSON'))
    OR
    -- member_of: valid subjects PERSON, ORGANIZATION, VALIDATOR only
    (r.predicate = 'member_of' AND subj.entity_type NOT IN ('PERSON', 'ORGANIZATION', 'VALIDATOR'))
    OR
    -- member_of: valid objects ORGANIZATION, PROJECT only
    (r.predicate = 'member_of' AND obj.entity_type NOT IN ('ORGANIZATION', 'PROJECT'))
    OR
    -- leads: valid subjects PERSON, ORGANIZATION only
    (r.predicate = 'leads' AND subj.entity_type NOT IN ('PERSON', 'ORGANIZATION'))
    OR
    -- leads: valid objects ORGANIZATION, PROJECT, EVENT, PROCESS only
    (r.predicate = 'leads' AND obj.entity_type NOT IN ('ORGANIZATION', 'PROJECT', 'EVENT', 'PROCESS'))
    OR
    -- located_in: valid objects LOCATION only
    (r.predicate = 'located_in' AND obj.entity_type NOT IN ('LOCATION'))
    OR
    -- authored: valid subjects PERSON, ORGANIZATION only
    (r.predicate = 'authored' AND subj.entity_type NOT IN ('PERSON', 'ORGANIZATION'))
    OR
    -- validates: valid subjects VALIDATOR, ORGANIZATION, TECHNOLOGY only
    (r.predicate = 'validates' AND subj.entity_type NOT IN ('VALIDATOR', 'ORGANIZATION', 'TECHNOLOGY'))
    OR
    -- delegates: valid objects VALIDATOR, PERSON, ORGANIZATION only
    (r.predicate = 'delegates' AND obj.entity_type NOT IN ('VALIDATOR', 'PERSON', 'ORGANIZATION'))
    OR
    -- votes: valid subjects PERSON, ORGANIZATION, VALIDATOR only
    (r.predicate = 'votes' AND subj.entity_type NOT IN ('PERSON', 'ORGANIZATION', 'VALIDATOR'));

\echo 'Backup created: koi_relationships_backup_fix015b'
\echo ''

-- ============================================================================
-- STEP 2: PREFLIGHT COUNT BY CONSTRAINT TYPE
-- ============================================================================
\echo 'STEP 2: Preflight violation counts...'
\echo ''

SELECT
    predicate,
    subject_type,
    object_type,
    COUNT(*) AS violation_count
FROM koi_relationships_backup_fix015b
GROUP BY predicate, subject_type, object_type
ORDER BY violation_count DESC, predicate, subject_type, object_type;

\echo ''
\echo 'Total violations to delete:'
SELECT COUNT(*) AS total_violations FROM koi_relationships_backup_fix015b;
\echo ''

-- ============================================================================
-- STEP 3: DELETE IN TRANSACTION
-- ============================================================================
\echo 'STEP 3: Deleting violations in transaction...'
\echo ''

BEGIN;

DELETE FROM koi_relationships
WHERE id IN (SELECT id FROM koi_relationships_backup_fix015b);

-- Show deleted count
\echo 'Rows deleted:'
SELECT COUNT(*) AS deleted_count FROM koi_relationships_backup_fix015b;

COMMIT;

\echo ''
\echo 'Transaction committed.'
\echo ''

-- ============================================================================
-- STEP 4: POST-CHECK - VERIFY ZERO REMAINING VIOLATIONS
-- ============================================================================
\echo 'STEP 4: Post-check for remaining violations...'
\echo ''

SELECT
    'operates_bad_subject' AS constraint_type,
    COUNT(*) AS remaining
FROM koi_relationships r
JOIN entity_registry subj ON r.subject_entity_id = subj.id
WHERE r.predicate = 'operates' AND subj.entity_type IN ('CONCEPT', 'EVENT')

UNION ALL

SELECT
    'operates_bad_object' AS constraint_type,
    COUNT(*) AS remaining
FROM koi_relationships r
JOIN entity_registry obj ON r.object_entity_id = obj.id
WHERE r.predicate = 'operates' AND obj.entity_type IN ('CONCEPT', 'MATERIAL', 'LOCATION', 'EVENT')

UNION ALL

SELECT
    'founded_bad_subject' AS constraint_type,
    COUNT(*) AS remaining
FROM koi_relationships r
JOIN entity_registry subj ON r.subject_entity_id = subj.id
WHERE r.predicate = 'founded' AND subj.entity_type NOT IN ('PERSON', 'ORGANIZATION')

UNION ALL

SELECT
    'founded_bad_object' AS constraint_type,
    COUNT(*) AS remaining
FROM koi_relationships r
JOIN entity_registry obj ON r.object_entity_id = obj.id
WHERE r.predicate = 'founded' AND obj.entity_type NOT IN ('ORGANIZATION', 'PROJECT', 'TECHNOLOGY')

UNION ALL

SELECT
    'leads_bad_object' AS constraint_type,
    COUNT(*) AS remaining
FROM koi_relationships r
JOIN entity_registry obj ON r.object_entity_id = obj.id
WHERE r.predicate = 'leads' AND obj.entity_type NOT IN ('ORGANIZATION', 'PROJECT', 'EVENT', 'PROCESS')

UNION ALL

SELECT
    'located_in_bad_object' AS constraint_type,
    COUNT(*) AS remaining
FROM koi_relationships r
JOIN entity_registry obj ON r.object_entity_id = obj.id
WHERE r.predicate = 'located_in' AND obj.entity_type NOT IN ('LOCATION')

UNION ALL

SELECT
    'works_at_bad_subject' AS constraint_type,
    COUNT(*) AS remaining
FROM koi_relationships r
JOIN entity_registry subj ON r.subject_entity_id = subj.id
WHERE r.predicate = 'works_at' AND subj.entity_type NOT IN ('PERSON')

UNION ALL

SELECT
    'works_at_bad_object' AS constraint_type,
    COUNT(*) AS remaining
FROM koi_relationships r
JOIN entity_registry obj ON r.object_entity_id = obj.id
WHERE r.predicate = 'works_at' AND obj.entity_type NOT IN ('ORGANIZATION', 'PROJECT', 'VALIDATOR')

UNION ALL

SELECT
    'member_of_bad_subject' AS constraint_type,
    COUNT(*) AS remaining
FROM koi_relationships r
JOIN entity_registry subj ON r.subject_entity_id = subj.id
WHERE r.predicate = 'member_of' AND subj.entity_type NOT IN ('PERSON', 'ORGANIZATION', 'VALIDATOR')

UNION ALL

SELECT
    'member_of_bad_object' AS constraint_type,
    COUNT(*) AS remaining
FROM koi_relationships r
JOIN entity_registry obj ON r.object_entity_id = obj.id
WHERE r.predicate = 'member_of' AND obj.entity_type NOT IN ('ORGANIZATION', 'PROJECT')

UNION ALL

SELECT
    'authored_bad_subject' AS constraint_type,
    COUNT(*) AS remaining
FROM koi_relationships r
JOIN entity_registry subj ON r.subject_entity_id = subj.id
WHERE r.predicate = 'authored' AND subj.entity_type NOT IN ('PERSON', 'ORGANIZATION')

UNION ALL

SELECT
    'employs_bad_subject' AS constraint_type,
    COUNT(*) AS remaining
FROM koi_relationships r
JOIN entity_registry subj ON r.subject_entity_id = subj.id
WHERE r.predicate = 'employs' AND subj.entity_type NOT IN ('ORGANIZATION')

UNION ALL

SELECT
    'employs_bad_object' AS constraint_type,
    COUNT(*) AS remaining
FROM koi_relationships r
JOIN entity_registry obj ON r.object_entity_id = obj.id
WHERE r.predicate = 'employs' AND obj.entity_type NOT IN ('PERSON')

UNION ALL

SELECT
    'validates_bad_subject' AS constraint_type,
    COUNT(*) AS remaining
FROM koi_relationships r
JOIN entity_registry subj ON r.subject_entity_id = subj.id
WHERE r.predicate = 'validates' AND subj.entity_type NOT IN ('VALIDATOR', 'ORGANIZATION', 'TECHNOLOGY')

UNION ALL

SELECT
    'delegates_bad_object' AS constraint_type,
    COUNT(*) AS remaining
FROM koi_relationships r
JOIN entity_registry obj ON r.object_entity_id = obj.id
WHERE r.predicate = 'delegates' AND obj.entity_type NOT IN ('VALIDATOR', 'PERSON', 'ORGANIZATION')

UNION ALL

SELECT
    'votes_bad_subject' AS constraint_type,
    COUNT(*) AS remaining
FROM koi_relationships r
JOIN entity_registry subj ON r.subject_entity_id = subj.id
WHERE r.predicate = 'votes' AND subj.entity_type NOT IN ('PERSON', 'ORGANIZATION', 'VALIDATOR');

\echo ''
\echo 'All counts should be 0 above.'
\echo ''

-- ============================================================================
-- STEP 5: SUMMARY FOR DOCUMENTATION
-- ============================================================================
\echo '=============================================='
\echo 'SUMMARY FOR DOCUMENTATION'
\echo '=============================================='
\echo ''
\echo 'Paste the following into koi-processor/docs/archive/knowledge-graph-review-2026-01.md:'
\echo ''
\echo '```markdown'
\echo '### FIX-015b: Type Violation Cleanup (2025-12-25)'
\echo ''
\echo '**Deleted violations by predicate:**'

SELECT
    '- `' || predicate || '` (' || subject_type || ' → ' || object_type || '): ' || COUNT(*) || ' deleted' AS line
FROM koi_relationships_backup_fix015b
GROUP BY predicate, subject_type, object_type
ORDER BY COUNT(*) DESC, predicate;

\echo ''
\echo '**Total deleted:**'
SELECT '- **' || COUNT(*) || '** type-invalid relationships removed' AS line
FROM koi_relationships_backup_fix015b;

\echo ''
\echo '**Post-cleanup verification:** 0 remaining violations across all constraint types.'
\echo ''
\echo '**Fuseki rebuild:** Required to sync RDF store with PostgreSQL.'
\echo '```'
\echo ''

-- ============================================================================
-- FUSEKI REBUILD NOTE
-- ============================================================================
\echo '=============================================='
\echo 'NEXT STEP: FUSEKI REBUILD'
\echo '=============================================='
\echo ''
\echo 'The RDF triple store (Fuseki) caches relationships independently.'
\echo 'After running this cleanup, you MUST rebuild Fuseki:'
\echo ''
\echo '  python scripts/regenerate_fuseki_graph.py'
\echo ''
\echo 'This exports fresh triples from PostgreSQL (entities + relationships)'
\echo 'and replaces the /koi dataset in Fuseki.'
\echo ''
\echo 'If you skip this step, the graph API will still return deleted edges.'
\echo ''
\echo '=============================================='
\echo 'CLEANUP COMPLETE'
\echo '=============================================='
