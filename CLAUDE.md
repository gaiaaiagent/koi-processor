# Project Context for Claude

> **DEPLOY WARNING**: This checkout (`koi-processor`, branch `regen-prod`) is NOT the deploy
> source for the personal-koi server or NUC federation. The deploy source is the **koi-server
> worktree** (`~/projects/RegenAI/koi-server`, branch `server/stable`). `deploy.sh` in the
> Dobby repo rsyncs from koi-server, not here. Cherry-pick commits from koi-server into this
> repo to keep history aligned, but do not expect edits here to reach production.

**Project**: Regen Network Knowledge Graph Quality Improvement
**Status**: ✅ COMPLETE - Production Deployed (2025-12-25)
**Your Role**: AI coding assistant helping with knowledge graph quality

---

## What This Project Is

Improving the quality of Regen Network's knowledge graph (KOI system) through:
1. Better entity extraction and linking
2. Modular post-processing pipeline
3. Hybrid Graph-Boosted RAG for retrieval

**Result**: Quality improved from 62% to 99.7%

---

## Current State (2025-12-25)

### Stage 6 Re-Extraction - COMPLETE

| Metric | Value |
|--------|-------|
| Documents processed | 12,002 |
| Entities extracted | 88,322 |
| Relationships | 17,329 |
| Unique entities (entity_registry) | 29,641 |
| Unique relationships | 15,498 |

### FIX-007 Predicate Consolidation - COMPLETE

| Metric | Before | After |
|--------|--------|-------|
| Distinct predicates | 3,303 | 1,501 |
| Relationships | 15,757 | 15,414 |

### FIX-015 Predicate Type Guard - COMPLETE

| Component | Status |
|-----------|--------|
| Type constraints | 11 predicates with subject/object type rules |
| Env vars | `PREDICATE_GUARD_VALIDATE_TYPES`, `PREDICATE_GUARD_STRICT_TYPES` |
| Violations cleaned | 171 type-invalid relationships deleted |
| Backup table | `koi_relationships_backup_fix015b` |

### Production Deployment - COMPLETE

| Endpoint | Triples | Status |
|----------|---------|--------|
| /koi (production) | 163,703 | ✅ Deployed |
| /koi-staging | 163,703 | ✅ Deployed |

## TerminusDB Graph Mirror (Phase 1, 2026-02-25)

Status: code-complete and smoke-validated in local environment.

Architecture:
- PostgreSQL is authoritative.
- `terminusdb_outbox` stores async graph-write intents in the same PG transaction.
- `scripts/terminusdb/outbox_worker.py` drains outbox rows to TerminusDB.
- `api/terminusdb_adapter.py` enforces schema guard (`schema_ok`) and idempotent upserts.

Operational docs:
- `scripts/terminusdb/README.md`
- `scripts/terminusdb/smoke_phase1.sh`

Critical run command pattern (for env propagation to child processes):
```bash
set -a; source config/personal.env; set +a
```

## Graph Traversal (Phase A, 2026-02-25)

PostgreSQL recursive CTE-based graph traversal. No TDB dependency.

Key files:
- `api/graph_queries.py` — static SQL CTEs + async functions (neighborhood, shortest-path, directed relationships)
- `api/personal_ingest_api.py` — endpoints + Pydantic models (`GraphNode`, `GraphEdge`, `NeighborhoodResponse`, `PathStep`, `ShortestPathResponse`)
- `api/vault_parser.py` — `get_entity_relationships()` now delegates to `graph_queries.get_relationships_directed()`

Endpoints:
- `GET /relationships/{entity_uri:path}` — added `direction` param (backward-compatible, default `"both"`)
- `GET /graph/neighborhood/{entity_uri:path}` — multi-hop BFS neighborhood (max_depth=4, max_nodes=500, 5s timeout)
- `GET /graph/shortest-path?source=...&target=...` — BFS shortest path (max_depth=8, deterministic edge selection)

