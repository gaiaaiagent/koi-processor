# PREREG — correction drill 001 (substrate-first membrane, walking skeleton)

**Status:** REGISTERED r3 — prereg-skeptic verdict **PROCEED** (conditional fix
applied in this revision; skeptic pre-approved the wording, no further cycle
required). Implementation may begin. **Registered:** 2026-08-26 ·
**Amended:** 2026-08-26 (skeptic BLOCK findings F1–F5, R1–R3, N1–N4) · **Plan:**
`~/.claude/plans/substrate-first-learning-membrane.md` v2.1; this drill is the
acceptance run of **Steps 2–4** (Step 1 is this registration) · Session 15367643.

This is an **integration-contract prereg**, not a measured-range experiment: the
expected values below are fixed by the contract by design. It deliberately does NOT
use the `registered-experiment` workflow, whose achievable-range gate rejects point
values fixed by construction. Each bar instead states how it can fail (a real
defect class) and why it can pass (a verified fixture property).

## 1. Frozen baseline (measured 2026-08-26; receipts in
`~/.claude/plans/substrate-membrane-step0-tree-map.md` r2; every row independently
re-verified live by prereg-skeptic)

| What | Value |
|---|---|
| Mac serving koi tree | `koi-processor-service` @ `regen-prod` = `74da823d3cd9bd44a4882a2ed2444db3ac0beb39`, clean, = origin |
| NUC serving koi tree | `/home/dobby/projects/RegenAI/koi-processor` @ `316f2e22be4b4951c3f90691e53d5553d7126578`, dirty ×10 |
| NUC content identity | diff `071364987b47e8f1b0483c8c0e89a5653b6b52436a8643407e4081b9067ad872` · manifest `8badaf389293adc2c8ae4279e11776e03454e2eb89a9c68d000dc95686fda3bc` |
| Dobby baseline | `main` @ `879aefd`, clean (baseline only — see Run header, §1a) |
| Canon repo/note | spore @ `32849c7`; `docs/research/connections/johar-recursive-intelligence.md`, clean at HEAD, sha256 `0787f30808fff320e43bf82e6f2598001752d6e0cd135cf444eabb1e7ef97a98` (spore tree has 335 unrelated dirty entries; the drill touches exactly this file) |
| Scored assertion | `spore:connection:johar-recursive-intelligence:C5` (line 125) |
| Negative-control assertion | `spore:connection:johar-recursive-intelligence:C1` (line 113) |
| Fixture chain (real, live; 3 nodes / 2 edges above root / 0 dangling; 3 supersedes edges DB-wide, third pair unrelated) | root `orn:koi-net.claim:a9ebdbee4e0c90913cf1813fdb24c76a` ← `orn:koi-net.claim:07ce17bc6c3004527b418f9dca9c69d3` ← chain head `orn:koi-net.claim:2c7738b48e91ee9a4420ed5f5f8c90be`; all `source_document=spore.connection.johar-recursive-intelligence`, `c_id=C5` |
| Chain semantics | restatement-evolution — exercises reconciliation/multi-hop/coalescing; invalidation is proven by the isolated arm |

### 1a. Run header (recorded at run start — F4)

The §1 table is the **baseline of the serving trees**, which must be UNMOVED at run
start. New code never runs as a moved serving tree; it runs as branch scripts and
explicitly deployed files. The run header records:

- `koi-membrane-wt` HEAD (the branch code actually executing), clean — or
  record `git diff --binary HEAD | sha256sum`, as the §1 NUC row models;
- dobby branch SHA **plus a NUC deploy receipt**: sha256 of each deployed briefing
  file on the NUC (NUC `.git` is decorative per the 2026-05-14 postmortem — content
  hashes, not commit claims);
- re-assertion that Mac serving tree = `74da823…` (clean) and NUC diff/manifest
  hashes = §1 values;
- fixture re-verification (R3): one psql receipt showing 2 edges above root /
  0 dangling / 3 supersedes edges DB-wide;
