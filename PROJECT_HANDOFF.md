# Project handoff

> ## ⚠ LIVE CHANGES MADE 2026-09-13/14 — session `7e3da78a`, worktree `koi-document-ingest-integrity-20260913`
>
> Read this before touching the entity registry, the extractor, or the substack job.
> Every figure below was re-derived at wrap time, not carried forward from a message.
>
> ### 🚫 HARD PRE-RESUME BLOCKER — issue #68
>
> **Do not deploy PR #66 or replay any document until #68 lands.** Verified in this
> worktree: `scripts/schemas/deep_extraction_doc_v1.schema.json` and `…_v2…` both
> restrict `entities[].type` to the SAME 7-value enum — `Person, Organization,
> Project, Concept, Location, Protocol, CaseStudy` — and `TYPE_PRIORITY`
> (`scripts/extract_deep_documents.py:167`) covers exactly those 7. **`Document` and
> `Event` are absent from all three.**
>
> Why that is a blocker specifically *because of* #66: the extractor cannot emit
> `Document`, so an essay title can only come out as `Project`; it cannot emit
> `Event`, so `DWeb Camp 2026: Root Systems` cannot be re-declared correctly. Pinning
> makes the resulting type **permanent** — entity_type is hashed into the URI and
> cannot be corrected in place. Today's unpinned path writes the wrong type
> *correctably*; #66 would write it *irreversibly*. Pinning a wrong contract is worse
> than not pinning.
>
> ### State, verified
>
> | claim | verified |
> |---|---|
> | `com.personal-koi.substack-deep-extract` disabled AND unloaded | ✅ absent from `launchctl list`, present in `print-disabled`. **Keep it that way.** Backlog 356. |
> | branch clean, pushed | ✅ `git status --porcelain` empty; HEAD = origin = `1d7f708` |
> | PR #66 | ✅ OPEN, **draft**, **0 status checks**, head `1d7f708`. **Do not merge.** |
> | tests | ✅ 48 passed (`tests/test_ingest_identity.py`) |
> | facts retracted | ✅ exactly 5; ID set matches the target set; all other facts preserved |
> | federation | ✅ 3 `knowledge_episode` UPDATEs delivered to the same 4 peers as the originals; **peer application proven 0/4** — only NUC confirmed *receipt*, and `EventQueue.confirm()` is documented as receipt, not application |
> | issues filed | ✅ #67 federated fact retractions + peer application proof · #68 type contract (blocker above) · #69 transactional rollback/replay reconciliation |
> | replay task | ✅ `koi-2026-09-14-michaelgarfield-pinned-replay`, open, due **2026-09-21**, now vault-backed |
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
> Gate now, with the audited alias decisions: scenius `pass_after_preregistration`,
> rip-wendell-berry `pass_after_preregistration`, advertising `pass`. The two
> `pass_after_preregistration` results are exactly where #68 bites — the endpoints
> they would create are essay titles that the current schema can only type `Project`.
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
> **Re-enable preconditions (operator):** #68 landed · #66 deployed to BOTH
> `koi-processor-runtime` AND `koi-processor-service` · API restarted · OpenAPI
> exposing `subject_uri`/`object_uri` · canary proving `endpoints_pinned`.
>
> **Run the gates before ingesting anything:**
> `venv/bin/python scripts/check_document_integrity.py --document-rid <rid> [--payload <curated.json>] [--alias-decisions <file>]`
> (read-only; 0 = both pass, 1 = identity blocked or semantic fail, 3 = misconfigured).
>
> **Do NOT add the new floors to `darren-workflow`'s gate catalog yet** —
> `com.personal-koi.website-sensor` is loaded and runs `verify_doc_ingest.py --mode strict`
> weekly with `gate_ok` gating retirement, from the runtime clone. Adding floors before
> that clone carries this branch makes every weekly run exit 2 on `key ABSENT from evidence`.
>
> **New audit surface:** `SELECT * FROM entity_current_norm_duplicates;` — 169 live
> duplicate sets, 125 drift-created, 347 rows (#61 AC6).

**Updated:** 2026-09-14 11:38 PDT
**Session:** Claude Code · `7e3da78a` · Document-ingest identity hardening (#62/#61/#64), and the live repair it forced
**Status:** Branch `fix/document-ingest-integrity` @ `1d7f708`, clean and pushed; **draft PR #66** (0 checks, do not merge); migrations **124** and **125** applied live; `com.personal-koi.substack-deep-extract` **disabled and unloaded** (backlog 356); three michaelgarfield documents **half-repaired**; **issue #68 is a hard pre-resume blocker**.

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

**Session `7e3da78a`, 2026-09-13→14 (koi-infra).** Began as "harden high-integrity
document ingestion before importing another important paper" (#62, coordinated with
#61/#53, then #64). Became a repair arc when the one bounded substack batch this
session authorised demonstrated the defect live.

- **Draft PR #66** (`1d7f708`, 0 GitHub checks): payload-endpoint identity contract
  (`api/ingest_identity.py`) — collect → preflight → exact-only preregister → freeze
  with a bijection assertion → write pinned → verify persisted, seated between
  `merge_extractions()` and the first fact POST. `FactInput` gains
  `subject_uri`/`object_uri`; an unhonourable pin is a 422, never a silent fallback.
  Migrations **124** (normalizer compatibility READ, not a GENERATED column) and
  **125** (producer receipts + a semantic verdict kept structurally separate from
  structural completion), both applied live. 48 tests.
- **Measured, not assumed:** all four #62 collapses reproduce today (JW matching the
  issue to 4 decimals); the 100-fact batch boundary is **not** the mechanism (3 of 4
  pairs are in the same batch); `route_used` is **wrong**, not thin, and was solved
  once on `origin/fix/extractor-fallback-reasons` and lost; `GPT-4o`/`GPT-4.1`/
  `gpt-5.4` are three unrepaired live instances of #61.
- **Found while fixing:** `facts_to_episode_payload` never forwarded `confidence`
  (30,474 document facts, 213 with a value — all from the Buehler repair);
  `httpx.HTTPStatusError` escaped the transport fallback, repair loop AND per-window
  dead-letter, so one provider 4xx aborted a whole document. Both fixed.
- **One bounded substack batch ran and proved the point:** 3 documents, 5 wrong
  bindings — two fuzzy title collapses (JW 0.8518 against a 0.85 threshold; 0.9064),
  one cross-type, and two bound by EXACT match to an abstract Concept. Job then
  disabled by the operator.
- **Bounded repair, operator-directed:** 5 facts soft-retracted via the sanctioned
  endpoint; 3 `knowledge_episode` UPDATEs federated to the original 4-peer sets.
  Entity decisions applied — merge **325**, retype **326** + merge **327**, and a
  `Freedom (internet-blocking software)` Project distinct from the abstract Concept.
- **Three of my own design errors, each caught by real data, not by re-reading the
  diff:** the quality layer scored the real fixtures *backwards* (a
  distinct-over-total ratio punished thoroughness — replaced by `chunk_coverage` +
  `predicate_concentration`); `tombstone_risk` blocked a correctly-*repaired* graph;
  and a cross-type advisory would have minted the DWeb Berlin twin. Plus two the
  tests caught: an advisory that could never fire, and dict last-write-wins.

## Next steps

1. **#68 — align the deep-extraction type contract with `Document` and `Event`.**
   HARD BLOCKER; see the top block. Nothing else proceeds first.
2. **#67 — federated fact retractions + peer-application proof.** `retract_fact`
   emits no federation event at all; peer application is unverifiable from here.
3. **#69 — transactional document rollback / replay reconciliation.** Stale
   `document_entity_links` and discourse moves are why "retracted" ≠ "repaired".
4. **Then** the pinned replay — task `koi-2026-09-14-michaelgarfield-pinned-replay`,
   due **2026-09-21**. Not before #68, and not with `--force`.
5. Only after all of the above: deploy #66 to BOTH checkouts, restart the API,
   verify OpenAPI, canary `endpoints_pinned`, then consider re-enabling the job.

## Open questions

- **Peer application of the 3 retraction UPDATEs is unproven (0/4)** and may not be
  provable without DB/SSH access to a peer. #67 should decide what proof looks like.
- **`Knowledge Graphs` (plural, vault-backed, 11 facts)** is a separate live identity
  from the merged `knowledge graph`. Merge 325 was correct but is not final
  canonicalization — broader #61 work.
- **169 live duplicate sets (125 drift-created, 347 rows)** need an operator merge
  pass. Not attempted here.
- Whether the darren-workflow gate catalog gains the new floors is gated on the
  runtime clone carrying this branch — adding them earlier breaks the weekly
  website-sensor.

## Verification and working tree

- Branch `fix/document-ingest-integrity` @ `1d7f708`; `git status --porcelain` empty;
  `git diff --check` clean; HEAD == `origin/fix/document-ingest-integrity`.
- 48/48 `tests/test_ingest_identity.py`; 18/18 across the adjacent suites touched.
  **3 pre-existing unrelated failures** in `tests/test_knowledge_router_facts_gate.py`,
  verified identical on a clean tree (stash control).
- Both migrations dry-run inside a rolled-back transaction AND given negative controls
  that break them **by deletion** (exit 3 each). Two earlier control attempts died on
  syntax errors rather than the assertion, proving nothing, and were redone.
- Canon validator: **not applicable** (`scripts/validate_spec_dag.py` absent).
- Gates re-derived at wrap time: Buehler curated 115→115 bijective PASS/PASS; the
  stored thin run FAIL (`chunk_coverage` 0.2131); the three repaired documents
  `pass_after_preregistration` ×2 and `pass` ×1.

## Recent sessions

| Date | Provider | Session | Summary |
|---|---|---|---|
| 2026-09-14 | Claude Code | `7e3da78a` (koi-infra) | **Document-ingest identity hardening (#62/#61/#64) + a live repair it forced.** Draft PR #66 @ `1d7f708`, 48 tests, migrations 124+125 applied live. Proved all four #62 collapses still reproduce and that the batch boundary is NOT the mechanism. One bounded substack batch wrote **5 wrong bindings across 3 docs**; job disabled by the operator. Bounded repair: 5 facts retracted + 3 federated UPDATEs (delivery 4/4, **application 0/4**); merges 325/327, retype 326. Filed #67/#68/#69. **#68 is a hard pre-resume blocker** — the extraction schemas cannot emit `Document` or `Event`, and pinning would make those wrong types permanent. Three of my own design errors caught by real data, not by re-reading the diff. |
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
