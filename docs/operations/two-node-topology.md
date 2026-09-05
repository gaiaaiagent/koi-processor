# Two-node topology: what actually flows to the NUC, and what does not

**Measured 2026-09-04** (session `a0f88bbf`). Every claim below was produced by a command run
that day, against the live laptop and the live NUC. Figures are snapshots of a moving system —
re-measure before acting on any of them.

This document exists because the reverse claim was believed and acted on. Commit `dcda7f7`
corrected `CLAUDE.md`'s "commit to `regen-prod` → the NUC gets it", which was false. What
follows is the positive statement that correction implied but did not write down.

---

## 1. The one-sentence shape

`deploy.sh` moves **files from a checkout the laptop does not serve** to a NUC whose Postgres
schema is mutated **entirely by hand**; the only automated cross-host check is a weekly 4-table
row-count sweep whose last OK verdict was 2026-05-17 and which reports into a log nobody gates on,
and it is structurally incapable of seeing either the 5 phantom migration-ledger rows or the 12
vocabulary rows that exist on the laptop and not on the NUC. (A second monitor, `soak-check.sh`,
runs every 2h and *spans* both hosts, but each half reconciles one node against its own disk — it
never compares the two to each other. §8 counts both as "cross-host monitors"; this sentence counts
only the one that actually compares them.)

**And as of 2026-09-04 the koi-processor rsync leg is not running at all** — see §2a. The NUC's
frozen tree is a consequence, not a coincidence.

---

## 2. What flows

`~/projects/dobby/scripts/deploy.sh` (287 lines) performs **4 rsyncs**, all excluding the
repository metadata directory:

| source | destination | `--delete` |
|---|---|---|
| `~/projects/RegenAI/koi-processor/` (dry-run guard, aborts if >5 deletions) | `dobby@192.168.1.69:projects/RegenAI/koi-processor/` | dry-run only |
| `~/projects/dobby/` | `:/home/dobby/dobby/` | yes |
| `~/projects/personal-koi-mcp/` | `:/home/dobby/personal-koi-mcp/` + remote `npm install --production` | yes |
| `~/projects/RegenAI/koi-processor/` | `:projects/RegenAI/koi-processor/` | yes |

### 2a. ⚠ The koi-processor leg is currently BLOCKED, and in practice SKIPPED

The first table row is aspirational today. `deploy.sh` runs a blast-radius guard before syncing
koi-processor: it does the rsync as a dry run, counts deletions, and aborts if the count exceeds
`KOI_DELETE_LIMIT` (default **5**). Measured 2026-09-04, that dry run reports **158 deletions**, so
the guard exits 1 and *nothing* is synced or restarted.

The script names its own workaround, and it is the one that gets used:

```
SKIP_KOI_SYNC=1 bash scripts/deploy.sh
```

Under `SKIP_KOI_SYNC=1` the dobby rsync, the personal-koi-mcp rsync + remote `npm install
--production`, and **both** `systemctl restart`s still run — only the NUC's koi-processor tree is
left untouched.

**So the honest statement is stronger than "the deploy source is a moving dev checkout": right now
koi-processor code does not reach the NUC by `deploy.sh` at all.** That is why the NUC's tree is
frozen at migration 107 / 2026-07-14 and why someone had to hand-commit its live state on
2026-08-31. Any plan that assumes `deploy.sh` will carry a fix to the NUC is wrong twice over.

It also pushes **Claude Code OAuth credentials** read from the macOS Keychain to
`~/.claude/.credentials.json`, then runs `sudo systemctl restart dobby-koi-processor` and
`dobby-gateway`.

Four laptop LaunchAgents move data independently of `deploy.sh`. Two push
(`sync-daily-notes-to-nuc`, `sync-events-to-nuc`), two pull (`sync-nuc-sessions`,
`mirror-nuc-vault`). **None touches Postgres schema or the vocabulary tables.**

---

## 3. What does not flow: schema

`deploy.sh` applies **zero** schema changes. Counted with positive controls so the zeros are
proven searches rather than broken patterns:

```
grep -ci psql deploy.sh             -> 0     grep -c systemctl deploy.sh -> 3   (control)
grep -ci migrate deploy.sh          -> 0     grep -c rsync deploy.sh     -> 17  (control)
grep -ci apply_migration deploy.sh  -> 0     grep -c 'ssh ' deploy.sh    -> 16  (control)
grep -ci alembic deploy.sh          -> 0
grep -ci pg_dump deploy.sh          -> 0
```

