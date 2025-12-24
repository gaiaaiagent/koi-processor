# Knowledge Graph Regression Verification Report

**Post FIX-006 Entity Deduplication**

**Report Generated:** 2025-12-23
**Production Commit:** c7a9fb3e28d6be2e79751bc183a02116a27dc9b7
**Reference:** `docs/archive/knowledge-graph-review-2025-12.md`

---

## Summary Table

| Category | Check | Status | Notes |
|----------|-------|--------|-------|
| ENTITY Type | Count = 0 | **PASS** | Generic ENTITY type eliminated |
| HTTP URIs | Count = 0 | **PASS** | All URIs use HTTPS |
| Self-Referential | Count = 0 | **PASS** | No subject_entity_id = object_entity_id |
| HumanActor Type | Count = 0 | **PASS** | HumanActor type eliminated |
| Same-Type Duplicates | Count = 0 | **PASS** | All (normalized_text, type) clusters resolved |
| Predicate Count | 1,499 | **PASS** | Reduced from 3,303 → 1,501 → 1,499 |
| Entity Dedup Applied | 323/323 | **PASS** | All merges in dedup_merge_plan applied |

---

## Quality Gates (All Passing)

| Gate | Description | Count | Status |
|------|-------------|-------|--------|
| A | No `http://regen.network/` URIs | 0 | **PASS** |
| B1 | No `ontology#` types | 0 | **PASS** |
| B2 | No `ontology#` predicates | 0 | **PASS** |
| C | No self-referential triples | 0 | **PASS** |

---

## Error Categories: Resolved

### E001 - Regen Network as HUMANACTOR

**Status:** **RESOLVED**

Before: `Regen Network` was typed as `HUMANACTOR`
After: `Regen Network` is now `ORGANIZATION` with 3,702 occurrences

```sql
SELECT entity_text, entity_type, occurrence_count
FROM entity_registry WHERE entity_text = 'Regen Network';
-- Result: ORGANIZATION, 3702
```

### E045-E056 - AI Systems Typed as PERSON

**Status:** **RESOLVED**

Before: Claude, GPT, Whisper AI, etc. were typed as `PERSON`
After: All now typed as `TECHNOLOGY`

| Entity | Old Type | New Type | Occurrences |
|--------|----------|----------|-------------|
| Claude | PERSON | TECHNOLOGY | 107 |
| GPT | PERSON | TECHNOLOGY | 25 |
| Whisper AI | PERSON | TECHNOLOGY | 5 |
| postgres | PERSON | TECHNOLOGY | 25 |
| OpenAI | PERSON | TECHNOLOGY + ORGANIZATION | 42 + 10 |

### E072-E093 - Organizations as HumanActor

**Status:** **RESOLVED**

Before: 0 entities with `HumanActor` type exist
Query: `SELECT COUNT(*) FROM entity_registry WHERE entity_type IN ('HumanActor', 'HUMANACTOR');`
Result: 0

Verified examples:
- Coca-Cola: Now ORGANIZATION (3 occurrences)
- Regen Network: Now ORGANIZATION (3,702 occurrences)

### E015-E018, E030, E122-E143, E305-E326 - Duplicate Entities

**Status:** **RESOLVED via FIX-006**

374 duplicate entities merged across two passes:
- Pass 1: ~51 merges (partial due to rollback bug)
- Pass 2: 323 merges (tier1_normalized: 207, tier1_5_canonical: 116)

Key consolidations verified:
- **Gregory Landua:** Single entity with 684 occurrences (all variants merged)
- **Regen Foundation:** Single entity with 610 occurrences (all variants merged)

Same-type duplicate clusters remaining: **0**

```sql
SELECT COUNT(*) FROM (
  SELECT LOWER(TRIM(entity_text)), entity_type, COUNT(*)
  FROM entity_registry
  GROUP BY LOWER(TRIM(entity_text)), entity_type
  HAVING COUNT(*) > 1
) dupes;
-- Result: 0
```

### E147-E182 - Predicate Duplication (FIX-007)

**Status:** **RESOLVED**

Predicates consolidated from 3,303 → 1,501 → 1,499

