# Predicate-Type Constraints Analysis

**Date:** 2025-12-25
**Status:** Analysis Complete, Fix Proposed
**Related:** FIX-015 (proposed)

---

## Executive Summary

Analysis of the `operates` predicate revealed **127 relationships** where the object is typed as CONCEPT, which is semantically invalid. Investigation shows this is a **systemic gap**: the extraction prompt defines allowed predicates but provides no guidance on which entity types each predicate should connect.

**Root cause:** We carefully constrained entity extraction but left relationship extraction largely unconstrained.

**Impact:** Estimated 2-5% of relationships may have predicate-type mismatches.

**Recommendation:** Add a `RelationshipTypeValidator` post-processing module and enhance the extraction prompt with predicate usage guidance.

---

## 1. Problem Discovery

### Initial Finding

During the Week 14 carbon entity audit, we found:
- "carbon" exists in entity_registry with 62 occurrences
- Only 1 relationship: `World Economic Forum → operates → carbon`

### Source Context

The source document contained:
> "...we had been invited to co-author a World Economic Forum paper on blockchain and carbon..."

The LLM incorrectly extracted "WEF operates carbon" instead of "WEF discusses/published carbon" or creating a paper entity.

### Broader Investigation

Checking all `operates` → CONCEPT relationships revealed **127 instances**, not just the WEF case.

---

## 2. Data Analysis

### operates → CONCEPT by Subject Type

| Subject Type | Count | Assessment |
|--------------|-------|------------|
| PERSON | 52 | All wrong - people don't "operate" concepts |
| ORGANIZATION | 50 | Mostly wrong, some may be mis-typed programs |
| CONCEPT | 9 | All wrong - concept→operates→concept is nonsense |
| TECHNOLOGY | 8 | Borderline - tech might "operate on" concepts |
| PROJECT | 4 | Borderline |
| EVENT | 2 | Wrong |
| VALIDATOR | 2 | Borderline |
| **Total** | **127** | |

### By Occurrence Count

| Occurrences | Count | Implication |
|-------------|-------|-------------|
| 1 (single) | 121 | Low confidence - LLM saw it once |
| 2-3 | 4 | Slightly more confident |
| 4+ | 2 | Only "burn function" cases |

### Sample Wrong Relationships

**PERSON → operates → CONCEPT (clearly wrong):**

| Subject | Object | Should Be |
|---------|--------|-----------|
| Gregory Landua | Tokenomics | discusses |
| Bruce Damer | origins of life | researches |
| Mike Weist | quantum consciousness | discusses |
| Will | ethics | discusses |
| Carl Fristen | Active inference | researches |
| Robert | burn function | implements |
| Aaron Craelius | burn function | implements |

**ORGANIZATION → operates → CONCEPT (wrong predicate):**

| Subject | Object | Should Be |
|---------|--------|-----------|
| McKinsey | nature-based markets | analyzes |
| Blockscience | monetary policy | models |
| Cryptopedia | security | documents |
| Greenbiz | nature-based markets | covers |

**CONCEPT → operates → CONCEPT (nonsense):**

| Subject | Object |
|---------|--------|
| bio region | ecological data center |
| market data | economics |
| Staking | jailed validators |
| seed phrase | Private Key |
| DAO-owned liquidity | mercenary capital |

### Borderline Cases (may be mis-typed entities)

Some ORG → operates → CONCEPT might be valid if the "concept" is actually a program:

| Subject | Object | Assessment |
|---------|--------|------------|
| LASEG | Payment for Environmental Services | Likely their actual program - should be PROJECT |
| Regen Registry | NbS | Could be their NbS program |
| Climate Margins | regenerative tourism | Their business focus |

---

## 3. Root Cause Analysis

### 3.1 Extraction Prompt Gap

**Current state in `prompt_builder.py`:**

Entity types are well-defined:
```
## ALLOWED ENTITY TYPES (use UPPERCASE exactly as shown)
PERSON, ORGANIZATION, PROJECT, CONCEPT, TECHNOLOGY...

## CRITICAL TYPE RULES
### AI Systems are TECHNOLOGY, never PERSON
ChatGPT, GPT-4, Claude... DO NOT type these as PERSON.
```

Predicates are only listed:
```
### Canonical predicates (ONLY use these)
- Core: supports, uses, mentions, implements, includes, manages,
  enables, part_of, operates, creates, founded, discusses...
```

**The asymmetry:**

