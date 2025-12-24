# Knowledge Graph Polysemy Split Report - Cycle 2026-01

**Generated:** 2025-12-24 04:57:49
**Database:** eliza

---

## Summary

| Category | Labels | Percentage |
| --- | --- | --- |
| Total type conflicts | 2,743 | 100% |
| Expected polysemy (allowlist) | 1,816 | 66.2% |
| **Unexpected conflicts (actionable)** | **927** | **33.8%** |

## Expected Polysemy Pairs (Allowlist)

These type pairs represent legitimate multi-type entities:

- **CONCEPT↔PROCESS**
- **CONCEPT↔PROJECT**
- **CONCEPT↔STANDARD**
- **CONCEPT↔TECHNOLOGY**
- **ORGANIZATION↔PROJECT**
- **ORGANIZATION↔TECHNOLOGY**
- **PROJECT↔TECHNOLOGY**
- **STANDARD↔TECHNOLOGY**

Conflicts where ALL type pairs are in this allowlist are classified as expected polysemy.

## Unexpected Pair Distribution

Type pairs NOT in the allowlist (sorted by label count):

| Type Pair | Labels | Total Occurrences |
| --- | --- | --- |
| CONCEPT↔ORGANIZATION | 157 | 2,711 |
| PROCESS↔TECHNOLOGY | 115 | 1,572 |
| CONCEPT↔MATERIAL | 62 | 1,132 |
| CONCEPT↔CREDIT_CLASS | 54 | 393 |
| PROCESS↔PROJECT | 49 | 925 |
| CONCEPT↔GOVERNANCE_PROPOSAL | 47 | 323 |
| API_MESSAGE↔CONCEPT | 40 | 297 |
| ORGANIZATION↔VALIDATOR | 36 | 498 |
| PROJECT↔STANDARD | 35 | 790 |
| PROJECT↔VALIDATOR | 33 | 365 |
| TECHNOLOGY↔VALIDATOR | 26 | 321 |
| CONCEPT↔EVIDENCE | 26 | 244 |
| API_MESSAGE↔TECHNOLOGY | 26 | 177 |
| MODULE↔TECHNOLOGY | 25 | 519 |
| CONCEPT↔LICENSE | 22 | 209 |
| PROCESS↔STANDARD | 22 | 289 |
| API_MESSAGE↔EVENT | 22 | 86 |
| MATERIAL↔TECHNOLOGY | 21 | 511 |
| CLAIM↔CONCEPT | 21 | 130 |
| CONCEPT↔MODULE | 19 | 287 |

## Top 20 Unexpected Conflicts (Actionable)

These are the highest-occurrence conflicts with at least one unexpected type pair:

