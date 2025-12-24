# Regen Network Knowledge Graph Quality Review - Cycle 2026-01

**Started:** 2025-12-24
**Last Updated:** 2025-12-23
**Status:** Type Conflict Sprint - Week 1 Complete
**Graph URL:** https://regen.gaiaai.xyz/graph
**Server:** ssh darren@202.61.196.119
**Primary Repo:** koi-processor

**Day-1 Audit Report:** [kg_audit_2026_01_day1.md](reports/kg_audit_2026_01_day1.md)

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

## Type Conflict Sprint (Week 1)

**Report:** [type_conflicts_top_2026_01.md](reports/type_conflicts_top_2026_01.md)

### Top 20 Type Conflicts by Occurrence

| Rank | Label | Types | Occurrences | Classification |
|------|-------|-------|-------------|----------------|
| 1 | notion | TECHNOLOGY, ORGANIZATION, PROJECT | 336 | Polysemy (platform + company) |
| 2 | regen commons | ORGANIZATION, PROJECT, CONCEPT | 317 | Polysemy (org + project) |
| 3 | governance | CONCEPT, ORGANIZATION | 276 | **Wrong-type** (ORGANIZATION=2 is noise) |
| 4 | koi | PROJECT, TECHNOLOGY, PERSON, CONCEPT, STANDARD | 236 | **Wrong-type** (PERSON/CONCEPT/STANDARD is noise) |
| 5 | aerodrome | TECHNOLOGY, PROJECT, ORGANIZATION | 234 | Polysemy (DeFi protocol) |
| 6 | sparql | TECHNOLOGY, CONCEPT, STANDARD | 224 | Polysemy (tech + standard) |
| 7 | telegram | TECHNOLOGY, ORGANIZATION | 219 | Polysemy (platform + company) |
| 8 | youtube | TECHNOLOGY, ORGANIZATION | 212 | Polysemy (platform + company) |
| 9 | discord | TECHNOLOGY, ORGANIZATION | 208 | Polysemy (platform + company) |
| 10 | agent-based modeling | CONCEPT, TECHNOLOGY, PROJECT, PROCESS | 184 | **Wrong-type** (CONCEPT is correct) |
| 11 | hydrax | TECHNOLOGY, PROJECT, ORGANIZATION | 183 | Polysemy (protocol) |
| 12 | koi project | PROJECT, TECHNOLOGY | 181 | **Wrong-type** (PROJECT is correct) |
| 13 | twitter | TECHNOLOGY, ORGANIZATION, PROJECT | 180 | Polysemy + noise (PROJECT=1) |
| 14 | blockchain | TECHNOLOGY, CONCEPT | 177 | Polysemy |
| 15 | python | TECHNOLOGY, PROJECT | 172 | **Wrong-type** (PROJECT=1 is noise) |
| 16 | regen tokenomics | CONCEPT, PROJECT, ORGANIZATION | 166 | Polysemy + noise (ORGANIZATION=6) |
| 17 | regen tokenomics ai assistant | TECHNOLOGY, PROJECT | 164 | **Wrong-type** (TECHNOLOGY is correct) |
| 18 | koi-processor | PROJECT, TECHNOLOGY | 161 | Polysemy (repo + codebase) |
| 19 | ethereum | TECHNOLOGY, PROJECT, ORGANIZATION, LOCATION | 158 | **Wrong-type** (LOCATION=4 is noise) |
| 20 | regen-koi-mcp | PROJECT, TECHNOLOGY | 151 | Polysemy (repo + codebase) |

### Classification Summary

- **Polysemy (Keep):** 12 entities - legitimate multi-type references
- **Wrong-type (Fix):** 8 entities - extraction noise to remove
- **Total wrong-type occurrences:** ~60

### E325-Pattern Artifacts

| Entity | Type | Occurrences | Status |
|--------|------|-------------|--------|
| Will-Regen Foundation | PERSON | 9 | **Fixed in FIX-009** |
| Chris-Chainflow | PERSON | 6 | **Fixed in FIX-009** |
| Curtis-Meme_Network | PERSON | 4 | **Fixed in FIX-009** |

**Root Cause:** LLM extraction merged first name + organization name in text.

**Fix:** Added `is_firstname_orgname_artifact()` to EntityQualityFilter.

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

| Fix ID | Description | Priority | Complexity | Status |
|--------|-------------|----------|------------|--------|
| FIX-009 | E325 FirstName-OrgName artifact filter | High | Medium | **Complete** |
| FIX-010 | Block single-occurrence wrong-type noise | Medium | Simple | Proposed |
| FIX-011 | Block LOCATION for blockchain names | Medium | Simple | Proposed |

