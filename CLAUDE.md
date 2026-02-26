# Project Context for Claude

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

## KOI-net Vault Sync — Phase Sync-1.5 SOAK IN PROGRESS (2026-02-26)

Implementation complete. 39/39 tests pass. Two-peer smoke 15/15 (watcher off + on).
Runtime SHA: `5ddd839e`. Soak started 2026-02-26T04:31:19Z, go/no-go at 72h.

Added in Sync-1.5:
- SyncMetrics (23 fields, persisted to JSONB singleton table)
- VaultWatcher (watchdog-based, debounce, fail-open)
- Backpressure caps (file/byte/event per scan, delete reserve)
- Reconcile endpoint (detect drift, gated repair mode)
- Structured logging (key=value format)

Soak runbook: `docs/runbooks/vault-sync-soak.md`
Canonical phased roadmap: `docs/planning/KOI_NET_VAULT_SYNC_ROADMAP.md`

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

**Last Updated**: 2026-02-25
**Phase**: Complete - All major milestones achieved + Personal KOI active development + TerminusDB Phase 1 validated + Vault Sync Phase Sync-1 validated

---

## Session History

| Session ID | Date | Scope | Key Work |
|------------|------|-------|----------|
| `df92b730` | 2026-02-25 | koi-processor | Phase 1 TDB smoke test: fresh import, health/outbox/auth/fail-open/idempotency/reconciliation all pass. Fixed vault_parser.py SAVEPOINT bug. Created smoke_phase1.sh. Updated README + CLAUDE.md. Committed + pushed. |
| `371b493e` | 2026-02-25 | koi-processor | Phase A graph traversal: neighborhood + shortest-path endpoints via PG recursive CTEs. Direction param on /relationships. 33/33 tests pass. EXPLAIN ANALYZE confirms sub-3ms latency. |
| `17263f5c` | 2026-02-25 | koi-processor | Vault Sync Phase Sync-1: implemented VaultSyncManager, smoke test script, 17 unit tests. Two-peer smoke validated (15/15) between darren-personal ↔ nuc-personal. Fixed 3 bugs: WireManifest field stripping, poll manifest preservation, FORGET origin_seq monotonicity. |
| current | 2026-02-26 | koi-processor | Vault Sync Phase Sync-1.5: 5 WPs (metrics, logging, backpressure, watcher, reconcile). 39/39 tests. Deployed to both peers (SHA 5ddd839e). Fixed smoke test tilde-expansion bug. 15/15 smoke (watcher off + on). Soak started 2026-02-26T04:31Z. |
