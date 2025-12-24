# Type Conflict Unexpected Pairs Report - Week 6

**Generated:** 2025-12-24 05:22:23
**Database:** eliza
**Purpose:** Detailed breakdown of conflicts OUTSIDE the polysemy allowlist

---

## Summary

| Category | Labels | Percentage |
| --- | --- | --- |
| Total type conflicts | 2,743 | 100% |
| Expected polysemy (allowlist) | 1,818 | 66.3% |
| **Unexpected conflicts** | **925** | **33.7%** |

## Current Allowlist (8 pairs)

- CONCEPT↔PROCESS
- CONCEPT↔PROJECT
- CONCEPT↔STANDARD
- CONCEPT↔TECHNOLOGY
- ORGANIZATION↔PROJECT
- ORGANIZATION↔TECHNOLOGY
- PROJECT↔TECHNOLOGY
- STANDARD↔TECHNOLOGY

## Unexpected Pairs Summary

Ranked by label count (conflicts contributing to this pair):

| Type Pair | Label Count | Total Occurrences |
| --- | --- | --- |
| CONCEPT↔ORGANIZATION | 157 | 2,711 |
| PROCESS↔TECHNOLOGY | 114 | 1,388 |
| CONCEPT↔MATERIAL | 62 | 1,132 |
| CONCEPT↔CREDIT_CLASS | 54 | 393 |
| PROCESS↔PROJECT | 48 | 741 |
| CONCEPT↔GOVERNANCE_PROPOSAL | 47 | 323 |
| API_MESSAGE↔CONCEPT | 40 | 297 |
| ORGANIZATION↔VALIDATOR | 36 | 498 |
| PROJECT↔STANDARD | 34 | 554 |
| PROJECT↔VALIDATOR | 33 | 365 |
| TECHNOLOGY↔VALIDATOR | 26 | 321 |
| CONCEPT↔EVIDENCE | 26 | 244 |
| API_MESSAGE↔TECHNOLOGY | 26 | 177 |
| MODULE↔TECHNOLOGY | 25 | 519 |
| PROCESS↔STANDARD | 22 | 289 |
| CONCEPT↔LICENSE | 22 | 209 |
| API_MESSAGE↔EVENT | 22 | 86 |
| MATERIAL↔TECHNOLOGY | 21 | 511 |
| CLAIM↔CONCEPT | 21 | 130 |
| CONCEPT↔MODULE | 19 | 287 |
| EVENT↔PROJECT | 18 | 218 |
| LOCATION↔TECHNOLOGY | 17 | 288 |
| CREDIT_CLASS↔PROJECT | 17 | 159 |
| PERSON↔VALIDATOR | 16 | 140 |
| LOCATION↔PROJECT | 15 | 274 |
| ORGANIZATION↔STANDARD | 14 | 139 |
| ORGANIZATION↔PERSON | 13 | 122 |
| CONCEPT↔LOCATION | 12 | 172 |
| EVIDENCE↔TECHNOLOGY | 12 | 144 |
| CONCEPT↔QUESTION | 12 | 85 |
| CONCEPT↔EVENT | 11 | 163 |
| EVIDENCE↔PROJECT | 11 | 149 |
| GOVERNANCE_PROPOSAL↔PROCESS | 11 | 70 |
| MATERIAL↔PROJECT | 10 | 308 |
| ORGANIZATION↔PROCESS | 10 | 302 |
| LOCATION↔ORGANIZATION | 10 | 158 |
| EVIDENCE↔PROCESS | 10 | 143 |
| CREDIT_CLASS↔STANDARD | 10 | 71 |
| MODULE↔PROCESS | 9 | 244 |
| EVENT↔ORGANIZATION | 9 | 128 |
| LICENSE↔STANDARD | 9 | 119 |
| PERSON↔PROJECT | 9 | 84 |
| API_MESSAGE↔PROCESS | 9 | 52 |
| GOVERNANCE_PROPOSAL↔PROJECT | 8 | 88 |
| LICENSE↔TECHNOLOGY | 7 | 107 |
| CREDIT_CLASS↔TECHNOLOGY | 7 | 56 |
| API_MESSAGE↔GOVERNANCE_PROPOSAL | 7 | 39 |
| EVENT↔TECHNOLOGY | 6 | 127 |
| GOVERNANCE_PROPOSAL↔TECHNOLOGY | 6 | 50 |
| EVIDENCE↔STANDARD | 5 | 99 |
| CLAIM↔TECHNOLOGY | 5 | 48 |
| EVENT↔PROCESS | 5 | 16 |
| MODULE↔ORGANIZATION | 4 | 141 |
| MATERIAL↔ORGANIZATION | 4 | 56 |
| LICENSE↔ORGANIZATION | 4 | 35 |
| MODULE↔PROJECT | 3 | 174 |
| MATERIAL↔PROCESS | 3 | 86 |
| EVIDENCE↔MATERIAL | 3 | 74 |
| CREDIT_CLASS↔MATERIAL | 3 | 48 |
| CREDIT_CLASS↔ORGANIZATION | 3 | 46 |
| GOVERNANCE_PROPOSAL↔ORGANIZATION | 3 | 28 |
| LOCATION↔PERSON | 3 | 26 |
| GOVERNANCE_PROPOSAL↔STANDARD | 3 | 23 |
| PERSON↔TECHNOLOGY | 3 | 20 |
| CONCEPT↔PERSON | 3 | 15 |
| EVENT↔LOCATION | 2 | 72 |
| MATERIAL↔STANDARD | 2 | 69 |
| CLAIM↔PROCESS | 2 | 39 |
| CREDIT_CLASS↔PROCESS | 2 | 19 |
| LICENSE↔PROJECT | 2 | 18 |
| KEEPER↔MODULE | 2 | 17 |
| CLAIM↔CREDIT_CLASS | 2 | 11 |
| CLAIM↔STANDARD | 2 | 11 |
| LOCATION↔MATERIAL | 2 | 4 |
| CLAIM↔EVIDENCE | 2 | 4 |
| API_MESSAGE↔MODULE | 1 | 39 |
| PROCESS↔QUESTION | 1 | 28 |
| API_MESSAGE↔PROJECT | 1 | 21 |
| CONCEPT↔KEEPER | 1 | 18 |
| API_MESSAGE↔MATERIAL | 1 | 16 |
| KEEPER↔ORGANIZATION | 1 | 12 |
| CLAIM↔GOVERNANCE_PROPOSAL | 1 | 11 |
| MATERIAL↔PERSON | 1 | 10 |
| MODULE↔QUESTION | 1 | 8 |
| QUESTION↔TECHNOLOGY | 1 | 8 |
| API_MESSAGE↔CREDIT_CLASS | 1 | 6 |
| LOCATION↔STANDARD | 1 | 4 |
| LOCATION↔VALIDATOR | 1 | 4 |
| EVENT↔PERSON | 1 | 3 |
| GOVERNANCE_PROPOSAL↔QUESTION | 1 | 3 |
| CONCEPT↔VALIDATOR | 1 | 2 |
| LICENSE↔PROCESS | 1 | 2 |

## Top 3 Actionable Pairs

These 3 pairs account for **333** labels (36.0% of unexpected conflicts):

1. **CONCEPT↔ORGANIZATION**: 157 labels, 2,711 occurrences
2. **PROCESS↔TECHNOLOGY**: 114 labels, 1,388 occurrences
3. **CONCEPT↔MATERIAL**: 62 labels, 1,132 occurrences

---

## Detailed Pair Breakdowns

For each unexpected pair, showing top 20 labels with per-type occurrence breakdown.

### CONCEPT↔ORGANIZATION

**Labels:** 157 | **Total Occurrences:** 2,711

| Label | Total | CONCEPT | ORGANIZATION | Other Types |
| --- | --- | --- | --- | --- |
| regen commons | 317 | 19 | 151 | PROJECT(147) |
| regen tokenomics | 166 | 117 | 6 | PROJECT(43) |
| discourse | 139 | 2 | 12 | TECHNOLOGY(125) |
| liquidity dao | 124 | 12 | 97 | PROJECT(15) |
| regeneration | 88 | 85 | 2 | PROCESS(1) |
| r&d | 86 | 10 | 69 | PROCESS(5), PROJECT(2) |
| dao | 74 | 44 | 22 | TECHNOLOGY(7), PROJECT(1) |
| the commons | 71 | 43 | 28 | - |
| regen ai | 65 | 1 | 1 | TECHNOLOGY(43), PROJECT(20) |
| regenai | 65 | 1 | 10 | PROJECT(35), TECHNOLOGY(19) |
| spv | 63 | 33 | 18 | PROJECT(9), TECHNOLOGY(3) |
| planetary regeneration | 45 | 40 | 1 | PROJECT(4) |
| terrasos | 42 | 2 | 37 | PROJECT(3) |
| knowledge organization infrastructure | 41 | 30 | 1 | PROJECT(5), TECHNOLOGY(4), STANDARD(1) |
| token economics working group | 38 | 2 | 33 | PROJECT(2), EVENT(1) |
| desci insights | 37 | 12 | 9 | PROJECT(13), TECHNOLOGY(3) |
| cosmos ecosystem | 37 | 19 | 4 | TECHNOLOGY(7), PROJECT(7) |
| steward council | 35 | 2 | 32 | PROJECT(1) |
| duna | 34 | 15 | 10 | TECHNOLOGY(6), PROJECT(3) |
| tokenomics working group | 33 | 2 | 28 | PROJECT(2), PROCESS(1) |

### PROCESS↔TECHNOLOGY

**Labels:** 114 | **Total Occurrences:** 1,388

| Label | Total | PROCESS | TECHNOLOGY | Other Types |
| --- | --- | --- | --- | --- |
| semantic search | 104 | 1 | 8 | CONCEPT(95) |
| verification | 103 | 86 | 1 | CONCEPT(15), PROJECT(1) |
| hybrid search | 80 | 1 | 6 | CONCEPT(73) |
| marketplace | 71 | 1 | 27 | MODULE(16), CONCEPT(16), PROJECT(11) |
| cat receipts | 63 | 1 | 22 | CONCEPT(20), EVIDENCE(17), MATERIAL(1), PROJECT(1), STANDARD(1) |
| data anchoring | 48 | 15 | 8 | CONCEPT(24), MODULE(1) |
| entityqualityfilter | 38 | 1 | 31 | MODULE(6) |
| sortition | 36 | 4 | 1 | CONCEPT(31) |
| constitution | 33 | 1 | 1 | CONCEPT(25), STANDARD(6) |
| attestation | 32 | 10 | 2 | CONCEPT(19), CLAIM(1) |
| validator registry | 31 | 4 | 2 | PROJECT(16), CONCEPT(9) |
| koi pipeline | 29 | 1 | 7 | PROJECT(21) |
| governance forum | 28 | 3 | 13 | ORGANIZATION(9), CONCEPT(2), PROJECT(1) |
| tokenization | 27 | 3 | 2 | CONCEPT(22) |
| canonicalresolver | 24 | 1 | 19 | MODULE(4) |
| conviction voting | 23 | 3 | 1 | CONCEPT(19) |
| embedding generation | 21 | 4 | 3 | CONCEPT(14) |
| confidencefilter | 20 | 1 | 15 | MODULE(4) |
| version control | 20 | 2 | 2 | CONCEPT(16) |
| quadratic voting | 19 | 3 | 3 | CONCEPT(13) |

### CONCEPT↔MATERIAL

**Labels:** 62 | **Total Occurrences:** 1,132

