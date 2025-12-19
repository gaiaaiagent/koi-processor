# pgvector Infrastructure Investigation Findings

**Date**: 2025-12-09
**Context**: Investigating existing pgvector infrastructure to revise deduplication architecture

---

## Executive Summary

**Key Finding**: pgvector is installed and operational, but **entity-level embeddings do not exist**. Only document-level embeddings are currently generated.

**Implication**: We can use the pgvector waterfall strategy (Exact → Vector → New) as suggested by the expert, but we need to **create** the entity_registry infrastructure from scratch.

---

## Current Infrastructure

### ✅ What EXISTS

1. **pgvector Extension**: Installed (version 0.5.1)

2. **koi_embeddings Table**: Operational with:
   - Multiple vector dimensions: 384, 512, 768, 1024, 1536, 3072
   - HNSW index on dim_1024 (fast approximate nearest neighbor)
   - IVFFlat indexes on dim_768 and dim_1536
   - `jena_uri` and `jena_embedding_uri` columns (currently unused)

3. **Embedding Generation**: Code exists in:
   - `src/core/koi_event_bridge_v2.py`
   - `src/core/bge_server.py`
   - `src/core/koi_knowledge_mcp_server.py`

### ❌ What DOES NOT EXIST

1. **Entity-Level Embeddings**:
   - Current embeddings are for **documents** (koi_memories)
   - NOT for individual extracted entities

2. **entity_registry Table**:
   - No table linking entity text → embedding → fuseki_uri
   - No deduplication registry

3. **Entity Resolver**:
   - No code for Exact → Vector → New waterfall logic
   - No fuzzy matching for entities

---

## Current Entity Storage

### koi_kg_extractions Table

**Structure**:
```sql
- id (PK)
- memory_rid (link to source document)
- extraction_rid (unique)
- entities (JSONB array)  -- ← Entities stored here
- statements (JSONB)
- relations (JSONB)
- confidence_score
- created_at
```

**Entity Format** (JSONB):
```json
{
  "name": "regen",
  "type": "Organization",
  "confidence": 0.8,
  "properties": {}
}
```

**Current Stats**:
- 5,907 extractions
- 29,577 total entities
- **Heavy duplication** (e.g., "regen" appears many times as separate entities)

---

## Duplication Examples

From sample query of entities matching 'regen':

```
| entity_name                         | entity_type  |
|-------------------------------------|--------------|
| regen                               | Organization | ← Duplicate 1
| regen                               | Organization | ← Duplicate 2
| /regen/data/v2/anchor-by-hash       | Project      |
| regen.data.v2.Query/AnchorByIRI     | Project      |
| regen1k82wewrfkhdmegw6uxrgwwzrsd... | Person       | ← Duplicate 1
| regen1k82wewrfkhdmegw6uxrgwwzrsd... | Person       | ← Duplicate 2
```

**Problem**: Each extraction creates duplicate entity entries, even for exact same name + type.

---

## Revised Architecture: pgvector Waterfall

Based on expert advice and existing infrastructure, here's what we need to build:

### 1. Create entity_registry Table

```sql
CREATE TABLE entity_registry (
    id SERIAL PRIMARY KEY,
    fuseki_uri TEXT UNIQUE NOT NULL,      -- Deterministic URI
    entity_text TEXT NOT NULL,            -- Original name
    entity_type TEXT NOT NULL,            -- PERSON, ORGANIZATION, etc.
    normalized_text TEXT NOT NULL,        -- Lowercased, cleaned
    embedding VECTOR(1536) NOT NULL,      -- Semantic embedding
    first_seen_at TIMESTAMP DEFAULT NOW(),
    last_seen_at TIMESTAMP DEFAULT NOW(),
    occurrence_count INTEGER DEFAULT 1
);

-- L1: Exact Match Index (B-Tree, fastest)
CREATE INDEX idx_entity_exact ON entity_registry (normalized_text, entity_type);

-- L2: Semantic Match Index (HNSW, vector similarity)
CREATE INDEX idx_entity_vector ON entity_registry
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- Optional L1.5: Fuzzy Trigram Index (typos)
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX idx_entity_trigram ON entity_registry
  USING gin (normalized_text gin_trgm_ops);
```

### 2. Three-Tier Lookup Strategy

**Tier 1: Exact Lexical Match** (instant, ~microseconds)
```sql
SELECT fuseki_uri
FROM entity_registry
WHERE normalized_text = LOWER(?)
  AND entity_type = ?
```

**Tier 2: Semantic Vector Match** (fast, ~milliseconds)
```sql
SELECT fuseki_uri, entity_text,
       1 - (embedding <=> ?) AS similarity
FROM entity_registry
WHERE 1 - (embedding <=> ?) > 0.92
  AND entity_type = ?
ORDER BY similarity DESC
LIMIT 1
```

**Tier 3: Create New Entity**
- Generate deterministic URI (SHA256 hash)
- Generate embedding
- Insert into registry
- Insert into Fuseki

