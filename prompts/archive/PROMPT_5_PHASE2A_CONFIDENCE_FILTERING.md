# PROMPT 5: Phase 2a - Confidence Filtering Implementation

**Agent Role**: Implementation Agent
**Phase**: 2a - Confidence Threshold Filtering
**Duration**: 1-2 weeks
**Difficulty**: Medium
**Prerequisites**: Phase 1 complete (99.7% quality score achieved)

---

## Context

You are continuing work on the Regen KOI knowledge graph quality improvement project. Phase 1 (Week 1) was a massive success:

- ✅ Quality score improved: 62% → 99.7%
- ✅ 616 low-quality entities removed
- ✅ Canonical registry expanded to 88 entries, 194 aliases
- ✅ EntityQualityFilter deployed (96.6% accuracy)
- ✅ Production deployment complete with zero errors

**Current Challenge**: The quality review identified 3,021 entities with `low_confidence` scores (81.8% of flagged entities). While pattern-based filtering caught technical patterns and generic nouns, confidence-based filtering will catch semantically weak extractions.

**Your Mission**: Implement confidence threshold filtering in the extraction pipeline to block low-confidence entities before they enter the knowledge graph.

---

## Objective

Add confidence score filtering to the knowledge graph integration layer, preventing entities and relationships below confidence thresholds from being inserted.

**Expected Outcome**:
- Confidence filtering operational in production
- 5-10% additional quality improvement
- Configurable thresholds (easy to tune)
- Test coverage for confidence filtering
- Minimal false positives (<1%)

---

## Environment

**Project Location**: `/Users/darrenzal/projects/RegenAI/koi-processor/`

**Key Files**:
```
koi-processor/
├── src/knowledge_graph/
│   ├── graph_integration.py          # Main integration layer (MODIFY THIS)
│   ├── improvements/
│   │   ├── entity_quality_filter.py  # Existing filter (reference)
│   │   └── canonical_resolver.py     # Existing resolver (reference)
│   └── config/
│       └── quality_config.json       # NEW: Add confidence thresholds
├── tests/
│   └── test_confidence_filtering.py  # NEW: Add tests
└── .env                               # Database credentials
```

**Database**:
- **Production**: `darren@202.61.196.119` (PostgreSQL port 5433, database: eliza)
- **Local**: `localhost:5433` (if available)
- **Credentials**: In `.env` file (`POSTGRES_URL`)

**Testing Strategy**:
- Unit tests first (mock data)
- Integration tests on sample extraction (10-20 documents)
- Production deployment with monitoring

---

## Tasks

### Task 1: Create Confidence Configuration (30 min)

**Objective**: Add configurable confidence thresholds

**Implementation**:

Create `src/knowledge_graph/config/quality_config.json`:
```json
{
  "version": "1.0.0",
  "confidence_thresholds": {
    "entity_min_confidence": 0.70,
    "relationship_min_confidence": 0.80,
    "strict_mode": false,
    "allow_null_confidence": true
  },
  "confidence_policy": {
    "comment": "Entities below min_confidence are blocked. Relationships require higher confidence.",
    "null_handling": "allow_null_confidence=true means entities without confidence scores are allowed (backward compatibility)",
    "strict_mode": "strict_mode=true requires all entities to have confidence scores"
  }
}
```

**Acceptance Criteria**:
- [x] Config file created with default thresholds
- [x] JSON is valid and well-documented
- [x] Thresholds are conservative (not too aggressive)

---

### Task 2: Add ConfidenceFilter Class (1-2 hours)

**Objective**: Create a reusable confidence filtering module

**Implementation**:

Create `src/knowledge_graph/improvements/confidence_filter.py`:

