#!/usr/bin/env python3
"""
Staging Graph Loader - Phase 1 (Optimized with Batch Inserts)
Loads tree-sitter extracted entities and edges to regen_graph_v2 (staging)

IMPORTANT: This writes to staging graph ONLY, never to production (regen_graph)

Usage (on server):
    python scripts/load_to_staging.py --repo regen-ledger --path /opt/projects/regen-repos/regen-ledger

Usage (local with SSH tunnel):
    ssh -f -N -L 5433:localhost:5433 darren@202.61.196.119
    python scripts/load_to_staging.py --repo regen-ledger --path /path/to/repo
"""

import os
import sys
import argparse
import asyncio
from pathlib import Path
from typing import List, Tuple, Dict, Any
from datetime import datetime, timezone
import hashlib
import time

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "core"))

import asyncpg
from loguru import logger

from tree_sitter_extractor import TreeSitterExtractor, CodeEntity, CodeEdge


# Database configuration
DB_CONFIG = {
    "host": os.environ.get("KOI_DB_HOST", "localhost"),
    "port": int(os.environ.get("KOI_DB_PORT", "5433")),
    "database": os.environ.get("KOI_DB_NAME", "eliza"),
    "user": os.environ.get("KOI_DB_USER", "postgres"),
    "password": os.environ.get("KOI_DB_PASSWORD", "postgres"),
}

STAGING_GRAPH = "regen_graph_v2"
BATCH_SIZE = 100  # Entities per batch insert


