#!/usr/bin/env python3
"""
Aggregate Weeks 3-4 results for comprehensive analysis.

This script:
1. Combines all Week 3 and Week 4 results into a single dataset
2. Validates data integrity
3. Calculates comprehensive metrics
4. Generates summary statistics
"""

import json
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime


def load_results(file_path: Path) -> dict:
    """Load results JSON file."""
    with open(file_path) as f:
        data = json.load(f)
    # Handle both formats: {results: {...}} and direct {...}
    return data.get('results', data)


def detect_source(doc_id: str) -> str:
    """Detect source type from document ID."""
    doc_lower = doc_id.lower()
    if 'discourse' in doc_lower or 'forum' in doc_lower:
        return 'discourse'
    elif 'website' in doc_lower or 'web' in doc_lower:
        return 'website'
    elif 'notion' in doc_lower:
        return 'notion'
    elif 'podcast' in doc_lower:
        return 'podcast'
    elif 'github' in doc_lower:
        return 'github'
    elif 'gitlab' in doc_lower:
        return 'gitlab'
    elif 'youtube' in doc_lower:
        return 'youtube'
    else:
        return 'other'


def aggregate_metrics(results: dict) -> dict:
    """Calculate aggregate metrics from results."""
    baseline_total = 0
    passed_total = 0
    blocked_total = 0

    by_source = defaultdict(lambda: {
        'docs': 0, 'baseline': 0, 'passed': 0, 'blocked': 0
    })
    by_module = defaultdict(int)
    by_pattern = defaultdict(int)
    by_confidence = {'high': 0, 'medium': 0, 'low': 0}

    blocked_entities = []

    for doc_id, doc_data in results.items():
        # Source detection
        source = detect_source(doc_id)

        # Get entity counts
        baseline = doc_data.get('baseline', {}).get('entity_count', 0)
        pipeline_result = doc_data.get('pipeline_result', {})
        passed = pipeline_result.get('passed_count', 0)
        blocked = pipeline_result.get('blocked_count', 0)

        baseline_total += baseline
        passed_total += passed
        blocked_total += blocked

        by_source[source]['docs'] += 1
        by_source[source]['baseline'] += baseline
        by_source[source]['passed'] += passed
        by_source[source]['blocked'] += blocked

        # Blocked entity analysis
        for entity in pipeline_result.get('blocked_entities', []):
            module = entity.get('blocked_by', 'Unknown')
            by_module[module] += 1

            reason = entity.get('reason', 'unknown')
            # Extract pattern from reason
            pattern = reason.split(':')[0] if ':' in reason else reason
            pattern = pattern.split(',')[0].strip()  # Take first pattern
            by_pattern[pattern] += 1

            blocked_entities.append({
                'name': entity.get('name', 'unknown'),
                'type': entity.get('type', 'unknown'),
                'module': module,
                'reason': reason,
                'source': source
            })

        # Confidence analysis (from passed entities)
        for entity in pipeline_result.get('passed_entities', []):
            conf = entity.get('confidence', 0)
            if conf >= 0.85:
                by_confidence['high'] += 1
            elif conf >= 0.70:
                by_confidence['medium'] += 1
            else:
                by_confidence['low'] += 1

    pass_rate = (passed_total / baseline_total * 100) if baseline_total > 0 else 0
    block_rate = (blocked_total / baseline_total * 100) if baseline_total > 0 else 0

    return {
        'generated_at': datetime.now().isoformat(),
        'totals': {
            'documents': len(results),
            'baseline': baseline_total,
            'passed': passed_total,
            'blocked': blocked_total,
            'pass_rate': round(pass_rate, 2),
            'block_rate': round(block_rate, 2)
        },
        'by_source': dict(by_source),
        'by_module': dict(by_module),
        'by_pattern': dict(by_pattern),
        'by_confidence': by_confidence,
        'blocked_sample': blocked_entities[:50]  # Sample for review
    }


def print_metrics(metrics: dict, title: str = ""):
    """Print formatted metrics."""
    print("\n" + "=" * 70)
    print(title if title else "AGGREGATE METRICS")
    print("=" * 70)

    totals = metrics['totals']
    print(f"\nDocuments: {totals['documents']}")
    print(f"Baseline Entities: {totals['baseline']:,}")
    print(f"Passed: {totals['passed']:,} ({totals['pass_rate']:.2f}%)")
    print(f"Blocked: {totals['blocked']:,} ({totals['block_rate']:.2f}%)")

    print("\nBy Source:")
    for source, data in sorted(metrics['by_source'].items(),
                               key=lambda x: x[1]['docs'], reverse=True):
        if data['baseline'] > 0:
            pass_rate = data['passed'] / data['baseline'] * 100
            block_rate = data['blocked'] / data['baseline'] * 100
        else:
            pass_rate = block_rate = 0
        print(f"  {source:15} {data['docs']:4} docs | "
              f"{data['baseline']:6,} → {data['passed']:6,} "
              f"({pass_rate:.1f}% pass, {block_rate:.1f}% block)")

    print("\nBy Module:")
    total_blocked = sum(metrics['by_module'].values())
    for module, count in sorted(metrics['by_module'].items(),
                                key=lambda x: x[1], reverse=True):
        pct = (count / total_blocked * 100) if total_blocked > 0 else 0
        print(f"  {module:30} {count:4} ({pct:.1f}%)")

    print("\nTop Block Patterns:")
    for pattern, count in sorted(metrics['by_pattern'].items(),
                                 key=lambda x: x[1], reverse=True)[:15]:
        pct = (count / total_blocked * 100) if total_blocked > 0 else 0
        print(f"  {pattern:30} {count:4} ({pct:.1f}%)")

    print("\nConfidence Distribution (passed entities):")
    total_conf = sum(metrics['by_confidence'].values())
    for tier, count in metrics['by_confidence'].items():
        pct = (count / total_conf * 100) if total_conf > 0 else 0
        print(f"  {tier:10} {count:6,} ({pct:.1f}%)")


