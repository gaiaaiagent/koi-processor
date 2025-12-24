# Type Conflicts Report - Cycle 2026-01

**Generated:** 2025-12-23
**Database:** eliza (production)
**Total Type Conflicts:** 2,749 (entities with same normalized label but different types)

---

## Top 50 Conflicts by Total Occurrences

| Rank | Normalized Label | Types | Type Count | Rows | Total Occurrences |
|------|------------------|-------|------------|------|-------------------|
| 1 | notion | ORGANIZATION, PROJECT, TECHNOLOGY | 3 | 3 | 336 |
| 2 | regen commons | CONCEPT, ORGANIZATION, PROJECT | 3 | 3 | 317 |
| 3 | governance | CONCEPT, ORGANIZATION | 2 | 2 | 276 |
| 4 | koi | CONCEPT, PERSON, PROJECT, STANDARD, TECHNOLOGY | 5 | 5 | 236 |
| 5 | aerodrome | ORGANIZATION, PROJECT, TECHNOLOGY | 3 | 3 | 234 |
| 6 | sparql | CONCEPT, STANDARD, TECHNOLOGY | 3 | 3 | 224 |
| 7 | telegram | ORGANIZATION, TECHNOLOGY | 2 | 2 | 219 |
| 8 | youtube | ORGANIZATION, TECHNOLOGY | 2 | 2 | 212 |
| 9 | discord | ORGANIZATION, TECHNOLOGY | 2 | 2 | 208 |
| 10 | agent-based modeling | CONCEPT, PROCESS, PROJECT, TECHNOLOGY | 4 | 4 | 184 |
| 11 | hydrax | ORGANIZATION, PROJECT, TECHNOLOGY | 3 | 3 | 183 |
| 12 | koi project | PROJECT, TECHNOLOGY | 2 | 2 | 181 |
| 13 | twitter | ORGANIZATION, PROJECT, TECHNOLOGY | 3 | 3 | 180 |
| 14 | blockchain | CONCEPT, TECHNOLOGY | 2 | 2 | 177 |
| 15 | python | PROJECT, TECHNOLOGY | 2 | 2 | 172 |
| 16 | regen tokenomics | CONCEPT, ORGANIZATION, PROJECT | 3 | 3 | 166 |
| 17 | regen tokenomics ai assistant | PROJECT, TECHNOLOGY | 2 | 2 | 164 |
| 18 | koi-processor | PROJECT, TECHNOLOGY | 2 | 2 | 161 |
| 19 | ethereum | LOCATION, ORGANIZATION, PROJECT, TECHNOLOGY | 4 | 4 | 158 |
| 20 | regen-koi-mcp | PROJECT, TECHNOLOGY | 2 | 2 | 151 |
| 21 | exchequer.fi | ORGANIZATION, PROJECT, TECHNOLOGY | 3 | 3 | 148 |
| 22 | ai | CONCEPT, TECHNOLOGY | 2 | 2 | 144 |
| 23 | discourse | CONCEPT, ORGANIZATION, TECHNOLOGY | 3 | 3 | 139 |
| 24 | mcp server | CONCEPT, TECHNOLOGY | 2 | 2 | 135 |
| 25 | knowledge graph | CONCEPT, TECHNOLOGY | 2 | 2 | 133 |
| 26 | typescript | CONCEPT, TECHNOLOGY | 2 | 2 | 128 |
| 27 | liquidity dao | CONCEPT, ORGANIZATION, PROJECT | 3 | 3 | 124 |
| 28 | usdc | CONCEPT, MATERIAL, PROJECT, TECHNOLOGY | 4 | 4 | 123 |
| 29 | biodiversity | CONCEPT, MATERIAL | 2 | 2 | 113 |
| 30 | base | LOCATION, MODULE, ORGANIZATION, PROJECT, TECHNOLOGY | 5 | 5 | 109 |
| 31 | rdf | CONCEPT, STANDARD, TECHNOLOGY | 3 | 3 | 106 |
| 32 | mcp | CONCEPT, PROJECT, STANDARD, TECHNOLOGY | 4 | 4 | 104 |
| 33 | semantic search | CONCEPT, PROCESS, TECHNOLOGY | 3 | 3 | 104 |
| 34 | verification | CONCEPT, PROCESS, PROJECT, TECHNOLOGY | 4 | 4 | 103 |
| 35 | web3 | CONCEPT, TECHNOLOGY | 2 | 2 | 102 |
| 36 | refi | CONCEPT, PROJECT | 2 | 2 | 101 |
| 37 | vector search | CONCEPT, TECHNOLOGY | 2 | 2 | 100 |
| 38 | medium | ORGANIZATION, TECHNOLOGY | 2 | 2 | 99 |
| 39 | firstprinciplesai | ORGANIZATION, TECHNOLOGY | 2 | 2 | 94 |
| 40 | gaia ai | ORGANIZATION, PROJECT, TECHNOLOGY | 3 | 3 | 94 |
| 41 | solana | LOCATION, ORGANIZATION, PROJECT, TECHNOLOGY, VALIDATOR | 5 | 5 | 94 |
| 42 | koi-sensors | PROJECT, TECHNOLOGY | 2 | 2 | 93 |
| 43 | regeneration | CONCEPT, ORGANIZATION, PROCESS | 3 | 3 | 88 |
| 44 | regen token economy | CONCEPT, PROJECT | 2 | 2 | 88 |
| 45 | transparency | CONCEPT, PROCESS | 2 | 2 | 87 |
| 46 | r&d | CONCEPT, ORGANIZATION, PROCESS, PROJECT | 4 | 4 | 86 |
| 47 | polygon | LOCATION, ORGANIZATION, PROJECT, TECHNOLOGY | 4 | 4 | 81 |
| 48 | hybrid search | CONCEPT, PROCESS, TECHNOLOGY | 3 | 3 | 80 |
| 49 | claude code | PROJECT, TECHNOLOGY | 2 | 2 | 78 |
| 50 | carbon sequestration | CONCEPT, MATERIAL | 2 | 2 | 74 |

