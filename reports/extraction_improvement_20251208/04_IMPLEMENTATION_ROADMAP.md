# Implementation Roadmap
## Regen KOI Knowledge Graph Extraction Improvement

**Date**: 2025-12-08
**Status**: Phased Implementation Plan
**Total Phases**: 4
**Focus**: Measurable quality improvements with minimal disruption

---

## Executive Summary

This roadmap prioritizes improvements by **impact** (quality gain) and **effort** (implementation complexity). The goal is to achieve significant quality improvements early while building toward a comprehensive extraction pipeline.

### Priority Matrix

| Improvement | Impact | Effort | Priority |
|-------------|--------|--------|----------|
| Entity Quality Filter | HIGH | LOW | P0 - Critical |
| Canonical Entity Registry | HIGH | LOW | P0 - Critical |
| Fuzzy Deduplication | HIGH | MEDIUM | P1 - High |
| Ontology Normalization | MEDIUM | LOW | P1 - High |
| List Splitter | MEDIUM | MEDIUM | P1 - High |
| Discourse Extraction Profile | HIGH | MEDIUM | P2 - Medium |
| Privacy Tagging | MEDIUM | LOW | P2 - Medium |
| Batch API Integration | MEDIUM | HIGH | P3 - Low |
| GraphRAG Clustering | LOW | HIGH | P3 - Low |

---

## Phase 1: Critical Quality Fixes

**Duration**: 1-2 weeks
**Goal**: Immediate quality improvement on existing data
**Deliverables**: Working EntityQualityFilter + Canonical Registry

### Task 1.1: EntityQualityFilter Implementation

**Effort**: 2-3 days
**Dependencies**: None
**Success Metric**: 10%+ reduction in low-quality entities

```
Files to Create:
├── src/knowledge_graph/improvements/
│   ├── __init__.py
│   ├── entity_quality_filter.py
│   └── tests/
│       └── test_entity_quality_filter.py
```

**Implementation Steps**:
1. [ ] Create module with 7 filter checks (pronouns, numerics, etc.)
2. [ ] Add unit tests for each filter type
3. [ ] Test on sample of current Regen entities
4. [ ] Generate quality metrics report
5. [ ] Document filter rules and exceptions

**Acceptance Criteria**:
- [ ] Filters block: pronouns, generic nouns, numerics, tautological, sentence-like
- [ ] >80% unit test coverage
- [ ] Tested on 1,000+ real Regen entities
- [ ] Before/after comparison shows measurable improvement

### Task 1.2: Canonical Entity Registry

**Effort**: 1-2 days
**Dependencies**: None
**Success Metric**: Core Regen entities have consistent names

```
Files to Create:
├── data/
│   └── canonical_entities.json
```

**Registry Contents**:
```json
{
  "organizations": {
    "regen-network": {
      "canonical_name": "Regen Network",
      "aliases": [
        "regen.network", "Regen", "RND",
        "Regen Network Development",
        "Regen Network Inc"
      ]
    },
    "regen-registry": {
      "canonical_name": "Regen Registry",
      "aliases": ["Registry", "Regen Registry Program"]
    }
  },
  "projects": {
    "ecocredit": {
      "canonical_name": "Regen Ecocredit Module",
      "aliases": ["ecocredit", "eco-credit", "Ecocredit"]
    }
  },
  "people": {
    // Top contributors, authors, validators
  }
}
```

**Implementation Steps**:
1. [ ] Analyze current entity data for common variants
2. [ ] Create initial registry with 50+ entries
3. [ ] Add merge patterns for complex cases
4. [ ] Create resolver module
5. [ ] Test on sample data

### Task 1.3: Integration Test

**Effort**: 1 day
**Dependencies**: 1.1, 1.2
**Success Metric**: Pipeline runs end-to-end on test data

**Steps**:
1. [ ] Create test harness with sample entities
2. [ ] Run filter + resolver in sequence
3. [ ] Generate before/after quality report
4. [ ] Document any edge cases or issues

---

## Phase 2: Pipeline Architecture

