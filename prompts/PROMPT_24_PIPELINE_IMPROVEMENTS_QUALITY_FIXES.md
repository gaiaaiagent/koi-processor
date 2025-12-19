# PROMPT_24: Pipeline Improvements - Quality Fixes

**Date**: 2025-12-10
**Status**: EXECUTED LOCALLY (tests passing)
**Phase**: Phase 3 Continuation - Quality Enhancement
**Previous**: PROMPT_23 (GitHub extraction in progress)
**Next**: PROMPT_25 (Targeted re-extraction of problematic sources)

---

## Context

GitHub extraction is in progress (601/4,400 docs complete, ~12 hours remaining).

Investigation of intermediate results revealed quality issues in legacy backfill data:

**Critical Issues**:
1. **Chunk repetition artifacts**: Single forum post → 1,157 mentions of same CLAIM
2. **Template/boilerplate extraction**: JIRA IDs, issue templates as entities
3. **Placeholder entities**: "Public Users", "Unknown", "Anonymous" as PERSON (513 mentions)
4. **Type collisions**: "Regen Network" split across PROJECT/PERSON/ORG

**Root Causes**:
- Legacy backfill (Oct 2024) used per-chunk extraction without document-level dedup
- No template/boilerplate filtering
- Weak type guards for brand terms
- Missing pattern filters for JIRA/issue IDs

**Source Document**: `/tmp/intermediate_results_findings.md`

**Affected Sources**:
- Discourse forums (~839 docs) - Worst offender
- GitHub Issues (~200 docs) - Template text
- Notion pages - Placeholders
- GitHub Markdown - Currently extracting, cleaner data

---

## Objective

Implement pipeline improvements to prevent quality issues in remaining GitHub extraction and prepare for targeted re-extraction of problematic sources.

**Goals**:
1. Add pattern filters for JIRA/issue IDs and boilerplate
2. Implement document-level entity deduplication
3. Enhance CanonicalResolver for Regen brand terms
4. Test improvements with real examples
5. Deploy to production (non-breaking, backward compatible)

**Success Criteria**:
- JIRA IDs (e.g., "APP-776") blocked
- Known boilerplate (e.g., "Testing Instructions") blocked
- Placeholder entities (e.g., "Public Users") blocked
- Document-level dedup prevents chunk repetition
- Regen brand terms canonicalized to single type
- Zero regression in existing tests
- Production deployment successful

---

## Current State

### Infrastructure
- **Entity Registry**: 6,858 unique entities, 31,352 total mentions
- **Pipeline**: EntityQualityFilter active with 8 rules
- **CanonicalResolver**: 88 entries, 194 aliases
- **Tests**: 121 passing
- **GitHub Extraction**: 601 docs complete, running in background

### Known Bad Actors (from findings)
```python
INFLATED_COUNTS = {
    # Chunk repetition artifacts
    "Knowledge network expands with data ingestion": 1157,  # CLAIM
    "Strengthening collective intelligence": 201,  # CLAIM

    # Template/boilerplate
    "APP-776": 509,  # JIRA ID as CLAIM
    "DRY Principles": 444,  # EVIDENCE (template)
    "Testing Instructions": 185,  # CLAIM (template)

    # Placeholders
    "Public Users": 304,  # PERSON
    "Unknown": 140,  # PERSON
    "Anonymous": 69,  # PERSON
}

TYPE_COLLISIONS = {
    "Regen Network": ["PROJECT", "PERSON", "ORG"],  # 1,918 total
    "Regen": ["PROJECT", "ORG", "PERSON"],
    "Regen Registry": ["PROJECT", "ORG"],
    "Regen Ledger": ["PROJECT", "ORG"],
}
```

## Progress Update (2025-12-10)

