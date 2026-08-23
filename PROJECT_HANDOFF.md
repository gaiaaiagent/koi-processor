# Project handoff

**Updated:** 2026-08-23 12:30 PDT
**Session:** Claude Code · c1defaa8-ad3c-4de2-9e54-47361c370b33 · Wikilink silent-rollback defect → intent leak → resolver replay
**Status:** Six commits pushed (`763ede4..2169721`). Two defects that were NOT on yesterday's list are fixed and deployed (API restarted 12:05:33, PID 82263). The resolver shadow gate has returned a real verdict — `explicit_policy_split`, 36 outcome divergences — instead of the ~170-day wait. One clock-gated item and one operator decision remain.

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

Six commits, all pushed. The API was restarted at 12:05:33 so the fixes are live — before that
it had been running code from 23:57 the previous night.

### The wikilink silent-rollback defect (not on yesterday's list)

`/register-entity` was returning HTTP 200 `success=true` while **rolling back the registration
itself**. Three defects composed:

1. `api/vault_parser.py` `parse_wikilink` did `path.rsplit('/', 1)`, so a nested vault path
   produced the folder key `"meetings/bkc cop"`. Every key in `folder_type_map` is a single
   segment, so nested paths matched nothing and came back untyped. Nested is the vault's actual
   convention (`Meetings/<series>/<date> <title>`).
2. The untyped tier ordered by `occurrence_count` — a RegenAI-era column that exists in
   **zero** tables of `personal_koi`. It raised `UndefinedColumnError` on every execution.
3. The handler caught that exception and kept going. **`except Exception` does not un-abort a
   PostgreSQL transaction**, and the whole handler runs in one `conn.transaction()`, so every
   later statement failed and the closing COMMIT became a silent ROLLBACK.

Fixed in `56f61d1`, `98d3d26`, `f169f29`, `d38ce26`: real column with a deterministic tiebreak,
segments walked outermost-first, `SAVEPOINT vault_rel_sync` containment, and a
`relationship_sync_error` field so partial failure is reportable.

**Dating, from git, not inference:** the bad `ORDER BY` shipped 2026-02-01 (`01343c4`), but
`sourceNote` became a mapped predicate field on 2026-02-26 (`8681900`) — one day *after* the
Feb-25 bulk sync. That is why 97 Task notes registered and then effectively none did for six
months.

**Population, measured with a control (koi task 8292):**

| | count |
|---|---|
| Would have rolled back, and are NOT registered | **1,924** (1,902 `Tasks/`) |
| Would fail today but ARE registered | 60 — all 2026-02-25, before `sourceNote` was mapped |
| Clean targets, unregistered — never pushed, NOT this defect | 3,456 |

The 60 are the falsification test the model had to pass. The 3,456 is the control against
overclaiming. **No repair done** — that is a separate decision.

### The intent_match_proposals leak (also not on the list)

The 2026-08-22 purge closed the three tables it swept. `POST /intents/match` writes a fourth,
`tests/test_intent_registry.py` exercises it over HTTP against the live backend, and nothing
deleted it — so the leak continued one table over: 260 rows, 256 pointing at intents that no
longer existed. Rows were still arriving; **some of today's were produced by this session's own
test runs**, which is the mechanism in miniature (the conftest DSN redirect cannot contain a
suite that talks HTTP to the live API).

`c5d3759`: teardown extended (keyed on intent RID, `OR` not `AND`), conftest tripwire widened,
and `scripts/check_intent_leak_observation.py` now counts orphaned proposals **with a positive
control**. Without that last part, the AC1 gate would have reported `orphaned: 0` at 13:47 today
and closed task 7878 while 256 orphans sat in the database.

256 purged, backed up in full to `intent_match_proposals_backup_orphans_20260823`. Partition was
clean: 256 both-missing, **0 half-orphans**, 4 intact.

### The resolver shadow gate now has an answer

`2169721` adds `scripts/replay_resolver_shadow.py`. Live sampling needed ~170 days and could
never reach the 10 of 13 callers with no organic traffic; after a full day exactly **one**
observation existed. The replay produced **1,110 attempts in 10m26s, all 13 callers, 0 dropped**.

**Verdict: exit 3, `explicit_policy_split` — 358 candidate divergences, 36 outcome divergences
(3.2%).** Legacy and strict do NOT agree, so the wrapper is load-bearing and consolidation is
unsafe. Evidence retained at `evidence/resolver-shadow/replay-20260823.{log,report.json}`.

Admissibility is enforced, not assumed: records carry `"replay": true`, and the analyzer counts
them for divergence/attempts/callers but **excludes them from overhead and elapsed-days** — in a
replay the shadow comparison is the entire workload, so its overhead ratio would trip exit 4 for
a reason unrelated to the policy. Tests carry the live-record control for each exclusion.

## Next steps

