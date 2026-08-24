# Project handoff

**Updated:** 2026-08-24 13:20 PDT
**Session:** Claude Code · c1defaa8-ad3c-4de2-9e54-47361c370b33 · Silent-rollback defect → resolver strict flip → entity-type canonicalization
**Status:** 15 commits pushed (`763ede4..331b105`), working tree clean, API live and healthy. Every open item from the prior handoff is closed; two new decisions were made and executed. Nothing is blocked.

> ## Completed this session

- **Found and fixed a silent-rollback defect in `/register-entity`** (`56f61d1`, `98d3d26`, `f169f29`, `d38ce26`). It returned HTTP 200 `success=true` while **discarding the registration**: a caught `UndefinedColumnError` (the untyped wikilink tier ordered by `occurrence_count`, a column `personal_koi` has never had) left the shared transaction aborted, so COMMIT became a silent ROLLBACK. `except` is not a savepoint. Three composing defects — nested vault paths lost their type hint via `rsplit('/', 1)`, the bad column, and the swallow — all fixed, with `SAVEPOINT vault_rel_sync` containment. ~1,924 notes affected since 2026-02-26; git dates it precisely (`sourceNote` became a mapped field 2026-02-26, one day after the bulk sync that succeeded).
- **Flipped the resolver to strict on both tiers**, each measured separately first. Built `scripts/replay_resolver_shadow.py`, which answered a gate that needed ~170 days of live sampling in ~10 minutes: 1,110 attempts across all 13 callers. Fuzzy (`c350640`): legacy was wrong on **89.2%** of 259 Meeting attempts vs strict's **6.6%**. Semantic (`ce400f1`), measured separately: 17 divergences, **all Meeting, all legacy accepting a different-dated meeting**, 0% every other type.
- **Made that flip safe by fixing data, not policy.** Alias backfill was proposed and **does not work** — both sides of each pair existed as separate entities and Tier 1 precedes the alias tier. Did **12 merges + 2 retypes** instead; afterwards every residual Location divergence is legacy accepting something *wrong*. Also removed 23 junk Location rows (IPs/hostnames) + 98 doc links.
- **Closed the `intent_match_proposals` leak** (`c5d3759`) — the 2026-08-22 purge swept three tables; a fourth kept filling. 256 orphans purged with backup, teardown + tripwire + AC1 gate all widened. **The gate was widened before it ran**, or it would have certified `orphaned: 0` while 256 orphans sat in the database.
- **koi 7878 AC1 PASSED** at 13:48:57 on 08-23, exit 0, both positive controls firing (225 entity rows, 256 proposal rows). Re-derived in psql rather than trusting the script.
- **Canonicalized the entity-type vocabulary: 421 → 3 non-canonical rows** (`d3bd22a`, `331b105`). Admitted `Document` + `Event` (390 rows, 598 edges, 498 doc links — schema.org types written deliberately, and a `Document` entity already ranked *first* in unified-search), then retyped 28 more. All reversible.
- **Fixed the launchd guard's blind spot** (`5e70697`). It globbed one label namespace and missed three jobs — one running `doc_scanner.py` from the shared dev checkout, 284 commits behind, exactly what the guard exists to forbid. Guard now reads both namespaces, `WorkingDirectory`, and launched script bodies.

## Next steps

1. **koi 8386 — re-measure entity creation after the strict flip** (due 2026-08-31). Only ~20h of post-flip data existed at wrap-up and the windows are not comparable (pre-flip is 285 of 317 rows from one vault burst). Methodology is on the task: same-source only, exclude bursts, normalize per hour, and treat `resolution_tier='tier3_created_ambiguous'` as the key signal (5 → 0 so far, n far too small).
2. **Decide the 3 remaining non-canonical rows.** `Resource` "BKC COP Emails" needs a **vault edit first** — its note declares `"@type": Resource`, so retyping the DB row alone is undone by the next sync. The 2 `Session` rows are inert; deleting them is a data decision, not a retype.
3. **Optional: a Task-skip rule on `/register-entity`.** koi 8292 is closed won't-fix, but nothing yet prevents a bulk vault sync from creating ~1,900 Task entities. Must key on resolved `entity_type`, not the `Tasks/` path prefix — 10 of 15 non-`Tasks/` hits live under `Shared/*/Tasks/`.