| Label | Total Occ | Types | Unexpected Pairs |
| --- | --- | --- | --- |
| regen commons | 317 | ORGANIZATION(151), PROJECT(147), CONCEPT(19) | CONCEPT↔ORGANIZATION |
| koi | 236 | PROJECT(166), TECHNOLOGY(65), PERSON(2), CONCEPT(2), STANDARD(1) | PERSON↔PROJECT, PROJECT↔STANDARD, PERSON↔TECHNOLOGY, CONCEPT↔PERSON, PERSON↔STANDARD |
| agent-based modeling | 184 | CONCEPT(178), TECHNOLOGY(4), PROJECT(1), PROCESS(1) | PROCESS↔TECHNOLOGY, PROCESS↔PROJECT |
| regen tokenomics | 166 | CONCEPT(117), PROJECT(43), ORGANIZATION(6) | CONCEPT↔ORGANIZATION |
| discourse | 139 | TECHNOLOGY(125), ORGANIZATION(12), CONCEPT(2) | CONCEPT↔ORGANIZATION |
| liquidity dao | 124 | ORGANIZATION(97), PROJECT(15), CONCEPT(12) | CONCEPT↔ORGANIZATION |
| usdc | 123 | TECHNOLOGY(94), MATERIAL(14), CONCEPT(13), PROJECT(2) | MATERIAL↔TECHNOLOGY, CONCEPT↔MATERIAL, MATERIAL↔PROJECT |
| biodiversity | 113 | CONCEPT(112), MATERIAL(1) | CONCEPT↔MATERIAL |
| mcp | 104 | TECHNOLOGY(91), STANDARD(5), CONCEPT(4), PROJECT(4) | PROJECT↔STANDARD |
| semantic search | 104 | CONCEPT(95), TECHNOLOGY(8), PROCESS(1) | PROCESS↔TECHNOLOGY |
| verification | 103 | PROCESS(86), CONCEPT(15), TECHNOLOGY(1), PROJECT(1) | PROCESS↔TECHNOLOGY, PROCESS↔PROJECT |
| base | 95 | TECHNOLOGY(68), PROJECT(24), ORGANIZATION(2), MODULE(1) | MODULE↔TECHNOLOGY, MODULE↔PROJECT, MODULE↔ORGANIZATION |
| solana | 91 | TECHNOLOGY(59), PROJECT(22), ORGANIZATION(9), VALIDATOR(1) | TECHNOLOGY↔VALIDATOR, PROJECT↔VALIDATOR, ORGANIZATION↔VALIDATOR |
| regeneration | 88 | CONCEPT(85), ORGANIZATION(2), PROCESS(1) | CONCEPT↔ORGANIZATION, ORGANIZATION↔PROCESS |
| r&d | 86 | ORGANIZATION(69), CONCEPT(10), PROCESS(5), PROJECT(2) | CONCEPT↔ORGANIZATION, ORGANIZATION↔PROCESS, PROCESS↔PROJECT |
| hybrid search | 80 | CONCEPT(73), TECHNOLOGY(6), PROCESS(1) | PROCESS↔TECHNOLOGY |
| dao | 74 | CONCEPT(44), ORGANIZATION(22), TECHNOLOGY(7), PROJECT(1) | CONCEPT↔ORGANIZATION |
| carbon sequestration | 74 | CONCEPT(73), MATERIAL(1) | CONCEPT↔MATERIAL |
| json-ld | 72 | TECHNOLOGY(32), STANDARD(21), CONCEPT(18), LICENSE(1) | LICENSE↔TECHNOLOGY, LICENSE↔STANDARD, CONCEPT↔LICENSE |
| the commons | 71 | CONCEPT(43), ORGANIZATION(28) | CONCEPT↔ORGANIZATION |

## Type Distribution in Unexpected Conflicts

| Type | Labels | Total Occurrences |
| --- | --- | --- |
| CONCEPT | 544 | 3,250 |
| TECHNOLOGY | 287 | 1,683 |
| PROJECT | 251 | 1,346 |
| ORGANIZATION | 242 | 1,196 |
| PROCESS | 210 | 524 |
| API_MESSAGE | 88 | 227 |
| STANDARD | 86 | 224 |
| VALIDATOR | 75 | 296 |
| MATERIAL | 71 | 250 |
| CREDIT_CLASS | 70 | 133 |
| GOVERNANCE_PROPOSAL | 68 | 185 |
| EVENT | 55 | 92 |
| LOCATION | 48 | 183 |
| EVIDENCE | 41 | 82 |
| PERSON | 40 | 121 |
| MODULE | 38 | 158 |
| LICENSE | 33 | 71 |
| CLAIM | 25 | 35 |
| QUESTION | 14 | 15 |
| KEEPER | 3 | 20 |

## Sample Expected Polysemy (Top 10)

For reference, these high-occurrence conflicts are classified as expected polysemy:

| Label | Total Occ | Types |
| --- | --- | --- |
| notion | 336 | TECHNOLOGY(308), ORGANIZATION(27), PROJECT(1) |
| aerodrome | 234 | TECHNOLOGY(100), PROJECT(98), ORGANIZATION(36) |
| sparql | 224 | TECHNOLOGY(186), CONCEPT(29), STANDARD(9) |
| telegram | 219 | TECHNOLOGY(212), ORGANIZATION(7) |
| youtube | 212 | TECHNOLOGY(208), ORGANIZATION(4) |
| discord | 208 | TECHNOLOGY(193), ORGANIZATION(15) |
| hydrax | 183 | TECHNOLOGY(83), PROJECT(81), ORGANIZATION(19) |
| twitter | 179 | TECHNOLOGY(164), ORGANIZATION(15) |
| blockchain | 177 | TECHNOLOGY(148), CONCEPT(29) |
| koi-processor | 161 | PROJECT(107), TECHNOLOGY(54) |

## Recommendations

### Priority Actions

1. **Review top unexpected conflicts** - Determine if they are:
   - True extraction errors (fix/remove wrong type)
   - Missing from allowlist (add pair if legitimate)

2. **Expand allowlist if needed** - Consider adding:

3. **Target remaining wrong-type noise** - Low-occurrence unexpected types

---

*Report generated by `scripts/kg_audit_polysemy_report.py`*