- Added pattern-based blocking for JIRA/issue IDs, ERC standards, boilerplate phrases, and placeholder PERSON entities in `src/knowledge_graph/improvements/entity_quality_filter.py` and the inline fallback `src/knowledge_graph/postprocessing/modules/entity_quality_module.py`; expanded tests in `src/knowledge_graph/improvements/tests/test_entity_quality_filter.py` and `tests/test_pipeline_modules.py`.
- Introduced document-level deduplication via `src/knowledge_graph/postprocessing/modules/document_dedup_module.py`; registered in the pipeline builder/config (`src/knowledge_graph/config/pipeline_config.json`, `src/knowledge_graph/postprocessing/pipeline.py`) and GraphIntegration (`src/knowledge_graph/graph_integration.py`); new tests in `tests/test_document_deduplicator.py`.
- Canonical resolver now supports `allow_type_mismatch` + canonical type alignment; registry updated (`data/canonical_entities.json` with Regen Ledger → TECHNOLOGY and added Regen Marketplace) and config defaults set in `pipeline_config.json`; logic/tests updated in `src/knowledge_graph/improvements/canonical_resolver.py`, `src/knowledge_graph/improvements/tests/test_canonical_resolver.py`, and `tests/test_pipeline_modules.py`.
- Added end-to-end validation script `scripts/test_pipeline_improvements.py` (all cases passing locally).

### Validation (local)

- `pytest src/knowledge_graph/improvements/tests/test_entity_quality_filter.py`
- `pytest src/knowledge_graph/improvements/tests/test_canonical_resolver.py`
- `pytest tests/test_document_deduplicator.py tests/test_pipeline_modules.py`
- `python scripts/test_pipeline_improvements.py`

---

## Implementation Plan

### Task 1: Add Pattern Filters to EntityQualityFilter

**File**: `src/knowledge_graph/improvements/entity_quality_filter.py` (and inline fallback `src/knowledge_graph/postprocessing/modules/entity_quality_module.py`)

**Changes**:

```python
class EntityQualityFilter(PostProcessingModule):
    """Quality filter with pattern-based blocking"""

    # Add new patterns
    JIRA_ISSUE_PATTERN = re.compile(r'^[A-Z]+-\d+$')  # APP-776, ERC-20, etc.
    ERC_STANDARD_PATTERN = re.compile(r'^ERC-\d+$')  # ERC-20, ERC-721, ERC-1155

    # Known boilerplate from issue/forum templates
    BOILERPLATE_BLOCKLIST = {
        # Issue templates
        "Testing Instructions",
        "DRY Principles",
        "Test Plan",
        "Acceptance Criteria",
        "Definition of Done",
        "Success Criteria",

        # Forum boilerplate
        "Knowledge network expands with data ingestion",
        "Strengthening collective intelligence",
        "Building regenerative economies",

        # Placeholders
        "Public Users",
        "Unknown",
        "Anonymous",
        "N/A",
        "TBD",
        "TODO",

        # Generic claims (too vague)
        "More information needed",
        "Further research required",
        "Additional context",
    }

    def _should_block_entity(self, entity: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Check if entity should be blocked"""
        entity_text = entity.get("entity_text", "")
        entity_type = entity.get("entity_type", "")

        # Existing checks...
        # (pronouns, generics, urls, etc.)

        # NEW: Block JIRA/issue IDs
        if self.JIRA_ISSUE_PATTERN.match(entity_text):
            return True, "jira_issue_id"

        # NEW: Block ERC standards (often misidentified as entities)
        if self.ERC_STANDARD_PATTERN.match(entity_text):
            return True, "erc_standard"

        # NEW: Block known boilerplate
        if entity_text in self.BOILERPLATE_BLOCKLIST:
            return True, "boilerplate"

        # NEW: Block placeholder PERSONs
        if entity_type == "PERSON" and entity_text in {"Public Users", "Unknown", "Anonymous"}:
            return True, "placeholder_person"

        return False, None
```

**Test Cases** (add to `tests/test_entity_quality_filter.py`):

