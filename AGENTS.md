<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-08-26 10:20 PDT

**Current status:** tree clean, 0 ahead/behind `origin/regen-prod` (`d506c6b`). Cleaned a real 3rd conflict-storm wave (240 files total, zero loss), built `com.personal-koi.vault-conflict-sweep` (30-min job), pinned it with `test_the_plist_cannot_storm` after finding a real `ThrottleInterval < StartInterval` bug. Reload at ~10:18 reset its timer — next unattended fire ~10:48, not the earlier ~10:28.

**Next:** 1) koi 8386 re-measure (due 08-31, clock-gated). 2) Confirm unattended fire ~10:48 (canary timing changed). 3) Watch the `vault-conflict-review` task queue. 4) Optional items in PROJECT_HANDOFF.md.

**Watch:** `/entities/retype` mints a new row on a type change — `dry_run:true` first, never `vault_register_entity`. `PATCH /tasks/{task_key}` 404s on a key containing `/`. `koi-sensors-runtime` pinned to `main`, not `regen-prod`.

**Verification:** launchd-guard suite 52/52, incl. new mutation-tested `test_the_plist_cannot_storm`. Full suite last clean-measured at `aa93856`: 43 failed / 1570 passed, zero regressions. 0 non-canonical entity-type rows. All backups reversible.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
