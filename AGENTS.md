<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-08-23 13:52 PDT

**Current status:** Seven commits pushed; API restarted 12:05:33 (PID 82263) so they are live. **koi 7878 PASSED and is closed** (13:48:57, exit 0, both positive controls firing). Two defects that were NOT on the previous list are fixed: (a) `/register-entity` returned HTTP 200 `success=true` while SILENTLY ROLLING BACK the registration — a caught `UndefinedColumnError` on a column `personal_koi` never had left the shared transaction aborted, so COMMIT became ROLLBACK; ~1,924 notes since 2026-02-26. (b) `intent_match_proposals` was still leaking one table over from the 2026-08-22 purge — 256 orphans purged with backup; the AC1 gate was widened to see it BEFORE it ran, or it would have certified clean past them.

**Next — both are DECISIONS with evidence, not tasks:** 1) koi 8294, flip resolver legacy→strict. Per-type replay: Meeting 86.9% divergence and **legacy wrong on 89.2% of Meeting attempts vs strict 6.6%**; SpecDoc 65.5%, Person 8% also favour strict (`clark`→`clare`, `joel`→`joe`). But Location 43.3% / Organization 1.8% are strict's COST — it loses legitimate merges (`victoria`→`victoria bc`). Trade is wrong-merges→duplicates. Backfill Location/Org aliases first (only 34 of 675 Locations have any), and consider a PER-TYPE policy rather than a global switch. 2) koi 8292, do NOT backfill the 1,902 Task notes — `task_registry` holds owner/project/source for 4,331/2,886/4,413 rows vs the graph's 59/39/34, and Task entities have zero document links; backfill would take Task 70→~1,972. Register the 22 non-Task notes only, and make `/register-entity` skip Task deliberately. 3) Migration 112 has no spec at all — that, not the 08-29 date, is the blocker. 4) `tests/test_launchd_job_targets.py` globs `com.personal-koi.*` and misses three `com.personal.koi-*` jobs, one running from the shared DEV checkout; it also never reads `WorkingDirectory`.

**Watch:** The 3 residual excess Meeting mappings are INTENTIONAL — do not drive them to zero. Never de-dup Person rows naively; the `dave` alias would misroute 20 of 22 "Dave" attendees away from David Fortson. Never purge fixtures on `ILIKE '%test%'`. Do NOT add an `occurrence_count` column — it is RegenAI-era. `tests/test_intent_registry.py` writes to the LIVE db over HTTP by design; the conftest DSN redirect cannot contain it, so its teardown must stay complete. Re-measure before acting; concurrent sessions write this DB.

**Verification:** regen-prod, 0 uncommitted, 0 ahead. Red-baseline gate 10/10 PASS; governance 4/4; meeting suites 57/57; wikilink suite 19/19; shadow+replay 15/15. Full suite 44 failed / 1455 passed against a MEASURED pre-session baseline of 45 failed / 1438 passed at `763ede4` — no new failures. Every delete has a timestamped backup table.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