The only `migrat` substring in the entire file is inside a comment on line 111.

**Consequence — corrected 2026-09-04, and the correction is time-bounded.** An earlier draft said
"every NUC migration is hand-delivered and hand-applied." The second half is right; **the first is
false**. The `.sql` *files* arrived by rsync: by ctime, **124 of 128 landed on 2026-06-24** and the
last **4 on 2026-07-19**. Nothing has arrived since — which is exactly why the tree tops out at
**107**, and why 111/114/115 could be *applied* there with no file to show for it.

The accurate statement: **files flowed until 2026-07-19 and have not since; execution has always
been manual.** There is no migration scheduler — the NUC's timer surface (28 system + 4 user
timers, `systemctl --user` required for the latter, no crontab for `dobby` or `root`, stock
`/etc/crontab`) contains no migration unit.

*Method note, because it inverted the answer once.* mtime and ctime answer different questions
here: `rsync -a` preserves the source **mtime**, so mtimes are *authoring* dates, while **ctime**
is when the inode landed. Whole-second mtimes mark rsync-delivered files (128/128 of the
migrations, with `api/*.py` as a matching control); sub-second means born on the box. A first pass
at this test compared the nanosecond field against **ten** zeros when `stat -c %y` emits **nine**,
so every file failed and the conclusion came out exactly backwards.

---

## 4. Three trees, three branches — the deploy source is not the serving source

| tree | role | branch (2026-09-04) | has migrations 111–118? |
|---|---|---|---|
| `~/projects/koi-processor-service` | **the laptop serves this** (uvicorn cwd, :8351) | `regen-prod` `fd3f430` | yes |
| `~/projects/RegenAI/koi-processor` | **`deploy.sh` pushes this** | `darren/tenant-stamping-phase1` | **no** — newest is `108_tenant_id.sql` |
| NUC `/home/dobby/projects/RegenAI/koi-processor` | **the NUC serves this** | `nuc-runtime` `aa4be290` | no — newest `.sql` is 107, frozen since 2026-07-14 |

Read the middle row twice. What reaches the NUC is whatever branch a session last left the
shared dev checkout on — the same checkout this repo's own `CLAUDE.md` tells you to "assume
moves under you."

The repository metadata directory **does** exist on the NUC and its working tree is clean:
someone committed the rsync'd state on 2026-08-31 with the message *"commit the live
protocol-alignment work before a checkout erases it."* The mechanism that made that metadata
meaningless — its exclusion from every rsync — is still fully in place. Only the symptom was
patched. `FETCH_HEAD` there was last touched 2026-08-05.

### 4a. Determining what a process actually loaded

A plausible directory is not evidence. Ask the running process:

```bash
# NUC (Linux)
systemctl show dobby-koi-processor -p MainPID     # -> 362825
readlink /proc/362825/cwd                          # -> the REAL cwd
ps -o lstart= -p 362825                            # when did it start?
# laptop (macOS; no /proc)
lsof -a -p <PID> -d cwd -Fn | tail -1 | sed 's/^n//'
```

Use `ps -o lstart`, never `ps aux` — the latter prints a bare clock time for a process started
yesterday, which reads as today.

As measured: the NUC process started **2026-08-27 01:11:40**, the newest source file in its tree
is **2026-08-26 13:35:24**, so it is *not* stale relative to its own disk. Note also that the
deploy marker (2026-08-26 21:33:54) *precedes* the process start by 3h38m — `deploy.sh` restarts
before it writes markers, so that restart did not come from `deploy.sh`.

---

## 5. A fourth clone, with its hazard correctly bounded

`~/projects/koi-processor-runtime` — the home of the sensor LaunchAgents — was **9 commits
behind** the serving checkout (`b5a7e8e` vs `fd3f430`).

Those 9 commits touch **two non-documentation files**: `api/entity_schema.py` (commit `4a9e17a`,
which added `Incident`/`Component` to `DEFAULT_SCHEMAS`) and `tests/test_launchd_job_targets.py`,
plus 3 documentation files. The runtime clone's `entity_schema.py` contains **0** occurrences of
`Component` against **5** in the serving checkout, so that change is genuinely not live there.

