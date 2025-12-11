# Knowledge Graph Extraction Method Improvement - Learning from YonEarth Project

## Mission

Investigate how to improve the Regen KOI knowledge graph extraction pipeline by adapting proven techniques from the YonEarth project. Design a new extraction architecture that handles diverse data sources (forums, websites, PDFs, Notion pages, etc.) while maintaining the quality controls demonstrated in the YonEarth book/podcast extraction.

## Context

### Current Regen KOI Extraction System

**Location**: `/Users/darrenzal/projects/RegenAI/koi-processor/`

**Current Statistics**:
- 23,273 entities extracted
- 23,273 relationships
- Data sources: Discourse forums, Telegram, Twitter, Medium, GitHub, Notion
- Entity types: Organization (8,192), Project (6,067), Activity (5,290), Person (3,549)

**Key Files**:
- `src/knowledge_graph/graph_integration.py` - Current graph construction
- `src/core/code_graph_processor.py` - Code-specific graph processing
- `src/core/ontology_summary.md` - Current ontology

**Known Issues** (from quality review):
- Pronouns and generic nouns as entities
- Missing entity quality filters
- Inconsistent relationship predicates
- Limited deduplication
- No fictional/non-fictional entity distinction

### YonEarth Reference System

**Location**: `ssh claudeuser@152.53.37.180:/home/claudeuser/yonearth-gaia-chatbot/`

**Documentation**: `docs/KNOWLEDGE_GRAPH_DATA.md`, `docs/KNOWLEDGE_GRAPH_EXTRACTION_REVIEW.md`

**Statistics**:
- 26,219 entities (after deduplication)
- 39,118 relationships
- 3,708 fictional entities (correctly tagged)
- 573 Level-1 communities + 73 Level-2 communities
- 0% unknown source provenance

**Key Strengths**:
1. **Modular Post-Processing Pipeline**: Entity quality filters, fictional tagging, list splitting, ontology normalization
2. **Content-Specific Extraction Profiles**: Different prompts for technical books, fiction, rhetorical content, podcasts
3. **Robust Entity Resolution**: Canonical alias mapping, fuzzy deduplication (85%+ similarity), fictional override on merge
4. **Parent-Child Chunking**: ~3,000 token parent chunks for context, smaller child chunks for vector indexing
5. **Batch API Usage**: Async processing with OpenAI Batch API (gpt-5.1)
6. **GraphRAG Hierarchy**: Hierarchical Leiden clustering, UMAP embeddings, community detection
7. **Provenance Tracking**: Every entity traces back to source chunk, extraction job metadata

**Key Scripts** (reference these for implementation ideas):
- `scripts/extract_content_batch.py` - Unified batch extraction for all content types
- `scripts/process_batch_results.py` - Post-processing pipeline orchestrator
- `scripts/deduplicate_entities.py` - Entity resolution and merging
- `scripts/build_unified_graph_v2.py` - Unified graph construction
- `scripts/generate_graphrag_hierarchy.py` - Community detection and clustering
- `src/knowledge_graph/validators/entity_quality_filter.py` - Quality filters
- `src/knowledge_graph/postprocessing/` - Modular post-processing modules

## Your Tasks

### 1. Comparative Architecture Analysis

**Compare extraction approaches**:

- [ ] Document current Regen KOI extraction flow (end-to-end)
- [ ] Document YonEarth extraction flow (end-to-end)
- [ ] Create side-by-side comparison table of key differences
- [ ] Identify YonEarth strengths applicable to Regen use case
- [ ] Identify Regen-specific challenges YonEarth doesn't address

**Deliverable**: Architecture comparison report (Markdown) with diagrams

### 2. Source-Specific Extraction Profile Design

**Design extraction profiles for Regen data sources**:

YonEarth has content-type-specific prompts (technical, fiction, rhetorical, podcasts). Regen needs profiles for:

- [ ] **Discourse Forums**: Conversational, question-answer, multi-participant
  - Entity types: Person (participants), Topic, Question, Proposal, Organization
  - Predicates: "asked", "answered", "proposed", "discussed", "agreed_with"
  - Special handling: Quote attribution, thread structure, consensus signals

- [ ] **Telegram/Discord Messages**: Short-form, rapid-fire, contextual
  - Entity types: Person, Event, Announcement, Resource_Link
  - Predicates: "mentioned", "shared", "announced", "replied_to"
  - Special handling: Emoji reactions, link extraction, user mentions

