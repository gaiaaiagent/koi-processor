# Eval Baselines

Committed-to-git snapshots of RAG eval runs that subsequent sessions can
compare against. This directory is the source of truth for "what did we
measure, when, and under what system configuration."

## Why this exists

`tests/eval/results/` is `.gitignored` (by design — it's the noisy
working directory for every run). That means baselines stored there
disappear when disks get wiped, when a workstation is rebuilt, or when
someone runs `rm -rf results/*.json.tmp`. During the 2026-04-21 Octo
embedding switch, the referenced `-p2-synthesis-mechanism-2026-04-04-045002.json`
was missing locally and had to be replaced with a documented CR=0.327 from
CLAUDE.md — that gap motivated this archive.

Baselines here are frozen: only add new ones, don't overwrite or rewrite.

## Naming convention

`<tag>-<yyyy-mm-dd>.json`

- `tag` summarises what the run captures (system state, optimization,
  feature flag, etc.) — e.g. `p2-synthesis-full`, `openai-switch-full`,
  `b9c-planner-canonical`
- date is the run date (UTC is fine)

Copy from `tests/eval/results/-<tag>-<yyyy-mm-dd>-<HHMMSS>.json` to
`tests/eval/baselines/<tag>-<yyyy-mm-dd>.json` to promote.

## Inventory

### `p2-synthesis-full-2026-04-04.json`

- **Captures:** post-synthesis-clause Phase 2 state on Octo before any
  embedding-provider change. Documentation-primary prompt clause added;
  retrieval still via poly `Qwen3-Embedding-0.6B` at dim=1024.
- **Config:** `gpt-4.1-mini` judge, `planner=true`, `answer_mode=default`,
  100 questions (9-category golden_qa).
- **Headline metrics:**
  - Faithfulness: **0.9822**
  - Answer relevancy: **0.8112**
  - Context relevancy: **0.4337**
  - OOD abstention accuracy: 1.0 (10/10)
  - Classifier accuracy: 0.91
  - Pass rate: 22/100
- **Per-category CR:** entity_definition 0.556, relationship_path 0.490,
  governance_policy 0.476, commitment_claim 0.407, roadmap_status 0.327,
  out_of_domain 0.000 (expected, abstentions).
- **Use:** canonical pre-OpenAI-switch comparison target.
- **Source run:** `tests/eval/results/-p2-synthesis-full-2026-04-04-050100.json`
  (may not be present locally — that's the problem this directory
  addresses).

### `openai-switch-full-2026-04-21.json`

- **Captures:** post-OpenAI-embedding-switch state on Octo. Runtime
  embedding provider changed from poly `Qwen3-Embedding-0.6B` to OpenAI
  `text-embedding-3-large` @ dim=1024. All 2,839 entities + 3,675 chunks
  re-embedded in OpenAI semantic space (one-time cost: $0.17).
- **Config:** `gpt-4.1-mini` judge, `planner=true`, `answer_mode=default`,
  100 questions total (97 scored after 3 known_limit exclusions).
- **Headline metrics:**
  - Faithfulness: **0.9816** (Δ −0.0006 vs p2 baseline, within ±1% gate)
  - Answer relevancy: **0.842** (Δ +0.031, +3.8%)
  - Context relevancy: **0.4562** (Δ +0.0225, +5.2%)
  - OOD abstention accuracy: 1.0 (10/10)
  - Classifier accuracy: 0.91
  - Pass rate: 31/97 (vs 22/100 baseline, +9 passes)
  - Avg latency: 7.07s (essentially flat vs 7.03s)
- **Per-category CR deltas vs p2 baseline:**
  - governance_policy 0.476 → 0.541 (**+0.065, +13.7%**) — largest jump
  - relationship_path 0.490 → 0.514 (+0.024, +5.0%)
  - commitment_claim 0.407 → 0.425 (+0.018, +4.5%)
  - entity_definition 0.556 → 0.580 (+0.024, +4.4%)
  - roadmap_status 0.327 → 0.335 (+0.008, +2.6%)
  - out_of_domain 0.000 → 0.000 (flat, expected — abstentions)
- **Use:** canonical post-OpenAI-switch comparison target. Any future
  retrieval/prompt/routing optimization should beat this before being
  promoted to production.
- **Source run:** `tests/eval/results/-openai-switch-full-2026-04-21-103259.json`

## Running a comparison

Use `run_eval.py --compare <baseline_A> <baseline_B>`:

```bash
python tests/eval/run_eval.py --compare \
  tests/eval/baselines/p2-synthesis-full-2026-04-04.json \
  tests/eval/results/-<new-run>.json
```

The comparison is matched-subset — questions present in both reports are
scored head-to-head.

## Promotion checklist

When promoting a new results file to baseline:

1. Run completed cleanly (no errors, scored_count >= total-known_limit)
2. Decide if it replaces a prior baseline or adds a new reference point
   (usually the latter — baselines are history, not just "latest")
3. Copy to `tests/eval/baselines/<tag>-<yyyy-mm-dd>.json`
4. Add an entry to the Inventory section above with:
   - What system state it captures (flag, model, code SHA if informative)
   - Config (judge model, planner, answer_mode, question count)
   - Headline metrics (F / AR / CR / OOD / classifier / pass)
   - Notable per-category numbers
   - Source run path in results/
5. Commit both the baseline JSON and the README update in the same change.
