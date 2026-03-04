#!/usr/bin/env python3
"""
Compute Graph Metrics for GraphRAG v1 (B2)

Runs Leiden community detection and betweenness centrality over the
entity_relationships graph and caches results in entity_graph_metrics.

Uses graph_version hash (SHA-256 of entity_count:rel_count:max_timestamps)
for cache invalidation — only recomputes when the hash changes.

Usage:
    # Compute metrics (skips if cache is fresh)
    python scripts/compute_graph_metrics.py

    # Force recompute
    python scripts/compute_graph_metrics.py --force

    # Specify database URL
    python scripts/compute_graph_metrics.py --db-url postgresql://postgres@localhost/octo_koi

    # Print stats without computing
    python scripts/compute_graph_metrics.py --stats-only
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from typing import Dict, List, Optional, Tuple

try:
    import asyncpg
except ImportError:
    print("Error: asyncpg not installed. Run: pip install asyncpg")
    sys.exit(1)

try:
    import networkx as nx
except ImportError:
    print("Error: networkx not installed. Run: pip install networkx")
    sys.exit(1)

# Leiden is preferred but falls back to Louvain (NetworkX built-in)
try:
    import leidenalg
    import igraph as ig
    HAS_LEIDEN = True
except ImportError:
    HAS_LEIDEN = False
    print("Note: leidenalg not available, using Louvain (networkx) fallback")


async def get_db_url() -> str:
    """Build DB URL from env vars (same pattern as personal_ingest_api.py)."""
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "")
    db_name = os.getenv("DB_NAME", "octo_koi")
    if password:
        return f"postgresql://{user}:{password}@{host}:{port}/{db_name}"
    return f"postgresql://{user}@{host}:{port}/{db_name}"


async def compute_graph_version(conn: asyncpg.Connection) -> str:
    """Compute deterministic graph version hash from DB state."""
    row = await conn.fetchrow("""
        SELECT
            (SELECT COUNT(*) FROM entity_registry) AS entity_count,
            (SELECT COUNT(*) FROM entity_relationships) AS rel_count,
            (SELECT MAX(updated_at) FROM entity_registry) AS max_entity_updated,
            (SELECT GREATEST(MAX(created_at), MAX(updated_at)) FROM entity_relationships) AS max_rel_changed
    """)
    state_str = (
        f"{row['entity_count']}:{row['rel_count']}:"
        f"{row['max_entity_updated']}:{row['max_rel_changed']}"
    )
    return hashlib.sha256(state_str.encode()).hexdigest()[:16]


async def get_cached_version(conn: asyncpg.Connection) -> Optional[str]:
    """Get the graph_version of cached metrics, if any."""
    try:
        row = await conn.fetchrow(
            "SELECT DISTINCT graph_version FROM entity_graph_metrics LIMIT 1"
        )
        return row["graph_version"] if row else None
    except asyncpg.exceptions.UndefinedTableError:
        return None


async def load_graph(conn: asyncpg.Connection) -> Tuple[nx.Graph, Dict[int, str], Dict[str, int]]:
    """Load entity_relationships as a NetworkX graph.

    Returns (graph, id_to_uri, uri_to_id) mappings.
    """
    # Load entities
    entity_rows = await conn.fetch(
        "SELECT id, fuseki_uri, entity_text, entity_type FROM entity_registry"
    )
    uri_to_id: Dict[str, int] = {}
    id_to_uri: Dict[int, str] = {}
    id_to_label: Dict[int, str] = {}

    for row in entity_rows:
        uri_to_id[row["fuseki_uri"]] = row["id"]
        id_to_uri[row["id"]] = row["fuseki_uri"]
        id_to_label[row["id"]] = row["entity_text"]

    # Load relationships
    rel_rows = await conn.fetch(
        "SELECT subject_uri, object_uri, predicate FROM entity_relationships"
    )

    G = nx.Graph()
    for eid in id_to_uri:
        G.add_node(eid, label=id_to_label.get(eid, ""))

    for row in rel_rows:
        s_id = uri_to_id.get(row["subject_uri"])
        o_id = uri_to_id.get(row["object_uri"])
        if s_id is not None and o_id is not None and s_id != o_id:
            G.add_edge(s_id, o_id, predicate=row["predicate"])

    return G, id_to_uri, uri_to_id


def compute_communities_leiden(G: nx.Graph) -> Dict[int, Tuple[int, int]]:
    """Compute 2-level Leiden communities using igraph.

    Returns {node_id: (community_l1, community_l2)}.
    """
    # Convert NetworkX → igraph
    node_list = list(G.nodes())
    node_index = {n: i for i, n in enumerate(node_list)}

    ig_graph = ig.Graph()
    ig_graph.add_vertices(len(node_list))
    edges = [(node_index[u], node_index[v]) for u, v in G.edges()]
    ig_graph.add_edges(edges)

    # L1: coarse communities
    partition_l1 = leidenalg.find_partition(ig_graph, leidenalg.ModularityVertexPartition)
    l1_membership = partition_l1.membership

    # L2: subcommunities within each L1 community
    l2_membership = [0] * len(node_list)
    l2_counter = 0
    for comm_id in set(l1_membership):
        # Get subgraph for this community
        comm_nodes = [i for i, m in enumerate(l1_membership) if m == comm_id]
        if len(comm_nodes) < 3:
            for n in comm_nodes:
                l2_membership[n] = l2_counter
            l2_counter += 1
            continue

        subgraph = ig_graph.subgraph(comm_nodes)
        try:
            sub_partition = leidenalg.find_partition(
                subgraph, leidenalg.ModularityVertexPartition
            )
            for local_idx, sub_comm in enumerate(sub_partition.membership):
                global_idx = comm_nodes[local_idx]
                l2_membership[global_idx] = l2_counter + sub_comm
            l2_counter += max(sub_partition.membership) + 1
        except Exception:
            for n in comm_nodes:
                l2_membership[n] = l2_counter
            l2_counter += 1

    result = {}
    for i, node_id in enumerate(node_list):
        result[node_id] = (l1_membership[i], l2_membership[i])

    return result


def compute_communities_louvain(G: nx.Graph) -> Dict[int, Tuple[int, int]]:
    """Fallback: compute communities using NetworkX Louvain."""
    from networkx.algorithms.community import louvain_communities

    # L1
    communities_l1 = louvain_communities(G, seed=42)
    node_to_l1 = {}
    for comm_id, comm_nodes in enumerate(communities_l1):
        for n in comm_nodes:
            node_to_l1[n] = comm_id

    # L2: run Louvain again within each L1 community
    node_to_l2 = {}
    l2_counter = 0
    for comm_id, comm_nodes in enumerate(communities_l1):
        if len(comm_nodes) < 3:
            for n in comm_nodes:
                node_to_l2[n] = l2_counter
            l2_counter += 1
            continue

        subgraph = G.subgraph(comm_nodes)
        try:
            sub_comms = louvain_communities(subgraph, seed=42)
            for sub_id, sub_nodes in enumerate(sub_comms):
                for n in sub_nodes:
                    node_to_l2[n] = l2_counter + sub_id
            l2_counter += len(sub_comms)
        except Exception:
            for n in comm_nodes:
                node_to_l2[n] = l2_counter
            l2_counter += 1

    result = {}
    for node_id in G.nodes():
        result[node_id] = (node_to_l1.get(node_id, 0), node_to_l2.get(node_id, 0))

    return result


def compute_betweenness(G: nx.Graph) -> Dict[int, float]:
    """Compute betweenness centrality for all nodes."""
    return nx.betweenness_centrality(G)


async def save_metrics(
    conn: asyncpg.Connection,
    communities: Dict[int, Tuple[int, int]],
    betweenness: Dict[int, float],
    graph_version: str,
) -> int:
    """Save computed metrics to entity_graph_metrics table."""
    # Clear old metrics
    await conn.execute("DELETE FROM entity_graph_metrics")

    # Batch insert
    records = []
    for entity_id, (l1, l2) in communities.items():
        bc = betweenness.get(entity_id, 0.0)
        records.append((entity_id, l1, l2, bc, graph_version))

    await conn.executemany(
        """
        INSERT INTO entity_graph_metrics (entity_id, community_l1, community_l2, betweenness, graph_version)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (entity_id) DO UPDATE SET
            community_l1 = EXCLUDED.community_l1,
            community_l2 = EXCLUDED.community_l2,
            betweenness = EXCLUDED.betweenness,
            graph_version = EXCLUDED.graph_version,
            computed_at = NOW()
        """,
        records,
    )
    return len(records)


async def print_stats(conn: asyncpg.Connection) -> None:
    """Print current graph metrics stats."""
    try:
        rows = await conn.fetch("""
            SELECT
                graph_version,
                COUNT(*) as entities,
                COUNT(DISTINCT community_l1) as l1_communities,
                COUNT(DISTINCT community_l2) as l2_communities,
                ROUND(AVG(betweenness)::numeric, 6) as avg_betweenness,
                ROUND(MAX(betweenness)::numeric, 6) as max_betweenness,
                MIN(computed_at) as computed_at
            FROM entity_graph_metrics
            GROUP BY graph_version
        """)
        if not rows:
            print("No cached metrics found.")
            return

        for row in rows:
            print(f"Graph version: {row['graph_version']}")
            print(f"  Entities:       {row['entities']}")
            print(f"  L1 communities: {row['l1_communities']}")
            print(f"  L2 communities: {row['l2_communities']}")
            print(f"  Avg betweenness: {row['avg_betweenness']}")
            print(f"  Max betweenness: {row['max_betweenness']}")
            print(f"  Computed at:    {row['computed_at']}")

        # Top 10 by betweenness
        top = await conn.fetch("""
            SELECT
                egm.entity_id,
                er.entity_text,
                er.entity_type,
                egm.community_l1,
                egm.betweenness
            FROM entity_graph_metrics egm
            JOIN entity_registry er ON er.id = egm.entity_id
            ORDER BY egm.betweenness DESC
            LIMIT 10
        """)
        print(f"\n  Top 10 by betweenness centrality:")
        for row in top:
            print(f"    [{row['community_l1']:2d}] {row['betweenness']:.6f}  {row['entity_text']} ({row['entity_type']})")

    except asyncpg.exceptions.UndefinedTableError:
        print("entity_graph_metrics table does not exist. Run migration 059 first.")


async def main():
    parser = argparse.ArgumentParser(description="Compute graph metrics for GraphRAG")
    parser.add_argument("--db-url", help="PostgreSQL connection URL")
    parser.add_argument("--force", action="store_true", help="Force recompute even if cache is fresh")
    parser.add_argument("--stats-only", action="store_true", help="Print stats without computing")
    args = parser.parse_args()

    db_url = args.db_url or await get_db_url()
    conn = await asyncpg.connect(db_url)

    try:
        if args.stats_only:
            await print_stats(conn)
            return

        # Check graph version
        current_version = await compute_graph_version(conn)
        cached_version = await get_cached_version(conn)

        print(f"Current graph version: {current_version}")
        print(f"Cached graph version:  {cached_version or 'none'}")

        if current_version == cached_version and not args.force:
            print("Cache is fresh — skipping computation.")
            await print_stats(conn)
            return

        print(f"\nGraph changed (or --force). Computing metrics...")

        # Load graph
        t0 = time.monotonic()
        G, id_to_uri, uri_to_id = await load_graph(conn)
        t_load = time.monotonic() - t0
        print(f"  Graph loaded: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges ({t_load:.2f}s)")

        # Remove isolated nodes for community detection
        connected = [n for n in G.nodes() if G.degree(n) > 0]
        G_connected = G.subgraph(connected).copy()
        print(f"  Connected subgraph: {G_connected.number_of_nodes()} nodes")

        # Community detection
        t0 = time.monotonic()
        if HAS_LEIDEN:
            print("  Running Leiden community detection...")
            communities = compute_communities_leiden(G_connected)
        else:
            print("  Running Louvain community detection (fallback)...")
            communities = compute_communities_louvain(G_connected)

        # Add isolated nodes with community -1
        for n in G.nodes():
            if n not in communities:
                communities[n] = (-1, -1)

        t_comm = time.monotonic() - t0
        l1_set = set(c[0] for c in communities.values() if c[0] >= 0)
        l2_set = set(c[1] for c in communities.values() if c[1] >= 0)
        print(f"  Communities: {len(l1_set)} L1, {len(l2_set)} L2 ({t_comm:.2f}s)")

        # Betweenness centrality
        t0 = time.monotonic()
        print("  Computing betweenness centrality...")
        betweenness = compute_betweenness(G)
        t_bc = time.monotonic() - t0
        top_bc = sorted(betweenness.items(), key=lambda x: x[1], reverse=True)[:5]
        print(f"  Betweenness computed ({t_bc:.2f}s). Top 5:")
        for node_id, bc in top_bc:
            label = G.nodes[node_id].get("label", f"id={node_id}")
            print(f"    {bc:.6f}  {label}")

        # Save to DB
        t0 = time.monotonic()
        count = await save_metrics(conn, communities, betweenness, current_version)
        t_save = time.monotonic() - t0
        print(f"\n  Saved {count} entity metrics (graph_version={current_version}, {t_save:.2f}s)")

        # Print final stats
        print()
        await print_stats(conn)

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