```python
"""
Confidence-based filtering for knowledge graph entities and relationships.

Filters out entities and relationships with confidence scores below configurable thresholds.
This complements pattern-based filtering (EntityQualityFilter) by catching semantically weak extractions.

Usage:
    from src.knowledge_graph.improvements import ConfidenceFilter

    filter = ConfidenceFilter(
        entity_threshold=0.70,
        relationship_threshold=0.80
    )

    # Check entity
    is_valid, reason = filter.filter_entity(
        name="Gregory Landua",
        entity_type="PERSON",
        confidence=0.85
    )
    # Returns: (True, None)

    # Check low-confidence entity
    is_valid, reason = filter.filter_entity(
        name="some ambiguous entity",
        entity_type="CONCEPT",
        confidence=0.45
    )
    # Returns: (False, "confidence_too_low")
"""

from typing import Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class ConfidenceFilter:
    """Filters entities and relationships based on confidence scores."""

    def __init__(
        self,
        entity_threshold: float = 0.70,
        relationship_threshold: float = 0.80,
        allow_null: bool = True,
        strict_mode: bool = False
    ):
        """
        Initialize confidence filter.

        Args:
            entity_threshold: Minimum confidence for entities (0.0-1.0)
            relationship_threshold: Minimum confidence for relationships
            allow_null: If True, entities without confidence are allowed
            strict_mode: If True, all entities must have confidence scores
        """
        self.entity_threshold = entity_threshold
        self.relationship_threshold = relationship_threshold
        self.allow_null = allow_null
        self.strict_mode = strict_mode

        # Statistics
        self.stats = {
            'total_checked': 0,
            'blocked_low_confidence': 0,
            'blocked_missing_confidence': 0,
            'allowed': 0
        }

    def filter_entity(
        self,
        name: str,
        entity_type: str,
        confidence: Optional[float] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if entity meets confidence threshold.

        Args:
            name: Entity name
            entity_type: Entity type
            confidence: Confidence score (0.0-1.0) or None

        Returns:
            Tuple of (is_valid, block_reason)
            - (True, None) if entity passes
            - (False, reason) if entity should be blocked
        """
        self.stats['total_checked'] += 1

        # Handle missing confidence
        if confidence is None:
            if self.strict_mode:
                self.stats['blocked_missing_confidence'] += 1
                return False, "missing_confidence_strict_mode"
            elif self.allow_null:
                self.stats['allowed'] += 1
                return True, None
            else:
                self.stats['blocked_missing_confidence'] += 1
                return False, "missing_confidence"

        # Validate confidence range
        if not (0.0 <= confidence <= 1.0):
            logger.warning(f"Invalid confidence value: {confidence} for entity '{name}'")
            self.stats['blocked_low_confidence'] += 1
            return False, "invalid_confidence_range"

        # Check threshold
        if confidence < self.entity_threshold:
            self.stats['blocked_low_confidence'] += 1
            return False, "confidence_too_low"

        self.stats['allowed'] += 1
        return True, None

    def filter_relationship(
        self,
        source: str,
        predicate: str,
        target: str,
        confidence: Optional[float] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if relationship meets confidence threshold.

        Args:
            source: Source entity name
            predicate: Relationship predicate
            target: Target entity name
            confidence: Confidence score or None

        Returns:
            Tuple of (is_valid, block_reason)
        """
        self.stats['total_checked'] += 1

        # Handle missing confidence
        if confidence is None:
            if self.strict_mode:
                self.stats['blocked_missing_confidence'] += 1
                return False, "missing_confidence_strict_mode"
            elif self.allow_null:
                self.stats['allowed'] += 1
                return True, None
            else:
                self.stats['blocked_missing_confidence'] += 1
                return False, "missing_confidence"

        # Check threshold (relationships require higher confidence)
        if confidence < self.relationship_threshold:
            self.stats['blocked_low_confidence'] += 1
            return False, "confidence_too_low"

        self.stats['allowed'] += 1
        return True, None

    def get_stats(self) -> dict:
        """Get filtering statistics."""
        total = self.stats['total_checked']
        if total == 0:
            return self.stats

        return {
            **self.stats,
            'block_rate': self.stats['blocked_low_confidence'] / total,
            'allow_rate': self.stats['allowed'] / total
        }

    def reset_stats(self):
        """Reset statistics counters."""
        self.stats = {
            'total_checked': 0,
            'blocked_low_confidence': 0,
            'blocked_missing_confidence': 0,
            'allowed': 0
        }


# Export
__all__ = ['ConfidenceFilter']
```