Safety: auth guard (`_check_graph_auth`), frontier fanout guard (CTE capped at max_nodes*3), asyncpg timeout=5.0.

Tests:
- `tests/test_graph_traversal.py` — 21 isolated fixture tests (rollback transactions)
- `tests/test_graph_traversal_smoke.py` — 12 live-DB smoke tests (requires running API)

When schema mismatch is detected (`fuseki_uri` legacy schema), run:
```bash
python -m scripts.terminusdb.import_from_postgres --fresh
```

## Federation Validation Update (2026-02-25)

Live peer validation completed between local Darren node and blank-slate NUC peer:

- Bidirectional KOI-net edge approval and polling verified.
- Bidirectional `/koi-net/share` smoke test verified with receipt in `/koi-net/shared-with-me`.
- Bootstrap runbook validated on blank host path (`bootstrap-node.sh` + `setup-node.sh` + `validate-node.sh`).

Bug fix shipped:

- `GET /koi-net/shared-with-me?since=...` now binds `since` as `datetime` (previously `str`, causing asyncpg timestamptz binding 500s).

## KOI-net Vault Sync — Phase Sync-1 VALIDATED (2026-02-25)

Two-peer smoke test passes 15/15 between darren-personal and nuc-personal (Dobby).

Key files:
- `api/vault_sync.py` — VaultSyncManager (scan, trigger, apply, conflict, reconcile)
- `api/koi_net_router.py` — vault sync endpoints (configure, trigger, status)
- `api/koi_protocol.py` — WireManifest with `extra="allow"` for extension fields
- `migrations/049_vault_sync.sql` — schema (vault_sync_state, vault_sync_config, vault_sync_applied_events)
- `tests/test_vault_sync.py` — 39 unit tests (17 Sync-1 + 22 Sync-1.5)
- `scripts/federation/smoke-vault-sync.sh` — two-peer smoke test (15 checks)
- `scripts/federation/soak-check.sh` — periodic soak monitoring
- `migrations/050_vault_sync_metrics.sql` — metrics persistence table

Env vars: `VAULT_SYNC_ENABLED=true`, `VAULT_SYNC_FOLDER=Shared`, `VAULT_SYNC_REPAIR_ENABLED=false` (during soak)

Bugs found and fixed during live two-peer testing:
1. `WireManifest` Pydantic model stripped extension fields — `extra="allow"`.
2. Poll endpoint manifest transformation dropped custom fields — preserve via `dict(m)`.
3. FORGET `origin_seq` not incrementing — stale-event guard rejected deletes.
4. Smoke test tilde expansion in SSH remote commands — unquote paths for remote `~` expansion.

## KOI-net Vault Sync — Phase Sync-1.5 COMPLETE (2026-03-04)

Soak PASSED. 6+ days (2026-02-26 → 2026-03-04), zero rejected events, zero reconcile drift on both peers.
Runtime SHA: `5ddd839e` → `cf805a77` (E2EE upgrade during soak). 39/39 tests pass.

Added in Sync-1.5:
- SyncMetrics (23 fields, persisted to JSONB singleton table)
- VaultWatcher (watchdog-based, debounce, fail-open)
- Backpressure caps (file/byte/event per scan, delete reserve)
- Reconcile endpoint (detect drift, gated repair mode)
- Structured logging (key=value format)

Soak runbook: `docs/runbooks/vault-sync-soak.md`
Canonical phased roadmap: `docs/planning/KOI_NET_VAULT_SYNC_ROADMAP.md`

## KOI-net Vault Sync — E2EE (2026-03-03)

End-to-end encryption for vault sync using X25519 + ChaCha20-Poly1305. Zero new dependencies
(`cryptography>=42.0.0` already installed). File contents encrypted in event queue, transit, and relay —
plaintext only on endpoints (Obsidian vault).

