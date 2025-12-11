# PROMPT 20B: Graph Insertion & Cross-Document Deduplication Strategy

**Date**: 2025-12-09
**Status**: CRITICAL FOLLOW-UP to PROMPT_20
**Priority**: URGENT
**Estimated Time**: 1-2 hours

---

## Critical Question

**User's hypothesis**: "yonearth does have cross-doc deduplication - it just uses the knowledge graph as the registry. When inserting, it checks if an entity already exists in Neo4j and merges to the existing node."

**Agent's finding**: "No persistent cross-doc registry"

**These could both be true!** We need to understand the **graph insertion strategy**.

---

## Context

The difference between:

### Approach A: Static Registry (what koi-processor does now)
```python
# Pre-extraction: Load static registry
canonical_entities = load_json("canonical_entities.json")  # 88 entities

# During extraction
if entity_name in canonical_entities:
    entity_name = canonical_entities[entity_name]

# Graph insertion: Create new URI every time
entity_uri = f"https://regen.network/entity/{hash(entity_name)}"
graph.insert(entity_uri, ...)
```
**Result**: Each extraction creates new URI, even for same entity name

### Approach B: Graph-as-Registry (what user thinks yonearth does)
```python
# Graph insertion: Check if entity exists
existing = neo4j.query("MATCH (n {name: $name}) RETURN n", name=entity_name)

if existing:
    # Merge with existing node
    neo4j.query("MERGE (n {name: $name}) SET n += $properties", ...)
else:
    # Create new node
    neo4j.query("CREATE (n {name: $name, ...})", ...)
```
**Result**: Multiple documents mentioning "Regen Network" all point to SAME node

### Approach C: No Registry (worst case)
```python
# Just insert everything, create duplicates
graph.insert(entity_name, ...)
```
**Result**: Massive duplication

---

## Investigation Tasks

### Task 1: Find Graph Insertion Code (30 minutes)

**Server**: `ssh claudeuser@152.53.37.180`
**Project**: `cd ~/yonearth-gaia-chatbot`

#### A. Locate Neo4j Insertion Logic

```bash
# Find graph insertion code
find . -name "*.py" | xargs grep -l "neo4j\|graph.*insert\|CREATE\|MERGE" | head -20

# Look for Neo4j driver usage
grep -r "from neo4j import" . --include="*.py"
grep -r "driver\|session\|transaction" . --include="*.py" | grep neo4j

# Find entity insertion specifically
grep -r "def.*insert.*entity\|def.*create.*node\|def.*merge.*entity" . --include="*.py"
```

#### B. Read the Insertion Functions

For each insertion function found, document:

1. **Does it check for existing entities?**
   - MERGE vs CREATE?
   - Any query before insert?
   - Fuzzy matching during insert?

2. **How are entity IDs/URIs created?**
   - UUID?
   - Hash of name?
   - Neo4j auto-ID?
   - Based on existing node ID?

3. **What happens with duplicates?**
   - Properties merged?
   - Relationships consolidated?
   - Provenance tracked?

#### C. Find Integration Between Pipeline and Graph

```bash
# Look for how pipeline output goes to graph
grep -r "pipeline.*graph\|postprocess.*insert\|entities.*neo4j" . --include="*.py"

# Find orchestration scripts
ls scripts/*graph*.py scripts/*build*.py scripts/*insert*.py 2>/dev/null
```

### Task 2: Trace a Complete Flow (30 minutes)

**Pick a single entity** (e.g., "Regen Network") and trace:

1. **Extraction**: How is it extracted from text?
2. **Pipeline**: What modules process it?
3. **Deduplication**: Is it merged with similar names?
4. **Insertion**: What query inserts it into Neo4j?
5. **Duplicate handling**: If "Regen Network" was already in graph, what happens?

**Document the exact code path** with file:line references.

### Task 3: Check Neo4j Schema/Constraints (15 minutes)

```bash
# Look for Neo4j schema definitions
find . -name "*.cypher" -o -name "*schema*" -o -name "*constraint*"

# Look for uniqueness constraints in code
grep -r "UNIQUE\|CONSTRAINT\|INDEX" . --include="*.py" --include="*.cypher"

# Check if entity names have uniqueness constraint
grep -r "CREATE CONSTRAINT\|CREATE INDEX" . --include="*.py" -A 3
```

**Question**: Are there uniqueness constraints on entity names/IDs?

### Task 4: Read Graph Builder Implementation (30 minutes)

**Key file** (from earlier investigation): `scripts/build_unified_graph_hybrid.py`

Read this file **specifically for graph insertion logic**:

```python
# Find in build_unified_graph_hybrid.py:
# 1. How does it insert entities?
# 2. Does it check for duplicates during insertion?
# 3. What queries does it use (CREATE vs MERGE)?
# 4. How are cross-document entities handled?
```

Document:
- All Neo4j queries used
- Insertion vs merging logic
- Duplicate detection method
- Entity ID strategy

---

## Specific Questions to Answer

### Q1: Entity Insertion Method

When yonearth inserts "Regen Network" into Neo4j:

- [ ] Uses `CREATE` (always creates new node)?
- [ ] Uses `MERGE` (creates if not exists, else returns existing)?
- [ ] Custom logic (query first, then insert/update)?
- [ ] Batch insertion (processes many entities at once)?

