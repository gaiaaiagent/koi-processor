# PREREG — correction drill 001 (substrate-first membrane, walking skeleton)

**Status:** REGISTERED — pending prereg-skeptic PROCEED. No implementation run
before that verdict. **Registered:** 2026-08-26 ~14:45 PDT · **Plan:**
`~/.claude/plans/substrate-first-learning-membrane.md` v2.1, Steps 1–4 ·
**Session:** 15367643.

This is an **integration-contract prereg**, not a measured-range experiment: the
expected values below are fixed by the contract by design. It is deliberately NOT
run through the `registered-experiment` workflow, whose achievable-range gate
rejects point values fixed by construction. Each bar instead states how it can
fail (a real defect class) and why it can pass (a verified fixture property).

## 1. Frozen environment (measured 2026-08-26, receipts in
`~/.claude/plans/substrate-membrane-step0-tree-map.md` r2)

| What | Value |
|---|---|
| Mac serving koi tree | `koi-processor-service` @ `regen-prod` = `74da823d3cd9bd44a4882a2ed2444db3ac0beb39`, clean, = origin |
| NUC serving koi tree | `/home/dobby/projects/RegenAI/koi-processor` @ `316f2e22be4b4951c3f90691e53d5553d7126578`, dirty ×10 |
| NUC content identity | diff `071364987b47e8f1b0483c8c0e89a5653b6b52436a8643407e4081b9067ad872` · manifest `8badaf389293adc2c8ae4279e11776e03454e2eb89a9c68d000dc95686fda3bc` |
| Dobby | `main` @ `879aefd`, clean |
| Canon repo/note | spore @ `32849c7`; `docs/research/connections/johar-recursive-intelligence.md`, **clean at HEAD**, sha256 `0787f30808fff320e43bf82e6f2598001752d6e0cd135cf444eabb1e7ef97a98` (spore worktree has 335 unrelated dirty entries; the drill touches exactly this one file) |
| Canon assertion | `spore:connection:johar-recursive-intelligence:C5` ("Context legibility — …") |
| Fixture chain (real, live, verified 3 nodes / 2 edges / 0 dangling) | root `orn:koi-net.claim:a9ebdbee4e0c90913cf1813fdb24c76a` ← `orn:koi-net.claim:07ce17bc6c3004527b418f9dca9c69d3` ← `orn:koi-net.claim:2c7738b48e91ee9a4420ed5f5f8c90be`; all `source_document=spore.connection.johar-recursive-intelligence`, `c_id=C5` |
| Chain semantics | restatement-evolution (identical statement prefixes) — exercises reconciliation/multi-hop/coalescing/`unaffected`, NOT invalidation (isolated arm covers that) |

**Abort/re-pin rule:** if at run start the Mac serving SHA ≠ `74da823…` or the NUC
diff/manifest hashes differ, re-pin and amend this table in a follow-up commit
before running. A run against unpinned content is void.

## 2. Scope to implement before the run (this branch)

Minimum for the drill: `claim_supersession_events` (with deterministic
`source_event_key`, `parent_event_id`, backfill of exactly the 3 real
`supersedes_rid` edges as `unclassified`) · dependency-manifest frontmatter
projection → `canon_dependencies` · nightly-style reconciliation (anti-join,
`ON CONFLICT DO NOTHING`) → `canon_dependency_impacts` (UNIQUE dependency +
causal event) → `canon_review_cases` (one open case per assertion, partial unique
index) → deterministic task projection (task key includes `case_id`,
`source_type=canon-review`) · Dobby: separate canon-review task query + Canon
Reviews brief section + watcher-health footer + `briefing.sh` dry-run mode.
The canonicalization registry / immutability guard are on this branch but are NOT
drill dependencies and are not scored here.

## 3. Production arm — procedure

1. Add the dependency manifest to the frozen spore note's frontmatter
   (`canonAssertion: spore:connection:johar-recursive-intelligence:C5`,
   `evidenceDependencies: [orn:koi-net.claim:a9ebdbee4e0c90913cf1813fdb24c76a]`)
   as a **single-file commit** in spore; record the commit SHA and post-edit file
   sha256.
