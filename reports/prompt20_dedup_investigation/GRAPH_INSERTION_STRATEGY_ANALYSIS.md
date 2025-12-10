# Graph Insertion Strategy Analysis: yonearth-gaia-chatbot

## Executive Summary
**Does yonearth use graph-as-registry for cross-doc dedup?** No. They deduplicate entities in-memory across all loaded extraction files, then clear Neo4j and `CREATE` fresh nodes/edges each run. The graph is not consulted as a registry; there is no MERGE-on-insert and the database is wiped before insertion.

**Key finding:** Cross-document dedup happens before insertion (fuzzy + validator), not during or after insert. Cross-run persistence is zero because the database is cleared on each build.

## Entity Insertion Method
- **Code Location:** `src/knowledge_graph/graph/graph_builder.py` lines 101-195 (load/dedup), 336-410 (populate_neo4j); `src/knowledge_graph/graph/neo4j_client.py` lines 68-121 (execution helpers, indexes).
- **Insertion Logic:** `populate_neo4j()` clears Neo4j (`clear_database()`), creates non-unique indexes on name/type/id, builds Cypher `CREATE (e:Entity {…})` statements for every deduplicated entity, and batch executes them (batch size 100). No pre-insert lookup or MERGE.
- **Entity IDs:** Generated before dedup as `"{type}_{name}_{episode}_{chunk}"` (line 101). After dedup the canonical ID is the first entity’s ID in the merged group; that ID is then written as `e.id` in Neo4j.
- **Duplicate Handling:** Insertion phase assumes upstream dedup succeeded; there is no on-write duplicate prevention. Neo4j indexes are non-unique; duplicates would be inserted if upstream dedup missed them.

## Cross-Document Deduplication
- **Strategy:** Dedup happens in memory across all extraction files loaded for the run. `deduplicate_entities()` groups by type and merges entities with exact or `fuzz.ratio` ≥ similarity_threshold (default 90) and optional `EntityMergeValidator` veto (lines 108-196). Relationship endpoints are rewritten to canonical names and relationships are deduped by a normalized key (lines 261-307).
- **Graph-as-Registry:** Not used. Each build starts with `MATCH (n) DETACH DELETE n` via `clear_database()` and then recreates everything with `CREATE`.
- **Performance:** Quadratic per-type fuzzy matching; batching is only for write throughput (100 per batch). No incremental/streaming dedup.

## Relationship Insertion
- **Code Location:** `src/knowledge_graph/graph/graph_builder.py` lines 270-307 (dedup) and 380-410 (insert).
- **Duplicate Handling:** Relationships are deduped pre-insert by key `(source_entity, relationship_type, target_entity)`; metadata chunks are merged. Insertion uses `MATCH` on canonical entity IDs then `CREATE (source)-[r:TYPE {description, episode}]->(target)`; no MERGE or uniqueness constraint on relationships.
- **Provenance:** Episode number and description are stored in relationship properties; no per-document provenance beyond that.

## Neo4j Schema
- **Constraints/Indexes:** `neo4j_client.create_indexes()` issues non-unique indexes on `Entity.name`, `Entity.type`, `Entity.id` (lines 88-104). No uniqueness constraints; duplicates would be permitted if presented.
- **Entity ID Strategy:** Custom string IDs as above; no reliance on Neo4j internal IDs.

## Complete Flow Example (current design)
1. **Extraction:** Entities/relationships from all episode JSONs are loaded (lines 60-99). Each entity gets an ID with episode/chunk baked in.
2. **Deduplication:** In-memory fuzzy merge by type (lines 108-196). Aliases/provenance stored; canonical ID is first in group. Relationship endpoints rewritten and deduped (lines 261-307).
3. **Graph Reset:** `clear_database()` wipes all prior nodes/edges.
4. **Insertion:** Entities created with `CREATE`; relationships created with `MATCH` + `CREATE`. No graph lookup/merge.
5. **Result:** Cross-document dedup applies only to the current batch of extractions. Cross-run dedup does not exist (database always rebuilt).

## Comparison: yonearth vs koi-processor
| Aspect | yonearth | koi-processor | Gap? |
|--------|----------|--------------|------|
| Insertion method | `CREATE` after wiping DB; non-unique indexes | Fuseki inserts new URIs; no wipe by default | Both lack graph-as-registry |
| Duplicate check on insert | None (assumes pre-dedup) | None | Yes |
| Cross-document linking | In-memory dedup across loaded files per run | Static registry of ~88 + none | Both lack persistent registry |
| Cross-run persistence | No (DB cleared each build) | Yes (RDF store persists) | Different behaviors |

## Critical Implications for koi-processor
- If we need persistent cross-doc dedup, we cannot rely on yonearth’s pattern: they rebuild and `CREATE` every run. We must introduce a registry or pre-insert lookup/merge strategy suited to Fuseki/SPARQL (query-before-insert or managed canonical registry).
- Upstream dedup must be robust; no safety net at insertion time. Any missed duplicates are stored as separate nodes.
- Without uniqueness constraints, MERGE-like behavior would have to be implemented explicitly (e.g., SPARQL ASK + INSERT).

## Recommendations
- Treat graph-as-registry as missing in yonearth; design a Fuseki-aware registry/merge layer.
- Keep upstream fuzzy/validator dedup, but add a persistent canonical map (DB/JSON) to survive runs and to rewrite relationship endpoints.
- If adopting Neo4j-like semantics in Fuseki, implement: (1) pre-insert existence check by canonical name/ID; (2) consistent URI generation tied to canonical entity IDs; (3) optional uniqueness constraints if store supports.

## Code References
- Entity load/dedup/insertion: `src/knowledge_graph/graph/graph_builder.py` lines 60-196, 261-307, 336-410.
- Neo4j client/index setup: `src/knowledge_graph/graph/neo4j_client.py` lines 68-121.
- Dedup guardrails: `src/knowledge_graph/validators/entity_merge_validator.py` (merge vetoes, thresholds).
