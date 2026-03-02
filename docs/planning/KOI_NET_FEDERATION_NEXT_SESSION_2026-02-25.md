# KOI-net Federation Next Session Plan (2026-02-25)

## Scope

Continue federation work from completed slices:
- Slice 1: KOI-net P2P sharing baseline
- Slice 2: Context depth traversal + missing reference reporting
- Slice 3: `recipient_type=peer|commons` + commons intake staging/decision APIs

## Current Implemented State

### API
- `POST /koi-net/share`
  - supports `share_mode`, `context_depth`, `recipient_type`
- `GET /koi-net/shared-with-me`
  - includes `recipient_type`, intake/review fields, dependency summaries
- `GET /koi-net/commons/intake`
- `POST /koi-net/commons/intake/decide` (localhost + admin token)

### DB
- Migration `047_shared_documents_intake.sql` applied on local `personal_koi`.

### MCP
- `share_document` supports `recipient_type`
- Added tools:
  - `commons_intake`
  - `commons_intake_decide`

## Open Technical Work

1. Real cross-node commons staging test
- Configure remote commons node with:
  - `KOI_COMMONS_INTAKE_ENABLED=true`
  - `KOI_COMMONS_AUTO_APPROVE=false`
- Verify inbound poll flow creates `intake_status=staged` records from real network events.

2. Intake-to-ingestion pipeline behavior
- Define exact ingest semantics on approve:
  - whether to trigger extraction/entity linking immediately
  - whether to maintain a separate `staged`/`approved` index visibility boundary.

3. Reference-pack policy hardening
- Add explicit policy controls for dependency inclusion on commons targets:
  - max dependencies
  - max payload bytes
  - max traversal depth per recipient class.

4. Governance/audit trail
- Add immutable intake decision log table (separate from mutable row state), including:
  - actor
  - timestamp
  - action
  - reason.

## Session Start Checklist

1. Start backend and verify:
- `GET /koi-net/health` is healthy.

2. Confirm schema:
- `SELECT column_name FROM information_schema.columns WHERE table_name='koi_shared_documents';`
  - must include `recipient_type`, `intake_status`, `reviewed_at`, `reviewed_by`, `review_notes`.

3. Verify admin token path:
- `$KOI_STATE_DIR/admin_token` exists.

4. Smoke commons intake endpoints:
- `GET /koi-net/commons/intake?status=all`
- `POST /koi-net/commons/intake/decide` with local bearer token.

## Definition of Done for Next Session

1. Real network share to a commons node arrives as `staged`.
2. Approval action changes intake state and triggers the intended downstream ingest behavior.
3. Rejection action keeps payload out of active ingest/search paths.
4. End-to-end runbook is documented with exact commands for Darren/Shawn + commons target.
