-- Phase 4 verification suite for overnight Option C
-- Run with: docker exec gaia-postgres-1 psql -U postgres -d eliza -f option_c_verify.sql

\echo '=== 4.1: Cohort coverage at every layer ==='
WITH cohort_def AS (
  SELECT 'newsletter' AS name, '%nate-jones-substack%' AS pat UNION ALL
  SELECT 'notion', 'orn:notion.page:%' UNION ALL
  SELECT 'github', 'regen.github:%' UNION ALL
  SELECT 'forum', 'regen.forum%' UNION ALL
  SELECT 'web', 'orn:web.page:%' UNION ALL
  SELECT 'youtube', 'regen.youtube:%'
)
SELECT
  cd.name,
  (SELECT COUNT(*) FROM koi_memories m WHERE m.rid LIKE cd.pat) AS rows_in_memories,
  (SELECT COUNT(DISTINCT regexp_replace(m.rid, '#chunk[0-9]+$', '')) FROM koi_memories m WHERE m.rid LIKE cd.pat) AS distinct_docs,
  (SELECT COUNT(DISTINCT c.chunk_rid) FROM koi_memory_chunks c WHERE c.document_rid LIKE cd.pat) AS chunks_in_chunks_table,
  (SELECT COUNT(DISTINCT l.chunk_rid) FROM koi_entity_chunk_links l WHERE l.document_rid LIKE cd.pat) AS chunks_with_entity_links
FROM cohort_def cd;

\echo '=== 4.2: Privacy invariant — PUBLIC_LEAK column should be 0 ==='
SELECT
  m.access_source,
  COUNT(DISTINCT er.fuseki_uri) AS entities_for_cohort,
  COUNT(DISTINCT er.fuseki_uri) FILTER (WHERE er.node_private = true) AS private_entities,
  COUNT(DISTINCT er.fuseki_uri) FILTER (WHERE er.node_private = false) AS PUBLIC_LEAK
FROM koi_memories m
JOIN koi_entity_chunk_links l ON l.document_rid = m.rid
JOIN entity_registry er ON er.fuseki_uri = l.entity_uri
WHERE m.is_private = true
GROUP BY m.access_source;

\echo '=== 4.3: Post-fix cohort entity counts (chunk_links → entity_registry join) ==='
SELECT
  CASE
    WHEN m.rid LIKE 'regen.github:%' THEN 'github'
    WHEN m.rid LIKE 'orn:notion.page:%' THEN 'notion'
    WHEN m.rid LIKE 'regen.newsletter:%' THEN 'newsletter'
    WHEN m.rid LIKE 'regen.forum%' THEN 'forum'
    WHEN m.rid LIKE 'orn:web.page:%' THEN 'web'
    ELSE 'other'
  END AS cohort,
  COUNT(DISTINCT er.fuseki_uri) AS unique_entities,
  COUNT(*) AS total_chunk_links
FROM koi_memories m
JOIN koi_entity_chunk_links l ON l.document_rid = m.rid
JOIN entity_registry er ON er.fuseki_uri = l.entity_uri
GROUP BY 1 ORDER BY 2 DESC;

\echo '=== 4.4: Github cohort cleanup verification (should be 0) ==='
SELECT COUNT(*) AS remaining_code_in_github
FROM koi_memories
WHERE rid LIKE 'regen.github:%'
  AND regexp_replace(rid, '#chunk[0-9]+$', '') ~ '\.(go|ts|tsx|py|js|jsx|sh|sql|c|cpp|h|rs|java|kt|rb|php|cs|sol|proto|graphql|gql|json|yaml|yml|toml|ini|cfg|sum|mod|lock)$';

\echo '=== 4.5: entity_registry coverage of chunk_link URIs (orphan rate should drop dramatically) ==='
SELECT
  COUNT(DISTINCT entity_uri) AS distinct_uris_total,
  COUNT(DISTINCT entity_uri) FILTER (WHERE EXISTS (SELECT 1 FROM entity_registry er WHERE er.fuseki_uri = l.entity_uri)) AS uris_with_registry_match,
  COUNT(DISTINCT entity_uri) FILTER (WHERE NOT EXISTS (SELECT 1 FROM entity_registry er WHERE er.fuseki_uri = l.entity_uri)) AS orphan_uris
FROM koi_entity_chunk_links l
WHERE entity_uri IS NOT NULL;

\echo '=== 4.6: Strict PUBLIC_LEAK hard-stop check ==='
SELECT COUNT(*) AS public_leak_count
FROM (
  SELECT DISTINCT er.fuseki_uri
  FROM entity_registry er
  WHERE er.node_private = false
    AND EXISTS (
      SELECT 1 FROM koi_entity_chunk_links l
      JOIN koi_memories m ON m.rid = l.document_rid
      WHERE l.entity_uri = er.fuseki_uri
        AND m.is_private = true
    )
) t;
