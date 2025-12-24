# Regen Network Knowledge Graph Quality Review - Cycle 2026-01

**Started:** 2025-12-24
**Last Updated:** 2025-12-24
**Status:** Week 3 Complete - Wrong-Type Cleanup Applied
**Graph URL:** https://regen.gaiaai.xyz/graph
**Server:** ssh darren@202.61.196.119
**Primary Repo:** koi-processor

**Day-1 Audit Report:** [kg_audit_2026_01_day1.md](reports/kg_audit_2026_01_day1.md)
**Post-Week 3 Audit Report:** [kg_audit_2026_01_post_week3.md](reports/kg_audit_2026_01_post_week3.md)

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

## Type Conflict Sprint (Week 2)

**Report:** [type_conflict_pairs_2026_01.md](reports/type_conflict_pairs_2026_01.md)

### Type Conflict Pair Analysis

Analyzed 2,749 type conflicts grouped by type-pair:

| Rank | Type Pair | Conflicting Labels | Total Occurrences | Classification |
|------|-----------|-------------------|-------------------|----------------|
| 1 | CONCEPT↔TECHNOLOGY | 719 | 6,903 | Polysemy |
| 2 | CONCEPT↔PROCESS | 599 | 4,166 | Polysemy |
| 3 | PROJECT↔TECHNOLOGY | 446 | 6,872 | Polysemy |
| 4 | CONCEPT↔PROJECT | 287 | 2,979 | Polysemy |
| 5 | ORGANIZATION↔PROJECT | 266 | 3,203 | Polysemy |

**Key Finding:** Top 5 conflict pairs account for **2,317 labels (84%)** of all type conflicts. These are predominantly **legitimate polysemy** - entities that genuinely have multiple valid types in different contexts.

### Why This Matters

| Impact Area | Problem | Example |
|-------------|---------|---------|
| **GraphRAG** | Wrong-type entities pollute retrieval | "ethereum LOCATION" returns wrong results |
| **Queryability** | SPARQL by type is incomplete | Searching TECHNOLOGY misses LOCATION variants |
| **Entity Linking** | Can't merge correctly | Same entity, different types → duplicate nodes |

### Actionable Conflicts: LOCATION↔Blockchain

The **LOCATION↔TECHNOLOGY** pair (22 labels, 494 occ) contains blockchain names incorrectly typed as LOCATION:

| Entity | LOCATION Occ | Should Be | Action |
|--------|--------------|-----------|--------|
| polygon | 17 | TECHNOLOGY/PROJECT | **FIX-011** |
| base | 14 | TECHNOLOGY/PROJECT | Review (ambiguous) |
| ethereum | 4 | TECHNOLOGY/PROJECT | **FIX-011** |
| solana | 3 | TECHNOLOGY/PROJECT | **FIX-011** |
| arbitrum | 2 | TECHNOLOGY/PROJECT | **FIX-011** |

**Impact of FIX-011:** ~26+ wrong-type occurrences prevented in future extractions.

---

## Type Conflict Sprint (Week 3)

**Report:** [fix010_fix012_cleanup_2026_01.md](reports/fix010_fix012_cleanup_2026_01.md)

### One-Time Cleanup Applied

Applied safe cleanup of existing wrong-type entities in production. All targets verified to have **zero references** before deletion.

#### FIX-010: Single-Occurrence Wrong-Type Noise (6 rows deleted)

| Entity | Wrong Type | Correct Type(s) Retained |
|--------|------------|-------------------------|
| Koi Project | TECHNOLOGY (3 occ) | PROJECT (178) |
| Twitter | PROJECT (1 occ) | TECHNOLOGY (164), ORGANIZATION (15) |
| MCP server | CONCEPT (1 occ) | TECHNOLOGY (134) |
| TypeScript | CONCEPT (1 occ) | TECHNOLOGY (127) |
| Regen Tokenomics AI Assistant | PROJECT (1 occ) | TECHNOLOGY (163) |
| Python | PROJECT (1 occ) | TECHNOLOGY (171) |

