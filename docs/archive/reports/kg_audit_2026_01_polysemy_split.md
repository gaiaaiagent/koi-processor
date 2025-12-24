# Knowledge Graph Polysemy Split Report - Cycle 2026-01

**Generated:** 2025-12-24 04:40:47
**Database:** eliza

---

## Summary

| Category | Labels | Percentage |
| --- | --- | --- |
| Total type conflicts | 2,743 | 100% |
| Expected polysemy (allowlist) | 1,561 | 56.9% |
| **Unexpected conflicts (actionable)** | **1,182** | **43.1%** |

## Expected Polysemy Pairs (Allowlist)

These type pairs represent legitimate multi-type entities:

- **CONCEPT↔PROCESS**
- **CONCEPT↔PROJECT**
- **CONCEPT↔TECHNOLOGY**
- **ORGANIZATION↔PROJECT**
- **PROJECT↔TECHNOLOGY**

Conflicts where ALL type pairs are in this allowlist are classified as expected polysemy.

## Unexpected Pair Distribution

Type pairs NOT in the allowlist (sorted by label count):

| Type Pair | Labels | Total Occurrences |
| --- | --- | --- |
| ORGANIZATION↔TECHNOLOGY | 169 | 4,882 |
| CONCEPT↔ORGANIZATION | 157 | 2,711 |
| CONCEPT↔STANDARD | 131 | 1,801 |
| PROCESS↔TECHNOLOGY | 115 | 1,572 |
| STANDARD↔TECHNOLOGY | 87 | 1,538 |
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

## Top 20 Unexpected Conflicts (Actionable)

These are the highest-occurrence conflicts with at least one unexpected type pair:

| Label | Total Occ | Types | Unexpected Pairs |
| --- | --- | --- | --- |
| notion | 336 | TECHNOLOGY(308), ORGANIZATION(27), PROJECT(1) | ORGANIZATION↔TECHNOLOGY |
| regen commons | 317 | ORGANIZATION(151), PROJECT(147), CONCEPT(19) | CONCEPT↔ORGANIZATION |
| koi | 236 | PROJECT(166), TECHNOLOGY(65), PERSON(2), CONCEPT(2), STANDARD(1) | PERSON↔PROJECT, PROJECT↔STANDARD, PERSON↔TECHNOLOGY, STANDARD↔TECHNOLOGY, CONCEPT↔PERSON, PERSON↔STANDARD, CONCEPT↔STANDARD |
| aerodrome | 234 | TECHNOLOGY(100), PROJECT(98), ORGANIZATION(36) | ORGANIZATION↔TECHNOLOGY |
| sparql | 224 | TECHNOLOGY(186), CONCEPT(29), STANDARD(9) | STANDARD↔TECHNOLOGY, CONCEPT↔STANDARD |
| telegram | 219 | TECHNOLOGY(212), ORGANIZATION(7) | ORGANIZATION↔TECHNOLOGY |
| youtube | 212 | TECHNOLOGY(208), ORGANIZATION(4) | ORGANIZATION↔TECHNOLOGY |
| discord | 208 | TECHNOLOGY(193), ORGANIZATION(15) | ORGANIZATION↔TECHNOLOGY |
| agent-based modeling | 184 | CONCEPT(178), TECHNOLOGY(4), PROJECT(1), PROCESS(1) | PROCESS↔TECHNOLOGY, PROCESS↔PROJECT |
| hydrax | 183 | TECHNOLOGY(83), PROJECT(81), ORGANIZATION(19) | ORGANIZATION↔TECHNOLOGY |
| twitter | 179 | TECHNOLOGY(164), ORGANIZATION(15) | ORGANIZATION↔TECHNOLOGY |
| regen tokenomics | 166 | CONCEPT(117), PROJECT(43), ORGANIZATION(6) | CONCEPT↔ORGANIZATION |
| ethereum | 154 | TECHNOLOGY(128), PROJECT(17), ORGANIZATION(9) | ORGANIZATION↔TECHNOLOGY |
| exchequer.fi | 148 | PROJECT(92), ORGANIZATION(45), TECHNOLOGY(11) | ORGANIZATION↔TECHNOLOGY |
| discourse | 139 | TECHNOLOGY(125), ORGANIZATION(12), CONCEPT(2) | ORGANIZATION↔TECHNOLOGY, CONCEPT↔ORGANIZATION |
| liquidity dao | 124 | ORGANIZATION(97), PROJECT(15), CONCEPT(12) | CONCEPT↔ORGANIZATION |
| usdc | 123 | TECHNOLOGY(94), MATERIAL(14), CONCEPT(13), PROJECT(2) | MATERIAL↔TECHNOLOGY, CONCEPT↔MATERIAL, MATERIAL↔PROJECT |
| biodiversity | 113 | CONCEPT(112), MATERIAL(1) | CONCEPT↔MATERIAL |
| rdf | 106 | TECHNOLOGY(52), CONCEPT(31), STANDARD(23) | STANDARD↔TECHNOLOGY, CONCEPT↔STANDARD |
| mcp | 104 | TECHNOLOGY(91), STANDARD(5), CONCEPT(4), PROJECT(4) | STANDARD↔TECHNOLOGY, CONCEPT↔STANDARD, PROJECT↔STANDARD |

## Type Distribution in Unexpected Conflicts

| Type | Labels | Total Occurrences |
| --- | --- | --- |
| CONCEPT | 632 | 3,550 |
| TECHNOLOGY | 474 | 4,354 |
| ORGANIZATION | 368 | 2,072 |
| PROJECT | 302 | 1,876 |
| STANDARD | 215 | 464 |
| PROCESS | 210 | 524 |
| API_MESSAGE | 88 | 227 |
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
| blockchain | 177 | TECHNOLOGY(148), CONCEPT(29) |
| koi-processor | 161 | PROJECT(107), TECHNOLOGY(54) |
| regen-koi-mcp | 151 | PROJECT(91), TECHNOLOGY(60) |
| ai | 144 | TECHNOLOGY(141), CONCEPT(3) |
| knowledge graph | 133 | CONCEPT(117), TECHNOLOGY(16) |
| web3 | 102 | CONCEPT(65), TECHNOLOGY(37) |
| refi | 101 | CONCEPT(100), PROJECT(1) |
| vector search | 100 | CONCEPT(73), TECHNOLOGY(27) |
| koi-sensors | 93 | PROJECT(59), TECHNOLOGY(34) |
| regen token economy | 88 | CONCEPT(87), PROJECT(1) |

## Recommendations

### Priority Actions

1. **Review top unexpected conflicts** - Determine if they are:
   - True extraction errors (fix/remove wrong type)
   - Missing from allowlist (add pair if legitimate)

2. **Expand allowlist if needed** - Consider adding:
   - `ORGANIZATION↔TECHNOLOGY` (platforms that are also companies)
   - `CONCEPT↔STANDARD` (standards that are also concepts)
   - `STANDARD↔TECHNOLOGY` (tech standards)

3. **Target remaining wrong-type noise** - Low-occurrence unexpected types

---

*Report generated by `scripts/kg_audit_polysemy_report.py`*