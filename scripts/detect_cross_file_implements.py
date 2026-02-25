#!/usr/bin/env python3
"""Cross-File IMPLEMENTS Edge Detection for Go - Optimized with batch inserts"""

import asyncio
import asyncpg
import argparse
import re
from typing import Dict, List, Set, Tuple
from loguru import logger
from collections import defaultdict

DB_CONFIG = {
    "host": "localhost",
    "port": 5433,
    "database": "eliza",
    "user": "postgres",
    "password": "postgres",
}

STAGING_GRAPH = "regen_graph_v2"


def parse_interface_methods(signature: str) -> Set[str]:
    """Extract method names from a Go interface signature."""
    sig = signature.replace('\\n', '\n').replace('\\t', '\t')
    methods = set()
    pattern = r'^\s*([A-Z][a-zA-Z0-9_]*)\s*\('
    
    for line in sig.split('\n'):
        line = line.strip()
        if not line or line.startswith('//') or line.startswith('type '):
            continue
        if re.match(r'^[a-zA-Z_.]+$', line.rstrip(',')):
            continue
        match = re.match(pattern, line)
        if match:
            methods.add(match.group(1))
    
    return methods


async def get_interfaces(conn: asyncpg.Connection, repo: str = None) -> Dict[str, Tuple[str, Set[str], str]]:
    query = f"""
        SELECT * FROM cypher('{STAGING_GRAPH}', $$
            MATCH (i:Interface)
            WHERE i.language = 'go'
            RETURN i.entity_id, i.name, i.signature, i.file_path, i.repo
        $$) as (entity_id agtype, name agtype, signature agtype, file_path agtype, repo agtype);
    """
    rows = await conn.fetch(query)
    interfaces = {}
    
    for row in rows:
        entity_id = str(row['entity_id']).strip('"')
        name = str(row['name']).strip('"')
        signature = str(row['signature']).strip('"')
        file_path = str(row['file_path']).strip('"')
        row_repo = str(row['repo']).strip('"')
        
        if repo and row_repo != repo:
            continue
        methods = parse_interface_methods(signature)
        if methods:
            interfaces[entity_id] = (name, methods, file_path)
    
    return interfaces


async def get_type_methods(conn: asyncpg.Connection, repo: str = None) -> Dict[str, Set[str]]:
    query = f"""
        SELECT * FROM cypher('{STAGING_GRAPH}', $$
            MATCH (m:Method)
            WHERE m.language = 'go' AND m.receiver_type IS NOT NULL
            RETURN m.receiver_type, m.name, m.repo
        $$) as (receiver_type agtype, name agtype, repo agtype);
    """
    rows = await conn.fetch(query)
    type_methods = defaultdict(set)
    
    for row in rows:
        row_repo = str(row['repo']).strip('"')
        if repo and row_repo != repo:
            continue
        receiver = str(row['receiver_type']).strip('"').lstrip('*')
        method_name = str(row['name']).strip('"')
        type_methods[receiver].add(method_name)
    
    return dict(type_methods)


async def get_type_entities(conn: asyncpg.Connection, repo: str = None) -> Dict[str, str]:
    type_entities = {}
    for label in ['Struct', 'Keeper', 'Handler', 'Message']:
        try:
            query = f"""
                SELECT * FROM cypher('{STAGING_GRAPH}', $$
                    MATCH (s:{label})
                    WHERE s.language = 'go'
                    RETURN s.name, s.entity_id, s.repo
                $$) as (name agtype, entity_id agtype, repo agtype);
            """
            rows = await conn.fetch(query)
            for row in rows:
                row_repo = str(row['repo']).strip('"')
                if repo and row_repo != repo:
                    continue
                name = str(row['name']).strip('"')
                entity_id = str(row['entity_id']).strip('"')
                type_entities[name] = entity_id
        except:
            pass
    return type_entities


async def get_existing_implements(conn: asyncpg.Connection) -> Set[Tuple[str, str]]:
    query = f"""
        SELECT * FROM cypher('{STAGING_GRAPH}', $$
            MATCH (c)-[e:IMPLEMENTS]->(i:Interface)
            WHERE c.language = 'go'
            RETURN c.entity_id, i.entity_id
        $$) as (from_id agtype, to_id agtype);
    """
    rows = await conn.fetch(query)
    existing = set()
    for row in rows:
        from_id = str(row['from_id']).strip('"')
        to_id = str(row['to_id']).strip('"')
        existing.add((from_id, to_id))
    return existing


async def get_entity_graph_ids(conn: asyncpg.Connection, entity_ids: Set[str]) -> Dict[str, int]:
    """Get graph IDs for entity IDs."""
    query = f"""
        SELECT * FROM cypher('{STAGING_GRAPH}', $$
            MATCH (n)
            WHERE n.entity_id IS NOT NULL
            RETURN n.entity_id, id(n)
        $$) as (entity_id agtype, graph_id agtype);
    """
    rows = await conn.fetch(query)
    id_map = {}
    for row in rows:
        eid = str(row['entity_id']).strip('"')
        if eid in entity_ids:
            id_map[eid] = int(str(row['graph_id']))
    return id_map


