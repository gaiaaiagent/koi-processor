#!/usr/bin/env python3
"""
B5 Chat Evaluation Harness — Stratified BKC Baseline

Evaluates the BKC /chat endpoint across 7 query categories to establish
a quality baseline (B1) for A/B comparison against B2 GraphRAG.

Metrics:
  - Resolution rate: % queries returning >0 sources
  - Source count: avg sources per query
  - Latency: p50 and p95 response time
  - Answer relevance: LLM-as-judge (5-point scale, "relevant" = 4+)

Usage:
    # Run against Octo server via SSH tunnel
    ssh -L 8351:127.0.0.1:8351 root@45.132.245.30 -N &
    python scripts/eval_chat_baseline.py

    # Or specify a custom URL
    python scripts/eval_chat_baseline.py --api-url http://127.0.0.1:8351

    # Run with LLM relevance judging (requires OPENAI_API_KEY)
    python scripts/eval_chat_baseline.py --judge

    # Compare against saved baseline
    python scripts/eval_chat_baseline.py --compare docs/eval/b1-baseline.json
"""

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import httpx
except ImportError:
    print("Error: httpx not installed. Run: pip install httpx")
    sys.exit(1)

# ── Stratified BKC Query Set (28 queries) ────────────────────────────

EVAL_QUERIES = [
    # ── Entity Resolution (5) ──
    {
        "query": "Regenerate Cascadia",
        "category": "entity_resolution",
        "expected_hint": "Organization focused on bioregional regeneration in Cascadia",
    },
    {
        "query": "Salish Sea herring",
        "category": "entity_resolution",
        "expected_hint": "Herring monitoring or ecology in the Salish Sea bioregion",
    },
    {
        "query": "KOI protocol",
        "category": "entity_resolution",
        "expected_hint": "Knowledge Organization Infrastructure protocol by BlockScience",
    },
    {
        "query": "Benjamin Life",
        "category": "entity_resolution",
        "expected_hint": "Person involved in bioregional knowledge commoning",
    },
    {
        "query": "Front Range bioregion",
        "category": "entity_resolution",
        "expected_hint": "Colorado Front Range bioregion or related geographic area",
    },

    # ── Relationship Traversal (5) ──
    {
        "query": "What organizations work in the Salish Sea?",
        "category": "relationship_traversal",
        "expected_hint": "Should list organizations located_in or affiliated_with Salish Sea",
    },
    {
        "query": "Who is involved in herring monitoring?",
        "category": "relationship_traversal",
        "expected_hint": "People or orgs connected to herring monitoring practices",
    },
    {
        "query": "What projects does Regenerate Cascadia have?",
        "category": "relationship_traversal",
        "expected_hint": "Projects linked to Regenerate Cascadia via has_project or related predicates",
    },
    {
        "query": "What practices are used in the Salish Sea bioregion?",
        "category": "relationship_traversal",
        "expected_hint": "Practices linked to Salish Sea via practiced_in or located_in",
    },
    {
        "query": "What concepts relate to knowledge commoning?",
        "category": "relationship_traversal",
        "expected_hint": "Concepts connected via broader/narrower/related_to predicates",
    },

    # ── Roadmap (5) ──
    {
        "query": "What's on the critical path for the BKC roadmap?",
        "category": "roadmap",
        "expected_hint": "Should mention P0/critical priority roadmap items",
    },
    {
        "query": "What milestones are done?",
        "category": "roadmap",
        "expected_hint": "Completed work items or milestones from the roadmap",
    },
    {
        "query": "Show P0 items",
        "category": "roadmap",
        "expected_hint": "High priority items from the roadmap",
    },
    {
        "query": "What are the next steps for the BKC project?",
        "category": "roadmap",
        "expected_hint": "Planned or in-progress roadmap items",
    },
    {
        "query": "What depends on the chat retrieval system?",
        "category": "roadmap",
        "expected_hint": "Items that depend_on or are blocked by B1 chat",
    },

    # ── Web Content (3) ──
    {
        "query": "What web sources have been ingested about bioregional governance?",
        "category": "web_content",
        "expected_hint": "Ingested web sources related to governance",
    },
    {
        "query": "Summarize the latest web sources",
        "category": "web_content",
        "expected_hint": "Recent web submissions with titles and summaries",
    },
    {
        "query": "What is the Salish Sea Knowledge Garden?",
        "category": "web_content",
        "expected_hint": "The public Quartz site and its purpose",
    },

    # ── Commitment Pooling (2) ──
    {
        "query": "How does commitment pooling work?",
        "category": "commitment_pooling",
        "expected_hint": "Explanation of commitment lifecycle: proposed → verified → active → redeemed",
    },
    {
        "query": "What are community asset vouchers?",
        "category": "commitment_pooling",
        "expected_hint": "CAVs as first-class KOI entities for mutual aid",
    },

    # ── Cross-Domain (3) ──
    {
        "query": "How does the roadmap connect to commitment pooling?",
        "category": "cross_domain",
        "expected_hint": "C-series roadmap items and their relationship to commitment work",
    },
    {
        "query": "What projects relate to herring?",
        "category": "cross_domain",
        "expected_hint": "Projects, practices, or organizations connected to herring ecology",
    },
    {
        "query": "How do bioregions connect to each other in the knowledge commons?",
        "category": "cross_domain",
        "expected_hint": "Federation, KOI-net, holon topology connecting bioregional nodes",
    },

    # ── Regen Secondary (5) — backward compat with existing eval ──
    {
        "query": "Gregory Landua",
        "category": "regen_secondary",
        "expected_hint": "Person, co-founder or key figure at Regen Network",
    },
    {
        "query": "What is Regen Network?",
        "category": "regen_secondary",
        "expected_hint": "Organization focused on ecological regeneration and ecocredits",
    },
    {
        "query": "Nature Carbon Tonne",
        "category": "regen_secondary",
        "expected_hint": "NCT token or credit class in Regen ecosystem",
    },
    {
        "query": "What is ecological monitoring?",
        "category": "regen_secondary",
        "expected_hint": "Monitoring practices for ecosystem health",
    },
    {
        "query": "How do ecocredits work?",
        "category": "regen_secondary",
        "expected_hint": "Regen Network ecocredit issuance and trading",
    },
]

