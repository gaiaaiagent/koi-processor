#!/usr/bin/env python3
"""
Week 13 GraphRAG Context Evaluation Harness

Evaluates the quality of graph context returned by the /api/koi/query endpoint.
Focus: Context relevance metrics (edge count, predicate distribution, dominant entity detection).

Usage:
    python scripts/eval_graphrag.py [--api-url URL]
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from collections import Counter
from typing import Dict, List, Any, Optional

try:
    import httpx
except ImportError:
    print("Error: httpx not installed. Run: pip install httpx")
    sys.exit(1)


# API configuration
DEFAULT_API_URL = "http://localhost:8301"

# Evaluation queries (15 total)
EVAL_QUERIES = [
    # Entity-Heavy Queries (8)
    {
        "query": "Gregory Landua",
        "category": "entity_heavy",
        "expected_type": "PERSON",
        "notes": "Known polysemic entity (PERSON vs ORG)",
    },
    {
        "query": "Regen Network ecocredits",
        "category": "entity_heavy",
        "expected_type": "ORGANIZATION",
        "notes": "Core domain entity",
    },
    {
        "query": "CarbonPlus Grasslands credit class",
        "category": "entity_heavy",
        "expected_type": "CREDIT_CLASS",
        "notes": "Domain-specific credit class entity",
    },
    {
        "query": "x/ecocredit module",
        "category": "entity_heavy",
        "expected_type": "MODULE",
        "notes": "Cosmos SDK module entity",
    },
    {
        "query": "Chorus One validator",
        "category": "entity_heavy",
        "expected_type": "VALIDATOR",
        "notes": "Blockchain validator entity",
    },
    {
        "query": "Martin Wainstein",
        "category": "entity_heavy",
        "expected_type": "PERSON",
        "notes": "Person entity with relationships",
    },
    {
        "query": "NCT token",
        "category": "entity_heavy",
        "expected_type": "TECHNOLOGY",
        "notes": "Technology/token entity",
    },
    {
        "query": "Cosmos SDK",
        "category": "entity_heavy",
        "expected_type": "TECHNOLOGY",
        "notes": "Technology with many relationships",
    },
    # Ambiguous/Complex Queries (7)
    {
        "query": "How does the carbon credit retirement process work?",
        "category": "ambiguous",
        "expected_type": None,
        "notes": "Process-oriented query requiring relationship traversal",
    },
    {
        "query": "Who founded Regen Network?",
        "category": "ambiguous",
        "expected_type": "PERSON",
        "notes": "Requires relationship extraction (founded)",
    },
    {
        "query": "What projects use x/group module?",
        "category": "ambiguous",
        "expected_type": "MODULE",
        "notes": "Requires graph expansion for uses relationship",
    },
    {
        "query": "Relationship between NCT and ecocredits",
        "category": "ambiguous",
        "expected_type": None,
        "notes": "Multi-entity query",
    },
    {
        "query": "Where is Regen Network based?",
        "category": "ambiguous",
        "expected_type": "LOCATION",
        "notes": "Location relationship query",
    },
    {
        "query": "What validators support Regen mainnet?",
        "category": "ambiguous",
        "expected_type": "VALIDATOR",
        "notes": "Validator relationship query",
    },
    {
        "query": "How are credit classes created?",
        "category": "ambiguous",
        "expected_type": None,
        "notes": "Process understanding query",
    },
]


async def run_query(
    client: httpx.AsyncClient,
    api_url: str,
    query: str,
) -> Dict[str, Any]:
    """Run a single query with graph context enabled."""
    try:
        response = await client.post(
            f"{api_url}/api/koi/query",
            json={
                "question": query,
                "limit": 10,
                "graph_context": True,  # Explicitly request graph context
            },
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        print(f"  HTTP error for '{query}': {e.response.status_code}")
        return {"error": str(e)}
    except httpx.RequestError as e:
        print(f"  Request error for '{query}': {e}")
        return {"error": str(e)}


def analyze_graph_context(graph_context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyze a graph context response for quality metrics."""
    if not graph_context:
        return {
            "has_context": False,
            "has_dominant_entity": False,
            "edge_count": 0,
            "truncated": False,
            "predicates": [],
            "subject_entities": [],
            "object_entities": [],
        }

    dominant = graph_context.get("dominant_entity")
    edges = graph_context.get("edges", [])

    predicates = [e.get("predicate") for e in edges if e.get("predicate")]
    subjects = [e.get("subject_text") for e in edges if e.get("subject_text")]
    objects = [e.get("object_text") for e in edges if e.get("object_text")]

    return {
        "has_context": True,
        "has_dominant_entity": dominant is not None,
        "dominant_entity_text": dominant.get("text") if dominant else None,
        "dominant_entity_type": dominant.get("type") if dominant else None,
        "dominant_entity_occurrence": dominant.get("occurrence_count") if dominant else None,
        "edge_count": len(edges),
        "truncated": graph_context.get("truncated", False),
        "predicates": predicates,
        "predicate_distribution": dict(Counter(predicates)),
        "unique_predicates": len(set(predicates)),
        "subject_entities": list(set(subjects)),
        "object_entities": list(set(objects)),
        "unique_entities": len(set(subjects + objects)),
    }