async def create_implements_edges_batch(
    conn: asyncpg.Connection,
    edges: List[Tuple[str, str, str, str]],
    dry_run: bool = False
) -> int:
    """Create IMPLEMENTS edges using batch SQL inserts."""
    if not edges:
        return 0
    
    if dry_run:
        logger.info(f"DRY RUN: Would create {len(edges)} IMPLEMENTS edges")
        for from_id, to_id, type_name, iface_name in edges[:20]:
            logger.info(f"  {type_name} -> {iface_name}")
        if len(edges) > 20:
            logger.info(f"  ... and {len(edges) - 20} more")
        return 0
    
    # Get all entity IDs we need
    all_entity_ids = set()
    for from_id, to_id, _, _ in edges:
        all_entity_ids.add(from_id)
        all_entity_ids.add(to_id)
    
    logger.info(f"Loading graph IDs for {len(all_entity_ids)} entities...")
    id_map = await get_entity_graph_ids(conn, all_entity_ids)
    logger.info(f"  Found {len(id_map)} graph IDs")
    
    # Filter to valid edges
    valid_edges = []
    for from_id, to_id, type_name, iface_name in edges:
        from_gid = id_map.get(from_id)
        to_gid = id_map.get(to_id)
        if from_gid and to_gid:
            edge_id = f"{from_id[:8]}_{to_id[:8]}_crossfile"
            valid_edges.append((from_gid, to_gid, edge_id))
    
    logger.info(f"Creating {len(valid_edges)} edges (skipped {len(edges) - len(valid_edges)} missing nodes)...")
    
    # Batch insert
    batch_size = 500
    created = 0
    
    for batch_start in range(0, len(valid_edges), batch_size):
        batch = valid_edges[batch_start:batch_start + batch_size]
        
        values = []
        for from_gid, to_gid, edge_id in batch:
            props = f'{{"edge_id": "{edge_id}", "cross_file": true}}'
            values.append(f"(graphid_in('{from_gid}'), graphid_in('{to_gid}'), '{props}'::agtype)")
        
        try:
            insert_sql = f"""
                INSERT INTO {STAGING_GRAPH}."IMPLEMENTS" (start_id, end_id, properties)
                VALUES {', '.join(values)}
                ON CONFLICT DO NOTHING
            """
            await conn.execute(insert_sql)
            created += len(batch)
            logger.info(f"  Inserted batch {batch_start//batch_size + 1}: {len(batch)} edges")
        except Exception as e:
            logger.error(f"Batch insert failed: {e}")
            # Fall back to individual inserts
            for from_gid, to_gid, edge_id in batch:
                try:
                    query = f"""
                        SELECT * FROM cypher('{STAGING_GRAPH}', $$
                            MATCH (c) WHERE id(c) = {from_gid}
                            MATCH (i) WHERE id(i) = {to_gid}
                            CREATE (c)-[:IMPLEMENTS {{edge_id: '{edge_id}', cross_file: true}}]->(i)
                        $$) as (r agtype);
                    """
                    await conn.execute(query)
                    created += 1
                except:
                    pass
    
    return created


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", help="Repository to process")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    
    db_url = f"postgresql://{DB_CONFIG['user']}:{DB_CONFIG['password']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"
    
    logger.info("="*60)
    logger.info("CROSS-FILE IMPLEMENTS DETECTION")
    logger.info("="*60)
    
    conn = await asyncpg.connect(db_url)
    await conn.execute("SET search_path = ag_catalog, public;")
    await conn.execute("LOAD 'age';")
    
    try:
        logger.info(f"Loading interfaces{f' for {args.repo}' if args.repo else ''}...")
        interfaces = await get_interfaces(conn, args.repo)
        logger.info(f"  Found {len(interfaces)} interfaces with methods")
        
        logger.info("Loading type methods...")
        type_methods = await get_type_methods(conn, args.repo)
        logger.info(f"  Found {len(type_methods)} types with methods")
        
        logger.info("Loading type entities...")
        type_entities = await get_type_entities(conn, args.repo)
        logger.info(f"  Found {len(type_entities)} type entities")
        
        logger.info("Loading existing IMPLEMENTS edges...")
        existing = await get_existing_implements(conn)
        logger.info(f"  Found {len(existing)} existing edges")
        
        logger.info("\nDetecting cross-file IMPLEMENTS...")
        new_edges = []
        
        for iface_id, (iface_name, iface_methods, iface_file) in interfaces.items():
            if len(iface_methods) < 1:
                continue
            for type_name, methods in type_methods.items():
                if iface_methods.issubset(methods):
                    type_id = type_entities.get(type_name)
                    if type_id and (type_id, iface_id) not in existing:
                        new_edges.append((type_id, iface_id, type_name, iface_name))
        
        logger.info(f"Found {len(new_edges)} new cross-file IMPLEMENTS relationships")
        
        if new_edges:
            created = await create_implements_edges_batch(conn, new_edges, args.dry_run)
            if not args.dry_run:
                logger.info(f"\nCreated {created} new IMPLEMENTS edges")
        
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
