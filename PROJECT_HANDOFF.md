# Project handoff

**Updated:** 2026-09-04 19:50 PDT
**Session:** Claude Code · a0f88bbf · MCP supply chain + the two-node written statement (with `1e1f2abb` in parallel)
**Status:** `regen-prod` @ `eb4345a`, **published, 0 ahead / 0 behind**, tree clean, 114 passed / 2 skipped in the launchd suite. **Nothing is half-applied here.** One thing is deliberately unpublished and is the single open action: `personal-koi-mcp` @ `409fe9d` (axios lockfile) is 2 ahead of its origin — see Next steps #1.

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

**Session `a0f88bbf`, 2026-09-04.** Scope was `PROJECT_HANDOFF` Next steps #1 and #2, cut to
"Part 1 in full + the written statement" before starting. Six commits, all published.

- **MCP supply chain.** `409fe9d` on `personal-koi-mcp` (lockfile only): axios 1.12.2→**1.20.0**,
  form-data→**4.0.6** (HIGH), follow-redirects→**1.16.0**, plus the two undisclosed movers a later
  audit caught — **`proxy-from-env` 1.1.0→2.1.0 (semver major)** and hasown. Before/after `npm
  audit` set diff: **30 advisories resolved, 0 introduced, 11→8 vulnerable packages**. All **39
  Dependabot alerts dismissed**; task 9323 done.
- **The written statement.** `docs/operations/two-node-topology.md`, linked from `CLAUDE.md`.
- **The launchd guard now asks "does the target still exist?" of EVERY installed job** (`eb4345a`),
  after a fourth subset-enumeration instance. Two things it exposed: `com.darren.*` never matched
  `com.darrenzal.*` (9 vs 24 plists, **zero overlap**), and **three installed plists are malformed
  XML no parser will read** — two of them loaded at exit 0 from launchd's cache, so they work today
  and **will not survive a reload**. Three registers, each with a staleness assertion; all controls
  run and restored.
- **Ten koi tasks** filed or updated, all dated.

### What this session got wrong, and how it was caught

Kept because it is the session's most reusable output. A 34-agent adversarial workflow raised 29
findings, 22 survived refutation, and the parallel session `1e1f2abb` caught two more.

| my claim | truth | the defective probe |
|---|---|---|
| "every NUC migration is hand-delivered" | files **did** flow by rsync — 124 of 128 landed 2026-06-24, last 4 on 2026-07-19 | compared the nanosecond field against **ten** zeros when `stat -c %y` emits **nine**, so all 128 "failed" and the answer inverted. **Came within one command of publishing a refutation of a correct claim.** |
| "soak-check never printed OK" | it printed **592 OKs over six months**; the durable log **jumped checkouts** 2026-08-25 | read `/tmp/soak-cron.log`, which only covers the post-jump window — a subset stated as a universal |
| "six LaunchAgent entrypoints" | **nine** — three reach the clone through wrappers | enumerated `ProgramArguments` only |
| "`allowed_facets` is the obvious hole" | **0 rows on both sides** — but *not inert*: `tr_entity_facets_registered` is ENABLED on both, so every non-empty facet write is rejected today | — |
| commit msg listed 3 version changes | **six**, one a semver major | — |
| "dismissed all 39 alerts" | this session dismissed **29**; 10 predated it | — |

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

Ordered **by kind, not by number**. #1 is the only thing this session left undone; #2 and #4 are
parked with reasons; #3 is unchanged and still wants a cold facilitator.