async def run_evaluation(api_url: str) -> List[Dict[str, Any]]:
    """Run all evaluation queries and collect results."""
    results = []

    async with httpx.AsyncClient() as client:
        print(f"\nRunning {len(EVAL_QUERIES)} evaluation queries against {api_url}...")
        print("-" * 60)

        for i, eval_query in enumerate(EVAL_QUERIES, 1):
            query = eval_query["query"]
            print(f"\n[{i}/{len(EVAL_QUERIES)}] {query}")

            response = await run_query(client, api_url, query)

            if "error" in response:
                result = {
                    "query": query,
                    "category": eval_query["category"],
                    "expected_type": eval_query["expected_type"],
                    "notes": eval_query["notes"],
                    "error": response["error"],
                    "analysis": None,
                }
            else:
                graph_context = response.get("graph_context")
                analysis = analyze_graph_context(graph_context)

                result = {
                    "query": query,
                    "category": eval_query["category"],
                    "expected_type": eval_query["expected_type"],
                    "notes": eval_query["notes"],
                    "confidence": response.get("confidence", 0),
                    "total_results": response.get("total_results", 0),
                    "analysis": analysis,
                }

                # Print summary
                if analysis["has_context"]:
                    entity = analysis.get("dominant_entity_text", "None")
                    entity_type = analysis.get("dominant_entity_type", "")
                    print(f"  Dominant: {entity} ({entity_type})")
                    print(f"  Edges: {analysis['edge_count']}, Unique predicates: {analysis['unique_predicates']}")
                else:
                    print("  No graph context returned")

            results.append(result)

    return results