Key files:
- `api/koi_encryption.py` — Core E2EE module (keygen, ECDH, encrypt/decrypt)
- `api/node_identity.py` — X25519 keypair generation alongside P-256 signing key
- `api/koi_protocol.py` — `encryption_key` field on `NodeProfile`
- `api/koi_net_router.py` — Peer encryption key stored on handshake
- `api/vault_sync.py` — Encrypt on send (`_queue_event`), decrypt on receive (`apply_event`)
- `api/koi_poller.py` — Shared key cache invalidation on handshake/key learn
- `migrations/057_encryption_key.sql` — `encryption_key TEXT` column on `koi_net_nodes`

Crypto stack: X25519 ECDH → HKDF-SHA256 → ChaCha20-Poly1305 (AEAD). AAD = event RID (path binding).
Backward compatible: plaintext fallback when peer lacks encryption key.

Env: No new env vars. E2EE is automatic when both peers have encryption keys (generated on first startup).

### Code↔Docs Bridge - COMPLETE

| Component | Count |
|-----------|-------|
| Code artifacts | 16,820 |
| Doc→code links | 6,453 |
| Entity→code links | 241 |
| AGE stub nodes | 5,464 |
| AGE edges | 6,463 |

### Quality Gates (All Passing)

| Gate | Check | Result |
|------|-------|--------|
| A | No http://regen.network/ | ✅ 0 |
| B1 | No ontology# types | ✅ 0 |
| B2 | No ontology# predicates | ✅ 0 |
| C | No self-ref triples | ✅ 0 |

---

## Runtime Convergence (2026-02-26)

This repo is the **canonical** KOI runtime. The Octo deployment repo pins a specific commit via `vendor/pin.txt` and syncs code with `vendor/sync.sh`.

### Capabilities Registry
- `api/capabilities.py` — Central registry of feature flags, loaded from env vars or named profiles (`personal`, `bkc_coordinator`, `bkc_leaf`)
- `DEPLOYMENT_PROFILE` env var selects which features are active

### Router Modules
Capability-gated endpoint groups, mounted conditionally at startup:
- `api/routers/graph_router.py` — `/graph/*` traversal + temporal queries (assertion history)
- `api/routers/web_router.py` — `/web/*` content curation (BKC only)
- `api/routers/github_router.py` — `/github/*` repo scanning (BKC only)
- `api/routers/vault_sync_router.py` — `/koi-net/vault-sync/*` (personal only)
- `api/routers/network_router.py` — `/network/*` coordinator aggregation (BKC coordinator only)

### Startup Profiles
- `api/profiles/personal.py` — Vault sync, TerminusDB adapter
- `api/profiles/bkc_coordinator.py` — Pipeline handlers, web/GitHub sensors
- `api/profiles/bkc_leaf.py` — Minimal (federation only)

### Migration Governance
- `migrations/052_koi_migrations_registry.sql` — Registry table (`migration_id`, `checksum`, `applied_at`)
- `migrations/baselines/` — Per-database manifests (`personal_koi.json`, `octo_koi.json`, `gv_koi.json`, `fr_koi.json`)
- `scripts/stamp_baseline.py` — Stamp existing migrations into registry with checksum verification
- Migration IDs are namespaced: `core:*`, `bkc:*`, `personal:*`

### Commons Intake Pipeline (2026-02-26)

Full intake workflow for federated knowledge contributions:
- **State machine:** `staged → approved → ingesting → (ingested | needs_merge_review | failed)`
- `api/commons_ingest_worker.py` — Async background worker (advisory locks, `FOR UPDATE SKIP LOCKED`, retry/backoff, stale lease reaper)
- Entity resolution with confidence thresholds: auto-merge ≥0.95, ambiguous 0.85-0.95 → merge candidate queue
- `COMMONS_INGEST_ENABLED=true` env var gates worker startup

New endpoints (in `api/koi_net_router.py`):
- `GET /koi-net/commons/intake` — List shares by status
- `POST /koi-net/commons/intake/decide` — Approve/reject a staged share
- `GET /koi-net/commons/intake/{share_id}/decisions` — Immutable decision audit trail
- `GET /koi-net/commons/intake/{share_id}/merge-candidates` — Ambiguous entity matches
- `POST /koi-net/commons/intake/{share_id}/resolve-merges` — Admin resolution of merge candidates

