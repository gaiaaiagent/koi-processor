#!/usr/bin/env python3
"""
Community Detection for Code Graphs (Leiden Algorithm)

Post-processing script that detects communities in an Apache AGE code graph
using the Leiden algorithm. Operates on Function/Class/Interface/Method/Handler
nodes connected by CALLS edges within a single repository.

Creates Community nodes and MEMBER_OF edges in the graph for downstream use
by detect_flows.py and other analysis tools.

Usage:
    python scripts/community_detection.py --repo koi-processor
    python scripts/community_detection.py --all-repos
    python scripts/community_detection.py --repo koi-processor --dry-run
    python scripts/community_detection.py --repo koi-processor --graph-name regen_graph_v2

Requirements:
    pip install igraph leidenalg asyncpg loguru
"""

import os
import sys
import argparse
import asyncio
import hashlib
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import igraph
import leidenalg
from loguru import logger

DB_CONFIG = {
    "host": os.environ.get("KOI_DB_HOST", "localhost"),
    "port": int(os.environ.get("KOI_DB_PORT", "5433")),
    "database": os.environ.get("KOI_DB_NAME", "eliza"),
    "user": os.environ.get("KOI_DB_USER", "postgres"),
    "password": os.environ.get("KOI_DB_PASSWORD", "postgres"),
}

ELIGIBLE_NODE_TYPES = ("Function", "Class", "Interface", "Method", "Handler")
LARGE_GRAPH_THRESHOLD = 10_000


def escape_cypher(text: str) -> str:
    """Escape special characters for Cypher property values."""
    if not text:
        return ""
    return (
        text.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
        .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    )


async def setup_age(conn: asyncpg.Connection) -> None:
    """Load the AGE extension and set the search path."""
    await conn.execute("LOAD 'age';")
    await conn.execute("SET search_path = ag_catalog, '$user', public;")


def _db_url() -> str:
    return (
        f"postgresql://{DB_CONFIG['user']}:{DB_CONFIG['password']}"
        f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"
    )


def _generate_run_id(repo: str) -> str:
    raw = f"{repo}:community:{datetime.now(timezone.utc).isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# -- 1. Ensure AGE labels ------------------------------------------------

async def ensure_labels(conn: asyncpg.Connection, graph_name: str = "regen_graph") -> None:
    """Create Community vertex label and MEMBER_OF edge label if they don't exist."""
    for label_name, is_edge in [("Community", False), ("MEMBER_OF", True)]:
        exists = await conn.fetchval(
            "SELECT COUNT(*) FROM ag_catalog.ag_label WHERE name = $1 AND graph = "
            "(SELECT graphid FROM ag_catalog.ag_graph WHERE name = $2)",
            label_name, graph_name,
        )
        if exists == 0:
            if is_edge:
                await conn.execute(f"""
                    SELECT * FROM cypher('{graph_name}', $$
                        CREATE (a:_Dummy)-[r:{label_name}]->(b:_Dummy)
                        DELETE r, a, b
                    $$) as (result agtype);
                """)
            else:
                await conn.execute(f"SELECT create_vlabel('{graph_name}', '{label_name}');")
            logger.info(f"Created {'edge' if is_edge else 'vertex'} label '{label_name}'")


# -- 2. Cleanup old communities ------------------------------------------

async def cleanup_old_communities(
    conn: asyncpg.Connection, repo: str, graph_name: str = "regen_graph",
) -> int:
    """Remove existing Community nodes (and MEMBER_OF edges) for a repo."""
    repo_escaped = escape_cypher(repo)
    row = await conn.fetchrow(f"""
    SELECT * FROM cypher('{graph_name}', $$
        MATCH (c:Community {{repo: '{repo_escaped}'}})
        RETURN count(c) as cnt
    $$) as (cnt agtype);
    """)
    old_count = int(str(row["cnt"])) if row else 0
    if old_count > 0:
        await conn.execute(f"""
        SELECT * FROM cypher('{graph_name}', $$
            MATCH (c:Community {{repo: '{repo_escaped}'}})
            DETACH DELETE c
        $$) as (result agtype);
        """)
        logger.info(f"Deleted {old_count} old Community node(s) for repo '{repo}'")
    return old_count


