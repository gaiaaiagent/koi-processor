# Entity Variants Report - Cycle 2026-01

**Generated:** 2025-12-24 04:41:34
**Database:** eliza
**Labels analyzed:** 10

---

## Overview

This report analyzes entity variants (same label, different types) to determine:
- Which are **true polysemy** (legitimate multi-type entities)
- Which are **typing drift** (extraction errors to fix)

**Labels analyzed:**
- notion
- koi
- governance
- regen commons
- aerodrome
- sparql
- telegram
- youtube
- discord
- agent-based modeling

---

## Variant Analysis

### notion

**Variants found:** 3

| Type | Occurrences | % | URI |
| --- | --- | --- | --- |
| TECHNOLOGY | 308 | 91.7% | https://regen.network/tech/56cea43387f8a388 |
| ORGANIZATION | 27 | 8.0% | https://regen.network/org/4ae48cfc0b1356b2 |
| PROJECT | 1 | 0.3% | https://regen.network/project/afa20c31488348c3 |

#### TECHNOLOGY (ID: 51)

**Relationships:** 0 total (0 as subject, 0 as object)




#### ORGANIZATION (ID: 1454)

**Relationships:** 0 total (0 as subject, 0 as object)




#### PROJECT (ID: 24272)

**Relationships:** 0 total (0 as subject, 0 as object)




**Classification:**
- **Mixed**: Likely polysemy with some wrong-type noise in: PROJECT

---

### koi

**Variants found:** 5

| Type | Occurrences | % | URI |
| --- | --- | --- | --- |
| PROJECT | 166 | 70.3% | https://regen.network/project/f700592737609a3e |
| TECHNOLOGY | 65 | 27.5% | https://regen.network/tech/d31d9527cb5182b7 |
| CONCEPT | 2 | 0.8% | https://regen.network/concept/0e0b8dd3ff1d86fb |
| PERSON | 2 | 0.8% | https://regen.network/person/4bc92156608025bf |
| STANDARD | 1 | 0.4% | https://regen.network/standard/b05b76263896c8a5 |

#### PROJECT (ID: 170)

**Relationships:** 0 total (0 as subject, 0 as object)




#### TECHNOLOGY (ID: 12)

**Relationships:** 0 total (0 as subject, 0 as object)




#### CONCEPT (ID: 6535)

**Relationships:** 0 total (0 as subject, 0 as object)




#### PERSON (ID: 6928)

**Relationships:** 0 total (0 as subject, 0 as object)




#### STANDARD (ID: 27552)

**Relationships:** 0 total (0 as subject, 0 as object)




**Classification:**
- **Mixed**: Likely polysemy with some wrong-type noise in: CONCEPT, PERSON, STANDARD

---

### governance

**Variants found:** 1

| Type | Occurrences | % | URI |
| --- | --- | --- | --- |
| CONCEPT | 274 | 100.0% | https://regen.network/concept/73ab5586426cb823 |

#### CONCEPT (ID: 640)

**Relationships:** 49 total (15 as subject, 34 as object)

**As subject (→):**
- `manages` (2)
- `enables` (2)
- `approves` (2)
- `associated_with` (1)
- `decides` (1)

**As object (←):**
- `supports` (6)
- `uses` (4)
- `relates_to` (3)
- `contains` (2)
- `mentions` (2)

**Top connected entities:**
| Entity | Type | Connections |
| --- | --- | --- |
| Regen Network | ORGANIZATION | 2 |
| Regen Registry | ORGANIZATION | 2 |
| AI agents | TECHNOLOGY | 2 |
| DAOdao | PROJECT | 2 |
| burning features | CONCEPT | 1 |

**Classification:**
- Single type, no conflict

---

### regen commons

**Variants found:** 3

