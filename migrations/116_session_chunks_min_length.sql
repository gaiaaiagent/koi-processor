-- =============================================================================
-- Migration 116: minimum chunk length on session_chunks
-- =============================================================================
-- Date:     2026-09-02
-- Plan:     ~/.claude/plans/koi-pipeline-hardening-audit-2026-08-31.md, Phase 1 #13
--
-- Apply:    psql -d personal_koi -v ON_ERROR_STOP=1 -1 -f migrations/116_session_chunks_min_length.sql
-- Rollback: psql -d personal_koi -v ON_ERROR_STOP=1 -1 -f migrations/116_session_chunks_min_length_down.sql
--
-- =============================================================================
-- *** DO NOT APPLY YET. APPLYING THIS TODAY BREAKS SESSION INGESTION. ***
-- =============================================================================
-- The producer is still emitting the rows this constraint rejects. Measured
-- 2026-09-02:
--
--     day          chunks written    under 100 chars
--     2026-09-02        5,227          4,118  (79%)
--     2026-09-01       11,504         10,429  (91%)
--     2026-08-31        2,746          2,368  (86%)
--
-- So this is not a guard against a historical mistake -- it rejects what the
-- live chunker writes on most inserts. Applied now, session-ingest batches
-- start failing in production.
--
-- ROOT CAUSE, in a different repo:
--   RegenAI/koi-sensors/sensors/claude_sessions/claude_session_sensor.py:1162
--       chunk_text = f"User: {user_text}\n\nAssistant: {assistant_text}"
--   Nothing checks that either half is non-empty. When _extract_text() returns
--   "" for both (tool-result and meta messages), the chunk is exactly
--   "User: \n\nAssistant: " -- 18 chars trimmed. Same pattern at :1332.
--
-- REQUIRED ORDER (starting anywhere else is an outage or a re-fill):
--   1. Fix the sensor to skip empty turn-pairs.        <-- BLOCKS THIS FILE
--   2. Apply this migration (NOT VALID; new writes only).
--   3. Delete the ~322k historical stubs, then REINDEX the 3.6GB HNSW index.
--   4. ALTER TABLE session_chunks VALIDATE CONSTRAINT chk_session_chunks_min_length;
--
-- Step 2 before step 1 breaks ingestion. Step 3 before step 2 lets the
-- pipeline refill what was just deleted.
--
-- =============================================================================
-- WHY "NOT VALID", AND WHY THAT IS THE POINT
-- =============================================================================
-- Measured on personal_koi, 2026-09-02:
--
--     session_chunks total                       481,846
--     with length(trim(chunk_text)) < 100        322,662   (67.0%)
--
-- Two thirds of the table violates this constraint, so it CANNOT be added
-- validated without either failing outright or forcing a 322k-row deletion
-- into this transaction. NOT VALID adds the constraint for all FUTURE writes
-- while leaving existing rows untouched and unscanned -- no table rewrite, no
-- ACCESS EXCLUSIVE beyond a brief catalog lock.
--
-- This is deliberate sequencing, not a compromise: the guard must land BEFORE
-- the repair, or the generator keeps re-creating the condition while the
-- cleanup runs. Deleting the 322k rows first and adding the constraint after
-- leaves a window in which the pipeline refills what was just deleted.
--
-- The dominant shape is an 18-character stub -- literally "User: \n\nAssistant:"
-- (note the space after "User:", which is easy to get wrong when constructing
-- a comparison by hand rather than sampling the column). These are turn-pair
-- chunks where both turns were empty. They carry no content, yet each one is
-- embedded at 3072 dimensions and indexed into the HNSW index that the
-- sessions retrieval surface queries -- forming a large near-duplicate cluster
-- that wins ANN on unspecific queries and occupies a guaranteed answer slot.
--
-- 100 is not arbitrary: TextChunker.min_chunk_size = 100 already exists in the
-- Python chunker and is simply not applied on this path. This makes the
-- database agree with the value the code already declares.
--
-- TO VALIDATE LATER (after the 322k rows are dealt with, which is a separate
-- operator decision -- it is a deletion plus a REINDEX of a 3.6GB index):
--     ALTER TABLE session_chunks VALIDATE CONSTRAINT chk_session_chunks_min_length;
-- VALIDATE takes only a SHARE UPDATE EXCLUSIVE lock, so it does not block
-- reads or writes; it will simply fail while any violating row remains.
-- =============================================================================

ALTER TABLE session_chunks
    DROP CONSTRAINT IF EXISTS chk_session_chunks_min_length;

ALTER TABLE session_chunks
    ADD CONSTRAINT chk_session_chunks_min_length
    CHECK (length(trim(chunk_text)) >= 100)
    NOT VALID;

COMMENT ON CONSTRAINT chk_session_chunks_min_length ON session_chunks IS
  'Minimum meaningful chunk length, mirroring TextChunker.min_chunk_size=100 which the Python chunker declares but does not apply on this path. Added NOT VALID on 2026-09-02: 322,662 of 481,846 existing rows (67%) violate it, dominated by the 18-char "User: \nAssistant:" empty-turn-pair stub. Guard-before-repair is deliberate -- validating requires deleting those rows first, then ALTER TABLE ... VALIDATE CONSTRAINT.';


-- -----------------------------------------------------------------------------
-- Migration bookkeeping
-- -----------------------------------------------------------------------------
INSERT INTO koi_migrations (migration_id, checksum)
VALUES ('116_session_chunks_min_length', 'v1_not_valid_min_length')
ON CONFLICT (migration_id) DO NOTHING;
