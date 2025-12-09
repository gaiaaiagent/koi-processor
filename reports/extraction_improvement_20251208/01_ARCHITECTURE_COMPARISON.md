# Architecture Comparison Report
## Regen KOI vs YonEarth Knowledge Graph Extraction Systems

**Date**: 2025-12-08
**Author**: Claude Code Investigation
**Purpose**: Identify architectural patterns from YonEarth that can improve Regen KOI extraction quality

---

## Executive Summary

The YonEarth knowledge graph extraction system demonstrates significantly more mature data quality practices than the current Regen KOI system. Key differentiators include:

1. **Modular post-processing pipeline** with 20+ specialized modules
2. **Entity quality filtering** that blocks pronouns, generic nouns, and sentence fragments
3. **Content-specific extraction profiles** tuned for different source types
4. **Robust deduplication** with canonical alias mapping and fuzzy matching
5. **Batch API processing** for cost-effective large-scale extraction
6. **Fictional entity tagging** to separate narrative content from facts

The Regen KOI system has a solid RDF foundation but lacks the quality control mechanisms that make YonEarth's graph significantly cleaner.

---

## System Overview

### Regen KOI Current State

**Location**: `/Users/darrenzal/projects/RegenAI/koi-processor/`

**Architecture Pattern**: RDF-based knowledge graph with direct integration

```
┌─────────────────────────────────────────────────────────────┐
│                    Data Sources                              │
│  Discourse │ Twitter │ Medium │ GitHub │ Notion │ Telegram  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              LLM Extraction (OpenAI/Anthropic)              │
│        src/extraction/llm_extractor.py                      │
│        src/extraction/openai_extractor.py                   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│           Direct RDF Integration (No Post-Processing)       │
│        src/knowledge_graph/graph_integration.py             │
│        - Entity URI generation                              │
│        - Basic type mapping                                 │
│        - Source provenance linking                          │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    RDF Triple Store                         │
│        Memory / PostgreSQL / SPARQL Endpoint                │
└─────────────────────────────────────────────────────────────┘
```

**Current Statistics**:
- 23,273 entities extracted
- 23,273 relationships
- Entity types: Organization (8,192), Project (6,067), Activity (5,290), Person (3,549)

**Known Quality Issues**:
- 3,690+ entities flagged for quality issues
- 26 instances of generic nouns ("User", "farmers", "company")
- Pronouns appearing as entities ("we", "they")
- Sentence fragments as entity names
- No deduplication (many variants of same entity)
- Inconsistent relationship predicates

### YonEarth System

**Location**: `claudeuser@152.53.37.180:/home/claudeuser/yonearth-gaia-chatbot/`

**Architecture Pattern**: Multi-phase batch extraction with modular post-processing

```
┌─────────────────────────────────────────────────────────────┐
│                    Content Sources                          │
│      172 Podcast Episodes  │  4 Books (Technical, Fiction)  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│   Phase 1: Parent-Child Chunking                            │
│   scripts/extract_content_batch.py                          │
│   - ~3,000 token parent chunks (context)                    │
│   - ~600 token child chunks (vector indexing)               │
│   - Content-specific profiles (technical/fiction/rhetorical)│
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│   Phase 2: Batch API Extraction (OpenAI gpt-4o-mini)        │
│   - Async batch submission                                  │
│   - Status polling                                          │
│   - Result download                                         │
│   - Failed chunk retry                                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│   Phase 3: Post-Processing Pipeline                         │
│   scripts/process_batch_results.py                          │
│   ┌─────────────────────────────────────────────────────┐   │
│   │  Universal Modules (20+ modules):                    │   │
│   │  - EntityQualityFilter (pronouns, generics)          │   │
│   │  - ListSplitter (POS-aware list detection)           │   │
│   │  - PronounResolver (anaphoric resolution)            │   │
│   │  - PredicateNormalizer                               │   │
│   │  - VagueEntityBlocker                                │   │
│   │  - ContextEnricher                                   │   │
│   │  - Deduplicator                                      │   │
│   └─────────────────────────────────────────────────────┘   │
│   ┌─────────────────────────────────────────────────────┐   │
│   │  Content-Specific Modules:                          │   │
│   │  - FictionalCharacterTagger                          │   │
│   │  - PraiseQuoteDetector                               │   │
│   │  - BibliographicCitationParser                       │   │
│   └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│   Phase 4: Entity Resolution & Deduplication                │
│   scripts/deduplicate_entities.py                           │
│   - Canonical alias mapping (JSON registry)                 │
│   - 85%+ fuzzy string matching                              │
│   - Type-aware merging                                      │
│   - Fictional override on merge                             │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│   Phase 5: Unified Graph Build                              │
│   scripts/build_unified_graph_v2.py                         │
│   - Edge validation (target existence)                      │
│   - Self-loop removal                                       │
│   - Duplicate relationship pruning                          │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│   Phase 6: GraphRAG Hierarchy Generation                    │
│   scripts/generate_graphrag_hierarchy.py                    │
│   - Entity embeddings (BGE-large)                           │
│   - Hierarchical Leiden clustering                          │
│   - UMAP 3D positions                                       │
│   - Community titles/descriptions                           │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   unified_v2.json (24 MB)                   │
│   - 26,219 deduplicated entities                            │
│   - 39,118 relationships                                    │
│   - 3,708 fictional entities (correctly tagged)             │
│   - 573 Level-1 communities                                 │
│   - 0% unknown source provenance                            │
└─────────────────────────────────────────────────────────────┘
```

