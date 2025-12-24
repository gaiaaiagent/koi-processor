# Regen Network Knowledge Graph Quality Review - Cycle 2026-01

**Started:** 2025-12-24
**Last Updated:** 2025-12-24
**Status:** Day-1 Baseline Captured
**Graph URL:** https://regen.gaiaai.xyz/graph
**Server:** ssh darren@202.61.196.119
**Primary Repo:** koi-processor

---

## Day-1 Baseline (2025-12-24)

*Captured via `scripts/kg_audit_report.py`*

### Summary Metrics

| Metric | Value |
|--------|-------|
| Entities (entity_registry) | 29,667 |
| Relationships (koi_relationships) | 15,364 |
| Distinct Predicates | 1,499 |
| Quality Gates | 4/4 PASS |

### Quality Gates

| Gate | Check | Count | Status |
|------|-------|-------|--------|
| A | No http:// URIs | 0 | **PASS** |
| B | No generic ENTITY type | 0 | **PASS** |
| C | No self-referential | 0 | **PASS** |
| D | No HumanActor type | 0 | **PASS** |

### Entity Type Distribution

| Type | Count |
|------|-------|
| CONCEPT | 13,892 |
| TECHNOLOGY | 4,903 |
| PROCESS | 2,245 |
| PROJECT | 1,807 |
| ORGANIZATION | 1,673 |
| PERSON | 952 |
| CLAIM | 598 |
| API_MESSAGE | 513 |
| STANDARD | 501 |
| GOVERNANCE_PROPOSAL | 487 |
| LOCATION | 420 |
| MATERIAL | 265 |
| EVIDENCE | 261 |
| EVENT | 243 |
| QUESTION | 235 |
| VALIDATOR | 214 |
| CREDIT_CLASS | 176 |
| MODULE | 136 |
| LICENSE | 115 |
| KEEPER | 31 |

### Same-Type Duplicates

**0 remaining** - All deduplication completed in 2025-12.

### Type Conflicts (Cross-Type Collisions)

**2,749 total** - Informational, not auto-merged.

Top 5:
1. `notion` - TECHNOLOGY(308), ORGANIZATION(27), PROJECT(1)
2. `regen commons` - ORGANIZATION(151), PROJECT(147), CONCEPT(19)
3. `governance` - CONCEPT(274), ORGANIZATION(2)
4. `koi` - PROJECT(166), TECHNOLOGY(65), CONCEPT(2), PERSON(2), STANDARD(1)
5. `aerodrome` - TECHNOLOGY(100), PROJECT(98), ORGANIZATION(36)

---

## Carry-Over from 2025-12

*Reference: `docs/archive/knowledge-graph-review-2025-12.md` Cycle Closeout section*

1. **tier1x_fuzzy PERSON proposals** - 6-8 identified false positives. Requires manual review.
2. **type_conflict backlog** - 2,749 cross-type collisions. Informational only.
3. **Single-token PERSON ambiguity** - Protected by canonical registry but not fully resolved.
4. **Further predicate reduction** - 1,499 → ~100-200 optional consolidation.
5. **E325 Will-Regen Foundation** - False extraction artifact (9 occurrences). Candidate for removal.

---

## Review Sprint Plan

### Automated Audits

Run periodically via:
```bash
cd /opt/projects/koi-processor
set -a; source .env; set +a
python3 scripts/kg_audit_report.py --out docs/archive/reports/kg_audit_YYYY_MM_DD.md
```

### Manual Sampling Strategy

1. **Top-by-Occurrence (Weekly)**
   - Review top 25 entities by occurrence_count
   - Verify types are correct
   - Check for extraction artifacts

2. **Random Per-Type (Bi-weekly)**
   - Sample 10 random entities per major type (PERSON, ORGANIZATION, CONCEPT, TECHNOLOGY)
   - Verify entity quality and type correctness

3. **Random Relationships (Bi-weekly)**
   - Sample 20 random relationships
   - Verify predicate makes sense for subject/object types
   - Check for nonsensical connections

### Error ID Assignment

New errors discovered during this cycle will be assigned IDs starting from **E400**.

Format: `E4XX` where XX is sequential.

---

## Findings

*Document any new quality issues discovered during this cycle.*

| ID | Finding | Severity | Category |
|----|---------|----------|----------|
| E400 | Will-Regen Foundation (PERSON, 9 occ) - false artifact | Low | Extraction Artifact |
| | | | |

---

## Root Causes

*For each finding, identify the root cause.*

| Finding | Root Cause | Code Location |
|---------|------------|---------------|
| E400 | LLM extraction merged "Will" + "Regen Foundation" in a sentence | N/A - extraction-time |
| | | |

---

## Fix Plan

*Proposed fixes for identified issues.*

| Fix ID | Description | Priority | Complexity |
|--------|-------------|----------|------------|
| FIX-009 | Manual cleanup of Will-Regen Foundation | Low | Simple |
| | | | |

---

## Canary Validation

*Test fixes on subset before full deployment.*

| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| | | | | |

---

## Full Re-Extraction Decision

**Decision:** [ ] Proceed with re-extraction / [ ] Defer / [x] Not needed

**Rationale:** 2025-12 Stage 6 re-extraction is recent; no systemic issues warrant full re-run.

**Scope:** N/A

---

## Progress Summary

| Phase | Status | Details |
|-------|--------|---------|
| Day-1 Baseline | Complete | Metrics captured 2025-12-24 |
| Regression Verification | Complete | [Report](reports/kg_regression_verification_2025_12_postfix006.md) |
| Error Discovery | Not Started | |
| Root Cause Analysis | Not Started | |
| Fix Implementation | Not Started | |
| Canary Validation | Not Started | |
| Full Re-Extraction | Not Needed | |

---

## Reports

- [Day-1 Audit Report](reports/kg_audit_2026_01_day1.md)
- [2025-12 Regression Verification](reports/kg_regression_verification_2025_12_postfix006.md)

---

## Cycle Closeout

*To be completed at end of cycle.*

### Final Metrics

| Metric | Before | After |
|--------|--------|-------|
| | | |

### Changes Made

### Remaining Issues

---

**Cycle Closed:** YYYY-MM-DD