| Label | Total | CONCEPT | MATERIAL | Other Types |
| --- | --- | --- | --- | --- |
| usdc | 123 | 13 | 14 | TECHNOLOGY(94), PROJECT(2) |
| biodiversity | 113 | 112 | 1 | - |
| carbon sequestration | 74 | 73 | 1 | - |
| cat receipts | 63 | 20 | 1 | TECHNOLOGY(22), EVIDENCE(17), PROCESS(1), PROJECT(1), STANDARD(1) |
| ecological assets | 63 | 60 | 3 | - |
| carbon | 62 | 29 | 33 | - |
| regen tokens | 46 | 8 | 20 | TECHNOLOGY(18) |
| eth | 45 | 1 | 3 | TECHNOLOGY(37), PROJECT(4) |
| region token | 43 | 19 | 2 | TECHNOLOGY(22) |
| soil health | 34 | 29 | 5 | - |
| atom | 33 | 3 | 1 | TECHNOLOGY(26), PROJECT(3) |
| soil | 31 | 2 | 24 | CREDIT_CLASS(3), ORGANIZATION(2) |
| ecological data | 27 | 25 | 2 | - |
| soil carbon | 24 | 3 | 21 | - |
| soil organic carbon | 21 | 8 | 13 | - |
| uregen | 19 | 4 | 11 | TECHNOLOGY(4) |
| stablecoins | 19 | 12 | 4 | TECHNOLOGY(3) |
| usdt.kava | 17 | 1 | 3 | TECHNOLOGY(13) |
| water | 17 | 6 | 11 | - |
| credits | 16 | 10 | 2 | API_MESSAGE(4) |

### CONCEPT↔CREDIT_CLASS

**Labels:** 54 | **Total Occurrences:** 393

| Label | Total | CONCEPT | CREDIT_CLASS | Other Types |
| --- | --- | --- | --- | --- |
| credit type | 63 | 61 | 2 | - |
| soil | 31 | 2 | 3 | MATERIAL(24), ORGANIZATION(2) |
| rccs | 18 | 8 | 1 | TECHNOLOGY(5), STANDARD(4) |
| methodology for grazing in vineyard systems | 15 | 6 | 1 | STANDARD(6), PROJECT(2) |
| biocultural credits | 14 | 11 | 3 | - |
| $nct | 13 | 2 | 2 | TECHNOLOGY(6), MATERIAL(2), PROJECT(1) |
| credit types | 12 | 11 | 1 | - |
| jaguar credits | 11 | 1 | 9 | PROJECT(1) |
| credit class admin | 11 | 10 | 1 | - |
| creditclass | 10 | 2 | 8 | - |
| eco-credit retirements | 10 | 8 | 1 | PROCESS(1) |
| planetary regenerative credit (prc) | 9 | 5 | 4 | - |
| ghg co-benefits in grazing systems | 9 | 5 | 4 | - |
| desert regreening | 9 | 5 | 3 | PROCESS(1) |
| biocultural credit | 8 | 7 | 1 | - |
| soil health credits | 8 | 7 | 1 | - |
| voluntary biodiversity credit | 8 | 4 | 4 | - |
| biodiversity stewardship credit methodology | 7 | 3 | 3 | STANDARD(1) |
| desert regreening credit class | 7 | 1 | 6 | - |
| class | 6 | 1 | 1 | API_MESSAGE(4) |

### PROCESS↔PROJECT

**Labels:** 48 | **Total Occurrences:** 741

| Label | Total | PROCESS | PROJECT | Other Types |
| --- | --- | --- | --- | --- |
| verification | 103 | 86 | 1 | CONCEPT(15), TECHNOLOGY(1) |
| r&d | 86 | 5 | 2 | ORGANIZATION(69), CONCEPT(10) |
| marketplace | 71 | 1 | 11 | TECHNOLOGY(27), MODULE(16), CONCEPT(16) |
| cat receipts | 63 | 1 | 1 | TECHNOLOGY(22), CONCEPT(20), EVIDENCE(17), MATERIAL(1), STANDARD(1) |
| project registration | 38 | 31 | 1 | CONCEPT(6) |
| tokenomics working group | 33 | 1 | 2 | ORGANIZATION(28), CONCEPT(2) |
| spv progress | 31 | 26 | 4 | CONCEPT(1) |
| validator registry | 31 | 4 | 16 | CONCEPT(9), TECHNOLOGY(2) |
| koi pipeline | 29 | 1 | 21 | TECHNOLOGY(7) |
| governance forum | 28 | 3 | 1 | TECHNOLOGY(13), ORGANIZATION(9), CONCEPT(2) |
| project plan | 28 | 3 | 6 | CONCEPT(14), EVIDENCE(5) |
| regen coordination | 26 | 1 | 8 | ORGANIZATION(13), CONCEPT(4) |
| story dev sprint | 16 | 2 | 13 | TECHNOLOGY(1) |
| pacto | 16 | 1 | 11 | CONCEPT(2), TECHNOLOGY(2) |
| plan de vida | 13 | 1 | 2 | CONCEPT(10) |
| implementation roadmap | 9 | 4 | 4 | CONCEPT(1) |
| project management | 9 | 1 | 1 | CONCEPT(7) |
| region registry | 8 | 1 | 7 | - |
| koi sensor-to-agent pipeline | 8 | 2 | 3 | TECHNOLOGY(3) |
| ccep | 7 | 1 | 2 | TECHNOLOGY(4) |

### CONCEPT↔GOVERNANCE_PROPOSAL

**Labels:** 47 | **Total Occurrences:** 323

| Label | Total | CONCEPT | GOVERNANCE_PROPOSAL | Other Types |
| --- | --- | --- | --- | --- |
| community-spend-pool | 24 | 21 | 2 | PROJECT(1) |
| currency allowlist | 20 | 19 | 1 | - |
| $regen tokenomics wg | 16 | 3 | 2 | ORGANIZATION(8), PROJECT(3) |
| on-chain proposal | 15 | 4 | 11 | - |
| regen commons constitution | 14 | 7 | 1 | STANDARD(3), PROJECT(3) |
| community spend proposal | 13 | 3 | 10 | - |
| token burning upgrades | 11 | 5 | 5 | CLAIM(1) |
| governance vote | 10 | 6 | 1 | PROCESS(3) |
| enabling transfers | 10 | 6 | 4 | - |
| software upgrade | 10 | 3 | 1 | PROCESS(5), TECHNOLOGY(1) |
| software upgrade proposal | 10 | 2 | 5 | PROCESS(3) |
| chain upgrade | 10 | 2 | 1 | PROCESS(7) |
| desert regreening credit class proposal | 10 | 2 | 8 | - |
| message-based governance proposals | 10 | 4 | 5 | PROCESS(1) |
| on-chain governance proposal | 8 | 1 | 7 | - |
| inflation reduction proposal | 8 | 3 | 5 | - |
| liquidity proposal | 7 | 1 | 6 | - |
| lowering inflation | 7 | 5 | 2 | - |
| regen constitution | 7 | 4 | 2 | STANDARD(1) |
| text proposal | 7 | 1 | 6 | - |

### API_MESSAGE↔CONCEPT

**Labels:** 40 | **Total Occurrences:** 297

| Label | Total | API_MESSAGE | CONCEPT | Other Types |
| --- | --- | --- | --- | --- |
| basket | 39 | 2 | 16 | MODULE(18), TECHNOLOGY(3) |
| bridge | 21 | 7 | 3 | TECHNOLOGY(9), PROJECT(2) |
| amino message | 20 | 1 | 17 | TECHNOLOGY(2) |
| credits | 16 | 4 | 10 | MATERIAL(2) |
| contenthash | 15 | 2 | 13 | - |
| resolver | 14 | 5 | 3 | TECHNOLOGY(6) |
| issuance | 11 | 1 | 2 | PROCESS(8) |
| credittype | 11 | 7 | 4 | - |
| sell order | 10 | 1 | 9 | - |
| burn | 8 | 1 | 7 | - |
| classinfo | 8 | 5 | 3 | - |
| batchissuance | 8 | 5 | 2 | PROCESS(1) |
| classcreatorallowlist | 7 | 4 | 3 | - |
| classfee | 7 | 5 | 2 | - |
| class | 6 | 4 | 1 | CREDIT_CLASS(1) |
| basketbalance | 6 | 4 | 1 | TECHNOLOGY(1) |
| allowedclasscreators | 6 | 5 | 1 | - |
| batch | 6 | 4 | 2 | - |
| batchbalance | 6 | 4 | 2 | - |
| eventsealbatch | 5 | 3 | 1 | EVENT(1) |

### ORGANIZATION↔VALIDATOR

**Labels:** 36 | **Total Occurrences:** 498

| Label | Total | ORGANIZATION | VALIDATOR | Other Types |
| --- | --- | --- | --- | --- |
| solana | 91 | 9 | 1 | TECHNOLOGY(59), PROJECT(22) |
| kava | 36 | 14 | 1 | PROJECT(13), TECHNOLOGY(8) |
| notional | 29 | 16 | 13 | - |
| eco bridge | 28 | 1 | 1 | TECHNOLOGY(20), PROJECT(6) |
| stargaze | 24 | 6 | 2 | PROJECT(12), TECHNOLOGY(4) |
| akash | 21 | 1 | 1 | PROJECT(17), TECHNOLOGY(2) |
| chainflow | 20 | 4 | 16 | - |
| cambium | 17 | 9 | 8 | - |
| cyberg | 17 | 1 | 15 | PROJECT(1) |
| 01node | 16 | 1 | 15 | - |
| loacom | 15 | 6 | 8 | PERSON(1) |
| ivzor | 15 | 1 | 14 | - |
| bitsong | 12 | 1 | 1 | PROJECT(7), TECHNOLOGY(3) |
| avalanche | 11 | 1 | 1 | PROJECT(7), TECHNOLOGY(2) |
| stargaze.fi | 11 | 1 | 7 | PROJECT(3) |
| stakelab | 10 | 5 | 5 | - |
| informal systems | 9 | 6 | 3 | - |
| alphabiota-loa labs | 9 | 4 | 2 | PERSON(3) |
| strangelove | 9 | 3 | 6 | - |
| forbole | 9 | 5 | 2 | TECHNOLOGY(2) |

### PROJECT↔STANDARD

**Labels:** 34 | **Total Occurrences:** 554

| Label | Total | PROJECT | STANDARD | Other Types |
| --- | --- | --- | --- | --- |
| mcp | 104 | 4 | 5 | TECHNOLOGY(91), CONCEPT(4) |
| cat receipts | 63 | 1 | 1 | TECHNOLOGY(22), CONCEPT(20), EVIDENCE(17), PROCESS(1), MATERIAL(1) |
| regen-data-standards | 47 | 30 | 7 | TECHNOLOGY(10) |
| knowledge organization infrastructure | 41 | 5 | 1 | CONCEPT(30), TECHNOLOGY(4), ORGANIZATION(1) |
| koi protocol | 39 | 6 | 11 | TECHNOLOGY(14), CONCEPT(8) |
| regen registry program guide | 22 | 1 | 21 | - |
| redd+ | 21 | 2 | 2 | CONCEPT(17) |
| orcid | 19 | 1 | 2 | TECHNOLOGY(14), ORGANIZATION(2) |
| methodology for grazing in vineyard systems | 15 | 2 | 6 | CONCEPT(6), CREDIT_CLASS(1) |
| prt | 15 | 4 | 1 | TECHNOLOGY(5), CONCEPT(5) |
| regen commons constitution | 14 | 3 | 3 | CONCEPT(7), GOVERNANCE_PROPOSAL(1) |
| white paper | 14 | 1 | 1 | CONCEPT(6), TECHNOLOGY(5), EVIDENCE(1) |
| iris | 13 | 6 | 1 | CONCEPT(3), TECHNOLOGY(3) |
| regen registry handbook | 13 | 5 | 7 | TECHNOLOGY(1) |
| koi rids | 11 | 1 | 1 | TECHNOLOGY(5), CONCEPT(3), EVIDENCE(1) |
| era | 11 | 1 | 2 | ORGANIZATION(6), CONCEPT(2) |
| mvp constitution | 9 | 1 | 3 | CONCEPT(5) |
| koi master implementation guide | 8 | 3 | 2 | TECHNOLOGY(3) |
| article 6 implementation partnership | 7 | 2 | 1 | ORGANIZATION(4) |
| regen charter | 7 | 3 | 3 | CONCEPT(1) |

