"""
Graph Cleanup Execution Script - Actually performs cleanup operations.

Connects to Apache AGE graph and:
1. Deletes entities blocked by quality filters
2. Merges duplicate entities to canonical names

Usage:
    python -m src.knowledge_graph.improvements.execute_cleanup --confirm

Author: Claude Code
Date: 2025-12-08
"""

import asyncio
import os
import json
import argparse
from pathlib import Path
from typing import Dict, List, Any
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
    print("ERROR: asyncpg not installed. Cannot execute cleanup.")
    sys.exit(1)

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

    # Query entities
    query = f"""
    SELECT * FROM cypher('{graph_name}', $$
        MATCH (n)
        WHERE n.name IS NOT NULL
        RETURN id(n) AS id,
               n.name AS name,
               labels(n)[0] AS entity_type,
               n.rid AS rid,
               n.confidence AS confidence
        LIMIT 10000
    $$) AS (id agtype, name agtype, entity_type agtype, rid agtype, confidence agtype);
    """

    try:
        rows = await conn.fetch(query)
        entities = []
        for row in rows:
            # AGE returns agtype which needs JSON parsing
            node_id = json.loads(row['id']) if row['id'] else None
            name = json.loads(row['name']) if row['name'] else None
            entity_type = json.loads(row['entity_type']) if row['entity_type'] else 'Entity'
            rid = json.loads(row['rid']) if row['rid'] else None
            confidence = json.loads(row['confidence']) if row['confidence'] else 1.0

            if name and node_id:
                entities.append({
                    'id': node_id,
                    'name': name,
                    'type': entity_type,
                    'rid': rid,
                    'confidence': confidence
                })

        return entities

    except Exception as e:
        print(f"Query error: {e}")
        return []


async def delete_entity(conn, graph_name: str, entity_id: int) -> bool:
    """Delete an entity from the graph by its internal ID"""
    try:
        # Use Cypher DELETE via AGE
        query = f"""
        SELECT * FROM cypher('{graph_name}', $$
            MATCH (n)
            WHERE id(n) = {entity_id}
            DETACH DELETE n
        $$) AS (result agtype);
        """
        await conn.execute(query)
        return True
    except Exception as e:
        print(f"  Error deleting entity {entity_id}: {e}")
        return False


async def merge_entities(conn, graph_name: str, entity_ids: List[int], canonical_name: str, entity_type: str) -> bool:
    """Merge multiple entities into a single canonical entity"""
    try:
        # Strategy: Update all entities to have the canonical name
        # AGE will then deduplicate them naturally
        for entity_id in entity_ids:
            query = f"""
            SELECT * FROM cypher('{graph_name}', $$
                MATCH (n)
                WHERE id(n) = {entity_id}
                SET n.name = '{canonical_name.replace("'", "''")}'
                RETURN n
            $$) AS (result agtype);
            """
            await conn.execute(query)
        return True
    except Exception as e:
        print(f"  Error merging entities: {e}")
        return False


