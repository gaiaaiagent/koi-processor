<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-08-26 10:40 PDT

**Current status:** tree clean, 0 ahead/behind `origin/regen-prod` (`74edb67`). Cleaned a real 3rd conflict-storm wave (240 files, zero loss), built + pinned `com.personal-koi.vault-conflict-sweep` (30-min job, anti-storm tested), and fixed (not skipped) `tests/test_koi_flow_integration.py`'s months-stale collection failure — full suite runs clean with no `--ignore` needed anymore.

**Next:** 1) koi 8386 re-measure (due 08-31, clock-gated). 2) Confirm sweep job fires unattended ~10:48. 3) Watch the `vault-conflict-review` task queue. 4) Optional items in PROJECT_HANDOFF.md.

**Watch:** `/entities/retype` mints a new row on a type change — `dry_run:true` first, never `vault_register_entity`. `PATCH /tasks/{task_key}` 404s on a key containing `/`. `koi-sensors-runtime` pinned to `main`, not `regen-prod`.

**Verification:** full suite at `74edb67`, no `--ignore` needed: 43 failed / 1584 passed, zero regressions (same 43 pre-existing every run). launchd-guard 52/52. 0 non-canonical entity-type rows. All backups reversible.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