New migrations:
- `053_commons_decision_log.sql` — `koi_commons_decisions` table + expanded `intake_status` constraint
- `054_commons_merge_candidates.sql` — `koi_commons_merge_candidates` table

New env vars:
- `COMMONS_INGEST_ENABLED` — Enable the async ingest worker (default: `false`)
- `KOI_COMMONS_SERVICE_TOKEN` — Bearer token for remote BFF access to commons admin endpoints

### Chat Endpoint (2026-02-26)

`POST /chat` — RAG-powered conversational interface:
- Semantic search over entity embeddings (pgvector)
- Falls back to text search if no embedding available
- Calls LLM (configurable via `CHAT_LLM_MODEL`, default: `gpt-4o-mini`) for grounded answer
- Returns `{ answer, sources, intent }`
- Requires `OPENAI_API_KEY`; returns 503 if unavailable

### GraphRAG Export Validation (2026-02-26)

Status: validated, ready to merge/deploy.

Validated change:
- `scripts/export_graph_hierarchy.py` now outputs full format (`entities`, `relationships`, `clusters`, `metadata`) to `graphrag_hierarchy.json` with hierarchical clustering and centrality fields.

Smoke test evidence:
1. Export run on live production DB (`max-entities=8000`) produced `/tmp/graphrag_hierarchy_candidate.json` (7362 entities, 13567 relationships, L1=233, L2=14).
2. Headless load test of `GAIA/graph/GraphRAG3D_EmbeddingView.html` succeeded with candidate JSON as primary dataset.
3. Core flows verified in browser automation: entity search/select, cluster focus, relationship line rendering.
4. No JS runtime exceptions; only expected optional-layout 404s (graphsage/force/community summary sidecar files).

### Contract Tests
- `tests/test_contract.py` — Behavioral contract suite (run against any profile, live server)
- `tests/test_interop_matrix.py` — Federation interop + commons correctness gates (C1-C3)

Run: `BASE_URL=http://127.0.0.1:8351 pytest tests/test_contract.py -v -m core`

---

## Key Scripts

### Re-Extraction
- `scripts/reextraction/stage6_full_reextract_gemini.py` - Stage 6 extraction (Gemini)
- `scripts/reextraction/stage6_reprocess_missing.py` - Reprocess failed docs

### Post-Processing
- `scripts/fix007_consolidate_predicates_postgres.py` - Predicate consolidation
- `scripts/regenerate_fuseki_graph.py` - Fuseki rebuild from PostgreSQL

### Docstring Semantic Extraction (Production Run 2026-02-19)
- `scripts/extract_docstring_semantics.py` - Route code docstrings through LLM semantic extractor
- `src/core/docstring_filter.py` - Filter/aggregate meaningful docstrings for LLM

| Repo | Files | Batches | Entities (raw → passed) | Relationships |
|------|-------|---------|------------------------|---------------|
| koi-processor | 213 | 232 | 1,402 → 1,112 | 66 |
| regen-ledger | 306 | 935 | 10,470 → 9,396 | 169 |
| **Total** | **519** | **1,167** | **11,872 → 10,508** | **235** |

Top entity types: API_MESSAGE (5,565), CONCEPT (3,463), TECHNOLOGY (960), PROCESS (190), MODULE (92), KEEPER (36), CREDIT_CLASS (27)

### Code Bridge
- `scripts/code_bridge/export_code_artifacts.py` - Populate code artifacts
- `scripts/code_bridge/link_docs_to_code.py` - Doc-level linking
- `scripts/code_bridge/link_entities_to_code.py` - Entity-level linking
- `scripts/code_bridge/sync_stubs_to_age.py` - AGE stub sync

---

## Quality Pipeline