| Aspect | Entity Extraction | Relationship Extraction |
|--------|-------------------|------------------------|
| Allowed values | Listed | Listed |
| Definitions | Detailed | None |
| Examples | Multiple | Few, no predicate-specific |
| Negative examples | Extensive | None |
| Type constraints | Built-in | None |

### 3.2 Post-Processing Gap

Current pipeline modules:

| Module | Purpose | Catches This? |
|--------|---------|---------------|
| ConfidenceFilter | Filter low-confidence | No (0.9 confidence) |
| DocumentLevelDeduplicator | Dedupe entities | No |
| CanonicalResolver | Resolve to canonical | No |
| OntologyNormalizer | Normalize types | No |
| EntityQualityFilter | Filter bad entities | No (entities only) |

**Missing:** `RelationshipQualityFilter` or `RelationshipTypeValidator`

The `predicate_guard.py` validates predicates are canonical but doesn't validate type compatibility.

### 3.3 Ontology Complexity

- 20 entity types
- ~70 canonical predicates
- 70 × 20 × 20 = **28,000 possible type combinations**

The LLM must guess which combinations are valid. Some are obvious (founded: PERSON→ORG), many aren't.

---

## 4. Scale Assessment

### Is This an Edge Case?

**No.** The 127 `operates→CONCEPT` relationships represent 0.8% of ~15,400 total relationships. But this is likely the tip of the iceberg.

### Other Predicates at Risk

| Predicate | Expected Constraints | Potential Issues |
|-----------|---------------------|------------------|
| founded | Subject: PERSON, Object: ORG/PROJECT | ? |
| works_at | Subject: PERSON, Object: ORG | ? |
| located_in | Object: LOCATION | ? |
| member_of | Subject: PERSON/ORG, Object: ORG | ? |
| authored | Subject: PERSON, Object: document | ? |
| leads | Subject: PERSON, Object: ORG/PROJECT | ? |

**Estimated total impact:** 2-5% of relationships may have type mismatches.

---

## 5. Proposed Solutions

### 5.1 Post-Processing: RelationshipTypeValidator Module

Add a new pipeline module that validates predicate-type compatibility:

```python
# src/knowledge_graph/postprocessing/modules/relationship_type_validator.py

PREDICATE_CONSTRAINTS = {
    "operates": {
        "valid_object_types": {"ORGANIZATION", "PROJECT", "TECHNOLOGY", "VALIDATOR", "MODULE", "PLATFORM"},
        "blocked_object_types": {"CONCEPT", "MATERIAL", "PERSON", "LOCATION", "EVENT"},
    },
    "founded": {
        "valid_subject_types": {"PERSON"},
        "valid_object_types": {"ORGANIZATION", "PROJECT"},
    },
    "works_at": {
        "valid_subject_types": {"PERSON"},
        "valid_object_types": {"ORGANIZATION"},
    },
    "located_in": {
        "valid_object_types": {"LOCATION"},
    },
    "member_of": {
        "valid_subject_types": {"PERSON", "ORGANIZATION"},
        "valid_object_types": {"ORGANIZATION", "PROJECT"},
    },
    "leads": {
        "valid_subject_types": {"PERSON"},
        "valid_object_types": {"ORGANIZATION", "PROJECT", "EVENT"},
    },
    "authored": {
        "valid_subject_types": {"PERSON"},
    },
}

def validate_relationship(subject_type: str, predicate: str, object_type: str) -> tuple[bool, str]:
    """
    Validate that a relationship's types are compatible with the predicate.

    Returns:
        (is_valid, reason) - True if valid, False with reason if invalid
    """
    if predicate not in PREDICATE_CONSTRAINTS:
        return True, "no constraints defined"

    constraints = PREDICATE_CONSTRAINTS[predicate]

    # Check blocked types first
    if "blocked_object_types" in constraints:
        if object_type in constraints["blocked_object_types"]:
            return False, f"{predicate} cannot target {object_type}"

    if "blocked_subject_types" in constraints:
        if subject_type in constraints["blocked_subject_types"]:
            return False, f"{subject_type} cannot use {predicate}"

    # Check valid types
    if "valid_object_types" in constraints:
        if object_type not in constraints["valid_object_types"]:
            return False, f"{predicate} expects object type in {constraints['valid_object_types']}"

    if "valid_subject_types" in constraints:
        if subject_type not in constraints["valid_subject_types"]:
            return False, f"{predicate} expects subject type in {constraints['valid_subject_types']}"

    return True, "valid"
```