```python
def test_jira_id_blocking():
    """Test JIRA/issue ID pattern blocking"""
    blocked = [
        {"entity_text": "APP-776", "entity_type": "CLAIM"},
        {"entity_text": "JIRA-123", "entity_type": "PROJECT"},
        {"entity_text": "ERC-20", "entity_type": "EVIDENCE"},
        {"entity_text": "ERC-721", "entity_type": "TECHNOLOGY"},
    ]
    for entity in blocked:
        result = filter.process_entity(entity)
        assert result is None, f"Should block {entity['entity_text']}"

def test_boilerplate_blocking():
    """Test template/boilerplate blocking"""
    blocked = [
        {"entity_text": "Testing Instructions", "entity_type": "CLAIM"},
        {"entity_text": "DRY Principles", "entity_type": "EVIDENCE"},
        {"entity_text": "Public Users", "entity_type": "PERSON"},
        {"entity_text": "Unknown", "entity_type": "PERSON"},
    ]
    for entity in blocked:
        result = filter.process_entity(entity)
        assert result is None, f"Should block {entity['entity_text']}"
```

**Expected Impact**:
- Block ~1,500+ noisy entity mentions
- Prevent similar issues in remaining GitHub docs

---

### Task 2: Document-Level Entity Deduplication

**Problem**: Legacy backfill emitted same entity for every chunk of a document.

**Solution**: Add document-level deduplication module to pipeline.

**New File**: `src/knowledge_graph/postprocessing/modules/document_dedup_module.py`

```python
from typing import Dict, Any, Set
from ..pipeline import PostProcessingModule, ProcessingContext

class DocumentLevelDeduplicator(PostProcessingModule):
    """Prevents duplicate entities within same document (memory_rid)"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.name = "DocumentLevelDeduplicator"

    def process(self, context: ProcessingContext) -> ProcessingContext:
        """Remove duplicate entities within same document"""
        memory_rid = context.memory_rid
        entities = context.entities

        # Track unique entities: (normalized_text, entity_type) -> first occurrence
        seen_entities: Dict[tuple, Dict] = {}
        deduped_entities = []

        for entity in entities:
            entity_text = entity.get("entity_text", "")
            entity_type = entity.get("entity_type", "")
            normalized = entity_text.lower().strip()

            key = (normalized, entity_type)

            if key not in seen_entities:
                # First occurrence - keep it
                seen_entities[key] = entity
                deduped_entities.append(entity)
            else:
                # Duplicate - skip, but log
                context.add_stat("document_duplicates_removed", 1)

        # Update context
        duplicates_removed = len(entities) - len(deduped_entities)
        if duplicates_removed > 0:
            self.logger.info(
                f"Document {memory_rid}: Removed {duplicates_removed} duplicate entities"
            )

        context.entities = deduped_entities
        return context
```

**Integration** (add to pipeline config):

```json
{
  "modules": [
    {
      "name": "ConfidenceFilter",
      "enabled": true,
      "config": {"threshold": 0.70}
    },
    {
      "name": "DocumentLevelDeduplicator",
      "enabled": true,
      "config": {}
    },
    {
      "name": "CanonicalResolver",
      "enabled": true
    },
    {
      "name": "EntityQualityFilter",
      "enabled": true
    },
    {
      "name": "ListSplitter",
      "enabled": true
    },
    {
      "name": "OntologyNormalizer",
      "enabled": true
    }
  ]
}
```

**Test Cases**:

```python
def test_document_level_dedup():
    """Test document-level deduplication"""
    context = ProcessingContext(
        memory_rid="test.doc",
        entities=[
            {"entity_text": "Regen Network", "entity_type": "ORG"},
            {"entity_text": "Regen Network", "entity_type": "ORG"},  # Duplicate
            {"entity_text": "regen network", "entity_type": "ORG"},  # Case variant
            {"entity_text": "Gregory Landua", "entity_type": "PERSON"},
            {"entity_text": "Gregory Landua", "entity_type": "PERSON"},  # Duplicate
        ]
    )

    deduplicator = DocumentLevelDeduplicator({})
    result = deduplicator.process(context)

    assert len(result.entities) == 2, "Should dedupe to 2 unique entities"
    assert result.get_stat("document_duplicates_removed") == 3
```

