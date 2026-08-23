<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-08-23 15:40 PDT

**Current status:** Nine commits pushed (`763ede4..c350640`), API restarted 15:33:36 so everything is live. **koi 7878 PASSED and is closed. koi 8294 DECIDED, EXECUTED and closed.** Today fixed two defects that were not on the morning list, closed a fixture leak, fixed the launchd guard's blind spot, and flipped the resolver policy on measured evidence.

**The resolver fuzzy tier is now STRICT** (`c350640`, `active_policy=strict_fuzzy+legacy_semantic`). Measured by a new replay harness: 1,110 attempts across all 13 callers; on Meeting, legacy was wrong on **89.2%** of 259 attempts vs strict's **6.6%**. `personal_ingest_api` was already strict on its fuzzy tier, so the two resolvers had silently disagreed about the same graph; a test now pins that they agree. **BOTH semantic tiers stay on legacy deliberately** — the replay observes fuzzy candidates only, so flipping semantic would be a claim the measurement does not support (koi task 8299; a test pins it so it is not "tidied up").

**What made the flip safe:** Location/Org were the counter-argument (strict declines legitimate short/long merges). Alias backfill was proposed and **does not work** — both sides existed as separate entities and Tier 1 runs before the alias tier. Fixed as **12 merges + 2 retypes** instead (all reversible). Afterwards every residual Location divergence is legacy accepting something *wrong* (`sidney`/`sydney`, `colorado`/`colorado river`, six IP pairs). Also removed 23 junk Location rows (IPs/hostnames) + 98 doc links; source `extract-session-entities` at an 11.6% rate, dormant 30+ days.

**Next:** 1) **Watch entity creation** — strict declines more, so expect MORE new entities. Baseline before go-live: 08-23: 6, 08-22: 311, 08-21: 96, 08-20: 1018, 08-19: 67, 08-17: 713 (the big days are backfills, not organic). Re-measure in a week. 2) koi 8299 — semantic-tier replay before flipping that tier; needs no provider calls, `embedding_3072` is already stored. 3) koi 8292 — recommended **won't-fix**: do not backfill the 1,902 Task notes (`task_registry` holds owner/project/source for 4,331/2,886/4,413 rows vs the graph's 59/39/34, and Task entities have zero doc links); register only the 22 non-Task notes, and make `/register-entity` skip Task deliberately. 4) **Migration 112** — the 08-29 date was never the blocker; there is no spec at all, and it should be written against the now-strict policy. 5) The Organization short/long long tail is unenumerated.

**Watch:** The 3 residual excess Meeting mappings are INTENTIONAL. Never de-dup Person rows naively (`dave` would misroute 20 of 22 "Dave" attendees). Never purge fixtures on `ILIKE '%test%'`. Do NOT add an `occurrence_count` column. `regen` / `indigenomics` / `ethereum` are **polysemous** — not aliases, not duplicates; context decides, which is Tier 1.5's job. `tests/test_intent_registry.py` writes to the LIVE db over HTTP by design. Concurrent sessions write this DB — re-measure before acting.

**Verification:** regen-prod, 0 uncommitted, 0 ahead. Red-baseline gate 10/10; governance 4/4; 148 focused tests. Full suite 44 failed / 1455 passed vs a MEASURED pre-session baseline of 45 failed / 1438 passed at `763ede4` — no new failures. The flip test is proven non-vacuous (reverting to legacy turns it red). Every delete has a timestamped backup table.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
