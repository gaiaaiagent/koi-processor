# Regen Network Knowledge Graph Quality Review & Re-Extraction

**Started:** 2025-12-20
**Last Updated:** 2025-12-23
**Status:** ✅ CLOSED (Cycle 2025‑12)
**Graph URL:** https://regen.gaiaai.xyz/graph
**Server:** ssh darren@202.61.196.119
**Primary Repo:** koi-processor

---

## Overview

This document tracks the systematic review of the Regen Network knowledge graph, identification of errors, root cause analysis, fixes, and eventual full re-extraction.

## Progress Summary (2025-12-23)

| Phase | Status | Details |
|-------|--------|---------|
| Stage 6 Re-Extraction | ✅ | 12,002 docs, 88,322 entities, 17,329 relationships |
| FIX-007 Predicate Consolidation | ✅ | 3,303 → 1,501 predicates (-54.6%) |
| Fuseki Rebuild (Production) | ✅ | 163,699 triples (final), all quality gates passing |
| Code↔Docs Bridge | ✅ | 16,820 code artifacts, 6,453 doc→code links |
| AGE Sync | ✅ | 5,464 stub nodes, 6,463 edges (MENTIONS + CODE_REF) |
| **FIX-006 Entity Deduplication** | ✅ | Deployed + 323 merges applied: 29,667 entities, 163,699 triples |

---

## FIX-006 Entity Deduplication - DEPLOYED

**Deployed:** 2025-12-23
**Status:** ✅ Production deployed, canary validated

### Implementation Summary

FIX-006 introduces a multi-tier entity deduplication system:

| Tier | Method | Threshold | Description |
|------|--------|-----------|-------------|
| 1 | Exact Match | 100% | B-Tree lookup on normalized_text |
| 1.5 | Canonical Alias | 100% | Lookup in canonical_entities.json |
| 1.x | Fuzzy String | Type-specific | Jaro-Winkler (PERSON: 0.93), Token-sort (ORG: 0.85) |
| 2 | Semantic | Type-specific | HNSW vector similarity |
| 3 | Create New | - | Deterministic URI generation |

### Key Changes

1. **Shared normalizer** (`entity_normalizer.py`) - underscore/hyphen/@ handling, pipe suffix stripping
2. **Per-type thresholds** - PERSON 0.93 (raised from 0.88), ORGANIZATION 0.85
3. **Expanded canonical registry** (`canonical_entities.json` v2.1.0) - 212 mappings
4. **Fuzzy tier** (rapidfuzz) - Jaro-Winkler for names, token_sort_ratio for orgs

### Dry-Run Results (2025-12-23)

| Metric | Count |
|--------|------:|
| Total merge proposals | 8,414 |
| tier1_normalized | 268 |
| tier1_5_canonical | 97 |
| tier1x_fuzzy | 3,785 |
| type_conflict (report-only) | 4,264 |
| PERSON proposals | 101 |

### Canary Validation

Entity resolution tested with production entity_registry:

| Variant | Resolution | Method |
|---------|------------|--------|
| Gregory_Regen | Gregory Landua | tier1_5_canonical |
| Gregory \| RND | Gregory Landua | tier1_5_canonical |
| greg-landua | Gregory Landua | tier1_5_canonical |
| regen.foundation | Regen Foundation | tier1_exact |
| @willszal | Will Szal | tier1_5_canonical |

### Files Deployed to Production

- `src/knowledge_graph/entity_normalizer.py` (new)
- `src/knowledge_graph/entity_resolver.py` (modified)
- `src/knowledge_graph/graph_integration.py` (modified)
- `src/knowledge_graph/improvements/canonical_resolver.py` (modified)
- `src/knowledge_graph/uri_generator.py` (modified)
- `data/canonical_entities.json` (v2.1.0)
- `requirements.txt` (added rapidfuzz)
- `tests/test_fix006_entity_dedup.py` (32 tests)
- `scripts/dedup_dry_run.py`
- `scripts/apply_safe_merges.py`
- `scripts/apply_dedup_merges.py`

### Safe Merges Applied (2025-12-23)

Applied safe entity merges in two passes to clean up existing duplicates:

| Metric | Before | After Pass 2 |
|--------|--------|--------------|
| Entities | 30,041 | 29,667 |
| Triples | 165,619 | 163,699 |
| Relationships | 15,414 | 15,364 |
| Quality Gates | 4/4 PASS | 4/4 PASS |

**Pass 1** (apply_safe_merges.py):
- Attempted 365 safe merges (tier1_normalized + tier1_5_canonical)
- Script had rollback bug that reverted prior successful merges on each error
- ~51 entities merged before issue identified

**Pass 2** (apply_dedup_merges.py with fixes):
- Fixed SQL CTE reference error (changed `merge_fields_sql("w", "l")` to `merge_fields_sql("w", "c")`)
- Added self-referential relationship deletion before merge
- Added SAVEPOINT handling for per-merge transaction isolation
- **323 merges successfully applied**:
  - tier1_normalized: 207
  - tier1_5_canonical: 116
- Fuseki rebuilt from merged PostgreSQL data

**Resolved Duplicate Clusters:**
| Entity | Before | After | Canonical Occurrences |
|--------|--------|-------|----------------------|
| Gregory Landua | 5 variants | 1 canonical | 684 |
| Regen Foundation | 10 variants | 1 canonical | 610 |

### Remaining Work

1. Manual review of tier1x_fuzzy proposals (3,785 - has 6-8 PERSON false positives)
2. Type conflict curation (4,264 - requires domain expertise)
3. Consider full re-extraction to leverage FIX-006 for remaining duplicates

---

## Future Work

1. ~~Apply safe entity merges~~ - ✅ DONE (2025-12-23)
2. Further predicate reduction (1,501 → ~100-200) - optional, only if UX/retrieval needs it
3. FIX-008 (dual-write strategy) - optional, current PG→Fuseki rebuild path works
4. Review tier1x_fuzzy merge proposals for safe ORGANIZATION merges

---

## Workflow Stages

1. **Error Discovery** - Review graph and catalog all errors found
2. **Root Cause Analysis** - Determine where each error originated (extraction, post-processing, loading, etc.)
3. **Fix Planning** - Design solutions for each error category
4. **Implementation** - Make the fixes in the relevant codebase
5. **Targeted Testing** - Test fixes on affected document subsets
6. **Full Re-Extraction** - Run complete extraction with all fixes applied

---

## Stage 1: Error Discovery

### Errors Found

#### Incorrect Entity Types

| ID | Entity | Current Type | Should Be | Reasoning | Date Found |
|----|--------|--------------|-----------|-----------|------------|
| E001 | Regen Network | HUMANACTOR | ORGANIZATION | Regen Network is a company/organization, not a human actor | 2025-12-20 |
| E002 | Regen Network community | HUMANACTOR | GROUP/COMMUNITY | This is a community of people, not an individual human actor | 2025-12-20 |
| E003 | Regen Network Community (person 2) | PERSON | GROUP/COMMUNITY | Duplicate of E002, and communities are not persons | 2025-12-20 |
| E004 | Major Release Process | CLAIM | PROCESS/PROCEDURE | A process is not a claim - what would even be claimed here? | 2025-12-20 |
| E005 | Regen Registry | ORGANIZATION | PLATFORM/PROGRAM | Per official docs: "A comprehensive program, platform, and process designed to establish community standards and legal frameworks for quantifying, monitoring, and trading ecological credits" | 2025-12-20 |
| E006 | Add ecocredit batch metadata migration | CLAIM | TASK/COMMIT | This is a git commit message or task, not a claim | 2025-12-20 |
| E007 | Fix REGEN_HOME environment variable | CLAIM | TASK/COMMIT | This is a git commit message or task, not a claim | 2025-12-20 |
| E008 | carbon sequestration | PROJECT | CONCEPT/PROCESS | Carbon sequestration is a concept/ecological process, not a specific project | 2025-12-20 |
| E009 | biochar | CLAIM | MATERIAL/SUBSTANCE | Biochar is a physical material (charcoal-like substance), not a claim | 2025-12-20 |
| E010 | Biochar Credit Class | PROJECT | CREDIT_CLASS | Credit Classes are classification entities per Regen's schema (rfs:CreditClassInfo) | 2025-12-20 |
| E011 | Node | PERSON | ??? | "Node" is not a person - likely extraction artifact | 2025-12-20 |
| E012 | commonsoriented | ENTITY | N/A (remove?) | Appears to be a tag/attribute, not a meaningful entity | 2025-12-20 |
| E013 | forum user | HUMANACTOR | ROLE/TEMPLATE | Generic "forum user" is a role template, not a specific actor | 2025-12-20 |
| E014 | reviewers (humanactor 2) | HUMANACTOR | ROLE/TEMPLATE | Generic "reviewers" is a role, not a specific actor | 2025-12-20 |
| E256 | Patch Release Process | CLAIM | PROCESS/PROCEDURE | This is a release workflow heading, not a claim.<br>Query: `SELECT entity_text, occurrence_count FROM entity_registry WHERE entity_type='CLAIM' AND entity_text='Patch Release Process';` | 2025-12-20 |
| E257 | Minor Release Process | CLAIM | PROCESS/PROCEDURE | This is a release workflow heading, not a claim.<br>Query: `SELECT entity_text, occurrence_count FROM entity_registry WHERE entity_type='CLAIM' AND entity_text='Minor Release Process';` | 2025-12-20 |
| E258 | Release Candidate Process | CLAIM | PROCESS/PROCEDURE | This is a release workflow heading, not a claim.<br>Query: `SELECT entity_text, occurrence_count FROM entity_registry WHERE entity_type='CLAIM' AND entity_text='Release Candidate Process';` | 2025-12-20 |
| E259 | Validator Setup | CLAIM | PROCESS/PROCEDURE | Operational setup instructions are procedures, not claims.<br>Query: `SELECT entity_text, occurrence_count FROM entity_registry WHERE entity_type='CLAIM' AND entity_text='Validator Setup';` | 2025-12-20 |
| E260 | Validator Moniker Submission | CLAIM | PROCESS/PROCEDURE | A submission procedure/workflow, not a claim.<br>Query: `SELECT entity_text, occurrence_count FROM entity_registry WHERE entity_type='CLAIM' AND entity_text='Validator Moniker Submission';` | 2025-12-20 |
| E261 | Add marketplace Msg/UpdateSellOrders | CLAIM | CHANGELOG_ENTRY / CODE_CHANGE | Looks like a changelog/commit entry from GitHub, not an extracted claim.<br>Query: `SELECT entity_text, occurrence_count FROM entity_registry WHERE entity_type='CLAIM' AND entity_text='Add marketplace Msg/UpdateSellOrders';` | 2025-12-20 |
| E262 | Add BasketBalances query server method | CLAIM | CHANGELOG_ENTRY / CODE_CHANGE | Changelog/commit-style action phrase extracted as CLAIM.<br>Query: `SELECT entity_text, occurrence_count FROM entity_registry WHERE entity_type='CLAIM' AND entity_text='Add BasketBalances query server method';` | 2025-12-20 |
| E263 | Update protobuf package version to v1 | CLAIM | CHANGELOG_ENTRY / CODE_CHANGE | Implementation detail/version bump extracted as CLAIM.<br>Query: `SELECT entity_text, occurrence_count FROM entity_registry WHERE entity_type='CLAIM' AND entity_text='Update protobuf package version to v1';` | 2025-12-20 |
| E264 | MsgCreateBatch | CLAIM | PROTOBUF_MESSAGE / TX_TYPE | This is a protobuf/API identifier, not a natural-language claim.<br>Query: `SELECT entity_text, occurrence_count FROM entity_registry WHERE entity_type='CLAIM' AND entity_text='MsgCreateBatch';` | 2025-12-20 |
| E265 | EventBuyDirect | CLAIM | PROTOBUF_EVENT | This is an emitted event name, not a claim.<br>Query: `SELECT entity_text, occurrence_count FROM entity_registry WHERE entity_type='CLAIM' AND entity_text='EventBuyDirect';` | 2025-12-20 |
| E266 | Discussions | CLAIM | FORUM_CATEGORY / TOPIC | Generic forum/category heading extracted as CLAIM.<br>Query: `SELECT entity_text, occurrence_count FROM entity_registry WHERE entity_type='CLAIM' AND entity_text='Discussions';` | 2025-12-20 |
| E267 | Semantic Versioning | CLAIM | STANDARD / POLICY | A software versioning standard, not a claim.<br>Query: `SELECT entity_text, occurrence_count FROM entity_registry WHERE entity_type='CLAIM' AND entity_text='Semantic Versioning';` | 2025-12-20 |
| E268 | Release v0.6.0 | CLAIM | SOFTWARE_RELEASE / VERSION | A release/version label, not a claim.<br>Query: `SELECT entity_text, occurrence_count FROM entity_registry WHERE entity_type='CLAIM' AND entity_text='Release v0.6.0';` | 2025-12-20 |
| E269 | Bitcoin | (Fuseki) `koi#Claim` / `koi#PROJECT` / `koi#ORGANIZATION` / `koi#HumanActor` | ASSET / TECHNOLOGY (remove CLAIM) | In Fuseki, `Bitcoin` is erroneously typed as `Claim` (and even `HumanActor`). Should be a technology/asset concept, not a claim or person/org.<br>Query: `PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> SELECT ?type WHERE { <http://regen.network/koi/entity/Bitcoin> a ?type }` | 2025-12-20 |
| E270 | IPFS | (Fuseki) includes `koi#Claim` | TECHNOLOGY (remove CLAIM) | IPFS is a technology/protocol; the `Claim` type assertion is incorrect.<br>Query: `PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> SELECT ?type WHERE { <http://regen.network/koi/entity/IPFS> a ?type }` | 2025-12-20 |
| E271 | Telegram | (Fuseki) includes `koi#Claim` | PLATFORM / ORGANIZATION (remove CLAIM) | Telegram is a messaging platform (org/product), not a claim; `Claim` typing indicates systematic over-typing.<br>Query: `PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> SELECT ?type WHERE { <http://regen.network/koi/entity/Telegram> a ?type }` | 2025-12-20 |
| E272 | blockchain | (Fuseki) includes `koi#Claim` | CONCEPT / TECHNOLOGY (remove CLAIM) | “blockchain” is a concept/technology, not a claim.<br>Query: `PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> SELECT ?type WHERE { <http://regen.network/koi/entity/blockchain> a ?type }` | 2025-12-20 |
| E273 | AI | (Fuseki) includes `koi#Claim` | CONCEPT / TECHNOLOGY (remove CLAIM) | “AI” is a concept/technology, not a claim; `Claim` typing is incorrect.<br>Query: `PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> SELECT ?type WHERE { <http://regen.network/koi/entity/AI> a ?type }` | 2025-12-20 |
| E274 | Regen Ledger | (Fuseki) includes `koi#Claim` / `koi#HumanActor` / `koi#PERSON` | TECHNOLOGY / PROJECT (remove CLAIM) | In Fuseki, Regen Ledger is incorrectly typed as `Claim` and even as a person/human actor; it should be a software project/technology.<br>Query: `PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> SELECT ?type WHERE { <http://regen.network/koi/entity/Regen_Ledger> a ?type }` | 2025-12-20 |
| E031 | $Regen | PROJECT (and others) | CURRENCY/TOKEN | Token/Currency is not a project. Also has conflicting types. | 2025-12-20 |
| E032 | Regen Token | PROJECT (and others) | CURRENCY/TOKEN | Token/Currency is not a project. | 2025-12-20 |
| E033 | USDC | PROJECT | CURRENCY/TOKEN | Stablecoin is not a project. | 2025-12-20 |
| E034 | ATOM | PROJECT | CURRENCY/TOKEN | Network token is not a project. | 2025-12-20 |
| E035 | Cosmos SDK | PROJECT (and others) | SOFTWARE/TECHNOLOGY | Software library/framework is not a project (in ecological sense). | 2025-12-20 |
| E036 | CosmWasm | PROJECT | SOFTWARE/TECHNOLOGY | Smart contract platform/software is not a project. | 2025-12-20 |
| E037 | Inter-Blockchain Communication Protocol | PROJECT | PROTOCOL/STANDARD | Protocol is not a project. | 2025-12-20 |
| E038 | Mailing List Mode | PROJECT | ARTIFACT (Remove) | Forum interface text extracted as entity. | 2025-12-20 |
| E039 | Podcast Episode | PROJECT | CONTENT/MEDIA | Podcast episode is media content, not a project. | 2025-12-20 |
| E040 | Nature Carbon Tonne (NCT) | PROJECT | CREDIT_CLASS/TOKEN | Digital Carbon Token/Class is not a project. | 2025-12-20 |
| E044 | Regen Data | PROJECT | CONCEPT/TOPIC | Too generic. Likely refers to the domain of data or the Data Module. | 2025-12-20 |
| E045 | Claude | PERSON | AI_SYSTEM | Claude is Anthropic's AI assistant, not a person | 2025-12-20 |
| E046 | Claude Code | PERSON | AI_SYSTEM | Claude Code is an AI coding assistant tool | 2025-12-20 |
| E047 | OpenAI | PERSON | ORGANIZATION | OpenAI is a company, not a person | 2025-12-20 |
| E048 | GPT | PERSON | AI_SYSTEM | GPT is an AI model family, not a person | 2025-12-20 |
| E049 | gaiaaiagent | PERSON | AI_SYSTEM | This is an AI agent, not a person | 2025-12-20 |
| E050 | agent | PERSON | ROLE/ARTIFACT | Generic "agent" is either a role or extraction artifact | 2025-12-20 |
| E051 | Eliza Agent | PERSON | AI_SYSTEM | Eliza Agent is an AI system | 2025-12-20 |
| E052 | Registry Agent | PERSON | AI_SYSTEM | Registry Agent is an AI/automated system | 2025-12-20 |
| E053 | BGE Embedding Server | PERSON | SOFTWARE | BGE is an embedding model/server, not a person | 2025-12-20 |
| E054 | DeepSeek Coder 6.7B | PERSON | AI_SYSTEM | DeepSeek is an AI model | 2025-12-20 |
| E055 | Llama 3.2 | PERSON | AI_SYSTEM | Llama is an AI model | 2025-12-20 |
| E056 | Whisper AI | PERSON | AI_SYSTEM | Whisper is an AI speech model | 2025-12-20 |
| E057 | Development Team | PERSON | GROUP/TEAM | Teams are groups, not individuals | 2025-12-20 |
| E058 | REGEN LEDGER LEAD | PERSON | ROLE | This is a role/position, not a person | 2025-12-20 |
| E059 | COMMS LEAD | PERSON | ROLE | This is a role/position, not a person | 2025-12-20 |
| E060 | PARTNERSHIPS LEAD | PERSON | ROLE | This is a role/position, not a person | 2025-12-20 |
| E061 | Keeper | PERSON | SOFTWARE_COMPONENT | In blockchain context, Keeper is a module component | 2025-12-20 |
| E062 | Node Operator | PERSON | ROLE | This is a role, not a specific person | 2025-12-20 |
| E063 | validators | PERSON | GROUP/ROLE | Generic "validators" is a group/role | 2025-12-20 |
| E064 | Reviewer | PERSON | ROLE | Generic reviewer is a role template | 2025-12-20 |
| E065 | Admin | PERSON | ROLE | Generic admin is a role template | 2025-12-20 |
| E066 | Local Communities | PERSON | GROUP | Communities are groups, not individuals | 2025-12-20 |
| E067 | Indigenous Peoples | PERSON | GROUP | This is a demographic group, not a person | 2025-12-20 |
| E068 | land stewards | PERSON | ROLE/GROUP | This is a role/group, not an individual | 2025-12-20 |
| E069 | Researchers | PERSON | GROUP/ROLE | Generic researchers is a group | 2025-12-20 |
| E070 | Project Developer | PERSON | ROLE | This is a role, not a person | 2025-12-20 |
| E071 | System Administrator | PERSON | ROLE | This is a role, not a person | 2025-12-20 |
| E072 | Coca-Cola | HumanActor | ORGANIZATION | Coca-Cola is a corporation | 2025-12-20 |
| E073 | Colgate-Palmolive | HumanActor | ORGANIZATION | Colgate-Palmolive is a corporation | 2025-12-20 |
| E074 | Danone | HumanActor | ORGANIZATION | Danone is a corporation | 2025-12-20 |
| E075 | Nestlé | HumanActor | ORGANIZATION | Nestlé is a corporation | 2025-12-20 |
| E076 | Microsoft | HumanActor | ORGANIZATION | Microsoft is a corporation | 2025-12-20 |
| E077 | General Mills | HumanActor | ORGANIZATION | General Mills is a corporation | 2025-12-20 |
| E078 | BlockScience | HumanActor | ORGANIZATION | BlockScience is a research organization | 2025-12-20 |
| E079 | Gold Standard | HumanActor | ORGANIZATION | Gold Standard is a certification organization | 2025-12-20 |
| E080 | American Carbon Registry | HumanActor | ORGANIZATION | This is a carbon registry organization | 2025-12-20 |
| E081 | DAO | HumanActor | ORG_TYPE/CONCEPT | "DAO" is an organization type concept, not an actor | 2025-12-20 |
| E082 | DAODAO | HumanActor | ORGANIZATION | DAODAO is a DAO platform/organization | 2025-12-20 |
| E083 | Celo | HumanActor | BLOCKCHAIN | Celo is a blockchain, not a human actor | 2025-12-20 |
| E084 | Cosmos | HumanActor | BLOCKCHAIN/ECOSYSTEM | Cosmos is a blockchain ecosystem | 2025-12-20 |
| E085 | Discord | HumanActor | PLATFORM | Discord is a communication platform | 2025-12-20 |
| E086 | Twitter | HumanActor | PLATFORM | Twitter is a social media platform | 2025-12-20 |
| E087 | Telegram | HumanActor | PLATFORM | Telegram is a messaging platform | 2025-12-20 |
| E088 | Colombia | HumanActor | COUNTRY | Colombia is a country, not a human actor | 2025-12-20 |
| E089 | proof of stake | HumanActor | CONCEPT | Proof of stake is a consensus mechanism concept | 2025-12-20 |
| E090 | permaculture | HumanActor | CONCEPT | Permaculture is an agricultural/design concept | 2025-12-20 |
| E091 | $REGEN | HumanActor | TOKEN | $REGEN is a cryptocurrency token | 2025-12-20 |
| E092 | $regen | HumanActor | TOKEN | Token variant - duplicate of E091 | 2025-12-20 |
| E093 | REGEN token | HumanActor | TOKEN | Token variant - duplicate of E091 | 2025-12-20 |
| E094 | John Doe | PERSON | PLACEHOLDER/ARTIFACT | Placeholder name, not a real person | 2025-12-20 |
| E095 | Jane Doe | PERSON | PLACEHOLDER/ARTIFACT | Placeholder name, not a real person | 2025-12-20 |
| E096 | yourusername | PERSON | PLACEHOLDER/ARTIFACT | Template placeholder, not a real person | 2025-12-20 |
| E097 | postgres | PERSON | SOFTWARE | PostgreSQL database, not a person | 2025-12-20 |
| E098 | logger | PERSON | SOFTWARE_COMPONENT | Software logging component, not a person | 2025-12-20 |
| E099 | regen-network/ledger-core-dev | PERSON | GITHUB_REPO | This is a GitHub repository path | 2025-12-20 |
| E100 | regenie-corpus | PERSON | GITHUB_REPO | This is a repository name | 2025-12-20 |
| E101 | 355,000 households | PERSON | QUANTITY/ARTIFACT | This is a numeric quantity, not a person | 2025-12-20 |
| E102 | Governance Proposal #123 | PERSON | PROPOSAL | This is a governance proposal, not a person | 2025-12-20 |
| E103 | regen1k82wewrfkhdmegw6uxrgwwzrsd7593t8tej2d5 | PERSON | BLOCKCHAIN_ADDRESS | This is a Regen blockchain address | 2025-12-20 |
| E104 | RND Inc. | PERSON | ORGANIZATION | Regen Network Development Inc. is a company | 2025-12-20 |
| E105 | RND, PBC | PERSON | ORGANIZATION | Regen Network Development PBC is a company | 2025-12-20 |
| E106 | Terra Genesis International, LLC | PERSON | ORGANIZATION | This is a company | 2025-12-20 |
| E107 | Unmanned Aerial Vehicles (Drones) | PERSON | TECHNOLOGY | Drones are technology, not people | 2025-12-20 |
| E108 | Regenald | PERSON | AI_SYSTEM | Regenald is a bot/AI mascot | 2025-12-20 |
| E109 | GraphClient | PERSON | SOFTWARE | GraphClient is a software library | 2025-12-20 |
| E110 | MCP tools | PERSON | SOFTWARE | MCP tools are software components | 2025-12-20 |
| E111 | MCP | PERSON | SOFTWARE/PROTOCOL | Model Context Protocol is software | 2025-12-20 |
| E112 | Contributor Covenant | PERSON | DOCUMENT | Contributor Covenant is a code of conduct document | 2025-12-20 |
| E113 | KOI E | PERSON | ARTIFACT | Unclear extraction artifact | 2025-12-20 |
| E114 | I-A | PERSON | ARTIFACT | Single/double letter extraction artifact | 2025-12-20 |
| E115 | j | PERSON | ARTIFACT | Single letter - likely extraction error | 2025-12-20 |
| E116 | d | PERSON | ARTIFACT | Single letter - likely extraction error | 2025-12-20 |
| E117 | e | PERSON | ARTIFACT | Single letter - likely extraction error | 2025-12-20 |
| E118 | Maya Angelou | PERSON | PERSON (but likely irrelevant) | Famous poet - likely from quotes, not Regen ecosystem member | 2025-12-20 |
| E119 | Mary Oliver | PERSON | PERSON (but likely irrelevant) | Famous poet - likely from quotes, not Regen ecosystem member | 2025-12-20 |
| E120 | E.O. Wilson | PERSON | PERSON (but likely irrelevant) | Famous biologist - likely from quotes/references, not active participant | 2025-12-20 |
| E121 | Elinor Ostrom | PERSON | PERSON (but likely irrelevant) | Famous economist (deceased) - cited in discussions, not active participant | 2025-12-20 |

#### EVIDENCE Type Errors (Session 8)

| ID | Entity | Current Type | Should Be | Reasoning | Date Found |
|----|--------|--------------|-----------|-----------|------------|
| E278 | Test Results (19), Performance Metrics (13), Version Check (11) | EVIDENCE | ARTIFACT/REMOVE | Documentation headers/sections, not actual evidence | 2025-12-20 |
| E279 | Props Interface (9), Implementation Details (8), API Endpoints (7) | EVIDENCE | ARTIFACT/REMOVE | Code documentation sections incorrectly extracted as evidence | 2025-12-20 |
| E280 | Docker Commands (5), Installation Commands (4), Test Script (4) | EVIDENCE | ARTIFACT/REMOVE | Command documentation extracted as evidence | 2025-12-20 |
| E281 | Database Schema (3), API Response Schema (3), Code Snippet (3) | EVIDENCE | ARTIFACT/REMOVE | Technical documentation sections, not evidence | 2025-12-20 |

#### QUESTION Type Errors (Session 8)

| ID | Entity | Current Type | Should Be | Reasoning | Date Found |
|----|--------|--------------|-----------|-----------|------------|
| E282 | FAQ (5), Common Questions (4) | QUESTION | CATEGORY/REMOVE | Section headers, not actual questions | 2025-12-20 |
| E283 | userQuery (3) | QUESTION | CODE/REMOVE | Variable name from code, not a question | 2025-12-20 |
| E284 | "Show me the call graph for MsgRetire" (2), "Find orphaned code..." (2) | QUESTION | COMMAND | Commands with no question marks - imperative form | 2025-12-20 |
| E285 | Category differentiation (3), Onboarding Journey (3), Auth Approach (2) | QUESTION | CONCEPT/PROCESS | Topics/processes incorrectly typed as questions | 2025-12-20 |
| E286 | MsgRetire Sending (2), Priority order (2), Tool Naming (2) | QUESTION | TOPIC/REMOVE | Discussion topics, not questions | 2025-12-20 |

#### CONCEPT Type Errors (Session 8)

