# PROMPT 20: Comprehensive Deduplication & Quality Investigation

**Date**: 2025-12-09
**Status**: URGENT - BLOCKING EXTRACTION
**Priority**: CRITICAL
**Estimated Time**: 4-6 hours (investigation + planning)

---

## Critical Directive

**DO NOT implement anything yet.** First, we need a **complete picture** of:

1. **Everything** yonearth-gaia-chatbot does for dedup/quality/resolution
2. **Why** they made each choice
3. **What gaps** exist even in their approach
4. **What's missing** that should be added

**Only after full investigation** → Create comprehensive implementation plan

---

## Context

**Problem**: We discovered cross-document deduplication gap, but pronoun resolution is just ONE example of what might be missing.

**User's concern**: "We should investigate the yonearth repo to look at everything they did, and then even after that, think about what might be missing."

**This is the right approach.** We need systematic investigation, not piecemeal porting.

---

## Part 1: Comprehensive yonearth-gaia-chatbot Investigation

**Server**: `ssh claudeuser@152.53.37.180`
**Project**: `cd ~/yonearth-gaia-chatbot`

### Step 1: Map Entire Quality/Dedup Architecture (1 hour)

**Goal**: Understand EVERYTHING they do, not just what we've seen so far.

#### A. Find ALL Quality/Dedup/Resolution Modules

```bash
cd ~/yonearth-gaia-chatbot

# Find all Python files related to quality/dedup/resolution
find . -type f -name "*.py" | xargs grep -l -E "(dedup|resolution|quality|clean|filter|normalize|resolve|merge)" | sort

# Map directory structure
tree src/knowledge_graph/postprocessing/universal/ -L 3
tree src/knowledge_graph/graph/ -L 2
tree scripts/ -L 1 | grep -E "(build|clean|dedup|quality)"

# List ALL modules in postprocessing
ls -la src/knowledge_graph/postprocessing/universal/
ls -la src/knowledge_graph/postprocessing/
```

#### B. Read EVERY Module (Don't Skip!)

For each module found, read and document:

**Universal postprocessing modules** (`src/knowledge_graph/postprocessing/universal/`):
```bash
# List all modules
ls src/knowledge_graph/postprocessing/universal/

# For EACH module, document:
# - Purpose
# - Input/Output
# - Configuration options
# - Dependencies
# - Integration points
```

**Expected modules** (from investigation, but there may be MORE):
- [ ] `entity_deduplicator.py` - Read and document
- [ ] `pronoun_resolver.py` - Read and document
- [ ] `semantic_deduplicator.py` - Read and document
- [ ] ANY OTHER .py files - Read and document

**Graph building** (`src/knowledge_graph/graph/`):
- [ ] `graph_builder.py` - Read fuzzy matching logic
- [ ] Any other graph quality modules

**Scripts** (`scripts/`):
- [ ] `build_unified_graph_hybrid.py` - Full pipeline orchestration
- [ ] Any validation/quality check scripts

#### C. Document Each Module's Functionality

Create a structured analysis for EACH module:

**Template**:
```markdown
### Module: [name]

**Location**: [path]

**Purpose**: [1-2 sentence description]

**What it does**:
- Input: [data structure]
- Output: [data structure]
- Algorithm: [brief description]
- Key features: [list]

**Configuration**:
- Parameters: [list with defaults]
- Tunables: [what can be adjusted]

**Dependencies**:
- Libraries: [list]
- Other modules: [list]

**Examples** (from code):
```python
[paste key code snippets]
```

**Edge cases handled**:
- [list specific edge cases]

**Performance considerations**:
- Complexity: [O(n), O(n²), etc.]
- Bottlenecks: [any known issues]

**Gaps/Limitations**:
- [what this module doesn't handle]
```

### Step 2: Understand Pipeline Architecture (1 hour)

#### A. Find Pipeline Configuration

```bash
# Look for pipeline definitions
find . -name "*.py" -o -name "*.json" -o -name "*.yaml" | xargs grep -l "pipeline\|workflow\|stages"

# Check main entry points
grep -r "def main\|if __name__" scripts/*.py | head -20

# Look for orchestration code
find . -name "*pipeline*.py" -o -name "*orchestrat*.py" -o -name "*workflow*.py"
```

#### B. Document Processing Flow

Map out the COMPLETE flow:

```
Raw Text
   ↓
[Step 1: ?]
   ↓
[Step 2: ?]
   ↓
...
   ↓
Clean Knowledge Graph
```

**Questions to answer**:
1. What order do modules run?
2. Are there multiple passes?
3. What happens to blocked entities?
4. How are errors handled?
5. Is there rollback/retry logic?

#### C. Check for Pre-Processing

```bash
# Look for text cleaning before extraction
grep -r "clean.*text\|normalize.*text\|preprocess" src/ --include="*.py"

# Look for sentence splitting, tokenization
grep -r "sentence\|tokenize\|split.*text" src/ --include="*.py"
```

### Step 3: Analyze Quality Metrics & Validation (30 minutes)

#### A. Find Validation Code

