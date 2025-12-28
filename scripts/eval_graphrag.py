#!/usr/bin/env python3
"""
Week 21 GraphRAG Context Evaluation Harness

Evaluates the quality of graph context returned by the /api/koi/query endpoint.
Focus: Context relevance metrics (edge count, predicate distribution, dominant entity detection).

Week 21 enhancements:
- Separate metrics for entity-style vs question-style queries
- Track: resolution rate, avg edge count, % queries with >0 edges
- Support for multi-entity query detection
- New test queries for question-style coverage

Usage:
    python scripts/eval_graphrag.py [--api-url URL] [--save-baseline] [--compare-baseline]
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

# Evaluation queries (21 total - 8 entity + 13 question)
# Week 21: Renamed "ambiguous" to "question" for clarity, added 3 new question queries
# Week 21b: Added 3 more multi-entity queries (4 total) to avoid single-case success skew
EVAL_QUERIES = [
    # Entity-Heavy Queries (8)
    {
        "query": "Gregory Landua",
        "category": "entity",
        "expected_type": "PERSON",
        "notes": "Known polysemic entity (PERSON vs ORG)",
    },
    {
        "query": "Regen Network ecocredits",
        "category": "entity",
        "expected_type": "ORGANIZATION",
        "notes": "Core domain entity",
    },
    {
        "query": "CarbonPlus Grasslands credit class",
        "category": "entity",
        "expected_type": "CREDIT_CLASS",
        "notes": "Domain-specific credit class entity",
    },
    {
        "query": "x/ecocredit module",
        "category": "entity",
        "expected_type": "MODULE",
        "notes": "Cosmos SDK module entity",
    },
    {
        "query": "Chorus One validator",
        "category": "entity",
        "expected_type": "VALIDATOR",
        "notes": "Blockchain validator entity",
    },
    {
        "query": "Martin Wainstein",
        "category": "entity",
        "expected_type": "PERSON",
        "notes": "Person entity with relationships",
    },
    {
        "query": "NCT token",
        "category": "entity",
        "expected_type": "TECHNOLOGY",
        "notes": "Technology/token entity",
    },
    {
        "query": "Cosmos SDK",
        "category": "entity",
        "expected_type": "TECHNOLOGY",
        "notes": "Technology with many relationships",
    },
    # Question-Style Queries (10 - original 7 + 3 new)
    {
        "query": "How does the carbon credit retirement process work?",
        "category": "question",
        "expected_type": None,
        "notes": "Process-oriented query requiring relationship traversal",
    },
    {
        "query": "Who founded Regen Network?",
        "category": "question",
        "expected_type": "PERSON",
        "notes": "Requires relationship extraction (founded)",
    },
    {
        "query": "What projects use x/group module?",
        "category": "question",
        "expected_type": "MODULE",
        "notes": "Requires graph expansion for uses relationship",
    },
    {
        "query": "Relationship between NCT and ecocredits",
        "category": "question",
        "expected_type": None,
        "notes": "Multi-entity query - expects dual resolution",
        "expected_query_type": "multi_entity",
    },
    {
        "query": "Gregory Landua vs Martin Wainstein leadership roles",
        "category": "question",
        "expected_type": None,
        "notes": "Multi-entity query with two PERSON entities",
        "expected_query_type": "multi_entity",
    },
    {
        "query": "Relationship between Regen Network and Cosmos SDK",
        "category": "question",
        "expected_type": None,
        "notes": "Multi-entity query with ORG and TECHNOLOGY",
        "expected_query_type": "multi_entity",
    },
    {
        "query": "CarbonPlus Grasslands and Regen Registry connection",
        "category": "question",
        "expected_type": None,
        "notes": "Multi-entity query with CREDIT_CLASS and ORGANIZATION",
        "expected_query_type": "multi_entity",
    },
    {
        "query": "Where is Regen Network based?",
        "category": "question",
        "expected_type": "LOCATION",
        "notes": "Location relationship query",
    },
    {
        "query": "What validators support Regen mainnet?",
        "category": "question",
        "expected_type": "VALIDATOR",
        "notes": "Validator relationship query",
    },
    {
        "query": "How are credit classes created?",
        "category": "question",
        "expected_type": None,
        "notes": "Process understanding query",
    },
    # Week 21: New question queries
    {
        "query": "How is the Regen Ledger related to Cosmos SDK?",
        "category": "question",
        "expected_type": None,
        "notes": "Relationship query between two tech entities",
        "expected_query_type": "question",
    },
    {
        "query": "Who leads Regen Network?",
        "category": "question",
        "expected_type": "PERSON",
        "notes": "Leadership relationship query",
    },
    {
        "query": "What tools integrate with Regen Registry?",
        "category": "question",
        "expected_type": "TECHNOLOGY",
        "notes": "Integration relationship query",
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
            "has_secondary_entity": False,
            "query_type": None,
            "edge_count": 0,
            "truncated": False,
            "predicates": [],
            "subject_entities": [],
            "object_entities": [],
        }

    dominant = graph_context.get("dominant_entity")
    secondary = graph_context.get("secondary_entity")  # Week 21
    edges = graph_context.get("edges", [])
    query_type = graph_context.get("query_type")  # Week 21

    predicates = [e.get("predicate") for e in edges if e.get("predicate")]
    subjects = [e.get("subject_text") for e in edges if e.get("subject_text")]
    objects = [e.get("object_text") for e in edges if e.get("object_text")]

    # Week 21: Track source_entity distribution for multi-entity queries
    source_entities = [e.get("source_entity") for e in edges if e.get("source_entity")]
    source_entity_dist = dict(Counter(source_entities)) if source_entities else {}

    return {
        "has_context": True,
        "has_dominant_entity": dominant is not None,
        "has_secondary_entity": secondary is not None,  # Week 21
        "query_type": query_type,  # Week 21
        "dominant_entity_text": dominant.get("text") if dominant else None,
        "dominant_entity_type": dominant.get("type") if dominant else None,
        "dominant_entity_occurrence": dominant.get("occurrence_count") if dominant else None,
        "secondary_entity_text": secondary.get("text") if secondary else None,  # Week 21
        "secondary_entity_type": secondary.get("type") if secondary else None,  # Week 21
        "edge_count": len(edges),
        "truncated": graph_context.get("truncated", False),
        "predicates": predicates,
        "predicate_distribution": dict(Counter(predicates)),
        "unique_predicates": len(set(predicates)),
        "subject_entities": list(set(subjects)),
        "object_entities": list(set(objects)),
        "unique_entities": len(set(subjects + objects)),
        "source_entity_distribution": source_entity_dist,  # Week 21
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
                    "expected_query_type": eval_query.get("expected_query_type"),
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
                    "expected_query_type": eval_query.get("expected_query_type"),
                    "notes": eval_query["notes"],
                    "confidence": response.get("confidence", 0),
                    "total_results": response.get("total_results", 0),
                    "analysis": analysis,
                }

                # Print summary
                if analysis["has_context"]:
                    entity = analysis.get("dominant_entity_text", "None")
                    entity_type = analysis.get("dominant_entity_type", "")
                    query_type = analysis.get("query_type", "")
                    secondary = analysis.get("secondary_entity_text")
                    print(f"  Type: {query_type} | Dominant: {entity} ({entity_type})")
                    if secondary:
                        print(f"  Secondary: {secondary} ({analysis.get('secondary_entity_type', '')})")
                    print(f"  Edges: {analysis['edge_count']}, Unique predicates: {analysis['unique_predicates']}")
                else:
                    print("  No graph context returned")

            results.append(result)

    return results


def calculate_metrics(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate summary metrics for the evaluation results."""
    total = len(results)

    # Overall metrics
    with_context = sum(1 for r in results if r.get("analysis", {}).get("has_context", False))
    with_entity = sum(1 for r in results if r.get("analysis", {}).get("has_dominant_entity", False))
    with_edges = sum(1 for r in results if r.get("analysis", {}).get("edge_count", 0) > 0)

    edge_counts = [r["analysis"]["edge_count"] for r in results if r.get("analysis")]
    avg_edges = sum(edge_counts) / len(edge_counts) if edge_counts else 0

    edge_counts_with_context = [r["analysis"]["edge_count"] for r in results
                                 if r.get("analysis", {}).get("has_context", False)]
    avg_edges_resolved = sum(edge_counts_with_context) / len(edge_counts_with_context) if edge_counts_with_context else 0

    truncated = sum(1 for r in results if r.get("analysis", {}).get("truncated", False))

    # By category
    entity_queries = [r for r in results if r["category"] == "entity"]
    question_queries = [r for r in results if r["category"] == "question"]

    entity_with_context = sum(1 for r in entity_queries if r.get("analysis", {}).get("has_context", False))
    question_with_context = sum(1 for r in question_queries if r.get("analysis", {}).get("has_context", False))

    entity_edge_counts = [r["analysis"]["edge_count"] for r in entity_queries
                          if r.get("analysis", {}).get("has_context", False)]
    question_edge_counts = [r["analysis"]["edge_count"] for r in question_queries
                            if r.get("analysis", {}).get("has_context", False)]

    entity_avg_edges = sum(entity_edge_counts) / len(entity_edge_counts) if entity_edge_counts else 0
    question_avg_edges = sum(question_edge_counts) / len(question_edge_counts) if question_edge_counts else 0

    # Week 21: Query type detection accuracy
    detected_query_types = {}
    for r in results:
        qt = r.get("analysis", {}).get("query_type")
        if qt:
            detected_query_types[qt] = detected_query_types.get(qt, 0) + 1

    # Multi-entity metrics
    multi_entity_queries = [r for r in results if r.get("expected_query_type") == "multi_entity"]
    multi_entity_resolved = sum(1 for r in multi_entity_queries
                                 if r.get("analysis", {}).get("has_secondary_entity", False))

    return {
        "total": total,
        "with_context": with_context,
        "with_entity": with_entity,
        "with_edges": with_edges,
        "avg_edges": avg_edges,
        "avg_edges_resolved": avg_edges_resolved,
        "truncated": truncated,
        "resolution_rate": with_context / total if total > 0 else 0,
        "entity": {
            "total": len(entity_queries),
            "with_context": entity_with_context,
            "resolution_rate": entity_with_context / len(entity_queries) if entity_queries else 0,
            "avg_edges": entity_avg_edges,
        },
        "question": {
            "total": len(question_queries),
            "with_context": question_with_context,
            "resolution_rate": question_with_context / len(question_queries) if question_queries else 0,
            "avg_edges": question_avg_edges,
        },
        "query_type_distribution": detected_query_types,
        "multi_entity": {
            "total": len(multi_entity_queries),
            "resolved_both": multi_entity_resolved,
        },
    }


