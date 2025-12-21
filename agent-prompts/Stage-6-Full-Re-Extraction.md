# Agent Task: Stage 6 — Full Re-Extraction (Gemini → PostgreSQL → Fuseki)

## Context

FIX-001 through FIX-005 are **DEPLOYED** (regen-prod `601ef9d1`).

Stage 6 is a **clean rebuild** of the knowledge graph from the full KOI corpus.
Target architecture:

1. Extract with `GeminiExtractor.extract_metadata()` (FIX-002 prompt + output contract)
2. Post-process with the **production pipeline config**:
   - `src/knowledge_graph/config/pipeline_config.json`
3. Persist to **PostgreSQL (authoritative)**:
   - `entity_registry` (nodes)
   - `koi_relationships` (edges)
   - `koi_kg_extractions` (per-document provenance record)
4. Rebuild **Fuseki is derived** from PostgreSQL via `scripts/regenerate_fuseki_graph.py`:
   - staging first (`KOI_STAGING=true`)
   - production second (`--confirm-prod`)

## Critical Notes (do not skip)

1. **Do not use `KnowledgeGraphIntegrator.integrate_document()` as the Stage 6 persistence path.**
   - `integrate_document()` still creates RDF URIs for entities in-memory and does not populate `entity_registry` for extracted entities.
   - Stage 6 must persist entities explicitly via `EntityResolver.get_or_create_entity()` after pipeline filtering.

2. **Avoid double-counting `entity_registry.occurrence_count`.**
   - Relationship persistence in `src/knowledge_graph/graph_integration.py` calls `EntityResolver.get_or_create_entity()` for subject/object, which increments counts.
   - If Stage 6 also persists entities from the entity list, you can accidentally increment counts twice.
   - Recommended approach in this prompt: persist entities once, then persist relationships via lookup-only join on `entity_registry` (no additional entity increments).

3. **Do not attempt to rebuild `koi_entity_chunk_links` in Stage 6 unless you add a dedicated rebuild job.**
   - The repo currently has no write-path for this table; it’s used by `koi-query-api.ts`.

## Read First (required)

| File | Why |
|------|-----|
| `src/extraction/gemini_extractor.py` | Extractor API + output keys (`extracted_entities`, `extracted_relationships`) |
| `src/knowledge_graph/config/pipeline_config.json` | Canonical pipeline ordering for Stage 6 |
| `src/knowledge_graph/postprocessing/pipeline.py` | How pipeline is built/registered |
| `src/knowledge_graph/entity_resolver.py` | EntityResolver DB behavior (Tier1/Tier2/Tier3) |
| `src/knowledge_graph/graph_integration.py` | Relationship schema + `normalize_predicate()` + FIX-003 inference/lookup helpers |
| `migrations/013_create_kg_tables.sql` | `koi_kg_extractions` schema (what fields exist) |
| `scripts/regenerate_fuseki_graph.py` | Fuseki rebuild procedure + safety latch |

## Environment Setup (server)

```bash
cd /opt/projects/koi-processor

# venv required on server
ls -la .venv/bin/python

# Gemini
export GEMINI_API_KEY="..."
export GEMINI_MODEL="gemini-3-flash-preview"
export GEMINI_DISABLE_SAFETY="true"
export GEMINI_THINKING_LEVEL="low"

# Postgres (set to the working server creds)
export POSTGRES_HOST="localhost"
export POSTGRES_PORT="5433"
export POSTGRES_DB="eliza"
export POSTGRES_USER="postgres"
export POSTGRES_PASSWORD="postgres"

# Fuseki (for rebuild step only)
export FUSEKI_USER="admin"
export FUSEKI_PASSWORD="admin"
export FUSEKI_ENDPOINT="http://localhost:3030/koi"
export FUSEKI_STAGING_ENDPOINT="http://localhost:3030/koi-staging"
```

## Step 0: Verify Corpus Size

Expected (from planning): ~30,904 docs.

```bash
ssh darren@202.61.196.119 "PGPASSWORD=$POSTGRES_PASSWORD psql -h localhost -p 5433 -U $POSTGRES_USER -d $POSTGRES_DB -c \"
SELECT COUNT(*) AS docs
FROM koi_memories
WHERE superseded_at IS NULL
  AND content->>'text' IS NOT NULL
  AND LENGTH(content->>'text') > 50;
\""
```

