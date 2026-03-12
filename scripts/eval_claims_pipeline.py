#!/usr/bin/env python3
"""Claims Engine — Pipeline Evaluation Harness.

Runs the full claims lifecycle and produces structured metrics JSON
for testnet dogfooding and regression tracking.

Pipeline steps measured:
  1. POST /claims/           — create claim
  2. POST /claims/{rid}/attestations (reviewer 1)
  3. PATCH /claims/{rid}/verify → peer_reviewed
  4. POST /claims/{rid}/attestations (reviewer 2)
  5. PATCH /claims/{rid}/verify → verified
  6. POST /claims/{rid}/prepare-anchor
  7. POST /claims/{rid}/anchor
  8. GET  /claims/{rid}/proof-pack

Usage:
    python -m scripts.eval_claims_pipeline
    python -m scripts.eval_claims_pipeline --skip-anchor --runs 3
    python -m scripts.eval_claims_pipeline --compare docs/eval/claims-baseline.json
"""

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_URL = "http://localhost:8351"


# ── HTTP helper (copied from test_claims_api.py — stdlib only) ────────

def _req(method: str, path: str, body=None):
    """Make HTTP request and return (status, data)."""
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            resp_data = json.loads(e.read().decode())
        except Exception:
            resp_data = {"detail": str(e)}
        return e.code, resp_data


def _resolve_or_create_entity(name: str, entity_type: str):
    """Resolve an existing entity or create via /ingest. Returns URI or None."""
    status, data = _req("POST", "/entity/resolve", {
        "label": name, "type_hint": entity_type,
    })
    if status == 200 and data.get("candidates") and not data.get("is_new"):
        return data["candidates"][0]["uri"]

    ts = int(time.time())
    status, data = _req("POST", "/ingest", {
        "document_rid": f"eval:claims-pipeline-{entity_type.lower()}-{ts}",
        "source": "eval_claims_pipeline",
        "entities": [{"name": name, "type": entity_type}],
    })
    if status == 200 and data.get("canonical_entities"):
        uri = data["canonical_entities"][0].get("uri")
        if uri:
            return uri
    return None


# ── Pipeline step runner ──────────────────────────────────────────────

def run_step(name: str, method: str, path: str, body=None, expected_status=None):
    """Run a single pipeline step, returning a step result dict."""
    if expected_status is None:
        expected_status = {200, 201, 202}

    start = time.monotonic()
    try:
        status, data = _req(method, path, body)
        elapsed = time.monotonic() - start
        success = status in expected_status
        return {
            "step": name,
            "success": success,
            "status": status,
            "latency_s": round(elapsed, 4),
            "data": data,
            "error": None if success else f"HTTP {status}: {data.get('detail', '')}",
        }
    except Exception as e:
        elapsed = time.monotonic() - start
        return {
            "step": name,
            "success": False,
            "status": 0,
            "latency_s": round(elapsed, 4),
            "data": {},
            "error": str(e),
        }


def run_pipeline(run_id: int, skip_anchor: bool, claimant_uri: str, reviewer_uris: list):
    """Execute the full claims pipeline once. Returns list of step results."""
    steps = []
    ts = int(time.time())

    # Step 1: Create claim
    step = run_step(
        "create_claim", "POST", "/claims/",
        body={
            "claimant_uri": claimant_uri,
            "statement": f"Eval harness: restored 25 hectares of degraded wetland in test bioregion (run {run_id} t={ts})",
            "claim_type": "ecological",
            "metadata": {"quantity": 25, "unit": "hectares", "eval_run": run_id},
        },
        expected_status={201},
    )
    steps.append(step)
    if not step["success"]:
        return steps
    rid = step["data"].get("claim_rid")
    if not rid:
        step["error"] = "No claim_rid in response"
        step["success"] = False
        return steps

    # Step 2: Attestation (reviewer 1)
    step = run_step(
        "attestation_1", "POST", f"/claims/{rid}/attestations",
        body={
            "reviewer_uri": reviewer_uris[0],
            "verdict": "approved",
            "rationale": f"Eval run {run_id}: first reviewer attestation",
        },
        expected_status={200, 201},
    )
    steps.append(step)
    if not step["success"]:
        return steps

    # Step 3: Verify → peer_reviewed
    step = run_step(
        "verify_peer_reviewed", "PATCH", f"/claims/{rid}/verify",
        body={
            "new_level": "peer_reviewed",
            "actor": "eval_harness",
            "reason": f"Eval run {run_id}",
        },
    )
    steps.append(step)
    if not step["success"]:
        return steps

    # Step 4: Attestation (reviewer 2)
    step = run_step(
        "attestation_2", "POST", f"/claims/{rid}/attestations",
        body={
            "reviewer_uri": reviewer_uris[1],
            "verdict": "approved",
            "rationale": f"Eval run {run_id}: second reviewer attestation",
        },
        expected_status={200, 201},
    )
    steps.append(step)
    if not step["success"]:
        return steps

    # Step 5: Verify → verified
    step = run_step(
        "verify_verified", "PATCH", f"/claims/{rid}/verify",
        body={
            "new_level": "verified",
            "actor": "eval_harness",
            "reason": f"Eval run {run_id}",
        },
    )
    steps.append(step)
    if not step["success"]:
        return steps

    # Step 6: Prepare anchor
    step = run_step("prepare_anchor", "POST", f"/claims/{rid}/prepare-anchor")
    steps.append(step)
    content_hash = step["data"].get("content_hash") if step["success"] else None

    # Step 7: Anchor (optional)
    if skip_anchor:
        steps.append({
            "step": "anchor",
            "success": True,
            "status": 0,
            "latency_s": 0,
            "data": {"skipped": True},
            "error": None,
        })
    else:
        step = run_step(
            "anchor", "POST", f"/claims/{rid}/anchor",
            expected_status={200, 202},
        )
        steps.append(step)
        if not step["success"]:
            return steps

    # Step 8: Proof-pack (only if anchored)
    if skip_anchor:
        steps.append({
            "step": "proof_pack",
            "success": True,
            "status": 0,
            "latency_s": 0,
            "data": {"skipped": True, "reason": "anchor skipped"},
            "error": None,
        })
    else:
        step = run_step("proof_pack", "GET", f"/claims/{rid}/proof-pack")
        steps.append(step)
        if step["success"] and content_hash:
            step["data"]["hash_verified"] = (
                step["data"].get("claim_content_hash_verified", False)
            )

    return steps