def generate_report(results: List[Dict[str, Any]], output_path: Path, baseline: Dict = None) -> None:
    """Generate a markdown evaluation report."""
    timestamp = datetime.now().isoformat()
    metrics = calculate_metrics(results)

    # Aggregate predicate distribution
    all_predicates: List[str] = []
    for r in results:
        if r.get("analysis"):
            all_predicates.extend(r["analysis"].get("predicates", []))
    predicate_dist = Counter(all_predicates)

    report = f"""# Week 21 GraphRAG Context Evaluation Results

**Date:** {timestamp}
**API:** localhost:8301
**Total Queries:** {metrics['total']} (Entity: {metrics['entity']['total']}, Question: {metrics['question']['total']})

## Summary

| Metric | Value |
|--------|-------|
| Total queries | {metrics['total']} |
| Queries with graph context | {metrics['with_context']} ({100*metrics['resolution_rate']:.1f}%) |
| Queries with dominant entity | {metrics['with_entity']} ({100*metrics['with_entity']/metrics['total']:.1f}%) |
| Queries with >0 edges | {metrics['with_edges']} ({100*metrics['with_edges']/metrics['total']:.1f}%) |
| Average edge count (all) | {metrics['avg_edges']:.1f} |
| Average edge count (resolved) | {metrics['avg_edges_resolved']:.1f} |
| Queries with truncated context | {metrics['truncated']} |

## By Category (Week 21 Focus)

| Category | Total | With Context | Resolution Rate | Avg Edges |
|----------|-------|--------------|-----------------|-----------|
| Entity | {metrics['entity']['total']} | {metrics['entity']['with_context']} | {100*metrics['entity']['resolution_rate']:.1f}% | {metrics['entity']['avg_edges']:.1f} |
| Question | {metrics['question']['total']} | {metrics['question']['with_context']} | {100*metrics['question']['resolution_rate']:.1f}% | {metrics['question']['avg_edges']:.1f} |

### Success Criteria Check

| Criterion | Target | Actual | Status |
|-----------|--------|--------|--------|
| Question-style resolution rate | >70% | {100*metrics['question']['resolution_rate']:.1f}% | {'✅ PASS' if metrics['question']['resolution_rate'] > 0.70 else '❌ FAIL'} |
| Entity-style resolution (no regression) | >80% | {100*metrics['entity']['resolution_rate']:.1f}% | {'✅ PASS' if metrics['entity']['resolution_rate'] >= 0.80 else '⚠️ CHECK'} |
| Avg edges per resolved query | >=5 | {metrics['avg_edges_resolved']:.1f} | {'✅ PASS' if metrics['avg_edges_resolved'] >= 5 else '❌ FAIL'} |

"""

    # Add baseline comparison if available
    if baseline:
        report += f"""## Baseline Comparison

| Metric | Baseline | Current | Delta |
|--------|----------|---------|-------|
| Overall Resolution Rate | {100*baseline.get('resolution_rate', 0):.1f}% | {100*metrics['resolution_rate']:.1f}% | {100*(metrics['resolution_rate'] - baseline.get('resolution_rate', 0)):+.1f}% |
| Question Resolution Rate | {100*baseline.get('question', {}).get('resolution_rate', 0):.1f}% | {100*metrics['question']['resolution_rate']:.1f}% | {100*(metrics['question']['resolution_rate'] - baseline.get('question', {}).get('resolution_rate', 0)):+.1f}% |
| Entity Resolution Rate | {100*baseline.get('entity', {}).get('resolution_rate', 0):.1f}% | {100*metrics['entity']['resolution_rate']:.1f}% | {100*(metrics['entity']['resolution_rate'] - baseline.get('entity', {}).get('resolution_rate', 0)):+.1f}% |
| Avg Edges (Resolved) | {baseline.get('avg_edges_resolved', 0):.1f} | {metrics['avg_edges_resolved']:.1f} | {metrics['avg_edges_resolved'] - baseline.get('avg_edges_resolved', 0):+.1f} |

"""

    # Query type distribution
    if metrics['query_type_distribution']:
        report += """## Query Type Detection (Week 21)

| Detected Type | Count |
|--------------|-------|
"""
        for qt, count in sorted(metrics['query_type_distribution'].items()):
            report += f"| {qt} | {count} |\n"
        report += "\n"

    # Multi-entity stats
    if metrics['multi_entity']['total'] > 0:
        report += f"""## Multi-Entity Resolution

| Metric | Value |
|--------|-------|
| Multi-entity queries | {metrics['multi_entity']['total']} |
| Resolved both entities | {metrics['multi_entity']['resolved_both']} |

"""

    report += """## Predicate Distribution (Top 10)

| Predicate | Count |
|-----------|-------|
"""

    for pred, count in predicate_dist.most_common(10):
        report += f"| {pred} | {count} |\n"

    report += "\n## Detailed Results\n\n"

    # Group by category
    for category in ["entity", "question"]:
        cat_results = [r for r in results if r["category"] == category]
        report += f"### {category.title()} Queries\n\n"

        for r in cat_results:
            query = r["query"]
            notes = r.get("notes", "")

            report += f"#### {query}\n\n"
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
                query_type = analysis.get("query_type", "")
                report += f"- **Query type:** {query_type}\n"
                report += f"- **Dominant entity:** {entity} ({entity_type}, occ={occurrence})\n"

                # Secondary entity for multi-entity queries
                if analysis.get("has_secondary_entity"):
                    secondary = analysis.get("secondary_entity_text")
                    secondary_type = analysis.get("secondary_entity_type")
                    report += f"- **Secondary entity:** {secondary} ({secondary_type})\n"

                report += f"- **Edge count:** {analysis['edge_count']}\n"
                report += f"- **Unique predicates:** {analysis['unique_predicates']}\n"
                report += f"- **Truncated:** {analysis['truncated']}\n"

                if analysis.get("predicate_distribution"):
                    preds = ", ".join(f"{k}({v})" for k, v in list(analysis["predicate_distribution"].items())[:5])
                    report += f"- **Top predicates:** {preds}\n"

                if analysis.get("source_entity_distribution"):
                    sources = ", ".join(f"{k}({v})" for k, v in analysis["source_entity_distribution"].items())
                    report += f"- **Source entities:** {sources}\n"
            else:
                report += "- **Graph context:** None returned\n"

            report += "\n"

    # Write report
    output_path.write_text(report)
    print(f"\nReport written to: {output_path}")

    return metrics