**Find the actual Cypher query** or Python code.

### Q2: Duplicate Detection During Insertion

When inserting entity that might already exist:

- [ ] Exact name match check?
- [ ] Fuzzy name match check (during insertion)?
- [ ] Check by entity ID/URI?
- [ ] No check (always insert)?
- [ ] Deduplication happens later (post-insertion cleanup)?

### Q3: Cross-Document Linking

If Document A and Document B both mention "Gregory Landua":

- [ ] Both create separate nodes (duplicates)?
- [ ] Both link to same existing node?
- [ ] Merged during insertion?
- [ ] Merged in post-processing?

**Find evidence** in code.

### Q4: Relationship Handling

When inserting relationship (Gregory → founded → Regen):

- [ ] Check if relationship already exists?
- [ ] Allow duplicate relationships?
- [ ] Merge relationship properties?
- [ ] Track provenance (which document)?

### Q5: Performance Strategy

For large-scale insertion (thousands of entities):

- [ ] Insert all, then deduplicate?
- [ ] Check before each insert (slow but accurate)?
- [ ] Batch deduplicate every N entities?
- [ ] Other strategy?

---

## Evidence to Collect

### Code Samples

Extract and document:

1. **Primary insertion function**
```python
def insert_entity_to_graph(entity, graph_session):
    # Paste actual code here
    ...
```

2. **Duplicate check logic**
```python
# If exists, show the code
```

3. **Neo4j queries used**
```cypher
// Actual Cypher queries
```

### Configuration

Check for:
- Deduplication thresholds in config files
- Neo4j connection settings
- Batch sizes
- Index/constraint definitions

---

## Deliverable

**Document**: `GRAPH_INSERTION_STRATEGY_ANALYSIS.md`

### Structure:

```markdown
# Graph Insertion Strategy Analysis: yonearth-gaia-chatbot

## Executive Summary

**Does yonearth use graph-as-registry for cross-doc deduplication?**
[YES/NO/PARTIAL - with clear explanation]

**Key finding**: [1-2 sentence answer to the critical question]

## Entity Insertion Method

### Code Location
[file:line references]

### Insertion Logic
[Detailed description with code samples]

### Duplicate Handling
[How duplicates are prevented/merged]

## Cross-Document Deduplication

### Strategy
[Describe how entities from multiple documents are handled]

### Evidence
[Code samples, queries, configuration]

### Performance Considerations
[How they handle scale]

## Relationship Insertion

### Code Location
[file:line references]

### Duplicate Relationship Handling
[How they prevent/merge duplicate relationships]

## Neo4j Schema

### Constraints
[Uniqueness constraints, indexes]

### Entity ID Strategy
[How entity IDs are generated]

## Complete Flow Example

### Scenario
Document A: "Gregory founded Regen Network"
Document B: "Gregory Landua works at Regen"

### Step-by-step
1. Document A extraction: ...
2. Document A insertion: ...
3. Document B extraction: ...
4. Document B insertion: ...
5. Result in graph: ...

## Comparison: yonearth vs koi-processor

| Aspect | yonearth | koi-processor | Gap? |
|--------|----------|---------------|------|
| Insertion method | [CREATE/MERGE/custom] | CREATE (always new URI) | YES/NO |
| Duplicate check | [yes/no, how] | No (only static registry) | YES/NO |
| Cross-doc linking | [yes/no, how] | No | YES/NO |
| Graph database | Neo4j | Fuseki (RDF) | Different |

## Critical Implications for koi-processor

### If yonearth uses graph-as-registry:

**Option A**: Port same approach
- Requires: [list changes needed]
- Pros: [benefits]
- Cons: [challenges]

**Option B**: Different approach for Fuseki/RDF
- Fuseki doesn't have MERGE like Neo4j
- Would need: [alternative strategy]

### If yonearth does NOT use graph-as-registry:

[Explain what they do instead and implications]

## Recommendations

[Based on findings, what should koi-processor do?]

## Code References

[File:line references for all key functions]
```

---

## Success Criteria

Investigation complete when we can answer:

- ✅ Exactly how yonearth inserts entities into Neo4j
- ✅ Whether they check for existing entities during insertion
- ✅ How cross-document deduplication works (if at all)
- ✅ What's different due to Neo4j vs Fuseki
- ✅ What approach koi-processor should take

---

## Critical Note: Neo4j vs Fuseki Differences

**yonearth uses Neo4j** (property graph):
- `MERGE` command (create if not exists)
- Easy to check for existing nodes
- Native duplicate prevention

**koi-processor uses Fuseki** (RDF/SPARQL):
- No `MERGE` equivalent
- Must query before insert
- Different architecture

**This matters!** Even if yonearth uses graph-as-registry, we might need a different approach due to Fuseki.

---

## Time Estimate

| Task | Time |
|------|------|
| Find insertion code | 30 min |
| Trace complete flow | 30 min |
| Check schema/constraints | 15 min |
| Read graph builder | 30 min |
| Document findings | 15 min |
| **TOTAL** | **2 hours** |

---

**Priority**: URGENT - Clarifies core architecture question
**Blocking**: Need this to finalize implementation approach
**Expected outcome**: Clear understanding of graph insertion strategy