---

## Side-by-Side Comparison

| Aspect | Regen KOI | YonEarth | Gap Analysis |
|--------|-----------|----------|--------------|
| **Entity Quality Filtering** | None | 7+ filters (pronouns, numerics, tautological, generics, sentence-like) | CRITICAL: Needs immediate implementation |
| **Post-Processing Pipeline** | None | 20+ modular modules with priority ordering | HIGH: Core architecture missing |
| **Content-Specific Extraction** | Single generic prompt | 4 profiles (episode, technical, fiction, rhetorical) | HIGH: Needs profiles for Discourse, Notion, Medium |
| **Chunking Strategy** | Not documented | Parent-child (~3K/600 tokens) with overlap | MEDIUM: Improve context preservation |
| **Batch Processing** | Synchronous | OpenAI Batch API (50% cheaper) | MEDIUM: Cost optimization |
| **Deduplication** | None | Canonical + fuzzy (85%+ threshold) | HIGH: Many duplicate entities |
| **Fictional/Privacy Tagging** | None | Fictional registry + source-based tagging | MEDIUM: Needed for Notion internal docs |
| **Ontology Normalization** | Ad-hoc | Validated type/predicate lists | MEDIUM: Inconsistent types |
| **Provenance Tracking** | Basic source link | Full chunk-level attribution, job metadata | LOW: Current approach adequate |
| **GraphRAG Clustering** | None | Hierarchical Leiden + UMAP | LOW: Future enhancement |
| **Entity Resolution** | URI-based dedup only | Alias registry + fuzzy matching | HIGH: Variants not merged |

---

## Key YonEarth Strengths Applicable to Regen

### 1. Entity Quality Filter (CRITICAL)

**YonEarth Implementation**: `src/knowledge_graph/validators/entity_quality_filter.py`

```python
class EntityQualityFilter:
    STOP_WORD_ENTITIES = {
        'we', 'she', 'he', 'they', 'it', 'i', 'you',
        'people', 'person', 'individual', 'individuals',
        'mom', 'dad', 'mother', 'father', 'friend', 'friends',
        'farmer', 'teacher', 'scientist', 'activist',
    }

    def filter_entity(self, entity: Dict) -> Tuple[bool, str]:
        # 7 sequential filters
        # Returns (passes, rejection_reason)
```

**Applicability to Regen**: Direct port with Regen-specific additions:
- Add "user", "member", "participant" for forum context
- Add domain-specific generic terms ("project", "initiative" when lowercase)

### 2. Modular Post-Processing Pipeline (HIGH)

**YonEarth Implementation**: `src/knowledge_graph/postprocessing/`

```
postprocessing/
├── base.py                    # Abstract module interface
├── universal/                 # Works for all content types
│   ├── list_splitter.py      # POS-aware list splitting
│   ├── pronoun_resolver.py   # Anaphoric resolution
│   ├── predicate_normalizer.py
│   └── vague_entity_blocker.py
├── content_specific/
│   └── books/
│       ├── fictional_character_tagger.py
│       └── praise_quote_detector.py
└── pipelines/
    ├── book_pipeline.py      # Pre-configured for books
    └── custom_pipeline.py    # Builder pattern
```

**Key Design Pattern**:
- Each module has `priority` (execution order)
- Modules declare `dependencies` on other modules
- `ProcessingContext` provides shared state
- Statistics tracking per module

