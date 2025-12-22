# Agent Task: Stage 6 — Full Re-Extraction (Gemini → PostgreSQL → Fuseki)

## Context

FIX-001 through FIX-005 are **DEPLOYED** (regen-prod `601ef9d1`).

Stage 6 is a **clean rebuild** of the knowledge graph from the full KOI corpus.
Stage 6 corpus for this run is **natural-language only**:

- Include: Discourse + Notion + Website + other non-repo sources
- Include (repo sources): **GitHub + GitLab docs only** (by `metadata.file_path`)
- Exclude: all repo code/config chunks, and **exclude repo rows where `file_path` is NULL** (issues/PRs/discussions can be a later incremental pass)

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

Expected for docs-only Stage 6: ~11–12k docs (will vary slightly as sensors update).

This count must match the exact filter in:
- `scripts/reextraction/stage6_canary_gemini.py`
- `scripts/reextraction/stage6_full_reextract_gemini.py`

```bash
ssh darren@202.61.196.119 "PGPASSWORD=$POSTGRES_PASSWORD psql -h localhost -p 5433 -U $POSTGRES_USER -d $POSTGRES_DB -c \"
SELECT COUNT(*) AS docs
FROM koi_memories
WHERE superseded_at IS NULL
  AND content->>'text' IS NOT NULL
  AND LENGTH(content->>'text') > 50
  AND (
    (source_sensor NOT ILIKE '%github%' AND source_sensor NOT ILIKE '%gitlab%')
    OR (
      (source_sensor ILIKE '%github%' OR source_sensor ILIKE '%gitlab%')
      AND (metadata ? 'file_path')
      AND (metadata->>'file_path') IS NOT NULL
      AND (
        (metadata->>'file_path') ~* '[.](md|mdx|rst|txt)$'
        OR (metadata->>'file_path') ~* '(^|/)(readme|license|changelog)([.].*)?$'
        OR (metadata->>'file_path') ILIKE '%/docs/%'
      )
      AND (metadata->>'file_path') NOT ILIKE '%.pb.go'
      AND (metadata->>'file_path') !~* '/(node_modules|vendor|dist|build|generated)/'
      AND (metadata->>'file_path') !~* '/(test|tests|examples)/'
      AND (metadata->>'file_path') !~* '_test[.][^/]+$'
    )
  );
\""
```

Optional (recommended): verify per-sensor distribution before starting.

```bash
ssh darren@202.61.196.119 "PGPASSWORD=$POSTGRES_PASSWORD psql -h localhost -p 5433 -U $POSTGRES_USER -d $POSTGRES_DB -c \"
SELECT split_part(source_sensor,'-',1) AS sensor, COUNT(*)
FROM koi_memories
WHERE superseded_at IS NULL
  AND content->>'text' IS NOT NULL
  AND LENGTH(content->>'text') > 50
  AND (
    (source_sensor NOT ILIKE '%github%' AND source_sensor NOT ILIKE '%gitlab%')
    OR (
      (source_sensor ILIKE '%github%' OR source_sensor ILIKE '%gitlab%')
      AND (metadata ? 'file_path')
      AND (metadata->>'file_path') IS NOT NULL
      AND (
        (metadata->>'file_path') ~* '[.](md|mdx|rst|txt)$'
        OR (metadata->>'file_path') ~* '(^|/)(readme|license|changelog)([.].*)?$'
        OR (metadata->>'file_path') ILIKE '%/docs/%'
      )
      AND (metadata->>'file_path') NOT ILIKE '%.pb.go'
      AND (metadata->>'file_path') !~* '/(node_modules|vendor|dist|build|generated)/'
      AND (metadata->>'file_path') !~* '/(test|tests|examples)/'
      AND (metadata->>'file_path') !~* '_test[.][^/]+$'
    )
  )
GROUP BY 1
ORDER BY 2 DESC;
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
-- Recommended for a clean Stage 6 rerun without destroying other history:
DELETE FROM koi_kg_extractions
WHERE extractor_version = 'stage6-gemini'
  AND extraction_rid LIKE '%:stage6:%';
```

Also remove any previous checkpoint before starting fresh:

```bash
rm -f /opt/projects/koi-processor/scripts/reextraction/.stage6_checkpoint.json
```

## Step 3: Canary Run (10 docs)

Run:

```bash
cd /opt/projects/koi-processor
unset OPENAI_API_KEY
PYTHONPATH=src ./.venv/bin/python scripts/reextraction/stage6_canary_gemini.py 10
```

Canary pass criteria:
- 10/10 docs processed without exceptions
- `SELECT COUNT(*) FROM entity_registry WHERE fuseki_uri LIKE 'http://%';` returns 0
- `SELECT COUNT(*) FROM koi_relationships;` is > 0
- Spot-check console output: repo docs should show `file_path` ending in `.md/.mdx/.rst/.txt` (no `.go/.py/.tsx/.json`)

## Step 4: Full Re-Extraction Script (batch + checkpoint)

Run the existing full extraction script (docs-only corpus filter is built in):

```bash
cd /opt/projects/koi-processor
unset OPENAI_API_KEY

# Recommended: run under screen/tmux
screen -S stage6

PYTHONPATH=src ./.venv/bin/python scripts/reextraction/stage6_full_reextract_gemini.py --batch-size 50 --rate-limit 0.5

# Detach: Ctrl+A, D
```

Notes:
- Stable iteration: ordered by `koi_memories.id` (UUID) and checkpointed.
- Checkpoint file: `scripts/reextraction/.stage6_checkpoint.json`
- Writes:
  - `koi_kg_extractions` (per doc provenance)
  - `entity_registry` (pipeline-passed entities only)
  - `koi_relationships` (lookup-only insert to avoid double-counting entity occurrence counts)

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
