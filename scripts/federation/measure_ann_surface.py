#!/usr/bin/env python3
"""Before/after for an ANN surface's result ordering, per surface, per corpus.

`hnsw.iterative_scan = relaxed_order` returns rows only APPROXIMATELY sorted by
distance. A surface that orders by the index output without re-sorting is
mis-ranked, and since each surface turns rank into an RRF weight
(`1.0/(k+rank+1)`) and then truncates, a mis-ranked chunk does not merely sit
lower — it falls off the end.

Each surface gets its OWN frozen query set, written against that surface's
actual corpus. A shared set would be meaningless here: the `wiki` surface serves
99k P2P Foundation pages and the `docs` surface serves 48k email newsletters,
so a query that retrieves well from one retrieves nothing from the other, and a
"0 results" run proves nothing about ordering.

Reports membership and ordering SEPARATELY, plus mean similarity of the kept
top-N — because a changed order is not self-evidently better or worse, and the
similarity is what settles it.

    python scripts/federation/measure_ann_surface.py --surface wiki --out before.json
    python scripts/federation/measure_ann_surface.py --compare before.json after.json
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

PG = os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")
CACHE = Path("/tmp/ann_surface_embeddings.json")

# Frozen before the change, grounded in sampled content from each corpus.
QUERY_SETS = {
    # 99,034 chunks / 33,808 pages, all wiki.p2pfoundation.net.
    "wiki": [
        "peer production and commons based economy",
        "employee ownership cooperative legislation",
        "do artifacts have politics Langdon Winner",
        "alternative payment card without fees",
        "valuation of open intangible assets",
        "catabolic collapse of industrial civilization",
        "distributed monitoring system for clusters",
        "sharing as a threat to copyright",
        "from trees to piles of leaves knowledge",
        "ambient intelligence and pervasive ICT",
        "phenomenology in therapeutic practice",
        "open cooperativism and platform coops",
        "mutual credit and complementary currency",
        "urban commons governance",
        "free software licensing and reciprocity",
    ],
    # metadata->>'repo' IS NOT NULL — dominated by email-newsletter (48,112),
    # substack-backfill (679), research-papers (272), calendar-ics (255).
    "docs": [
        "memory wall and model context",
        "equational theory for quantum circuits",
        "yarning conversation with Tyson Yunkaporta",
        "weekly working group calendar invitation",
        "donation thank you ancient rainforest",
        "AI agent tooling newsletter",
        "regenerative finance and ecological credits",
        "bioregional organising and watershed",
        "large language model evaluation benchmark",
        "open source governance and maintainers",
        "climate adaptation and resilience planning",
        "indigenous knowledge and data sovereignty",
        "distributed systems consensus protocol",
        "community land trust and housing",
        "graph database and knowledge representation",
    ],
}

# Shipped predicates, verbatim. `{order}` is where the fix goes.
SURFACE_SQL = {
    "wiki": """
        SELECT mc.chunk_rid,
               1 - (mc.embedding_3072::halfvec(3072) <=> $1::halfvec(3072)) AS score
        FROM koi_memory_chunks mc
        WHERE mc.embedding_3072 IS NOT NULL
          AND mc.document_rid LIKE 'mediawiki:%'
        ORDER BY mc.embedding_3072::halfvec(3072) <=> $1::halfvec(3072)
        LIMIT 20""",
    "docs": """
        SELECT mc.chunk_rid,
               1 - (mc.embedding_3072::halfvec(3072) <=> $1::halfvec(3072)) AS score
        FROM koi_memory_chunks mc
        WHERE mc.embedding_3072 IS NOT NULL
          AND mc.metadata->>'repo' IS NOT NULL
        ORDER BY mc.embedding_3072::halfvec(3072) <=> $1::halfvec(3072)
        LIMIT 20""",
}

FIXED_SQL = {
    name: f"WITH hits AS ({sql}\n) SELECT * FROM hits ORDER BY score DESC"
    for name, sql in SURFACE_SQL.items()
}


async def embeddings(queries) -> dict:
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    missing = [q for q in queries if q not in cache]
    if missing:
        from api.embedding_provider import OpenAIEmbeddingProvider
        e = OpenAIEmbeddingProvider(api_key=os.getenv("OPENAI_API_KEY"),
                                    model="text-embedding-3-large", dimension=3072)
        for q in missing:
            cache[q] = "[" + ",".join(str(x) for x in await e.embed(q)) + "]"
        CACHE.write_text(json.dumps(cache))
    return cache


def inversions(scores) -> int:
    return sum(1 for i in range(len(scores) - 1)
               if scores[i] < scores[i + 1] - 1e-12)


async def measure(surface: str, fixed: bool, top_n: int) -> dict:
    queries = QUERY_SETS[surface]
    embs = await embeddings(queries)
    sql = (FIXED_SQL if fixed else SURFACE_SQL)[surface]
    conn = await asyncpg.connect(PG)
    try:
        out = {"surface": surface, "fixed": fixed, "queries": {}}
        for q in queries:
            rows = await conn.fetch(sql, embs[q])
            scores = [float(r["score"]) for r in rows]
            out["queries"][q] = {
                "identity": [r["chunk_rid"] for r in rows],
                "scores": [round(s, 9) for s in scores],
                "inversions": inversions(scores),
                # What the endpoint actually keeps after truncation.
                "kept_mean_similarity": (
                    round(statistics.mean(scores[:top_n]), 6) if scores else 0.0),
            }
        vs = out["queries"].values()
        out["totals"] = {
            "rows": sum(len(v["identity"]) for v in vs),
            "inversions": sum(v["inversions"] for v in vs),
            "queries_affected": sum(1 for v in vs if v["inversions"]),
            "mean_kept_similarity": round(
                statistics.mean([v["kept_mean_similarity"] for v in vs]), 6),
        }
        return out
    finally:
        await conn.close()


def compare(b: dict, a: dict) -> int:
    tb, ta = b["totals"], a["totals"]
    print(f"surface: {b['surface']}\n")
    print(f"{'metric':<24}{'before':>13}{'after':>13}")
    for k in ("rows", "inversions", "queries_affected", "mean_kept_similarity"):
        print(f"{k:<24}{tb[k]:>13}{ta[k]:>13}")

    if tb["rows"] == 0:
        print("\n  ⚠ zero rows before — the query set does not match this corpus, "
              "the measurement proves nothing")
        return 1

    reordered = members = same = 0
    for q in b["queries"]:
        bi = b["queries"][q]["identity"]
        ai = a["queries"].get(q, {}).get("identity", [])
        if bi == ai:
            same += 1
        elif set(bi) == set(ai):
            reordered += 1
        else:
            members += 1
    print(f"\n── result set ──\n  identical {same}   reordered {reordered}   "
          f"different members {members}   (of {len(b['queries'])})")

    up = down = flat = 0
    for q in b["queries"]:
        mb = b["queries"][q]["kept_mean_similarity"]
        ma = a["queries"].get(q, {}).get("kept_mean_similarity", 0.0)
        up += ma > mb + 1e-9
        down += ma < mb - 1e-9
        flat += abs(ma - mb) <= 1e-9
    print(f"\n── kept top-N mean similarity ──\n  improved {up}   degraded {down}"
          f"   unchanged {flat}")
    if down:
        print("  ⚠ some queries degraded — do not ship on the aggregate alone")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--surface", choices=sorted(QUERY_SETS))
    ap.add_argument("--fixed", action="store_true",
                    help="measure the outer-ORDER-BY variant")
    ap.add_argument("--top-n", type=int, default=10)
    ap.add_argument("--out")
    ap.add_argument("--compare", nargs=2)
    args = ap.parse_args()

    if args.compare:
        with open(args.compare[0]) as f1, open(args.compare[1]) as f2:
            return compare(json.load(f1), json.load(f2))
    if not args.surface:
        ap.error("--surface required unless --compare")
    data = asyncio.run(measure(args.surface, args.fixed, args.top_n))
    if args.out:
        Path(args.out).write_text(json.dumps(data, indent=1))
    t = data["totals"]
    print(f"{data['surface']} fixed={data['fixed']}  rows={t['rows']}  "
          f"inversions={t['inversions']} ({t['queries_affected']} queries)  "
          f"kept_sim={t['mean_kept_similarity']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
