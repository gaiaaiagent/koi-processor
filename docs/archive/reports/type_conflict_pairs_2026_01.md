# Type Conflict Pair Analysis - Cycle 2026-01

**Generated:** 2025-12-23
**Database:** eliza (production)
**Purpose:** Quantify type conflicts by pair to prioritize fixes

---

## Summary

**Total type conflict pairs:** 2,749 entities with same normalized label but different types

The analysis groups these conflicts by type-pair to identify which combinations are:
1. **Legitimate polysemy** - entities that genuinely have multiple valid types
2. **Extraction noise** - wrong types that should be filtered or removed

---

## Top 10 Type Conflict Pairs

| Rank | Type Pair | Conflicting Labels | Total Occurrences | Classification |
|------|-----------|-------------------|-------------------|----------------|
| 1 | CONCEPT↔TECHNOLOGY | 719 | 6,903 | **Polysemy** |
| 2 | CONCEPT↔PROCESS | 599 | 4,166 | **Polysemy** |
| 3 | PROJECT↔TECHNOLOGY | 446 | 6,872 | **Polysemy** |
| 4 | CONCEPT↔PROJECT | 287 | 2,979 | **Polysemy** |
| 5 | ORGANIZATION↔PROJECT | 266 | 3,203 | **Polysemy** |
| 6 | ORGANIZATION↔TECHNOLOGY | 171 | 3,870 | **Polysemy** |
| 7 | CONCEPT↔ORGANIZATION | 159 | 2,156 | Mixed |
| 8 | CONCEPT↔STANDARD | 131 | 912 | **Polysemy** |
| 9 | PROCESS↔TECHNOLOGY | 115 | 644 | Mixed |
| 10 | STANDARD↔TECHNOLOGY | 87 | 975 | **Polysemy** |

### Key Finding

The top 6 conflict pairs account for **2,588 labels (94%)** of all type conflicts. These are predominantly **legitimate polysemy** cases:

- **CONCEPT↔TECHNOLOGY**: "blockchain", "AI", "knowledge graph" - terms that are both abstract concepts and implemented technologies
- **PROJECT↔TECHNOLOGY**: "koi-processor", "regen-koi-mcp" - code repositories that are both projects and technical implementations
- **ORGANIZATION↔PROJECT**: "Regen Commons", "Aerodrome" - DeFi/web3 entities that are both organizations and projects

---

## Full Type Pair Distribution

| Type Pair | Labels | Occurrences |
|-----------|--------|-------------|
| CONCEPT↔TECHNOLOGY | 719 | 6,903 |
| CONCEPT↔PROCESS | 599 | 4,166 |
| PROJECT↔TECHNOLOGY | 446 | 6,872 |
| CONCEPT↔PROJECT | 287 | 2,979 |
| ORGANIZATION↔PROJECT | 266 | 3,203 |
| ORGANIZATION↔TECHNOLOGY | 171 | 3,870 |
| CONCEPT↔ORGANIZATION | 159 | 2,156 |
| CONCEPT↔STANDARD | 131 | 912 |
| PROCESS↔TECHNOLOGY | 115 | 644 |
| STANDARD↔TECHNOLOGY | 87 | 975 |
| CONCEPT↔MATERIAL | 62 | 799 |
| CONCEPT↔CREDIT_CLASS | 54 | 322 |
| PROCESS↔PROJECT | 49 | 367 |
| CONCEPT↔GOVERNANCE_PROPOSAL | 47 | 281 |
| API_MESSAGE↔CONCEPT | 40 | 237 |
| ORGANIZATION↔VALIDATOR | 36 | 296 |
| PROJECT↔STANDARD | 35 | 371 |
| PROJECT↔VALIDATOR | 33 | 207 |
| TECHNOLOGY↔VALIDATOR | 26 | 171 |
| CONCEPT↔EVIDENCE | 26 | 166 |
| API_MESSAGE↔TECHNOLOGY | 26 | 103 |
| MODULE↔TECHNOLOGY | 25 | 380 |
| **LOCATION↔TECHNOLOGY** | **22** | **494** |
| API_MESSAGE↔EVENT | 22 | 82 |
| PROCESS↔STANDARD | 22 | 94 |
| CONCEPT↔LICENSE | 22 | 134 |
| MATERIAL↔TECHNOLOGY | 21 | 363 |
| CLAIM↔CONCEPT | 21 | 103 |
| LOCATION↔PROJECT | 20 | 279 |
| CONCEPT↔MODULE | 19 | 188 |

---

## Actionable Conflicts: LOCATION↔TECHNOLOGY/PROJECT