async def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Week 21 GraphRAG Context Evaluation")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="API base URL")
    parser.add_argument("--save-baseline", action="store_true", help="Save current results as baseline")
    parser.add_argument("--compare-baseline", action="store_true", help="Compare against saved baseline")
    args = parser.parse_args()

    # Baseline file path
    baseline_path = Path(__file__).parent.parent / "docs" / "week21_graphrag_baseline.json"

    # Load baseline if comparing
    baseline = None
    if args.compare_baseline and baseline_path.exists():
        baseline = json.loads(baseline_path.read_text())
        print(f"Loaded baseline from: {baseline_path}")

    # Run evaluation
    results = await run_evaluation(args.api_url)

    # Generate report
    output_path = Path(__file__).parent.parent / "docs" / "week21_graphrag_evaluation.md"
    metrics = generate_report(results, output_path, baseline)

    # Save baseline if requested
    if args.save_baseline:
        baseline_path.write_text(json.dumps(metrics, indent=2))
        print(f"Baseline saved to: {baseline_path}")

    # Print summary
    print("\n" + "=" * 60)
    print("WEEK 21 EVALUATION COMPLETE")
    print("=" * 60)
    print(f"Overall Resolution Rate: {metrics['with_context']}/{metrics['total']} ({100*metrics['resolution_rate']:.1f}%)")
    print(f"Entity Resolution Rate: {metrics['entity']['with_context']}/{metrics['entity']['total']} ({100*metrics['entity']['resolution_rate']:.1f}%)")
    print(f"Question Resolution Rate: {metrics['question']['with_context']}/{metrics['question']['total']} ({100*metrics['question']['resolution_rate']:.1f}%)")
    print(f"Avg Edges (Resolved): {metrics['avg_edges_resolved']:.1f}")

    # Success criteria summary
    print("\n" + "-" * 40)
    print("SUCCESS CRITERIA:")
    q_pass = metrics['question']['resolution_rate'] > 0.70
    e_pass = metrics['entity']['resolution_rate'] >= 0.80
    edge_pass = metrics['avg_edges_resolved'] >= 5

    print(f"  Question Resolution >70%: {'✅ PASS' if q_pass else '❌ FAIL'} ({100*metrics['question']['resolution_rate']:.1f}%)")
    print(f"  Entity Resolution >=80%: {'✅ PASS' if e_pass else '⚠️ CHECK'} ({100*metrics['entity']['resolution_rate']:.1f}%)")
    print(f"  Avg Edges >=5: {'✅ PASS' if edge_pass else '❌ FAIL'} ({metrics['avg_edges_resolved']:.1f})")


if __name__ == "__main__":
    asyncio.run(main())
