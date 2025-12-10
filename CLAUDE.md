# Project Context for Claude

**Project**: Regen Network Knowledge Graph Quality Improvement
**Current Phase**: Phase 3 PAUSED - Implementing Cross-Document Deduplication
**Status**: CRITICAL - Extraction stopped at 300/4,710 docs
**Your Role**: AI coding assistant helping with knowledge graph quality

---

## What This Project Is

Improving the quality of Regen Network's knowledge graph (KOI system) through:
1. Better entity extraction
2. Modular post-processing pipeline
3. Quality control filters

**Result**: Quality improved from 62% → 99.7%

---

## Current State

### ✅ Complete (Phases 1-2)
- Quality filters (EntityQualityFilter, CanonicalResolver)
- Pipeline framework (5 modules, 121 tests)
- Graph integration (pipeline + legacy modes)
- Production deployment (zero errors)

### ⚠️ CURRENT CRITICAL ISSUE (2025-12-09)

**Extraction STOPPED**: GitHub extraction paused at 300/4,710 docs

**Reason**: Critical gap discovered - no cross-document entity deduplication

**Problem**:
- "Regen Network" + "Regen" + "REGEN" + "$REGEN" = 2,261 duplicate entries
- "Gregory Landua" + "Gregory" + "Gregory_RND" = 290 duplicate entries
- ~30-40% entity fragmentation across knowledge graph

**Solution**: pgvector-based semantic deduplication (PROMPT_21)

**Action Items**:
1. ✅ Investigation complete (PROMPT_19) - Initial findings
2. ✅ Comprehensive investigation (PROMPT_20) - yonearth modules documented
3. ✅ **Graph insertion strategy** (PROMPT_20B) - yonearth uses batch model, not incremental
4. ✅ **pgvector investigation** - Discovered existing infrastructure, revised architecture
5. ✅ **Implement pgvector deduplication** (PROMPT_21) - COMPLETE (35 tests passing, A+ grade)
6. ✅ **Backfill existing entities** (PROMPT_22) - COMPLETE (76.8% dedup, 29,577 → 6,842 unique)
7. ✅ **Fix .env loading** - Added load_dotenv() to backfill script (Tier 2 now enabled)
8. 🔄 **Resume GitHub extraction** (PROMPT_23) - READY (4,410 docs remaining, ~5-7 hours)

### 🎯 Phase 3 Status
- ✅ Re-extraction: 1,016 docs complete (97.63% quality)
- ✅ Fresh extraction: Discourse (839), YouTube (15), GitLab (200), GitHub Activity (51)
- 🟡 Fresh extraction: GitHub Markdown (300/4,710) - **PAUSED**

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

**Server**: `darren@202.61.196.119:5433`
**Database**: PostgreSQL (eliza)
**Graph**: Apache Jena Fuseki (http://localhost:3030/koi)
**Pipeline Status**: ACTIVE (default mode)

**Metrics**:
- Quality: 99.7%
- Tests: 121/121 passing
- Performance: < 1% overhead
- Documents: 3,497 processed

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

**Last Updated**: 2025-12-10
**Phase**: Phase 3 - Cross-Document Deduplication
**Status**: Infrastructure COMPLETE (A+ grade) - PROMPT_23 ready for execution (4,410 docs, 5-7 hours)