Top 10 predicates by frequency:
| Predicate | Count |
|-----------|-------|
| supports | 1,730 |
| uses | 1,069 |
| associated_with | 859 |
| relates_to | 694 |
| mentions | 529 |
| operates | 510 |
| participates_in | 443 |
| includes | 391 |
| manages | 381 |
| implements | 368 |

---

## Known Remaining Issues (Not Errors - Informational)

### Type Conflicts (Cross-Type Collisions)

**Count:** 2,749 normalized names with multiple types

These are expected and informational only. Common examples where the same name legitimately has multiple types:

| Name | Types | Total Occurrences |
|------|-------|-------------------|
| notion | TECHNOLOGY(308), ORGANIZATION(27), PROJECT(1) | 336 |
| regen commons | ORGANIZATION(151), PROJECT(147), CONCEPT(19) | 317 |
| governance | CONCEPT(274), ORGANIZATION(2) | 276 |
| koi | PROJECT(166), TECHNOLOGY(65), CONCEPT(2), PERSON(2), STANDARD(1) | 236 |
| aerodrome | TECHNOLOGY(100), PROJECT(98), ORGANIZATION(36) | 234 |
| sparql | TECHNOLOGY(186), CONCEPT(29), STANDARD(9) | 224 |

**Decision:** Not auto-merged. Some represent legitimate polysemy; others may warrant manual review in 2026-01.

### Single-Token PERSON Names

**Status:** Baseline captured (not an error, but tracked for ambiguity)

Single first names that may refer to multiple people:

| Name | Occurrences |
|------|-------------|
| Max | 265 |
| James | 239 |
| Mark | 197 |
| Will | 138 |
| Brandon | 115 |
| Julia | 53 |
| Sarah | 47 |
| Becca | 43 |
| Scott | 43 |
| Paul | 33 |

**Protection:** Canonical registry requirement prevents incorrect merges.

### E325 - Will-Regen Foundation

**Status:** Still Present (False Merge Artifact)

`Will-Regen Foundation` exists as PERSON with 9 occurrences. This is likely a false extraction artifact (Will Szal incorrectly merged with Regen Foundation in text). Candidate for manual cleanup in 2026-01.

```sql
SELECT entity_text, entity_type, occurrence_count
FROM entity_registry
WHERE entity_text LIKE '%Will-Regen%';
-- Result: Will-Regen Foundation | PERSON | 9
```

---

## Final Metrics

| Metric | 2025-12 Closeout | Post-Verification |
|--------|------------------|-------------------|
| Entities | 29,667 | 29,667 |
| Relationships | 15,364 | 15,364 |
| Distinct Predicates | 1,501 | 1,499 |
| ENTITY type | 0 | 0 |
| HumanActor type | 0 | 0 |
| HTTP URIs | 0 | 0 |
| Self-referential | 0 | 0 |
| Type conflicts | 4,264 | 2,749 |
| Same-type duplicates | 374 | 0 |

---

## Audit Trail Verification

### dedup_merge_plan Table

```sql
SELECT applied, COUNT(*) FROM dedup_merge_plan GROUP BY applied;
-- Result: t | 323
```

All 323 merges from Pass 2 are recorded with `applied=true`.

### Production State

- **Commit:** c7a9fb3e28d6be2e79751bc183a02116a27dc9b7
- **Tests:** 32/32 passing (`test_fix006_entity_dedup.py`)
- **Dependencies:** rapidfuzz 3.14.1 installed

---

## Conclusion

**All major error categories from 2025-12 are RESOLVED.**

The FIX-006 entity deduplication and FIX-007 predicate consolidation successfully addressed:
- Generic ENTITY type elimination
- HumanActor type elimination
- Same-type duplicate consolidation (374 entities merged)
- Predicate reduction (3,303 → 1,499)
- Quality gate compliance (4/4 PASS)

**Carry-over items for 2026-01:**
1. E325 `Will-Regen Foundation` artifact (9 occurrences)
2. Type conflict review (2,749 cross-type collisions)
3. tier1x_fuzzy PERSON proposals (6-8 false positive candidates)
4. Optional: Further predicate reduction (1,499 → ~100-200)