### 5.2 Prompt Enhancement: Predicate Usage Guidance

Add to `prompt_builder.py`:

```python
## PREDICATE USAGE RULES (CRITICAL)

### operates
Use when subject RUNS or MANAGES the object operationally.
- Valid: "Regen Network operates Regen Registry" (ORG → ORG)
- Valid: "DeSci Labs operates DeSci Publish" (ORG → PROJECT)
- WRONG: "Gregory operates Tokenomics" → use "discusses" instead
- WRONG: "McKinsey operates nature-based markets" → use "analyzes" instead
Object MUST be: ORGANIZATION, PROJECT, TECHNOLOGY, VALIDATOR, MODULE
Object must NOT be: CONCEPT, MATERIAL, PERSON, LOCATION

### founded
Subject must be PERSON. Object must be ORGANIZATION or PROJECT.
- Valid: "Gregory Landua founded Regen Network"
- WRONG: "Regen Network founded carbon markets"

### works_at / member_of
Subject must be PERSON (or ORG for member_of). Object must be ORGANIZATION.
- Valid: "Sarah Bax works_at Regen Foundation"
- WRONG: "Regen Network works_at blockchain"

### located_in
Object must be LOCATION (country, city, region).
- Valid: "Regen Network located_in Boulder, Colorado"
- WRONG: "Carbon Credit located_in blockchain"
```

### 5.3 Long-Term: Predicate Consolidation

Consider reducing 70 predicates to ~40-50 by merging similar ones:

| Current | Merge Into |
|---------|------------|
| discusses, explains, describes | discusses |
| analyzes, evaluates, measures | analyzes |
| operates, runs, manages | operates |
| creates, generates, produces | creates |

Fewer predicates = less confusion for LLM.

---

## 6. Immediate Cleanup

### Safe to Delete (63 relationships)

| Subject Type | Count | Rationale |
|--------------|-------|-----------|
| PERSON | 52 | People don't "operate" concepts |
| CONCEPT | 9 | CONCEPT→operates→CONCEPT is nonsense |
| EVENT | 2 | Events don't "operate" concepts |

### SQL for Cleanup

```sql
-- Delete PERSON/CONCEPT/EVENT → operates → CONCEPT
DELETE FROM koi_relationships
WHERE id IN (
    SELECT r.id
    FROM koi_relationships r
    JOIN entity_registry s ON r.subject_entity_id = s.id
    JOIN entity_registry o ON r.object_entity_id = o.id
    WHERE r.predicate = 'operates'
      AND o.entity_type = 'CONCEPT'
      AND s.entity_type IN ('PERSON', 'CONCEPT', 'EVENT')
);
```

### Review Needed (64 relationships)

ORG/TECHNOLOGY/PROJECT/VALIDATOR → operates → CONCEPT should be reviewed case-by-case:
- Some may be valid programs mis-typed as CONCEPT
- Some should use different predicates (discusses, focuses_on)
- Some should be deleted

---

## 7. Action Plan

| Priority | Action | Effort | Impact |
|----------|--------|--------|--------|
| 1 | Delete 63 clearly-wrong relationships | Low | Immediate cleanup |
| 2 | Add RelationshipTypeValidator module | Medium | Prevents future issues |
| 3 | Add predicate guidance to prompt | Medium | Better extraction |
| 4 | Audit other predicates (founded, works_at, etc.) | Medium | Find more issues |
| 5 | Consider predicate consolidation | High | Long-term simplification |

---

## 8. Key Insight

**We built careful guardrails for entity extraction but left relationship extraction to LLM intuition.**

The LLM knows the vocabulary (allowed predicates) but not the grammar (which types they connect).

This is analogous to telling someone "you can use these 70 verbs" but not explaining that "founded" requires a person as subject, or that "operates" implies running a system.

---

## Appendix: Full List of operates → CONCEPT Relationships

See database query:
```sql
SELECT s.entity_text, s.entity_type, o.entity_text, r.occurrence_count
FROM koi_relationships r
JOIN entity_registry s ON r.subject_entity_id = s.id
JOIN entity_registry o ON r.object_entity_id = o.id
WHERE r.predicate = 'operates'
  AND o.entity_type = 'CONCEPT'
ORDER BY s.entity_type, r.occurrence_count DESC;
```

---

**Document Author:** Claude Code
**Last Updated:** 2025-12-25