### PROJECT↔VALIDATOR

**Labels:** 33 | **Total Occurrences:** 365

| Label | Total | PROJECT | VALIDATOR | Other Types |
| --- | --- | --- | --- | --- |
| solana | 91 | 22 | 1 | TECHNOLOGY(59), ORGANIZATION(9) |
| kava | 36 | 13 | 1 | ORGANIZATION(14), TECHNOLOGY(8) |
| eco bridge | 28 | 6 | 1 | TECHNOLOGY(20), ORGANIZATION(1) |
| stargaze | 24 | 12 | 2 | ORGANIZATION(6), TECHNOLOGY(4) |
| akash | 21 | 17 | 1 | TECHNOLOGY(2), ORGANIZATION(1) |
| cyberg | 17 | 1 | 15 | ORGANIZATION(1) |
| hopr | 12 | 8 | 4 | - |
| bitsong | 12 | 7 | 1 | TECHNOLOGY(3), ORGANIZATION(1) |
| avalanche | 11 | 7 | 1 | TECHNOLOGY(2), ORGANIZATION(1) |
| stargaze.fi | 11 | 3 | 7 | ORGANIZATION(1) |
| marlin | 9 | 5 | 4 | - |
| sentinel | 8 | 3 | 1 | TECHNOLOGY(3), ORGANIZATION(1) |
| ecobridge | 7 | 1 | 4 | TECHNOLOGY(2) |
| stafi | 7 | 1 | 4 | TECHNOLOGY(2) |
| dock | 6 | 1 | 5 | - |
| starname | 6 | 3 | 1 | TECHNOLOGY(2) |
| agoric | 6 | 2 | 3 | TECHNOLOGY(1) |
| earthistdao | 5 | 1 | 1 | ORGANIZATION(3) |
| kusama | 5 | 2 | 1 | TECHNOLOGY(2) |
| earthist | 5 | 1 | 4 | - |

### TECHNOLOGY↔VALIDATOR

**Labels:** 26 | **Total Occurrences:** 321

| Label | Total | TECHNOLOGY | VALIDATOR | Other Types |
| --- | --- | --- | --- | --- |
| solana | 91 | 59 | 1 | PROJECT(22), ORGANIZATION(9) |
| kava | 36 | 8 | 1 | ORGANIZATION(14), PROJECT(13) |
| eco bridge | 28 | 20 | 1 | PROJECT(6), ORGANIZATION(1) |
| stargaze | 24 | 4 | 2 | PROJECT(12), ORGANIZATION(6) |
| akash | 21 | 2 | 1 | PROJECT(17), ORGANIZATION(1) |
| bitsong | 12 | 3 | 1 | PROJECT(7), ORGANIZATION(1) |
| avalanche | 11 | 2 | 1 | PROJECT(7), ORGANIZATION(1) |
| forbole | 9 | 2 | 2 | ORGANIZATION(5) |
| sentinel | 8 | 3 | 1 | PROJECT(3), ORGANIZATION(1) |
| ecobridge | 7 | 2 | 4 | PROJECT(1) |
| polkachu | 7 | 1 | 5 | ORGANIZATION(1) |
| stafi | 7 | 2 | 4 | PROJECT(1) |
| p2p | 6 | 1 | 3 | ORGANIZATION(2) |
| starname | 6 | 2 | 1 | PROJECT(3) |
| agoric | 6 | 1 | 3 | PROJECT(2) |
| kusama | 5 | 2 | 1 | PROJECT(2) |
| regen node | 5 | 3 | 2 | - |
| quasar | 4 | 1 | 1 | PROJECT(2) |
| oasis | 4 | 2 | 1 | PROJECT(1) |
| cronos | 4 | 1 | 1 | PROJECT(2) |

### CONCEPT↔EVIDENCE

**Labels:** 26 | **Total Occurrences:** 244

| Label | Total | CONCEPT | EVIDENCE | Other Types |
| --- | --- | --- | --- | --- |
| cat receipts | 63 | 20 | 17 | TECHNOLOGY(22), PROCESS(1), MATERIAL(1), PROJECT(1), STANDARD(1) |
| project plan | 28 | 14 | 5 | PROJECT(6), PROCESS(3) |
| white paper | 14 | 6 | 1 | TECHNOLOGY(5), PROJECT(1), STANDARD(1) |
| monitoring report | 14 | 2 | 6 | PROCESS(6) |
| rids | 12 | 8 | 4 | - |
| cat receipt | 11 | 3 | 2 | TECHNOLOGY(6) |
| koi rids | 11 | 3 | 1 | TECHNOLOGY(5), PROJECT(1), STANDARD(1) |
| cryptographic proofs | 10 | 9 | 1 | - |
| retirement certificates | 10 | 7 | 1 | TECHNOLOGY(1), PROJECT(1) |
| cat receipt chain | 9 | 3 | 2 | TECHNOLOGY(2), MATERIAL(1), PROCESS(1) |
| whitepaper | 7 | 1 | 3 | PROJECT(2), STANDARD(1) |
| monitoring reports | 6 | 1 | 2 | PROCESS(3) |
| legitimacynote | 6 | 5 | 1 | - |
| land owner rights verification | 5 | 1 | 1 | PROCESS(3) |
| attestations | 5 | 2 | 2 | TECHNOLOGY(1) |
| project design document | 4 | 2 | 2 | - |
| $regen investor foresight | 4 | 2 | 2 | - |
| immutable audit trail | 3 | 2 | 1 | - |
| notice file | 3 | 2 | 1 | - |
| baseline data | 3 | 2 | 1 | - |

### API_MESSAGE↔TECHNOLOGY

**Labels:** 26 | **Total Occurrences:** 177

| Label | Total | API_MESSAGE | TECHNOLOGY | Other Types |
| --- | --- | --- | --- | --- |
| basket | 39 | 2 | 3 | MODULE(18), CONCEPT(16) |
| bridge | 21 | 7 | 9 | CONCEPT(3), PROJECT(2) |
| amino message | 20 | 1 | 2 | CONCEPT(17) |
| resolver | 14 | 5 | 6 | CONCEPT(3) |
| eventbridge | 7 | 3 | 1 | EVENT(3) |
| eventbridgereceive | 6 | 2 | 1 | EVENT(3) |
| basketbalance | 6 | 4 | 1 | CONCEPT(1) |
| dataanchor | 5 | 2 | 1 | CONCEPT(2) |
| batchcontract | 5 | 4 | 1 | - |
| anchor | 5 | 1 | 1 | PROCESS(2), CONCEPT(1) |
| mcp__regen__list-sell-orders | 4 | 2 | 2 | - |
| pickfrombasket | 4 | 1 | 1 | CONCEPT(1), PROCESS(1) |
| allbalances | 4 | 3 | 1 | - |
| eventanchor | 4 | 2 | 1 | EVENT(1) |
| contenthashes | 4 | 2 | 2 | - |
| dataresolver | 3 | 2 | 1 | - |
| mcp__regen__list-classes | 3 | 1 | 2 | - |
| mcp__regen__list-credit-batches | 3 | 1 | 2 | - |
| mcp__regen__list-projects | 3 | 1 | 2 | - |
| protobufmessage | 3 | 2 | 1 | - |

### MODULE↔TECHNOLOGY

**Labels:** 25 | **Total Occurrences:** 519

| Label | Total | MODULE | TECHNOLOGY | Other Types |
| --- | --- | --- | --- | --- |
| base | 95 | 1 | 68 | PROJECT(24), ORGANIZATION(2) |
| marketplace | 71 | 16 | 27 | CONCEPT(16), PROJECT(11), PROCESS(1) |
| data anchoring | 48 | 1 | 8 | CONCEPT(24), PROCESS(15) |
| claims engine | 43 | 1 | 42 | - |
| basket | 39 | 18 | 3 | CONCEPT(16), API_MESSAGE(2) |
| entityqualityfilter | 38 | 6 | 31 | PROCESS(1) |
| canonicalresolver | 24 | 4 | 19 | PROCESS(1) |
| confidencefilter | 20 | 4 | 15 | PROCESS(1) |
| agent | 16 | 1 | 11 | CONCEPT(4) |
| ontologynormalizer | 15 | 3 | 11 | PROCESS(1) |
| orm | 15 | 1 | 9 | CONCEPT(5) |
| listsplitter | 15 | 3 | 11 | PROCESS(1) |
| authz | 14 | 10 | 2 | CONCEPT(2) |
| ica | 10 | 2 | 4 | CONCEPT(4) |
| yonearth | 8 | 1 | 1 | ORGANIZATION(3), PROJECT(3) |
| cosmos sdk module | 8 | 6 | 1 | QUESTION(1) |
| confidencefiltermodule | 7 | 1 | 6 | - |
| token factory | 7 | 2 | 5 | - |
| canonicalresolvermodule | 6 | 1 | 5 | - |
| templates | 5 | 2 | 3 | - |

### PROCESS↔STANDARD

**Labels:** 22 | **Total Occurrences:** 289

| Label | Total | PROCESS | STANDARD | Other Types |
| --- | --- | --- | --- | --- |
| methodology | 66 | 4 | 3 | CONCEPT(59) |
| cat receipts | 63 | 1 | 1 | TECHNOLOGY(22), CONCEPT(20), EVIDENCE(17), MATERIAL(1), PROJECT(1) |
| constitution | 33 | 1 | 6 | CONCEPT(25), TECHNOLOGY(1) |
| approved methodology | 23 | 1 | 6 | CONCEPT(16) |
| decision-making protocol | 14 | 6 | 2 | CONCEPT(6) |
| credit protocols | 13 | 2 | 3 | CONCEPT(8) |
| ghg accounting | 12 | 3 | 1 | CONCEPT(8) |
| member protocol | 11 | 4 | 4 | CONCEPT(2), TECHNOLOGY(1) |
| publishing protocol | 8 | 3 | 2 | CONCEPT(3) |
| expert peer reviewer guidelines | 8 | 2 | 6 | - |
| brand usage protocol | 7 | 5 | 2 | - |
| ip protocol | 6 | 1 | 1 | CONCEPT(2), TECHNOLOGY(2) |
| conflict & tension protocol | 4 | 2 | 2 | - |
| methodology whitelist | 3 | 2 | 1 | - |
| standards setting | 3 | 1 | 2 | - |
| token ownership and trading policy | 3 | 1 | 1 | CONCEPT(1) |
| kyc/aml | 2 | 1 | 1 | - |
| regen registry protocols | 2 | 1 | 1 | - |
| registered carbon credit verifications | 2 | 1 | 1 | - |
| data management and sharing policy | 2 | 1 | 1 | - |

### CONCEPT↔LICENSE

**Labels:** 22 | **Total Occurrences:** 209

