<!-- workstream: koi-infra -->

> **Stream scope:** koi-infra (this repo + `personal-koi-mcp`). Cross-stream recommendations require operator opt-in. See `~/AGENTS.md` and `~/CLAUDE.md` "Stream-scope discipline" sections for the rule. Sister surface for Claude Code: `CLAUDE.md` in this directory.

<!-- end-skill:handoff:start -->
## Cross-tool session handoff

This snapshot was refreshed by the end skill. Use it before planning or recommending project work.

**Updated:** 2026-08-23 16:50 PDT

**Current status:** Twelve commits pushed (`763ede4..HEAD`), API restarted so all of it is live. **koi 7878, 8294 and 8299 are closed.** The resolver now uses the **strict** token-overlap policy on BOTH the fuzzy and semantic tiers (`active_policy="strict"`), each flipped only after being measured separately. Two open items are decisions, not tasks: koi **8368** (Document/Event) and koi **8292** (Task backfill).

**Resolver, settled today.** Fuzzy flipped on 1,110 replayed attempts across all 13 callers — on Meeting, legacy was wrong on **89.2%** of 259 attempts vs strict's **6.6%**. Semantic flipped separately on 204 observations: 17 divergences, **all Meeting (94.4%), all legacy accepting a different-dated meeting**, 0% on every other type. What made it safe was fixing the data, not the policy: **alias backfill does not work** (both sides of each pair existed as separate entities and Tier 1 precedes the alias tier), so it was **12 merges + 2 retypes** instead. Afterwards every residual Location divergence is legacy accepting something *wrong*. Also removed 23 junk Location rows (IPs/hostnames) + 98 doc links.

**Next — both are DECISIONS with the evidence already gathered:**
1. **koi 8368 — blocks migration 112.** Spec now exists: `docs/architecture/migration-112-entity-type-canonicalization.md`. The 08-29 date was never the blocker; the missing spec was. Two parking-lot numbers were wrong: 421 non-canonical rows (not ~546) across 16 types, and **4** collisions (not 44). `Document` (240) + `Event` (150) are 390 of the 421 and are **not typos** — deliberate schema.org types carrying 598 edges and 498 doc links, from two dormant pipelines. Choose: **(a)** admit both as canonical (nothing retyped; migration shrinks to a 31-row tail) or **(b)** retype them (needs a defensible mapping; an extracted `Event` is probably not a `Meeting`).
2. **koi 8292 — recommended won't-fix.** Do not backfill the ~1,902 Task notes: `task_registry` holds owner/project/source for 4,331/2,886/4,413 rows vs the graph's 59/39/34, Task entities have zero doc links, and it would take Task from 70 → ~1,972 embedded rows. **Count corrected:** the non-Task residue is **5 notes**, not 22 — 10 of the 15 non-`Tasks/` hits live under `Shared/*/Tasks/`. Any skip rule must key on entity_type, not the path prefix, for that reason.
3. **Watch entity creation** — strict declines more. Pre-flip baseline: 08-23: 6, 08-22: 311, 08-21: 96, 08-20: 1018, 08-19: 67, 08-17: 713 (big days are backfills, not organic). Re-measure in a week.
4. The migration-112 gate does not implement the burst/organic split the handoff asks for — 279 of 317 rows (88%) are one `personal-vault` burst.

**Watch:** The 3 residual excess Meeting mappings are INTENTIONAL. Never de-dup Person rows naively (`dave` misroutes 20 of 22 "Dave" attendees). Never purge fixtures on `ILIKE '%test%'`. Do NOT add an `occurrence_count` column. **`regen`/`indigenomics`/`ethereum`/`open`/`nature`/`amazon` are polysemous, not duplicates** — the Organization "long tail" is 225 prefix pairs but 126 have a short name with MULTIPLE long forms (`open` → 14 orgs), so it is a resolution problem for Tier 1.5, not a merge backlog. `/entities/retype` MINTS A NEW ROW when no live row occupies the target URI — check first, or you recreate the duplicate you just merged. `tests/test_intent_registry.py` writes to the LIVE db over HTTP by design. Concurrent sessions write this DB.

**Verification:** regen-prod, 0 uncommitted, 0 ahead. Red-baseline gate 10/10; governance 4/4. Full suite **44 failed / 1486 passed** vs a MEASURED `763ede4` baseline of 45/1438 — zero new failures, one fixed. Both policy flips are pinned by tests proven non-vacuous (reverting turns them red). Every delete has a timestamped backup table.

Full source of truth: `PROJECT_HANDOFF.md`. Re-read it when more detail is needed and re-verify volatile external facts before acting.
<!-- end-skill:handoff:end -->
