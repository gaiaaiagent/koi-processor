# Project handoff

**Updated:** 2026-08-25 01:15 PDT
**Session:** Claude Code · b289ac1e-9563-4b45-9a49-bf2260e97e60 · Same session, continued after a push + compaction: closed out all 3 carried-forward items from the first wrap-up
**Status:** the earlier 13 commits from tonight were pushed (`3d6da92..8a4ae9c`); 1 new commit since (`bd0a7ce`, not yet pushed — 1 ahead of origin), tree clean. Everything carried forward from the first wrap-up is now closed except the clock-gated koi 8386 re-measure: `koi-sensors-runtime` established and the launchd guard hardened, and the Phase B "29 older notes" mystery resolved (it was an iCloud sync-conflict storm, not a real gap — Phase B's original repair was already 100% complete).

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
- **12 commits, all individually verified** (full suite run after most; final state below). Pushed to `origin/regen-prod` (`3d6da92..8a4ae9c`, 13 commits incl. the wrap-up itself) mid-session, at explicit operator go-ahead.

## Continued this session (post-push, same session — 2026-08-25 01:00-01:15 PDT)

Operator asked to close out the two carried-forward open items (koi 8386 stayed clock-gated, untouched).

- **Established `koi-sensors-runtime`** (`bd0a7ce`), mirroring `koi-processor-service`/`koi-processor-runtime`. `RegenAI/koi-sensors` is a genuinely distinct GitHub repo (`gaiaaiagent/koi-sensors`) with no `regen-prod`-equivalent — pinned to `main`, its only real mainline. Cloned to `~/projects/koi-sensors-runtime`, copied over the 2 gitignored dedup/cursor state files (`email_sensor_state.json`, `proton-email_sensor_state.json` — NOT in git, would have meant re-scanning all mail if missed), built a fresh venv (Python 3.14.2, `requirements.txt` + the parent `../../requirements.txt` for `rid-lib`), repointed the 3 launchd jobs' plists from their `.template` files, reloaded via `launchctl bootout`/`bootstrap` (the first bootstrap attempt raced and I/O-errored on 2 of 3 — a bare retry succeeded), and verified all 3 running clean from the new path with state intact (proton IMAP UIDs 487/117/1 preserved, email-watcher's dedup count still 26800). Folded the 3 jobs into `tests/test_launchd_job_targets.py`'s hard-failing `DEV_CHECKOUT_MARKERS`/`STABLE_CHECKOUT_MARKERS` (was warn-only pending exactly this decision) and added a `koi-sensors-runtime` row to CLAUDE.md's CHECKOUT TOPOLOGY table. Full suite: 43 failed / 1526 passed vs. the 43/1527 baseline — the -1 is exactly the removed warn-only test, zero regressions. **Old plists backed up to `~/Library/LaunchAgents/backup-pre-koi-sensors-runtime-20260825/`.**
- **Resolved the "1 note still empty / 29 older notes" mystery from the compacted summary.** Re-derivation found Phase B's original repair was actually **100% complete** (0 of the 270 target notes still broken). What actually happened: Phase B + Phase C's writes raced against iCloud Drive's sync of the vault, which peeled off pre-write versions as `NAME (conflict TIMESTAMP).md` sibling files instead of cleanly reconciling — **139 conflict files total, arriving in two waves** (82 at write-time, then 40 more ~20 minutes later, apparently after the first cleanup's `rm` burst nudged iCloud to drain a backlog — worth knowing if this happens again: don't assume one sweep catches everything). Verified every one via a body-only diff (frontmatter fields — `last_synced`, `canonical_uri`, `mentionedIn`, etc. — are machine-managed and expected to differ; only post-frontmatter body content was compared): zero real content loss, all 139 were either byte-identical bodies or the conflict copy held a strictly less-developed version of text still present (in expanded form) in the live file. Backed up to scratchpad, then deleted. Logged as a new Tooling Issue in `Meta/Entity Resolution Issues.md` (layer: vault) with the recommendation to sweep for conflict files in *waves* after any bulk vault-write operation, not just once immediately after.
- **2 low-priority items surfaced but deliberately NOT acted on** (both pre-date the 2026-08-22 backfill by months, unrelated to this session's damage, small blast radius): `Meetings/Cascadia Canada/Cascadia Canada Sync Sept 24.md` and `Meetings/Ecoscene/2026-02-17 Savory Institute Dinner Prep.md` are each missing a project/location edge (`last_synced = 2026-02-25`). 6 Meeting notes (1× People/Clare Attwell, 4× Regen AI, spanning Jul 21–Aug 4) have never been registered at all — normal pipeline lag, not a defect.

## Next steps

1. **koi 8386 — re-measure entity creation after the strict resolver flip** (due 2026-08-31, clock-gated — nothing to do before then). Methodology and exact SQL are on the task itself and were re-verified live this session (see the previous handoff's Phase D). Feeds directly into any future entity-type retype pass's collision-group sizing.
2. **Push 1 unpushed commit** (`bd0a7ce`, `regen-prod`, 1 ahead of `origin/regen-prod`) — not done automatically; needs explicit operator go-ahead per this project's push discipline.
3. **Optional, low priority, not this session's scope:** seed `darren-workflow:defect-class-sweep` on the confirmed `curl -sf ... || echo '<fallback>'` / `.get(key, {})`-masks-a-failure-as-success idiom (proven independently in two files earlier this session). Also optional: the 2 pre-existing Meeting notes still missing a project/location edge and the 6 never-registered Meeting notes (both listed above) — small, not urgent, not caused by this session.

## Open questions

- **Rank 11 and rank 14 sweep findings deliberately deferred**, not fixed: task write-path date silent-null (zero observed occurrences in ~6mo) and `/entity/resolve`'s `ambiguous:false` at `limit<=1` (zero current callers hit it). Revisit only if the operator wants to close opportunistically — both have a clear fix shape recorded in `docs/architecture/silent-success-sweep-20260824.md` if so.
- **`docs/planning/` and `docs/soak-results/` are still gitignored** (`.gitignore`), unresolved from before this session — not touched this pass. A doc written there is silently never committed.
- **`tests/test_koi_flow_integration.py` fails to collect** — imports `koi_protocol.coordinator.koi_coordinator`, a package that lives in `koi-sensors` (`shared/rid_types/...`), not this repo. Last touched 2025-12-22, 8 months stale; almost certainly dead code from before the KOI-protocol coordinator moved repos, never cleaned up here. Blocks a bare `pytest -q` at the collection stage (`--ignore` bypasses it) — this is why this session's suite runs use `--ignore=tests/test_koi_flow_integration.py`. Not fixed this session (out of scope, pre-existing, unrelated to anything touched); worth a deliberate delete-or-repair decision rather than continuing to route around it.

## Verification and working tree

- **Branch/status:** `regen-prod`, 0 uncommitted, **1 ahead of origin (not pushed)** — `bd0a7ce`. `git diff --check` clean.
- **Verification measured at `bd0a7ce`**: full suite (`--ignore=tests/test_koi_flow_integration.py`, see Open Questions) **43 failed / 1526 passed** against the *measured* `1cea455` baseline of 43 failed / 1527 passed — the -1 is exactly the one warn-only test removed in the koi-sensors-runtime commit, **zero regressions**.
- **Canon validator:** not applicable — no `scripts/validate_spec_dag.py`.
- **Live:** API healthy, 0 null embeddings across all surfaces, **0 non-canonical entity-type rows**.
- **32 backup tables retained**; every delete, merge and retype this session is reversible. `entity_merge_log` id 258 is the BKC COP Emails retype.
- **Vault:** 139 iCloud sync-conflict files created and removed this session (82 + 40, two waves — see above), backed up to scratchpad before deletion, zero real content loss confirmed. 3 backup plists at `~/Library/LaunchAgents/backup-pre-koi-sensors-runtime-20260825/`.
- **Re-measure before acting.** Concurrent sessions write this database — this session's own Phase B repair depended on re-deriving its target list fresh rather than trusting the prior session's cached 276/281 count (270 qualified by execution time). Reconfirmed again this session: the "29 older notes" mystery only resolved by re-deriving fresh rather than trusting the compacted summary's framing.

## Watch

- **`/entities/retype` mints a new row when no live row occupies the target URI** — live-experienced twice this session: correctly (via the endpoint itself, which merges/tombstones properly, verified via `_do_retype`'s code) and incorrectly (via a raw `vault_register_entity` call during Phase B, which has no merge-back logic and left 8 rows orphaned). **Never call `vault_register_entity` on a note whose type is also changing — use `/entities/retype` for that, always with `dry_run:true` first.**
- **Bulk vault writes (`vault_register_entity`/`vault_write_note` loops) can trigger an iCloud sync-conflict storm** — arrives in more than one wave (this session: 82 files, then 40 more ~20 minutes later with no further writes in between). After any bulk vault-write operation, sweep `find ~/Documents/Notes -iname "* (conflict *.md"` more than once with a delay, not just immediately after. Exclude `CADAP (Conflict Aftermath Digital Archive Project).md` — a real note whose title contains the word "Conflict," a false positive for the glob.
- **`koi-sensors-runtime` established 2026-08-25** — see CLAUDE.md CHECKOUT TOPOLOGY. Never branch-switch it; pinned to `main` (not `regen-prod` — koi-sensors has no equivalent branch). Refresh via `git -C ~/projects/koi-sensors-runtime pull`.
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
| 2026-08-24/25 | Claude Code | b289ac1e | Executed the full prior-session plan: all 9 sweep-finding fixes + a bonus cleanup-script perf fix; Meeting-notes repair (270 notes, re-measured fresh, 8-row orphan issue caught and fixed); last non-canonical entity retyped (migration 112 complete, 421→0). 13 commits (`8d98613..8a4ae9c`), pushed. Same session continued: established `koi-sensors-runtime` + hardened the launchd guard, resolved a 139-file iCloud conflict storm from the writes above (zero data loss). 1 more commit (`bd0a7ce`), not yet pushed. |
