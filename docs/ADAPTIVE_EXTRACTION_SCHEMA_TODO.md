# Adaptive Extraction Schema - TODO

**Created:** 2025-12-19
**Status:** Incomplete - schema mismatches blocking entity extraction

## Summary

The adaptive extraction system is designed to extract general entities (people, concepts, organizations) from content like YouTube transcripts and podcast episodes, storing them in Apache Jena Fuseki. The RAG search UNION fix is complete, but entity extraction to Jena is blocked by database schema mismatches.

## Current Architecture

```
┌─────────────────────────────┐
│     Apache AGE (PostgreSQL) │ ← Code entities (Functions, Classes)
│     Port: via PostgreSQL    │   Populated by code_graph_service.py
└─────────────────────────────┘

┌─────────────────────────────┐
│   Apache Jena Fuseki (3030) │ ← General entities (people, concepts)
│     155K existing triples   │   Should be populated by adaptive_extraction_api.py
└─────────────────────────────┘

Services:
- Code Graph Service (8350) - GitHub code → AGE
- Adaptive Extraction (8351) - YouTube/podcasts → Jena (BLOCKED)
- Hybrid RAG API (8301) - Unified search, triggers extraction
```

## Schema Issues

### 1. `koi_query_log` Table

**Current state:** Partially exists, missing columns

**Missing columns:**
```sql
ALTER TABLE koi_query_log ADD COLUMN IF NOT EXISTS result_count INTEGER;
ALTER TABLE koi_query_log ADD COLUMN IF NOT EXISTS response_time_ms INTEGER;
ALTER TABLE koi_query_log ADD COLUMN IF NOT EXISTS top_result_score DOUBLE PRECISION;
```

**Note:** The `id` column was changed from `INTEGER` to `TEXT` to support UUID-style IDs from the code.

### 2. `koi_adaptive_extractions` Table

**Current state:** Created but incomplete

**Missing columns (based on error logs):**
```sql
ALTER TABLE koi_adaptive_extractions ADD COLUMN IF NOT EXISTS cat_receipt_rid TEXT;
-- May need additional columns - audit src/core/adaptive_extractor.py lines 348-410
```

**Reference migration:** `/opt/projects/koi-processor/migrations/011_adaptive_knowledge_query_log.sql`

### 3. Potentially Missing Tables

The migration file also references:
- `koi_query_analytics` - analytics view/materialized view
- `koi_problematic_queries` - tracking low-confidence queries

## Migration Files to Review

```
/opt/projects/koi-processor/migrations/011_adaptive_knowledge_query_log.sql
```

**Issue:** Migration expects `koi_query_log.id` to be UUID, but code uses TEXT. Need to reconcile.

## Intended Entity Extraction Flow

```
1. User queries Hybrid RAG API (POST /api/koi/query)
2. Search returns results with confidence score
3. If confidence < 0.7, extraction is triggered
4. Hybrid RAG calls POST http://localhost:8351/extract
5. Adaptive extractor:
   a. Logs query to koi_query_log
   b. Calls OpenAI to extract entities from top documents
   c. Stores extractions in koi_adaptive_extractions
   d. Syncs extracted entities to Apache Jena via SPARQL
6. Future queries benefit from enriched knowledge graph
```

## Files to Audit

1. **Code that writes to database:**
   - `/opt/projects/koi-processor/src/core/adaptive_extractor.py` (lines 348-410)
   - `/opt/projects/koi-processor/adaptive_extraction_api.py` (lines 150-170)

2. **Expected table schemas:**
   - `/opt/projects/koi-processor/migrations/011_adaptive_knowledge_query_log.sql`

3. **Jena sync code:**
   - Check how extracted entities get synced to Jena Fuseki

## Environment Requirements

The adaptive extraction service requires:
```bash
# In .env file
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
OPENAI_EXTRACT_MODEL=gpt-4o-mini  # or gpt-4
POSTGRES_URL=postgresql://postgres:postgres@localhost:5433/eliza
```

## Quick Test Command

Once schema is fixed:
```bash
curl -X POST http://localhost:8301/api/koi/query \
  -H "Content-Type: application/json" \
  -d {question: Karl Friston active inference, limit: 5}
```

Check extraction logs:
```bash
tail -f /opt/projects/koi-processor/logs/adaptive_extraction.log
```

## Next Steps

1. Audit `adaptive_extractor.py` to identify ALL columns used
2. Create comprehensive schema migration
3. Test extraction end-to-end
4. Verify entities appear in Jena Fuseki
5. Consider batch extraction for existing YouTube content

---

**Related Fixes Completed (2025-12-19):**
- ✅ RAG search UNION query (YouTube now searchable)
- ✅ Adaptive extraction service running on port 8351
- ✅ Hybrid RAG configured to call 8351 for extraction