| Label | Total | CONCEPT | LICENSE | Other Types |
| --- | --- | --- | --- | --- |
| json-ld | 72 | 18 | 1 | TECHNOLOGY(32), STANDARD(21) |
| regen works | 15 | 4 | 1 | PROJECT(7), TECHNOLOGY(3) |
| code of conduct | 14 | 10 | 2 | STANDARD(2) |
| open source | 13 | 10 | 3 | - |
| source form | 10 | 9 | 1 | - |
| copyright | 10 | 8 | 2 | - |
| turtle | 10 | 2 | 1 | STANDARD(4), TECHNOLOGY(3) |
| regen trademark | 9 | 8 | 1 | - |
| privacy policy | 7 | 5 | 2 | - |
| commons governance rules | 6 | 4 | 1 | STANDARD(1) |
| commons conditional use license | 6 | 1 | 5 | - |
| trademark | 5 | 3 | 2 | - |
| open license | 5 | 3 | 2 | - |
| terms of service | 5 | 3 | 2 | - |
| patent license | 4 | 1 | 3 | - |
| trademark law | 4 | 2 | 1 | STANDARD(1) |
| commons licensee | 3 | 2 | 1 | - |
| token ownership & trading policy | 3 | 1 | 1 | STANDARD(1) |
| copyright license | 2 | 1 | 1 | - |
| special license | 2 | 1 | 1 | - |

### API_MESSAGE↔EVENT

**Labels:** 22 | **Total Occurrences:** 86

| Label | Total | API_MESSAGE | EVENT | Other Types |
| --- | --- | --- | --- | --- |
| eventretire | 9 | 5 | 4 | - |
| eventbridge | 7 | 3 | 3 | TECHNOLOGY(1) |
| eventbridgereceive | 6 | 2 | 3 | TECHNOLOGY(1) |
| eventsealbatch | 5 | 3 | 1 | CONCEPT(1) |
| eventcreatebatch | 5 | 3 | 2 | - |
| eventanchor | 4 | 2 | 1 | TECHNOLOGY(1) |
| eventcreateclass | 4 | 3 | 1 | - |
| eventupdatebatchmetadata | 4 | 3 | 1 | - |
| eventupdateprojectadmin | 4 | 3 | 1 | - |
| eventupdateprojectmetadata | 4 | 3 | 1 | - |
| eventallowdenom | 3 | 2 | 1 | - |
| eventattest | 3 | 2 | 1 | - |
| eventcancel | 3 | 2 | 1 | - |
| eventcreate | 3 | 2 | 1 | - |
| eventcreateproject | 3 | 2 | 1 | - |
| eventdefineresolver | 3 | 2 | 1 | - |
| eventmint | 3 | 2 | 1 | - |
| eventmintbatchcredits | 3 | 2 | 1 | - |
| eventregisterresolver | 3 | 2 | 1 | - |
| eventupdateclassissuers | 3 | 2 | 1 | - |

### MATERIAL↔TECHNOLOGY

**Labels:** 21 | **Total Occurrences:** 511

| Label | Total | MATERIAL | TECHNOLOGY | Other Types |
| --- | --- | --- | --- | --- |
| usdc | 123 | 14 | 94 | CONCEPT(13), PROJECT(2) |
| cat receipts | 63 | 1 | 22 | CONCEPT(20), EVIDENCE(17), PROCESS(1), PROJECT(1), STANDARD(1) |
| regen tokens | 46 | 20 | 18 | CONCEPT(8) |
| eth | 45 | 3 | 37 | PROJECT(4), CONCEPT(1) |
| region token | 43 | 2 | 22 | CONCEPT(19) |
| atom | 33 | 1 | 26 | PROJECT(3), CONCEPT(3) |
| uregen | 19 | 11 | 4 | CONCEPT(4) |
| stablecoins | 19 | 4 | 3 | CONCEPT(12) |
| usdt.kava | 17 | 3 | 13 | CONCEPT(1) |
| buffer pool | 14 | 1 | 2 | CONCEPT(10), PROCESS(1) |
| axlusdc | 13 | 1 | 10 | CONCEPT(2) |
| $nct | 13 | 2 | 6 | CONCEPT(2), CREDIT_CLASS(2), PROJECT(1) |
| region tokens | 12 | 2 | 4 | CONCEPT(6) |
| btc | 12 | 1 | 10 | CONCEPT(1) |
| noble-issued usdc | 10 | 1 | 9 | - |
| cat receipt chain | 9 | 1 | 2 | CONCEPT(3), EVIDENCE(2), PROCESS(1) |
| carbono | 6 | 1 | 1 | CONCEPT(4) |
| hydx | 5 | 1 | 3 | PROJECT(1) |
| gravity usdc | 4 | 1 | 2 | CONCEPT(1) |
| axelar usdc | 3 | 1 | 1 | PROJECT(1) |

### CLAIM↔CONCEPT

**Labels:** 21 | **Total Occurrences:** 130

| Label | Total | CLAIM | CONCEPT | Other Types |
| --- | --- | --- | --- | --- |
| attestation | 32 | 1 | 19 | PROCESS(10), TECHNOLOGY(2) |
| ecological claims | 19 | 9 | 10 | - |
| token burning upgrades | 11 | 1 | 5 | GOVERNANCE_PROPOSAL(5) |
| catreceipt | 7 | 1 | 1 | TECHNOLOGY(4), PROCESS(1) |
| certified regen | 6 | 1 | 3 | CREDIT_CLASS(1), STANDARD(1) |
| project ownership | 6 | 1 | 5 | - |
| ghg mitigation claim | 6 | 1 | 5 | - |
| protección permanente | 5 | 1 | 4 | - |
| regen score | 5 | 1 | 1 | TECHNOLOGY(1), CREDIT_CLASS(1), STANDARD(1) |
| programmable trust | 4 | 1 | 3 | - |
| cat receipt chains | 4 | 1 | 3 | - |
| disclaimer of warranty | 4 | 1 | 3 | - |
| ecological data claims | 4 | 2 | 2 | - |
| credits as claims | 3 | 2 | 1 | - |
| reclamos ecológicos | 2 | 1 | 1 | - |
| high cost of verification | 2 | 1 | 1 | - |
| credential claim | 2 | 1 | 1 | - |
| decentralized verification of ecological action | 2 | 1 | 1 | - |
| carbon verification claim | 2 | 1 | 1 | - |
| garbage in, garbage out | 2 | 1 | 1 | - |

### CONCEPT↔MODULE

**Labels:** 19 | **Total Occurrences:** 287

| Label | Total | CONCEPT | MODULE | Other Types |
| --- | --- | --- | --- | --- |
| marketplace | 71 | 16 | 16 | TECHNOLOGY(27), PROJECT(11), PROCESS(1) |
| data anchoring | 48 | 24 | 1 | PROCESS(15), TECHNOLOGY(8) |
| basket | 39 | 16 | 18 | TECHNOLOGY(3), API_MESSAGE(2) |
| data | 26 | 2 | 22 | ORGANIZATION(2) |
| basket submodule | 18 | 1 | 17 | - |
| agent | 16 | 4 | 1 | TECHNOLOGY(11) |
| orm | 15 | 5 | 1 | TECHNOLOGY(9) |
| authz | 14 | 2 | 10 | TECHNOLOGY(2) |
| ica | 10 | 4 | 2 | TECHNOLOGY(4) |
| slashing | 7 | 3 | 2 | PROCESS(2) |
| distribution | 6 | 1 | 4 | PROCESS(1) |
| claims | 3 | 1 | 1 | TECHNOLOGY(1) |
| rbam | 2 | 1 | 1 | - |
| transformmodule | 2 | 1 | 1 | - |
| filtermodule | 2 | 1 | 1 | - |
| controller | 2 | 1 | 1 | - |
| ibc-transfer | 2 | 1 | 1 | - |
| cosmos modules | 2 | 1 | 1 | - |
| message authorization | 2 | 1 | 1 | - |

### EVENT↔PROJECT

**Labels:** 18 | **Total Occurrences:** 218

| Label | Total | EVENT | PROJECT | Other Types |
| --- | --- | --- | --- | --- |
| regen mainnet | 66 | 1 | 48 | TECHNOLOGY(14), LOCATION(2), CONCEPT(1) |
| token economics working group | 38 | 1 | 2 | ORGANIZATION(33), CONCEPT(2) |
| planetary regeneration podcast | 32 | 5 | 25 | ORGANIZATION(1), TECHNOLOGY(1) |
| bioregional assembly | 23 | 1 | 1 | ORGANIZATION(14), CONCEPT(7) |
| hambach testnet | 12 | 1 | 10 | TECHNOLOGY(1) |
| regen tokenomics working group | 9 | 1 | 3 | ORGANIZATION(5) |
| regenerati news hour | 6 | 4 | 2 | - |
| regen builder lab | 5 | 3 | 2 | - |
| green proofing series | 4 | 3 | 1 | - |
| assembly | 4 | 2 | 1 | CONCEPT(1) |
| impact evaluator research retreat | 4 | 2 | 2 | - |
| builder lab | 3 | 1 | 2 | - |
| eco credit builder lab | 2 | 1 | 1 | - |
| research retreat | 2 | 1 | 1 | - |
| d/acc residency 2025 | 2 | 1 | 1 | - |
| gitcoin round 22-24 | 2 | 1 | 1 | - |
| techround 100 | 2 | 1 | 1 | - |
| phase 1 | 2 | 1 | 1 | - |

### LOCATION↔TECHNOLOGY

**Labels:** 17 | **Total Occurrences:** 288

| Label | Total | LOCATION | TECHNOLOGY | Other Types |
| --- | --- | --- | --- | --- |
| regen mainnet | 66 | 2 | 14 | PROJECT(48), CONCEPT(1), EVENT(1) |
| mainnet | 53 | 2 | 28 | PROJECT(15), CONCEPT(8) |
| regen testnet | 39 | 4 | 8 | PROJECT(27) |
| redwood testnet | 35 | 1 | 13 | PROJECT(21) |
| uniswap | 28 | 1 | 19 | ORGANIZATION(4), PROJECT(4) |
| region | 14 | 1 | 2 | CONCEPT(8), PROJECT(3) |
| cello | 13 | 1 | 6 | PROJECT(6) |
| linux | 8 | 2 | 6 | - |
| ubuntu | 7 | 3 | 4 | - |
| gardens | 4 | 1 | 2 | ORGANIZATION(1) |
| op | 4 | 1 | 3 | - |
| gmt | 4 | 2 | 1 | VALIDATOR(1) |
| cyberspace | 3 | 1 | 1 | CONCEPT(1) |
| windows | 3 | 1 | 2 | - |
| ubuntu 20.04+ | 3 | 2 | 1 | - |
| airbnb | 2 | 1 | 1 | - |
| minsk | 2 | 1 | 1 | - |

### CREDIT_CLASS↔PROJECT

**Labels:** 17 | **Total Occurrences:** 159

