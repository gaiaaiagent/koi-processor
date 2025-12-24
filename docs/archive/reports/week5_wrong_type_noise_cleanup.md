# Week 5 Wrong-Type Noise Cleanup Report

**Date:** 2025-12-24
**Database:** eliza (production)
**Action:** Delete wrong-type entity_registry rows with zero references

---

## Summary

| Metric | Value |
|--------|-------|
| Rows deleted | 6 |
| Occurrences removed | 8 |
| Relationships affected | 0 |
| Chunk links affected | 0 |

---

## Deletion Targets (From Week 4 Analysis)

These entities were identified as wrong-type noise in the entity variants report:

| Label | Wrong Type | Occurrences | Correct Types Retained |
|-------|------------|-------------|------------------------|
| notion | PROJECT | 1 | TECHNOLOGY (308), ORGANIZATION (27) |
| koi | CONCEPT | 2 | PROJECT (166), TECHNOLOGY (65) |
| koi | PERSON | 2 | PROJECT (166), TECHNOLOGY (65) |
| koi | STANDARD | 1 | PROJECT (166), TECHNOLOGY (65) |
| agent-based modeling | PROJECT | 1 | CONCEPT (178), TECHNOLOGY (4) |
| agent-based modeling | PROCESS | 1 | CONCEPT (178), TECHNOLOGY (4) |

---

## Safety Verification Queries

### Relationship Check

```sql
SELECT
    e.id, e.entity_text, e.entity_type,
    COALESCE(subj.subj_count, 0) as relationships_as_subject,
    COALESCE(obj.obj_count, 0) as relationships_as_object
FROM entity_registry e
LEFT JOIN (SELECT subject_entity_id, COUNT(*) as subj_count FROM koi_relationships GROUP BY subject_entity_id) subj
    ON e.id = subj.subject_entity_id
LEFT JOIN (SELECT object_entity_id, COUNT(*) as obj_count FROM koi_relationships GROUP BY object_entity_id) obj
    ON e.id = obj.object_entity_id
WHERE e.id IN (22981, 17220, 6928, 6535, 27552, 24272);
```

**Result:** All 6 rows have 0 relationships as subject AND 0 as object.

### Chunk Link Check

```sql
SELECT
    e.id, e.entity_text, e.entity_type, e.fuseki_uri,
    COUNT(cl.entity_uri) as chunk_links
FROM entity_registry e
LEFT JOIN koi_entity_chunk_links cl ON e.fuseki_uri = cl.entity_uri
WHERE e.id IN (22981, 17220, 6928, 6535, 27552, 24272)
GROUP BY e.id, e.entity_text, e.entity_type, e.fuseki_uri;
```

**Result:** All 6 rows have 0 chunk links.

---

## Rows Deleted

| ID | entity_text | entity_type | occurrence_count | fuseki_uri |
|----|-------------|-------------|------------------|------------|
| 6535 | KOI | CONCEPT | 2 | https://regen.network/concept/0e0b8dd3ff1d86fb |
| 6928 | Koi | PERSON | 2 | https://regen.network/person/4bc92156608025bf |
| 17220 | Agent-Based Modeling | PROJECT | 1 | https://regen.network/project/f89354515ee2b104 |
| 22981 | Agent-Based Modeling | PROCESS | 1 | https://regen.network/process/87c9b902d29e991f |
| 24272 | Notion | PROJECT | 1 | https://regen.network/project/afa20c31488348c3 |
| 27552 | KOI | STANDARD | 1 | https://regen.network/standard/b05b76263896c8a5 |

---

## Deletion Command

```sql
BEGIN;

DELETE FROM entity_registry
WHERE id IN (22981, 17220, 6928, 6535, 27552, 24272);

COMMIT;
```

**Result:** `DELETE 6` - 6 rows successfully removed.

---

## Post-Deletion Verification

```sql
SELECT COUNT(*) as remaining
FROM entity_registry
WHERE id IN (22981, 17220, 6928, 6535, 27552, 24272);
```

**Result:** 0 remaining (deletion confirmed).

---

## Fuseki Rebuild Required

Since entity_registry rows were deleted, the Fuseki graph needs to be rebuilt to remove the corresponding triples:

```bash
cd /opt/projects/koi-processor
export POSTGRES_HOST=localhost POSTGRES_PORT=5433 POSTGRES_DB=eliza POSTGRES_USER=postgres POSTGRES_PASSWORD=postgres
python3 scripts/regenerate_fuseki_graph.py
```

---

*Cleanup performed as part of Cycle 2026-01 Week 5*