**Duration**: 1-2 weeks
**Goal**: Establish modular post-processing framework
**Deliverables**: Working pipeline with 4+ modules

### Task 2.1: Base Framework

**Effort**: 2-3 days
**Dependencies**: Phase 1

```
Files to Create:
├── src/knowledge_graph/postprocessing/
│   ├── __init__.py
│   ├── base.py              # ProcessingContext, PostProcessingModule
│   ├── pipeline.py          # PipelineOrchestrator
│   └── tests/
│       └── test_pipeline.py
```

**Implementation Steps**:
1. [ ] Port ProcessingContext from design doc
2. [ ] Port PostProcessingModule abstract class
3. [ ] Implement PipelineOrchestrator
4. [ ] Add unit tests for framework
5. [ ] Document module creation pattern

### Task 2.2: Migrate Quality Filter to Framework

**Effort**: 1 day
**Dependencies**: 2.1

**Steps**:
1. [ ] Refactor EntityQualityFilter to extend PostProcessingModule
2. [ ] Add proper stats tracking
3. [ ] Update tests

### Task 2.3: Implement OntologyNormalizer

**Effort**: 2 days
**Dependencies**: 2.1

```
Files to Create:
├── src/knowledge_graph/postprocessing/
│   └── ontology_normalizer.py
├── data/
│   └── ontology/
│       ├── entity_types.json
│       └── predicates.json
```

**Steps**:
1. [ ] Define type mapping (COMPANY → FORMAL_ORGANIZATION)
2. [ ] Define predicate mapping
3. [ ] Create JSON schema files
4. [ ] Implement normalizer module
5. [ ] Add tests

### Task 2.4: Implement FuzzyDeduplicator

**Effort**: 2-3 days
**Dependencies**: 2.3

**Steps**:
1. [ ] Implement similarity function (character bigrams)
2. [ ] Implement type-aware grouping
3. [ ] Implement merge logic
4. [ ] Configure 85% threshold
5. [ ] Add tests with known duplicates

### Task 2.5: Implement ListSplitter

**Effort**: 2 days
**Dependencies**: 2.1

**Steps**:
1. [ ] Port list detection logic
2. [ ] Port split logic with compound term protection
3. [ ] Add POS tagging support (optional)
4. [ ] Add tests

---

## Phase 3: Source-Specific Profiles

**Duration**: 1-2 weeks
**Goal**: Tailored extraction for top 3 sources
**Deliverables**: Discourse, Notion, Medium profiles

### Task 3.1: Extraction Profile Framework

**Effort**: 1-2 days
**Dependencies**: Phase 2

```
Files to Create:
├── src/extraction/
│   ├── profiles/
│   │   ├── __init__.py
│   │   ├── base_profile.py
│   │   └── profile_loader.py
├── data/
│   └── extraction_profiles/
│       └── schema.json
```

### Task 3.2: Discourse Profile

**Effort**: 2-3 days
**Dependencies**: 3.1

```
Files to Create:
├── data/extraction_profiles/
│   └── discourse_v1.json
├── src/extraction/profiles/
│   └── discourse.py
```

**Profile Contents**:
- Entity types: PERSON, PROPOSAL, TOPIC, CONCEPT, PROJECT
- Predicates: asked, answered, proposed, supports, opposes
- System prompt: Forum-optimized extraction instructions
- Special handling: Quote attribution, thread structure

### Task 3.3: Notion Profile

**Effort**: 2 days
**Dependencies**: 3.1

**Profile Contents**:
- Entity types: STRATEGY, PARTNER, MILESTONE, INITIATIVE
- Predicates: proposes, partners_with, targets, assigned_to
- Privacy: Default private, sensitivity keywords
- Special handling: Database properties, page hierarchy

### Task 3.4: Medium Profile

**Effort**: 2 days
**Dependencies**: 3.1

**Profile Contents**:
- Entity types: ARTICLE, AUTHOR, CONCEPT, CASE_STUDY
- Predicates: authored, explains, showcases, cites
- Special handling: Section context, author attribution

### Task 3.5: PrivacyTagger Module

