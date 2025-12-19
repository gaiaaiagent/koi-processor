# Project Context for Claude

**Project**: Regen Network Knowledge Graph & RAG System
**Current Phase**: RAG Search Working, Entity Extraction Enhancement Pending
**Status**: YouTube searchable via RAG, Jena extraction needs schema work
**Your Role**: AI coding assistant for KOI knowledge infrastructure

---

## What This Project Is

The KOI (Knowledge Organization Infrastructure) system for Regen Network:
1. **Document ingestion** via sensors (YouTube, GitHub, Discourse, etc.)
2. **Vector embeddings** for semantic search (RAG)
3. **Entity extraction** to knowledge graphs (Apache AGE for code, Jena for general)
4. **Quality pipeline** with filters and normalization

**Quality Result**: 62% → 99.7% entity quality

---

## Current State (2025-12-19)

### ✅ Recently Completed
- **YouTube Transcription Fixed**: Scribe API parsing corrected (`transcript_text` field)
- **RAG Search UNION**: Queries both `koi_embeddings` (49K) and `koi_memory_chunks` (1K+)
- **YouTube Searchable**: "Karl Friston active inference" returns YouTube chunks
- **Service Ports Separated**: Code Graph (8350), Adaptive Extraction (8351), Hybrid RAG (8301)

### 🔄 In Progress
- **Jena Entity Extraction**: Schema mismatches blocking extraction
- See `docs/ADAPTIVE_EXTRACTION_SCHEMA_TODO.md` for details

### ✅ Previously Complete (Phases 1-3)

**Phase 1-2** (Quality & Pipeline):
- Quality filters (EntityQualityFilter, CanonicalResolver)
- Pipeline framework (5 modules, 121 tests)
- Graph integration (pipeline + legacy modes)
- Production deployment (zero errors)

**Phase 3** (Cross-Document Deduplication) - COMPLETE 2025-12-10:
1. ✅ Investigation & Design (PROMPT_19-21)
   - Discovered pgvector infrastructure
   - Implemented 3-tier deduplication waterfall
   - 35 tests passing, A+ code quality grade

2. ✅ Infrastructure Deployment (PROMPT_21-22)
   - Tier 1 (Exact): B-Tree index matching (microseconds)
   - Tier 2 (Semantic): pgvector + OpenAI embeddings (milliseconds)
   - Tier 3 (New): Insert new entities
   - Backfill: 76.8% dedup rate achieved

3. ✅ Quality Improvements (PROMPT_24)
   - EntityQualityFilter: JIRA/boilerplate/placeholder blocking
   - DocumentDedupModule: Prevents chunk repetition
   - CanonicalResolver: Type mismatch handling + Regen brand terms
   - Validation: 100% JIRA reduction, 95% chunk dedup

4. ✅ Full Re-extraction (PROMPT_26)
   - Re-extracted 878 Discourse + GitHub Issues documents
   - Applied all PROMPT_24 improvements
   - Result: ALL bad entities eliminated (0 occurrences)

5. ✅ Entity Consolidation (PROMPT_27)
   - Fixed type mismatches: 678 merges, 0 collisions remaining
   - Enabled Tier 2 semantic dedup: 68.6% dedup rate
   - Final state: 13,227 unique entities, 43,909 mentions, 69.88% dedup
   - Quality: Production-ready with zero errors

6. ✅ Batch Semantic Consolidation (PROMPT_30) - COMPLETE 2025-12-10:
   - Semantic clustering (0.88 threshold): 189 entities merged
   - Domain expert review: 9 false positives split, 2 keeps validated
   - Final state: 12,985 unique entities, 43,430 mentions, 70.10% dedup
   - Quality: Zero type collisions, production-ready

7. ✅ Knowledge Graph Deployment (2025-12-11):
   - Fuseki graph regenerated from entity_registry
   - PostgreSQL → RDF export: 12,985 entities → 64,925 triples
   - Graph synchronized (PostgreSQL + Fuseki)
   - Production endpoints: http://localhost:3030/koi

---

## Key Files

### Prompts (How We Got Here)

**Active Prompts** (`prompts/`):
- `ALL_PROMPTS_SUMMARY.md` - Complete project history summary
- `PROMPT_24_PIPELINE_IMPROVEMENTS_QUALITY_FIXES.md` - Quality improvements (JIRA/boilerplate/dedup)
- `PROMPT_27_ENTITY_REGISTRY_CONSOLIDATION.md` - Type fixes + semantic consolidation
- `PROMPT_30_BATCH_CONSOLIDATION_REVIEW.md` - Batch semantic consolidation + user review

**Archived Prompts** (`prompts/archive/`):
- `PROMPT_1-23` - Phase 1-3 implementation history (21 prompts)
- Supporting docs: Investigation findings, expert feedback, analysis reports

**Key Reports** (`reports/phase3/`):
- Production server only - generated during execution

### Code
Main codebase: `koi-processor/`

**Pipeline Framework**:
- `src/knowledge_graph/postprocessing/` - Framework + modules
- `src/knowledge_graph/postprocessing/modules/` - 5 modules
- `src/knowledge_graph/config/pipeline_config.json` - Configuration

**Tests**: `tests/` (121 passing)

**Scripts**: `scripts/` (validation, testing)

