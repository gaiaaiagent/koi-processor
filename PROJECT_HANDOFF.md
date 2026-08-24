# Project handoff

**Updated:** 2026-08-24 14:40 PDT
**Session:** Claude Code · c1defaa8-ad3c-4de2-9e54-47361c370b33 · Silent-rollback defect → strict resolver → entity types → silent-success sweep
**Status:** 18 commits pushed (`763ede4..2a50f07`), tree clean, API healthy. Every item from the previous handoff is closed. A sweep of this session's own defect class then found **14 more confirmed instances**, one of which corrects a claim made earlier in the session.

> ## ⚠ START KOI SESSIONS IN THIS CHECKOUT
>
> `~/projects/koi-processor-service` — despite the name, this is **not a separate repo**. All four
> local checkouts are clones of `gaiaaiagent/koi-processor`; the directory names encode *roles*, not
> repositories. There is no `gaiaaiagent/koi-processor-service`.
>
> | checkout | role | branch |
> |---|---|---|
> | `koi-processor-service` | **serves :8351** (uvicorn cwd) — start here | `regen-prod` |
> | `koi-processor-runtime` | the sensor launchd jobs | `regen-prod`, never switch |
> | `RegenAI/koi-processor` | shared dev checkout | whatever a session left it on |
>
> **Do not start ontology sessions in `RegenAI/koi-processor`.** As of 2026-08-23 it is **284 commits
> behind** `regen-prod` on a feature branch with uncommitted work, and it has no `PROJECT_HANDOFF.md`
> — so the SessionStart hook walks upward and injects `~/projects/RegenAI/PROJECT_HANDOFF.md`
> instead, which is dated 2026-08-19 and describes Claims Engine call prep and a Notion blocker.
> A session there starts confidently oriented to the wrong work. It will pick these files up on its
> own once that branch merges from `regen-prod`.

## Completed this session

- **Fixed a silent-rollback defect in `/register-entity`** (`56f61d1`, `98d3d26`, `f169f29`, `d38ce26`). It returned HTTP 200 `success=true` while **discarding the registration**: a caught `UndefinedColumnError` (the untyped wikilink tier ordered by `occurrence_count`, a column `personal_koi` has never had) left the shared transaction aborted, so COMMIT became a silent ROLLBACK. `except` is not a savepoint. ~1,924 notes since 2026-02-26; git dates it exactly.
- **Flipped the resolver to strict on both tiers, each measured separately.** `scripts/replay_resolver_shadow.py` answered in ~10 minutes a gate that needed ~170 days of live sampling. Fuzzy (`c350640`): legacy wrong on **89.2%** of 259 Meeting attempts vs strict's **6.6%**. Semantic (`ce400f1`), measured after: 17 divergences, **all Meeting, all legacy accepting a different-dated meeting**, 0% every other type.
- **Made the flip safe by fixing data, not policy.** Alias backfill was proposed and **does not work** (both sides existed as separate entities; Tier 1 precedes the alias tier), so it was **12 merges + 2 retypes**. Afterwards every residual Location divergence is legacy accepting something *wrong*.
- **koi 7878 AC1 PASSED**, both positive controls firing. **Closed the `intent_match_proposals` leak** (`c5d3759`) — the gate was widened *before* it ran, or it would have certified `orphaned: 0` over 256 orphans.
- **Canonicalized the entity-type vocabulary: 421 → 1 non-canonical row.** Admitted `Document` + `Event` (390 rows, 598 edges, 498 doc links — deliberate schema.org types; a `Document` entity already ranked *first* in unified-search), retyped 28, deleted 2 inert rows. All reversible.
- **Fixed the launchd guard's blind spot** (`5e70697`) — it globbed one label namespace and missed three jobs, one running from the shared dev checkout.
- **Swept the session's own defect class** (`dc03b9a`, `2a50f07`): 7 lenses → 23 candidates → each adversarially refuted → **14 confirmed, 10 refuted**. The four known instances were **not** most of the class.

## Next steps

