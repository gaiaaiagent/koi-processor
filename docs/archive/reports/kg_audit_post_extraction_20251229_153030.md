# Knowledge Graph Audit Report - Cycle 2026-01

**Generated:** 2025-12-29 15:30:31
**Database:** eliza

---

## Summary Metrics

| Metric | Value |
| --- | --- |
| Entities (entity_registry) | 29,670 |
| Relationships (koi_relationships) | 21,566 |
| Distinct Predicates | 1,495 |

## Quality Gates

| Gate | Check | Count | Status |
| --- | --- | --- | --- |
| A | No http:// URIs | 0 | **PASS** |
| B | No generic ENTITY type | 0 | **PASS** |
| C | No self-referential | 0 | **PASS** |
| D | No HumanActor type | 0 | **PASS** |

## Entity Type Distribution

| Type | Count |
| --- | --- |
| CONCEPT | 13,946 |
| TECHNOLOGY | 4,960 |
| PROCESS | 2,248 |
| PROJECT | 1,807 |
| ORGANIZATION | 1,680 |
| PERSON | 802 |
| CLAIM | 598 |
| API_MESSAGE | 527 |
| STANDARD | 500 |
| GOVERNANCE_PROPOSAL | 488 |
| LOCATION | 416 |
| MATERIAL | 263 |
| EVIDENCE | 261 |
| EVENT | 245 |
| QUESTION | 235 |
| VALIDATOR | 218 |
| CREDIT_CLASS | 175 |
| MODULE | 142 |
| LICENSE | 116 |
| KEEPER | 31 |
| DOCUMENT | 4 |
| ERROR | 2 |
| VERSION | 2 |
| FUNCTION | 2 |
| DATE | 1 |
| DISCUSSION | 1 |

## Top 25 Entities by Occurrence

| Entity | Type | Occurrences |
| --- | --- | --- |
| contracts | TECHNOLOGY | 8,285 |
| Regen Network | ORGANIZATION | 7,744 |
| Regen Ledger | TECHNOLOGY | 3,833 |
| Fixed Cap + Dynamic Supply Model | CONCEPT | 2,733 |
| GitHub | ORGANIZATION | 2,062 |
| Ecocredit Module | PROJECT | 1,941 |
| React | TECHNOLOGY | 1,677 |
| Cosmos SDK | PROJECT | 1,613 |
| Validation processes | CONCEPT | 1,292 |
| ethical capital formation | CONCEPT | 1,062 |
| RemoveAllowedDenom | API_MESSAGE | 1,056 |
| Regen Registry | ORGANIZATION | 998 |
| Gregory Landua | PERSON | 964 |
| Cosmoshub | PROJECT | 944 |
| Hydrex | ORGANIZATION | 905 |
| marketplace | MODULE | 899 |
| TypeScript | TECHNOLOGY | 899 |
| postgresql | TECHNOLOGY | 877 |
| Notion | TECHNOLOGY | 864 |
| Regen Foundation | ORGANIZATION | 851 |
| Python | TECHNOLOGY | 793 |
| regenerative metaphor | CONCEPT | 679 |
| Carbon Credit | CONCEPT | 585 |
| Koi Project | PROJECT | 540 |
| high level application logic | CONCEPT | 518 |

## Top 25 Predicates by Frequency

| Predicate | Count |
| --- | --- |
| supports | 1,979 |
| uses | 1,953 |
| relates_to | 1,363 |
| associated_with | 1,100 |
| includes | 952 |
| part_of | 780 |
| mentions | 732 |
| implements | 663 |
| manages | 603 |
| participates_in | 506 |
| defines | 423 |
| enables | 416 |
| requires | 387 |
| operates | 372 |
| provides | 345 |
| interacts_with | 318 |
| contains | 302 |
| works_with | 301 |
| proposes | 249 |
| is_a | 247 |
| creates | 232 |
| located_in | 226 |
| affects | 180 |
| integrates_with | 174 |
| governs | 171 |

## Top 25 Type Conflicts (Cross-Type Collisions)

Entities with the same normalized name but different types:

