"""
Graph Cleanup Script - Dry-run analysis of entity quality in Apache AGE graph.

Scans all entities in the knowledge graph and reports what would be:
- Blocked by quality filters
- Resolved to canonical names
- Flagged for duplicate merging

Usage:
    # Dry-run mode (default - no changes)
    python -m src.knowledge_graph.improvements.cleanup_graph

    # Verbose mode
    python -m src.knowledge_graph.improvements.cleanup_graph --verbose

Author: Claude Code
Date: 2025-12-08
"""

import asyncio
import os
import json
import argparse
from pathlib import Path
from typing import Dict, List, Any, Tuple
from collections import defaultdict
from datetime import datetime
import sys

# Add project root to path
project_root = Path(__file__).parents[3]
sys.path.insert(0, str(project_root))

try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False
    print("Warning: asyncpg not installed. Database connectivity disabled.")

from src.knowledge_graph.improvements import EntityQualityFilter, FilterConfig, CanonicalResolver


def parse_postgres_url(url: str) -> Dict[str, str]:
    """Parse PostgreSQL URL into connection parameters"""
    import re
    match = re.match(r'postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)', url)
    if match:
        user, password, host, port, database = match.groups()
        return {
            'user': user,
            'password': password,
            'host': host,
            'port': int(port),
            'database': database
        }
    return {
        'user': 'postgres',
        'password': 'postgres',
        'host': 'localhost',
        'port': 5433,
        'database': 'eliza'
    }


async def get_graph_entities(conn, graph_name: str) -> List[Dict]:
    """Query all entities from Apache AGE graph"""

    # Load AGE extension
    await conn.execute("LOAD 'age';")
    await conn.execute("SET search_path = ag_catalog, '$user', public;")

    # Query entities - AGE uses cypher queries via ag_catalog
    query = f"""
    SELECT * FROM cypher('{graph_name}', $$
        MATCH (n)
        WHERE n.name IS NOT NULL
        RETURN n.name AS name,
               labels(n)[0] AS entity_type,
               n.rid AS rid,
               n.confidence AS confidence
        LIMIT 10000
    $$) AS (name agtype, entity_type agtype, rid agtype, confidence agtype);
    """

    try:
        rows = await conn.fetch(query)
        entities = []
        for row in rows:
            # AGE returns agtype which needs JSON parsing
            name = json.loads(row['name']) if row['name'] else None
            entity_type = json.loads(row['entity_type']) if row['entity_type'] else 'Entity'
            rid = json.loads(row['rid']) if row['rid'] else None
            confidence = json.loads(row['confidence']) if row['confidence'] else 1.0

            if name:
                entities.append({
                    'name': name,
                    'type': entity_type,
                    'rid': rid,
                    'confidence': confidence
                })

        return entities

    except Exception as e:
        print(f"Query error: {e}")
        return []


