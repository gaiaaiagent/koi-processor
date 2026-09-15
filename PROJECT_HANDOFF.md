# Project handoff

> ## ⚠ LIVE CHANGES MADE 2026-09-13/14 — sessions `7e3da78a` + `bb26783d`, worktree `koi-document-ingest-integrity-20260913`
>
> Read this before touching the entity registry, the extractor, or the substack job.
> Every figure below was re-derived at wrap time, not carried forward from a message.
>
> ### ✅ REVIEW BLOCKERS B1–B4 / M1–M8 FIXED IN THE BRANCH — 2026-09-14, session `98bc9fe1`
>
> The independent 101-agent review (session `76e0e751`) found PR #66 "ready for
> implementation fixes": the identity module had three defects that defeated it on
> real payloads (B1 relative registration URL → every first ingest died in httpx;
> B2 cross-type blindness on any multi-type payload; B3 raw-spelling map lookup),
> plus M1–M8. All are fixed in commits `cdc445c` (identity), `741ab0a` (quality),
> `8c711be` (gate), `79f1a21` (migration 126) — fixture-first, each core fix
> revert-proven, zero regressions vs the merge-base. Detail in *Completed this
> session*. **One behaviour change to know about before re-enabling anything:**
> an untyped fact endpoint no longer defaults to `Concept`; it BLOCKS as
> `type_undeclared` until an audited `--type-decisions` entry types it. Read-only
> census: **239 of 1,755** cached documents (1,045 endpoints) will block in strict
> mode for that reason. That is the operator's trade to make, not the extractor's.
>
> **Re-review follow-up (same session, later):** the re-review of `d51fb41` (session
> `76e0e751`) found ONE new merge blocker, introduced by the M6 fix itself: rebinding
> `_norm` to the entity normalizer also changed the **hash input of the persistent
> discourse-move uuid5 id**, so 5,578 of 13,767 stored document moves (1,169 docs, 12
> of them on the three michaelgarfield replay targets) would have stopped matching
> their own id and a replay would have inserted a twin beside each. Fixed in commit
> `97f6525` with a dedicated whitespace-only `discourse_move_id_key` /
> `discourse_move_id` used ONLY for the id hash — merging and identity keep
> `normalize_entity_text`. Pinned by literal UUIDs (`tests/test_discourse_move_ids.py`,
> 6 tests: 5 fail at `d51fb41`, the hyphen-free control passes on both sides).
> Read-only census after the patch: **13,765 / 13,767 stored ids reproduced**; the 2
> that match neither derivation are pre-existing (June 2026) anomalies, reported and
> untouched; 21/21 michaelgarfield target rows reproduced. Same commit corrects the
> false M4 "preflight already refuses" comments (preflight IGNORES an invalid alias
> decision and the endpoint can be minted before the freeze refuses — deferred to
> #69), the 1,046→1,045 count, the `collect_endpoints` docstring, and the spec's
> stale "67 byte-identical failures" claim. **The re-review's verdict was BLOCK on
> that one item only; no merge blocker is known to remain** — deploy, replay and
> job re-enablement are still gated by #67/#69 and the runbook order below.
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
> | branch clean, pushed | ✅ `git status --porcelain` empty; HEAD == origin. Review fixes = `cdc445c`, `741ab0a`, `8c711be`, `79f1a21`; re-review follow-up (move-id hash input) = `97f6525`; #68 code = `3bbe405`; the tip is the docs wrap on top. |
> | PR #66 | ✅ OPEN, **draft**, **0 status checks**; body carries `Closes #62` / `Closes #68` on separate lines (`closingIssuesReferences` lists BOTH — "and #68" was not a closing keyword). **Do not merge.** |
> | tests | ✅ **179** across the seven directly-affected suites, each file run ONCE (`--collect-only` = 179): 74 `test_ingest_identity.py` + 8 `test_check_document_integrity.py` + 19 `test_extraction_quality.py` + 15 `test_migration_126.py` + 29 `test_document_extraction_type_contract.py` + 28 `..._fixtures.py` + 6 `test_discourse_move_ids.py`. 72 are new this session. (The earlier "214" was the same 107 tests passed to pytest twice; an earlier version of this row said 180/7/73, which counted the move-id file's parametrized cases twice.) Full suite, same flags both sides (`--continue-on-collection-errors`): merge-base **55 failed / 10 errors / 1,755 passed** (1,985 items); branch at `c133ab8` **54 failed / 10 errors / 1,929 passed** (2,158 items); branch at `97f6525` **54 failed / 10 errors / 1,934 passed** (2,163 items). Those are two different runs with different collections, not one total: +6 `test_discourse_move_ids.py` items, −1 `test_launchd_job_targets.py::test_running_process_cwd_matches_its_plist[...]` case, which is parametrized over `running_koi_jobs()` at collection time. FAILED/ERROR node-id sets: identical between the two branch runs; **no failure exists on the branch that is not at the merge-base**; the one difference is a live-HTTP `ReadTimeout` at the merge-base, environmental. |
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
> the rest of the evidence as `{scenius,rip-wendell-berry}-curated.json`. They are
> **validation artifacts**, consumed only by the read-only gate
> (`check_document_integrity.py --payload`) — there is deliberately no replay path that
> consumes them until #69 supplies sanctioned reconciliation. A replay reads the stored
> windows, and would need the type overrides applied to the cache through a
> #69-sanctioned mechanism.
>
> ⚠ **Scope boundary.** Only endpoints with NO live row were retyped.
> `Whole Earth Catalog`, `Standing by Words` and `Manifesto: The Mad Farmer Liberation
> Front` are live `Project` rows — retyping those in a payload would correctly raise a
> `cross_type_conflict`. They need an operator `/entities/retype`, not curation.
>
> Gate-validation artifacts (curated payloads) + before/after evidence:
> `~/Documents/sources/michaelgarfield-substack-repair-20260914/`. Not replay inputs —
> see above. Do NOT use `--force` or a generic re-ingest.
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
> ~~review blockers fixed~~ ✅ (branch only, `cdc445c`…`79f1a21`) · #67 · #69 ·
> #66 deployed to BOTH `koi-processor-runtime` AND `koi-processor-service` · API
> restarted · OpenAPI exposing `subject_uri`/`object_uri` on `FactInput` AND
> `fact_ids` on `EpisodeCreateResponse` (the extractor now REFUSES a server that
> pins without naming the rows it wrote — `identity_verification_unscoped`) ·
> canary proving `endpoints_pinned` · a decision on the 239 documents that will
> block as `type_undeclared` (type decisions, or accept the block).
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
> fail, 2 = `--strict-review` / `--strict-preregistration` tripped, 3 = misconfigured
> (now also: a `--payload` whose `curation.document_rid` is not `--document-rid`).
> `--type-decisions` is the ONLY thing that clears a `cross_type_conflict` or types
> an endpoint the extractor left untyped (`type_undeclared`). The gate runs the
> PRODUCTION merge over the stored windows since `8c711be` and prints the TYPE each
> pending endpoint would be minted as — on the stored michaelgarfield windows that
> is `-> Project` for both essays; only the curated validation artifacts say
> `-> Document`, and no replay path reads those (see #69).
>
> **Do NOT add the new floors to `darren-workflow`'s gate catalog yet** —
> `com.personal-koi.website-sensor` is loaded and runs `verify_doc_ingest.py --mode strict`
> weekly with `gate_ok` gating retirement, from the runtime clone. Adding floors before
> that clone carries this branch makes every weekly run exit 2 on `key ABSENT from evidence`.
>
> **New audit surface:** `SELECT * FROM entity_current_norm_duplicates;` — 169 live
> duplicate sets, 125 drift-created, 347 rows (#61 AC6).

**Updated:** 2026-09-15 00:05 PDT
**Session:** Claude Code · 98bc9fe1-c3bb-4228-abe8-81cb0204c0bd · KOI: PR #66 review blockers + move-id hash input fixed
**Status:** Branch clean and pushed at the docs wrap above `97f6525`; B1–B4/M1–M8 and the re-review's move-id blocker are fixed in draft PR #66 (closes #62/#68), nothing deployed, migration 126 unapplied, job disabled — no merge blocker known to remain; #67/#69 and the deploy-before-replay order still gate everything live.

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

**Session `98bc9fe1`, 2026-09-14 (koi-infra).** Fixed the independently verified
blockers from the 101-agent review of draft PR #66 (session `76e0e751`) — B1–B4,
M1–M8 — fixture-first, one lead + two focused subagents, no live writes.

- **B1** `preregister_missing` POSTed a relative path on a hostless client and died
  in httpx on every first ingest. It now resolves an ABSOLUTE URL before any
  request (`base_url=` / absolute path / the client's own base) and refuses with a
  typed `IdentityError` otherwise; the extractor passes `KOI_BASE_URL`; `main()`
  catches `IdentityError`; identity failures stamp `deep_extraction_last_error`.
- **B2** cross-type detection is per ENDPOINT (the any-type same-label read is no
  longer filtered by the payload-wide type set); alias decisions bind only a live
  row of the endpoint's own type.
- **B3** `FrozenMap.uri_for`/`type_for` resolve by the canonical normalized key,
  so `GPT-4` and `gpt_4` both hit the one binding.
- **M3** an untyped fact endpoint has `entity_type=None` and BLOCKS as
  `type_undeclared` (evidence: what the graph holds under that label, any type);
  only an audited `--type-decisions` entry types it. Nothing defaults to Concept.
  Census (read-only, re-derived): **239 of 1,755** cached docs (1,045 endpoints)
  will now block in strict mode until typed — a throughput trade the operator
  should know about before re-enabling the job.
- **M4** the freeze validates an alias decision's target: live, same type,
  agrees with `expected_uris`.
- **M5** `EpisodeCreateResponse.fact_ids`; verification is scoped to the rows this
  run wrote; the extractor refuses if a server returns none.
- **M6** the merge lives in `api/extraction_merge.py`, keyed on
  `normalize_entity_text`; 0 cached docs still raise `payload_type_conflict`.
  **Follow-up (re-review blocker):** the entity key had also become the
  discourse-move ID hash input. `discourse_move_id_key` (lower + strip + collapse
  whitespace — the historical derivation, byte-for-byte) and `discourse_move_id`
  now own that hash; nothing else changed. Census: drift 5,579 → 2 rows (the 2 are
  pre-existing anomalies, `471473d4…` and `9da04ce8…`, June 2026, matching NEITHER
  derivation — reported, not rewritten); the 12 hyphen/underscore-titled
  michaelgarfield moves keep their stored ids.
- **M4 (ordering, documented not fixed):** preflight logs and IGNORES an alias
  decision whose URI is dead/unknown/wrong-type, so the endpoint can go `missing`,
  be MINTED by preregistration, and only then be refused at the freeze. The comments
  claiming preflight blocks were false and are corrected; making the mint reversible
  is #69's job.
- **M7** `check_document_integrity.py` runs the production merge; bijection is
  evaluated on the resolvable endpoints when some are missing; would-be mints are
  printed WITH their type; blockers counted before truncation; check-only evidence
  no longer fabricates a `verification` block; `curation.document_rid` must match
  `--document-rid` (exit 3, before connecting). `curate_cached_payload.py` emits
  the production merge and calls its output a validation artifact.
- **M1/M2** quality: per-window `chunk_ranges`; window-band fallback; `discourse_variety`
  → `discourse_concentration`; `ingest_document.py` reads `ext["quality"]`.
- **M8** migration 126 transactional + ledger row + exact set assertion.
- **B4** runbook order corrected (deploy before replay); curated payloads are
  gate inputs only — no replay path consumes them, by design, until #69.

  Method: one lead + two focused subagents (quality; migration/docs/PR), no broad
  audit. Every core fix was written test-first (failing run recorded), then
  revert-proven: with each fix undone alone its tests fail (`E ValueError: unknown
  url type: '/register-entity'`, `assert 'missing' == 'cross_type_conflict'`,
  `assert 'orn:…x-0' == None`, `assert 'Concept' is None`, `DID NOT RAISE
  IdentityError`, `assert 0 == 1` fact_ids, `assert 10 == 12` blockers, …) and the
  restored tree is green. **Two findings beyond the review's list**, both fixed:
  the M1b `elif` fall-through over-credited windows WITH `chunk_indices` too (the
  extractor's own path, not only the gate's stand-ins), and the earlier "214 tests"
  figure was the same 107 tests passed to pytest twice.
- **No live writes.** Every statement against `personal_koi` was a SELECT (gate
  runs on the three michaelgarfield docs, the M3 census). Test writes went to
  `personal_koi_test` inside rolled-back transactions. No replay, no `--force`, no
  migration applied, no job touched.


---

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
3. **Deploy #66 to BOTH `koi-processor-service` and `koi-processor-runtime`**, restart
   the API, and verify `/openapi.json`: `FactInput` must expose `subject_uri` /
   `object_uri` and `EpisodeCreateResponse` must expose `fact_ids`. Migration 126
   applies at this step (a no-op here, needed on a rebuilt database). Facts are
   written by the API, so refreshing the runtime clone alone moves nothing.
4. **ONLY THEN** the pinned replay — task `koi-2026-09-14-michaelgarfield-pinned-replay`,
   due **2026-09-21** — from the **stored windows via the extractor's normal path**,
   NOT from a curated payload file (no such input exists, by design). Re-run the
   read-only gate first and inspect the type each pending endpoint would be minted as.
   Not with `--force`.
5. Canary `endpoints_pinned`, then consider re-enabling the job.

## Open questions

- **How should the 239 `type_undeclared` documents be handled?** In strict mode they
  now block until an audited type decision names each untyped endpoint (1,045 of
  them). Options: per-corpus decision files, a prompt fix so the extractor declares
  every fact endpoint (it already says it MUST), or accepting the block for the
  backlog. Defaulting them to `Concept` is no longer on the table — that was M3.
- **Is `discourse_concentration`'s 0.90/0.70 the right starting pair?** Chosen by
  analogy with `predicate_concentration`, unmeasured under 5 moves (→ review), and
  stated as a starting value. The three michaelgarfield docs score 0.13–0.29.
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

**Session `98bc9fe1`:**

- Branch `fix/document-ingest-integrity`; `git status --porcelain` empty;
  `git diff --check` clean; HEAD == `origin/fix/document-ingest-integrity`.
- **179** tests across the seven directly-affected suites (each file once; see the
  table at the top; `--collect-only` = 179); **72 new this session**; every one green.
  The move-id fix: `tests/test_discourse_move_ids.py` 6 tests — 5 FAIL at `d51fb41` and with the
  key reverted to the entity normalizer (pinned UUIDs differ), the hyphen-free
  control PASSES in all three states; the affected discourse/identity suites
  (`test_discourse_move_ids` + `test_discourse_search` + `test_ingest_identity` +
  `..._fixtures`) 130 passed; the six-suite command 173 passed; `git diff --check`
  clean; full suite re-run after the fix, same flags: 54 failed / 10 errors / 1,934 passed / 2,163 items (the earlier branch run at `c133ab8`: 1,929 passed / 2,158 items). Derived from the two saved outputs: +5 items = +6 `test_discourse_move_ids.py` −1 `test_running_process_cwd_matches_its_plist[...]`, the case parametrized over the launchd jobs running at collection time. FAILED/ERROR node-id set IDENTICAL between the two branch runs and still a strict subset of the merge-base's.
- Full suite vs a throwaway `git worktree` at the merge-base `b57b612`, identical
  flags (`-p no:cacheprovider -p no:warnings --continue-on-collection-errors`):
  merge-base 55 failed / 10 errors / 1,755 passed; branch 54 failed / 10 errors /
  1,929 passed. `comm` over the sorted FAILED/ERROR node-id sets: **nothing fails on
  the branch that does not fail at the merge-base.** The single merge-base-only
  failure (`test_task_registry.py::…::test_limit_5000_accepted`) is an
  `httpx.ReadTimeout` against a live HTTP surface — environmental. The
  `tests/test_koi_flow_integration.py` collection error is pre-existing on both sides.
- **Revert proof, 12 core fixes:** each undone alone in the working tree, its tests
  run (all fail), file restored from HEAD, restored tree 82/82 green. The quality and
  migration subagents did the same for M1a/M1b/M1c/M2 and M8 (7 of 15 migration
  tests fail with the ledger row and set check removed).
- Read-only gate re-run on the three live michaelgarfield docs from their stored
  windows: all three exit 0 (`pass_after_preregistration` ×2, `pass`), 14→14 /
  13→13 / 13→13 bijective, semantic `review` (endpoint_integrity unmeasured in
  check-only mode is now correctly `review`, not a fabricated pass) — and the two
  pending essays print `-> Project`; the curated artifacts print `-> Document`.
- `render_extraction_contract.py --check` → all 4 surfaces match.
- Canon validator: not applicable (`scripts/validate_spec_dag.py` absent).

**Session `bb26783d`:**

- Branch `fix/document-ingest-integrity`; `git status --porcelain` empty;
  `git diff --check` clean; HEAD == `origin/fix/document-ingest-integrity`.
- **107** tests across the three directly-affected files (50 identity + 29 contract +
  28 fixtures) (the earlier '214' double-counted: the same 107 tests were passed to
  pytest twice).
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
| 2026-09-14 | Claude Code | `98bc9fe1` (koi-infra, continued) | **Re-review merge blocker closed: discourse-move id hash input restored.** Commit `97f6525`. The M6 rename had made the entity normalizer the uuid5 input for persistent discourse-move ids; read-only census showed 5,579/13,767 stored document moves (1,169 docs, 12 on the michaelgarfield replay targets) no longer matched, so a replay would have twinned them. Dedicated `discourse_move_id_key` (whitespace-only, historical) + `discourse_move_id`; 6 tests pinning literal UUIDs (5 fail at `d51fb41`, hyphen-free control passes on both sides); census after: **13,765/13,767 reproduced**, 21/21 target rows, 2 pre-existing June-2026 anomalies reported and untouched. Same commit: false M4 "preflight already refuses" comments corrected (preflight ignores an invalid alias decision → possible mint before the freeze refuses; deferred to #69), 1,046→1,045, `collect_endpoints` docstring, spec's stale "67 byte-identical" → strict subset. 179 tests across seven suites green (`--collect-only` = 179); full-suite failure set identical to the prior branch run (1,934 vs 1,929 passed is a different collection, +6 move-id items −1 launchd-parametrized case, not the same run). No live writes; PR #66 still draft. |
| 2026-09-14 | Claude Code | `98bc9fe1` (koi-infra) | **Review blockers B1–B4 / M1–M8 fixed in draft PR #66.** Four commits (`cdc445c` identity, `741ab0a` quality, `8c711be` gate, `79f1a21` migration 126), fixture-first, each core fix revert-proven, **zero regressions** vs the merge-base (54F/10E vs 55F/10E; +174 passing). B1: registration URL is absolute or a typed refusal — previously every first ingest died in httpx. B2: cross-type per endpoint. B3: frozen-map lookups by canonical key. **M3: untyped endpoints BLOCK (`type_undeclared`) instead of defaulting to Concept — 239/1,755 cached docs affected, operator's call.** M4: alias decisions validated at the freeze. M5: `fact_ids` + run-scoped verification. M6: one merge key (`api/extraction_merge.py`). M7: gate on the production merge, prints the type it would mint (`-> Project` from the stored windows — the B4 hazard, now visible). M1/M2/M8 fixed; B4 runbook order corrected (deploy BEFORE replay); curated payloads are gate-only validation artifacts; `curation.document_rid` checked; three shared handoff rows restored; spec 0.88→0.75 (`similarity_threshold`); PR now `Closes #62` / `Closes #68` (both link). No live writes. |
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
| 2026-09-03/04 | Claude Code | e1dd0df8 | **Vocabulary arc.** Backup had never been bootstrapped — now armed and proven unattended. `/entities/retype` made reversible after 142 irreversible merges. Launchd glob widened (2 violations). `restart.sh` false-ERROR fixed. E3 shipped, tripwire restored. 23 commits published; flood fix live after a second pull. **Five of my own claims overturned by measurement and corrected at every site.** |
| 2026-09-12 | Claude Code | `045186e8` (stream A: history-preserving website sensor) | **Steps 0–3 of `~/.claude/plans/history-preserving-website-sensor.md` DONE; steps 4–5 NOT STARTED.** Committed `da408e0`: migration 123 **APPLIED** (`assertion PASSED (claims=513, snapshot rows=511)`, one-shot — do not re-run, it exits 3 with an alarming-but-correct message once any document is ingested), its down file (export now runs, verified exit 3 on failure, placed below `ON_ERROR_STOP` deliberately), `koi-history` dispatcher (7 verbs, none built yet; unbuilt → exit 2), `koi-dump-ok` (full-read validator, both controls demonstrated). Fresh dump `personal_koi-step3-20260912-201553.dump` full-read verified. Postgres instruments on (`log_lock_waits`, `log_min_duration_statement=10s`, `log_checkpoints`, `log_autovacuum_min_duration=1s`). **Nothing in `scripts/ingest_document.py` or `api/routers/` is modified — a peer saw another session's `ingest_document.py` process and inferred step 4 was in flight; it was not.** Untracked `p.txt`/`:` are not mine. **NEXT (fresh session):** step 4 = `ingest_document.py` (see `~/.claude/plans/history-preserving-website-sensor-artifacts/q1_fixed_pair.md` — CANONICAL over `ingest_side_pair.md`); step 5 = `api/routers/history_router.py` + apply txn (`apply_transaction.md`, `endpoint_contracts.md`, `state_machine.md`). **Checkpoint after step 5, contract:** demonstrate the store's behaviour when the storage write SUCCEEDS but the bytes are WRONG, not only when it fails (a8751c7e's ask; their T2 corrupt-remote-preserving-size-and-mtime test is the specimen). Three protected fixtures: `document:656d1923…` live/8 chunks, `document:c3b0ebcd…` superseded/0, `document:58fe10e0…` superseded/0 — koi task `koi-2026-09-12-protected-versioning-fixtures`, DO NOT MODIFY. Task `koi-2026-09-13-045186e8-step5-checkpoint` due 2026-09-14. Plan is lint-green at ~740 lines with 22 parking-lot items; the machine gate for heavy work is in its Rollback section (gate on `aomhost` + swap growth, never `pgrep zoom.us` or `pages free` — both were unsatisfiable stalls). |
| 2026-09-11 | Claude Code | `919b51a0` (cross-stream, started in rage-research) | `restart.sh` cycled the service cleanly but did not clear the embedding-repair backoff, because `/health` reads the repair job's persisted state file (`EMBED_REPAIR_STATE`), not process memory; the fix is the repair job's own `--ignore-backoff`. Record in the rage-research handoff. |