| Type | Occurrences | % | URI |
| --- | --- | --- | --- |
| ORGANIZATION | 151 | 47.6% | https://regen.network/org/5a687fc38a3e8e80 |
| PROJECT | 147 | 46.4% | https://regen.network/project/9cbeb0e1ce6cbe52 |
| CONCEPT | 19 | 6.0% | https://regen.network/concept/8217d07a4398f313 |

#### ORGANIZATION (ID: 137)

**Relationships:** 0 total (0 as subject, 0 as object)




#### PROJECT (ID: 70)

**Relationships:** 0 total (0 as subject, 0 as object)




#### CONCEPT (ID: 1410)

**Relationships:** 0 total (0 as subject, 0 as object)




**Classification:**
- **Multi-polysemy**: Entity legitimately appears as multiple types

---

### aerodrome

**Variants found:** 3

| Type | Occurrences | % | URI |
| --- | --- | --- | --- |
| TECHNOLOGY | 100 | 42.7% | https://regen.network/tech/7aa7f35746d28480 |
| PROJECT | 98 | 41.9% | https://regen.network/project/dcddc63bea378856 |
| ORGANIZATION | 36 | 15.4% | https://regen.network/org/fd0f5a2eb5309009 |

#### TECHNOLOGY (ID: 54)

**Relationships:** 1 total (0 as subject, 1 as object)


**As object (←):**
- `associated_with` (1)

**Top connected entities:**
| Entity | Type | Connections |
| --- | --- | --- |
| Hydrax | TECHNOLOGY | 1 |

#### PROJECT (ID: 118)

**Relationships:** 0 total (0 as subject, 0 as object)




#### ORGANIZATION (ID: 2990)

**Relationships:** 0 total (0 as subject, 0 as object)




**Classification:**
- **Multi-polysemy**: Entity legitimately appears as multiple types

---

### sparql

**Variants found:** 3

| Type | Occurrences | % | URI |
| --- | --- | --- | --- |
| TECHNOLOGY | 186 | 83.0% | https://regen.network/tech/6fc3868bf14a0de0 |
| CONCEPT | 29 | 12.9% | https://regen.network/concept/700362002a65d489 |
| STANDARD | 9 | 4.0% | https://regen.network/standard/feb58a8d48661ba7 |

#### TECHNOLOGY (ID: 304)

**Relationships:** 6 total (4 as subject, 2 as object)

**As subject (→):**
- `supports` (3)
- `participates_in` (1)

**As object (←):**
- `combines` (1)
- `provides` (1)

**Top connected entities:**
| Entity | Type | Connections |
| --- | --- | --- |
| Apache Jena Fuseki | TECHNOLOGY | 1 |
| database query optimization | CONCEPT | 1 |
| federation | CONCEPT | 1 |
| OWL | STANDARD | 1 |
| query fusion | CONCEPT | 1 |

#### CONCEPT (ID: 2083)

**Relationships:** 0 total (0 as subject, 0 as object)




#### STANDARD (ID: 1569)

**Relationships:** 0 total (0 as subject, 0 as object)




**Classification:**
- **Multi-polysemy**: Entity legitimately appears as multiple types

---

### telegram

**Variants found:** 2

| Type | Occurrences | % | URI |
| --- | --- | --- | --- |
| TECHNOLOGY | 212 | 96.8% | https://regen.network/tech/397c3cc5ae5e6516 |
| ORGANIZATION | 7 | 3.2% | https://regen.network/org/5f9a0f55b85b9fd2 |

#### TECHNOLOGY (ID: 17)

**Relationships:** 0 total (0 as subject, 0 as object)




#### ORGANIZATION (ID: 666)

**Relationships:** 0 total (0 as subject, 0 as object)




**Classification:**
- **Polysemy candidate**: Both TECHNOLOGY and ORGANIZATION have significant occurrences

---

### youtube

**Variants found:** 2

| Type | Occurrences | % | URI |
| --- | --- | --- | --- |
| TECHNOLOGY | 208 | 98.1% | https://regen.network/tech/8f504a635793e235 |
| ORGANIZATION | 4 | 1.9% | https://regen.network/org/cb368a41b3526d10 |

