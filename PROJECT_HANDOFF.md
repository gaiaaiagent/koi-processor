# Project handoff

**Updated:** 2026-09-02 22:50 PDT
**Session:** Claude Code · 72cf052b-64fd-4382-b6a7-d74795f79ee8 · Phase 0/1 pipeline hardening — guards, merge reversibility, veto enforcement
**Status:** Tree clean on `regen-prod`, **12 commits ahead of origin** (unpushed). Phase 0 complete and Phase 1 substantially landed: backup restore-verified, `unmerge` live and proven across 57 real merges, `entity_non_match` enforcing at six resolver tiers, credential + persona guards shipped, `:8351` LAN hole closed and A/B-verified. Focused suite **3 failed / 72 passed / 2 skipped**; the 3 are pre-existing 401-vs-503 auth failures, proven pre-existing by stashing.

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

- **Backups exist and restore.** Nightly `pg_dump` job (`scripts/koi_backup.sh` + plist) — the 27GB primary previously had none. Restore **verified**: 11GB dump, data-only restore in 17 min, all six tables populated, `knowledge_facts` restored at **59,877 exactly matching the audit's figure**. Three guard bugs found by testing, not review: the size floor was calibrated on the NUC's dumps (would have passed a 1.29GB truncation), `stat -f%z` returns **nothing** on this machine (`/usr/local/bin/stat` shadows `/usr/bin/stat`), and `ls|grep|head` aborted the script on first run under `pipefail`.
- **Merges are reversible.** Migration 118 + `capture_reversal()` before rewiring + `POST /entities/unmerge`. Used in anger the same night: **57 merges, 57 with capture, 0 refused, 0 failed.** The 262 pre-118 merges are irreversible and unmerge **refuses** them — `rewired` recorded counts, not identities, so a best-effort undo would manufacture wrong provenance while reporting success.
- **`entity_non_match` veto: 44 adjudicated pairs, enforcing at all six accept tiers** of `resolve_entity()`. The obvious bridge (synthesise the query URI) would have silently missed **28%** of the register — 21 of 75 vetoed URIs don't reproduce from their current (name, type), including two of the five operator overrides. The lookup goes through the registry instead.
- **Credential write-path guard** (`api/secret_guard.py`): 8/9 real credential facts caught, **0.010% false positives across 60,122 facts**. Calibrated against the *real* retracted values — all three `HAS_TOKEN`s are 32-char lowercase hex, which the obvious "mixed character classes" test misses entirely. Found a live token (`USES_TOKEN` on project-node-2) the predicate-name cleanup had missed.
- **Persona merge guard**: refuses merging away a `(via X)` row whose principal has no independent row. **30 rows at risk** (21 Person + 9 Claim); `Clare Brodeur (via Hylo)` is the only Brodeur in the graph.
- **`:8351` LAN hole closed.** Both old `pf` rules were scoped `on utun0`, so plain-LAN traffic was never evaluated. Fixed rule verified by A/B from the NUC: **200 over WireGuard, timeout over LAN, same host and service**. Generator + its assertion also fixed — the old check passed on the very ruleset it existed to catch.
- **Type-mismatch void closed.** 1,023 tasks, owner=None, 95 days, still accruing. Cancelled 49 dead-type ones (migration 112 retired `Place`/`SoftwareApplication` and nothing told the extractor); converted the writer to one rolling task per class counted **from** `document_extraction_item_errors`, which was already the second copy.

## Next steps

