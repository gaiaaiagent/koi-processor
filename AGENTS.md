<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-08-22 19:58 PDT

**Current status:** Meeting promotion shipped end-to-end — 28 → 262 Meeting entities and 70 → 1,081 `attended` edges with ZERO date-mismatched, after a same-day resolver fix; all work pushed, tree clean, 0 ahead.

**Next:** 1) koi 7878 — the intent fix's 24h observation window, resolves 2026-08-23. 2) Decide split-vs-leave on the 43 historical Meeting mapping collapses. 3) `passes_token_overlap_check` is defined TWICE with DIFFERENT bodies (`resolution_primitives.py:194` vs `personal_ingest_api.py:728`, the latter shadowing the import at `:160`).

**Watch:** Do NOT de-dup Person rows naively — `Dave Bronner` carries the registered alias `dave`, so clearing fragment rows would route all 22 "Dave" attendees to him at confidence 1.0 while the vault says 20 are David Fortson. Re-measure before acting; concurrent sessions write this DB.

**Verification:** regen-prod, 0 uncommitted, 0 ahead, `git diff --check` clean. Red-baseline gate 10/10 PASS (`scripts/run-red-baseline-gate.sh`); live-write governance 4/4; Meeting identity 20/20. API healthy, PID 2535. Every delete has a `*_backup_*_20260822` snapshot.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
