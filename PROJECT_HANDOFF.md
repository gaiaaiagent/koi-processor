# Project handoff

**Updated:** 2026-08-25 00:45 PDT
**Session:** Claude Code · b289ac1e-9563-4b45-9a49-bf2260e97e60 · Executed the prior handoff's plan end to end: all 9 sweep-finding fixes, the Meeting-notes repair, the last non-canonical entity retyped
**Status:** 12 commits, tree clean, not yet pushed (0 behind / 12 ahead of origin). Every item from the previous handoff is closed: all 9 planned sweep-finding fixes shipped, the 276-Meeting-note repair executed (270 re-measured fresh, 262 correctly handled, 8 surfaced and resolved a new secondary defect), and the last non-canonical entity-type row retyped — migration 112 is now fully complete (421 → 0).

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
> **Do not start ontology sessions in `RegenAI/koi-processor`.** It is a moving target sessions
> branch-switch freely, with no `PROJECT_HANDOFF.md` of its own — a session there starts oriented to
> whatever is at `~/projects/RegenAI/PROJECT_HANDOFF.md` instead, which is a different project.

## Completed this session

Executed the previous handoff's plan (`~/.claude/plans/can-you-make-a-fancy-torvalds.md`) in full, reviewed and corrected once by an adversarial re-read before execution.

