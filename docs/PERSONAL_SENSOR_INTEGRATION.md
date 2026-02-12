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

Notes:

- Email documents are stored in `koi_memories` with `source_sensor='email-sensor'`.
- Claude sessions are stored in dedicated `session_ingestion_log`, `session_chunks`, and `session_tool_usage` tables.

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

- `POST /search-sessions`
- `GET /session-stats`
- `GET /session-tools`
- `GET /session-files`

## Local Verification Checklist

1. Email sensor inserts rows with `source_sensor='email-sensor'`.
2. `email_metadata` rows exist and link to `koi_memories.id`.
3. Claude sessions sensor populates `session_ingestion_log` and `session_chunks`.
4. Session endpoints return results for indexed sessions.
