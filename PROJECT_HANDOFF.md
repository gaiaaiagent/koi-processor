# Project handoff

**Updated:** 2026-08-23 00:10 PDT
**Session:** Claude Code · ffb7988e-3224-4372-8b24-e54e2d083a09 · Ontology safety rails → Meeting promotion → historical repair
**Status:** All executable priorities are done and pushed (`38c11fe`). Meeting graph is 302 entities / 1,261 edges with **zero** cross-date groups and **zero** date-mismatched edges. Only two clock-gated items remain, plus one design decision on the resolver shadow gate.

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

Three concurrent sessions worked this repo today (this one, `abb2c016`/d9, and `koi-wt-nate-federation`); 26 commits are on `origin/regen-prod`.

- **Meeting identity fix** (`d1be8ac`) — two meetings on different dates no longer resolve to the same entity. `normalize_entity_text` flattens `-` to a space, so `2026-01-28` became three ordinary tokens that *inflated* Jaro-Winkler similarity: the field that distinguishes meetings was what made them look alike. Measured damage: `entity_rid_mappings` 70 rows → 27 URIs (2.59×), and 93 of 151 determinable `attended` edges pointed at the wrong meeting. **The obvious fix — adding `Meeting` to `passes_distinctive_token_check` — was tested first and ACCEPTS all three collapse pairs**; a test pins that so it is not re-proposed. 20 tests, both directions.
- **93 false `attended` assertions deleted** after snapshot (`entity_relationships_backup_meeting_identity_20260822`, full rows). Decomposed before acting: 158 edges = 93 mismatched + 58 agreeing + **7 undeterminable**, the last deliberately left alone.
- **Meeting backfill** (`3cbee4d`, session d9) — +234 Meeting entities, +1,011 edges from a frozen 1,365-slot corpus. Independently verified here: **0 date-mismatched of 1,076 determinable**, mapping excess unchanged at 43 (zero new collapse, ratio 2.59× → 1.16×), 0 NULL embeddings.
- **Live-write gate expressed as a property** (`8eb0e32`, `0d21ec8`, `988da21`) — `tests/test_live_write_governance.py` derives its population from the filesystem and fails on any new ungoverned live writer. `pytest.ini` gained `testpaths`; `scripts/run-red-baseline-gate.sh` makes the gate a runnable command and gave `KOI_REQUIRE_BACKEND` its first caller.
- **Two fixture leaks closed** — `test_task_registry` (627 rows) and the intent suite; three backlogs purged with snapshots (612 tasks, 1,310 intents, 62 claims). The claims purge used a **claimant-identity** signature, not text: a naive `ILIKE '%test%'` would have deleted 93 genuine claims including six of Darren's own.
- **Historical Meeting repair + resolver split** (`38c11fe`, a later session) — 262 → **302** Meetings, 1,081 → **1,261** edges, 379 pending; mappings 304 rows / **301** URIs / 3 intentional same-date excess. The duplicated resolver guard now has explicit `legacy`/`strict` names with caller behavior preserved, and bounded shadow measurement runs at 10% sampling behind a 1,024-entry non-blocking queue. All six latent live-writers governed; allowlist down to the two non-persisting exceptions. Independently re-derived here: every figure exact, 0 cross-date groups, 0 date-mismatched edges.
- **Migration 111 propagated** to `personal_koi_test` and to the NUC federation peer — the NUC had neither column on a live 14,435-row registry, so a push before migrating would have broken its federation write path.

## Next steps