#### TECHNOLOGY (ID: 184)

**Relationships:** 17 total (8 as subject, 9 as object)

**As subject (→):**
- `monitors` (4)
- `supports` (3)
- `hosts` (1)

**As object (←):**
- `operates` (4)
- `contains` (3)
- `uses` (2)

**Top connected entities:**
| Entity | Type | Connections |
| --- | --- | --- |
| Regen Foundation | ORGANIZATION | 5 |
| Regen Network | ORGANIZATION | 5 |
| FirstPrinciplesAI | ORGANIZATION | 4 |
| First Principles AI | ORGANIZATION | 2 |
| regeneration | CONCEPT | 1 |

#### ORGANIZATION (ID: 4710)

**Relationships:** 0 total (0 as subject, 0 as object)




**Classification:**
- **Polysemy candidate**: Both TECHNOLOGY and ORGANIZATION have significant occurrences

---

### discord

**Variants found:** 2

| Type | Occurrences | % | URI |
| --- | --- | --- | --- |
| TECHNOLOGY | 193 | 92.8% | https://regen.network/tech/b0593e5e805e1b0a |
| ORGANIZATION | 15 | 7.2% | https://regen.network/org/f7843d2d2ea9304c |

#### TECHNOLOGY (ID: 71)

**Relationships:** 0 total (0 as subject, 0 as object)




#### ORGANIZATION (ID: 667)

**Relationships:** 0 total (0 as subject, 0 as object)




**Classification:**
- **Polysemy candidate**: Both TECHNOLOGY and ORGANIZATION have significant occurrences

---

### agent-based modeling

**Variants found:** 4

| Type | Occurrences | % | URI |
| --- | --- | --- | --- |
| CONCEPT | 178 | 96.7% | https://regen.network/concept/1a1298e7c731949c |
| TECHNOLOGY | 4 | 2.2% | https://regen.network/tech/3b577a70396c690b |
| PROJECT | 1 | 0.5% | https://regen.network/project/f89354515ee2b104 |
| PROCESS | 1 | 0.5% | https://regen.network/process/87c9b902d29e991f |

#### CONCEPT (ID: 58)

**Relationships:** 5 total (4 as subject, 1 as object)

**As subject (→):**
- `relates_to` (2)
- `defines` (1)
- `uses` (1)

**As object (←):**
- `supports` (1)

**Top connected entities:**
| Entity | Type | Connections |
| --- | --- | --- |
| DAOdao | PROJECT | 1 |
| digital twin | CONCEPT | 1 |
| economic model | CONCEPT | 1 |
| Regen Token Economy | CONCEPT | 1 |
| Strategic Partnerships | CONCEPT | 1 |

#### TECHNOLOGY (ID: 16915)

**Relationships:** 0 total (0 as subject, 0 as object)




#### PROJECT (ID: 17220)

**Relationships:** 0 total (0 as subject, 0 as object)




#### PROCESS (ID: 22981)

**Relationships:** 0 total (0 as subject, 0 as object)




**Classification:**
- **Mixed**: Likely polysemy with some wrong-type noise in: PROJECT, PROCESS

---

## Summary & Recommendations

| Label | Classification | Recommended Action |
| --- | --- | --- |
| notion | Wrong-type noise | Remove low-occ types: PROJECT |
| koi | Wrong-type noise | Remove low-occ types: CONCEPT, PERSON, STANDARD |
| governance | Single type | None needed |
| regen commons | True polysemy | Keep all types |
| aerodrome | True polysemy | Keep all types |
| sparql | True polysemy | Keep all types |
| telegram | True polysemy | Keep all types |
| youtube | True polysemy | Keep all types |
| discord | True polysemy | Keep all types |
| agent-based modeling | Wrong-type noise | Remove low-occ types: PROJECT, PROCESS |

---

*Report generated by `scripts/entity_variants_report.py`*