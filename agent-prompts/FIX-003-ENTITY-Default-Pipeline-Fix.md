# Agent Task: FIX-003 - Stop ENTITY-Default Inserts + Fix Pipeline Ordering

## Context

FIX-001 (namespace/URI) and FIX-002 (extractor unification) are DEPLOYED. FIX-003 is the final P1 fix before re-extraction.

**Problem:** The dominant driver of generic `ENTITY` overuse is:
1. Relationship-driven entity creation defaults to `entity_type="ENTITY"` when type is missing
2. Pipeline ordering runs quality filters BEFORE type normalization, so `HumanActor` bypasses PERSON-specific checks
3. Missing placeholder/min-length validation lets garbage entities through

**Evidence:**
- 15,558 generic ENTITY rows with empty metadata
- 8+ single-character entities (should be filtered)
- 265+ placeholder-pattern entities ("Unknown", "Anonymous User", "TBD")

## Objective

Implement FIX-003 to:
1. Fix pipeline ordering so `OntologyNormalizer` runs BEFORE `ListSplitter` and `EntityQualityFilter`
2. Add min-length validation (single chars filtered)
3. Expand placeholder detection patterns
4. Stop relationship-driven ENTITY creation with predicate-based type inference
5. Add metrics/logging for ENTITY fallback occurrences

## Repo Paths

- Server: /opt/projects/koi-processor (production)
- Local: /Users/darrenzal/projects/RegenAI/koi-processor
- Documentation: /Users/darrenzal/projects/RegenAI/knowledge-graph-review-2025-12.md

## Guardrails

- Implement code + tests only. Do NOT run full re-extraction as part of FIX-003.
- Do NOT run against production DB/Fuseki unless explicitly instructed.

## Read First (required)

| File | Purpose |
|------|---------|
| `src/knowledge_graph/graph_integration.py:773-850` | `_add_relationship()` - where ENTITY defaults happen (lines 820-821) |
| `src/knowledge_graph/graph_integration.py:971-1010` | `_resolve_entity_for_relationship()` - entity resolution for relationships |
| `src/knowledge_graph/config/pipeline_config.json` | Pipeline module ordering (uses `config["pipeline"]["modules"]` with `m["name"]`) |
| `src/knowledge_graph/postprocessing/modules/entity_quality_module.py` | EntityQualityFilter wrapper module |
| `src/knowledge_graph/improvements/entity_quality_filter.py` | Underlying quality filter rules |
| `src/knowledge_graph/postprocessing/modules/ontology_normalizer_module.py` | Type normalization module |
| `src/knowledge_graph/models.py` | `ResolvedEntity` dataclass used by relationship code |
| `src/core/entity_types.py` | FIX-002 type normalization functions |

## Implementation Steps

### Step 1: Fix Pipeline Ordering

Update `pipeline_config.json` to run `OntologyNormalizer` BEFORE `ListSplitter` and `EntityQualityFilter`.

**Current order (problematic):**
```
1. ConfidenceFilter
2. DocumentLevelDeduplicator
3. CanonicalResolver
4. EntityQualityFilter  ← Runs before normalization!
5. ListSplitter
6. OntologyNormalizer   ← Too late, HumanActor already bypassed PERSON checks
```

**Fixed order:**
```
1. ConfidenceFilter
2. DocumentLevelDeduplicator
3. CanonicalResolver
4. OntologyNormalizer   ← Move to position 4 (normalize types FIRST)
5. ListSplitter         ← Now sees PERSON (not HumanActor), so splitting works
6. EntityQualityFilter  ← Now sees normalized types and split items
```

**Why OntologyNormalizer before ListSplitter:** ListSplitter is enabled only for `["PERSON", "ORGANIZATION", "PROJECT"]`. If a `HumanActor` entity isn't normalized to `PERSON` first, it won't be split. OntologyNormalizer must run before ListSplitter.

In `pipeline_config.json`, reorder the `config["pipeline"]["modules"]` array accordingly (module objects use `{"name": "..."}`).

### Step 2: Add Min-Length Validation

In `entity_quality_filter.py`, add:

```python
# In EntityQualityFilter class:
MIN_NAME_LENGTH = 2  # Block 0-1 char names

def is_too_short(self, name: str) -> bool:
    """Block empty/single-character entity names."""
    stripped = name.strip()
    return len(stripped) < self.MIN_NAME_LENGTH
```

Integrate into the filtering logic to drop single-char entities.

### Step 3: Expand Placeholder Detection

Add these patterns to `entity_quality_filter.py`:

```python
# In EntityQualityFilter class:
PLACEHOLDER_PATTERNS = [
    re.compile(r'^unknown\s*\d*$', re.IGNORECASE),
    re.compile(r'^anonymous(\s+user)?$', re.IGNORECASE),
    re.compile(r'^public\s+users?$', re.IGNORECASE),
    re.compile(r'^user\s*\d+$', re.IGNORECASE),
    re.compile(r'^(tbd|todo|n/?a|none)$', re.IGNORECASE),
    re.compile(r'^placeholder\s*\d*$', re.IGNORECASE),
    re.compile(r'^(test|dummy|sample)\s*(user|data|entity)?$', re.IGNORECASE),
]

def is_placeholder(self, name: str, entity_type: str = None) -> bool:
    """Check for placeholder patterns (applies to ALL types now)."""
    for pattern in self.PLACEHOLDER_PATTERNS:
        if pattern.match(name.strip()):
            return True
    return False
```

Integrate placeholder checks into the main `filter_with_reasons(...)` flow (early), and ensure whitelisted names still bypass filtering.

### Step 4: Stop Relationship-Driven ENTITY Creation

**This is the core of FIX-003.** In `graph_integration.py`, the current code at lines 820-821:

```python
# CURRENT (problematic):
subject_type = rel.get("subject_type", "ENTITY")  # Defaults to ENTITY!
object_type = rel.get("object_type", "ENTITY")    # Defaults to ENTITY!
```

**Change to:** Default to `None`, infer from predicate, and if still unknown: try to resolve to an existing entity by name (across all types) or skip the relationship entirely.

Add predicate-based type inference to `KnowledgeGraphIntegrator`:

```python
# Add to KnowledgeGraphIntegrator class
PREDICATE_TYPE_HINTS = {
    'works_at': {'subject': 'PERSON', 'object': 'ORGANIZATION'},
    'founded': {'subject': 'PERSON', 'object': 'ORGANIZATION'},
    'co_founded': {'subject': 'PERSON', 'object': 'ORGANIZATION'},
    'created': {'subject': 'PERSON', 'object': 'PROJECT'},
    'developed': {'subject': 'PERSON', 'object': 'TECHNOLOGY'},
    'located_in': {'subject': None, 'object': 'LOCATION'},
    'based_in': {'subject': None, 'object': 'LOCATION'},
    'part_of': {'subject': None, 'object': 'ORGANIZATION'},
    'member_of': {'subject': 'PERSON', 'object': 'ORGANIZATION'},
    'supports': {'subject': None, 'object': 'CONCEPT'},
    'implements': {'subject': 'TECHNOLOGY', 'object': 'CONCEPT'},
    'uses': {'subject': None, 'object': 'TECHNOLOGY'},
    'attended': {'subject': 'PERSON', 'object': 'EVENT'},
    'spoke_at': {'subject': 'PERSON', 'object': 'EVENT'},
    'organized': {'subject': 'PERSON', 'object': 'EVENT'},
}

def _infer_type_from_predicate(self, predicate: str, role: str) -> Optional[str]:
    """Infer entity type from predicate and role (subject/object)."""
    # Use normalized predicate for lookup (in _add_relationship(), `predicate` is already normalized)
    key = predicate or ""
    hints = self.PREDICATE_TYPE_HINTS.get(key, {})
    return hints.get(role)  # Returns None if no hint
```

**Modify `_add_relationship()` (around lines 820-824):**