**Acceptance Criteria**:
- [x] ConfidenceFilter class implemented
- [x] Handles None/null confidence gracefully
- [x] Separate thresholds for entities vs relationships
- [x] Statistics tracking
- [x] Clear documentation and examples

---

### Task 3: Integrate into Graph Integration Layer (2-3 hours)

**Objective**: Add confidence filtering to `graph_integration.py`

**Current Code** (simplified):
```python
# src/knowledge_graph/graph_integration.py (current)

class KnowledgeGraphIntegrator:
    def __init__(self, enable_quality_controls=True):
        if enable_quality_controls:
            self.entity_filter = EntityQualityFilter(FilterConfig())
            self.canonical_resolver = CanonicalResolver()

        self.quality_stats = {
            'total_processed': 0,
            'blocked': 0,
            'canonicalized': 0,
            'inserted': 0
        }

    def process_entity(self, name: str, entity_type: str, confidence: float = None, **kwargs):
        """Process entity with quality controls."""
        self.quality_stats['total_processed'] += 1

        # Step 1: Canonical resolution
        canonical_name, was_resolved = self.canonical_resolver.resolve(name, entity_type)
        if was_resolved:
            self.quality_stats['canonicalized'] += 1
            name = canonical_name

        # Step 2: Quality filter
        is_valid, reasons = self.entity_filter.filter_with_reasons(name, entity_type)
        if not is_valid:
            self.quality_stats['blocked'] += 1
            return None

        # Step 3: Insert to graph
        entity = self._insert_entity(name, entity_type, confidence=confidence, **kwargs)
        self.quality_stats['inserted'] += 1
        return entity
```

**Modified Code** (add confidence filtering):
```python
# src/knowledge_graph/graph_integration.py (modified)

import json
from pathlib import Path
from src.knowledge_graph.improvements import ConfidenceFilter

class KnowledgeGraphIntegrator:
    def __init__(self, enable_quality_controls=True):
        if enable_quality_controls:
            self.entity_filter = EntityQualityFilter(FilterConfig())
            self.canonical_resolver = CanonicalResolver()

            # NEW: Load confidence config and initialize filter
            config_path = Path(__file__).parent / 'config' / 'quality_config.json'
            with open(config_path) as f:
                config = json.load(f)

            conf_thresholds = config['confidence_thresholds']
            self.confidence_filter = ConfidenceFilter(
                entity_threshold=conf_thresholds['entity_min_confidence'],
                relationship_threshold=conf_thresholds['relationship_min_confidence'],
                allow_null=conf_thresholds['allow_null_confidence'],
                strict_mode=conf_thresholds['strict_mode']
            )
        else:
            self.confidence_filter = None

        self.quality_stats = {
            'total_processed': 0,
            'blocked_by_pattern': 0,        # Pattern-based filter
            'blocked_by_confidence': 0,      # NEW: Confidence filter
            'canonicalized': 0,
            'inserted': 0
        }

    def process_entity(self, name: str, entity_type: str, confidence: float = None, **kwargs):
        """Process entity with quality controls including confidence filtering."""
        self.quality_stats['total_processed'] += 1

        # Step 1: Confidence filter (early exit)
        if self.confidence_filter:
            is_valid, reason = self.confidence_filter.filter_entity(name, entity_type, confidence)
            if not is_valid:
                self.quality_stats['blocked_by_confidence'] += 1
                logger.debug(f"Blocked low-confidence entity '{name}' ({entity_type}): {reason}")
                return None

        # Step 2: Canonical resolution
        canonical_name, was_resolved = self.canonical_resolver.resolve(name, entity_type)
        if was_resolved:
            self.quality_stats['canonicalized'] += 1
            name = canonical_name

        # Step 3: Pattern-based quality filter
        is_valid, reasons = self.entity_filter.filter_with_reasons(name, entity_type)
        if not is_valid:
            self.quality_stats['blocked_by_pattern'] += 1
            logger.debug(f"Blocked entity '{name}': {', '.join(reasons)}")
            return None

        # Step 4: Insert to graph
        entity = self._insert_entity(name, entity_type, confidence=confidence, **kwargs)
        self.quality_stats['inserted'] += 1
        return entity

    def process_relationship(self, source: str, predicate: str, target: str, confidence: float = None, **kwargs):
        """Process relationship with confidence filtering."""

        # NEW: Check confidence for relationships
        if self.confidence_filter:
            is_valid, reason = self.confidence_filter.filter_relationship(
                source, predicate, target, confidence
            )
            if not is_valid:
                logger.debug(f"Blocked low-confidence relationship: ({source})-[{predicate}]->({target}): {reason}")
                return None

        # Existing relationship insertion logic
        return self._insert_relationship(source, predicate, target, confidence=confidence, **kwargs)

    def get_quality_stats(self) -> dict:
        """Get comprehensive quality statistics."""
        stats = self.quality_stats.copy()

        # Add confidence filter stats
        if self.confidence_filter:
            stats['confidence_filter'] = self.confidence_filter.get_stats()

        # Calculate rates
        total = stats['total_processed']
        if total > 0:
            stats['blocked_total'] = stats['blocked_by_pattern'] + stats['blocked_by_confidence']
            stats['block_rate'] = stats['blocked_total'] / total
            stats['insert_rate'] = stats['inserted'] / total

        return stats
```