**But it is inert for every scheduled job.** `DEFAULT_SCHEMAS` is consumed by only
`api/llm_enricher.py`, `api/routers/knowledge_router.py`, `entity_schema.py` itself and one test.
None of the clone's **nine** LaunchAgent jobs reaches any of them, and that clone serves no API
surface.

⚠ **Nine, not six** — corrected 2026-09-04 after an audit caught the wrong denominator. Six name an
in-clone script directly in `ProgramArguments` (`calendar-export`, `embedding-repair`,
`research-author-sensor`, `substack-deep-extract`, `substack-gmail-bridge`, `substack-sensor`).
**Three more reach clone Python through wrappers** in `~/.config/personal-koi/` while setting
`WorkingDirectory` to the clone: `vault-conflict-sweep`, `knowledge-health` and `repo-doc-sensors`
(the last two run `knowledge_health.py` and `doc_scanner.py`, which do real graph writes). This
repo's own `CLAUDE.md` already said "nine jobs execute from its untracked `venv/`"; enumerating by
`ProgramArguments` alone silently skipped three. The inertness result is unchanged — all nine come
back clean — but anyone re-testing "is the clone safe to leave behind?" against six entrypoints
would miss a third of the surface.

State it that way. *"9 commits behind and nothing checks it"* reads as an active hazard and is
not one — that is the *populated-is-not-conformant* shape, an unbounded claim standing in for a
measured one.

⚠ **Caveat, honestly:** only **direct** imports were checked. A transitive chain is not excluded.
If the clone is ever caught up, prove the sensors still run with a canary rather than with this
paragraph.

---

## 6. The migration ledgers

### 6a. Phantom rows — recorded applied, file absent

The NUC's `koi_migrations` records **111, 114, 115** as APPLIED while those `.sql` files exist
nowhere in its serving tree — **and nowhere in the deploy source either**, so no rsync could ever
have delivered them. The DDL is genuinely present (`canon_watch_status` and `resolver_decisions`
tables exist there), so the migrations really ran; the files were hand-carried and not kept.

Two more have the same defect and are easy to miss: **`personal:106_ingest_idempotency`** and
**`personal:107_entity_closure`**. Worse, the NUC simultaneously holds *different* migrations at
those numbers (`106_entity_notion_mappings.sql`, `107_entity_visibility_scope.sql`). **The
numbering space has forked.**

### 6b. The checksum column cannot detect this

`koi_migrations.checksum` is a **hand-typed label** (`v1_type_scaffold`, `v1_walking_skeleton`,
`v1_phase0_guards`), not a content hash. It is identical on both machines for 111/114/115, so it
proves nothing about whether the same SQL ran. What does prove hand-application is the spread of
apply times between the two machines — **+11h32m, +14s, +47m27s** — three different gaps, so
three separate ad-hoc events, not one pipeline.

Contrast `101_entity_merge`, `personal:086_deep_extraction_schema` and the `personal:10x` rows,
which carry real sha256 content hashes. ⚠ Corrected 2026-09-04: `personal:079`–`082` do **not** —
they all carry the hand-typed label `v1_protocol_layer`, exactly the pattern this section argues
against (NUC `personal:08%` = 1 hashed of 4; laptop = 0 of 3). Sharper still, two of the five
phantom rows — `personal:106` and `personal:107` — *do* carry real sha256 hashes, and those hashes
match the **laptop's** copies of the corresponding `.sql` files. The phantom-row problem is
therefore not "no checksum"; it is that a correct checksum can be recorded on a machine where the
file it hashes has never existed.

### 6c. The ledgers cannot be diffed by raw id

The NUC has **three** migration tables where the laptop has two:

| table | NUC rows | laptop |
|---|---|---|
| `koi_migrations` | 22 | 105 |
| `koi_schema_migrations` | 42 | absent |
| `schema_migrations` | 4 | present |

And the two `koi_migrations` ledgers use **incompatible id namespaces** — the laptop uses `core:`
and bare ids, the NUC uses `personal:`. Any comparison must strip the namespace prefix first.
Without normalisation a naive filename-versus-ledger diff on the laptop alone reports **55 + 85**
phantom rows; normalised, the real local state is 34 files with no ledger row and 4 ledger rows
whose numbers (079–082) collide between two lineages. That local mess is pre-existing and out of
scope here — it is the reason any parity check must compare **node to node**, not ledger to files.

---

## 7. The vocabulary divergence that already exists

