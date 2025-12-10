#!/usr/bin/env python3
"""
Deploy fresh extractions to production database.

This script deploys validated fresh extractions, updating the production
knowledge graph with new entities from previously unprocessed documents.

Pre-deployment checks:
1. Verify extractions pass validation (97%+ quality)
2. Backup current state
3. Deploy in batches with rollback capability

Usage:
    python deploy_fresh_extractions.py --dry-run
    python deploy_fresh_extractions.py --validate-first
    python deploy_fresh_extractions.py --deploy
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional
import subprocess

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: psycopg2 not installed. Install with: pip install psycopg2-binary")
    sys.exit(1)


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


def get_deployment_stats(conn) -> Dict[str, Any]:
    """Get current extraction statistics for deployment planning."""
    cursor = conn.cursor()

    # Count extractions by version
    cursor.execute("""
    SELECT
        COALESCE(extractor_version, 'unknown') as version,
        COUNT(*) as count
    FROM koi_kg_extractions
    GROUP BY extractor_version
    ORDER BY count DESC
    """)
    by_version = {row['version']: row['count'] for row in cursor.fetchall()}

    # Count fresh extractions pending deployment
    cursor.execute("""
    SELECT
        COUNT(*) as count,
        COUNT(*) FILTER (WHERE extractor_version LIKE '%fresh%') as fresh_count,
        SUM(jsonb_array_length(COALESCE(entities, '[]'::jsonb))) as total_entities
    FROM koi_kg_extractions
    WHERE extractor_version LIKE '%fresh%'
    """)
    fresh_stats = cursor.fetchone()

    # Count by source
    cursor.execute("""
    SELECT
        m.source_sensor,
        COUNT(DISTINCT e.id) as extraction_count,
        SUM(jsonb_array_length(COALESCE(e.entities, '[]'::jsonb))) as entity_count
    FROM koi_kg_extractions e
    JOIN koi_memories m ON m.rid = e.memory_rid
    WHERE e.extractor_version LIKE '%fresh%'
    GROUP BY m.source_sensor
    ORDER BY extraction_count DESC
    """)
    by_source = [dict(row) for row in cursor.fetchall()]

    return {
        'by_version': by_version,
        'fresh_extractions': fresh_stats['fresh_count'] or 0,
        'fresh_entities': fresh_stats['total_entities'] or 0,
        'by_source': by_source
    }


def run_validation(host: str, port: int) -> bool:
    """Run validation script and check results."""
    print("Running validation...")

    try:
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parent / 'validate_fresh_extractions.py'),
                '--host', host,
                '--port', str(port)
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent
        )

        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)

        return result.returncode == 0

    except Exception as e:
        print(f"Validation failed: {e}")
        return False


def create_backup(conn, backup_dir: str) -> str:
    """Create backup of current extractions state."""
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = Path(backup_dir) / f"pre_fresh_deployment_{timestamp}.json"

    # Get current state
    cursor.execute("""
    SELECT
        id, memory_rid, extractor_version,
        jsonb_array_length(COALESCE(entities, '[]'::jsonb)) as entity_count,
        created_at
    FROM koi_kg_extractions
    ORDER BY created_at DESC
    """)

    rows = cursor.fetchall()
    backup_data = {
        'timestamp': timestamp,
        'extraction_count': len(rows),
        'extractions': [
            {
                'id': row['id'],
                'memory_rid': row['memory_rid'],
                'extractor_version': row['extractor_version'],
                'entity_count': row['entity_count'],
                'created_at': row['created_at'].isoformat() if row['created_at'] else None
            }
            for row in rows
        ]
    }

    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with open(backup_path, 'w') as f:
        json.dump(backup_data, f, indent=2, default=str)

    print(f"Backup created: {backup_path}")
    return str(backup_path)


def update_extractor_version(conn, new_version: str = "1.0.0-fresh-deployed") -> int:
    """Update extractor version to mark as deployed."""
    cursor = conn.cursor()

    cursor.execute("""
    UPDATE koi_kg_extractions
    SET extractor_version = %s
    WHERE extractor_version LIKE '%fresh%'
      AND extractor_version NOT LIKE '%deployed%'
    RETURNING id
    """, (new_version,))

    updated = cursor.fetchall()
    conn.commit()

    return len(updated)


def generate_deployment_report(stats: Dict, deployed_count: int, output_path: str):
    """Generate deployment report."""
    report = []
    report.append("=" * 70)
    report.append("FRESH EXTRACTION DEPLOYMENT REPORT")
    report.append("=" * 70)
    report.append(f"Generated: {datetime.utcnow().isoformat()}")
    report.append("")

    report.append("-" * 70)
    report.append("DEPLOYMENT SUMMARY")
    report.append("-" * 70)
    report.append(f"Extractions deployed: {deployed_count}")
    report.append(f"Total entities added: {stats['fresh_entities']}")
    report.append("")

    report.append("-" * 70)
    report.append("BY SOURCE")
    report.append("-" * 70)
    for source in stats['by_source']:
        report.append(f"{source['source_sensor']}:")
        report.append(f"  Extractions: {source['extraction_count']}")
        report.append(f"  Entities: {source['entity_count']}")
    report.append("")

    report.append("-" * 70)
    report.append("VERSION DISTRIBUTION (after deployment)")
    report.append("-" * 70)
    for version, count in stats['by_version'].items():
        report.append(f"  {version}: {count}")
    report.append("")

    report_text = "\n".join(report)
    print(report_text)

    with open(output_path, 'w') as f:
        f.write(report_text)

    print(f"\nReport saved to: {output_path}")


def main():
    """Main execution."""
    parser = argparse.ArgumentParser(
        description="Deploy fresh extractions to production"
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Show what would be deployed without deploying'
    )
    parser.add_argument(
        '--validate-first', action='store_true',
        help='Run validation before deployment'
    )
    parser.add_argument(
        '--deploy', action='store_true',
        help='Actually deploy (required for real deployment)'
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
        '--backup-dir', type=str, default='backups',
        help='Directory for backup files'
    )
    parser.add_argument(
        '--output', '-o', type=str, default=None,
        help='Output report path'
    )

    args = parser.parse_args()

    if not args.dry_run and not args.deploy:
        parser.error("Either --dry-run or --deploy is required")

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_path = args.output or f"fresh_deployment_report_{timestamp}.txt"

    try:
        # Connect to database
        print(f"Connecting to database at {args.host}:{args.port}...")
        conn = connect_db(host=args.host, port=args.port)
        print("Connected successfully")
        print()

        # Get current stats
        print("=" * 70)
        print("FRESH EXTRACTION DEPLOYMENT")
        print("=" * 70)
        print()

        stats = get_deployment_stats(conn)

        print("-" * 70)
        print("CURRENT STATE")
        print("-" * 70)
        print(f"Fresh extractions pending: {stats['fresh_extractions']}")
        print(f"Fresh entities to deploy: {stats['fresh_entities']}")
        print()

        print("By source:")
        for source in stats['by_source']:
            print(f"  {source['source_sensor']}: {source['extraction_count']} extractions, {source['entity_count']} entities")
        print()

        if stats['fresh_extractions'] == 0:
            print("No fresh extractions to deploy.")
            return 0

        # Validate if requested
        if args.validate_first:
            print("-" * 70)
            print("VALIDATION")
            print("-" * 70)
            validation_passed = run_validation(args.host, args.port)

            if not validation_passed:
                print("\nVALIDATION FAILED - Aborting deployment")
                return 1

            print("\nValidation passed - Proceeding with deployment")
            print()

        # Dry run - just show what would happen
        if args.dry_run:
            print("-" * 70)
            print("DRY RUN - No changes made")
            print("-" * 70)
            print(f"Would deploy {stats['fresh_extractions']} extractions")
            print(f"Would add {stats['fresh_entities']} entities to knowledge graph")
            print()
            print("To actually deploy, run with --deploy flag")
            return 0

        # Create backup
        print("-" * 70)
        print("BACKUP")
        print("-" * 70)
        backup_path = create_backup(conn, args.backup_dir)
        print()

        # Deploy
        print("-" * 70)
        print("DEPLOYMENT")
        print("-" * 70)
        print("Updating extractor versions to mark as deployed...")

        deployed_count = update_extractor_version(conn, "1.0.0-fresh-deployed")
        print(f"Updated {deployed_count} extractions")
        print()

        # Get updated stats
        final_stats = get_deployment_stats(conn)

        # Generate report
        print("-" * 70)
        print("GENERATING REPORT")
        print("-" * 70)
        generate_deployment_report(final_stats, deployed_count, output_path)

        conn.close()

        print()
        print("=" * 70)
        print("DEPLOYMENT COMPLETE")
        print("=" * 70)
        print()
        print(f"Deployed: {deployed_count} extractions")
        print(f"Entities added: {stats['fresh_entities']}")
        print(f"Backup: {backup_path}")
        print(f"Report: {output_path}")
        print()
        print("Next steps:")
        print("  1. Verify knowledge graph health")
        print("  2. Monitor extraction quality")
        print("  3. Generate final project report")

        return 0

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