- **All 9 Phase-A sweep-finding fixes shipped** (`33dbfbe`..`bd659c2`): consent-leakage gate's fail-open masking (surfaced 2 more instances of the same bug in the process); 3 missing SAVEPOINT gaps around `/register-entity`; `GET /tasks/` now 422s on malformed dates (paired with an MCP schema fix in `personal-koi-mcp`); commitment/pool graph-edge FK failures (6 sites, 100% guaranteed to fail) now surface via a `relationship_warning` field instead of silently 200'ing; Tier-3 entity creation now checks the INSERT's command tag (live-log-proven double-creation bug fixed); `soak-check.sh` no longer fabricates `rejected_total: 0` for an unreachable peer; the deploy-topology guard widened to cron (found and fixed a **live** violation) and now reports koi-sensors dependencies without false-failing them (no remedy target exists yet); `web_fetcher`'s entity scanner excludes tombstoned rows; two script exit-code fixes. Full suite re-verified after each commit — zero new failures throughout.
- **Bonus, unplanned but load-bearing: fixed a 9-minute hang in the test-cleanup script itself** (`8d98613`) — `live_write_cleanup.py`'s federation-table recovery scan did an unindexed `row_to_json(t)::text LIKE` across ~199K rows; measured 221s → 4.4s after adding a recency-bound fast path. Found because it blocked live verification of the A1 fix; had left a 5-month-old orphaned test row uncleaned.
- **Phase B: repaired the 276 Meeting notes** (live vault + DB operation, no git artifact besides the doc update in `1cea455`). Re-measured fresh rather than trusting the cached count (270 qualified today) — snapshotted `entity_relationships`/`pending_relationships`/`entity_rid_mappings` first. 234 notes got a live edge restored directly, 28 correctly queued to `pending_relationships` (working as designed), and 8 surfaced a genuine secondary defect: their vault note's title/`@type` had been edited independently after the 2026-08-22 backfill, so re-registration computed a different deterministic URI and left the pre-backfill row orphaned. Confirmed harmless (0 edges/links/facts) before backing up and deleting.
- **Phase C: retyped the last non-canonical entity-type row** — "BKC COP Emails", `Resource` → `Document` (operator decision, not `Project`). Used `/entities/retype` (not a raw `vault_register_entity` call — the Phase B orphan issue is exactly why: it mints a new URI on a type change and needs the retype endpoint's merge/tombstone handling, confirmed via direct code read of `_do_retype`). `dry_run:true` first, confirmed no existing `Document` twin, then applied (`merge_log_id: 258`). **Migration 112 is now fully complete: 421 → 0 non-canonical rows**, live-verified.
- **12 commits, all individually verified** (full suite run after most; final state below).

## Next steps

1. **koi 8386 — re-measure entity creation after the strict resolver flip** (due 2026-08-31, clock-gated — nothing to do before then). Methodology and exact SQL are on the task itself and were re-verified live this session (see the previous handoff's Phase D). Feeds directly into any future entity-type retype pass's collision-group sizing.
2. **Push the 12 unpushed commits** (`regen-prod`, 0 behind / 12 ahead of `origin/regen-prod`) — not done automatically; needs explicit operator go-ahead per this project's push discipline.
3. **Optional (Phase E, only if time permits):** seed `darren-workflow:defect-class-sweep` on the confirmed `curl -sf ... || echo '<fallback>'` / `.get(key, {})`-masks-a-failure-as-success idiom (now proven independently in two files this session) across this repo and adjacent ones. Also optional: decide whether to establish a `koi-sensors-runtime` clone (would let A7's koi-sensors warning become a hard-fail with a real remedy, mirroring `koi-processor-runtime`'s pattern).

## Open questions

- **Rank 11 and rank 14 sweep findings deliberately deferred**, not fixed: task write-path date silent-null (zero observed occurrences in ~6mo) and `/entity/resolve`'s `ambiguous:false` at `limit<=1` (zero current callers hit it). Revisit only if the operator wants to close opportunistically — both have a clear fix shape recorded in `docs/architecture/silent-success-sweep-20260824.md` if so.
- **`docs/planning/` and `docs/soak-results/` are still gitignored** (`.gitignore`), unresolved from before this session — not touched this pass. A doc written there is silently never committed.
- **No koi-sensors-runtime clone exists**, unlike `koi-processor-service`/`koi-processor-runtime` — 3 launchd jobs (`email-sensor`, `email-watcher`, `proton-email-sensor`) depend on `~/projects/RegenAI/koi-sensors`, a checkout with the identical branch-switches-freely risk as `RegenAI/koi-processor`, currently only reported (pytest warning), not hard-failed, because there's no repoint target. Deciding whether to establish one is a separate, deferred decision.

## Verification and working tree

- **Branch/status:** `regen-prod`, 0 uncommitted, **12 ahead of origin (not pushed)**, `git diff --check` clean.
- **Verification measured at `1cea455`** — the final commit: full suite **43 failed / 1527 passed** against the *measured* `763ede4` baseline of 45 failed / 1438 passed — **zero new failures**, +89 net passing (this session's new/fixed tests). Re-run after every Phase-A commit, not just at the end.
- **Canon validator:** not applicable — no `scripts/validate_spec_dag.py`.
- **Live:** API healthy, 30 entity types, 0 null embeddings, **0 non-canonical entity-type rows** (was 1 at session start, 421 at migration-112's start).
- **32 backup tables retained**; every delete, merge and retype this session is reversible. This session added: `backup_20260825_meeting_repair_{entity_relationships,pending_relationships,entity_rid_mappings}`, `backup_20260825_orphaned_meeting_entities_{registry,mappings}`. `entity_merge_log` id 258 is the BKC COP Emails retype.
- **Re-measure before acting.** Concurrent sessions write this database — this session's own Phase B repair depended on re-deriving its target list fresh rather than trusting the prior session's cached 276/281 count (270 qualified by execution time).

## Watch

- **`/entities/retype` mints a new row when no live row occupies the target URI** — live-experienced twice this session: correctly (via the endpoint itself, which merges/tombstones properly, verified via `_do_retype`'s code) and incorrectly (via a raw `vault_register_entity` call during Phase B, which has no merge-back logic and left 8 rows orphaned). **Never call `vault_register_entity` on a note whose type is also changing — use `/entities/retype` for that, always with `dry_run:true` first.**
- The 3 residual excess Meeting mappings (noted in the prior handoff) are **INTENTIONAL**. Do not drive them to zero.
- Never de-dup Person rows naively — `dave` would misroute 20 of 22 "Dave" attendees.
- Never purge fixtures on `ILIKE '%test%'`; do **not** add an `occurrence_count` column.
- `regen`/`open`/`nature`/`amazon`/`indigenomics`/`ethereum` are **polysemous, not duplicates**: 126 of 225 Organization prefix pairs have a short name with multiple long forms.
- `tests/test_intent_registry.py` and `tests/test_task_registry.py` write to the **live** DB over HTTP by design.
- The 74 `koi_sustained_write` SpecDoc rows are **real content**, not load-test junk.
- **`live_write_cleanup.py`'s federation-table scan is now recency-bounded (24h window)** — correct for any normal test run, but if a live-write test ever genuinely needs longer than 24h to complete, its cleanup would miss federation-table rows outside that window. Not expected to matter for this repo's actual test durations (seconds to low minutes).

## Recent sessions

| Date | Provider | Session | Summary |
|---|---|---|---|
| 2026-08-23 | Claude Code | (fresh) | Historical Meeting repair, resolver legacy/strict split, live-writer governance (`38c11fe`) |
| 2026-08-23/24 | Claude Code | c1defaa8 | `/register-entity` silent rollback; resolver → strict (both tiers, measured); intent-proposal leak; entity types 421→1; launchd guard; silent-success sweep (14 confirmed). 18 commits (`763ede4..2a50f07`) |
| 2026-08-24/25 | Claude Code | b289ac1e | Executed the full prior-session plan: all 9 sweep-finding fixes + a bonus cleanup-script perf fix; Meeting-notes repair (270 notes, re-measured fresh, 8-row orphan issue caught and fixed); last non-canonical entity retyped (migration 112 complete, 421→0). 12 commits (`8d98613..1cea455`), not yet pushed. |