| Label | Total | CREDIT_CLASS | PROJECT | Other Types |
| --- | --- | --- | --- | --- |
| c01-001 | 65 | 5 | 60 | - |
| methodology for grazing in vineyard systems | 15 | 1 | 2 | STANDARD(6), CONCEPT(6) |
| $nct | 13 | 2 | 1 | TECHNOLOGY(6), CONCEPT(2), MATERIAL(2) |
| city forest credits | 11 | 4 | 3 | ORGANIZATION(4) |
| jaguar credits | 11 | 9 | 1 | CONCEPT(1) |
| p001 | 6 | 1 | 4 | STANDARD(1) |
| biocultural jaguar credits | 5 | 4 | 1 | - |
| bioterra | 5 | 4 | 1 | - |
| coi | 4 | 1 | 1 | TECHNOLOGY(1), CONCEPT(1) |
| natureplus | 4 | 2 | 1 | ORGANIZATION(1) |
| grazing land management | 4 | 1 | 1 | CONCEPT(2) |
| daf-wrapped eco credits | 4 | 1 | 1 | CONCEPT(2) |
| eco-credits | 4 | 2 | 1 | MATERIAL(1) |
| matsés credit | 2 | 1 | 1 | - |
| jaguar biocultural credits | 2 | 1 | 1 | - |
| jaguar biocultural credit | 2 | 1 | 1 | - |
| terrasos biodiversity units | 2 | 1 | 1 | - |

### PERSON↔VALIDATOR

**Labels:** 16 | **Total Occurrences:** 140

| Label | Total | PERSON | VALIDATOR | Other Types |
| --- | --- | --- | --- | --- |
| ryanchristo-chora_validator | 26 | 1 | 25 | - |
| loacom | 15 | 1 | 8 | ORGANIZATION(6) |
| sebytza05 | 14 | 3 | 11 | - |
| kytzu | 9 | 1 | 8 | - |
| kingsuper | 9 | 6 | 3 | - |
| alphabiota-loa labs | 9 | 3 | 2 | ORGANIZATION(4) |
| akik takat | 9 | 1 | 8 | - |
| waynewayner | 8 | 1 | 7 | - |
| chris-chainflow | 8 | 6 | 2 | - |
| swidnikk | 8 | 6 | 2 | - |
| ekonavi | 7 | 1 | 4 | ORGANIZATION(2) |
| ushakov | 5 | 1 | 4 | - |
| alex (bambarello) | 4 | 3 | 1 | - |
| jjangg96 | 4 | 3 | 1 | - |
| kamuel bob-bliss dynamics | 3 | 2 | 1 | - |
| jd_cephalopod | 2 | 1 | 1 | - |

### LOCATION↔PROJECT

**Labels:** 15 | **Total Occurrences:** 274

| Label | Total | LOCATION | PROJECT | Other Types |
| --- | --- | --- | --- | --- |
| regen mainnet | 66 | 2 | 48 | TECHNOLOGY(14), CONCEPT(1), EVENT(1) |
| mainnet | 53 | 2 | 15 | TECHNOLOGY(28), CONCEPT(8) |
| regen testnet | 39 | 4 | 27 | TECHNOLOGY(8) |
| redwood testnet | 35 | 1 | 21 | TECHNOLOGY(13) |
| uniswap | 28 | 1 | 4 | TECHNOLOGY(19), ORGANIZATION(4) |
| region | 14 | 1 | 3 | CONCEPT(8), TECHNOLOGY(2) |
| cello | 13 | 1 | 6 | TECHNOLOGY(6) |
| cuencas sagradas | 5 | 1 | 4 | - |
| blaston farm | 4 | 1 | 3 | - |
| hambach | 4 | 2 | 2 | - |
| sharamensa | 4 | 2 | 2 | - |
| elk mountain lodge | 3 | 1 | 2 | - |
| barú eco-hotel | 2 | 1 | 1 | - |
| sharaminza | 2 | 1 | 1 | - |
| cambridge | 2 | 1 | 1 | - |

### ORGANIZATION↔STANDARD

**Labels:** 14 | **Total Occurrences:** 139

| Label | Total | ORGANIZATION | STANDARD | Other Types |
| --- | --- | --- | --- | --- |
| knowledge organization infrastructure | 41 | 1 | 1 | CONCEPT(30), PROJECT(5), TECHNOLOGY(4) |
| orcid | 19 | 2 | 2 | TECHNOLOGY(14), PROJECT(1) |
| cdp | 18 | 9 | 6 | CONCEPT(2), TECHNOLOGY(1) |
| aws | 12 | 1 | 3 | TECHNOLOGY(8) |
| era | 11 | 6 | 2 | CONCEPT(2), PROJECT(1) |
| sbti | 9 | 3 | 6 | - |
| article 6 implementation partnership | 7 | 4 | 1 | PROJECT(2) |
| tnfd | 4 | 1 | 3 | - |
| icvcm | 4 | 2 | 2 | - |
| icma | 3 | 1 | 2 | - |
| contributor covenant | 3 | 1 | 1 | LICENSE(1) |
| lma | 3 | 1 | 2 | - |
| vcmi | 3 | 1 | 2 | - |
| global reporting initiative | 2 | 1 | 1 | - |

### ORGANIZATION↔PERSON

**Labels:** 13 | **Total Occurrences:** 122

| Label | Total | ORGANIZATION | PERSON | Other Types |
| --- | --- | --- | --- | --- |
| branch out | 33 | 22 | 7 | PROJECT(4) |
| loacom | 15 | 6 | 1 | VALIDATOR(8) |
| tmo | 14 | 1 | 13 | - |
| mars | 12 | 5 | 5 | PROJECT(2) |
| can | 10 | 2 | 1 | PROJECT(7) |
| alphabiota-loa labs | 9 | 4 | 3 | VALIDATOR(2) |
| ekonavi | 7 | 2 | 1 | VALIDATOR(4) |
| adam | 6 | 1 | 2 | TECHNOLOGY(3) |
| arthine | 4 | 1 | 2 | PROJECT(1) |
| dell | 4 | 2 | 2 | - |
| bertie kutokin | 3 | 1 | 2 | - |
| shuar | 3 | 1 | 2 | - |
| klinga | 2 | 1 | 1 | - |

### CONCEPT↔LOCATION

**Labels:** 12 | **Total Occurrences:** 172

| Label | Total | CONCEPT | LOCATION | Other Types |
| --- | --- | --- | --- | --- |
| regen mainnet | 66 | 1 | 2 | PROJECT(48), TECHNOLOGY(14), EVENT(1) |
| mainnet | 53 | 8 | 2 | TECHNOLOGY(28), PROJECT(15) |
| global south | 19 | 3 | 16 | - |
| region | 14 | 8 | 1 | PROJECT(3), TECHNOLOGY(2) |
| cyberspace | 3 | 1 | 1 | TECHNOLOGY(1) |
| marine | 3 | 2 | 1 | - |
| national parks | 3 | 2 | 1 | - |
| biodiversity hotspots | 3 | 2 | 1 | - |
| lake | 2 | 1 | 1 | - |
| spanish | 2 | 1 | 1 | - |
| chicago | 2 | 1 | 1 | - |
| project location | 2 | 1 | 1 | - |

### EVIDENCE↔TECHNOLOGY

**Labels:** 12 | **Total Occurrences:** 144

| Label | Total | EVIDENCE | TECHNOLOGY | Other Types |
| --- | --- | --- | --- | --- |
| cat receipts | 63 | 17 | 22 | CONCEPT(20), PROCESS(1), MATERIAL(1), PROJECT(1), STANDARD(1) |
| white paper | 14 | 1 | 5 | CONCEPT(6), PROJECT(1), STANDARD(1) |
| cat receipt | 11 | 2 | 6 | CONCEPT(3) |
| koi rids | 11 | 1 | 5 | CONCEPT(3), PROJECT(1), STANDARD(1) |
| retirement certificates | 10 | 1 | 1 | CONCEPT(7), PROJECT(1) |
| cat receipt chain | 9 | 2 | 2 | CONCEPT(3), MATERIAL(1), PROCESS(1) |
| satellite imagery | 8 | 1 | 7 | - |
| attestations | 5 | 2 | 1 | CONCEPT(2) |
| regen:13tovgf5azqsvsejqv562xkkeoe3rr3bjwa29phvkvf77vakvmcdvvd.rdf | 5 | 3 | 1 | PROJECT(1) |
| blockchain audit trails | 4 | 1 | 3 | - |
| regen technical token economics paper | 2 | 1 | 1 | - |
| imágenes satelitales | 2 | 1 | 1 | - |

### CONCEPT↔QUESTION

**Labels:** 12 | **Total Occurrences:** 85

| Label | Total | CONCEPT | QUESTION | Other Types |
| --- | --- | --- | --- | --- |
| governance proposals | 28 | 26 | 1 | PROCESS(1) |
| governance structure | 27 | 26 | 1 | - |
| commercial willingness | 4 | 3 | 1 | - |
| problem–solution fit | 4 | 3 | 1 | - |
| road to one dollar | 4 | 3 | 1 | - |
| universal basic income | 4 | 3 | 1 | - |
| fixed kept dynamic supply | 3 | 2 | 1 | - |
| maximum supply of the token | 3 | 1 | 2 | - |
| email notification setting | 2 | 1 | 1 | - |
| fair distribution of resources | 2 | 1 | 1 | - |
| savory controversy | 2 | 1 | 1 | - |
| carbon vs. co-benefit balance | 2 | 1 | 1 | - |

### CONCEPT↔EVENT

**Labels:** 11 | **Total Occurrences:** 163

| Label | Total | CONCEPT | EVENT | Other Types |
| --- | --- | --- | --- | --- |
| regen mainnet | 66 | 1 | 1 | PROJECT(48), TECHNOLOGY(14), LOCATION(2) |
| token economics working group | 38 | 2 | 1 | ORGANIZATION(33), PROJECT(2) |
| bioregional assembly | 23 | 7 | 1 | ORGANIZATION(14), PROJECT(1) |
| ethereum localism | 7 | 6 | 1 | - |
| fun events | 7 | 6 | 1 | - |
| eventsealbatch | 5 | 1 | 1 | API_MESSAGE(3) |
| mainnet launch | 4 | 2 | 1 | PROCESS(1) |
| assembly | 4 | 1 | 2 | PROJECT(1) |
| green proofing | 3 | 1 | 1 | ORGANIZATION(1) |
| covid | 3 | 1 | 2 | - |
| project start date | 3 | 1 | 1 | PROCESS(1) |

### EVIDENCE↔PROJECT

**Labels:** 11 | **Total Occurrences:** 149

| Label | Total | EVIDENCE | PROJECT | Other Types |
| --- | --- | --- | --- | --- |
| cat receipts | 63 | 17 | 1 | TECHNOLOGY(22), CONCEPT(20), PROCESS(1), MATERIAL(1), STANDARD(1) |
| project plan | 28 | 5 | 6 | CONCEPT(14), PROCESS(3) |
| white paper | 14 | 1 | 1 | CONCEPT(6), TECHNOLOGY(5), STANDARD(1) |
| koi rids | 11 | 1 | 1 | TECHNOLOGY(5), CONCEPT(3), STANDARD(1) |
| retirement certificates | 10 | 1 | 1 | CONCEPT(7), TECHNOLOGY(1) |
| whitepaper | 7 | 3 | 2 | STANDARD(1), CONCEPT(1) |
| regen:13tovgf5azqsvsejqv562xkkeoe3rr3bjwa29phvkvf77vakvmcdvvd.rdf | 5 | 3 | 1 | TECHNOLOGY(1) |
| terrasos whitepaper | 4 | 1 | 1 | STANDARD(2) |
| executive summary (2025) | 3 | 1 | 2 | - |
| regen white paper | 2 | 1 | 1 | - |
| desert_regreening_credit_proposal_v1 | 2 | 1 | 1 | - |

### GOVERNANCE_PROPOSAL↔PROCESS

**Labels:** 11 | **Total Occurrences:** 70

