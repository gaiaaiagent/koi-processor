<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-08-26 01:10 PDT

**Current status:** tree clean, 0 ahead/behind `origin/regen-prod` (`098eaf2` — 2 more commits from another session, backend already restarted). A verification session caught this session's conflict-storm cleanup as incomplete: a 3rd wave of 101 iCloud conflict files arrived past the original short check window. Re-triaged (zero data loss), cleaned with a 60-min stability monitor this time.

**Next:** 1) koi 8386 re-measure (due 08-31, clock-gated). 2) Nothing else outstanding. 3) Optional/low-priority items in PROJECT_HANDOFF.md.

**Watch:** `/entities/retype` mints a new row on a type change — `dry_run:true` first, never `vault_register_entity`. Bulk vault writes can trigger a 3+ wave iCloud conflict storm spanning 60+ minutes — a short stability check is not sufficient. `koi-sensors-runtime` pinned to `main`, not `regen-prod` — see CHECKOUT TOPOLOGY below.

**Verification:** suite at `bd0a7ce` (no code change since): 43 failed / 1526 passed vs `1cea455` baseline 43/1527 (-1 deliberate) — zero regressions. 0 non-canonical entity-type rows. All backups reversible.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