| ID | Entity | Current Type | Should Be | Reasoning | Date Found |
|----|--------|--------------|-----------|-----------|------------|
| E287 | Apache License, Version 2.0 | CONCEPT | LICENSE | Software licenses should have own type | 2025-12-20 |
| E288 | Regen Conditional Use License (4) | CONCEPT | LICENSE | Regen's license should be LICENSE type | 2025-12-20 |

#### TECHNOLOGY Type Errors (Session 8)

| ID | Entity | Current Type | Should Be | Reasoning | Date Found |
|----|--------|--------------|-----------|-----------|------------|
| E289 | Regen Ledger (1819), Regen Marketplace (398) | TECHNOLOGY | BLOCKCHAIN/PLATFORM | Only 2 entities typed as TECHNOLOGY in entire PostgreSQL. Regen Ledger is blockchain, Marketplace is platform. | 2025-12-20 |
| E290 | PostgreSQL (48), TypeScript (10), GraphQL (10), docker (6), React (4), python (2) | ENTITY | TECHNOLOGY | Technologies typed as generic ENTITY instead of TECHNOLOGY | 2025-12-20 |

#### Generic ENTITY Overuse - Additional Findings (Session 8)

| ID | Entity | Current Type | Should Be | Reasoning | Date Found |
|----|--------|--------------|-----------|-----------|------------|
| E291 | Success metrics (1792) | ENTITY | REMOVE | Generic phrase extracted repeatedly - extraction artifact | 2025-12-20 |
| E292 | rnd_rf_prioritize_purpose (377), regen.admin (272) | ENTITY | REMOVE | Snake_case code artifacts - variable/function names | 2025-12-20 |
| E293 | Planetary Regeneration Podcast (160) | ENTITY | MEDIA/PODCAST | Podcast should be typed as MEDIA | 2025-12-20 |
| E294 | regenerative finance (169), carbon sequestration (37), blockchain (39) | ENTITY | CONCEPT | Concepts typed as generic ENTITY | 2025-12-20 |

#### Code Artifact Entities (Session 8)

| ID | Entity | Current Type | Should Be | Reasoning | Date Found |
|----|--------|--------------|-----------|-----------|------------|
| E295 | professional_validator_status (17), governance_commitment (11), semantic_versioning_policy (10) | ENTITY | REMOVE | Snake_case config/variable names from code extraction | 2025-12-20 |
| E296 | council_structure_proposal (9), docs_as_source_of_truth (9), cosmos_ecosystem_overview | ENTITY | REMOVE | More snake_case code artifacts | 2025-12-20 |

#### Placeholder/Anonymous Entities (Session 8)

| ID | Entity | Current Type | Should Be | Reasoning | Date Found |
|----|--------|--------------|-----------|-----------|------------|
| E297 | unknown author (50), Unnamed community member (37), Unnamed commenter (18) | ENTITY | REMOVE | Placeholder entities representing unknown authors | 2025-12-20 |
| E298 | Unnamed liquidity provider (10), Unnamed proposer (6), Unknown forum participant (9) | ENTITY | REMOVE | More placeholder entities that shouldn't be nodes | 2025-12-20 |

#### Role-based Generic Entities (Session 8)

| ID | Entity | Current Type | Should Be | Reasoning | Date Found |
|----|--------|--------------|-----------|-----------|------------|
| E299 | proposal author (68), project proponent (67), Original poster (64) | ENTITY | ROLE/REMOVE | Generic role references extracted as entities | 2025-12-20 |
| E300 | contributors (48), approvers (36), researchers (34), community member (34) | ENTITY | ROLE/REMOVE | Generic role terms that shouldn't be standalone entities | 2025-12-20 |
| E301 | validator (35), verifier (32), relayers (22), buyers (22), delegators (19) | ENTITY | ROLE/REMOVE | More generic roles extracted as entities | 2025-12-20 |

#### Git Commits as Claims (Session 8)

**CONSOLIDATED:** This issue is covered by E261-E263 (specific examples) and E275 (systematic pattern analysis showing 556+ CLAIMs in PostgreSQL matching git commit patterns). The original E302-E304 have been removed as duplicates.

#### Duplicate Entities (Need Merging)

| ID | Duplicate Entities | Likely Canonical Entity | Confidence | Date Found |
|----|-------------------|------------------------|------------|------------|
| E015 | Gregory, Gregory Landua, Gregory Regen, Gregory_RND, Gregory0, Greg Rielandwe | Gregory Landua (CEO of Regen Network) | High | 2025-12-20 |
| E016 | Christian, Christian Shearer, Christian_Regen, Regen Christian | Christian Shearer | High | 2025-12-20 |
| E017 | Regen Registry, Regen Registry Program | Regen Registry | High | 2025-12-20 |
| E018 | Regen Network community, Regen Network Community (person 2) | Regen Network Community | High | 2025-12-20 |
| E041 | Biocultural Credit Pilot, Biocultural crediting pilot, Jaguar Biocultural Credit Pilot, Biocultural Creding pilot, Biocultural Crediting Pilot | Jaguar Biocultural Credit Pilot (or Biocultural Credit Pilot) | High | 2025-12-20 |
| E042 | Regen (merged entity) | Regen Network (Org), $Regen (Token) | High (needs splitting) | 2025-12-20 |
| E122 | Will (112), Will Szal (56), @willszal (1), willszal (1), willzal (1), Will from Regen Foundation (2) | Will Szal | High | 2025-12-20 |
| E123 | Sarah (16), Sarah B (1), Sarah Bax (166) | Sarah Bax | High | 2025-12-20 |
| E124 | Aaron C (6), Aaron_C (2), Aaron Craelius (75), A. Craelius (2) | Aaron Craelius | High | 2025-12-20 |
| E125 | Becca (16), Becca Harman (46), beccahRND (3), Rebecca (6), Rebecca Harmon (2), rebecca_regen (3) | Becca Harman | High | 2025-12-20 |
| E126 | Max (33), Max Semenchuk (64), Max_Semenchuk (5) | Max Semenchuk | High | 2025-12-20 |
| E127 | Ned (8), Ned Horning (64) | Ned Horning | High | 2025-12-20 |
| E128 | Dave Fortson (94), David Forson (1), @David Fortson (3), dave_fortson-LOA_Labs_Validator (2) | Dave Fortson | High | 2025-12-20 |
| E129 | Jeancarlo (36), Jeancarlo Barrios (48) | Jeancarlo Barrios | High | 2025-12-20 |
| E130 | Ryan Christoffersen (66), ryanchristo-Chora_Validator (34) | Ryan Christoffersen | High | 2025-12-20 |
| E131 | Darren (15), darrenzal (16), Darren Zal (50) | Darren Zal | High | 2025-12-20 |
| E132 | Josh (18), Josh Fairhead (40), Ethan Josh_Fairhead (4) | Josh Fairhead | High | 2025-12-20 |
| E133 | Gisel (14), Gisel Booman (63), Giselle (14), G. Booman (2) | Gisel Booman | High | 2025-12-20 |
| E134 | Sam (15), Sam Bennetts (33) | Sam Bennetts | High | 2025-12-20 |
| E135 | Robert (26), Robert Zaremba (25), Roberto (9) | Robert Zaremba (if same person) | Medium | 2025-12-20 |
| E136 | Paul Weidner (39), paul121 (9), @paul121 (1) | Paul Weidner | High | 2025-12-20 |
| E137 | Austin (8), Austin Smith (4), Austin Wade Smith (20) | Austin Wade Smith | High | 2025-12-20 |
| E138 | Tica (4), Tica Lubin (20) | Tica Lubin | High | 2025-12-20 |
| E139 | corlock (14), corlock_RND (10) | corlock (forum handle) | High | 2025-12-20 |
| E140 | $REGEN (HumanActor), $Regen (HumanActor), $regen (HumanActor), REGEN token (HumanActor), $REGEN token (PERSON) | $REGEN (TOKEN type) | High | 2025-12-20 |
| E141 | DAO (HumanActor), DAO DAO (HumanActor), DAODAO (HumanActor), DAO_DAO (HumanActor), DAODAO team (PERSON) | DAODAO (ORGANIZATION) | High | 2025-12-20 |
| E142 | Michael Zargham (15), Zargham (19) | Michael Zargham | High | 2025-12-20 |
| E143 | Arturo (38), Agustín (10) - if same person | Arturo (needs verification) | Low | 2025-12-20 |
| E305 | Cory (9), Cory Levinson (6), clevinson (2) | Cory Levinson | High | 2025-12-20 |
| E306 | Akik Takat (11), Akit Takat (5) | Akik Takat (typo in Akit) | High | 2025-12-20 |
| E307 | Alex (Bambarello) (2), Alex Bambarello (1) | Alex Bambarello | High | 2025-12-20 |
| E308 | Alexander Kondakov (4), Alexander_Kondakov-P2P_Validator (2) | Alexander Kondakov | High | 2025-12-20 |
| E309 | Botao (2), Botao Amber Hu (6), Amber Hu (2) | Botao Amber Hu | Medium | 2025-12-20 |
| E310 | B. Weinberg (1), Brian Weinberg (19) | Brian Weinberg | High | 2025-12-20 |
| E311 | RND Inc. (PERSON), RND Inc (ORG-41), RND inc (ORG-22), RND PBC (ORG-67), RNDPBC (ORG-4) | RND Inc and RND PBC (separate legal entities) | High | 2025-12-20 |
| E312 | LOA Labs (60), loalabs (7), alphaBiota-LOA Labs (18), LOACOM (44) | LOA Labs | High | 2025-12-20 |
| E313 | Block Science Team (PERSON), BlockScience (ORG-148) | BlockScience | High | 2025-12-20 |
| E314 | Terrasos (ORG-55+ENTITY-32), Terrasos team (PERSON) | Terrasos | High | 2025-12-20 |
| E315 | Viridios Validator (ENTITY), Viridios_Validator (PERSON+ENTITY) | Viridios Validator | High | 2025-12-20 |
| E316 | Jae Kwon (PERSON+ENTITY), Jay Kwon (ENTITY-typo) | Jae Kwon | High | 2025-12-20 |
| E317 | Cosmos ecosystem (ORG+ENTITY), Cosmos Ecosystem (PROJECT) | Cosmos Ecosystem | High | 2025-12-20 |
| E318 | Toucan Protocol (ORG-34), Toucan team (ORG+ENTITY) | Toucan Protocol | High | 2025-12-20 |
| E319 | DeSci Foundation (ORG-10+ENTITY-6) | DeSci Foundation | High | 2025-12-20 |
| E320 | Klima Foundation (ORG-10+ENTITY-18) | Klima Foundation | High | 2025-12-20 |
| E321 | Open Earth Foundation (ORG-11+ENTITY-18) | Open Earth Foundation | High | 2025-12-20 |
| E322 | Amanda (1), Amanda Joy Ravenhill (PERSON-10+ENTITY-50) | Amanda Joy Ravenhill | High | 2025-12-20 |
| E323 | Gregory RND variants, gregory@regen.network, @gregory_landua | Gregory Landua (additional to E030) | High | 2025-12-20 |
| E324 | ryanchristo-Chora Validator (ORG+ENTITY), Ryan Christofferson (typo) | Ryan Christoffersen | High | 2025-12-20 |
| E325 | Will-Regen Foundation (ORG-63) | Incorrectly merged Will Szal with Regen Foundation | High | 2025-12-20 |
| E326 | Osmosis (ORG-119), Osmosis Community (PERSON+ENTITY) | Osmosis | High | 2025-12-20 |

#### Duplicate/Malformed Relationships

| ID | Relationship 1 | Relationship 2 | Issue | Date Found |
|----|---------------|----------------|-------|------------|
| E019 | Rhamis Kent —GUEST_ON→ Planetary Regeneration Podcast | Rhamis Kent —GUESTON→ Planetary Regeneration Podcast | Duplicate with different relation name formatting (underscore vs no underscore) | 2025-12-20 |

#### Predicate Duplication Issues (NEW CATEGORY)

Discovered via SPARQL query: `SELECT DISTINCT ?p (COUNT(*) as ?count) WHERE { ?s ?p ?o } GROUP BY ?p`

| ID | Predicate Group | Variations | Total Count | Issue | Date Found |
|----|-----------------|------------|-------------|-------|------------|
| E147 | guest-related | guestOn (3), guestsOn (2), isGuestOn (1), guest_on (1), featuredGuest (1) | 8 | 5 variations of the same relationship | 2025-12-20 |
| E148 | supports-related | supports (6164), supportedBy (1024), is_supported_by (212), support (89), ontology#supports (66), supported_by (57), ontology#supportedBy (25) | 7637 | 7 variations, namespace + format mixing | 2025-12-20 |
| E149 | relates-related | relatesTo (181), is_related_to (146), isRelatedTo (95), relatedTo (47), related_to (29), relates_to (8) | 506 | 6 variations, camelCase + snake_case mixing | 2025-12-20 |
| E150 | participates-related | participatesIn (154), participateIn (48), participates_in (33), participate_in (28), participated_in (24) | 287 | 5 variations including tense mixing | 2025-12-20 |
| E151 | integrates-related | integrates (140), integratesWith (47), integrates_with (25) | 212 | 3 variations | 2025-12-20 |
| E152 | uses-related | uses (848), use (73), ontology#uses (38) | 959 | 3 variations, namespace mixing | 2025-12-20 |
| E153 | develops-related | develops (635), developed (50), ontology#develops (45) | 730 | 3 variations, tense + namespace mixing | 2025-12-20 |
| E154 | provides-related | provides (622), providesEvidenceFor (77), provides_evidence_for (30), providesAccessTo (28), provide (22) | 779 | 5 variations | 2025-12-20 |
| E155 | enables-related | enables (579), enable (36) | 615 | 2 variations, singular/plural | 2025-12-20 |
| E156 | creates-related | creates (164), created (23) | 187 | 2 variations, tense mixing | 2025-12-20 |
| E157 | hosts-related | hosts (167), ontology#hosts (23), hostedBy (6+2) | 198 | namespace mixing | 2025-12-20 |
| E158 | confidence namespace | http://regen.network/koi#confidence (26055), https://regen.network/koi#confidence (1715) | 27770 | HTTP vs HTTPS protocol inconsistency | 2025-12-20 |
| E159 | coauthored-related | coauthored (14), coAuthored (5) | 19 | camelCase inconsistency | 2025-12-20 |
| E160 | cofounded-related | coFounded (12), cofounded (10), ontology#coFounded (1) | 23 | camelCase + namespace inconsistency | 2025-12-20 |
| E161 | shutdown-related | shutDown (1), shutdown (1) | 2 | camelCase inconsistency | 2025-12-20 |
| E162 | worksFor-related | worksFor (123), ontology#worksFor (25), works_for (1+1) | 150 | namespace + format mixing | 2025-12-20 |
| E163 | memberOf-related | memberOf (194), ontology#memberOf (6) | 200 | namespace mixing | 2025-12-20 |
| E164 | focusesOn-related | focusesOn (187), focuses_on (9+5), ontology#focusesOn (14) | 215 | format + namespace mixing | 2025-12-20 |

#### Predicate Tense Inconsistencies (NEW CATEGORY)

| ID | Present Tense | Past Tense | Counts | Issue | Date Found |
|----|---------------|------------|--------|-------|------------|
| E165 | authors | authored | 28 / 84 | Same relationship in different tenses | 2025-12-20 |
| E166 | develops | developed | 45 / 50 | Same relationship in different tenses | 2025-12-20 |
| E167 | creates | created | 164 / 23 | Same relationship in different tenses | 2025-12-20 |
| E168 | performs | performed | 159 / 10 | Same relationship in different tenses | 2025-12-20 |
| E169 | reviews | reviewed | 48 / 8 | Same relationship in different tenses | 2025-12-20 |
| E170 | validates | validated | 79 / 8 | Same relationship in different tenses | 2025-12-20 |
| E171 | answers | answered | 120 / 6 | Same relationship in different tenses | 2025-12-20 |
| E172 | receives | received | 52 / 6 | Same relationship in different tenses | 2025-12-20 |
| E173 | announces | announced | 28 / 4 | Same relationship in different tenses | 2025-12-20 |
| E174 | connects | connected | 27 / 3 | Same relationship in different tenses | 2025-12-20 |

#### UPPER_CASE Predicate Issues (NEW CATEGORY)

| ID | Predicate | Count | Issue | Date Found |
|----|-----------|-------|-------|------------|
| E175 | MENTIONS | 6 | UPPER_CASE when most predicates use camelCase | 2025-12-20 |
| E176 | HANDLES | 4 | UPPER_CASE when most predicates use camelCase | 2025-12-20 |
| E177 | CONTAINS | 3 | UPPER_CASE when most predicates use camelCase | 2025-12-20 |
| E178 | DOCUMENTED_IN | 3 | UPPER_CASE when most predicates use camelCase | 2025-12-20 |
| E179 | AFFILIATED_WITH | 1 | UPPER_CASE when most predicates use camelCase | 2025-12-20 |
| E180 | HANDLED_BY | 1 | UPPER_CASE when most predicates use camelCase | 2025-12-20 |
| E181 | HAS_METHOD | 1 | UPPER_CASE when most predicates use camelCase | 2025-12-20 |
| E182 | FETCHED / FETCHE | 1 / 1 | UPPER_CASE + typo (FETCHE missing D) | 2025-12-20 |

#### Nonsensical Relationships

| ID | Relationship | Issue | Date Found |
|----|-------------|-------|------------|
| E020 | Regen Commons —OPERATES→ forum user | Generic "forum user" shouldn't have relationships - this is a template/role | 2025-12-20 |
| E021 | forum user —LICENSESCONTENTTO→ Regen Commons | Same issue - relationships with generic template entities | 2025-12-20 |
| E022 | forum user —INDEMNIFIES→ Regen Commons | Same issue - relationships with generic template entities | 2025-12-20 |
| E183 | forum user —owns→ content_ownership | Template entity relationship from Terms of Service | 2025-12-20 |
| E184 | forum user —mustComplyWith→ acceptable_use_restrictions | Template entity relationship from Terms of Service | 2025-12-20 |
| E185 | forum user —mustComplyWith→ content_standards_restrictions | Template entity relationship from Terms of Service | 2025-12-20 |
| E186 | reviewers —apply→ labels required for issues | Template entity relationship from guidelines | 2025-12-20 |
| E187 | reviewers —applyGuidelinesFrom→ reviewer_checklist_required | Template entity relationship from guidelines | 2025-12-20 |
| E188 | researchers —receivesFeedbackFrom→ reviewers | Relationship between two generic role entities | 2025-12-20 |
| E189 | TMO (Organization) —worksFor→ BASIN Natural Capital | Organizations don't "work for" - should be partnersWith or contractsWith | 2025-12-20 |
| E190 | Claim: Regen Ledger —develops→ Ecosystem Service Credits Management | Claims can't develop things - Regen Ledger is SOFTWARE, not CLAIM | 2025-12-20 |
| E191 | Claim: Regen Ledger —manages→ credit class | Same issue - incorrect entity type leads to nonsensical relationship | 2025-12-20 |
| E192 | Claim: Regen Ledger —hosts→ Jaguar Stewardship Credits | Same issue - incorrect entity type leads to nonsensical relationship | 2025-12-20 |
| E193 | Claim: Regen Network —develops→ Regen Ledger | Regen Network is ORGANIZATION not CLAIM - mistyping causes nonsensical relationship | 2025-12-20 |

#### Self-Referential Relationship Issues (NEW CATEGORY)

| ID | Relationship | Issue | Date Found |
|----|-------------|-------|------------|
| E194 | Claim —supports→ Claim | Type-level self-reference - meaningless relationship | 2025-12-20 |
| E195 | Claim —is_related_to→ Claim | Type-level self-reference - meaningless relationship | 2025-12-20 |
| E196 | Spotify —isOwnedBy→ Spotify | Self-ownership - extraction error | 2025-12-20 |
| E197 | $REGEN Coin —hasCategory→ $REGEN Coin | Self-categorization - meaningless | 2025-12-20 |
| E198 | REGEN Coin —hasCategory→ REGEN Coin | Self-categorization - meaningless | 2025-12-20 |
| E199 | MsgSendResponse —no_changes→ MsgSendResponse | Self-reference with odd predicate | 2025-12-20 |
| E200 | MsgRetireResponse —no_changes→ MsgRetireResponse | Self-reference with odd predicate | 2025-12-20 |
| E201 | QueryParamsRequest —isSameAs→ QueryParamsRequest | Self-reference - trivially true | 2025-12-20 |
| E202 | Open biodiversity data —supports→ Open biodiversity data | Self-support - meaningless | 2025-12-20 |
| E203 | Keeper —has_name→ Keeper | Self-naming - extraction artifact | 2025-12-20 |
| E204 | indexer —isInstanceOf→ indexer | Self-instantiation - meaningless | 2025-12-20 |

#### Data Store Relationship Issues (NEW CATEGORY)

| ID | Issue | Details | Date Found |
|----|-------|---------|------------|
| E205 | PostgreSQL relationships table empty | 0 rows in PostgreSQL `relationships` table, while Fuseki has ~162,754 triples including thousands of relationships. All relationship data only in Fuseki. | 2025-12-20 |
| E206 | No relationship schema validation | Predicates are free-form strings with no controlled vocabulary - leads to 200+ unique predicate variations | 2025-12-20 |

#### Schema/Ontology Issues

| ID | Issue | Details | Date Found |
|----|-------|---------|------------|
| E023 | HUMANACTOR vs PERSON type distinction unclear | Why do both types exist? What's the semantic difference? | 2025-12-20 |
| E024 | Not building on Regen Network's official schemas | Regen uses LinkML schemas at https://framework.regen.network/schema/ - we should use these as a BASE and EXTEND them to include additional types we need (claims, people, organizations, concepts, etc.) rather than creating parallel/duplicate type systems | 2025-12-20 |
| E025 | Type case inconsistency | Same types exist with different cases: `koi#Claim` (19,698) vs `koi#CLAIM` (9,156), `koi#Person` (704) vs `koi#PERSON` (1,902), `koi#Project` (1,582) vs `koi#PROJECT` (2,047), etc. Should be normalized to one case. | 2025-12-20 |
| E026 | Multiple namespace prefixes | Types exist under both `http://regen.network/koi#` and `https://regen.network/ontology#` (e.g., `koi#Claim` vs `ontology#Claim`). Should use a single consistent namespace. | 2025-12-20 |
| E027 | Entities have multiple conflicting types | Same entity (e.g., Gregory_Landua) has multiple type assertions: HumanActor, Person, AND PERSON. Should have single canonical type. | 2025-12-20 |
| E028 | HumanActor type exists in Fuseki but not PostgreSQL | Fuseki has 3,867 HumanActor entities, but PostgreSQL entity_registry has no HUMANACTOR type - data inconsistency between stores | 2025-12-20 |
| E029 | ENTITY as fallback type overused | 15,558 entities typed as generic "ENTITY" in PostgreSQL - should be more specific types | 2025-12-20 |
| E251 | Multiple Claim classes and casing in Fuseki | Claim is split across `http://regen.network/koi#Claim` (19,698), `http://regen.network/koi#CLAIM` (9,156), and `https://regen.network/ontology#Claim` (689), creating parallel schemas and inconsistent typing.<br>Query: `SELECT ?type (COUNT(?s) as ?count) WHERE { ?s a ?type } GROUP BY ?type ORDER BY DESC(?count) LIMIT 40` | 2025-12-20 |
| E252 | Unlabeled Claim resources (ontology namespace) | 689 resources typed as `https://regen.network/ontology#Claim` have no `rdfs:label`, making them unusable in label-based search/UX and breaking parity with `koi`-namespace entities.<br>Query: `PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> SELECT (COUNT(?s) as ?count) WHERE { ?s a ?type . FILTER(CONTAINS(LCASE(STR(?type)), 'claim')) FILTER NOT EXISTS { ?s rdfs:label ?l } }` | 2025-12-20 |
| E253 | Duplicate Claim type assertions (Claim + CLAIM) | 166 entities in Fuseki are simultaneously typed as both `koi#Claim` and `koi#CLAIM`, indicating type-normalization failure and inflating type-based counts.<br>Query: `SELECT (COUNT(DISTINCT ?s) as ?count) WHERE { ?s a <http://regen.network/koi#Claim> ; a <http://regen.network/koi#CLAIM> }` | 2025-12-20 |

#### Data Store Inconsistencies

| ID | Issue | Details | Date Found |
|----|-------|---------|------------|
| E254 | PostgreSQL `fuseki_uri` values do not exist in Fuseki | For CLAIM entities, PostgreSQL stores IRIs like `https://regen.network/claim/<id>`, but Fuseki contains **0** resources with that subject prefix, so registry-to-graph linking is broken (no `owl:sameAs` bridge found). Example: `Add marketplace Msg/UpdateSellOrders` is `https://regen.network/claim/52a7b9992a72f7bd` in PostgreSQL but `http://regen.network/koi/entity/Add_marketplace_Msg_UpdateSellOrders` in Fuseki.<br>Queries: `SELECT fuseki_uri FROM entity_registry WHERE entity_type='CLAIM' AND entity_text='Add marketplace Msg/UpdateSellOrders';` and `SELECT (COUNT(?s) as ?count) WHERE { ?s ?p ?o . FILTER(STRSTARTS(STR(?s), 'https://regen.network/claim/')) }` | 2025-12-20 |
| E255 | CLAIM population mismatch (PostgreSQL vs Fuseki) | PostgreSQL has 7,828 CLAIM rows, while Fuseki has 29,377 distinct subjects with a `*Claim*` rdf:type, indicating inconsistent loading/registry coverage and/or divergent type/URI strategies.<br>Queries: `SELECT COUNT(*) FROM entity_registry WHERE entity_type='CLAIM';` and `SELECT (COUNT(DISTINCT ?s) as ?count) WHERE { ?s a ?type . FILTER(CONTAINS(LCASE(STR(?type)), 'claim')) }` | 2025-12-20 |

#### Systematic Extraction Artifacts (CLAIM)

