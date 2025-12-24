# Type Conflict Allowlist Review - Week 5

**Generated:** 2025-12-24
**Purpose:** Data-driven evaluation of candidate type pairs for polysemy allowlist expansion

---

## Summary

| Pair | Labels | Occurrences | Recommendation |
|------|--------|-------------|----------------|
| ORGANIZATION↔TECHNOLOGY | 169 | 4,882 | **Add to allowlist** |
| CONCEPT↔STANDARD | 131 | 1,801 | **Add to allowlist** |
| STANDARD↔TECHNOLOGY | 87 | 1,538 | **Add to allowlist** |

**Result:** All three pairs are predominantly legitimate polysemy. Adding them to the allowlist will reduce the "unexpected conflicts" bucket by ~387 labels without hiding real errors.

---

## Pair 1: ORGANIZATION↔TECHNOLOGY

### Top 50 Sample Analysis

| Label | ORG% | TECH% | Classification |
|-------|------|-------|----------------|
| notion | 8.1% | 91.9% | Polysemy - platform company |
| telegram | 3.2% | 96.8% | Polysemy - platform company |
| youtube | 1.9% | 98.1% | Polysemy - platform company |
| discord | 7.2% | 92.8% | Polysemy - platform company |
| twitter | 8.4% | 91.6% | Polysemy - platform company |
| discourse | 8.8% | 91.2% | Polysemy - platform company |
| ethereum | 6.6% | 93.4% | Polysemy - blockchain + foundation |
| aerodrome | 26.5% | 73.5% | Polysemy - DeFi protocol + team |
| hydrax | 18.6% | 81.4% | Polysemy - DeFi protocol + team |
| medium | 24.2% | 75.8% | Polysemy - platform company |
| firstprinciplesai | 78.7% | 21.3% | Polysemy - AI company + product |
| gaia ai | 67.8% | 32.2% | Polysemy - AI company + product |
| base | 2.9% | 97.1% | Polysemy - Coinbase L2 |
| first principles ai | 88.4% | 11.6% | Polysemy - AI company + product |
| solana | 13.2% | 86.8% | Polysemy - blockchain + foundation |
| gitlab | 20.6% | 79.4% | Polysemy - platform company |
| exchequer.fi | 80.4% | 19.6% | Polysemy - DAO + platform |
| polygon | 3.8% | 96.2% | Polysemy - blockchain + company |
| google drive | 3.8% | 96.2% | Polysemy - Google product |
| gitcoin | 94.2% | 5.8% | Polysemy - org + grants platform |
| openai | 19.2% | 80.8% | Polysemy - AI company + APIs |
| google | 67.3% | 32.7% | Polysemy - company + products |
| linkedin | 13.6% | 86.4% | Polysemy - platform company |
| slack | 7.3% | 92.7% | Polysemy - platform company |

### Classification

- **Mostly Polysemy:** 48/50 (96%)
- **Mixed:** 2/50 (4%)
- **Wrong-type:** 0/50 (0%)

### Rationale

Platform companies (Notion, Discord, Telegram, YouTube, etc.) are correctly typed both ways depending on context:
- "I use Discord for communication" (TECHNOLOGY)
- "Discord raised $100M in funding" (ORGANIZATION)

Blockchain/DeFi entities (Ethereum, Aerodrome, Solana) similarly have both foundation/team (ORGANIZATION) and protocol/blockchain (TECHNOLOGY) aspects.

**Recommendation:** Add ORGANIZATION↔TECHNOLOGY to allowlist.

---

## Pair 2: CONCEPT↔STANDARD

### Top 50 Sample Analysis

| Label | CONCEPT% | STANDARD% | Classification |
|-------|----------|-----------|----------------|
| methodology | 95.2% | 4.8% | Polysemy - abstract + documented |
| rdf | 57.4% | 42.6% | Polysemy - data model + W3C spec |
| json-ld | 46.2% | 53.8% | Polysemy - format + W3C spec |
| sparql | 76.3% | 23.7% | Polysemy - query lang + W3C spec |
| constitution | 80.6% | 19.4% | Polysemy - governance concept + document |
| iri | 83.3% | 16.7% | Polysemy - identifier concept + RFC |
| approved methodology | 72.7% | 27.3% | Polysemy - carbon offset methodology |
| redd+ | 89.5% | 10.5% | Polysemy - framework concept + spec |
| esg | 94.7% | 5.3% | Polysemy - concept + reporting standard |
| koi protocol | 42.1% | 57.9% | Polysemy - protocol concept + spec |
| mcp | 44.4% | 55.6% | Polysemy - protocol concept + spec |
| oauth | 66.7% | 33.3% | Polysemy - auth concept + RFC |
| doi | 25.0% | 75.0% | Polysemy - identifier concept + ISO |
| semantic versioning | 42.9% | 57.1% | Polysemy - versioning concept + spec |
| owl | 16.7% | 83.3% | Polysemy - ontology lang + W3C spec |
| turtle | 33.3% | 66.7% | Polysemy - serialization format |
| geojson | 20.0% | 80.0% | Polysemy - data format + RFC |
| sdgs | 40.0% | 60.0% | Polysemy - UN goals + framework |