## Step 1: Backup (required)

```bash
ssh darren@202.61.196.119 \"mkdir -p /home/darren/backups\"
ssh darren@202.61.196.119 \"PGPASSWORD=$POSTGRES_PASSWORD pg_dump -h localhost -p 5433 -U $POSTGRES_USER $POSTGRES_DB | gzip > /home/darren/backups/eliza_pre_stage6_$(date +%Y%m%d_%H%M%S).sql.gz\"
```

## Step 2: Reset KG Tables (replace-in-place)

Stage 6 is a clean rebuild. After backup:

```sql
TRUNCATE TABLE koi_relationships RESTART IDENTITY;
TRUNCATE TABLE entity_registry RESTART IDENTITY;

-- Optional: keep koi_kg_extractions for audit history by NOT truncating.
-- If you want a clean provenance table too:
-- TRUNCATE TABLE koi_kg_extractions RESTART IDENTITY;
```

## Step 3: Canary Run (5 docs)

Create `scripts/reextraction/stage6_canary_gemini.py` on the server (or locally, then copy) and run it.

```python
#!/usr/bin/env python3
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import psycopg2
from psycopg2.extras import RealDictCursor

from extraction.gemini_extractor import GeminiExtractor
from knowledge_graph.graph_integration import KnowledgeGraphIntegrator, normalize_predicate


def infer_source_type(source_sensor: str) -> str:
    s = (source_sensor or "").lower()
    if "discourse" in s:
        return "discourse"
    if "github" in s:
        return "github"
    if "gitlab" in s:
        return "github"
    if "medium" in s:
        return "medium"
    if "twitter" in s:
        return "twitter"
    return "website"


async def main(limit: int = 5):
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    extractor = GeminiExtractor()
    kg = KnowledgeGraphIntegrator(store_type="memory", use_pipeline=True, enable_deduplication=True)

    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5433)),
        database=os.getenv("POSTGRES_DB", "eliza"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, rid, source_sensor, content->>'text' AS text
            FROM koi_memories
            WHERE superseded_at IS NULL
              AND content->>'text' IS NOT NULL
              AND LENGTH(content->>'text') > 200
            ORDER BY RANDOM()
            LIMIT %s
            """,
            (limit,),
        )
        docs = cur.fetchall()

    ok = 0
    for doc in docs:
        source_type = infer_source_type(doc["source_sensor"])

        extraction = await extractor.extract_metadata(
            doc["text"],
            source_type,
            existing_metadata={"rid": doc["rid"]},
        )

        raw_entities = extraction.get("extracted_entities", [])
        raw_relationships = extraction.get("extracted_relationships", [])

        # Run pipeline on full doc context (entities + relationships)
        context = kg.pipeline.process_entities(
            raw_entities,
            raw_relationships,
            metadata={"memory_rid": doc["rid"], "run_id": run_id, "source_type": source_type},
        )

        # Persist entities once per doc (doc-level dedup already applied by pipeline)
        for e in context.entities:
            kg.entity_resolver.get_or_create_entity(
                e.name,
                e.type,
                metadata={"doc_rid": doc["rid"], "run_id": run_id, "source_type": source_type},
            )

        # Persist relationships via lookup-only to avoid incrementing occurrence_count again
        with kg.pg_conn.cursor() as pg_cur:
            for r in context.relationships:
                subj = kg._find_existing_entity_by_name(r.source)
                obj = kg._find_existing_entity_by_name(r.target)
                if not subj or not obj:
                    continue

                pred = normalize_predicate(r.predicate)
                if not pred:
                    continue

                pg_cur.execute(
                    """
                    INSERT INTO koi_relationships
                      (subject_entity_id, predicate, object_entity_id, confidence, last_doc_rid, last_run_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
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
                    """,
                    (subj.entity_id, pred, obj.entity_id, r.confidence, doc["rid"], run_id),
                )
        kg.pg_conn.commit()

        ok += 1
        print(f"[canary] {doc['rid']} -> raw_e={len(raw_entities)} raw_r={len(raw_relationships)} "
              f"passed_e={len(context.entities)} passed_r={len(context.relationships)} blocked_e={len(context.blocked_entities)}")

    conn.close()
    kg.log_entity_stats()
    print(f"[canary] ok={ok}/{len(docs)} run_id={run_id}")


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    asyncio.run(main(limit))
```

