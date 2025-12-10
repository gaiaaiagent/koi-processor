#!/usr/bin/env python3
"""
Query documents that need fresh extraction (never had entity extraction).

This script identifies documents from all sources that have NO entity extractions,
unlike re-extraction which deals with documents that have low-quality extractions.

Scope (1,065 documents expected):
- Discourse: ~569 documents
- YouTube: ~15 documents
- GitLab: ~30 documents
- GitHub Activity: ~23 documents
- GitHub (markdown only): ~428 documents

Usage:
    python query_fresh_extraction_documents.py
    python query_fresh_extraction_documents.py --output fresh_extraction_documents.json
    python query_fresh_extraction_documents.py --source discourse
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional

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


def get_documents_without_extraction(conn, source_filter: Optional[str] = None) -> Dict[str, List[Dict]]:
    """
    Get all documents that have NO entity extractions.

    Args:
        conn: Database connection
        source_filter: Optional source to filter (discourse, youtube, gitlab, github, github-activity)

    Returns:
        Dict mapping source type to list of documents
    """
    cursor = conn.cursor()

    # Define source patterns
    source_patterns = {
        'discourse': 'discourse-sensor%',
        'youtube': 'youtube-sensor%',
        'gitlab': 'gitlab-sensor%',
        'github-activity': 'github-activity-sensor%',
        'github-markdown': 'github-sensor%',  # Special handling below
        'notion': 'notion-sensor%',
        'podcast': 'podcast-sensor%',
        'website': 'website-sensor%',
        'medium': 'medium-sensor%'
    }

    results_by_source = {}

    # Process each source
    sources_to_query = [source_filter] if source_filter else source_patterns.keys()

    for source in sources_to_query:
        if source not in source_patterns:
            print(f"Warning: Unknown source '{source}', skipping")
            continue

        pattern = source_patterns[source]

        # Special handling for GitHub markdown - filter to markdown files only
        if source == 'github-markdown':
            markdown_conditions = """
                AND (
                    m.rid LIKE '%.md#%'
                    OR m.rid LIKE '%.mdx#%'
                    OR m.rid LIKE '%README#%'
                    OR m.rid LIKE '%.rst#%'
                    OR m.rid LIKE '%.txt#%'
                    OR m.rid LIKE '%.asciidoc#%'
                    OR m.rid LIKE '%.adoc#%'
                )
            """
        else:
            markdown_conditions = ""

        query = f"""
        SELECT
            m.rid,
            m.source_sensor,
            m.content->>'title' as title,
            m.content->>'url' as url,
            LENGTH(m.content->>'text') as content_length,
            m.created_at
        FROM koi_memories m
        LEFT JOIN koi_kg_extractions e ON m.rid = e.memory_rid
        WHERE
            m.source_sensor LIKE %s
            AND e.id IS NULL
            AND m.content->>'text' IS NOT NULL
            AND LENGTH(m.content->>'text') > 50
            {markdown_conditions}
        ORDER BY m.created_at DESC
        """

        cursor.execute(query, (pattern,))
        rows = cursor.fetchall()

        documents = []
        for row in rows:
            documents.append({
                'rid': row['rid'],
                'source_sensor': row['source_sensor'],
                'title': row['title'],
                'url': row['url'],
                'content_length': row['content_length'],
                'created_at': row['created_at'].isoformat() if row['created_at'] else None
            })

        results_by_source[source] = documents
        print(f"  {source:20s}: {len(documents):5d} documents")

    return results_by_source


def get_extraction_statistics(conn) -> Dict[str, Any]:
    """Get statistics about current extractions in the database."""
    cursor = conn.cursor()

    # Total documents vs documents with extractions
    query = """
    SELECT
        m.source_sensor,
        COUNT(DISTINCT m.rid) as total_documents,
        COUNT(DISTINCT CASE WHEN e.id IS NOT NULL THEN m.rid END) as with_extraction,
        COUNT(DISTINCT CASE WHEN e.id IS NULL THEN m.rid END) as without_extraction
    FROM koi_memories m
    LEFT JOIN koi_kg_extractions e ON m.rid = e.memory_rid
    WHERE m.content->>'text' IS NOT NULL
    GROUP BY m.source_sensor
    ORDER BY m.source_sensor
    """

    cursor.execute(query)
    rows = cursor.fetchall()

    stats = {}
    for row in rows:
        stats[row['source_sensor']] = {
            'total': row['total_documents'],
            'with_extraction': row['with_extraction'],
            'without_extraction': row['without_extraction']
        }

    return stats


def save_results(results: Dict[str, Any], output_path: str):
    """Save results to JSON file."""
    # Calculate totals
    total_count = sum(len(docs) for docs in results['documents_by_source'].values())

    output = {
        'generated_at': datetime.utcnow().isoformat(),
        'summary': {
            'total_documents_needing_extraction': total_count,
            'by_source': {
                source: len(docs)
                for source, docs in results['documents_by_source'].items()
            }
        },
        'documents_by_source': results['documents_by_source'],
        'statistics': results.get('statistics', {})
    }

    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\nSaved to: {output_path}")


def main():
    """Main execution."""
    parser = argparse.ArgumentParser(
        description="Query documents needing fresh extraction"
    )
    parser.add_argument(
        '--output', '-o', type=str, default=None,
        help='Output JSON file path (default: fresh_extraction_documents.json)'
    )
    parser.add_argument(
        '--source', '-s', type=str, default=None,
        help='Filter to specific source (discourse, youtube, gitlab, github-activity, github-markdown)'
    )
    parser.add_argument(
        '--host', type=str, default='localhost',
        help='Database host (default: localhost)'
    )
    parser.add_argument(
        '--port', type=int, default=5433,
        help='Database port (default: 5433)'
    )
    parser.add_argument(
        '--stats-only', action='store_true',
        help='Only show statistics, do not output full document list'
    )

    args = parser.parse_args()

    # Set default output path
    script_dir = Path(__file__).parent
    output_path = Path(args.output) if args.output else script_dir / 'fresh_extraction_documents.json'

    try:
        # Connect to database
        print(f"Connecting to database at {args.host}:{args.port}...")
        conn = connect_db(host=args.host, port=args.port)
        print("Connected successfully")
        print()

        print("=" * 70)
        print("FRESH EXTRACTION DOCUMENT QUERY")
        print("=" * 70)
        print()

        # Get statistics first
        print("Gathering extraction statistics...")
        stats = get_extraction_statistics(conn)
        print()

        # Show summary by sensor
        print("-" * 70)
        print("EXTRACTION STATUS BY SOURCE SENSOR")
        print("-" * 70)
        print()
        print(f"{'Source Sensor':<40} {'Total':>8} {'With':>8} {'Without':>8}")
        print("-" * 70)

        total_with = 0
        total_without = 0
        for sensor, data in sorted(stats.items()):
            total_with += data['with_extraction']
            total_without += data['without_extraction']
            print(f"{sensor:<40} {data['total']:>8} {data['with_extraction']:>8} {data['without_extraction']:>8}")

        print("-" * 70)
        print(f"{'TOTAL':<40} {total_with + total_without:>8} {total_with:>8} {total_without:>8}")
        print()

        if args.stats_only:
            conn.close()
            return 0

        # Query documents needing extraction
        print("-" * 70)
        print("DOCUMENTS NEEDING FRESH EXTRACTION")
        print("-" * 70)
        print()

        documents_by_source = get_documents_without_extraction(conn, args.source)
        conn.close()

        # Calculate total
        total_count = sum(len(docs) for docs in documents_by_source.values())

        print()
        print(f"Total documents needing fresh extraction: {total_count}")

        # Save results
        results = {
            'documents_by_source': documents_by_source,
            'statistics': stats
        }
        save_results(results, str(output_path))

        # Show sample documents
        print()
        print("-" * 70)
        print("SAMPLE DOCUMENTS (first 5 per source)")
        print("-" * 70)

        for source, docs in sorted(documents_by_source.items()):
            if docs:
                print(f"\n{source} ({len(docs)} documents):")
                for doc in docs[:5]:
                    title = doc.get('title', 'No title')
                    if title and len(title) > 50:
                        title = title[:47] + "..."
                    print(f"  - {title}")
                if len(docs) > 5:
                    print(f"  ... and {len(docs) - 5} more")

        print()
        print("=" * 70)
        print("QUERY COMPLETE")
        print("=" * 70)
        print()
        print("Summary by source:")
        for source, docs in sorted(documents_by_source.items()):
            print(f"  {source:20s}: {len(docs):5d} documents")
        print(f"  {'TOTAL':<20s}: {total_count:5d} documents")
        print()
        print("Next steps:")
        print(f"  1. Review {output_path}")
        print("  2. Run fresh extraction using extract_fresh_documents.py")

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