---

## Top 30 Conflicts: Per-Type Breakdown

### 1. notion (336 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| TECHNOLOGY | Notion | 308 | 51 |
| ORGANIZATION | Notion | 27 | 1454 |
| PROJECT | Notion | 1 | 24272 |

**Analysis:** Notion is primarily a technology/software product. ORGANIZATION may be valid (Notion Labs, the company), but PROJECT(1) is extraction noise.

### 2. regen commons (317 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| ORGANIZATION | Regen Commons | 151 | 137 |
| PROJECT | Regen Commons | 147 | 70 |
| CONCEPT | Regen Commons | 19 | 1410 |

**Analysis:** Legitimate polysemy. Regen Commons is both an organization AND a project. CONCEPT(19) may be valid when discussing "the regen commons" as an idea.

### 3. governance (276 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| CONCEPT | Governance | 274 | 640 |
| ORGANIZATION | Governance | 2 | 16117 |

**Analysis:** CONCEPT is correct. ORGANIZATION(2) is extraction noise - "governance" is not an organization.

### 4. koi (236 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| PROJECT | KOI | 166 | 170 |
| TECHNOLOGY | koi | 65 | 12 |
| PERSON | Koi | 2 | 6928 |
| CONCEPT | KOI | 2 | 6535 |
| STANDARD | KOI | 1 | 27552 |

**Analysis:** PROJECT and TECHNOLOGY are valid (KOI is both a project and a tech stack). PERSON(2) is likely extraction noise (fish reference or typo). CONCEPT(2) and STANDARD(1) are extraction noise.

### 5. aerodrome (234 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| TECHNOLOGY | Aerodrome | 100 | 54 |
| PROJECT | Aerodrome | 98 | 118 |
| ORGANIZATION | Aerodrome | 36 | 2990 |

**Analysis:** Legitimate polysemy. Aerodrome is a DeFi project (PROJECT), built on tech (TECHNOLOGY), run by a team (ORGANIZATION).

### 6. sparql (224 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| TECHNOLOGY | SPARQL | 186 | 304 |
| CONCEPT | SPARQL | 29 | 2083 |
| STANDARD | SPARQL | 9 | 1569 |

