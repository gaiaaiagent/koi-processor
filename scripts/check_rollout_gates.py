#!/usr/bin/env python3
"""
B2 GraphRAG Rollout Gate Check

Compares B2 GraphRAG eval results against B1 baseline.
Exits non-zero if any gate fails.

Gates:
  0. graph_version consistency (b1 and b2 must match)
  1. p95 latency within 1.10x of B1
  2. Global relevance_avg >= B1
  3. Per-category resolution rate >= B1
"""

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Check B2 vs B1 rollout gates")
    parser.add_argument("--b1", default="docs/eval/b1-baseline.json", help="B1 baseline JSON")
    parser.add_argument("--b2", default="docs/eval/b2-graphrag.json", help="B2 GraphRAG JSON")
    args = parser.parse_args()

    for label, path in [("B1", args.b1), ("B2", args.b2)]:
        if not Path(path).exists():
            print(f"ERROR: {label} file not found: {path}")
            sys.exit(2)

    b1 = json.loads(Path(args.b1).read_text())
    b2 = json.loads(Path(args.b2).read_text())
    failures = []

    # Gate 0: graph_version consistency
    b1_gv = b1.get("graph_version")
    b2_gv = b2.get("graph_version")
    if not b1_gv or not b2_gv:
        failures.append(f"FAIL: graph_version missing (b1={b1_gv} b2={b2_gv}) — eval output incomplete")
    elif b1_gv != b2_gv:
        failures.append(f"FAIL: graph_version mismatch (b1={b1_gv} b2={b2_gv}) — rerun")
    else:
        print(f"PASS: graph_version consistent ({b1_gv})")

    # Gate 1: p95 latency within 1.10x of B1
    b1_p95 = b1["aggregates"]["latency_p95_s"]
    b2_p95 = b2["aggregates"]["latency_p95_s"]
    threshold = b1_p95 * 1.10
    if b2_p95 > threshold:
        failures.append(f"FAIL: p95 regression {b2_p95:.3f}s > {threshold:.3f}s (1.10x B1)")
    else:
        print(f"PASS: p95 {b2_p95:.3f}s <= {threshold:.3f}s")

    # Gate 2: global relevance no regression
    b1_rel = b1["aggregates"].get("relevance_avg")
    b2_rel = b2["aggregates"].get("relevance_avg")
    if b1_rel is None or b2_rel is None:
        failures.append("FAIL: relevance_avg missing")
    elif b2_rel < b1_rel:
        failures.append(f"FAIL: relevance regression {b2_rel:.2f} < {b1_rel:.2f}")
    else:
        print(f"PASS: relevance {b2_rel:.2f} >= {b1_rel:.2f}")

    # Gate 3: per-category resolution — no category regresses
    for cat, b1_cat in b1["aggregates"]["by_category"].items():
        b2_cat = b2["aggregates"]["by_category"].get(cat, {})
        b2_res = b2_cat.get("resolution_rate", 0)
        b1_res = b1_cat["resolution_rate"]
        if b2_res < b1_res:
            failures.append(f"FAIL: {cat} resolution {b2_res:.1f}% < {b1_res:.1f}%")
        else:
            print(f"PASS: {cat} resolution {b2_res:.1f}% >= {b1_res:.1f}%")

    print()
    if failures:
        for f in failures:
            print(f)
        print("\nROLLOUT GATES: FAILED")
        sys.exit(1)
    else:
        print("ALL ROLLOUT GATES PASSED")


if __name__ == "__main__":
    main()
