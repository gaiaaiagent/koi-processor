# Project handoff

**Updated:** 2026-08-22 19:58 PDT
**Session:** Claude Code · ffb7988e-3224-4372-8b24-e54e2d083a09 · Ontology safety rails → Meeting promotion
**Status:** Meeting promotion shipped end-to-end — 28 → 262 Meeting entities and 70 → 1,081 `attended` edges with **zero** date-mismatched, after a same-day resolver fix; all work pushed, working tree clean, 0 ahead.

## Completed this session

Three concurrent sessions worked this repo today (this one, `abb2c016`/d9, and `koi-wt-nate-federation`); 26 commits are on `origin/regen-prod`.

- **Meeting identity fix** (`d1be8ac`) — two meetings on different dates no longer resolve to the same entity. `normalize_entity_text` flattens `-` to a space, so `2026-01-28` became three ordinary tokens that *inflated* Jaro-Winkler similarity: the field that distinguishes meetings was what made them look alike. Measured damage: `entity_rid_mappings` 70 rows → 27 URIs (2.59×), and 93 of 151 determinable `attended` edges pointed at the wrong meeting. **The obvious fix — adding `Meeting` to `passes_distinctive_token_check` — was tested first and ACCEPTS all three collapse pairs**; a test pins that so it is not re-proposed. 20 tests, both directions.
- **93 false `attended` assertions deleted** after snapshot (`entity_relationships_backup_meeting_identity_20260822`, full rows). Decomposed before acting: 158 edges = 93 mismatched + 58 agreeing + **7 undeterminable**, the last deliberately left alone.
- **Meeting backfill** (`3cbee4d`, session d9) — +234 Meeting entities, +1,011 edges from a frozen 1,365-slot corpus. Independently verified here: **0 date-mismatched of 1,076 determinable**, mapping excess unchanged at 43 (zero new collapse, ratio 2.59× → 1.16×), 0 NULL embeddings.
- **Live-write gate expressed as a property** (`8eb0e32`, `0d21ec8`, `988da21`) — `tests/test_live_write_governance.py` derives its population from the filesystem and fails on any new ungoverned live writer. `pytest.ini` gained `testpaths`; `scripts/run-red-baseline-gate.sh` makes the gate a runnable command and gave `KOI_REQUIRE_BACKEND` its first caller.
- **Two fixture leaks closed** — `test_task_registry` (627 rows) and the intent suite; three backlogs purged with snapshots (612 tasks, 1,310 intents, 62 claims). The claims purge used a **claimant-identity** signature, not text: a naive `ILIKE '%test%'` would have deleted 93 genuine claims including six of Darren's own.
- **Migration 111 propagated** to `personal_koi_test` and to the NUC (`dobby@192.168.1.69`) — the NUC had neither column on a live 14,435-row registry, so a push before migrating would have broken its federation write path.

## Next steps

1. **koi 7878 — AC1's 24-hour window** on the intent-leak fix. Mechanism is proven; elapsed time is not. Resolves 2026-08-23 by observation. Deliberately left open.
2. **Historical Meeting mapping collapse** — 43 excess mappings from before the guard remain. Decide split-vs-leave now that correct entities exist alongside.
3. **`passes_token_overlap_check` is defined twice with DIFFERENT bodies** (`resolution_primitives.py:194` vs `personal_ingest_api.py:728`, the latter shadowing the import at `:160`). Eleven modules import the primitives. Two divergent implementations of the guard that decides entity identity.
4. Migration 112 (type consolidation) — needs ~1 week of `resolution_tier` data; ~Aug 29. Constraint from B0's refutation: **keep `Organization` a distinct core type**.
5. Two latent leakers in the gate allowlist (`test_interop_matrix.py`, and the 5 dormant `tests/*.sh`).

## Open questions

- Which caller stopped registering Meeting entities after the 2026-02-25 backfill — a one-off script, or a regressed pipeline path? Unresolved; the backfill worked around it rather than answering it.
- `meeting-bioregional-learning-bbd3da7e298c` is `Concept` in `entity_registry` but `Meeting` in `entity_rid_mappings`, with live `attended` edges. Which table is authoritative for Meeting identity?
- The "~10.2% of prompts are meeting-shaped" demand claim is **neither substantiated nor refuted** — 7.57% corpus-wide, 13.1% last month, single-rater classifier, and meeting is the smallest of three shapes measured. Do not build a case on it.
- Person duplicate mess: 1,034 of 4,770 live Person rows are bare single tokens. **Do not de-dup naively** — `Dave Bronner` carries the alias `dave`, so cleaning fragment rows would route all 22 "Dave" attendees to him at confidence 1.0 while the vault says 20 are David Fortson.

## Verification and working tree

- **Branch/status:** `regen-prod`, 0 uncommitted, **0 ahead of origin**, `git diff --check` clean.
- **Verification:** red-baseline gate **10/10 PASS** (`scripts/run-red-baseline-gate.sh`); live-write governance gate 4/4; Meeting identity 20/20; drift-retry 10/10. API healthy (PID 2535, restarted 18:58 to load the guard). Graph: 262 Meetings / 1,081 `attended` / 31,661 registry.
- **Canon validator:** not applicable — no `scripts/validate_spec_dag.py` in this repo.
- **Snapshots retained** (all deletes reversible): `entity_relationships_backup_meeting_identity_20260822`, `task_registry_backup_fixtures_20260822`, `intent_registry_backup_fixtures_20260822`, `intent_state_log_backup_fixtures_20260822`, `claims_backup_fixtures_20260822`, `claim_attestations_backup_fixtures_20260822`, `entity_registry_backup_intent_fixtures_20260822`.
- **Re-measure before acting.** Concurrent sessions write this database; `document_entity_links` grew 6,820 → 6,870 during one measurement window.

## Recent sessions

| Date | Provider | Session | Summary |
|---|---|---|---|
| 2026-08-22 | Claude Code | ffb7988e | Ontology safety rails → Meeting identity fix + promotion; 26 commits across 3 sessions |