**Acceptance Criteria**:
- [x] ConfidenceFilter integrated into graph_integration.py
- [x] Confidence checked before pattern filtering (early exit for performance)
- [x] Relationship confidence filtering implemented
- [x] Statistics tracking updated
- [x] Backward compatible (allow_null=true by default)

---

### Task 4: Write Comprehensive Tests (2-3 hours)

**Objective**: Ensure confidence filtering works correctly

**Implementation**:

Create `tests/test_confidence_filtering.py`:

```python
"""
Tests for confidence-based filtering.

Tests cover:
- ConfidenceFilter class behavior
- Graph integration with confidence filtering
- Edge cases (None, invalid ranges, etc.)
- Statistics tracking
"""

import pytest
from src.knowledge_graph.improvements import ConfidenceFilter


class TestConfidenceFilter:
    """Test ConfidenceFilter class."""

    def test_entity_above_threshold(self):
        """Entity with confidence above threshold should pass."""
        filter = ConfidenceFilter(entity_threshold=0.70)
        is_valid, reason = filter.filter_entity("Gregory Landua", "PERSON", 0.85)

        assert is_valid is True
        assert reason is None

    def test_entity_below_threshold(self):
        """Entity with confidence below threshold should be blocked."""
        filter = ConfidenceFilter(entity_threshold=0.70)
        is_valid, reason = filter.filter_entity("ambiguous entity", "CONCEPT", 0.45)

        assert is_valid is False
        assert reason == "confidence_too_low"

    def test_entity_at_threshold(self):
        """Entity at exactly threshold should pass."""
        filter = ConfidenceFilter(entity_threshold=0.70)
        is_valid, reason = filter.filter_entity("Regen Network", "ORGANIZATION", 0.70)

        assert is_valid is True

    def test_entity_none_confidence_allow_null(self):
        """Entity with None confidence should pass if allow_null=True."""
        filter = ConfidenceFilter(entity_threshold=0.70, allow_null=True)
        is_valid, reason = filter.filter_entity("Entity", "CONCEPT", None)

        assert is_valid is True
        assert reason is None

    def test_entity_none_confidence_strict_mode(self):
        """Entity with None confidence should fail in strict mode."""
        filter = ConfidenceFilter(entity_threshold=0.70, strict_mode=True, allow_null=False)
        is_valid, reason = filter.filter_entity("Entity", "CONCEPT", None)

        assert is_valid is False
        assert "missing_confidence" in reason

    def test_relationship_higher_threshold(self):
        """Relationships should use higher threshold."""
        filter = ConfidenceFilter(
            entity_threshold=0.70,
            relationship_threshold=0.80
        )

        # 0.75 is above entity threshold but below relationship threshold
        is_valid, reason = filter.filter_relationship(
            "Entity A", "relates_to", "Entity B", 0.75
        )

        assert is_valid is False
        assert reason == "confidence_too_low"

    def test_invalid_confidence_range(self):
        """Confidence outside 0-1 range should be blocked."""
        filter = ConfidenceFilter(entity_threshold=0.70)

        # Test > 1.0
        is_valid, reason = filter.filter_entity("Entity", "CONCEPT", 1.5)
        assert is_valid is False

        # Test < 0.0
        is_valid, reason = filter.filter_entity("Entity", "CONCEPT", -0.5)
        assert is_valid is False

    def test_statistics_tracking(self):
        """Statistics should be tracked correctly."""
        filter = ConfidenceFilter(entity_threshold=0.70)

        # Process multiple entities
        filter.filter_entity("Entity 1", "CONCEPT", 0.85)  # Pass
        filter.filter_entity("Entity 2", "CONCEPT", 0.45)  # Block
        filter.filter_entity("Entity 3", "CONCEPT", None)  # Pass (allow_null)
        filter.filter_entity("Entity 4", "CONCEPT", 0.90)  # Pass

        stats = filter.get_stats()

        assert stats['total_checked'] == 4
        assert stats['blocked_low_confidence'] == 1
        assert stats['allowed'] == 3
        assert stats['block_rate'] == 0.25


class TestGraphIntegrationWithConfidence:
    """Test graph integration with confidence filtering."""

    def test_low_confidence_entity_blocked(self):
        """Low-confidence entity should not be inserted."""
        # Mock or test with actual integrator
        # This would require setting up test database or mocking
        pass

    def test_high_confidence_entity_inserted(self):
        """High-confidence entity should be inserted."""
        pass

    def test_statistics_updated(self):
        """Quality stats should reflect confidence filtering."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

**Additional Tests** (integration tests):

Create `tests/test_confidence_integration.py`:
```python
"""Integration tests for confidence filtering with sample extraction."""

