#!/usr/bin/env python3
"""Before/after measurement for the unified-search `memories` surface.

The change under measurement widens that surface's SELECT so chunk metadata
(title / author / source_url) reaches the payload. It touches a shared hot path
serving ~61,730 memory chunks, so two things need proving, not one:

  1. **Coverage improves** — the fields actually arrive.
  2. **Nothing else moves** — widening a SELECT must not change WHICH chunks are
     returned or in WHAT ORDER. This is the half that would otherwise be assumed.

Run against an isolated instance on a spare port so :8351 is untouched:

    python scripts/federation/measure_memories_surface.py --port 8399 --out before.json
    # apply the change, restart that instance
    python scripts/federation/measure_memories_surface.py --port 8399 --out after.json
    python scripts/federation/measure_memories_surface.py --compare before.json after.json
"""

import argparse
import json
import statistics
import sys
import time
import urllib.parse
import urllib.request

# Fixed query set, written before the change and not tuned afterwards. Mixed
# deliberately: some target the newly-landed Nate Jones documents, most target
# the pre-existing corpus, because "must not regress existing search" is the
# constraint that matters most here.
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

FIELDS = ("title", "author", "source_url")


def fetch(port: int, query: str, limit: int = 10) -> list:
    url = (f"http://localhost:{port}/knowledge/unified-search?"
           + urllib.parse.urlencode({"query": query, "include": "memories",
                                     "limit": limit}))
    with urllib.request.urlopen(url, timeout=120) as r:
        return json.load(r).get("results", [])


def measure(port: int) -> dict:
    out = {"queries": {}}
    latencies = []
    for q in QUERIES:
        _t0 = time.perf_counter()
        rows = fetch(port, q)
        latencies.append((time.perf_counter() - _t0) * 1000)
        mem = [r for r in rows if r.get("source") in
               {"memory", "email", "substack", "calendar"}]
        out["queries"][q] = {
            # Identity: ordered chunk_rids. This is the regression check.
            "identity": [r.get("chunk_rid") for r in mem],
            "n": len(mem),
            "coverage": {
                f: sum(1 for r in mem
                       if (r.get(f) or (r.get("metadata") or {}).get(f)))
                for f in FIELDS
            },
            "sample": mem[0] if mem else None,
        }
    tot = {f: sum(v["coverage"][f] for v in out["queries"].values()) for f in FIELDS}
    tot["results"] = sum(v["n"] for v in out["queries"].values())
    tot["latency_p50_ms"] = round(statistics.median(latencies), 1)
    tot["latency_mean_ms"] = round(statistics.mean(latencies), 1)
    out["totals"] = tot
    return out


def compare(before: dict, after: dict) -> int:
    b, a = before["totals"], after["totals"]
    print(f"{'field':<12} {'before':>10} {'after':>10}   of {a['results']} results")
    for f in FIELDS:
        print(f"{f:<12} {b[f]:>10} {a[f]:>10}")
    print(f"{'results':<12} {b['results']:>10} {a['results']:>10}")
    print(f"{'e2e p50 ms':<12} {b.get('latency_p50_ms','-'):>10} {a.get('latency_p50_ms','-'):>10}")
    print(f"{'e2e mean ms':<12} {b.get('latency_mean_ms','-'):>10} {a.get('latency_mean_ms','-'):>10}")

    # Membership and order are reported SEPARATELY and mean different things.
    # This script originally treated any difference as a failure, on the
    # assumption that widening a SELECT cannot change ordering. That assumption
    # was wrong: hnsw.iterative_scan is `relaxed_order`, so the index returns
    # rows only approximately sorted, and adding an outer ORDER BY corrects a
    # pre-existing mis-ranking. A reorder here is therefore not automatically a
    # regression — but it is never automatically fine either, so both numbers
    # are printed and the judgement is left to a similarity comparison.
    print("\n── did the RESULT SET change? ──")
    reordered, remembered = [], []
    for q in before["queries"]:
        bi = before["queries"][q]["identity"]
        ai = after["queries"].get(q, {}).get("identity") or []
        if bi == ai:
            continue
        (remembered if set(bi) != set(ai) else reordered).append((q, bi, ai))

    if not reordered and not remembered:
        print(f"  identical for all {len(before['queries'])} queries "
              f"({b['results']} results) — payload-only")
    else:
        print(f"  same members, different order : {len(reordered)} queries")
        print(f"  different members             : {len(remembered)} queries")
        for q, bi, ai in (remembered or reordered)[:2]:
            print(f"    {q!r}\n      before: {bi[:2]}\n      after:  {ai[:2]}")
        print("  → compare mean similarity of the kept top-N before calling "
              "this better or worse; a changed order is not self-evidently either.")

    if a["results"] == 0:
        print("\n  ⚠ zero results overall — the measurement proves nothing")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8399)
    ap.add_argument("--out")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    args = ap.parse_args()

    if args.compare:
        with open(args.compare[0]) as f1, open(args.compare[1]) as f2:
            return compare(json.load(f1), json.load(f2))

    data = measure(args.port)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(data, f, indent=1)
    t = data["totals"]
    print(f"results={t['results']}  " + "  ".join(f"{f}={t[f]}" for f in FIELDS)
          + f"  e2e_p50={t['latency_p50_ms']}ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