1. **koi 7878 — AC1's 24-hour window.** Evaluate no earlier than **2026-08-23 13:47 PDT**. Currently 0 nonconforming and 0 orphaned Intents. Pass = no post-cutover rows, or every new row has non-null `resolution_tier`, `source='intent-registry'`, and an `intent_key`. Positive control: the 225-row backup cohort.
2. **DECIDE: make replay the primary evidence for the resolver shadow gate.** Phase 6 requires 1,000 sampled attempts reaching a permissive guard boundary, at 10% sampling. Organic entity creation runs **~60/day** (the 620/713/1,018 days were backfills, not organic), so that is **~6 observations/day → roughly 170 days**; zero have been emitted so far. The plan already contemplates "a deterministic replay fixture" — promoting replay to primary evidence produces the same counterfactual outcome data in a day, at no production cost, and covers zero-traffic callers that live sampling will never reach. Otherwise the gate never fires and the legacy/strict wrapper state becomes permanent **by default rather than by choice** — which is an acceptable outcome, but should be chosen.
3. **Migration 112** — eligible after **2026-08-29 10:05 PDT**, after 7 days of organic `resolution_tier` data. Report the Meeting backfill burst separately from organic traffic. Constraint from B0's refutation: **keep `Organization` a distinct core type**; no Person deduplication.
4. Minor: confirm the backup for the four stamped knowledge-add rows — the end state is verifiable (310 rows, zero NULL tiers) but no matching backup table was locatable.

## Open questions

- Which caller stopped registering Meeting entities after the 2026-02-25 backfill — a one-off script, or a regressed pipeline path? Unresolved; the backfill worked around it rather than answering it.
- `meeting-bioregional-learning-bbd3da7e298c` is `Concept` in `entity_registry` but `Meeting` in `entity_rid_mappings`, with live `attended` edges. Which table is authoritative for Meeting identity?
- The "~10.2% of prompts are meeting-shaped" demand claim is **neither substantiated nor refuted** — 7.57% corpus-wide, 13.1% last month, single-rater classifier, and meeting is the smallest of three shapes measured. Do not build a case on it.
- Person duplicate mess: 1,034 of 4,770 live Person rows are bare single tokens. **Do not de-dup naively** — `Dave Bronner` carries the alias `dave`, so cleaning fragment rows would route all 22 "Dave" attendees to him at confidence 1.0 while the vault says 20 are David Fortson.

## Verification and working tree

- **Branch/status:** `regen-prod`, 0 uncommitted, **0 ahead of origin**, `git diff --check` clean.
- **Verification:** red-baseline gate **10/10 PASS** (`scripts/run-red-baseline-gate.sh`); live-write governance 4/4; Meeting identity 20/20; drift-retry 10/10; 78 focused tests pass. API healthy (PID 22784). Graph as of 2026-08-23 00:10: **302** Meetings / **1,261** `attended` / **379** pending / 31701 registry rows.
- **Canon validator:** not applicable — no `scripts/validate_spec_dag.py` in this repo.
- **Snapshots retained** (all deletes reversible): `entity_relationships_backup_meeting_identity_20260822`, `task_registry_backup_fixtures_20260822`, `intent_registry_backup_fixtures_20260822`, `intent_state_log_backup_fixtures_20260822`, `claims_backup_fixtures_20260822`, `claim_attestations_backup_fixtures_20260822`, `entity_registry_backup_intent_fixtures_20260822`.
- **Re-measure before acting.** Concurrent sessions write this database; `document_entity_links` grew 6,820 → 6,870 during one measurement window.
- **The 3 residual excess Meeting mappings are INTENTIONAL, not debt.** After the repair, `entity_rid_mappings` is 304 rows / 301 URIs. The excess of 3 is three `(canonical_uri, date)` groups holding more than one artifact for the *same* meeting on the *same* date — transcripts and note variants — which correctly share one Meeting entity. Anyone seeing "excess: 3" should not try to drive it to zero. Verified 0 cross-date groups and 0 date-mismatched edges as of 2026-08-23 00:07.
- **Two dateless Meeting mappings are preserved deliberately:** `Meetings/Bioregioning/Bioregional Learning.md` and `Meetings/Cascadia Canada/Cascadia Canada Sync Sept 24.md`. Both are singletons. Do not infer a year from partial text like "Sept 24".

## Recent sessions

| Date | Provider | Session | Summary |
|---|---|---|---|
| 2026-08-22 | Claude Code | ffb7988e | Ontology safety rails → Meeting identity fix + promotion; 26 commits across 3 sessions |
| 2026-08-23 | Claude Code | (fresh) | Historical Meeting repair, resolver legacy/strict split, live-writer governance (`38c11fe`) |