1. **⛔ THE ONE OPEN ACTION — make the axios fix live.** `409fe9d` is committed on
   `personal-koi-mcp` main but **unpublished**, and `node_modules` still holds **1.12.2**, so the
   fix is *not live*. Two blockers, one of which is a decision:
   - **Publishing also publishes `d659abb`**, another session's commit already in that branch's
     history. Separating them needs a rebase. Operator call.
   - **`npm install` + restarting the MCP processes.** Enumerate at the moment you restart with
     `~/.config/personal-koi/enumerate-mcp-processes.sh` (committed at
     `scripts/enumerate-mcp-processes.sh`, drift-asserted) — **never carry a count**, it was 8, 9,
     10, then 7 within an hour. Note the install also lands ~25 pre-existing `@esbuild` platform
     packages unrelated to this change.
   Verification is already done and does not need repeating: scratch tree at 1.20.0, `npm ci` +
   `tsc` + 5 GET-only MCP tools against live `:8351`, 5/5 pass, **zero row delta** across five
   tables; control run — unreachable backend gives 0/5 and exit 1. ⚠ **Do not verify with
   `evals/claims_smoke.ts`**: zero teardown, mints a claim per run, and calls `anchor_claim`,
   which on this stack targets **mainnet**.

2. **The two-node monitor — designed, approved, deliberately NOT built.** Task
   `koi-nuc-parity-monitor-rescope`. Both sessions on 2026-09-04 independently concluded the
   approved design guards the wrong thing: vocabulary drift is slow and bounded and hurt nobody,
   while **unvalidated prose about system state** was wrong six times in one day and was
   mechanically checkable throughout. A checker that verifies *the document's own claims* would
   have caught 4 of the dozen. Also: adding a third monitor beside two saturated ones is how the
   third gets ignored. **Fresh-eyes design question — should not be picked up by `a0f88bbf` or
   `1e1f2abb`.** Full approved design is preserved in
   `~/.claude/plans/start-in-projects-koi-processor-service-curried-flute.md` (Deliverable B).