**Expected Impact**:
- Prevent chunk repetition artifacts like "Knowledge network expands..." × 1,157
- Reduce entity mentions by ~30-40% in chunked documents

---

### Task 3: Enhance CanonicalResolver for Regen Brand Terms

**File**: `data/canonical_entities.json`

**Add Brand Term Section**:

```json
{
  "canonical_entities": {
    // Existing entries...

    // === REGEN NETWORK BRAND TERMS ===
    // All Regen-branded terms should canonicalize to ORGANIZATION type

    "Regen Network": {
      "canonical_name": "Regen Network",
      "canonical_type": "ORGANIZATION",
      "aliases": [
        "Regen",
        "Regen network",
        "REGEN",
        "regen network",
        "Regen Network Development"
      ],
      "description": "Main organization behind Regen Ledger and ecosystem"
    },

    "Regen Registry": {
      "canonical_name": "Regen Registry",
      "canonical_type": "ORGANIZATION",
      "aliases": [
        "Regen registry",
        "Registry",
        "regen registry"
      ],
      "description": "Registry program for ecological credits"
    },

    "Regen Ledger": {
      "canonical_name": "Regen Ledger",
      "canonical_type": "TECHNOLOGY",
      "aliases": [
        "Regen ledger",
        "regen-ledger",
        "Regen blockchain",
        "Regen chain"
      ],
      "description": "Blockchain infrastructure for ecological assets"
    },

    "Regen Marketplace": {
      "canonical_name": "Regen Marketplace",
      "canonical_type": "TECHNOLOGY",
      "aliases": [
        "Regen marketplace",
        "Marketplace",
        "regen marketplace"
      ],
      "description": "Platform for trading ecological credits"
    },

    "Regen Foundation": {
      "canonical_name": "Regen Foundation",
      "canonical_type": "ORGANIZATION",
      "aliases": [
        "Regen foundation",
        "Foundation"
      ],
      "description": "Non-profit foundation supporting Regen ecosystem"
    }
  }
}
```

**Update CanonicalResolver Logic**:

```python
class CanonicalResolver(PostProcessingModule):
    """Resolve entity aliases to canonical forms"""

    def _resolve_entity(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve entity to canonical form"""
        entity_text = entity.get("entity_text", "")
        entity_type = entity.get("entity_type", "")

        # Check canonical entries
        for canonical_name, entry in self.canonical_map.items():
            canonical_type = entry["canonical_type"]
            aliases = entry["aliases"]

            # Exact match on canonical name
            if entity_text == canonical_name:
                entity["entity_text"] = canonical_name
                entity["entity_type"] = canonical_type
                entity["resolved"] = True
                return entity

            # Check aliases (case-insensitive)
            normalized_text = entity_text.lower().strip()
            normalized_aliases = [a.lower().strip() for a in aliases]

            if normalized_text in normalized_aliases:
                entity["entity_text"] = canonical_name
                entity["entity_type"] = canonical_type
                entity["resolved"] = True
                self.logger.debug(f"Resolved '{entity_text}' → '{canonical_name}' ({canonical_type})")
                return entity

        return entity
```

**Test Cases**:

```python
def test_regen_brand_canonicalization():
    """Test Regen brand terms resolve correctly"""
    test_cases = [
        # Input → Expected canonical form
        ({"entity_text": "Regen", "entity_type": "PROJECT"},
         {"entity_text": "Regen Network", "entity_type": "ORGANIZATION"}),

        ({"entity_text": "REGEN", "entity_type": "PERSON"},
         {"entity_text": "Regen Network", "entity_type": "ORGANIZATION"}),

        ({"entity_text": "regen network", "entity_type": "PROJECT"},
         {"entity_text": "Regen Network", "entity_type": "ORGANIZATION"}),

        ({"entity_text": "Regen ledger", "entity_type": "PROJECT"},
         {"entity_text": "Regen Ledger", "entity_type": "TECHNOLOGY"}),
    ]

    resolver = CanonicalResolver({})
    for input_entity, expected in test_cases:
        result = resolver._resolve_entity(input_entity)
        assert result["entity_text"] == expected["entity_text"]
        assert result["entity_type"] == expected["entity_type"]
```