def escape_cypher(text: str) -> str:
    """Escape special characters for Cypher queries"""
    if not text:
        return ""
    return (
        text
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


async def setup_age(conn: asyncpg.Connection):
    """Load AGE extension and set search path"""
    await conn.execute("LOAD 'age';")
    await conn.execute("SET search_path = ag_catalog, '$user', public;")


async def create_staging_graph(conn: asyncpg.Connection) -> bool:
    """Create staging graph if it doesn't exist"""
    try:
        result = await conn.fetchval("""
            SELECT COUNT(*) FROM ag_catalog.ag_graph WHERE name = $1
        """, STAGING_GRAPH)

        if result > 0:
            logger.info(f"Staging graph '{STAGING_GRAPH}' already exists")
            return True

        await conn.execute(f"SELECT create_graph('{STAGING_GRAPH}');")
        logger.info(f"Created staging graph '{STAGING_GRAPH}'")
        return True

    except Exception as e:
        logger.error(f"Error creating staging graph: {e}")
        return False


async def clear_staging_graph(conn: asyncpg.Connection, repo: str = None):
    """Clear entities from staging graph"""
    try:
        if repo:
            query = f"""
            SELECT * FROM cypher('{STAGING_GRAPH}', $$
                MATCH (n {{repo: '{repo}'}})
                DETACH DELETE n
            $$) as (result agtype);
            """
        else:
            query = f"""
            SELECT * FROM cypher('{STAGING_GRAPH}', $$
                MATCH (n)
                DETACH DELETE n
            $$) as (result agtype);
            """

        await conn.execute(query)
        logger.info(f"Cleared staging graph{' for repo ' + repo if repo else ''}")

    except Exception as e:
        logger.error(f"Error clearing staging graph: {e}")


async def load_entities_batch(
    conn: asyncpg.Connection,
    entities: List[CodeEntity],
    extraction_run_id: str
) -> Tuple[int, int]:
    """Load entities using batch inserts - much faster than one-by-one"""
    success = 0
    failed = 0

    # Process in batches
    for batch_start in range(0, len(entities), BATCH_SIZE):
        batch = entities[batch_start:batch_start + BATCH_SIZE]

        try:
            # Build batch CREATE statements
            create_statements = []
            for entity in batch:
                label = entity.entity_type
                props = {
                    "entity_id": entity.entity_id,
                    "name": escape_cypher(entity.name),
                    "entity_type": entity.entity_type,
                    "file_path": escape_cypher(entity.file_path),
                    "line_start": entity.line_start,
                    "line_end": entity.line_end,
                    "language": entity.language,
                    "repo": entity.repo,
                    "signature": escape_cypher(entity.signature[:500] if entity.signature else ""),
                    "params": escape_cypher(entity.params[:200] if entity.params else ""),
                    "return_type": escape_cypher(entity.return_type[:100] if entity.return_type else ""),
                    "docstring": escape_cypher(entity.docstring[:500] if entity.docstring else ""),
                    "receiver_type": escape_cypher(entity.receiver_type or ""),
                    "extraction_method": entity.extraction_method,
                    "extraction_run_id": extraction_run_id,
                }

                stmt = f"""CREATE (:{label} {{
                    entity_id: '{props['entity_id']}',
                    name: '{props['name']}',
                    entity_type: '{props['entity_type']}',
                    file_path: '{props['file_path']}',
                    line_start: {props['line_start']},
                    line_end: {props['line_end']},
                    language: '{props['language']}',
                    repo: '{props['repo']}',
                    signature: '{props['signature']}',
                    params: '{props['params']}',
                    return_type: '{props['return_type']}',
                    docstring: '{props['docstring']}',
                    receiver_type: '{props['receiver_type']}',
                    extraction_method: '{props['extraction_method']}',
                    extraction_run_id: '{props['extraction_run_id']}'
                }})"""
                create_statements.append(stmt)

            # Execute batch as single query
            batch_query = f"""
            SELECT * FROM cypher('{STAGING_GRAPH}', $$
                {' '.join(create_statements)}
            $$) as (result agtype);
            """

            await conn.execute(batch_query)
            success += len(batch)

        except Exception as e:
            logger.error(f"Batch insert failed at {batch_start}: {e}")
            # Fall back to individual inserts for this batch
            for entity in batch:
                try:
                    await load_entity_single(conn, entity, extraction_run_id)
                    success += 1
                except:
                    failed += 1

        if (batch_start + BATCH_SIZE) % 1000 == 0 or batch_start + BATCH_SIZE >= len(entities):
            logger.info(f"  Loaded {min(batch_start + BATCH_SIZE, len(entities))}/{len(entities)} entities...")

    return success, failed


async def load_entity_single(
    conn: asyncpg.Connection,
    entity: CodeEntity,
    extraction_run_id: str
) -> bool:
    """Load a single entity (fallback for failed batches)"""
    label = entity.entity_type
    props = {
        "entity_id": entity.entity_id,
        "name": escape_cypher(entity.name),
        "entity_type": entity.entity_type,
        "file_path": escape_cypher(entity.file_path),
        "line_start": entity.line_start,
        "line_end": entity.line_end,
        "language": entity.language,
        "repo": entity.repo,
        "signature": escape_cypher(entity.signature[:500] if entity.signature else ""),
        "extraction_run_id": extraction_run_id,
    }

    query = f"""
    SELECT * FROM cypher('{STAGING_GRAPH}', $$
        CREATE (:{label} {{
            entity_id: '{props['entity_id']}',
            name: '{props['name']}',
            repo: '{props['repo']}',
            file_path: '{props['file_path']}',
            line_start: {props['line_start']}
        }})
    $$) as (result agtype);
    """

    await conn.execute(query)
    return True


async def load_entity_id_map(conn: asyncpg.Connection) -> Tuple[Dict[str, int], Dict[str, List[int]]]:
    """Pre-load entity_id -> graph_id and name -> graph_ids mappings for fast edge creation"""
    logger.info("  Pre-loading entity ID mappings...")

    # Get all entities with their graph IDs
    query = f"""
    SELECT * FROM cypher('{STAGING_GRAPH}', $$
        MATCH (n)
        RETURN n.entity_id as entity_id, n.name as name, id(n) as graph_id
    $$) as (entity_id agtype, name agtype, graph_id agtype);
    """

    rows = await conn.fetch(query)

    # entity_id -> graph_id (unique)
    id_map = {}
    # name -> list of graph_ids (can have duplicates)
    name_map = {}

    for row in rows:
        entity_id = str(row['entity_id']).strip('"')
        name = str(row['name']).strip('"')
        graph_id = int(str(row['graph_id']))

        id_map[entity_id] = graph_id
        if name not in name_map:
            name_map[name] = []
        name_map[name].append(graph_id)

    logger.info(f"  Loaded {len(id_map)} entity IDs, {len(name_map)} unique names")
    return id_map, name_map


async def load_edges_batch(
    conn: asyncpg.Connection,
    edges: List[CodeEdge],
    extraction_run_id: str
) -> Tuple[int, int]:
    """Load edges using optimized batch approach with pre-loaded IDs"""
    success = 0
    failed = 0
    skipped = 0

    # Pre-load entity mappings (this is the key optimization!)
    id_map, name_map = await load_entity_id_map(conn)

    # Group edges by type for reporting
    edges_by_type = {}
    for edge in edges:
        edges_by_type.setdefault(edge.edge_type, []).append(edge)

    for edge_type, type_edges in edges_by_type.items():
        logger.info(f"  Loading {len(type_edges)} {edge_type} edges...")

        # Process in larger batches since we're using graph IDs
        edge_batch_size = 50  # Smaller batches for edge creation

        for batch_start in range(0, len(type_edges), edge_batch_size):
            batch = type_edges[batch_start:batch_start + edge_batch_size]

            # Build batch of edge creates using graph IDs
            create_statements = []
            for edge in batch:
                source_gid = id_map.get(edge.from_entity_id)
                target_gids = name_map.get(edge.to_entity_id, [])

                if source_gid is None:
                    skipped += 1
                    continue

                if not target_gids:
                    skipped += 1
                    continue

                # Create edge to first matching target (by name)
                target_gid = target_gids[0]

                stmt = f"""
                    MATCH (a) WHERE id(a) = {source_gid}
                    MATCH (b) WHERE id(b) = {target_gid}
                    CREATE (a)-[:{edge_type} {{
                        edge_id: '{edge.edge_id}',
                        line_number: {edge.line_number},
                        extraction_run_id: '{extraction_run_id}'
                    }}]->(b)
                """
                create_statements.append(stmt)

            if create_statements:
                try:
                    # Execute batch
                    batch_query = f"""
                    SELECT * FROM cypher('{STAGING_GRAPH}', $$
                        {' '.join(create_statements)}
                    $$) as (result agtype);
                    """
                    await conn.execute(batch_query)
                    success += len(create_statements)
                except Exception as e:
                    # If batch fails, try individual edges
                    for stmt in create_statements:
                        try:
                            single_query = f"""
                            SELECT * FROM cypher('{STAGING_GRAPH}', $$
                                {stmt}
                            $$) as (result agtype);
                            """
                            await conn.execute(single_query)
                            success += 1
                        except:
                            failed += 1

            if (batch_start + edge_batch_size) % 5000 == 0 or batch_start + edge_batch_size >= len(type_edges):
                logger.info(f"    Processed {min(batch_start + edge_batch_size, len(type_edges))}/{len(type_edges)} {edge_type} edges (success: {success}, skipped: {skipped})...")

    logger.info(f"  Edge loading complete: {success} success, {failed} failed, {skipped} skipped (no matching nodes)")
    return success, failed


def detect_language(file_path: str) -> str:
    """Detect language from file extension"""
    ext_map = {
        ".go": "go",
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".js": "javascript",
        ".jsx": "javascript",
    }
    for ext, lang in ext_map.items():
        if file_path.endswith(ext):
            return lang
    return ""


async def extract_repository(
    repo_path: Path,
    repo_name: str,
    extractor: TreeSitterExtractor
) -> Tuple[List[CodeEntity], List[CodeEdge]]:
    """Extract all entities and edges from a repository"""
    all_entities = []
    all_edges = []

    extensions = ["*.go", "*.py", "*.ts", "*.tsx", "*.js"]
    files = []
    for ext in extensions:
        files.extend(repo_path.glob(f"**/{ext}"))

    files = [
        f for f in files
        if "vendor" not in str(f)
        and "node_modules" not in str(f)
        and not str(f).endswith("_test.go")
        and ".git" not in str(f)
    ]

    logger.info(f"Found {len(files)} code files in {repo_name}")

    for i, file_path in enumerate(files):
        try:
            language = detect_language(str(file_path))
            if not language:
                continue

            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            rel_path = str(file_path.relative_to(repo_path))
            entities, edges = extractor.extract(language, content, rel_path, repo_name)

            all_entities.extend(entities)
            all_edges.extend(edges)

        except Exception as e:
            logger.warning(f"Error processing {file_path}: {e}")

        if (i + 1) % 100 == 0:
            logger.info(f"  Processed {i + 1}/{len(files)} files...")

    return all_entities, all_edges


async def main():
    parser = argparse.ArgumentParser(description="Load tree-sitter entities to staging graph")
    parser.add_argument("--repo", required=True, help="Repository name")
    parser.add_argument("--path", required=True, help="Path to repository")
    parser.add_argument("--dry-run", action="store_true", help="Extract without loading")
    parser.add_argument("--clear-first", action="store_true", help="Clear existing data first")
    parser.add_argument("--skip-edges", action="store_true", help="Skip edge loading (faster)")

    args = parser.parse_args()

    repo_path = Path(args.path)
    if not repo_path.exists():
        logger.error(f"Repository path does not exist: {repo_path}")
        sys.exit(1)

    extraction_run_id = hashlib.sha256(
        f"{args.repo}:{datetime.now(timezone.utc).isoformat()}".encode()
    ).hexdigest()[:16]

    logger.info(f"=" * 50)
    logger.info(f"STAGING GRAPH LOADER")
    logger.info(f"=" * 50)
    logger.info(f"Extraction run: {extraction_run_id}")
    logger.info(f"Repository: {args.repo}")
    logger.info(f"Path: {repo_path}")
    logger.info(f"Target graph: {STAGING_GRAPH}")
    logger.info(f"Batch size: {BATCH_SIZE}")

    start_time = time.time()

    # Initialize extractor
    extractor = TreeSitterExtractor()

    # Extract entities and edges
    logger.info("Extracting entities and edges...")
    entities, edges = await extract_repository(repo_path, args.repo, extractor)

    extraction_time = time.time() - start_time
    logger.info(f"Extraction complete in {extraction_time:.1f}s")
    logger.info(f"  Entities: {len(entities)}")
    logger.info(f"  Edges: {len(edges)}")

    # Print breakdown
    by_type = {}
    for e in entities:
        by_type[e.entity_type] = by_type.get(e.entity_type, 0) + 1

    logger.info("Entity breakdown:")
    for t, c in sorted(by_type.items(), key=lambda x: -x[1]):
        logger.info(f"  {t}: {c}")

    if args.dry_run:
        logger.info("Dry run complete - no data written")
        return

    # Connect to database
    db_url = f"postgresql://{DB_CONFIG['user']}:{DB_CONFIG['password']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"

    logger.info(f"Connecting to {DB_CONFIG['host']}:{DB_CONFIG['port']}...")

    try:
        conn = await asyncpg.connect(db_url)
        await setup_age(conn)

        if not await create_staging_graph(conn):
            await conn.close()
            sys.exit(1)

        if args.clear_first:
            await clear_staging_graph(conn, args.repo)

        # Load entities
        load_start = time.time()
        logger.info("Loading entities...")
        entity_success, entity_failed = await load_entities_batch(conn, entities, extraction_run_id)
        entity_time = time.time() - load_start
        logger.info(f"Entities loaded in {entity_time:.1f}s: {entity_success} success, {entity_failed} failed")

        # Load edges
        edge_success = 0
        edge_failed = 0
        if not args.skip_edges:
            edge_start = time.time()
            logger.info("Loading edges...")
            edge_success, edge_failed = await load_edges_batch(conn, edges, extraction_run_id)
            edge_time = time.time() - edge_start
            logger.info(f"Edges loaded in {edge_time:.1f}s: {edge_success} success, {edge_failed} failed")

        total_time = time.time() - start_time

        logger.info("=" * 50)
        logger.info("LOAD COMPLETE")
        logger.info("=" * 50)
        logger.info(f"  Total time: {total_time:.1f}s")
        logger.info(f"  Entities: {entity_success}")
        logger.info(f"  Edges: {edge_success}")
        logger.info(f"  Graph: {STAGING_GRAPH}")
        logger.info("=" * 50)

        await conn.close()

    except Exception as e:
        logger.error(f"Database error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