| Label | Total | GOVERNANCE_PROPOSAL | PROCESS | Other Types |
| --- | --- | --- | --- | --- |
| governance vote | 10 | 1 | 3 | CONCEPT(6) |
| software upgrade | 10 | 1 | 5 | CONCEPT(3), TECHNOLOGY(1) |
| software upgrade proposal | 10 | 5 | 3 | CONCEPT(2) |
| chain upgrade | 10 | 1 | 7 | CONCEPT(2) |
| message-based governance proposals | 10 | 5 | 1 | CONCEPT(4) |
| upgrade proposal | 6 | 4 | 1 | CONCEPT(1) |
| community vote | 5 | 1 | 1 | CONCEPT(3) |
| forum proposal | 3 | 2 | 1 | - |
| liquiditydao emissions transfer #3 | 2 | 1 | 1 | - |
| community proposals | 2 | 1 | 1 | - |
| bridge whitelisting | 2 | 1 | 1 | - |

### MATERIAL↔PROJECT

**Labels:** 10 | **Total Occurrences:** 308

| Label | Total | MATERIAL | PROJECT | Other Types |
| --- | --- | --- | --- | --- |
| usdc | 123 | 14 | 2 | TECHNOLOGY(94), CONCEPT(13) |
| cat receipts | 63 | 1 | 1 | TECHNOLOGY(22), CONCEPT(20), EVIDENCE(17), PROCESS(1), STANDARD(1) |
| eth | 45 | 3 | 4 | TECHNOLOGY(37), CONCEPT(1) |
| atom | 33 | 1 | 3 | TECHNOLOGY(26), CONCEPT(3) |
| $nct | 13 | 2 | 1 | TECHNOLOGY(6), CONCEPT(2), CREDIT_CLASS(2) |
| jaguar | 10 | 1 | 7 | CONCEPT(1), PERSON(1) |
| regen credits | 9 | 1 | 1 | CONCEPT(7) |
| hydx | 5 | 1 | 1 | TECHNOLOGY(3) |
| eco-credits | 4 | 1 | 1 | CREDIT_CLASS(2) |
| axelar usdc | 3 | 1 | 1 | TECHNOLOGY(1) |

### ORGANIZATION↔PROCESS

**Labels:** 10 | **Total Occurrences:** 302

| Label | Total | ORGANIZATION | PROCESS | Other Types |
| --- | --- | --- | --- | --- |
| regeneration | 88 | 2 | 1 | CONCEPT(85) |
| r&d | 86 | 69 | 5 | CONCEPT(10), PROJECT(2) |
| tokenomics working group | 33 | 28 | 1 | CONCEPT(2), PROJECT(2) |
| governance forum | 28 | 9 | 3 | TECHNOLOGY(13), CONCEPT(2), PROJECT(1) |
| regen coordination | 26 | 13 | 1 | PROJECT(8), CONCEPT(4) |
| working group | 12 | 5 | 1 | CONCEPT(6) |
| validation | 12 | 1 | 5 | CONCEPT(6) |
| marketing | 8 | 1 | 1 | CONCEPT(6) |
| endaoment | 5 | 1 | 1 | CONCEPT(3) |
| commons review panel | 4 | 2 | 2 | - |

### LOCATION↔ORGANIZATION

**Labels:** 10 | **Total Occurrences:** 158

| Label | Total | LOCATION | ORGANIZATION | Other Types |
| --- | --- | --- | --- | --- |
| us | 43 | 41 | 2 | - |
| amazon | 42 | 40 | 2 | - |
| uniswap | 28 | 1 | 4 | TECHNOLOGY(19), PROJECT(4) |
| eu | 14 | 10 | 4 | - |
| sharamentsa | 12 | 11 | 1 | - |
| amazon sacred headwaters | 6 | 2 | 4 | - |
| mashantucket pequot museum | 4 | 3 | 1 | - |
| gardens | 4 | 1 | 1 | TECHNOLOGY(2) |
| shell | 3 | 1 | 2 | - |
| brown university | 2 | 1 | 1 | - |

### EVIDENCE↔PROCESS

**Labels:** 10 | **Total Occurrences:** 143

| Label | Total | EVIDENCE | PROCESS | Other Types |
| --- | --- | --- | --- | --- |
| cat receipts | 63 | 17 | 1 | TECHNOLOGY(22), CONCEPT(20), MATERIAL(1), PROJECT(1), STANDARD(1) |
| project plan | 28 | 5 | 3 | CONCEPT(14), PROJECT(6) |
| monitoring report | 14 | 6 | 6 | CONCEPT(2) |
| cat receipt chain | 9 | 2 | 1 | CONCEPT(3), TECHNOLOGY(2), MATERIAL(1) |
| verification report | 9 | 3 | 6 | - |
| monitoring reports | 6 | 2 | 3 | CONCEPT(1) |
| land owner rights verification | 5 | 1 | 3 | CONCEPT(1) |
| evidence extraction | 3 | 1 | 2 | - |
| vvc verification report | 3 | 2 | 1 | - |
| risk memo | 3 | 1 | 1 | CONCEPT(1) |

### CREDIT_CLASS↔STANDARD

**Labels:** 10 | **Total Occurrences:** 71

| Label | Total | CREDIT_CLASS | STANDARD | Other Types |
| --- | --- | --- | --- | --- |
| rccs | 18 | 1 | 4 | CONCEPT(8), TECHNOLOGY(5) |
| methodology for grazing in vineyard systems | 15 | 1 | 6 | CONCEPT(6), PROJECT(2) |
| biodiversity stewardship credit methodology | 7 | 3 | 1 | CONCEPT(3) |
| p001 | 6 | 1 | 1 | PROJECT(4) |
| certified regen | 6 | 1 | 1 | CONCEPT(3), CLAIM(1) |
| water benefit units | 5 | 1 | 1 | CONCEPT(3) |
| regen score | 5 | 1 | 1 | CLAIM(1), CONCEPT(1), TECHNOLOGY(1) |
| ccb | 4 | 2 | 2 | - |
| jurisdictional & nested redd+ | 3 | 2 | 1 | - |
| regen registry credit classes | 2 | 1 | 1 | - |

### MODULE↔PROCESS

**Labels:** 9 | **Total Occurrences:** 244

| Label | Total | MODULE | PROCESS | Other Types |
| --- | --- | --- | --- | --- |
| marketplace | 71 | 16 | 1 | TECHNOLOGY(27), CONCEPT(16), PROJECT(11) |
| data anchoring | 48 | 1 | 15 | CONCEPT(24), TECHNOLOGY(8) |
| entityqualityfilter | 38 | 6 | 1 | TECHNOLOGY(31) |
| canonicalresolver | 24 | 4 | 1 | TECHNOLOGY(19) |
| confidencefilter | 20 | 4 | 1 | TECHNOLOGY(15) |
| ontologynormalizer | 15 | 3 | 1 | TECHNOLOGY(11) |
| listsplitter | 15 | 3 | 1 | TECHNOLOGY(11) |
| slashing | 7 | 2 | 2 | CONCEPT(3) |
| distribution | 6 | 4 | 1 | CONCEPT(1) |

### EVENT↔ORGANIZATION

**Labels:** 9 | **Total Occurrences:** 128

| Label | Total | EVENT | ORGANIZATION | Other Types |
| --- | --- | --- | --- | --- |
| token economics working group | 38 | 1 | 33 | CONCEPT(2), PROJECT(2) |
| planetary regeneration podcast | 32 | 5 | 1 | PROJECT(25), TECHNOLOGY(1) |
| bioregional assembly | 23 | 1 | 14 | CONCEPT(7), PROJECT(1) |
| funding the commons | 11 | 10 | 1 | - |
| regen tokenomics working group | 9 | 1 | 5 | PROJECT(3) |
| cop | 6 | 3 | 3 | - |
| un general assembly | 4 | 3 | 1 | - |
| green proofing | 3 | 1 | 1 | CONCEPT(1) |
| asamblea siekopai | 2 | 1 | 1 | - |

### LICENSE↔STANDARD

**Labels:** 9 | **Total Occurrences:** 119

| Label | Total | LICENSE | STANDARD | Other Types |
| --- | --- | --- | --- | --- |
| json-ld | 72 | 1 | 21 | TECHNOLOGY(32), CONCEPT(18) |
| code of conduct | 14 | 2 | 2 | CONCEPT(10) |
| turtle | 10 | 1 | 4 | TECHNOLOGY(3), CONCEPT(2) |
| commons governance rules | 6 | 1 | 1 | CONCEPT(4) |
| joint development agreement | 5 | 4 | 1 | - |
| trademark law | 4 | 1 | 1 | CONCEPT(2) |
| contributor covenant | 3 | 1 | 1 | ORGANIZATION(1) |
| token ownership & trading policy | 3 | 1 | 1 | CONCEPT(1) |
| reciprocity schedule a | 2 | 1 | 1 | - |

### PERSON↔PROJECT

**Labels:** 9 | **Total Occurrences:** 84

| Label | Total | PERSON | PROJECT | Other Types |
| --- | --- | --- | --- | --- |
| branch out | 33 | 7 | 4 | ORGANIZATION(22) |
| mars | 12 | 5 | 2 | ORGANIZATION(5) |
| jaguar | 10 | 1 | 7 | CONCEPT(1), MATERIAL(1) |
| can | 10 | 1 | 7 | ORGANIZATION(2) |
| richard | 6 | 5 | 1 | - |
| sylvie | 5 | 2 | 3 | - |
| arthine | 4 | 2 | 1 | ORGANIZATION(1) |
| klee medow | 2 | 1 | 1 | - |
| charam | 2 | 1 | 1 | - |

### API_MESSAGE↔PROCESS

**Labels:** 9 | **Total Occurrences:** 52

| Label | Total | API_MESSAGE | PROCESS | Other Types |
| --- | --- | --- | --- | --- |
| issuance | 11 | 1 | 8 | CONCEPT(2) |
| retire | 9 | 6 | 3 | - |
| batchissuance | 8 | 5 | 1 | CONCEPT(2) |
| createproject | 5 | 4 | 1 | - |
| anchor | 5 | 1 | 2 | TECHNOLOGY(1), CONCEPT(1) |
| pickfrombasket | 4 | 1 | 1 | CONCEPT(1), TECHNOLOGY(1) |
| attest | 4 | 1 | 3 | - |
| eventtransfer | 4 | 3 | 1 | - |
| regen/msgbridgereceive | 2 | 1 | 1 | - |

### GOVERNANCE_PROPOSAL↔PROJECT

**Labels:** 8 | **Total Occurrences:** 88

| Label | Total | GOVERNANCE_PROPOSAL | PROJECT | Other Types |
| --- | --- | --- | --- | --- |
| community-spend-pool | 24 | 2 | 1 | CONCEPT(21) |
| $regen tokenomics wg | 16 | 2 | 3 | ORGANIZATION(8), CONCEPT(3) |
| regen commons constitution | 14 | 1 | 3 | CONCEPT(7), STANDARD(3) |
| regen ledger v5.1 | 8 | 5 | 2 | TECHNOLOGY(1) |
| regen ledger v5.0 | 8 | 3 | 3 | TECHNOLOGY(2) |
| pre-pilot desert regreening credit proposal | 6 | 5 | 1 | - |
| regen network proof of authority consensus rfc | 6 | 4 | 1 | TECHNOLOGY(1) |
| regen educational dao | 6 | 1 | 2 | ORGANIZATION(3) |

### LICENSE↔TECHNOLOGY

**Labels:** 7 | **Total Occurrences:** 107