**Applicability to Regen**: Adopt same architecture with Regen-specific modules:
- `DiscourseThreadResolver` - Handle quote attribution, thread structure
- `NotionPrivacyTagger` - Tag internal/sensitive content
- `TelegramMentionResolver` - Expand @mentions and reactions

### 3. Content-Specific Extraction Profiles (HIGH)

**YonEarth Implementation**:

```python
@dataclass
class ContentProfile:
    content_type: str           # "episode", "technical", "fiction"
    reality_tag: str            # "factual", "fictional", "conceptual"
    system_prompt_focus: str    # Additional LLM instructions
    chunking_strategy: str      # "speaker", "hierarchical", "narrative"

BOOK_PROFILES = {
    "Soil Stewardship Handbook": ContentProfile(
        content_type="hybrid_technical",
        system_prompt_focus="Extract TWO layers: rhetorical + technical..."
    ),
    "VIRIDITAS": ContentProfile(
        content_type="fiction",
        system_prompt_focus="This is NARRATIVE FICTION. Mark ALL entities as fictional..."
    ),
}
```

**Applicability to Regen**: Define profiles for:
- Discourse forums (Q&A, proposals, consensus signals)
- Notion pages (strategy, internal docs, partnerships)
- Medium articles (long-form, authored content)
- GitHub docs (technical, API-focused)
- Telegram/Discord (rapid-fire, contextual)

### 4. Deduplication with Canonical Aliases (HIGH)

**YonEarth Implementation**: `scripts/deduplicate_entities.py`

```python
class EntityDeduplicator:
    def _normalize_key(self, name: str, entity_type: str) -> Tuple[str, str]:
        # Lowercase, strip, type-aware
        return (name.lower().strip(), entity_type.upper().strip())

    def _merge_group(self, group: List[Dict]) -> Dict:
        # Union aliases, longest description wins
        # Fictional override: non-fictional wins on merge
```

**Canonical Alias Registry** (`data/canonical_entities.json`):
```json
{
    "organizations": {
        "y-on-earth": {
            "canonical_name": "Y on Earth Community",
            "aliases": ["Y on Earth", "YonEarth", "yonearth.org"]
        }
    }
}
```

**Applicability to Regen**: Create `regen_canonical_entities.json`:
- "regen.network" → "Regen Network"
- "RND" → "Regen Network Development"
- "ecocredit" → "Regen Ecocredit Module"
- Multiple forum username variants

### 5. Parent-Child Chunking (MEDIUM)

**YonEarth Implementation**:
- **Parent chunks**: ~3,000 tokens (context for extraction)
- **Child chunks**: ~600 tokens with 100-token overlap (vector indexing)
- Content-type specific chunking (speaker-based for podcasts, hierarchical for books)

**Applicability to Regen**:
- Forum posts: Parent = full thread, Child = individual replies
- Articles: Parent = section, Child = paragraphs
- Chat messages: Parent = conversation window (30 messages), Child = message groups

---

## Regen-Specific Challenges YonEarth Doesn't Address

### 1. Multi-Source Entity Correlation

**Problem**: Same entity mentioned across Discourse, Twitter, Medium with different names
**YonEarth**: Single content type (podcasts/books) - no cross-source correlation
**Solution**: Cross-source entity linking using:
- Email/username as correlation key
- Organization name fuzzy matching across sources
- Temporal co-occurrence analysis

### 2. Real-Time vs Batch Processing

**Problem**: Regen may need near-real-time processing for forum monitoring
**YonEarth**: Pure batch processing (24-hour cycles)
**Solution**: Hybrid architecture
- Batch for historical backfill
- Streaming for new content with deferred quality passes

### 3. Privacy and Permissions

**Problem**: Notion internal docs, private forum categories
**YonEarth**: All content public (podcasts)
**Solution**: Privacy tagging module with:
- Source-based default privacy (Notion = private by default)
- Keyword detection (NDA, confidential, internal)
- Permission inheritance from parent docs

### 4. Governance and Proposal Tracking

**Problem**: Track proposal lifecycle (draft → discussion → vote → implementation)
**YonEarth**: No governance-specific patterns
**Solution**: Custom extraction profile for governance:
- Entity types: Proposal, Vote, Amendment
- Predicates: proposed, amended, voted_for, implemented
- Temporal status tracking

### 5. On-Chain Data Integration

**Problem**: Link forum discussions to on-chain events (credit batches, registry updates)
**YonEarth**: No blockchain integration
**Solution**: Separate code graph processor (existing) + entity linking:
- Forum mentions of credit batch IDs
- Registry methodology discussions linked to on-chain methodologies

