#!/usr/bin/env python3
"""Before/after for `retrieval_executors.text_search` — the main RAG path.

Exercises the SHIPPED function, not a transcription of its SQL, so the thing
measured is the thing that runs.

What is wrong with it: the vector arm assigns `vrank` with
`ROW_NUMBER() OVER (ORDER BY <distance>)` directly over an HNSW index scan.
EXPLAIN shows **no Sort node** — the WindowAgg trusts the index's ordering, and
under `hnsw.iterative_scan = relaxed_order` that ordering is only approximate.
`vrank` IS the RRF weight (`1.0/(vrank+60)`), so an approximate rank is a wrong
fused score, and the fused list is then truncated — a mis-ranked chunk does not
merely sit lower, it falls out.

Reports, separately:
  - vrank correctness (inversions against true similarity)
  - the returned bundle identity (membership vs order)
  - mean confidence of the kept top-k, which is what says better or worse

    python scripts/federation/measure_text_search.py --out before.json
    python scripts/federation/measure_text_search.py --compare before.json after.json
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from api.retrieval_executors import text_search  # noqa: E402

# Inlined rather than imported so this branch stands alone: the memories-surface
# work lives on a different branch and the two must be mergeable independently.
# Same frozen set, so the two measurements remain comparable.
QUERIES = [
    "what did Nate Jones say about AI agents and context",
    "two-class system knowledge work",
    "prompts for picking the right AI model",
    "bioregional financing facility",
    "regenerative agriculture credits",
    "knowledge commons governance",
    "sheaf cohomology globalization",
    "entity resolution semantic matching",
    "koi-net federation protocol",
    "commitment pool routing",
    "energy blindness and the great simplification",
    "substack newsletter ingestion pipeline",
    "vault sync conflict resolution",
    "impact claim verification",
    "postgres pgvector index performance",
]
CACHE = Path("/tmp/ac11_query_embeddings.json")

PG = os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")

# The shipped vector arm, isolated, to count vrank inversions directly.
VRANK_SQL = """
    SELECT 1 - (c.embedding_3072::halfvec(3072) <=> $1::halfvec(3072)) AS score,
           ROW_NUMBER() OVER (
               ORDER BY c.embedding_3072::halfvec(3072) <=> $1::halfvec(3072)
           ) AS vrank
    FROM koi_memory_chunks c
    JOIN koi_memories m ON m.rid = c.document_rid
    WHERE c.embedding_3072 IS NOT NULL
      AND c.content->>'entity_name' IS NULL
    ORDER BY c.embedding_3072::halfvec(3072) <=> $1::halfvec(3072) LIMIT 40"""


async def measure() -> dict:
    embs = json.loads(CACHE.read_text())
    conn = await asyncpg.connect(PG)
    try:
        out = {"queries": {}}
        vrank_inv = vrank_q = 0
        for q in QUERIES:
            emb = json.loads(embs[q])

            rows = await conn.fetch(VRANK_SQL, embs[q])
            scores = [float(r["score"]) for r in rows]
            n_inv = sum(1 for i in range(len(scores) - 1)
                        if scores[i] < scores[i + 1] - 1e-12)
            vrank_inv += n_inv
            vrank_q += n_inv > 0

            bundles = await text_search(q, emb, conn, top_k=8)
            out["queries"][q] = {
                "identity": [b.source_uri for b in bundles],
                "confidences": [round(float(b.confidence), 6) for b in bundles],
                "vrank_inversions": n_inv,
            }
        conf = [c for v in out["queries"].values() for c in v["confidences"]]
        out["totals"] = {
            "bundles": sum(len(v["identity"]) for v in out["queries"].values()),
            "vrank_inversions": vrank_inv,
            "vrank_queries_affected": vrank_q,
            "mean_confidence": round(statistics.mean(conf), 6) if conf else 0.0,
        }
        return out
    finally:
        await conn.close()


def compare(b: dict, a: dict) -> int:
    tb, ta = b["totals"], a["totals"]
    print(f"{'metric':<26}{'before':>12}{'after':>12}")
    for k in ("bundles", "vrank_inversions", "vrank_queries_affected",
              "mean_confidence"):
        print(f"{k:<26}{tb[k]:>12}{ta[k]:>12}")

    print("\n── returned bundles ──")
    reordered = members = same = 0
    for q in b["queries"]:
        bi, ai = b["queries"][q]["identity"], a["queries"].get(q, {}).get("identity", [])
        if bi == ai:
            same += 1
        elif set(bi) == set(ai):
            reordered += 1
        else:
            members += 1
    print(f"  identical            : {same}/{len(b['queries'])}")
    print(f"  same members, redone : {reordered}")
    print(f"  different members    : {members}")

    print("\n── better or worse? mean confidence of the kept top-k, per query ──")
    up = down = flat = 0
    for q in b["queries"]:
        cb = b["queries"][q]["confidences"]
        ca = a["queries"].get(q, {}).get("confidences", [])
        mb = statistics.mean(cb) if cb else 0.0
        ma = statistics.mean(ca) if ca else 0.0
        if ma > mb + 1e-9:
            up += 1
        elif ma < mb - 1e-9:
            down += 1
        else:
            flat += 1
    print(f"  improved {up}   degraded {down}   unchanged {flat}")
    if ta["bundles"] == 0:
        print("  ⚠ zero bundles — the measurement proves nothing")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--compare", nargs=2)
    args = ap.parse_args()
    if args.compare:
        with open(args.compare[0]) as f1, open(args.compare[1]) as f2:
            return compare(json.load(f1), json.load(f2))
    data = asyncio.run(measure())
    if args.out:
        Path(args.out).write_text(json.dumps(data, indent=1))
    t = data["totals"]
    print(f"bundles={t['bundles']}  vrank_inversions={t['vrank_inversions']} "
          f"({t['vrank_queries_affected']}/{len(QUERIES)} queries)  "
          f"mean_conf={t['mean_confidence']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