def generate_report(results: List[Dict[str, Any]], output_path: Path) -> None:
    """Generate a markdown evaluation report."""
    timestamp = datetime.now().isoformat()

    # Calculate summary statistics
    total = len(results)
    with_context = sum(1 for r in results if r.get("analysis", {}).get("has_context", False))
    with_entity = sum(1 for r in results if r.get("analysis", {}).get("has_dominant_entity", False))
    edge_counts = [r["analysis"]["edge_count"] for r in results if r.get("analysis")]
    avg_edges = sum(edge_counts) / len(edge_counts) if edge_counts else 0
    truncated = sum(1 for r in results if r.get("analysis", {}).get("truncated", False))

    # Aggregate predicate distribution
    all_predicates: List[str] = []
    for r in results:
        if r.get("analysis"):
            all_predicates.extend(r["analysis"].get("predicates", []))
    predicate_dist = Counter(all_predicates)

    # By category
    entity_heavy = [r for r in results if r["category"] == "entity_heavy"]
    ambiguous = [r for r in results if r["category"] == "ambiguous"]

    entity_heavy_with_context = sum(1 for r in entity_heavy if r.get("analysis", {}).get("has_context", False))
    ambiguous_with_context = sum(1 for r in ambiguous if r.get("analysis", {}).get("has_context", False))

    report = f"""# Week 13 GraphRAG Context Evaluation Results

**Date:** {timestamp}
**API:** localhost:8301

## Summary

| Metric | Value |
|--------|-------|
| Total queries | {total} |
| Queries with graph context | {with_context} ({100*with_context/total:.1f}%) |
| Queries with dominant entity | {with_entity} ({100*with_entity/total:.1f}%) |
| Average edge count | {avg_edges:.1f} |
| Queries with truncated context | {truncated} |

## By Category

| Category | Total | With Context | % |
|----------|-------|--------------|---|
| Entity-Heavy | {len(entity_heavy)} | {entity_heavy_with_context} | {100*entity_heavy_with_context/len(entity_heavy):.1f}% |
| Ambiguous | {len(ambiguous)} | {ambiguous_with_context} | {100*ambiguous_with_context/len(ambiguous):.1f}% |

## Predicate Distribution (Top 10)

| Predicate | Count |
|-----------|-------|
"""

    for pred, count in predicate_dist.most_common(10):
        report += f"| {pred} | {count} |\n"

    report += "\n## Detailed Results\n\n"

    for r in results:
        query = r["query"]
        category = r["category"]
        notes = r.get("notes", "")

        report += f"### {query}\n\n"
        report += f"- **Category:** {category}\n"
        report += f"- **Notes:** {notes}\n"

        if "error" in r:
            report += f"- **Error:** {r['error']}\n\n"
            continue

        report += f"- **Confidence:** {r.get('confidence', 0):.3f}\n"
        report += f"- **Total results:** {r.get('total_results', 0)}\n"

        analysis = r.get("analysis", {})
        if analysis.get("has_context"):
            entity = analysis.get("dominant_entity_text", "None")
            entity_type = analysis.get("dominant_entity_type", "")
            occurrence = analysis.get("dominant_entity_occurrence", 0)
            report += f"- **Dominant entity:** {entity} ({entity_type}, occ={occurrence})\n"
            report += f"- **Edge count:** {analysis['edge_count']}\n"
            report += f"- **Unique predicates:** {analysis['unique_predicates']}\n"
            report += f"- **Truncated:** {analysis['truncated']}\n"

            if analysis.get("predicate_distribution"):
                preds = ", ".join(f"{k}({v})" for k, v in list(analysis["predicate_distribution"].items())[:5])
                report += f"- **Top predicates:** {preds}\n"
        else:
            report += "- **Graph context:** None returned\n"

        report += "\n"

    # Write report
    output_path.write_text(report)
    print(f"\nReport written to: {output_path}")


async def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="GraphRAG Context Evaluation")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="API base URL")
    args = parser.parse_args()

    # Run evaluation
    results = await run_evaluation(args.api_url)

    # Generate report
    output_path = Path(__file__).parent.parent / "docs" / "week13_graphrag_context_evaluation.md"
    generate_report(results, output_path)

    # Print summary
    total = len(results)
    with_context = sum(1 for r in results if r.get("analysis", {}).get("has_context", False))
    with_entity = sum(1 for r in results if r.get("analysis", {}).get("has_dominant_entity", False))

    print("\n" + "=" * 60)
    print("EVALUATION COMPLETE")
    print("=" * 60)
    print(f"Queries with graph context: {with_context}/{total} ({100*with_context/total:.1f}%)")
    print(f"Queries with dominant entity: {with_entity}/{total} ({100*with_entity/total:.1f}%)")


if __name__ == "__main__":
    asyncio.run(main())
