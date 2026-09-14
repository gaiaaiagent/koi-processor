<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-09-14 11:38 PDT

**Current status:** `fix/document-ingest-integrity` @ `1d7f708`, clean and pushed. **Draft PR #66** (0 checks — do NOT merge) implements #62 identity pinning, #61 normalizer compat, #64 provenance + semantic quality; 48 tests. Migrations **124/125** applied live. `substack-deep-extract` **disabled and unloaded** (backlog 356) — keep it so.

**Next:** 1) **#68 — HARD BLOCKER**: both extraction schemas and `TYPE_PRIORITY` omit `Document` and `Event`, and pinning makes entity_type permanent (hashed into the URI), so #66 would make today's correctable wrong types irreversible. 2) **#67** — `retract_fact` emits no federation event; peer application unproven 0/4. 3) **#69** — transactional rollback/replay. Then the pinned replay (task `koi-2026-09-14-michaelgarfield-pinned-replay`, due 09-21). Do NOT deploy #66, replay, or re-enable the job before these.

**Watch:** 3 michaelgarfield docs are HALF-repaired — 5 facts retracted, but `deep_extracted_at` still set (deliberate) and entity links/discourse moves stale. Refreshing `koi-processor-runtime` alone is NOT enough: facts are written by the API, so `koi-processor-service` + restart move the write path. Do not add the new gate-catalog floors yet.

**Verification:** `git status --porcelain` empty; `git diff --check` clean; 48/48 identity tests; 3 pre-existing unrelated failures in `test_knowledge_router_facts_gate.py` (identical on a clean tree). No canon validator in this repo. Read-only gate: `scripts/check_document_integrity.py`.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