| Label | Total | LICENSE | TECHNOLOGY | Other Types |
| --- | --- | --- | --- | --- |
| json-ld | 72 | 1 | 32 | STANDARD(21), CONCEPT(18) |
| regen works | 15 | 1 | 3 | PROJECT(7), CONCEPT(4) |
| turtle | 10 | 1 | 3 | STANDARD(4), CONCEPT(2) |
| let's encrypt | 4 | 1 | 2 | ORGANIZATION(1) |
| emovis | 2 | 1 | 1 | - |
| common-pooled technology licenses | 2 | 1 | 1 | - |
| open protocols | 2 | 1 | 1 | - |

### CREDIT_CLASS↔TECHNOLOGY

**Labels:** 7 | **Total Occurrences:** 56

| Label | Total | CREDIT_CLASS | TECHNOLOGY | Other Types |
| --- | --- | --- | --- | --- |
| rccs | 18 | 1 | 5 | CONCEPT(8), STANDARD(4) |
| $nct | 13 | 2 | 6 | CONCEPT(2), MATERIAL(2), PROJECT(1) |
| $branch token | 7 | 1 | 6 | - |
| creditbatch | 6 | 1 | 1 | CONCEPT(4) |
| regen score | 5 | 1 | 1 | CLAIM(1), CONCEPT(1), STANDARD(1) |
| coi | 4 | 1 | 1 | PROJECT(1), CONCEPT(1) |
| cfc credits | 3 | 2 | 1 | - |

### API_MESSAGE↔GOVERNANCE_PROPOSAL

**Labels:** 7 | **Total Occurrences:** 39

| Label | Total | API_MESSAGE | GOVERNANCE_PROPOSAL | Other Types |
| --- | --- | --- | --- | --- |
| addcredittype | 7 | 5 | 2 | - |
| alloweddenom | 7 | 6 | 1 | - |
| setclasscreatorallowlist | 6 | 5 | 1 | - |
| removealloweddenom | 6 | 3 | 3 | - |
| updatebasketfee | 6 | 4 | 2 | - |
| addalloweddenom | 4 | 3 | 1 | - |
| credittypeproposal | 3 | 1 | 2 | - |

### EVENT↔TECHNOLOGY

**Labels:** 6 | **Total Occurrences:** 127

| Label | Total | EVENT | TECHNOLOGY | Other Types |
| --- | --- | --- | --- | --- |
| regen mainnet | 66 | 1 | 14 | PROJECT(48), LOCATION(2), CONCEPT(1) |
| planetary regeneration podcast | 32 | 5 | 1 | PROJECT(25), ORGANIZATION(1) |
| hambach testnet | 12 | 1 | 1 | PROJECT(10) |
| eventbridge | 7 | 3 | 1 | API_MESSAGE(3) |
| eventbridgereceive | 6 | 3 | 1 | API_MESSAGE(2) |
| eventanchor | 4 | 1 | 1 | API_MESSAGE(2) |

### GOVERNANCE_PROPOSAL↔TECHNOLOGY

**Labels:** 6 | **Total Occurrences:** 50

| Label | Total | GOVERNANCE_PROPOSAL | TECHNOLOGY | Other Types |
| --- | --- | --- | --- | --- |
| regen ledger v6.0 | 11 | 7 | 4 | - |
| software upgrade | 10 | 1 | 1 | PROCESS(5), CONCEPT(3) |
| regen ledger v5.1 | 8 | 5 | 1 | PROJECT(2) |
| regen ledger v5.0 | 8 | 3 | 2 | PROJECT(3) |
| regen ledger v3.0 | 7 | 4 | 3 | - |
| regen network proof of authority consensus rfc | 6 | 4 | 1 | PROJECT(1) |

### EVIDENCE↔STANDARD

**Labels:** 5 | **Total Occurrences:** 99

| Label | Total | EVIDENCE | STANDARD | Other Types |
| --- | --- | --- | --- | --- |
| cat receipts | 63 | 17 | 1 | TECHNOLOGY(22), CONCEPT(20), PROCESS(1), MATERIAL(1), PROJECT(1) |
| white paper | 14 | 1 | 1 | CONCEPT(6), TECHNOLOGY(5), PROJECT(1) |
| koi rids | 11 | 1 | 1 | TECHNOLOGY(5), CONCEPT(3), PROJECT(1) |
| whitepaper | 7 | 3 | 1 | PROJECT(2), CONCEPT(1) |
| terrasos whitepaper | 4 | 1 | 2 | PROJECT(1) |

### CLAIM↔TECHNOLOGY

**Labels:** 5 | **Total Occurrences:** 48

| Label | Total | CLAIM | TECHNOLOGY | Other Types |
| --- | --- | --- | --- | --- |
| attestation | 32 | 1 | 2 | CONCEPT(19), PROCESS(10) |
| catreceipt | 7 | 1 | 4 | CONCEPT(1), PROCESS(1) |
| regen score | 5 | 1 | 1 | CONCEPT(1), CREDIT_CLASS(1), STANDARD(1) |
| the design pathway for regenerating earth | 2 | 1 | 1 | - |
| production operational | 2 | 1 | 1 | - |

### EVENT↔PROCESS

**Labels:** 5 | **Total Occurrences:** 16

| Label | Total | EVENT | PROCESS | Other Types |
| --- | --- | --- | --- | --- |
| mainnet launch | 4 | 1 | 1 | CONCEPT(2) |
| project termination | 4 | 1 | 3 | - |
| project start date | 3 | 1 | 1 | CONCEPT(1) |
| steward council election | 3 | 1 | 2 | - |
| mvp launch | 2 | 1 | 1 | - |

### MODULE↔ORGANIZATION

**Labels:** 4 | **Total Occurrences:** 141

| Label | Total | MODULE | ORGANIZATION | Other Types |
| --- | --- | --- | --- | --- |
| base | 95 | 1 | 2 | TECHNOLOGY(68), PROJECT(24) |
| data | 26 | 22 | 2 | CONCEPT(2) |
| bank | 12 | 9 | 1 | KEEPER(2) |
| yonearth | 8 | 1 | 3 | PROJECT(3), TECHNOLOGY(1) |

### MATERIAL↔ORGANIZATION

**Labels:** 4 | **Total Occurrences:** 56

| Label | Total | MATERIAL | ORGANIZATION | Other Types |
| --- | --- | --- | --- | --- |
| soil | 31 | 24 | 2 | CREDIT_CLASS(3), CONCEPT(2) |
| nature | 17 | 1 | 16 | - |
| forest | 4 | 1 | 1 | CONCEPT(2) |
| ghg | 4 | 1 | 1 | CONCEPT(2) |

### LICENSE↔ORGANIZATION

**Labels:** 4 | **Total Occurrences:** 35

| Label | Total | LICENSE | ORGANIZATION | Other Types |
| --- | --- | --- | --- | --- |
| creative commons | 18 | 14 | 4 | - |
| apache | 10 | 8 | 2 | - |
| let's encrypt | 4 | 1 | 1 | TECHNOLOGY(2) |
| contributor covenant | 3 | 1 | 1 | STANDARD(1) |

### MODULE↔PROJECT

**Labels:** 3 | **Total Occurrences:** 174

| Label | Total | MODULE | PROJECT | Other Types |
| --- | --- | --- | --- | --- |
| base | 95 | 1 | 24 | TECHNOLOGY(68), ORGANIZATION(2) |
| marketplace | 71 | 16 | 11 | TECHNOLOGY(27), CONCEPT(16), PROCESS(1) |
| yonearth | 8 | 1 | 3 | ORGANIZATION(3), TECHNOLOGY(1) |

### MATERIAL↔PROCESS

**Labels:** 3 | **Total Occurrences:** 86

| Label | Total | MATERIAL | PROCESS | Other Types |
| --- | --- | --- | --- | --- |
| cat receipts | 63 | 1 | 1 | TECHNOLOGY(22), CONCEPT(20), EVIDENCE(17), PROJECT(1), STANDARD(1) |
| buffer pool | 14 | 1 | 1 | CONCEPT(10), TECHNOLOGY(2) |
| cat receipt chain | 9 | 1 | 1 | CONCEPT(3), EVIDENCE(2), TECHNOLOGY(2) |

### EVIDENCE↔MATERIAL

**Labels:** 3 | **Total Occurrences:** 74

| Label | Total | EVIDENCE | MATERIAL | Other Types |
| --- | --- | --- | --- | --- |
| cat receipts | 63 | 17 | 1 | TECHNOLOGY(22), CONCEPT(20), PROCESS(1), PROJECT(1), STANDARD(1) |
| cat receipt chain | 9 | 2 | 1 | CONCEPT(3), TECHNOLOGY(2), PROCESS(1) |
| emission factors | 2 | 1 | 1 | - |

### CREDIT_CLASS↔MATERIAL

**Labels:** 3 | **Total Occurrences:** 48

| Label | Total | CREDIT_CLASS | MATERIAL | Other Types |
| --- | --- | --- | --- | --- |
| soil | 31 | 3 | 24 | ORGANIZATION(2), CONCEPT(2) |
| $nct | 13 | 2 | 2 | TECHNOLOGY(6), CONCEPT(2), PROJECT(1) |
| eco-credits | 4 | 2 | 1 | PROJECT(1) |

### CREDIT_CLASS↔ORGANIZATION

**Labels:** 3 | **Total Occurrences:** 46

| Label | Total | CREDIT_CLASS | ORGANIZATION | Other Types |
| --- | --- | --- | --- | --- |
| soil | 31 | 3 | 2 | MATERIAL(24), CONCEPT(2) |
| city forest credits | 11 | 4 | 4 | PROJECT(3) |
| natureplus | 4 | 2 | 1 | PROJECT(1) |

### GOVERNANCE_PROPOSAL↔ORGANIZATION

**Labels:** 3 | **Total Occurrences:** 28

| Label | Total | GOVERNANCE_PROPOSAL | ORGANIZATION | Other Types |
| --- | --- | --- | --- | --- |
| $regen tokenomics wg | 16 | 2 | 8 | PROJECT(3), CONCEPT(3) |
| creation of the $regen liquidity dao | 6 | 5 | 1 | - |
| regen educational dao | 6 | 1 | 3 | PROJECT(2) |

### LOCATION↔PERSON

**Labels:** 3 | **Total Occurrences:** 26

| Label | Total | LOCATION | PERSON | Other Types |
| --- | --- | --- | --- | --- |
| austin | 19 | 3 | 16 | - |
| shantigar | 4 | 3 | 1 | - |
| kauai | 3 | 1 | 2 | - |

### GOVERNANCE_PROPOSAL↔STANDARD

**Labels:** 3 | **Total Occurrences:** 23

| Label | Total | GOVERNANCE_PROPOSAL | STANDARD | Other Types |
| --- | --- | --- | --- | --- |
| regen commons constitution | 14 | 1 | 3 | CONCEPT(7), PROJECT(3) |
| regen constitution | 7 | 2 | 1 | CONCEPT(4) |
| protocol governance proposal template | 2 | 1 | 1 | - |

### PERSON↔TECHNOLOGY

**Labels:** 3 | **Total Occurrences:** 20

| Label | Total | PERSON | TECHNOLOGY | Other Types |
| --- | --- | --- | --- | --- |
| shaun | 9 | 7 | 2 | - |
| adam | 6 | 2 | 3 | ORGANIZATION(1) |
| sage | 5 | 2 | 3 | - |

### CONCEPT↔PERSON

**Labels:** 3 | **Total Occurrences:** 15

| Label | Total | CONCEPT | PERSON | Other Types |
| --- | --- | --- | --- | --- |
| jaguar | 10 | 1 | 1 | PROJECT(7), MATERIAL(1) |
| registryagent | 3 | 2 | 1 | - |
| grant | 2 | 1 | 1 | - |

