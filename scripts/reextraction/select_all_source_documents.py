#!/usr/bin/env python3
"""
Select ALL documents from a specific source for re-extraction.

Unlike select_pilot_documents.py which uses stratified sampling,
this script selects ALL documents matching the source filter.

Used for Week 3+ full re-extraction phases.

Usage:
    python select_all_source_documents.py --source discourse-forum --output forum_all_documents.json
    python select_all_source_documents.py --source discourse-sensor --output sensor_all_documents.json
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Dict
from datetime import datetime

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


def get_all_documents_by_source(conn, source_filter: str) -> List[Dict]:
    """
    Get ALL documents from a specific source that have extractions.

    Args:
        conn: Database connection
        source_filter: Source sensor pattern (e.g., 'discourse-forum', 'discourse-sensor')

    Returns:
        List of document dictionaries with metadata
    """
    cursor = conn.cursor()

    # Map source filter to actual patterns in the database
    source_patterns = {
        'discourse': 'discourse%',  # All discourse sources
        'discourse-sensor': 'discourse-sensor%',
        'github': 'github%',
        'web': 'website%',
        'website': 'website%',
        'notion': 'notion%',
        'podcast': 'podcast%'
    }

    pattern = source_patterns.get(source_filter, f'{source_filter}%')

    query = """
    WITH doc_data AS (
        SELECT DISTINCT
            SPLIT_PART(m.rid, '#', 1) as document_rid,
            m.source_sensor,
            (m.content->>'title')::text as title,
            (m.content->>'url')::text as url,
            AVG(e.confidence_score) as avg_confidence,
            COUNT(DISTINCT e.id) as extraction_count,
            SUM(jsonb_array_length(COALESCE(e.entities, '[]'::jsonb))) as entity_count,
            MAX(m.created_at) as created_at
        FROM koi_kg_extractions e
        JOIN koi_memories m ON m.rid = e.memory_rid
        WHERE
            m.source_sensor ILIKE %s
            AND e.entities IS NOT NULL
            AND jsonb_array_length(e.entities) > 0
        GROUP BY SPLIT_PART(m.rid, '#', 1), m.source_sensor, m.content->>'title', m.content->>'url'
        HAVING COUNT(DISTINCT e.id) >= 1
    )
    SELECT
        document_rid,
        source_sensor,
        title,
        url,
        avg_confidence,
        extraction_count,
        entity_count,
        created_at
    FROM doc_data
    ORDER BY document_rid;
    """

    cursor.execute(query, (pattern,))
    return cursor.fetchall()


def serialize_document(doc: Dict) -> Dict:
    """Convert document to JSON-serializable format."""
    result = {}
    for key, value in doc.items():
        if hasattr(value, 'isoformat'):  # datetime
            result[key] = value.isoformat()
        elif isinstance(value, (int, float, str, bool, type(None))):
            result[key] = value
        else:
            result[key] = str(value)
    return result


def save_documents(documents: List[Dict], output_path: str, source_filter: str):
    """Save documents to JSON file."""
    serializable_docs = [serialize_document(doc) for doc in documents]

    # Add quality_tier based on confidence for compatibility with existing scripts
    for doc in serializable_docs:
        conf = doc.get('avg_confidence', 0) or 0
        if conf > 0.85:
            doc['quality_tier'] = 'high'
        elif conf >= 0.70:
            doc['quality_tier'] = 'medium'
        else:
            doc['quality_tier'] = 'low'

    output = {
        'generated_at': datetime.utcnow().isoformat(),
        'source_filter': source_filter,
        'total_count': len(serializable_docs),
        'by_tier': {
            'high': len([d for d in serializable_docs if d['quality_tier'] == 'high']),
            'medium': len([d for d in serializable_docs if d['quality_tier'] == 'medium']),
            'low': len([d for d in serializable_docs if d['quality_tier'] == 'low'])
        },
        'documents': serializable_docs
    }

    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"\nSaved to: {output_path}")


def generate_stats(documents: List[Dict], source_filter: str):
    """Generate and display statistics about selected documents."""
    print()
    print("-" * 70)
    print(f"SELECTION STATISTICS: {source_filter}")
    print("-" * 70)

    if not documents:
        print("No documents found!")
        return

    print(f"\nTotal documents: {len(documents)}")

    # Count by source sensor (more specific)
    source_counts = {}
    for doc in documents:
        source = doc.get('source_sensor', 'unknown')
        source_counts[source] = source_counts.get(source, 0) + 1

    print(f"\nBy source sensor:")
    for source, count in sorted(source_counts.items(), key=lambda x: -x[1]):
        pct = count / len(documents) * 100
        print(f"  {source[:40]:40s}: {count:4d} ({pct:5.1f}%)")

    # Confidence statistics
    confidences = [doc.get('avg_confidence', 0) for doc in documents if doc.get('avg_confidence')]
    if confidences:
        print(f"\nConfidence scores:")
        print(f"  Average: {sum(confidences) / len(confidences):.3f}")
        print(f"  Min: {min(confidences):.3f}")
        print(f"  Max: {max(confidences):.3f}")

    # Entity counts
    entity_counts = [doc.get('entity_count', 0) or 0 for doc in documents]
    if entity_counts:
        print(f"\nEntity counts:")
        print(f"  Total: {sum(entity_counts)}")
        print(f"  Average per doc: {sum(entity_counts) / len(entity_counts):.1f}")


def main():
    """Main execution."""
    parser = argparse.ArgumentParser(
        description="Select ALL documents from a specific source for re-extraction"
    )
    parser.add_argument(
        '--source', '-s', type=str, required=True,
        help='Source filter (e.g., discourse-forum, discourse-sensor, github, web, notion)'
    )
    parser.add_argument(
        '--output', '-o', type=str, default=None,
        help='Output file path (default: scripts/reextraction/{source}_all_documents.json)'
    )
    parser.add_argument(
        '--host', type=str, default='localhost',
        help='Database host (default: localhost)'
    )
    parser.add_argument(
        '--port', type=int, default=5433,
        help='Database port (default: 5433)'
    )

    args = parser.parse_args()

    # Set default output path
    if args.output:
        output_path = Path(args.output)
    else:
        safe_source = args.source.replace('-', '_')
        output_path = Path(__file__).parent / f'{safe_source}_all_documents.json'

    try:
        # Connect to database
        print(f"Connecting to database at {args.host}:{args.port}...")
        conn = connect_db(host=args.host, port=args.port)
        print("Connected successfully")
        print()

        print("=" * 70)
        print(f"SELECTING ALL {args.source.upper()} DOCUMENTS")
        print("=" * 70)
        print()

        # Get all documents
        documents = get_all_documents_by_source(conn, args.source)
        conn.close()

        if not documents:
            print(f"ERROR: No documents found for source: {args.source}")
            return 1

        print(f"Found {len(documents)} documents")

        # Save to file
        save_documents(documents, str(output_path), args.source)

        # Generate stats
        generate_stats(documents, args.source)

        print()
        print("=" * 70)
        print("SELECTION COMPLETE")
        print("=" * 70)
        print()
        print("Next steps:")
        print(f"  1. Review {output_path}")
        print("  2. Split into batches for processing")

        return 0

    except psycopg2.OperationalError as e:
        print(f"ERROR: Database connection failed: {e}")
        print()
        print("Make sure you can access the database:")
        print(f"  ssh -L {args.port}:localhost:5433 darren@202.61.196.119")
        return 1
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