#### FIX-011: Blockchain-as-LOCATION Cleanup (5 rows deleted)

| Entity | Wrong Type | Correct Type(s) Retained |
|--------|------------|-------------------------|
| Base | LOCATION (14 occ) | TECHNOLOGY (68), PROJECT (24), ORGANIZATION (2) |
| Polygon | LOCATION (17 occ) | TECHNOLOGY (51), PROJECT (11), ORGANIZATION (2) |
| Solana | LOCATION (3 occ) | TECHNOLOGY (59), PROJECT (22), ORGANIZATION (9) |
| Ethereum | LOCATION (4 occ) | TECHNOLOGY (128), PROJECT (17), ORGANIZATION (9) |
| Arbitrum | LOCATION (2 occ) | TECHNOLOGY (9), PROJECT (3) |

#### FIX-012: Governance-as-ORGANIZATION Cleanup (1 row deleted)

| Entity | Wrong Type | Correct Type Retained |
|--------|------------|----------------------|
| Governance | ORGANIZATION (2 occ) | CONCEPT (274) |

### Week 3 Metrics Summary

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| entity_registry rows | 29,667 | 29,655 | -12 |
| Fuseki triples | 165,619 | 163,639 | -1,980 |
| Quality Gates | 4/4 PASS | 4/4 PASS | — |

### Key Insight: Polysemy Dominates Remaining Conflicts

The remaining ~2,737 type conflicts are predominantly **legitimate polysemy** - entities that genuinely have multiple valid types in different contexts:

- **CONCEPT↔TECHNOLOGY** (719 labels): "blockchain", "AI", "knowledge graph"
- **PROJECT↔TECHNOLOGY** (446 labels): "koi-processor", "regen-koi-mcp"
- **ORGANIZATION↔PROJECT** (266 labels): "Regen Commons", "Aerodrome"

**Future work:** These require disambiguation strategy at query/UI level, not data cleanup. Approaches include:
1. Context-aware entity resolution during retrieval
2. Type priority ranking based on query intent
3. Multi-type entity representation in GraphRAG

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
| FIX-010 | Block single-occurrence wrong-type noise | Medium | Simple | **Complete + Cleanup Applied** |
| FIX-011 | Block LOCATION for blockchain names | Medium | Simple | **Complete + Cleanup Applied** |
| FIX-012 | Governance ORGANIZATION cleanup | Low | Simple | **Complete + Cleanup Applied** |

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

### FIX-011: Block LOCATION for Blockchain Names

**File:** `src/knowledge_graph/improvements/entity_quality_filter.py`

