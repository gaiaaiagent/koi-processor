# Project Context for Claude

**Project**: Regen Network Knowledge Graph Quality Improvement
**Current Phase**: Phase 3 COMPLETE - Hybrid RAG Deployed
**Status**: ALL SYSTEMS OPERATIONAL
**Your Role**: AI coding assistant helping with knowledge graph quality

---

## What This Project Is

Improving the quality of Regen Network's knowledge graph (KOI system) through:
1. Better entity extraction and linking
2. Modular post-processing pipeline
3. Hybrid Graph-Boosted RAG for retrieval

**Result**: Quality improved from 62% to 99.7%

---

## Current State (2025-12-20)

### Completed Features

**Entity System:**
- Entity Registry: 12,985 unique entities, 70.10% dedup rate
- Entity-Chunk Links: 614,021 associations
- Semantic deduplication (Tier 1 exact + Tier 2 embedding)

**Hybrid RAG:**
- Vector search (BGE-1024 embeddings)
- Entity/Graph search (koi_entity_chunk_links)
- Keyword search (PostgreSQL tsvector)
- Weighted Average Fusion (0.6V + 0.2E + 0.2K + 0.15 boost)
- Source-Diversity Sampling (prevents GitHub from dominating)
- 3-Layer Content Dedup: Query (md5), Storage (SHA-256), Sensor (canonical RIDs)

**Content Deduplication:**
- Active memories: 31,265 unique documents
- Query-level: md5(content) partitioning in SQL CTEs
- Storage-level: content_hash column with global check
- Sensor-level: Canonical RIDs (no temp dir names)

**Quality Pipeline:**
- 5 modules: ConfidenceFilter, CanonicalResolver, EntityQualityFilter, ListSplitter, OntologyNormalizer
- 121 tests passing
- Zero type collisions

---

## Key Files

### Production API
- koi-query-api.ts - Main query API with Hybrid RAG
- bge-mcp-ts/adaptive-features.ts - Fusion algorithms

### Documentation
- docs/HYBRID_RAG_ARCHITECTURE.md - Technical architecture
- docs/CHANGELOG.md - Version history

---

## Production Environment

**Server**: darren@202.61.196.119:5433
**Database**: PostgreSQL (eliza)
**API**: PM2 process hybrid-rag-api on port 8301

---

**Last Updated**: 2025-12-20
**Phase**: Hybrid RAG v2.0.0 Deployed