| Name | Type Count | Total Occurrences | Types |
| --- | --- | --- | --- |
| contracts | 2 | 8,287 | TECHNOLOGY(8285), CONCEPT(2) |
| validation processes | 2 | 1,293 | CONCEPT(1292), PROCESS(1) |
| removealloweddenom | 2 | 1,060 | API_MESSAGE(1056), GOVERNANCE_PROPOSAL(4) |
| marketplace | 5 | 1,033 | MODULE(899), CONCEPT(73), TECHNOLOGY(38), PROJECT(22), PROCESS(1) |
| cosmoshub | 2 | 948 | PROJECT(944), TECHNOLOGY(4) |
| notion | 2 | 935 | TECHNOLOGY(864), ORGANIZATION(71) |
| hydrex | 3 | 929 | ORGANIZATION(905), TECHNOLOGY(15), PROJECT(9) |
| discord | 2 | 513 | TECHNOLOGY(479), ORGANIZATION(34) |
| discourse | 3 | 474 | TECHNOLOGY(450), ORGANIZATION(21), CONCEPT(3) |
| twitter | 2 | 458 | TECHNOLOGY(428), ORGANIZATION(30) |
| telegram | 2 | 413 | TECHNOLOGY(394), ORGANIZATION(19) |
| koi-processor | 2 | 387 | PROJECT(265), TECHNOLOGY(122) |
| regen commons | 3 | 385 | ORGANIZATION(191), PROJECT(170), CONCEPT(24) |
| go | 2 | 377 | TECHNOLOGY(376), PROJECT(1) |
| medium | 2 | 359 | TECHNOLOGY(307), ORGANIZATION(52) |
| sparql | 3 | 346 | TECHNOLOGY(260), CONCEPT(61), STANDARD(25) |
| koi | 2 | 343 | PROJECT(257), TECHNOLOGY(86) |
| youtube | 2 | 326 | TECHNOLOGY(317), ORGANIZATION(9) |
| msgcreatebatch | 2 | 297 | API_MESSAGE(295), FUNCTION(2) |
| knowledge graph | 2 | 287 | CONCEPT(261), TECHNOLOGY(26) |
| aerodrome | 3 | 268 | TECHNOLOGY(120), PROJECT(104), ORGANIZATION(44) |
| ethereum | 3 | 243 | TECHNOLOGY(198), PROJECT(28), ORGANIZATION(17) |
| blockchain | 2 | 239 | TECHNOLOGY(174), CONCEPT(65) |
| eventupdateprojectadmin | 2 | 233 | EVENT(230), API_MESSAGE(3) |
| basket | 4 | 216 | CONCEPT(108), MODULE(85), API_MESSAGE(20), TECHNOLOGY(3) |

## Remaining Duplicate Clusters (Same Type)

| Name | Type | Count | Total Occurrences |
| --- | --- | --- | --- |
| regen-koi-mcp | PROJECT | 2 | 149 |
| website-sensor | TECHNOLOGY | 2 | 70 |
| regen-data-standards | PROJECT | 2 | 55 |
| web-components | PROJECT | 2 | 46 |
| yt-dlp | TECHNOLOGY | 2 | 25 |
| text-embedding-3-large | TECHNOLOGY | 2 | 15 |
| life-centered stewardship | CONCEPT | 2 | 10 |
| buf.build | TECHNOLOGY | 2 | 7 |
| pr-k_zadov | VALIDATOR | 2 | 7 |
| whole-systems thinking | CONCEPT | 2 | 6 |
| regencommons-discourse | TECHNOLOGY | 2 | 6 |
| unified-v1 | CONCEPT | 2 | 5 |
| amd ryzen 5 3600 hexa-core | TECHNOLOGY | 2 | 5 |
| regen-network/mainnet | PROJECT | 2 | 5 |
| spanish-node | VALIDATOR | 2 | 5 |
| forum-regen-network | TECHNOLOGY | 2 | 4 |
| hetzner ax51-nvme | TECHNOLOGY | 2 | 4 |
| registry-regen-network | TECHNOLOGY | 2 | 4 |
| ubuntu -18 lts | TECHNOLOGY | 2 | 3 |
| cross-chain communication protocol | CONCEPT | 2 | 3 |

## Single-Token PERSON Entities (Ambiguity Tracking)

First names that may refer to multiple people:

| Name | Occurrences |
| --- | --- |
| James | 255 |
| Mark | 205 |
| Julia | 53 |
| Sarah | 50 |
| Jeancarlo | 38 |
| Alice | 35 |
| Monty | 33 |
| Paul | 33 |
| Sari | 30 |
| Chris | 29 |
| S4mmyb | 28 |
| Pete | 28 |
| Bob | 27 |
| Christian_Regen | 27 |
| Robert | 25 |
| Alex | 24 |
| TMO | 22 |
| Sam | 22 |
| Marie | 22 |
| Joel | 21 |

---

*Report generated by `scripts/kg_audit_report.py`*