def main():
    """Main aggregation function."""
    base_path = Path(__file__).parent

    print("=" * 70)
    print("WEEKS 3-4 AGGREGATION")
    print("=" * 70)

    # Find all result files
    week3_path = base_path / 'week3_results'
    week4_path = base_path / 'week4_results'

    combined = {}
    week3_combined = {}
    week4_combined = {}

    # Load Week 3 results
    print("\nLoading Week 3 results...")
    if week3_path.exists():
        for result_file in week3_path.glob('*_results.json'):
            print(f"  Loading: {result_file.name}")
            results = load_results(result_file)
            week3_combined.update(results)
        print(f"  Week 3 total: {len(week3_combined)} documents")
    else:
        print("  WARNING: week3_results directory not found")

    # Load Week 4 results
    print("\nLoading Week 4 results...")
    if week4_path.exists():
        # Try combined file first
        combined_file = week4_path / 'week4_all_results.json'
        if combined_file.exists():
            print(f"  Loading: {combined_file.name}")
            week4_combined = load_results(combined_file)
        else:
            # Load individual result files
            for result_file in week4_path.glob('*_results.json'):
                print(f"  Loading: {result_file.name}")
                results = load_results(result_file)
                week4_combined.update(results)
        print(f"  Week 4 total: {len(week4_combined)} documents")
    else:
        print("  WARNING: week4_results directory not found")

    # Combine all results
    combined = {**week3_combined, **week4_combined}

    print(f"\nCombined Total: {len(combined)} documents")

    # Check for duplicates
    expected_total = len(week3_combined) + len(week4_combined)
    if len(combined) != expected_total:
        print(f"WARNING: Duplicate document IDs detected!")
        print(f"  Week 3: {len(week3_combined)}")
        print(f"  Week 4: {len(week4_combined)}")
        print(f"  Combined: {len(combined)}")
        print(f"  Expected: {expected_total}")

    # Save combined results
    if combined:
        output_file = base_path / 'weeks_3_4_combined_results.json'
        with open(output_file, 'w') as f:
            json.dump({
                'generated_at': datetime.now().isoformat(),
                'results': combined
            }, f, indent=2)
        print(f"\nSaved: {output_file}")

    # Calculate metrics for each week and combined
    if week3_combined:
        week3_metrics = aggregate_metrics(week3_combined)
        print_metrics(week3_metrics, "WEEK 3 METRICS")

        metrics_file = base_path / 'week3_metrics.json'
        with open(metrics_file, 'w') as f:
            json.dump(week3_metrics, f, indent=2)
        print(f"\nSaved: {metrics_file}")

    if week4_combined:
        week4_metrics = aggregate_metrics(week4_combined)
        print_metrics(week4_metrics, "WEEK 4 METRICS")

        metrics_file = base_path / 'week4_metrics.json'
        with open(metrics_file, 'w') as f:
            json.dump(week4_metrics, f, indent=2)
        print(f"\nSaved: {metrics_file}")

    if combined:
        combined_metrics = aggregate_metrics(combined)
        print_metrics(combined_metrics, "COMBINED WEEKS 3-4 METRICS")

        metrics_file = base_path / 'weeks_3_4_metrics.json'
        with open(metrics_file, 'w') as f:
            json.dump(combined_metrics, f, indent=2)
        print(f"\nSaved: {metrics_file}")

    # Status summary
    print("\n" + "=" * 70)
    print("STATUS SUMMARY")
    print("=" * 70)

    if combined:
        totals = combined_metrics['totals']
        print(f"\nDocuments Processed: {totals['documents']}")
        print(f"Pass Rate: {totals['pass_rate']:.2f}%")
        print(f"Block Rate: {totals['block_rate']:.2f}%")

        # GO/NO-GO assessment
        print("\nGO/NO-GO Assessment:")
        if totals['pass_rate'] >= 97.0:
            print(f"  ✅ Pass rate {totals['pass_rate']:.2f}% exceeds 97% target")
        elif totals['pass_rate'] >= 95.0:
            print(f"  ✅ Pass rate {totals['pass_rate']:.2f}% meets 95% minimum")
        else:
            print(f"  ❌ Pass rate {totals['pass_rate']:.2f}% below 95% minimum")

        if 2.0 <= totals['block_rate'] <= 5.0:
            print(f"  ✅ Block rate {totals['block_rate']:.2f}% within 2-5% target")
        else:
            print(f"  ⚠️  Block rate {totals['block_rate']:.2f}% outside 2-5% target")
    else:
        print("\nNo data available for analysis")

    return combined_metrics if combined else None


if __name__ == '__main__':
    main()
