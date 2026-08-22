# text_search / code_filter — the index-shape question, answered

**Question (Darren, 2026-08-22):** before trading recall for latency, can the
planner problem itself be fixed — an expression or partial index on the JSONB
`code_filter` predicate, so the filter combines with an ordered index scan
instead of falling back to a seq scan over 164k rows?

**Answer: the index fix works, and it does not buy exactness. The trade is
inherent to HNSW, not to the filter.**

## What was measured

1. **Free test first.** Forcing the existing index (`enable_seqscan=off`) on the
   filtered query: **10,059 ms p50 and 82.8% recall** — *worse than the seq scan
   on both axes*. So the planner's seq-scan choice was correct, and the cost is
   heap fetches: the JSONB predicate can only be evaluated from the heap, so
   164k random fetches lose to a sequential read.

2. **Partial index built** to remove exactly that: same `m=16, ef_construction=64`
   as the existing index so the experiment isolated partial-ness, not build
   parameters:
   `... USING hnsw ((embedding_3072::halfvec(3072)) halfvec_cosine_ops)
    WHERE embedding_3072 IS NOT NULL AND content->>'entity_name' IS NULL`
   1,274 MB, built CONCURRENTLY, ~50 min.

   The planner chose it unaided. Latency collapsed from 8,377 ms to single/double
   digit ms. Recall against the exact seq-scan result as ground truth, warmed,
   two passes:

   | ef_search | recall mean | recall min | queries at 100% | p50 |
   |---|---|---|---|---|
   | 40 (default) | 76.0% | **0.0%** | 6/30 | ~5 ms |
   | 200 | 94.5% | 72.5% | 14/30 | ~16–21 ms |
   | 800 | 97.0% | 80.0% | 20/30 | ~44–207 ms |
   | seq scan (today) | **100%** | 100% | — | 8,377 ms |

   `hnsw.iterative_scan` mode (relaxed_order / strict_order / off) changed
   **recall not at all** — identical 76.0 / 94.5 / 97.0 across all three. It
   changes latency only. Recall is a function of `ef_search`.

## Two process notes

- **A first pass reported 100% recall at ef 40 in 9,983 ms.** That was cold cache
  on a just-built index, and the inverse recall/ef relationship it implied is not
  real. Warming and repeating produced the monotonic table above. A number that
  contradicts how the algorithm works is a measurement bug until proven otherwise.
- **Creating the index silently changed live retrieval.** Once valid, the planner
  used it for the shipped `text_search` query immediately — 168 ms at ~76% recall
  instead of 8,377 ms at 100%. That is the recall trade being taken by side
  effect, which is what the instruction said not to do. **The index was dropped**
  (`DROP INDEX CONCURRENTLY`) and the live plan verified back to exact seq scan.
  Rebuilding it is ~50 minutes whenever the decision is made.

## What is left to decide

Exact-and-fast is not on the table via index shape. The options are:

| option | recall | latency | cost |
|---|---|---|---|
| leave as-is | 100% | 8,377 ms | none |
| partial index, ef 200 | 94.5% (min 72.5%) | ~20 ms | 1.3 GB + rebuild |
| partial index, ef 800 | 97.0% (min 80.0%) | ~44–207 ms | 1.3 GB + rebuild |
| + rebuild at higher m / ef_construction | untested | untested | full HNSW rebuild |

The last row is the only remaining lever on recall, and it is untested: the
current index is `m=16, ef_construction=64`, which is low for a 164k × 3072
corpus. If the answer needs to beat 97%, that is the experiment to run before
choosing an `ef_search`.