**Expected Impact**:
- Consolidate "Regen Network" from 3 types → 1 ORGANIZATION (1,918 mentions)
- Consolidate other Regen brand terms similarly

---

### Task 4: Test End-to-End with Real Examples

**Test Script**: `scripts/test_pipeline_improvements.py`

```python
import sys
sys.path.insert(0, "/opt/projects/koi-processor/src")

from knowledge_graph.graph_integration import KnowledgeGraphIntegrator

# Initialize with new pipeline
kg = KnowledgeGraphIntegrator(
    store_type="memory",
    use_pipeline=True,
    use_entity_resolver=False  # Test pipeline only
)

# Test Case 1: JIRA IDs should be blocked
test_entities_jira = [
    {"entity_text": "APP-776", "entity_type": "CLAIM", "confidence": 0.95},
    {"entity_text": "ERC-20", "entity_type": "TECHNOLOGY", "confidence": 0.90},
    {"entity_text": "JIRA-123", "entity_type": "PROJECT", "confidence": 0.85},
]

print("=== Test 1: JIRA/Issue ID Blocking ===")
result = kg.process_entities_batch(test_entities_jira)
print(f"Input: 3 entities → Output: {len(result)} entities")
assert len(result) == 0, "All JIRA IDs should be blocked"
print("✓ PASS: All JIRA IDs blocked\n")

# Test Case 2: Boilerplate should be blocked
test_entities_boilerplate = [
    {"entity_text": "Testing Instructions", "entity_type": "CLAIM", "confidence": 0.95},
    {"entity_text": "DRY Principles", "entity_type": "EVIDENCE", "confidence": 0.90},
    {"entity_text": "Public Users", "entity_type": "PERSON", "confidence": 0.85},
    {"entity_text": "Unknown", "entity_type": "PERSON", "confidence": 0.80},
]

print("=== Test 2: Boilerplate Blocking ===")
result = kg.process_entities_batch(test_entities_boilerplate)
print(f"Input: 4 entities → Output: {len(result)} entities")
assert len(result) == 0, "All boilerplate should be blocked"
print("✓ PASS: All boilerplate blocked\n")

# Test Case 3: Document-level dedup
test_entities_dedup = [
    {"entity_text": "Regen Network", "entity_type": "ORG", "confidence": 0.95},
    {"entity_text": "Regen Network", "entity_type": "ORG", "confidence": 0.95},  # Dup
    {"entity_text": "regen network", "entity_type": "ORG", "confidence": 0.90},  # Dup (case)
    {"entity_text": "Gregory Landua", "entity_type": "PERSON", "confidence": 0.85},
]

print("=== Test 3: Document-Level Deduplication ===")
result = kg.process_entities_batch(test_entities_dedup)
print(f"Input: 4 entities → Output: {len(result)} entities")
assert len(result) == 2, "Should dedupe to 2 unique entities"
print("✓ PASS: Duplicates removed\n")

# Test Case 4: Brand term canonicalization
test_entities_brand = [
    {"entity_text": "Regen", "entity_type": "PROJECT", "confidence": 0.95},
    {"entity_text": "REGEN", "entity_type": "PERSON", "confidence": 0.90},
    {"entity_text": "regen network", "entity_type": "PROJECT", "confidence": 0.85},
    {"entity_text": "Regen Ledger", "entity_type": "PROJECT", "confidence": 0.80},
]

print("=== Test 4: Brand Term Canonicalization ===")
result = kg.process_entities_batch(test_entities_brand)
print(f"Input: 4 entities → Output: {len(result)} entities")
# All "Regen" variants should become "Regen Network" (ORGANIZATION)
regen_network_count = sum(1 for e in result if e["entity_text"] == "Regen Network")
print(f"'Regen Network' (ORGANIZATION) count: {regen_network_count}")
assert regen_network_count == 3, "Three variants should canonicalize to 'Regen Network'"
print("✓ PASS: Brand terms canonicalized\n")

# Test Case 5: Real-world example from findings
test_entities_real = [
    {"entity_text": "Knowledge network expands with data ingestion", "entity_type": "CLAIM", "confidence": 0.95},
    {"entity_text": "APP-776", "entity_type": "CLAIM", "confidence": 0.90},
    {"entity_text": "Public Users", "entity_type": "PERSON", "confidence": 0.85},
    {"entity_text": "Regen Network", "entity_type": "ORG", "confidence": 0.95},
    {"entity_text": "Gregory Landua", "entity_type": "PERSON", "confidence": 0.90},
]

print("=== Test 5: Real-World Mixed Batch ===")
result = kg.process_entities_batch(test_entities_real)
print(f"Input: 5 entities → Output: {len(result)} entities")
# Should block: boilerplate CLAIM, JIRA ID, placeholder PERSON
# Should pass: Regen Network, Gregory Landua
assert len(result) == 2, "Should keep only 2 valid entities"
assert any(e["entity_text"] == "Regen Network" for e in result)
assert any(e["entity_text"] == "Gregory Landua" for e in result)
print("✓ PASS: Mixed batch filtered correctly\n")

print("=== ALL TESTS PASSED ===")
```

