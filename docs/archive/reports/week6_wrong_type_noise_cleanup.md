# Week 6 Wrong-Type Noise Cleanup Report

**Date:** 2025-12-24
**Database:** eliza (production)
**Action:** Delete wrong-type entity_registry rows with zero references

---

## Summary

| Metric | Value |
|--------|-------|
| Rows deleted | 8 |
| Occurrences removed | 10 |
| Relationships affected | 0 |
| Chunk links affected | 0 |

---

## Deletion Targets (From Week 6 Analysis)

These entities were identified as wrong-type noise in the unexpected pairs report:

### FIX-013: Code Modules as PROCESS

| ID | entity_text | entity_type | occurrence_count | Correct Type |
|----|-------------|-------------|------------------|--------------|
| 29013 | EntityQualityFilter | PROCESS | 1 | TECHNOLOGY |
| 29012 | CanonicalResolver | PROCESS | 1 | TECHNOLOGY |
| 29011 | ConfidenceFilter | PROCESS | 1 | TECHNOLOGY |
| 29014 | ListSplitter | PROCESS | 1 | TECHNOLOGY |
| 29015 | OntologyNormalizer | PROCESS | 1 | TECHNOLOGY |

### FIX-014: Abstract Concepts as MATERIAL

| ID | entity_text | entity_type | occurrence_count | Correct Type |
|----|-------------|-------------|------------------|--------------|
| 25478 | biodiversity | MATERIAL | 1 | CONCEPT |
| 25638 | carbon sequestration | MATERIAL | 1 | CONCEPT |
| 8842 | ecological assets | MATERIAL | 3 | CONCEPT |

---

## Safety Verification

All 8 rows verified to have:
- 0 relationships as subject
- 0 relationships as object
- 0 chunk links

Query used:
```sql
WITH noise_candidates AS (
    SELECT e.id, e.entity_text, e.entity_type, e.normalized_text, e.occurrence_count, e.fuseki_uri
    FROM entity_registry e
    WHERE (
        (LOWER(e.entity_text) IN (
            'entityqualityfilter', 'canonicalresolver', 'confidencefilter',
            'documentleveldeduplicator', 'ontologynormalizer', 'listsplitter'
        ) AND e.entity_type = 'PROCESS')
        OR
        (LOWER(e.entity_text) IN (
            'biodiversity', 'carbon sequestration', 'ecological assets',
            'sustainability', 'regeneration', 'ecosystem services'
        ) AND e.entity_type = 'MATERIAL')
    )
),
refs AS (
    SELECT nc.id,
        COALESCE(subj.subj_count, 0) as rel_as_subject,
        COALESCE(obj.obj_count, 0) as rel_as_object,
        COALESCE(cl.chunk_links, 0) as chunk_links
    FROM noise_candidates nc
    LEFT JOIN (SELECT subject_entity_id, COUNT(*) as subj_count FROM koi_relationships GROUP BY subject_entity_id) subj
        ON nc.id = subj.subject_entity_id
    LEFT JOIN (SELECT object_entity_id, COUNT(*) as obj_count FROM koi_relationships GROUP BY object_entity_id) obj
        ON nc.id = obj.object_entity_id
    LEFT JOIN (SELECT entity_uri, COUNT(*) as chunk_links FROM koi_entity_chunk_links GROUP BY entity_uri) cl
        ON nc.fuseki_uri = cl.entity_uri
)
SELECT nc.*, r.rel_as_subject, r.rel_as_object, r.chunk_links,
       (r.rel_as_subject + r.rel_as_object + r.chunk_links) as total_refs
FROM noise_candidates nc
JOIN refs r ON nc.id = r.id
WHERE (r.rel_as_subject + r.rel_as_object + r.chunk_links) = 0
ORDER BY total_refs, nc.occurrence_count;
```

**Result:** All 8 rows have 0 total references.

---

## Deletion Command

```sql
BEGIN;

DELETE FROM entity_registry
WHERE id IN (29013, 29012, 29011, 29014, 29015, 25478, 25638, 8842);

COMMIT;
```

---

## Post-Deletion Verification

**Deletion confirmed:** `DELETE 8`

**Remaining check:** 0 rows remaining

**Fuseki rebuild:**
```
✓ Entities exported: 29,641
✓ Relationships exported: 15,364
✓ Triples created: 163,569
✓ Fuseki cleared: True
✓ Fuseki loaded: True
✓ Fuseki triple count: 163,569
```

---

## Metrics Update

| Metric | Post-Week 5 | Post-Week 6 | Change |
|--------|-------------|-------------|--------|
| entity_registry rows | 29,649 | 29,641 | -8 |
| Fuseki triples | 163,609 | 163,569 | -40 |
| Quality Gates | 4/4 PASS | 4/4 PASS | — |

---

*Cleanup performed as part of Cycle 2026-01 Week 6*
