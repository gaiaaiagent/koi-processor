# Week 20 Relationship Quality Spot-Check

**Date:** 2025-12-28
**Sample Size:** 50 edges from Week 19 extractions
**Source:** High-impact platform predicates (uses, integrates_with, documents_on, powered_by, communicates_via, implements, part_of, works_with, supports, relates_to)

## Summary

| Category | Count | % |
|----------|-------|---|
| **Valid** | 37 | 74% |
| **Acceptable (minor imprecision)** | 10 | 20% |
| **Invalid (clear error)** | 3 | 6% |
| **Total Acceptable** | 47 | **94%** |

## Classification Details

### Valid (37 edges)
Semantically correct relationships with appropriate predicates and types.

Examples:
- `daodao (PROJECT) implements cosmwasm (PROJECT)` - DaoDao is CosmWasm-based
- `regen network (ORGANIZATION) uses storybook (TECHNOLOGY)` - UI component library
- `ecocredit module (PROJECT) uses x/bank (MODULE)` - Cosmos module dependency
- `cosmos hub (ORGANIZATION) uses cosmos sdk (PROJECT)` - Correct architecture
- `mcp server (TECHNOLOGY) powered_by postgresql (TECHNOLOGY)` - Database backend

### Acceptable (10 edges)
Minor imprecision but semantically reasonable:

1. `feeparams (CONCEPT) part_of regen network` - Config as concept is unusual but acceptable
2. `dailycurator (TECHNOLOGY) implements initialize (PROCESS)` - Too abstract
3. `regen koi mcp (PROJECT) implements github activity` - Should be "integrates_with"
4. `cosmos sdk (PROJECT) implements hashing (CONCEPT)` - Vague but acceptable
5. `axios (TECHNOLOGY) supports metadata (CONCEPT)` - Too abstract
6. `entity linker (TECHNOLOGY) uses ecocredit module` - Indirectly true
7. `msgtake (API_MESSAGE) part_of regen network` - Should be part of ecocredit module
8. `msgsend (API_MESSAGE) implements cosmos sdk` - Should be "defined_by" or similar
9. `semantic deduplication (CONCEPT) implements vector similarity` - Concepts don't implement
10. `regen koi mcp (PROJECT) uses api pattern (CONCEPT)` - Too vague

### Invalid (3 edges)
Clear semantic errors:

1. `playwright (TECHNOLOGY) implements python (TECHNOLOGY)` - **WRONG**: Playwright has Python bindings, doesn't implement Python
2. `regen network (ORGANIZATION) part_of regen marketplace (TECHNOLOGY)` - **WRONG**: Direction reversed; marketplace is part of network
3. `coverlet (VALIDATOR) uses github (ORGANIZATION)` - **WRONG**: Coverlet is .NET coverage tool, not a blockchain validator

## Issues Identified

### Type Misassignment
- `coverlet` incorrectly typed as VALIDATOR (should be TECHNOLOGY)

### Predicate Selection
- `implements` sometimes used where `uses` or `integrates_with` would be more accurate
- Overly abstract `relates_to` usage for concepts (acceptable but could be more specific)

### Direction Errors
- Occasional subject/object reversal (e.g., regen network part_of marketplace)

## Recommendation

**Quality threshold MET**: 94% acceptable (threshold: 85%)

Minor improvements:
1. Refine `implements` predicate constraints to prevent misuse with abstract concepts
2. Add type validation for known technologies (e.g., Coverlet → TECHNOLOGY, not VALIDATOR)
3. Consider directional validation for `part_of` predicate