Run:

```bash
cd /opt/projects/koi-processor
PYTHONPATH=src ./.venv/bin/python scripts/reextraction/stage6_canary_gemini.py 5
```

Canary pass criteria:
- 5/5 docs processed without exceptions
- `SELECT COUNT(*) FROM entity_registry WHERE fuseki_uri LIKE 'http://%';` returns 0
- `SELECT COUNT(*) FROM koi_relationships;` is > 0

## Step 4: Full Re-Extraction Script (batch + checkpoint)

Create `scripts/reextraction/stage6_full_reextract_gemini.py` by adapting the canary into:

- Stable iteration by `koi_memories.id` (order by id, resume by last id)
- Batch size default 50 (tune for rate limits)
- Checkpoint file `scripts/reextraction/.stage6_checkpoint.json` containing:
  - `run_id`
  - `last_koi_memories_id`
  - `processed_count`
  - `error_count`
- Writes:
  - `koi_kg_extractions` for each memory (extraction_type=`passA`, extractor_version=`stage6-gemini`)
  - `entity_registry` via `EntityResolver.get_or_create_entity()` using **pipeline-passed entities only**
  - `koi_relationships` via lookup+upsert (as shown in canary)

Notes for `koi_kg_extractions` inserts:
- Use `memory_rid = koi_memories.rid`
- `extraction_rid` must be unique; include run_id (e.g. `"{memory_rid}:kg:passA:stage6:{run_id}"`)
- Store `entities` as the pipeline-passed entity dicts
- Store `relations` as `{subject, predicate, object, confidence}` using pipeline outputs
- `tokens_consumed`: if present in GeminiExtractor output under `token_usage.total_tokens`, store it; otherwise 0
- `cost_usd`: store 0 unless you compute cost explicitly

## Step 5: Post-Extraction Verification (PostgreSQL)

```sql
-- Entity count
SELECT COUNT(*) AS entity_count FROM entity_registry;

-- Type distribution (expect FIX-005 types to appear)
SELECT entity_type, COUNT(*) AS count
FROM entity_registry
GROUP BY entity_type
ORDER BY count DESC;

-- ENTITY should drop sharply (baseline was 15,558)
SELECT COUNT(*) AS entity_count FROM entity_registry WHERE entity_type = 'ENTITY';

-- MODULE distinct from PROJECT
SELECT entity_type, COUNT(*) FROM entity_registry
WHERE entity_type IN ('MODULE', 'PROJECT')
GROUP BY entity_type;

-- Relationships exist (baseline was 0)
SELECT COUNT(*) AS relationship_count FROM koi_relationships;

-- No HTTP URIs
SELECT COUNT(*) FROM entity_registry WHERE fuseki_uri LIKE 'http://%';
```

## Step 6: Rebuild Fuseki from PostgreSQL (staging → production)

Staging:

```bash
cd /opt/projects/koi-processor
KOI_STAGING=true PYTHONPATH=src ./.venv/bin/python scripts/regenerate_fuseki_graph.py
```

Production:

```bash
cd /opt/projects/koi-processor
PYTHONPATH=src ./.venv/bin/python scripts/regenerate_fuseki_graph.py --confirm-prod
```

## Deliverables

1. Canary output + run_id
2. Full run summary:
   - docs processed / errors
   - `entity_registry` count + `ENTITY` count
   - `koi_relationships` count
   - full type distribution (should include FIX-005 types)
3. Fuseki rebuild summary (staging then prod)
4. Recommendation to proceed to FIX-006 / FIX-007 tuning

## Rollback Plan

1. Restore Postgres from `/home/darren/backups/eliza_pre_stage6_*.sql.gz`
2. Re-run `scripts/regenerate_fuseki_graph.py --confirm-prod` to rebuild Fuseki from restored PostgreSQL

