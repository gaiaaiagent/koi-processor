-- Cleanup of 4 Person duplicate pairs in entity_registry.
-- v2: INSERT...ON CONFLICT pattern so overlap rows merge mention_count
-- instead of failing the transaction. Idempotent — safe to re-run.
--
-- Background: 4 visible (LOWER(normalized_text), entity_type) dups remained
-- after Phase D/E cleanup. Session-source URIs (created 2026-04-16) have
-- hashes the current deterministic generate_entity_uri() cannot reproduce,
-- while vault-source URIs (created 2026-04-18 to 4/23) match. Vault URIs
-- are canonical; session URIs are sediment.

\set ON_ERROR_STOP on
BEGIN;

-- Step 1: For each session-uri row in document_entity_links, upsert the
-- corresponding vault-uri row. On conflict (a document already linked to
-- both URIs), sum mention_count and keep the first non-null context.
INSERT INTO document_entity_links (document_rid, entity_uri, mention_count, context, created_at)
SELECT d.document_rid,
       (CASE d.entity_uri
          WHEN 'orn:personal-koi.entity:person-bryony-3df7310f5d39'   THEN 'orn:personal-koi.entity:person-bryony-52c314c24bd2'
          WHEN 'orn:personal-koi.entity:person-corey-d226e6d6d60e'    THEN 'orn:personal-koi.entity:person-corey-553d952e97f4'
          WHEN 'orn:personal-koi.entity:person-patricia-54a7b18f2637' THEN 'orn:personal-koi.entity:person-patricia-8de06f9c9369'
          WHEN 'orn:personal-koi.entity:person-samuel-3e6f7568aac8'   THEN 'orn:personal-koi.entity:person-samuel-28e21000a572'
        END) AS target_uri,
       d.mention_count, d.context, d.created_at
FROM document_entity_links d
WHERE d.entity_uri IN (
  'orn:personal-koi.entity:person-bryony-3df7310f5d39',
  'orn:personal-koi.entity:person-corey-d226e6d6d60e',
  'orn:personal-koi.entity:person-patricia-54a7b18f2637',
  'orn:personal-koi.entity:person-samuel-3e6f7568aac8'
)
ON CONFLICT (document_rid, entity_uri) DO UPDATE SET
  mention_count = document_entity_links.mention_count + EXCLUDED.mention_count,
  context       = COALESCE(document_entity_links.context, EXCLUDED.context);

-- Step 2: Delete the session-uri rows from document_entity_links.
DELETE FROM document_entity_links WHERE entity_uri IN (
  'orn:personal-koi.entity:person-bryony-3df7310f5d39',
  'orn:personal-koi.entity:person-corey-d226e6d6d60e',
  'orn:personal-koi.entity:person-patricia-54a7b18f2637',
  'orn:personal-koi.entity:person-samuel-3e6f7568aac8'
);

-- Step 3: Delete the 4 sediment entity_registry rows.
DELETE FROM entity_registry WHERE fuseki_uri IN (
  'orn:personal-koi.entity:person-bryony-3df7310f5d39',
  'orn:personal-koi.entity:person-corey-d226e6d6d60e',
  'orn:personal-koi.entity:person-patricia-54a7b18f2637',
  'orn:personal-koi.entity:person-samuel-3e6f7568aac8'
);

-- Verify: 0 session URIs left in document_entity_links.
SELECT '0 expected' AS check_a,
       COUNT(*) AS still_session_in_links
FROM document_entity_links
WHERE entity_uri IN (
  'orn:personal-koi.entity:person-bryony-3df7310f5d39',
  'orn:personal-koi.entity:person-corey-d226e6d6d60e',
  'orn:personal-koi.entity:person-patricia-54a7b18f2637',
  'orn:personal-koi.entity:person-samuel-3e6f7568aac8'
);

-- Verify: 0 Person dups by (LOWER(normalized_text), entity_type).
SELECT '0 expected' AS check_b,
       COUNT(*) AS remaining_person_dup_keys
FROM (
  SELECT LOWER(normalized_text) AS nk
  FROM entity_registry
  WHERE entity_type='Person'
  GROUP BY 1
  HAVING COUNT(*) > 1
) x;

-- Sanity: mention_count totals on keeper rows after merge.
SELECT entity_uri, SUM(mention_count) AS total_mentions, COUNT(*) AS n_docs
FROM document_entity_links
WHERE entity_uri IN (
  'orn:personal-koi.entity:person-bryony-52c314c24bd2',
  'orn:personal-koi.entity:person-corey-553d952e97f4',
  'orn:personal-koi.entity:person-patricia-8de06f9c9369',
  'orn:personal-koi.entity:person-samuel-28e21000a572'
)
GROUP BY entity_uri
ORDER BY entity_uri;

COMMIT;
