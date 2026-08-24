<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-08-24 13:20 PDT

**Current status:** 15 commits pushed (`763ede4..331b105`), tree clean, API healthy, all prior open items closed. `/register-entity` no longer returns success while silently rolling back the registration — a caught error left the shared transaction aborted, so COMMIT became ROLLBACK. Resolver now **strict** on both tiers, each measured first. Entity types 421 → 3.

**Next:** 1) koi 8386 — re-measure entity creation post-flip (due 08-31); ~20h of data so far, windows not comparable. 2) Decide the 3 remaining non-canonical rows; `Resource` "BKC COP Emails" needs a **vault edit first** or the retype reverts. 3) Optional Task-skip rule, keyed on `entity_type` not the `Tasks/` prefix.

**Watch:** `docs/planning/` is gitignored — docs written there are silently never committed. Type enforcement deliberately NOT added (one drift row since the 07-13 validator). `regen`/`open`/`nature` are polysemous, not duplicates. `/entities/retype` mints a new row when the target URI is free.

**Verification:** regen-prod, 0 uncommitted/ahead, `diff --check` clean. Gate 10/10; governance 4/4; 155 focused tests. Full suite 44 failed / 1486 passed vs a MEASURED baseline of 45/1438 — zero new. 22 backups; all changes reversible.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