---

## Recommended Architecture for Regen KOI

```
┌─────────────────────────────────────────────────────────────┐
│                    Data Sources (Existing)                  │
│  Discourse │ Notion │ Medium │ GitHub │ Telegram │ Twitter │
└─────────────────────────────────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         │                    │                    │
         ▼                    ▼                    ▼
┌────────────────┐  ┌────────────────┐  ┌────────────────┐
│ Discourse      │  │ Notion         │  │ Article        │
│ Profile        │  │ Profile        │  │ Profile        │
│ (Q&A, threads) │  │ (Strategy, DB) │  │ (Long-form)    │
└────────────────┘  └────────────────┘  └────────────────┘
         │                    │                    │
         └────────────────────┼────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│   NEW: Parent-Child Chunker                                 │
│   - Source-specific chunking strategies                     │
│   - Metadata extraction rules                               │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│   LLM Extraction (Existing, Enhanced)                       │
│   - Profile-specific prompts                                │
│   - Batch API integration (NEW)                             │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│   NEW: Post-Processing Pipeline                             │
│   ┌─────────────────────────────────────────────────────┐   │
│   │  1. EntityQualityFilter (block pronouns, generics)   │   │
│   │  2. ListSplitter (handle comma-separated entities)   │   │
│   │  3. PrivacyTagger (mark internal/sensitive content)  │   │
│   │  4. OntologyNormalizer (standardize types)           │   │
│   │  5. CanonicalResolver (alias mapping)                │   │
│   │  6. FuzzyDeduplicator (85%+ similarity merge)        │   │
│   │  7. RelationshipValidator (prune orphan edges)       │   │
│   │  8. DiscourseEnricher (thread structure, quotes)     │   │
│   └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│   RDF Graph Integration (Existing, Enhanced)                │
│   - Add quality metadata to triples                         │
│   - Privacy-aware export                                    │
└─────────────────────────────────────────────────────────────┘
```

---

## Implementation Priorities

### Phase 1: Critical Quality Fixes (Week 1-2)
1. **Entity Quality Filter** - Block pronouns, generics, sentence fragments
2. **Canonical Entity Registry** - Create Regen-specific alias mappings
3. **Basic Deduplication** - Fuzzy matching at 85% threshold

### Phase 2: Pipeline Architecture (Week 2-3)
4. **Modular Post-Processing Framework** - Port YonEarth base architecture
5. **List Splitter** - Handle comma-separated entities
6. **Ontology Normalizer** - Standardize entity types and predicates

### Phase 3: Source-Specific Profiles (Week 3-4)
7. **Discourse Extraction Profile** - Q&A, proposals, thread structure
8. **Notion Extraction Profile** - Strategy docs, partnerships
9. **Medium Extraction Profile** - Long-form articles

### Phase 4: Advanced Features (Week 4-6)
10. **Privacy Tagger** - Internal doc handling
11. **Batch API Integration** - Cost optimization
12. **GraphRAG Hierarchy** - Community detection for visualization

---

## Appendix: Key YonEarth Files Reference

| File | Purpose | Adaptation Notes |
|------|---------|------------------|
| `src/knowledge_graph/validators/entity_quality_filter.py` | Entity quality filtering | Direct port + Regen additions |
| `src/knowledge_graph/postprocessing/base.py` | Module base class | Port as-is |
| `src/knowledge_graph/postprocessing/universal/list_splitter.py` | POS-aware list splitting | Port with simplified config |
| `scripts/deduplicate_entities.py` | Entity deduplication | Adapt for RDF integration |
| `scripts/extract_content_batch.py` | Batch extraction orchestrator | Reference for batch API setup |
| `data/canonical_entities.json` | Alias registry | Create Regen-specific version |
| `data/fictional_characters.json` | Fictional entity registry | Adapt for privacy tagging |

---

## Conclusion

The YonEarth system provides a proven blueprint for high-quality knowledge graph extraction. The key insight is that **raw LLM extraction is insufficient** - significant quality gains come from:

1. **Pre-extraction**: Content-specific prompts and chunking
2. **Post-extraction**: Multi-stage quality filtering and normalization
3. **Resolution**: Deduplication with domain-specific alias mapping

Regen KOI should adopt this multi-phase architecture while adapting modules for its unique challenges (multi-source correlation, privacy, governance tracking).

The recommended starting point is the **Entity Quality Filter POC** - this single component will demonstrate immediate, measurable improvement on real data.
