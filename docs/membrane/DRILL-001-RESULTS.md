# DRILL-001 RESULTS — correction drill (walking skeleton)

**Prereg:** `PREREG-correction-drill-001.md` @ r3 `71f10dd` + amendment `7a8271d`
(re-pin) · **Run date:** 2026-08-26 · **Verdict: ALL 13 BARS PASS (B1–B13)**

Receipt convention (§6): host · surface/path · command · exit code · content
identity · timestamp. All timestamps UTC.

## Run attempts

- **Attempt 1 — VOIDED at run start** (22:35:47Z): Mac serving tree had moved
  `74da823` → `a9487f0` (one docs-only commit, verified by `diff --stat`).
  Voided per the §1a abort rule before any reconciliation; re-pinned by prereg
  amendment commit `7a8271d`. No production writes occurred under the void.
- **Attempt 2 — the registered run** (header 22:36:51Z; first reconciliation
  22:37:26Z).

## Run header (attempt 2, all green)

| Pin | Value | Receipt |
|---|---|---|
| Mac serving tree | `a9487f00…` clean = origin, dirty 0 | MacBook · git rev-parse/status · 22:36:51Z |
| Executing worktree | `koi-membrane-wt` @ `7a8271d` clean | same |
| Dobby branch | `feat/canon-review-brief` @ `d2dd2e5` | MacBook · git rev-parse |
| NUC deploy receipt | `scripts/briefing.sh` sha256 `dc42730a8938c9d7951a48d35852def02621fb72455e80278bf35d5e95729471` — identical Mac + NUC | scp + sha256sum both hosts · 22:34Z |
| NUC serving tree | `316f2e2`, diff sha256 `07136498…ad872` — EXACT §1 match | NUC · git diff --binary \| sha256sum · 22:35:52Z |
| Spore manifest | single-file commit `2d5d1c5` (+7 lines); post-edit note sha256 `06a9beb92ab08f726b8cd27b190eb6c97f604f4f703b021b3f7a4179e9cf2ff8` | spore · git show --stat |
| Fixture re-verify | edges_above_root=2 · dangling=0 · db_wide=3 | MacBook · psql personal_koi · 22:36:51Z |
| Clean slate (Mac) | impacts=0 · cases=0 · canon tasks ALL statuses=0 (direct `task_registry` count) | psql · 22:36:51Z |
| Clean slate (NUC) | impacts=0 · cases=0 · canon tasks ALL statuses=0 | ssh psql · 22:37:17Z |
| Preflight transport | federation-check.sh: NUC→Mac peer OK, WG UP both, no NUC warnings; /tasks/stats rc=0 both nodes | 22:36:51–22:37:17Z |

Development containment held: every projector/reconciler development execution
ran against `koi_drill_isolated`; the 22:37:26Z reconciliation was the FIRST
against `personal_koi` (clean-slate receipts immediately prior).

## Production-arm bars

| Bar | Verdict | Receipt |
|---|---|---|
| **B1** exactly 2 impacts for C5, causal events = the two chain supersessions | **PASS** | psql 22:37:27Z: `B1_impacts_C5\|2`; impact rows list exactly `a9eb…→07ce…` and `07ce…→2c77…` |
| **B2** exactly 1 open case keyed `…:C5` | **PASS** | case_id=1, slug `spore:connection:johar-recursive-intelligence:C5`, sole row |
| **B3** exactly 1 task, key contains case_id | **PASS** | `canon-review::spore:connection:johar-recursive-intelligence:C5::case-1` (status inbox) |
| **B4** 10 re-runs add 0 rows | **PASS** | impacts/cases/tasks 2/1/1 before (22:37:43Z) and after 10 runs, rc=0 |
| **B5** task on NUC ≤30 min after green preflight | **PASS — 5m08s** | created 22:37:26Z (Mac ingest); visible 22:42:34Z via NUC `GET /tasks/?source_type=canon-review`; full row incl. context `canon-watch last_scan=… case=1 impacts=2` |
| **B6** dry-run brief renders case + footer; no vault write, no send | **PASS** (attempt 2, 22:43:34–22:54Z, service env sourced). Attempt 1 (22:42:53Z) had no OAuth token — all investigators rc=2, fallback text; the dry-run GATE still worked (delivery skipped, rc=0); execution-env error, not a bar failure, brief re-runs unrestricted by §2. | dryrun.md line 110 `## Canon Reviews` naming the C5 case + context; line 116 footer `_canon-watch: 1 open review(s) — last_scan=… case=1 impacts=2 watcher=reconcile_canon.py_`. Absence instruments: NUC brief-note sha256 post == pre (`4e500263f42621a140726dd5…`); Mac brief note absent pre AND post; journalctl -u dobby-gateway window since 22:42:52Z → 0 send/vault-write markers |
| **B7** canon note untouched (invariant check) | **PASS** | post-drill sha256 `06a9beb92ab08f726b8cd27b190eb6c97f604f4f703b021b3f7a4179e9cf2ff8` == post-manifest-commit hash (22:54:40Z) |
| **B8** C1 negative control: 0 impacts / 0 cases / 0 tasks / no brief line | **PASS** | `B8_C1_impacts\|0`; only case/task are C5's; `grep -c ':C1'` on the rendered brief = 0 |