async def analyze_graph_quality(
    graph_name: str = None,
    db_url: str = None,
    verbose: bool = False
) -> Dict[str, Any]:
    """
    Analyze graph entity quality without making changes.

    Returns:
        Analysis report with quality metrics and recommendations
    """

    print("\n" + "=" * 70)
    print("KNOWLEDGE GRAPH QUALITY ANALYSIS (DRY-RUN)")
    print("=" * 70)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"Mode: DRY-RUN (no changes will be made)")
    print()

    # Initialize quality controls
    entity_filter = EntityQualityFilter(FilterConfig())
    canonical_resolver = CanonicalResolver()

    # Load environment if not provided
    if db_url is None:
        from dotenv import load_dotenv
        load_dotenv(project_root / '.env')
        db_url = os.getenv('POSTGRES_URL', 'postgresql://postgres:postgres@localhost:5433/eliza')

    if graph_name is None:
        graph_name = os.getenv('GRAPH_NAME', 'regen_graph')

    print(f"Graph: {graph_name}")
    print("-" * 70)

    # Results tracking
    results = {
        'timestamp': datetime.now().isoformat(),
        'graph_name': graph_name,
        'mode': 'dry_run',
        'entities_analyzed': 0,
        'would_block': [],
        'would_canonicalize': [],
        'duplicates_found': defaultdict(list),
        'by_block_reason': defaultdict(int),
        'quality_score': 0
    }

    # Try database connection if available
    entities = []
    if HAS_ASYNCPG:
        try:
            db_config = parse_postgres_url(db_url)
            db_url_full = f"postgresql://{db_config['user']}:{db_config.get('password', '')}@{db_config['host']}:{db_config['port']}/{db_config['database']}"
            conn = await asyncpg.connect(db_url_full)

            print(f"Connected to database: {db_config['host']}:{db_config['port']}/{db_config['database']}")
            entities = await get_graph_entities(conn, graph_name)
            await conn.close()

            print(f"Loaded {len(entities)} entities from graph")

        except Exception as e:
            print(f"Database connection failed: {e}")
            print("Using sample entities from quality review instead...")

    # If no database or connection failed, use sample from quality review
    if not entities:
        print("\nUsing sample entities from quality review reports...")
        review_file = project_root / 'reports' / 'kg_quality_review_20251208' / 'entity_quality_issues.csv'
        if review_file.exists():
            import csv
            with open(review_file) as f:
                reader = csv.DictReader(f)
                entities = [
                    {
                        'name': row['entity_name'],
                        'type': row['entity_type'],
                        'rid': row['entity_rid'],
                        'confidence': float(row.get('confidence', 1.0))
                    }
                    for row in reader
                ]
            print(f"Loaded {len(entities)} entities from quality review")
        else:
            print("No quality review file found. Using minimal sample.")
            entities = [
                {'name': 'we', 'type': 'PERSON'},
                {'name': 'they', 'type': 'PERSON'},
                {'name': 'Regen Network', 'type': 'ORGANIZATION'},
                {'name': 'regen.network', 'type': 'ORGANIZATION'},
                {'name': 'Gregory Landua', 'type': 'PERSON'},
                {'name': 'localhost:9090', 'type': 'PROJECT'},
            ]

    results['entities_analyzed'] = len(entities)

    print()
    print("-" * 70)
    print("ANALYZING ENTITIES...")
    print("-" * 70)

    # Analyze each entity
    canonical_names_seen = defaultdict(list)

    for entity in entities:
        name = entity['name']
        entity_type = entity.get('type', 'Entity')

        # Check quality filter
        is_valid, reasons = entity_filter.filter_with_reasons(name, entity_type)
        if not is_valid:
            results['would_block'].append({
                'name': name,
                'type': entity_type,
                'reasons': reasons
            })
            for reason in reasons:
                results['by_block_reason'][reason] += 1

        # Check canonical resolution
        canonical_name, was_resolved = canonical_resolver.resolve(name, entity_type)
        if was_resolved and canonical_name != name:
            results['would_canonicalize'].append({
                'original': name,
                'canonical': canonical_name,
                'type': entity_type
            })

        # Track potential duplicates (same canonical name)
        if was_resolved:
            canonical_names_seen[canonical_name.lower()].append(name)

    # Find duplicates (same canonical name, different original names)
    for canonical, originals in canonical_names_seen.items():
        unique_originals = list(set(originals))
        if len(unique_originals) > 1:
            results['duplicates_found'][canonical] = unique_originals

    # Calculate quality score (higher = better)
    total = results['entities_analyzed']
    blocked = len(results['would_block'])
    quality_score = ((total - blocked) / total * 100) if total > 0 else 100
    results['quality_score'] = round(quality_score, 2)

    # Print results
    print()
    print("=" * 70)
    print("ANALYSIS RESULTS")
    print("=" * 70)

    print(f"\n1. ENTITIES ANALYZED: {results['entities_analyzed']:,}")

    print(f"\n2. WOULD BLOCK: {len(results['would_block']):,} entities ({100 - quality_score:.1f}%)")
    if verbose and results['would_block']:
        print("   Sample blocked entities:")
        for item in results['would_block'][:10]:
            print(f"     - {item['name'][:40]:<40s} ({item['type']}) -> {', '.join(item['reasons'])}")
        if len(results['would_block']) > 10:
            print(f"     ... and {len(results['would_block']) - 10} more")

    print(f"\n3. WOULD CANONICALIZE: {len(results['would_canonicalize']):,} entities")
    if verbose and results['would_canonicalize']:
        print("   Sample canonicalizations:")
        for item in results['would_canonicalize'][:10]:
            print(f"     - {item['original'][:30]:<30s} -> {item['canonical']}")
        if len(results['would_canonicalize']) > 10:
            print(f"     ... and {len(results['would_canonicalize']) - 10} more")

    print(f"\n4. DUPLICATE CLUSTERS: {len(results['duplicates_found']):,}")
    if verbose and results['duplicates_found']:
        print("   Sample duplicate clusters:")
        for canonical, originals in list(results['duplicates_found'].items())[:5]:
            print(f"     - {canonical}: {originals}")

    print(f"\n5. BLOCK REASONS BREAKDOWN:")
    for reason, count in sorted(results['by_block_reason'].items(), key=lambda x: -x[1]):
        print(f"     - {reason:<20s}: {count:,}")

    print()
    print("=" * 70)
    print(f"QUALITY SCORE: {quality_score:.1f}%")
    print("=" * 70)

    # Save detailed results
    output_dir = project_root / 'reports' / 'cleanup_analysis'
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f'cleanup_dryrun_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'

    # Convert defaultdicts to regular dicts for JSON serialization
    results['duplicates_found'] = dict(results['duplicates_found'])
    results['by_block_reason'] = dict(results['by_block_reason'])

    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\nDetailed results saved to: {output_file}")

    return results


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Analyze knowledge graph entity quality (dry-run)'
    )
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Show detailed entity lists')
    parser.add_argument('--graph', '-g', type=str, default=None,
                       help='Graph name (default: from env)')
    parser.add_argument('--db-url', type=str, default=None,
                       help='PostgreSQL URL (default: from env)')

    args = parser.parse_args()

    try:
        results = asyncio.run(analyze_graph_quality(
            graph_name=args.graph,
            db_url=args.db_url,
            verbose=args.verbose
        ))
        return 0
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