| ID | Pattern | Evidence | Example Entities | Date Found |
|----|---------|----------|------------------|------------|
| E275 | Changelog/commit-style action phrases extracted as CLAIM | PostgreSQL: 556 CLAIMs match `^(add|update|fix|refactor|rename|remove|integrate|support|configure|configured|create|patch|migration) `; Fuseki: 873 claim-typed labels match the same pattern.<br>Queries: `SELECT COUNT(*) FROM entity_registry WHERE entity_type='CLAIM' AND entity_text ~* '^(add|update|fix|refactor|rename|remove|integrate|support|configure|configured|create|patch|migration) ';` and `PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> SELECT (COUNT(?s) as ?count) WHERE { ?s a ?type ; rdfs:label ?label . FILTER(CONTAINS(LCASE(STR(?type)), 'claim')) FILTER(REGEX(STR(?label), '^(Add|Update|Fix|Refactor|Rename|Remove|Integrate|Support|Configured|Create|Patch|Migration) ', 'i')) }` | Add marketplace Msg/UpdateSellOrders; Update protobuf package version to v1; Add Msg/UpdateBatchMetadata | 2025-12-20 |
| E276 | Protobuf/API identifiers extracted as CLAIM | PostgreSQL: 112 CLAIMs match message/query/event patterns (`Msg%`, `Msg/`, `Query/`); Fuseki: 455 claim-typed labels match `(^Msg|Msg/|Query/|Event)`.<br>Queries: `SELECT COUNT(*) FROM entity_registry WHERE entity_type='CLAIM' AND (entity_text ILIKE '%msg/%' OR entity_text ILIKE '%query/%' OR entity_text ILIKE 'Msg%');` and `PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> SELECT (COUNT(?s) as ?count) WHERE { ?s a ?type ; rdfs:label ?label . FILTER(CONTAINS(LCASE(STR(?type)), 'claim')) FILTER(REGEX(STR(?label), '(^Msg|Msg/|Query/|Event)', 'i')) }` | MsgCreateBatch; EventBuyDirect; Add Query/Balances | 2025-12-20 |
| E277 | Documentation headings (Process/Guideline) extracted as CLAIM | PostgreSQL: 197 CLAIMs contain “process” and 26 contain “guideline”; Fuseki: 422 claim-typed labels contain “process” and 81 contain “guideline”.<br>Queries: `SELECT COUNT(*) FROM entity_registry WHERE entity_type='CLAIM' AND entity_text ILIKE '%process%';` / `... ILIKE '%guideline%';` and `PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> SELECT (COUNT(?s) as ?count) WHERE { ?s a ?type ; rdfs:label ?label . FILTER(CONTAINS(LCASE(STR(?type)), 'claim')) FILTER(CONTAINS(LCASE(STR(?label)), 'process')) }` / `... 'guideline'` | Patch Release Process; Documentation Guidelines; Pull Request Guidelines | 2025-12-20 |
| E043 | Fuseki Namespace Fragmentation | `koi#Project` (1582), `koi#PROJECT` (2047), `ontology#Project` (29), `ontology#PROJECT` (42). Multiple namespaces and cases for same type. | 2025-12-20 |
| E144 | Person/HumanActor Type Fragmentation | 7 different URIs for person-related types in Fuseki: `koi#HumanActor` (3867), `koi#PERSON` (1902), `koi#Person` (704), `ontology#HumanActor` (218), `ontology#PERSON` (27), `koi#HUMANACTOR` (7), `ontology#Person` (1). Should be normalized to single canonical type. | 2025-12-20 |
| E145 | PostgreSQL/Fuseki PERSON count mismatch | PostgreSQL has 1,460 PERSON entities. Fuseki has 1902 PERSON + 704 Person = 2,606 person-typed entities (not counting HumanActor). Data inconsistency between stores. | 2025-12-20 |
| E146 | HumanActor type semantic confusion | HumanActor (3,867 in Fuseki) contains organizations (Coca-Cola, Microsoft), blockchains (Celo, Cosmos), platforms (Discord, Twitter), concepts (proof of stake, permaculture), tokens ($REGEN), and countries (Colombia) - not just human actors. Type is being used as catch-all. | 2025-12-20 |
| E207 | Organization/ORGANIZATION Type Fragmentation | Fuseki has both `koi#Organization` (1,331) and `koi#ORGANIZATION` (1,387). Entities have BOTH types. PostgreSQL has 1,106 ORGANIZATION. | 2025-12-20 |
| E208 | PostgreSQL/Fuseki ORGANIZATION count mismatch | PostgreSQL: 1,106. Fuseki: 1,554 unique organization-typed entities. 448 entity discrepancy. | 2025-12-20 |

#### ORGANIZATION Entities with Wrong Types (Session 5 - E209-E229)

| ID | Entity | Current Type | Should Be | Reasoning | Date Found |
|----|--------|--------------|-----------|-----------|------------|
| E209 | Switzerland, Mexico, Colombia, Vietnam, Uganda, Costa Rica, India, Germany, Kenya, China, Ecuador, Ghana, Indonesia, Singapore, Rwanda, Peru, Guatemala, France, Cambodia | ORGANIZATION | LOCATION/COUNTRY | 20+ countries typed as ORGANIZATION | 2025-12-20 |
| E210 | California, Montana, Iowa, Schwyz | ORGANIZATION | LOCATION/STATE | US states and Swiss cantons | 2025-12-20 |
| E211 | Europe, Africa, Asia, Amazonia | ORGANIZATION | LOCATION/REGION | Continents/regions | 2025-12-20 |
| E212 | $REGEN, $NCT, $USDC, $BRANCH, $Regen, $regen, $Branch | ORGANIZATION | TOKEN/ASSET | Cryptocurrency tokens | 2025-12-20 |
| E213 | AI, Science, Nature, Agriculture, "AI agents" | ORGANIZATION | CONCEPT | Abstract concepts | 2025-12-20 |
| E214 | GitHub, Discord, ChatGPT, Twitter, Telegram, AWS, Medium, LinkedIn, YouTube, Reddit, Substack | ORGANIZATION | PLATFORM/SOFTWARE | Software products vs companies | 2025-12-20 |
| E215 | "the company", "indigenous communities", "corporates", "regulators", "governments", "investors", "partners", "network" | ORGANIZATION | ROLE/CATEGORY (Remove) | Generic lowercase category terms | 2025-12-20 |
| E216 | "Bächerstrasse 42, 8806 Bäch, SZ Switzerland", "8806 Bäch" | ORGANIZATION | ADDRESS (Remove) | Physical addresses | 2025-12-20 |
| E217 | @regen_network | ORGANIZATION | SOCIAL_HANDLE (Remove) | Twitter handle | 2025-12-20 |
| E218 | info@desci.com | ORGANIZATION | EMAIL (Remove) | Email address | 2025-12-20 |
| E219 | "A Dow Jones Company", "AUS companies" | ORGANIZATION | DESCRIPTION (Remove) | Descriptive phrases | 2025-12-20 |
| E220 | 501(c)3 | ORGANIZATION | LEGAL_STATUS (Remove) | Tax/legal designation | 2025-12-20 |
| E221 | Credit Class Admin, Methodology Developer | ORGANIZATION | ROLE | Roles in Regen ecosystem | 2025-12-20 |
| E222 | Osmosis, Axelar, Cosmos Hub, Evmos, Noble, Kava, Polygon, Ethereum, Neutron, Juno, Stride | ORGANIZATION | BLOCKCHAIN/PROTOCOL | Blockchain networks | 2025-12-20 |
| E223 | EU | ORGANIZATION | POLITY/POLITICAL_UNION | Political/economic union | 2025-12-20 |
| E224 | DRC | ORGANIZATION | COUNTRY | Democratic Republic of Congo | 2025-12-20 |
| E225 | localhost | ORGANIZATION | ARTIFACT (Remove) | Technical term | 2025-12-20 |
| E226 | Ecometric Methodology Update ID0044 V1.2.2_Final.pdf | ORGANIZATION | DOCUMENT | PDF filename | 2025-12-20 |
| E227 | Klima collaboration on Aerodrome deployment, Klima partnership on Base | ORGANIZATION | EVENT/ACTIVITY | Partnership activities | 2025-12-20 |
| E228 | Tokenomics WG, dPID Working Group, BxC working group | ORGANIZATION | WORKING_GROUP | Working groups | 2025-12-20 |
| E229 | our team, dev team, sustainability teams | ORGANIZATION | TEAM (Remove) | Generic team references | 2025-12-20 |

#### ORGANIZATION Duplicates (Session 5 - E230-E240)

| ID | Duplicate Entities | Likely Canonical Entity | Confidence | Date Found |
|----|-------------------|------------------------|------------|------------|
| E230 | Regen Network Development PBC (76), RND PBC (67), RND Inc (41), RND inc (22), RNDPBC (4), RND & Co. (1) | Regen Network Development, PBC | High | 2025-12-20 |
| E231 | Community of Sharamentsa (61), Sharamentsa Achuar (41), Sharamentsa community (14), Achuar Sharamentsa community (8) | Community of Sharamentsa | High | 2025-12-20 |
| E232 | Achuar Nation (28), Achuar nation, Achuar Community, Achuar community | Achuar Nation | High | 2025-12-20 |
| E233 | ERA (32), ERA Brazil (93), ERABrazil (4), Ecosystem Regeneration Associates (8), ERA team (3) | Ecosystem Regeneration Associates | High | 2025-12-20 |
| E234 | Ecometric (194), Ecometrik (6), Ecometeric (10), I ecometric Ltd. (4), iscometric (2) | Ecometric | High | 2025-12-20 |
| E235 | LOA Labs (60), loalabs (7), alphaBiota-LOA Labs (18), alphaBiota (7) | LOA Labs | Medium | 2025-12-20 |
| E236 | Fundacion Pachamama (128), Pachamama (24), Pachamama Alliance (20) | Fundacion Pachamama | Medium | 2025-12-20 |
| E237 | Kulshan Carbon Trust (154), Howard Kulshan Carbon Trust (2) | Kulshan Carbon Trust | High | 2025-12-20 |
| E238 | $REGEN (13), $Regen (1), $regen (1) | $REGEN (should be TOKEN) | High | 2025-12-20 |
| E239 | Toucan Protocol (34), Toucan team (2) | Toucan Protocol | High | 2025-12-20 |
| E240 | RF (20), Regen Foundation (711), Will-Regen Foundation (63) | Regen Foundation (RF is abbreviation) | Medium (Will-Regen Foundation unclear - may be extraction artifact. Note: Regen Foundation is DISTINCT from Regen Network Development PBC - different legal entities in same ecosystem) | 2025-12-20 |

**Note on Multi-Entity Organizational Structures:**
Many crypto/DAO organizations have multiple distinct legal entities that are NOT duplicates:
- **DAO** - Decentralized governance layer (e.g., KlimaDAO, Regen DAO)
- **Foundation** - Non-profit legal wrapper (e.g., Klima Foundation, Regen Foundation, Interchain Foundation)
- **For-profit company** - Development/commercial arm (e.g., Regen Network Development PBC, RND Inc)
- **Network/Protocol** - The actual blockchain/protocol (e.g., Regen Network, Cosmos Hub)

When reviewing duplicates, consider whether entities are truly duplicates (spelling variations of same entity) vs. intentionally separate legal/governance structures within the same ecosystem. For example:
- Regen Foundation ≠ Regen Network Development PBC (different legal entities)
- KlimaDAO ≠ Klima Foundation (different legal entities)
- RND PBC = RND Inc = RNDPBC (same company, different name formats - TRUE duplicates)

#### Generic ENTITY Type Analysis (E029 Deep Dive)

**Summary:** The 15,558 entities typed as generic "ENTITY" in PostgreSQL were analyzed via database queries.

| Category | Approx Count | Should Be | Examples |
|----------|--------------|-----------|----------|
| Forum Usernames | ~1,890 | PERSON | aliefaisala, sebytza05, hammerfest, chainflow |
| Credit Classes/Methodologies | ~887 | CREDIT_CLASS/METHODOLOGY | Biocultural Jaguar Credits, Biochar methodology |
| Organizations | ~727 | ORGANIZATION | Liquidity DAO, Eden Dao, Fundacion Pachamama |
| Person Names | ~562 | PERSON | Ethan Buchman, Daniel Christian Wahl |
| Software | ~329 | SOFTWARE | KOI, KOI Processor, PostgreSQL |
| Events/Media | ~169 | EVENT/MEDIA | Planetary Regeneration Podcast |
| Internal IDs | ~500+ | REMOVE | Claim_1_xxx, Evidence_1_xxx |
| Concepts | ~200+ | CONCEPT | blockchain, biodiversity, tokenization |
| Groups/Teams | ~150+ | GROUP | tokenomics working group |
| Role Templates | ~100+ | REMOVE/ROLE | contributors, researchers |

**Specific Errors (E327-E370):**

| ID | Entity | Current Type | Should Be | Reasoning | Date Found |
|----|--------|--------------|-----------|-----------|------------|
| E327 | Ethan Buchman | ENTITY | PERSON | Cosmos co-founder (HumanActor/PERSON in Fuseki) | 2025-12-20 |
| E328 | Daniel Christian Wahl | ENTITY | PERSON | Known author and consultant | 2025-12-20 |
| E329 | Liquidity DAO | ENTITY | ORGANIZATION | DAO (has 6 conflicting types in Fuseki!) | 2025-12-20 |
| E330 | Eden Dao | ENTITY | ORGANIZATION | DAO is an organization | 2025-12-20 |
| E331 | Crypto Commons Association | ENTITY | ORGANIZATION | Association | 2025-12-20 |
| E332 | Open Earth Foundation | ENTITY | ORGANIZATION | Foundation | 2025-12-20 |
| E333 | Fundacion Pachamama | ENTITY | ORGANIZATION | Foundation | 2025-12-20 |
| E334 | Biocultural Jaguar Credits | ENTITY | CREDIT_CLASS | Credit class per Regen Registry | 2025-12-20 |
| E335 | Jaguar Stewardship Credits | ENTITY | CREDIT_CLASS | Credit class | 2025-12-20 |
| E336 | City Forest Carbon Plus credits | ENTITY | CREDIT_CLASS | Credit class | 2025-12-20 |
| E337 | In-Stand Surface Application of Biochar methodology | ENTITY | METHODOLOGY | Methodology document | 2025-12-20 |
| E338 | Biodiversity Stewardship Credit Methodology | ENTITY | METHODOLOGY | Methodology document | 2025-12-20 |
| E339 | Desert Regreening Credit Class methodology | ENTITY | METHODOLOGY | Methodology document | 2025-12-20 |
| E340 | KOI | ENTITY | SOFTWARE | Software system | 2025-12-20 |
| E341 | KOI Processor | ENTITY | SOFTWARE | Software (PROJECT/PERSON in Fuseki!) | 2025-12-20 |
| E342 | Regen MCP TypeScript Server | ENTITY | SOFTWARE | Software server | 2025-12-20 |
| E343 | PostgreSQL | ENTITY | SOFTWARE | Database software | 2025-12-20 |
| E344 | Planetary Regeneration Podcast | ENTITY | MEDIA | Podcast | 2025-12-20 |
| E345 | monthly network-wide calls | ENTITY | EVENT | Recurring events | 2025-12-20 |
| E346 | Community Call, January 30 2025 | ENTITY | EVENT | Specific event | 2025-12-20 |
| E347 | tokenomics working group | ENTITY | GROUP | Working group | 2025-12-20 |
| E348 | REGEN Tokenomics Working Group | ENTITY | GROUP | Duplicate of E347 | 2025-12-20 |
| E349 | Regen Ledger team | ENTITY | GROUP | Team | 2025-12-20 |
| E350 | Validators | ENTITY | ROLE | Generic role | 2025-12-20 |
| E351 | contributors | ENTITY | ROLE (REMOVE?) | Generic role template | 2025-12-20 |
| E352 | researchers | ENTITY | ROLE (REMOVE?) | Generic role template | 2025-12-20 |
| E353 | blockchain | ENTITY | CONCEPT | Concept | 2025-12-20 |
| E354 | biodiversity | ENTITY | CONCEPT | Concept | 2025-12-20 |
| E355 | regenerative finance | ENTITY | CONCEPT | Concept | 2025-12-20 |
| E356 | Claim_1_interspecies_money_agency | ENTITY | REMOVE | Internal extraction ID | 2025-12-20 |
| E357 | Evidence_1_Sudd_flooding | ENTITY | REMOVE | Internal extraction ID | 2025-12-20 |
| E358 | rnd_rf_prioritize_purpose | ENTITY | REMOVE | Internal key | 2025-12-20 |
| E359 | professional_validator_status | ENTITY | REMOVE | Internal key | 2025-12-20 |
| E360 | aliefaisala | ENTITY | PERSON (forum) | Forum username | 2025-12-20 |
| E361 | sebytza05 | ENTITY | PERSON (forum) | Forum username | 2025-12-20 |
| E362 | hammerfest | ENTITY | PERSON (forum) | Forum username | 2025-12-20 |
| E363 | chainflow | ENTITY | PERSON/ORG | Validator | 2025-12-20 |
| E364 | westaking | ENTITY | ORGANIZATION | Validator org | 2025-12-20 |
| E365 | Success metrics | ENTITY | REMOVE | Generic phrase (1792 occurrences) | 2025-12-20 |
| E366 | validator registry | ENTITY | PLATFORM | Registry component | 2025-12-20 |
| E367 | Regen Website | ENTITY | PLATFORM | Web platform | 2025-12-20 |
| E368 | Regen Commons | ENTITY | PLATFORM/ORG | Platform or organization | 2025-12-20 |
| E369 | Ecometric Methodology Update ID0044.pdf | ENTITY | DOCUMENT | Document/file | 2025-12-20 |
| E370 | RegenerativeStandardSOC_V2.0.docx | ENTITY | DOCUMENT | Document/file | 2025-12-20 |

**Key Findings:**
1. PostgreSQL ENTITY is a fallback type when classification fails
2. Fuseki often has better types but with conflicts (e.g., Liquidity DAO has 6 types)
3. ~1,890 forum usernames need PERSON type
4. Internal IDs (Claim_X, Evidence_X) are extraction artifacts, not real entities
5. Role templates (contributors, researchers) shouldn't be standalone entities

#### Additional Gregory Duplicates (discovered via database query)

| ID | Duplicate Entities | Likely Canonical Entity | Confidence | Date Found |
|----|-------------------|------------------------|------------|------------|
| E030 | Gregory Landua, Gregory, Gregory_RND, Gregory0, Gregory Regen, Gregory Landau, Gregory Landuales, Gregory Landway, Gregory Landwey, Gregory Landua (stage) | Gregory Landua (CEO of Regen Network) | High | 2025-12-20 |

### Missing Entities Analysis (Session 9 - 2025-12-20)

This section identifies entities that SHOULD exist in the knowledge graph based on Regen Network's knowledge base, official documentation, and ecosystem activity. These represent gaps in extraction coverage.

#### Missing or Underrepresented Credit Classes

| ID | Entity | Expected Type | Evidence/Source | Priority |
|----|--------|---------------|-----------------|----------|
| M001 | C03: TCO2 Toucan Carbon Tokens Credit Class | CREDIT_CLASS | regen-data-standards schema, active on-chain | High |
| M002 | C04: Ruuts Credit Class for Soil Carbon Sequestration | CREDIT_CLASS | regen-data-standards schema | High |
| M003 | C05: Carbon Removal through Biochar Production | CREDIT_CLASS | regen-data-standards schema | High |
| M004 | Ecometric GHG Benefits Credit Class | CREDIT_CLASS | registry.regen.network, active methodology | High |
| M005 | CarbonPlus Grasslands Credit Class | CREDIT_CLASS | registry.regen.network | Medium |
| M006 | Terrasos Biodiversity Unit (TBU) | CREDIT_CLASS | registry.regen.network, recent launch | High |
| M007 | Virridy Watershed Nature-Based Infrastructure | CREDIT_CLASS/METHODOLOGY | registry.regen.network | Medium |

#### Missing Governance Proposals

| ID | Entity | Expected Type | Evidence/Source | Priority |
|----|--------|---------------|-----------------|----------|
| M008 | Governance Proposal #5: Regen Ledger v2.0 Upgrade | PROPOSAL | guides.regen.network, passed 99.99% | Medium |
| M009 | Governance Proposal #9: Regen Ledger v3.0 Upgrade | PROPOSAL | guides.regen.network, passed 99.87% | Medium |
| M010 | Governance Proposal #16: Adding Axelar USDC | PROPOSAL | guides.regen.network, passed | Medium |
| M011 | Governance Proposal #20: Add REGEN to Marketplace | PROPOSAL | guides.regen.network, passed 98.18% | Medium |
| M012 | Governance Proposal #24: MsgRemoveAllowedDenom | PROPOSAL | guides.regen.network, passed | Medium |
| M013 | Governance Proposal #28: Regen Ledger v5.1 Upgrade | PROPOSAL | guides.regen.network, passed 98.80% | Medium |
| M014 | NCT Basket Criteria Update Proposal | PROPOSAL | forum.regen.network | Medium |

#### Missing Validators (Active Set)

| ID | Entity | Expected Type | Evidence/Source | Priority |
|----|--------|---------------|-----------------|----------|
| M015 | Stakin Validator | VALIDATOR | forum.regen.network validator registry | Medium |
| M016 | Stake2earn Validator | VALIDATOR | forum.regen.network validator registry | Medium |
| M017 | Cambium Validator | VALIDATOR | forum.regen.network (noted as shutting down) | Low |
| M018 | P2P Validator | VALIDATOR | Alexander Kondakov's validator | Medium |

#### Missing Projects

| ID | Entity | Expected Type | Evidence/Source | Priority |
|----|--------|---------------|-----------------|----------|
| M019 | Sharamentsa Achuar Jaguar Stewardship Project | PROJECT | regen.network/buyers, Pachamama partnership | High |
| M020 | Biodiversidad Ancestral Project | PROJECT | regen.network/buyers, Ecuador | High |
| M021 | Matses Biocultural Credit Project | PROJECT | Notion internal docs | Medium |
| M022 | ERA Brazil Reforestation Projects | PROJECT | Multiple forum references | Medium |

#### Missing Technical Components

| ID | Entity | Expected Type | Evidence/Source | Priority |
|----|--------|---------------|-----------------|----------|
| M023 | MsgCreateBatch (as API_MESSAGE, not CLAIM) | API_MESSAGE | regen-ledger codebase | High |
| M024 | MsgRetire (as API_MESSAGE, not CLAIM) | API_MESSAGE | regen-ledger codebase | High |
| M025 | MsgSend (as API_MESSAGE, not CLAIM) | API_MESSAGE | regen-ledger codebase | High |
| M026 | Ecocredit Keeper | KEEPER | regen-ledger x/ecocredit | Medium |
| M027 | Marketplace Keeper | KEEPER | regen-ledger x/ecocredit/marketplace | Medium |
| M028 | Data Module Keeper | KEEPER | regen-ledger x/data | Medium |

#### Missing Events/Milestones

| ID | Entity | Expected Type | Evidence/Source | Priority |
|----|--------|---------------|-----------------|----------|
| M029 | Regen Mainnet Launch (April 2021) | EVENT | guides.regen.network, forum | High |
| M030 | Registry 2.0 Launch | EVENT | YouTube, community calls | High |
| M031 | Toucan Bridge Integration | EVENT | Forum discussions, governance | Medium |
| M032 | NCT on Regen Launch | EVENT | Forum, multiple sources | Medium |

#### Key Observations

1. **Credit Class Coverage Gap**: While credit classes are mentioned, they may not be properly typed as CREDIT_CLASS with correct metadata (admin, methodology links, etc.)

2. **Governance Proposals Missing**: The graph appears to lack systematic extraction of on-chain governance proposals with their voting outcomes

3. **Validator Registry Incomplete**: Many active validators from the forum registry may not be extracted as VALIDATOR entities

4. **Technical Components Mistyped**: Protobuf messages, queries, and events are extracted as CLAIMs rather than their proper technical types

5. **Projects vs Credit Classes Confusion**: Projects (specific land/site implementations) may be conflated with Credit Classes (the standards/methodologies)

#### Recommendations for Re-Extraction

1. **Add PROPOSAL entity type** for governance proposals with metadata (vote outcome, quorum, date)
2. **Add VALIDATOR entity type** for network validators with metadata (moniker, address, status)
3. **Distinguish CREDIT_CLASS from PROJECT** - classes are standards, projects are implementations
4. **Add API_MESSAGE, API_QUERY, API_EVENT types** for technical documentation
5. **Add EVENT/MILESTONE type** for network history events

---

### Error Categories Summary

Known categories (add new categories as discovered):

- [x] Entity extraction errors (E011, E012, E013, E014, E094-E117)
- [x] Relationship extraction errors (E019, E020, E021, E022, E183-E193)
- [x] Duplicate entities (E015-E018, E030, E122-E143, E305-E326)
- [x] Missing entities (M001-M032) - NEW
- [x] Incorrect entity types (E001-E010, E045-E093, E104-E112)
- [x] Malformed data/formatting issues (E019, E169-E186)
- [x] Graph structure issues (E194-E204) - self-referential relationships
- [x] Schema/ontology issues (E023-E029, E043-E044, E144-E146)
- [x] Data store inconsistencies (E028, E145, E205-E206)
- [x] AI/Software as Person (E045-E056, E108-E111) - NEW
- [x] Roles/Groups as Person (E057-E071) - NEW
- [x] Organizations as HumanActor (E072-E087) - NEW
- [x] Concepts/Non-entities as Person/HumanActor (E088-E093, E107) - NEW
- [x] Placeholder/Template entities (E094-E096) - NEW
- [x] Irrelevant/Out-of-scope Persons (E118-E121) - NEW
- [x] Predicate Duplication Issues (Relationship IDs in file) - NEW (namespace, case, format mixing)
- [x] Predicate Tense Inconsistencies - NEW (authored vs authors)
- [x] UPPER_CASE Predicates - NEW
- [x] Nonsensical Relationships (E183-E193) - NEW (template entities, type mismatches)
- [x] Self-Referential Relationships (E194-E204) - NEW

**Note:** Some error IDs (E147-E168) are used both for duplicate entity detection and predicate issues due to concurrent updates. See individual sections for details.

---

## Stage 2: Root Cause Analysis

**Analysis Date:** 2025-12-20
**Analyzed By:** Claude Code (Root Cause Analysis Agent)

---

### Root Cause Categories

| Category | Description | Example Files |
|----------|-------------|---------------|
| PROMPT_ISSUE | LLM extraction prompt needs improvement | llm_extractor.py, openai_extractor.py |
| MISSING_NORMALIZATION | No normalization where needed | ontology_normalizer_module.py |
| MISSING_VALIDATION | No validation/filtering | entity_quality_filter.py |
| NAMESPACE_INCONSISTENCY | Multiple namespaces for same concept | regenerate_fuseki_graph.py, graph_integration.py |
| SCHEMA_MISMATCH | Schema definitions inconsistent across stores | uri_generator.py |
| MISSING_FEATURE | Feature not implemented (e.g., deduplication) | entity_resolver.py |
| HARDCODED_VALUE | Hardcoded values causing issues | Multiple files |

---

### Finding 1: Git Commits Extracted as CLAIM

**Error IDs:** E006, E007, E261-E263, E275 (556+ entities affected)

**Stage:** Extraction

**Files:**
- `src/extraction/llm_extractor.py` lines 139-248
- `src/extraction/openai_extractor.py` lines 150-257

**Root Cause:** The LLM extraction prompt lists entity types including "CLAIM" but doesn't specifically exclude git commit messages, changelog entries, or version bumps. The prompt says:

```
"entities": [
    {"type": "CLAIM", "name": "...", "confidence": 0.8}
]
```

When processing GitHub changelogs, the LLM interprets lines like "Add marketplace Msg/UpdateSellOrders" as claims about features being added.

**Category:** PROMPT_ISSUE

**Evidence:**
- 556 CLAIMs in PostgreSQL match `^(add|update|fix|refactor|rename|remove|integrate|support|configure|create|patch|migration)`
- 873 claim-typed labels in Fuseki match the same pattern

**Proposed Fix:** Add negative examples to the extraction prompt:
```
DO NOT EXTRACT as CLAIM:
- Git commit messages (e.g., "Add...", "Fix...", "Update...")
- Changelog entries
- Version bumps (e.g., "Release v0.6.0")
- Code documentation headings
```

**Confidence:** High

---

### Finding 2: Organizations Typed as HumanActor/PERSON

**Error IDs:** E001, E072-E087, E104-E106 (100+ entities affected)

**Stage:** Extraction

**Files:**
- `src/extraction/llm_extractor.py` line 146
- `src/extraction/openai_extractor.py` line 157

**Root Cause:** The prompt lists entity types as "HumanActor/PERSON, ORGANIZATION" but uses "HumanActor" as the primary type. The LLM appears to default to HumanActor for any entity that "acts" or "does things" in the text - including organizations like Microsoft, Coca-Cola, and blockchains like Celo.

From line 146 of llm_extractor.py:
```python
2. Entities based on Regen ontology (HumanActor/PERSON, ORGANIZATION, PROJECT, CONCEPT, TECHNOLOGY, CLAIM, EVIDENCE, QUESTION)
```

The slash between HumanActor/PERSON creates ambiguity, and "HumanActor" is often chosen for any entity with agency.

**Category:** PROMPT_ISSUE + MISSING_VALIDATION

**Evidence:**
- Fuseki has 3,867 HumanActor entities including corporations, blockchains, platforms
- E072-E087 show major corporations (Coca-Cola, Microsoft, Nestlé) as HumanActor
- Countries like Colombia (E088) typed as HumanActor

**Proposed Fix:**
1. Remove HumanActor from prompt entirely - use PERSON only
2. Add examples distinguishing PERSON vs ORGANIZATION:
   ```
   PERSON: Named individuals ("Gregory Landua", "Sarah Bax")
   ORGANIZATION: Companies, DAOs, foundations, platforms ("Microsoft", "Regen Foundation", "Discord")
   ```
3. Add post-processing validation to check if PERSON entities are on a known organization list

**Confidence:** High

---

### Finding 3: Namespace Inconsistency (HTTP vs HTTPS, koi# vs ontology#)