3. **The vocabulary decision — needs the operator, rested, with a cold facilitator.** 9315 + 9317, gating migration 113. Both E1 blockers are now answered (federation mirrors verbatim, no normalisation anywhere on the apply path; NUC migration is separate and manual). ⚠ **Neither `e1dd0df8` nor `1e1f2abb` should facilitate this** — nor `a0f88bbf`. (Note 2026-09-04: cold facilitation is the right instinct but authorship was **refuted as the variable** — three different authors made the same class of error on the topology paragraph the same day. It is a staffing convention that depends on someone remembering; the mechanical companion is task `koi-reproduce-command-rule-for-state-claims`. Keep both.) Both sessions authored the evidence pack (`~/.claude/plans/koi-vocabulary-decisions-9315-9317-2026-09-04.md`, also attached to the `context` of tasks 9315/9317 — that pack and those task contexts, not this file, carry the corrected numbers), and a session that framed the options cannot adversarially test its own framing. It wants a session reading the pack cold. Not urgent: due 2026-09-17 and the divergence grows at single digits/day.
4. **Parked for the 09-06 repair cycle, all dated and owned elsewhere.**
   `koi-reproduce-command-rule-for-state-claims` (the proposed rule + today's full evidence; held
   because `~/.claude/CLAUDE.md:125` forbids same-session self-modification — *recording* it now is
   deliberate, since the evidence decays), `koi-malformed-signal-export-plists` (⚠ **do not "fix"
   casually — both jobs currently work from launchd's cache; a bootout/bootstrap is how you find
   out**), 9413 (darren-workflow's), and `koi-vault-sync-disabled-on-serving-checkout-only`
   (**answer this BEFORE the soak-check honesty fix**, or that fix encodes the wrong assumption
   about what normal is).

5. **Residue.** The 45-row retype (~22–29% precision; 3 need merges and `/retype` never calls `persona_merge_hazard`), and the `incident-enrich` / `walk.py` asymmetry — the bug is not unbounded growth, it is that **one producer has a clock and no allowlist while its sibling has neither**.

## Open questions

- **Are `Protocol`/`Project` TYPES or FACETS?** Gates migration 113's shape. Task 9315. `allowed_facets` exists with an FK and a shape CHECK — zero rows, and **zero *application* readers but ONE live database reader**: `entity_facets_registered_guard` is bound to the **enabled** trigger `tr_entity_facets_registered` on `entity_registry`'s hot write path, and with the table empty it rejects every non-empty facet write today. "Zero users" was refuted 2026-09-04; seeding is step one of any facet answer.
- **What IS the predicate vocabulary?** Task 9317. Only **36 of 4,938** case-folded predicates are in the 56-row table, so a casing rewrite touches ~465 collision rows and leaves ~4,900 unlisted predicates exactly where they are. Casing is a small slice of the question, not the question.
- **The 4 fold-collisions are two relations, not two spellings** — they split by *producer*: uppercase is LLM prose extraction (Apr–Aug), lowercase is `walk.py`/`adjudicate.py` at confidence 1.0 (last 36h). Recommended: rename the structural side (`contains_file`, `invokes_script`). ⚠ Do **not** rely on `predicate_raw IS NULL` to un-fold them later — it separates these 465 perfectly today only by coincidence of producer timing; 2,222 uppercase rows elsewhere carry it.
- **Migration 113 was never written and its gate has been open ~10 days.** Two blockers beyond the decision: a committed architecture doc argues enforcement should *not* be a hard constraint (overridable — its own number is wrong — but explicitly), and `personal_koi_test.allowed_entity_types` has **0 rows** with migration 111 missing there, so an FK would refuse every entity insert in the suite.
- **9313's title says `DECIDED` while its context says "this is an operator call."** One of them is stale; other sessions read both.
- **The unattended-git guard has now false-positived five times** on the word appearing in prose (it refused a read-only `ls .git/hooks`, a heredoc running no git command, and an `echo` label). Deliberately **not** patched this session — the ≥48h soak rule forbids same-session self-modification from a lesson learned in that session; it belongs in the 09-06 repair cycle as a Pattern card.
- Carried unchanged from 2026-09-02: the Organization→Person natural experiment does not work; 17 do-not-merge rows remain unseedable; `KOI_CLAIMS_SERVICE_TOKEN` is populated.

## Verification and working tree

- **Branch/status:** `regen-prod` @ `eb4345a`, working tree clean, **0 ahead / 0 behind**.
  `personal-koi-mcp` @ `409fe9d`, **2 ahead of origin, unpublished by design** (Next steps #1),
  with one unrelated third-party edit in `src/koi-api-tools.ts` left uncommitted on purpose —
  committing it would fire the repo's `post-commit` → `rebuild-dist.sh` hook and recompile `dist/`
  underneath the live MCP processes.
- **Tests:** 114 passed / 2 skipped in `tests/test_launchd_job_targets.py` (grew from 72 when the
  existence check widened to every installed plist).
- **Positive controls were RUN, not written.** Emptying `KNOWN_MISSING_TARGETS` fails
  `phase7-autoflip` alone; emptying `KNOWN_UNPARSEABLE` fails both signal plists alone; a bogus
  register entry fails the staleness assertion; appending a line to `scripts/enumerate-mcp-processes.sh`
  fails its drift test. All restored.
- **Live:** service healthy, embeddings available. **Nothing was restarted or deployed today.**
- **⚠ `stat` is SHADOWED and is KILLED (exit 137, zero output) on BSD flags — use `/usr/bin/stat`.**
  `/usr/bin/ps` does not exist; use `/bin/ps`. `timeout`/`gtimeout` are absent. `gh` **consumes
  stdin in a loop** — batch PATCHes silently no-op at exit 0 without `</dev/null`; `dismissed_comment`
  caps at 280 chars and GitHub **409s on amending an already-dismissed alert**.
- **⚠ mtime vs ctime on the NUC.** `rsync -a` preserves the source **mtime**, so mtimes are
  *authoring* dates and **ctime** is when the inode landed. Whole-second mtime = rsync-delivered.
  Getting this backwards inverted a conclusion today; the nanosecond field from `stat -c %y` is
  **nine** digits, not ten.
- **⚠ Route presence does not prove module vintage** — compare `ps -o lstart` to the **mtime** of
  the newest loaded source. Serving PID via `lsof -ti :8351 -sTCP:LISTEN`.
- **⚠ Before deploying, check reachability:** `git merge-base --is-ancestor <sha> origin/<branch>`.

## Recent sessions

| Date | Provider | Session | Summary |
|---|---|---|---|
| 2026-08-24/25 | Claude Code | b289ac1e | Executed the full prior plan: 9 sweep fixes, Meeting-notes repair (270 notes), migration 112 complete (421→0). Established `koi-sensors-runtime` + hardened the launchd guard. |
| 2026-08-26 | Claude Code | b289ac1e (resumes) | 3rd conflict wave cleaned; built `com.personal-koi.vault-conflict-sweep`; fixed a real `ThrottleInterval < StartInterval` bug + added a mutation-tested anti-storm pin; fixed `test_koi_flow_integration.py`'s months-stale collection failure. |
| 2026-08-26 | Claude Code | c1defaa8 | **Verification pass.** Found A7 unexecutable, a gating inconsistency, A3's live blast radius; caught the conflict cleanup incomplete at 101 files; named the iCloud root cause; canary-proved the sweep fires unattended. No code changes. |
| 2026-09-01/02 | Claude Code | 72cf052b | **Phase 0/1 hardening.** Backup + verified restore; merge reversibility (`unmerge`, used on 57 merges); `entity_non_match` seeded (44) and enforcing at 6 tiers; credential + persona guards; `:8351` LAN hole closed and A/B-verified; type-mismatch void closed. 12 commits. |
| 2026-09-04 | Claude Code | a0f88bbf | **MCP supply chain + the two-node written statement.** axios lockfile committed (30 advisories cleared, not yet live); 39 Dependabot alerts dismissed; `docs/operations/two-node-topology.md` written; launchd guard widened to every installed plist after a 4th subset-enumeration instance, exposing 3 malformed plists and a namespace (`com.darrenzal.*`) no glob ever matched. 6 commits published, 10 tasks filed. A 34-agent audit + the parallel session overturned **6 of my own claims**, two of them corrections I had just made. |
| 2026-09-04 | Claude Code | 1e1f2abb | **Decisions 9315/9317 prepared; email guard completed; 116 cleared.** 20 agents over two workflows, every lens returned CORRECTED. Killed the 27.9× ratio (it is ~8×), the 176 population (166), the 142/57 reversibility split (0%, not 71%), the one-insert-path premise (two live writers), and "the NUC is unreachable" (it is reachable, and already holds the divergent vocabulary without failing). Corrected my own false report that 116 was blocked — I read the layout instead of asking the process. 2 koi-sensors commits, both positive-controlled. |
| 2026-09-03 | Claude Code | e1dd0df8 | **Backup armed; retype made reversible.** The nightly backup plist had never been bootstrapped — newest dump was Aug 31, hand-run, ~3 days unbacked on 27 GB. `/entities/retype` captured no reversal and had already made **142 irreversible merges**. Launchd guard enumerated a subset (missed `com.darren.*`, found 2 real violations). `restart.sh` reported ERROR on restarts that succeeded (30s budget vs 40–73s startup). Retracted the false D4b claim before it shipped. 5 commits, all positive-controlled. |
| 2026-09-03/04 | Claude Code | e1dd0df8 | **Vocabulary arc.** Backup had never been bootstrapped — now armed and proven unattended. `/entities/retype` made reversible after 142 irreversible merges. Launchd glob widened (2 violations). `restart.sh` false-ERROR fixed. E3 shipped, tripwire restored. 23 commits published; flood fix live after a second pull. **Five of my own claims overturned by measurement and corrected at every site.** |