## Isolated-arm bars (koi_drill_isolated)

**B9–B13 ALL PASS** — 9/9 tests green at worktree `7a8271d`
(`pytest tests/test_canon_watch_drill.py`, 22:38:43Z, rc=0), covering: B9
semantic correction (impact + case + `update_canon` resolution) · B10 rejection
immutability (row persists through approval-as-resolution) · B11 replay ×10 →
1 event / 1 impact · B12 ten distinct changes → 10 impacts / 1 case / 1 task ·
B13 containment (resolved case + new change → NEW case + NEW task, completed
task untouched) · plus B1-shape multi-hop, B8-shape decoy (business-key
traversal yields nothing), per-assertion coalescing, B4-shape idempotency.
(Earlier prose said "10 tests"; the file holds 9 test functions.)

## Environment findings (not bars)

1. **Mac/NUC claims divergence:** migration 114's backfill mirrored 3 supersedes
   edges on the Mac but **70 on the NUC** — the NUC claims table holds real
   supersession history the Mac lacks. Nothing fabricated (all rows mirror
   `supersedes_rid` data); reconciliation runs only on the Mac, so bars
   unaffected. Same defect family as the 30-vs-28 `allowed_entity_types` drift,
   larger. Follow-up candidate: claims-table federation reconciliation.
2. **Redundant federation events:** each ingest upsert emits a domain event —
   11 `canon-review` events queued for 1 task (1 create + 10 re-run updates).
   Harmless (idempotent apply) but worth a dedup-at-emit later.
3. **Serving-tree churn:** 4 HEAD moves in one day; the abort rule fired once
   and worked as designed.

## Test-suite gate (AC10-style)

- **dobby:** branch vs `main` identical — 30 failed / 436 passed on BOTH
  (baseline == current; failures are Mac-venv environment class, suite is green
  on the NUC). Focused tests: 2/2 new briefing tests pass (the end-to-end
  dry-run test runs ~2m50s).
- **koi-processor:** `tests/test_koi_flow_integration.py` fails at COLLECTION
  on this venv (pre-existing RIDType double-registration importing from the
  sibling `koi-sensors-runtime` clone; branch diff on that file = 0 lines) —
  excluded with this documentation. Suite with exclusion: 44 failed / 1622
  passed / 139 skipped / 13 xfailed / 9 errors (6m56s). External review had
  reported ~43 red. **Attribution complete:** a second identical-tree run gave
  43 FAILED + 9 ERROR (±1 flake vs the first run's 44, itself proving
  non-attribution); the full failure-name list contains ZERO membrane files;
  the 6 lines matching the word "canon" are `test_type_canonicalization_and_
  annotation.py` — the pre-existing entity-type work, untouched by this branch.
  The 9 drill tests passed inside the suite. Gate holds.

## Case resolution (recorded, not scored)

**Deliberately left OPEN, pending operator disposition** — the architecture
reserves Disposition for the human, and exercising it by agent at its first
occurrence would breach the rule the drill exists to install. Expected verdict
when Darren disposes: `unaffected` (the chain is restatement-evolution of the
note's own C5, whose current text the note already carries). Until then the
case surfaces organically in the real morning brief's Canon Reviews section —
a live continuation of B6. To resolve: set the case's status/resolution and
mark task `canon-review::…::case-1` done.

## Post-drill state

Reconciliation remains COLD (no timer, no schedule) — generalization is plan
Step 8, gated on this drill. The one open case + task are real and awaiting
disposition. `koi_drill_isolated` retained for Step-8 development (drop when
done).