```python
# In _add_relationship(), replace lines 820-824 with:

# NEW FIX-003: Get explicit types first
subject_type = rel.get("subject_type")
object_type = rel.get("object_type")

# NEW FIX-003: Try predicate inference if type not provided
# Note: 'predicate' is already normalized via normalize_predicate() above (line 814)
if not subject_type:
    subject_type = self._infer_type_from_predicate(predicate, "subject")
    if subject_type:
        self.predicate_inferred_count += 1

if not object_type:
    object_type = self._infer_type_from_predicate(predicate, "object")
    if object_type:
        self.predicate_inferred_count += 1

# NEW FIX-003: If type still None, try to find existing entity by name across types
if subject_type is None:
    existing = self._find_existing_entity_by_name(subject_name)
    if existing:
        subject_type = existing.entity_type
        self.existing_lookup_count += 1
    else:
        # Log and skip - don't create new ENTITY rows
        # Note: ambiguous matches already counted in _find_existing_entity_by_name
        self.logger.debug(f"[ENTITY-SKIP] No type for subject '{subject_name}' in '{predicate}', skipping relationship")
        self.entity_skip_count += 1
        return None

if object_type is None:
    existing = self._find_existing_entity_by_name(object_name)
    if existing:
        object_type = existing.entity_type
        self.existing_lookup_count += 1
    else:
        self.logger.debug(f"[ENTITY-SKIP] No type for object '{object_name}' in '{predicate}', skipping relationship")
        self.entity_skip_count += 1
        return None

# Continue with existing code:
subject = self._resolve_entity_for_relationship(subject_name, subject_type)
object_ = self._resolve_entity_for_relationship(object_name, object_type)
```

**Add helper method to find existing entities by name (with ambiguity guard):**

```python
def _find_existing_entity_by_name(self, name: str) -> Optional[Any]:
    """
    Look up existing entity by name across all types.

    Returns entity row if found and UNAMBIGUOUS, None otherwise.
    Used to avoid creating new ENTITY rows when we can resolve to existing typed entities.

    IMPORTANT: If multiple entities match with DIFFERENT types, return None (ambiguous).
    """
    if not self.pg_conn or not HAS_PSYCOPG2:
        return None

    try:
        normalized = None
        if getattr(self, "entity_resolver", None) is not None and getattr(self.entity_resolver, "uri_gen", None) is not None:
            normalized = self.entity_resolver.uri_gen.normalize_name(name)

        with self.pg_conn.cursor() as cursor:
            # Prefer normalized_text (indexed) when available; also avoid anchoring to existing ENTITY rows.
            if normalized:
                cursor.execute("""
                    SELECT entity_type, MIN(id) AS id, MIN(entity_text) AS entity_text, MIN(fuseki_uri) AS fuseki_uri
                    FROM entity_registry
                    WHERE normalized_text = %s AND entity_type != 'ENTITY'
                    GROUP BY entity_type
                """, (normalized,))
            else:
                cursor.execute("""
                    SELECT entity_type, MIN(id) AS id, MIN(entity_text) AS entity_text, MIN(fuseki_uri) AS fuseki_uri
                    FROM entity_registry
                    WHERE LOWER(TRIM(entity_text)) = LOWER(TRIM(%s)) AND entity_type != 'ENTITY'
                    GROUP BY entity_type
                """, (name,))

            rows = cursor.fetchall()

            if not rows:
                return None

            # Check for ambiguity: multiple matches with different types
            types_found = set(row[0] for row in rows)
            if len(types_found) > 1:
                self.logger.debug(f"[ENTITY-AMBIGUOUS] '{name}' matches {len(rows)} entities across types: {types_found}")
                self.entity_ambiguous_count += 1
                return None  # Don't guess

            # Unambiguous: return first match
            row = rows[0]  # (entity_type, id, entity_text, fuseki_uri)
            from types import SimpleNamespace
            return SimpleNamespace(
                entity_type=row[0],
                entity_id=row[1],
                entity_text=row[2],
                fuseki_uri=row[3],
            )
    except Exception as e:
        self.logger.debug(f"Entity lookup failed for '{name}': {e}")
        return None
```

### Step 5: Add Metrics/Logging

Add granular counters to track behavior and measure "ENTITY avoided" signal:

```python
# In KnowledgeGraphIntegrator.__init__:
self.predicate_inferred_count = 0   # (a) Types inferred from predicate hints
self.existing_lookup_count = 0      # (b) Types resolved via existing entity lookup
self.entity_skip_count = 0          # (c) Relationships skipped - no type found
self.entity_ambiguous_count = 0     # (c) Relationships skipped - ambiguous type match

# Add summary method:
def log_entity_stats(self):
    """Log FIX-003 metrics for re-extraction analysis."""
    total_avoided = self.predicate_inferred_count + self.existing_lookup_count
    total_skipped = self.entity_skip_count + self.entity_ambiguous_count

    self.logger.info(f"[FIX-003] === Entity Type Resolution Summary ===")
    self.logger.info(f"[FIX-003] Types inferred from predicate: {self.predicate_inferred_count}")
    self.logger.info(f"[FIX-003] Types resolved via existing entity: {self.existing_lookup_count}")
    self.logger.info(f"[FIX-003] Relationships skipped (unknown type): {self.entity_skip_count}")
    self.logger.info(f"[FIX-003] Relationships skipped (ambiguous match): {self.entity_ambiguous_count}")
    self.logger.info(f"[FIX-003] Total ENTITY creations avoided: {total_avoided}")
    self.logger.info(f"[FIX-003] Total relationships skipped: {total_skipped}")
```

Call `log_entity_stats()` once per run (not per document) so logs remain readable.

## Testing Plan

### Unit Tests

Create `tests/test_fix003_entity_validation.py`:

```python
def test_min_length_filter_blocks_single_char():
    """Single-char names should be filtered."""
    from knowledge_graph.improvements.entity_quality_filter import EntityQualityFilter

    filter = EntityQualityFilter()

    # Should be blocked
    assert filter.is_too_short("X") == True
    assert filter.is_too_short(" ") == True
    assert filter.is_too_short("A") == True  # Single letters blocked
    assert filter.is_too_short("I") == True  # Single letters blocked

    # Two-char strings are allowed by length rule
    assert filter.is_too_short("US") == False
    assert filter.is_too_short("AI") == False
    assert filter.is_too_short("UK") == False

def test_placeholder_detection_expanded():
    """Placeholder patterns should be caught."""
    from knowledge_graph.improvements.entity_quality_filter import EntityQualityFilter

    filter = EntityQualityFilter()

    assert filter.is_placeholder("Unknown") == True
    assert filter.is_placeholder("Anonymous User") == True
    assert filter.is_placeholder("User 123") == True
    assert filter.is_placeholder("N/A") == True
    assert filter.is_placeholder("TBD") == True
    assert filter.is_placeholder("placeholder") == True
    assert filter.is_placeholder("test user") == True

    # Should NOT be placeholder
    assert filter.is_placeholder("Gregory Landua") == False
    assert filter.is_placeholder("Regen Network") == False

def test_predicate_type_inference():
    """Predicate should hint entity types."""
    from knowledge_graph.graph_integration import KnowledgeGraphIntegrator

    # Avoid __init__ (it may attempt DB connections in some environments)
    integrator = KnowledgeGraphIntegrator.__new__(KnowledgeGraphIntegrator)

    assert integrator._infer_type_from_predicate("works_at", "subject") == "PERSON"
    assert integrator._infer_type_from_predicate("works_at", "object") == "ORGANIZATION"
    assert integrator._infer_type_from_predicate("located_in", "object") == "LOCATION"
    assert integrator._infer_type_from_predicate("unknown_predicate", "subject") is None

def test_pipeline_order_normalizer_before_filter():
    """OntologyNormalizer should run before ListSplitter and EntityQualityFilter."""
    import json
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    config_path = repo_root / 'src/knowledge_graph/config/pipeline_config.json'
    config = json.loads(config_path.read_text())

    # Correct structure: config["pipeline"]["modules"] with m["name"]
    modules = [m['name'] for m in config['pipeline']['modules']]
    normalizer_idx = modules.index('OntologyNormalizer')
    splitter_idx = modules.index('ListSplitter')
    filter_idx = modules.index('EntityQualityFilter')

    assert normalizer_idx < splitter_idx, "OntologyNormalizer must run before ListSplitter"
    assert splitter_idx < filter_idx, "ListSplitter must run before EntityQualityFilter"
```