import asyncio
from src.knowledge_graph.graph_integration import KnowledgeGraphIntegrator


async def test_sample_extraction():
    """Test confidence filtering on sample document extraction."""

    integrator = KnowledgeGraphIntegrator(enable_quality_controls=True)

    # Sample entities with varying confidence
    test_entities = [
        ("Gregory Landua", "PERSON", 0.95),      # High confidence - should insert
        ("Regen Network", "ORGANIZATION", 0.88),  # High confidence - should insert
        ("ambiguous term", "CONCEPT", 0.45),      # Low confidence - should block
        ("some project", "PROJECT", 0.62),        # Below threshold - should block
        ("Carbon Credits", "CONCEPT", 0.91),      # High confidence - should insert
        ("it", "PERSON", 0.85),                   # High confidence but pattern blocked
    ]

    for name, entity_type, confidence in test_entities:
        integrator.process_entity(name, entity_type, confidence=confidence)

    # Check statistics
    stats = integrator.get_quality_stats()

    print("\n=== Integration Test Results ===")
    print(f"Total processed: {stats['total_processed']}")
    print(f"Blocked by confidence: {stats['blocked_by_confidence']}")
    print(f"Blocked by pattern: {stats['blocked_by_pattern']}")
    print(f"Inserted: {stats['inserted']}")
    print(f"Block rate: {stats['block_rate']:.2%}")

    # Assertions
    assert stats['total_processed'] == 6
    assert stats['blocked_by_confidence'] == 2  # ambiguous term, some project
    assert stats['blocked_by_pattern'] == 1      # "it"
    assert stats['inserted'] == 3                # Gregory, Regen, Carbon Credits

    print("\n✅ Integration test passed!")


