# Hybrid Graph-Boosted RAG Architecture

## Overview

The KOI Query API implements a Hybrid Graph-Boosted Retrieval-Augmented Generation (RAG) 
system that combines three search modalities:

1. **Vector Search** - Semantic similarity using BGE-1024 embeddings
2. **Entity/Graph Search** - Knowledge graph traversal via entity-chunk links
3. **Keyword Search** - Full-text search using PostgreSQL tsvector

## Key Components

### 0. Content Deduplication (3-Layer Protection)

Prevents duplicate content from appearing in search results:

**Query-Level (koi-query-api.ts):**
- `md5(content)` PARTITION BY in all search CTEs
- Groups identical content, keeps highest-scoring instance
- Safety net for historical duplicates

**Storage-Level (koi_event_bridge_v2.py):**
- `content_hash` column (SHA-256) on koi_memories
- Global check: rejects new entries if content_hash already exists
- Catches cross-RID duplicates from sensors

**Sensor-Level (gitlab/github sensors):**
- Canonical RIDs without run-specific info (no temp dir names)
- Format: `gitlab_regen-public-docs_WhitePaper.tex` (not `gitlab_sensor_xyz123/...`)
- Prevents duplicates at the source

### 1. Entity Registry (koi_entity_registry)

Stores canonical entities with deduplication:
- Tier 1 (Exact): B-Tree index matching on entity_name_lower
- Tier 2 (Semantic): pgvector cosine similarity (0.92 threshold)
- Tier 3: Insert new entity if no match

Current stats: 12,985 unique entities, 43,430 mentions, 70.10% dedup rate

### 2. Entity-Chunk Links (koi_entity_chunk_links)

Maps entities to document chunks:
- 614,021 entity-memory associations
- B-Tree index for fast entity matching
- Enables graph-based document retrieval

### 3. Weighted Average Fusion (adaptive-features.ts)

Fusion weights:
- VECTOR_WEIGHT = 0.6 (semantic relevance)
- ENTITY_WEIGHT = 0.2 (graph/entity match)
- KEYWORD_WEIGHT = 0.2 (exact text match)
- ENTITY_BOOST = 0.15 (bonus for entity overlap)

Formula: score = (vectorScore * 0.6) + (entityScore * 0.2) + (keywordScore * 0.2) + entityBoost

### 4. Source-Diversity Sampling

Prevents any single source from dominating entity search results:

**Source Types:**
- web (orn:web.page:*)
- github (regen.github:*)
- gitlab (regen.gitlab:*)
- other

**Web Domains (for diversity within web):**
- main (regen.network)
- forum (forum.regen.network)
- registry (registry.regen.network)
- guides (guides.regen.network)

**Sampling Strategy:**
- Non-web sources: Top 25 from each source type
- Web sources: Top 10 from each domain (up to 50 total)
- Guarantees homepage (regen.network) is included in boost candidates

## Query Flow

1. Parse Query: Extract words >= 3 chars, build n-gram patterns
2. Parallel Search: Execute vector, entity, and keyword searches concurrently
3. Merge Results: Combine by document ID, track scores from each source
4. Apply Boost: Add 0.15 entity boost for overlapping documents
5. Sort and Return: Order by final weighted score

## Design Rationale

### Why Source-Diversity Sampling?

Without diversity sampling, high-volume sources like GitHub (976 matches) 
dominate entity search results, pushing out high-value sources like the 
homepage (8 matches). This prevents the homepage from getting entity boost
even though it contains relevant entities.

### Why Weighted Average over RRF?

Reciprocal Rank Fusion (RRF) compresses scores, making it hard to distinguish
between results. Weighted Average preserves score discrimination while still
combining multiple signals.

### Why Entity Boost?

Documents that appear in BOTH vector search (semantically relevant) AND 
entity search (explicitly mentions query entities) are highly likely to be
the best answers. The 0.15 boost rewards this overlap.

## Files Reference

- koi-query-api.ts - Main API with entity search + content dedup
- bge-mcp-ts/adaptive-features.ts - Fusion algorithms
- src/core/koi_event_bridge_v2.py - Storage-level dedup
- scripts/archive/entity_chunk_linker.py - Batch entity linking
- scripts/backfill_entity_registry.py - Registry population
- migrations/023_content_hash_dedup_index.sql - Content hash schema

---

*Last Updated: 2025-12-20*