6 modules: ConfidenceFilter, DocumentLevelDeduplicator, CanonicalResolver, OntologyNormalizer, ListSplitter, EntityQualityFilter

**Entity Deduplication:**
- Tier 1: Exact match (B-Tree, microseconds)
- Tier 2: Semantic match (HNSW vector, milliseconds)
- Tier 3: Create new (deterministic URI)

---

## Production Environment

**Server**: darren@202.61.196.119
**Code Path**: /opt/projects/koi-processor
**Database**: PostgreSQL (eliza) on port 5433 (Docker: gaia-postgres-1)
**Fuseki**: Apache Jena Fuseki on port 3030 (Docker: fuseki-koi)
**Graph URL**: https://regen.gaiaai.xyz/graph

**Environment setup:**
```bash
cd /opt/projects/koi-processor
set -a; source .env; set +a
```

---

## Documentation

- `docs/HYBRID_RAG_ARCHITECTURE.md` - Technical architecture
- `docs/CODE_DOCS_BRIDGE.md` - Code↔Docs bridge documentation
- `docs/CHANGELOG.md` - Version history
- `docs/planning/KOI_NET_VAULT_SYNC_ROADMAP.md` - Canonical phased vault-sync plan
- `docs/archive/knowledge-graph-review-2026-01.md` - Current cycle tracking doc

---

## Hybrid Search (2025-12-24)

**Status**: ✅ Fixed - keyword_score now working

**Root Cause**: RID mismatch in fusion - entity chunks (`UUID#chunk14`) didn't merge with keyword base docs (`UUID`)

**Key Files**:
- `koi-query-api.ts` - Keyword search with strict-first ordering
- `bge-mcp-ts/adaptive-features.ts` - Fusion with RID normalization
- `migrations/025_add_content_tsv_fts.sql` - FTS schema
- `scripts/backfill-fts.sql` - Backfill script

**Debug Flags**: `DEBUG_AUTH`, `DEBUG_EXTRACTION`, `DEBUG_FUSION`, `DEBUG_KEYWORD_SEARCH`, `DEBUG_GRAPH_EXPANSION`

---

## Graph Expansion PoC (2025-12-24)

**Status**: ✅ Deployed - log-only analysis

**Purpose**: Analyze potential recall gains from 1-hop relationship traversal without changing search results.

**How it works**:
1. Extract matched entity names from entity search results
2. Filter to multi-token names (>= 2 words OR >= 8 chars) to reduce noise
3. Look up entities in entity_registry using `normalized_text` index
4. Find 1-hop neighbors via koi_relationships (confidence >= 0.5, occurrence_count >= 2)
5. Count how many new docs the neighbors would add (skipped if > 10 neighbors)
6. Log the analysis (no ranking change)

**Key Function**: `get1HopNeighbors()` in `koi-query-api.ts`

**Filters/Guards**:
- Multi-token filter: Only entities with space or >= 8 chars used as seeds
- High-degree guard: Skips COUNT when neighbors > 10
- Quality thresholds: confidence >= 0.5, occurrence_count >= 2

**Sample Output**:
```
[GraphExpansion] Query: "Gregory Landua"
[GraphExpansion] Matched 1 entities: gregory landua
[GraphExpansion] Expanded to 5: Regen Network (ORGANIZATION), RND PBC (ORGANIZATION)
[GraphExpansion] Predicates: represents, associated_with, mentions, attended
[GraphExpansion] Would add 1667/1682 new docs (60 direct)
```

**Enabling**: Set `DEBUG_GRAPH_EXPANSION=true` in ecosystem.hybrid.config.js

---

## Polysemy Rerank (2025-12-26)

**Status**: ✅ Deployed - production enabled

**Purpose**: Boost search results that match a resolved entity when the query maps to a known entity in the knowledge graph.

**How it works**:
1. Query text is normalized and looked up in `entity_registry`
2. If a unique entity match is found, it becomes the "resolved entity"
3. Results containing that entity get a 1.15x score boost
4. The `resolved_entity` field is returned in the API response

