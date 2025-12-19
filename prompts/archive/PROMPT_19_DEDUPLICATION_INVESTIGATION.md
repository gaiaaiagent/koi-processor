# PROMPT 19: Cross-Document Deduplication & Coreference Investigation

**Date**: 2025-12-09
**Status**: URGENT INVESTIGATION REQUIRED
**Context**: Fresh extraction underway (4,710 GitHub docs), but critical gaps identified in post-processing

---

## Critical Concerns Raised

User identified two major missing features that may severely impact knowledge graph quality:

1. **Cross-Document Deduplication**: We only do within-pipeline canonicalization (alias resolution), not across-document entity deduplication
2. **Coreference Resolution**: We don't resolve pronouns ("he", "she", "they") back to named entities

**User's hypothesis**: The yonearth-gaia-chatbot project (which inspired this entire re-extraction effort) likely does these things, and we should too.

---

## Investigation Tasks

### Task 1: Analyze yonearth-gaia-chatbot Approach

**Server**: `ssh claudeuser@152.53.37.180`
**Project**: `cd yonearth-gaia-chatbot`

**Questions to answer**:

1. **Entity Deduplication**:
   - Does yonearth-gaia-chatbot perform cross-document entity deduplication?
   - If yes, how? (fuzzy matching, embeddings, graph queries, etc.)
   - At what stage? (during extraction, post-extraction, graph insertion?)
   - What tools/libraries? (spaCy, Dedupe.io, custom?)

2. **Coreference Resolution**:
   - Does yonearth-gaia-chatbot resolve coreferences (pronouns → entities)?
   - If yes, what library? (spaCy, CoreNLP, Hugging Face?)
   - How accurate is it?

3. **Knowledge Graph Structure**:
   - How does yonearth-gaia-chatbot handle duplicate entities?
   - Does it use node IDs, URIs, or other unique identifiers?
   - How are relationships linked to entities?

**Where to look**:
```bash
# Search for deduplication code
grep -r "deduplicate\|dedup\|merge.*entit" yonearth-gaia-chatbot/ --include="*.py"

# Search for coreference resolution
grep -r "coref\|pronoun\|resolve.*reference" yonearth-gaia-chatbot/ --include="*.py"

# Search for entity linking
grep -r "entity.*link\|link.*entit\|canonical" yonearth-gaia-chatbot/ --include="*.py"

# Check for libraries
grep -r "spacy\|corenlp\|neuralcoref" yonearth-gaia-chatbot/ --include="*.py" --include="requirements*.txt"
```

---

### Task 2: Analyze koi-processor Current State

**Server**: `ssh darren@202.61.196.119`
**Project**: `cd /opt/projects/koi-processor`

**Questions to answer**:

1. **Current Deduplication**:
   - What does CanonicalResolver actually do? (read the code)
   - Does it only work with a static registry (88 entities, 194 aliases)?
   - Is there any cross-document deduplication at graph insertion time?

2. **Graph Structure**:
   - How does Fuseki store entities? (check queries)
   - Are there duplicate nodes in the graph currently?
   - Query: How many "Regen Network" nodes exist? (should be 1)

3. **Relationship Integrity**:
   - If entities are duplicated, are relationships fragmented?
   - Example: If "Gregory Landua" appears in 100 docs, do we have 100 nodes or 1 node with 100 relationships?

**Queries to run**:
```sparql
# Check for duplicate entities (same name, different URIs)
SELECT ?name (COUNT(?entity) as ?count)
WHERE {
  ?entity rdfs:label ?name .
}
GROUP BY ?name
HAVING (?count > 1)
ORDER BY DESC(?count)
LIMIT 50

# Count total entities
SELECT (COUNT(DISTINCT ?entity) as ?total)
WHERE {
  ?entity a ?type .
  FILTER(?type IN (regen:HumanActor, regen:Organization, regen:Project))
}

# Check relationship integrity
SELECT ?rel ?source ?target
WHERE {
  ?rel regen:source ?source .
  ?rel regen:target ?target .
}
LIMIT 10
```

**Where to look**:
```bash
# Check CanonicalResolver implementation
cat src/knowledge_graph/postprocessing/modules/canonical_resolver.py

# Check graph integration (how entities are inserted)
cat src/knowledge_graph/graph_integration.py

# Check if there's any deduplication at insert time
grep -r "deduplicate\|merge\|exists" src/knowledge_graph/ --include="*.py"
```