# ── LLM-as-Judge Prompt ──────────────────────────────────────────────

JUDGE_SYSTEM = """You are an evaluation judge for a bioregional knowledge commons chat system.
Rate the relevance and quality of the answer on a 1-5 scale:
  1 = Completely irrelevant or wrong
  2 = Mentions the topic but misses key information
  3 = Partially relevant, some useful info but incomplete
  4 = Relevant and mostly accurate, good coverage
  5 = Highly relevant, accurate, well-grounded in sources

Respond with ONLY a JSON object: {"score": <int>, "reason": "<1 sentence>"}"""

JUDGE_USER = """Query: {query}
Expected topic: {expected_hint}
Sources returned: {source_count}
Answer: {answer}

Rate the answer's relevance (1-5):"""


async def compute_graph_version(api_url: str, client: httpx.AsyncClient) -> str:
    """Get the deterministic graph-state hash from the /graph-version endpoint.

    The server computes SHA-256(entity_count:rel_count:max_entity_updated:max_rel_created)[:16].
    This changes whenever entities or relationships are added/modified.
    """
    try:
        resp = await client.get(f"{api_url}/graph-version", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data["graph_version"]
    except Exception:
        pass

    # Fallback: hash of entity count from /entity-search (best effort)
    try:
        resp = await client.get(f"{api_url}/entity-search?query=a&limit=1", timeout=10)
        entity_data = resp.json()
        entity_count = len(entity_data) if isinstance(entity_data, list) else 0
    except Exception:
        entity_count = -1
    state_str = f"fallback:{entity_count}"
    return hashlib.sha256(state_str.encode()).hexdigest()[:16]


async def run_query(
    client: httpx.AsyncClient,
    api_url: str,
    query_spec: Dict[str, Any],
    retrieval_mode: str = "hybrid",
) -> Dict[str, Any]:
    """Run a single chat query and collect metrics."""
    start = time.monotonic()
    try:
        payload = {"query": query_spec["query"], "max_context_entities": 10}
        if retrieval_mode != "hybrid":
            payload["retrieval_mode"] = retrieval_mode
        resp = await client.post(
            f"{api_url}/chat",
            json=payload,
            timeout=30,
        )
        elapsed = time.monotonic() - start
        if resp.status_code != 200:
            return {
                "query": query_spec["query"],
                "category": query_spec["category"],
                "expected_hint": query_spec["expected_hint"],
                "status": "error",
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                "latency_s": round(elapsed, 3),
                "source_count": 0,
                "sources": [],
                "answer": "",
            }
        data = resp.json()
        sources = data.get("sources", [])
        return {
            "query": query_spec["query"],
            "category": query_spec["category"],
            "expected_hint": query_spec["expected_hint"],
            "status": "ok",
            "latency_s": round(elapsed, 3),
            "source_count": len(sources),
            "sources": [
                {"label": s.get("label", ""), "type": s.get("entity_type", ""), "score": s.get("score", 0)}
                for s in sources
            ],
            "answer": data.get("answer", ""),
            "intent": data.get("intent", {}),
        }
    except Exception as e:
        elapsed = time.monotonic() - start
        return {
            "query": query_spec["query"],
            "category": query_spec["category"],
            "expected_hint": query_spec["expected_hint"],
            "status": "error",
            "error": str(e),
            "latency_s": round(elapsed, 3),
            "source_count": 0,
            "sources": [],
            "answer": "",
        }


async def judge_relevance(
    openai_client: Any,
    result: Dict[str, Any],
) -> Dict[str, Any]:
    """Use GPT-4o-mini to score answer relevance."""
    import openai as openai_module

    prompt = JUDGE_USER.format(
        query=result["query"],
        expected_hint=result["expected_hint"],
        source_count=result["source_count"],
        answer=result["answer"][:1000],
    )
    try:
        resp = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=100,
        )
        text = resp.choices[0].message.content.strip()
        # Parse JSON response
        judge_result = json.loads(text)
        return {
            "score": int(judge_result.get("score", 0)),
            "reason": judge_result.get("reason", ""),
        }
    except Exception as e:
        return {"score": 0, "reason": f"Judge error: {e}"}


