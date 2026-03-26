#!/usr/bin/env python3
"""
B5 — Automated evaluation pipeline for /chat RAG endpoint.

Requires:
  - Local koi-api server running on localhost:8351
  - OPENAI_API_KEY set (used by both /chat and DeepEval metrics)
  - deepeval installed: pip install deepeval

Usage:
  python3 tests/eval/run_eval.py                    # run eval, save report
  python3 tests/eval/run_eval.py --base-url http://45.132.245.30:8351  # eval against Octo
  python3 tests/eval/run_eval.py --tag baseline     # custom tag in filename
"""

import argparse
import json
import os
import sys
import time
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


def query_chat(base_url: str, question: str, multi_query: bool = False) -> dict:
    """Hit /chat endpoint and return response."""
    payload = {"query": question}
    if multi_query:
        payload["multi_query"] = True
    resp = requests.post(
        f"{base_url}/chat",
        json=payload,
        timeout=60 if multi_query else 30,  # multi-query needs more time
    )
    resp.raise_for_status()
    return resp.json()


def run_eval(base_url: str, tag: str = "", multi_query: bool = False) -> dict:
    """Run evaluation against all golden QA pairs."""
    with open(GOLDEN_QA_PATH) as f:
        golden_qa = json.load(f)

    # Initialize metrics
    faithfulness = FaithfulnessMetric(threshold=THRESHOLDS["faithfulness"])
    answer_relevancy = AnswerRelevancyMetric(threshold=THRESHOLDS["answer_relevancy"])
    context_relevancy = ContextualRelevancyMetric(threshold=THRESHOLDS["context_relevancy"])

    results = []
    totals = {"faithfulness": [], "answer_relevancy": [], "context_relevancy": []}
    errors = []

    for i, qa in enumerate(golden_qa):
        qid = qa["id"]
        question = qa["question"]
        print(f"[{i+1}/{len(golden_qa)}] {qid}: {question[:60]}...", end=" ", flush=True)

        try:
            t0 = time.time()
            chat_resp = query_chat(base_url, question, multi_query=multi_query)
            latency = time.time() - t0

            answer = chat_resp.get("answer", "")
            sources = chat_resp.get("sources", [])

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
                except Exception as e:
                    scores[name] = None
                    print(f"\n  WARNING: {name} metric failed: {e}")

            passed = all(
                scores.get(k) is not None and scores[k] >= v
                for k, v in THRESHOLDS.items()
            )

            result = {
                "id": qid,
                "category": qa["category"],
                "question": question,
                "answer": answer[:500],
                "sources_count": len(sources),
                "retrieval_context_count": len(retrieval_context),
                "latency_s": round(latency, 2),
                "scores": scores,
                "passed": passed,
            }
            results.append(result)
            status = "PASS" if passed else "FAIL"
            print(f"{status} (f={scores.get('faithfulness')}, ar={scores.get('answer_relevancy')}, cr={scores.get('context_relevancy')}) [{latency:.1f}s]")

        except Exception as e:
            print(f"ERROR: {e}")
            errors.append({"id": qid, "error": str(e)})
            results.append({
                "id": qid,
                "category": qa["category"],
                "question": question,
                "error": str(e),
                "passed": False,
            })

    # Compute aggregates
    def avg(lst):
        return round(sum(lst) / len(lst), 4) if lst else None

    summary = {
        "total": len(golden_qa),
        "passed": sum(1 for r in results if r.get("passed")),
        "failed": sum(1 for r in results if not r.get("passed")),
        "errors": len(errors),
        "avg_scores": {k: avg(v) for k, v in totals.items()},
        "avg_latency_s": avg([r["latency_s"] for r in results if "latency_s" in r]),
        "thresholds": THRESHOLDS,
    }

    report = {
        "timestamp": datetime.now().isoformat(),
        "base_url": base_url,
        "tag": tag,
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

    # Print summary
    print("\n" + "=" * 60)
    print(f"EVALUATION SUMMARY ({tag or 'default'})")
    print("=" * 60)
    print(f"  Total:   {summary['total']}")
    print(f"  Passed:  {summary['passed']}")
    print(f"  Failed:  {summary['failed']}")
    print(f"  Errors:  {summary['errors']}")
    print(f"  Avg faithfulness:       {summary['avg_scores']['faithfulness']}")
    print(f"  Avg answer_relevancy:   {summary['avg_scores']['answer_relevancy']}")
    print(f"  Avg context_relevancy:  {summary['avg_scores']['context_relevancy']}")
    print(f"  Avg latency:            {summary['avg_latency_s']}s")
    print(f"  Report saved: {report_path}")
    print("=" * 60)

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run RAG evaluation")
    parser.add_argument("--base-url", default="http://localhost:8351", help="KOI API base URL")
    parser.add_argument("--tag", default="", help="Tag for the report filename (e.g. 'baseline', 'post-bm25')")
    parser.add_argument("--multi-query", action="store_true", help="Enable B8b multi-query expansion in /chat requests")
    args = parser.parse_args()

    run_eval(args.base_url, args.tag, multi_query=args.multi_query)