---

### Task 3: Quantify the Problem

**Estimate impact of missing deduplication**:

1. **Sample Analysis**:
   - Take 100 random documents from the knowledge graph
   - Count unique entity names
   - Check how many have multiple URIs/nodes
   - Calculate duplication rate

2. **High-Profile Entities**:
   - Query for "Regen Network", "Gregory Landua", "Ecocredit", "Cosmos"
   - How many nodes for each?
   - Expected: 1 node per entity
   - If >1: PROBLEM

3. **Relationship Fragmentation**:
   - If "Regen Network" has 50 duplicate nodes
   - Relationships are split across all 50 nodes
   - This breaks graph queries (can't find all relationships)

**Example**:
```
Without deduplication:
- Doc 1: (Gregory Landua) --[founded]--> (Regen Network #1)
- Doc 2: (Gregory Landua) --[works_at]--> (Regen Network #2)
- Doc 3: (Gregory Landua) --[CEO_of]--> (Regen Network #3)

Result: 3 separate "Regen Network" nodes, relationships fragmented

With deduplication:
- All docs: (Gregory Landua) --[founded, works_at, CEO_of]--> (Regen Network)

Result: 1 unified node, all relationships connected
```

---

### Task 4: Compare Approaches

Create a comparison table:

| Feature | yonearth-gaia-chatbot | koi-processor | Gap? |
|---------|----------------------|---------------|------|
| Cross-document deduplication | [YES/NO] - [method] | NO - only canonical registry | [YES/NO] |
| Coreference resolution | [YES/NO] - [library] | NO | [YES/NO] |
| Entity linking (external KBs) | [YES/NO] - [which KBs] | NO | [YES/NO] |
| Fuzzy name matching | [YES/NO] - [how] | NO | [YES/NO] |
| Node uniqueness guarantee | [YES/NO] - [how] | [YES/NO] - [how] | [YES/NO] |

---

### Task 5: Assess Urgency

**Timeline considerations**:

1. **Fresh extraction in progress** (4,710 docs, ~12 hours remaining)
2. **Should we STOP the extraction?**
   - If deduplication is critical, we should add it BEFORE extracting more
   - Or: Continue extraction, add deduplication as post-processing step?

3. **Re-extraction impact**:
   - We already extracted 1,016 docs (re-extraction)
   - If we add deduplication now, do we need to re-process those?

**Decision tree**:
```
IF yonearth-gaia-chatbot does deduplication:
  IF deduplication is simple to add:
    → STOP fresh extraction
    → Add deduplication
    → Resume extraction
  ELSE IF deduplication is complex:
    → Continue extraction
    → Add deduplication as Phase 4 (post-processing)
ELSE IF yonearth-gaia-chatbot does NOT do deduplication:
  → Investigate WHY (maybe it's not needed?)
  → Assess if koi-processor needs it differently
```

---

## Deliverables

### Report: DEDUPLICATION_INVESTIGATION_REPORT.md

Should contain:

1. **Executive Summary**:
   - Does yonearth-gaia-chatbot do deduplication? YES/NO
   - Does koi-processor need it? YES/NO
   - Urgency level: CRITICAL / HIGH / MEDIUM / LOW

2. **yonearth-gaia-chatbot Analysis**:
   - Deduplication approach (if any)
   - Coreference resolution approach (if any)
   - Code samples
   - Libraries used

3. **koi-processor Current State**:
   - What CanonicalResolver actually does
   - Current duplication rate (% of entities duplicated)
   - Examples of problematic duplicates
   - Relationship fragmentation severity

4. **Gap Analysis**:
   - Feature comparison table
   - What's missing
   - Impact on quality (quantified)

5. **Recommendations**:
   - Option A: Simple fixes (if possible)
   - Option B: Complex implementation (if needed)
   - Option C: No action needed (if justified)
   - Timeline for each option

6. **Action Items**:
   - Immediate: [list]
   - Short-term (this week): [list]
   - Long-term (next phase): [list]

---

## Technical Deep Dives

### If Deduplication is Needed

**Approach 1: Graph-Based Deduplication (at insert time)**
```python
# Pseudocode
def insert_entity(name, type, properties):
    # Check if entity already exists
    existing = graph.query(f"SELECT ?uri WHERE {{ ?uri rdfs:label '{name}' ; a {type} }}")

    if existing:
        # Merge with existing entity
        entity_uri = existing[0]
        graph.add_properties(entity_uri, properties)
    else:
        # Create new entity
        entity_uri = create_uri(name, type)
        graph.insert(entity_uri, properties)

    return entity_uri
```

**Approach 2: Fuzzy Matching (for typos/variations)**
```python
from fuzzywuzzy import fuzz

def find_similar_entities(name, threshold=90):
    all_entities = graph.query("SELECT ?name WHERE { ?entity rdfs:label ?name }")

    similar = []
    for existing_name in all_entities:
        if fuzz.ratio(name, existing_name) > threshold:
            similar.append(existing_name)

    return similar
```

**Approach 3: Embedding-Based (semantic similarity)**
```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer('all-MiniLM-L6-v2')

def find_duplicate_by_embedding(name, context, threshold=0.85):
    query_embedding = model.encode(f"{name} {context}")

    # Compare with existing entities
    for entity in existing_entities:
        entity_embedding = model.encode(f"{entity.name} {entity.context}")
        similarity = cosine_similarity(query_embedding, entity_embedding)

        if similarity > threshold:
            return entity

    return None
```

---

### If Coreference Resolution is Needed

**Approach 1: spaCy (fast, good for English)**
```python
import spacy
nlp = spacy.load("en_core_web_sm")

# Add neuralcoref for better coreference
import neuralcoref
neuralcoref.add_to_pipe(nlp)

def resolve_coreferences(text):
    doc = nlp(text)

    # Get resolved text
    resolved_text = doc._.coref_resolved

    return resolved_text

# Example:
# Input: "Gregory founded Regen Network. He is the CEO."
# Output: "Gregory founded Regen Network. Gregory is the CEO."
```

**Approach 2: AllenNLP (more accurate, slower)**
```python
from allennlp.predictors.predictor import Predictor

predictor = Predictor.from_path(
    "https://storage.googleapis.com/allennlp-public-models/coref-spanbert-large-2021.03.10.tar.gz"
)

def resolve_coreferences(text):
    result = predictor.predict(document=text)
    clusters = result['clusters']

    # Replace pronouns with antecedents
    resolved_text = replace_pronouns_with_clusters(text, clusters)

    return resolved_text
```

---

## Investigation Checklist

- [ ] Task 1: Analyze yonearth-gaia-chatbot deduplication approach
- [ ] Task 1: Analyze yonearth-gaia-chatbot coreference resolution
- [ ] Task 2: Understand koi-processor CanonicalResolver
- [ ] Task 2: Run SPARQL queries to check for duplicates
- [ ] Task 2: Measure current duplication rate
- [ ] Task 3: Quantify relationship fragmentation
- [ ] Task 3: Identify high-profile duplicate entities
- [ ] Task 4: Create comparison table
- [ ] Task 5: Assess urgency and timeline impact
- [ ] Write DEDUPLICATION_INVESTIGATION_REPORT.md
- [ ] Provide recommendations with cost/benefit analysis

---

## Success Criteria

A successful investigation will answer:

1. **Does yonearth-gaia-chatbot do deduplication?** (YES/NO + evidence)
2. **How severe is duplication in koi-processor?** (quantified %)
3. **Should we stop the fresh extraction?** (YES/NO + rationale)
4. **What's the recommended approach?** (detailed plan)
5. **What's the timeline?** (hours/days for implementation)

---

## Context Files

**Local**:
- `/Users/darrenzal/projects/RegenAI/koi-processor/CLAUDE.md` - Project context
- `/Users/darrenzal/projects/RegenAI/koi-processor/src/knowledge_graph/postprocessing/modules/canonical_resolver.py` - Current canonicalization
- `/Users/darrenzal/projects/RegenAI/koi-processor/src/knowledge_graph/config/canonical_entities.json` - Static registry

**yonearth-gaia-chatbot**:
- `/home/claudeuser/yonearth-gaia-chatbot/` - Entire codebase to investigate

**Production**:
- `darren@202.61.196.119:/opt/projects/koi-processor` - Current extraction
- Fuseki: `http://localhost:3030/koi` - Knowledge graph

---

**Assigned to**: Next agent
**Priority**: URGENT (extraction in progress)
**Expected completion**: 2-4 hours
