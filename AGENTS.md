<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-08-24 14:40 PDT

**Current status:** 18 commits pushed (`763ede4..2a50f07`), tree clean, API healthy, all prior items closed. `/register-entity` no longer reports success over an aborted transaction; the resolver is **strict** on both tiers, each measured before flipping; entity types 421 → 1. A sweep of that class then confirmed **14 more instances**.

**Next:** 1) **DECIDE + REPAIR** (koi 8387): 276 Meeting notes have zero `project`/`location` edges — the 08-22 backfill sent one frontmatter key into a **replace-all** sync, and 278/281 hashes are current so a re-sync *skips* them. 2) Fix the sweep findings worst-first: the **consent-leakage gate fails OPEN**; `GET /tasks/` drops all four date filters on malformed input. 3) koi 8386 — re-measure entity creation (due 08-31, clock-gated).

**Watch:** `docs/planning/` and `docs/soak-results/` are gitignored — docs written there are silently never committed. Type enforcement deliberately NOT added. `regen`/`open`/`nature` are polysemous, not duplicates. `/entities/retype` mints a new row when the target URI is free.

**Verification:** measured at `2a50f07`, the final commit: 44 failed / 1492 passed vs a MEASURED `763ede4` baseline of 45/1438 — zero new. Gate 10/10; governance 4/4; 155 focused tests. 23 backups; all reversible.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
