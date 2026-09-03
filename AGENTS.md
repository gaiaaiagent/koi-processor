<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-09-02 22:50 PDT

**Current status:** Tree clean on `regen-prod`, **12 commits ahead of origin (unpushed)**. Phase 0 complete, Phase 1 substantially landed: backup restore-verified, `unmerge` live and proven on 57 real merges, `entity_non_match` enforcing at six resolver tiers, credential + persona guards shipped, `:8351` LAN hole closed and A/B-verified.

**Next:** 1) Push the 12 commits (operator-present gate). 2) Deploy the two parked sensor fixes to `koi-sensors-runtime` — **migration 116 is blocked on the chunker one**; applying it first breaks session ingestion. 3) NUC cutover memo refreshed; execution parked pending operator go.

**Watch:** Apply migrations to BOTH `personal_koi` and `personal_koi_test` — `conftest.py:41` rewrites `POSTGRES_URL`; applying 115 only to the live DB left 19 tests red. 116 is written and deliberately unapplied. The Organization→Person experiment does NOT work: zero email-sourced, dormant since before the guard existed.

**Verification:** Focused suite 3 failed / 72 passed / 2 skipped; the 3 are pre-existing `401 != 503` auth failures, proven by stashing. `git diff --check` clean. No canon validator in this repo.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