### 3. Deterministic URI Generation

**Strategy**: Content-addressable URIs using SHA256

```python
def generate_uri(entity_text, entity_type):
    normalized = normalize(entity_text)  # lowercase, trim, etc.
    content = f"{normalized}:{entity_type}"
    hash_digest = hashlib.sha256(content.encode()).hexdigest()
    return f"https://regen.network/{type_prefix}/{hash_digest[:16]}"
```

**Benefits**:
- Same entity always gets same URI (anti-duplication shield)
- No collisions (SHA256 is cryptographically secure)
- Deterministic (reproducible)

### 4. Self-Healing Fuseki Sync

**Problem**: Dual-write risk (Postgres succeeds, Fuseki fails)

**Solution**: Lazy repair
```python
if uri_from_postgres:
    # Check if exists in Fuseki
    exists = fuseki.ask(f"ASK {{ <{uri}> ?p ?o }}")
    if not exists:
        # Self-heal: Re-insert skeleton
        fuseki.insert_skeleton(uri, entity_text, entity_type)
```

---

## Comparison: Original Plan vs Revised Plan

| Aspect | Original (PROMPT_21) | Revised (pgvector) |
|--------|----------------------|--------------------|
| **L1** | Python LRU cache | Postgres exact match (B-Tree) |
| **L2** | Fuseki Lucene | Postgres vector (HNSW) |
| **L3** | Deterministic URI | Deterministic URI (same) |
| **Fuzzy Search** | Jena Text config | pgvector cosine similarity |
| **Matching Type** | Lexical (spelling) | Semantic (meaning) |
| **Infrastructure** | New (Lucene setup) | Existing (pgvector) |
| **Complexity** | High (Fuseki reconfig) | Medium (new table only) |
| **Performance** | Good | Better (HNSW is faster) |

---

## Advantages of pgvector Approach

1. **Semantic Matching**: "IBM" matches "International Business Machines" (vectors are similar)
2. **No Fuseki Changes**: Don't need to reconfigure Jena Text/Lucene
3. **Simpler**: One new table vs complex Fuseki setup
4. **Faster**: HNSW is optimized for vector search
5. **More Accurate**: Meaning > spelling
6. **Existing Infrastructure**: pgvector already installed
7. **Proven**: Already generating embeddings for documents

---

## Implementation Plan

### Phase 1: Database Setup (30 mins)
- Create entity_registry table
- Add indexes (B-Tree, HNSW, optional pg_trgm)

### Phase 2: URI Generator (30 mins)
- Implement DeterministicURIGenerator class
- Write tests for deterministic behavior

### Phase 3: Entity Resolver (1 hour)
- Implement waterfall logic (Exact → Vector → New)
- Add embedding generation for entity names
- Write tests

### Phase 4: Integration (1 hour)
- Update graph_integration.py to use resolver
- Add self-healing Fuseki sync
- Monitor deduplication metrics

### Phase 5: Validation (30 mins)
- End-to-end test with sample entities
- Verify "regen" + "Regen Network" → same URI
- Check performance (L1 hit rate, L2 latency)

**Total Estimated Time**: 3.5 hours (vs 6-8 hours for Jena Text approach)

---

## Next Steps

1. ✅ Investigation complete (this document)
2. ⏳ Revise PROMPT_21 with pgvector architecture
3. ⏳ Create entity_registry table
4. ⏳ Implement DeterministicURIGenerator
5. ⏳ Implement EntityResolver with waterfall logic
6. ⏳ Integrate with graph insertion pipeline
7. ⏳ Test and validate
8. ⏳ Resume GitHub extraction (300/4,710)

---

## Critical Questions Answered

**Q: Can we use existing embeddings?**
A: No. Current embeddings are for documents, not entities. We need to generate entity-specific embeddings.

**Q: What embedding model to use?**
A: Use same as documents (OpenAI text-embedding-ada-002, 1536 dimensions) for consistency.

**Q: How to handle existing 29,577 entities?**
A: Backfill: Generate embeddings for all existing entities, deduplicate, update registry.

**Q: What about performance with 29,577+ entities?**
A: HNSW index handles millions of vectors efficiently. With HNSW (m=16, ef=64), expect <100ms per query.

**Q: Threshold for vector similarity?**
A: Start with 0.92 (recommended by expert). Tune based on false positive/negative rates.

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Dual-write failure | Medium | Self-healing Fuseki sync |
| Vector threshold too loose | Medium | Start conservative (0.92), monitor, tune |
| Embedding generation slow | Low | Batch process, cache |
| HNSW index rebuild time | Low | Build offline, swap in |

---

**Status**: Investigation complete
**Recommendation**: Proceed with pgvector waterfall approach
**Estimated Savings**: 2.5-4.5 hours vs Jena Text approach
**Quality Improvement**: Better (semantic > lexical matching)