**Effort**: 1-2 days
**Dependencies**: 3.3

```
Files to Create:
├── src/knowledge_graph/postprocessing/
│   └── privacy_tagger.py
```

---

## Phase 4: Advanced Features

**Duration**: 2-4 weeks
**Goal**: Cost optimization and visualization
**Deliverables**: Batch API, GraphRAG clustering

### Task 4.1: Chunking Strategy Implementation

**Effort**: 2-3 days
**Dependencies**: Phase 2

```
Files to Create:
├── src/extraction/
│   └── chunking/
│       ├── __init__.py
│       ├── parent_child_chunker.py
│       └── strategies/
│           ├── discourse_chunker.py
│           ├── article_chunker.py
│           └── chat_chunker.py
```

**Configuration**:
- Parent chunks: ~3,000 tokens (context)
- Child chunks: ~600 tokens (indexing)
- Source-specific strategies

### Task 4.2: Batch API Integration

**Effort**: 3-5 days
**Dependencies**: 4.1

```
Files to Create:
├── src/extraction/
│   └── batch/
│       ├── __init__.py
│       ├── batch_submitter.py
│       ├── batch_poller.py
│       ├── result_processor.py
│       └── retry_handler.py
```

**Features**:
- Batch job submission
- Status polling
- Result download
- Failed chunk retry
- Progress tracking

### Task 4.3: RelationshipValidator Module

**Effort**: 1-2 days
**Dependencies**: Phase 2

**Features**:
- Target existence check
- Self-loop removal
- Duplicate relationship pruning

### Task 4.4: GraphRAG Clustering (Future)

**Effort**: 5+ days
**Dependencies**: All previous phases

**Features**:
- Entity embeddings (BGE-large)
- Hierarchical Leiden clustering
- UMAP 3D positions
- Community titles/descriptions

---

## Success Metrics

### Phase 1 Metrics

| Metric | Current | Target | Measurement |
|--------|---------|--------|-------------|
| Pronoun entities | ~50 | 0 | Count entities matching pronoun list |
| Generic noun entities | ~26 | 0 | Count entities matching generic list |
| Sentence-like entities | ~500 | <50 | Count entities with verb patterns |
| Tautological entities | ~200 | 0 | Count name == type |
| Entity name consistency | N/A | 90%+ | % entities with canonical names |

### Phase 2 Metrics

| Metric | Current | Target | Measurement |
|--------|---------|--------|-------------|
| Duplicate entities | ~15% | <5% | Fuzzy match count / total |
| Type consistency | ~80% | 95%+ | % entities with normalized types |
| List entities split | 0 | 100% | Count "A, B, and C" patterns processed |

### Phase 3 Metrics

| Metric | Current | Target | Measurement |
|--------|---------|--------|-------------|
| Source profile coverage | 0% | 60%+ | % documents using source-specific profiles |
| Privacy tagging accuracy | N/A | 95%+ | Manual review sample |
| Extraction quality score | N/A | 8/10 | Manual evaluation rubric |

### Phase 4 Metrics

| Metric | Current | Target | Measurement |
|--------|---------|--------|-------------|
| Extraction cost | $X/1000 docs | 50% reduction | API cost tracking |
| Processing throughput | N/A | 10x improvement | Docs/hour batch vs sync |
| Community coverage | 0 | 500+ communities | GraphRAG output |

---

## Risk Mitigation

### Technical Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Filter too aggressive | Medium | Medium | Configurable thresholds, whitelist support |
| Fuzzy match false positives | Medium | High | Type-aware matching, manual review sample |
| Batch API rate limits | Low | Medium | Exponential backoff, quota monitoring |
| Performance degradation | Low | Medium | Profiling, caching, parallel processing |

### Process Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Scope creep | Medium | Medium | Fixed phase deliverables, change control |
| Data quality issues | High | Low | Comprehensive testing on real data |
| Integration conflicts | Medium | Medium | Feature flags, gradual rollout |

---

## Rollback Strategy

### Phase 1 Rollback
- Disable quality filter via config flag
- Original entities preserved in `_raw_entities` field

