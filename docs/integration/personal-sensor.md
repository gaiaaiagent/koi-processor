# Personal Sensor Integration

This document defines the contract between local personal sensors (`koi-sensors`) and the personal ingest/query API (`koi-processor`).

## Repositories in the Local Stack

- `koi-sensors`: Collects personal data (`email`, `claude_sessions`) and sends/ingests it.
- `koi-processor`: Stores/query personal data (`koi_memories`, `email_metadata`, `session_*` tables).
- `personal-koi-mcp`: MCP tool surface used by Claude Code.

## Canonical Sensor Identifiers

Use these source IDs consistently:

- `email-sensor`
- `claude-sessions-sensor`
- `research-paper-sensor`

Notes:

- Email documents are stored in `koi_memories` with `source_sensor='email-sensor'`.
- Claude sessions are stored in dedicated `session_ingestion_log`, `session_chunks`, and `session_tool_usage` tables.
- Research paper detections are stored in `koi_memories` with `source_sensor='research-paper-sensor'`.

## Research Paper Sensor

The local research author sensor monitors configured publication sources and queues new papers into the shared research corpus before deeper scientific-discourse extraction.

Current config:

- Config file: `config/research_author_sensors.yaml`
- Robert Ghrist sources: official UPenn preprints page and arXiv API query `au:"Robert Ghrist"`
- Corpus root: `/Users/darrenzal/Documents/Research/Papers`
- LaunchAgent label: `com.personal-koi.research-author-sensor`
- Schedule: daily at 07:35 local time

Behavior:

- Normalizes title, year, authors, URLs, arXiv IDs, and abstracts when available.
- Deduplicates against `manifest.jsonl`, existing author folders, and legacy `[year]` title formats.
- Scores project relevance for sheaves, discourse, lattices, network coordination, robotics, sensor networks, persistence, geometry, and topology.
- Writes queued paper folders with `metadata.yaml`, `abstract.md`, and `notes.md`.
- Downloads public PDFs when available.
- Emits idempotent personal-KOI events for newly queued papers.

### Agent-First Scientific Extraction

Scientific discourse extraction should default to an agent workflow, not unattended paid LLM API calls. This applies to both Codex and Claude Code: the agent reads exported prompt windows, writes validated JSON window outputs, and the backend still performs the canonical merge, entity resolution, fact writing, discourse move writing, quality review, and local artifact export.

Headless extraction transports are disabled by default. To run them intentionally, set `DOC_EXTRACTOR_ALLOW_HEADLESS_LLM=1`; optional fallbacks such as `DOC_EXTRACTOR_CLAUDE_P_FALLBACK=1` and `DOC_EXTRACTOR_OPENAI_FALLBACK=1` must also be explicitly enabled. This policy covers LLM extraction; embeddings still use the configured embedding provider.

Agent workflow:

```bash
# 1. RAG-index a converted paper without deep extraction.
python scripts/ingest_document.py \
  --source-path /Users/darrenzal/Documents/Research/Papers/authors/ghrist-robert/<paper>/extracted.md \
  --tier rag \
  --slug <paper> \
  --name "<paper title>" \
  --group-id sheaf-explorer

# 2. Export prompt windows for Codex/Claude Code agents.
python scripts/extract_deep_documents.py \
  --document-rid document:<sha> \
  --tier thorough \
  --export-window-prompts /tmp/paper-windows/<paper>

# 3. In Codex or Claude Code, assign one or more agents/subagents to read
#    window-NNN.prompt.md and write valid extractor JSON to window-NNN.json.

# 4. Import agent-produced JSON and run the canonical backend merge/write path.
DOC_EXTRACTOR_AGENT_WINDOW_DIR=/tmp/paper-windows/<paper> \
python scripts/extract_deep_documents.py \
  --document-rid document:<sha> \
  --tier thorough \
  --group-id sheaf-explorer
```

The corpus wrapper can use the same agent output directory when a paper folder is already selected:

```bash
DOC_EXTRACTOR_AGENT_WINDOW_DIR=/tmp/paper-windows/<paper> \
python scripts/ingest_research_papers.py \
  --corpus-root /Users/darrenzal/Documents/Research/Papers \
  --author ghrist-robert \
  --paper-id ghrist-robert/<paper> \
  --limit 1
```

Expected outputs remain `discourse-elements.json`, `triples.jsonl`, `quality-review.json`, and `ingest-result.json` in the paper folder, plus facts/entities/episodes/discourse rows in personal KOI.

## Required Migration for Email Metadata

Run this migration before using the email sensor:

```bash
psql "$PERSONAL_KOI_DB_URL" -f migrations/033_email_sensor_tables.sql
```

This creates:

- `email_metadata`
- supporting indexes and constraints

## Personal Session Endpoints

These endpoints are served by `api/personal_ingest_api.py`:

- `POST /search-sessions` — semantic search over session chunks
- `GET /session-stats` — index statistics
- `GET /session-tools` — query by tool/MCP usage
- `GET /session-files` — query by files accessed
- `GET /search-sessions-by-entity` — find sessions mentioning a specific entity

## Session-Entity Knowledge Graph Integration

The Claude sessions sensor extracts entities from session transcripts and links them to the personal knowledge graph via the `/ingest` endpoint.

**Flow**: Session transcript → chunk + embed → extract entities (OpenAI gpt-4o-mini) → `POST /ingest` with `replace_existing=True` → entity resolution (4-tier) → `document_entity_links` populated → entity notes gain `mentionedIn` links to sessions.

**Key parameters on `/ingest`**:
- `replace_existing: bool = False` — atomic delete+insert of existing links for a document_rid (enables idempotent reprocessing)
- `link_existing_only: bool = False` — skip Tier 3 entity creation, only link to existing entities

**Config** (`koi-sensors/sensors/claude_sessions/config.personal.yaml`):
- `entity_extraction.enabled` — gate all extraction
- `entity_extraction.link_existing` — gate `/ingest` call
- `entity_extraction.extract_new` — maps to `link_existing_only` flag (inverted)
- `entity_extraction.model` — LLM for extraction (default: `gpt-4o-mini`)
- `entity_extraction.max_chunks` — chunks sent to LLM (default: 5)

**Privacy**: Text is redacted before LLM call (`_redact_for_extraction()`) — strips env vars, API keys, connection strings, private keys, base64 blobs.

**Failure safety**: Extraction returns `(entities, success)`. On failure (`success=False`), existing links are preserved (no `/ingest` call). Only validated results (including valid empty `[]`) trigger `replace_existing`.

**Known limitation**: Redaction regex does not handle escaped quotes inside quoted env values. Accepted as impractical edge case.

**Required migration**:
```bash
psql "$PERSONAL_KOI_DB_URL" -f migrations/055_session_schema_governance.sql
```

## Local Verification Checklist

1. Email sensor inserts rows with `source_sensor='email-sensor'`.
2. `email_metadata` rows exist and link to `koi_memories.id`.
3. Claude sessions sensor populates `session_ingestion_log` and `session_chunks`.
4. Session endpoints return results for indexed sessions.
5. After entity extraction: `document_entity_links` contains rows with `claude-session:` RIDs.
6. `GET /search-sessions-by-entity?entity_name=...` returns sessions mentioning the entity.
7. `GET /entity/{uri}/mentioned-in` includes sessions alongside vault documents.
