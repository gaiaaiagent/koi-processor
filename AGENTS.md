<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-09-14 23:25 PDT

**Current status:** `fix/document-ingest-integrity`, clean and pushed (tip = docs wrap above the fixes). B1–B4/M1–M8 fixed (`cdc445c`…`79f1a21`) AND the re-review's last blocker — the discourse-move uuid5 hash input the M6 rename changed — fixed in `97f6525`. **Draft PR #66** (0 checks — do NOT merge) closes #62/#68; **no merge blocker known to remain.** Migration 126 **NOT applied**; `substack-deep-extract` **disabled** (backlog 356).

**Next:** 1) **#67** federated fact retraction. 2) **#69** transactional rollback/replay (also owns the M4 gap: preflight IGNORES an invalid alias decision, so a row can be minted before the freeze refuses). 3) Deploy #66 to BOTH checkouts, restart, verify OpenAPI (`subject_uri`/`object_uri` AND `fact_ids`) — **before** the pinned replay (due 09-21).

**Watch:** untyped endpoints BLOCK (`type_undeclared`) — **239/1,755 cached docs**; operator's call. 2 pre-existing move rows (June 2026) match no id derivation — reported, untouched.

**Verification:** tree clean; 180 tests / seven suites green (7 new pin literal move-id UUIDs; 5 fail at `d51fb41`, control passes); census 13,765/13,767 stored move ids reproduced, 21/21 michaelgarfield rows; full-suite failure set unchanged, strict subset of merge-base; no live writes.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
