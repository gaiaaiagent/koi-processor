#!/usr/bin/env python3
"""
B5/B9a — Automated evaluation pipeline for /chat RAG endpoint.

Supports:
  - Category-level reporting via TAXONOMY_MAP normalization
  - Evidence recall metric (when gold_evidence present)
  - Planner telemetry (plan_trace recording + aggregation)
  - Matched-subset comparison (persists scored question IDs)
  - --planner flag for A/B comparison (sends planner=true to /chat)

Requires:
  - Local koi-api server running on localhost:8351
  - OPENAI_API_KEY set (used by both /chat and DeepEval metrics)
  - deepeval installed: pip install deepeval

Usage:
  python3 tests/eval/run_eval.py                    # run eval, save report
  python3 tests/eval/run_eval.py --base-url http://localhost:18351  # eval via SSH tunnel
  python3 tests/eval/run_eval.py --tag baseline     # custom tag in filename
  python3 tests/eval/run_eval.py --planner          # send planner=true to /chat
  python3 tests/eval/run_eval.py --multi-query      # enable B8b multi-query expansion
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests

# DeepEval imports
from deepeval.metrics import (
    FaithfulnessMetric,
    AnswerRelevancyMetric,
    ContextualRelevancyMetric,
)
from deepeval.test_case import LLMTestCase


SCRIPT_DIR = Path(__file__).parent
GOLDEN_QA_PATH = SCRIPT_DIR / "golden_qa.json"
RESULTS_DIR = SCRIPT_DIR / "results"

# Pass thresholds
THRESHOLDS = {
    "faithfulness": 0.7,
    "answer_relevancy": 0.6,
    "context_relevancy": 0.6,
}

# ---------------------------------------------------------------------------
# Taxonomy normalization (B9a QueryTaxonomy alignment)
# ---------------------------------------------------------------------------

TAXONOMY_MAP = {
    # Old category names → QueryTaxonomy
    "entity_lookup": "entity_definition",
    "relationship": "relationship_path",
    "multi_hop": "relationship_path",
    "negative": "out_of_domain",
    # New categories (identity mapping)
    "entity_definition": "entity_definition",
    "governance": "governance_policy",
    "governance_policy": "governance_policy",
    "roadmap_status": "roadmap_status",
    "commitment_claim": "commitment_claim",
    "thematic": "entity_definition",  # default for thematic
    "out_of_domain": "out_of_domain",
    "cross_node_provenance": "cross_node_provenance",
}

# Per-question overrides where category default is wrong
TAXONOMY_OVERRIDES = {
    "thematic_2": "governance_policy",  # "How does BKC handle data sovereignty?"
}


def normalize_taxonomy(question_id: str, raw_category: str) -> str:
    """Normalize a golden QA category to a QueryTaxonomy value."""
    if question_id in TAXONOMY_OVERRIDES:
        return TAXONOMY_OVERRIDES[question_id]
    return TAXONOMY_MAP.get(raw_category, raw_category)


# ---------------------------------------------------------------------------
# Evidence recall
# ---------------------------------------------------------------------------

def compute_evidence_recall(
    gold_evidence: list[str] | None,
    retrieval_context: list[str],
    overlap_threshold: float = 0.8,
) -> float | None:
    """Compute evidence recall: fraction of gold_evidence snippets found
    in retrieval_context via case-insensitive substring matching.

    Returns None if gold_evidence is empty/missing (skip metric).
    """
    if not gold_evidence:
        return None

    found = 0
    context_lower = " ".join(retrieval_context).lower()
    for snippet in gold_evidence:
        snippet_lower = snippet.lower()
        # Check if enough of the snippet appears in context
        # Use sliding window: if 80%+ of snippet chars match, count as found
        snippet_len = len(snippet_lower)
        if snippet_len == 0:
            found += 1
            continue
        threshold_len = int(snippet_len * overlap_threshold)
        # Simple check: is a threshold-length substring of snippet in context?
        for start in range(snippet_len - threshold_len + 1):
            window = snippet_lower[start:start + threshold_len]
            if window in context_lower:
                found += 1
                break

    return found / len(gold_evidence)


# ---------------------------------------------------------------------------
# Chat query
# ---------------------------------------------------------------------------

def query_chat(
    base_url: str,
    question: str,
    multi_query: bool = False,
    planner: bool = False,
) -> dict:
    """Hit /chat endpoint and return response."""
    payload = {"query": question}
    if multi_query:
        payload["multi_query"] = True
    if planner:
        payload["planner"] = True
    resp = requests.post(
        f"{base_url}/chat",
        json=payload,
        timeout=60 if (multi_query or planner) else 30,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Main eval loop
# ---------------------------------------------------------------------------

def run_eval(
    base_url: str,
    tag: str = "",
    multi_query: bool = False,
    planner: bool = False,
) -> dict:
    """Run evaluation against all golden QA pairs."""
    with open(GOLDEN_QA_PATH) as f:
        golden_qa = json.load(f)

    # Initialize metrics
    faithfulness = FaithfulnessMetric(threshold=THRESHOLDS["faithfulness"])
    answer_relevancy = AnswerRelevancyMetric(threshold=THRESHOLDS["answer_relevancy"])
    context_relevancy = ContextualRelevancyMetric(threshold=THRESHOLDS["context_relevancy"])

    results = []
    totals = {"faithfulness": [], "answer_relevancy": [], "context_relevancy": []}
    evidence_recalls = []
    errors = []

    # Category-level accumulators
    cat_totals = defaultdict(lambda: {"faithfulness": [], "answer_relevancy": [],
                                       "context_relevancy": [], "passed": 0, "total": 0})

    # Planner telemetry accumulators
    planner_traces = []

    for i, qa in enumerate(golden_qa):
        qid = qa["id"]
        question = qa["question"]
        raw_category = qa["category"]
        norm_category = normalize_taxonomy(qid, raw_category)
        print(f"[{i+1}/{len(golden_qa)}] {qid}: {question[:60]}...", end=" ", flush=True)

        try:
            t0 = time.time()
            chat_resp = query_chat(base_url, question, multi_query=multi_query, planner=planner)
            latency = time.time() - t0

            answer = chat_resp.get("answer", "")
            sources = chat_resp.get("sources", [])
            plan_trace = chat_resp.get("plan_trace")

            # Build retrieval context from sources
            retrieval_context = []
            for s in sources:
                ctx = s.get("description", "") or s.get("label", "")
                if ctx:
                    retrieval_context.append(ctx)

            # Create DeepEval test case
            test_case = LLMTestCase(
                input=question,
                actual_output=answer,
                expected_output=qa["expected_answer"],
                retrieval_context=retrieval_context if retrieval_context else ["(no context retrieved)"],
            )

            # Score each metric
            scores = {}
            for name, metric in [
                ("faithfulness", faithfulness),
                ("answer_relevancy", answer_relevancy),
                ("context_relevancy", context_relevancy),
            ]:
                try:
                    metric.measure(test_case)
                    scores[name] = round(metric.score, 4)
                    totals[name].append(metric.score)
                    cat_totals[norm_category][name].append(metric.score)
                except Exception as e:
                    scores[name] = None
                    print(f"\n  WARNING: {name} metric failed: {e}")

            # Evidence recall (optional, when gold_evidence present)
            gold_evidence = qa.get("gold_evidence")
            ev_recall = compute_evidence_recall(gold_evidence, retrieval_context)
            if ev_recall is not None:
                scores["evidence_recall"] = round(ev_recall, 4)
                evidence_recalls.append(ev_recall)

            passed = all(
                scores.get(k) is not None and scores[k] >= v
                for k, v in THRESHOLDS.items()
            )

            cat_totals[norm_category]["total"] += 1
            if passed:
                cat_totals[norm_category]["passed"] += 1

            result = {
                "id": qid,
                "category": raw_category,
                "normalized_category": norm_category,
                "question": question,
                "answer": answer[:500],
                "sources_count": len(sources),
                "retrieval_context_count": len(retrieval_context),
                "latency_s": round(latency, 2),
                "scores": scores,
                "passed": passed,
            }

            # Record planner telemetry when present
            if plan_trace is not None:
                result["plan_trace"] = plan_trace
                planner_traces.append(plan_trace)

            results.append(result)
            status = "PASS" if passed else "FAIL"
            score_parts = [f"f={scores.get('faithfulness')}", f"ar={scores.get('answer_relevancy')}", f"cr={scores.get('context_relevancy')}"]
            if ev_recall is not None:
                score_parts.append(f"er={scores.get('evidence_recall')}")
            print(f"{status} ({', '.join(score_parts)}) [{latency:.1f}s]")

        except Exception as e:
            print(f"ERROR: {e}")
            errors.append({"id": qid, "error": str(e)})
            cat_totals[norm_category]["total"] += 1
            results.append({
                "id": qid,
                "category": raw_category,
                "normalized_category": norm_category,
                "question": question,
                "error": str(e),
                "passed": False,
            })

    # -----------------------------------------------------------------------
    # Aggregates
    # -----------------------------------------------------------------------
    def avg(lst):
        return round(sum(lst) / len(lst), 4) if lst else None

    # Scored question IDs (for matched-subset comparison)
    scored_ids = [r["id"] for r in results if r.get("scores", {}).get("context_relevancy") is not None]

    # Category-level summary
    categories = {}
    for cat, data in sorted(cat_totals.items()):
        categories[cat] = {
            "total": data["total"],
            "passed": data["passed"],
            "avg_faithfulness": avg(data["faithfulness"]),
            "avg_answer_relevancy": avg(data["answer_relevancy"]),
            "avg_context_relevancy": avg(data["context_relevancy"]),
        }

    # Planner telemetry summary
    planner_summary = None
    if planner_traces:
        fallback_count = sum(1 for t in planner_traces if t.get("fallback"))
        abstain_count = sum(1 for t in planner_traces if t.get("abstained"))
        confidences = [t.get("confidence") for t in planner_traces if t.get("confidence") is not None]
        # Classifier accuracy: compare plan_trace.taxonomy to golden QA normalized category
        correct_classifications = 0
        classification_total = 0
        for r in results:
            pt = r.get("plan_trace")
            if pt and pt.get("taxonomy"):
                classification_total += 1
                if pt["taxonomy"] == r.get("normalized_category"):
                    correct_classifications += 1
        planner_summary = {
            "total_traces": len(planner_traces),
            "fallback_rate": round(fallback_count / len(planner_traces), 4) if planner_traces else None,
            "abstention_rate": round(abstain_count / len(planner_traces), 4) if planner_traces else None,
            "avg_confidence": avg(confidences),
            "classifier_accuracy": round(correct_classifications / classification_total, 4) if classification_total else None,
        }

    summary = {
        "total": len(golden_qa),
        "passed": sum(1 for r in results if r.get("passed")),
        "failed": sum(1 for r in results if not r.get("passed")),
        "errors": len(errors),
        "scored_count": len(scored_ids),
        "avg_scores": {k: avg(v) for k, v in totals.items()},
        "avg_evidence_recall": avg(evidence_recalls) if evidence_recalls else None,
        "avg_latency_s": avg([r["latency_s"] for r in results if "latency_s" in r]),
        "thresholds": THRESHOLDS,
        "categories": categories,
    }
    if planner_summary:
        summary["planner"] = planner_summary

    report = {
        "timestamp": datetime.now().isoformat(),
        "base_url": base_url,
        "tag": tag,
        "planner_enabled": planner,
        "multi_query_enabled": multi_query,
        "scored_ids": scored_ids,
        "summary": summary,
        "results": results,
    }

    # Save report
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    tag_suffix = f"-{tag}" if tag else ""
    filename = f"{tag_suffix or 'eval'}-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.json"
    report_path = RESULTS_DIR / filename
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    # -----------------------------------------------------------------------
    # Print summary
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print(f"EVALUATION SUMMARY ({tag or 'default'})")
    print("=" * 70)
    print(f"  Total:   {summary['total']}")
    print(f"  Scored:  {summary['scored_count']}")
    print(f"  Passed:  {summary['passed']}")
    print(f"  Failed:  {summary['failed']}")
    print(f"  Errors:  {summary['errors']}")
    print(f"  Avg faithfulness:       {summary['avg_scores']['faithfulness']}")
    print(f"  Avg answer_relevancy:   {summary['avg_scores']['answer_relevancy']}")
    print(f"  Avg context_relevancy:  {summary['avg_scores']['context_relevancy']}")
    if summary['avg_evidence_recall'] is not None:
        print(f"  Avg evidence_recall:    {summary['avg_evidence_recall']}")
    print(f"  Avg latency:            {summary['avg_latency_s']}s")

    # Category table
    print(f"\n  {'Category':<25s} {'CR':>6s} {'AR':>6s} {'F':>6s} {'Pass':>6s}  n")
    print(f"  {'-'*60}")
    for cat, data in sorted(categories.items()):
        cr = f"{data['avg_context_relevancy']:.3f}" if data['avg_context_relevancy'] is not None else "  N/A"
        ar = f"{data['avg_answer_relevancy']:.3f}" if data['avg_answer_relevancy'] is not None else "  N/A"
        f_ = f"{data['avg_faithfulness']:.3f}" if data['avg_faithfulness'] is not None else "  N/A"
        pr = f"{data['passed']}/{data['total']}"
        print(f"  {cat:<25s} {cr:>6s} {ar:>6s} {f_:>6s} {pr:>6s}  {data['total']}")

    # Planner telemetry
    if planner_summary:
        print(f"\n  Planner telemetry:")
        print(f"    Fallback rate:        {planner_summary['fallback_rate']}")
        print(f"    Abstention rate:      {planner_summary['abstention_rate']}")
        print(f"    Avg confidence:       {planner_summary['avg_confidence']}")
        print(f"    Classifier accuracy:  {planner_summary['classifier_accuracy']}")

    print(f"\n  Report saved: {report_path}")
    print("=" * 70)

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run RAG evaluation")
    parser.add_argument("--base-url", default="http://localhost:8351", help="KOI API base URL")
    parser.add_argument("--tag", default="", help="Tag for the report filename")
    parser.add_argument("--multi-query", action="store_true", help="Enable B8b multi-query expansion")
    parser.add_argument("--planner", action="store_true", help="Send planner=true to /chat (B9a)")
    args = parser.parse_args()

    run_eval(args.base_url, args.tag, multi_query=args.multi_query, planner=args.planner)