- [ ] **Twitter/Social Media**: Public commentary, links, brief insights
  - Entity types: Person (author), Concept, Resource, Event
  - Predicates: "tweeted_about", "shared_link", "attended"
  - Special handling: Hashtags, @mentions, URL expansion

- [ ] **Medium Articles**: Long-form, structured, authored content
  - Entity types: Person (author), Concept, Project, Case_Study
  - Predicates: "authored", "analyzed", "advocated_for"
  - Special handling: Section headings, citations, author attribution

- [ ] **GitHub Documentation**: Technical, API-focused, code examples
  - Entity types: Function, Class, Module, Configuration, API_Endpoint
  - Predicates: "implements", "depends_on", "configures"
  - Special handling: Code blocks, version info, deprecation notices

- [ ] **Notion Pages**: Structured internal docs, strategy, planning
  - Entity types: Strategy, Initiative, Partner, Milestone
  - Predicates: "proposes", "partners_with", "targets"
  - Special handling: Database properties, hierarchical pages, privacy tags

- [ ] **PDFs/Reports**: Formal, citable, structured
  - Entity types: Report, Finding, Recommendation, Metric
  - Predicates: "reports", "recommends", "measures"
  - Special handling: Page citations, figure captions, author attribution

**Deliverable**:
- Extraction profile specification for each source type (JSON schema)
- Prompt templates for each profile
- Entity type ontology (unified across all profiles)
- Predicate vocabulary (unified across all profiles)

### 3. Modular Post-Processing Pipeline Design

**Adapt YonEarth's post-processing architecture**:

YonEarth uses a modular pipeline with discrete steps. Design similar for Regen:

**Required Modules** (in processing order):

1. **Entity Quality Filter** (blocks low-quality entities)
   - Stop-word entities (pronouns, generic nouns)
   - Numeric-only entities
   - Tautological entities
   - Lowercase single-word PERSONs
   - Sentence fragments

2. **List Splitter** (splits "A, B, and C" into separate entities)
   - Comma-separated entity names
   - Conjunction detection ("and", "or")

3. **Privacy Tagger** (marks internal/sensitive content)
   - Source-based tagging (Notion pages marked private by default)
   - Keyword detection (NDA, confidential, internal)

4. **Fictional Entity Tagger** (marks non-real entities)
   - Source-based tagging (books, hypothetical scenarios)
   - Context-based tagging (metaphors, examples)

5. **Ontology Normalizer** (standardizes types and predicates)
   - Entity type normalization (COMPANY → FORMAL_ORGANIZATION)
   - Predicate normalization (is_a → is, related → related_to)
   - Allowed type/predicate validation

6. **Canonical Entity Resolver** (maps aliases to canonical forms)
   - Known alias mappings (yonearth.org → Y on Earth Community)
   - Acronym expansion (RND → Regen Network Development)
   - URL normalization

7. **Fuzzy Deduplicator** (merges similar entities)
   - 85%+ string similarity threshold
   - Type-aware matching (only merge same types)
   - Fictional override (non-fictional wins on merge)

8. **Relationship Validator** (ensures edge integrity)
   - Target existence check (prune orphaned edges)
   - Self-loop removal
   - Duplicate relationship pruning

**Deliverable**:
- Module specifications (Python class interfaces)
- Processing pipeline DAG (directed acyclic graph)
- Configuration schema (YAML/JSON)
- Example implementation for 1-2 modules (Python)

### 4. Parent-Child Chunking Strategy

**Design chunking approach for Regen sources**:

YonEarth uses ~3,000 token parent chunks for extraction context. Adapt for Regen:

**Forum Posts**:
- Parent chunk: Full thread (up to 6,000 tokens)
- Child chunks: Individual replies (600 tokens, 100 overlap)
- Metadata: Thread ID, author, timestamp, category

**Chat Messages**:
- Parent chunk: Conversation window (30 messages or 3,000 tokens)
- Child chunks: Message groups (5 messages)
- Metadata: Channel, participants, timestamp range

**Articles/Docs**:
- Parent chunk: Section (3,000 tokens)
- Child chunks: Paragraphs (600 tokens, 100 overlap)
- Metadata: Article title, author, section heading, publish date

**PDFs**:
- Parent chunk: Page or logical section (3,000 tokens)
- Child chunks: Paragraphs/subsections (600 tokens, 100 overlap)
- Metadata: Filename, page number, section title

**Deliverable**:
- Chunking specifications per source type
- Metadata extraction rules
- Chunking implementation plan (leverage existing libraries like `langchain`)

