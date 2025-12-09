#!/usr/bin/env python3
"""
Compare old vs new extractions and generate detailed report.

Analyzes the differences between baseline and pipeline-processed entities:
- Entity count changes
- Block rates by module
- Type normalization statistics
- List splitting results
- Quality improvement metrics

Input: baseline_entities.json, pilot_results.json
Output: comparison_report.md

Usage:
    python scripts/reextraction/compare_extractions.py
    python scripts/reextraction/compare_extractions.py --output custom_report.md
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from collections import Counter

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))


def load_json(path: str) -> Dict:
    """Load JSON file."""
    with open(path, 'r') as f:
        return json.load(f)


def calculate_metrics(baseline: Dict, results: Dict) -> Dict[str, Any]:
    """
    Calculate comprehensive comparison metrics.

    Args:
        baseline: Baseline entities data
        results: Pipeline processing results

    Returns:
        Dictionary of calculated metrics
    """
    # Extract documents from both sources
    baseline_docs = baseline.get('documents', baseline)
    result_docs = results.get('results', results)

    # Basic counts
    doc_count = len(result_docs)
    total_baseline_entities = 0
    total_passed_entities = 0
    total_blocked_entities = 0

    # Block reason tracking
    block_reasons = Counter()
    blocked_by_module = Counter()

    # Transformation tracking
    modified_entities = []
    split_entities = []
    type_normalized = []

    # Entity type analysis
    baseline_types = Counter()
    passed_types = Counter()
    blocked_types = Counter()

    # Confidence analysis
    baseline_confidences = []
    passed_confidences = []
    blocked_confidences = []

    # Per-tier analysis
    tier_stats = {
        'high': {'baseline': 0, 'passed': 0, 'blocked': 0, 'docs': 0},
        'medium': {'baseline': 0, 'passed': 0, 'blocked': 0, 'docs': 0},
        'low': {'baseline': 0, 'passed': 0, 'blocked': 0, 'docs': 0}
    }

    # Process each document
    for doc_rid, result in result_docs.items():
        # Get baseline data
        baseline_data = baseline_docs.get(doc_rid, {})
        baseline_extraction = baseline_data.get('extraction', {})
        baseline_entities = baseline_extraction.get('entities', [])

        # Get result data
        pipeline_result = result.get('pipeline_result', {})
        changes = result.get('changes', {})
        doc_info = result.get('document', {})

        # Count entities
        baseline_count = len(baseline_entities)
        passed_count = pipeline_result.get('passed_count', 0)
        blocked_count = pipeline_result.get('blocked_count', 0)

        total_baseline_entities += baseline_count
        total_passed_entities += passed_count
        total_blocked_entities += blocked_count

        # Analyze baseline entities
        for entity in baseline_entities:
            entity_type = entity.get('type', 'UNKNOWN')
            baseline_types[entity_type] += 1

            conf = entity.get('confidence')
            if conf is not None:
                baseline_confidences.append(float(conf))

        # Analyze passed entities
        for entity in pipeline_result.get('passed_entities', []):
            entity_type = entity.get('type', 'UNKNOWN')
            passed_types[entity_type] += 1

            conf = entity.get('confidence')
            if conf is not None:
                passed_confidences.append(float(conf))

        # Analyze blocked entities
        for blocked in changes.get('blocked', []):
            entity_type = blocked.get('type', 'UNKNOWN')
            blocked_types[entity_type] += 1

            conf = blocked.get('confidence')
            if conf is not None:
                blocked_confidences.append(float(conf))

            reason = blocked.get('reason', 'Unknown')
            block_reasons[reason] += 1

            module = blocked.get('blocked_by', 'Unknown')
            blocked_by_module[module] += 1

        # Track transformations
        modified_entities.extend(changes.get('modified', []))
        split_entities.extend(changes.get('split', []))
        type_normalized.extend(changes.get('type_normalized', []))

        # Per-tier stats
        tier = doc_info.get('quality_tier', 'unknown')
        if tier in tier_stats:
            tier_stats[tier]['baseline'] += baseline_count
            tier_stats[tier]['passed'] += passed_count
            tier_stats[tier]['blocked'] += blocked_count
            tier_stats[tier]['docs'] += 1

    # Calculate derived metrics
    metrics = {
        'overview': {
            'document_count': doc_count,
            'total_baseline_entities': total_baseline_entities,
            'total_passed_entities': total_passed_entities,
            'total_blocked_entities': total_blocked_entities,
            'overall_pass_rate': total_passed_entities / total_baseline_entities * 100 if total_baseline_entities else 0,
            'overall_block_rate': total_blocked_entities / total_baseline_entities * 100 if total_baseline_entities else 0,
            'quality_improvement': (total_baseline_entities - total_blocked_entities) / total_baseline_entities * 100 if total_baseline_entities else 0
        },
        'block_analysis': {
            'by_reason': dict(block_reasons.most_common(20)),
            'by_module': dict(blocked_by_module.most_common(10))
        },
        'transformations': {
            'canonical_resolutions': len(modified_entities),
            'list_splits': len(split_entities),
            'type_normalizations': len(type_normalized),
            'sample_resolutions': modified_entities[:10],
            'sample_splits': split_entities[:10],
            'sample_normalizations': type_normalized[:10]
        },
        'type_analysis': {
            'baseline_types': dict(baseline_types.most_common(15)),
            'passed_types': dict(passed_types.most_common(15)),
            'blocked_types': dict(blocked_types.most_common(15))
        },
        'confidence_analysis': {
            'baseline': {
                'count': len(baseline_confidences),
                'avg': sum(baseline_confidences) / len(baseline_confidences) if baseline_confidences else 0,
                'min': min(baseline_confidences) if baseline_confidences else 0,
                'max': max(baseline_confidences) if baseline_confidences else 0
            },
            'passed': {
                'count': len(passed_confidences),
                'avg': sum(passed_confidences) / len(passed_confidences) if passed_confidences else 0,
                'min': min(passed_confidences) if passed_confidences else 0,
                'max': max(passed_confidences) if passed_confidences else 0
            },
            'blocked': {
                'count': len(blocked_confidences),
                'avg': sum(blocked_confidences) / len(blocked_confidences) if blocked_confidences else 0,
                'min': min(blocked_confidences) if blocked_confidences else 0,
                'max': max(blocked_confidences) if blocked_confidences else 0
            }
        },
        'tier_analysis': tier_stats
    }

    return metrics


def generate_markdown_report(metrics: Dict, output_path: str):
    """
    Generate a detailed markdown comparison report.

    Args:
        metrics: Calculated metrics dictionary
        output_path: Path to save the report
    """
    overview = metrics['overview']
    block_analysis = metrics['block_analysis']
    transformations = metrics['transformations']
    type_analysis = metrics['type_analysis']
    confidence = metrics['confidence_analysis']
    tier_analysis = metrics['tier_analysis']

    report = []
    report.append("# Re-extraction Comparison Report")
    report.append(f"\n**Generated**: {datetime.utcnow().isoformat()}")
    report.append(f"\n**Phase**: Pilot Re-extraction Analysis")
    report.append("\n---")

    # Executive Summary
    report.append("\n## Executive Summary")
    report.append(f"\n- **Documents Processed**: {overview['document_count']}")
    report.append(f"- **Total Baseline Entities**: {overview['total_baseline_entities']:,}")
    report.append(f"- **Entities After Pipeline**: {overview['total_passed_entities']:,}")
    report.append(f"- **Entities Blocked**: {overview['total_blocked_entities']:,}")
    report.append(f"- **Overall Pass Rate**: {overview['overall_pass_rate']:.1f}%")
    report.append(f"- **Quality Improvement**: Removed {overview['total_blocked_entities']:,} low-quality entities ({overview['overall_block_rate']:.1f}%)")

    # Block Analysis
    report.append("\n## Block Analysis")

    report.append("\n### By Module")
    report.append("\n| Module | Count | Percentage |")
    report.append("|--------|-------|------------|")
    total_blocked = overview['total_blocked_entities']
    for module, count in sorted(block_analysis['by_module'].items(), key=lambda x: -x[1]):
        pct = count / total_blocked * 100 if total_blocked else 0
        report.append(f"| {module} | {count:,} | {pct:.1f}% |")

    report.append("\n### By Reason (Top 10)")
    report.append("\n| Reason | Count |")
    report.append("|--------|-------|")
    for reason, count in list(block_analysis['by_reason'].items())[:10]:
        # Truncate long reasons
        reason_short = reason[:60] + "..." if len(reason) > 60 else reason
        report.append(f"| {reason_short} | {count:,} |")

    # Transformations
    report.append("\n## Transformations")
    report.append(f"\n- **Canonical Resolutions**: {transformations['canonical_resolutions']}")
    report.append(f"- **List Splits**: {transformations['list_splits']}")
    report.append(f"- **Type Normalizations**: {transformations['type_normalizations']}")

    if transformations['sample_resolutions']:
        report.append("\n### Sample Canonical Resolutions")
        report.append("\n| Original | Resolved |")
        report.append("|----------|----------|")
        for item in transformations['sample_resolutions'][:5]:
            report.append(f"| {item.get('original', 'N/A')} | {item.get('resolved', 'N/A')} |")

    if transformations['sample_splits']:
        report.append("\n### Sample List Splits")
        report.append("\n| Original | Split Result |")
        report.append("|----------|--------------|")
        for item in transformations['sample_splits'][:5]:
            report.append(f"| {item.get('original', 'N/A')} | {item.get('result', 'N/A')} |")

    if transformations['sample_normalizations']:
        report.append("\n### Sample Type Normalizations")
        report.append("\n| Entity | Original Type | Normalized |")
        report.append("|--------|---------------|------------|")
        for item in transformations['sample_normalizations'][:5]:
            report.append(f"| {item.get('name', 'N/A')} | {item.get('original_type', 'N/A')} | {item.get('normalized_type', 'N/A')} |")

    # Entity Type Analysis
    report.append("\n## Entity Type Analysis")

    report.append("\n### Baseline vs Passed Types")
    report.append("\n| Type | Baseline | Passed | Blocked | Pass Rate |")
    report.append("|------|----------|--------|---------|-----------|")

    # Combine all types
    all_types = set(type_analysis['baseline_types'].keys()) | set(type_analysis['passed_types'].keys())
    for entity_type in sorted(all_types):
        baseline = type_analysis['baseline_types'].get(entity_type, 0)
        passed = type_analysis['passed_types'].get(entity_type, 0)
        blocked = type_analysis['blocked_types'].get(entity_type, 0)
        pass_rate = passed / baseline * 100 if baseline else 0
        report.append(f"| {entity_type} | {baseline:,} | {passed:,} | {blocked:,} | {pass_rate:.1f}% |")

    # Confidence Analysis
    report.append("\n## Confidence Analysis")
    report.append("\n| Metric | Baseline | Passed | Blocked |")
    report.append("|--------|----------|--------|---------|")
    report.append(f"| Count | {confidence['baseline']['count']:,} | {confidence['passed']['count']:,} | {confidence['blocked']['count']:,} |")
    report.append(f"| Average | {confidence['baseline']['avg']:.3f} | {confidence['passed']['avg']:.3f} | {confidence['blocked']['avg']:.3f} |")
    report.append(f"| Min | {confidence['baseline']['min']:.3f} | {confidence['passed']['min']:.3f} | {confidence['blocked']['min']:.3f} |")
    report.append(f"| Max | {confidence['baseline']['max']:.3f} | {confidence['passed']['max']:.3f} | {confidence['blocked']['max']:.3f} |")

    # Tier Analysis
    report.append("\n## Quality Tier Analysis")
    report.append("\n| Tier | Documents | Baseline | Passed | Blocked | Pass Rate |")
    report.append("|------|-----------|----------|--------|---------|-----------|")
    for tier, stats in tier_analysis.items():
        if stats['docs'] > 0:
            pass_rate = stats['passed'] / stats['baseline'] * 100 if stats['baseline'] else 0
            report.append(f"| {tier} | {stats['docs']} | {stats['baseline']:,} | {stats['passed']:,} | {stats['blocked']:,} | {pass_rate:.1f}% |")

    # Recommendations
    report.append("\n## Recommendations")
    report.append("\nBased on the analysis:")

    total_blocked = overview['total_blocked_entities']
    if total_blocked > 0:
        # Get top blocking module
        top_module = max(block_analysis['by_module'].items(), key=lambda x: x[1])
        report.append(f"\n1. **{top_module[0]}** is the most active filter, blocking {top_module[1]:,} entities ({top_module[1]/total_blocked*100:.1f}%)")

    if transformations['canonical_resolutions'] > 0:
        report.append(f"\n2. **Canonical resolution** normalized {transformations['canonical_resolutions']} entity names, improving consistency")

    if transformations['list_splits'] > 0:
        report.append(f"\n3. **List splitting** separated {transformations['list_splits']} compound entities into individuals")

    # Quality improvement note
    improvement = overview['overall_block_rate']
    if improvement > 0:
        report.append(f"\n4. Overall quality improved by removing {improvement:.1f}% low-quality entities")

    report.append("\n---")
    report.append(f"\n*Report generated by compare_extractions.py*")

    # Write report
    with open(output_path, 'w') as f:
        f.write('\n'.join(report))

    print(f"Report saved to: {output_path}")


def print_summary(metrics: Dict):
    """Print a console summary of the comparison."""
    overview = metrics['overview']
    block_analysis = metrics['block_analysis']

    print()
    print("-" * 70)
    print("COMPARISON SUMMARY")
    print("-" * 70)

    print(f"\nDocuments: {overview['document_count']}")
    print(f"Baseline entities: {overview['total_baseline_entities']:,}")
    print(f"Passed entities: {overview['total_passed_entities']:,} ({overview['overall_pass_rate']:.1f}%)")
    print(f"Blocked entities: {overview['total_blocked_entities']:,} ({overview['overall_block_rate']:.1f}%)")

    print(f"\nBlocked by module:")
    for module, count in sorted(block_analysis['by_module'].items(), key=lambda x: -x[1]):
        pct = count / overview['total_blocked_entities'] * 100 if overview['total_blocked_entities'] else 0
        print(f"  {module:30s}: {count:5d} ({pct:5.1f}%)")

    trans = metrics['transformations']
    print(f"\nTransformations:")
    print(f"  Canonical resolutions: {trans['canonical_resolutions']}")
    print(f"  List splits: {trans['list_splits']}")
    print(f"  Type normalizations: {trans['type_normalizations']}")


def main():
    """Main execution."""
    parser = argparse.ArgumentParser(
        description="Compare baseline and pipeline-processed extractions"
    )
    parser.add_argument(
        '--baseline', '-b', type=str, default=None,
        help='Baseline file path (default: scripts/reextraction/baseline_entities.json)'
    )
    parser.add_argument(
        '--results', '-r', type=str, default=None,
        help='Results file path (default: scripts/reextraction/pilot_results.json)'
    )
    parser.add_argument(
        '--output', '-o', type=str, default=None,
        help='Output report path (default: scripts/reextraction/comparison_report.md)'
    )
    parser.add_argument(
        '--json', '-j', action='store_true',
        help='Also output metrics as JSON'
    )

    args = parser.parse_args()

    # Set default paths
    script_dir = Path(__file__).parent
    baseline_path = Path(args.baseline) if args.baseline else script_dir / 'baseline_entities.json'
    results_path = Path(args.results) if args.results else script_dir / 'pilot_results.json'
    output_path = Path(args.output) if args.output else script_dir / 'comparison_report.md'

    # Validate inputs exist
    if not baseline_path.exists():
        print(f"ERROR: {baseline_path} not found")
        print("Run extract_baseline_entities.py first")
        return 1

    if not results_path.exists():
        print(f"ERROR: {results_path} not found")
        print("Run reextract_pilot.py first")
        return 1

    try:
        print("=" * 70)
        print("EXTRACTION COMPARISON")
        print("=" * 70)
        print()

        # Load data
        print(f"Loading baseline from: {baseline_path}")
        baseline = load_json(str(baseline_path))

        print(f"Loading results from: {results_path}")
        results = load_json(str(results_path))

        # Calculate metrics
        print("Calculating metrics...")
        metrics = calculate_metrics(baseline, results)

        # Print summary
        print_summary(metrics)

        # Generate report
        print()
        print("Generating report...")
        generate_markdown_report(metrics, str(output_path))

        # Optionally save JSON metrics
        if args.json:
            json_path = output_path.with_suffix('.json')
            with open(json_path, 'w') as f:
                json.dump(metrics, f, indent=2, default=str)
            print(f"Metrics saved to: {json_path}")

        print()
        print("=" * 70)
        print("COMPARISON COMPLETE")
        print("=" * 70)
        print()
        print(f"Report generated: {output_path}")
        print()
        print("Review the report to understand:")
        print("  - Which modules blocked the most entities")
        print("  - What transformations were applied")
        print("  - How quality differs by document tier")

        return 0

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
