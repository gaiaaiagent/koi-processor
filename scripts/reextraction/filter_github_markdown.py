#!/usr/bin/env python3
"""
Filter GitHub memories to markdown files only.

This script identifies GitHub sensor documents that are markdown/documentation
files (not code) and determines which ones need entity extraction.

Usage:
    python filter_github_markdown.py
    python filter_github_markdown.py --output github_markdown_files.json
    python filter_github_markdown.py --host localhost --port 5433
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any

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


def get_github_markdown_documents(conn) -> Dict[str, Any]:
    """
    Get GitHub markdown documents and their extraction status.

    Returns dict with:
    - total: total markdown files found
    - has_extraction: list of RIDs with extractions
    - needs_extraction: list of RIDs needing extraction
    - documents: full document details
    """
    cursor = conn.cursor()

    # Markdown file extensions to include
    markdown_patterns = [
        "%.md#%",
        "%.mdx#%",
        "%README#%",
        "%.rst#%",
        "%.txt#%",
        "%.asciidoc#%",
        "%.adoc#%"
    ]

    # Build WHERE clause
    conditions = " OR ".join([f"m.rid LIKE '{pattern}'" for pattern in markdown_patterns])

    query = f"""
    SELECT
        m.rid,
        m.source_sensor,
        m.content->>'title' as title,
        m.content->>'url' as url,
        LENGTH(m.content->>'text') as content_length,
        m.created_at,
        e.id as extraction_id,
        CASE WHEN e.id IS NOT NULL THEN
            jsonb_array_length(COALESCE(e.entities, '[]'::jsonb))
        ELSE 0 END as entity_count
    FROM koi_memories m
    LEFT JOIN koi_kg_extractions e ON m.rid = e.memory_rid
    WHERE
        m.source_sensor LIKE 'github-sensor%'
        AND ({conditions})
    ORDER BY m.created_at DESC
    """

    cursor.execute(query)
    results = cursor.fetchall()

    # Separate into has_extraction vs needs_extraction
    has_extraction = []
    needs_extraction = []
    documents = []

    for row in results:
        doc = {
            'rid': row['rid'],
            'source_sensor': row['source_sensor'],
            'title': row['title'],
            'url': row['url'],
            'content_length': row['content_length'],
            'created_at': row['created_at'].isoformat() if row['created_at'] else None,
            'has_extraction': row['extraction_id'] is not None,
            'entity_count': row['entity_count'] or 0
        }
        documents.append(doc)

        if row['extraction_id']:
            has_extraction.append(row['rid'])
        else:
            needs_extraction.append(row['rid'])

    # Extract filenames for summary
    def extract_filename(rid: str) -> str:
        """Extract filename from RID like 'github_regen_ledger_docs_README.md#chunk_0'"""
        # Remove chunk suffix
        base = rid.split('#')[0] if '#' in rid else rid
        # Get last part (filename)
        parts = base.split('_')
        return parts[-1] if parts else rid

    # Count by file extension
    extension_counts = {}
    for doc in documents:
        filename = extract_filename(doc['rid'])
        ext = filename.split('.')[-1] if '.' in filename else 'no_extension'
        if not doc['has_extraction']:
            extension_counts[ext] = extension_counts.get(ext, 0) + 1

    return {
        'total': len(results),
        'has_extraction': has_extraction,
        'needs_extraction': needs_extraction,
        'documents': documents,
        'extension_counts': extension_counts
    }


def get_github_activity_documents(conn) -> Dict[str, Any]:
    """
    Get GitHub Activity documents and their extraction status.
    """
    cursor = conn.cursor()

    query = """
    SELECT
        m.rid,
        m.source_sensor,
        m.content->>'title' as title,
        m.content->>'url' as url,
        LENGTH(m.content->>'text') as content_length,
        m.created_at,
        e.id as extraction_id,
        CASE WHEN e.id IS NOT NULL THEN
            jsonb_array_length(COALESCE(e.entities, '[]'::jsonb))
        ELSE 0 END as entity_count
    FROM koi_memories m
    LEFT JOIN koi_kg_extractions e ON m.rid = e.memory_rid
    WHERE m.source_sensor LIKE 'github-activity-sensor%'
    ORDER BY m.created_at DESC
    """

    cursor.execute(query)
    results = cursor.fetchall()

    has_extraction = []
    needs_extraction = []
    documents = []

    for row in results:
        doc = {
            'rid': row['rid'],
            'source_sensor': row['source_sensor'],
            'title': row['title'],
            'url': row['url'],
            'content_length': row['content_length'],
            'created_at': row['created_at'].isoformat() if row['created_at'] else None,
            'has_extraction': row['extraction_id'] is not None,
            'entity_count': row['entity_count'] or 0
        }
        documents.append(doc)

        if row['extraction_id']:
            has_extraction.append(row['rid'])
        else:
            needs_extraction.append(row['rid'])

    return {
        'total': len(results),
        'has_extraction': has_extraction,
        'needs_extraction': needs_extraction,
        'documents': documents
    }


def save_results(results: Dict[str, Any], output_path: str):
    """Save results to JSON file."""
    output = {
        'generated_at': datetime.utcnow().isoformat(),
        **results
    }

    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\nSaved to: {output_path}")


def main():
    """Main execution."""
    parser = argparse.ArgumentParser(
        description="Filter GitHub memories to identify markdown files for extraction"
    )
    parser.add_argument(
        '--output', '-o', type=str, default=None,
        help='Output JSON file path (default: github_markdown_files.json)'
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
    script_dir = Path(__file__).parent
    output_path = Path(args.output) if args.output else script_dir / 'github_markdown_files.json'

    try:
        # Connect to database
        print(f"Connecting to database at {args.host}:{args.port}...")
        conn = connect_db(host=args.host, port=args.port)
        print("Connected successfully")
        print()

        print("=" * 70)
        print("GITHUB CONTENT FILTER")
        print("=" * 70)
        print()

        # Get GitHub markdown files
        print("Querying GitHub sensor for markdown files...")
        markdown_results = get_github_markdown_documents(conn)

        print(f"\nGitHub Markdown Files:")
        print(f"  Total found: {markdown_results['total']}")
        print(f"  Has extractions: {len(markdown_results['has_extraction'])}")
        print(f"  Needs extraction: {len(markdown_results['needs_extraction'])}")

        if markdown_results['extension_counts']:
            print(f"\n  Files needing extraction by extension:")
            for ext, count in sorted(markdown_results['extension_counts'].items(), key=lambda x: -x[1]):
                print(f"    .{ext}: {count}")

        # Get GitHub Activity documents
        print("\nQuerying GitHub Activity sensor...")
        activity_results = get_github_activity_documents(conn)

        print(f"\nGitHub Activity Documents:")
        print(f"  Total found: {activity_results['total']}")
        print(f"  Has extractions: {len(activity_results['has_extraction'])}")
        print(f"  Needs extraction: {len(activity_results['needs_extraction'])}")

        conn.close()

        # Combine results
        combined = {
            'summary': {
                'github_markdown': {
                    'total': markdown_results['total'],
                    'has_extraction': len(markdown_results['has_extraction']),
                    'needs_extraction': len(markdown_results['needs_extraction'])
                },
                'github_activity': {
                    'total': activity_results['total'],
                    'has_extraction': len(activity_results['has_extraction']),
                    'needs_extraction': len(activity_results['needs_extraction'])
                },
                'total_needs_extraction': (
                    len(markdown_results['needs_extraction']) +
                    len(activity_results['needs_extraction'])
                )
            },
            'github_markdown': markdown_results,
            'github_activity': activity_results
        }

        # Save results
        save_results(combined, str(output_path))

        # Sample files needing extraction
        print()
        print("-" * 70)
        print("SAMPLE FILES NEEDING EXTRACTION")
        print("-" * 70)

        print("\nGitHub Markdown (first 10):")
        for rid in markdown_results['needs_extraction'][:10]:
            # Extract readable filename
            filename = rid.split('#')[0].split('_')[-1] if '_' in rid else rid
            print(f"  - {filename}")
        if len(markdown_results['needs_extraction']) > 10:
            print(f"  ... and {len(markdown_results['needs_extraction']) - 10} more")

        print("\nGitHub Activity (first 10):")
        for rid in activity_results['needs_extraction'][:10]:
            print(f"  - {rid[:60]}...")
        if len(activity_results['needs_extraction']) > 10:
            print(f"  ... and {len(activity_results['needs_extraction']) - 10} more")

        print()
        print("=" * 70)
        print("FILTER COMPLETE")
        print("=" * 70)
        print()

        total_needs = combined['summary']['total_needs_extraction']
        print(f"Ready to extract {total_needs} GitHub documents:")
        print(f"  - {len(markdown_results['needs_extraction'])} markdown files")
        print(f"  - {len(activity_results['needs_extraction'])} activity documents")
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
