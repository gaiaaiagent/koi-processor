# PROMPT 20: Port Cross-Document Deduplication from yonearth-gaia-chatbot

**Date**: 2025-12-09
**Status**: URGENT - BLOCKING EXTRACTION
**Priority**: CRITICAL
**Estimated Time**: 3-4 hours

---

## Context

**Fresh extraction STOPPED at 300/4,710 GitHub docs** due to critical gap: no cross-document entity deduplication.

**Problem identified**: Current koi-processor creates duplicate entity literals for name variations:
- "Regen Network" (1,207) + "Regen" (694) + "REGEN" (69) + "$REGEN" (291) = **2,261 occurrences of same entity**
- "Gregory Landua" (88) + "Gregory" (121) + "Gregory_RND" (81) = **290 occurrences of same person**

**Impact**: ~30-40% entity fragmentation, relationships split across duplicate nodes, graph queries broken.

**Solution**: Port proven deduplication approach from yonearth-gaia-chatbot.

---

## Investigation Summary (Already Complete)

From yonearth-gaia-chatbot analysis:

### Deduplication Modules Found

**Location**: `ssh claudeuser@152.53.37.180` → `/home/claudeuser/yonearth-gaia-chatbot`

1. **EntityDeduplicator** (`src/knowledge_graph/postprocessing/universal/entity_deduplicator.py`)
   - Case-insensitive variant matching
   - Deterministic resolver (longest/most frequent/earliest wins)

2. **FuzzyMatcher** (`src/knowledge_graph/graph/graph_builder.py`, `scripts/build_unified_graph_hybrid.py`)
   - Uses fuzzywuzzy with 90-95% threshold
   - Groups entities by type before matching
   - Optional GraphSAGE embedding tie-breaks

3. **PronounResolver** (`src/knowledge_graph/postprocessing/universal/pronoun_resolver.py` v1.6)
   - Multi-pass context window
   - Possessive support ("his", "her")
   - Generic pronoun mapping
   - Caches resolved entities

4. **SemanticDeduplicator** (`src/knowledge_graph/postprocessing/universal/semantic_deduplicator.py`)
   - Sentence-transformer similarity (~0.87 threshold)
   - Drops near-duplicate relationships

---

## Porting Strategy

### Phase 1: Core Fuzzy Deduplication (MUST HAVE)

Port the fuzzy matching logic to koi-processor pipeline.

**Target**: 90-95% reduction in name variation duplicates.

**Approach**:
1. Create new module: `src/knowledge_graph/postprocessing/modules/fuzzy_deduplicator.py`
2. Integrate into pipeline after CanonicalResolver
3. Use fuzzywuzzy for fast, deterministic matching

**What to port from yonearth**:
```python
# From yonearth's graph_builder.py (approximate)
from fuzzywuzzy import fuzz

def deduplicate_entities_fuzzy(entities, threshold=92):
    """Group similar entity names using fuzzy matching."""
    groups = defaultdict(list)

    for entity in entities:
        # Find best match in existing groups
        best_match = None
        best_score = 0

        for canonical_name in groups.keys():
            score = fuzz.ratio(entity['name'].lower(), canonical_name.lower())
            if score > best_score:
                best_score = score
                best_match = canonical_name

        if best_score >= threshold:
            groups[best_match].append(entity)
        else:
            groups[entity['name']].append(entity)

    # Choose canonical name for each group (longest, most frequent, or earliest)
    canonical_entities = []
    for group_entities in groups.values():
        canonical = choose_canonical(group_entities)  # longest/most_frequent logic
        canonical_entities.append(canonical)

    return canonical_entities
```

**Key decisions**:
- **Threshold**: 92% (balance precision/recall)
- **Canonical selection**: Longest name (e.g., "Gregory Landua" > "Gregory")
- **Type-aware**: Only match entities of same type
- **Relationship rewriting**: Update all relationship sources/targets to canonical names

---

### Phase 2: Enhanced Quality Filtering (MUST HAVE)

Fix EntityQualityFilter gaps identified in SPARQL analysis.

**Current gaps**:
- "User" (130 occurrences) - should be blocked ❌
- "unknown" / "Unknown" (257 occurrences) - should be blocked ❌
- "Validator" (82 occurrences) - should be blocked ❌
- "community" (57 occurrences) - should be blocked ❌