**Error IDs:** E025, E026, E148, E152, E153, E158, E251

**Stage:** Loading/RDF Generation

**Files:**
- `src/knowledge_graph/graph_integration.py` lines 145-146
- `scripts/regenerate_fuseki_graph.py` lines 33-35
- `src/knowledge_graph/uri_generator.py` line 22

**Root Cause:** Multiple namespace definitions across the codebase with inconsistent protocols and paths:

| File | Namespace | Protocol |
|------|-----------|----------|
| graph_integration.py:145 | `https://regen.network/ontology#` | HTTPS |
| graph_integration.py:146 | `https://regen.network/koi#` | HTTPS |
| regenerate_fuseki_graph.py:33 | `http://regen.network/koi#` | HTTP |
| regenerate_fuseki_graph.py:34 | `http://regen.network/koi/entity/` | HTTP |
| uri_generator.py:22 | `https://regen.network` | HTTPS |

This creates:
- 26,055 triples with `http://regen.network/koi#confidence`
- 1,715 triples with `https://regen.network/koi#confidence`
- Types under both `koi#` and `ontology#` namespaces

**Category:** NAMESPACE_INCONSISTENCY

**Evidence:**
- E158: HTTP vs HTTPS protocol inconsistency in confidence predicates
- E148, E152, E153: Predicates exist in both `koi#` and `ontology#` namespaces
- E251: Claim types split across `koi#Claim`, `koi#CLAIM`, `ontology#Claim`

**Proposed Fix:**
1. Create a single `config/namespaces.py` file:
   ```python
   KOI_NAMESPACE = "https://regen.network/koi#"  # Single source of truth
   ENTITY_NAMESPACE = "https://regen.network/koi/entity/"
   ```
2. Update all files to import from this single source
3. Run migration script to normalize existing data to HTTPS

**Confidence:** High

---

### Finding 4: Type Case Inconsistency (CLAIM vs Claim vs claim)

**Error IDs:** E025, E043, E144, E251, E253

**Stage:** Extraction + Loading

**Files:**
- `src/extraction/llm_extractor.py` lines 159-168
- `src/knowledge_graph/postprocessing/modules/ontology_normalizer_module.py` lines 31-76
- `scripts/regenerate_fuseki_graph.py` line 120-121

**Root Cause:** Multiple issues cause type case inconsistency:

1. **LLM prompt uses mixed case:** The prompt examples show "PERSON" (uppercase) but also "HumanActor" (PascalCase)

2. **Normalizer exists but not always applied:** `OntologyNormalizerModule` normalizes to UPPERCASE but:
   - Only processes entities going through the pipeline
   - regenerate_fuseki_graph.py creates `KOI[f"{entity_type}Entity"]` which appends "Entity" suffix

3. **regenerate_fuseki_graph.py line 120-121:**
   ```python
   entity_type = entity["entity_type"]
   type_uri = KOI[f"{entity_type}Entity"]  # Creates PERSONEntity, CLAIMEntity
   ```

**Category:** MISSING_NORMALIZATION

**Evidence:**
- `koi#Claim` (19,698) vs `koi#CLAIM` (9,156)
- `koi#Person` (704) vs `koi#PERSON` (1,902)
- 166 entities have BOTH Claim AND CLAIM types

**Proposed Fix:**
1. Enforce UPPERCASE in OntologyNormalizerModule (already does this)
2. Ensure all data paths use the normalizer
3. Fix regenerate_fuseki_graph.py to use consistent type naming:
   ```python
   type_uri = KOI[entity_type.upper()]  # Not entity_type + "Entity"
   ```
4. Add validation in entity_resolver to normalize types on insert

**Confidence:** High

---

### Finding 5: Predicate Duplication and Inconsistency

**Error IDs:** E147-E164, E165-E174, E175-E182

**Stage:** Extraction

**Files:**
- `src/extraction/llm_extractor.py` lines 169-172
- `src/extraction/openai_extractor.py` lines 174-176
- `src/knowledge_graph/postprocessing/modules/ontology_normalizer_module.py` lines 78-117
- `src/core/canonical_predicates.json`

**Root Cause:** The LLM extraction prompts don't constrain predicate vocabulary. The only example given is:

```
"relationships": [
    {"subject": "entity1", "predicate": "supports", "object": "entity2"}
]
```

This allows the LLM to generate free-form predicates like:
- 7 variations of "supports": supports, supportedBy, is_supported_by, support, ontology#supports, supported_by, ontology#supportedBy
- 6 variations of "relatesTo": relatesTo, is_related_to, isRelatedTo, relatedTo, related_to, relates_to
- Tense mixing: authors vs authored, develops vs developed

The `OntologyNormalizerModule` has predicate mappings (lines 78-117) but the set is incomplete, and the canonical_predicates.json only maps to generic categories.

**Category:** PROMPT_ISSUE + MISSING_NORMALIZATION

**Evidence:**
- 200+ unique predicates in Fuseki
- E147: 5 variations of guest-related predicates
- E148: 7637 triples across 7 "supports" variations
- E165-E174: Present vs past tense mixing

**Proposed Fix:**
1. Add controlled vocabulary to extraction prompt:
   ```
   ALLOWED PREDICATES:
   - worksFor, memberOf, foundedBy, locatedIn
   - supports, mentions, develops, uses
   - participatesIn, contributesTo, collaboratesWith

   Always use present tense. Always use camelCase.
   ```
2. Expand OntologyNormalizerModule predicate mappings
3. Add validation to reject non-standard predicates

**Confidence:** High

---

### Finding 6: Entity Deduplication Insufficient for Name Variants

**Error IDs:** E015, E030, E122-E143, E305-E326

**Stage:** Post-Processing

**Files:**
- `src/knowledge_graph/entity_resolver.py` lines 24-38, 170-232
- `src/knowledge_graph/improvements/canonical_resolver.py`

**Root Cause:** The EntityResolver has three tiers:
1. Tier 1: Exact match on normalized text
2. Tier 1.5: Canonical mapping from data/canonical_entities.json
3. Tier 2: Semantic match via embeddings (threshold 0.88)
4. Tier 3: Create new

However:
- Tier 1 normalization (lines 50-83 of uri_generator.py) is minimal:
  - Lowercase
  - Remove articles (the, a, an)
  - Collapse whitespace
  - NO handling of first name vs full name (Will vs Will Szal)
  - NO handling of usernames vs real names (@willszal vs Will Szal)

- Tier 1.5 canonical mappings exist but are incomplete

- Tier 2 semantic threshold of 0.88 may not catch all variations:
  - "Will" and "Will Szal" may not reach 0.88 similarity

**Category:** MISSING_FEATURE

**Evidence:**
- E122: Will (112), Will Szal (56), @willszal (1), willszal (1), willzal (1) = 5 variants
- E030: Gregory Landua has 10+ variants
- E305-E326: Many organization and person duplicates

**Proposed Fix:**
1. Expand canonical_entities.json with known name variants
2. Add name variant detection:
   ```python
   def detect_name_variants(name1, name2):
       # Check if one is first name of the other
       # Check if one is username variant (@, underscore)
       # Check for typos (Levenshtein distance)
   ```
3. Lower semantic threshold to 0.80 for PERSON entities
4. Add post-extraction consolidation pass for PERSON entities

**Confidence:** High

---

### Finding 7: AI Systems/Software Typed as PERSON

**Error IDs:** E045-E056, E108-E111 (20+ entities affected)

**Stage:** Extraction

**Files:**
- `src/extraction/llm_extractor.py` lines 179-197
- `src/knowledge_graph/improvements/entity_quality_filter.py`

**Root Cause:** The prompt does not explicitly exclude AI systems, software, or bots from PERSON classification. When content mentions "Claude said..." or "the agent recommended...", the LLM extracts these as PERSON entities.

The entity_quality_filter.py has extensive pattern matching but no specific check for known AI system names.

**Category:** PROMPT_ISSUE + MISSING_VALIDATION

**Evidence:**
- E045-E048: Claude, Claude Code, OpenAI, GPT as PERSON
- E049-E052: gaiaaiagent, Eliza Agent, Registry Agent as PERSON
- E053-E056: BGE Embedding Server, DeepSeek, Llama, Whisper as PERSON

**Proposed Fix:**
1. Add to prompt:
   ```
   DO NOT EXTRACT as PERSON:
   - AI systems/models: Claude, GPT, Llama, DeepSeek, Whisper
   - Software agents: *Agent, *Bot
   - Software components: *Server, *Service, *Client
   ```
2. Add AI_SYSTEM and SOFTWARE to entity type list
3. Add known AI/software names to entity_quality_filter blocklist

**Confidence:** High

---

### Finding 8: PostgreSQL vs Fuseki Data Mismatch

**Error IDs:** E028, E145, E205, E254, E255

**Stage:** Loading

**Files:**
- `scripts/regenerate_fuseki_graph.py`
- `src/knowledge_graph/graph_integration.py`
- `src/knowledge_graph/entity_resolver.py`

**Root Cause:** Two separate data loading paths exist:

1. **PostgreSQL entity_registry:** Populated by EntityResolver during extraction
2. **Fuseki triple store:** Can be populated by:
   - graph_integration.py (direct RDF insertion)
   - regenerate_fuseki_graph.py (batch export from PostgreSQL)
   - Other scripts

Issues:
- URI formats differ: PostgreSQL stores `https://regen.network/claim/<hash>`, but Fuseki has `http://regen.network/koi/entity/<name>`
- No owl:sameAs bridging between URI formats
- Relationship data only in Fuseki (PostgreSQL relationships table empty per E205)
- Count mismatches: PostgreSQL 7,828 CLAIMs vs Fuseki 29,377

**Category:** SCHEMA_MISMATCH

**Evidence:**
- E254: PostgreSQL fuseki_uri values don't exist in Fuseki
- E255: CLAIM count mismatch (7,828 vs 29,377)
- E028: HumanActor in Fuseki but not PostgreSQL

**Proposed Fix:**
1. Choose single URI generation strategy
2. Use regenerate_fuseki_graph.py as ONLY path to Fuseki
3. Add validation script to check PostgreSQL/Fuseki parity
4. Add owl:sameAs triples if multiple URIs must exist

**Confidence:** High

---

### Finding 9: Generic ENTITY Fallback Overused

**Error IDs:** E029, E291-E301 (15,558 entities affected)

**Stage:** Extraction + Loading

**Files:**
- `src/knowledge_graph/uri_generator.py` line 38
- `src/extraction/llm_extractor.py`

**Root Cause:** In uri_generator.py line 108:
```python
type_prefix = self.TYPE_PREFIXES.get(entity_type_upper, "entity")  # Default to "entity"
```

When the entity type from extraction doesn't match known types, it falls back to generic "entity". This happens when:
- LLM returns unexpected type names
- Type normalization doesn't recognize the type
- Confidence is too low for specific typing

**Category:** MISSING_VALIDATION

**Evidence:**
- 15,558 entities typed as generic ENTITY in PostgreSQL
- Many should be PERSON (forum usernames), ORGANIZATION, CONCEPT, etc.

**Proposed Fix:**
1. Add more types to TYPE_PREFIXES
2. Add validation: reject extraction if type is unknown
3. Run classification pass on existing ENTITY entities
4. Log when falling back to generic type

**Confidence:** High

---

### Finding 10: Self-Referential Relationships Not Validated

**Error IDs:** E194-E204 (11+ relationships affected)

**Stage:** Loading

**Files:**
- `src/knowledge_graph/graph_integration.py` lines 669-708

**Root Cause:** The `_add_relationship` method validates that subject, predicate, and object exist, but does NOT validate that subject ≠ object. Self-referential relationships are created without any check:

```python
# Line 678 - Only checks for non-empty, not for self-reference
if not all([subject_name, predicate, object_name]):
    return None

# Line 703 - Creates triple without checking subject != object
self.graph.add((subject_uri, pred_uri, object_uri))
```

This allows meaningless relationships like:
- `Claim —supports→ Claim` (type-level self-reference)
- `Spotify —isOwnedBy→ Spotify` (self-ownership)
- `MsgSendResponse —no_changes→ MsgSendResponse`

**Category:** MISSING_VALIDATION

**Evidence:**
- E194-E204: 11 self-referential relationship patterns documented
- No self-reference check in extraction prompts or post-processing

**Proposed Fix:**
1. Add validation in `_add_relationship`:
   ```python
   # After line 678
   if subject_name.lower().strip() == object_name.lower().strip():
       self.logger.debug(f"Blocked self-referential: {subject_name} —{predicate}→ {object_name}")
       return None
   ```
2. Add to extraction prompt:
   ```
   DO NOT EXTRACT relationships where subject equals object.
   ```
3. Add post-processing module `SelfReferenceFilter`

**Confidence:** High

---

### Finding 11: Roles/Groups Typed as PERSON (Incomplete Blocklist)

**Error IDs:** E057-E071 (15 entities affected)

**Stage:** Post-Processing

**Files:**
- `src/knowledge_graph/improvements/entity_quality_filter.py` lines 337-360

**Root Cause:** The `GENERIC_GROUP_TERMS` blocklist (lines 339-360) is incomplete. It catches:
- ✅ Generic group terms: "validators", "community", "users"

But misses:
- ❌ Role patterns: "REGEN LEDGER LEAD", "COMMS LEAD", "PARTNERSHIPS LEAD"
- ❌ Capitalized versions: "Development Team" (lowercase "team" is blocked, but capitalized passes)
- ❌ Software components in blockchain context: "Keeper" (a Cosmos SDK module)
- ❌ Generic role patterns: "Node Operator", "Reviewer", "Admin"

The `is_generic_group` method (lines 568-597) uses case-insensitive matching (`normalized = name.strip().lower()`), but the DEFAULT_STOP_WORDS only block lowercase versions of some terms.

**Category:** MISSING_VALIDATION