# -- 3. Pull graph data ---------------------------------------------------

async def pull_graph_data(
    conn: asyncpg.Connection, repo: str, graph_name: str = "regen_graph",
) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str]]]:
    """Query AGE for eligible nodes and CALLS edges filtered by repo.

    Matches nodes by both vertex label AND entity_type property since the
    production graph (regen_graph) uses labels while the staging graph
    (regen_graph_v2) uses the entity_type property.
    """
    repo_escaped = escape_cypher(repo)

    # Query each eligible label separately to handle AGE's label-based storage
    all_node_rows = []
    for node_type in ELIGIBLE_NODE_TYPES:
        try:
            rows = await conn.fetch(f"""
            SELECT * FROM cypher('{graph_name}', $$
                MATCH (n:{node_type})
                WHERE n.repo = '{repo_escaped}'
                RETURN n.entity_id, n.name, n.file_path, '{node_type}' as entity_type
            $$) as (entity_id agtype, name agtype, file_path agtype, entity_type agtype);
            """)
            all_node_rows.extend(rows)
        except Exception:
            pass  # Label may not exist in this graph

    # Also check entity_type property for graphs that store type as property
    type_clauses = " OR ".join(f"n.entity_type = '{t}'" for t in ELIGIBLE_NODE_TYPES)
    try:
        prop_rows = await conn.fetch(f"""
        SELECT * FROM cypher('{graph_name}', $$
            MATCH (n)
            WHERE n.repo = '{repo_escaped}' AND ({type_clauses})
            RETURN n.entity_id, n.name, n.file_path, n.entity_type
        $$) as (entity_id agtype, name agtype, file_path agtype, entity_type agtype);
        """)
        all_node_rows.extend(prop_rows)
    except Exception:
        pass

    node_rows = all_node_rows

    nodes: List[Dict[str, Any]] = []
    node_ids_set: set = set()
    for row in node_rows:
        eid = str(row["entity_id"]).strip('"')
        if eid in node_ids_set:
            continue  # deduplicate across label and property queries
        nodes.append({
            "entity_id": eid,
            "name": str(row["name"]).strip('"'),
            "file_path": str(row["file_path"]).strip('"'),
            "entity_type": str(row["entity_type"]).strip('"'),
        })
        node_ids_set.add(eid)
    logger.info(f"Pulled {len(nodes)} eligible nodes for repo '{repo}'")

    edge_rows = await conn.fetch(f"""
    SELECT * FROM cypher('{graph_name}', $$
        MATCH (a)-[:CALLS]->(b)
        WHERE a.repo = '{repo_escaped}' AND b.repo = '{repo_escaped}'
        RETURN a.entity_id, b.entity_id
    $$) as (source_id agtype, target_id agtype);
    """)

    edges: List[Tuple[str, str]] = []
    for row in edge_rows:
        src = str(row["source_id"]).strip('"')
        tgt = str(row["target_id"]).strip('"')
        if src in node_ids_set and tgt in node_ids_set:
            edges.append((src, tgt))
    logger.info(f"Pulled {len(edges)} CALLS edges for repo '{repo}'")
    return nodes, edges


# -- 4. Build igraph ------------------------------------------------------

def build_igraph(
    nodes: List[Dict[str, Any]], edges: List[Tuple[str, str]],
) -> Tuple[igraph.Graph, Dict[str, int], Dict[int, str]]:
    """Construct an undirected igraph.Graph from nodes and edges."""
    id_to_idx: Dict[str, int] = {}
    idx_to_id: Dict[int, str] = {}
    for i, node in enumerate(nodes):
        id_to_idx[node["entity_id"]] = i
        idx_to_id[i] = node["entity_id"]

    g = igraph.Graph(n=len(nodes), directed=False)
    g.vs["entity_id"] = [n["entity_id"] for n in nodes]
    g.vs["name"] = [n["name"] for n in nodes]
    g.vs["file_path"] = [n["file_path"] for n in nodes]
    g.vs["entity_type"] = [n["entity_type"] for n in nodes]

    igraph_edges = []
    for src, tgt in edges:
        si, ti = id_to_idx.get(src), id_to_idx.get(tgt)
        if si is not None and ti is not None and si != ti:
            igraph_edges.append((si, ti))
    if igraph_edges:
        g.add_edges(igraph_edges)
    g.simplify(multiple=True, loops=True)

    logger.info(
        f"Built igraph: {g.vcount()} vertices, {g.ecount()} edges, "
        f"{len(g.components())} connected components"
    )
    return g, id_to_idx, idx_to_id


