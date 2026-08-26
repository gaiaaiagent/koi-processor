<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-08-26 10:05 PDT

**Current status:** tree clean, 0 ahead/behind `origin/regen-prod` (`aa93856`). Cleaned a real 3rd conflict-storm wave (101 files, zero data loss), then built the operator-chosen durable fix: `com.personal-koi.vault-conflict-sweep` (30-min launchd job) auto-cleans safe conflict copies and files a task for anything real. Shared this checkout with a concurrent session throughout; no incidents.

**Next:** 1) koi 8386 re-measure (due 08-31, clock-gated). 2) Watch the `vault-conflict-review` task queue for anything the sweep flags. 3) Optional/low-priority items in PROJECT_HANDOFF.md.

**Watch:** `/entities/retype` mints a new row on a type change — `dry_run:true` first, never `vault_register_entity`. `PATCH /tasks/{task_key}` 404s on a key containing `/` — never build one from a raw path. `koi-sensors-runtime` pinned to `main`, not `regen-prod` — see CHECKOUT TOPOLOGY below.

**Verification:** clean suite at `aa93856`: 43 failed / 1570 passed — 43 matches baseline exactly, zero regressions. `test_launchd_job_targets.py` 42/42. 0 non-canonical entity-type rows. All backups reversible.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
