# Project Context for Claude

**Project**: Regen Network Knowledge Graph Quality Improvement
**Current Phase**: Phase 3 COMPLETE - Production Ready
**Status**: ✅ ALL SYSTEMS OPERATIONAL
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

### ✅ Complete (All Phases)

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

---

## Key Files

### Prompts (How We Got Here)

**Active Prompts** (`prompts/`):
- `ALL_PROMPTS_SUMMARY.md` - Complete project history summary
- `PROMPT_24_PIPELINE_IMPROVEMENTS_QUALITY_FIXES.md` - Quality improvements (JIRA/boilerplate/dedup)
- `PROMPT_27_ENTITY_REGISTRY_CONSOLIDATION.md` - Type fixes + semantic consolidation

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
**Phase**: Phase 3 COMPLETE
**Status**: ✅ PRODUCTION READY
**Entity Registry**: 13,227 unique entities, 43,909 mentions, 69.88% dedup rate
**Quality**: Zero type collisions, zero placeholders, zero errors
**Systems**: Tier 1+2 dedup active, all pipeline modules operational
