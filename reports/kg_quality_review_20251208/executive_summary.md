# Knowledge Graph Quality Review - Executive Summary

**Date:** December 8, 2025
**Graph:** regen_graph (Production - 202.61.196.119:5433)
**Analyst:** Claude Code Audit

---

## Overall Health Score: 62/100

### Score Breakdown
| Category | Score | Weight | Weighted |
|----------|-------|--------|----------|
| Data Completeness | 75/100 | 20% | 15.0 |
| Entity Quality | 55/100 | 25% | 13.75 |
| Duplicate Management | 40/100 | 20% | 8.0 |
| Semantic Consistency | 60/100 | 15% | 9.0 |
| Provenance Quality | 80/100 | 10% | 8.0 |
| RAG Utility | 65/100 | 10% | 6.5 |
| **Total** | | 100% | **62.25** |

---

## Graph Statistics

| Metric | Value |
|--------|-------|
| Total Entities | 14,706 |
| Total Statements | 19,608 |
| Total Extractions | 4,547 |
| Source Memories | 49,027 |
| Date Range | Sep 27 - Dec 8, 2025 |

### Entity Type Distribution
| Type | Count | Percentage |
|------|-------|------------|
| Organization | 6,922 | 47.1% |
| Project | 5,154 | 35.0% |
| Person | 2,630 | 17.9% |

### Top Predicates
| Predicate | Count |
|-----------|-------|
| is | 688 |
| provides | 687 |
| has | 291 |
| supports | 269 |
| is associated with | 225 |

---

## Top 10 Critical Issues

### 1. **CRITICAL: Massive Case-Insensitive Duplicates** (Impact: HIGH)
- "Regen Network" has 4 variants (911 occurrences): "regen network", "Regen network", "Regen Network", "REGEN Network"
- "Regen" has 3 variants (465 occurrences as Org, 157 as Project)
- Estimated 20+ major entities with duplicate variants

**Recommendation:** Implement case-insensitive entity normalization during extraction.

### 2. **CRITICAL: Cross-Type Duplicates** (Impact: HIGH)
- 25 entities appear as both Organization AND Project
- Examples:
  - "Regen Registry": Organization (212) AND Project (187)
  - "Regen Ledger": Project (283) AND Organization (56)
  - "Cosmos SDK": Project (55) AND Organization (9)

**Recommendation:** Define canonical entity types and migrate misclassified entities.

### 3. **HIGH: Low-Confidence Entities Below 0.85 Threshold** (Impact: MEDIUM)
- 3,280 entities (22%) have confidence < 0.85
- Breakdown: 0.80-0.84: 2,382 | 0.70-0.79: 849 | Below 0.70: 49

**Recommendation:** Flag low-confidence entities for human review or exclusion from RAG.

### 4. **HIGH: Low-Confidence Statements** (Impact: MEDIUM)
- 9,507 statements (48.5%) have confidence < 0.85
- Only 214 statements (1%) have confidence >= 0.95

**Recommendation:** Consider higher extraction confidence thresholds.

### 5. **MEDIUM: Generic Noun Entities** (Impact: MEDIUM)
- 26 instances of generic nouns extracted as entities
- "User" (14), "farmers" (4), "company" (3), "protocol" (1)

**Recommendation:** Add generic noun blacklist to extraction pipeline.

### 6. **MEDIUM: Single-Word Plural Generic Persons** (Impact: MEDIUM)
- "scientists" (9), "validators" (7), "farmers" (4), "validator" (3), "delegator" (3)
- These should be filtered or converted to role tags

**Recommendation:** Exclude plural generic nouns from Person entity type.

### 7. **MEDIUM: Short Acronym Entities** (Impact: LOW)
- 20+ entities with 2-3 character names: RND (118), GoE (19), NCT (15), RF (13)
- Some valid (RND = Regen Network Development), some ambiguous

**Recommendation:** Require acronym expansion or context linking.

### 8. **LOW: Generic Predicates Overuse** (Impact: LOW)
- "is" (688), "has" (291), "are" (66) - 5.3% of all statements
- Many contain useful information, but some are noise (e.g., "has 0 replies, 3 views")

**Recommendation:** Implement predicate quality scoring.

### 9. **LOW: Unbalanced Source Coverage** (Impact: LOW)
- Website: 2,776 extractions (61%)
- Notion: 842 (19%)
- Discourse: 806 (18%)
- Podcast: 111 (2%)
- GitHub: 5 (<1%)

**Recommendation:** Increase GitHub and Podcast extraction coverage.

### 10. **INFO: No Detected Contradictions** (Impact: N/A)
- Contradiction detection table is empty (0 rows)
- Either no contradictions exist, or detection isn't running

**Recommendation:** Verify contradiction detection pipeline is operational.

---

## Recommended Immediate Fixes

### Priority 1: Entity Deduplication (Week 1)
1. Create canonical entity mapping for top 50 hub entities
2. Merge case-insensitive duplicates (script provided)
3. Resolve cross-type duplicates with domain expert input

### Priority 2: Quality Filtering (Week 1-2)
1. Add generic noun blacklist to extraction
2. Implement confidence threshold filtering (< 0.85)
3. Flag short acronyms for review

### Priority 3: Data Enrichment (Week 2-3)
1. Expand GitHub source coverage
2. Add entity aliases/synonyms
3. Create inverse relationship generation

---

## Long-Term Improvement Roadmap

### Phase 1: Data Cleanup (1-2 weeks)
- [ ] Run deduplication scripts on production
- [ ] Remove low-quality entities
- [ ] Normalize entity types

### Phase 2: Pipeline Improvements (2-4 weeks)
- [ ] Add entity resolution during extraction
- [ ] Implement cross-reference validation
- [ ] Add acronym expansion

### Phase 3: Ontology Enhancement (4-6 weeks)
- [ ] Define formal entity type hierarchy
- [ ] Create predicate taxonomy
- [ ] Implement semantic validation rules

### Phase 4: RAG Optimization (6-8 weeks)
- [ ] Build entity embedding index
- [ ] Create entity relationship graph
- [ ] Implement context-aware retrieval

---

## Files Generated

1. `executive_summary.md` - This document
2. `entity_quality_issues.csv` - Detailed entity issues
3. `duplicate_clusters.json` - Duplicate entity mappings
4. `statement_quality_issues.csv` - Statement issues
5. `provenance_scorecard.json` - Source quality metrics
6. `fix_scripts/` - Python/SQL cleanup scripts
7. `benchmarks.json` - Before/after metrics

---

## Methodology Notes

- Analysis performed via direct SQL queries on production database
- Confidence threshold set at 0.85 (strict) per user preference
- All queries read-only; no production data modified
- Fuzzy duplicate detection limited to case-insensitive matching (full Levenshtein analysis recommended for production)