# -- 5. Run Leiden ---------------------------------------------------------

def run_leiden(graph: igraph.Graph, resolution: float = 1.0):
    """Run Leiden community detection.

    Uses RBConfigurationVertexPartition when resolution != 1.0 (for large
    graphs) since ModularityVertexPartition doesn't support resolution_parameter.
    """
    if graph.vcount() == 0:
        logger.warning("Empty graph, skipping Leiden")
        return None
    if resolution != 1.0:
        partition = leidenalg.find_partition(
            graph, leidenalg.RBConfigurationVertexPartition,
            resolution_parameter=resolution,
        )
    else:
        partition = leidenalg.find_partition(
            graph, leidenalg.ModularityVertexPartition,
        )
    logger.info(
        f"Leiden found {len(partition)} communities "
        f"(modularity={partition.modularity:.4f}, resolution={resolution})"
    )
    return partition


# -- 6-7. Generate labels -------------------------------------------------

def _most_common_parent_dir(file_paths: List[str]) -> str:
    """Find the most common parent directory among file paths."""
    parents: List[str] = []
    for fp in file_paths:
        parts = fp.split("/")
        if len(parts) > 1:
            parents.append("/".join(parts[:min(2, len(parts) - 1)]) + "/")
        else:
            parents.append("/")
    if not parents:
        return "root"
    most_common, _ = Counter(parents).most_common(1)[0]
    return most_common


# -- 8. Calculate cohesion -------------------------------------------------

def calculate_cohesion(member_indices: List[int], graph: igraph.Graph) -> float:
    """Internal edge density: internal_edges / (n*(n-1)/2). Clamped to [0,1]."""
    n = len(member_indices)
    if n < 2:
        return 0.0
    possible = n * (n - 1) / 2.0
    member_set = set(member_indices)
    internal_edges = 0
    for idx in member_indices:
        for neighbor in graph.neighbors(idx):
            if neighbor in member_set and neighbor > idx:
                internal_edges += 1
    return max(0.0, min(1.0, internal_edges / possible))


def generate_labels(
    partition, nodes: List[Dict[str, Any]],
    id_to_idx: Dict[str, int], graph: igraph.Graph,
) -> List[Dict[str, Any]]:
    """Build community descriptors from Leiden partition. Filters singletons."""
    if partition is None:
        return []
    idx_to_id = {v: k for k, v in id_to_idx.items()}
    communities: List[Dict[str, Any]] = []

    for comm_idx, member_indices in enumerate(partition):
        if len(member_indices) < 2:
            continue
        member_entity_ids = [idx_to_id[i] for i in member_indices]
        member_file_paths = [
            graph.vs[i]["file_path"] for i in member_indices
            if graph.vs[i]["file_path"]
        ]
        label = _most_common_parent_dir(member_file_paths)
        communities.append({
            "community_id": f"comm_{comm_idx}",
            "name": label.rstrip("/") if label != "/" else "root",
            "members": member_entity_ids,
            "member_indices": member_indices,
            "cohesion": round(calculate_cohesion(member_indices, graph), 4),
            "symbol_count": len(member_indices),
        })

    communities.sort(key=lambda c: c["symbol_count"], reverse=True)
    logger.info(
        f"Generated {len(communities)} non-singleton communities "
        f"(filtered from {len(partition)} total partitions)"
    )
    return communities


# -- 9. Store communities in AGE -------------------------------------------

