# Project handoff

**Updated:** 2026-09-12 22:55 PDT
**Session:** Claude Code · a8751c7e · Stream B wrap: biofi ingest, off-host encrypted backups, command centre, migration 123 reviewed + applied
**Status:** `regen-prod` @ `e1cfac8`, **pushed, in sync with origin** (`git ls-remote` verified); migration 123 **APPLIED and live** (`claims=513, snapshot rows=511`, one-shot); every database and the source archive now have **checksum-verified, encrypted off-host copies on gaia** with recovery proven on the NUC; Stream A steps 0–3 done, 4–5 not started; tree clean except two other sessions' debris files.

> **Read this before re-opening the topology doc.** That one paragraph was rewritten **six times on
> 2026-09-04** by two sessions, producing ~a dozen false claims, every one the same shape: *a probe
> answering a narrower question than the sentence built on it.* Two of my corrections were
> themselves false. Do not "improve" `docs/operations/two-node-topology.md` from memory or from a
> sibling file — re-run the reproduce commands it now carries. Proposed rule for the 09-06 cycle,
> with the full evidence, is koi task `koi-reproduce-command-rule-for-state-claims`.

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

**Session `a8751c7e`, 2026-09-11→12 (Stream B).** Began as "ingest biofi.earth"; became the
backup/recovery arc when the archive built for it exposed that every backup lived on the disk it
was protecting. Three peer sessions coordinated by mailbox all day; ~100 agents across four
adversarial workflows.

- **biofi.earth ingested** via a new git-versioned website sensor (`koi-processor-runtime`, branch
  `website-sensor-2026-09-12`): 83 discovered → 69 ingested; live 65 docs / 226 chunks / 1,189 facts /
  705 entities / 169 discourse moves / 84 claims, 0 null embeds. Sensor now scheduled **weekly**
  (Sundays 06:40) — it had never been scheduled at all.
- **Every database off-host, encrypted.** `scripts/koi_offsite_copy.sh` (gpg `-z 0`, three guards
  that can each fail: `file -b` PGP, `pg_restore` REJECTS it, recipient key named) →
  `gaia:koi-offsite/`; `koi_backup_all.sh` drives `koi_backup.sh` per database (personal_koi + the
  four that had **no backup of any kind**); `koi_backup_check.sh` (launchd 12:00) reads the markers
  nothing read before and flags a START with no OK/FAIL — the 2026-09-11 signature. Measured
  transfer ~7 MB/s sustained (a 300 MB probe said 11); a restore is **index-bound** (one hnsw index
  on 66k×3072 ran 57 min; data layer proven complete first, 100% embedding coverage). Runbook:
  `docs/backup-and-recovery.md`.
- **Source archive** (`~/Documents/koi-source-archive`, README says *never pushed* — a content rule,
  not a durability one): `koi_archive_offsite.sh` ships an encrypted `git bundle` (launchd 08:30,
  skips when the REMOTE still holds HEAD). Key `F5EE933A8DC407E4` on laptop + NUC + login Keychain
  (`security -w` returns **hex**; `xxd -r -p`). **Recovery drilled on the NUC with the laptop assumed
  gone** — tree `f3fb091d…` byte-identical, with a virgin-keyring negative control. That drill found
  the arrangement was unrecoverable: gaia authorised only the laptop's SSH key; NUC's key added.
- **Migration 123 reviewed** (29 agents, 21 raised / 14 refuted / 0 above low) and cleared; three
  vacuous closing assertions and the rollback file's commented-out export found, all fixed by
  `045186e8` before apply. `koinet` federation checked: zero `document:` RIDs and zero doclinks have
  ever left this node, so supersession creates no FORGET obligation today.
- **Command centre** at `http://127.0.0.1:5051/#dash` (darren-workflow `0d8e37e`): KOI network graph
  (6 nodes/10 edges, hand-drawn SVG — topology is hub-and-spoke), system health, tasks + incidents.
  Immediately found 7 launchd jobs failing quietly and a peer (`octo-salish-sea`) sharing documents
  with no koi-net edge.
- `koi-processor-service` `regen-prod`: 7 commits (`3f2c3c3`…`680432d` + this wrap), all pushed.

