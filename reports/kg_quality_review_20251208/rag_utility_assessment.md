# RAG Utility Assessment

## Overall RAG Readiness Score: 65/100

## Assessment Categories

### 1. Entity Searchability (Score: 60/100)

**Strengths:**
- 14,706 entities provide good coverage
- Clear entity type categorization (Org/Project/Person)
- RID-based unique identification

**Weaknesses:**
- Case-insensitive duplicates fragment search results
- Generic entities pollute results ("User" appears 14 times)
- Short acronyms may cause false positives (RND, NCT, etc.)

**Impact on RAG:**
- Query for "Regen Network" may miss variants
- Entity linking accuracy reduced by duplicates
- Disambiguation required for acronyms

**Recommendations:**
```python
# Add to RAG pipeline
def normalize_entity_query(query):
    """Normalize entity names for consistent matching."""
    return query.lower().strip()

def expand_acronyms(entity_name):
    """Expand known acronyms."""
    ACRONYMS = {
        "RND": "Regen Network Development",
        "NCT": "Nature Carbon Tonne",
        # ... add more
    }
    return ACRONYMS.get(entity_name.upper(), entity_name)
```

### 2. Statement Quality for QA (Score: 55/100)

**Strengths:**
- 19,608 statements provide factual claims
- Subject-predicate-object structure is clean
- Confidence scores available for filtering

**Weaknesses:**
- 48.5% of statements have confidence < 0.85
- Generic predicates ("is", "has") lack semantic precision
- Some statements are metadata noise (e.g., "has 0 replies")

**Impact on RAG:**
- Low-confidence statements may generate incorrect answers
- Generic predicates make relationship inference difficult
- Noise statements reduce precision

**Recommendations:**
```sql
-- Filter statements for RAG queries
SELECT * FROM statements
WHERE confidence >= 0.85
  AND predicate NOT IN ('is', 'has', 'are')
  AND object NOT LIKE '%replies%views%';
```

### 3. Knowledge Coverage (Score: 75/100)

**Strengths:**
- Core Regen ecosystem well represented
- Key organizations and projects captured
- Good temporal coverage (Sep-Dec 2025)

**Weaknesses:**
- GitHub source underrepresented (<1%)
- No social media coverage (Twitter, Telegram)
- Historical content missing (pre-Sep 2025)

**Impact on RAG:**
- Technical questions may lack code-level answers
- Community discussions not captured
- Historical context unavailable

### 4. Entity Relationships (Score: 55/100)

**Strengths:**
- Statements capture entity relationships
- Subject-object pairs provide graph structure
- Predicates describe relationship types

**Weaknesses:**
- No graph database indexing for traversal
- Missing inverse relationships
- Cross-type relationships sparse

**Impact on RAG:**
- Multi-hop queries difficult
- "Who works at X" requires statement scan
- Relationship-based context retrieval limited

**Recommendations:**
```cypher
// Create AGE graph for relationship queries
MATCH (s:Entity {name: $subject}), (o:Entity {name: $object})
CREATE (s)-[:RELATES_TO {predicate: $predicate}]->(o)
```

### 5. Disambiguation Capability (Score: 50/100)

**Strengths:**
- Entity types help distinguish (Org vs Project)
- RIDs provide unique identification
- Source attribution available

**Weaknesses:**
- Same name, different types (Regen Registry: Org AND Project)
- Acronym ambiguity (DAO could be multiple entities)
- Person names without context (Gregory = Gregory Landua?)

**Impact on RAG:**
- Entity linking may choose wrong entity
- Ambiguous queries return mixed results
- Context-dependent disambiguation needed

## Entity Categories for RAG Filtering

### High-Quality Entities (Include in RAG)
- Confidence >= 0.85
- Name length > 3 characters
- Not in generic noun list
- Has clear type classification

**Estimated count:** 10,500 entities (71%)

### Review-Required Entities
- Confidence 0.70-0.84
- Short acronyms (3 chars)
- Cross-type duplicates

**Estimated count:** 2,500 entities (17%)

### Exclude from RAG
- Confidence < 0.70
- Generic nouns
- Pronouns (none found)
- Sentence fragments

**Estimated count:** 1,700 entities (12%)

## Recommended RAG Query Pipeline

```python
def rag_entity_search(query, min_confidence=0.85):
    """
    Search entities with quality filtering.

    1. Normalize query (lowercase, trim)
    2. Expand acronyms
    3. Search with fuzzy matching
    4. Filter by confidence threshold
    5. Deduplicate results
    6. Rank by relevance + confidence
    """
    normalized = normalize_query(query)
    expanded = expand_acronyms(normalized)

    results = search_entities(expanded)
    filtered = [e for e in results if e.confidence >= min_confidence]
    deduped = deduplicate_by_canonical(filtered)

    return rank_results(deduped)


def rag_statement_search(entity_name, min_confidence=0.85):
    """
    Get statements for entity with quality filtering.

    1. Find all statements with entity as subject/object
    2. Filter by confidence
    3. Remove noise statements
    4. Sort by relevance
    """
    statements = get_statements_for_entity(entity_name)
    filtered = [s for s in statements
                if s.confidence >= min_confidence
                and not is_noise_statement(s)]

    return sorted(filtered, key=lambda s: s.confidence, reverse=True)
```

## Metrics to Track

| Metric | Current | Target | Status |
|--------|---------|--------|--------|
| High-quality entities | 71% | 90% | 🟡 |
| Statement precision | 51% | 80% | 🔴 |
| Entity deduplication | 0% | 100% | 🔴 |
| Source coverage | 3 sources | 6 sources | 🟡 |
| Relationship density | Low | Medium | 🟡 |

## Priority Actions for RAG Improvement

1. **Immediate:** Apply entity deduplication
2. **Week 1:** Add confidence filtering to RAG queries
3. **Week 2:** Implement acronym expansion
4. **Week 3:** Build entity embedding index
5. **Month 2:** Create relationship graph for multi-hop queries