### EVENT↔LOCATION

**Labels:** 2 | **Total Occurrences:** 72

| Label | Total | EVENT | LOCATION | Other Types |
| --- | --- | --- | --- | --- |
| regen mainnet | 66 | 1 | 2 | PROJECT(48), TECHNOLOGY(14), CONCEPT(1) |
| east denver | 6 | 3 | 3 | - |

### MATERIAL↔STANDARD

**Labels:** 2 | **Total Occurrences:** 69

| Label | Total | MATERIAL | STANDARD | Other Types |
| --- | --- | --- | --- | --- |
| cat receipts | 63 | 1 | 1 | TECHNOLOGY(22), CONCEPT(20), EVIDENCE(17), PROCESS(1), PROJECT(1) |
| greenhouse gasses | 6 | 2 | 1 | CONCEPT(3) |

### CLAIM↔PROCESS

**Labels:** 2 | **Total Occurrences:** 39

| Label | Total | CLAIM | PROCESS | Other Types |
| --- | --- | --- | --- | --- |
| attestation | 32 | 1 | 10 | CONCEPT(19), TECHNOLOGY(2) |
| catreceipt | 7 | 1 | 1 | TECHNOLOGY(4), CONCEPT(1) |

### CREDIT_CLASS↔PROCESS

**Labels:** 2 | **Total Occurrences:** 19

| Label | Total | CREDIT_CLASS | PROCESS | Other Types |
| --- | --- | --- | --- | --- |
| eco-credit retirements | 10 | 1 | 1 | CONCEPT(8) |
| desert regreening | 9 | 3 | 1 | CONCEPT(5) |

### LICENSE↔PROJECT

**Labels:** 2 | **Total Occurrences:** 18

| Label | Total | LICENSE | PROJECT | Other Types |
| --- | --- | --- | --- | --- |
| regen works | 15 | 1 | 7 | CONCEPT(4), TECHNOLOGY(3) |
| regen brand trademark | 3 | 2 | 1 | - |

### KEEPER↔MODULE

**Labels:** 2 | **Total Occurrences:** 17

| Label | Total | KEEPER | MODULE | Other Types |
| --- | --- | --- | --- | --- |
| bank | 12 | 2 | 9 | ORGANIZATION(1) |
| bank module | 5 | 1 | 4 | - |

### CLAIM↔CREDIT_CLASS

**Labels:** 2 | **Total Occurrences:** 11

| Label | Total | CLAIM | CREDIT_CLASS | Other Types |
| --- | --- | --- | --- | --- |
| certified regen | 6 | 1 | 1 | CONCEPT(3), STANDARD(1) |
| regen score | 5 | 1 | 1 | CONCEPT(1), TECHNOLOGY(1), STANDARD(1) |

### CLAIM↔STANDARD

**Labels:** 2 | **Total Occurrences:** 11

| Label | Total | CLAIM | STANDARD | Other Types |
| --- | --- | --- | --- | --- |
| certified regen | 6 | 1 | 1 | CONCEPT(3), CREDIT_CLASS(1) |
| regen score | 5 | 1 | 1 | CONCEPT(1), TECHNOLOGY(1), CREDIT_CLASS(1) |

### LOCATION↔MATERIAL

**Labels:** 2 | **Total Occurrences:** 4

| Label | Total | LOCATION | MATERIAL | Other Types |
| --- | --- | --- | --- | --- |
| rainforest | 2 | 1 | 1 | - |
| selva | 2 | 1 | 1 | - |

### CLAIM↔EVIDENCE

**Labels:** 2 | **Total Occurrences:** 4

| Label | Total | CLAIM | EVIDENCE | Other Types |
| --- | --- | --- | --- | --- |
| usd 524 million invested as of 2019 | 2 | 1 | 1 | - |
| 1.3 million hectares under pes contracts | 2 | 1 | 1 | - |

### API_MESSAGE↔MODULE

**Labels:** 1 | **Total Occurrences:** 39

| Label | Total | API_MESSAGE | MODULE | Other Types |
| --- | --- | --- | --- | --- |
| basket | 39 | 2 | 18 | CONCEPT(16), TECHNOLOGY(3) |

### PROCESS↔QUESTION

**Labels:** 1 | **Total Occurrences:** 28

| Label | Total | PROCESS | QUESTION | Other Types |
| --- | --- | --- | --- | --- |
| governance proposals | 28 | 1 | 1 | CONCEPT(26) |

### API_MESSAGE↔PROJECT

**Labels:** 1 | **Total Occurrences:** 21

| Label | Total | API_MESSAGE | PROJECT | Other Types |
| --- | --- | --- | --- | --- |
| bridge | 21 | 7 | 2 | TECHNOLOGY(9), CONCEPT(3) |

### CONCEPT↔KEEPER

**Labels:** 1 | **Total Occurrences:** 18

| Label | Total | CONCEPT | KEEPER | Other Types |
| --- | --- | --- | --- | --- |
| basket keeper | 18 | 1 | 17 | - |

### API_MESSAGE↔MATERIAL

**Labels:** 1 | **Total Occurrences:** 16

| Label | Total | API_MESSAGE | MATERIAL | Other Types |
| --- | --- | --- | --- | --- |
| credits | 16 | 4 | 2 | CONCEPT(10) |

### KEEPER↔ORGANIZATION

**Labels:** 1 | **Total Occurrences:** 12

| Label | Total | KEEPER | ORGANIZATION | Other Types |
| --- | --- | --- | --- | --- |
| bank | 12 | 2 | 1 | MODULE(9) |

### CLAIM↔GOVERNANCE_PROPOSAL

**Labels:** 1 | **Total Occurrences:** 11

| Label | Total | CLAIM | GOVERNANCE_PROPOSAL | Other Types |
| --- | --- | --- | --- | --- |
| token burning upgrades | 11 | 1 | 5 | CONCEPT(5) |

### MATERIAL↔PERSON

**Labels:** 1 | **Total Occurrences:** 10

| Label | Total | MATERIAL | PERSON | Other Types |
| --- | --- | --- | --- | --- |
| jaguar | 10 | 1 | 1 | PROJECT(7), CONCEPT(1) |

### MODULE↔QUESTION

**Labels:** 1 | **Total Occurrences:** 8

| Label | Total | MODULE | QUESTION | Other Types |
| --- | --- | --- | --- | --- |
| cosmos sdk module | 8 | 6 | 1 | TECHNOLOGY(1) |

### QUESTION↔TECHNOLOGY

**Labels:** 1 | **Total Occurrences:** 8

| Label | Total | QUESTION | TECHNOLOGY | Other Types |
| --- | --- | --- | --- | --- |
| cosmos sdk module | 8 | 1 | 1 | MODULE(6) |

### API_MESSAGE↔CREDIT_CLASS

**Labels:** 1 | **Total Occurrences:** 6

| Label | Total | API_MESSAGE | CREDIT_CLASS | Other Types |
| --- | --- | --- | --- | --- |
| class | 6 | 4 | 1 | CONCEPT(1) |

### LOCATION↔STANDARD

**Labels:** 1 | **Total Occurrences:** 4

| Label | Total | LOCATION | STANDARD | Other Types |
| --- | --- | --- | --- | --- |
| manchester | 4 | 2 | 2 | - |

### LOCATION↔VALIDATOR

**Labels:** 1 | **Total Occurrences:** 4

| Label | Total | LOCATION | VALIDATOR | Other Types |
| --- | --- | --- | --- | --- |
| gmt | 4 | 2 | 1 | TECHNOLOGY(1) |

### EVENT↔PERSON

**Labels:** 1 | **Total Occurrences:** 3

| Label | Total | EVENT | PERSON | Other Types |
| --- | --- | --- | --- | --- |
| ethan | 3 | 1 | 2 | - |

### GOVERNANCE_PROPOSAL↔QUESTION

**Labels:** 1 | **Total Occurrences:** 3

| Label | Total | GOVERNANCE_PROPOSAL | QUESTION | Other Types |
| --- | --- | --- | --- | --- |
| feedback request: desert regreening credit class proposal | 3 | 2 | 1 | - |

### CONCEPT↔VALIDATOR

**Labels:** 1 | **Total Occurrences:** 2

| Label | Total | CONCEPT | VALIDATOR | Other Types |
| --- | --- | --- | --- | --- |
| bioregional validators | 2 | 1 | 1 | - |

### LICENSE↔PROCESS

**Labels:** 1 | **Total Occurrences:** 2

| Label | Total | LICENSE | PROCESS | Other Types |
| --- | --- | --- | --- | --- |
| intellectual property licensing | 2 | 1 | 1 | - |

---

## Analysis: Actionable Wrong-Type Patterns

Based on the pair breakdowns above, identify patterns where:
- One type is clearly dominant (>90% of occurrences)
- The minority type has very low counts (1-5 occurrences)
- The minority type appears to be extraction noise

### Auto-Detected Candidates

Labels where one type has <3 occurrences and <5% of total (potential noise):

| Label | Wrong Type | Wrong Occ | Dominant Type | Dominant Occ |
| --- | --- | --- | --- | --- |
| amino message | API_MESSAGE | 1 | CONCEPT | 17 |
| terrasos | CONCEPT | 2 | ORGANIZATION | 37 |
| discourse | CONCEPT | 2 | ORGANIZATION | 12 |
| regenai | CONCEPT | 1 | ORGANIZATION | 10 |
| eth | CONCEPT | 1 | MATERIAL | 3 |
| regen ai | CONCEPT | 1 | ORGANIZATION | 1 |
| age | CONCEPT | 1 | ORGANIZATION | 1 |
| credit type | CREDIT_CLASS | 2 | CONCEPT | 61 |
| currency allowlist | GOVERNANCE_PROPOSAL | 1 | CONCEPT | 19 |
| biodiversity | MATERIAL | 1 | CONCEPT | 112 |
| carbon sequestration | MATERIAL | 1 | CONCEPT | 73 |
| ecological assets | MATERIAL | 3 | CONCEPT | 60 |
| cat receipts | MATERIAL | 1 | CONCEPT | 20 |
| region token | MATERIAL | 2 | CONCEPT | 19 |
| atom | MATERIAL | 1 | CONCEPT | 3 |
| regeneration | ORGANIZATION | 2 | CONCEPT | 85 |
| planetary regeneration | ORGANIZATION | 1 | CONCEPT | 40 |
| knowledge organization infrastructure | ORGANIZATION | 1 | CONCEPT | 30 |
| ontology | ORGANIZATION | 1 | CONCEPT | 29 |
| regen ai | ORGANIZATION | 1 | CONCEPT | 1 |
| age | ORGANIZATION | 1 | CONCEPT | 1 |
| eco bridge | ORGANIZATION | 1 | VALIDATOR | 1 |
| akash | ORGANIZATION | 1 | VALIDATOR | 1 |
| entityqualityfilter | PROCESS | 1 | TECHNOLOGY | 31 |
| marketplace | PROCESS | 1 | TECHNOLOGY | 27 |
| cat receipts | PROCESS | 1 | TECHNOLOGY | 22 |
| canonicalresolver | PROCESS | 1 | TECHNOLOGY | 19 |
| confidencefilter | PROCESS | 1 | TECHNOLOGY | 15 |
| semantic search | PROCESS | 1 | TECHNOLOGY | 8 |
| regen coordination | PROCESS | 1 | PROJECT | 8 |

---

*Report generated by `scripts/kg_audit_unexpected_pairs_report.py`*