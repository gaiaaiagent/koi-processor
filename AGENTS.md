<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-08-25 00:45 PDT

**Current status:** 12 commits, tree clean, not pushed (12 ahead). Prior handoff fully closed: all 9 sweep-finding fixes shipped, 276-Meeting-note repair executed (270 re-measured fresh, 8-row orphan issue caught+fixed), last non-canonical entity retyped — migration 112 complete, 421→0.

**Next:** 1) koi 8386 re-measure (due 08-31, clock-gated). 2) Push 12 commits — needs operator go-ahead. 3) Optional: defect-class-sweep on curl-fallback-masks-failure idiom; decide on a koi-sensors-runtime clone.

**Watch:** `/entities/retype` mints a new row on a type change — `dry_run:true` first. Never use `vault_register_entity` for a type change (no merge-back; caused 8 orphans this session). Rank 11/14 sweep findings deliberately deferred.

**Verification:** at `1cea455`: 43 failed / 1527 passed vs measured `763ede4` baseline 45/1438 — zero new failures. 0 non-canonical entity-type rows (was 1). 32 backups; all reversible.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