**Key Functions**:
- `resolveQueryPolysemy()` in `koi-query-api.ts` - Entity resolution
- `applyPolysemyRerank()` in `koi-query-api.ts` - Score boosting

**Configuration** (in `ecosystem.hybrid.config.js`):
- `ENABLE_POLYSEMY_RERANK=true` - Enable/disable feature
- `DEBUG_POLYSEMY_RERANK=false` - Enable debug logging

**Response Fields**:
- `resolved_entity` - The matched entity (text, type, occurrence_count, etc.)
- `polysemy_debug` - Debug info (only when `DEBUG_POLYSEMY_RERANK=true`)

**Evaluation Results** (15-query test):
- Entity resolution rate: 60% (9/15 queries)
- Score improvement: +15% for resolved entities
- No regressions observed

---

## Future Work (Optional)

1. Further predicate reduction (1,501 → ~100-200)
2. ~10 snake_case entities cleanup
3. ✅ FIX-006 (entity dedup) - DEPLOYED 2025-12-23
4. FIX-008 (dual-write strategy review)
5. ✅ FIX-020 (alias audit/merge) - DEPLOYED 2025-12-29 (8 merges)
6. ✅ FIX-015 (predicate type guard) - DEPLOYED 2025-12-25
7. ✅ Polysemy rerank - DEPLOYED 2025-12-26

---

## New Scripts (2025-12-29)

| Script | Purpose |
|--------|---------|
| `scripts/alias_audit.py` | Generate audit report of alias duplicates |
| `scripts/apply_alias_merges.py` | Apply safe merges with backups |
| `scripts/export_graph_hierarchy.py` | Export to 3D viz format (→ GAIA/graph/) |
| `scripts/post_extraction_audit.sh` | Post-extraction quality checklist |

---

## Weekly Digest Cache Fix (2026-01-02)

**Status**: ✅ Deployed

**Root Cause**: Cache lookup ignored date parameters, returning stale 6+ day old digests

**Fix**: Date-range aware caching with new filename pattern `weekly_digest_{start}_to_{end}.md`

**Key Files**:
- `src/content/content_dashboard.py` - Cache lookup with date-range matching
- `src/content/weekly_curator_llm.py` - Date-range filename on export

---

## Event Bridge Routing Fix (2026-01-02)

**Status**: ✅ Fixed

**Root Cause**: Forwarder was sending to semantic bridge (port 8004) instead of v2 bridge (port 8100)

**Fix**: Set `EVENT_BRIDGE_URL=http://localhost:8100` and `EVENT_BRIDGE_ENDPOINT=/process-koi-event` in .env

---

## Fuseki Provenance Auth Fix (2026-01-02)

**Status**: ✅ Deployed

**Root Cause**: `provenance_to_rdf.py` didn't pass credentials for Fuseki writes

**Fix**: Added Basic Auth support via `FUSEKI_USER`/`FUSEKI_PASSWORD` env vars

**Runbook**: `docs/runbook-fuseki-provenance.md`

---

## Personal KOI Backend Bug Fixes (2026-02-09)

**Status**: ✅ Fixed & Deployed

**Bug 1 - Silent ingest failure**: `/ingest` endpoint caught INSERT exceptions silently, returning false success. Fixed by tracking `failed_entities` list and reporting in response stats with `success=False`.

**Bug 2 - False entity merge (token overlap)**: "Silke Helfrich" merged with "Simon Grant" because Jaro-Winkler score (0.6398) exceeded phonetic threshold (0.6) despite zero token overlap. Fixed by adding token overlap guard: if both names have 2+ tokens and share zero tokens, reject regardless of score.

**Key File**: `api/personal_ingest_api.py` (lines 723-755 for Bug 2, lines 1345-1418 for Bug 1)

**Commit**: `5a0dfa7e` on `feature/obsidian-kg-sync-plan`

---