Strictly one-directional — the NUC is a **proper subset** of the laptop in both tables. Nothing
exists on the NUC that does not exist on the laptop.

| table | laptop | NUC | laptop-only rows |
|---|---|---|---|
| `allowed_entity_types` | 32 | 28 | `Component`, `Document`, `Event`, `Incident` |
| `allowed_predicates` | 56 | 48 | `abstracted_from`, `addresses`, `contains`, `guards`, `implicates`, `invokes`, `reads_config`, `schedules` |
| `allowed_facets` | 0 | 0 | — (see §7a) |

Migration `111_entity_type_registry.sql` seeds **exactly** the NUC's 28 types. So this is not a
missed migration — it is drift *after* migration.

**The 12 extras exist in no migration file anywhere.** `grep -rn "'Component'" migrations/*.sql`
returns no hits; so does a search for `reads_config`/`abstracted_from`/`implicates`. They were
`INSERT`ed directly into the laptop database:

```
Document   2026-08-24 11:52:49
Event      2026-08-24 11:52:49
Incident   2026-09-03 19:58:37
Component  2026-09-03 19:58:37
```

(`allowed_predicates` has no `created_at`, so its 8 cannot be timestamped.)

**Nothing in the entire stack** — not `deploy.sh`, not any timer, not the drift sweep — would
ever move these rows to the NUC or notice they are missing.

### 7a. `allowed_facets` is empty on both sides, and it is NOT inert

There is no *parity* finding here, and that is precisely where the observation is usually dropped.
Record it as a constraint instead:

`entity_facets_registered_guard` is bound to the trigger `tr_entity_facets_registered` on
`entity_registry`'s hot write path, and that trigger is **ENABLED on both nodes** (verified
2026-09-04, as is `tr_layers_only_guard_facts` on `knowledge_facts`). With `allowed_facets`
holding zero rows, **every non-empty facet write is rejected today**, symmetrically, on both
machines.

The table is fail-closed, not unused. Seeding it is step one of any facet-based answer to the
open entity-vocabulary question. Do not read "0 rows on both sides" as "nobody uses this."

---

## 8. Both existing cross-host monitors are saturated alarms

### 8a. `dobby-drift-sweep` — NUC, weekly

`scripts/koi_drift_sweep.py` (in this repo, but executed **only** on the NUC by
`dobby-drift-sweep.timer`, Sundays 06:00). It runs one statement per side —
`SELECT COUNT(*)` over `entity_registry`, `knowledge_facts`, `knowledge_episodes`,
`document_entity_links` — and compares each table's NUC-minus-laptop gap against a hardcoded
`BASELINE_GAPS` snapshot, as a percentage of table size.

Verification that "row-count-only" is literal: `grep -cniE "INSERT|UPDATE |DELETE|CREATE"` → 0,
with `grep -c SELECT` → 2 as the control.

**It has reported DRIFT on every reachable run since 2026-05-24. The last `OK` was
2026-05-17** — three and a half months ago. Full history from the NUC's `drift.log`:

| run | status | max drift |
|---|---|---|
| 2026-05-17 | **OK** | 0.94% |
| 2026-05-24 | DRIFT | 5.57% |
| 2026-05-31 | DRIFT | 15.22% |
| 2026-06-07 → 06-21 | WG_UNREACHABLE | — |
| 2026-06-28 | DRIFT | 40.81% |
| 2026-07-05 → 08-09 | WG_UNREACHABLE (**six consecutive weeks**) | — |
| 2026-08-16 | DRIFT | 56.78% |
| 2026-08-23 | DRIFT | 54.29% |
| 2026-08-30 | DRIFT | 55.77% |

So of the last 15 scheduled runs, **9 could not measure at all and 6 cried DRIFT**. Not one
returned OK. `BASELINE_GAPS`
was snapshotted at the 2026-05-13 reconciliation and has **never been re-taken** — the script's
own comment asks for exactly that, and inbox task **2736** owns it.

This is not a log nobody sees: `~/projects/dobby/scripts/briefing.sh` reads the last line and
renders a "Federation parity" warning into the **Telegram morning brief**. A warning that fires
every time is a warning that has stopped carrying information.

Re-snapshotting the baseline is a judgement about *real* federation divergence and must not be
done casually — it would mark a 55% gap as OK by fiat.

### 8b. `soak-check.sh` — laptop, every 2 hours

