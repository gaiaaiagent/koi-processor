#!/usr/bin/env python3
"""
Validate fresh extractions through the quality pipeline.

This script validates entity extractions to ensure they meet quality standards
before deployment to production. It analyzes:
- Pass rate through pipeline
- Entity type distribution
- Blocked entity patterns
- Confidence scores
- Comparison with re-extraction baseline

Target: 97%+ pass rate (consistent with re-extraction results)

Usage:
    python validate_fresh_extractions.py
    python validate_fresh_extractions.py --source discourse
    python validate_fresh_extractions.py --since 2025-12-09
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from collections import Counter

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: psycopg2 not installed. Install with: pip install psycopg2-binary")
    sys.exit(1)

# Import pipeline
try:
    from knowledge_graph.postprocessing import (
        PipelineOrchestrator,
        ProcessingContext,
        Entity as PipelineEntity,
        create_pipeline_from_config
    )
    from knowledge_graph.postprocessing.modules import (
        ConfidenceFilterModule,
        CanonicalResolverModule,
        EntityQualityFilterModule,
        ListSplitterModule,
        OntologyNormalizerModule
    )
    HAS_PIPELINE = True
except ImportError:
    print("Warning: Pipeline not available")
    HAS_PIPELINE = False


def connect_db(host: str = "localhost", port: int = 5433,
               database: str = "eliza", user: str = "postgres",
               password: str = "postgres"):
    """Connect to PostgreSQL database."""
    return psycopg2.connect(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        cursor_factory=RealDictCursor
    )


def get_fresh_extractions(conn, source: Optional[str] = None,
                          since: Optional[str] = None) -> List[Dict]:
    """
    Get fresh extractions from database.

    Args:
        conn: Database connection
        source: Optional source filter
        since: Optional date filter (YYYY-MM-DD)

    Returns:
        List of extraction records
    """
    cursor = conn.cursor()

    where_clauses = ["e.extractor_version LIKE '%fresh%'"]

    if source:
        source_patterns = {
            'discourse': 'discourse-sensor%',
            'youtube': 'youtube-sensor%',
            'gitlab': 'gitlab-sensor%',
            'github-activity': 'github-activity-sensor%',
            'github-markdown': 'github-sensor%',
        }
        pattern = source_patterns.get(source, f'{source}%')
        where_clauses.append(f"m.source_sensor LIKE '{pattern}'")

    if since:
        where_clauses.append(f"e.created_at >= '{since}'")

    where_sql = " AND ".join(where_clauses)

    query = f"""
    SELECT
        e.id,
        e.memory_rid,
        e.entities,
        e.relationships,
        e.confidence_score,
        e.extractor_version,
        e.created_at,
        m.source_sensor
    FROM koi_kg_extractions e
    JOIN koi_memories m ON m.rid = e.memory_rid
    WHERE {where_sql}
    ORDER BY e.created_at DESC
    """

    cursor.execute(query)
    return cursor.fetchall()


def create_pipeline() -> PipelineOrchestrator:
    """Create the validation pipeline."""
    config_path = Path(__file__).parent.parent.parent / 'src/knowledge_graph/config/pipeline_config.json'

    if config_path.exists():
        return create_pipeline_from_config(str(config_path))
    else:
        return PipelineOrchestrator([
            ConfidenceFilterModule({'entity_threshold': 0.70, 'relationship_threshold': 0.80}),
            CanonicalResolverModule(),
            EntityQualityFilterModule(),
            ListSplitterModule(),
            OntologyNormalizerModule()
        ])


def validate_extraction(extraction: Dict, pipeline: PipelineOrchestrator) -> Dict[str, Any]:
    """
    Validate a single extraction through the pipeline.

    Args:
        extraction: Extraction record from database
        pipeline: Processing pipeline

    Returns:
        Validation results
    """
    entities = extraction.get('entities', [])
    if not entities:
        return {
            'rid': extraction['memory_rid'],
            'total': 0,
            'passed': 0,
            'blocked': 0,
            'pass_rate': 100.0,
            'blocked_entities': []
        }

    # Convert to pipeline entities
    pipeline_entities = []
    for e in entities:
        pe = PipelineEntity(
            name=e.get('name', ''),
            type=e.get('type', 'UNKNOWN'),
            confidence=e.get('confidence', 0.8)
        )
        pipeline_entities.append(pe)

    # Process through pipeline
    context = ProcessingContext(entities=pipeline_entities)
    pipeline.reset()
    result = pipeline.process(context)

    # Get blocked entities
    blocked_entities = [
        {
            'name': e.name,
            'type': e.type,
            'reason': e.metadata.get('blocked_reason', 'Unknown'),
            'blocked_by': e.metadata.get('blocked_by', 'Unknown')
        }
        for e in result.blocked_entities
    ]

    total = len(entities)
    passed = len(result.entities)
    blocked = len(blocked_entities)

    return {
        'rid': extraction['memory_rid'],
        'source': extraction.get('source_sensor', 'unknown'),
        'total': total,
        'passed': passed,
        'blocked': blocked,
        'pass_rate': round(passed / total * 100, 2) if total > 0 else 100.0,
        'blocked_entities': blocked_entities
    }


def aggregate_results(results: List[Dict]) -> Dict[str, Any]:
    """
    Aggregate validation results.

    Args:
        results: List of per-document validation results

    Returns:
        Aggregated statistics
    """
    total_entities = sum(r['total'] for r in results)
    total_passed = sum(r['passed'] for r in results)
    total_blocked = sum(r['blocked'] for r in results)

    # Count by source
    by_source = {}
    for r in results:
        source = r.get('source', 'unknown')
        if source not in by_source:
            by_source[source] = {'documents': 0, 'entities': 0, 'passed': 0, 'blocked': 0}
        by_source[source]['documents'] += 1
        by_source[source]['entities'] += r['total']
        by_source[source]['passed'] += r['passed']
        by_source[source]['blocked'] += r['blocked']

    # Calculate pass rates by source
    for source, stats in by_source.items():
        if stats['entities'] > 0:
            stats['pass_rate'] = round(stats['passed'] / stats['entities'] * 100, 2)
        else:
            stats['pass_rate'] = 100.0

    # Collect all blocked entities
    all_blocked = []
    for r in results:
        all_blocked.extend(r['blocked_entities'])

    # Analyze block reasons
    block_reasons = Counter()
    block_modules = Counter()
    for b in all_blocked:
        block_reasons[b.get('reason', 'Unknown')] += 1
        block_modules[b.get('blocked_by', 'Unknown')] += 1

    # Sample blocked entities
    sample_blocked = all_blocked[:50] if len(all_blocked) > 50 else all_blocked

    return {
        'summary': {
            'documents': len(results),
            'total_entities': total_entities,
            'entities_passed': total_passed,
            'entities_blocked': total_blocked,
            'pass_rate': round(total_passed / total_entities * 100, 2) if total_entities > 0 else 0,
            'block_rate': round(total_blocked / total_entities * 100, 2) if total_entities > 0 else 0
        },
        'by_source': by_source,
        'block_analysis': {
            'by_reason': dict(block_reasons.most_common(20)),
            'by_module': dict(block_modules.most_common())
        },
        'sample_blocked': sample_blocked
    }


def generate_report(aggregated: Dict, output_path: str):
    """Generate validation report."""
    report = []
    report.append("=" * 70)
    report.append("FRESH EXTRACTION VALIDATION REPORT")
    report.append("=" * 70)
    report.append(f"Generated: {datetime.utcnow().isoformat()}")
    report.append("")

    # Summary
    summary = aggregated['summary']
    report.append("-" * 70)
    report.append("SUMMARY")
    report.append("-" * 70)
    report.append(f"Documents validated: {summary['documents']}")
    report.append(f"Total entities: {summary['total_entities']}")
    report.append(f"Entities passed: {summary['entities_passed']} ({summary['pass_rate']}%)")
    report.append(f"Entities blocked: {summary['entities_blocked']} ({summary['block_rate']}%)")
    report.append("")

    # Target comparison
    target_rate = 97.0
    if summary['pass_rate'] >= target_rate:
        report.append(f"TARGET MET: {summary['pass_rate']}% >= {target_rate}% target")
    else:
        report.append(f"TARGET NOT MET: {summary['pass_rate']}% < {target_rate}% target")
    report.append("")

    # By source
    report.append("-" * 70)
    report.append("BY SOURCE")
    report.append("-" * 70)
    for source, stats in sorted(aggregated['by_source'].items()):
        report.append(f"{source}:")
        report.append(f"  Documents: {stats['documents']}")
        report.append(f"  Entities: {stats['entities']}")
        report.append(f"  Pass rate: {stats['pass_rate']}%")
    report.append("")

    # Block analysis
    report.append("-" * 70)
    report.append("BLOCK ANALYSIS")
    report.append("-" * 70)
    report.append("By Module:")
    for module, count in aggregated['block_analysis']['by_module'].items():
        pct = count / summary['entities_blocked'] * 100 if summary['entities_blocked'] > 0 else 0
        report.append(f"  {module}: {count} ({pct:.1f}%)")
    report.append("")
    report.append("Top Block Reasons:")
    for reason, count in list(aggregated['block_analysis']['by_reason'].items())[:10]:
        report.append(f"  {reason}: {count}")
    report.append("")

    # Sample blocked
    report.append("-" * 70)
    report.append("SAMPLE BLOCKED ENTITIES (first 20)")
    report.append("-" * 70)
    for b in aggregated['sample_blocked'][:20]:
        report.append(f"  '{b['name']}' ({b['type']}) - {b['reason']}")
    report.append("")

    # Write report
    report_text = "\n".join(report)
    print(report_text)

    with open(output_path, 'w') as f:
        f.write(report_text)

    # Also save JSON
    json_path = output_path.replace('.txt', '.json')
    with open(json_path, 'w') as f:
        json.dump(aggregated, f, indent=2, default=str)

    print(f"\nReport saved to: {output_path}")
    print(f"JSON data saved to: {json_path}")


def main():
    """Main execution."""
    if not HAS_PIPELINE:
        print("ERROR: Pipeline not available. Cannot validate.")
        return 1

    parser = argparse.ArgumentParser(
        description="Validate fresh extractions through quality pipeline"
    )
    parser.add_argument(
        '--source', '-s', type=str, default=None,
        choices=['discourse', 'youtube', 'gitlab', 'github-activity', 'github-markdown'],
        help='Filter by source'
    )
    parser.add_argument(
        '--since', type=str, default=None,
        help='Filter extractions since date (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--host', type=str, default='localhost',
        help='Database host'
    )
    parser.add_argument(
        '--port', type=int, default=5433,
        help='Database port'
    )
    parser.add_argument(
        '--output', '-o', type=str, default=None,
        help='Output report path'
    )

    args = parser.parse_args()

    # Set default output
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_path = args.output or f"fresh_validation_report_{timestamp}.txt"

    try:
        # Connect to database
        print(f"Connecting to database at {args.host}:{args.port}...")
        conn = connect_db(host=args.host, port=args.port)
        print("Connected successfully")
        print()

        # Get extractions
        print("Querying fresh extractions...")
        extractions = get_fresh_extractions(conn, args.source, args.since)
        print(f"Found {len(extractions)} fresh extractions")

        if not extractions:
            print("No fresh extractions found. Nothing to validate.")
            return 0

        # Create pipeline
        print("\nCreating validation pipeline...")
        pipeline = create_pipeline()
        print(f"Pipeline created with {len(pipeline)} modules")

        # Validate each extraction
        print("\nValidating extractions...")
        results = []
        for i, extraction in enumerate(extractions, 1):
            if i % 100 == 0 or i == len(extractions):
                print(f"  [{i}/{len(extractions)}] Validating...")

            result = validate_extraction(extraction, pipeline)
            results.append(result)

        conn.close()

        # Aggregate results
        print("\nAggregating results...")
        aggregated = aggregate_results(results)

        # Generate report
        print("\nGenerating report...")
        generate_report(aggregated, output_path)

        # Return success/failure based on target
        target_rate = 97.0
        actual_rate = aggregated['summary']['pass_rate']

        print()
        print("=" * 70)
        print("VALIDATION COMPLETE")
        print("=" * 70)
        print()
        print(f"Pass rate: {actual_rate}% (target: {target_rate}%)")

        if actual_rate >= target_rate:
            print("STATUS: PASSED - Ready for deployment")
            return 0
        else:
            print("STATUS: NEEDS REVIEW - Below target threshold")
            return 1

    except psycopg2.OperationalError as e:
        print(f"ERROR: Database connection failed: {e}")
        print(f"\nMake sure you have an SSH tunnel:")
        print(f"  ssh -L {args.port}:localhost:5433 darren@202.61.196.119")
        return 1
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
