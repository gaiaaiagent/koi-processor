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
- Quality filters (EntityQualityFilter, CanonicalResolver)
- Pipeline framework (5 modules, 121 tests)
- pgvector deduplication (76.8% dedup, 29,577 → 6,842 unique)
- Cross-document entity resolution

---

## Key Files

### Prompts (How We Got Here)
All in root directory:
- `PROMPT_1_KG_QUALITY_REVIEW.md` - Initial quality audit
- `PROMPT_2_EXTRACTION_METHOD_IMPROVEMENT.md` - Investigation & design
- `PROMPT_3_PHASE1_IMPLEMENTATION.md` - Quality filters implementation
- `PROMPT_4_WEEK1_PRODUCTION_DEPLOYMENT.md` - Cleanup & deployment
- `PROMPT_5_PHASE2A_CONFIDENCE_FILTERING.md` - Confidence filtering
- `PROMPT_6_PHASE2B_PIPELINE_FRAMEWORK.md` - Pipeline framework
- `PROMPT_7_GRAPH_INTEGRATION.md` - Graph integration
- `PROMPT_18_FRESH_EXTRACTION_EXECUTION.md` - Fresh document extraction
- `PROMPT_19_DEDUPLICATION_INVESTIGATION.md` - Initial dedup gap investigation
- `PROMPT_20_COMPREHENSIVE_DEDUP_INVESTIGATION.md` - ✅ Complete yonearth module analysis
- `PROMPT_20B_GRAPH_INSERTION_INVESTIGATION.md` - ✅ Graph-as-registry investigation
- `PGVECTOR_INVESTIGATION_FINDINGS.md` - ✅ pgvector infrastructure analysis
- `PROMPT_21_IMPLEMENT_PGVECTOR_DEDUPLICATION.md` - ✅ **COMPLETE** - pgvector dedup (35 tests, A+ grade)
- `PROMPT_21_OLD_JENA_TEXT_APPROACH.md` - Archived: Original Jena Text approach
- `EXPERT_FEEDBACK_INCORPORATED.md` - ✅ Expert review changes (A- → A+)
- `PROMPT_22_BACKFILL_EXISTING_ENTITIES.md` - ✅ **COMPLETE** - Backfill (76.8% dedup, 29,577 → 6,842)
- `BACKFILL_RESULTS_ANALYSIS.md` - ✅ Analysis of backfill results
- `BACKFILL_DOTENV_FIX.md` - ✅ Fix for .env loading issue (Tier 2 now enabled)
- `PROMPT_23_RESUME_GITHUB_EXTRACTION.md` - **READY** - Resume extraction (4,410 docs, 5-7 hours)

**Summary**: `ALL_PROMPTS_SUMMARY.md`

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
User wants to push to GitHub, then plan re-extraction

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
- ✅ Backups exist (651MB PostgreSQL + 3.6MB Fuseki)
- ✅ Rollback procedure documented
- ✅ Both pipeline and legacy modes work
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
**Phase**: RAG Search Complete, Entity Extraction Enhancement
**Status**: YouTube searchable via RAG UNION query. Jena extraction pending schema fixes.