**Run Tests**:
```bash
ssh darren@202.61.196.119 "cd /opt/projects/koi-processor && source venv/bin/activate && python3 scripts/test_pipeline_improvements.py"
```

---

### Task 5: Production Deployment

**Checklist**:

1. **Run full test suite**:
```bash
pytest tests/ -v
# Expected: 121+ passing (added ~15 new tests)
```

2. **Test with sample document**:
```python
# Extract a known problematic document ID to verify fixes
memory_rid = "regen.forum-post:forum.regen.network_561_post_1"
# Should now emit unique entities only, no chunk repetition
```

3. **Deploy updated files**:
```bash
# Copy updated files to production
scp src/knowledge_graph/postprocessing/modules/entity_quality_filter.py darren@202.61.196.119:/opt/projects/koi-processor/src/knowledge_graph/postprocessing/modules/
scp src/knowledge_graph/postprocessing/modules/document_dedup.py darren@202.61.196.119:/opt/projects/koi-processor/src/knowledge_graph/postprocessing/modules/
scp src/knowledge_graph/config/canonical_entities.json darren@202.61.196.119:/opt/projects/koi-processor/src/knowledge_graph/config/
```

4. **Verify GitHub extraction picks up changes**:
```bash
# Check extraction process is still running
ssh darren@202.61.196.119 "ps aux | grep extract_fresh_documents"

# Monitor for new entities being blocked
ssh darren@202.61.196.119 "cd /opt/projects/koi-processor && tail -f logs/extraction.log | grep -E '(blocked|boilerplate|jira)'"
```

5. **Validate pipeline stats**:
```sql
-- Check blocked entity stats
SELECT
    context->'stats'->>'entities_blocked' as blocked,
    context->'stats'->>'document_duplicates_removed' as deduped
FROM pipeline_processing_log
WHERE created_at >= NOW() - INTERVAL '1 hour'
ORDER BY created_at DESC
LIMIT 10;
```

---

## Validation Criteria

### Functional Tests
- ✅ All 121+ existing tests pass
- ✅ 15+ new tests pass (pattern filters, dedup, canonicalization)
- ✅ End-to-end test script passes all 5 test cases

### Integration Tests
- ✅ GitHub extraction continues without errors
- ✅ New entities are properly filtered
- ✅ Entity registry growth rate normalizes (no inflated counts)

### Quality Metrics (Monitor After Deployment)
- ✅ JIRA IDs blocked: 0 new "APP-*" or "ERC-*" entities
- ✅ Boilerplate blocked: 0 new "Testing Instructions", "DRY Principles", etc.
- ✅ Placeholders blocked: 0 new "Public Users", "Unknown", "Anonymous"
- ✅ Document dedup working: occurrence_count growth rate matches extraction rate
- ✅ Brand terms consolidated: "Regen Network" remains ORGANIZATION type

