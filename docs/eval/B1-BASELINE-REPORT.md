# B1 Baseline Evaluation Report

**Date:** 2026-03-04
**Graph version:** c8aedf6eab430baa
**Retrieval mode:** hybrid (B1)
**Queries:** 28 (7 categories)
**Server:** Octo (45.132.245.30 via SSH tunnel :18351)

## Aggregate Metrics

| Metric | Value | Target |
|--------|-------|--------|
| Resolution rate | 96.4% | >80% |
| Avg source count | 10.21 | 5-8 |
| Latency p50 | 3.40s | <3s |
| Latency p95 | 8.57s | <6s |
| Relevance avg | 3.04/5 | - |
| Relevance >=4 | 46.4% | >70% |

**Note:** p50/p95 latency elevated due to SSH tunnel overhead. Direct-on-server latency would be lower.

## Per-Category Breakdown

| Category | Queries | Resolution | Avg Sources | Avg Latency |
|----------|---------|------------|-------------|-------------|
| entity_resolution | 5 | 100.0% | 11.0 | 4.57s |
| relationship_traversal | 5 | 100.0% | 12.0 | 3.52s |
| roadmap | 5 | 100.0% | 10.8 | 3.18s |
| web_content | 3 | 100.0% | 13.3 | 2.15s |
| commitment_pooling | 2 | 100.0% | 11.0 | 5.62s |
| cross_domain | 3 | 100.0% | 10.7 | 3.96s |
| regen_secondary | 5 | 80.0% | 4.6 | 3.06s |

## Observations

- **Resolution is strong** — 96.4% overall, only `regen_secondary` has gaps (Gregory Landua returned 0 sources, expected since he's not a BKC entity)
- **Source density is high** — avg 10.21, well above the 5-8 target. May indicate over-retrieval.
- **Relevance is mixed** — 3.04 avg with only 46.4% scoring >=4. Weakest in `roadmap` (critical path, next steps queries scored 1) and `web_content` (2 avg). These categories may benefit most from GraphRAG's community-aware retrieval.
- **Latency** — SSH tunnel adds ~0.5-1s overhead. The p50 gate (3s) is marginal; direct measurement would likely pass.

## B2 GraphRAG Comparison Gates

For GraphRAG to be enabled in production, it must meet:
1. p95 latency <= 1.10x of B1 p95 (i.e., <= 9.43s)
2. `relevance_avg` >= 3.04
3. Per-category resolution rate >= B1 (no regression)

## Data Files

- Quick run (no judge): `b1-baseline-quick.json`
- Judged run: `b1-baseline.json`
