# Week 13 GraphRAG Context Evaluation Results

**Date:** 2025-12-25T04:53:14.966552
**API:** localhost:8301

## Summary

| Metric | Value |
|--------|-------|
| Total queries | 15 |
| Queries with graph context | 8 (53.3%) |
| Queries with dominant entity | 8 (53.3%) |
| Average edge count | 6.5 |
| Queries with truncated context | 4 |

## By Category

| Category | Total | With Context | % |
|----------|-------|--------------|---|
| Entity-Heavy | 8 | 4 | 50.0% |
| Ambiguous | 7 | 4 | 57.1% |

## Predicate Distribution (Top 10)

| Predicate | Count |
|-----------|-------|
| manages | 10 |
| associated_with | 9 |
| represents | 8 |
| mentions | 7 |
| operates | 7 |
| works_with | 6 |
| part_of | 6 |
| monitors | 6 |
| attended | 4 |
| uses | 4 |

## Detailed Results

### Gregory Landua

- **Category:** entity_heavy
- **Notes:** Known polysemic entity (PERSON vs ORG)
- **Confidence:** 0.627
- **Total results:** 41
- **Dominant entity:** Gregory Landua (PERSON, occ=684)
- **Edge count:** 20
- **Unique predicates:** 11
- **Truncated:** True
- **Top predicates:** associated_with(2), represents(2), attended(4), mentions(4), visited(1)

### Regen Network ecocredits

- **Category:** entity_heavy
- **Notes:** Core domain entity
- **Confidence:** 0.632
- **Total results:** 81
- **Dominant entity:** Regen Network (ORGANIZATION, occ=3702)
- **Edge count:** 20
- **Unique predicates:** 12
- **Truncated:** True
- **Top predicates:** works_with(2), associated_with(2), maintains(1), manages(3), part_of(2)

### CarbonPlus Grasslands credit class

- **Category:** entity_heavy
- **Notes:** Domain-specific credit class entity
- **Confidence:** 0.619
- **Total results:** 60
- **Dominant entity:** Credit Class (CONCEPT, occ=186)
- **Edge count:** 11
- **Unique predicates:** 10
- **Truncated:** False
- **Top predicates:** manages(1), governsissuanceof(1), operates_within(1), issued_under(1), supports(1)

### x/ecocredit module

- **Category:** entity_heavy
- **Notes:** Cosmos SDK module entity
- **Confidence:** 0.568
- **Total results:** 69
- **Graph context:** None returned

### Chorus One validator

- **Category:** entity_heavy
- **Notes:** Blockchain validator entity
- **Confidence:** 0.616
- **Total results:** 83
- **Graph context:** None returned

### Martin Wainstein

- **Category:** entity_heavy
- **Notes:** Person entity with relationships
- **Confidence:** 0.441
- **Total results:** 14
- **Graph context:** None returned

### NCT token

- **Category:** entity_heavy
- **Notes:** Technology/token entity
- **Confidence:** 0.528
- **Total results:** 59
- **Graph context:** None returned

### Cosmos SDK

- **Category:** entity_heavy
- **Notes:** Technology with many relationships
- **Confidence:** 0.560
- **Total results:** 79
- **Dominant entity:** Cosmos (PROJECT, occ=145)
- **Edge count:** 5
- **Unique predicates:** 5
- **Truncated:** False
- **Top predicates:** operates(1), uses(1), analyzes(1), operates_within(1), supports(1)

### How does the carbon credit retirement process work?

- **Category:** ambiguous
- **Notes:** Process-oriented query requiring relationship traversal
- **Confidence:** 0.544
- **Total results:** 75
- **Dominant entity:** carbon (MATERIAL, occ=33)
- **Edge count:** 0
- **Unique predicates:** 0
- **Truncated:** False

### Who founded Regen Network?

- **Category:** ambiguous
- **Notes:** Requires relationship extraction (founded)
- **Confidence:** 0.625
- **Total results:** 80
- **Dominant entity:** Regen Network (ORGANIZATION, occ=3702)
- **Edge count:** 20
- **Unique predicates:** 12
- **Truncated:** True
- **Top predicates:** works_with(2), associated_with(2), maintains(1), manages(3), part_of(2)

### What projects use x/group module?

- **Category:** ambiguous
- **Notes:** Requires graph expansion for uses relationship
- **Confidence:** 0.514
- **Total results:** 76
- **Graph context:** None returned

### Relationship between NCT and ecocredits

- **Category:** ambiguous
- **Notes:** Multi-entity query
- **Confidence:** 0.541
- **Total results:** 69
- **Dominant entity:** Ecocredits (PROJECT, occ=126)
- **Edge count:** 2
- **Unique predicates:** 2
- **Truncated:** False
- **Top predicates:** associated_with(1), issues(1)

### Where is Regen Network based?

- **Category:** ambiguous
- **Notes:** Location relationship query
- **Confidence:** 0.612
- **Total results:** 81
- **Dominant entity:** Regen Network (ORGANIZATION, occ=3702)
- **Edge count:** 20
- **Unique predicates:** 12
- **Truncated:** True
- **Top predicates:** works_with(2), associated_with(2), maintains(1), manages(3), part_of(2)

### What validators support Regen mainnet?

- **Category:** ambiguous
- **Notes:** Validator relationship query
- **Confidence:** 0.620
- **Total results:** 69
- **Graph context:** None returned

### How are credit classes created?

- **Category:** ambiguous
- **Notes:** Process understanding query
- **Confidence:** 0.572
- **Total results:** 73
- **Graph context:** None returned

