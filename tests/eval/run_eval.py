#!/usr/bin/env python3
"""
B5/B9a — Automated evaluation pipeline for /chat RAG endpoint.

Supports:
  - Category-level reporting via TAXONOMY_MAP normalization
  - Evidence recall metric (when gold_evidence present)
  - Planner telemetry (plan_trace recording + aggregation)
  - Matched-subset comparison (persists scored question IDs)
  - --planner flag for A/B comparison (sends planner=true to /chat)
  - Per-question checkpoint (resume after quota failure)
  - Retry/backoff on DeepEval metric scoring
  - Raw /chat response + plan_trace persisted per question
  - --resume <report-path> to continue from checkpoint
  - --compare <report-a> <report-b> for matched-subset A/B analysis
  - --ids for subset runs (comma-separated question IDs)
  - --metrics for single-metric runs (comma-separated metric names)
  - --rescore for offline rescoring of saved reports with a different judge model

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
  python3 tests/eval/run_eval.py --resume results/my-report.json   # resume from checkpoint
  python3 tests/eval/run_eval.py --compare results/a.json results/b.json  # A/B comparison
  python3 tests/eval/run_eval.py --ids commitment_claim_1,commitment_claim_2 --metrics context_relevancy
  python3 tests/eval/run_eval.py --rescore results/report.json --eval-model gpt-4.1 --metrics context_relevancy
  python3 tests/eval/run_eval.py --answer-mode explainer --ids relationship_5,multi_hop_1 --tag brief-subset  # structured brief eval

Subset compare workflow (Phase 4/5):
  # Run default + planner with same judge, then compare:
  python3 tests/eval/run_eval.py --ids commitment_claim_1,...,commitment_claim_5 --metrics context_relevancy --eval-model gpt-4.1-mini --tag default-subset
  python3 tests/eval/run_eval.py --ids commitment_claim_1,...,commitment_claim_5 --metrics context_relevancy --eval-model gpt-4.1-mini --planner --tag planner-subset
  python3 tests/eval/run_eval.py --compare results/-default-subset-*.json results/-planner-subset-*.json
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

# Retry config for DeepEval metric scoring
METRIC_MAX_RETRIES = 3
METRIC_RETRY_BASE_DELAY = 5  # seconds, doubles each retry

# Canonical scoring model — gate thresholds are calibrated against this model.
# gpt-4o-mini available via --eval-model for cheap dev iterations.
CANONICAL_EVAL_MODEL = "gpt-4.1"

# ---------------------------------------------------------------------------
# Taxonomy normalization (B9a QueryTaxonomy alignment)
# ---------------------------------------------------------------------------

TAXONOMY_MAP = {
    # Old category names -> QueryTaxonomy
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
        snippet_len = len(snippet_lower)
        if snippet_len == 0:
            found += 1
            continue
        threshold_len = int(snippet_len * overlap_threshold)
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
    answer_mode: str = "default",
) -> dict:
    """Hit /chat endpoint and return response."""
    payload = {"query": question}
    if multi_query:
        payload["multi_query"] = True
    if planner:
        payload["planner"] = True
    if answer_mode != "default":
        payload["answer_mode"] = answer_mode
    resp = requests.post(
        f"{base_url}/chat",
        json=payload,
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Metric scoring with retry/backoff
# ---------------------------------------------------------------------------

def score_metric_with_retry(metric, test_case, metric_name: str) -> float | None:
    """Score a single DeepEval metric with exponential backoff retry.
    Returns the score or None if all retries fail."""
    for attempt in range(METRIC_MAX_RETRIES):
        try:
            metric.measure(test_case)
            return round(metric.score, 4)
        except Exception as e:
            delay = METRIC_RETRY_BASE_DELAY * (2 ** attempt)
            if attempt < METRIC_MAX_RETRIES - 1:
                print(f"\n  RETRY {attempt+1}/{METRIC_MAX_RETRIES}: {metric_name} failed ({e}), waiting {delay}s...", end=" ", flush=True)
                time.sleep(delay)
            else:
                print(f"\n  FAILED: {metric_name} after {METRIC_MAX_RETRIES} attempts: {e}")
                return None


# ---------------------------------------------------------------------------
# Checkpoint management
# ---------------------------------------------------------------------------

def save_checkpoint(report_path: Path, report: dict):
    """Save current report state as checkpoint."""
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)


def load_checkpoint(report_path: Path) -> dict | None:
    """Load existing checkpoint report. Returns None if not found."""
    if report_path.exists():
        with open(report_path) as f:
            return json.load(f)
    return None


def check_resume_model_integrity(checkpoint: dict, eval_model: str) -> str | None:
    """Check that checkpoint's eval_model matches the requested eval_model.
    Returns an error message string if integrity check fails, None if OK."""
    checkpoint_model = checkpoint.get("eval_model")
    has_scored = any(
        r.get("scores", {}).get("context_relevancy") is not None
        for r in checkpoint.get("results", [])
    )
    if has_scored and not checkpoint_model:
        return (
            "Checkpoint has scored results but no eval_model metadata. "
            "Cannot verify scoring model consistency. "
            "Start a fresh run (without --resume)."
        )
    if checkpoint_model and checkpoint_model != eval_model:
        return (
            f"Checkpoint was scored with '{checkpoint_model}' but "
            f"'--eval-model {eval_model}' was requested. "
            f"Cannot mix scoring models in one report. "
            f"Either use '--eval-model {checkpoint_model}' or start fresh."
        )
    return None


# ---------------------------------------------------------------------------
# Aggregate computation (shared by run_eval and compare)
# ---------------------------------------------------------------------------

def compute_aggregates(results: list[dict], golden_qa: list[dict] | None = None) -> dict:
    """Compute summary aggregates from a list of result dicts.

    known_limit questions are still included in results (for visibility) but
    are excluded from all canonical gate calculations (totals, categories,
    pass/fail, planner telemetry).
    """
    def avg(lst):
        return round(sum(lst) / len(lst), 4) if lst else None

    # Partition: known-limit questions run + appear in reports but don't affect gate math.
    known_limit_results = [r for r in results if r.get("known_limit")]
    canonical_results = [r for r in results if not r.get("known_limit")]

    totals = {"faithfulness": [], "answer_relevancy": [], "context_relevancy": []}
    evidence_recalls = []
    cat_totals = defaultdict(lambda: {"faithfulness": [], "answer_relevancy": [],
                                       "context_relevancy": [], "passed": 0, "total": 0})
    planner_traces = []

    for r in canonical_results:
        norm_category = r.get("normalized_category", "unknown")
        scores = r.get("scores", {})
        cat_totals[norm_category]["total"] += 1

        if r.get("passed"):
            cat_totals[norm_category]["passed"] += 1

        for k in ["faithfulness", "answer_relevancy", "context_relevancy"]:
            v = scores.get(k)
            if v is not None:
                totals[k].append(v)
                cat_totals[norm_category][k].append(v)

        er = scores.get("evidence_recall")
        if er is not None:
            evidence_recalls.append(er)

        pt = r.get("plan_trace")
        if pt is not None:
            planner_traces.append(pt)

    scored_ids = [r["id"] for r in canonical_results if r.get("scores", {}).get("context_relevancy") is not None]

    categories = {}
    for cat, data in sorted(cat_totals.items()):
        categories[cat] = {
            "total": data["total"],
            "passed": data["passed"],
            "avg_faithfulness": avg(data["faithfulness"]),
            "avg_answer_relevancy": avg(data["answer_relevancy"]),
            "avg_context_relevancy": avg(data["context_relevancy"]),
        }

    planner_summary = None
    if planner_traces:
        fallback_count = sum(1 for t in planner_traces if t.get("fallback"))
        abstain_count = sum(1 for t in planner_traces if t.get("abstained"))
        confidences = [t.get("confidence") for t in planner_traces if t.get("confidence") is not None]
        correct = 0
        class_total = 0
        for r in results:
            pt = r.get("plan_trace")
            if pt and pt.get("taxonomy"):
                class_total += 1
                if pt["taxonomy"] == r.get("normalized_category"):
                    correct += 1
        # OOD abstention accuracy
        ood_total = sum(1 for r in results if r.get("normalized_category") == "out_of_domain")
        ood_abstained = sum(
            1 for r in results
            if r.get("normalized_category") == "out_of_domain"
            and r.get("plan_trace", {}).get("abstained")
        )
        planner_summary = {
            "total_traces": len(planner_traces),
            "fallback_rate": round(fallback_count / len(planner_traces), 4) if planner_traces else None,
            "abstention_rate": round(abstain_count / len(planner_traces), 4) if planner_traces else None,
            "avg_confidence": avg(confidences),
            "classifier_accuracy": round(correct / class_total, 4) if class_total else None,
            "ood_total": ood_total,
            "ood_abstained": ood_abstained,
            "ood_abstention_accuracy": round(ood_abstained / ood_total, 4) if ood_total else None,
        }

    total_questions = len(canonical_results)
    summary = {
        "total": total_questions,
        "passed": sum(1 for r in canonical_results if r.get("passed")),
        "failed": sum(1 for r in canonical_results if not r.get("passed")),
        "errors": sum(1 for r in canonical_results if r.get("error")),
        "scored_count": len(scored_ids),
        "avg_scores": {k: avg(v) for k, v in totals.items()},
        "avg_evidence_recall": avg(evidence_recalls) if evidence_recalls else None,
        "avg_latency_s": avg([r["latency_s"] for r in results if "latency_s" in r]),
        "thresholds": THRESHOLDS,
        "categories": categories,
    }
    if planner_summary:
        summary["planner"] = planner_summary
    if known_limit_results:
        summary["known_limit_total"] = len(known_limit_results)
        summary["known_limit_ids"] = [r["id"] for r in known_limit_results]

    return summary, scored_ids


# ---------------------------------------------------------------------------
# Print summary
# ---------------------------------------------------------------------------

def print_summary(summary: dict, tag: str = "", report_path: Path | None = None):
    """Print formatted summary to stdout."""
    print("\n" + "=" * 70)
    print(f"EVALUATION SUMMARY ({tag or 'default'})")
    print("=" * 70)
    print(f"  Total:   {summary['total']}")
    print(f"  Scored:  {summary['scored_count']}")
    print(f"  Passed:  {summary['passed']}")
    print(f"  Failed:  {summary['failed']}")
    print(f"  Errors:  {summary['errors']}")
    if summary.get("known_limit_total", 0) > 0:
        kl_ids = ", ".join(summary.get("known_limit_ids", []))
        print(f"  Known-limit (excluded from gate): {summary['known_limit_total']} ({kl_ids})")
    print(f"  Avg faithfulness:       {summary['avg_scores']['faithfulness']}")
    print(f"  Avg answer_relevancy:   {summary['avg_scores']['answer_relevancy']}")
    print(f"  Avg context_relevancy:  {summary['avg_scores']['context_relevancy']}")
    if summary.get('avg_evidence_recall') is not None:
        print(f"  Avg evidence_recall:    {summary['avg_evidence_recall']}")
    print(f"  Avg latency:            {summary['avg_latency_s']}s")

    categories = summary.get("categories", {})
    print(f"\n  {'Category':<25s} {'CR':>6s} {'AR':>6s} {'F':>6s} {'Pass':>6s}  n")
    print(f"  {'-'*60}")
    for cat, data in sorted(categories.items()):
        cr = f"{data['avg_context_relevancy']:.3f}" if data['avg_context_relevancy'] is not None else "  N/A"
        ar = f"{data['avg_answer_relevancy']:.3f}" if data['avg_answer_relevancy'] is not None else "  N/A"
        f_ = f"{data['avg_faithfulness']:.3f}" if data['avg_faithfulness'] is not None else "  N/A"
        pr = f"{data['passed']}/{data['total']}"
        print(f"  {cat:<25s} {cr:>6s} {ar:>6s} {f_:>6s} {pr:>6s}  {data['total']}")

    planner_summary = summary.get("planner")
    if planner_summary:
        print(f"\n  Planner telemetry:")
        print(f"    Fallback rate:        {planner_summary['fallback_rate']}")
        print(f"    Abstention rate:      {planner_summary['abstention_rate']}")
        print(f"    Avg confidence:       {planner_summary['avg_confidence']}")
        print(f"    Classifier accuracy:  {planner_summary['classifier_accuracy']}")
        ood_acc = planner_summary.get('ood_abstention_accuracy')
        ood_total = planner_summary.get('ood_total', 0)
        if ood_acc is not None:
            ood_abstained = planner_summary.get('ood_abstained', 0)
            print(f"    OOD abstention:       {ood_acc:.1%} ({ood_abstained}/{ood_total})")

    if report_path:
        print(f"\n  Report saved: {report_path}")
    print("=" * 70)


# ---------------------------------------------------------------------------
# Main eval loop
# ---------------------------------------------------------------------------

def run_eval(
    base_url: str,
    tag: str = "",
    multi_query: bool = False,
    planner: bool = False,
    resume_path: str | None = None,
    eval_model: str = CANONICAL_EVAL_MODEL,
    question_ids: list[str] | None = None,
    metric_names: list[str] | None = None,
    answer_mode: str = "default",
) -> dict:
    """Run evaluation against golden QA pairs with checkpoint/resume.

    Args:
        question_ids: If set, only run these question IDs (subset mode).
        metric_names: If set, only score these metrics (e.g. ["context_relevancy"]).
        answer_mode: If set to 'explainer', passes answer_mode=explainer to /chat.
    """
    with open(GOLDEN_QA_PATH) as f:
        golden_qa = json.load(f)

    # Filter to subset if --ids provided
    if question_ids:
        all_ids = {qa["id"] for qa in golden_qa}
        unknown = set(question_ids) - all_ids
        if unknown:
            print(f"WARNING: Unknown question IDs (skipped): {unknown}")
        golden_qa = [qa for qa in golden_qa if qa["id"] in set(question_ids)]
        if not golden_qa:
            print("ERROR: No matching questions found for --ids")
            sys.exit(1)
        print(f"Subset mode: {len(golden_qa)} questions selected")

    # Validate metric names
    valid_metrics = {"faithfulness", "answer_relevancy", "context_relevancy"}
    if metric_names:
        bad = set(metric_names) - valid_metrics
        if bad:
            print(f"ERROR: Unknown metrics: {bad}. Valid: {valid_metrics}")
            sys.exit(1)
        print(f"Metrics mode: scoring only {metric_names}")

    # Determine report path
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    tag_suffix = f"-{tag}" if tag else ""

    # Resume from checkpoint or start fresh
    completed_ids = {}  # id -> result dict
    if resume_path:
        resume_file = Path(resume_path)
        if not resume_file.is_absolute():
            resume_file = RESULTS_DIR / resume_file.name
        checkpoint = load_checkpoint(resume_file)
        if checkpoint:
            # Resume model integrity — fail closed on mismatch or unknown
            integrity_error = check_resume_model_integrity(checkpoint, eval_model)
            if integrity_error:
                print(f"ERROR: {integrity_error}")
                sys.exit(1)

            for r in checkpoint.get("results", []):
                # Only count as completed if it has scores (not just /chat response)
                if r.get("scores", {}).get("context_relevancy") is not None:
                    completed_ids[r["id"]] = r
                elif r.get("error"):
                    # Don't skip errored questions — retry them
                    pass
                elif r.get("chat_response"):
                    # Has /chat response but no scores — re-score only
                    completed_ids[r["id"]] = r
                    completed_ids[r["id"]]["_needs_scoring"] = True
            print(f"Resuming: {len(completed_ids)} questions loaded ({sum(1 for v in completed_ids.values() if not v.get('_needs_scoring'))} fully scored, {sum(1 for v in completed_ids.values() if v.get('_needs_scoring'))} need scoring)")
            report_path = resume_file
        else:
            print(f"WARNING: Resume file not found: {resume_file}, starting fresh")
            filename = f"{tag_suffix or 'eval'}-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.json"
            report_path = RESULTS_DIR / filename
    else:
        filename = f"{tag_suffix or 'eval'}-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.json"
        report_path = RESULTS_DIR / filename

    # Initialize only requested metrics
    active_metrics = metric_names or list(valid_metrics)
    metrics_map = {}
    if "faithfulness" in active_metrics:
        metrics_map["faithfulness"] = FaithfulnessMetric(threshold=THRESHOLDS["faithfulness"], model=eval_model)
    if "answer_relevancy" in active_metrics:
        metrics_map["answer_relevancy"] = AnswerRelevancyMetric(threshold=THRESHOLDS["answer_relevancy"], model=eval_model)
    if "context_relevancy" in active_metrics:
        metrics_map["context_relevancy"] = ContextualRelevancyMetric(threshold=THRESHOLDS["context_relevancy"], model=eval_model)

    results = []

    for i, qa in enumerate(golden_qa):
        qid = qa["id"]
        question = qa["question"]
        raw_category = qa["category"]
        norm_category = normalize_taxonomy(qid, raw_category)

        # Check if already completed in checkpoint
        if qid in completed_ids and not completed_ids[qid].get("_needs_scoring"):
            results.append(completed_ids[qid])
            scores = completed_ids[qid].get("scores", {})
            cr = scores.get("context_relevancy", "?")
            print(f"[{i+1}/{len(golden_qa)}] {qid}: CACHED (cr={cr})")
            continue

        print(f"[{i+1}/{len(golden_qa)}] {qid}: {question[:60]}...", end=" ", flush=True)

        # Phase 1: Get /chat response (or reuse from checkpoint)
        chat_resp = None
        latency = None
        if qid in completed_ids and completed_ids[qid].get("chat_response"):
            chat_resp = completed_ids[qid]["chat_response"]
            latency = completed_ids[qid].get("latency_s", 0)
            print("(cached response)", end=" ", flush=True)
        else:
            try:
                t0 = time.time()
                chat_resp = query_chat(base_url, question, multi_query=multi_query, planner=planner, answer_mode=answer_mode)
                latency = round(time.time() - t0, 2)
            except Exception as e:
                print(f"ERROR: {e}")
                result = {
                    "id": qid,
                    "category": raw_category,
                    "normalized_category": norm_category,
                    "question": question,
                    "error": str(e),
                    "passed": False,
                    "known_limit": qa.get("known_limit", False),
                }
                results.append(result)
                # Checkpoint after each question
                _save_incremental(report_path, results, golden_qa, tag, base_url, planner, multi_query, eval_model, answer_mode)
                continue

        answer = chat_resp.get("answer", "")
        sources = chat_resp.get("sources", [])
        plan_trace = chat_resp.get("plan_trace")

        # Build retrieval context from sources
        retrieval_context = []
        for s in sources:
            ctx = s.get("description", "") or s.get("label", "")
            if ctx:
                retrieval_context.append(ctx)

        # Phase 2: Score metrics with retry/backoff
        test_case = LLMTestCase(
            input=question,
            actual_output=answer,
            expected_output=qa["expected_answer"],
            retrieval_context=retrieval_context if retrieval_context else ["(no context retrieved)"],
        )

        scores = {}
        for name, metric in metrics_map.items():
            scores[name] = score_metric_with_retry(metric, test_case, name)

        # Evidence recall (optional)
        gold_evidence = qa.get("gold_evidence")
        ev_recall = compute_evidence_recall(gold_evidence, retrieval_context)
        if ev_recall is not None:
            scores["evidence_recall"] = round(ev_recall, 4)

        # Only check thresholds for metrics we actually scored
        passed = all(
            scores.get(k) is not None and scores[k] >= v
            for k, v in THRESHOLDS.items()
            if k in active_metrics
        )

        # Persist all sources for explainer mode (needed to validate S# ref resolution);
        # cap at 20 for default mode to keep report sizes manageable.
        _sources_to_persist = sources if answer_mode == "explainer" else sources[:20]
        _chat_response = {
            "answer": answer,
            "sources_count": len(sources),
            "sources": [
                {
                    "label": s.get("label", ""),
                    "uri": s.get("uri", ""),
                    "entity_type": s.get("entity_type", s.get("type", "")),
                    "ref": s.get("ref"),
                }
                for s in _sources_to_persist
            ],
            "intent": chat_resp.get("intent"),
            "retrieval_mode": chat_resp.get("retrieval_mode"),
        }
        if answer_mode == "explainer":
            _chat_response["brief_payload_version"] = chat_resp.get("brief_payload_version")
            _chat_response["brief_payload"] = chat_resp.get("brief_payload")

        result = {
            "id": qid,
            "category": raw_category,
            "normalized_category": norm_category,
            "question": question,
            "answer": answer[:500],
            "chat_response": _chat_response,
            "sources_count": len(sources),
            "retrieval_context_count": len(retrieval_context),
            "retrieval_context": retrieval_context,
            "latency_s": latency,
            "scores": scores,
            "passed": passed,
            "known_limit": qa.get("known_limit", False),
        }

        if plan_trace is not None:
            result["plan_trace"] = plan_trace
            result["chat_response"]["plan_trace"] = plan_trace

        results.append(result)

        status = "PASS" if passed else "FAIL"
        score_parts = [f"f={scores.get('faithfulness')}", f"ar={scores.get('answer_relevancy')}", f"cr={scores.get('context_relevancy')}"]
        if ev_recall is not None:
            score_parts.append(f"er={scores.get('evidence_recall')}")
        print(f"{status} ({', '.join(score_parts)}) [{latency:.1f}s]")

        # Checkpoint after each question
        _save_incremental(report_path, results, golden_qa, tag, base_url, planner, multi_query, eval_model, answer_mode)

    # Final aggregation and save
    summary, scored_ids = compute_aggregates(results)

    report = {
        "timestamp": datetime.now().isoformat(),
        "base_url": base_url,
        "tag": tag,
        "eval_model": eval_model,
        "planner_enabled": planner,
        "multi_query_enabled": multi_query,
        "answer_mode": answer_mode,
        "scored_ids": scored_ids,
        "summary": summary,
        "results": results,
    }

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    print_summary(summary, tag, report_path)
    return report


def _save_incremental(report_path, results, golden_qa, tag, base_url, planner, multi_query, eval_model=None, answer_mode="default"):
    """Save checkpoint after each question completion."""
    summary, scored_ids = compute_aggregates(results)
    report = {
        "timestamp": datetime.now().isoformat(),
        "base_url": base_url,
        "tag": tag,
        "eval_model": eval_model,
        "planner_enabled": planner,
        "multi_query_enabled": multi_query,
        "answer_mode": answer_mode,
        "scored_ids": scored_ids,
        "summary": summary,
        "results": results,
        "_checkpoint": True,
    }
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)


# ---------------------------------------------------------------------------
# A/B Comparison
# ---------------------------------------------------------------------------

def _orient_comparison(report_a, report_b, path_a, path_b):
    """Normalize so baseline=default, candidate=planner. Returns dict."""
    planner_a = report_a.get("planner_enabled", False)
    planner_b = report_b.get("planner_enabled", False)
    results_a = {r["id"]: r for r in report_a.get("results", [])}
    results_b = {r["id"]: r for r in report_b.get("results", [])}

    if planner_a and not planner_b:
        # Args swapped — normalize
        return {
            "baseline_report": report_b, "candidate_report": report_a,
            "baseline_results": results_b, "candidate_results": results_a,
            "baseline_path": path_b, "candidate_path": path_a,
            "baseline_label": f"{report_b.get('tag', 'A') or 'A'} (default/baseline)",
            "candidate_label": f"{report_a.get('tag', 'B') or 'B'} (planner/candidate)",
        }
    elif not planner_a and planner_b:
        return {
            "baseline_report": report_a, "candidate_report": report_b,
            "baseline_results": results_a, "candidate_results": results_b,
            "baseline_path": path_a, "candidate_path": path_b,
            "baseline_label": f"{report_a.get('tag', 'A') or 'A'} (default/baseline)",
            "candidate_label": f"{report_b.get('tag', 'B') or 'B'} (planner/candidate)",
        }
    elif planner_a and planner_b:
        print("WARNING: Both reports have planner enabled — using arg order")
        return {
            "baseline_report": report_a, "candidate_report": report_b,
            "baseline_results": results_a, "candidate_results": results_b,
            "baseline_path": path_a, "candidate_path": path_b,
            "baseline_label": f"{report_a.get('tag', 'A') or 'A'} (baseline)",
            "candidate_label": f"{report_b.get('tag', 'B') or 'B'} (candidate)",
        }
    else:
        # Neither is planner — arg order, abstention gate will SKIP
        return {
            "baseline_report": report_a, "candidate_report": report_b,
            "baseline_results": results_a, "candidate_results": results_b,
            "baseline_path": path_a, "candidate_path": path_b,
            "baseline_label": f"{report_a.get('tag', 'A') or 'A'} (baseline)",
            "candidate_label": f"{report_b.get('tag', 'B') or 'B'} (candidate)",
        }


def compare_reports(path_a: str, path_b: str):
    """Compare two eval reports on their matched scored-question intersection."""
    with open(path_a) as f:
        report_a = json.load(f)
    with open(path_b) as f:
        report_b = json.load(f)

    # Normalize orientation: baseline=default, candidate=planner
    ori = _orient_comparison(report_a, report_b, path_a, path_b)
    baseline_report = ori["baseline_report"]
    candidate_report = ori["candidate_report"]
    baseline_results = ori["baseline_results"]
    candidate_results = ori["candidate_results"]
    baseline_label = ori["baseline_label"]
    candidate_label = ori["candidate_label"]

    # Find intersection of scored questions (both have CR scores).
    # known_limit questions are excluded from the gate intersection — they still
    # appear in each report's results list but don't affect gate pass/fail.
    scored_base = {rid for rid, r in baseline_results.items()
                   if r.get("scores", {}).get("context_relevancy") is not None
                   and not r.get("known_limit")}
    scored_cand = {rid for rid, r in candidate_results.items()
                   if r.get("scores", {}).get("context_relevancy") is not None
                   and not r.get("known_limit")}
    matched_ids = sorted(scored_base & scored_cand)

    # Report known-limit exclusions for transparency
    known_limit_ids = sorted({rid for rid, r in baseline_results.items() if r.get("known_limit")}
                              | {rid for rid, r in candidate_results.items() if r.get("known_limit")})
    if known_limit_ids:
        print(f"  Known-limit (excluded from gate): {known_limit_ids}")

    print("\n" + "=" * 80)
    print(f"A/B COMPARISON: {baseline_label} vs {candidate_label}")
    print("=" * 80)
    print(f"  Baseline:  {ori['baseline_path']}")
    print(f"  Candidate: {ori['candidate_path']}")
    print(f"  Scored in baseline:  {len(scored_base)}")
    print(f"  Scored in candidate: {len(scored_cand)}")
    print(f"  Matched intersection: {len(matched_ids)}")

    # Eval model metadata
    baseline_model = baseline_report.get("eval_model", "(not recorded)")
    candidate_model = candidate_report.get("eval_model", "(not recorded)")
    print(f"  Eval model (baseline):   {baseline_model}")
    print(f"  Eval model (candidate):  {candidate_model}")

    canonical_comparison = (
        baseline_model == CANONICAL_EVAL_MODEL and
        candidate_model == CANONICAL_EVAL_MODEL
    )
    if baseline_model != candidate_model:
        print(f"  WARNING: Different scoring models — metrics may not be comparable")
    if not canonical_comparison:
        print(f"  NOTE: Non-canonical scoring — gate verdict is informational only")

    if not matched_ids:
        print("  NO MATCHED QUESTIONS — cannot compare")
        return {"gate_pass": False, "canonical": canonical_comparison}

    # Compute matched-subset metrics
    def matched_avg(results_dict, ids, metric_key):
        vals = [results_dict[qid]["scores"][metric_key] for qid in ids
                if results_dict[qid].get("scores", {}).get(metric_key) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    def matched_pass_rate(results_dict, ids):
        passed = sum(1 for qid in ids if results_dict[qid].get("passed"))
        return round(passed / len(ids), 4) if ids else None

    def matched_avg_latency(results_dict, ids):
        lats = [results_dict[qid]["latency_s"] for qid in ids if "latency_s" in results_dict[qid]]
        return round(sum(lats) / len(lats), 2) if lats else None

    metrics = ["context_relevancy", "answer_relevancy", "faithfulness"]
    print(f"\n  {'Metric':<25s} {'Base':>8s} {'Cand':>8s} {'Delta':>8s} {'%':>7s}")
    print(f"  {'-'*56}")

    for m in metrics:
        vbase = matched_avg(baseline_results, matched_ids, m)
        vcand = matched_avg(candidate_results, matched_ids, m)
        if vbase is not None and vcand is not None:
            delta = round(vcand - vbase, 4)
            pct = f"{delta/vbase*100:+.1f}%" if vbase > 0 else "N/A"
        else:
            delta = "N/A"
            pct = "N/A"
        print(f"  {m:<25s} {str(vbase):>8s} {str(vcand):>8s} {str(delta):>8s} {pct:>7s}")

    # Pass rate
    pr_base = matched_pass_rate(baseline_results, matched_ids)
    pr_cand = matched_pass_rate(candidate_results, matched_ids)
    pr_delta = round(pr_cand - pr_base, 4) if pr_base is not None and pr_cand is not None else "N/A"
    print(f"  {'pass_rate':<25s} {str(pr_base):>8s} {str(pr_cand):>8s} {str(pr_delta):>8s}")

    # Latency
    lat_base = matched_avg_latency(baseline_results, matched_ids)
    lat_cand = matched_avg_latency(candidate_results, matched_ids)
    lat_delta = f"{lat_cand - lat_base:+.1f}s" if lat_base is not None and lat_cand is not None else "N/A"
    print(f"  {'avg_latency_s':<25s} {str(lat_base):>8s} {str(lat_cand):>8s} {lat_delta:>8s}")

    # Category-level comparison
    cat_ids = defaultdict(list)
    for qid in matched_ids:
        cat = baseline_results[qid].get("normalized_category", "unknown")
        cat_ids[cat].append(qid)

    print(f"\n  Category-level CR deltas (matched subset):")
    print(f"  {'Category':<25s} {'n':>4s} {'Base':>7s} {'Cand':>7s} {'Delta':>8s}")
    print(f"  {'-'*52}")

    for cat in sorted(cat_ids):
        ids = cat_ids[cat]
        c_base = matched_avg(baseline_results, ids, "context_relevancy")
        c_cand = matched_avg(candidate_results, ids, "context_relevancy")
        if c_base is not None and c_cand is not None:
            d = round(c_cand - c_base, 4)
            print(f"  {cat:<25s} {len(ids):>4d} {c_base:>7.3f} {c_cand:>7.3f} {d:>+8.4f}")
        else:
            print(f"  {cat:<25s} {len(ids):>4d} {'N/A':>7s} {'N/A':>7s} {'N/A':>8s}")

    # Planner telemetry (from candidate if planner, else baseline)
    candidate_is_planner = candidate_report.get("planner_enabled", False)
    baseline_is_planner = baseline_report.get("planner_enabled", False)
    planner_report = candidate_report if candidate_is_planner else (baseline_report if baseline_is_planner else None)
    if planner_report:
        ps = planner_report.get("summary", {}).get("planner")
        if ps:
            planner_side = "candidate" if candidate_is_planner else "baseline"
            print(f"\n  Planner telemetry ({planner_side}):")
            print(f"    Fallback rate:        {ps.get('fallback_rate')}")
            print(f"    Abstention rate:      {ps.get('abstention_rate')}")
            print(f"    Avg confidence:       {ps.get('avg_confidence')}")
            print(f"    Classifier accuracy:  {ps.get('classifier_accuracy')}")
            ood_acc = ps.get('ood_abstention_accuracy')
            if ood_acc is not None:
                print(f"    OOD abstention:       {ood_acc:.1%} ({ps.get('ood_abstained', 0)}/{ps.get('ood_total', 0)})")

    # Partition matched questions into in-domain and OOD
    ood_ids = [qid for qid in matched_ids
               if baseline_results[qid].get("normalized_category") == "out_of_domain"]
    in_domain_ids = [qid for qid in matched_ids
                     if baseline_results[qid].get("normalized_category") != "out_of_domain"]

    # Phase 5 Gate Check
    print(f"\n  Phase 5 Gate Check:")
    gate_pass = True

    # CR improvement (all matched)
    cr_base = matched_avg(baseline_results, matched_ids, "context_relevancy")
    cr_cand = matched_avg(candidate_results, matched_ids, "context_relevancy")
    if cr_base is not None and cr_cand is not None:
        cr_holds = cr_cand >= cr_base
        print(f"    CR holds/improves:    {'PASS' if cr_holds else 'FAIL'} ({cr_base} -> {cr_cand})")
        if not cr_holds:
            gate_pass = False
    else:
        print(f"    CR holds/improves:    SKIP (insufficient data)")

    # AR within 5% — IN-DOMAIN ONLY (excludes OOD)
    ar_base = matched_avg(baseline_results, in_domain_ids, "answer_relevancy")
    ar_cand = matched_avg(candidate_results, in_domain_ids, "answer_relevancy")
    if ar_base is not None and ar_cand is not None:
        ar_within = abs(ar_cand - ar_base) <= 0.05
        print(f"    AR within 5% (in-domain, n={len(in_domain_ids)}): {'PASS' if ar_within else 'FAIL'} ({ar_base} -> {ar_cand}, delta={ar_cand-ar_base:+.4f})")
        if not ar_within:
            gate_pass = False
    else:
        print(f"    AR within 5% (in-domain): SKIP (insufficient data)")

    # OOD abstention accuracy >= 80% (planner only)
    if candidate_is_planner and ood_ids:
        ood_abstained = sum(
            1 for qid in ood_ids
            if candidate_results[qid].get("plan_trace", {}).get("abstained")
        )
        ood_acc = ood_abstained / len(ood_ids)
        ood_pass = ood_acc >= 0.80
        print(f"    OOD abstention >= 80% (n={len(ood_ids)}): {'PASS' if ood_pass else 'FAIL'} ({ood_acc:.1%}, {ood_abstained}/{len(ood_ids)})")
        if not ood_pass:
            gate_pass = False
    elif not candidate_is_planner and not baseline_is_planner:
        print(f"    OOD abstention:       SKIP (no planner in comparison)")
    elif not ood_ids:
        print(f"    OOD abstention:       SKIP (no OOD questions in matched set)")

    # F within 5% (all matched)
    f_base = matched_avg(baseline_results, matched_ids, "faithfulness")
    f_cand = matched_avg(candidate_results, matched_ids, "faithfulness")
    if f_base is not None and f_cand is not None:
        f_within = abs(f_cand - f_base) <= 0.05
        print(f"    F within 5%:          {'PASS' if f_within else 'FAIL'} ({f_base} -> {f_cand}, delta={f_cand-f_base:+.4f})")
        if not f_within:
            gate_pass = False
    else:
        print(f"    F within 5%:          SKIP (insufficient data)")

    # Classifier accuracy >= 80%
    if planner_report:
        ps = planner_report.get("summary", {}).get("planner", {})
        ca = ps.get("classifier_accuracy")
        if ca is not None:
            ca_pass = ca >= 0.80
            print(f"    Classifier >= 80%:    {'PASS' if ca_pass else 'FAIL'} ({ca:.1%})")
            if not ca_pass:
                gate_pass = False
        else:
            print(f"    Classifier >= 80%:    SKIP (no data)")

    # Error count
    errors_base = sum(1 for r in baseline_results.values() if r.get("error"))
    errors_cand = sum(1 for r in candidate_results.values() if r.get("error"))
    no_errors = errors_base == 0 and errors_cand == 0
    print(f"    Zero runtime errors:  {'PASS' if no_errors else 'FAIL'} (base={errors_base}, cand={errors_cand})")
    if not no_errors:
        gate_pass = False

    # Final verdict
    if canonical_comparison:
        print(f"\n  GATE VERDICT: {'PASS' if gate_pass else 'FAIL'}")
    else:
        print(f"\n  GATE VERDICT (informational): {'PASS' if gate_pass else 'FAIL'}")
        print(f"  (Official gate requires both reports scored with {CANONICAL_EVAL_MODEL})")

    print("=" * 80)

    return {"gate_pass": gate_pass, "canonical": canonical_comparison}


# ---------------------------------------------------------------------------
# Rescore mode
# ---------------------------------------------------------------------------

def rescore_report(
    source_path: str,
    eval_model: str = CANONICAL_EVAL_MODEL,
    metric_names: list[str] | None = None,
    tag: str = "",
) -> dict:
    """Rescore a saved report with a different judge model.

    Requires retrieval_context in saved results. Skips all /chat calls.
    Always writes a new model-tagged report file — never overwrites the source.
    """
    source = Path(source_path)
    if not source.exists():
        print(f"ERROR: Source report not found: {source}")
        sys.exit(1)

    with open(source) as f:
        report = json.load(f)

    results = report.get("results", [])
    if not results:
        print("ERROR: Source report has no results")
        sys.exit(1)

    # Verify retrieval_context exists in at least one result
    has_context = any(r.get("retrieval_context") for r in results if not r.get("error"))
    if not has_context:
        print("ERROR: Source report has no retrieval_context fields. "
              "Only reports generated with B9b.1+ (retrieval_context persisted) can be rescored.")
        sys.exit(1)

    valid_metrics = {"faithfulness", "answer_relevancy", "context_relevancy"}
    active_metrics = metric_names or list(valid_metrics)
    bad = set(active_metrics) - valid_metrics
    if bad:
        print(f"ERROR: Unknown metrics: {bad}. Valid: {valid_metrics}")
        sys.exit(1)

    # Initialize metrics with the new eval model
    metrics_map = {}
    if "faithfulness" in active_metrics:
        metrics_map["faithfulness"] = FaithfulnessMetric(threshold=THRESHOLDS["faithfulness"], model=eval_model)
    if "answer_relevancy" in active_metrics:
        metrics_map["answer_relevancy"] = AnswerRelevancyMetric(threshold=THRESHOLDS["answer_relevancy"], model=eval_model)
    if "context_relevancy" in active_metrics:
        metrics_map["context_relevancy"] = ContextualRelevancyMetric(threshold=THRESHOLDS["context_relevancy"], model=eval_model)

    print(f"Rescoring {len(results)} results from {source.name}")
    print(f"  Source eval_model: {report.get('eval_model', '(not recorded)')}")
    print(f"  Rescore eval_model: {eval_model}")
    print(f"  Metrics: {list(metrics_map.keys())}")

    # Load golden QA for expected answers
    with open(GOLDEN_QA_PATH) as f:
        golden_qa = json.load(f)
    expected_map = {qa["id"]: qa["expected_answer"] for qa in golden_qa}

    rescored_results = []
    for i, r in enumerate(results):
        qid = r["id"]
        question = r.get("question", "")

        if r.get("error"):
            rescored_results.append(r)
            print(f"[{i+1}/{len(results)}] {qid}: SKIPPED (error in source)")
            continue

        retrieval_context = r.get("retrieval_context", [])
        if not retrieval_context:
            rescored_results.append(r)
            print(f"[{i+1}/{len(results)}] {qid}: SKIPPED (no retrieval_context)")
            continue

        answer = r.get("chat_response", {}).get("answer", "") or r.get("answer", "")
        expected = expected_map.get(qid, "")

        print(f"[{i+1}/{len(results)}] {qid}: ", end="", flush=True)

        test_case = LLMTestCase(
            input=question,
            actual_output=answer,
            expected_output=expected,
            retrieval_context=retrieval_context if retrieval_context else ["(no context retrieved)"],
        )

        scores = {}
        for name, metric in metrics_map.items():
            scores[name] = score_metric_with_retry(metric, test_case, name)

        # Only check thresholds for scored metrics
        passed = all(
            scores.get(k) is not None and scores[k] >= v
            for k, v in THRESHOLDS.items()
            if k in active_metrics
        )

        # Build rescored result — preserve all original fields, update scores
        rescored = dict(r)
        rescored["scores"] = scores
        rescored["passed"] = passed
        rescored_results.append(rescored)

        score_parts = [f"{k}={v}" for k, v in scores.items()]
        status = "PASS" if passed else "FAIL"
        print(f"{status} ({', '.join(score_parts)})")

    # Generate output filename with model tag
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    model_slug = eval_model.replace(".", "").replace("-", "")
    tag_part = f"-{tag}" if tag else ""
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    out_filename = f"-rescore-{model_slug}{tag_part}-{timestamp}.json"
    out_path = RESULTS_DIR / out_filename

    summary, scored_ids = compute_aggregates(rescored_results)

    out_report = {
        "timestamp": datetime.now().isoformat(),
        "source_report": str(source),
        "rescore": True,
        "eval_model": eval_model,
        "tag": tag or f"rescore-{eval_model}",
        "planner_enabled": report.get("planner_enabled", False),
        "multi_query_enabled": report.get("multi_query_enabled", False),
        "scored_ids": scored_ids,
        "summary": summary,
        "results": rescored_results,
    }

    with open(out_path, "w") as f:
        json.dump(out_report, f, indent=2)

    print_summary(summary, tag or f"rescore-{eval_model}", out_path)
    return out_report


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

def run_preflight(base_url: str) -> bool:
    """Run embedding preflight checks before a full eval.

    Calls GET /diagnostics/embedding-preflight and validates:
      1. Service health (provider configured)
      2. Runtime column dimensions match provider
      3. Gold entity canaries (ranking sanity)

    Returns True if all checks pass.
    """
    print("\n" + "=" * 60)
    print("EVAL PREFLIGHT CHECK")
    print("=" * 60)

    # Step 1: Service health
    print("\n  [1/3] Service health...", end=" ", flush=True)
    try:
        resp = requests.get(f"{base_url}/health", timeout=10)
        resp.raise_for_status()
        health = resp.json()
        if health.get("status") != "healthy":
            print(f"FAIL — status: {health.get('status')}")
            return False
        if not health.get("embedding_available"):
            print("FAIL — no embedding provider configured")
            return False
        print(f"OK ({health.get('embedding_model')}, dim={health.get('embedding_dimension')})")
    except Exception as e:
        print(f"FAIL — {e}")
        return False

    # Step 2+3: Diagnostic endpoint (dimensions + canaries)
    print("  [2/3] Dimension check + [3/3] Canary check...", end=" ", flush=True)
    try:
        resp = requests.get(f"{base_url}/diagnostics/embedding-preflight", timeout=30)
        resp.raise_for_status()
        diag = resp.json()
    except Exception as e:
        print(f"FAIL — {e}")
        return False

    all_pass = True

    # Dimensions
    dim_check = diag.get("dimension_check", {})
    if not dim_check.get("pass"):
        all_pass = False

    dim_details = dim_check.get("details", [])
    dim_ok = sum(1 for d in dim_details if isinstance(d, dict) and d.get("match"))
    dim_total = len([d for d in dim_details if isinstance(d, dict)])
    print(f"\n    Dimensions: {dim_ok}/{dim_total} match", end="")
    for d in dim_details:
        if isinstance(d, dict) and not d.get("match") and d.get("db_dimension"):
            print(f"\n      MISMATCH: {d['table']}.{d['column']} = {d['db_dimension']}, provider = {d['provider_dimension']}", end="")
    print()

    # Canaries
    canary_check = diag.get("canary_check", {})
    if not canary_check.get("pass"):
        all_pass = False

    canary_details = canary_check.get("details", [])
    for c in canary_details:
        if "error" in c:
            print(f"    Canary ERROR: {c['error']}")
        else:
            status = "OK" if c.get("expected_found") else "MISS"
            print(f"    Canary [{status}]: \"{c['query']}\" -> top5={c.get('top5', [])[:3]} (sim={c.get('top_similarity', 0):.3f})")

    # Verdict
    overall = diag.get("overall_pass", False)
    print(f"\n  PREFLIGHT: {'PASS' if overall else 'FAIL'}")
    print("=" * 60)

    return overall


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run RAG evaluation")
    parser.add_argument("--base-url", default="http://localhost:8351", help="KOI API base URL")
    parser.add_argument("--tag", default="", help="Tag for the report filename")
    parser.add_argument("--multi-query", action="store_true", help="Enable B8b multi-query expansion")
    parser.add_argument("--planner", action="store_true", help="Send planner=true to /chat (B9a)")
    parser.add_argument("--resume", metavar="REPORT_PATH", help="Resume from checkpoint report file")
    parser.add_argument("--eval-model", default=CANONICAL_EVAL_MODEL,
                        help=f"LLM model for DeepEval scoring (default: {CANONICAL_EVAL_MODEL})")
    parser.add_argument("--compare", nargs=2, metavar=("REPORT_A", "REPORT_B"),
                        help="Compare two reports (matched-subset A/B)")
    parser.add_argument("--ids", type=str, default=None,
                        help="Comma-separated question IDs for subset runs (e.g. --ids commitment_claim_1,commitment_claim_2)")
    parser.add_argument("--metrics", type=str, default=None,
                        help="Comma-separated metric names (e.g. --metrics context_relevancy). Default: all three")
    parser.add_argument("--rescore", metavar="REPORT_PATH", default=None,
                        help="Rescore a saved report with a different --eval-model (requires retrieval_context in source)")
    parser.add_argument("--preflight", action="store_true",
                        help="Run embedding preflight checks (dimension, canary) and exit. No eval scoring.")
    parser.add_argument("--answer-mode", default="default", choices=["default", "explainer"],
                        help="Answer mode to send to /chat (default: default; use 'explainer' for structured brief)")
    args = parser.parse_args()

    # Parse comma-separated lists
    question_ids = [x.strip() for x in args.ids.split(",")] if args.ids else None
    metric_names = [x.strip() for x in args.metrics.split(",")] if args.metrics else None

    if args.preflight:
        ok = run_preflight(args.base_url)
        sys.exit(0 if ok else 1)
    elif args.rescore:
        rescore_report(args.rescore, eval_model=args.eval_model,
                       metric_names=metric_names, tag=args.tag)
    elif args.compare:
        compare_reports(args.compare[0], args.compare[1])
    else:
        run_eval(args.base_url, args.tag, multi_query=args.multi_query,
                 planner=args.planner, resume_path=args.resume,
                 eval_model=args.eval_model, question_ids=question_ids,
                 metric_names=metric_names, answer_mode=args.answer_mode)