**Updates needed**:
```python
# Add to EntityQualityFilter stop words
ADDITIONAL_STOP_WORDS = {
    "user", "users",
    "unknown",
    "validator", "validators",
    "community", "communities",
    "author", "authors",
    "contributor", "contributors",
    "member", "members",
    "team",
}
```

---

### Phase 3: Pronoun Resolution (NICE TO HAVE)

Port yonearth's PronounResolver if time permits.

**Target**: Resolve pronouns like "he", "she", "they" to named entities.

**Complexity**: MEDIUM (context window, multi-pass)

**Decision**: Implement if Phase 1 & 2 complete in < 2 hours, otherwise defer to Phase 4.

**What to port**:
- Context window tracking (last N entities mentioned)
- Pronoun → entity mapping ("he" → last male person entity)
- Possessive handling ("his company" → "Gregory Landua's company")

---

## Implementation Plan

### Step 1: Create FuzzyDeduplicator Module (1.5 hours)

**File**: `src/knowledge_graph/postprocessing/modules/fuzzy_deduplicator.py`

**Requirements**:
1. Inherits from `PostProcessingModule`
2. Input: List of entities from previous modules
3. Output: Deduplicated entities with canonical names
4. Side effect: Updates relationship sources/targets to canonical names
5. Logs: Reports deduplication statistics

**Template**:
```python
from typing import Dict, List, Any
from collections import defaultdict
from fuzzywuzzy import fuzz
from .base import PostProcessingModule
from ..context import ProcessingContext

class FuzzyDeduplicator(PostProcessingModule):
    """
    Deduplicate entities using fuzzy string matching.

    Merges name variations (e.g., "Regen Network" / "Regen" / "REGEN")
    to canonical forms based on configurable similarity threshold.
    """

    def __init__(self, threshold: float = 92.0, canonical_strategy: str = "longest"):
        """
        Args:
            threshold: Fuzzy match threshold (0-100). Default 92.
            canonical_strategy: How to choose canonical name.
                - "longest": Choose longest variant
                - "most_frequent": Choose most common variant
                - "first": Choose first encountered variant
        """
        super().__init__()
        self.threshold = threshold
        self.canonical_strategy = canonical_strategy
        self.stats = {
            "groups_found": 0,
            "entities_merged": 0,
            "relationships_updated": 0
        }

    def process(self, context: ProcessingContext) -> ProcessingContext:
        """Deduplicate entities and update relationships."""
        # Group entities by type (only match same types)
        by_type = self._group_by_type(context.entities)

        # Deduplicate each type group
        canonical_map = {}  # variant_name -> canonical_name
        deduplicated = []

        for entity_type, entities in by_type.items():
            type_canonical = self._deduplicate_group(entities)
            deduplicated.extend(type_canonical['entities'])
            canonical_map.update(type_canonical['mapping'])

        # Update relationships to use canonical names
        updated_relationships = self._update_relationships(
            context.relationships,
            canonical_map
        )

        # Update context
        context.entities = deduplicated
        context.relationships = updated_relationships

        # Log stats
        self.logger.info(
            f"FuzzyDeduplicator: {self.stats['entities_merged']} entities merged "
            f"into {self.stats['groups_found']} canonical forms"
        )

        return context

    def _group_by_type(self, entities: List[Dict]) -> Dict[str, List[Dict]]:
        """Group entities by type."""
        by_type = defaultdict(list)
        for entity in entities:
            entity_type = entity.get('type', 'UNKNOWN')
            by_type[entity_type].append(entity)
        return by_type

    def _deduplicate_group(self, entities: List[Dict]) -> Dict[str, Any]:
        """Deduplicate a group of same-type entities."""
        if not entities:
            return {'entities': [], 'mapping': {}}

        # Group similar names
        groups = defaultdict(list)

        for entity in entities:
            name = entity['name']
            matched = False

            # Find best matching group
            best_match = None
            best_score = 0

            for canonical_name in groups.keys():
                score = fuzz.ratio(name.lower(), canonical_name.lower())
                if score > best_score:
                    best_score = score
                    best_match = canonical_name

            # Add to existing group or create new
            if best_score >= self.threshold:
                groups[best_match].append(entity)
                matched = True
            else:
                groups[name].append(entity)

        # Choose canonical entity for each group
        canonical_entities = []
        canonical_map = {}

        for group_entities in groups.values():
            canonical = self._choose_canonical(group_entities)
            canonical_entities.append(canonical)

            # Map all variants to canonical
            for entity in group_entities:
                if entity['name'] != canonical['name']:
                    canonical_map[entity['name']] = canonical['name']
                    self.stats['entities_merged'] += 1

        self.stats['groups_found'] += len(canonical_entities)

        return {
            'entities': canonical_entities,
            'mapping': canonical_map
        }

    def _choose_canonical(self, entities: List[Dict]) -> Dict:
        """Choose canonical entity from group."""
        if self.canonical_strategy == "longest":
            return max(entities, key=lambda e: len(e['name']))
        elif self.canonical_strategy == "most_frequent":
            # Count occurrences (if tracked)
            return entities[0]  # fallback to first
        else:  # "first"
            return entities[0]

    def _update_relationships(
        self,
        relationships: List[Dict],
        canonical_map: Dict[str, str]
    ) -> List[Dict]:
        """Update relationship sources/targets to canonical names."""
        updated = []

        for rel in relationships:
            new_rel = rel.copy()

            # Update source
            if rel.get('source') in canonical_map:
                new_rel['source'] = canonical_map[rel['source']]
                new_rel['source_canonical'] = True
                self.stats['relationships_updated'] += 1

            # Update target
            if rel.get('target') in canonical_map:
                new_rel['target'] = canonical_map[rel['target']]
                new_rel['target_canonical'] = True
                self.stats['relationships_updated'] += 1

            updated.append(new_rel)

        return updated
```