**Analysis:** TECHNOLOGY is primary. STANDARD is valid (SPARQL is a W3C standard). CONCEPT(29) is borderline.

### 7. telegram (219 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| TECHNOLOGY | Telegram | 212 | 17 |
| ORGANIZATION | Telegram | 7 | 666 |

**Analysis:** Legitimate polysemy. Telegram is both a platform (TECHNOLOGY) and a company (ORGANIZATION).

### 8. youtube (212 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| TECHNOLOGY | youtube | 208 | 184 |
| ORGANIZATION | YouTube | 4 | 4710 |

**Analysis:** Legitimate polysemy. YouTube is a platform (TECHNOLOGY) and a company (ORGANIZATION).

### 9. discord (208 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| TECHNOLOGY | Discord | 193 | 71 |
| ORGANIZATION | Discord | 15 | 667 |

**Analysis:** Legitimate polysemy. Discord is a platform (TECHNOLOGY) and a company (ORGANIZATION).

### 10. agent-based modeling (184 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| CONCEPT | Agent-Based Modeling | 178 | 58 |
| TECHNOLOGY | Agent-Based Modeling | 4 | 16915 |
| PROJECT | Agent-Based Modeling | 1 | 17220 |
| PROCESS | Agent-Based Modeling | 1 | 22981 |

**Analysis:** CONCEPT is correct. TECHNOLOGY(4), PROJECT(1), PROCESS(1) are extraction noise.

### 11. hydrax (183 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| TECHNOLOGY | Hydrax | 83 | 53 |
| PROJECT | Hydrax | 81 | 117 |
| ORGANIZATION | Hydrax | 19 | 2989 |

**Analysis:** Legitimate polysemy. Hydrax is a project/technology/organization.

### 12. koi project (181 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| PROJECT | Koi Project | 178 | 436 |
| TECHNOLOGY | Koi Project | 3 | 9433 |

**Analysis:** PROJECT is correct. TECHNOLOGY(3) is extraction noise.

### 13. twitter (180 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| TECHNOLOGY | Twitter | 164 | 899 |
| ORGANIZATION | Twitter | 15 | 385 |
| PROJECT | Twitter | 1 | 11646 |

**Analysis:** TECHNOLOGY and ORGANIZATION are valid. PROJECT(1) is extraction noise.

### 14. blockchain (177 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| TECHNOLOGY | blockchain | 148 | 418 |
| CONCEPT | blockchain | 29 | 675 |

**Analysis:** Legitimate polysemy. Blockchain is both a technology and a concept.

### 15. python (172 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| TECHNOLOGY | Python | 171 | 515 |
| PROJECT | Python | 1 | 29940 |

**Analysis:** TECHNOLOGY is correct. PROJECT(1) is extraction noise.

### 16. regen tokenomics (166 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| CONCEPT | Regen Tokenomics | 117 | 57 |
| PROJECT | REGEN Tokenomics | 43 | 3839 |
| ORGANIZATION | Regen Tokenomics | 6 | 7184 |

**Analysis:** CONCEPT and PROJECT are valid. ORGANIZATION(6) is extraction noise.

### 17. regen tokenomics ai assistant (164 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| TECHNOLOGY | Regen Tokenomics AI Assistant | 163 | 52 |
| PROJECT | Regen Tokenomics AI Assistant | 1 | 29198 |

**Analysis:** TECHNOLOGY is correct. PROJECT(1) is extraction noise.

### 18. koi-processor (161 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| PROJECT | koi-processor | 107 | 300 |
| TECHNOLOGY | koi-processor | 54 | 775 |

**Analysis:** Legitimate polysemy. koi-processor is both a project (repo) and technology (codebase).

### 19. ethereum (158 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| TECHNOLOGY | Ethereum | 128 | 1392 |
| PROJECT | Ethereum | 17 | 2003 |
| ORGANIZATION | Ethereum | 9 | 3376 |
| LOCATION | Ethereum | 4 | 3883 |

**Analysis:** TECHNOLOGY, PROJECT, and ORGANIZATION are valid. LOCATION(4) is extraction noise.

