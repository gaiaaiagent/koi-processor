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
- `substack-corpus-backfill` — Substack corpus (Johar/Ruddick/Bauwens); RID `substack-corpus:<feed_slug>:<post_slug>`. See the "Substack corpus ingestion" section in the repo `CLAUDE.md`.
- RSS/Atom feed sensor (`rss-<slug>` access sources)
- `research-paper-sensor` — arXiv/author paper corpus
- `proton-email-sensor`

Notes:

- Email documents are stored in `koi_memories` with `source_sensor='email-sensor'`.
- Claude sessions are stored in dedicated `session_ingestion_log`, `session_chunks`, and `session_tool_usage` tables.
- **Deployment topology:** the personal-KOI sensor launchd jobs (`com.personal-koi.substack-*`, `research-author-sensor`, etc.) run from the dedicated **`~/projects/koi-processor-runtime`** clone pinned to `regen-prod` — NOT the dev checkout. To update sensor code: commit to `regen-prod`, then `git -C ~/projects/koi-processor-runtime pull`. (See DEPLOY TOPOLOGY in the repo `CLAUDE.md`.)

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