**Test coverage**:
```python
# tests/test_fuzzy_deduplicator.py

def test_merge_name_variations():
    """Test merging common name variations."""
    entities = [
        {"name": "Regen Network", "type": "ORGANIZATION"},
        {"name": "Regen", "type": "ORGANIZATION"},
        {"name": "REGEN", "type": "ORGANIZATION"},
    ]

    deduplicator = FuzzyDeduplicator(threshold=85)
    context = ProcessingContext(entities=entities)
    result = deduplicator.process(context)

    # Should merge to 1 canonical entity (longest name)
    assert len(result.entities) == 1
    assert result.entities[0]['name'] == "Regen Network"

def test_preserve_different_types():
    """Don't merge entities of different types."""
    entities = [
        {"name": "Regen", "type": "ORGANIZATION"},
        {"name": "Regen", "type": "PROJECT"},
    ]

    deduplicator = FuzzyDeduplicator()
    context = ProcessingContext(entities=entities)
    result = deduplicator.process(context)

    # Should NOT merge (different types)
    assert len(result.entities) == 2

def test_relationship_rewriting():
    """Test relationships updated to canonical names."""
    entities = [
        {"name": "Gregory Landua", "type": "PERSON"},
        {"name": "Gregory", "type": "PERSON"},
        {"name": "Regen Network", "type": "ORGANIZATION"},
        {"name": "Regen", "type": "ORGANIZATION"},
    ]

    relationships = [
        {"source": "Gregory", "predicate": "founded", "target": "Regen"}
    ]

    deduplicator = FuzzyDeduplicator()
    context = ProcessingContext(entities=entities, relationships=relationships)
    result = deduplicator.process(context)

    # Relationship should use canonical names
    assert result.relationships[0]['source'] == "Gregory Landua"
    assert result.relationships[0]['target'] == "Regen Network"
```

---

### Step 2: Update EntityQualityFilter (30 minutes)

**File**: `src/knowledge_graph/postprocessing/modules/entity_quality_filter.py`

**Changes**:
```python
# Add to STOP_WORDS set
STOP_WORDS = {
    # ... existing stop words ...

    # Generic roles (ADDED)
    "user", "users",
    "author", "authors",
    "contributor", "contributors",
    "member", "members",
    "validator", "validators",

    # Placeholders (ADDED)
    "unknown", "tbd", "tba",
    "n/a", "na",

    # Generic groups (ADDED)
    "team", "group", "community", "communities",
}
```

**Test**:
```python
def test_block_generic_roles():
    """Test blocking generic role names."""
    filter = EntityQualityFilter()

    assert filter._is_stop_word({"name": "User", "type": "PERSON"})
    assert filter._is_stop_word({"name": "unknown", "type": "ORGANIZATION"})
    assert filter._is_stop_word({"name": "Validator", "type": "PERSON"})
    assert filter._is_stop_word({"name": "community", "type": "ORGANIZATION"})
```