### Reports
- `koi-processor/reports/phase1/` - Phase 1 results
- `koi-processor/reports/phase2/` - Phase 2 results
- `koi-processor/reports/quality_review/` - Quality analysis

---

## Production Environment

**Server**: `darren@202.61.196.119`
**Database**: PostgreSQL on port 5433 (database: eliza)

### Service Ports
| Service | Port | Purpose |
|---------|------|---------|
| Coordinator | 8005 | Event routing from sensors |
| Semantic Bridge | 8004 | Event processing, embeddings |
| Hybrid RAG API | 8301 | Unified search (koi-query-api.ts) |
| Code Graph Service | 8350 | GitHub code → Apache AGE |
| Adaptive Extraction | 8351 | General entities → Jena |
| Apache Jena Fuseki | 3030 | Knowledge graph (SPARQL) |
| BGE Embedding Server | 8090 | Text embeddings |

### Databases
- **PostgreSQL + pgvector**: Document embeddings (`koi_embeddings`, `koi_memory_chunks`)
- **PostgreSQL + Apache AGE**: Code entity graph (`regen_graph`)
- **Apache Jena Fuseki**: General knowledge graph (155K triples)

**Metrics**:
- Quality: 99.7%
- Tests: 121/121 passing
- YouTube docs: 47 videos, 1,068 chunks with transcripts
- Entity Registry: 12,985 unique entities, 43,430 mentions, 70.10% dedup rate
- Knowledge Graph: 64,925 RDF triples (Fuseki synchronized)

---

## Pipeline Architecture

### Framework Components
1. **ProcessingContext** - Shared state object
2. **PostProcessingModule** - Base class for modules
3. **PipelineOrchestrator** - Executes modules in sequence
4. **PipelineBuilder** - Creates from configuration

### Modules (5 operational)
1. **ConfidenceFilter** - Blocks low-confidence (< 0.70)
2. **CanonicalResolver** - Resolves aliases (88 entries, 194 aliases)
3. **EntityQualityFilter** - Blocks pronouns, generics, URLs, patterns
4. **ListSplitter** - Splits "A and B" → separate entities
5. **OntologyNormalizer** - Standardizes types (COMPANY → ORGANIZATION)

### Usage

```python
from knowledge_graph.graph_integration import KnowledgeGraphIntegrator

# Pipeline mode (default)
kg = KnowledgeGraphIntegrator(
    store_type="memory",
    use_pipeline=True
)

# Process entities
valid_entities = kg.process_entities_batch([...])
```

See `koi-processor/src/knowledge_graph/README.md` for full docs.

---

## Next Steps

### Immediate
- Complete Jena entity extraction (fix schema issues in `docs/ADAPTIVE_EXTRACTION_SCHEMA_TODO.md`)
- Consider batch extraction for existing YouTube content

### Re-extraction Plan
See `RE_EXTRACTION_PLAN.md` for full strategy.

**Option A** (Recommended): Incremental re-extraction
- Week 1: Secure backups + build scripts
- Week 2: Pilot (100 documents)
- Weeks 3-5: Full re-extraction (3,497 documents)
- Week 6: Analysis + reporting

**Goal**: 99.7% → 99.9%+ quality

---

## Common Tasks

### Run Tests
```bash
cd koi-processor
pytest tests/test_pipeline_framework.py tests/test_pipeline_modules.py tests/test_graph_integration.py
# Expected: 121 passed
```

### Check Production
```bash
ssh darren@202.61.196.119 "cd /opt/projects/koi-processor && python3 -m pytest tests/ --tb=no -q"
```

### Query Knowledge Graph
```bash
ssh darren@202.61.196.119 "curl -s 'http://localhost:3030/koi/sparql' --data-urlencode 'query=SELECT (COUNT(*) as ?count) WHERE { ?s ?p ?o }'"
```

---

## Important Notes

### Production Safety
- ✅ Backups exist (767MB PostgreSQL + 3.6MB Fuseki)
- ✅ Rollback procedure documented (PRODUCTION_DEPLOYMENT_SUMMARY.md:218-232)
- ✅ Both pipeline and legacy modes work
- ✅ Knowledge graph synchronized (PostgreSQL ↔ Fuseki)
- ⚠️ Always backup before re-extraction

### Code Quality
- 121 tests passing (269% of target)
- < 1% performance overhead
- Zero errors in production
- Backward compatible

### Documentation
- All phases documented in prompts
- Reports organized by phase
- Architecture docs updated
- Pipeline usage guide complete

---

## If You Need Help

1. **Check prompts**: `PROMPT_[1-7]_*.md` show how we got here
2. **Check reports**: `koi-processor/reports/` has detailed results
3. **Check tests**: `tests/` show expected behavior
4. **Check docs**: `koi-processor/docs/` has technical details

---

## Project Philosophy

- **Quality over speed**: 99.7% quality maintained
- **Testing first**: 121 tests before deployment
- **Modular design**: Easy to extend (5 modules)
- **Configuration-driven**: No code changes for tuning
- **Backward compatible**: Legacy mode still works

---

**Last Updated**: 2025-12-19
**Phase**: RAG Search Complete, Entity Extraction Enhancement Pending
**Status**: YouTube searchable via RAG UNION query. Jena extraction pending schema fixes.
**Systems**: Tier 1+2 dedup active, all pipeline modules operational, Fuseki graph deployed
