#!/usr/bin/env python3
"""
Polysemy Resolver Evaluation Script

Evaluates the polysemy resolver against a labeled dataset to measure:
- Top-1 type accuracy (does the winner match expected_type?)
- Top-3 coverage (is expected_type in top 3 results?)
- Resolution method distribution
- Failure analysis

Usage:
    cd /opt/projects/koi-processor
    set -a; source .env; set +a
    PYTHONPATH=src python scripts/eval_polysemy_resolver.py

    # Use custom dataset
    PYTHONPATH=src python scripts/eval_polysemy_resolver.py --dataset path/to/eval.jsonl

Author: Claude Code
Date: 2025-12-24
Version: 1.0.0
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Set
from dataclasses import dataclass, field

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("Error: psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)

from knowledge_graph.polysemy_resolver import (
    resolve_entity,
    get_default_db_config,
)


def infer_type_hint(context: str) -> Optional[str]:
    """
    Infer type hint from context text using keyword heuristics.

    Args:
        context: The context string describing entity usage

    Returns:
        Inferred type hint or None if no clear signal
    """
    context_lower = context.lower()

    # TECHNOLOGY signals
    if any(kw in context_lower for kw in [
        'platform', 'software', 'tool', 'api', 'protocol', 'deployed',
        'layer 2', 'l2', 'blockchain', 'chain', 'mainnet', 'network',
        'sdk', 'library', 'framework', 'format', 'stored on', 'query'
    ]):
        return 'TECHNOLOGY'

    # ORGANIZATION signals
    if any(kw in context_lower for kw in [
        'company', 'founded', 'organization', 'team', 'community',
        'join our', 'group'
    ]):
        return 'ORGANIZATION'

    # PROJECT signals
    # Note: 'ecosystem' removed - it's more commonly part of concept phrases
    # like "ecosystem services" rather than indicating a PROJECT
    if any(kw in context_lower for kw in [
        'project', 'initiative', 'repository', 'integration',
        'provides'
    ]):
        return 'PROJECT'

    # CONCEPT signals
    if any(kw in context_lower for kw in [
        'concept', 'definition', 'measuring', 'valuing', 'practices',
        'methodology', 'loss', 'services'
    ]):
        return 'CONCEPT'

    # PROCESS signals
    if any(kw in context_lower for kw in [
        'process', 'required', 'verification', 'validation'
    ]):
        return 'PROCESS'

    # PERSON signals
    if any(kw in context_lower for kw in [
        'founded by', 'leads', 'person', 'author'
    ]):
        return 'PERSON'

    # STANDARD signals
    if any(kw in context_lower for kw in [
        'standard', 'specification', 'modeled in'
    ]):
        return 'STANDARD'

    return None


@dataclass
class EvalCase:
    """Single evaluation case."""
    label: str
    context: str
    expected_types: Set[str]
    notes: str

    # Results
    found: bool = False
    winner_type: Optional[str] = None
    top3_types: List[str] = field(default_factory=list)
    resolution_method: Optional[str] = None
    is_polysemy: bool = False

    # Type hint info
    type_hint_used: Optional[str] = None
    type_hint_inferred: Optional[str] = None

    # Metrics
    top1_correct: bool = False
    top3_correct: bool = False


@dataclass
class EvalResult:
    """Aggregated evaluation results."""
    total_cases: int
    found_cases: int
    not_found_cases: int

    # Accuracy metrics
    top1_correct: int
    top1_accuracy: float
    top3_correct: int
    top3_accuracy: float

    # Failure analysis
    failures: List[EvalCase]
    not_found: List[EvalCase]

    # Resolution method distribution
    resolution_methods: Dict[str, int]

    # Polysemy stats
    polysemy_cases: int

    # Type hint stats
    use_context_hints: bool = False
    hints_inferred: int = 0
    hints_matched: int = 0  # Cases where hint matched winner type


def load_eval_dataset(path: str) -> List[EvalCase]:
    """Load evaluation dataset from JSONL file."""
    cases = []

    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            data = json.loads(line)

            # Handle expected_type as list or single value
            expected = data.get('expected_type', [])
            if isinstance(expected, str):
                expected = [expected]

            cases.append(EvalCase(
                label=data['label'],
                context=data.get('context', ''),
                expected_types=set(t.upper() for t in expected),
                notes=data.get('notes', ''),
            ))

    return cases


def run_evaluation(cases: List[EvalCase], conn, use_context_hints: bool = False) -> EvalResult:
    """Run evaluation on all cases.

    Args:
        cases: List of evaluation cases
        conn: Database connection
        use_context_hints: If True, infer type hints from context and use them

    Returns:
        EvalResult with aggregated metrics
    """
    found_cases = 0
    not_found_cases = 0
    top1_correct = 0
    top3_correct = 0
    failures = []
    not_found = []
    resolution_methods = {}
    polysemy_cases = 0
    hints_inferred = 0
    hints_matched = 0

    for case in cases:
        # Optionally infer type hint from context
        type_hint = None
        if use_context_hints:
            type_hint = infer_type_hint(case.context)
            case.type_hint_inferred = type_hint
            if type_hint:
                hints_inferred += 1
                case.type_hint_used = type_hint

        result = resolve_entity(case.label, type_hint=type_hint, conn=conn)

        if result.winner is None:
            not_found_cases += 1
            case.found = False
            not_found.append(case)
            continue

        found_cases += 1
        case.found = True
        case.winner_type = result.winner.entity_type
        case.resolution_method = result.resolution_method
        case.is_polysemy = result.is_polysemy

        if result.is_polysemy:
            polysemy_cases += 1

        # Track resolution methods
        rm = result.resolution_method
        resolution_methods[rm] = resolution_methods.get(rm, 0) + 1

        # Collect top-3 types
        top3 = [result.winner.entity_type]
        for alt in result.alternatives[:2]:
            top3.append(alt.entity_type)
        case.top3_types = top3

        # Check top-1 accuracy
        if result.winner.entity_type.upper() in case.expected_types:
            case.top1_correct = True
            top1_correct += 1
        else:
            failures.append(case)

        # Track if type hint matched winner
        if use_context_hints and case.type_hint_used:
            if case.type_hint_used.upper() == result.winner.entity_type.upper():
                hints_matched += 1

        # Check top-3 accuracy
        top3_upper = set(t.upper() for t in top3)
        if case.expected_types & top3_upper:
            case.top3_correct = True
            top3_correct += 1

    return EvalResult(
        total_cases=len(cases),
        found_cases=found_cases,
        not_found_cases=not_found_cases,
        top1_correct=top1_correct,
        top1_accuracy=top1_correct / found_cases if found_cases > 0 else 0,
        top3_correct=top3_correct,
        top3_accuracy=top3_correct / found_cases if found_cases > 0 else 0,
        failures=failures,
        not_found=not_found,
        resolution_methods=resolution_methods,
        polysemy_cases=polysemy_cases,
        use_context_hints=use_context_hints,
        hints_inferred=hints_inferred,
        hints_matched=hints_matched,
    )


def generate_markdown_report(result: EvalResult, cases: List[EvalCase], output_path: str):
    """Generate markdown report of evaluation results."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Title depends on whether hints were used
    if result.use_context_hints:
        title = "# Polysemy Resolver Evaluation Report - Week 9 (With Context Hints)"
    else:
        title = "# Polysemy Resolver Evaluation Report - Week 9"

    lines = [
        title,
        "",
        f"**Generated:** {now}",
        f"**Dataset:** {len(cases)} cases",
        f"**Context Hints:** {'Enabled' if result.use_context_hints else 'Disabled'}",
        "",
        "---",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total Cases | {result.total_cases} |",
        f"| Found in DB | {result.found_cases} ({100*result.found_cases/result.total_cases:.1f}%) |",
        f"| Not Found | {result.not_found_cases} |",
        f"| Polysemy Cases | {result.polysemy_cases} |",
    ]

    # Add hint stats if enabled
    if result.use_context_hints:
        lines.extend([
            f"| Hints Inferred | {result.hints_inferred} |",
            f"| Hints Matched Winner | {result.hints_matched} |",
        ])

    lines.extend([
        "",
        "---",
        "",
        "## Accuracy Metrics",
        "",
        "| Metric | Correct | Total | Accuracy |",
        "|--------|---------|-------|----------|",
        f"| Top-1 Type Accuracy | {result.top1_correct} | {result.found_cases} | **{100*result.top1_accuracy:.1f}%** |",
        f"| Top-3 Coverage | {result.top3_correct} | {result.found_cases} | **{100*result.top3_accuracy:.1f}%** |",
        "",
    ])

    # Pass/Fail determination
    if result.top1_accuracy >= 0.8:
        lines.append("**Status:** PASS (Top-1 accuracy >= 80%)")
    else:
        lines.append("**Status:** NEEDS IMPROVEMENT (Top-1 accuracy < 80%)")

    lines.extend([
        "",
        "---",
        "",
        "## Resolution Method Distribution",
        "",
        "| Method | Count |",
        "|--------|-------|",
    ])

    for method, count in sorted(result.resolution_methods.items(), key=lambda x: -x[1]):
        lines.append(f"| {method} | {count} |")

    lines.extend([
        "",
        "---",
        "",
        "## Failures (Wrong Top-1 Type)",
        "",
    ])

    if result.failures:
        lines.append("| Label | Expected | Got | Method | Notes |")
        lines.append("|-------|----------|-----|--------|-------|")

        for case in result.failures:
            expected = ", ".join(sorted(case.expected_types))
            lines.append(f"| {case.label} | {expected} | {case.winner_type} | {case.resolution_method} | {case.notes} |")
    else:
        lines.append("No failures - all found entities matched expected types.")

    lines.extend([
        "",
        "---",
        "",
        "## Not Found in Database",
        "",
    ])

    if result.not_found:
        lines.append("| Label | Expected | Notes |")
        lines.append("|-------|----------|-------|")

        for case in result.not_found:
            expected = ", ".join(sorted(case.expected_types))
            lines.append(f"| {case.label} | {expected} | {case.notes} |")
    else:
        lines.append("All labels found in database.")

    lines.extend([
        "",
        "---",
        "",
        "## All Results",
        "",
    ])

    # Include Hint column if hints were used
    if result.use_context_hints:
        lines.append("| Label | Found | Hint | Winner | Top-1 | Top-3 | Polysemy | Notes |")
        lines.append("|-------|-------|------|--------|-------|-------|----------|-------|")
    else:
        lines.append("| Label | Found | Winner | Top-1 | Top-3 | Polysemy | Notes |")
        lines.append("|-------|-------|--------|-------|-------|----------|-------|")

    for case in cases:
        found = "Yes" if case.found else "No"
        winner = case.winner_type or "N/A"
        top1 = "✓" if case.top1_correct else ("✗" if case.found else "-")
        top3 = "✓" if case.top3_correct else ("✗" if case.found else "-")
        polysemy = "Yes" if case.is_polysemy else "No"
        notes = case.notes[:40] + "..." if len(case.notes) > 40 else case.notes
        hint = case.type_hint_used or "-"

        if result.use_context_hints:
            lines.append(f"| {case.label} | {found} | {hint} | {winner} | {top1} | {top3} | {polysemy} | {notes} |")
        else:
            lines.append(f"| {case.label} | {found} | {winner} | {top1} | {top3} | {polysemy} | {notes} |")

    lines.extend([
        "",
        "---",
        "",
        "## Interpretation",
        "",
        "- **Top-1 Accuracy**: How often the resolver returns the expected type as the winner",
        "- **Top-3 Coverage**: How often the expected type appears in the top 3 results",
        "- **Polysemy Cases**: Labels that have multiple valid types in the database",
        "",
        "A low Top-1 but high Top-3 suggests the resolver is finding valid alternatives",
        "but not ranking them correctly for the intended use case.",
        "",
        "---",
        "",
        "*Report generated by `scripts/eval_polysemy_resolver.py`*",
    ])

    report = "\n".join(lines)

    with open(output_path, 'w') as f:
        f.write(report)

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate polysemy resolver against labeled dataset"
    )
    parser.add_argument(
        "--dataset",
        default="docs/archive/reports/polysemy_eval_set_week7.jsonl",
        help="Path to evaluation dataset (JSONL)"
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output path for evaluation report (auto-generated if not specified)"
    )
    parser.add_argument(
        "--use-context-hint",
        action="store_true",
        help="Infer type hints from context and use them for resolution"
    )
    args = parser.parse_args()

    # Determine output path based on whether hints are used
    if args.output:
        output_file = args.output
    elif args.use_context_hint:
        output_file = "docs/archive/reports/polysemy_resolver_eval_week9_with_hints.md"
    else:
        output_file = "docs/archive/reports/polysemy_resolver_eval_week9.md"

    # Load dataset
    dataset_path = Path(__file__).parent.parent / args.dataset
    if not dataset_path.exists():
        # Try as absolute path
        dataset_path = Path(args.dataset)

    if not dataset_path.exists():
        print(f"Error: Dataset not found: {args.dataset}")
        sys.exit(1)

    print(f"Loading evaluation dataset from: {dataset_path}")
    cases = load_eval_dataset(str(dataset_path))
    print(f"Loaded {len(cases)} evaluation cases")

    if args.use_context_hint:
        print("Context hints: ENABLED")
    else:
        print("Context hints: DISABLED")

    # Connect to database
    db_config = get_default_db_config()
    print(f"Connecting to database: {db_config['host']}:{db_config['port']}/{db_config['database']}")

    try:
        conn = psycopg2.connect(**db_config)
    except Exception as e:
        print(f"Error: Failed to connect to database: {e}")
        sys.exit(1)

    # Run evaluation
    print("Running evaluation...")
    result = run_evaluation(cases, conn, use_context_hints=args.use_context_hint)

    conn.close()

    # Print summary
    print()
    print("=" * 60)
    print("EVALUATION RESULTS")
    if args.use_context_hint:
        print("(With Context Hints)")
    print("=" * 60)
    print(f"Total Cases: {result.total_cases}")
    print(f"Found in DB: {result.found_cases}")
    print(f"Not Found: {result.not_found_cases}")
    print()
    print(f"Top-1 Accuracy: {result.top1_correct}/{result.found_cases} ({100*result.top1_accuracy:.1f}%)")
    print(f"Top-3 Coverage: {result.top3_correct}/{result.found_cases} ({100*result.top3_accuracy:.1f}%)")
    print()

    if args.use_context_hint:
        print(f"Hints Inferred: {result.hints_inferred}")
        print(f"Hints Matched Winner: {result.hints_matched}")
        print()

    if result.failures:
        print("Failures:")
        for case in result.failures[:5]:
            expected = ", ".join(sorted(case.expected_types))
            hint_info = f" (hint: {case.type_hint_used})" if case.type_hint_used else ""
            print(f"  - {case.label}: expected {expected}, got {case.winner_type}{hint_info}")
        if len(result.failures) > 5:
            print(f"  ... and {len(result.failures) - 5} more")

    print()
    print("Resolution Methods:")
    for method, count in sorted(result.resolution_methods.items(), key=lambda x: -x[1]):
        print(f"  - {method}: {count}")

    # Generate report
    output_path = Path(__file__).parent.parent / output_file
    output_path.parent.mkdir(parents=True, exist_ok=True)

    generate_markdown_report(result, cases, str(output_path))
    print()
    print(f"Report written to: {output_path}")


if __name__ == "__main__":
    main()
