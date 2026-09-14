# Project handoff

> ## ⚠ LIVE CHANGES MADE 2026-09-13/14 — sessions `7e3da78a` + `bb26783d`, worktree `koi-document-ingest-integrity-20260913`
>
> Read this before touching the entity registry, the extractor, or the substack job.
> Every figure below was re-derived at wrap time, not carried forward from a message.
>
> ### ✅ #68 CLEARED IN THE BRANCH — 2026-09-14, session `bb26783d`, commit `3bbe405`
>
> This block used to read "🚫 HARD PRE-RESUME BLOCKER — do not deploy PR #66 or replay
> any document until #68 lands". #68 has landed **in the branch**, on no deployed
> surface. Detail in *Completed this session* below.
>
> **Still true: do NOT deploy #66 and do NOT replay.** #67 and #69 are untouched and
> the re-enable preconditions below are unchanged.
>
> ### State, verified
>
> | claim | verified |
> |---|---|
> | `com.personal-koi.substack-deep-extract` disabled AND unloaded | ✅ absent from `launchctl list`, present in `print-disabled`. **Keep it that way.** Backlog 356. |
> | branch clean, pushed | ✅ `git status --porcelain` empty; HEAD = origin = `3bbe405` |
> | PR #66 | ✅ OPEN, **draft**, **0 status checks**, head `3bbe405`; body updated, now `Closes #62 and #68`. **Do not merge.** |
> | tests | ✅ 50 `test_ingest_identity.py` + 29 `test_document_extraction_type_contract.py` + 28 `..._fixtures.py` = **107**; **214** across every suite touching the extractor or identity. Full suite vs a clean worktree at the merge-base: **67 failures/errors, identical sets — zero regressions**, +53 passing. |
> | facts retracted | ✅ exactly 5; ID set matches the target set; all other facts preserved |
> | federation | ✅ 3 `knowledge_episode` UPDATEs delivered to the same 4 peers as the originals; **peer application proven 0/4** — only NUC confirmed *receipt*, and `EventQueue.confirm()` is documented as receipt, not application |
> | issues filed | ✅ #67 federated fact retractions + peer application proof (OPEN) · ~~#68 type contract~~ **implemented `3bbe405`, AC comment posted** · #69 transactional rollback/replay reconciliation (OPEN) |
> | replay task | ✅ `koi-2026-09-14-michaelgarfield-pinned-replay`, open, due **2026-09-21**, now vault-backed |
>
> **Migration 126** (`document_extraction_type_registry`) is written and **NOT
> applied** — deliberately. Verified read-only that it is a **no-op on this laptop**:
> `allowed_entity_types` already holds `Document` and `Event` with
> `extractable = true`, 9 extractable in total. It exists so a database rebuilt from
> `migrations/` does not reproduce migration 111's "extractable = true for exactly
> the 7". Its down file clears the flag and deliberately does NOT delete the rows.
>
> **Applied to `personal_koi` (live):** migration **124** `entity_normalization_compat`
> (read-side only — deliberately NOT a GENERATED column; that would collapse 125
> currently-distinct live groups) and migration **125**
> `extraction_provenance_and_quality`. 125's down file destroys data that lives
> nowhere else; it exports first, below `ON_ERROR_STOP`.
>
> ### The three Michael documents are HALF-REPAIRED
>
> `substack-corpus:michaelgarfield:{scenius,rip-wendell-berry,advertising}`.
> Five facts retracted and federated, **but**: `deep_extracted_at` is deliberately
> still set (so the unattended backlog cannot select them), `document_entity_links`
> (16/15/14) and `session_discourse_moves` (7/8/6) are **stale**. Local fact
> retraction is NOT complete repair — that is #69.
>
> Gate now, re-derived 2026-09-14 after #68: **all three exit 0 with zero real
> blockers** — scenius and rip-wendell-berry `pass_after_preregistration`, advertising
> `pass` (13 → 13 distinct URIs, bijective). The two pending endpoints are the named
> essays, and they now resolve as **`Document`**, not `Project`.
>
> That needed an audited type override on the CACHED payload, applied WITHOUT a
> replay by the new `scripts/curate_cached_payload.py`; the curated files sit beside
> the rest of the evidence as `{scenius,rip-wendell-berry}-curated.json`.
>
> ⚠ **Scope boundary.** Only endpoints with NO live row were retyped.
> `Whole Earth Catalog`, `Standing by Words` and `Manifesto: The Mad Farmer Liberation
> Front` are live `Project` rows — retyping those in a payload would correctly raise a
> `cross_type_conflict`. They need an operator `/entities/retype`, not curation.
>
> Replay inputs + before/after evidence:
> `~/Documents/sources/michaelgarfield-substack-repair-20260914/`. Do NOT use `--force`
> or a generic re-ingest.
>
> ### Entity decisions applied (operator, 2026-09-14)
>
> - `knowledge graph` duplicate merged — **merge log 325**, survivor keeps 112 facts.
>   ⚠ **Valid but NOT final canonicalization:** `concept-knowledge-graphs-170dfbfe3308`
>   ("Knowledge Graphs", plural, **vault-backed**, 11 active facts) is still separate —
>   it normalizes to `knowledge graphs`, so the current normalizer does not see it as
>   the same identity. Belongs to the broader #61 work.
> - `DWeb Berlin` retyped Project→Event (**326**) and merged into
>   `DWeb Camp 2026: Root Systems` (**327**); alias `dweb berlin` retained; 8 facts
>   carried; **no Organization minted**.
> - `Freedom (internet-blocking software)` created as a Project distinct from the
>   abstract `freedom` Concept, which kept its 1 unrelated fact.
> - Cached Scenius extraction corrected `Organization`→`Event` (one-line diff verified).
>
> ### Two systemic findings
>
> - ⚠ **`retract_fact` emits NO federation event.** Every fact ever retracted on this
>   node is still live on peers. The 3 UPDATEs above were emitted by hand. → #67
> - ⚠ **Peer application is unverifiable from here.** `confirm()` is receipt-only, the
>   peer read API hides retracted rows (proved with a local control), and there is no
>   SSH/DB access to any peer. Delivery: 4/4. Application: 0/4. → #67
>
> ### Both code paths are UN-HARDENED today
>
> The job runs from `koi-processor-runtime` @ `c11a4c3`; the live API is a process
> started **2026-09-11**. Neither carries #66. Refreshing the runtime clone alone is
> **not** sufficient — facts are written by the API, so the *service* checkout plus a
> restart are what move the write path.
>
> **Re-enable preconditions (operator):** ~~#68 landed~~ ✅ (branch only, `3bbe405`) ·
> #66 deployed to BOTH
> `koi-processor-runtime` AND `koi-processor-service` · API restarted · OpenAPI
> exposing `subject_uri`/`object_uri` · canary proving `endpoints_pinned`.
>
> **Run the gates before ingesting anything** (read-only):
>
> ```
> venv/bin/python scripts/check_document_integrity.py --document-rid <rid> \
>     [--payload <curated.json>] [--alias-decisions <f>] [--type-decisions <f>]
> ```
>
> 0 = both pass (now INCLUDING `pass_after_preregistration`, which was crashing with
> `KeyError` and exiting 1 — corrected 2026-09-14), 1 = identity blocked or semantic
> fail, 2 = `--strict-review` / `--strict-preregistration` tripped, 3 = misconfigured.
> `--type-decisions` is new and is the ONLY thing that clears a `cross_type_conflict`.
>
> **Do NOT add the new floors to `darren-workflow`'s gate catalog yet** —
> `com.personal-koi.website-sensor` is loaded and runs `verify_doc_ingest.py --mode strict`
> weekly with `gate_ok` gating retirement, from the runtime clone. Adding floors before
> that clone carries this branch makes every weekly run exit 2 on `key ABSENT from evidence`.
>
> **New audit surface:** `SELECT * FROM entity_current_norm_duplicates;` — 169 live
> duplicate sets, 125 drift-created, 347 rows (#61 AC6).

**Updated:** 2026-09-14 12:47 PDT
**Session:** Claude Code · `bb26783d` · Issue #68 — one authoritative document-extraction type contract, with Document and Event
**Status:** Branch `fix/document-ingest-integrity` @ `3bbe405`, clean and pushed; **draft PR #66** (0 checks, do not merge) now closes #62 and #68; **#68 is resolved in the branch, not deployed**; migrations 124/125 live, **126 written and NOT applied** (verified no-op); `com.personal-koi.substack-deep-extract` still **disabled and unloaded** (backlog 356); three michaelgarfield documents still **half-repaired**; **#67 and #69 still gate the replay**.

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

**Session `bb26783d`, 2026-09-14 (koi-infra).** Bounded to issue **#68**: align the
deep-document extraction type contract with `Document` and `Event`. Commit
**`3bbe405`**, pushed; draft PR #66 body rewritten (now `Closes #62 and #68`).

- **The root cause was the inverse of the issue title.** Re-derived read-only:
  `allowed_entity_types` designates **9** types extractable — the seven plus
  `Document` and `Event`. Three code surfaces said seven. The registry was already
  right; nothing compared them. So the fix is a single source plus a drift check,
  not two enum edits.
- **One source, every surface derived.** `api/document_extraction_contract.py` is
  now the only place the vocabulary is written.
  `scripts/render_extraction_contract.py` derives both schemas and, in both prompts,
  the typing rules, the appendix enum, HARD OUTPUT RULE 2 **and** the lead-in
  sentence — which turned out to be a seventh hand-maintained copy nobody had
  counted. `--check` fails on any hand edit; a missing marker or an ambiguous enum
  line raises rather than skipping the surface.
- **Priorities chosen on the observed failure, not taste.**
  `Person 9 > Event 8 > Organization 7 > Document 6 > Project 5 > Location 4 >
  Protocol 3 > CaseStudy 2 > Concept 1`. The legacy seven keep their exact relative
  order (asserted). Before this, `.get(etype, 0)` ranked both new types **below
  `Concept`**, the floor, silently.
- **Cross-window coercion is no longer silent.** `merge_extractions` still keys on
  the normalized name (so one entity typed two ways stays one entity), but every
  coercion and every off-contract type is now reported in the payload, the run
  receipt and the gate. Counted per entity record, not per comparison.
- **The audited-type-decision escape hatch was dead end-to-end, and is now live.**
  `preflight_endpoints` had accepted `type_decisions` since #66 and **no caller ever
  passed them**; worse, `preregister_missing` refused the mint even when a decision
  had cleared the gate, so the blocker's own advertised remedy did nothing. Wired
  through `DOC_IDENTITY_TYPE_DECISIONS` and `--type-decisions`, carried on the
  finding, honoured at registration, logged loudly, recorded in the receipt. One
  existing test's asserted behaviour was changed deliberately, with the reasoning in
  its docstring.
- **Drift detection at startup, not only in tests.** `assert_contract_surfaces()`
  checks the **loaded** prompt and schema and fails terminally. This is the half that
  covers the real deployment gap: the launchd job runs from `koi-processor-runtime`
  and every path is env-overridable, so a test over this repo proves nothing about
  what an unattended run sent to a model. The prompt half matters most — a current
  schema with a stale prompt yields seven-type output that validates perfectly.
- **Two defects found while implementing, neither in the issue.**
  `check_document_integrity.py` crashed with `KeyError: 'endpoints'` on
  `pass_after_preregistration` — the state of exactly the two documents #68 is about
  — and exited `1` for it despite its own code documenting it as a pass. Both fixed.
- **`scripts/curate_cached_payload.py`** applies audited type overrides to a cached
  extraction without replaying, and refuses an override that matched nothing or was
  already true.

## Next steps

1. **#67 — federated fact retractions + peer-application proof.** `retract_fact`
   emits no federation event at all; peer application is unverifiable from here
   (delivery 4/4, application 0/4).
2. **#69 — transactional document rollback / replay reconciliation.** Stale
   `document_entity_links` and discourse moves are why "retracted" ≠ "repaired".
3. **Then** the pinned replay — task `koi-2026-09-14-michaelgarfield-pinned-replay`,
   due **2026-09-21**. Curated payloads are ready (above). Not before #67/#69, and
   not with `--force`.
4. Only after all of the above: deploy #66 to BOTH checkouts, restart the API,
   verify OpenAPI, canary `endpoints_pinned`, then consider re-enabling the job.
   Migration 126 applies at that point (a no-op here, needed on a rebuilt database).

## Open questions

- **Does `Event` outranking `Organization` hold up?** Chosen on one observed collapse
  (`DWeb Berlin` captured as an Organization) with no counter-example. The test that
  pins it names the rationale, so a change is a decision rather than a drift — but a
  second corpus could overturn it.
- **`Whole Earth Catalog`, `Standing by Words`, `Manifesto: The Mad Farmer Liberation
  Front`** are live `Project` rows that are arguably `Document`s. Payload curation
  cannot fix that; it needs an operator `/entities/retype` on the graph.
- Unchanged from last session and still open: peer application of the 3 retraction
  UPDATEs is unproven (0/4, may not be provable from here → #67); `Knowledge Graphs`
  (plural, vault-backed, 11 facts) is still a separate live identity from the merged
  `knowledge graph` (#61); 169 live duplicate sets need an operator merge pass; the
  gate-catalog floors stay parked until the runtime clone carries this branch.

## Verification and working tree

- Branch `fix/document-ingest-integrity` @ `3bbe405`; `git status --porcelain` empty;
  `git diff --check` clean; HEAD == `origin/fix/document-ingest-integrity`.
- **107** tests across the three directly-affected files (50 identity + 29 contract +
  28 fixtures); **214** across every suite touching the extractor or the identity
  contract.
- **Zero regressions, measured rather than assumed.** Full suite in this tree vs a
  throwaway `git worktree` at the merge-base: **67 failures/errors, byte-identical
  sets**; +53 passing. ⚠ 67 is larger than the "3 pre-existing" recorded here before —
  that figure covered only the suites the previous session ran. Most of the other 64
  are per-test schema fixtures missing `entity_merge_log.reversal`, which the
  production schema has. None is ours; none is fixed by this branch.
- Every drift check has a **positive control** proving it can fail (hand-edited schema,
  hand-edited prompt rule, missing marker, ambiguous enum line, stale schema, stale
  prompt, schema with no enum). The fixtures also assert the OLD seven-type enum
  **rejects** them, so a fixture that stopped exercising the fix would say so.
- The live-registry drift test runs against the real `allowed_entity_types` and does
  **not** skip.
- `python scripts/render_extraction_contract.py --check` → `all 4 contract surfaces
  match doc-entity-types-v2-2026-09-14`.
- **No live graph writes this session** — every database statement was a `SELECT`.
  `substack-deep-extract` re-verified absent from `launchctl list` and present in
  `print-disabled`; `koi-processor-runtime` and `koi-processor-service` both still on
  `regen-prod`. No replay, no re-ingest, no `--force`.
- Canon validator: **not applicable** (`scripts/validate_spec_dag.py` absent).

## Recent sessions

| Date | Provider | Session | Summary |
|---|---|---|---|
| 2026-09-14 | Claude Code | `bb26783d` (koi-infra) | **Issue #68 — one authoritative document-extraction type contract.** Commit `3bbe405`, pushed; PR #66 body rewritten (`Closes #62 and #68`). Root cause was the inverse of the title: the registry already marked **9** types extractable while three code surfaces said seven, and nothing compared them. `api/document_extraction_contract.py` is now the single source and all four surfaces are DERIVED (`render_extraction_contract.py --check`); `assert_contract_surfaces()` re-checks the LOADED prompt+schema at run start, because the job runs from a different checkout. Found two things not in the issue: the audited-type-decision escape hatch was **dead end-to-end** (no caller passed `type_decisions`, and preregistration refused even a decided conflict), and the operator gate **crashed with KeyError** on exactly the two documents #68 is about. 57 new tests, each drift check with a positive control; full suite vs a clean worktree = **67 failures, identical sets, zero regressions**. All three michaelgarfield payloads now exit 0 with the essays typed `Document`. No live writes; migration 126 written and NOT applied (verified no-op). |
| 2026-09-14 | Claude Code | `7e3da78a` (koi-infra) | **Document-ingest identity hardening (#62/#61/#64) + a live repair it forced.** Draft PR #66 @ `1d7f708`, 48 tests, migrations 124+125 applied live. Proved all four #62 collapses still reproduce and that the batch boundary is NOT the mechanism. One bounded substack batch wrote **5 wrong bindings across 3 docs**; job disabled by the operator. Bounded repair: 5 facts retracted + 3 federated UPDATEs (delivery 4/4, **application 0/4**); merges 325/327, retype 326. Filed #67/#68/#69. **#68 is a hard pre-resume blocker** — the extraction schemas cannot emit `Document` or `Event`, and pinning would make those wrong types permanent. Three of my own design errors caught by real data, not by re-reading the diff. |
| 2026-09-11/12 | Claude Code | `a8751c7e` (stream B) | **biofi.earth ingested + the backup arc.** New git-versioned website sensor (runtime branch `website-sensor-2026-09-12`, scheduled weekly). Every database and the source archive now **encrypted off-host on gaia**, checksum-verified, with recovery **drilled on the NUC** — which found the arrangement unrecoverable (gaia authorised one SSH key) and fixed it. Restore measured index-bound (57 min for one hnsw index; data proven complete first). `koi_backup_check.sh` now reads the markers nothing read. Migration 123 adversarially reviewed (29 agents) and cleared; federation checked (zero `document:` RIDs ever left this node). Command centre at :5051 `#dash`. ~14 instrument-name defects in my own work, all found by running things; memory written. 7 commits here, all pushed; `e1cfac8` in sync. |
| 2026-09-07 | Claude Code | `217282eb` (cross-stream, started in darren-workflow) | **This repo owns upcoming work but was NOT modified.** New plan `~/.claude/plans/koi-web-ingest-integrity.md` targets it: `POST /web/preview` returns **zero bytes after 60s for `https://example.com`** (service otherwise healthy — `/health` 200, peers polling), and `/web/ingest` returns `"status": "ingested"` for calls that persist nothing (0 log rows, 0 chunks, vs a positive control of 1,619 rows). Plan defines an `ingested`/`not_persisted`/`skipped` enum enforced server-side here, plus a `web_ingest_jobs` async table. Laptop-first migration order. Nothing executed. |
| 2026-08-26 | Claude Code | b289ac1e (resumes) | 3rd conflict wave cleaned; built `com.personal-koi.vault-conflict-sweep`; fixed a real `ThrottleInterval < StartInterval` bug + added a mutation-tested anti-storm pin; fixed `test_koi_flow_integration.py`'s months-stale collection failure. |
| 2026-08-26 | Claude Code | c1defaa8 | **Verification pass.** Found A7 unexecutable, a gating inconsistency, A3's live blast radius; caught the conflict cleanup incomplete at 101 files; named the iCloud root cause; canary-proved the sweep fires unattended. No code changes. |
| 2026-09-01/02 | Claude Code | 72cf052b | **Phase 0/1 hardening.** Backup + verified restore; merge reversibility (`unmerge`, used on 57 merges); `entity_non_match` seeded (44) and enforcing at 6 tiers; credential + persona guards; `:8351` LAN hole closed and A/B-verified; type-mismatch void closed. 12 commits. |
| 2026-09-04 | Claude Code | a0f88bbf | **MCP supply chain + the two-node written statement.** axios lockfile committed (30 advisories cleared, not yet live); 39 Dependabot alerts dismissed; `docs/operations/two-node-topology.md` written; launchd guard widened to every installed plist after a 4th subset-enumeration instance, exposing 3 malformed plists and a namespace (`com.darrenzal.*`) no glob ever matched. 6 commits published, 10 tasks filed. A 34-agent audit + the parallel session overturned **6 of my own claims**, two of them corrections I had just made. |
| 2026-09-04 | Claude Code | 1e1f2abb | **Decisions 9315/9317 prepared; email guard completed; 116 cleared.** 20 agents over two workflows, every lens returned CORRECTED. Killed the 27.9× ratio (it is ~8×), the 176 population (166), the 142/57 reversibility split (0%, not 71%), the one-insert-path premise (two live writers), and "the NUC is unreachable" (it is reachable, and already holds the divergent vocabulary without failing). Corrected my own false report that 116 was blocked — I read the layout instead of asking the process. 2 koi-sensors commits, both positive-controlled. |
| 2026-09-03 | Claude Code | e1dd0df8 | **Backup armed; retype made reversible.** The nightly backup plist had never been bootstrapped — newest dump was Aug 31, hand-run, ~3 days unbacked on 27 GB. `/entities/retype` captured no reversal and had already made **142 irreversible merges**. Launchd guard enumerated a subset (missed `com.darren.*`, found 2 real violations). `restart.sh` reported ERROR on restarts that succeeded (30s budget vs 40–73s startup). Retracted the false D4b claim before it shipped. 5 commits, all positive-controlled. |