### 20. regen-koi-mcp (151 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| PROJECT | regen-koi-mcp | 91 | 451 |
| TECHNOLOGY | regen-koi-mcp | 60 | 4310 |

**Analysis:** Legitimate polysemy. regen-koi-mcp is both a project (repo) and technology (codebase).

### 21. exchequer.fi (148 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| PROJECT | exchequer.fi | 92 | 115 |
| ORGANIZATION | exchequer.fi | 45 | 56 |
| TECHNOLOGY | exchequer.fi | 11 | 2181 |

**Analysis:** Legitimate polysemy. Exchequer is a DeFi project/organization/platform.

### 22. ai (144 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| TECHNOLOGY | AI | 141 | 188 |
| CONCEPT | AI | 3 | 1446 |

**Analysis:** Legitimate polysemy. AI is both a technology and a concept.

### 23. discourse (139 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| TECHNOLOGY | Discourse | 125 | 288 |
| ORGANIZATION | Discourse | 12 | 6515 |
| CONCEPT | discourse | 2 | 5514 |

**Analysis:** TECHNOLOGY and ORGANIZATION are valid. CONCEPT(2) is a different word sense (general discourse/discussion).

### 24. mcp server (135 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| TECHNOLOGY | MCP Server | 134 | 393 |
| CONCEPT | MCP server | 1 | 15823 |

**Analysis:** TECHNOLOGY is correct. CONCEPT(1) is extraction noise.

### 25. knowledge graph (133 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| CONCEPT | knowledge graph | 117 | 80 |
| TECHNOLOGY | Knowledge Graph | 16 | 1504 |

**Analysis:** Legitimate polysemy. Knowledge graph is both a concept and a technology.

### 26. typescript (128 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| TECHNOLOGY | TypeScript | 127 | 832 |
| CONCEPT | TypeScript | 1 | 15846 |

**Analysis:** TECHNOLOGY is correct. CONCEPT(1) is extraction noise.

### 27. liquidity dao (124 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| ORGANIZATION | Liquidity DAO | 97 | 536 |
| PROJECT | Liquidity DAO | 15 | 2444 |
| CONCEPT | Liquidity DAO | 12 | 3408 |

**Analysis:** ORGANIZATION is primary. PROJECT is valid. CONCEPT(12) is extraction noise.

### 28. usdc (123 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| TECHNOLOGY | USDC | 94 | 1595 |
| MATERIAL | USDC | 14 | 4012 |
| CONCEPT | USDC | 13 | 3428 |
| PROJECT | usdc | 2 | 21819 |

**Analysis:** TECHNOLOGY is primary. MATERIAL may be valid (USDC as a financial instrument). CONCEPT(13) and PROJECT(2) are extraction noise.

### 29. biodiversity (113 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| CONCEPT | biodiversity | 112 | 1299 |
| MATERIAL | biodiversity | 1 | 25478 |

**Analysis:** CONCEPT is correct. MATERIAL(1) is extraction noise.

### 30. base (109 total)
| Type | Entity Text | Occurrences | ID |
|------|-------------|-------------|-----|
| TECHNOLOGY | Base | 68 | 611 |
| PROJECT | Base | 24 | 4690 |
| LOCATION | Base | 14 | 126 |
| ORGANIZATION | Base | 2 | 17713 |
| MODULE | base | 1 | 14771 |

**Analysis:** "Base" is ambiguous. TECHNOLOGY and PROJECT refer to Coinbase's L2. LOCATION is a generic word sense. MODULE is a code reference. ORGANIZATION is extraction noise.

---

## E325-Pattern Artifacts (FirstName-OrgName as PERSON)

| Entity Text | Type | Occurrences | ID |
|-------------|------|-------------|-----|
| Will-Regen Foundation | PERSON | 9 | 11157 |
| Chris-Chainflow | PERSON | 6 | 4568 |
| Curtis-Meme_Network | PERSON | 4 | 8245 |

**Root Cause:** LLM extraction incorrectly merged a first name with an organization name (e.g., "Will said Regen Foundation..." parsed as single entity).