**Evidence:**
- E057: "Development Team" passed filter (should match "team" but doesn't)
- E058-E060: "*LEAD" role patterns not blocked
- E061: "Keeper" is a Cosmos module component, not a person
- E062-E071: Various roles/groups extracted as PERSON

**Proposed Fix:**
1. Add role patterns to blocklist:
   ```python
   ROLE_PATTERNS = [
       re.compile(r'\bLEAD\b', re.IGNORECASE),     # "COMMS LEAD", "Partnerships Lead"
       re.compile(r'\bOperator\b', re.IGNORECASE), # "Node Operator"
       re.compile(r'\bTeam\b', re.IGNORECASE),     # "Development Team"
       re.compile(r'\bAdministrator\b', re.IGNORECASE),
   ]
   ```
2. Add Cosmos SDK module names to blocklist:
   ```python
   COSMOS_MODULES = {"keeper", "querier", "handler", "genesis", "indexer"}
   ```
3. Add `is_role_pattern` method to EntityQualityFilter

**Confidence:** High

---

### Error Source Mapping Summary

| Error ID | Stage | File/Component | Root Cause | Category |
|----------|-------|----------------|------------|----------|
| E006-E007, E261-E263, E275 | Extraction | llm_extractor.py:139-248 | Prompt doesn't exclude git commits from CLAIM | PROMPT_ISSUE |
| E001, E072-E087 | Extraction | llm_extractor.py:146 | HumanActor used for organizations | PROMPT_ISSUE |
| E025, E026, E158 | Loading | Multiple files | HTTP vs HTTPS namespace inconsistency | NAMESPACE_INCONSISTENCY |
| E025, E043, E251 | Loading | regenerate_fuseki_graph.py:120-121 | Type case not normalized (CLAIM vs Claim) | MISSING_NORMALIZATION |
| E147-E164 | Extraction | llm_extractor.py:169-172 | No controlled vocabulary for predicates | PROMPT_ISSUE |
| E165-E174 | Extraction | llm_extractor.py | Tense inconsistency in predicates | PROMPT_ISSUE |
| E015, E122-E143 | Post-Processing | entity_resolver.py | Name variants not deduplicated | MISSING_FEATURE |
| E045-E056 | Extraction | llm_extractor.py | AI systems extracted as PERSON | PROMPT_ISSUE |
| E254-E255 | Loading | regenerate_fuseki_graph.py | PostgreSQL/Fuseki URI mismatch | SCHEMA_MISMATCH |
| E029, E291-E301 | Loading | uri_generator.py:108 | Generic ENTITY fallback overused | MISSING_VALIDATION |
| E194-E204 | Loading | graph_integration.py:669-708 | No self-reference validation | MISSING_VALIDATION |
| E057-E071 | Post-Processing | entity_quality_filter.py:337-360 | Role/group patterns not blocked | MISSING_VALIDATION |

### Pipeline Stages Reference

- **Extraction Stage** - Initial entity/relationship extraction from documents (`src/extraction/`)
- **Post-Processing Stage** - Cleaning, deduplication, normalization (`src/knowledge_graph/postprocessing/`)
- **Loading Stage** - Database insertion and graph construction (`src/knowledge_graph/`, `scripts/`)
- **Visualization Stage** - Graph display issues (frontend)

---

## Stage 3: Fix Planning

**Synthesized From:** Agent 1 (Codex CLI), Agent 2 (Claude Code)
**Synthesis Date:** 2025-12-20

### Fix Priority Matrix

| Priority | Fixes | Rationale |
|----------|-------|-----------|
| P0 (Prerequisite) | FIX-001 | Makes migrations + parity checks meaningful by eliminating namespace/type URI drift first. |
| P1 (Stop Bad Data) | FIX-002, FIX-003 | Prevents new bad entities/claims from being generated (prompt/schema) and stops ENTITY-default inserts + filter bypass paths. |
| P2 (Improve Filters) | FIX-004, FIX-005 | Improves filtering + typing quality after extraction (roles/groups, missing/coarse ontology types). |
| P3 (Dedup/Relationships) | FIX-006, FIX-007 | Only tune dedup after type stability; then enforce canonical predicates + relationship validation. |
| P4 (Schema/Sync) | FIX-008 | Architectural decisions for PostgreSQL/Fuseki parity + relationship persistence strategy. |

---

### Detailed Fix Plans

### FIX-001: Canonical Namespace + Type URI Conventions (HTTP→HTTPS, KOI/ontology strategy, case)

**Related Findings:** F3, F4
**Related Errors:** E025-E027, E043, E144, E158, E207, E251-E253
**Severity:** High (prerequisite; affects large swaths of triples/types)
**Complexity:** High
**Priority:** P0
**Dependencies:** None

#### Problem Summary
The codebase currently defines multiple namespace bases and type URI patterns (HTTP vs HTTPS, `koi#` vs `ontology#`, and mixed-case class names). This fragments the graph into parallel vocabularies and makes parity/migration checks unreliable. Two different protocols are used: `http://regen.network/koi#` in `regenerate_fuseki_graph.py:33` and `https://regen.network/koi#` in `graph_integration.py:146`. Additionally, entity types use inconsistent casing: `koi#Claim` vs `koi#CLAIM`.

#### Affected Files
| File | Changes Required |
|------|------------------|
| `koi-processor/src/knowledge_graph/graph_integration.py` | Replace inline namespace constants with a single canonical source (`:144-156`). |
| `koi-processor/scripts/regenerate_fuseki_graph.py` | Switch namespaces to HTTPS and stop generating `"{entity_type}Entity"` type URIs (`:32-35`, `:119-121`). |
| `koi-processor/src/knowledge_graph/postprocessing/modules/ontology_normalizer_module.py` | Ensure canonical type-case rules align with the chosen class namespace (`:31-76`, `:179-194`). |
| `koi-processor/src/knowledge_graph/uri_generator.py` | Confirm base URI strategy aligns with canonical namespace decision (`:22`, `:107-121`). |

#### Implementation Steps
1. Define canonical namespace constants in a new shared module (e.g., `koi-processor/src/core/namespaces.py`) and replace hard-coded namespaces in `koi-processor/src/knowledge_graph/graph_integration.py:144-156`.
2. Decide the **canonical class namespace** for entity types (open question from review):
   - Option A (recommended): classes in `https://regen.network/ontology#` and KOI predicates in `https://regen.network/koi#`.
   - Option B: all classes in `https://regen.network/koi#`.
   Document the decision and apply it consistently in `_parse_type_uri()` call sites (`koi-processor/src/knowledge_graph/graph_integration.py:651-657`, `:845-859`).
3. Update `koi-processor/scripts/regenerate_fuseki_graph.py:33`:
   ```python
   # BEFORE
   KOI = Namespace("http://regen.network/koi#")
   # AFTER
   KOI = Namespace("https://regen.network/koi#")
   ```
4. Update lines 34-35 similarly for ENTITY and REL namespaces.
5. Replace `koi-processor/scripts/regenerate_fuseki_graph.py:119-121` type URI generation with canonical casing and namespace (remove `"Entity"` suffix and mixed-case drift).
6. **Choose canonical convention:** UPPERCASE (matches existing `OntologyNormalizerModule` output).
7. Expand `OntologyNormalizerModule.DEFAULT_TYPE_MAPPINGS` to include ALL variations:
   ```python
   CASE_NORMALIZATIONS = {
       'Claim': 'CLAIM',
       'claim': 'CLAIM',
       'Evidence': 'EVIDENCE',
       'evidence': 'EVIDENCE',
       'Question': 'QUESTION',
       'question': 'QUESTION',
       'Person': 'PERSON',
       'person': 'PERSON',
       'HumanActor': 'PERSON',
       'humanactor': 'PERSON',
       'Organization': 'ORGANIZATION',
       'organization': 'ORGANIZATION',
       'Project': 'PROJECT',
       'project': 'PROJECT',
       'Concept': 'CONCEPT',
       'concept': 'CONCEPT',
   }
   ```

#### Code Changes
```python
# src/core/namespaces.py (NEW FILE)
from rdflib import Namespace

# Canonical namespace definitions - SINGLE SOURCE OF TRUTH
KOI = Namespace("https://regen.network/koi#")
ENTITY = Namespace("https://regen.network/koi/entity/")
REL = Namespace("https://regen.network/koi/relationship/")
REGEN = Namespace("https://regen.network/ontology#")

# Import in all other files:
# from src.core.namespaces import KOI, ENTITY, REL, REGEN
```

#### Data Migration
```sparql
# Fuseki SPARQL UPDATE to fix existing HTTP triples
DELETE { ?s ?p ?o }
INSERT { ?s_new ?p_new ?o_new }
WHERE {
  ?s ?p ?o .
  BIND(IRI(REPLACE(STR(?s), "http://regen.network/", "https://regen.network/")) AS ?s_new)
  BIND(IRI(REPLACE(STR(?p), "http://regen.network/", "https://regen.network/")) AS ?p_new)
  BIND(
    IF(ISIRI(?o),
       IRI(REPLACE(STR(?o), "http://regen.network/", "https://regen.network/")),
       ?o
    ) AS ?o_new
  )
  FILTER(CONTAINS(STR(?s), "http://regen.network/") ||
         CONTAINS(STR(?p), "http://regen.network/") ||
         (ISIRI(?o) && CONTAINS(STR(?o), "http://regen.network/")))
}
```

```sparql
# Fix type case inconsistencies in Fuseki
DELETE { ?s a <https://regen.network/koi#Claim> }
INSERT { ?s a <https://regen.network/koi#CLAIM> }
WHERE { ?s a <https://regen.network/koi#Claim> };

DELETE { ?s a <https://regen.network/ontology#Claim> }
INSERT { ?s a <https://regen.network/koi#CLAIM> }
WHERE { ?s a <https://regen.network/ontology#Claim> };

# Repeat for Person/PERSON, Evidence/EVIDENCE, etc.
```

```sql
-- PostgreSQL: Update fuseki_uri in entity_registry
UPDATE entity_registry
SET fuseki_uri = REPLACE(fuseki_uri, 'http://regen.network/', 'https://regen.network/')
WHERE fuseki_uri LIKE 'http://regen.network/%';

-- PostgreSQL: Normalize entity_type column
UPDATE entity_registry
SET entity_type = UPPER(entity_type)
WHERE entity_type != UPPER(entity_type);
```

#### Testing Plan
- Unit test: namespace constants are used everywhere (smoke test by importing and checking string values).
- Unit test: `ontology_normalizer_module` normalizes all case variants.
- Integration test: `koi-processor/scripts/regenerate_fuseki_graph.py` produces HTTPS namespaces and canonical type URIs.
- Data validation (Fuseki):
  ```sparql
  SELECT (COUNT(*) AS ?count) WHERE {
    ?s ?p ?o .
    FILTER(CONTAINS(STR(?s), "http://regen.network/"))
  }
  # Expected: 0
  ```
  ```sparql
  SELECT DISTINCT ?type (COUNT(*) AS ?count)
  WHERE { ?s a ?type . FILTER(CONTAINS(STR(?type), "koi#")) }
  GROUP BY ?type ORDER BY ?type
  # Verify: Only UPPERCASE types remain
  ```

#### Rollback Plan
- Restore Fuseki from the latest backup (`fuseki-db-20251212_023204.tar.gz` per review doc).
- Restore PostgreSQL from the latest backup (`eliza_pre_phase2_20251211_192720.sql.gz` per review doc).
- Revert the namespace module changes and re-run the prior export/import path.

#### Estimated Impact
- Errors fixed: 10 (F3=3, F4=7)
- Entities affected: ≥166 dual-typed entities (F4 evidence) plus any entities in non-canonical namespaces
- Triples affected: ≥26,055 `http://regen.network/koi#confidence` triples (F3 evidence) + ~5,000+ type assertions

---

### FIX-002: Extractor/Schema Unification + Prompt Hardening (HumanActor ambiguity, git commits, AI systems, boilerplate)

**Related Findings:** F1, F2, F7, F15
**Related Errors:** E001-E004, E006-E007, E013-E014, E020-E022, E045-E056, E072-E093, E104-E111, E146, E183-E193, E256-E277, E302, E304
**Severity:** High (stops new bad data at the source)
**Complexity:** High
**Priority:** P1
**Dependencies:** FIX-001 (recommended before migrations/parity checks)

#### Problem Summary
`llm_extractor.py` and `openai_extractor.py` diverge in allowed entity types and JSON schema, leading to systematic mis-typing (especially `HumanActor` vs `PERSON`/`ORGANIZATION`) and poor downstream filtering. `openai_extractor.py:157` only lists `(HumanActor, Claim, Evidence, Question)` while `llm_extractor.py:146` lists a broader set. Prompts also allow commit/changelog lines as CLAIMs, treat AI systems as PERSON, and don't reliably suppress boilerplate/template content.

#### Affected Files
| File | Changes Required |
|------|------------------|
| `koi-processor/src/extraction/llm_extractor.py` | Update prompt + ensure parsed entities retain confidence (`:_build_extraction_prompt:139-248`, `:_parse_extraction:340-373`). |
| `koi-processor/src/extraction/openai_extractor.py` | Align prompt/schema types and include confidence in emitted JSON (`:_build_extraction_prompt:150-257`). |
| `koi-processor/src/core/entity_types.py` (NEW) | Single source of truth for entity types. |
| `koi-processor/src/knowledge_graph/improvements/entity_quality_filter.py` | Add git/changelog patterns, AI/software blocklist, boilerplate blocklist. |

#### Implementation Steps
1. Create `src/core/entity_types.py` with canonical type definitions:
   ```python
	   CANONICAL_ENTITY_TYPES = {
	       'PERSON': {
	           'description': 'Named individuals with proper names',
	           'aliases': ['HumanActor', 'HUMAN_ACTOR', 'INDIVIDUAL'],
	           'examples': ['Gregory Landua', 'Sarah Bax'],
	       },
       'ORGANIZATION': {
           'description': 'Companies, foundations, networks, institutions',
           'aliases': ['ORG', 'COMPANY', 'FOUNDATION'],
           'examples': ['Regen Network', 'World Wildlife Fund'],
       },
       'PROJECT': {
           'description': 'Named initiatives, software, platforms',
           'aliases': ['REPO', 'SOFTWARE', 'PRODUCT'],
           'examples': ['Regen Ledger', 'Koi Project'],
       },
       'CONCEPT': {
           'description': 'Abstract ideas, methodologies, frameworks',
           'aliases': ['IDEA', 'TOPIC', 'METHODOLOGY'],
           'examples': ['regenerative agriculture', 'proof of stake'],
       },
	       'TECHNOLOGY': {
	           'description': 'Technical systems and tools',
	           'aliases': ['TECH', 'TOOL', 'SYSTEM'],
	           'examples': ['blockchain', 'smart contracts'],
	       },
	       'LOCATION': {
	           'description': 'Geographic places (countries, cities, regions)',
	           'aliases': ['PLACE', 'CITY', 'COUNTRY', 'REGION', 'GPE'],
	           'examples': ['Boulder, Colorado', 'Colombia'],
	       },
	       'EVENT': {
	           'description': 'Named events (calls, conferences, workshops) when explicitly titled',
	           'aliases': ['MEETING', 'CONFERENCE', 'WORKSHOP', 'SUMMIT'],
	           'examples': ['Regen Network Community Call', 'COP28'],
	       },
	       'CLAIM': {'description': 'Assertions and statements', 'aliases': []},
	       'EVIDENCE': {'description': 'Supporting data and proof', 'aliases': []},
	       'QUESTION': {'description': 'Questions and inquiries', 'aliases': []},
	   }
	   ```
	2. Update `openai_extractor.py:157` to use full type list:
   ```python
   # BEFORE (line 157)
   2. Entities based on Regen Network ontology (HumanActor, Claim, Evidence, Question)
   # AFTER
	   2. Entities based on Regen Network ontology (PERSON, ORGANIZATION, PROJECT, CONCEPT, TECHNOLOGY, LOCATION, EVENT, CLAIM, EVIDENCE, QUESTION)
	   ```
3. Add explicit exclusion patterns for git commits/changelog:
   ```python
   GIT_CHANGELOG_PATTERNS: List[re.Pattern] = [
       re.compile(r'^(feat|fix|chore|docs|style|refactor|test|build|ci|perf)(\(.*?\))?:', re.IGNORECASE),
       re.compile(r'^Merge (pull request|branch|remote)', re.IGNORECASE),
       re.compile(r'^v?\d+\.\d+(\.\d+)?(-\w+)?$'),
       re.compile(r'^\[.*?\]\s*(Added|Fixed|Changed|Removed|Updated|Deprecated)', re.IGNORECASE),
       re.compile(r'^(PR|Issue|Bug|Feature)[\s#:-]+\d+', re.IGNORECASE),
   ]
   ```
4. Add AI/software blocklist:
   ```python
   AI_SOFTWARE_BLOCKLIST = {
       'chatgpt', 'gpt-4', 'gpt-3', 'gpt-4o', 'claude', 'copilot', 'bard',
       'alexa', 'siri', 'cortana', 'github actions', 'ci bot', 'auto-merge',
       'openai', 'anthropic', 'google assistant', 'dall-e', 'midjourney',
       'stable diffusion', 'llama', 'mistral', 'gemini', 'palm',
   }
   ```
5. Add explicit "AI systems are not PERSON" rules in both prompts (F7), and reclassify them as `TECHNOLOGY`.
6. Boilerplate/template suppression (F15):
   - Add prompt-level "no entities/relationships from templates" guidance.
   - Add a deterministic pre-check in extractors to short-circuit extraction when content matches strong template signatures (e.g., headings like "Acceptance Criteria", "Steps to reproduce", "Test plan").
7. Ensure confidence is present end-to-end in both extractors.

#### Code Changes
```python
# entity_quality_filter.py - Add git/changelog detection
def is_git_changelog(self, name: str, entity_type: str) -> bool:
    """Check if name looks like git commit or changelog entry."""
    if entity_type.upper() != 'CLAIM':
        return False
    for pattern in self.GIT_CHANGELOG_PATTERNS:
        if pattern.search(name.strip()):
            return True
    return False

def is_ai_software_as_person(self, name: str, entity_type: str) -> bool:
    if entity_type.upper() != 'PERSON':
        return False
    return name.strip().lower() in self.AI_SOFTWARE_BLOCKLIST
```

#### Data Migration
**⚠️ OPTIONAL: Only if not doing full re-extraction (see Q6)**
```sql
-- Delete CLAIM entities matching git/changelog patterns
DELETE FROM entity_registry
WHERE entity_type = 'CLAIM'
  AND (
    entity_text ~* '^(feat|fix|chore|docs|refactor|test|build|ci)\('
    OR entity_text ~* '^Merge (pull request|branch)'
    OR entity_text ~* '^v?\d+\.\d+\.\d+'
    OR entity_text ~* '^\[.*\] (Added|Fixed|Changed|Removed)'
  );

-- Retype HumanActor entities that look like organizations
UPDATE entity_registry
SET entity_type = 'ORGANIZATION'
WHERE entity_type IN ('HUMANACTOR', 'HumanActor')
  AND (
    entity_text ~* '(Network|Foundation|DAO|Protocol|Labs|Inc|Corp|LLC)$'
    OR entity_text ~* '^(The|A) .* (Network|Foundation|Organization|Institute)$'
  );

-- Retype AI/software entities from PERSON to TECHNOLOGY
UPDATE entity_registry
SET entity_type = 'TECHNOLOGY'
WHERE entity_type = 'PERSON'
  AND LOWER(entity_text) IN (
    'chatgpt', 'gpt-4', 'gpt-3', 'claude', 'copilot', 'bard',
    'alexa', 'siri', 'openai', 'anthropic', 'github actions'
  );
```

#### Testing Plan
- Unit tests:
  - Prompt builder emits the unified type list for both extractors.
  - `_parse_extraction` preserves `confidence` values.
  - Git patterns correctly identified and blocked.
  - AI names blocked when typed as PERSON.
- Integration tests:
  - Feed a fixture "git changelog" document and assert **0** CLAIM entities extracted.
  - Feed a fixture with "Claude / GPT / OpenAI" mentions and assert they are not typed as PERSON.
  - Feed an issue template fixture and assert extraction returns empty entities/relationships.
  - Re-extract sample documents with both extractors, compare output.

#### Rollback Plan
- Revert extractor prompt/schema changes.
- Rerun extraction with the previous extractor versions and restore from backups if cleanup migrations were applied.

#### Estimated Impact
- Errors fixed: 88 (F1=27, F2=29, F7=16, F15=16)
- Entities affected (evidence): ≥518 commit-like CLAIMs; ≥130 org-like HumanActor labels; ≥23 orgs mistyped as PERSON/HUMANACTOR; ≥16 AI-system PERSONs
- Triples affected: downstream reductions in claim/entity/relationship triples after re-extraction

---

### FIX-003: Stop ENTITY-Default Inserts + Fix Pipeline Ordering + Add Placeholder/Min-Length Validation

**Related Findings:** F9, F12, F13
**Related Errors:** E012, E029, E094-E096, E115-E117, E290-E301, E327-E370
**Severity:** High (drives the largest volume of low-quality rows)
**Complexity:** High
**Priority:** P1
**Dependencies:** FIX-001, FIX-002

#### Problem Summary
The dominant driver of generic `ENTITY` overuse is relationship-driven entity creation: `KnowledgeGraphIntegrator._add_relationship()` calls `_get_or_create_entity_by_name(name)` which defaults `entity_type="ENTITY"` (`koi-processor/src/knowledge_graph/graph_integration.py:690-693`, `:714-743`). Additionally, pipeline ordering runs `EntityQualityFilter` before `OntologyNormalizer`, allowing `HumanActor` (and other pre-normalized types) to bypass PERSON-specific placeholder/generic checks.

#### Affected Files
| File | Changes Required |
|------|------------------|
| `koi-processor/src/knowledge_graph/graph_integration.py` | Infer/preserve entity types during relationship insertion and stop defaulting to `ENTITY` (`:_add_relationship:669-708`, `:_get_or_create_entity_by_name:714-797`). |
| `koi-processor/src/knowledge_graph/config/pipeline_config.json` | Reorder modules so `OntologyNormalizer` runs before quality filters (`:5-61`). |
| `koi-processor/src/knowledge_graph/improvements/entity_quality_filter.py` | Add min-length + placeholder regex patterns; treat `HUMANACTOR` like PERSON for filters (`:562-597`, `:643-780`). |

#### Implementation Steps
1. **Fix pipeline ordering** so type normalization happens before PERSON-specific checks:
   - Update `koi-processor/src/knowledge_graph/graph_integration.py:204-211` and `koi-processor/src/knowledge_graph/config/pipeline_config.json:33-61` to run `OntologyNormalizer` before `EntityQualityFilter` and `ListSplitter`.
2. **Min-length validation (F12):**
   ```python
   def is_too_short(self, name: str) -> bool:
       """Check if name is too short (single letter/character)."""
       stripped = name.strip()
       if stripped in self._whitelist:
           return False
       return len(stripped) <= 1
   ```
3. **Placeholder detection expansion (F13):**
   ```python
   PLACEHOLDER_PATTERNS = [
       re.compile(r'^unknown\s*\d*$', re.IGNORECASE),
       re.compile(r'^anonymous(\s+user)?$', re.IGNORECASE),
       re.compile(r'^public\s+users?$', re.IGNORECASE),
       re.compile(r'^user\s*\d+$', re.IGNORECASE),
       re.compile(r'^(tbd|todo|n/?a|none)$', re.IGNORECASE),
       re.compile(r'^placeholder\s*\d*$', re.IGNORECASE),
       re.compile(r'^(test|dummy|sample)\s*(user|data|entity)?$', re.IGNORECASE),
   ]

   def is_placeholder(self, name: str, entity_type: str = None) -> bool:
       """Check for placeholder patterns (applies to ALL types now)."""
       for pattern in self.PLACEHOLDER_PATTERNS:
           if pattern.match(name.strip()):
               return True
       return False
   ```
4. **Stop relationship-driven ENTITY creation (F9):**
   - In `koi-processor/src/knowledge_graph/graph_integration.py:690-693`, infer subject/object type before calling `_get_or_create_entity_by_name()`. Add predicate-based type inference:
   ```python
   PREDICATE_TYPE_HINTS = {
       'works_at': {'subject': 'PERSON', 'object': 'ORGANIZATION'},
       'founded': {'subject': 'PERSON', 'object': 'ORGANIZATION'},
       'located_in': {'subject': None, 'object': 'LOCATION'},
       'part_of': {'subject': None, 'object': 'ORGANIZATION'},
       'supports': {'subject': None, 'object': 'CONCEPT'},
   }

   def _infer_type_from_predicate(self, predicate: str, role: str) -> str:
       hints = self.PREDICATE_TYPE_HINTS.get(predicate.lower(), {})
       return hints.get(role, 'ENTITY')  # Still fallback to ENTITY if unknown
   ```
5. Add metrics/logging to quantify how often ENTITY fallback occurs after the fix.

#### Code Changes
```python
# graph_integration.py (within _add_relationship)
def _add_relationship(self, rel: Dict[str, Any], doc_uri: URIRef) -> Optional[Tuple]:
    # Extract type hints from relationship
    subject_type = rel.get("subject_type", self._infer_type_from_predicate(predicate, "subject"))
    object_type = rel.get("object_type", self._infer_type_from_predicate(predicate, "object"))

    subject_uri = self._get_or_create_entity_by_name(subject_name, entity_type=subject_type)
    object_uri = self._get_or_create_entity_by_name(object_name, entity_type=object_type)
```

#### Data Migration
**⚠️ OPTIONAL: Only if not doing full re-extraction (see Q6)**
```sql
-- Delete single-letter entities
DELETE FROM entity_registry
WHERE LENGTH(TRIM(entity_text)) <= 1
  AND entity_text NOT IN (SELECT unnest(ARRAY['I', 'A', 'R', 'C']));  -- Preserve whitelisted abbreviations

-- Delete placeholder entities
DELETE FROM entity_registry
WHERE entity_text ~* '^(unknown|anonymous|public users?|tbd|todo|n/?a|placeholder)\s*\d*$';

-- Analyze ENTITY rows and attempt type inference
UPDATE entity_registry
SET entity_type = 'ORGANIZATION',
    metadata = jsonb_build_object('inferred_from', 'pattern_match', 'original_type', 'ENTITY')
WHERE entity_type = 'ENTITY'
  AND entity_text ~* '(Network|Foundation|DAO|Protocol|Labs)$';
```

#### Testing Plan
- Unit test: min-length filter blocks `A` for PERSON/HUMANACTOR but does not block `US` if whitelisted.
- Unit test: placeholder regex blocks "Anonymous", "User 123", "N/A".
- Integration test: relationship insertion does not create `entity_type='ENTITY'` when a type can be inferred.
- Regression test: pipeline ordering change makes `HumanActor` run through PERSON filters before insertion.
- Data validation:
  ```sql
  SELECT COUNT(*) FROM entity_registry
  WHERE entity_type = 'ENTITY' AND metadata = '{}';
  -- Expected: Significantly reduced from 15,558
  ```

#### Rollback Plan
- Revert pipeline ordering and relationship typing inference.
- Restore PostgreSQL from backup if bulk reclassification was applied.

#### Estimated Impact
- Errors fixed: 64 (F9=56, F12=3, F13=5)
- Entities affected (evidence): ≥15,558 generic ENTITY rows with empty metadata; ≥8 single-character entities; ≥265 placeholder-pattern entities
- Triples affected: downstream reductions in relationship-driven entity creation and cleanup of placeholder nodes

---

### FIX-004: Role/Group Detection Upgrade (singular/plural + multi-word role patterns)

**Related Findings:** F11
**Related Errors:** E057-E071
**Severity:** Medium (high leverage for PERSON/HumanActor noise)
**Complexity:** Medium
**Priority:** P2
**Dependencies:** FIX-003 (ensures type normalization/order so PERSON checks actually apply)

#### Problem Summary
Role/group terms are being extracted as PERSON due to gaps in term coverage (singular vs plural and multi-word roles). The current `GENERIC_GROUP_TERMS` omits key singular forms (e.g., `team`) and relies on last-token checks that miss many role patterns.

#### Affected Files
| File | Changes Required |
|------|------------------|
| `koi-processor/src/knowledge_graph/improvements/entity_quality_filter.py` | Expand generic group/role detection (`:338-360`, `:568-597`). |
| `koi-processor/src/extraction/llm_extractor.py` | Reinforce PERSON vs GROUP guidance (already present at `:189-197`; expand examples). |
| `koi-processor/src/extraction/openai_extractor.py` | Same as above (`:198-206`). |

#### Implementation Steps
1. Expand blocklist with singular forms:
   ```python
   GENERIC_GROUP_TERMS: Set[str] = {
       # Include both singular and plural
       "buyer", "buyers", "seller", "sellers", "trader", "traders",
       "investor", "investors", "stakeholder", "stakeholders",
       "partner", "partners", "sponsor", "sponsors",
       "funder", "funders", "donor", "donors", "backer", "backers",
       "user", "users", "member", "members", "participant", "participants",
       "team", "teams", "group", "groups", "community", "communities",
       "contributor", "contributors", "validator", "validators",
       "developer", "developers", "builder", "builders",
   }
   ```
2. Add regex-based role detection:
   ```python
   ROLE_PATTERNS = [
       re.compile(r'\b(buyers?|sellers?|traders?)\b', re.IGNORECASE),
       re.compile(r'\b(validators?|delegators?|voters?)\b', re.IGNORECASE),
       re.compile(r'\b(developers?|builders?|contributors?)\b', re.IGNORECASE),
       re.compile(r'\b(communities|teams?|groups?|networks?)\b', re.IGNORECASE),
       re.compile(r'\b(lead|manager|operator|reviewer|admin|moderator)\b', re.IGNORECASE),
       re.compile(r'\b(committee|council|task ?force)\b', re.IGNORECASE),
   ]

   def matches_role_pattern(self, name: str, entity_type: str) -> bool:
       if entity_type.upper() not in ('PERSON', 'HUMANACTOR', 'ENTITY'):
           return False
       for pattern in self.ROLE_PATTERNS:
           if pattern.search(name):
               return True
       return False
   ```
3. Decide handling for "Keeper" and other Cosmos SDK terms:
   - Block as PERSON (short-term), or
   - Retype to a domain type in FIX-005 (preferred long-term).
4. Add a "retag vs drop" policy:
   - If a role term is generic with no named individual, drop it.
   - If it's a named team/group, consider retagging to ORGANIZATION (requires careful heuristics).

#### Data Migration
```sql
-- Delete generic group terms typed as PERSON
DELETE FROM entity_registry
WHERE entity_type IN ('PERSON', 'HUMANACTOR')
  AND LOWER(entity_text) ~* '\b(buyers?|sellers?|traders?|validators?|delegators?|communities|teams?|groups?)\b$';
```

#### Testing Plan
- Unit tests for `is_generic_group()` covering:
  - "Development Team" (should block/retag)
  - "Comms Lead" / "Partnerships Lead" (should block/retag)
  - "validators" vs "validator" (both blocked)
- Data validation:
  ```sql
  SELECT entity_text FROM entity_registry
  WHERE entity_type = 'PERSON'
    AND LOWER(entity_text) IN ('buyers', 'buyer', 'validators', 'validator', 'community');
  -- Expected: 0 rows
  ```

#### Rollback Plan
- Revert filter expansions.
- If retagging/deletes were applied in PostgreSQL, restore from backup or replay the audit table in reverse.

#### Estimated Impact
- Errors fixed: 15
- Entities affected (evidence): ≥115 role/group-like PERSON entities
- Triples affected: downstream reductions in relationships involving role/group pseudo-person nodes

---

### FIX-005: Ontology Granularity Expansion (missing/coarse types + domain types)

**Related Findings:** F16
**Related Errors:** E005, E008-E011, E023-E024, E031-E040, E044, E097-E103, E107, E112-E114, E118-E121, E209-E229, E278-E289, M001-M032
**Severity:** Medium (large error ID surface, but feature-like)
**Complexity:** High
**Priority:** P2
**Dependencies:** FIX-001, FIX-002 (schema alignment needed before adding more types)

#### Problem Summary
The current type system is too coarse and omits key domain entities (e.g., credit classes, governance proposals, keepers, Msg* message types). This drives mis-typing, missing entities, and later dedup instability because entities that should be distinct get collapsed under broad types.

#### Affected Files
| File | Changes Required |
|------|------------------|
| `koi-processor/src/extraction/llm_extractor.py` | Expand ontology type list and examples (`:146`, `:159-167`). |
| `koi-processor/src/extraction/openai_extractor.py` | Expand schema/type list (`:157`, `:170-176`). |
| `koi-processor/src/knowledge_graph/postprocessing/modules/ontology_normalizer_module.py` | Add mappings for new domain types (`:31-76`). |
| `koi-processor/src/knowledge_graph/uri_generator.py` | Add TYPE_PREFIXES for new types (`:24-39`). |
| `koi-processor/src/knowledge_graph/improvements/entity_quality_filter.py` | Adjust technical patterns/whitelists to avoid blocking legitimate domain identifiers. |

#### Implementation Steps

**Phase 1:** Add core Regen domain types:
1. Define a target expanded type list:
   - Domain: `CREDIT_CLASS`, `GOVERNANCE_PROPOSAL`, `VALIDATOR`, `MODULE`, `API_MESSAGE`, `KEEPER`.
   - General: `LICENSE`, `PROCESS`, `MATERIAL`, `STANDARD`.
2. Update both extractor prompts to include the new types and crisp selection guidance.
3. Extend `OntologyNormalizerModule.DEFAULT_TYPE_MAPPINGS`:
   ```python
   DEFAULT_TYPE_MAPPINGS.update({
     "CREDITCLASS": "CREDIT_CLASS",
     "GOVERNANCEPROPOSAL": "GOVERNANCE_PROPOSAL",
     "MESSAGE": "API_MESSAGE",
   })
   ```
4. Extend deterministic URI support for the new types:
   ```python
   TYPE_PREFIXES.update({
     "CREDIT_CLASS": "credit-class",
     "GOVERNANCE_PROPOSAL": "proposal",
     "API_MESSAGE": "msg",
     "MODULE": "module",
     "VALIDATOR": "validator",
   })
   ```

**Phase 2:** Add general categories (LICENSE, STANDARD, PROCESS, MATERIAL).

**Phase 3:** Add post-classification pass for coarse types.

5. Update filters so domain identifiers aren't blanket-blocked as "technical patterns" when they're legitimately typed (e.g., allow `MsgSendResponse` as `API_MESSAGE`).

#### Data Migration
```sql
-- Retype based on patterns
UPDATE entity_registry
SET entity_type = 'CREDIT_CLASS'
WHERE entity_type IN ('CONCEPT', 'PROJECT', 'ENTITY')
  AND entity_text ~* 'Credit Class$';

UPDATE entity_registry
SET entity_type = 'API_MESSAGE'
WHERE entity_type IN ('CONCEPT', 'TECHNOLOGY', 'ENTITY')
  AND entity_text ~* '^Msg[A-Z]';
```

#### Testing Plan
- Unit tests:
  - URI generation includes prefixes for new types (no fallback to `"entity"`).
  - OntologyNormalizer maps variants correctly.
- Integration tests:
  - Fixture docs containing credit classes / Msg* types extract the new types and are not filtered out.

#### Rollback Plan
- Revert prompt/type additions and URI prefixes.
- Re-run extraction with prior type list and restore from backups if migrations applied.

#### Estimated Impact
- Errors fixed: 98 (+32 missing entity IDs)
- Entities affected: potentially hundreds (type expansion affects classification and dedup stability broadly)
- Triples affected: broad; expect large redistribution across types and predicate/type triples

---

### FIX-006: Dedup Improvements (per-type thresholds + deterministic alias rules)

**Related Findings:** F6
**Related Errors:** E015-E018, E030, E041-E042, E122-E143, E230-E240, E305-E326
**Severity:** Medium (many duplicate clusters; requires careful tuning)
**Complexity:** High
**Priority:** P3
**Dependencies:** FIX-001, FIX-002, FIX-003, FIX-005 (type stability first)

#### Problem Summary
Dedup is currently sensitive to type instability and uses thresholds that are not tuned per type. The effective threshold in the main pipeline is `0.95` (`koi-processor/src/knowledge_graph/graph_integration.py:254-286`), while `EntityResolver`'s default is `0.88` (`koi-processor/src/knowledge_graph/entity_resolver.py:40-62`). Name normalization is minimal, so common variants (usernames vs names, first-name-only, punctuation) remain as duplicates.

#### Affected Files
| File | Changes Required |
|------|------------------|
| `koi-processor/src/knowledge_graph/graph_integration.py` | Make dedup threshold configurable per type (`:254-286`). |
| `koi-processor/src/knowledge_graph/entity_resolver.py` | Add per-type threshold support + deterministic alias rules before embeddings (`:40-74`, `:196-265`). |
| `koi-processor/src/knowledge_graph/uri_generator.py` | Improve `normalize_name()` to reduce trivial variants (`:50-83`). |
| `koi-processor/data/canonical_entities.json` | Expand curated alias mappings for high-value entities. |

#### Implementation Steps
1. Introduce per-type dedup thresholds:
   ```python
   TYPE_THRESHOLDS = {
       'PERSON': 0.92,      # Lower to catch name variations
       'ORGANIZATION': 0.95, # Higher to avoid false merges
       'CONCEPT': 0.90,     # Lower for semantic similarity
       'CLAIM': 0.98,       # Very high - claims should be nearly identical
       'DEFAULT': 0.95,
   }

   def _threshold_for_type(self, entity_type: str) -> float:
       return self.per_type_thresholds.get(entity_type.upper(), self.fuzzy_threshold)
   ```
2. Add deterministic alias rules **before** embedding lookup:
   - Username normalization: strip leading `@`, treat `_`/`-` as spaces for PERSON candidates.
   - First-name/full-name heuristics (careful: avoid merging common names).
   - Punctuation normalization for obvious variants.
3. Expand `canonical_entities.json` with observed duplicates from error sets E122-E143 and E305-E326.
4. Add a "dedup dry run" report mode that outputs proposed merges without applying them (review/approval step).

#### Data Migration
```sql
-- Postgres merge strategy (transactional, staged)
-- Create a dedup_merge_plan table containing (winner_uri, loser_uri, reason, score)
CREATE TABLE dedup_merge_plan (
    winner_uri TEXT,
    loser_uri TEXT,
    reason TEXT,
    score FLOAT,
    applied BOOLEAN DEFAULT FALSE
);

-- Apply merges by consolidating occurrence_count/last_seen_at and rewriting references
```

#### Testing Plan
- Unit tests:
  - `normalize_name()` behavior for usernames and punctuation.
  - Per-type threshold selection logic.
- Integration tests:
  - Reproduce "Will" variant cases and verify they resolve to one canonical entity without false merges.

#### Rollback Plan
- Restore PG from backup (or revert merges using `dedup_merge_plan` reversal).
- Restore Fuseki backup if `owl:sameAs` or rewrite updates were applied.

#### Estimated Impact
- Errors fixed: 62
- Entities affected: all duplicate clusters referenced by E015-E018/E122-E143/E305-E326
- Triples affected: reduced duplication (fewer entities and duplicate relationship edges after rebuild)

---

### FIX-007: Predicate Canonicalization + Relationship Schema Validation (incl. self-reference)

**Related Findings:** F5, F10, F14
**Related Errors:** E019, E147-E157, E159-E182, E194-E204, E206
**Severity:** Medium
**Complexity:** High
**Priority:** P3
**Dependencies:** FIX-001, FIX-006 (recommended ordering: after type + dedup stability)

#### Problem Summary
Predicates are currently unconstrained, leading to 3,483 distinct predicates (F5 evidence). Although `OntologyNormalizerModule` contains predicate mappings, relationship predicates do not reliably flow through the pipeline today. Relationship validation is missing (allowing malformed/duplicate edges) and self-referential relationships are inserted without checks.

#### Affected Files
| File | Changes Required |
|------|------------------|
| `koi-processor/src/knowledge_graph/postprocessing/modules/ontology_normalizer_module.py` | Expand predicate mappings + keep canonical snake_case output (`:79-117`, `:196-212`). |
| `koi-processor/src/knowledge_graph/graph_integration.py` | Add relationship validation guardrails (`:_add_relationship:669-708`). |
| `koi-processor/src/core/canonical_predicates.json` | Redefine as "variant → canonical predicate" mapping. |
| `koi-processor/src/knowledge_graph/config/pipeline_config.json` | Insert a relationship validation module in the pipeline (new). |
| `koi-processor/src/knowledge_graph/postprocessing/modules/relationship_validator_module.py` (NEW) | New module for relationship validation. |

#### Implementation Steps
1. Define canonical predicate style (recommend: snake_case) and enforce it end-to-end.
2. Rewrite `canonical_predicates.json`:
   ```json
   {
     "canonical_predicates": {
       "works_at": ["employed_by", "works_for", "affiliated_with", "employed_at"],
       "founded": ["created", "established", "started", "launched"],
       "mentions": ["refers_to", "cites", "references", "discusses"],
       "part_of": ["member_of", "belongs_to", "in"],
       "located_in": ["based_in", "headquartered_in", "operates_in"],
       "supports": ["backs", "endorses", "advocates_for"],
       "works_with": ["collaborates_with", "partners_with", "cooperates_with"]
     },
     "style": "snake_case",
     "reject_unknown": false
   }
   ```
3. Implement `RelationshipValidatorModule` as a `FilterModule`:
   ```python
   # relationship_validator_module.py (new)
   class RelationshipValidatorModule(FilterModule):
       def should_block_relationship(self, rel):
           if not rel.source or not rel.predicate or not rel.target:
               return True, "missing_fields"
           if normalize(rel.source) == normalize(rel.target):
               return True, "self_reference"
           if canonical_predicate(rel.predicate) not in ALLOWED_PREDICATES:
               return True, "invalid_predicate"
           return False, ""
   ```
4. Add validation in `_add_relationship()` for self-referential relationships:
   ```python
   # Prevent self-referential relationships
   if subject_name and object_name:
       if subject_name.strip().lower() == object_name.strip().lower():
           self.logger.debug(f"Blocked self-referential: {subject_name} -> {object_name}")
           return None
   ```
5. Add relationship dedup within a document (prevent duplicate edges that differ only by predicate formatting).

#### Data Migration
**⚠️ OPTIONAL: Only if not doing full re-extraction (see Q6)**
```sparql
# Normalize predicates in Fuseki (example: GUEST_ON -> guest_on)
DELETE { ?s <https://regen.network/koi#GUEST_ON> ?o }
INSERT { ?s <https://regen.network/koi#guest_on> ?o }
WHERE { ?s <https://regen.network/koi#GUEST_ON> ?o };

# Delete self-referential triples in Fuseki
DELETE WHERE { ?s ?p ?s };
```

#### Testing Plan
- Unit tests:
  - `_normalize_predicate()` maps variants consistently.
  - Relationship validator blocks self-referential and malformed edges.
- Integration tests:
  - Insert a document with duplicated predicate variants and assert only canonical predicate survives.
- Data validation:
  ```sparql
  SELECT ?s ?p WHERE { ?s ?p ?s }
  # Expected: 0 results

  SELECT DISTINCT ?p (COUNT(*) AS ?count)
  WHERE { ?s ?p ?o }
  GROUP BY ?p ORDER BY DESC(?count)
  # Verify: Only canonical predicates remain
  ```

#### Rollback Plan
- Restore Fuseki backup if predicate rewrites were applied.
- Revert pipeline module additions and relationship validation logic.

#### Estimated Impact
- Errors fixed: 48 (F5=35, F10=11, F14=2)
- Entities affected: relationships touching affected predicates + any self-referential nodes
- Triples affected (evidence): 3,483 predicate URIs will collapse; ≥13 self-referential triples removed

---

### FIX-008: PostgreSQL/Fuseki Parity + Relationship Persistence Strategy (E205 decision)

**Related Findings:** F8, F17
**Related Errors:** E028, E145, E205, E208, E254-E255
**Severity:** High (parity + operability issue)
**Complexity:** High
**Priority:** P4
**Dependencies:** FIX-001 through FIX-007

#### Problem Summary
PostgreSQL and Fuseki are currently not aligned: entity counts diverge (e.g., PostgreSQL CLAIM entities 7,828 vs Fuseki claim-like entities 29,377 per review), and the PostgreSQL `relationships` table is empty (F17 metric: 0 rows). Multiple URI strategies exist across code paths, complicating store parity.

#### Affected Files
| File | Changes Required |
|------|------------------|
| `koi-processor/scripts/regenerate_fuseki_graph.py` | Make it a canonical export path (HTTPS namespaces + correct type URIs) and decide whether it must export relationships too (`:32-35`, `:119-121`). |
| `koi-processor/src/knowledge_graph/graph_integration.py` | Decide whether direct RDF insertion remains allowed; align entity URI strategy (`integrate_document:341-409`, `_add_entity:633-667`). |
| `koi-processor/scripts/validate_store_parity.py` (NEW) | Automated parity checks (counts + URI existence). |
| PostgreSQL `relationships` table | Decide implement vs deprecate. |

#### Implementation Steps
1. Make an explicit architectural decision (required):
   - **Option A (recommended):** PostgreSQL is authoritative for entities + relationships; Fuseki is derived from PG via a single export path.
   - **Option B:** Fuseki is authoritative; PostgreSQL entity_registry is a supporting index; relationships table remains unused.
2. Unify entity URI strategy:
   - Stop generating `KOI["entity:<hash>"]` URIs for extracted entities and instead use deterministic URIs that are persisted (via `EntityResolver`) for all entities.
3. Fix export script:
   - Apply FIX-001 changes in `koi-processor/scripts/regenerate_fuseki_graph.py:32-35` and `:119-121`.
   - If Option A: extend script to also export relationships from PostgreSQL.
4. Relationship persistence (E205/F17):
   - Current schema exists: `relationships(sourceEntityId, targetEntityId, agentId, tags, metadata)` and is empty.
   - Decide whether to:
     - Populate this schema (requires mapping extracted entities to `entities.id`), or
     - Create a new relationship table keyed by `entity_registry.fuseki_uri`.
5. Add parity validation tooling:
   ```python
   # validate_store_parity.py (new)
   pg_counts = query_pg("SELECT entity_type, COUNT(*) FROM entity_registry GROUP BY 1")
   fuseki_counts = query_sparql("SELECT ?t (COUNT(?s) AS ?c) WHERE { ?s a ?t } GROUP BY ?t")
   assert no_http_namespaces(fuseki_counts)
   assert all_entity_registry_uris_exist_in_fuseki()
   ```

#### Data Migration
- If Option A:
  - Backfill a relationship store from Fuseki (if Fuseki contains the only relationship history) before cutting over.
  - Then clear Fuseki and rebuild solely from PostgreSQL via the export script.
- If Option B:
  - Update parity expectations: PostgreSQL counts are not expected to match Fuseki for claim/evidence/question nodes that exist only in RDF.

#### Testing Plan
- Integration test: run `koi-processor/scripts/regenerate_fuseki_graph.py` end-to-end in a staging environment and confirm counts.
- Parity test: `validate_store_parity.py` fails CI if drift is detected beyond a configured tolerance.
- Data validation queries:
  ```sql
  SELECT COUNT(*) FROM relationships;
  -- Should be >0 if Option A implements persistence
  ```
  ```sparql
  SELECT (COUNT(?s) AS ?count) WHERE { ?s a ?type }
  ```

#### Rollback Plan
- Restore Fuseki and PostgreSQL from backups.
- Re-enable previous loading path (direct insertion vs export script), depending on chosen architecture.

#### Estimated Impact
- Errors fixed: 6 (F8=5, F17=1)
- Entities affected (evidence): at least the CLAIM mismatch surface (7,828 vs 29,377 claim-like)
- Triples affected: potentially full graph rebuild (most reliable path after pipeline fixes)

---

### Fix Dependency Graph

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ P0 (PREREQUISITE)                                                                │
│                                                                                  │
│   FIX-001 (Namespace + Type Case)  ──→  All downstream fixes depend on stable   │
│                                          URIs and canonical types               │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ P1 (STOP BAD DATA)                                                               │
│                                                                                  │
│   FIX-002 (Extractor Schema + Prompts) ────┐                                    │
│                                             │                                    │
│   FIX-003 (ENTITY Default + Filters)   ────┴──→  Prevent new errors at          │
│                                                   extraction and insertion       │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ P2 (IMPROVE FILTERS)                                                             │
│                                                                                  │
│   FIX-004 (Role/Group Detection)  ────┐                                         │
│                                        │                                         │
│   FIX-005 (Ontology Granularity)  ────┴──→  Catch errors post-extraction        │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ P3 (DEDUP/RELATIONSHIPS)                                                         │
│                                                                                  │
│   FIX-006 (Dedup Improvements)     ────┐                                        │
│                                         │                                        │
│   FIX-007 (Predicates + Rel Valid) ────┴──→  Clean up after type stability      │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ P4 (SCHEMA/SYNC)                                                                 │
│                                                                                  │
│   FIX-008 (Store Parity)   ──→  Run after all other fixes complete              │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

### Summary

| Priority | Fix Count | Total Errors Addressed* |
|----------|-----------|------------------------|
| P0       | 1         | 10  |
| P1       | 2         | 152 |
| P2       | 2         | 113 |
| P3       | 2         | 110 |
| P4       | 1         | 6   |
| **Total**| **8**     | **391** |

*Counts are the sum of "Error Count" per finding from the Stage 2 coverage matrix; overlapping error IDs across findings (if any) may cause double-counting.

---

### Resolved Questions

**Decision Date:** 2025-12-21

---

#### Q1: Canonical Namespace Choice

**Decision:** Use `https://regen.network/koi#` for types AND predicates

**Namespace Strategy:**
| Purpose | Namespace | Example |
|---------|-----------|---------|
| Entity types | `https://regen.network/koi#` | `koi#PERSON`, `koi#CLAIM` |
| Predicates | `https://regen.network/koi#` | `koi#works_at`, `koi#supports` |
| Entity instances | `https://regen.network/{type_prefix}/{hash}` | `https://regen.network/person/abc123` |

**Important:** Entity instance URIs use the **existing** pattern (`{type_prefix}/{hash}`) already in production (28,561 rows in `entity_registry.fuseki_uri`). No migration needed—just enforce HTTPS and consistent type/predicate namespaces. The `uri_generator.py` already generates this pattern correctly.

**Scope of FIX-001:** Only enforce:
- HTTPS everywhere (no HTTP)
- Types/predicates use `koi#` namespace
- UPPERCASE type names
- Entity instance URIs: keep existing pattern, just ensure HTTPS

**Rationale:**
- Quantified Evidence shows `koi#` dominates (26,055 triples)
- Simpler migration (HTTP→HTTPS only, no namespace consolidation)
- This is a domain-specific KG, not a public ontology—semantic purity matters less
- Fewer code changes required

---

#### Q2: AI Systems Classification

**Decision:** Use existing `TECHNOLOGY` type (no new `AI_SYSTEM` type)

**Rationale:**
- Only ~20 AI/software entities found (low volume)
- `TECHNOLOGY` accurately describes ChatGPT, Claude, etc.
- Can always add `AI_SYSTEM` later if needed

---

#### Q3: Relationship Persistence Strategy

**Decision:** Create new `koi_relationships` table keyed to `entity_registry`

**⚠️ TIMING: Early Stage 4 (before any Fuseki rebuild)**

This must be implemented BEFORE any "rebuild Fuseki from PostgreSQL" step. Otherwise edges are lost. Sequence:
1. Create `koi_relationships` table
2. Implement write-path in `graph_integration.py`
3. THEN rebuild Fuseki (which now exports edges too)

**Key Points:**
- PostgreSQL is authoritative for both entities AND relationships
- Fuseki is a derived/serving layer rebuilt from PostgreSQL
- Do NOT repurpose or drop `public.relationships` (ElizaOS table, different schema)
- Keep legacy tables untouched to avoid breaking app code/migrations/ORM

**Schema (with provenance):**
```sql
CREATE TABLE koi_relationships (
  id BIGSERIAL PRIMARY KEY,
  subject_entity_id INTEGER NOT NULL REFERENCES entity_registry(id) ON DELETE CASCADE,
  predicate TEXT NOT NULL,
  object_entity_id INTEGER NOT NULL REFERENCES entity_registry(id) ON DELETE CASCADE,
  confidence REAL,
  last_doc_rid TEXT,                               -- Provenance: most recent source document RID
  last_run_id TEXT,                                -- Provenance: most recent extraction run
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,     -- Can store doc_rids array for full provenance
  occurrence_count INTEGER NOT NULL DEFAULT 1,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT koi_relationships_no_self CHECK (subject_entity_id <> object_entity_id),
  CONSTRAINT koi_relationships_triple_uniq UNIQUE (subject_entity_id, predicate, object_entity_id)
);
CREATE INDEX koi_relationships_subject_idx ON koi_relationships(subject_entity_id);
CREATE INDEX koi_relationships_object_idx  ON koi_relationships(object_entity_id);
CREATE INDEX koi_relationships_pred_idx    ON koi_relationships(predicate);
CREATE INDEX koi_relationships_last_doc_idx ON koi_relationships(last_doc_rid);
```

**Provenance Decision:** Use `last_doc_rid` and `last_run_id` (always overwritten on conflict). Keep it simple—no doc_rids array for now:

**run_id source:** Use extraction batch timestamp in ISO format: `f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"`. Pass from extraction entry point through to `_add_relationship()` via context object or parameter.

```sql
-- Simplified upsert (NULL-safe confidence handling):
ON CONFLICT (subject_entity_id, predicate, object_entity_id) DO UPDATE SET
  occurrence_count = koi_relationships.occurrence_count + 1,
  last_seen_at = now(),
  last_doc_rid = EXCLUDED.last_doc_rid,
  last_run_id = EXCLUDED.last_run_id,
  confidence = COALESCE(
    GREATEST(koi_relationships.confidence, EXCLUDED.confidence),
    koi_relationships.confidence,
    EXCLUDED.confidence
  )
```

**Predicate Hygiene (enforce now, before FIX-007):**
```sql
-- Add CHECK constraint for lowercase snake_case predicates
ALTER TABLE koi_relationships
ADD CONSTRAINT koi_relationships_predicate_format
CHECK (predicate ~ '^[a-z0-9_]+$');
```

Normalize in code before insert:
```python
def normalize_predicate(predicate: str) -> str:
    """Strip URI prefix, convert to lowercase snake_case."""
    pred = predicate.split('#')[-1].split('/')[-1]
    pred = re.sub(r'(?<!^)(?=[A-Z])', '_', pred).lower()
    pred = re.sub(r'[^a-z0-9_]', '_', pred)
    return pred
```

**Entity Resolution API:**

Update `_get_or_create_entity_by_name()` to return a struct with both URI and entity_id:
```python
@dataclass
class ResolvedEntity:
    uri: URIRef
    entity_id: int
    tier: str  # 'tier1', 'tier2', 'tier3', 'created'

def _get_or_create_entity_by_name(self, name: str, entity_type: str) -> ResolvedEntity:
    # All resolution paths (cache, tier1, tier2, tier3, create) return this struct
    # Eliminates extra lookups in _add_relationship()
    ...
```

**Workflow:**
1. **Ingestion:** In `graph_integration.py:_add_relationship()`, get `ResolvedEntity` for subject/object, normalize predicate, upsert into `koi_relationships`
2. **Export:** Extend `regenerate_fuseki_graph.py` to export edges from `koi_relationships`
3. **Parity:** Add script to verify entity URIs exist in Fuseki, counts match, no namespace drift

**⚠️ Staging Fuseki Safety Requirements:**
Before any rebuild, ensure `regenerate_fuseki_graph.py` has:
1. **Separate staging endpoint:** `FUSEKI_STAGING_ENDPOINT` env var pointing to different dataset (e.g., `/koi-staging`)
2. **Endpoint logging:** Print target endpoint at script start: `print(f"Target Fuseki: {endpoint}")`
3. **Safety latch:** Require explicit `--confirm-prod` flag to write to production endpoint; abort if targeting prod without flag
4. **Dataset isolation:** Staging dataset must be separate from `/koi` production dataset

```python
# Example safety latch in regenerate_fuseki_graph.py
import sys
STAGING = os.environ.get('KOI_STAGING', 'false').lower() == 'true'
ENDPOINT = os.environ.get('FUSEKI_STAGING_ENDPOINT') if STAGING else os.environ.get('FUSEKI_ENDPOINT')

print(f"🎯 Target Fuseki endpoint: {ENDPOINT}")
print(f"📍 Mode: {'STAGING' if STAGING else 'PRODUCTION'}")

if not STAGING and '--confirm-prod' not in sys.argv:
    print("❌ ERROR: Production target requires --confirm-prod flag")
    sys.exit(1)
```

---

#### Q4: Dedup Threshold Tuning

**Decision:** Yes to sampling, with adjusted experiment design

**Experiment Design:**
- Use 50 duplicate **clusters** (not just 50 entities) from E015-E018/E122-E143
- Add 50 "hard negative" pairs (nearest-neighbor but known-not-duplicate)
- Test deterministic alias rules first (handles, punctuation, known canonicals)
- Then sweep semantic thresholds

**Two-Band Merge Policy:**
- **Auto-merge:** Only at very conservative cutoff (≥0.95)
- **Review queue:** Send 0.90–0.95 to merge-review queue
- **No merge:** Below 0.90

**Goal:** Zero false merges on sampled hard negatives; rely on review for gray zone

---

#### Q5: Domain Type Prioritization

**Decision:** Phased rollout with conditional adjustments

| Phase | Types | Condition |
|-------|-------|-----------|
| Phase 1 | `CREDIT_CLASS`, `GOVERNANCE_PROPOSAL` | Core Regen domain |
| Phase 2 | `VALIDATOR`, `API_MESSAGE`, `MODULE` | If chain/dev docs are polluting graph |
| Phase 3 | `LICENSE`, `KEEPER`, `PROCESS`, `MATERIAL` | Optional refinement |

**Note:** If Msg/keeper-like items are noise, consider filtering them OUT rather than modeling as first-class entities.

---

#### Q6: Migration Timing

**Decision:** Hybrid approach with adjusted P0 execution

**Execution Order:**
1. **Early Stage 4:** Create `koi_relationships` table + write-path (per Q3 timing)
2. **P0 (FIX-001):** Implement namespace/type fixes in code/scripts, then rebuild Fuseki
3. **P1-P2 (FIX-002 through FIX-005):** Code changes only, NO data migration yet
4. **After P1-P2:** Single re-extraction pass with new pipeline
5. **P3-P4 (FIX-006 through FIX-008):** Run after re-extraction when data is clean

**Critical:** Don't start "Fuseki derived from PG" rebuild until relationship persistence (Q3) exists—otherwise you rebuild nodes but lose edges.

**⚠️ Note on SQL/SPARQL Migration Blocks in FIX-002/003/007:**
The "Data Migration" sections in FIX-002, FIX-003, and FIX-007 contain SQL/SPARQL scripts. These are labeled **"OPTIONAL: Only if not doing full re-extraction"**. Since we ARE doing a full re-extraction after P1-P2, these scripts are for reference/debugging only—the re-extraction will naturally produce clean data with the new pipeline.

---

### Quality Gate (New Recommendation)

Add a post-extraction quality gate that fails the pipeline if thresholds regress:

| Check | Threshold | Action |
|-------|-----------|--------|
| HTTP namespace triples | 0 | Fail if any |
| ENTITY with empty metadata | < previous count | Fail if increases |
| Distinct predicates | < 100 | Warn if exceeds |
| Self-referential triples | 0 | Fail if any |
| Type case inconsistency | 0 | Fail if mixed case |
| **ontology# type IRIs** | **0** | **Fail if any (Q1 conformance)** |
| **ontology# predicates** | **0** | **Fail if any (Q1 conformance)** |
| **Predicate format violations** | **0** | **Fail if not lowercase snake_case** |

**Implementation:** SQL/SPARQL checks run after each extraction batch, block merge to main if regressions detected.

**Quality Gate SPARQL Queries:**
```sparql
# Check 1: No HTTP URIs in subjects, predicates, or objects
SELECT (COUNT(*) AS ?count) WHERE {
  ?s ?p ?o .
  FILTER(
    CONTAINS(STR(?s), "http://regen.network/") ||
    CONTAINS(STR(?p), "http://regen.network/") ||
    (ISIRI(?o) && CONTAINS(STR(?o), "http://regen.network/"))
  )
}
# Expected: 0

# Check 2: Only UPPERCASE types
SELECT DISTINCT ?type WHERE {
  ?s a ?type .
  FILTER(CONTAINS(STR(?type), "koi#"))
  FILTER(STR(?type) != UCASE(STR(?type)))
}
# Expected: 0 results

# Check 3: No self-referential triples
SELECT (COUNT(*) AS ?count) WHERE { ?s ?p ?s }
# Expected: 0

# Check 4: No ontology# type IRIs (Q1 conformance)
SELECT DISTINCT ?type WHERE {
  ?s a ?type .
  FILTER(CONTAINS(STR(?type), "ontology#"))
}
# Expected: 0 results

# Check 5: No ontology# predicates (Q1 conformance)
SELECT DISTINCT ?p WHERE {
  ?s ?p ?o .
  FILTER(CONTAINS(STR(?p), "ontology#"))
}
# Expected: 0 results
```

**Quality Gate SQL Queries:**
```sql
-- Check 6: No HTTP in fuseki_uri
SELECT COUNT(*) FROM entity_registry WHERE fuseki_uri LIKE 'http://%';
-- Expected: 0

-- Check 7: No mixed-case types
SELECT entity_type, COUNT(*) FROM entity_registry
WHERE entity_type != UPPER(entity_type) GROUP BY entity_type;
-- Expected: 0 rows

-- Check 8: Predicate format (after koi_relationships populated)
SELECT predicate FROM koi_relationships WHERE predicate !~ '^[a-z0-9_]+$';
-- Expected: 0 rows
```

---

### Verification Checklist

- [x] All 17 findings (F1-F17) addressed in fix plans
- [x] Priority matrix with rationale
- [x] Each fix includes: affected files, implementation steps, code changes, data migration, testing plan, rollback plan, estimated impact
- [x] Dependencies between fixes documented
- [x] Summary includes total error counts (391)
- [x] All 6 questions resolved with decisions documented
- [x] Dependency graph included
- [x] Quality gate with 8 checks (5 SPARQL + 3 SQL)
- [x] Entity resolution API defined (ResolvedEntity struct)
- [x] Predicate hygiene enforced (normalize + CHECK constraint)
- [x] Q1 conformance checks added (ontology# type/predicate detection)

---

## Stage 4: Implementation

### FIX-001 + Relationship Persistence Implementation (2025-12-21)

**Objective:** Implement foundational fixes required by all other fixes:
1. Relationship persistence table + write-path (Q3 prerequisite)
2. Namespace/Type URI conventions (FIX-001)
3. Staging rebuild with quality gate

**Implementation Status: COMPLETE ✓**

Staging validation and production rebuild completed 2025-12-21.

#### Part 1: koi_relationships Table

| Component | Status | Notes |
|-----------|--------|-------|
| Table creation with CHECK constraints | COMPLETE | `koi_relationships_no_self`, `koi_relationships_predicate_format` |
| Indexes (subject, object, predicate, last_doc) | COMPLETE | 4 indexes created |
| UNIQUE constraint on triple | COMPLETE | `(subject_entity_id, predicate, object_entity_id)` |
| FK references to entity_registry | COMPLETE | ON DELETE CASCADE |

#### Part 2: Code Changes

| File | Change | Status |
|------|--------|--------|
| `src/knowledge_graph/models.py` | Created `ResolvedEntity` dataclass | COMPLETE |
| `src/knowledge_graph/entity_resolver.py` | Updated all tiers to return `entity_id` | COMPLETE |
| `src/knowledge_graph/graph_integration.py` | Added `normalize_predicate()` function | COMPLETE |
| `src/knowledge_graph/graph_integration.py` | Added `_resolve_entity_for_relationship()` method | COMPLETE |
| `src/knowledge_graph/graph_integration.py` | Updated `_add_relationship()` with PG write-path | COMPLETE |
| `src/knowledge_graph/graph_integration.py` | Added `DIRECT_FUSEKI_WRITES_ENABLED = False` guard | COMPLETE |
| `src/knowledge_graph/graph_integration.py` | Updated `integrate_document()` with doc_rid/run_id | COMPLETE |
| `src/core/namespaces.py` | Created shared namespace module (HTTPS, koi#) | COMPLETE |
| `scripts/regenerate_fuseki_graph.py` | Added env wiring, safety latch, relationship export | COMPLETE |

#### Part 3: Quality Gate Results (Pre-Rebuild)

**PostgreSQL Checks:**
```
Check 1: koi_relationships table structure - PASS (all constraints present)
Check 2: No HTTP in fuseki_uri - PASS (http_count = 0)
Check 3: No mixed-case types - PASS (0 rows)
Check 4: Predicate format compliance - PASS (0 violations, table empty)
```

**Fuseki Pre-Rebuild State (Issues to be fixed by regeneration):**
```
Check 5: HTTP namespace triples - FAIL (155,097 triples with http://)
Check 6: ontology# type URIs - FAIL (20+ types using ontology#)
Check 7: Self-referential triples - FAIL (13 triples)
Total triples: 162,754
```

#### Part 4: Staging + Production Rebuild (2025-12-21)

**Staging Rebuild:**
```
Entities exported: 28,561
Relationships exported: 0 (table empty, expected)
Triples created: 142,805
RDF file: 7.13 MB
```

**Staging Quality Gates (All PASS):**
| Check | Result |
|-------|--------|
| HTTP namespace triples | 0 ✓ |
| ontology# type URIs | 0 ✓ |
| ontology# predicates | 0 ✓ |
| Self-referential triples | 0 ✓ |
| Types UPPERCASE | 12 types, all UPPERCASE ✓ |

**Production Rebuild:**
```
Started: 2025-12-21T07:43:28
Completed: 2025-12-21T07:43:48
Triples: 142,805 (matched staging)
```

**Production Quality Gates (All PASS):**
| Check | Before | After |
|-------|--------|-------|
| HTTP namespace triples | 155,097 | 0 ✓ |
| Self-referential triples | 13 | 0 ✓ |
| ontology# types | 20+ | 0 ✓ |

#### DIRECT_FUSEKI_WRITES_ENABLED Status

**Current Setting:** `False` (and should remain off)

**Architectural Decision:** PostgreSQL is the authoritative store; Fuseki is derived via regeneration.

This guard prevents live ingestion from writing directly to Fuseki:
- Relationships are persisted to `koi_relationships` only
- Entities are persisted to `entity_registry` only
- Fuseki is rebuilt from PostgreSQL via `regenerate_fuseki_graph.py`

**⚠️ Re-enabling direct writes is a SEPARATE follow-up task ("dual-write parity"):**
- Only consider re-enabling if `graph_integration.py` emits the exact same URI/type/predicate conventions as the regenerator
- Otherwise, direct writes will immediately reintroduce `ontology#` types and other drift
- For now, treat PG→Fuseki export as the only path to Fuseki

#### Backup

```
/home/darren/backups/eliza_pre_fix001_20251221_012218.sql.gz (837 MB)
```

#### Stage 4 Completion Notes

All steps completed 2025-12-21:
- ✓ Staging dataset created
- ✓ Staging rebuild passed all quality gates
- ✓ Production rebuild completed
- ✓ Production quality gates verified

**Commands Used:**
```bash
# Staging rebuild
KOI_STAGING=true FUSEKI_USER=admin FUSEKI_PASSWORD=admin \
  python scripts/regenerate_fuseki_graph.py

# Production rebuild
FUSEKI_USER=admin FUSEKI_PASSWORD=admin \
  python scripts/regenerate_fuseki_graph.py --confirm-prod
```

**Note:** `DIRECT_FUSEKI_WRITES_ENABLED` remains `False`. Re-enabling is a separate "dual-write parity" task.

### Fixes Implemented

| Fix ID | Status | Branch/Commit | Date Completed | Notes |
|--------|--------|---------------|----------------|-------|
| FIX-001 | DEPLOYED | regen-prod `e60e7b77` (/opt/projects/koi-processor) | 2025-12-21 | Namespace/Type URI conventions + production rebuild |
| Q3 (Relationships) | DEPLOYED | regen-prod `e60e7b77` (/opt/projects/koi-processor) | 2025-12-21 | koi_relationships table + write-path |
| FIX-002 | DEPLOYED | regen-prod `e60e7b77` (/opt/projects/koi-processor) | 2025-12-21 | Extractor unification + prompt hardening |
| FIX-002 (Gemini) | DEPLOYED | regen-prod `e60e7b77` (/opt/projects/koi-processor) | 2025-12-21 | Gemini extractor unified with FIX-002 (Gemini 3 SDK) |
| FIX-003 | DEPLOYED | regen-prod `e60e7b77` (/opt/projects/koi-processor) | 2025-12-21 | Pipeline ordering (OntologyNormalizer → ListSplitter → EntityQualityFilter) + min-length/placeholder filters + relationship type inference (no ENTITY defaults) |
| FIX-004 | DEPLOYED | regen-prod `e60e7b77` (/opt/projects/koi-processor) | 2025-12-21 | Role/group detection upgrade (singular/plural + multi-word role patterns; Cosmos role terms blocked as PERSON) |
| FIX-005 | DEPLOYED | regen-prod `601ef9d1` (/opt/projects/koi-processor) | 2025-12-21 | Ontology granularity expansion (+10 types); removes legacy MODULE→PROJECT mapping; KG regression suite: 203 passed (server) |

**Test commands**
- Local: `cd koi-processor && PYTHONPATH=src pytest -q`
- Server: `cd /opt/projects/koi-processor && PYTHONPATH=src ./.venv/bin/pytest -q`

---

## Stage 5: Targeted Testing

Testing must verify TWO things:
1. **Error fixes work** - The identified errors are resolved
2. **Correct data preserved** - Existing correct entities, relationships, and sub-graphs are NOT broken by our changes

### Stage 5 Results (2025-12-21)

#### Test Results Summary

| Part | Test | Result | Notes |
|------|------|--------|-------|
| 1 | Entity count match | ✅ PASS | PG: 28,561 = Fuseki: 28,561 (diff=0) |
| 2 | Type distribution match | ✅ PASS | All 12 types match exactly |
| 3 | Relationship persistence | ✅ PASS | Test triple verified, URIs correct |
| 4 | Referential integrity | ✅ PASS | See corrected analysis below |
| 5 | RAG API | ⚠️ NOT VERIFIED | API not running at test time |
| 6 | Sample entities (5) | ✅ PASS | All labels, types, URIs verified |

#### Part 4 Corrected Analysis: koi_entity_chunk_links

**Key Finding:** Entity boost uses `entity_name_lower` for search, NOT `entity_uri`. The legacy gaia.ai URIs in `entity_uri` are unused.

```
Table columns:
- entity_name_lower: Used for entity search ✓
- entity_uri: Contains legacy https://gaia.ai/id/... (unused for search)
- chunk_rid: Links to koi_memories.id
```

**Actual Matching:**
```sql
-- Entities matched by name (functional for entity boost)
SELECT COUNT(*) FROM koi_entity_chunk_links ecl
JOIN entity_registry er ON LOWER(ecl.entity_name) = LOWER(er.entity_text);
-- Result: 596,735 / 614,021 (97.2% match)

-- True orphans (entity names not in registry)
-- Result: ~17,286 (2.8%)
```

**Conclusion:** Entity boost is FUNCTIONAL because the join is by name, not URI. The `entity_uri` column with gaia.ai URIs is vestigial.

#### Baseline Metrics (for Stage 6 Comparison)

| Metric | Value |
|--------|-------|
| entity_registry count | 28,561 |
| Distinct entity_type count | 12 |
| koi_relationships count | 0 |
| koi_entity_chunk_links count | 614,021 |
| Fuseki triple count | 142,805 |

**Full Type Distribution:**

| Type | Count |
|------|-------|
| ENTITY | 15,558 |
| CLAIM | 7,828 |
| PERSON | 1,460 |
| PROJECT | 1,358 |
| ORGANIZATION | 1,106 |
| TECHNOLOGY | 490 |
| LOCATION | 291 |
| EVENT | 187 |
| CONCEPT | 141 |
| EVIDENCE | 77 |
| FUNCTION | 42 |
| QUESTION | 23 |

#### Recommendation

✅ **Proceed to Stage 6** (Re-extraction with LLM model comparison)

**Deferred items:**
- RAG API verification (needs API to be started)
- `entity_uri` column migration (cosmetic, not functional)

### Test Documents (Error Fixes)

| Error ID | Test Document(s) | Before Fix | After Fix | Status |
|----------|------------------|------------|-----------|--------|
| E001 | | | | |
| E002 | | | | |
| E003 | | | | |

### Regression Testing (Correct Data Preservation)

Before making fixes, we need to identify and document correct sub-graphs/data that MUST be preserved:

#### Known-Good Entities (must remain intact)

| Entity | Type | Key Relationships | Source Document(s) | Verified Post-Fix |
|--------|------|-------------------|-------------------|-------------------|
| | | | | |

#### Known-Good Relationships (must remain intact)

| Relationship | Source Document(s) | Verified Post-Fix |
|-------------|-------------------|-------------------|
| | | |

#### Known-Good Sub-Graphs (must remain intact)

| Sub-Graph Description | Key Nodes | Key Edges | Verified Post-Fix |
|----------------------|-----------|-----------|-------------------|
| | | | |

### Testing Strategy

1. **Before any fixes:** Snapshot/document known-good data from the current graph
2. **After each fix:** Run targeted extraction on affected documents
3. **Verify error fixed:** Check that the specific error no longer appears
4. **Verify no regression:** Check that all known-good data is still present and correct
5. **Document results:** Update tables above with pass/fail status

### Testing Commands

```bash
# NOTE: This repo has legacy test files that require optional components (audio pipeline, quality control)
# and/or a running local Postgres instance. Use the targeted KG regression suite below for fast feedback.

# Targeted KG regression suite (no network, no Postgres required)
cd /Users/darrenzal/projects/RegenAI/koi-processor
PYTHONPATH=src pytest -q \
  tests/test_fix002_extractor_contract.py \
  tests/test_pipeline_modules.py \
  tests/test_fix003_entity_validation.py \
  tests/test_fix004_role_detection.py

# On the production server, `pytest` may not be on PATH; use the repo venv entrypoint:
# (Create once: `python3 -m venv .venv && .venv/bin/pip install -U pytest rdflib SPARQLWrapper google-genai`)
# PYTHONPATH=src ./.venv/bin/pytest -q <same test list>

# Full test collection may fail due to legacy/optional tests. Avoid running pytest from repo root without
# explicitly selecting a test subset.

# Example: Query to verify entity still exists with correct type
# Example: Query to verify relationship still exists
# Example: Diff command to compare sub-graph before/after
```

### Regression Test Failures

If a fix causes regression (breaks correct data), document here:

| Fix ID | Regression Description | Correct Data Affected | Resolution |
|--------|----------------------|----------------------|------------|
| | | | |

---

## Stage 6: Full Re-Extraction

### Current Run (Docs-Only Corpus)

**Corpus definition (docs-only):**
- Include: Discourse + Notion + Website + other non-repo sources
- Include (repo sources): GitHub + GitLab docs only via `metadata.file_path`
  - Extensions: `.md`, `.mdx`, `.rst`, `.txt`
  - Allow `README*`, `LICENSE*`, `CHANGELOG*`, and `/docs/` paths
- Exclude: repo rows where `file_path` is NULL (issues/PRs), plus generated/vendor/test/example paths

**Expected corpus size:** ~11,195 docs (docs-only filter)

**Backup:** `/home/darren/backups/eliza_pre_stage6_docs_only_20251221_220548.sql.gz`

**Canary (10 docs):** PASS (HTTP URIs = 0, relationships > 0, docs-only file paths)

**Full run:** ✅ COMPLETE
- **Run ID:** `20251222_031114`
- **Start:** 2025-12-22T03:11:14Z
- **End:** 2025-12-23T05:36:00Z
- **Log:** `/home/darren/stage6_full_run.log`

**Final Results:**
| Metric | Initial Run | Reprocessing | Total |
|--------|-------------|--------------|-------|
| Documents | 10,953 | 1,049 | 12,002 |
| Errors | 639 → 0 | 0 | 0 |
| Entities | 80,007 | 8,315 | 88,322 |
| Relationships | 16,071 | 1,258 | 17,329 |
| Runtime | ~14 hrs | 80.7 min | ~15.3 hrs |

**Reprocessing Note:** Initial run had 639 errors (99% due to PostgreSQL "Connection refused" - max_connections exhausted). Fixed by bumping max_connections 200→400 and adding retry logic. All 1,049 missing documents successfully reprocessed on 2025-12-23.

### LLM Model Comparison (Pre-Extraction)

Before the full re-extraction, compare extraction quality between LLM models to select the best option.

**Current Model:** `gpt-4.1-mini`

**Candidate Model:** `gemini-3-flash-preview` (Batch API available for 50% cost savings)

#### Test Protocol

1. **Select test set:** 100 representative documents across source types (Discourse, GitHub, Web, Notion)
2. **Run extraction with both models** on identical documents
3. **Evaluate on three dimensions:**

| Dimension | Metrics | Weight |
|-----------|---------|--------|
| **Quality** | Entity accuracy, type correctness, relationship validity, false positive rate | 50% |
| **Cost** | $ per 1K documents, Batch API discount if applicable | 30% |
| **Speed** | Documents per minute, latency | 20% |

4. **Document results** in comparison table below
5. **Select winner** for full re-extraction

#### Model Comparison Results

**Test Date:** 2025-12-21
**Test Set:** 100 documents (25 each: forum-post, github, notion, website)
**Test Duration:** 1841 seconds (~31 minutes)

| Metric | gpt-4.1-mini | gemini-3-flash-preview | Winner |
|--------|--------------|------------------------|--------|
| Entity pass rate | 98.4% | 99.4% | Gemini |
| JSON parse errors | 13 docs (truncation) | 0 docs | Gemini |
| API errors | 1 (520 server error) | 0 | Gemini |
| Cost per 1K docs | **$1.08** | $1.75 | OpenAI |
| Input pricing (per 1M) | $0.40 | $0.50 | OpenAI |
| Output pricing (per 1M) | $1.60 | $3.00 | OpenAI |
| Batch API available? | Yes (50% off) | Yes (50% off) | Tie |
| Docs per minute | ~3.3 | ~3.3 | Tie |
| **Overall Winner** | | ✅ | **Gemini** |

**Decision Logic:**
- Gemini has higher quality (99.4% vs 98.4% pass rate)
- Zero parse errors vs 13 truncation issues for OpenAI
- Full extraction cost: ~$54 for 30,904 docs (covered by $300 free Gemini credits)
- Current Stage 6 run is docs-only (~11.2k docs), so expected cost is lower

#### Observations

**OpenAI Issues:**
- 13/100 documents had JSON truncation (unterminated strings)
- 1 transient 520 server error
- Truncation appears to be max_tokens limit, not content-related

**Gemini Observations:**
- Zero parse errors with safety filters disabled
- Higher output token usage (hence higher cost)
- More verbose entity extraction

#### Notes

- Both models support Batch API for 50% cost reduction (24hr SLO)
- With Batch API: OpenAI ~$0.54/1K, Gemini ~$0.87/1K
- Safety filters MUST be disabled for Gemini on Regen/BioFi content

#### Future: If Using gpt-4.1-mini

If OpenAI is used for extraction in the future:
1. **Increase `max_output_tokens`** to reduce truncation (currently seeing 13% truncation rate)
2. **Consider Batch API** for 50% cost savings if latency isn't critical

---

### Pre-Extraction Checklist

- [x] All identified errors have fixes implemented
- [x] All fixes tested on targeted subsets
- [x] All regression tests pass (known-good data preserved)
- [x] No regression test failures unresolved
- [x] koi-processor updated with all fixes
- [x] **LLM model comparison completed** (see above) and winner selected: **gemini-3-flash-preview**
- [x] Server environment prepared
- [x] Backup of current graph taken
- [x] Docs-only corpus filter applied (GitHub/GitLab markdown only, file_path required)
- [x] Canary run passed (10 docs)
- [ ] Known-good sub-graphs documented for post-extraction verification

### Extraction Log

**Date:** 2025-12-22 → 2025-12-23
**Run ID:** 20251222_031114
**Start:** 2025-12-22T03:11:14Z
**End:** 2025-12-23T05:36:00Z
**Status:** ✅ COMPLETE
**Documents Processed:** 12,002 (10,953 initial + 1,049 reprocessed)
**Entities Extracted:** 88,322 (80,007 + 8,315)
**Relationships Extracted:** 17,329 (16,071 + 1,258)
**Issues Encountered:** 0 (all 639 initial errors resolved via reprocessing)

### Next Steps (After Completion)

1. Post-extraction verification (entity counts, type distribution, ENTITY drop, HTTP URIs = 0)
2. Rebuild Fuseki from PostgreSQL (staging then production)
3. Entity-level code linking (`link_entities_to_code.py`)
4. Stub sync to AGE with mark/sweep and MENTIONS edges (`sync_stubs_to_age.py`)
5. Update baseline metrics and Stage 6 results in this document
6. Proceed with P3/P4 fixes on clean data: FIX-006 → FIX-007 → FIX-008

### Post-Extraction Verification

After full re-extraction, verify:

#### Automated Verification (PostgreSQL)

**Run ID:** `20251222_031114`
**Snapshot Date:** 2025-12-23 (before Fuseki rebuild)

| Metric | Value |
|--------|-------|
| `entity_registry` (unique entities) | 30,041 |
| `ENTITY` type count | 0 |
| `koi_relationships` (unique relationships) | 15,757 |
| Distinct predicates | 3,303 |
| HTTP `fuseki_uri` | 0 |
| Mixed-case `entity_type` | 0 |
| Predicate format violations | 0 |
| Self-referential relationships | 0 |

**Type Distribution:**

| Type | Count |
|------|-------|
| CONCEPT | 14,059 |
| TECHNOLOGY | 4,965 |
| PROCESS | 2,255 |
| PROJECT | 1,833 |
| ORGANIZATION | 1,731 |
| PERSON | 981 |
| CLAIM | 598 |
| API_MESSAGE | 513 |
| STANDARD | 502 |
| GOVERNANCE_PROPOSAL | 498 |
| LOCATION | 420 |
| MATERIAL | 265 |
| EVIDENCE | 261 |
| EVENT | 243 |
| QUESTION | 235 |
| VALIDATOR | 221 |
| CREDIT_CLASS | 177 |
| MODULE | 137 |
| LICENSE | 116 |
| KEEPER | 31 |

**Note:** Stage 6 run metrics (88,322 entities / 17,329 relationships) reflect total per-document extractions; `entity_registry` / `koi_relationships` are de-duplicated unique rows across the corpus.

#### Error Fixes Verified

| Error ID | Fixed? | Notes |
|----------|--------|-------|
| E001 | ✅ | Regen Network now typed as `ORGANIZATION` |
| E029 | ✅ | `ENTITY` fallback eliminated (`ENTITY` count = 0) |
| E205 | ✅ | Relationships now persisted (`koi_relationships` count = 15,757) |
| E206 | ✅ | Predicate format normalized + FIX-007 canonicalization applied (3,303 → 1,501 predicates) |

#### Known-Good Data Preserved

**Verification Date:** 2025-12-23 (Post-Staging Rebuild)

| Sub-Graph/Entity | Preserved? | Sample Predicates | Notes |
|-----------------|------------|-------------------|-------|
| Regen Network | ✅ | `adopts CosmWasm`, `administers Regen Registry`, `aims_to regenerate earth` | 30+ meaningful relationships |
| Regen Ledger | ✅ | `built_on Cosmos SDK`, `bridges_to Celo`, `anchors Regen Registry` | Core blockchain entity with proper tech relationships |
| Gregory Landua | ✅ | `advises Regen Network`, `attended Climate Week`, `associated_with Regen Foundation` | Person entity with expected professional/org relationships |
| Regen Registry | ✅ | `built_on Regen Ledger`, `anchors_data_in Regen Ledger`, `alternative_to Verra` | Registry entity with proper program relationships |
| Credit Classes (C01, BIO01) | ✅ | `is_a Credit Class`, `managed_by Regen Registry`, `located_in US-WA` | Credit class entities with expected schema |
| Governance Proposals | ✅ | `targets Regen Ledger`, `upgrades Regen Ledger`, `proposes_reduction_of max inflation` | Governance entities with proper action predicates |
| Validators (01node, 0base.vc) | ✅ | `validates Cosmos`, `participates_in Regen Network`, `operates_on Cosmos` | Validator entities with expected network relationships |

#### New Issues Discovered

| Issue | Description | Action Needed |
|-------|-------------|---------------|
| ~~Predicate explosion~~ | ~~3,303 distinct predicates in `koi_relationships`~~ | ✅ **RESOLVED** FIX-007 applied 2025-12-23: 3,303 → 1,501 predicates (54.6% reduction), 343 duplicate triples removed |
| Residual code-ish artifacts | 10 snake_case entities remain (e.g., `regen_graph_v2`) | Decide if these should be filtered or modeled; tighten `EntityQualityFilter` / dedup rules if needed |

---

### 2025-12-23 (FIX-007 Predicate Consolidation)

**Summary:** Applied FIX-007 predicate consolidation to PostgreSQL `koi_relationships` table, then rebuilt Fuseki staging.

**Results:**
| Metric | Before | After | Change |
|--------|--------|-------|--------|
| Unique Predicates | 3,303 | 1,501 | -54.6% |
| Total Relationships | 15,757 | 15,414 | -343 duplicates |
| Fuseki Triples | 165,962 | 165,619 | -343 |

**Quality Gates (Post-Consolidation):**
- Gate A (no http://regen.network/): ✅ 0
- Gate B1 (no ontology# types): ✅ 0
- Gate B2 (no ontology# predicates): ✅ 0
- Gate C (no self-ref triples): ✅ 0

**Key Consolidation Mappings Applied:**
- `utilizes` → `uses`
- `is_part_of` → `part_of`
- `integrates_with` → `interacts_with`
- `impacts` → `affects`
- `facilitates` → `enables`
- 1,805 total mappings applied

**Files Created:**
- `koi-processor/scripts/fix007_consolidate_predicates_postgres.py` - PostgreSQL-based consolidation script
- `/opt/projects/koi-processor/exports/predicate_consolidation_20251223.json` - Mapping log

---

### 2025-12-23 (Code↔Docs Bridge + AGE Sync)

**Summary:** Completed Code↔Docs bridge integration and synced stub nodes to Apache AGE graph database.

**Results:**
| Script | Result |
|--------|--------|
| `export_code_artifacts.py` | 16,820 code artifacts upserted |
| `link_docs_to_code.py` | 6,453 doc→code links created from 12,296 docs |
| `link_entities_to_code.py` | 241 entities linked (232 API_MESSAGE, 9 MODULE) |
| `sync_stubs_to_age.py` | 5,464 stub nodes + 6,463 edges synced to AGE |

**AGE Graph Contents:**
| Node Type | Count |
|-----------|-------|
| Person | 290 |
| Organization | 763 |
| CodeArtifact | 397 |
| Doc | 3,507 |
| Total Stubs | 5,464 |

| Edge Type | Count |
|-----------|-------|
| MENTIONS (doc→code) | 6,454 |
| CODE_REF (entity→code) | 9 |

**Bug Fixes Applied to `sync_stubs_to_age.py`:**
1. Added RealDictCursor for code_artifacts query
2. Removed multi-label MERGE syntax (AGE doesn't support `MERGE (n:Label1:Label2)`)
3. Changed batch_execute to run statements individually (avoids variable conflicts)

---

### 2025-12-23 (Production Deployment)

**Summary:** Promoted consolidated graph to production after all quality gates passed.

**Final Production State:**
| Endpoint | Triples | Predicates |
|----------|---------|------------|
| /koi (production) | 165,619 | 1,504 |
| /koi-staging | 165,619 | 1,504 |

**All Quality Gates Passing:**
- Gate A: No http://regen.network/ URIs
- Gate B1: No ontology# types
- Gate B2: No ontology# predicates
- Gate C: No self-referential triples

---

## Session Notes

### 2025-12-20 (Session 3: PERSON/HUMANACTOR Review)

**Focus:** Systematic review of all PERSON (1,460) and HumanActor (3,867) entities

**Database Queries Used:**
```sql
-- Top PERSON entities by occurrence
SELECT entity_text, occurrence_count FROM entity_registry
WHERE entity_type = 'PERSON' ORDER BY occurrence_count DESC LIMIT 100;

-- Find teams/groups mistyped as PERSON
SELECT entity_text, occurrence_count FROM entity_registry
WHERE entity_type = 'PERSON' AND (entity_text ILIKE '%team%' OR entity_text ILIKE '%lead%')
ORDER BY occurrence_count DESC;

-- Find AI/software mistyped as PERSON
SELECT entity_text, occurrence_count FROM entity_registry
WHERE entity_type = 'PERSON' AND (entity_text ILIKE '%claude%' OR entity_text ILIKE '%agent%')
ORDER BY occurrence_count DESC;

-- Find name duplicates
SELECT entity_text, occurrence_count FROM entity_registry
WHERE entity_type = 'PERSON' AND entity_text ILIKE '%will%' ORDER BY entity_text;
```

```sparql
-- Find all Person/HumanActor type variants
SELECT DISTINCT ?type (COUNT(?s) as ?count) WHERE {
  ?s a ?type .
  FILTER(CONTAINS(LCASE(str(?type)), "person") || CONTAINS(LCASE(str(?type)), "human"))
} GROUP BY ?type ORDER BY DESC(?count)
```

**Key Findings:**
1. **102 new errors identified** (E045-E146)
2. **Major type fragmentation:** 7 different URIs for person-related types in Fuseki
3. **22 duplicate person clusters** found (Will Szal, Sarah Bax, Aaron Craelius, etc.)
4. **12+ AI systems** incorrectly typed as PERSON (Claude, GPT, Eliza Agent, etc.)
5. **15+ roles/positions** incorrectly typed as PERSON (leads, operators, reviewers)
6. **16+ corporations** incorrectly typed as HumanActor (Coca-Cola, Microsoft, etc.)
7. **Concepts/countries** typed as HumanActor (Colombia, proof of stake, permaculture)
8. **Extraction artifacts:** blockchain addresses, repo paths, single letters, placeholders

**Pattern Analysis:**
- The HumanActor type appears to be a catch-all for anything that "acts" in documents
- PERSON type is being used for any named entity that isn't obviously something else
- Many forum usernames extracted alongside real names = duplicates
- GitHub commits/contributions cause software entities to be typed as people
- Podcast transcripts cause speakers/guests to be over-extracted

---

### 2025-12-20

- Started quality review process
- Manual review of structural view at https://regen.gaiaai.xyz/graph/#view=structural
- Identified 24 initial errors (E001-E024) across categories:
  - 14 incorrect entity types
  - 4 duplicate entity clusters
  - 1 duplicate/malformed relationship
  - 3 nonsensical relationships
  - 2 schema/ontology issues
- Database exploration revealed additional systematic issues (E025-E030):
  - Type case inconsistencies (Claim vs CLAIM vs claim)
  - Namespace inconsistencies (koi# vs ontology#)
  - Entities with multiple conflicting types
  - PostgreSQL/Fuseki data inconsistencies
  - 15,558 entities using generic "ENTITY" type
  - 10+ Gregory Landua duplicates found via database query
- Added Database Access Guide with PostgreSQL and SPARQL query examples
- Created agent review prompt for systematic graph review
- **Next steps:** Run agents to review remaining entity types and find additional errors

---

## Resources

- **Graph Visualization:** https://regen.gaiaai.xyz/graph
- **Server Access:** `ssh darren@202.61.196.119`
- **koi-processor Repo (local):** `/Users/darrenzal/projects/RegenAI/koi-processor`
- **koi-processor Repo (server):** `/opt/projects/koi-processor`

---

## Database Access Guide

The knowledge graph data is stored in two databases:

### 1. PostgreSQL (Primary Entity Storage)

**Connection:**
```bash
ssh darren@202.61.196.119
docker exec -it gaia-postgres-1 psql -U postgres -d eliza
```

**Key Tables:**
- `entity_registry` - Main entity table with types and embeddings
- `relationships` - Relationship data
- `koi_*` tables - Various KOI processing data

**Useful PostgreSQL Queries:**

```sql
-- Count entities by type
SELECT entity_type, COUNT(*) as count
FROM entity_registry
GROUP BY entity_type
ORDER BY count DESC;

-- Search for entities by name
SELECT entity_text, entity_type, occurrence_count
FROM entity_registry
WHERE entity_text ILIKE '%search_term%'
ORDER BY occurrence_count DESC
LIMIT 50;

-- Find potential duplicates (similar names)
SELECT entity_text, entity_type, occurrence_count
FROM entity_registry
WHERE entity_text ILIKE '%gregory%'
ORDER BY entity_text;

-- Get entities of a specific type
SELECT entity_text, occurrence_count, metadata
FROM entity_registry
WHERE entity_type = 'CLAIM'
ORDER BY occurrence_count DESC
LIMIT 100;
```

### 2. Apache Jena Fuseki (RDF Triple Store)

**Connection:**
```bash
ssh darren@202.61.196.119
# Query via curl
curl -s 'http://localhost:3030/koi/query' \
  -H 'Accept: application/sparql-results+json' \
  --data-urlencode 'query=YOUR_SPARQL_QUERY'
```

**Dataset:** `koi` (accessible at `http://localhost:3030/koi`)

**Useful SPARQL Queries:**

```sparql
# Prefixes to use
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX koi: <http://regen.network/koi#>

# Count entities by type
SELECT DISTINCT ?type (COUNT(?s) as ?count)
WHERE { ?s a ?type }
GROUP BY ?type
ORDER BY DESC(?count)

# Search for entities by label
SELECT ?s ?type ?label
WHERE {
  ?s rdfs:label ?label .
  ?s a ?type .
  FILTER(CONTAINS(LCASE(str(?label)), "search_term"))
}
LIMIT 50

# Get all relationships (predicates) and counts
SELECT DISTINCT ?p (COUNT(*) as ?count)
WHERE { ?s ?p ?o }
GROUP BY ?p
ORDER BY DESC(?count)

# Find entities with multiple types
SELECT ?s (GROUP_CONCAT(DISTINCT ?type; separator=", ") as ?types) (COUNT(DISTINCT ?type) as ?typeCount)
WHERE { ?s a ?type }
GROUP BY ?s
HAVING (COUNT(DISTINCT ?type) > 1)
ORDER BY DESC(?typeCount)
LIMIT 100

# Find relationships for a specific entity
SELECT ?p ?o
WHERE {
  ?s rdfs:label "Entity Name" .
  ?s ?p ?o
}
```

**Example curl commands:**

```bash
# Count by type
curl -s 'http://localhost:3030/koi/query' \
  -H 'Accept: application/sparql-results+json' \
  --data-urlencode 'query=SELECT DISTINCT ?type (COUNT(?s) as ?count) WHERE { ?s a ?type } GROUP BY ?type ORDER BY DESC(?count) LIMIT 30'

# Search for Gregory
curl -s 'http://localhost:3030/koi/query' \
  -H 'Accept: application/sparql-results+json' \
  --data-urlencode 'query=PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> SELECT ?s ?type ?label WHERE { ?s rdfs:label ?label . ?s a ?type . FILTER(CONTAINS(LCASE(str(?label)), "gregory")) } LIMIT 30'
```

### Current Graph Statistics (as of 2025-12-20)

**PostgreSQL entity_registry:**
| Type | Count |
|------|-------|
| ENTITY | 15,558 |
| CLAIM | 7,828 |
| PERSON | 1,460 |
| PROJECT | 1,358 |
| ORGANIZATION | 1,106 |
| EVIDENCE | 1,033 |
| QUESTION | 201 |
| CONCEPT | 12 |

**Fuseki RDF (showing type inconsistencies):**
| Type URI | Count |
|----------|-------|
| koi#Claim | 19,698 |
| koi#CLAIM | 9,156 |
| koi#HumanActor | 3,867 |
| koi#Evidence | 3,796 |
| koi#Question | 2,376 |
| koi#CONCEPT | 2,085 |
| koi#PROJECT | 2,047 |
| koi#PERSON | 1,902 |
| koi#Project | 1,582 |
| koi#ORGANIZATION | 1,387 |
| koi#Organization | 1,331 |
| koi#EVIDENCE | 1,184 |
| koi#Concept | 888 |
| koi#Person | 704 |
| ontology#Claim | 689 |
| ... | ... |

**Note:** The case inconsistencies (Claim vs CLAIM, Person vs PERSON) and namespace inconsistencies (koi# vs ontology#) are MAJOR issues that need to be fixed.

---

## Data Provenance

### Current Data Origin

**All current data is from the December 2025 re-extraction** - there is NO old/stale data from previous extractions.

**Evidence:**
- `entity_registry.first_seen_at` range: `2025-12-11 00:10` to `2025-12-12 05:02`
- All entities were created during this window

### Extraction History

| Date | Event | Notes |
|------|-------|-------|
| 2025-11-27 | Pre-ontology migration backup | `eliza_pre_ontology_migration_20251127_104903.dump` |
| 2025-12-08 | Re-extraction plan created | `docs/planning/RE_EXTRACTION_PLAN.md` |
| 2025-12-10 | Re-extraction completed | `reextraction_report_20251210.md` - entities: 6,858 → 15,242 |
| 2025-12-11 | Phase 2 backup created | `eliza_pre_phase2_20251211_192720.sql.gz` |
| 2025-12-11-12 | Phase 2 import | All current entity_registry data from this period |
| 2025-12-12 | Fuseki backups | `fuseki-db-20251212_023204.tar.gz` |

### Key Implication

**The errors we're finding are from the CURRENT extraction pipeline**, not legacy data. This means:
1. Fixes need to be made in the extraction/post-processing code
2. No need to worry about cleaning up old data - it doesn't exist
3. After fixing the pipeline, we'll need another full re-extraction

### Backup Locations (on server)

```
/home/darren/backups/
├── eliza_pre_ontology_migration_20251127_104903.dump (485MB)
├── eliza_pre_phase2_20251211_192720.sql.gz (767MB)
└── fuseki/
    ├── fuseki-data-archive-20251212_104300.tar.gz
    ├── fuseki-db-20251212_023204.tar.gz
    └── pre-update-db-20251212_022130.tar.gz
```

---

## Quick Reference: Adding New Errors

When you find an error:
1. Add a row to the appropriate error table with a unique ID (E0XX - continue from last used ID)
2. Include detailed reasoning for why it's an error
3. Note the date found
4. Update the Error Categories Summary checkboxes

---

## Agent Review Prompt

Use the following prompt to task agents with reviewing the knowledge graph. Each agent should focus on a specific subset or error type to ensure thorough coverage.

### Full Agent Prompt

```markdown
# Knowledge Graph Quality Review Task

## Context

You are reviewing a knowledge graph for Regen Network that was extracted from various documents (forum posts, documentation, GitHub repos, etc.) using the koi-processor pipeline. The graph contains entities (nodes) and relationships (edges) that represent Regen Network's ecosystem.

## Access Information

- **Graph Visualization:** https://regen.gaiaai.xyz/graph
  - Structural view: https://regen.gaiaai.xyz/graph/#view=structural
  - Use the UI to browse entities by type, search for specific entities, and explore relationships
- **Server Access:** ssh darren@202.61.196.119
  - Database contains the raw graph data if you need to query directly

## Your Task

Systematically review the knowledge graph and identify errors. For EACH error you find, document:
1. The specific entity or relationship
2. What's wrong (incorrect type, duplicate, nonsensical, etc.)
3. What it SHOULD be (if applicable)
4. Your reasoning for why this is an error

## Error Categories to Look For

**IMPORTANT:** The categories below are examples of known error types. You are NOT limited to these categories. If you find errors that don't fit existing categories, CREATE NEW CATEGORIES and document them. We want to find ALL errors, not just errors that fit predefined boxes.

### Known Error Categories (non-exhaustive)

#### 1. Incorrect Entity Types
- Entities assigned wrong semantic types
- Example: "Regen Network" typed as HUMANACTOR instead of ORGANIZATION
- Example: Git commit messages typed as CLAIM
- Example: Physical materials typed as CLAIM
- Check if Credit Classes are properly typed (should align with rfs:CreditClassInfo)

#### 2. Duplicate Entities
- Same real-world entity with multiple nodes (different names/spellings)
- Example: "Gregory Landua", "Gregory_RND", "Gregory0" all referring to same person
- Look for: name variations, typos, username vs real name, "X (person 2)" suffixes

#### 3. Relationship Issues
- Duplicate relationships with different formatting (e.g., GUEST_ON vs GUESTON)
- Nonsensical relationships (relationships with generic/template entities)
- Missing relationships that should exist
- Incorrect relationship types

#### 4. Extraction Artifacts
- Generic template entities that shouldn't be nodes (e.g., "forum user", "reviewers")
- Fragments or partial extractions (e.g., "Node" as an entity)
- Tags/attributes incorrectly extracted as entities

#### 5. Schema/Ontology Issues
- Inconsistent type systems (e.g., both HUMANACTOR and PERSON existing)
- Types that don't build on Regen Network's official LinkML schemas
- Reference: https://framework.regen.network/schema/
- Note: We want to USE Regen's schemas as a BASE and EXTEND them to include additional types we need (claims, people, organizations, concepts, etc.) - not replace or duplicate them

#### 6. [Add New Categories As Needed]
- If you find errors that don't fit the above categories, create a new category
- Document what the category is and why it represents an error type

## Regen Network Domain Knowledge

To help identify errors, here's key domain knowledge:

- **Regen Network**: A blockchain platform for ecological assets (this is an ORGANIZATION)
- **Regen Registry**: A program/platform for ecological credit standards (not just an organization)
- **Credit Classes**: Classification entities for ecosystem service credits (e.g., "Biochar Credit Class")
  - Should use type CREDIT_CLASS per Regen's schema
- **Key People**: Gregory Landua (CEO), Christian Shearer, and others - watch for name duplicates
- **Regen uses LinkML schemas**: https://framework.regen.network/schema/
  - Source: regen-network/regen-data-standards → /schema/src/
  - **IMPORTANT**: We want to use Regen's schemas as a BASE and EXTEND them to include additional types we need (claims, people, organizations, concepts, etc.) - not replace or duplicate their type system

## Output Format

For each error found, add to the appropriate table. If your error doesn't fit an existing table format, CREATE A NEW TABLE with appropriate columns.

**For Incorrect Entity Types:**
| ID | Entity | Current Type | Should Be | Reasoning | Date Found |

**For Duplicate Entities:**
| ID | Duplicate Entities | Likely Canonical Entity | Confidence | Date Found |

**For Relationship Issues:**
| ID | Relationship(s) | Issue | Date Found |

**For Schema Issues:**
| ID | Issue | Details | Date Found |

**For New Error Categories:**
Create a new section header (e.g., "#### Data Quality Issues") and define an appropriate table structure that captures the relevant information for that error type.

## Already Identified Errors (Don't Re-report)

The following have already been identified - focus on finding NEW errors:
- E001-E024 (see main tracking document for details)
- Includes: Regen Network typing, Gregory/Christian duplicates, GUEST_ON/GUESTON duplicate, forum user relationships, biochar/carbon sequestration typing

## Review Strategy

1. Start by browsing the structural view to see all entity types
2. For each type, sample entities and check if they're correctly typed
3. Look for patterns of similar errors (e.g., all git commits typed as CLAIM)
4. Search for known entity names to find duplicates
5. Examine relationships for nonsensical connections
6. Cross-reference with Regen's official schema where applicable

Start your review now and document all errors found with detailed reasoning.
```

---

## Agent Review Assignments

Track which parts of the graph have been reviewed by agents:

| Agent/Session | Focus Area | Status | Errors Found | Date |
|--------------|------------|--------|--------------|------|
| Manual Review | Initial scan of structural view | Complete | E001-E024 | 2025-12-20 |
| Agent (Session 2) | Entity types: PROJECT | Complete | E031-E044 | 2025-12-20 |
| Agent (Session 3) | Entity types: PERSON/HUMANACTOR (1,460 + 3,867 entities) | Complete | E045-E146 | 2025-12-20 |
| Agent (Session 4) | Relationship review (predicates, duplicates, nonsensical) | Complete | E147-E206 | 2025-12-20 |
| Agent (Session 5) | Duplicate entity detection (all types) | Complete | E207-E208, E209-E240, E305-E326 | 2025-12-20 |
| Agent (Session 6) | Generic ENTITY type analysis (15,558 entities) | Complete | E327-E370 | 2025-12-20 |
| Agent (Session 7) | Entity types: ORGANIZATION (1,106 in PG, 1,554 in Fuseki) | Complete | E209-E240 (shared with Session 5) | 2025-12-20 |
| Codex CLI (GPT-5.2) | Entity types: CLAIM (7,828 entities) | Complete | E251-E277 (incl. E256-E274) | 2025-12-20 |
| Agent (Session 8) | EVIDENCE, QUESTION, CONCEPT, TECHNOLOGY, generic ENTITY | Complete | E278-E301 (E302-E304 consolidated into E261-E263, E275) | 2025-12-20 |
| Claude (Session 9) | Missing entity review using KOI knowledge base | Complete | M001-M032 (new Missing Entity IDs) | 2025-12-20 |
| Gemini (Session 10) | Root Cause Analysis Review | Complete | Review of F1-F11 + Missing Root Causes | 2025-12-20 |

---

## Stage 2 Review Results

**Synthesized From:** Agent 1 (Gemini, main doc), Agent 2 (Codex CLI), Agent 3 (Claude Code)
**Synthesis Date:** 2025-12-21

### Verification Methodology

| Method | Applied To | Tools Used |
|--------|------------|------------|
| Code review | All file/line references | `rg`, `nl`, `sed` |
| Data queries | Evidence counts | PostgreSQL (`psql` over SSH), Fuseki SPARQL (`curl`) |
| Pattern matching | Entity validation | SQL regex (`~*`), SPARQL `regex()` |
| Cross-reference | Error↔Finding mapping | Manual review + scripted ID extraction |

**Data Sources:**
- PostgreSQL (production): `entity_registry` (28,561 rows), `koi_memories` (50,379 rows)
- Fuseki (production): `/koi` dataset (162,754 triples) via `http://localhost:3030/koi/query`
- Codebase: `/opt/projects/koi-processor/` on production server (mirrors local `koi-processor/`)

### 1) Findings Verification

| Finding | Verified? | Confidence | File/Lines | Notes |
|---------|-----------|------------|------------|-------|
| F1 | ✅ Yes | High | `src/extraction/llm_extractor.py:139-248`<br>`src/extraction/openai_extractor.py:150-257` | Prompts allow `CLAIM` but don’t exclude commit/changelog/procedure headings; PostgreSQL count=518 CLAIMs matching git/changelog patterns (see §9). |
| F2 | ⚠️ Partial | High | `src/extraction/llm_extractor.py:146`<br>`src/extraction/openai_extractor.py:157` | `llm_extractor.py` lists `HumanActor/PERSON` alongside `ORGANIZATION`, but `openai_extractor.py` only enumerates `(HumanActor, Claim, Evidence, Question)` in the schema; Fuseki has 130 HumanActor labels matching org-like suffixes (see §9). |
| F3 | ✅ Yes | High | `src/knowledge_graph/graph_integration.py:145-146`<br>`scripts/regenerate_fuseki_graph.py:33-35` | HTTP/HTTPS mismatch confirmed in code and Fuseki data (26,055 `http://...koi#confidence` vs 1,715 `https://...koi#confidence` triples; see §9). |
| F4 | ⚠️ Partial | High | `scripts/regenerate_fuseki_graph.py:119-121`<br>`src/knowledge_graph/postprocessing/modules/ontology_normalizer_module.py:31-76` | Case/type namespace drift is real (e.g., `koi#Claim` vs `koi#CLAIM`); 166 entities are typed as BOTH `koi#Claim` and `koi#CLAIM` (see §9). Fixes still require choosing a canonical type URI strategy (KOI vs ontology). |
| F5 | ⚠️ Partial | High | `src/extraction/llm_extractor.py:169-172`<br>`src/knowledge_graph/postprocessing/modules/ontology_normalizer_module.py:78-117`<br>`src/core/canonical_predicates.json` | Predicates are free-form; mapping is incomplete and `canonical_predicates.json` maps to categories (not canonical predicate forms). Fuseki currently contains 3,483 distinct predicates (see §9). |
| F6 | ⚠️ Partial | Medium | `src/knowledge_graph/entity_resolver.py:24-46`<br>`src/knowledge_graph/graph_integration.py:254-286`<br>`src/knowledge_graph/uri_generator.py:50-83` | Dedup tiers exist and “Will” variants reproduce; effective default similarity threshold in the main pipeline is `0.95` (not `0.88`). Needs type-stability fixes before tuning thresholds. |
| F7 | ⚠️ Partial | Medium | `src/extraction/llm_extractor.py:208-217`<br>`src/knowledge_graph/improvements/entity_quality_filter.py` | No explicit AI/software exclusions in prompts or filters; some PERSON-only checks can be bypassed depending on pipeline ordering and type normalization. |
| F8 | ⚠️ Partial | High | `scripts/regenerate_fuseki_graph.py`<br>`src/knowledge_graph/graph_integration.py` | PostgreSQL↔Fuseki mismatch confirmed (PostgreSQL CLAIMs 7,828 vs Fuseki claim-like entities 29,377; PostgreSQL `relationships` table 0 rows; see §9). |
| F9 | ❌ Incorrect | High | `src/knowledge_graph/graph_integration.py:669-742`<br>`src/knowledge_graph/graph_integration.py:714-797` | `uri_generator.py` fallback exists (`src/knowledge_graph/uri_generator.py:108`) but does not explain ENTITY overuse. Dominant driver is `_add_relationship` calling `_get_or_create_entity_by_name(name)` with default `entity_type=\"ENTITY\"` and inserting via `EntityResolver` with empty `{}` metadata (15,558 ENTITY rows with empty metadata; see §9). |
| F10 | ✅ Yes | High | `src/knowledge_graph/graph_integration.py:669-708` | Relationship insertion lacks `subject != object` validation; Fuseki has 13 self-referential triples of the form `?s ?p ?s` (see §9). |
| F11 | ⚠️ Partial | High | `src/knowledge_graph/improvements/entity_quality_filter.py:338-360`<br>`src/knowledge_graph/improvements/entity_quality_filter.py:568-597` | Blocklist gaps are real, but “capitalization” is not the failure mode (filter lowercases). Main miss is singular/compound role handling (e.g., `teams` exists but `team` does not); PostgreSQL has 115 PERSON entities matching group/role keywords (see §9). |

### 2) Corrections Needed

| Finding | Issue | Correction |
|---------|------|------------|
| F2 | `openai_extractor.py` schema/type list mismatch | At `src/extraction/openai_extractor.py:157`, the prompt schema enumerates only `(HumanActor, Claim, Evidence, Question)`; align extractor schemas/type lists (incl. ORGANIZATION/PROJECT/CONCEPT/TECHNOLOGY) to avoid systematic mis-typing. |
| F5 | Predicate normalization incomplete + naming mismatch | Expand end-to-end predicate controls to include E019-E022 and E206; decide canonical style (snake_case vs camelCase) and make prompt + normalizer + validation consistent. Update `canonical_predicates.json` to map variations → canonical predicates (not categories). |
| F6 | Dedup threshold misstated | `src/knowledge_graph/graph_integration.py:254` defaults semantic threshold to `0.95`; `EntityResolver`’s `0.88` default only applies where not overridden. Consider per-type thresholds to manage false positives. |
| F8 | URI strategy described too narrowly | Enumerate and collapse all active URI strategies (e.g., `https://regen.network/{type}/{hash}`, `koi#entity:<hash>`, `http://regen.network/koi/entity/...`), and decide authoritative source-of-truth + sync direction. |
| F9 | Root cause misattributed | ENTITY overuse is primarily created by relationship-driven entity creation (`_get_or_create_entity_by_name(..., entity_type=\"ENTITY\")`) with no metadata/type inference. Fix should be in `graph_integration.py` relationship/entity creation path, not only `uri_generator.py`. |
| F11 | Blocklist gap explanation off | The issue is suffix/role-pattern coverage (singular/plural, multi-word roles), not capitalization. Add role-pattern detection and apply it beyond PERSON-only cases when appropriate. |

### 3) Missing Root Causes

| Error IDs | Pattern | Possible Root Cause | Needs Investigation |
|-----------|---------|---------------------|---------------------|
| E019-E022, E206 | Duplicate/malformed relationships | Predicate vocabulary not constrained end-to-end; normalizer mappings don’t cover formatting variants | Add prompt allowlist + canonical mappings + relationship validation module |
| E094-E096, E297-E298 | Placeholder/template/anonymous entities | Placeholder lists are incomplete and not regex-based; pipeline ordering can bypass PERSON-only placeholder checks | Add regex placeholder detection across types; consider moving type normalization earlier or treating HumanActor as PERSON in filters |
| E115-E117 | Single-letter entities | No minimum name-length validation; can slip through if extracted as HumanActor then normalized later | Reproduce on source docs; add `min_name_length` and/or single-letter block for PERSON/HumanActor |
| E183-E193 | Nonsensical relationships | Boilerplate/template sources not excluded + entity-type errors propagate into relationships | Add doc-type heuristics + subject/object type-compatibility constraints |
| M001-M032 | Missing domain entities (credit classes, proposals, keepers, Msg*) | Prompts lack domain types (e.g., CREDIT_CLASS, PROPOSAL, VALIDATOR, API_MESSAGE, KEEPER) and filters may treat them as “code identifiers” | Add ontology types + whitelists; audit ingestion coverage for those sources |
| E205 | PostgreSQL relationships table empty | No implemented path persists relationships into PostgreSQL (entities only) | Decide whether table is deprecated vs missing feature; implement or remove/ignore |
| E002-E005, E008-E014, E287-E290 | Ontology granularity gaps | Prompt/type system too coarse (no LICENSE/PROCESS/MATERIAL/etc.) and no post-classifier | Extend ontology + URI prefixes; add post-classification pass for coarse types |

### 4) Gaps in Fix Proposals

| Finding | Gap | Recommendation |
|---------|-----|----------------|
| F1 | Negative examples alone may be brittle | Add deterministic post-filters (regex/doc-type heuristics) in addition to prompt negatives for commit/changelog/procedure headings |
| F2 | Prompt tweaks won’t fix schema divergence | Share a single base prompt/schema across extractors and add a migration plan to retype existing HumanActor/Claim entities |
| F3/F4 | Migration plan not specified | Provide a concrete migration: normalize HTTP→HTTPS, consolidate type namespaces (KOI vs ontology), and add `owl:sameAs` where unavoidable |
| F5 | Canonical predicate style undefined | Pick one convention (recommend: snake_case to match `OntologyNormalizerModule`) and enforce via extraction allowlist + reject/repair invalid predicates |
| F6 | Threshold-only tuning risks false merges | Use per-type thresholds + deterministic alias rules (usernames, first-name/full-name, punctuation) before lowering similarity thresholds |
| F9 | Fix must address relationship-driven entity creation | Propagate metadata/type context when creating entities from relationships; avoid defaulting to ENTITY; consider type inference from predicate/context |
| F10 | Exact `subject==object` check is insufficient | Also detect semantic self-reference (canonicalized/alias-equal) using entity resolution/canonicalization |
| F11 | Blocklists won’t catch multi-word roles reliably | Add regex-based role detection (or lightweight NLP) and apply to PERSON/HumanActor/ENTITY cases consistently |

### 5) Additional Observations

- **Pipeline ordering bug:** Default pipeline runs `EntityQualityFilter` before `OntologyNormalizer`; HumanActor values can bypass PERSON-specific checks until after normalization.  
- **Dual extractor divergence:** `llm_extractor.py` and `openai_extractor.py` differ in allowed types and schema, leading to inconsistent typing across sources.  
- **Multiple URI strategies:** At least three URI patterns are generated across code paths/scripts; consolidation is prerequisite to clean migrations.  
- **Two sources of truth:** PostgreSQL (entities) and Fuseki (entities + relationships) diverge without a robust, validated sync/parity mechanism; E205 suggests relationships are Fuseki-only today.  

### 6) Recommended Fix Order (Dependencies)

1. **F3 + F4 (Namespaces + type URI/case conventions)** — prerequisite for any data migration and for making parity checks meaningful.  
2. **F2 + F1 + F7 (Extractor/schema alignment + prompt hardening)** — stop generating new bad data; ensure both extractors emit the same ontology types.  
3. **F9 (Correct root cause) + pipeline ordering + placeholder/single-letter fixes** — stop ENTITY-default inserts and close filter bypass paths.  
4. **F11 (Role/group detection improvements)** — reduce PERSON/HumanActor noise and template-role entities.  
5. **F6 (Dedup improvements)** — only after type stability; tune thresholds per type and add deterministic alias rules.  
6. **F5 + F10 (Predicate normalization + relationship validation)** — enforce canonical predicates and prevent invalid/self-referential edges.  
7. **F8 + E205 decision (Store parity + relationship persistence strategy)** — pick a single authoritative loading path and ensure PostgreSQL/Fuseki alignment (or explicitly separate responsibilities).  

### 7) Summary

- **Findings reviewed:** 11  
- **Core root cause validated:** 10/11 (✅ 3 fully verified, ⚠️ 7 partial); **❌ 1 incorrect (F9)**  
- **Corrections needed:** 6  
- **Missing root causes identified:** 7  
- **Fix-proposal gaps identified:** 8  

### 8) Error Coverage Matrix

F12-F17 are additional root causes identified during review (not present in the original Stage 2 RCA write-up).

**New Findings (F12-F17):**
- F12: Single-letter entities not filtered (min-length validation missing and/or bypassed via type normalization order)
- F13: Placeholder/anonymous entities not filtered (blocklists too narrow; missing regex patterns)
- F14: Relationship schema validation missing (free-form predicates + relationship duplication)
- F15: Boilerplate/template extraction not excluded (template entities and template-driven relationships)
- F16: Ontology/type-system gaps (missing/coarse types + domain types; drives mis-typing and missing entities)
- F17: Relationships not persisted in PostgreSQL (relationships table remains empty)

| Finding | Covers Errors | Error Count |
|---------|---------------|-------------|
| F1 | E004, E006-E007, E256-E277, E302, E304 | 27 |
| F2 | E001-E003, E072-E093, E104-E106, E146 | 29 |
| F3 | E026, E158, E252 | 3 |
| F4 | E025, E027, E043, E144, E207, E251, E253 | 7 |
| F5 | E147-E157, E159-E182 | 35 |
| F6 | E015-E018, E030, E041-E042, E122-E143, E230-E240, E305-E326 | 62 |
| F7 | E045-E056, E108-E111 | 16 |
| F8 | E028, E145, E208, E254-E255 | 5 |
| F9 | E012, E029, E290-E296, E299-E301, E327-E370 | 56 |
| F10 | E194-E204 | 11 |
| F11 | E057-E071 | 15 |
| F12 | E115-E117 | 3 |
| F13 | E094-E096, E297-E298 | 5 |
| F14 | E019, E206 | 2 |
| F15 | E013-E014, E020-E022, E183-E193 | 16 |
| F16 | E005, E008-E011, E023-E024, E031-E040, E044, E097-E103, E107, E112-E114, E118-E121, E209-E229, E278-E289, M001-M032 | 98 |
| F17 | E205 | 1 |

**Uncovered Errors:** None — all Stage 1 `E###`/`M###` IDs present in this document are mapped (IDs not present in Stage 1: E241-E250, E303).

### 9) Quantified Evidence

| Finding | Metric | Count | Query/Source |
|---------|--------|-------|--------------|
| F1 | CLAIMs matching git/changelog patterns | 518 | PostgreSQL `entity_registry` (SQL regex per task spec) |
| F2 | Orgs mistyped as PERSON/HUMANACTOR (PG estimate via suffix/prefix) | 23 | PostgreSQL `entity_registry` (SQL regex per task spec) |
| F2 | HumanActor entities with org-like suffix in label/name | 130 | Fuseki SPARQL `regex(...$)` over `koi#HumanActor`/`ontology#HumanActor` |
| F3 | `http://regen.network/koi#confidence` triples | 26,055 | Fuseki SPARQL count |
| F3 | `https://regen.network/koi#confidence` triples | 1,715 | Fuseki SPARQL count |
| F4 | Entities typed as both `koi#Claim` and `koi#CLAIM` | 166 | Fuseki SPARQL count |
| F5 | Distinct predicates in graph | 3,483 | Fuseki SPARQL `COUNT(DISTINCT ?p)` |
| F8 | PostgreSQL CLAIM entities | 7,828 | PostgreSQL `entity_registry` |
| F8 | Fuseki claim-like entities (Claim/CLAIM/ontology#Claim) | 29,377 | Fuseki SPARQL count |
| F9 | ENTITY rows with empty metadata | 15,558 | PostgreSQL `entity_registry` (`metadata IS NULL OR '{}'`) |
| F10 | Self-referential triples (`?s ?p ?s`) | 13 | Fuseki SPARQL count |
| F11 | Generic group/role terms extracted as PERSON | 115 | PostgreSQL `entity_registry` (regex over `team|community|contributors|...`) |
| F12 | Single-character entities | 8 | PostgreSQL `entity_registry` (`LENGTH(entity_text)=1`) |
| F13 | Placeholder/anonymous patterns in entity_text | 265 | PostgreSQL `entity_registry` (regex per task spec) |
| F17 | PostgreSQL relationships rows | 0 | PostgreSQL `relationships` table |

---

## Cycle Closeout (2025-12-23)

### Final Metrics

| Metric | Value |
|--------|-------|
| Entities (entity_registry) | 29,667 |
| Relationships (koi_relationships) | 15,364 |
| Triples (Fuseki /koi) | 163,699 |
| Distinct predicates | 1,501 |
| Code artifacts | 16,820 |
| Doc→code links | 6,453 |
| AGE stub nodes | 5,464 |
| AGE edges | 6,463 |
| **Quality Gates** | **4/4 PASS** |

### Quality Gate Results

| Gate | Check | Result |
|------|-------|--------|
| A | No `http://regen.network/` URIs | ✅ 0 |
| B1 | No `ontology#` types | ✅ 0 |
| B2 | No `ontology#` predicates | ✅ 0 |
| C | No self-referential triples | ✅ 0 |

### Major Fixes Completed

| Fix | Status | Impact |
|-----|--------|--------|
| FIX-006 Entity Deduplication | ✅ Complete | Per-type thresholds, fuzzy tier, canonical registry |
| FIX-007 Predicate Consolidation | ✅ Complete | 3,303 → 1,501 predicates (-54.6%) |
| Stage-6 Full Re-Extraction | ✅ Complete | 12,002 docs processed |
| Code↔Docs Bridge | ✅ Complete | Bi-directional linking implemented |

### Merge Summary

**Total Duplicate Entities Removed:** 374 (30,041 → 29,667)

| Pass | Script | Method | Applied | Blocked |
|------|--------|--------|---------|---------|
| 1 | apply_safe_merges.py | tier1_normalized + tier1_5_canonical | ~51 | Rollback bug |
| 2 | apply_dedup_merges.py | tier1_normalized + tier1_5_canonical | 323 | 0 |

**Bug Fixes Applied During Pass 2:**
1. SQL CTE reference error - `merge_fields_sql("w", "l")` → `merge_fields_sql("w", "c")`
2. Self-referential relationship constraint - Added DELETE step before merge
3. Transaction isolation - Added SAVEPOINT handling

### Artifacts & Audit Trail

**Production Server:** darren@202.61.196.119:/opt/projects/koi-processor

**Merge CSVs:**
- `merges.csv` - Initial dry-run (8,414 proposals)
- `merges_v2.csv` - v2 dry-run with raised PERSON threshold
- `merges_post_deploy.csv` - Post-deployment dry-run
- `merges_after_merge.csv` - Final state dry-run (8,325 proposals)

**Logs:**
- `dedup_dry_run.log`, `dedup_dry_run_v2.log`, `dedup_post_deploy.log`
- `dedup_after_merge.log`
- `fuseki_rebuild_20251223_161856.log`, `fuseki_rebuild_20251223_221921.log`

**Database Backups:**
- `backups/fix006_merge_backup_20251223_230018.sql`
- `backups/fix006_merge_backup_20251223_230128.sql`
- `backups/fix006_merge_backup_20251223_230219.sql`

**Audit Table:**
- `dedup_merge_plan` - 323 rows, all with `applied=true`

### Known Remaining Issues / Carry-Over Candidates

1. **tier1x_fuzzy PERSON proposals** - 6-8 identified false positives (Speaker 1/2, Michael's Arkham, etc.). Do not auto-merge; requires manual review or domain expert curation.

2. **type_conflict backlog** - 4,264 cross-type collisions (e.g., CONCEPT↔TECHNOLOGY). Informational only; not auto-merged.

3. **Single-token PERSON ambiguity** - Single first names ("Will", "Greg") can match unrelated people. Protected by canonical registry requirement but not fully resolved.

4. **Further predicate reduction** - 1,501 predicates could potentially be reduced to ~100-200 with additional consolidation. Optional based on UX/retrieval needs.

### Verification Checklist

- [x] All 32 FIX-006 tests passing on production
- [x] rapidfuzz 3.14.1 installed
- [x] All key files match between local repo and production
- [x] dedup_merge_plan table populated with merge history
- [x] Database backups preserved
- [x] Fuseki quality gates passing

### Next Steps (Cycle 2026-01)

1. Review knowledge graph quality after running in production
2. Consider full re-extraction if new duplicate patterns emerge
3. Evaluate tier1x_fuzzy ORGANIZATION merges (higher confidence than PERSON)
4. Monitor for any remaining duplicate clusters

---

**Cycle Closed:** 2025-12-23
**Next Review:** knowledge-graph-review-2026-01.md