2. Project the manifest into `canon_dependencies` on the Mac node.
3. Run reconciliation once; then re-run it 10 more times.
4. Observe task federation Mac → NUC; run the Dobby brief in dry-run mode on the
   NUC.

**Authoritative watcher node: MacBook** (canon repos + primary KOI live there).
**Brief node: NUC.** Transport: existing koi task federation; the federation
receipt is part of bar B5.

## 4. Preregistered bars (production arm)

| # | Expectation (exact) | Can fail via | Can pass because |
|---|---|---|---|
| B1 | Reconciliation yields **exactly 2** `canon_dependency_impacts` rows for the dependency, causal events = the two supersessions (a9eb→07ce, 07ce→2c77) | missing multi-hop traversal (→1); spurious self/duplicate events (→>2) | chain verified: exactly 2 supersession edges above the root |
| B2 | **Exactly 1** open `canon_review_case` for `…:C5` | case-per-impact bug (→2) | coalescing keyed by assertion, preregistered |
| B3 | **Exactly 1** koi task; its key contains the `case_id` | task-per-impact; key omits case_id (silent reuse later) | deterministic projection contract |
| B4 | 10 reconciliation re-runs add **0** rows to impacts/cases/tasks | non-deterministic `source_event_key`; missing unique keys | idempotency is a schema property under test |
| B5 | Task visible on NUC via `GET /tasks/?source_type=canon-review` within **30 min**, receipt recorded | D7-class silent no-op emitter; WG down; federation lag | task federation is live infrastructure (historical receipts exist) |
| B6 | Dobby brief **dry-run** renders a Canon Reviews section naming the case + health footer; **no vault write, no Telegram send** | unfixed bucket gap (undated tasks invisible); dry-run leaking delivery | the Dobby change in §2 exists precisely to make this passable |
| B7 | Canon note file untouched by any watcher/brief process: post-drill sha256 equals the post-manifest-commit sha256 | any auto-rewrite path | nothing in §2 writes to canon repos |

The drill **fails** if any bar fails. A failed drill is a finding, not a reason to
adjust bars post hoc; bars change only by amendment commit before a re-run.

## 5. Isolated arm — `koi_drill_isolated` DB only

Created fresh from the branch migrations; dropped after. Fixtures:
- **Semantic correction:** claim P superseded by ¬P (`kind: correction`) → impact +
  case whose expected resolution is `update_canon`.
- **Rejection:** immutable rejection event → impact; a later approval event changes
  case resolution, never erases the rejection row.
- **AC4 arm A:** the same supersession event replayed 10× → exactly 1 impact row.
- **AC4 arm B:** 10 distinct upstream changes → 10 impacts, 1 case, 1 task.
- **Typed-null fixtures:** all four null types (`unknown/not_documented/
  not_observed/not_applicable`) accepted; blank cell rejected.

## 6. Prohibitions

- **No fabricated events in the production DB.** Production INSERTs are limited
  to: the 3-edge `unclassified` backfill (mirroring existing `supersedes_rid`
  data), membrane-table rows produced by reconciliation, and one koi task.
  No synthetic supersessions, no synthetic attestations, no claim mutations.
- No message delivery of any kind from drill runs (dry-run only).
- Isolated-arm fixtures never touch `personal_koi`.

## 7. Receipt convention (applies to every bar)

Each receipt records: **host · surface/path · exact command · exit code · content
identity (sha256 / RID / taskKey+case_id) · timestamp.** A bare measured value
without these is not a receipt. (Adopted after seven measurement-surface errors
in the planning dialogue; graduates to the membrane-wide convention.)

## 8. Outcome recording

Results land in `docs/membrane/DRILL-001-RESULTS.md` (same branch) with per-bar
receipts; case resolution for the production case is expected to be `unaffected`
or `update_canon` (restatement-evolution chain) and is recorded, not scored.