**Changes:**
- Added `BLOCKCHAIN_NAMES` set with ~70 blockchain/network names (L1s, L2s, Cosmos chains)
- Added `is_blockchain_as_location()` method
- Integrated into filter chain (check #17)
- Added `blockchain_as_location` reason to stats

**Tests:** `src/knowledge_graph/improvements/tests/test_entity_quality_filter.py::TestFIX011BlockchainAsLocation` (59 tests)

**Behavior:**
- Blocks: `ethereum`, `polygon`, `solana`, `arbitrum`, `base`, etc. when typed as LOCATION
- Allows: Same names when typed as TECHNOLOGY, PROJECT, ORGANIZATION
- Allows: Legitimate locations like `Boulder`, `Colorado`, `Amazon` (the river)
- Also blocks compound names: `ethereum mainnet`, `polygon network`, etc.

**Impact:** ~26+ wrong-type occurrences prevented in future extractions.

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

### FIX-011 Canary (2025-12-23)

**Blockchain Names as LOCATION Detection:**
| Input | Type | Expected | Actual | Status |
|-------|------|----------|--------|--------|
| Ethereum | LOCATION | BLOCKED | BLOCKED (blockchain_as_location) | **PASS** |
| Polygon | LOCATION | BLOCKED | BLOCKED (blockchain_as_location) | **PASS** |
| Solana | LOCATION | BLOCKED | BLOCKED (blockchain_as_location) | **PASS** |
| Arbitrum | LOCATION | BLOCKED | BLOCKED (blockchain_as_location) | **PASS** |
| Base | LOCATION | BLOCKED | BLOCKED (blockchain_as_location) | **PASS** |
| ethereum mainnet | LOCATION | BLOCKED | BLOCKED (blockchain_as_location) | **PASS** |

**Correct Types (No Regression):**
| Input | Type | Expected | Actual | Status |
|-------|------|----------|--------|--------|
| Ethereum | TECHNOLOGY | PASS | PASS | **PASS** |
| Polygon | PROJECT | PASS | PASS | **PASS** |
| Solana | ORGANIZATION | PASS | PASS | **PASS** |

**Legitimate Locations (No Regression):**
| Input | Type | Expected | Actual | Status |
|-------|------|----------|--------|--------|
| Boulder | LOCATION | PASS | PASS | **PASS** |
| Colorado | LOCATION | PASS | PASS | **PASS** |
| Amazon | LOCATION | PASS | PASS | **PASS** |

**Unit Tests:** 258/258 passing (59 new tests for FIX-011)

**Post-Deployment Verification Query:**
```sql
-- Verify no new blockchain-as-LOCATION entities are created
SELECT entity_text, entity_type, occurrence_count
FROM entity_registry
WHERE entity_type = 'LOCATION'
  AND LOWER(normalized_text) IN ('ethereum', 'polygon', 'solana', 'arbitrum', 'base', 'optimism')
ORDER BY occurrence_count DESC;
```

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
| **Week 2: Type Pair Analysis** | Complete | [Report](reports/type_conflict_pairs_2026_01.md) - dominant pairs identified |
| **Week 2: FIX-011 Prevention** | Complete | Block LOCATION for blockchain names (59 tests) |
| **Week 3: FIX-010/011/012 Cleanup** | Complete | [Report](reports/fix010_fix012_cleanup_2026_01.md) - 12 wrong-type rows removed |
| **Week 3: Baseline Update** | Complete | [Report](reports/kg_audit_2026_01_post_week3.md) - 29,655 entities |

---

## Reports

- [Day-1 Audit Report](reports/kg_audit_2026_01_day1.md)
- [2025-12 Regression Verification](reports/kg_regression_verification_2025_12_postfix006.md)
- [Type Conflicts Top 2026-01](reports/type_conflicts_top_2026_01.md)
- [Type Conflict Pairs 2026-01](reports/type_conflict_pairs_2026_01.md) - Week 2 pair analysis
- [FIX-010/011/012 Cleanup Report](reports/fix010_fix012_cleanup_2026_01.md) - Week 3 cleanup
- [Post-Week 3 Audit Report](reports/kg_audit_2026_01_post_week3.md) - Updated baseline

---

## Next Fix Batch (Completed in Week 3)

All proposed fixes have been applied. See [Week 3 cleanup report](reports/fix010_fix012_cleanup_2026_01.md).

| Fix ID | Description | Rows Removed | Status |
|--------|-------------|--------------|--------|
| FIX-010 | Single-occurrence wrong-type noise | 6 | **Complete** |
| FIX-011 | Blockchain-as-LOCATION cleanup | 5 | **Complete** |
| FIX-012 | Governance ORGANIZATION cleanup | 1 | **Complete** |

**Total:** 12 wrong-type entity rows removed from production.

### Remaining Work (Future Cycles)

1. **Polysemy disambiguation** - Query/UI level strategies for multi-type entities
2. **Targeted wrong-type prevention** - Additional EntityQualityFilter rules as discovered
3. **Single-token PERSON ambiguity** - Canonical registry protection in place, full resolution optional

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
