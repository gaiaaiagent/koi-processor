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