### Integration Test

```python
def test_relationship_skips_when_no_type_available():
    """Relationships should be skipped when entity type cannot be determined."""
    # This verifies that _add_relationship returns None and doesn't create ENTITY rows
    # when neither predicate inference nor existing entity lookup succeeds
    pass
```

### Validation Queries

After Stage 6 re-extraction (later), run on production:

```sql
-- Check ENTITY count (should decrease significantly after re-extraction)
SELECT COUNT(*) FROM entity_registry
WHERE entity_type = 'ENTITY' AND metadata = '{}';
-- Pre-fix: 15,558
-- Expected post-fix: < 5,000 (significant reduction)

-- Check single-char entities (should be 0 or near 0)
SELECT COUNT(*) FROM entity_registry
WHERE LENGTH(TRIM(entity_text)) <= 1;

-- Check placeholder entities (should be 0)
SELECT COUNT(*) FROM entity_registry
WHERE entity_text ~* '^(unknown|anonymous|public users?|tbd|todo|n/?a|placeholder)\s*\d*$';

-- Check type distribution (compare before/after)
SELECT entity_type, COUNT(*) as count
FROM entity_registry
GROUP BY entity_type
ORDER BY count DESC;
-- Save this output BEFORE and AFTER re-extraction for comparison
```

## Success Criteria

- [ ] `pipeline_config.json` order is: ConfidenceFilter → DocumentLevelDeduplicator → CanonicalResolver → OntologyNormalizer → ListSplitter → EntityQualityFilter
- [ ] `is_too_short()` method filters empty/single-char entities
- [ ] `is_placeholder()` catches all patterns listed above
- [ ] `_infer_type_from_predicate()` uses normalized predicate and provides type hints for common predicates
- [ ] `_add_relationship()` NO LONGER defaults to "ENTITY" - it skips relationships when type is unknown
- [ ] `_find_existing_entity_by_name()` has ambiguity guard (returns None if multiple types match)
- [ ] Granular counters track: `predicate_inferred_count`, `existing_lookup_count`, `entity_skip_count`, `entity_ambiguous_count`
- [ ] `log_entity_stats()` outputs "ENTITY creations avoided" summary
- [ ] All unit tests pass
- [ ] No regression in existing tests (run full test suite)

## Do NOT

- Do NOT run data migration SQL (re-extraction will clean data)
- Do NOT modify FIX-001/FIX-002 code (already deployed)
- Do NOT change database schema
- Do NOT push to production without running tests locally first
- Do NOT create new ENTITY rows from relationships - skip the relationship instead
- Do NOT run full re-extraction / Fuseki rebuild as part of FIX-003

## After Completion

1. Run tests locally:
   - From `koi-processor/`: `PYTHONPATH=src pytest -q`
2. Sync to production server:
   ```bash
   scp src/knowledge_graph/graph_integration.py darren@202.61.196.119:/opt/projects/koi-processor/src/knowledge_graph/
   scp src/knowledge_graph/config/pipeline_config.json darren@202.61.196.119:/opt/projects/koi-processor/src/knowledge_graph/config/
   scp src/knowledge_graph/improvements/entity_quality_filter.py darren@202.61.196.119:/opt/projects/koi-processor/src/knowledge_graph/improvements/
   scp tests/test_fix003_entity_validation.py darren@202.61.196.119:/opt/projects/koi-processor/tests/
   ```
3. Run tests on production (only if asked to deploy FIX-003)
4. Update status table in `knowledge-graph-review-2025-12.md`:
   ```
   | FIX-003 | DEPLOYED | ...koi-processor | 2025-12-XX | ENTITY default fix + pipeline ordering |
   ```
5. Ready for full re-extraction with Gemini 3 extractor

## Dependencies

- FIX-001: DEPLOYED (namespace/URI fixes)
- FIX-002: DEPLOYED (extractor unification, type normalization)