```bash
# Look for quality metrics
grep -r "quality\|metric\|score\|validate" scripts/ tests/ --include="*.py"

# Look for test suites
find tests/ -name "test_*.py" | xargs wc -l | sort -n

# Check for benchmarks
find . -name "*benchmark*" -o -name "*eval*" -o -name "*metric*"
```

#### B. Document Quality Checks

What quality metrics do they track?
- Deduplication rate?
- Entity precision/recall?
- Relationship accuracy?
- Coverage metrics?

### Step 4: Study Edge Cases & Error Handling (30 minutes)

#### A. Look for Edge Case Handling

```bash
# Search for edge case comments
grep -r "edge case\|corner case\|special case" src/ --include="*.py" -A 3

# Look for error handling
grep -r "try:\|except\|raise\|assert" src/knowledge_graph/ --include="*.py" | wc -l

# Find validation checks
grep -r "if not\|assert\|validate" src/knowledge_graph/ --include="*.py" | head -50
```

#### B. Document Known Issues

```bash
# Check for TODOs, FIXMEs
grep -r "TODO\|FIXME\|XXX\|HACK" src/ --include="*.py"

# Look at issue tracker if available
find . -name "ISSUES*" -o -name "BUGS*" -o -name "KNOWN*"
```

### Step 5: Extract Key Learnings (30 minutes)

Create comprehensive summary:

**Document**: `YONEARTH_COMPLETE_ANALYSIS.md`

```markdown
# Complete yonearth-gaia-chatbot Quality Pipeline Analysis

## Executive Summary
[3-5 bullet points of most important findings]

## All Modules Discovered
[Complete list with brief descriptions]

## Pipeline Architecture
[Complete flow diagram with ALL steps]

## Key Algorithms
[Detailed descriptions of core algorithms]

## Configuration & Tunables
[All configurable parameters across all modules]

## Edge Cases Handled
[Complete list of edge cases they address]

## Dependencies & Infrastructure
[All external libraries, tools, services]

## Performance Characteristics
[Speed, memory, scalability considerations]

## Known Limitations
[What they don't handle]

## Best Practices Observed
[Patterns worth replicating]
```

---

## Part 2: Gap Analysis Beyond yonearth (1 hour)

**After understanding yonearth completely**, think critically about gaps:

### A. Missing Entity Types

**Question**: Does yonearth handle ALL entity types relevant to Regen?

Check for:
- [ ] Scientific concepts (methodologies, metrics)
- [ ] Blockchain entities (validators, tokens, modules)
- [ ] Geographic entities (ecoregions, locations)
- [ ] Standards/protocols (Verra, Gold Standard)
- [ ] Data types (datasets, schemas)

### B. Domain-Specific Deduplication

**Question**: Are there Regen-specific aliases yonearth wouldn't know?

Examples:
- "Regen Network" vs "RND" vs "Regen Network Development, PBC"
- "Ecocredit" vs "Eco-credit" vs "ecocredit module"
- "Gregory Landua" vs "Greg" vs "Gregory_RND"
- "$REGEN" vs "REGEN token" vs "Regen coin"

### C. Context-Aware Resolution

**Question**: Do we need context to disambiguate?

Examples:
- "Validator" (blockchain validator vs data validator)
- "Module" (software module vs ecocredit module)
- "Network" (Regen Network vs Cosmos Network vs neural network)

### D. Temporal/Version Handling

**Question**: How to handle entities that change over time?

Examples:
- "Regen Ledger v1" vs "Regen Ledger v4"
- "Cosmos SDK 0.45" vs "Cosmos SDK 0.47"
- Rebranding: "Regen Network Development" → "Regen Foundation"

### E. Relationship Quality

**Question**: Beyond entity dedup, what about relationship dedup/quality?

Check for:
- Duplicate relationships (same triple, different phrasings)
- Conflicting relationships ("founded in 2017" vs "founded in 2020")
- Temporal relationships (past vs present)
- Confidence scoring for relationships

### F. Multi-Language Support

**Question**: Any non-English content in Regen corpus?

Check:
- Spanish documentation?
- Chinese community posts?
- Other languages?

### G. Abbreviation/Acronym Handling

**Question**: Comprehensive acronym resolution?

Examples:
- "IBC" → "Inter-Blockchain Communication"
- "CRU" → "Carbon Removal Unit"
- "NCT" → "Nature Carbon Ton"

### H. Possessive vs Entity

**Question**: Handle possessives correctly?

Examples:
- "Gregory's company" → extract "Gregory" and "company", resolve possessive
- "Regen's token" → "Regen Network" + "REGEN token"

### I. Compound Entities

**Question**: Split or keep together?

Examples:
- "Cosmos SDK" - keep as one entity or split to "Cosmos" + "SDK"?
- "Regen Registry" - single entity or "Regen Network" + "Registry"?

### J. Type Inference

**Question**: Can we infer types from context?

Example:
- "founded Regen" → Regen is likely ORGANIZATION
- "spoke with Gregory" → Gregory is likely PERSON

---

## Part 3: Literature Review (Optional, 30 minutes)