- **clean-slate precondition (F1):** on BOTH nodes, `canon_dependency_impacts`
  and `canon_review_cases` are empty AND
  `SELECT count(*) FROM task_registry WHERE source_type='canon-review'` = 0 —
  a direct DB query across ALL statuses, because the API's default listing
  excludes done/cancelled (`task_router.py:471`) and therefore cannot return
  the residue this precondition exists to exclude;
- **transport preflight (F5):** `~/.config/personal-koi/federation-check.sh`
  green + `GET /tasks/stats` succeeds on both nodes. Preflight failure → the run
  is **VOID** (abort; re-run permitted). It is not a bar failure. B5 failing
  *after* a green preflight is informative for the D7 class it names.

**Abort/re-pin rule:** any §1 pin or §1a precondition failing at run start voids
the run; re-pin and amend by commit before re-running. Bars change only by
amendment commit before a run — never after.

## 2. Scope to implement before the run (this branch)

Minimum for the drill: `claim_supersession_events` (deterministic
`source_event_key`, `parent_event_id`, backfill of exactly the 3 real
`supersedes_rid` edges as `unclassified`) · dependency-manifest frontmatter
projection → `canon_dependencies` · reconciliation (anti-join, `ON CONFLICT DO
NOTHING`) → `canon_dependency_impacts` (UNIQUE dependency + causal event) →
`canon_review_cases` (one open case per assertion, partial unique index) →
deterministic task projection (task key includes `case_id`,
`source_type=canon-review`) · Dobby: separate canon-review task query + Canon
Reviews brief section + watcher-health footer + `briefing.sh` dry-run mode.
The canonicalization registry / immutability guard ride this branch but are not
drill dependencies and are not scored here.

**Development containment (F1):** every development or debug execution of the
projector and reconciliation runs ONLY against `koi_drill_isolated`. The
production-arm run (§3) is **the first execution of reconciliation against
`personal_koi`**, evidenced by the §1a clean-slate receipt taken immediately
before it.

## 3. Production arm — procedure

1. Add the dependency manifest to the frozen spore note's frontmatter as a
   **single-file commit** (record commit SHA + post-edit file sha256):
   - `C5` → `evidenceDependencies: [orn:koi-net.claim:a9ebdbee4e0c90913cf1813fdb24c76a]` (scored arm — root of the chain)
   - `C1` → `evidenceDependencies: [orn:koi-net.claim:2c7738b48e91ee9a4420ed5f5f8c90be]` (negative control — chain head, zero supersessions above it)
2. Project the manifest into `canon_dependencies` on the Mac node (first and only
   production projector run).
3. Take the §1a run header + clean-slate + preflight receipts, then run
   reconciliation once; then re-run it 10 more times.
4. Observe task federation Mac → NUC; run the Dobby brief in **dry-run** on the NUC.

Authoritative watcher node: **MacBook**. Brief node: **NUC**. Transport: koi task
federation; the receipt is bar B5.

## 4. Bars — the drill FAILS if any of B1–B13 fails

Production arm:

| # | Expectation (exact) | Can fail via | Can pass because |
|---|---|---|---|
| B1 | Reconciliation yields **exactly 2** `canon_dependency_impacts` for the C5 dependency (causal events: a9eb→07ce, 07ce→2c77) | missing multi-hop (→1); absorbing the unrelated third DB edge or duplicates (→>2) | chain verified: exactly 2 edges above root; a third, unrelated edge exists DB-wide to catch over-collection |
| B2 | **Exactly 1** open `canon_review_case`, keyed to `…:C5` specifically | case-per-impact (→2); coalescer keyed by a constant (caught here by key inspection: the minted case's key ≠ `…:C5`) | key equality is scored, not just count |
| B3 | **Exactly 1** koi task; key contains the C5 `case_id` | task-per-impact; key omits case_id | deterministic projection contract; post-resolution reuse is separately tested by B13 |
| B4 | 10 reconciliation re-runs add **0** rows to impacts/cases/tasks | non-deterministic `source_event_key`; missing unique keys | idempotency is a schema property under test; §2 containment means these are the first production runs, not rehearsed ones |
| B5 | Task visible on NUC via `GET /tasks/?source_type=canon-review` within **30 min** of creation, receipt per §7; on failure the receipt also captures WG/tunnel state at failure time (distinguishes a D7-class defect from an ambient drop postdating the preflight — diagnosability only, the bar still fails) | D7-class silent no-op emitter; federation defect | preflight (§1a) was green, so ambient-transport failures void rather than fail |
| B6 | Dobby brief **dry-run** renders a Canon Reviews section naming the C5 case + health footer; **no vault write, no delivery** — instruments: pre/post sha256 of the brief vault-note paths on BOTH nodes (vault-sync CREATE/UPDATE asymmetry), plus a `journalctl -u dobby-gateway` window covering the run, grepped for send/vault-write markers | unfixed undated-task bucket gap; dry-run leaking delivery | the §2 Dobby change exists precisely to make this passable; absence claims carry named instruments (F3) |
| B7 | *(invariant check, limited detection power)* canon note untouched by watcher/brief: post-drill sha256 = post-manifest-commit sha256 | any auto-rewrite path | nothing in §2 writes to canon repos |
| B8 | **Negative control:** the C1→chain-head dependency yields **0** impacts, **0** cases, **0** tasks, and no C1 line in the brief | fire-on-everything watcher; provenance-based traversal (walking `source_document`/`c_id` instead of RID edges would fire for C1, whose dependency claim is `c_id=C5`) | chain head verified to have zero supersessions above it; premise transitively frozen by the §1a psql receipt |

Isolated arm (`koi_drill_isolated` only; per-assertion case counts asserted in the
shared isolated DB):

| # | Expectation | Proves |
|---|---|---|
| B9 | Semantic-correction fixture (P superseded by ¬P, `kind: correction`) → 1 impact + 1 case whose recorded resolution path is `update_canon` | actual invalidation, which the production chain does not carry |
| B10 | Rejection fixture: immutable rejection event → impact; a later approval event changes case resolution while the rejection row persists | verdict history is consumed immutably (D9) |
| B11 | The same supersession event replayed 10× → exactly **1** impact row | deterministic `source_event_key` dedup |
| B12 | 10 distinct upstream changes → **10** impacts, **1** case, **1** task | coalescing under real fan-in |
| B13 | Containment (R2): resolve the B9 case, inject 1 further distinct change → a **NEW** case + **NEW** task; the completed task is untouched | `case_id`-scoped task keys prevent silent reuse |

Typed-null enforcement is NOT scored here: its enforcing surface is the Step-7
mapping-cell schema (plan §3.10 / AC6), where it will be registered.

A failed drill is a finding, not a reason to adjust bars post hoc.

## 5. Prohibitions

- **No fabricated events in the production DB.** Production writes are limited to:
  the 3-edge `unclassified` backfill (mirroring existing `supersedes_rid` rows),
  `canon_dependencies` rows from the §3.1–2 manifest projection, membrane-table
  rows produced by the §3.3 reconciliation runs, and koi tasks projected from
  cases. No synthetic supersessions, no synthetic attestations, no claim mutations.
- **No development executions against `personal_koi`** (§2 containment), and
  **no production task with `source_type=canon-review` may be created before the
  run** — dobby brief-section development uses the repo's test fixtures/mocks,
  never the production task registry.
- No message delivery of any kind from drill runs (dry-run only).
- Isolated-arm fixtures never touch `personal_koi`.

## 6. Receipt convention (every bar, both arms)

Each receipt records: **host · surface/path · exact command · exit code · content
identity (sha256 / RID / taskKey+case_id) · timestamp.** A bare measured value is
not a receipt. Absence claims name the instrument that can return absence.

## 7. Outcome recording

Results land in `docs/membrane/DRILL-001-RESULTS.md` (same branch) with per-bar
receipts, the §1a run header, and the production case's recorded resolution
(`unaffected` or `update_canon`; recorded, not scored).