def compute_aggregates(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute aggregate metrics from per-query results."""
    ok_results = [r for r in results if r["status"] == "ok"]
    latencies = [r["latency_s"] for r in ok_results]
    source_counts = [r["source_count"] for r in ok_results]
    resolved = [r for r in ok_results if r["source_count"] > 0]

    agg = {
        "total_queries": len(results),
        "successful_queries": len(ok_results),
        "error_queries": len(results) - len(ok_results),
        # Denominator is total queries (not just successful) — errors count as unresolved
        "resolution_rate": round(len(resolved) / len(results) * 100, 1) if results else 0,
        "avg_source_count": round(statistics.mean(source_counts), 2) if source_counts else 0,
        "median_source_count": round(statistics.median(source_counts), 1) if source_counts else 0,
    }

    if latencies:
        sorted_lat = sorted(latencies)
        agg["latency_p50_s"] = round(sorted_lat[len(sorted_lat) // 2], 3)
        agg["latency_p95_s"] = round(sorted_lat[int(len(sorted_lat) * 0.95)], 3)
        agg["latency_avg_s"] = round(statistics.mean(latencies), 3)

    # Per-category breakdown
    categories = {}
    for r in ok_results:
        cat = r["category"]
        if cat not in categories:
            categories[cat] = {"total": 0, "resolved": 0, "latencies": [], "source_counts": []}
        categories[cat]["total"] += 1
        if r["source_count"] > 0:
            categories[cat]["resolved"] += 1
        categories[cat]["latencies"].append(r["latency_s"])
        categories[cat]["source_counts"].append(r["source_count"])

    cat_summary = {}
    for cat, data in categories.items():
        cat_summary[cat] = {
            "total": data["total"],
            "resolution_rate": round(data["resolved"] / data["total"] * 100, 1) if data["total"] else 0,
            "avg_source_count": round(statistics.mean(data["source_counts"]), 2),
            "avg_latency_s": round(statistics.mean(data["latencies"]), 3),
        }
    agg["by_category"] = cat_summary

    # Relevance stats (if judging was done)
    scores = [r.get("relevance", {}).get("score", 0) for r in results if r.get("relevance", {}).get("score", 0) > 0]
    if scores:
        agg["relevance_avg"] = round(statistics.mean(scores), 2)
        agg["relevance_pct_4plus"] = round(len([s for s in scores if s >= 4]) / len(scores) * 100, 1)

    return agg


def print_results(results: List[Dict[str, Any]], aggregates: Dict[str, Any]) -> None:
    """Print formatted results to stdout."""
    print("\n" + "=" * 72)
    print("  B5 Chat Evaluation — Stratified BKC Baseline")
    print("=" * 72)

    # Per-query results
    for r in results:
        status = "OK" if r["status"] == "ok" else "ERR"
        sources = r["source_count"]
        latency = r["latency_s"]
        rel = r.get("relevance", {}).get("score", "-")
        print(f"  [{status}] {r['category']:25s} | {latency:5.2f}s | {sources:2d} src | rel={rel} | {r['query'][:50]}")
        if r["status"] == "error":
            print(f"         ERROR: {r.get('error', 'unknown')[:70]}")

    # Aggregates
    print("\n" + "-" * 72)
    print("  AGGREGATE METRICS")
    print("-" * 72)
    print(f"  Resolution rate:  {aggregates['resolution_rate']:5.1f}%  (target: >80%)")
    print(f"  Avg source count: {aggregates['avg_source_count']:5.2f}   (target: 5-8)")
    print(f"  Latency p50:      {aggregates.get('latency_p50_s', 0):5.3f}s (target: <3s)")
    print(f"  Latency p95:      {aggregates.get('latency_p95_s', 0):5.3f}s (target: <6s)")

    if "relevance_avg" in aggregates:
        print(f"  Relevance avg:    {aggregates['relevance_avg']:5.2f}/5")
        print(f"  Relevance >=4:    {aggregates['relevance_pct_4plus']:5.1f}%  (target: >70%)")

    # Per-category
    print("\n  BY CATEGORY:")
    for cat, data in aggregates.get("by_category", {}).items():
        print(f"    {cat:25s} | res={data['resolution_rate']:5.1f}% | avg_src={data['avg_source_count']:4.1f} | lat={data['avg_latency_s']:5.3f}s")

    # Gate check
    print("\n" + "-" * 72)
    gates_passed = 0
    gates_total = 2
    if aggregates["resolution_rate"] >= 80:
        print("  PASS  Resolution rate >= 80%")
        gates_passed += 1
    else:
        print(f"  FAIL  Resolution rate {aggregates['resolution_rate']:.1f}% < 80%")

    p50 = aggregates.get("latency_p50_s", 999)
    if p50 <= 3.0:
        print(f"  PASS  Latency p50 {p50:.3f}s <= 3s")
        gates_passed += 1
    else:
        print(f"  FAIL  Latency p50 {p50:.3f}s > 3s")

    print(f"\n  Gates: {gates_passed}/{gates_total} passed")
    print("=" * 72)


def compare_baselines(current: Dict[str, Any], baseline_path: str) -> None:
    """Compare current run against a saved baseline."""
    with open(baseline_path) as f:
        baseline = json.load(f)

    b_agg = baseline["aggregates"]
    c_agg = current["aggregates"]

    print("\n" + "=" * 72)
    print("  BASELINE COMPARISON")
    print("=" * 72)

    metrics = [
        ("resolution_rate", "%", True),
        ("avg_source_count", "", True),
        ("latency_p50_s", "s", False),
        ("latency_p95_s", "s", False),
    ]
    if "relevance_avg" in b_agg and "relevance_avg" in c_agg:
        metrics.append(("relevance_avg", "/5", True))

    for metric, unit, higher_better in metrics:
        b_val = b_agg.get(metric, 0)
        c_val = c_agg.get(metric, 0)
        if b_val == 0:
            delta = "N/A"
        else:
            pct = (c_val - b_val) / b_val * 100
            arrow = "+" if pct > 0 else ""
            good = (pct > 0) == higher_better
            indicator = "better" if good else "WORSE"
            delta = f"{arrow}{pct:.1f}% ({indicator})"
        print(f"  {metric:25s} | baseline={b_val:6.2f}{unit} | current={c_val:6.2f}{unit} | {delta}")

    gv_match = baseline.get("graph_version") == current.get("graph_version", "")
    print(f"\n  Graph version match: {'YES' if gv_match else 'NO (re-baseline recommended)'}")
    print("=" * 72)


async def main():
    parser = argparse.ArgumentParser(description="B5 Chat Eval Harness")
    parser.add_argument("--api-url", default="http://127.0.0.1:8351", help="KOI API base URL")
    parser.add_argument("--judge", action="store_true", help="Enable LLM-as-judge relevance scoring")
    parser.add_argument("--save", default="docs/eval/b1-baseline.json", help="Save results to this path")
    parser.add_argument("--compare", help="Compare against a saved baseline file")
    parser.add_argument("--concurrency", type=int, default=1, help="Max concurrent queries (1 = sequential)")
    parser.add_argument("--retrieval-mode", default="hybrid", choices=["hybrid", "graphrag"],
                        help="Retrieval mode: hybrid (B1) or graphrag (B2)")
    args = parser.parse_args()

    print(f"B5 Eval Harness — {len(EVAL_QUERIES)} queries against {args.api_url}")
    print(f"Retrieval mode: {args.retrieval_mode}")
    print(f"Judge: {'enabled' if args.judge else 'disabled (use --judge to enable)'}")

    # Health check
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{args.api_url}/health", timeout=10)
            health = resp.json()
            print(f"Server health: {health.get('status', 'unknown')}")
        except Exception as e:
            print(f"ERROR: Cannot reach {args.api_url}/health — {e}")
            print("Hint: ssh -L 8351:127.0.0.1:8351 root@45.132.245.30 -N &")
            sys.exit(1)

        # Compute graph version snapshot
        graph_version = await compute_graph_version(args.api_url, client)
        print(f"Graph version: {graph_version}")

        # Run queries
        results: List[Dict[str, Any]] = []
        sem = asyncio.Semaphore(args.concurrency)

        async def bounded_query(spec):
            async with sem:
                return await run_query(client, args.api_url, spec, retrieval_mode=args.retrieval_mode)

        print(f"\nRunning {len(EVAL_QUERIES)} queries (concurrency={args.concurrency})...")
        tasks = [bounded_query(q) for q in EVAL_QUERIES]
        results = await asyncio.gather(*tasks)
        results = list(results)

    # LLM-as-judge scoring
    if args.judge:
        openai_key = os.getenv("OPENAI_API_KEY")
        if not openai_key:
            print("WARNING: OPENAI_API_KEY not set, skipping judge scoring")
        else:
            try:
                import openai
                judge_client = openai.OpenAI(api_key=openai_key)
                print("Running LLM-as-judge relevance scoring...")
                for i, r in enumerate(results):
                    if r["status"] == "ok" and r["answer"]:
                        score = await judge_relevance(judge_client, r)
                        results[i]["relevance"] = score
                        print(f"  [{i+1}/{len(results)}] score={score['score']} | {r['query'][:40]}")
            except ImportError:
                print("WARNING: openai package not installed, skipping judge")

    # Compute aggregates
    aggregates = compute_aggregates(results)

    # Print results
    print_results(results, aggregates)

    # Save baseline
    baseline = {
        "eval_version": "b5-v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "api_url": args.api_url,
        "retrieval_mode": args.retrieval_mode,
        "graph_version": graph_version,
        "query_count": len(results),
        "aggregates": aggregates,
        "results": results,
    }

    save_path = Path(args.save)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "w") as f:
        json.dump(baseline, f, indent=2)
    print(f"\nBaseline saved to {save_path}")

    # Compare if requested
    if args.compare:
        compare_baselines(baseline, args.compare)

    # Exit code based on gates
    if aggregates["resolution_rate"] < 80 or aggregates.get("latency_p50_s", 999) > 3.0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
