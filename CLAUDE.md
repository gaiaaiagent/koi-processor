<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/CLAUDE.md` "Stream-scope discipline" for rule. Sister surface for Codex sessions: `AGENTS.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-08-23 16:50 PDT

**Current status:** Twelve commits pushed (`763ede4..HEAD`), API restarted so all of it is live. **koi 7878, 8294 and 8299 are closed.** The resolver now uses the **strict** token-overlap policy on BOTH the fuzzy and semantic tiers (`active_policy="strict"`), each flipped only after being measured separately. Two open items are decisions, not tasks: koi **8368** (Document/Event) and koi **8292** (Task backfill).

**Resolver, settled today.** Fuzzy flipped on 1,110 replayed attempts across all 13 callers — on Meeting, legacy was wrong on **89.2%** of 259 attempts vs strict's **6.6%**. Semantic flipped separately on 204 observations: 17 divergences, **all Meeting (94.4%), all legacy accepting a different-dated meeting**, 0% on every other type. What made it safe was fixing the data, not the policy: **alias backfill does not work** (both sides of each pair existed as separate entities and Tier 1 precedes the alias tier), so it was **12 merges + 2 retypes** instead. Afterwards every residual Location divergence is legacy accepting something *wrong*. Also removed 23 junk Location rows (IPs/hostnames) + 98 doc links.

**Next — both are DECISIONS with the evidence already gathered:**
1. **koi 8368 — blocks migration 112.** Spec now exists: `docs/architecture/migration-112-entity-type-canonicalization.md`. The 08-29 date was never the blocker; the missing spec was. Two parking-lot numbers were wrong: 421 non-canonical rows (not ~546) across 16 types, and **4** collisions (not 44). `Document` (240) + `Event` (150) are 390 of the 421 and are **not typos** — deliberate schema.org types carrying 598 edges and 498 doc links, from two dormant pipelines. Choose: **(a)** admit both as canonical (nothing retyped; migration shrinks to a 31-row tail) or **(b)** retype them (needs a defensible mapping; an extracted `Event` is probably not a `Meeting`).
2. **koi 8292 — recommended won't-fix.** Do not backfill the ~1,902 Task notes: `task_registry` holds owner/project/source for 4,331/2,886/4,413 rows vs the graph's 59/39/34, Task entities have zero doc links, and it would take Task from 70 → ~1,972 embedded rows. **Count corrected:** the non-Task residue is **5 notes**, not 22 — 10 of the 15 non-`Tasks/` hits live under `Shared/*/Tasks/`. Any skip rule must key on entity_type, not the path prefix, for that reason.
3. **Watch entity creation** — strict declines more. Pre-flip baseline: 08-23: 6, 08-22: 311, 08-21: 96, 08-20: 1018, 08-19: 67, 08-17: 713 (big days are backfills, not organic). Re-measure in a week.
4. The migration-112 gate does not implement the burst/organic split the handoff asks for — 279 of 317 rows (88%) are one `personal-vault` burst.

**Watch:** The 3 residual excess Meeting mappings are INTENTIONAL. Never de-dup Person rows naively (`dave` misroutes 20 of 22 "Dave" attendees). Never purge fixtures on `ILIKE '%test%'`. Do NOT add an `occurrence_count` column. **`regen`/`indigenomics`/`ethereum`/`open`/`nature`/`amazon` are polysemous, not duplicates** — the Organization "long tail" is 225 prefix pairs but 126 have a short name with MULTIPLE long forms (`open` → 14 orgs), so it is a resolution problem for Tier 1.5, not a merge backlog. `/entities/retype` MINTS A NEW ROW when no live row occupies the target URI — check first, or you recreate the duplicate you just merged. `tests/test_intent_registry.py` writes to the LIVE db over HTTP by design. Concurrent sessions write this DB.