## BKC Ontology Entity Types (2026-02-09)

**Status**: ✅ Committed

Added 9 new entity types for BKC COP project: Practice, Pattern, CaseStudy, Bioregion, Protocol, Playbook, Question, Claim, Evidence. Plus 15 new predicates (knowledge commoning, discourse graph, SKOS).

**Key Files**: `api/entity_schema.py`, `api/vault_parser.py`, `migrations/038_bkc_predicates.sql`

**Commit**: `4649e37d` on `feature/obsidian-kg-sync-plan`

---

**Last Updated**: 2026-03-11
**Phase**: Complete - All major milestones achieved + Personal KOI active development + TerminusDB Phase 1 validated + Vault Sync Sync-1.5 COMPLETE + E2EE COMPLETE + Invite-Token Onboarding + Claims Engine V2 Dogfooding Setup

---

## Invite-Token Peer Onboarding (2026-03-04)

One-command peer onboarding for KOI-net federation. Reduces interactive onboarding from ~30 min to ~5 min.

**New flow:** Admin creates invite token → peer runs `bootstrap-node.sh --invite <token>` → admin approves WG key → SAS verification over Signal → edges approved.

Key files:
- `scripts/federation/invite_token.py` — Token format (KOI-INVITE-1), HMAC signing/verification, pure stdlib
- `scripts/federation/create-invite.sh` — Admin generates invite token
- `scripts/federation/compute-sas.sh` — Admin computes SAS code for identity verification
- `scripts/federation/approve-peer-edges.sh` — Admin approves all PROPOSED edges to/from a peer
- `scripts/federation/bootstrap-node.sh` — `--invite` flag for token-driven flow
- `scripts/federation/approve-peer.sh` — `--pubkey-only` flag for invite flow approval
- `scripts/federation/lib.sh` — `compute_sas()`, `peer_registry_lookup_by_number()`, `decode_invite_token()`
- `api/koi_protocol.py` — `defer_approval` field on `HandshakeRequest`
- `api/koi_net_router.py` — Conditional inbound edge status (PROPOSED when deferred)

Trust model: Token carries config (relay info, IP) for convenience. Identity verification is SAS (6-digit code confirmed over Signal). HMAC is admin-side only (prevents forgery/tampering at creation time).

Backward compatible: Manual flow (bootstrap without `--invite`, connect-peers.sh) unchanged.

Runbook: `docs/runbooks/peer-onboarding.md` (updated with invite flow section)

---

## Claims Engine V2 Hardening (2026-03-09)

Status: deployed to koi-server (`server/stable` @ `1903b9a9`), 62 tests passing.

**Ghost anchor bug fix:** `broadcast_anchor()` timeout now returns `ready_to_anchor=False` with `tx_hash`. Claim stays at `verified` — no ghost `ledger_anchored` transitions.

**New endpoint:** `POST /claims/{rid}/reconcile` — checks on-chain tx status for claims with pending broadcasts. Four outcomes: `anchored` (transition), `pending` (retry later), `failed` (clear tx_hash, re-anchor), `pending` (tx not indexed yet).

**202 pending response:** `/anchor` returns `AnchorPendingResponse` (HTTP 202) when broadcast succeeds but on-chain confirmation times out or REST verify fails (indexing lag).

Key files:
- `api/ledger_anchor.py` — `verify_anchor_onchain()`, `query_tx_status()` (never raises)
- `api/routers/claims_router.py` — `AnchorPendingResponse`, `ReconcileResponse`, `/reconcile` endpoint
- `migrations/065_claims_tx_hash.sql` — `tx_hash TEXT` column on claims table
- `tests/test_claims_reconcile.py` — 16 pytest tests (in-process ASGI + monkeypatch)
- `scripts/test_claims_api.py` — 4 new HTTP smoke tests (tests 17-20)

MCP changes (personal-koi-mcp):
- `reconcile_claim` tool added
- `anchor_claim` handler updated for 202 pending responses
- `evals/claims_smoke.ts` — 8-tool MCP smoke test

