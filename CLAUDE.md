# Project Context for Claude

**Project**: Regen Network Knowledge Graph Quality Improvement
**Status**: ✅ COMPLETE - Production Deployed (2025-12-23)
**Your Role**: AI coding assistant helping with knowledge graph quality

---

## What This Project Is

Improving the quality of Regen Network's knowledge graph (KOI system) through:
1. Better entity extraction and linking
2. Modular post-processing pipeline
3. Hybrid Graph-Boosted RAG for retrieval

**Result**: Quality improved from 62% to 99.7%

---

## Current State (2025-12-23)

### Stage 6 Re-Extraction - COMPLETE

| Metric | Value |
|--------|-------|
| Documents processed | 12,002 |
| Entities extracted | 88,322 |
| Relationships | 17,329 |
| Unique entities (entity_registry) | 30,041 |
| Unique relationships | 15,414 |

### FIX-007 Predicate Consolidation - COMPLETE

| Metric | Before | After |
|--------|--------|-------|
| Distinct predicates | 3,303 | 1,501 |
| Relationships | 15,757 | 15,414 |

### Production Deployment - COMPLETE

| Endpoint | Triples | Status |
|----------|---------|--------|
| /koi (production) | 165,619 | ✅ Deployed |
| /koi-staging | 165,619 | ✅ Deployed |

### Code↔Docs Bridge - COMPLETE

| Component | Count |
|-----------|-------|
| Code artifacts | 16,820 |
| Doc→code links | 6,453 |
| Entity→code links | 241 |
| AGE stub nodes | 5,464 |
| AGE edges | 6,463 |

### Quality Gates (All Passing)

| Gate | Check | Result |
|------|-------|--------|
| A | No http://regen.network/ | ✅ 0 |
| B1 | No ontology# types | ✅ 0 |
| B2 | No ontology# predicates | ✅ 0 |
| C | No self-ref triples | ✅ 0 |

---

## Key Scripts

### Re-Extraction
- `scripts/reextraction/stage6_full_reextract_gemini.py` - Stage 6 extraction (Gemini)
- `scripts/reextraction/stage6_reprocess_missing.py` - Reprocess failed docs

### Post-Processing
- `scripts/fix007_consolidate_predicates_postgres.py` - Predicate consolidation
- `scripts/regenerate_fuseki_graph.py` - Fuseki rebuild from PostgreSQL

### Code Bridge
- `scripts/code_bridge/export_code_artifacts.py` - Populate code artifacts
- `scripts/code_bridge/link_docs_to_code.py` - Doc-level linking
- `scripts/code_bridge/link_entities_to_code.py` - Entity-level linking
- `scripts/code_bridge/sync_stubs_to_age.py` - AGE stub sync

---

## Quality Pipeline

6 modules: ConfidenceFilter, DocumentLevelDeduplicator, CanonicalResolver, OntologyNormalizer, ListSplitter, EntityQualityFilter

**Entity Deduplication:**
- Tier 1: Exact match (B-Tree, microseconds)
- Tier 2: Semantic match (HNSW vector, milliseconds)
- Tier 3: Create new (deterministic URI)

---

## Production Environment

**Server**: darren@202.61.196.119
**Code Path**: /opt/projects/koi-processor
**Database**: PostgreSQL (eliza) on port 5433 (Docker: gaia-postgres-1)
**Fuseki**: Apache Jena Fuseki on port 3030 (Docker: fuseki-koi)
**Graph URL**: https://regen.gaiaai.xyz/graph

**Environment setup:**
```bash
cd /opt/projects/koi-processor
set -a; source .env; set +a
```

---

## Documentation

- `docs/HYBRID_RAG_ARCHITECTURE.md` - Technical architecture
- `docs/CODE_DOCS_BRIDGE.md` - Code↔Docs bridge documentation
- `docs/CHANGELOG.md` - Version history
- `/Users/darrenzal/projects/RegenAI/knowledge-graph-review-2025-12.md` - Master tracking doc

---

## Hybrid Search (2025-12-24)

**Status**: ✅ Fixed - keyword_score now working

**Root Cause**: RID mismatch in fusion - entity chunks (`UUID#chunk14`) didn't merge with keyword base docs (`UUID`)

**Key Files**:
- `koi-query-api.ts` - Keyword search with strict-first ordering
- `bge-mcp-ts/adaptive-features.ts` - Fusion with RID normalization
- `migrations/025_add_content_tsv_fts.sql` - FTS schema
- `scripts/backfill-fts.sql` - Backfill script

**Debug Flags**: `DEBUG_AUTH`, `DEBUG_EXTRACTION`, `DEBUG_FUSION`, `DEBUG_KEYWORD_SEARCH`

---

## Future Work (Optional)

1. Further predicate reduction (1,501 → ~100-200)
2. ~10 snake_case entities cleanup
3. ✅ FIX-006 (entity dedup) - DEPLOYED 2025-12-23
4. FIX-008 (dual-write strategy review)
5. Apply safe entity merges (tier1_normalized + tier1_5_canonical = 365 proposals)

---

**Last Updated**: 2025-12-24
**Phase**: Complete - All major milestones achieved
