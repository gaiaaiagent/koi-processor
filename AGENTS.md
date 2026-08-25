<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-08-25 01:15 PDT

**Current status:** 13 commits pushed earlier; same session made 1 more, not pushed (`bd0a7ce`). Established `koi-sensors-runtime` + hardened the launchd guard; resolved a 139-file iCloud sync-conflict storm from tonight's writes (zero data loss) — the "29 older notes" mystery was this, not a real gap. Phase B repair was already 100% complete.

**Next:** 1) koi 8386 re-measure (due 08-31, clock-gated). 2) Push `bd0a7ce` — needs operator go-ahead. 3) Optional/low-priority items in PROJECT_HANDOFF.md.

**Watch:** `/entities/retype` mints a new row on a type change — `dry_run:true` first, never `vault_register_entity`. Bulk vault writes can trigger a multi-wave iCloud conflict storm — sweep more than once. `koi-sensors-runtime` pinned to `main`, not `regen-prod` — see CHECKOUT TOPOLOGY below.

**Verification:** at `bd0a7ce`: 43 failed / 1526 passed vs `1cea455` baseline 43/1527 (-1 is a deliberate test removal) — zero regressions. 0 non-canonical entity-type rows. All backups reversible.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
