"""
POC Validation Script - Validates EntityQualityFilter against quality review data.

This script tests the POC filter against entities flagged in the quality review
to measure what percentage of issues would be automatically blocked.

Usage:
    python -m src.knowledge_graph.improvements.validate_poc

Author: Claude Code
Date: 2025-12-08
"""

import csv
import json
from pathlib import Path
from typing import Dict, List, Tuple
from collections import defaultdict
from datetime import datetime
import sys

# Add project root to path
project_root = Path(__file__).parents[3]
sys.path.insert(0, str(project_root))

from src.knowledge_graph.improvements.entity_quality_filter import EntityQualityFilter, FilterConfig


def load_quality_review_data(limit: int = None) -> List[Dict]:
    """Load flagged entities from quality review CSV."""

    review_file = project_root / 'reports' / 'kg_quality_review_20251208' / 'entity_quality_issues.csv'

    if not review_file.exists():
        raise FileNotFoundError(f"Quality review file not found: {review_file}")

    entities = []
    with open(review_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if limit and i >= limit:
                break
            entities.append(row)

    return entities


def validate_against_quality_review(verbose: bool = False) -> Tuple[Dict, float]:
    """
    Test POC filter against actual flagged entities from quality review.

    Returns:
        Tuple of (results_dict, block_rate_percentage)
    """

    print("\n" + "=" * 60)
    print("POC Validation Against Quality Review")
    print("=" * 60 + "\n")

    # Load quality review data
    print("Loading quality review data...")
    flagged_entities = load_quality_review_data()
    total_flagged = len(flagged_entities)
    print(f"Loaded {total_flagged:,} flagged entities\n")

    # Initialize filter
    filter_obj = EntityQualityFilter(FilterConfig())

    # Test each flagged entity
    results = []
    blocked_count = 0
    category_stats = defaultdict(lambda: {'total': 0, 'blocked': 0})
    block_reasons_stats = defaultdict(int)

    for row in flagged_entities:
        entity_name = row.get('entity_name', '')
        entity_type = row.get('entity_type', '')
        issue_category = row.get('issue_category', 'unknown')
        confidence = float(row.get('confidence', 0))

        # Test with POC filter
        is_valid, reasons = filter_obj.filter_with_reasons(entity_name, entity_type)
        would_block = not is_valid

        if would_block:
            blocked_count += 1
            for reason in reasons:
                block_reasons_stats[reason] += 1

        # Track by issue category
        category_stats[issue_category]['total'] += 1
        if would_block:
            category_stats[issue_category]['blocked'] += 1

        results.append({
            'entity_name': entity_name,
            'entity_type': entity_type,
            'quality_review_issue': issue_category,
            'confidence': confidence,
            'poc_would_block': would_block,
            'poc_block_reasons': reasons
        })

    block_rate = (blocked_count / total_flagged * 100) if total_flagged > 0 else 0

    # Generate report
    print("-" * 60)
    print("SUMMARY")
    print("-" * 60)
    print(f"Total Flagged Entities: {total_flagged:,}")
    print(f"POC Would Block:        {blocked_count:,} ({block_rate:.1f}%)")
    print(f"Still Need Review:      {total_flagged - blocked_count:,}")
    print()

    # Breakdown by issue category
    print("-" * 60)
    print("BREAKDOWN BY ISSUE CATEGORY")
    print("-" * 60)
    for category, stats in sorted(category_stats.items(), key=lambda x: -x[1]['total']):
        cat_blocked = stats['blocked']
        cat_total = stats['total']
        cat_rate = (cat_blocked / cat_total * 100) if cat_total > 0 else 0
        print(f"  {category:25s}: {cat_blocked:4d}/{cat_total:4d} ({cat_rate:5.1f}%)")
    print()

    # Breakdown by POC block reason
    print("-" * 60)
    print("POC BLOCK REASONS (why entities were blocked)")
    print("-" * 60)
    for reason, count in sorted(block_reasons_stats.items(), key=lambda x: -x[1]):
        print(f"  {reason:20s}: {count:,}")
    print()

    # Sample blocked entities
    print("-" * 60)
    print("SAMPLE BLOCKED ENTITIES (first 15)")
    print("-" * 60)
    sample_blocked = [r for r in results if r['poc_would_block']][:15]
    for r in sample_blocked:
        reasons_str = ", ".join(r['poc_block_reasons'])
        print(f"  '{r['entity_name'][:40]:40s}' ({r['entity_type']}) -> {reasons_str}")
    print()

    # Sample entities still needing review
    print("-" * 60)
    print("SAMPLE ENTITIES STILL NEEDING REVIEW (first 15)")
    print("-" * 60)
    sample_pass = [r for r in results if not r['poc_would_block']][:15]
    for r in sample_pass:
        print(f"  '{r['entity_name'][:40]:40s}' ({r['entity_type']}) - {r['quality_review_issue']}")
    print()

    # Save detailed results
    output_dir = Path(__file__).parent
    output_file = output_dir / 'poc_validation_results.json'

    validation_report = {
        'timestamp': datetime.now().isoformat(),
        'summary': {
            'total_flagged': total_flagged,
            'blocked_by_poc': blocked_count,
            'block_rate_pct': round(block_rate, 2),
            'still_need_review': total_flagged - blocked_count
        },
        'by_issue_category': {
            cat: {
                'total': stats['total'],
                'blocked': stats['blocked'],
                'block_rate_pct': round(stats['blocked'] / stats['total'] * 100, 2) if stats['total'] > 0 else 0
            }
            for cat, stats in category_stats.items()
        },
        'by_block_reason': dict(block_reasons_stats),
        'sample_blocked': sample_blocked[:50],
        'sample_needing_review': sample_pass[:50]
    }

    with open(output_file, 'w') as f:
        json.dump(validation_report, f, indent=2)

    # Calculate block rates for different categories
    # 1. Generic/garbage entities (what POC is designed to block)
    garbage_categories = ['generic_noun', 'plural_generic']
    garbage_total = sum(category_stats[cat]['total'] for cat in garbage_categories if cat in category_stats)
    garbage_blocked = sum(category_stats[cat]['blocked'] for cat in garbage_categories if cat in category_stats)
    garbage_block_rate = (garbage_blocked / garbage_total * 100) if garbage_total > 0 else 0

    # 2. Short acronyms (need expansion, not blocking)
    acronym_stats = category_stats.get('short_acronym', {'total': 0, 'blocked': 0})

    # 3. Low confidence (needs extraction metadata)
    low_conf_stats = category_stats.get('low_confidence', {'total': 0, 'blocked': 0})

    print("=" * 60)
    print("SUCCESS METRICS")
    print("=" * 60)

    print(f"\n1. GARBAGE ENTITY BLOCK RATE: {garbage_block_rate:.1f}%")
    print(f"   (Generic/junk entities the POC is designed to block)")
    print(f"   - generic_noun: {category_stats['generic_noun']['blocked']}/{category_stats['generic_noun']['total']} ({100*category_stats['generic_noun']['blocked']/max(1,category_stats['generic_noun']['total']):.0f}%)")
    print(f"   - plural_generic: {category_stats['plural_generic']['blocked']}/{category_stats['plural_generic']['total']} ({100*category_stats['plural_generic']['blocked']/max(1,category_stats['plural_generic']['total']):.0f}%)")

    print(f"\n2. SHORT ACRONYMS: {acronym_stats['total']:,}")
    print(f"   (Need contextual expansion, not blocking)")
    print(f"   Examples: IBC, IRI, CDP, EU - valid abbreviations")

    print(f"\n3. LOW CONFIDENCE ENTITIES: {low_conf_stats['total']:,}")
    print(f"   (Require extraction confidence thresholds)")
    print(f"   POC bonus blocks: {low_conf_stats['blocked']} via pattern matching")

    print(f"\n4. OVERALL IMPACT: {blocked_count:,} total entities blocked")
    print(f"   Breakdown by POC filter type:")
    for reason, count in sorted(block_reasons_stats.items(), key=lambda x: -x[1]):
        print(f"   - {reason}: {count}")

    # Overall assessment
    print("\n" + "=" * 60)
    if garbage_block_rate >= 90:
        print("SUCCESS! POC validates for garbage entity filtering!")
        print(f"Blocks {garbage_block_rate:.0f}% of generic/junk entities")
    else:
        print(f"POC blocks {garbage_block_rate:.0f}% of garbage entities (target: 90%)")
    print("=" * 60)
    print(f"\nDetailed results saved to: {output_file}")

    # Add garbage entity metrics to report
    validation_report['garbage_entity_filter'] = {
        'total': garbage_total,
        'blocked': garbage_blocked,
        'block_rate_pct': round(garbage_block_rate, 2)
    }

    # Re-save with updated metrics
    with open(output_file, 'w') as f:
        json.dump(validation_report, f, indent=2)

    return validation_report, garbage_block_rate


def main():
    """Main entry point."""
    try:
        report, garbage_block_rate = validate_against_quality_review()
        # Success if garbage entity block rate >= 90%
        return 0 if garbage_block_rate >= 90 else 1
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