**Verification:** regen-prod, 0 uncommitted, 0 ahead. Red-baseline gate 10/10; governance 4/4. Full suite **44 failed / 1486 passed** vs a MEASURED `763ede4` baseline of 45/1438 — zero new failures, one fixed. Both policy flips are pinned by tests proven non-vacuous (reverting turns them red). Every delete has a timestamped backup table.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->

# Project Context for Claude

> **DEPLOY TOPOLOGY** (updated 2026-07-16; supersedes the single-checkout table — see `c4d3a045` 2026-03-12 collapse + `12ecd839` 2026-04-14 stable re-introduction for older history):
>
> **Local checkouts (3 — which one you edit matters):**
>
> | Local checkout | Serves | Expected branch | Redeploy |
> |---|---|---|---|
> | `~/projects/koi-processor-service` | **Backend service** (personal-koi API, port 8351) — `~/.config/personal-koi/start.sh` `cd`s here | `regen-prod` (may be branch-switched by dev sessions) | `~/.config/personal-koi/restart.sh` (reloads from this working tree) |
> | `~/projects/koi-processor-runtime` | **The 4 personal-KOI sensor launchd jobs** (`substack-sensor`, `substack-gmail-bridge`, `substack-deep-extract`, `research-author-sensor`) | **pinned to `regen-prod`, never branch-switched** | `git -C ~/projects/koi-processor-runtime pull` |
> | `~/projects/RegenAI/koi-processor` (= `regenai/koi-processor`, case-insensitive FS) | Shared **dev** checkout | whatever branch a session is on | n/a (dev) |
>
> **⚠️ Trap:** sensor code edits must land in **`koi-processor-runtime`** (that's where the sensor jobs run). Editing sensors in `koi-processor-service` or the dev checkout and running `launchctl kickstart` will NOT change what the sensors execute. Commit to `regen-prod`, then `git pull` in the runtime clone.
>
> **Branches:**
>
> | Branch | Surface | How it deploys |
> |---|---|---|
> | **`regen-prod`** | Local personal-koi (backend + sensors, above) + **NUC federation** | NUC: Dobby's `deploy.sh` rsyncs. Local backend: `restart.sh`. Local sensors: `git pull` in runtime clone. |
> | **`stable`** | **RegenAI public production** (`$KOI_PROD_HOST`) | Operator-controlled promotion only — `git pull origin stable` on the prod host. Cherry-pick from `regen-prod` when ready. Keep clean. |
> | **`server/stable`** | ⚠️ **Orphaned** (pre-Mar-12 topology) | Do not push here. |
>
> **`regen-prod` takes direct pushes — this is intended (2026-08-14).** The branch carried a
> PR requirement that was decorative: `required_approving_review_count: 0` and
> `enforce_admins: false`, so the repo admin bypassed it by design and each direct push got
> logged as a "bypass" that needed explaining. The requirement was removed rather than
> enforced, because for a solo operator it added a step and no review. Force-push and branch
> deletion are still blocked. Deploy discipline lives where it always did: the deliberate
> `regen-prod` → `stable` promotion, not a self-approved PR.
>
> **CHECKOUT TOPOLOGY — which DIRECTORY serves what (added 2026-08-05).** The table above
> is about BRANCHES. The dimension that has actually caused incidents is which physical
> checkout each consumer reads, because several exist and they are NOT interchangeable:
>
> | Directory | Role | Rule |
> |---|---|---|
> | `~/projects/koi-processor-service` | **SERVES :8351** (uvicorn cwd; `start.sh` `WORKTREE=`) | ⛔ never switch branches here — the live API becomes whatever you leave it on. `start.sh` now REFUSES to serve a non-deployable branch. |
> | `~/projects/koi-processor-runtime` | **launchd sensor jobs** (incl. `embedding-repair`) | ⛔ never switch branches here — a job's script can vanish and it exits 78 silently. |
> | `~/projects/RegenAI/koi-processor` | shared DEV checkout | sessions switch this freely; assume it moves under you. |
> | `~/projects/koi-wt-*` | per-topic **worktrees** | ✅ do multi-step work here. `git worktree add ~/projects/koi-wt-<topic> <branch>` |
>
> Three incidents from getting this wrong: a branch switch orphaned the `chunk-embedder`
> LaunchAgent for **two days** (334 chunks written with NO embedding, nothing alerted),
> the SERVING checkout was found on a feature branch with a critical fix present only
> in the running process's memory — the next restart would have silently regressed it —
> and `calendar-export` ran off the DEV checkout and was **dead for sixteen days**
> (2026-07-31 → 08-16), silently freezing the calendar feed Obsidian reads. It wrote 892 KB
> of the identical error and showed exit 2 in `launchctl list` the whole time.
>
> **The rule is now mechanically enforced.** `tests/test_launchd_job_targets.py` reads the
> plists **installed** in `~/Library/LaunchAgents` (never the copies committed here — the
> divergence between the two IS the bug) and fails if any `com.personal-koi.*` job names a
> path that does not exist, or loads code from the shared dev checkout. It fails if the
> enumeration comes back empty, because a silent skip is what sixteen days looked like.
> Run it after touching any plist: `venv/bin/python -m pytest tests/test_launchd_job_targets.py`.
> Note the venv is `/Users/darrenzal/venvs/koi-server`; bare `python3` has no `asyncpg` and
> most of this repo's tests do not even collect under it.
>
> Translation: commit to `regen-prod` → NUC gets it via Dobby's deploy; the local backend needs `restart.sh`; the local sensors need a `git pull` in the runtime clone. RegenAI public production needs an explicit cherry-pick + push to `stable`.
>
> **EMBEDDING SELF-HEAL (2026-08-14).** `com.personal-koi.chunk-embedder` is **retired**
> (plist kept as `.retired-20260814`). Its replacement is
> **`com.personal-koi.embedding-repair`** → `scripts/run_embedding_repair.sh` →
> `scripts/backfill_null_embeddings.py`, every 300s from the runtime clone, covering **all
> four** surfaces that semantic search reads: chunks, facts, entities, `session_chunks`.
> Previously only chunks self-healed, so 103 fact rows sat invisible for two days.
>
> Two things about it are load-bearing and easy to undo by accident:
>
> - **The plist has no `KeepAlive`, deliberately.** `KeepAlive{SuccessfulExit:false}` with
>   no `ThrottleInterval` is what turned the 2026-08-12 OpenAI credit exhaustion into
>   **3,040 consecutive runs in 9h06m** where `StartInterval=300` intended 109 — launchd's
>   10s *minimum runtime* governs every crash. Each run made a failed provider call and the
>   pending queue **grew 10 → 144**. `ThrottleInterval 300` is belt-and-braces. There is a
>   test (`tests/test_embedding_repair.py::test_the_plist_cannot_storm`) that fails if
>   `KeepAlive` comes back, which is why the plist is committed to the repo at
>   `scripts/com.personal-koi.embedding-repair.plist` rather than living only in
>   `~/Library/LaunchAgents`.
> - **The job exits 0 when the provider is down.** That is not sloppiness — a nonzero exit
>   is precisely what launchd respawned on. Observability is the log line and the state
>   file (`logs/embedding-repair-state.json`), never the exit code. Exit 2 means a genuine
>   config error, 3 means the cost guard tripped.
>
> Manual run: `~/projects/koi-processor-runtime/scripts/run_embedding_repair.sh --dry-run`.
> To force a run inside an open circuit breaker, add `--ignore-backoff`.

**Project**: Regen Network Knowledge Graph Quality Improvement
**Status**: ✅ COMPLETE - Production Deployed (2025-12-25)
**Your Role**: AI coding assistant helping with knowledge graph quality

---

## ⚠️ Embedding Provider Note (post-2026-04-23 OpenAI migration)

**Personal-KOI canonical embedding source**: OpenAI `text-embedding-3-large` at 3072-dim (migration completed 2026-04-23 per personal-koi reframe; was previously poly-served Qwen-1024).

**`scripts/reconcile_missing_chunks.py` gotcha**: currently imports `RemoteEmbeddingProvider` (poly /embed at `http://10.100.0.1:8352`) by default. This routes to the legacy poly server, which may be overloaded or down. Post-migration, prefer one of:
- **`scripts/embed_jsonl_via_openai.py --dimensions 3072`** — OpenAI direct, accepts JSONL input
- **`scripts/import_reembeddings.py --input/--table/--column/--id-col`** — for re-embedding pipelines
- OR update reconcile script to accept `--provider openai` flag (one-line change pulling `OpenAIEmbeddingProvider` from `api/embedding_provider.py:53` instead of `RemoteEmbeddingProvider:173`)

**Reconcile work — RESOLVED 2026-04-28** via durable utility:
- **`scripts/backfill_3072_embeddings_from_manifest.py --manifest <path>`** — durable-by-template backfill: for each doc_id in manifest, finds chunks with `embedding_3072 IS NULL`, embeds via OpenAI text-embedding-3-large @ 3072-dim, UPDATEs in place. No re-chunking, no re-indexing — column-update only on existing chunks. Repeatable `--manifest` flag combines manifests. Cost-abort guard at $5.
- 2026-04-28 run: 37 docs (29 Spore Phase-4 + 8 IC+PM canon-alignment) / 247 chunks / 11.5s / $0.0315. All 247 chunks now carry 3072-dim embeddings; 0 missing across all three repos.
- Manifest file naming convention: `scripts/manifests/reconcile-<scope>-<YYYY-MM-DD>.txt` (one doc_id per line; `#` comments). Reusable for future "manifest of doc_ids needing 3072-dim backfill" jobs.

**`scripts/reconcile_missing_chunks.py` is now the deferred path**: a durable patch that re-routes it to OpenAI is its own scoped sprint when reconcile machinery needs that investment (it re-indexes from source files which is heavier than the column-update job today's 247-chunk backfill needed). For now: prefer `backfill_3072_embeddings_from_manifest.py` for column-only backfills.

**Provider abstraction location**: `api/embedding_provider.py` — has `OpenAIEmbeddingProvider` (line 53), `OllamaEmbeddingProvider` (line 97), `SentenceTransformerProvider` (line 136), `RemoteEmbeddingProvider` (line 173). `OpenAIEmbeddingProvider` already supports `text-embedding-3-large` at 3072-dim (line 68). `backfill_3072_embeddings_from_manifest.py` uses the OpenAI client directly (mirrors `embed_jsonl_via_openai.py` shape) rather than going through the provider abstraction — kept simple for the column-update job.

---

## Deep-extraction transport (2026-07-16)

`scripts/extract_deep_documents.py` runs entity/fact/discourse extraction per window. The **model and transport are env-tunable — not hardcoded** (the in-code `# FORCE Sonnet` comment is an unexamined default, contradicted by evidence). Select via `DOC_EXTRACTOR_TRANSPORT`:

- **`claude_p`** (DEFAULT) — Claude Code subscription via `claude -p`. $0 marginal, slower. The daily launchd jobs use this; leave it unset for them.
- **`api`** — direct Anthropic Messages API. Faster, metered against `ANTHROPIC_API_KEY`. (That key currently has **no credits**, so this path is unavailable until topped up.)
- **`openai`** — ANY OpenAI-compatible `/v1/chat/completions` endpoint (bring-your-own model: public OpenAI, self-hosted vLLM/Ollama, provider-hosted open model). Config: `DOC_EXTRACTOR_OPENAI_BASE_URL` / `_MODEL` / `_API_KEY` / `_NO_THINK` (=1 for reasoning models on vLLM) / `_MAX_TOKENS`. Generic public-OpenAI defaults are committed — nothing operator-specific.

Other knobs: `DOC_EXTRACTOR_MODEL` (claude_p/api model), `DOC_EXTRACTOR_REPAIR_PASSES` (re-ask smaller models on invalid JSON — recovers a missing comma / bare-string-vs-object slip), `DOC_WINDOW_CHARS`, `DOC_MAX_WINDOWS`, `DOC_EXTRACTOR_TIMEOUT` (default 300s; raise to 900 for slow/dense windows — this was previously mis-documented here as `DOC_CLAUDE_P_TIMEOUT`, a name with no effect; see memory `project_personal_koi_substack_pipeline`). As of 2026-08-05 extraction is serialized only by a **per-document** advisory lock (`deep-extract-doc:<rid>`) — concurrent extraction of *different* documents works; the earlier global lock is gone. See memory `reference_koi_extraction_model_tiering` for the correction.

**One-off batch on an alternate model** (keeps the daily jobs on `claude_p`): `scripts/run_batch_extract.sh <script.(sh|py)> [args]` sources an optional gitignored `config/extract-batch.env` (transport override; template `config/extract-batch.env.example`) then runs the extraction. This is how a backfill uses a faster/cheaper model without making it the permanent process. Model-tiering policy + evidence: memory `reference_koi_extraction_model_tiering`.

## Substack corpus ingestion (2026-07-16)

Personal-KOI ingests selected Substacks (Indy Johar, Will Ruddick, Michel Bauwens) under RID `substack-corpus:<feed_slug>:<post_slug>`, `source_sensor='substack-corpus-backfill'`. **Which publications** is personal config in `config/substack_publications.yaml` (gitignored; template `config/substack_publications.example.yaml`) loaded by `scripts/substack_config.py` — not hardcoded, so forks configure their own.

Two ingest paths + deep-extract, all as launchd jobs run from the **runtime clone** (see DEPLOY TOPOLOGY):
- `scripts/substack_sensor.py` (`com.personal-koi.substack-sensor`) — Substack public JSON API. Free posts need no auth; set `SUBSTACK_SID` (a paid subscriber's session cookie) to also ingest full-content paid posts.
- `scripts/ingest_substack_from_gmail.py` + `scripts/run_substack_gmail_bridge.sh` (`com.personal-koi.substack-gmail-bridge`) — pulls full-content **paid** post emails from Gmail over IMAP (auth reuses `~/.gmail-app-password`); feeds `scripts/ingest_substack_corpus.py`. Do NOT route these through the generic email sensor.
- `scripts/deep_extract_substack_corpus.sh` (`com.personal-koi.substack-deep-extract`) — graph-extracts newly-ingested posts (uses the transport above).

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
- `api/routers/web_router.py` — `/web/*` content preview/ingest when `web_sensor` is enabled (personal + coordinator profiles)
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

### Learning Field Projection (Phase 1)
- `scripts/project_bridge_notes.py` — Projects bridge notes from Spore, IC, FC (Flow Coding), and PM (Poietic Match) repos into KOI as Claims, Concepts, and Questions with argumentative edges (`supports`/`opposes`). Two claim layers: source claims (extracted from notes) and review claims (proposed canon changes). Uses `POST /claims/` API for claims, direct SQL for stance edges and questions. Idempotent; supports claim versioning via `supersedes_rid` on re-projection. Within Spore's graph-projections architecture (spore:ADR-0058 / spore:ADR-0070), this script operates as one infrastructure surface inside the Epistemic primary's KOI materialization — bridge-note intake — and does not itself encode the full 3-primary + 5-view-template taxonomy. See spore:ADR-0071 for the cross-repo scope clarification and pm:ADR-0016 for the PM-side canon realignment.
  - Usage: `python scripts/project_bridge_notes.py --dry-run` or `--apply` (optional `--note <path>` for single note)
  - Claimant orgs: `org:spore-learning-field`, `org:ic-learning-field`, `org:flow-coding-learning-field`, `org:poietic-match-learning-field`
  - Provenance: `metadata.source = 'learning_field'`, `metadata.projection_batch`, `metadata.project_uri`
  - Rollback: delete edges by `source = 'learning_field'`, then claims/entities by `metadata->>'source' = 'learning_field'` (see plan rollback section)
- `scripts/verify_learning_field.sql` — Phase 1 verification checklist. Run: `psql personal_koi -f scripts/verify_learning_field.sql`
  - Checks: claim counts by layer, edge counts by source, orphaned review claims, cross-project concept dedup, provenance completeness, governance cluster keys
  - Baseline (batch `20260403T210752Z`): 49 source, 82 review, 50 concepts, 50 questions, 137 supports, 6 opposes

---

## Quality Pipeline

6 modules: ConfidenceFilter, DocumentLevelDeduplicator, CanonicalResolver, OntologyNormalizer, ListSplitter, EntityQualityFilter

**Entity Deduplication:**
- Tier 1: Exact match (B-Tree, microseconds)
- Tier 2: Semantic match (HNSW vector, milliseconds)
- Tier 3: Create new (deterministic URI)

---

## Production Environment

**Server**: $KOI_PROD_HOST
**Branch**: `stable` (verified live 2026-07-16; HEAD `eaf1f77`) — NOT `regen-prod`. Has untracked local drift (backup files); the deploy baseline is the live host, not a clean `stable`.
**Code Path**: /opt/projects/koi-processor
**Database**: PostgreSQL (eliza) on port 5433 (Docker: gaia-postgres-1)
**Fuseki**: Apache Jena Fuseki on port 3030 (Docker: fuseki-koi)
**Graph URL**: https://regen.gaiaai.xyz/graph

**Live stack (verified 2026-07-16, RegenAI prod ≠ personal-koi):** runs a HYBRID —
`api.personal_ingest_api:app` (uvicorn) **+** the legacy event-bridge stack:
`src/core/bge_server.py` (**BGE 1024-dim**, note `src/core/` not repo root),
`src/core/koi_event_bridge_v2.py` (:8100), `src/core/koi_event_bridge_semantic.py`.
So the "legacy" ops/architecture docs (event-bridge, BGE-1024, `eliza` DB) are
**accurate for THIS surface** — but wrong for personal-koi (OpenAI-3072,
`personal_koi` DB, no event bridge, `regen-prod`). When a doc says `bge_server.py` /
`koi_event_bridge_v2.py` at repo root, the real path is now `src/core/`.

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
| `5053a533` | 2026-08-16/17 | koi-infra | **Silent-residue sweep.** `calendar-export` had been dead **16 days** (plist pointed at the shared DEV checkout, which a session branch-switched); it feeds Obsidian's calendar, so 25 real meetings were missing incl. one the next day. Restored from an unmerged Aug-5 recovery branch + repointed at the runtime clone + `tests/test_launchd_job_targets.py` asserts the rule over every installed plist. Landed a second stranded recovery branch (4 CLIs, one on the substack-gmail-bridge execution path and untracked in exactly one directory). **Tombstone sweep:** `00a3049` had fixed 1 of 11 sites — `/knowledge/unified-search` returned a *doubly dead* entity as top hit for "Pol.is". Retrieval EXCLUDES, resolution FOLLOWS transitively (`resolve_to_live_uri`, chains 2 deep); `resolve_entity_multi_tier` fixed as a wrapper so tiers can't be missed; 41 damaged `knowledge_facts` repaired. **646 test-fixture entities** purged from the live graph (accumulating since 2026-03-24; the intent suite archived the intent, never the entity row) + teardown by exact recomputed RID. Corrected my own 2026-08-14 "migration COMPLETE": it covered the `document:` namespace only — **12,798 more damaged docs / 46,793 chunks** remain (~$0.76 to re-embed, `--rid-like '%'`). Memories: `reference_koi_tombstone_exclude_vs_follow`, `feedback_silent_residue_beats_silent_failure`. |