**Proposed Fix:** Block PERSON entities matching pattern `^[A-Z][a-z]+-[A-Z].*` where the suffix matches organization-like patterns (Foundation, Network, DAO, etc.)

---

## Classification Summary

### Wrong-Type (Extraction Noise to Remove)

| Entity | Wrong Type(s) | Correct Type | Occurrences to Remove |
|--------|---------------|--------------|----------------------|
| governance | ORGANIZATION | CONCEPT | 2 |
| koi | PERSON, CONCEPT, STANDARD | PROJECT, TECHNOLOGY | 5 |
| agent-based modeling | TECHNOLOGY, PROJECT, PROCESS | CONCEPT | 6 |
| koi project | TECHNOLOGY | PROJECT | 3 |
| twitter | PROJECT | TECHNOLOGY, ORGANIZATION | 1 |
| python | PROJECT | TECHNOLOGY | 1 |
| regen tokenomics | ORGANIZATION | CONCEPT, PROJECT | 6 |
| regen tokenomics ai assistant | PROJECT | TECHNOLOGY | 1 |
| ethereum | LOCATION | TECHNOLOGY, PROJECT, ORGANIZATION | 4 |
| mcp server | CONCEPT | TECHNOLOGY | 1 |
| typescript | CONCEPT | TECHNOLOGY | 1 |
| liquidity dao | CONCEPT | ORGANIZATION, PROJECT | 12 |
| usdc | CONCEPT, PROJECT | TECHNOLOGY, MATERIAL | 15 |
| biodiversity | MATERIAL | CONCEPT | 1 |
| notion | PROJECT | TECHNOLOGY, ORGANIZATION | 1 |

**Total wrong-type occurrences:** ~60

### Legitimate Polysemy (Keep Multiple Types)

| Entity | Types | Rationale |
|--------|-------|-----------|
| regen commons | ORGANIZATION, PROJECT, CONCEPT | Org that runs a project about a concept |
| aerodrome | TECHNOLOGY, PROJECT, ORGANIZATION | DeFi protocol with all three aspects |
| sparql | TECHNOLOGY, STANDARD | Query language that is also a standard |
| telegram | TECHNOLOGY, ORGANIZATION | Platform and company |
| youtube | TECHNOLOGY, ORGANIZATION | Platform and company |
| discord | TECHNOLOGY, ORGANIZATION | Platform and company |
| hydrax | TECHNOLOGY, PROJECT, ORGANIZATION | Protocol with all three aspects |
| twitter | TECHNOLOGY, ORGANIZATION | Platform and company |
| blockchain | TECHNOLOGY, CONCEPT | Technology and abstract concept |
| koi-processor | PROJECT, TECHNOLOGY | Repo and codebase |
| regen-koi-mcp | PROJECT, TECHNOLOGY | Repo and codebase |
| exchequer.fi | PROJECT, ORGANIZATION, TECHNOLOGY | DeFi with all three |
| ai | TECHNOLOGY, CONCEPT | Technology and field of study |
| discourse | TECHNOLOGY, ORGANIZATION | Forum software and company |
| knowledge graph | CONCEPT, TECHNOLOGY | Abstract idea and implementation |
| base | TECHNOLOGY, PROJECT, LOCATION | L2 chain, project, and generic word |

---

## Priority Ranking for Fixes

### High Priority (Wrong-type, actionable)

1. **E325: FirstName-OrgName artifacts** - 3 entities, 19 occurrences - Clear extraction bug
2. **governance as ORGANIZATION** - 2 occurrences - Clear wrong-type
3. **koi as PERSON/CONCEPT/STANDARD** - 5 occurrences - Clear wrong-type
4. **agent-based modeling as TECHNOLOGY/PROJECT/PROCESS** - 6 occurrences - Clear wrong-type

### Medium Priority (Borderline cases)

5. **ethereum as LOCATION** - 4 occurrences
6. **liquidity dao as CONCEPT** - 12 occurrences
7. **usdc as CONCEPT/PROJECT** - 15 occurrences

### Low Priority (Minimal impact)

8. Single-occurrence noise (python as PROJECT, typescript as CONCEPT, etc.)

---

*Report generated for Type Conflict Sprint - Cycle 2026-01*
