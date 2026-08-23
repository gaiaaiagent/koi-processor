<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-08-23 12:30 PDT

**Current status:** Six commits pushed (`763ede4..2169721`); API restarted 12:05:33 (PID 82263) so they are live. Two defects that were NOT on the previous list are fixed: (a) `/register-entity` returned HTTP 200 `success=true` while SILENTLY ROLLING BACK the registration — a caught `UndefinedColumnError` on a column `personal_koi` never had left the shared transaction aborted, turning COMMIT into ROLLBACK; ~1,924 notes affected since 2026-02-26 (koi task 8292, quantified, not repaired). (b) `intent_match_proposals` was still leaking one table over from the 2026-08-22 purge — 256 orphans purged with backup, teardown + tripwire + AC1 gate all widened.

**Next:** 1) koi 7878 — run `venv/bin/python scripts/check_intent_leak_observation.py` at/after 2026-08-23 13:47 PDT; reads 0/0/0 and correctly refuses to close before then. 2) DECIDE koi task 8294: flip the resolver policy legacy→strict. The replay harness (`scripts/replay_resolver_shadow.py`, new) returned `explicit_policy_split` — 1,110 attempts, **36 outcome divergences**, and **18 of them are the cross-date Meeting collapse repaired in the DATA on 08-22**. `active_policy` is still `legacy`, so that guard would recreate it. Strict wins every case inspected but creates more entities — measure before/after. 3) Migration 112: the 08-29 date is not the blocker, there is no spec at all. 4) `tests/test_launchd_job_targets.py` globs `com.personal-koi.*` and misses three `com.personal.koi-*` jobs, one of which runs out of the shared DEV checkout.

**Watch:** The 3 residual excess Meeting mappings are INTENTIONAL — do not drive them to zero. Never de-dup Person rows naively; the `dave` alias would misroute 20 of 22 "Dave" attendees away from David Fortson. Never purge fixtures on `ILIKE '%test%'`. Do NOT add an `occurrence_count` column to fix anything — it is RegenAI-era. `tests/test_intent_registry.py` writes to the LIVE db over HTTP by design; the conftest DSN redirect cannot contain it, so its teardown must stay complete. Re-measure before acting; concurrent sessions write this DB.

**Verification:** regen-prod, 0 uncommitted, 0 ahead. Red-baseline gate 10/10 PASS; governance 4/4; meeting suites 57/57; new wikilink suite 19/19; shadow+replay 15/15. Full suite 44 failed / 1455 passed against a MEASURED pre-session baseline of 45 failed / 1438 passed at `763ede4` — no new failures. Every delete has a timestamped backup table.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
