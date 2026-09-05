# Project handoff

**Updated:** 2026-09-04 16:10 PDT
**Session:** Claude Code · e1dd0df8-3f9d-4a99-8425-1502f553264c · vocabulary arc — backup armed, retype made reversible, five self-corrections
**Status:** `regen-prod` @ `dcda7f7`, **published, 0 ahead / 0 behind**, tree clean, 120 passed / 4 skipped. Two sessions worked this repo today (`e1dd0df8` and `1e1f2abb`); both are wrapped and everything is on origin. Nothing is half-applied.

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

- **The nightly backup had never been bootstrapped.** Plist written 2026-09-01, never loaded; newest dump was Aug 31 and hand-run — ~3 days unbacked on 27 GB. Now armed and proven **both ways**: a forced run (12.25 GB, `pg_restore --list` rc=0 over 1,181 TOC entries) and the first unattended `StartCalendarInterval` fire (03:15:04 → 04:09:06, 12.32 GB, verified, kept 3). ⚠ Retention **pruning** is still unexercised.
- **`/entities/retype` had already made 142 irreversible merges** (`d06b038`). `capture_reversal` had exactly one call site — the `/merge` route — while `/retype` used the same `_do_merge` helper. Capture now lives in `_do_retype`, because three of its four branches do something `_do_merge` cannot see (in-place does no merge; **mint creates the survivor**; resurrect revives a tombstone). Undoing a mint previously left two live rows sharing a `normalized_text`, both embedded.
- **The launchd guard enumerated a subset** (`4941b1b`, `f3e46b3`) — `com.darren.*` was invisible; widening it found two real violations, one reached only through a launched script.
- **`restart.sh` reported ERROR on restarts that had succeeded** (`606e4aa`). 30 iterations of `curl --max-time 4; sleep 1` collapses to ~30s wall clock against a refused port, while startup under `pg_dump` takes 40–73s. Now a wall-clock deadline that distinguishes *failed to start* / *crash loop* / *alive but slow*.
- **E3 shipped** (`4a9e17a`) — `Incident`/`Component` added to `DEFAULT_SCHEMAS`. The point was the tripwire: `test_canonical_entity_types` is the **only** mechanism that would catch a mistaken type removal under 9315, and it was red on exactly those two. Proven by deleting `Protocol` and watching it fire.
- **Deploy chain executed**: 23 commits published, runtime clone pulled **twice** (the first pull deployed nothing — see Next steps #2 history), flood fix live, canary verified on log mtime + state file.
- **Five of my own claims were overturned by measurement** and are corrected at every site — see *Corrections* below. That is the session's most reusable output.

## Corrections made to this project's own record

| Claim | The probe that produced it | Truth |
|---|---|---|
| retype gap "never exercised" | `merged_by ILIKE '%retype%'` → 6 | `rewired ? 'retype'` → **142** |
| the fix is live | route present in `/openapi.json` | process predated the module by 85s |
| "nothing normalises today" | `predicate_raw` 0 rows differ | **satisfiable by construction** — one INSERT binds both columns to the same expression |
| "exactly one insert path" | literal grep → 1 hit | f-string hides a **72% majority writer** |
| "pull deploys the flood fix" | 40-behind count | the commit was unpublished; the 40-vs-53 gap *was* the answer |

All five share a shape: a plausible probe answering a different question than the one asked.

## Next steps

Ordered **by kind, not by number** — #1 is a decision, the rest are execution. Running them in numeric order front-loads the one item that cannot be done by working harder.

1. **MCP supply chain — EXECUTED 2026-09-04 (session `a0f88bbf`); one operator step left.**
   Lockfile committed as `409fe9d` on `personal-koi-mcp` main (unpushed): `npm update axios
   --package-lock-only`. **Six** version changes, not the three first reported — axios
   **1.20.0**, form-data 4.0.4→**4.0.6** (HIGH), follow-redirects 1.15.11→**1.16.0**,
   **`proxy-from-env` 1.1.0→2.1.0 (SEMVER MAJOR)**, hasown 2.0.2→2.0.4, and a root-version
   stale-lock resync. `package.json` untouched; +6 entries, 0 removals. The major is
   **unavoidable** — axios declares `^2.1.0` at both 1.18.0 and 1.20.0 — and resolves no advisory,
   so it is a carried cost that should be named rather than omitted (its 2.0.0 moved URL parsing
   from `url.parse` to WHATWG `URL`; public API unchanged). Before/after `npm audit` set diff:
   **30 advisories resolved, 0 introduced, 11 → 8 vulnerable packages**.
   **⛔ NOT LIVE** — `node_modules` still holds 1.12.2. Needs `npm install` + a restart of every
   running `dist/index.js`. Enumerate with `~/.config/personal-koi/enumerate-mcp-processes.sh`;
   the count is **volatile** (8→9→10→7→9 within an hour), so never carry a number.
   **Four figures in the original text were wrong:** 11 processes (it varies, 7 at last count);
   "26 advisory ranges" was the koi-processor *alert* count, a different population (npm audit
   says 28 for axios); the poll-URL fetch carries **no credentials** (bare global axios, no
   interceptors — `open(authUrl)` above it is the sharper hole); and **1.18.0 is not reachable
   by any lockfile-only operation** — `npm install axios@1.18.0` rewrites `^1.7.7` → `^1.18.0`.
   **It was never an axios problem:** 11 vulnerable packages, 5 high. The headline is
   `@modelcontextprotocol/sdk@1.20.0` — GHSA-345p-7cg4-v4c7, **cross-client data leak via shared
   transport reuse**, with instances live across two different agent harnesses. Task 9399.
   **The pm2 surface is now measured** (task 9400): `/opt/projects/koi-processor` has no manifest
   and no `node_modules`; bun resolves *up* to `/opt/projects/node_modules` (installed
   2025-10-16) holding express 5.1.0, body-parser 2.2.0, qs 6.14.0 and **path-to-regexp 8.3.0
   (HIGH)** — which nobody had named. axios is absent there. Dependabot can never see it: the
   manifest is outside the repo. The old "resolved in the repo lockfile" derivation was **wrong**
   (it read `~/node_modules`); prod matched by coincidence, which is what would have hidden it.
   **All 39 Dependabot alerts dismissed** `not_used` (task 9323 done) — needed no
   `security_events` scope, and batch PATCHes silently no-op unless `gh` gets `</dev/null`.
   Also filed: 9401 (graph_tool URL allowlist), 9402 (soak-check false alarm).
2. **The two-node problem — written statement DONE; the monitor is gated on you.**
   `docs/operations/two-node-topology.md` (unpushed) is the measured statement of what flows to
   the NUC and what does not, linked from the `DEPLOY TOPOLOGY` block.
   **⚠ The headline finding, added after audit: `deploy.sh`'s koi-processor rsync leg is not
   running at all.** Its blast-radius guard measures **158 deletions against a `KOI_DELETE_LIMIT`
   of 5** and exits 1 before any rsync; the documented workaround, `SKIP_KOI_SYNC=1`, skips that
   leg entirely while still running the dobby + personal-koi-mcp rsyncs and both `systemctl
   restart`s. So koi-processor code does **not** reach the NUC by `deploy.sh` today — which is why
   its tree is frozen at migration 107 / 2026-07-14 and why someone hand-committed its live state
   on 2026-08-31. Any plan assuming `deploy.sh` will carry a fix there is wrong twice over.
   **New findings it carries:** the phantom ledger rows are **five, not three** —
   `personal:106_ingest_idempotency` and `personal:107_entity_closure` have the same defect, and
   the NUC holds *different* migrations at those numbers, so **the numbering space has forked**.
   The two ledgers use **incompatible id namespaces** (`core:`/bare vs `personal:`) and cannot be
   diffed by raw id; the NUC has three migration tables to the laptop's two. `checksum` is a
   hand-typed label, not a content hash — what proves hand-application is the apply-time spread
   between machines (**+11h32m, +14s, +47m27s** = three separate events). The 12 laptop-only
   vocabulary rows exist in **no migration file anywhere**; 111 seeds exactly the NUC's 28, so
   this is drift *after* migration. **`allowed_facets` is empty on both sides but NOT inert** —
   `tr_entity_facets_registered` is ENABLED on both nodes, so every non-empty facet write is
   rejected today, symmetrically; that is a hard precondition on 9315's facet option.
   **Both cross-host monitors are saturated alarms:** `dobby-drift-sweep` has reported DRIFT on
   every reachable run since **2026-05-24** — last OK 2026-05-17 — at 40.81/56.78/54.29/55.77%
   against a baseline last taken 2026-05-13, straight into the **Telegram morning brief**, and was
   `WG_UNREACHABLE` for six consecutive weeks (of its last 15 runs: 9 could not measure, 6 cried
   DRIFT, 0 OK); `soak-check.sh` has **never** printed OK (87 DRIFT / 0 OK), with **105 of 115**
   runs unable to read its local half since 2026-08-26T07:00 — though its first 8 runs reported a
   *real* drift of 13→1038, now buried (task 9402). Re-snapshotting `BASELINE_GAPS` is
   deliberately **not** done — inbox task 2736 owns it, and it would mark a real 55% gap OK by fiat.
   **⛔ STILL GATED — the parity monitor was designed, not built.** It installs a recurring
   launchd job that SSHes to another host every 6h and writes tasks; design is in the approved
   plan (`~/.claude/plans/start-in-projects-koi-processor-service-curried-flute.md`), including
   a one-task-ever cap, an acknowledged-divergence baseline whose *staleness* is itself an alarm,
   `UNREACHABLE` ≠ `DIVERGENT`, and landing the runtime-clone catch-up as its own canary-verified
   step rather than as a side effect of shipping the monitor.
   **Correctly bounded, not a hazard:** the runtime clone is 9 commits behind, but they touch two
   non-doc files and `DEFAULT_SCHEMAS` is imported by no sensor entrypoint (6 verified across 72
   plists), so it is inert for every scheduled job. Direct imports only were checked.
3. **The vocabulary decision — needs the operator, rested, with a cold facilitator.** 9315 + 9317, gating migration 113. Both E1 blockers are now answered (federation mirrors verbatim, no normalisation anywhere on the apply path; NUC migration is separate and manual). ⚠ **Neither `e1dd0df8` nor `1e1f2abb` should facilitate this** — both authored the evidence pack (`~/.claude/plans/koi-vocabulary-decisions-9315-9317-2026-09-04.md`, also attached to the `context` of tasks 9315/9317 — that pack and those task contexts, not this file, carry the corrected numbers), and a session that framed the options cannot adversarially test its own framing. It wants a session reading the pack cold. Not urgent: due 2026-09-17 and the divergence grows at single digits/day.
4. **Residue.** The 45-row retype (~22–29% precision; 3 need merges and `/retype` never calls `persona_merge_hazard`), and the `incident-enrich` / `walk.py` asymmetry — the bug is not unbounded growth, it is that **one producer has a clock and no allowlist while its sibling has neither**.

## Open questions

- **Are `Protocol`/`Project` TYPES or FACETS?** Gates migration 113's shape. Task 9315. `allowed_facets` exists with an FK and a shape CHECK — zero rows, and **zero *application* readers but ONE live database reader**: `entity_facets_registered_guard` is bound to the **enabled** trigger `tr_entity_facets_registered` on `entity_registry`'s hot write path, and with the table empty it rejects every non-empty facet write today. "Zero users" was refuted 2026-09-04; seeding is step one of any facet answer.
- **What IS the predicate vocabulary?** Task 9317. Only **36 of 4,938** case-folded predicates are in the 56-row table, so a casing rewrite touches ~465 collision rows and leaves ~4,900 unlisted predicates exactly where they are. Casing is a small slice of the question, not the question.
- **The 4 fold-collisions are two relations, not two spellings** — they split by *producer*: uppercase is LLM prose extraction (Apr–Aug), lowercase is `walk.py`/`adjudicate.py` at confidence 1.0 (last 36h). Recommended: rename the structural side (`contains_file`, `invokes_script`). ⚠ Do **not** rely on `predicate_raw IS NULL` to un-fold them later — it separates these 465 perfectly today only by coincidence of producer timing; 2,222 uppercase rows elsewhere carry it.
- **Migration 113 was never written and its gate has been open ~10 days.** Two blockers beyond the decision: a committed architecture doc argues enforcement should *not* be a hard constraint (overridable — its own number is wrong — but explicitly), and `personal_koi_test.allowed_entity_types` has **0 rows** with migration 111 missing there, so an FK would refuse every entity insert in the suite.
- **9313's title says `DECIDED` while its context says "this is an operator call."** One of them is stale; other sessions read both.
- **The unattended-git guard has now false-positived five times** on the word appearing in prose (it refused a read-only `ls .git/hooks`, a heredoc running no git command, and an `echo` label). Deliberately **not** patched this session — the ≥48h soak rule forbids same-session self-modification from a lesson learned in that session; it belongs in the 09-06 repair cycle as a Pattern card.
- Carried unchanged from 2026-09-02: the Organization→Person natural experiment does not work; 17 do-not-merge rows remain unseedable; `KOI_CLAIMS_SERVICE_TOKEN` is populated.

## Verification and working tree

- **Branch/status:** `regen-prod` @ `dcda7f7`, working tree clean, **0 ahead / 0 behind**, `git diff --check` clean.
- **Tests:** **120 passed / 4 skipped** across the touched suites. `test_canonical_entity_types` is now GREEN (E3); it was red all session by design.
- **Positive controls were run, not merely written.** Disabling the minted-survivor delete fails the retype mint round trip while twin and in-place still pass; emptying `KNOWN_DEV_CHECKOUT_EXCEPTIONS` fails exactly the two known launchd violations; appending a line to `scripts/restart.sh` fails the drift test; deleting `Protocol` from `DEFAULT_SCHEMAS` fires the vocabulary tripwire.
- **Live:** service healthy, embeddings available, backup armed with `runs` incrementing on schedule.
- **Canon validator:** not applicable — no `scripts/validate_spec_dag.py` in this repo.
- **⚠ Route presence does not prove module vintage.** Compare `ps -o lstart` against the **mtime** of the newest loaded source file — mtime, not commit time; Python loads from disk. Use `launchctl list` or `lsof -ti :8351 -sTCP:LISTEN`; plain `lsof -ti :8351 | head -1` returned a transient *client* PID this session.
- **⚠ Before deploying any commit, check it is reachable from where you deploy FROM:** `git merge-base --is-ancestor <sha> origin/<branch>`. A pull cannot deliver an unpublished commit — this cost a wasted deploy and survived four review rounds.

## Recent sessions

| Date | Provider | Session | Summary |
|---|---|---|---|
| 2026-08-24/25 | Claude Code | b289ac1e | Executed the full prior plan: 9 sweep fixes, Meeting-notes repair (270 notes), migration 112 complete (421→0). Established `koi-sensors-runtime` + hardened the launchd guard. |
| 2026-08-26 | Claude Code | b289ac1e (resumes) | 3rd conflict wave cleaned; built `com.personal-koi.vault-conflict-sweep`; fixed a real `ThrottleInterval < StartInterval` bug + added a mutation-tested anti-storm pin; fixed `test_koi_flow_integration.py`'s months-stale collection failure. |
| 2026-08-26 | Claude Code | c1defaa8 | **Verification pass.** Found A7 unexecutable, a gating inconsistency, A3's live blast radius; caught the conflict cleanup incomplete at 101 files; named the iCloud root cause; canary-proved the sweep fires unattended. No code changes. |
| 2026-09-01/02 | Claude Code | 72cf052b | **Phase 0/1 hardening.** Backup + verified restore; merge reversibility (`unmerge`, used on 57 merges); `entity_non_match` seeded (44) and enforcing at 6 tiers; credential + persona guards; `:8351` LAN hole closed and A/B-verified; type-mismatch void closed. 12 commits. |
| 2026-09-04 | Claude Code | 1e1f2abb | **Decisions 9315/9317 prepared; email guard completed; 116 cleared.** 20 agents over two workflows, every lens returned CORRECTED. Killed the 27.9× ratio (it is ~8×), the 176 population (166), the 142/57 reversibility split (0%, not 71%), the one-insert-path premise (two live writers), and "the NUC is unreachable" (it is reachable, and already holds the divergent vocabulary without failing). Corrected my own false report that 116 was blocked — I read the layout instead of asking the process. 2 koi-sensors commits, both positive-controlled. |
| 2026-09-03 | Claude Code | e1dd0df8 | **Backup armed; retype made reversible.** The nightly backup plist had never been bootstrapped — newest dump was Aug 31, hand-run, ~3 days unbacked on 27 GB. `/entities/retype` captured no reversal and had already made **142 irreversible merges**. Launchd guard enumerated a subset (missed `com.darren.*`, found 2 real violations). `restart.sh` reported ERROR on restarts that succeeded (30s budget vs 40–73s startup). Retracted the false D4b claim before it shipped. 5 commits, all positive-controlled. |
| 2026-09-03/04 | Claude Code | e1dd0df8 | **Vocabulary arc.** Backup had never been bootstrapped — now armed and proven unattended. `/entities/retype` made reversible after 142 irreversible merges. Launchd glob widened (2 violations). `restart.sh` false-ERROR fixed. E3 shipped, tripwire restored. 23 commits published; flood fix live after a second pull. **Five of my own claims overturned by measurement and corrected at every site.** |
