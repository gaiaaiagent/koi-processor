#!/usr/bin/env python3
"""
Select 100 representative documents for pilot re-extraction.

Stratified sampling based on extraction quality:
- 50 high-quality entities (confidence > 0.85)
- 30 medium-quality (0.70-0.85)
- 20 low-quality (< 0.70)

Focuses on documents with existing extractions from discourse sources
for easier validation.

Output: pilot_documents.json

Usage:
    python scripts/reextraction/select_pilot_documents.py
    python scripts/reextraction/select_pilot_documents.py --count 10  # For testing
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Dict, Optional
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


def get_documents_by_quality(conn, min_conf: float, max_conf: float,
                             limit: int, source_filter: Optional[str] = None) -> List[Dict]:
    """
    Get documents with entities in the specified confidence range.

    Selects documents that have at least 3 entities for meaningful comparison.
    Prioritizes discourse sources for easier validation.

    Args:
        conn: Database connection
        min_conf: Minimum confidence score
        max_conf: Maximum confidence score
        limit: Maximum documents to return
        source_filter: Optional source sensor pattern (e.g., 'discourse')

    Returns:
        List of document dictionaries with metadata
    """
    cursor = conn.cursor()

    # Build source filter clause
    source_clause = ""
    params = [min_conf, max_conf]
    if source_filter:
        source_clause = "AND m.source_sensor ILIKE %s"
        params.append(f"%{source_filter}%")

    query = f"""
    WITH doc_quality AS (
        SELECT
            m.rid as document_rid,
            m.source_sensor,
            (m.content->>'title')::text as title,
            (m.content->>'url')::text as url,
            AVG(e.confidence_score) as avg_confidence,
            COUNT(DISTINCT e.id) as entity_count,
            jsonb_array_length(e.entities) as entities_per_extraction,
            m.created_at
        FROM koi_kg_extractions e
        JOIN koi_memories m ON m.rid = SPLIT_PART(e.memory_rid, '#', 1)
        WHERE
            e.confidence_score BETWEEN %s AND %s
            {source_clause}
            AND e.entities IS NOT NULL
            AND jsonb_array_length(e.entities) > 0
        GROUP BY m.rid, m.source_sensor, m.content, m.created_at
        HAVING COUNT(DISTINCT e.id) >= 1
    )
    SELECT
        document_rid,
        source_sensor,
        title,
        url,
        avg_confidence,
        entity_count,
        created_at
    FROM doc_quality
    ORDER BY RANDOM()
    LIMIT %s;
    """

    params.append(limit)
    cursor.execute(query, tuple(params))
    return cursor.fetchall()


def select_pilot_documents(conn, total_count: int = 100,
                           source_filter: Optional[str] = None) -> List[Dict]:
    """
    Select representative documents using stratified sampling.

    Distribution:
    - 50% high-quality (confidence > 0.85)
    - 30% medium-quality (confidence 0.70-0.85)
    - 20% low-quality (confidence < 0.70)

    Args:
        conn: Database connection
        total_count: Total documents to select
        source_filter: Optional source filter (e.g., 'discourse')

    Returns:
        List of selected document dictionaries
    """
    # Calculate counts for each tier
    high_count = int(total_count * 0.5)
    medium_count = int(total_count * 0.3)
    low_count = total_count - high_count - medium_count

    print("=" * 70)
    print("PILOT DOCUMENT SELECTION")
    print("=" * 70)
    print()
    print(f"Target: {total_count} documents")
    print(f"  - High quality (conf > 0.85): {high_count}")
    print(f"  - Medium quality (0.70-0.85): {medium_count}")
    print(f"  - Low quality (< 0.70): {low_count}")
    if source_filter:
        print(f"  - Source filter: {source_filter}")
    print()

    # Stratified sampling
    print("Selecting documents by quality tier...")

    # High quality (confidence > 0.85)
    print(f"  - Selecting high quality documents...")
    high_quality = get_documents_by_quality(conn, 0.85, 1.0, high_count, source_filter)
    print(f"    Found: {len(high_quality)}")

    # Medium quality (confidence 0.70-0.85)
    print(f"  - Selecting medium quality documents...")
    medium_quality = get_documents_by_quality(conn, 0.70, 0.85, medium_count, source_filter)
    print(f"    Found: {len(medium_quality)}")

    # Low quality (confidence < 0.70)
    print(f"  - Selecting low quality documents...")
    low_quality = get_documents_by_quality(conn, 0.0, 0.70, low_count, source_filter)
    print(f"    Found: {len(low_quality)}")

    # Combine with quality tier labels
    pilot_docs = []

    for doc in high_quality:
        doc_dict = dict(doc)
        doc_dict['quality_tier'] = 'high'
        pilot_docs.append(doc_dict)

    for doc in medium_quality:
        doc_dict = dict(doc)
        doc_dict['quality_tier'] = 'medium'
        pilot_docs.append(doc_dict)

    for doc in low_quality:
        doc_dict = dict(doc)
        doc_dict['quality_tier'] = 'low'
        pilot_docs.append(doc_dict)

    print()
    print(f"Selected {len(pilot_docs)} documents total")

    return pilot_docs


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


def save_pilot_documents(documents: List[Dict], output_path: str):
    """Save pilot documents to JSON file."""
    serializable_docs = [serialize_document(doc) for doc in documents]

    output = {
        'generated_at': datetime.utcnow().isoformat(),
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

    print()
    print(f"Saved to: {output_path}")


def generate_stats(documents: List[Dict]):
    """Generate and display statistics about selected documents."""
    print()
    print("-" * 70)
    print("SELECTION STATISTICS")
    print("-" * 70)

    if not documents:
        print("No documents selected!")
        return

    # Count by tier
    tier_counts = {'high': 0, 'medium': 0, 'low': 0}
    for doc in documents:
        tier_counts[doc.get('quality_tier', 'unknown')] += 1

    print(f"\nBy quality tier:")
    for tier, count in tier_counts.items():
        pct = count / len(documents) * 100
        print(f"  {tier:10s}: {count:4d} ({pct:5.1f}%)")

    # Count by source sensor
    source_counts = {}
    for doc in documents:
        source = doc.get('source_sensor', 'unknown')
        # Extract source type (e.g., 'discourse' from 'discourse-sensor-...')
        source_type = source.split('-')[0] if source else 'unknown'
        source_counts[source_type] = source_counts.get(source_type, 0) + 1

    print(f"\nBy source type:")
    for source, count in sorted(source_counts.items(), key=lambda x: -x[1]):
        pct = count / len(documents) * 100
        print(f"  {source:15s}: {count:4d} ({pct:5.1f}%)")

    # Confidence statistics
    confidences = [doc.get('avg_confidence', 0) for doc in documents if doc.get('avg_confidence')]
    if confidences:
        print(f"\nConfidence scores:")
        print(f"  Average: {sum(confidences) / len(confidences):.3f}")
        print(f"  Min: {min(confidences):.3f}")
        print(f"  Max: {max(confidences):.3f}")

    # Entity counts
    entity_counts = [doc.get('entity_count', 0) for doc in documents]
    if entity_counts:
        print(f"\nEntity counts:")
        print(f"  Total extractions: {sum(entity_counts)}")
        print(f"  Average per doc: {sum(entity_counts) / len(entity_counts):.1f}")


def main():
    """Main execution."""
    parser = argparse.ArgumentParser(
        description="Select pilot documents for re-extraction"
    )
    parser.add_argument(
        '--count', '-c', type=int, default=100,
        help='Total documents to select (default: 100)'
    )
    parser.add_argument(
        '--source', '-s', type=str, default=None,
        help='Filter by source type (e.g., discourse, github, podcast)'
    )
    parser.add_argument(
        '--output', '-o', type=str, default=None,
        help='Output file path (default: scripts/reextraction/pilot_documents.json)'
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
        output_path = Path(__file__).parent / 'pilot_documents.json'

    try:
        # Connect to database
        print(f"Connecting to database at {args.host}:{args.port}...")
        conn = connect_db(host=args.host, port=args.port)
        print("Connected successfully")
        print()

        # Select documents
        documents = select_pilot_documents(
            conn,
            total_count=args.count,
            source_filter=args.source
        )

        conn.close()

        if not documents:
            print("ERROR: No documents found matching criteria")
            return 1

        # Save to file
        save_pilot_documents(documents, str(output_path))

        # Generate stats
        generate_stats(documents)

        print()
        print("=" * 70)
        print("SELECTION COMPLETE")
        print("=" * 70)
        print()
        print("Next steps:")
        print(f"  1. Review {output_path}")
        print("  2. Run extract_baseline_entities.py")

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