Docs: [`docs/claims-engine-v1.md`](docs/claims-engine-v1.md)

---

## Claims Engine — Dogfooding Setup (2026-03-11)

Status: implemented. Team decision (Mar 10 call): use mainnet for all dogfooding — "mainnet is our testnet" (Gregory).

**New endpoint:** `GET /claims/chain-info` — returns `{chain_id, rpc_url, is_testnet}` for portal and eval harness chain detection.

**Dynamic chain_id:** State log entries now use `f"Anchored on Regen Ledger ({chain_id})"` instead of hardcoded "mainnet".

**Portal testnet indicator:** `static/demo.html` fetches `/claims/chain-info` on load — shows yellow "TESTNET" badge and chain_id in status bar when `is_testnet=true`. On mainnet, shows "Connected — regen-1".

**Eval harness:** `scripts/eval_claims_pipeline.py` — runs full 8-step claims lifecycle (create → attest → verify → prepare-anchor → anchor → proof-pack), produces structured JSON metrics. Flags: `--runs N`, `--skip-anchor`, `--save`, `--compare`. Stdlib-only, no external deps.

Key files:
- `api/routers/claims_router.py` — chain-info endpoint + dynamic chain_id in state log
- `static/demo.html` — testnet badge + chain_id in status bar
- `scripts/eval_claims_pipeline.py` — pipeline eval harness
- `config/personal.env` — commented testnet config block (optional)
- `docs/claims-engine-v1.md` — eval harness docs section

---

## Session History

| Session ID | Date | Scope | Key Work |
|------------|------|-------|----------|
| `df92b730` | 2026-02-25 | koi-processor | Phase 1 TDB smoke test: fresh import, health/outbox/auth/fail-open/idempotency/reconciliation all pass. Fixed vault_parser.py SAVEPOINT bug. Created smoke_phase1.sh. Updated README + CLAUDE.md. Committed + pushed. |
| `371b493e` | 2026-02-25 | koi-processor | Phase A graph traversal: neighborhood + shortest-path endpoints via PG recursive CTEs. Direction param on /relationships. 33/33 tests pass. EXPLAIN ANALYZE confirms sub-3ms latency. |
| `17263f5c` | 2026-02-25 | koi-processor | Vault Sync Phase Sync-1: implemented VaultSyncManager, smoke test script, 17 unit tests. Two-peer smoke validated (15/15) between darren-personal ↔ nuc-personal. Fixed 3 bugs: WireManifest field stripping, poll manifest preservation, FORGET origin_seq monotonicity. |
| `5ddd839e` | 2026-02-26 | koi-processor | Vault Sync Phase Sync-1.5: 5 WPs (metrics, logging, backpressure, watcher, reconcile). 39/39 tests. Deployed to both peers. 15/15 smoke (watcher off + on). Soak started 2026-02-26T04:31Z. |
| `684c3d97` | 2026-03-03 | koi-processor | E2EE for vault sync: X25519 + ChaCha20-Poly1305 encryption, zero new deps. Encrypt on send, decrypt on receive, backward-compatible plaintext fallback. Deployed to both nodes, migration 057 applied, handshake exchanged keys, verified ciphertext in event queue + plaintext delivery on NUC. Fixed koi-server start.sh (0.0.0.0 binding, increased health check retries). |
| `8ef466d5` | 2026-03-04 | koi-processor | Invite-token peer onboarding: 4 new scripts + 5 modified files. Token format (KOI-INVITE-1 + HMAC-SHA256), SAS verification, defer_approval handshake, resume-safe bootstrap, peer registry status machine (invited→approving→active). |
| `dcb9729d` | 2026-03-11 | koi-server | Federation domain event bridge: 6 domain types (entity/task/claim/attestation/commitment/pool), _koi_domain bypass, savepoint fix for relationship FK, state log dedup fix, 24 tests. Deployed to MacBook + NUC. |