---

## Files Modified

### Code Changes
1. `src/knowledge_graph/improvements/entity_quality_filter.py` - Add boilerplate/JIRA/placeholder filters and stats
2. `src/knowledge_graph/postprocessing/modules/entity_quality_module.py` - Inline fallback updated to match new filters
3. `src/knowledge_graph/postprocessing/modules/document_dedup_module.py` - New document-level dedup module
4. `src/knowledge_graph/postprocessing/modules/__init__.py` / `pipeline.py` / `graph_integration.py` - Register dedup in builder/default pipeline
5. `src/knowledge_graph/improvements/canonical_resolver.py` - Allow type mismatches, prioritize canonical types
6. `data/canonical_entities.json` - Regen Ledger → TECHNOLOGY, added Regen Marketplace, updated metadata
7. `src/knowledge_graph/config/pipeline_config.json` - Enable dedup, canonical type alignment, metadata updated
8. `src/knowledge_graph/postprocessing/__init__.py` - Documentation update

### Test Changes
9. `src/knowledge_graph/improvements/tests/test_entity_quality_filter.py` - New pattern filter coverage
10. `src/knowledge_graph/improvements/tests/test_canonical_resolver.py` - Brand/type alignment coverage
11. `tests/test_pipeline_modules.py` - Canonical type alignment + pattern filter cases
12. `tests/test_document_deduplicator.py` - Document-level dedup tests

### Scripts
13. `scripts/test_pipeline_improvements.py` - End-to-end validation script

### Documentation
14. `prompts/PROMPT_24_PIPELINE_IMPROVEMENTS_QUALITY_FIXES.md` - This file

---

## Rollback Plan

If issues arise:

1. **Disable DocumentLevelDeduplicator**:
```json
// In pipeline_config.json
{
  "name": "DocumentLevelDeduplicator",
  "enabled": false  // Disable if causing issues
}
```

2. **Revert EntityQualityFilter**:
```bash
git checkout HEAD~1 src/knowledge_graph/improvements/entity_quality_filter.py src/knowledge_graph/postprocessing/modules/entity_quality_module.py
```

3. **Revert CanonicalResolver**:
```bash
git checkout HEAD~1 data/canonical_entities.json src/knowledge_graph/improvements/canonical_resolver.py
```

4. **Restart extraction** (if needed):
```bash
ssh darren@202.61.196.119 "pkill -f extract_fresh_documents"
# Restart with previous commit
```

---

## Next Steps (PROMPT_25)

After PROMPT_24 deployment and GitHub extraction completion:

1. **Validate improvements** with sample queries
2. **Identify remaining problematic entities** in legacy data
3. **Plan targeted re-extraction** of Discourse/Issues with improved pipeline
4. **Implement legacy cleanup job** to remove known bad actors

---

## Cost Estimate

- **Development**: 2 hours
- **Testing**: 30 minutes
- **Deployment**: 15 minutes
- **API Costs**: $0 (no re-extraction yet)
- **Total**: ~3 hours

---

## Success Metrics

| Metric | Before | Target | Actual |
|--------|--------|--------|--------|
| **Pipeline Tests** | 121 | 136+ | TBD |
| **JIRA IDs Blocked** | N/A | 100% | TBD |
| **Boilerplate Blocked** | N/A | 100% | TBD |
| **Placeholders Blocked** | N/A | 100% | TBD |
| **Document Dedup Rate** | 0% | 30-40% | TBD |
| **Brand Term Consolidation** | 3 types | 1 type | TBD |

---

## References

- **Investigation**: `/tmp/intermediate_results_findings.md`
- **Current Pipeline**: `src/knowledge_graph/postprocessing/`
- **Test Suite**: `tests/test_pipeline_*.py`
- **Phase 3 Tracking**: `prompts/ALL_PROMPTS_SUMMARY.md`

---

**Last Updated**: 2025-12-10
**Status**: READY FOR EXECUTION
**Estimated Duration**: 3 hours
**Next**: PROMPT_25 (Targeted Re-extraction)