---

### Step 3: Integrate into Pipeline (30 minutes)

**File**: `src/knowledge_graph/config/pipeline_config.json`

**Update pipeline order**:
```json
{
  "modules": [
    {
      "name": "ConfidenceFilter",
      "class": "ConfidenceFilter",
      "config": {
        "entity_threshold": 0.70,
        "relationship_threshold": 0.80
      }
    },
    {
      "name": "CanonicalResolver",
      "class": "CanonicalResolver",
      "config": {
        "registry_path": "data/canonical_entities.json"
      }
    },
    {
      "name": "FuzzyDeduplicator",
      "class": "FuzzyDeduplicator",
      "config": {
        "threshold": 92,
        "canonical_strategy": "longest"
      }
    },
    {
      "name": "EntityQualityFilter",
      "class": "EntityQualityFilter",
      "config": {}
    },
    {
      "name": "ListSplitter",
      "class": "ListSplitter",
      "config": {
        "min_confidence": 0.75
      }
    },
    {
      "name": "OntologyNormalizer",
      "class": "OntologyNormalizer",
      "config": {}
    }
  ]
}
```

**Register module**:
```python
# src/knowledge_graph/postprocessing/modules/__init__.py

from .fuzzy_deduplicator import FuzzyDeduplicator

__all__ = [
    # ... existing ...
    "FuzzyDeduplicator",
]
```

---

### Step 4: Test on Sample Documents (1 hour)

**Test script**: `scripts/test_dedup_on_samples.py`

```python
#!/usr/bin/env python3
"""Test FuzzyDeduplicator on sample documents."""

import asyncio
from extraction.openai_extractor import OpenAIExtractor
from knowledge_graph.graph_integration import KnowledgeGraphIntegrator

SAMPLE_TEXTS = [
    # Document 1: "Regen Network"
    "Regen Network is building a blockchain for ecological data.",

    # Document 2: "Regen" (should merge with above)
    "Regen launched the ecocredit module in 2021.",

    # Document 3: "REGEN" (should merge)
    "The $REGEN token is used for governance.",

    # Document 4: Gregory variations
    "Gregory Landua founded the company in 2017.",
    "Gregory is the CEO of Regen Network.",
    "Gregory_RND posted on the forum today.",
]

async def test_dedup():
    """Test deduplication on sample documents."""
    extractor = OpenAIExtractor(model="gpt-4o-mini")
    kg = KnowledgeGraphIntegrator(store_type="memory", use_pipeline=True)

    all_entities = []
    all_relationships = []

    # Extract from each document
    for i, text in enumerate(SAMPLE_TEXTS):
        print(f"\n[{i+1}/{len(SAMPLE_TEXTS)}] Extracting from: {text[:50]}...")

        result = await extractor.extract_metadata(
            content=text,
            source_type="test",
            existing_metadata={"doc_id": i}
        )

        entities = result.get("entities", [])
        relationships = result.get("relationships", [])

        print(f"  Raw: {len(entities)} entities, {len(relationships)} relationships")

        all_entities.extend(entities)
        all_relationships.extend(relationships)

    # Process through pipeline
    print(f"\n{'='*60}")
    print(f"Processing through pipeline...")
    print(f"{'='*60}")

    processed = kg.process_entities_batch(all_entities, all_relationships)

    # Analyze results
    passed_entities = [e for e in processed if not e.get('blocked')]

    print(f"\n{'='*60}")
    print(f"RESULTS")
    print(f"{'='*60}")
    print(f"Total raw entities: {len(all_entities)}")
    print(f"After pipeline: {len(passed_entities)}")
    print(f"Blocked: {len(processed) - len(passed_entities)}")

    # Group by name to check deduplication
    from collections import Counter
    name_counts = Counter([e['name'] for e in passed_entities])

    print(f"\nEntity name distribution:")
    for name, count in name_counts.most_common(10):
        print(f"  {name}: {count}")

    # Check for expected merges
    print(f"\n{'='*60}")
    print(f"DEDUPLICATION CHECK")
    print(f"{'='*60}")

    # Should have merged "Regen Network" / "Regen" / "REGEN"
    regen_variants = [n for n in name_counts.keys() if "regen" in n.lower()]
    print(f"Regen variants found: {regen_variants}")
    print(f"  Expected: ['Regen Network'] (merged)")

    # Should have merged Gregory variants
    gregory_variants = [n for n in name_counts.keys() if "gregory" in n.lower()]
    print(f"Gregory variants found: {gregory_variants}")
    print(f"  Expected: ['Gregory Landua'] (merged)")

    return processed

if __name__ == "__main__":
    asyncio.run(test_dedup())
```