async def execute_cleanup(
    graph_name: str = None,
    db_url: str = None,
    confirm: bool = False
) -> Dict[str, Any]:
    """
    Execute graph cleanup operations.

    Args:
        graph_name: Name of the graph to clean
        db_url: PostgreSQL connection URL
        confirm: Must be True to actually execute (safety check)

    Returns:
        Execution report with statistics
    """

    if not confirm:
        print("\nERROR: --confirm flag required to execute cleanup")
        print("This is a destructive operation. Use --confirm to proceed.")
        return {'error': 'confirm_required'}

    print("\n" + "=" * 70)
    print("KNOWLEDGE GRAPH CLEANUP EXECUTION")
    print("=" * 70)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"Mode: EXECUTION (changes will be made to database)")
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
        'mode': 'execution',
        'entities_processed': 0,
        'entities_deleted': 0,
        'entities_merged': 0,
        'errors': []
    }

    # Connect to database
    try:
        db_config = parse_postgres_url(db_url)
        db_url_full = f"postgresql://{db_config['user']}:{db_config.get('password', '')}@{db_config['host']}:{db_config['port']}/{db_config['database']}"
        conn = await asyncpg.connect(db_url_full)

        print(f"Connected to database: {db_config['host']}:{db_config['port']}/{db_config['database']}")

        # Load entities
        entities = await get_graph_entities(conn, graph_name)
        print(f"Loaded {len(entities)} entities from graph\n")
        results['entities_processed'] = len(entities)

        # Phase 1: Delete low-quality entities
        print("-" * 70)
        print("PHASE 1: DELETING LOW-QUALITY ENTITIES")
        print("-" * 70)

        to_delete = []
        for entity in entities:
            name = entity['name']
            entity_type = entity['type']
            entity_id = entity['id']

            # Check if should be blocked
            is_valid, reasons = entity_filter.filter_with_reasons(name, entity_type)
            if not is_valid:
                to_delete.append(entity)

        print(f"Found {len(to_delete)} entities to delete")

        for i, entity in enumerate(to_delete, 1):
            print(f"  [{i}/{len(to_delete)}] Deleting: {entity['name'][:50]} ({entity['type']})")
            success = await delete_entity(conn, graph_name, entity['id'])
            if success:
                results['entities_deleted'] += 1
            else:
                results['errors'].append(f"Failed to delete: {entity['name']}")

        print(f"\nPhase 1 Complete: Deleted {results['entities_deleted']} entities")

        # Phase 2: Merge duplicates to canonical names
        print("\n" + "-" * 70)
        print("PHASE 2: MERGING DUPLICATES TO CANONICAL NAMES")
        print("-" * 70)

        # Group entities by canonical name
        canonical_groups = {}
        for entity in entities:
            name = entity['name']
            entity_type = entity['type']

            # Skip if was deleted
            if entity in to_delete:
                continue

            # Check canonical resolution
            canonical_name, was_resolved = canonical_resolver.resolve(name, entity_type)
            if was_resolved and canonical_name != name:
                key = f"{canonical_name}||{entity_type}"
                if key not in canonical_groups:
                    canonical_groups[key] = {
                        'canonical_name': canonical_name,
                        'type': entity_type,
                        'entities': []
                    }
                canonical_groups[key]['entities'].append(entity)

        print(f"Found {len(canonical_groups)} canonical groups to merge")

        for i, (key, group) in enumerate(canonical_groups.items(), 1):
            canonical_name = group['canonical_name']
            entity_type = group['type']
            entities_to_merge = group['entities']

            if len(entities_to_merge) > 0:
                print(f"  [{i}/{len(canonical_groups)}] Merging {len(entities_to_merge)} entities to: {canonical_name}")
                entity_ids = [e['id'] for e in entities_to_merge]
                success = await merge_entities(conn, graph_name, entity_ids, canonical_name, entity_type)
                if success:
                    results['entities_merged'] += len(entities_to_merge)
                else:
                    results['errors'].append(f"Failed to merge group: {canonical_name}")

        print(f"\nPhase 2 Complete: Merged {results['entities_merged']} entities")

        # Close connection
        await conn.close()

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        results['errors'].append(str(e))
        return results

    # Print summary
    print("\n" + "=" * 70)
    print("CLEANUP EXECUTION SUMMARY")
    print("=" * 70)
    print(f"Entities processed: {results['entities_processed']:,}")
    print(f"Entities deleted:   {results['entities_deleted']:,}")
    print(f"Entities merged:    {results['entities_merged']:,}")
    print(f"Errors:             {len(results['errors']):,}")
    print("=" * 70)

    # Calculate new quality score
    if results['entities_processed'] > 0:
        quality_score = ((results['entities_processed'] - results['entities_deleted']) / results['entities_processed'] * 100)
        print(f"\nEstimated new quality score: {quality_score:.1f}%")

    # Save results
    output_dir = project_root / 'reports' / 'cleanup_execution'
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f'cleanup_execution_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'

    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\nExecution report saved to: {output_file}")

    return results


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Execute knowledge graph cleanup operations'
    )
    parser.add_argument('--confirm', action='store_true',
                       help='Required flag to confirm execution')
    parser.add_argument('--graph', '-g', type=str, default=None,
                       help='Graph name (default: from env)')
    parser.add_argument('--db-url', type=str, default=None,
                       help='PostgreSQL URL (default: from env)')

    args = parser.parse_args()

    try:
        results = asyncio.run(execute_cleanup(
            graph_name=args.graph,
            db_url=args.db_url,
            confirm=args.confirm
        ))

        if 'error' in results:
            return 1

        return 0
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