### What this session got wrong, and how it was caught

~14 defects where **the instrument's name implied a property it did not measure**: `file` matching
the *filename*, `plutil -lint` accepting malformed XML, `pkill -f` matching the test harness, `pages
free` on macOS, a shadowed `stat`, suites printing `fail=3` and exiting 0, `grep -c || echo 0`
emitting two lines, a NUL byte git stored as binary, `sysctl` absent from the launchd PATH, a stale
load figure relayed as current, a process *named* after a file taken as evidence of who owned it.
Memory: `feedback_instrument_name_implies_property_it_does_not_measure.md`. Rule kept: **break by
deletion, not mutation** — every vacuous control mutated something that landed somewhere inert.
Also: added a `nuc` remote to a repo whose README said never-pushed (caught by `045186e8`, reversed
before any ref landed); relayed an operator delegation as authorisation (correctly refused).

### Also this cycle — **Session `045186e8`, 2026-09-11→12.** Designed and began executing
`~/.claude/plans/history-preserving-website-sensor.md` (Stream A). ~130 agents across seven
workflows (understand · design bakeoff · spec · two gates · merge · resolve), three Codex review
rounds, and a 29-agent peer review of the migration before it was applied.

- **Migration 123 applied** (`da408e0`, 22:19 PDT): `document_source_claim` (ownership + sticky
  `history_policy`, PK site+URL), `document_supersession` (append-only version chain + crash-recovery
  journal, no FK), one view, one nullable column on `session_discourse_moves`. Backfilled every
  document row into claims with **zero lost**; assertion demonstrated *failing* on an injected defect
  before apply (delete one claim → exit 3, caught three ways). Leg D adopted the two real legacy
  supersessions as `reason='legacy'` edges — observed, not performed. All nine indexes confirmed via
  `pg_indexes`, the instrument the file's own `to_regclass` checks cannot substitute for.
- **Down file made real.** Its export lines were inside comments; a reviewer ran it and destroyed
  511 rows at exit 0. Now runs, placed **below** `ON_ERROR_STOP` (measured: above it a failed export
  exits 0 and continues to the DROPs; below it exits 3 first), with literal paths because `\copy`
  does not interpolate `:'var'` in a filename (my first repair was broken by exactly that; my own
  test caught it).
- **`koi-dump-ok`** — full-read dump validator. Exists because `pg_restore --list` exits 0 with 1198
  entries on a 4.25 GB truncated dump. Controls demonstrated: partial → exit 1 in 29 s; good → exit 0.
- **`koi-history`** dispatcher — seven verbs, each tagged with the step that lands it; an unbuilt verb
  exits 2, never 0.
- **Postgres instruments on** (`log_lock_waits`, `log_min_duration_statement=10s`, `log_checkpoints`,
  `log_autovacuum_min_duration=1s`) — the 6.5 h `pg_dump` of 09-11 was undiagnosable because all four
  were off. Its kill was a human-confirmed restart at 09:44; its slowness stays undetermined.
