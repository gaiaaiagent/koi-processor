# Project Context for Claude

**Project**: Regen Network Knowledge Graph Quality Improvement
**Current Phase**: Stage 6 Full Re-Extraction (docs-only corpus)
**Status**: Stage 6 running (screen: stage6)
**Your Role**: AI coding assistant helping with knowledge graph quality

---

## What This Project Is

Improving the quality of Regen Network's knowledge graph (KOI system) through:
1. Better entity extraction and linking
2. Modular post-processing pipeline
3. Hybrid Graph-Boosted RAG for retrieval

**Result**: Quality improved from 62% to 99.7%

---

## Stage 6 Re-Extraction (Docs-Only)

**Corpus definition (docs-only):**
- Include: Discourse + Notion + Website + other non-repo sources
- Include (repo sources): GitHub + GitLab docs only via `metadata.file_path`
  - `.md`, `.mdx`, `.rst`, `.txt`, `README*`, `LICENSE*`, `CHANGELOG*`, `/docs/`
- Exclude: repo rows with `file_path IS NULL` + generated/vendor/test/example paths

**Run command (server):**
```bash
cd /opt/projects/koi-processor
set -a; source .env; set +a
unset OPENAI_API_KEY
PYTHONPATH=src ./.venv/bin/python scripts/reextraction/stage6_full_reextract_gemini.py --batch-size 50 --rate-limit 0.5
```

**Monitoring:**
- Screen: `stage6`
- Checkpoint: `scripts/reextraction/.stage6_checkpoint.json`
- Log: `/home/darren/stage6_full_run.log` (buffered; use screen for live output)

**Post-run steps:**
1. Post-extraction verification (entity counts, type distribution, HTTP URIs = 0)
2. Rebuild Fuseki (staging → production)
3. Entity-level code linking (`link_entities_to_code.py`)
4. Stub sync to AGE (`sync_stubs_to_age.py`)

---

## Current State (2025-12-22)

### Completed Features
Note: Counts below are pre-Stage 6 re-extraction; Stage 6 resets entity_registry/koi_relationships.

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
- 6 modules: ConfidenceFilter, DocumentLevelDeduplicator, CanonicalResolver, OntologyNormalizer, ListSplitter, EntityQualityFilter
- Targeted KG regression suite passing
- Zero type collisions

---

## Code Bridge (Docs ↔ Code Graph)

**Tables:**
- `koi_code_artifacts` (canonical code entities)
- `koi_doc_code_links` (doc → code links, preserves MENTIONS)

**Scripts:**
- `scripts/code_bridge/export_code_artifacts.py` (populate artifacts)
- `scripts/code_bridge/link_docs_to_code.py` (doc-level linking)
- `scripts/code_bridge/link_entities_to_code.py` (entity-level linking)
- `scripts/code_bridge/sync_stubs_to_age.py` (stub sync + MENTIONS edges)

**AGE stubs:**
- `Stub:*` nodes with `sync_run_id` and mark/sweep cleanup
- `MENTIONS` edges from docs to code artifacts
- `CODE_REF` edges from linked semantic entities to code artifacts

## Key Files

### Production API
- koi-query-api.ts - Main query API with Hybrid RAG
- bge-mcp-ts/adaptive-features.ts - Fusion algorithms

### Documentation
- docs/HYBRID_RAG_ARCHITECTURE.md - Technical architecture
- docs/CHANGELOG.md - Version history

---

## Production Environment

**Server**: darren@202.61.196.119
**Code Path**: /opt/projects/koi-processor
**Database**: PostgreSQL (eliza) on port 5433 (via Docker: gaia-postgres-1)
**Fuseki**: Apache Jena Fuseki on port 3030 (via Docker: fuseki-koi)
**API**: PM2 process hybrid-rag-api on port 8301

**Fuseki Credentials**: admin:admin (for SPARQL updates)

---

**Last Updated**: 2025-12-22
**Phase**: Stage 6 docs-only re-extraction in progress