# ── Aggregation ───────────────────────────────────────────────────────

def compute_aggregates(all_runs):
    """Compute aggregate metrics across all runs."""
    step_names = [
        "create_claim", "attestation_1", "verify_peer_reviewed",
        "attestation_2", "verify_verified", "prepare_anchor",
        "anchor", "proof_pack",
    ]

    by_step = {}
    total_success = 0
    total_steps = 0
    all_latencies = []
    anchor_latencies = []

    for run in all_runs:
        for step in run:
            name = step["step"]
            if name not in by_step:
                by_step[name] = {"successes": 0, "total": 0, "latencies": []}
            by_step[name]["total"] += 1
            total_steps += 1
            if step["success"]:
                by_step[name]["successes"] += 1
                total_success += 1
            if step["latency_s"] > 0:
                by_step[name]["latencies"].append(step["latency_s"])
                all_latencies.append(step["latency_s"])
                if name == "anchor":
                    anchor_latencies.append(step["latency_s"])

    step_agg = {}
    for name in step_names:
        if name in by_step:
            s = by_step[name]
            lats = s["latencies"]
            step_agg[name] = {
                "avg_latency_s": round(statistics.mean(lats), 4) if lats else 0,
                "success_rate": round(s["successes"] / s["total"] * 100, 1) if s["total"] else 0,
                "count": s["total"],
            }

    return {
        "total_steps": len(step_names),
        "success_rate": round(total_success / total_steps * 100, 1) if total_steps else 0,
        "total_latency_s": round(sum(all_latencies), 3),
        "anchor_latency_s": round(sum(anchor_latencies), 3) if anchor_latencies else 0,
        "by_step": step_agg,
    }


# ── Comparison ────────────────────────────────────────────────────────

def compare_baselines(current, baseline_path):
    """Compare current run against a saved baseline."""
    with open(baseline_path) as f:
        baseline = json.load(f)

    b_agg = baseline["aggregates"]
    c_agg = current["aggregates"]

    print("\n" + "=" * 60)
    print("  BASELINE COMPARISON")
    print("=" * 60)

    metrics = [
        ("success_rate", "%", True),
        ("total_latency_s", "s", False),
        ("anchor_latency_s", "s", False),
    ]

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
        print(f"  {metric:25s} | baseline={b_val:8.3f}{unit} | current={c_val:8.3f}{unit} | {delta}")

    # Per-step comparison
    print("\n  BY STEP:")
    for step_name, c_data in c_agg.get("by_step", {}).items():
        b_data = b_agg.get("by_step", {}).get(step_name, {})
        b_lat = b_data.get("avg_latency_s", 0)
        c_lat = c_data.get("avg_latency_s", 0)
        if b_lat > 0:
            pct = (c_lat - b_lat) / b_lat * 100
            arrow = "+" if pct > 0 else ""
            indicator = "SLOWER" if pct > 0 else "faster"
            delta = f"{arrow}{pct:.1f}% ({indicator})"
        else:
            delta = "N/A"
        print(f"    {step_name:25s} | {b_lat:6.4f}s → {c_lat:6.4f}s | {delta}")

    chain_match = baseline.get("chain_id") == current.get("chain_id")
    print(f"\n  Chain ID match: {'YES' if chain_match else 'NO (different environments)'}")
    print("=" * 60)