1. **DECIDE + REPAIR: 276 Meeting notes are missing their `project`/`location` edges** (koi 8387, rank 1). The 2026-08-22 backfill posted `frontmatter={"attendees": [...]}` — one key — and `/register-entity` does a **replace-all** relationship sync over whatever it is given, then stamps `sync_status='linked'` with the full-file hash. Measured: 281 mappings written, 276 declare `project` AND `location`, **all 276 have zero non-attended edges**. **278 of 281 hashes are current**, so a normal re-sync *skips* them — repair needs a forced pass or a hash bust. Data change; needs an operator decision.
2. **Fix the confirmed silent-success findings** (koi 8387; `docs/architecture/silent-success-sweep-20260824.md`). Highest first: the **consent-leakage gate fails OPEN** (wrong param names → 422 → `|| echo '{}'` → PASS); `GET /tasks/` drops all four date filters on malformed input (verified live); `resolve_pending_relationships` swallows a transaction-aborting insert **with no SAVEPOINT** while both siblings in the same file have one.
3. **koi 8386 — re-measure entity creation after the strict flip** (due 08-31). Clock-gated: ~22h of data and the windows are not comparable (pre-flip is 285 of 317 rows from one burst). Methodology on the task.
4. **The last non-canonical row**: `Resource` "BKC COP Emails". Its vault note declares `"@type": Resource`, so a DB retype alone is undone by the next sync — needs a **vault edit first**.

## Open questions

- **`docs/planning/` is gitignored** (`.gitignore:85`), and two files there are tracked from before the rule, which makes it look safe. A doc written there is silently never committed — this ate the migration-112 spec until it was caught. `docs/soak-results/` carries the same rule. Worth deciding whether it should stand.
- **Type-vocabulary enforcement was deliberately NOT added.** `allowed_entity_types` is read by nothing, but since the canonicalizing validator landed 2026-07-13 exactly **one** non-canonical row has been created — a hard FK would reject at the database what the application already fixes. The useful shape, if wanted, is a create-path guard that *logs*.
- **A Task-skip rule on `/register-entity` is optional, and I would argue against it as specified** — a blanket skip also blocks deliberate single-task registration; the guard belongs in the sync tool. Must key on `entity_type`, not the `Tasks/` prefix.
- The durable soak log lives in the **dev checkout** (`~/projects/RegenAI/koi-processor/docs/soak-results/`), which everything else was moved off.

## Verification and working tree

- **Branch/status:** `regen-prod`, 0 uncommitted, **0 ahead of origin**, `git diff --check` clean.
- **Verification measured at `2a50f07`** — the final commit, not an earlier one: full suite **44 failed / 1492 passed** against a *measured* `763ede4` baseline of 45 failed / 1438 passed — **zero new failures**. Red-baseline gate **10/10**; governance 4/4; **155 focused tests**. Both policy flips pinned by tests **proven non-vacuous**.
- **Canon validator:** not applicable — no `scripts/validate_spec_dag.py`.
- **Live:** API healthy, 30 entity types, 0 null embeddings, 31,493 live entities, **1** non-canonical row.
- **23 backup tables retained**; every delete, merge and retype this session is reversible.
- **Re-measure before acting.** Concurrent sessions write this database.

## Watch

- The 3 residual excess Meeting mappings are **INTENTIONAL**. Do not drive them to zero.
- Never de-dup Person rows naively — `dave` would misroute 20 of 22 "Dave" attendees.
- Never purge fixtures on `ILIKE '%test%'`; do **not** add an `occurrence_count` column.
- `/entities/retype` **mints a new row** when no live row occupies the target URI — check first.
- `regen`/`open`/`nature`/`amazon`/`indigenomics`/`ethereum` are **polysemous, not duplicates**: 126 of 225 Organization prefix pairs have a short name with multiple long forms (`open` → 14 orgs). Tier 1.5's problem, not a merge backlog.
- `tests/test_intent_registry.py` writes to the **live** DB over HTTP by design.
- The 74 `koi_sustained_write` SpecDoc rows are **real content**, not load-test junk.

## Recent sessions

| Date | Provider | Session | Summary |
|---|---|---|---|
| 2026-08-22 | Claude Code | ffb7988e | Ontology safety rails → Meeting identity fix + promotion; 26 commits across 3 sessions |
| 2026-08-23 | Claude Code | (fresh) | Historical Meeting repair, resolver legacy/strict split, live-writer governance (`38c11fe`) |
| 2026-08-23/24 | Claude Code | c1defaa8 | `/register-entity` silent rollback; resolver → strict (both tiers, measured); intent-proposal leak; entity types 421→1; launchd guard; silent-success sweep (14 confirmed). 18 commits (`763ede4..2a50f07`) |