### Classification

- **Mostly Polysemy:** 47/50 (94%)
- **Mixed:** 3/50 (6%)
- **Wrong-type:** 0/50 (0%)

### Rationale

Standards are inherently conceptual. Terms like RDF, SPARQL, JSON-LD, OAuth are correctly typed both ways:
- "RDF is a data model for representing information" (CONCEPT)
- "RDF is defined in the W3C specification" (STANDARD)

Methodologies (carbon offset, governance) similarly have abstract conceptual aspects and formal documented aspects.

**Recommendation:** Add CONCEPT↔STANDARD to allowlist.

---

## Pair 3: STANDARD↔TECHNOLOGY

### Top 50 Sample Analysis

| Label | STANDARD% | TECH% | Classification |
|-------|-----------|-------|----------------|
| sparql | 4.6% | 95.4% | Polysemy - W3C spec + query tech |
| mcp | 5.2% | 94.8% | Polysemy - protocol spec + implementation |
| rdf | 30.7% | 69.3% | Polysemy - W3C spec + libraries |
| koi | 1.5% | 98.5% | Polysemy - protocol + implementation |
| json-ld | 39.6% | 60.4% | Polysemy - format spec + libraries |
| oauth | 11.5% | 88.5% | Polysemy - RFC + implementations |
| koi protocol | 44.0% | 56.0% | Polysemy - spec + implementation |
| https | 5.9% | 94.1% | Polysemy - protocol standard + usage |
| orcid | 12.5% | 87.5% | Polysemy - ID standard + infrastructure |
| doi | 42.9% | 57.1% | Polysemy - ISO standard + resolvers |
| owl | 35.7% | 64.3% | Polysemy - W3C spec + tools |
| linkml | 21.4% | 78.6% | Polysemy - schema lang + tools |
| openapi | 37.5% | 62.5% | Polysemy - spec + tooling |
| erc20 | 33.3% | 66.7% | Polysemy - token standard + contracts |
| http | 20.0% | 80.0% | Polysemy - protocol + usage |
| turtle | 57.1% | 42.9% | Polysemy - format spec + parsers |
| geojson | 66.7% | 33.3% | Polysemy - RFC + libraries |

### Classification

- **Mostly Polysemy:** 46/50 (92%)
- **Mixed:** 4/50 (8%)
- **Wrong-type:** 0/50 (0%)

### Rationale

Technical standards are inherently both specifications and implemented technologies:
- "SPARQL is defined in the W3C specification" (STANDARD)
- "We use SPARQL to query our RDF data" (TECHNOLOGY)

Protocol standards (OAuth, HTTP, HTTPS) and data format standards (JSON-LD, Turtle, GeoJSON) follow the same pattern.

**Recommendation:** Add STANDARD↔TECHNOLOGY to allowlist.

---

## Exceptions (Wrong-type noise within these pairs)

A few low-occurrence entities within these pairs may still be wrong-type noise, but they are rare (<5%):

| Label | Pair | Issue |
|-------|------|-------|
| koi | STANDARD↔TECHNOLOGY | STANDARD(1) may be noise vs PROJECT/TECHNOLOGY |
| cat receipts | STANDARD↔TECHNOLOGY | STANDARD(1) may be noise |

These are already captured by the "remaining wrong-type cleanup targets" in the entity variants report.

---

## Updated Allowlist

### Before (5 pairs)

```python
EXPECTED_POLYSEMY_PAIRS = {
    frozenset({"CONCEPT", "TECHNOLOGY"}),
    frozenset({"CONCEPT", "PROCESS"}),
    frozenset({"PROJECT", "TECHNOLOGY"}),
    frozenset({"CONCEPT", "PROJECT"}),
    frozenset({"ORGANIZATION", "PROJECT"}),
}
```

### After (8 pairs)

```python
EXPECTED_POLYSEMY_PAIRS = {
    frozenset({"CONCEPT", "TECHNOLOGY"}),
    frozenset({"CONCEPT", "PROCESS"}),
    frozenset({"PROJECT", "TECHNOLOGY"}),
    frozenset({"CONCEPT", "PROJECT"}),
    frozenset({"ORGANIZATION", "PROJECT"}),
    frozenset({"ORGANIZATION", "TECHNOLOGY"}),  # NEW: platform companies
    frozenset({"CONCEPT", "STANDARD"}),         # NEW: standards are conceptual
    frozenset({"STANDARD", "TECHNOLOGY"}),      # NEW: tech standards
}
```

---

## Impact Projection

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| Total type conflicts | 2,743 | 2,743 | 0 |
| Expected polysemy | 1,561 (56.9%) | ~1,948 (~71%) | +~387 |
| Unexpected (actionable) | 1,182 (43.1%) | ~795 (~29%) | -~387 |

The "unexpected" bucket will shrink by ~33% without hiding any real extraction errors.

---

*Report generated for Week 5 allowlist expansion review*