### 5. Batch Extraction Pipeline Design

**Design async batch processing system**:

YonEarth uses OpenAI Batch API for cost-effective async processing. Design similar for Regen:

**Architecture Components**:
- [ ] Batch job submission (group chunks into batches)
- [ ] Status polling (check completion)
- [ ] Result download and validation
- [ ] Retry logic for failed chunks
- [ ] Progress tracking and resumption

**Deliverable**:
- Batch processing architecture diagram
- API integration specification (OpenAI Batch API)
- Job management schema (tracking batches, chunks, results)
- Error handling and retry logic specification

### 6. Entity Resolution & Deduplication Strategy

**Design deduplication system**:

YonEarth uses canonical alias mapping + fuzzy matching. Design for Regen:

**Canonical Alias Mappings** (curated list):
- regen.network → Regen Network
- RND → Regen Network Development
- ecocredit → Regen Ecocredit Module
- (Build comprehensive list from known Regen entities)

**Fuzzy Matching Rules**:
- Similarity threshold: 85%+
- Type-aware matching (only merge same entity types)
- Confidence-weighted merging (higher confidence entities are canonical)
- Fictional override (non-fictional always wins)

**Merge Strategy**:
- Preserve provenance (merge source lists)
- Union relationship sets
- Highest confidence value wins for attributes

**Deliverable**:
- Canonical alias mapping file (JSON)
- Deduplication algorithm specification
- Merge strategy rules
- Example entity merge cases with before/after

### 7. GraphRAG Hierarchy Integration

**Design community detection and clustering**:

YonEarth generates hierarchical communities for visualization and navigation. Design for Regen:

**Clustering Pipeline**:
- [ ] Generate entity embeddings (BGE-large or similar)
- [ ] Apply hierarchical Leiden algorithm
- [ ] Generate community titles and descriptions
- [ ] Compute UMAP 3D positions for visualization
- [ ] Build cluster registry with metadata

**Deliverable**:
- GraphRAG pipeline specification
- Embedding model selection (BGE vs alternatives)
- Clustering parameter recommendations
- Visualization data schema

### 8. Unified Graph Schema Design

**Design consolidated graph schema**:

Merge current Regen schema with YonEarth best practices:

**Entity Schema**:
```json
{
  "id": "uuid",
  "name": "canonical entity name",
  "type": "ONTOLOGY_TYPE",
  "aliases": ["alternative names"],
  "description": "extracted or generated description",
  "confidence": 0.0-1.0,
  "is_fictional": true/false,
  "is_private": true/false,
  "sources": [
    {
      "source_id": "chunk ID",
      "source_type": "discourse|telegram|medium|...",
      "source_url": "link to original",
      "extracted_at": "ISO timestamp",
      "extraction_job_id": "batch job ID"
    }
  ],
  "relationships": [
    {
      "predicate": "relationship type",
      "target_id": "entity UUID",
      "confidence": 0.0-1.0,
      "sources": [...]
    }
  ],
  "embeddings": {
    "model": "bge-large-en-v1.5",
    "vector": [768-dim array]
  }
}
```

**Relationship Schema**:
```json
{
  "id": "uuid",
  "subject_id": "entity UUID",
  "predicate": "relationship type",
  "object_id": "entity UUID",
  "confidence": 0.0-1.0,
  "sources": [...],
  "extracted_at": "ISO timestamp"
}
```

**Deliverable**:
- Complete graph schema specification (JSON Schema)
- Entity type ontology (allowed types)
- Predicate vocabulary (allowed relationship types)
- Migration plan from current schema to new schema

### 9. Implementation Roadmap

**Prioritize improvements and plan rollout**:

- [ ] Rank all proposed improvements by impact and effort
- [ ] Define implementation phases (Phase 1: critical fixes, Phase 2: enhancements, etc.)
- [ ] Identify dependencies between improvements
- [ ] Estimate effort for each phase (hours/days)
- [ ] Define success metrics for each phase
- [ ] Plan rollback/fallback strategies

**Deliverable**:
- Prioritized improvement backlog (Markdown table)
- Phased implementation plan (Gantt chart or similar)
- Success metrics and KPIs
- Risk mitigation strategies

### 10. Proof-of-Concept Implementation

**Implement one module end-to-end**:

Choose ONE component to fully implement as a proof-of-concept:

**Recommended**: Entity Quality Filter (highest impact, easiest to test)

- [ ] Implement `EntityQualityFilter` class (Python)
- [ ] Add unit tests (pytest)
- [ ] Run on sample of current Regen entities
- [ ] Generate before/after quality metrics
- [ ] Document findings and recommendations