The **LOCATION↔TECHNOLOGY** (22 labels, 494 occ) and **LOCATION↔PROJECT** (20 labels, 279 occ) pairs contain extractable noise from blockchain/network names incorrectly typed as LOCATION.

### Top LOCATION Conflicts

| Entity | Location Occ | Other Types | Other Occ | Action |
|--------|--------------|-------------|-----------|--------|
| us | 41 | ORGANIZATION | 2 | **Keep** - valid location |
| amazon | 40 | ORGANIZATION | 2 | **Keep** - valid location |
| polygon | 17 | ORG/PROJECT/TECH | 64 | **FIX-011** - blockchain L2 |
| global south | 16 | CONCEPT | 3 | **Keep** - valid location |
| base | 14 | MODULE/ORG/PROJECT/TECH | 95 | **Review** - ambiguous |
| sharamentsa | 11 | ORGANIZATION | 1 | **Keep** - valid location |
| eu | 10 | ORGANIZATION | 4 | **Keep** - valid location |
| ethereum | 4 | ORG/PROJECT/TECH | 154 | **FIX-011** - blockchain |
| regen testnet | 4 | PROJECT/TECH | 35 | **Keep** - valid network |
| solana | 3 | ORG/PROJECT/TECH/VALIDATOR | 91 | **FIX-011** - blockchain |
| arbitrum | 2 | PROJECT/TECH | 12 | **FIX-011** - L2 chain |
| mainnet | 2 | CONCEPT/PROJECT/TECH | 51 | **Review** - generic term |

### FIX-011 Candidates (Blockchain Names as LOCATION)

These entities should NEVER be typed as LOCATION:

| Entity | LOCATION Occurrences | Should Be |
|--------|---------------------|-----------|
| ethereum | 4 | TECHNOLOGY/PROJECT |
| polygon | 17 | TECHNOLOGY/PROJECT |
| solana | 3 | TECHNOLOGY/PROJECT |
| arbitrum | 2 | TECHNOLOGY/PROJECT |
| optimism | (to verify) | TECHNOLOGY/PROJECT |
| avalanche | (to verify) | TECHNOLOGY/PROJECT |

**Impact:** ~26+ wrong-type occurrences preventable in future extractions.

---

## Top CONCEPT↔TECHNOLOGY Conflicts (Sample)

| Entity | CONCEPT Occ | TECHNOLOGY Occ | Classification |
|--------|-------------|----------------|----------------|
| sparql | 29 | 186 | Polysemy (query lang + tech) |
| agent-based modeling | 178 | 4 | **Wrong-type** (TECH should be 0) |
| blockchain | 29 | 148 | Polysemy |
| ai | 3 | 141 | Polysemy |
| mcp server | 1 | 134 | **Wrong-type** (CONCEPT should be 0) |
| knowledge graph | 117 | 16 | Polysemy |
| typescript | 1 | 127 | **Wrong-type** (CONCEPT should be 0) |
| discourse | 2 | 125 | Polysemy (platform + generic) |
| semantic search | 95 | 8 | Polysemy |
| web3 | 65 | 37 | Polysemy |

---

## Recommendations

### High Priority (Actionable Now)

1. **FIX-011: Block LOCATION for blockchain names**
   - Entities: ethereum, polygon, solana, arbitrum, optimism, avalanche, base
   - Impact: ~26+ wrong-type occurrences prevented
   - Complexity: Simple blocklist in EntityQualityFilter

### Medium Priority (Batch cleanup)

2. **FIX-010: Remove single-occurrence wrong-type noise**
   - typescript as CONCEPT (1)
   - mcp server as CONCEPT (1)
   - agent-based modeling as TECHNOLOGY (4)
   - Impact: ~6 rows removed

### Low Priority (Future consideration)

3. **Ontology-level solution**
   - Define parent/child type relationships (TECHNOLOGY is-a CONCEPT)
   - Would reduce "conflicts" that are really hierarchical relationships

---

## Why This Matters

### Impact on GraphRAG/Hybrid Retrieval

1. **Query ambiguity**: Searching for "ethereum LOCATION" returns wrong results
2. **Entity linking failures**: Can't properly link mentions to canonical entities
3. **Embedding quality**: Wrong-type entities pollute vector space

### Impact on Knowledge Graph Quality

1. **Queryability**: SPARQL queries by type return incomplete/wrong results
2. **Visualization**: Type-based graph layouts become cluttered
3. **Downstream analysis**: Entity counts by type are inaccurate

---

*Report generated for Type Conflict Sprint - Cycle 2026-01 Week 2*
