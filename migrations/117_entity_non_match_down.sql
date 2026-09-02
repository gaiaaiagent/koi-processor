-- Rollback for migration 117. See that file for rationale.
-- Apply: psql -d personal_koi -v ON_ERROR_STOP=1 -1 -f migrations/117_entity_non_match_down.sql
--
-- NOTE: this DESTROYS operator-adjudicated negative evidence. The seeded vetoes
-- include five calls that came from the operator's own knowledge and exist in
-- no corpus (Cascadia North != CNSS, Salt Spring Digital Ecologies != Salt
-- Spring AI, the distinct Lightcone orgs, Raven Trust != Raven Capital,
-- Lekwungen the language vs the people). Those cannot be re-derived by any
-- resolver or re-read from any document. Export before dropping:
--
--   \copy (SELECT * FROM entity_non_match) TO 'entity_non_match_backup.csv' CSV HEADER

DROP TRIGGER IF EXISTS trg_entity_non_match_normalize_order ON entity_non_match;
DROP FUNCTION IF EXISTS entity_non_match_normalize_order();
DROP FUNCTION IF EXISTS entity_non_match_exists(TEXT, TEXT);
DROP FUNCTION IF EXISTS entity_non_match_partners(TEXT);
DROP TABLE IF EXISTS entity_non_match;

DELETE FROM koi_migrations WHERE migration_id = '117_entity_non_match';