1. **Push the 12 commits** (operator-present gate). `regen-prod` takes direct pushes by design.
2. **Deploy the two parked sensor fixes** to `koi-sensors-runtime` (`git pull`): the email From-name guard and the empty-turn-pair chunk fix. Both are live-behaviour changes to jobs on 1800s/continuous schedules. **Migration 116 is blocked on the second one** — applying it before the chunker fix ships breaks session ingestion (79–91% of daily chunks currently violate it).
3. **NUC cutover**: memo refreshed at `~/.claude/plans/nuc-koi-reconciliation-refresh-2026-08-31.md`; execution still parked pending operator go. `aa4be29` is now off-machine as `origin/nuc-runtime-filtered`.
4. *(structural, unscheduled)* **Ontology generator (audit D4b)** — generate the extraction prompt enum from `allowed_entity_types`. ⚠ **Corrected 2026-09-03: the "resolves the 518 Concept-absorption tasks as a side effect" half of this was FALSE** and is retracted. The extractor already emits `Protocol` freely (336 times) — the enum was never the constraint. The cause is a typed Tier-1 miss falling through to an **untyped** lookup that accepts any type (`api/routers/knowledge_router.py:1253-1288`), where the cross-type refusal fires only when the *hint* is `Concept`, never when the existing *row* is. Regenerating the enum resolves none of them. Nor are they uniformly non-triage: across 621 occurrences / **215 distinct entities**, ~46 (`Person`/`Organization`/`Location` stored as `Concept`) are real defects, ~176 (`Protocol`/`Project`) are a genuine types-vs-facets modelling question, and the rest want a type neither side proposed. The generator is still worth doing on its own merits — the vocabulary has 9 disagreeing copies and nothing enforces `allowed_entity_types` — just not for that reason. Same correction applied to the task-context string in `scripts/extract_deep_documents.py`; it also stands in `c3869af`'s commit message, which is history and cannot be amended.

## Open questions

- **The proposed Organization→Person natural experiment does not work.** That class is `substack-corpus` (46) + `document` (14), **zero email-sourced**, and last accrued 2026-08-20 — 13 days before the email guard existed. Post-deploy silence would read as confirmation of a guard with no causal path to it. To measure that guard, watch Person-creation rate from `email-sensor` in `entity_registry.source`.
- 17 rows of the do-not-merge register remain unseedable (entities named by id, display name, or as a class like "any Clare/Claire row"). They print on every seeder run by design — do not suppress.
- `KOI_CLAIMS_SERVICE_TOKEN` is populated and the HTTP merge path works; an earlier "it's empty" reading here was a misread of a grep that could only ever print variable names.

## Verification and working tree

- **Branch/status:** `regen-prod`, working tree clean, `git diff --check` clean, 12 ahead of `origin/regen-prod`.
- **Tests:** focused suite 3 failed / 72 passed / 2 skipped. The 3 (`test_knowledge_router_facts_gate.py`) are pre-existing `401 != 503` auth failures — confirmed pre-existing by stashing this session's changes and re-running. The 2 skips are live-data assertions that correctly skip on `personal_koi_test`.
- **⚠ Migrations must be applied to BOTH `personal_koi` and `personal_koi_test`.** `tests/conftest.py:41` rewrites `POSTGRES_URL` to the test DB; applying 115 only to the live DB left 19 tests failing on a missing column. 115/117/118 are applied to both; 116 is written and deliberately **not** applied anywhere.
- **Live:** service PID from 22:24 restart, healthy, 160 routes incl. `/entities/unmerge`. 44 vetoes, 57 reversible merges, 49 tasks cancelled.
- **Canon validator:** not applicable — no `scripts/validate_spec_dag.py`.

## Recent sessions

| Date | Provider | Session | Summary |
|---|---|---|---|
| 2026-08-24/25 | Claude Code | b289ac1e | Executed the full prior plan: 9 sweep fixes, Meeting-notes repair (270 notes), migration 112 complete (421→0). Established `koi-sensors-runtime` + hardened the launchd guard. |
| 2026-08-26 | Claude Code | b289ac1e (resumes) | 3rd conflict wave cleaned; built `com.personal-koi.vault-conflict-sweep`; fixed a real `ThrottleInterval < StartInterval` bug + added a mutation-tested anti-storm pin; fixed `test_koi_flow_integration.py`'s months-stale collection failure. |
| 2026-08-26 | Claude Code | c1defaa8 | **Verification pass.** Found A7 unexecutable, a gating inconsistency, A3's live blast radius; caught the conflict cleanup incomplete at 101 files; named the iCloud root cause; canary-proved the sweep fires unattended. No code changes. |
| 2026-09-01/02 | Claude Code | 72cf052b | **Phase 0/1 hardening.** Backup + verified restore; merge reversibility (`unmerge`, used on 57 merges); `entity_non_match` seeded (44) and enforcing at 6 tiers; credential + persona guards; `:8351` LAN hole closed and A/B-verified; type-mismatch void closed. 12 commits. |