A laptop crontab entry runs `scripts/federation/soak-check.sh` every 2 hours.

⚠ **Corrected: this monitor worked for six months and then went blind — it did not "never work."**
An earlier draft said "it has never printed `Status: OK`", sourced from `/tmp/soak-cron.log`
(87 DRIFT / 0 OK). That log only covers the current window. The durable log **jumped checkouts**,
and the two halves are one continuous history:

| copy | entries | OK verdicts | window |
|---|---|---|---|
| `~/projects/RegenAI/koi-processor/docs/soak-results/` | **1,349** | **592** | 2026-02-26T08:56:30Z → 2026-08-25T**03**:00:02Z |
| `~/projects/koi-processor-service/docs/soak-results/` | 115 | **0** | 2026-08-25T**05**:00:04Z → now |

Two hours apart — one cron interval. The breakage therefore has a **date**, 2026-08-25, and a
cause: the crontab `cd`s to `koi-processor-service`, and the durable log path is
`$PROJECT_DIR/docs/soak-results/`. "It never worked" would have hidden both.

⚠ **Corrected 2026-09-04.** An earlier draft of this section said "`?` for every field since
2026-08-26T05:04:46Z — 106 of 114 entries." Both operands were wrong and it collapsed two
distinct phases. `2026-08-26T05:04:46Z` is the **last healthy run**, not the first broken one —
a boundary timestamp used as the first member of the set it actually terminates. Measured over
`docs/soak-results/vault-sync-soak.jsonl` (115 entries, 2026-08-25T05:00:04Z → 2026-09-05T01:00:04Z):

| phase | window | entries | what the alarm meant |
|---|---|---|---|
| **1 — informative** | 2026-08-25T05:00:04Z → **2026-08-26T05:04:46Z** | 8 with a real local reading | DRIFT was **true**: local reconcile drift 13, rising to **1038** |
| **2 — uninformative** | **2026-08-26T07:00:03Z** → now | **107 of 115** blind on the status endpoint (**105** if `reconcile_drift`, a *different* endpoint, must also be `?`) | the local endpoint cannot be read at all |

Phase 2's cause: `VAULT_SYNC_ENABLED=false` makes `GET /koi-net/vault-sync/status` return
`{enabled:false, reason:...}` and the reconcile POST return 400, so none of the keys the script
reads exist and `LOCAL_DRIFT` can never equal `"0"` — the OK branch is unreachable by construction.

**The part worth keeping:** phase 1 was a real signal. A local reconcile drift of **1038** was
reported and then buried under three months of identical output. That is the actual cost of a
saturated alarm — not the noise, but the true reading nobody could distinguish from it.

It exits 0 regardless and appends to an untracked log. Tracked as task
`koi-soak-check-false-drift-alarm`.

### 8c. What neither can see

Both compare **counts**. Neither can detect a value *rewrite* (same row count, different
content — which is exactly the shape of a predicate-casing normalisation), a *swap* (one row
added, one removed), *which* rows differ, the vocabulary tables at all, the migration ledgers,
or trigger/schema state.

---

## 9. Enumeration method notes

Two things in this stack are invisible to the obvious command. Both cost time before being
written down.

- **`koi-backup.timer` is a `--user` timer.** `systemctl list-timers` does not show it;
  `systemctl --user list-timers` is required. It is healthy — last run 2026-09-04 03:39:50,
  2.7 GB dump, verified, `pruned=1 kept=11`. An earlier enumeration reported "28 system + 4 user
  timers" and this one reported it as "invisible to `systemctl list-timers`": same conclusion,
  different method. The method is what matters, so it is stated here explicitly.
- **`koi_drift_sweep.py` reached the NUC by commit, not by `deploy.sh`.** It is absent from the
  deploy source (`~/projects/RegenAI/koi-processor/scripts/`) and present on the NUC only via
  commits on the `nuc-runtime` branch. **Editing it in this repo would not reach the NUC.**

---

## 10. Reading this document later

The counts will be stale. The *shapes* are what to carry forward:

1. The deploy source is not the serving source, on either machine.
2. Schema is hand-applied on the NUC, and the ledger can record a migration whose file exists
   nowhere.
3. The vocabulary tables drift by direct `INSERT`, leaving no file, no ledger row, and no
   propagation path.
4. Every automated check that exists compares counts, and both have been crying wolf for months.
5. An empty table is not necessarily an unused one — check for a trigger reading it.