## Open questions

- **`docs/planning/` is gitignored** (`.gitignore:85`, "working documents"). Two files there are tracked from before the rule, which makes it look safe — a doc written there is silently never committed. That is how the migration-112 spec was lost until caught at wrap-up. Worth deciding whether to keep the rule, and whether other repos have the same trap.
- **Enforcement of the type vocabulary was deliberately NOT added.** `allowed_entity_types` is read by nothing. Since the canonicalizing validator landed 2026-07-13, exactly **one** non-canonical row has been created, so a hard FK would reject at the database what the application already fixes. If enforcement is wanted, the right shape is a create-path guard that *logs*, not a constraint that drops the write.
- **The `Idea` type** is both the single post-validator drift case and one of the collisions. Retyped to `Concept` this session; if `Idea` is a real distinction it should be admitted instead.
- Polysemous names (`regen`, `indigenomics`, `ethereum`, `open`, `nature`, `amazon`) are **not** duplicates. The Organization "long tail" is 225 prefix pairs, but 126 have a short name with multiple long forms (`open` → 14 distinct orgs). That is a Tier 1.5 contextual-resolution problem, not a merge backlog.

## Verification and working tree

- **Branch/status:** `regen-prod`, 0 uncommitted, **0 ahead of origin**, `git diff --check` clean.
- **Verification:** red-baseline gate **10/10 PASS**; live-write governance 4/4; **155 focused tests pass**. Full suite at last run **44 failed / 1486 passed** against a *measured* `763ede4` baseline of 45/1438 — zero new failures, one fixed. Both policy flips are pinned by tests **proven non-vacuous** (reverting turns them red).
- **Canon validator:** not applicable — no `scripts/validate_spec_dag.py` in this repo.
- **API:** healthy, 30 entity types, 0 null embeddings. Graph: 31,485 live entities, 302 Meetings / 1,261 `attended`, 3 non-canonical rows, 4 intent proposals.
- **Every delete and retype this session is reversible** — 22 timestamped backup tables retained; retypes tombstone rather than delete.
- **Re-measure before acting.** Concurrent sessions write this database.

## Watch

- The 3 residual excess Meeting mappings are **INTENTIONAL** (same meeting, same date, multiple artifacts). Do not drive them to zero.
- Never de-dup Person rows naively — `dave` would misroute 20 of 22 "Dave" attendees away from David Fortson.
- Never purge fixtures on `ILIKE '%test%'` — that nearly deleted 93 genuine claims once.
- Do **not** add an `occurrence_count` column; it is RegenAI-era.
- `/entities/retype` **mints a new row** when no live row occupies the target URI — check first, or you recreate the duplicate you just merged.
- `tests/test_intent_registry.py` writes to the **live** database over HTTP by design; the conftest DSN redirect cannot contain it, so its teardown must stay complete.
- The 74 `koi_sustained_write` SpecDoc rows are **real content** (`bkc.foundations.*`), not load-test junk — verified no twins exist.

## Recent sessions

| Date | Provider | Session | Summary |
|---|---|---|---|
| 2026-08-22 | Claude Code | ffb7988e | Ontology safety rails → Meeting identity fix + promotion; 26 commits across 3 sessions |
| 2026-08-23 | Claude Code | (fresh) | Historical Meeting repair, resolver legacy/strict split, live-writer governance (`38c11fe`) |
| 2026-08-23/24 | Claude Code | c1defaa8 | `/register-entity` silent rollback; resolver → strict (both tiers, measured); intent-proposal leak; entity types 421→3; launchd guard. 15 commits (`763ede4..331b105`) |