**Run test**:
```bash
cd /opt/projects/koi-processor
source venv/bin/activate
source .env
python3 scripts/test_dedup_on_samples.py
```

**Success criteria**:
- ✅ "Regen Network" / "Regen" / "REGEN" → merged to "Regen Network"
- ✅ "Gregory Landua" / "Gregory" / "Gregory_RND" → merged to "Gregory Landua"
- ✅ "User", "unknown", "Validator" → blocked by EntityQualityFilter
- ✅ Relationships rewritten to use canonical names

---

### Step 5: Run Full Pipeline Tests (30 minutes)

```bash
cd /opt/projects/koi-processor
pytest tests/test_pipeline_modules.py tests/test_fuzzy_deduplicator.py -v
```

**All 121+ tests must pass** before proceeding.

---

## Dependencies

**Install fuzzywuzzy**:
```bash
cd /opt/projects/koi-processor
source venv/bin/activate
pip install fuzzywuzzy python-Levenshtein
pip freeze > requirements.txt
```

**Optional (for Phase 3 - Pronoun Resolution)**:
```bash
pip install spacy
python -m spacy download en_core_web_sm
```

---

## Validation Before Resuming Extraction

**Before resuming GitHub extraction**, validate on recent extractions:

```bash
# Query database for entities from last 300 GitHub docs
ssh darren@202.61.196.119 "cd /opt/projects/koi-processor && PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza -c \"
SELECT
  json_extract_path_text(result, 'entities') as entities
FROM koi_kg_extractions
WHERE metadata->>'source' = 'github'
ORDER BY created_at DESC
LIMIT 10
\""
```

**Check for duplicates** in these 300 docs:
- Count "Regen" variations (should be merged to "Regen Network")
- Count "Gregory" variations (should be merged to "Gregory Landua")
- Verify no "User", "unknown", "Validator" entities

**If validation passes** → Resume extraction

---

## Deliverables

1. ✅ **FuzzyDeduplicator module** (`src/knowledge_graph/postprocessing/modules/fuzzy_deduplicator.py`)
2. ✅ **Updated EntityQualityFilter** (enhanced stop words)
3. ✅ **Pipeline integration** (pipeline_config.json updated)
4. ✅ **Test suite** (15+ tests for dedup module)
5. ✅ **Validation report** (dedup working on sample docs)
6. ✅ **Dependencies installed** (fuzzywuzzy)

---

## Success Criteria

**Before marking complete**:
- [ ] All 121+ pipeline tests passing
- [ ] Sample test shows < 5% duplicate entities (down from ~35%)
- [ ] "Regen Network" / "Regen" / "REGEN" → merged ✅
- [ ] "Gregory Landua" / "Gregory" / "Gregory_RND" → merged ✅
- [ ] "User", "unknown", "Validator" → blocked ✅
- [ ] Relationships correctly rewritten to canonical names
- [ ] No regressions in existing modules (97%+ pass rate maintained)

---

## Next Steps After Completion

1. Update PROMPT_18 with new dedup-enabled pipeline
2. Resume GitHub extraction from checkpoint (300/4,710)
3. Monitor deduplication metrics during extraction
4. Generate final report comparing before/after dedup stats

---

## Reference Files

**yonearth-gaia-chatbot** (for reference):
- `ssh claudeuser@152.53.37.180`
- `~/yonearth-gaia-chatbot/src/knowledge_graph/postprocessing/universal/entity_deduplicator.py`
- `~/yonearth-gaia-chatbot/src/knowledge_graph/graph/graph_builder.py`

**koi-processor** (to modify):
- `ssh darren@202.61.196.119`
- `/opt/projects/koi-processor/src/knowledge_graph/postprocessing/modules/`

---

**Priority**: CRITICAL - BLOCKING
**Status**: Ready to implement
**Estimated completion**: 3-4 hours