1. **koi 7878 — AC1.** Evaluate at/after **2026-08-23 13:47 PDT**:
   `venv/bin/python scripts/check_intent_leak_observation.py`. As of 12:30 it reads
   `nonconforming: 0, orphaned: 0, orphaned_proposals: 0` and correctly refuses to close
   (`window_elapsed: false`). Positive controls: 225 entity rows, 256 proposal rows.
2. **DECIDE: flip the resolver policy from legacy to strict (koi task 8294).** Every one of the
   36 divergences inspected favours strict. **18 of them are the cross-date Meeting collapse this
   repo repaired in the DATA yesterday** — `active_policy` is still `legacy`, so the guard that
   caused it would recreate it on the next resolution. Yesterday fixed the symptom, not the
   cause. But strict refuses matches legacy accepts, so expect fewer auto-merges and **more new
   entities** — measure before and after; it interacts with migration 112.
3. **Migration 112** — eligible after **2026-08-29 10:05 PDT**, but the date is not the real
   blocker: there is no specification anywhere (no SQL, no ADR, no task), only a two-line
   parking-lot entry and `scripts/check_migration_112_evidence.py`. Also 274 of 314 stamped rows
   (87%) are the Meeting backfill burst and the gate does not separate burst from organic.
   Keep `Organization` a distinct core type; no Person deduplication.
4. **The launchd guard has a blind spot.** `tests/test_launchd_job_targets.py:50` globs
   `com.personal-koi.*.plist`; three installed jobs use `com.personal.koi-*` and are uncovered —
   including `com.personal.koi-repo-doc-sensors`, which runs `doc_scanner.py` **out of the shared
   dev checkout**, the exact dependency the guard exists to forbid. Widening the glob alone is not
   enough: the guard reads only `ProgramArguments`/`Program`, never `WorkingDirectory`.

## Resolved — strike from the old list

- **Schema-dump drift (task 7878) was false when written.** `~/koi-backups/personal_koi-schema.sql`
  was refreshed 2026-08-22 13:59 PDT and has both columns; the task was created the same minute.
  Live-vs-test column diff is zero both ways.
- **The "missing" backup for the four stamped rows exists** —
  `entity_registry_backup_resolution_tier_gap_20260822`. The earlier session probed the wrong
  date suffix (`…_20260823`). Nothing was at risk: migration 111 added the column with no
  backfill, so the prior value was NULL by design.
- **Both old open questions answered.** No caller ever *stopped* registering Meetings — none ever
  did routinely; there are two bulk bursts, and the gap is a default folder list in
  `personal-koi-mcp`, not this repo. And `entity_registry.entity_type` is authoritative for
  behaviour while `entity_rid_mappings.entity_type` is authoritative for vault-facing reads —
  the `meeting-bioregional-learning` row is two surfaces answering two questions, not a conflict.

## Corrections made this session

- A commit message here (`98d3d26`) claimed "registering the entity genuinely succeeded". It did
  not — the transaction was poisoned. `d38ce26` states and fixes that.
- The orphaned proposals were reported mid-session as "served verbatim by `GET /intents/proposals`".
  Wrong: that endpoint inner-joins twice, so orphans never surfaced. It was table residue plus a
  blind gate, not a poisoned read surface.
- A comment in `vault_parser.py` (and its copy in `test_tombstone_isolation.py`) said the untyped
  tier "actively PREFERS the tombstone, which typically has the higher occurrence_count". It
  described behaviour that tier never had, since the query could not run. Both corrected.

## Watch

- **The 3 residual excess Meeting mappings are INTENTIONAL** — same meeting, same date, multiple
  artifacts. Do not drive them to zero.
- **Never de-dup Person rows naively** — the `dave` alias would misroute 20 of 22 "Dave" attendees
  away from David Fortson.
- **Never purge fixtures on `ILIKE '%test%'`** — that nearly deleted 93 genuine claims once.
- **Do not add an `occurrence_count` column** to `personal_koi` to "fix" anything.
- The **intent suite writes to the live database over HTTP**; the conftest DSN redirect cannot
  contain it. That is by design and is why its teardown must be complete.
- Concurrent sessions write this DB. Re-measure before acting.

## Verification

- Branch `regen-prod`, 0 uncommitted, **0 ahead of origin** (pushed `2169721`).
- Red-baseline gate **10/10 PASS**; live-write governance 4/4; meeting suites 57/57; tombstone
  isolation 16/16; new wikilink suite 19/19; shadow + replay 15/15.
- Full suite: **44 failed / 1455 passed**, against a measured pre-session baseline of
  **45 failed / 1438 passed** at `763ede4` — no new failures, +17 passes. The residual failures
  are pre-existing test-double and test-DB issues (`_FetchvalConn` lacks `.fetch`,
  `test_project_router` asyncpg errors) plus `tests/test_koi_flow_integration.py`, which fails to
  import (`No module named 'koi_protocol'`).
- API healthy, PID 82263, started 2026-08-23 12:05:33, cwd `koi-processor-service`.
- Every delete has a timestamped backup table.