if __name__ == "__main__":
    asyncio.run(test_sample_extraction())
```

**Acceptance Criteria**:
- [x] 15+ unit tests for ConfidenceFilter
- [x] Integration tests with sample data
- [x] Edge cases covered (None, invalid ranges, boundaries)
- [x] All tests passing

---

### Task 5: Test on Sample Extraction (2-3 hours)

**Objective**: Validate confidence filtering on real extraction data

**Implementation**:

Create `scripts/test_confidence_filtering.py`:

```python
"""
Test confidence filtering on sample extraction.

Runs a small-scale extraction (10-20 documents) with confidence filtering enabled,
then analyzes the results to ensure:
- No false positives (valid entities blocked)
- Appropriate block rate (5-15%)
- Quality improvement measurable
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parents[1]
sys.path.insert(0, str(project_root))

from src.knowledge_graph.graph_integration import KnowledgeGraphIntegrator


async def test_on_sample_extraction():
    """Test confidence filtering on sample document extraction."""

    print("=" * 70)
    print("CONFIDENCE FILTERING TEST - SAMPLE EXTRACTION")
    print("=" * 70)
    print()

    # Initialize integrator with quality controls
    integrator = KnowledgeGraphIntegrator(enable_quality_controls=True)

    # TODO: Run actual extraction on 10-20 documents
    # For now, simulate with sample data

    print("Extracting from sample documents...")
    print()

    # Simulate extraction results
    # In production, this would come from actual extraction
    sample_entities = [
        # High confidence entities (should insert)
        ("Gregory Landua", "PERSON", 0.95),
        ("Regen Network", "ORGANIZATION", 0.92),
        ("Carbon Credits", "CONCEPT", 0.88),
        ("Cosmos SDK", "PROJECT", 0.91),
        ("Will Szal", "PERSON", 0.89),

        # Medium-high confidence (should insert)
        ("Ecocredit Module", "PROJECT", 0.78),
        ("Regenerative Agriculture", "CONCEPT", 0.75),
        ("Osmosis", "ORGANIZATION", 0.82),

        # Low confidence (should block)
        ("some term", "CONCEPT", 0.45),
        ("unclear entity", "ORGANIZATION", 0.52),
        ("vague concept", "CONCEPT", 0.38),

        # Pattern-blocked (even with high confidence)
        ("it", "PERSON", 0.95),
        ("we", "PERSON", 0.88),
        ("localhost:3000", "PROJECT", 0.75),
    ]

    print(f"Processing {len(sample_entities)} extracted entities...\n")

    for name, entity_type, confidence in sample_entities:
        result = integrator.process_entity(name, entity_type, confidence=confidence)

        if result is None:
            status = "❌ BLOCKED"
        else:
            status = "✅ INSERTED"

        print(f"  {status}: {name:<30s} ({entity_type:<15s}, conf={confidence:.2f})")

    # Get statistics
    stats = integrator.get_quality_stats()

    print("\n" + "=" * 70)
    print("FILTERING STATISTICS")
    print("=" * 70)
    print(f"\nTotal processed:         {stats['total_processed']:>3}")
    print(f"Blocked by confidence:   {stats['blocked_by_confidence']:>3}")
    print(f"Blocked by pattern:      {stats['blocked_by_pattern']:>3}")
    print(f"Canonicalized:           {stats['canonicalized']:>3}")
    print(f"Inserted:                {stats['inserted']:>3}")
    print(f"\nBlock rate:              {stats['block_rate']:.1%}")
    print(f"Insert rate:             {stats['insert_rate']:.1%}")

    # Analyze confidence filter stats
    if 'confidence_filter' in stats:
        conf_stats = stats['confidence_filter']
        print("\n" + "-" * 70)
        print("CONFIDENCE FILTER BREAKDOWN")
        print("-" * 70)
        print(f"Total checked:           {conf_stats['total_checked']:>3}")
        print(f"Blocked (low conf):      {conf_stats['blocked_low_confidence']:>3}")
        print(f"Blocked (missing conf):  {conf_stats['blocked_missing_confidence']:>3}")
        print(f"Allowed:                 {conf_stats['allowed']:>3}")

    # Validate results
    print("\n" + "=" * 70)
    print("VALIDATION")
    print("=" * 70)

    checks = []

    # Check 1: Block rate should be reasonable (5-20%)
    if 0.05 <= stats['block_rate'] <= 0.30:
        checks.append(("✅", "Block rate is reasonable (5-30%)"))
    else:
        checks.append(("⚠️", f"Block rate is {stats['block_rate']:.1%} (expected 5-30%)"))

    # Check 2: Some entities should be inserted
    if stats['inserted'] > 0:
        checks.append(("✅", f"Entities inserted ({stats['inserted']})"))
    else:
        checks.append(("❌", "No entities inserted - filtering too aggressive"))

    # Check 3: Confidence filtering should block some entities
    if stats['blocked_by_confidence'] > 0:
        checks.append(("✅", f"Confidence filter working ({stats['blocked_by_confidence']} blocked)"))
    else:
        checks.append(("⚠️", "Confidence filter didn't block anything - threshold may be too low"))

    # Check 4: Pattern filter should still work
    if stats['blocked_by_pattern'] > 0:
        checks.append(("✅", f"Pattern filter working ({stats['blocked_by_pattern']} blocked)"))
    else:
        checks.append(("ℹ️", "Pattern filter didn't block anything in this sample"))

    for status, message in checks:
        print(f"{status} {message}")

    print("\n" + "=" * 70)
    print()

    return stats


if __name__ == "__main__":
    asyncio.run(test_on_sample_extraction())
```

**Run the test**:
```bash
cd /Users/darrenzal/projects/RegenAI/koi-processor
python scripts/test_confidence_filtering.py
```

**Acceptance Criteria**:
- [x] Sample extraction test runs successfully
- [x] Block rate is 5-20% (not too aggressive)
- [x] No obvious false positives
- [x] Statistics are accurate
- [x] Both filters (confidence + pattern) working together

---

### Task 6: Production Deployment (1-2 hours)

**Objective**: Deploy confidence filtering to production

**Pre-Deployment Checklist**:
- [ ] All tests passing (unit + integration)
- [ ] Sample extraction validated
- [ ] Configuration reviewed
- [ ] Backup strategy confirmed

**Deployment Steps**:

```bash
# 1. Sync files to production
scp src/knowledge_graph/config/quality_config.json darren@202.61.196.119:/opt/projects/koi-processor/src/knowledge_graph/config/
scp src/knowledge_graph/improvements/confidence_filter.py darren@202.61.196.119:/opt/projects/koi-processor/src/knowledge_graph/improvements/
scp src/knowledge_graph/graph_integration.py darren@202.61.196.119:/opt/projects/koi-processor/src/knowledge_graph/

# 2. Sync tests
scp tests/test_confidence_filtering.py darren@202.61.196.119:/opt/projects/koi-processor/tests/

# 3. SSH to production and run tests
ssh darren@202.61.196.119
cd /opt/projects/koi-processor
python3 -m pytest tests/test_confidence_filtering.py -v

# 4. Run sample extraction test
python3 scripts/test_confidence_filtering.py

# 5. Monitor first real extraction
# (Check logs for blocked entities, false positives)
```

**Monitoring** (first 24 hours):
- Check blocked entity logs
- Review any false positives
- Adjust thresholds if needed (edit quality_config.json)
- Monitor block rate (should be 5-15%)

**Acceptance Criteria**:
- [x] Confidence filtering deployed to production
- [x] Tests passing on production server
- [x] No errors in production logs
- [x] Block rate within expected range (5-15%)
- [x] No critical false positives

---

### Task 7: Generate Phase 2a Report (30 min)

**Objective**: Document Phase 2a completion

**Implementation**:

Create `reports/PHASE2A_CONFIDENCE_FILTERING_REPORT.md`:

Include:
1. **Executive Summary**: What was achieved
2. **Implementation Details**: Code changes, configuration
3. **Testing Results**: Unit tests, integration tests, sample extraction
4. **Production Deployment**: Deployment process, results
5. **Impact Analysis**: Before/after statistics, quality improvement
6. **Lessons Learned**: Challenges, solutions, recommendations
7. **Next Steps**: Phase 2b preview

**Acceptance Criteria**:
- [x] Comprehensive report generated
- [x] Includes statistics and metrics
- [x] Documents configuration and usage
- [x] Provides rollback instructions if needed

---

## Success Criteria

### Functionality
- [x] ConfidenceFilter class implemented and tested
- [x] Integrated into graph_integration.py
- [x] Configuration file created
- [x] Works alongside existing filters (pattern-based)

### Quality
- [x] 15+ unit tests, all passing
- [x] Integration tests passing
- [x] Block rate: 5-15% (not too aggressive)
- [x] No false positives on sample data

### Production
- [x] Deployed to production successfully
- [x] Tests passing on production server
- [x] Monitoring shows expected behavior
- [x] No errors in first 24 hours

### Documentation
- [x] Code well-commented
- [x] Phase 2a report generated
- [x] Configuration documented
- [x] Usage examples provided

---

## Expected Deliverables

1. **Code**:
   - `src/knowledge_graph/config/quality_config.json`
   - `src/knowledge_graph/improvements/confidence_filter.py`
   - Modified: `src/knowledge_graph/graph_integration.py`

2. **Tests**:
   - `tests/test_confidence_filtering.py` (15+ tests)
   - `tests/test_confidence_integration.py`
   - `scripts/test_confidence_filtering.py`

3. **Reports**:
   - `reports/PHASE2A_CONFIDENCE_FILTERING_REPORT.md`

4. **Statistics** (before/after):
   - Block rate improvement
   - Quality score change
   - False positive rate

---

## Grading Rubric

**A+ (95-100)**:
- All tests passing
- Block rate 5-15%
- Zero false positives
- Clean production deployment
- Quality score improved by 2-5%

**A (90-94)**:
- All tests passing
- Block rate within range
- <3 false positives
- Production deployment successful

**B (80-89)**:
- Most tests passing
- Block rate slightly off target
- Some false positives (need tuning)

**C (70-79)**:
- Basic functionality working
- Needs threshold adjustment
- Several false positives

---

## Resources

### Reference Files
- Phase 1 Report: `reports/PHASE1_IMPLEMENTATION_REPORT.md`
- Week 1 Report: `reports/WEEK1_PRODUCTION_DEPLOYMENT_REPORT.md`
- EntityQualityFilter: `src/knowledge_graph/improvements/entity_quality_filter.py`
- CanonicalResolver: `src/knowledge_graph/improvements/canonical_resolver.py`

### Quality Review Data
- Quality issues CSV: `reports/kg_quality_review_20251208/entity_quality_issues.csv`
- 3,021 low_confidence entities identified

### Database
- Production: `darren@202.61.196.119`
- PostgreSQL: port 5433, database: eliza
- Graph: regen_graph

---

## Notes

- **Backward Compatibility**: `allow_null_confidence=true` by default ensures existing extractions without confidence scores still work
- **Threshold Tuning**: Start conservative (0.70/0.80), tighten if needed
- **Performance**: Confidence check is fast (no complex computation), early exit for low-confidence entities
- **Monitoring**: First 24 hours critical for identifying false positives

---

**When Complete**: Generate Phase 2a report and notify user. If successful, user can proceed to Phase 2b (Pipeline Framework) or Phase 3 (Fuzzy Deduplication).

**Good luck!** 🚀
