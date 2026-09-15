<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-09-14 17:15 PDT

**Current status:** `fix/document-ingest-integrity`, clean and pushed (the tip is the docs wrap above the fix commits). The review's blockers B1–B4/M1–M8 are **fixed in the branch, none deployed** (`cdc445c` identity · `741ab0a` quality · `8c711be` gate · `79f1a21` migration 126). **Draft PR #66** (0 checks — do NOT merge) closes #62 and #68. Migration 126 still **NOT applied**; `substack-deep-extract` still **disabled** (backlog 356).

**Next:** 1) **#67** federated fact retraction (peer application 0/4). 2) **#69** transactional rollback/replay. 3) Deploy #66 to BOTH checkouts, restart, verify OpenAPI (`subject_uri`/`object_uri` AND `fact_ids`) — **before** the pinned replay (task due 09-21).

**Watch:** untyped fact endpoints now BLOCK (`type_undeclared`) instead of defaulting to Concept — **239/1,755 cached docs** affected in strict mode; operator's call. Stored michaelgarfield windows would still mint the essays as `Project` (the gate prints this); curated payloads are gate-only artifacts, no replay reads them until #69.

**Verification:** tree clean; 173 tests across the six affected suites (66 new) green; full suite vs merge-base, same flags: nothing fails on the branch that does not fail at the base; 12 core fixes revert-proven; no live writes.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