**Alternative**: Deduplication module or Batch extraction pipeline

**Deliverable**:
- Working Python code
- Unit tests with >80% coverage
- Sample data test results
- Integration guide (how to plug into existing pipeline)

## Deliverables Summary

Produce the following artifacts:

1. **Architecture Comparison Report** (Markdown, 5-10 pages)
   - Current Regen extraction flow
   - YonEarth extraction flow
   - Side-by-side comparison
   - Key learnings and applicability

2. **Source-Specific Extraction Profiles** (JSON + Markdown)
   - 7 extraction profiles for Regen data sources
   - Unified ontology specification
   - Prompt templates

3. **Modular Post-Processing Pipeline Design** (Markdown + Python interfaces)
   - 8 module specifications
   - Processing pipeline DAG
   - Configuration schema

4. **Chunking Strategy Specification** (Markdown)
   - Chunking rules per source type
   - Metadata extraction specifications

5. **Batch Processing System Design** (Architecture diagram + spec)
   - Job management schema
   - API integration plan
   - Error handling and retry logic

6. **Deduplication Strategy** (JSON + Markdown)
   - Canonical alias mappings
   - Fuzzy matching algorithm
   - Merge strategy rules

7. **GraphRAG Integration Plan** (Markdown)
   - Clustering pipeline
   - Embedding model selection
   - Visualization data schema

8. **Unified Graph Schema** (JSON Schema + Migration plan)
   - Entity and relationship schemas
   - Ontology and predicate vocabulary
   - Migration strategy from current to new schema

9. **Implementation Roadmap** (Markdown + Gantt chart)
   - Prioritized backlog
   - Phased rollout plan
   - Success metrics and KPIs

10. **Proof-of-Concept Code** (Python + tests)
    - Working implementation of 1 module
    - Test results on real data
    - Integration documentation

## Technical Requirements

**Environment**:
- Python 3.10+
- Access to both servers (SSH key authentication configured, no passwords needed):
  - Regen: `darren@202.61.196.119:/opt/projects/`
  - YonEarth: `claudeuser@152.53.37.180:/home/claudeuser/yonearth-gaia-chatbot/`
- Libraries: pandas, networkx, scipy, openai, langchain, apache-age-python

**YonEarth Reference Files** (read-only access):
```
ssh claudeuser@152.53.37.180
cd /home/claudeuser/yonearth-gaia-chatbot

# Key files to review:
docs/KNOWLEDGE_GRAPH_DATA.md
docs/KNOWLEDGE_GRAPH_EXTRACTION_REVIEW.md
scripts/extract_content_batch.py
scripts/process_batch_results.py
scripts/deduplicate_entities.py
src/knowledge_graph/validators/entity_quality_filter.py
src/knowledge_graph/postprocessing/
data/knowledge_graph_unified/unified_v2.json (sample)
```

**Output Location**:
- Save all reports to `/Users/darrenzal/projects/RegenAI/koi-processor/reports/extraction_improvement_YYYYMMDD/`
- Save POC code to `/Users/darrenzal/projects/RegenAI/koi-processor/src/knowledge_graph/improvements/`

## Success Criteria

- [ ] All 10 tasks completed
- [ ] All deliverables produced and saved
- [ ] Architecture comparison clearly explains YonEarth advantages
- [ ] Extraction profiles are specific and actionable
- [ ] Implementation roadmap is realistic and prioritized
- [ ] POC code runs successfully on real Regen data
- [ ] Documentation is clear and comprehensive

## Timeline

Estimated: 8-12 hours for full investigation + POC

## Notes

- **Read-only access** to YonEarth server - do not modify their system
- Focus on **adaptability** - YonEarth solved books/podcasts, Regen needs forums/chats/docs
- Prioritize **high-impact, low-effort** improvements in roadmap
- POC should demonstrate measurable quality improvement on real data
- Consider **cost** (Batch API is 50% cheaper than synchronous API)
- Consider **scale** (Regen has more diverse sources than YonEarth)

## Questions for Clarification

Before starting, confirm:
1. Which module should be implemented as POC (Entity Quality Filter recommended)?
2. Are there specific data sources to prioritize (e.g., Discourse forums first)?
3. Should the roadmap target a specific launch date for new extraction pipeline?
4. Are there existing extraction scripts in koi-processor that must be preserved/extended?
5. What is the budget/timeline for implementing the full roadmap?