# ── Main ──────────────────────────────────────────────────────────────

def main():
    global BASE_URL
    parser = argparse.ArgumentParser(description="Claims Engine Pipeline Eval Harness")
    parser.add_argument("--base-url", default="http://localhost:8351", help="API base URL")
    parser.add_argument("--runs", type=int, default=1, help="Number of pipeline runs")
    parser.add_argument("--skip-anchor", action="store_true", help="Skip on-chain anchor step")
    parser.add_argument("--save", default="docs/eval/claims-baseline.json", help="Save results path")
    parser.add_argument("--compare", help="Compare against saved baseline")
    args = parser.parse_args()
    BASE_URL = args.base_url

    # Health check
    print(f"Claims Engine — Pipeline Eval Harness")
    print(f"Base URL: {BASE_URL}")
    print(f"Runs: {args.runs}, Skip anchor: {args.skip_anchor}")
    print("=" * 60)

    try:
        status, data = _req("GET", "/health")
        if status != 200:
            raise Exception(f"status={status}")
    except Exception as e:
        print(f"ERROR: Server not reachable — {e}")
        print("Start with: ~/.config/personal-koi/start.sh")
        sys.exit(1)

    # Fetch chain info
    chain_id = "unknown"
    is_testnet = False
    try:
        status, ci = _req("GET", "/claims/chain-info")
        if status == 200:
            chain_id = ci.get("chain_id", "unknown")
            is_testnet = ci.get("is_testnet", False)
            print(f"Chain: {chain_id} ({'testnet' if is_testnet else 'mainnet'})")
    except Exception:
        print("Warning: Could not fetch chain info")

    # Setup entities
    print("\nSetup: resolving test entities...")
    claimant_uri = _resolve_or_create_entity("Eval Harness Test Org", "Organization")
    if not claimant_uri:
        print("ERROR: Could not create test claimant")
        sys.exit(1)
    print(f"  Claimant: {claimant_uri}")

    reviewer_uris = []
    for name in ["Eval Reviewer Alpha", "Eval Reviewer Beta"]:
        uri = _resolve_or_create_entity(name, "Person")
        if uri and uri not in reviewer_uris:
            reviewer_uris.append(uri)
    if len(reviewer_uris) < 2:
        print(f"ERROR: Need 2 reviewers, got {len(reviewer_uris)}")
        sys.exit(1)
    print(f"  Reviewers: {reviewer_uris}")

    # Run pipeline
    all_runs = []
    for i in range(1, args.runs + 1):
        print(f"\n--- Run {i}/{args.runs} ---")
        steps = run_pipeline(i, args.skip_anchor, claimant_uri, reviewer_uris)
        all_runs.append(steps)

        for step in steps:
            status_str = "OK" if step["success"] else "FAIL"
            skip = " (skipped)" if step["data"].get("skipped") else ""
            print(f"  [{status_str}] {step['step']:25s} | {step['latency_s']:6.4f}s{skip}")
            if step["error"]:
                print(f"         {step['error'][:80]}")

    # Aggregate
    aggregates = compute_aggregates(all_runs)

    print(f"\n{'=' * 60}")
    print("  AGGREGATE METRICS")
    print(f"{'=' * 60}")
    print(f"  Success rate:     {aggregates['success_rate']:5.1f}%")
    print(f"  Total latency:    {aggregates['total_latency_s']:5.3f}s")
    print(f"  Anchor latency:   {aggregates['anchor_latency_s']:5.3f}s")
    print(f"\n  BY STEP:")
    for step_name, data in aggregates["by_step"].items():
        print(f"    {step_name:25s} | lat={data['avg_latency_s']:6.4f}s | success={data['success_rate']:5.1f}%")

    # Build result
    result = {
        "eval_version": "claims-v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "api_url": BASE_URL,
        "chain_id": chain_id,
        "is_testnet": is_testnet,
        "run_count": args.runs,
        "skip_anchor": args.skip_anchor,
        "aggregates": aggregates,
        "runs": [
            {
                "run_id": i + 1,
                "steps": [
                    {k: v for k, v in step.items() if k != "data"}
                    for step in run
                ],
            }
            for i, run in enumerate(all_runs)
        ],
    }

    # Save
    save_path = Path(args.save)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nResults saved to {save_path}")

    # Compare
    if args.compare:
        compare_baselines(result, args.compare)

    # Exit code
    if aggregates["success_rate"] < 100:
        print("\nWARNING: Not all steps passed")
        sys.exit(1)
    print("\nAll pipeline steps passed!")


if __name__ == "__main__":
    main()