### FIX-009: E325 FirstName-OrgName Artifact Filter

**File:** `src/knowledge_graph/improvements/entity_quality_filter.py`

**Changes:**
- Added `FIRSTNAME_ORGNAME_PATTERN` regex to match `FirstName-OrgSuffix` patterns
- Added `is_firstname_orgname_artifact()` method
- Integrated into filter chain (check #16)
- Added `firstname_orgname_artifact` reason to stats

**Tests:** `src/knowledge_graph/improvements/tests/test_entity_quality_filter.py::TestE325FirstNameOrgNameArtifact` (26 tests)

**Behavior:**
- Blocks: `Will-Regen Foundation`, `Chris-Chainflow`, `Curtis-Meme_Network`
- Allows: `Mary-Jane`, `Jean-Pierre`, `Smith-Jones` (legitimate hyphenated names)
- Only applies to PERSON type

---

## Canary Validation

*Test fixes on subset before full deployment.*

### FIX-009 Canary (2025-12-23)

**E325 Artifact Detection:**
| Input | Type | Expected | Actual | Status |
|-------|------|----------|--------|--------|
| Will-Regen Foundation | PERSON | BLOCKED | BLOCKED (firstname_orgname_artifact) | **PASS** |
| Chris-Chainflow | PERSON | BLOCKED | BLOCKED (firstname_orgname_artifact) | **PASS** |
| Curtis-Meme_Network | PERSON | BLOCKED | BLOCKED (firstname_orgname_artifact) | **PASS** |

**Legitimate Names (No Regression):**
| Input | Type | Expected | Actual | Status |
|-------|------|----------|--------|--------|
| Mary-Jane Watson | PERSON | PASS | PASS | **PASS** |
| Jean-Pierre | PERSON | PASS | PASS | **PASS** |
| ryanchristo-Validator | PERSON | PASS | PASS | **PASS** |
| Gregory Landua | PERSON | PASS | PASS | **PASS** |
| Will Szal | PERSON | PASS | PASS | **PASS** |

**Unit Tests:** 199/199 passing

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
| Type Conflict Analysis | Complete | [Report](reports/type_conflicts_top_2026_01.md) - 2,749 conflicts classified |
| Error Discovery | Complete | E325 pattern artifacts identified (3 entities, 19 occurrences) |
| Root Cause Analysis | Complete | LLM extraction merge artifacts |
| Fix Implementation | Complete | FIX-009 in EntityQualityFilter (26 tests) |
| Canary Validation | Complete | All artifacts blocked, no regression |
| Full Re-Extraction | Not Needed | Fix prevents future artifacts |

---

## Reports

- [Day-1 Audit Report](reports/kg_audit_2026_01_day1.md)
- [2025-12 Regression Verification](reports/kg_regression_verification_2025_12_postfix006.md)
- [Type Conflicts Top 2026-01](reports/type_conflicts_top_2026_01.md)

---

## Next Fix Batch (Proposed)

Based on type conflict analysis, the next 2-3 fixes to tackle:

### FIX-010: Block Single-Occurrence Wrong-Type Noise

**Priority:** Medium | **Complexity:** Simple

Remove single-occurrence type variants that are extraction noise:
- `python` as PROJECT (1 occ) → keep only TECHNOLOGY (171)
- `typescript` as CONCEPT (1 occ) → keep only TECHNOLOGY (127)
- `twitter` as PROJECT (1 occ) → keep TECHNOLOGY (164) + ORGANIZATION (15)
- `regen tokenomics ai assistant` as PROJECT (1 occ) → keep only TECHNOLOGY (163)
- `koi project` as TECHNOLOGY (3 occ) → keep only PROJECT (178)

**Approach:** SQL delete of specific entity_registry rows by ID, then regenerate Fuseki.

**Impact:** ~6 wrong-type rows removed, cleaner type distribution.

### FIX-011: Block LOCATION for Blockchain Names

**Priority:** Medium | **Complexity:** Simple

Block LOCATION type for known blockchain/network names:
- `ethereum` as LOCATION (4 occ)
- `solana` as LOCATION (varies)
- `polygon` as LOCATION (varies)

**Approach:** Add to EntityQualityFilter a check that blocks LOCATION type for entities matching a blockchain name list.

**Impact:** ~10-15 wrong-type occurrences prevented in future extractions.

### FIX-012: Governance ORGANIZATION Cleanup (Optional)

**Priority:** Low | **Complexity:** Simple

`governance` typed as ORGANIZATION (2 occ) is noise - governance is a CONCEPT.

**Approach:** SQL delete of the 2 ORGANIZATION rows.

**Impact:** Minor cleanup.

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