- **Backup guard restored**: the partial dump was the newest by mtime and had halved `koi_backup.sh`'s
  size floor; renamed out of the glob (kept as `koi-dump-ok`'s negative-control fixture).
- **Three plan-level corrections that changed the work**, all from peers and all verified before
  acting: the d8 "shared sha256" evidence was `sha256("\n")` from empty extractions; `claims` has no
  `document_rid` so claims are adjudicated document-scoped (P-22); extraction composition is
  asymmetric on byte-identical input (n=5: 0.391/0.192/0.037/0.048/0.050), so per-version fact
  provenance is a parked item with a stated cost, not a blind spot.

### What this session got wrong, and how it was caught

- **Two negative controls that could not fail**, one of them the day the principle went into the
  plan twice. Rule kept: *break by deletion, not by mutation* — a mutation can land somewhere inert.
- **Two stalls in my own machine gate** within an hour: `pages free` (macOS keeps it near zero) and
  `pgrep zoom.us` (resident for over a day). Replaced with swap growth and `aomhost`. *A process
  being resident is not a claim about the resource.*
- **Two relayed numbers asserted in my own voice**: load "21" (was 174 — a live Zoom call) and a
  "28 GB" dump (12.5 GB; `ls` was one command away). And once, a phrase I had written six hours
  earlier, denied from memory. The tell is the asymmetry: the claim I doubted got verified, its
  neighbour did not.
- **Exit codes swallowed by pipes four times**, twice inside checks whose subject was exit-code honesty.

## Next steps

1. **Step 4 — `scripts/ingest_document.py`** (fresh session). Canonical spec:
   `~/.claude/plans/history-preserving-website-sensor-artifacts/q1_fixed_pair.md` (**supersedes**
   `ingest_side_pair.md`). Four edits: the `superseded_at` CASE in the `ON CONFLICT` arm (staged
   re-ingest of an already-hidden row keeps its stamp; everything else reveals); `--staged` on the
   INSERT path only; the flat `history_*` source_meta keys; the ingest-side pair (journal
   `successor_rid` PATCH with **four** conjuncts incl. the claim triple, + claim cutover) inside
   `_write_document`'s existing transaction; the symmetric flag guard. Fixtures on a **scratch DB**.
2. **Step 5 — `api/routers/history_router.py`** + the apply transaction (`apply_transaction.md`,
   `endpoint_contracts.md`, `state_machine.md`). Degraded mode is **503 `SCHEMA_ABSENT`** on
   mutating routes, never a 200 a client can mistake for success. `POST /history/read-filter` is its
   own route. Then **CHECKPOINT** — task `koi-2026-09-13-045186e8-step5-checkpoint`, due 09-14.
   Contract: demonstrate the store's behaviour when the storage write **succeeds but the bytes are
   wrong** (a8751c7e's T2 corrupt-remote-preserving-size-and-mtime is the specimen).
3. ~~Push~~ **Done** — `e1cfac8` in sync with origin, `git ls-remote` verified 22:52.
3b. **Stream B follow-ups** are koi tasks with due dates (all in the Upcoming view at :5051): review the 8 failing launchd jobs (09-15); `/repair` the 46 incident stubs (09-16); `104add19` close-out — score endpoint E, NC1, regression cohort (09-16); bring `koi-processor-runtime` forward, 20 behind, cp-only per plan step 14 (09-19).
4. Settled *during* steps 4–5, named so none is lost: `intended_tier` on `/history/intent` (blocks
   AC21 only); the six `koi-history` subcommands, each with its step; the soak baseline's persistent
   home (step 17); `/history/resolve`'s contract amendment.

## Open questions

- **Nothing blocks steps 4–5.** The operator authorised the build directly ("Yes, but hold step 3
  longer", then "Yes — apply migration 123"); both are on record in this session and in
  `a8751c7e`'s independent relay.
- `history_policy='drop'` stays **disabled in v1** (decision D-D) until its fixture and archive-commit
  step exist. Not a question — recorded so nobody enables it.
- Two documents superseded at the identical instant under one triple could produce a malformed chain
  (a reviewer's unverified concern; unreachable in tonight's data). Parking-lot territory.

## Verification and working tree

- Branch/status: `regen-prod` @ `e1cfac8`, **in sync with `origin/regen-prod`** (pushed 22:52, verified by
  `git ls-remote`). Tree clean; `p.txt` ("hi") and an empty `:` are other sessions' debris, left alone.
- Backups: `koi_backup_check.sh` → **all fresh** (5 databases + archive off-host, encrypted, on gaia).
  Off-host is checksum-verified, **not restore-verified** — gaia has no `pg_restore`; a real restore
  of the 09-12 dump was done locally (data layer complete, embeddings 100%).
- `git diff --check`: clean. No canon validator in this repo (not applicable).
- Migration 123: live. Post-apply checks that can actually fail: unclaimed live rows = 0 of 509;
  live rows without a live claim = 0; nine indexes present in `pg_indexes`. Three fixtures intact
  (`656d1923` live/8 chunks, `c3b0ebcd` superseded/0, `58fe10e0` superseded/0) — protected by koi
  task `koi-2026-09-12-protected-versioning-fixtures`, **DO NOT MODIFY**.
- Fresh dump `~/koi-backups/personal_koi-step3-20260912-201553.dump` (12,562,904,620 B), full-read
  verified 175 s. Rollback: `migrations/123_document_history_down.sql` (export runs first).
- **Machine gate for heavy work** (in the plan's Rollback section): `pgrep -x aomhost` absent · 1m
  load below 15m · swap flat over 20 s · no competing `pg_dump` · zero active backends. **Never**
  `pgrep zoom.us` or `pages free` — both are unsatisfiable stalls. Re-measure at the moment of the
  run; trust no figure in any message.
- `stat` on this machine is a shadowed `/usr/local/bin` binary that SIGKILLs (rc 137) on every path
  — use `/usr/bin/stat`. Memory: `reference_stat_binary_shadowed_and_broken.md`.

## Recent sessions

| Date | Provider | Session | Summary |
|---|---|---|---|
| 2026-09-11/12 | Claude Code | `a8751c7e` (stream B) | **biofi.earth ingested + the backup arc.** New git-versioned website sensor (runtime branch `website-sensor-2026-09-12`, scheduled weekly). Every database and the source archive now **encrypted off-host on gaia**, checksum-verified, with recovery **drilled on the NUC** — which found the arrangement unrecoverable (gaia authorised one SSH key) and fixed it. Restore measured index-bound (57 min for one hnsw index; data proven complete first). `koi_backup_check.sh` now reads the markers nothing read. Migration 123 adversarially reviewed (29 agents) and cleared; federation checked (zero `document:` RIDs ever left this node). Command centre at :5051 `#dash`. ~14 instrument-name defects in my own work, all found by running things; memory written. 7 commits here, all pushed; `e1cfac8` in sync. |
| 2026-09-07 | Claude Code | `217282eb` (cross-stream, started in darren-workflow) | **This repo owns upcoming work but was NOT modified.** New plan `~/.claude/plans/koi-web-ingest-integrity.md` targets it: `POST /web/preview` returns **zero bytes after 60s for `https://example.com`** (service otherwise healthy — `/health` 200, peers polling), and `/web/ingest` returns `"status": "ingested"` for calls that persist nothing (0 log rows, 0 chunks, vs a positive control of 1,619 rows). Plan defines an `ingested`/`not_persisted`/`skipped` enum enforced server-side here, plus a `web_ingest_jobs` async table. Laptop-first migration order. Nothing executed. |
| 2026-08-26 | Claude Code | b289ac1e (resumes) | 3rd conflict wave cleaned; built `com.personal-koi.vault-conflict-sweep`; fixed a real `ThrottleInterval < StartInterval` bug + added a mutation-tested anti-storm pin; fixed `test_koi_flow_integration.py`'s months-stale collection failure. |
| 2026-08-26 | Claude Code | c1defaa8 | **Verification pass.** Found A7 unexecutable, a gating inconsistency, A3's live blast radius; caught the conflict cleanup incomplete at 101 files; named the iCloud root cause; canary-proved the sweep fires unattended. No code changes. |
| 2026-09-01/02 | Claude Code | 72cf052b | **Phase 0/1 hardening.** Backup + verified restore; merge reversibility (`unmerge`, used on 57 merges); `entity_non_match` seeded (44) and enforcing at 6 tiers; credential + persona guards; `:8351` LAN hole closed and A/B-verified; type-mismatch void closed. 12 commits. |
| 2026-09-04 | Claude Code | a0f88bbf | **MCP supply chain + the two-node written statement.** axios lockfile committed (30 advisories cleared, not yet live); 39 Dependabot alerts dismissed; `docs/operations/two-node-topology.md` written; launchd guard widened to every installed plist after a 4th subset-enumeration instance, exposing 3 malformed plists and a namespace (`com.darrenzal.*`) no glob ever matched. 6 commits published, 10 tasks filed. A 34-agent audit + the parallel session overturned **6 of my own claims**, two of them corrections I had just made. |
| 2026-09-04 | Claude Code | 1e1f2abb | **Decisions 9315/9317 prepared; email guard completed; 116 cleared.** 20 agents over two workflows, every lens returned CORRECTED. Killed the 27.9× ratio (it is ~8×), the 176 population (166), the 142/57 reversibility split (0%, not 71%), the one-insert-path premise (two live writers), and "the NUC is unreachable" (it is reachable, and already holds the divergent vocabulary without failing). Corrected my own false report that 116 was blocked — I read the layout instead of asking the process. 2 koi-sensors commits, both positive-controlled. |
| 2026-09-03 | Claude Code | e1dd0df8 | **Backup armed; retype made reversible.** The nightly backup plist had never been bootstrapped — newest dump was Aug 31, hand-run, ~3 days unbacked on 27 GB. `/entities/retype` captured no reversal and had already made **142 irreversible merges**. Launchd guard enumerated a subset (missed `com.darren.*`, found 2 real violations). `restart.sh` reported ERROR on restarts that succeeded (30s budget vs 40–73s startup). Retracted the false D4b claim before it shipped. 5 commits, all positive-controlled. |
| 2026-09-03/04 | Claude Code | e1dd0df8 | **Vocabulary arc.** Backup had never been bootstrapped — now armed and proven unattended. `/entities/retype` made reversible after 142 irreversible merges. Launchd glob widened (2 violations). `restart.sh` false-ERROR fixed. E3 shipped, tripwire restored. 23 commits published; flood fix live after a second pull. **Five of my own claims overturned by measurement and corrected at every site.** |
| 2026-09-12 | Claude Code | `045186e8` (stream A: history-preserving website sensor) | **Steps 0–3 of `~/.claude/plans/history-preserving-website-sensor.md` DONE; steps 4–5 NOT STARTED.** Committed `da408e0`: migration 123 **APPLIED** (`assertion PASSED (claims=513, snapshot rows=511)`, one-shot — do not re-run, it exits 3 with an alarming-but-correct message once any document is ingested), its down file (export now runs, verified exit 3 on failure, placed below `ON_ERROR_STOP` deliberately), `koi-history` dispatcher (7 verbs, none built yet; unbuilt → exit 2), `koi-dump-ok` (full-read validator, both controls demonstrated). Fresh dump `personal_koi-step3-20260912-201553.dump` full-read verified. Postgres instruments on (`log_lock_waits`, `log_min_duration_statement=10s`, `log_checkpoints`, `log_autovacuum_min_duration=1s`). **Nothing in `scripts/ingest_document.py` or `api/routers/` is modified — a peer saw another session's `ingest_document.py` process and inferred step 4 was in flight; it was not.** Untracked `p.txt`/`:` are not mine. **NEXT (fresh session):** step 4 = `ingest_document.py` (see `~/.claude/plans/history-preserving-website-sensor-artifacts/q1_fixed_pair.md` — CANONICAL over `ingest_side_pair.md`); step 5 = `api/routers/history_router.py` + apply txn (`apply_transaction.md`, `endpoint_contracts.md`, `state_machine.md`). **Checkpoint after step 5, contract:** demonstrate the store's behaviour when the storage write SUCCEEDS but the bytes are WRONG, not only when it fails (a8751c7e's ask; their T2 corrupt-remote-preserving-size-and-mtime test is the specimen). Three protected fixtures: `document:656d1923…` live/8 chunks, `document:c3b0ebcd…` superseded/0, `document:58fe10e0…` superseded/0 — koi task `koi-2026-09-12-protected-versioning-fixtures`, DO NOT MODIFY. Task `koi-2026-09-13-045186e8-step5-checkpoint` due 2026-09-14. Plan is lint-green at ~740 lines with 22 parking-lot items; the machine gate for heavy work is in its Rollback section (gate on `aomhost` + swap growth, never `pgrep zoom.us` or `pages free` — both were unsatisfiable stalls). |
| 2026-09-11 | Claude Code | `919b51a0` (cross-stream, started in rage-research) | `restart.sh` cycled the service cleanly but did not clear the embedding-repair backoff, because `/health` reads the repair job's persisted state file (`EMBED_REPAIR_STATE`), not process memory; the fix is the repair job's own `--ignore-backoff`. Record in the rage-research handoff. |