async def store_communities(
    conn: asyncpg.Connection, communities: List[Dict[str, Any]],
    repo: str, run_id: str, graph_name: str = "regen_graph",
) -> int:
    """Create Community nodes and MEMBER_OF edges in AGE. Returns edge count."""
    if not communities:
        return 0

    repo_esc = escape_cypher(repo)
    run_esc = escape_cypher(run_id)

    # Create Community nodes
    for comm in communities:
        cid, cname = escape_cypher(comm["community_id"]), escape_cypher(comm["name"])
        await conn.execute(f"""
        SELECT * FROM cypher('{graph_name}', $$
            CREATE (:Community {{
                community_id: '{cid}', name: '{cname}',
                cohesion: {comm['cohesion']}, symbol_count: {comm['symbol_count']},
                repo: '{repo_esc}', extraction_run_id: '{run_esc}'
            }})
        $$) as (result agtype);
        """)
    logger.info(f"Created {len(communities)} Community nodes")

    # Get graph IDs for Community nodes
    comm_rows = await conn.fetch(f"""
    SELECT * FROM cypher('{graph_name}', $$
        MATCH (c:Community {{repo: '{repo_esc}', extraction_run_id: '{run_esc}'}})
        RETURN c.community_id, id(c) as gid
    $$) as (community_id agtype, gid agtype);
    """)
    comm_gid_map: Dict[str, int] = {
        str(r["community_id"]).strip('"'): int(str(r["gid"])) for r in comm_rows
    }

    # Get graph IDs for member nodes
    member_rows = await conn.fetch(f"""
    SELECT * FROM cypher('{graph_name}', $$
        MATCH (n) WHERE n.repo = '{repo_esc}' AND n.entity_id IS NOT NULL
        RETURN n.entity_id, id(n) as gid
    $$) as (entity_id agtype, gid agtype);
    """)
    entity_gid_map: Dict[str, int] = {
        str(r["entity_id"]).strip('"'): int(str(r["gid"])) for r in member_rows
    }

    # Insert MEMBER_OF edges via direct SQL (batch per community)
    total_edges = 0
    edge_batch_size = 500
    for comm in communities:
        cid = comm["community_id"]
        comm_gid = comm_gid_map.get(cid)
        if comm_gid is None:
            logger.warning(f"No graph ID for Community '{cid}', skipping edges")
            continue

        values: List[str] = []
        for entity_id in comm["members"]:
            member_gid = entity_gid_map.get(entity_id)
            if member_gid is None:
                continue
            props = (
                f'{{"community_id": "{escape_cypher(cid)}", '
                f'"extraction_run_id": "{run_esc}"}}'
            )
            values.append(
                f"(graphid_in('{member_gid}'), graphid_in('{comm_gid}'), "
                f"'{props}'::agtype)"
            )

        for batch_start in range(0, len(values), edge_batch_size):
            batch = values[batch_start:batch_start + edge_batch_size]
            if not batch:
                continue
            try:
                await conn.execute(f"""
                    INSERT INTO {graph_name}."MEMBER_OF" (start_id, end_id, properties)
                    VALUES {', '.join(batch)}
                    ON CONFLICT DO NOTHING
                """)
                total_edges += len(batch)
            except Exception as e:
                logger.error(f"MEMBER_OF insert failed for '{cid}': {e}")

    logger.info(f"Created {total_edges} MEMBER_OF edges")
    return total_edges


# -- 10. Main orchestrator ------------------------------------------------

async def detect_communities(
    conn: asyncpg.Connection, repo: str, run_id: str,
    graph_name: str = "regen_graph", dry_run: bool = False,
) -> Dict[str, str]:
    """Full community detection pipeline for a single repo.

    Returns a dict mapping entity_id -> community_id.
    """
    t0 = time.time()
    logger.info(f"{'=' * 50}")
    logger.info(f"COMMUNITY DETECTION: {repo}")
    logger.info(f"{'=' * 50}")
    logger.info(f"Run ID: {run_id} | Graph: {graph_name} | Mode: {'DRY RUN' if dry_run else 'WRITE'}")

    await ensure_labels(conn, graph_name)
    if not dry_run:
        await cleanup_old_communities(conn, repo, graph_name)

    nodes, edges = await pull_graph_data(conn, repo, graph_name)
    if len(nodes) < 2:
        logger.warning(f"Not enough nodes ({len(nodes)}) for community detection")
        return {}

    graph, id_to_idx, idx_to_id = build_igraph(nodes, edges)
    if graph.ecount() == 0:
        logger.warning("No edges in graph, skipping community detection")
        return {}

    resolution = 1.5 if graph.vcount() > LARGE_GRAPH_THRESHOLD else 1.0
    partition = run_leiden(graph, resolution=resolution)
    if partition is None:
        return {}

    communities = generate_labels(partition, nodes, id_to_idx, graph)
    if not communities:
        logger.info("No non-singleton communities found")
        return {}

    # Log top communities
    for comm in communities[:15]:
        logger.info(
            f"  {comm['community_id']:>8s}  {comm['name']:<30s}  "
            f"members={comm['symbol_count']:>4d}  cohesion={comm['cohesion']:.3f}"
        )
    if len(communities) > 15:
        logger.info(f"  ... and {len(communities) - 15} more communities")

    # Build entity_id -> community_id mapping
    entity_to_community: Dict[str, str] = {}
    for comm in communities:
        for eid in comm["members"]:
            entity_to_community[eid] = comm["community_id"]

    if dry_run:
        logger.info("Dry run -- skipping writes to graph")
    else:
        edge_count = await store_communities(conn, communities, repo, run_id, graph_name)
        logger.info(f"Stored {len(communities)} communities, {edge_count} MEMBER_OF edges")

    elapsed = time.time() - t0
    logger.info(
        f"Completed in {elapsed:.1f}s | Nodes: {len(nodes)}, Edges: {len(edges)}, "
        f"Communities: {len(communities)}, Assigned: {len(entity_to_community)}/{len(nodes)}"
    )
    return entity_to_community


