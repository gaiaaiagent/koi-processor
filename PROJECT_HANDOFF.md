# Project handoff

**Updated:** 2026-08-26 11:05 PDT
**Session:** Claude Code · c1defaa8-ad3c-4de2-9e54-47361c370b33 · Verification pass over the conflict-sweep + koi-sensors-runtime work
**Status:** Tree clean at `61b8fb1`, synced with origin, full suite **43 failed / 1586 passed** with **no `--ignore` needed** — zero new failures vs the measured `763ede4` baseline. The vault-conflict sweep is **canary-proven firing unattended**. koi 8386 is the only open item and is clock-gated to 08-31.

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

This session's own contribution was **review and independent verification**, not implementation — the fixes below were executed by concurrent sessions and checked here against primary data.

- **Reviewed the next-steps plan before execution** and found one item not executable as written: A7's "repoint the jobs onto a stable checkout" had **no target** — 3 of the 4 named jobs ran from `RegenAI/koi-sensors` (a *different repo*, clean on `main`), no `koi-sensors-runtime` existed, and the deploy topology never covered that repo. Also caught an internal gating inconsistency (A2 said "before Phase C"; the rationale and Phase B's own gate said Phase B) that could have run the 276-note repair with the savepoint gaps still open.
- **Flagged A3 as not zero-risk**: the MCP documents these params as *"ISO date YYYY-MM-DD"* but passes them through unvalidated. Verified live that `due_before=2026-08-24T00:00:00Z` silently returned **4,265** rows vs **415** for a valid date — so A3 converts a silent wrong answer into a hard 422 on live tooling.
- **Recommended a snapshot before Phase B's replace-all deletes**; it was taken (5 backup tables, 1,181 relationship rows).
- **Caught that the conflict-storm cleanup was incomplete** — 101 true conflict files remained after it was declared done, which led to the third-wave re-triage and the 60-minute stability monitor. My first count of 125 was wrong (my glob swept legitimate `…round 2.md` notes); the precise figure was 101.
- **Named the root cause nobody had**: `~/Documents/Notes` sits inside iCloud Desktop & Documents sync with multiple concurrent writers (MCP `vault_write_note` from parallel sessions, `com.personal.koi-knowledge-health`, Obsidian itself). Cleanup was symptomatic; that framing produced the scheduled-sweep decision.
- **Canary-proved the sweep fires unattended** — it fired at **10:47:57**, matching the predicted ~10:48 after the reload reset its countdown. Independently mutation-tested `test_the_plist_cannot_storm`: planting `KeepAlive` and lowering `ThrottleInterval` each turn it red, restore turns it green.
- **Verified the whole stack against this repo's own rules**: sweep runs from `koi-processor-runtime`, no `KeepAlive`, `ThrottleInterval == StartInterval == 1800`, covered by the launchd guard, committed and installed plists identical.

## Next steps

1. **koi 8386 — re-measure entity creation after the strict resolver flip.** Clock-gated to **2026-08-31**; nothing to do before then. Methodology is on the task: compare same-source channels only, exclude bursts, normalise per hour, and treat `resolution_tier='tier3_created_ambiguous'` as the decisive signal.
2. *(optional, low value)* Two pre-existing gap notes and 6 unregistered notes remain from the Meeting work; a defect-class sweep on the `curl -sf … || echo '<fallback>'` idiom was seeded but never run.

## Open questions

- **`test_vault_sync.py` has order-dependent failures.** `test_scan_new_file` / `test_scan_modified_file` failed in one full-suite run, **passed in isolation**, and running that file alone failed *differently* again. They did **not** reproduce at clean HEAD, so this is not a regression from the vault-sync commits — but it means suite counts for that file are not stable run-to-run, and a count delta there is not by itself evidence anyone broke something.
- **The iCloud hazard is now detected, not eliminated.** The 30-minute sweep auto-cleans stale conflict copies and files a task for anything with unique content. The underlying condition — an iCloud-synced vault with several concurrent writers — is unchanged. If storms recur at scale, the durable options are moving the vault out of iCloud sync or reducing concurrent writers.
- **`docs/planning/` and `docs/soak-results/` are gitignored**, with two files tracked from before the rule, which makes the directory look safe. A doc written there is silently never committed.

## Verification and working tree

- **Branch/status:** `regen-prod`, **0 uncommitted**, 0 ahead, 0 behind origin, `git diff --check` clean.
- **Verification measured at `61b8fb1`:** full suite **43 failed / 1586 passed**, **no `--ignore` flag** — the previously uncollectable `test_koi_flow_integration.py` now contributes 11 real passes. Zero new failures vs the measured `763ede4` baseline (45/1438). Red-baseline gate **10/10**; governance 4/4; 73 focused tests.
- ⚠ **An earlier reading of 46 failed / 1583 was an artifact**, not a regression: it was measured while a concurrent session's `test_federation_bridge.py` work was still uncommitted. At clean HEAD the number is 43, matching what that session reported.
- **Canon validator:** not applicable — no `scripts/validate_spec_dag.py`.
- **Live:** API healthy, 30 entity types, 0 null embeddings, **0 conflict files**, sweep last ran 10:47:57 and is firing on its 30-minute schedule.

## Watch

- The 3 residual excess Meeting mappings are **INTENTIONAL**. Do not drive them to zero.
- Never de-dup Person rows naively — `dave` would misroute 20 of 22 "Dave" attendees.
- Never purge fixtures on `ILIKE '%test%'`; do **not** add an `occurrence_count` column.
- `/entities/retype` **mints a new row** when no live row occupies the target URI — check first.
- `regen`/`open`/`nature`/`amazon` are **polysemous, not duplicates** (`open` → 14 distinct orgs).
- A **replace-all** write must never take a partial payload, and must never stamp a whole-artifact freshness marker from one — that is what cost 276 notes their `project`/`location` edges.
- **This checkout is shared.** Concurrent sessions commit here; scope every `git add` to your own files and re-measure before quoting counts.

## Recent sessions

| Date | Provider | Session | Summary |
|---|---|---|---|
| 2026-08-23/24 | Claude Code | c1defaa8 | `/register-entity` silent rollback; resolver → strict (both tiers, measured); intent-proposal leak; entity types 421→1; launchd guard; silent-success sweep (14 confirmed). 18 commits (`763ede4..2a50f07`) |
| 2026-08-24/25 | Claude Code | b289ac1e | Executed the full prior plan: 9 sweep fixes, Meeting-notes repair (270 notes), migration 112 complete (421→0). Established `koi-sensors-runtime` + hardened the launchd guard. |
| 2026-08-26 | Claude Code | b289ac1e (resumes) | 3rd conflict wave cleaned; built `com.personal-koi.vault-conflict-sweep`; fixed a real `ThrottleInterval < StartInterval` bug + added a mutation-tested anti-storm pin; fixed `test_koi_flow_integration.py`'s months-stale collection failure (11 tests now run for real). |
| 2026-08-26 | Claude Code | c1defaa8 | **Verification pass.** Reviewed the plan pre-execution (found A7 unexecutable, a gating inconsistency, A3's live blast radius); caught the conflict cleanup was incomplete at 101 files; named the iCloud root cause; canary-proved the sweep fires unattended at 10:47:57; mutation-tested the anti-storm pin. No code changes. |
