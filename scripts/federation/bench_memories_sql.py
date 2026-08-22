#!/usr/bin/env python3
"""Benchmark three shapes of the unified-search `memories` surface query.

Decides between two ways of getting document-level fields (title / author /
source_url) into a memories result:

  A  current   — no metadata at all (the shipped baseline)
  B  widen     — also select mc.metadata; needs a 61k-row backfill to be useful,
                 and that copy can then drift from the parent forever
  C  join      — ANN + LIMIT first in a CTE, then LEFT JOIN koi_memories for the
                 top-N only. No copied rows, no drift, always current.

C is only viable if the join does not disturb the ANN. The risk is concrete in
this repo: a pgvector cost-model defect once made the planner skip the HNSW
index entirely on a similar query. So this measures latency AND asserts the
index is still used, rather than assuming a CTE fences the plan.

Embeddings for the frozen query set are cached to disk so repeated runs compare
SQL, not OpenAI.

    python scripts/federation/bench_memories_sql.py --runs 10
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.federation.measure_memories_surface import QUERIES  # noqa: E402

CACHE = Path("/tmp/ac11_query_embeddings.json")
PG = os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")

_FILTER = """
                  WHERE mc.embedding_3072 IS NOT NULL
                    AND mc.document_rid NOT LIKE 'mediawiki:%'
                    AND mc.document_rid NOT LIKE 'doc-scanner:%'
                  ORDER BY mc.embedding_3072::halfvec(3072) <=> $1::halfvec(3072)
                  LIMIT 20"""

SQL_A = f"""
    SELECT mc.chunk_rid, mc.document_rid,
           mc.content->>'text' AS chunk_text,
           mc.content->>'title' AS title,
           1 - (mc.embedding_3072::halfvec(3072) <=> $1::halfvec(3072)) AS score
    FROM koi_memory_chunks mc {_FILTER}"""

SQL_B = f"""
    SELECT mc.chunk_rid, mc.document_rid,
           mc.content->>'text' AS chunk_text,
           mc.content->>'title' AS title,
           mc.metadata,
           1 - (mc.embedding_3072::halfvec(3072) <=> $1::halfvec(3072)) AS score
    FROM koi_memory_chunks mc {_FILTER}"""

# The CTE is what keeps the ANN + LIMIT ahead of the join, so the join touches
# 20 rows rather than the whole candidate set. LEFT, not INNER: the FK makes a
# missing parent impossible today, but an inner join would make result-set
# membership depend on that staying true, and a silently dropped result is the
# failure mode this codebase keeps paying for.
SQL_C = f"""
    WITH hits AS (
        SELECT mc.chunk_rid, mc.document_rid,
               mc.content->>'text' AS chunk_text,
               mc.content->>'title' AS title,
               mc.metadata,
               1 - (mc.embedding_3072::halfvec(3072) <=> $1::halfvec(3072)) AS score
        FROM koi_memory_chunks mc {_FILTER}
    )
    SELECT h.chunk_rid, h.document_rid, h.chunk_text, h.metadata, h.score,
           COALESCE(h.title, m.content->>'title')  AS title,
           m.metadata->>'author'                   AS parent_author,
           m.metadata->>'source_url'               AS parent_source_url
    FROM hits h
    LEFT JOIN koi_memories m ON m.rid = h.document_rid
    ORDER BY h.score DESC"""

VARIANTS = {"A current": SQL_A, "B widen": SQL_B, "C widen+join": SQL_C}


async def embeddings() -> dict:
    if CACHE.exists():
        return json.loads(CACHE.read_text())
    from api.embedding_provider import OpenAIEmbeddingProvider
    e = OpenAIEmbeddingProvider(api_key=os.getenv("OPENAI_API_KEY"),
                                model="text-embedding-3-large", dimension=3072)
    out = {}
    for q in QUERIES:
        out[q] = "[" + ",".join(str(x) for x in await e.embed(q)) + "]"
    CACHE.write_text(json.dumps(out))
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=10)
    args = ap.parse_args()

    embs = await embeddings()
    conn = await asyncpg.connect(PG)
    try:
        # Warm EVERY query against EVERY variant. A partial warm-up makes the
        # variant that runs first absorb all the cold-cache cost, which is what
        # a first pass of this benchmark did (variant A: p50 8ms, mean 55ms).
        for _ in range(2):
            for sql in VARIANTS.values():
                for q in QUERIES:
                    await conn.fetch(sql, embs[q])

        # Interleave variants within each run so any drift over the run (other
        # DB load, autovacuum) hits all three equally instead of whichever
        # happens to be measured last.
        timings = {name: [] for name in VARIANTS}
        identities, coverage = {}, {}
        # ROTATE the variant order every iteration. Interleaving alone is not
        # enough: with a fixed A,B,C order the first variant pays each query's
        # page-fault cost and the other two ride its cache. A previous pass
        # measured B as 35% FASTER than A purely from that, which is not a
        # thing an extra selected column can do.
        names = list(VARIANTS)
        for i in range(args.runs):
            for j, q in enumerate(QUERIES):
                shift = (i + j) % len(names)
                for name in names[shift:] + names[:shift]:
                    t0 = time.perf_counter()
                    await conn.fetch(VARIANTS[name], embs[q])
                    timings[name].append((time.perf_counter() - t0) * 1000)
        for name, sql in VARIANTS.items():
            samples = timings[name]
            identities[name] = {
                q: [r["chunk_rid"] for r in await conn.fetch(sql, embs[q])]
                for q in QUERIES
            }
            timings[name] = samples
            if name != "A current":
                cov = {"title": 0, "author": 0, "source_url": 0}
                for q in QUERIES:
                    for r in await conn.fetch(sql, embs[q]):
                        meta = json.loads(r["metadata"]) if r["metadata"] else {}
                        if r["title"] or meta.get("title"):
                            cov["title"] += 1
                        author = meta.get("author") or (
                            r["parent_author"] if "parent_author" in r else None)
                        url = meta.get("source_url") or (
                            r["parent_source_url"] if "parent_source_url" in r else None)
                        cov["author"] += bool(author)
                        cov["source_url"] += bool(url)
                coverage[name] = cov

        n_rows = sum(len(v) for v in identities["A current"].values())
        print(f"{len(QUERIES)} queries x {args.runs} runs, {n_rows} result rows\n")
        print(f"{'variant':<14}{'p50 ms':>9}{'p95 ms':>9}{'mean':>9}   vs A")
        base = statistics.median(timings["A current"])
        for name, s in timings.items():
            p50 = statistics.median(s)
            p95 = statistics.quantiles(s, n=20)[18]
            delta = "—" if name == "A current" else f"{p50 - base:+.2f} ms ({100*(p50-base)/base:+.1f}%)"
            print(f"{name:<14}{p50:>9.2f}{p95:>9.2f}{statistics.mean(s):>9.2f}   {delta}")

        print(f"\n{'variant':<14}{'title':>8}{'author':>8}{'url':>8}   of {n_rows}")
        for name, c in coverage.items():
            print(f"{name:<14}{c['title']:>8}{c['author']:>8}{c['source_url']:>8}")

        print("\n── is the ANN itself deterministic? (control) ──")
        # Without this, an ordering difference between variants is
        # indistinguishable from run-to-run noise in an APPROXIMATE index.
        a1 = {q: [r["chunk_rid"] for r in await conn.fetch(SQL_A, embs[q])]
              for q in QUERIES}
        a2 = {q: [r["chunk_rid"] for r in await conn.fetch(SQL_A, embs[q])]
              for q in QUERIES}
        stable = sum(a1[q] == a2[q] for q in QUERIES)
        print(f"  A vs A on a repeat run: {stable}/{len(QUERIES)} queries identical")

        print("\n── result set identical across variants? ──")
        ref = identities["A current"]
        for name, ids in identities.items():
            same_order = all(ids[q] == ref[q] for q in QUERIES)
            same_set = all(set(ids[q]) == set(ref[q]) for q in QUERIES)
            n_order = sum(ids[q] != ref[q] for q in QUERIES)
            n_set = sum(set(ids[q]) != set(ref[q]) for q in QUERIES)
            verdict = ("identical to A" if same_order else
                       f"same MEMBERS, different ORDER on {n_order} queries"
                       if same_set else
                       f"DIFFERENT MEMBERS on {n_set} queries")
            print(f"  {name:<14} {verdict}")

        print("\n── does the join keep the HNSW index? ──")
        plan = await conn.fetch(
            "EXPLAIN (ANALYZE, BUFFERS) " + SQL_C, embs[QUERIES[0]])
        text = "\n".join(r["QUERY PLAN"] for r in plan)
        used = "idx_koi_memory_chunks_embedding_3072_hnsw" in text
        print(f"  HNSW index used: {used}")
        if not used:
            print("  ⚠ the join changed the plan — C is NOT viable as written")
        for line in text.splitlines():
            if any(k in line for k in ("Index Scan", "Seq Scan", "Nested Loop",
                                       "Execution Time", "CTE")):
                print("   ", line.strip()[:120])
        return 0 if used else 1
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