**Search for academic/industry best practices**:

```bash
# Topics to research:
# - Entity deduplication in knowledge graphs
# - Coreference resolution state-of-the-art
# - Named entity disambiguation
# - Knowledge graph quality metrics
```

**Key papers/resources**:
- DeepMind's entity deduplication approaches?
- DBpedia entity disambiguation?
- Wikidata reconciliation?
- Google Knowledge Graph best practices?

---

## Part 4: Synthesize Complete Plan (1 hour)

**After Parts 1-3**, create comprehensive plan:

**Document**: `COMPLETE_DEDUP_IMPLEMENTATION_PLAN.md`

### Structure:

```markdown
# Complete Deduplication & Quality Implementation Plan

## Executive Summary
[What we're building, why, expected impact]

## Phase 1: Core Deduplication (MUST HAVE)
### 1.1 Fuzzy Entity Matching
[From yonearth + improvements]

### 1.2 Canonical Resolution
[Enhanced beyond current 88 entities]

### 1.3 Entity Quality Filtering
[From yonearth + Regen-specific]

### 1.4 [Any other MUST HAVE modules from yonearth]

## Phase 2: Advanced Resolution (SHOULD HAVE)
### 2.1 Pronoun Resolution
[From yonearth]

### 2.2 Acronym/Abbreviation Expansion
[New or from yonearth]

### 2.3 Possessive Handling
[New or from yonearth]

### 2.4 [Any other SHOULD HAVE modules]

## Phase 3: Relationship Quality (SHOULD HAVE)
### 3.1 Relationship Deduplication
[From yonearth]

### 3.2 Conflict Resolution
[New or from yonearth]

### 3.3 [Any other relationship modules]

## Phase 4: Domain-Specific (NICE TO HAVE)
### 4.1 Regen-Specific Aliases
[New - based on corpus analysis]

### 4.2 Blockchain Entity Handling
[New - validators, tokens, etc.]

### 4.3 [Any other domain-specific needs]

## Implementation Order
[Priority ranking based on impact vs effort]

## Testing Strategy
[How to validate each module]

## Rollback Plan
[If something breaks]

## Success Metrics
[How to measure quality improvement]

## Timeline
[Realistic estimates for each phase]

## Dependencies
[Libraries, tools, data needed]

## Risks & Mitigations
[What could go wrong, how to handle]
```

---

## Deliverables

By end of this investigation:

1. ✅ **YONEARTH_COMPLETE_ANALYSIS.md** - Full analysis of yonearth approach
2. ✅ **GAP_ANALYSIS.md** - What's missing even in yonearth
3. ✅ **COMPLETE_DEDUP_IMPLEMENTATION_PLAN.md** - Comprehensive implementation plan
4. ✅ **PRIORITIZED_MODULES.md** - What to implement in what order
5. ✅ **CODE_REFERENCES.md** - Links to relevant yonearth code for each module

---

## Investigation Checklist

### yonearth Analysis
- [ ] All postprocessing modules discovered and documented
- [ ] All graph building modules documented
- [ ] Pipeline architecture fully mapped
- [ ] Configuration options cataloged
- [ ] Edge cases documented
- [ ] Dependencies listed
- [ ] Performance characteristics understood
- [ ] Known limitations identified

### Gap Analysis
- [ ] Domain-specific needs identified
- [ ] Entity type coverage assessed
- [ ] Relationship quality needs defined
- [ ] Temporal/versioning needs understood
- [ ] Multi-language needs assessed
- [ ] Acronym handling scoped

### Implementation Planning
- [ ] Modules prioritized (MUST/SHOULD/NICE)
- [ ] Implementation order defined
- [ ] Testing strategy created
- [ ] Timeline estimated
- [ ] Dependencies identified
- [ ] Risks assessed

---

## Success Criteria

**Investigation complete when**:
- ✅ Can describe EVERY module in yonearth quality pipeline
- ✅ Understand WHY each design choice was made
- ✅ Identified ALL gaps beyond yonearth
- ✅ Created comprehensive, prioritized implementation plan
- ✅ Estimated realistic timeline
- ✅ Defined success metrics

**DO NOT START IMPLEMENTATION** until investigation is 100% complete.

---

## Time Allocation

| Phase | Time | Description |
|-------|------|-------------|
| Part 1: yonearth investigation | 3 hours | Deep dive into all modules |
| Part 2: Gap analysis | 1 hour | What's missing |
| Part 3: Literature review | 30 min | Best practices |
| Part 4: Planning | 1.5 hours | Comprehensive plan |
| **TOTAL** | **6 hours** | Complete understanding |

---

## Reference Servers

**yonearth-gaia-chatbot**:
- Server: `ssh claudeuser@152.53.37.180`
- Path: `/home/claudeuser/yonearth-gaia-chatbot`

**koi-processor** (reference only, don't modify yet):
- Server: `ssh darren@202.61.196.119`
- Path: `/opt/projects/koi-processor`

---

**Priority**: CRITICAL - BLOCKING
**Status**: Ready to start
**Expected completion**: 6 hours of thorough investigation
