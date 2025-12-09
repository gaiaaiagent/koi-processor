# Project Context for Claude

**Project**: Regen Network Knowledge Graph Quality Improvement
**Current Phase**: Phase 2b Complete | Re-extraction Planning
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

### 🎯 Next (Phase 3)
- Re-extraction of 3,497 documents with pipeline
- Option A: Incremental re-extraction (6 weeks)

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

**Last Updated**: 2025-12-08
**Phase**: 2b Complete, planning re-extraction
**Status**: Production operational, ready for next phase