### Phase 2 Rollback
- Each module can be disabled independently
- Pipeline supports bypass mode (pass-through)

### Phase 3 Rollback
- Fall back to generic extraction profile
- Profile selection is configurable per source

### Phase 4 Rollback
- Batch API can fall back to synchronous
- Clustering is optional enhancement

---

## Dependencies and Prerequisites

### Software Dependencies

```
# Phase 1
- Python 3.10+
- pytest (testing)
- rapidfuzz (optional, for fuzzy matching)

# Phase 2
- No additional dependencies

# Phase 3
- No additional dependencies

# Phase 4
- openai (Batch API)
- sentence-transformers (embeddings)
- leidenalg (clustering)
- umap-learn (dimensionality reduction)
```

### Data Prerequisites

| Phase | Data Required |
|-------|---------------|
| 1 | Sample of current Regen entities (1,000+) |
| 2 | Complete entity export for deduplication testing |
| 3 | Sample documents from each source type |
| 4 | Full document corpus for batch processing |

---

## Gantt Chart (Text Representation)

```
Week:         1    2    3    4    5    6
              ─────────────────────────────
Phase 1       ████████
  1.1 Filter  ████
  1.2 Registry   ███
  1.3 Test          ██

Phase 2            ████████████
  2.1 Framework       ███
  2.2 Migrate            █
  2.3 Ontology           ███
  2.4 Dedup                 ████
  2.5 ListSplit              ███

Phase 3                     ████████████
  3.1 Framework                 ██
  3.2 Discourse                   ████
  3.3 Notion                        ███
  3.4 Medium                          ███
  3.5 Privacy                           ██

Phase 4                              ████████
  4.1 Chunking                          ███
  4.2 Batch                               █████
  4.3 Validator                             ██
  4.4 GraphRAG                               █████
```

---

## Next Immediate Steps

1. **Today**: Implement EntityQualityFilter POC
2. **Tomorrow**: Test on real Regen data, generate metrics
3. **This week**: Create canonical entity registry
4. **Next week**: Begin Phase 2 framework implementation

---

## Appendix: File Structure After Implementation

```
koi-processor/
├── src/
│   ├── knowledge_graph/
│   │   ├── improvements/           # Phase 1 POC (migrate later)
│   │   │   ├── __init__.py
│   │   │   ├── entity_quality_filter.py
│   │   │   └── tests/
│   │   ├── postprocessing/         # Phase 2+
│   │   │   ├── __init__.py
│   │   │   ├── base.py
│   │   │   ├── pipeline.py
│   │   │   ├── entity_quality_filter.py
│   │   │   ├── list_splitter.py
│   │   │   ├── privacy_tagger.py
│   │   │   ├── ontology_normalizer.py
│   │   │   ├── canonical_resolver.py
│   │   │   ├── fuzzy_deduplicator.py
│   │   │   ├── relationship_validator.py
│   │   │   ├── discourse_enricher.py
│   │   │   └── tests/
│   │   └── graph_integration.py    # Existing, enhanced
│   └── extraction/
│       ├── profiles/               # Phase 3
│       │   ├── __init__.py
│       │   ├── base_profile.py
│       │   ├── profile_loader.py
│       │   ├── discourse.py
│       │   ├── notion.py
│       │   └── medium.py
│       ├── chunking/               # Phase 4
│       │   └── ...
│       └── batch/                  # Phase 4
│           └── ...
├── data/
│   ├── canonical_entities.json     # Phase 1
│   ├── ontology/                   # Phase 2
│   │   ├── entity_types.json
│   │   └── predicates.json
│   └── extraction_profiles/        # Phase 3
│       ├── schema.json
│       ├── discourse_v1.json
│       ├── notion_v1.json
│       └── medium_v1.json
└── reports/
    └── extraction_improvement_20251208/
        ├── 01_ARCHITECTURE_COMPARISON.md
        ├── 02_SOURCE_EXTRACTION_PROFILES.md
        ├── 03_POST_PROCESSING_PIPELINE.md
        └── 04_IMPLEMENTATION_ROADMAP.md
```