# -- CLI -------------------------------------------------------------------

async def _get_all_repos(conn: asyncpg.Connection, graph_name: str) -> List[str]:
    """Retrieve all distinct repo values from the graph."""
    rows = await conn.fetch(f"""
    SELECT * FROM cypher('{graph_name}', $$
        MATCH (n) WHERE n.repo IS NOT NULL RETURN DISTINCT n.repo
    $$) as (repo agtype);
    """)
    repos = sorted(str(row["repo"]).strip('"') for row in rows)
    return repos


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Detect communities in a code graph using the Leiden algorithm"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--repo", type=str, help="Single repository to process")
    group.add_argument("--all-repos", action="store_true", help="Process all repos in the graph")
    parser.add_argument("--dry-run", action="store_true", help="Analyse without writing")
    parser.add_argument("--graph-name", type=str, default="regen_graph",
                        help="AGE graph name (default: regen_graph)")
    args = parser.parse_args()

    logger.info(f"Connecting to {DB_CONFIG['host']}:{DB_CONFIG['port']}...")
    conn: Optional[asyncpg.Connection] = None

    try:
        conn = await asyncpg.connect(_db_url())
        await setup_age(conn)

        graph_exists = await conn.fetchval(
            "SELECT COUNT(*) FROM ag_catalog.ag_graph WHERE name = $1",
            args.graph_name,
        )
        if graph_exists == 0:
            logger.error(f"Graph '{args.graph_name}' does not exist")
            sys.exit(1)

        if args.all_repos:
            repos = await _get_all_repos(conn, args.graph_name)
            if not repos:
                logger.warning("No repos found in the graph")
                sys.exit(0)
            logger.info(f"Found {len(repos)} repos: {', '.join(repos)}")
        else:
            repos = [args.repo]

        all_results: Dict[str, Dict[str, str]] = {}
        total_start = time.time()

        for repo in repos:
            run_id = _generate_run_id(repo)
            try:
                result = await detect_communities(
                    conn, repo=repo, run_id=run_id,
                    graph_name=args.graph_name, dry_run=args.dry_run,
                )
                all_results[repo] = result
            except Exception as e:
                logger.error(f"Failed processing repo '{repo}': {e}")
                import traceback
                traceback.print_exc()

        total_elapsed = time.time() - total_start
        logger.info(f"{'=' * 50}")
        logger.info("COMMUNITY DETECTION COMPLETE")
        logger.info(f"{'=' * 50}")
        logger.info(f"  Repos processed: {len(all_results)}/{len(repos)}")
        for repo, mapping in all_results.items():
            logger.info(f"    {repo}: {len(mapping)} nodes assigned")
        logger.info(f"  Total time: {total_elapsed:.1f}s")
        logger.info(f"  Mode: {'DRY RUN' if args.dry_run else 'COMMITTED'}")

    except asyncpg.PostgresError as e:
        logger.error(f"Database error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        if conn is not None:
            await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
