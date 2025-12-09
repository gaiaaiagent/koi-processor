#!/bin/bash
# Check staging graph loading progress

ssh darren@202.61.196.119 'PGPASSWORD=postgres psql -U postgres -h localhost -p 5433 -d eliza << "EOF"
LOAD '\''age'\'';
SET search_path = ag_catalog, "$user", public;

-- Count entities by type
SELECT '\''=== STAGING GRAPH (regen_graph_v2) ==='\'';

SELECT * FROM cypher('\''regen_graph_v2'\'', $cypher$
  MATCH (n)
  WITH labels(n)[0] as label, count(*) as cnt
  RETURN label, cnt
  ORDER BY cnt DESC
$cypher$) as (label agtype, cnt agtype);

SELECT * FROM cypher('\''regen_graph_v2'\'', $cypher$
  MATCH (n)
  RETURN count(n) as total_entities
$cypher$) as (total_entities agtype);

SELECT * FROM cypher('\''regen_graph_v2'\'', $cypher$
  MATCH ()-[r]->()
  RETURN count(r) as total_edges
$cypher$) as (total_edges agtype);

SELECT '\''=== PRODUCTION GRAPH (regen_graph) ==='\'';

SELECT * FROM cypher('\''regen_graph'\'', $cypher$
  MATCH (n)
  RETURN count(n) as production_total
$cypher$) as (production_total agtype);

EOF'
