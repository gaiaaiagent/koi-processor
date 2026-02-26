#!/usr/bin/env python3
"""
Export KOI Knowledge Graph to GraphRAG 3D Visualization Format

Exports entity_registry and koi_relationships from PostgreSQL to the full JSON format
expected by GraphRAG3D_EmbeddingView.html visualization.

Pipeline:
1. Export entities from entity_registry (with occurrence_count filter)
2. Parse pgvector embeddings
3. Compute UMAP 3D positions
4. Export relationships from koi_relationships
5. Compute betweenness centrality (approximate for large graphs)
6. Hierarchical Leiden clustering (3-level hierarchy)
7. Build cluster positions and hierarchy
8. Output full graphrag_hierarchy.json (entities, relationships, clusters, metadata)

Usage:
    python scripts/export_graph_hierarchy.py [--min-occurrences 2] [--max-entities 10000]

Output:
    /opt/projects/GAIA/graph/data/graphrag_hierarchy/graphrag_hierarchy.json
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor

try:
    import umap
    HAS_UMAP = True
except ImportError:
    HAS_UMAP = False
    print("Warning: umap-learn not installed. Will use random positions.")

try:
    import networkx as nx
    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False
    print("Warning: networkx not installed. Betweenness centrality will be zero.")

try:
    from graspologic.partition import hierarchical_leiden
    HAS_GRASPOLOGIC = True
except ImportError:
    HAS_GRASPOLOGIC = False
    print("Warning: graspologic not installed. Will use simple clustering fallback.")

# ============================================================================
# Configuration
# ============================================================================

POSTGRES_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", 5433)),
    "database": os.getenv("POSTGRES_DB", "eliza"),
    "user": os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
}

OUTPUT_DIR = Path("/opt/projects/GAIA/graph/data/graphrag_hierarchy")
OUTPUT_FILE = OUTPUT_DIR / "graphrag_hierarchy.json"
BACKUP_FILE = OUTPUT_DIR / f"graphrag_hierarchy_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

# UMAP parameters
UMAP_N_COMPONENTS = 3
UMAP_N_NEIGHBORS = 15
UMAP_MIN_DIST = 0.1
UMAP_METRIC = "cosine"
UMAP_RANDOM_STATE = 42

# Canonical entities config
CANONICAL_ENTITIES_FILE = Path("/opt/projects/koi-processor/data/canonical_entities.json")

# ============================================================================
# Canonical Entity Functions
# ============================================================================

def load_canonical_entities() -> Tuple[Dict[str, str], set]:
    """Load canonical_entities.json and build alias->canonical mapping."""
    alias_to_canonical = {}
    canonical_names = set()

    if not CANONICAL_ENTITIES_FILE.exists():
        print(f"  Warning: {CANONICAL_ENTITIES_FILE} not found")
        return alias_to_canonical, canonical_names

    with open(CANONICAL_ENTITIES_FILE) as f:
        data = json.load(f)

    for category, entities in data.get("entities", {}).items():
        for entity_key, entity_data in entities.items():
            canonical_name = entity_data.get("canonical_name")
            if canonical_name:
                canonical_names.add(canonical_name)
                alias_to_canonical[canonical_name.lower()] = canonical_name
                for alias in entity_data.get("aliases", []):
                    alias_to_canonical[alias.lower()] = canonical_name

    print(f"  Loaded {len(canonical_names)} canonical entities with {len(alias_to_canonical)} aliases")
    return alias_to_canonical, canonical_names


# ============================================================================
# Database Functions
# ============================================================================

def connect_db():
    """Connect to PostgreSQL."""
    return psycopg2.connect(**POSTGRES_CONFIG)


def parse_pgvector(vec_str: str) -> Optional[np.ndarray]:
    """Parse pgvector string format to numpy array."""
    if not vec_str:
        return None
    try:
        cleaned = vec_str.strip('[]')
        values = [float(x) for x in cleaned.split(',')]
        return np.array(values, dtype=np.float32)
    except Exception as e:
        print(f"Warning: Failed to parse embedding: {e}")
        return None


def export_entities(conn, min_occurrences: int = 2, max_entities: int = 10000,
                    canonical_names: set = None) -> Tuple[Dict, np.ndarray, List[str]]:
    """Export entities from entity_registry."""
    print(f"\n{'='*80}")
    print("STEP 1: Export Entities from entity_registry")
    print(f"{'='*80}")

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    canonical_names = canonical_names or set()

    cursor.execute("""
        SELECT
            id,
            entity_text,
            entity_type,
            fuseki_uri,
            occurrence_count,
            embedding
        FROM entity_registry
        WHERE occurrence_count >= %s
        ORDER BY occurrence_count DESC
        LIMIT %s
    """, (min_occurrences, max_entities))

    rows = cursor.fetchall()
    print(f"  Fetched {len(rows)} entities (min_occurrences={min_occurrences}, max={max_entities})")

    # Also fetch canonical entities that might have low occurrence counts
    fetched_names = {row['entity_text'] for row in rows}
    missing_canonical = canonical_names - fetched_names
    if missing_canonical:
        print(f"  Fetching {len(missing_canonical)} canonical entities with low occurrence...")
        placeholders = ','.join(['%s'] * len(missing_canonical))
        cursor.execute(f"""
            SELECT
                id,
                entity_text,
                entity_type,
                fuseki_uri,
                occurrence_count,
                embedding
            FROM entity_registry
            WHERE entity_text IN ({placeholders})
        """, tuple(missing_canonical))
        canonical_rows = cursor.fetchall()
        rows.extend(canonical_rows)
        print(f"  Added {len(canonical_rows)} canonical entities")

    entities_dict = {}
    embeddings_list = []
    entity_ids = []
    embedding_dim = None

    for row in rows:
        entity_id = row['entity_text']

        emb = parse_pgvector(row['embedding']) if row['embedding'] else None

        if emb is not None:
            if embedding_dim is None:
                embedding_dim = len(emb)
            embeddings_list.append(emb)
            entity_ids.append(entity_id)

            entities_dict[entity_id] = {
                "label": row['entity_text'],
                "name": row['entity_text'],
                "type": row['entity_type'] or "UNKNOWN",
                "description": "",
                "confidence": 0.8,
                "degree": 0,
                "uri": row['fuseki_uri'] or "",
                "occurrence_count": row['occurrence_count'],
                "mention_count": row['occurrence_count'],
                "sources": [],
            }

    embeddings = np.array(embeddings_list, dtype=np.float32)
    print(f"  Entities with embeddings: {len(entity_ids)}")
    print(f"  Embedding dimension: {embedding_dim}")

    return entities_dict, embeddings, entity_ids


def export_relationships(conn, entity_ids: set) -> List[Dict]:
    """Export relationships from koi_relationships."""
    print(f"\n{'='*80}")
    print("STEP 2: Export Relationships from koi_relationships")
    print(f"{'='*80}")

    cursor = conn.cursor(cursor_factory=RealDictCursor)

    cursor.execute("""
        SELECT
            s.entity_text AS source,
            o.entity_text AS target,
            r.predicate,
            r.confidence
        FROM koi_relationships r
        JOIN entity_registry s ON r.subject_entity_id = s.id
        JOIN entity_registry o ON r.object_entity_id = o.id
    """)

    all_rels = cursor.fetchall()
    print(f"  Total relationships in DB: {len(all_rels)}")

    relationships = []
    for rel in all_rels:
        if rel['source'] in entity_ids and rel['target'] in entity_ids:
            relationships.append({
                "source": rel['source'],
                "target": rel['target'],
                "predicate": rel['predicate'],
                "confidence": float(rel['confidence']) if rel['confidence'] else 0.5,
            })

    print(f"  Relationships in exported set: {len(relationships)}")

    return relationships


# ============================================================================
# Graph Analysis Functions
# ============================================================================

def compute_degree_centrality(entities: Dict, relationships: List[Dict]) -> Dict[str, int]:
    """Compute degree centrality for each entity."""
    print(f"\n{'='*80}")
    print("STEP 3: Compute Degree Centrality")
    print(f"{'='*80}")

    degree_count = defaultdict(int)

    for rel in relationships:
        degree_count[rel['source']] += 1
        degree_count[rel['target']] += 1

    for entity_id, entity in entities.items():
        entity['degree'] = degree_count.get(entity_id, 0)

    max_degree = max(degree_count.values()) if degree_count else 0
    avg_degree = sum(degree_count.values()) / len(degree_count) if degree_count else 0
    print(f"  Max degree: {max_degree}")
    print(f"  Avg degree: {avg_degree:.2f}")

    return degree_count


def build_networkx_graph(entity_ids: List[str], relationships: List[Dict]):
    """Build a NetworkX graph for centrality computation."""
    if not HAS_NETWORKX:
        return None

    G = nx.Graph()
    G.add_nodes_from(entity_ids)
    for rel in relationships:
        G.add_edge(rel['source'], rel['target'], weight=rel.get('confidence', 0.5))

    return G


def compute_betweenness_centrality(G, entities: Dict, k: int = 512):
    """Compute approximate betweenness centrality using NetworkX."""
    print(f"\n{'='*80}")
    print("STEP 3b: Compute Betweenness Centrality")
    print(f"{'='*80}")

    if G is None or not HAS_NETWORKX:
        print("  Skipping: NetworkX not available")
        for entity in entities.values():
            entity['betweenness'] = 0.0
        return

    n_nodes = G.number_of_nodes()
    k_actual = min(k, n_nodes)
    print(f"  Graph: {n_nodes} nodes, {G.number_of_edges()} edges")
    print(f"  Using k={k_actual} samples for approximation")

    bc = nx.betweenness_centrality(G, k=k_actual, normalized=True)

    for entity_id, entity in entities.items():
        entity['betweenness'] = bc.get(entity_id, 0.0)

    top_bc = sorted(bc.items(), key=lambda x: x[1], reverse=True)[:10]
    print(f"  Top 10 by betweenness:")
    for name, score in top_bc:
        print(f"    {name}: {score:.6f}")


def compute_relationship_strengths(entities: Dict, relationships: List[Dict]):
    """Compute relationship strengths per entity from confidence scores."""
    print(f"\n{'='*80}")
    print("STEP 3c: Compute Relationship Strengths")
    print(f"{'='*80}")

    strengths = defaultdict(lambda: defaultdict(float))

    for rel in relationships:
        conf = rel.get('confidence', 0.5)
        strengths[rel['source']][rel['target']] += conf
        strengths[rel['target']][rel['source']] += conf

    for entity_id, entity in entities.items():
        if entity_id in strengths:
            # Keep top 20 strongest connections
            sorted_rels = sorted(strengths[entity_id].items(), key=lambda x: x[1], reverse=True)[:20]
            entity['relationship_strengths'] = {k: round(v, 3) for k, v in sorted_rels}
        else:
            entity['relationship_strengths'] = {}

    total_with_strengths = sum(1 for e in entities.values() if e.get('relationship_strengths'))
    print(f"  Entities with relationship strengths: {total_with_strengths}")


# ============================================================================
# UMAP Positioning
# ============================================================================

def compute_umap_positions(embeddings: np.ndarray) -> np.ndarray:
    """Reduce embeddings to 3D using UMAP."""
    print(f"\n{'='*80}")
    print("STEP 4: Compute UMAP 3D Positions")
    print(f"{'='*80}")

    if not HAS_UMAP:
        print("  Warning: UMAP not available, using random positions")
        np.random.seed(42)
        positions = np.random.randn(len(embeddings), 3) * 50
        return positions

    print(f"  Input shape: {embeddings.shape}")
    print(f"  UMAP params: n_neighbors={UMAP_N_NEIGHBORS}, min_dist={UMAP_MIN_DIST}")

    reducer = umap.UMAP(
        n_components=UMAP_N_COMPONENTS,
        n_neighbors=min(UMAP_N_NEIGHBORS, len(embeddings) - 1),
        min_dist=UMAP_MIN_DIST,
        metric=UMAP_METRIC,
        random_state=UMAP_RANDOM_STATE,
        verbose=True,
    )

    positions = reducer.fit_transform(embeddings)

    # Scale positions for visualization
    positions = positions * 50

    print(f"  Output shape: {positions.shape}")
    print(f"  Position ranges: x[{positions[:,0].min():.2f}, {positions[:,0].max():.2f}]")
    print(f"                   y[{positions[:,1].min():.2f}, {positions[:,1].max():.2f}]")
    print(f"                   z[{positions[:,2].min():.2f}, {positions[:,2].max():.2f}]")

    return positions


# ============================================================================
# Clustering
# ============================================================================

def compute_leiden_clustering(G, entity_ids: List[str], positions: np.ndarray,
                              entities: Dict) -> Dict:
    """
    Compute hierarchical Leiden clustering.
    Returns cluster hierarchy with level_0 (entities), level_1 (fine), level_2 (coarse).
    """
    print(f"\n{'='*80}")
    print("STEP 5: Compute Hierarchical Leiden Clustering")
    print(f"{'='*80}")

    entity_to_idx = {eid: i for i, eid in enumerate(entity_ids)}

    if HAS_GRASPOLOGIC and G is not None and G.number_of_edges() > 0:
        print("  Using graspologic hierarchical_leiden")
        try:
            results = hierarchical_leiden(G, max_cluster_size=100, random_seed=42)

            # Build membership at each level
            # Results is a list of HierarchicalCluster objects with .node, .cluster, .level
            level_memberships = defaultdict(lambda: defaultdict(list))
            for hc in results:
                level_memberships[hc.level][hc.cluster].append(str(hc.node))

            levels = sorted(level_memberships.keys())
            print(f"  Found {len(levels)} hierarchy levels")
            for lvl in levels:
                n_clusters = len(level_memberships[lvl])
                print(f"    Level {lvl}: {n_clusters} clusters")

        except Exception as e:
            print(f"  Leiden failed: {e}, falling back to simple clustering")
            level_memberships = _simple_clustering_fallback(G, entity_ids, entities)
    else:
        print("  Using simple clustering fallback")
        level_memberships = _simple_clustering_fallback(G, entity_ids, entities)

    # Build cluster structures
    clusters = _build_cluster_hierarchy(level_memberships, entity_ids, positions, entities)

    return clusters


def _simple_clustering_fallback(G, entity_ids: List[str], entities: Dict) -> Dict:
    """Simple type-based clustering as fallback when Leiden unavailable."""
    level_memberships = defaultdict(lambda: defaultdict(list))

    # Level 0: each entity is its own cluster
    for eid in entity_ids:
        level_memberships[0][eid].append(eid)

    # Level 1: cluster by entity type
    type_clusters = defaultdict(list)
    for eid in entity_ids:
        etype = entities.get(eid, {}).get('type', 'UNKNOWN')
        type_clusters[f"type_{etype}"].append(eid)
    level_memberships[1] = type_clusters

    # Level 2: merge small type clusters into "Other" super-cluster
    coarse_clusters = defaultdict(list)
    for cluster_id, members in type_clusters.items():
        if len(members) > 50:
            coarse_clusters[cluster_id] = members
        else:
            coarse_clusters['misc_other'].extend(members)
    level_memberships[2] = coarse_clusters

    return level_memberships


def _build_cluster_hierarchy(level_memberships: Dict, entity_ids: List[str],
                              positions: np.ndarray, entities: Dict) -> Dict:
    """Build the cluster hierarchy JSON structure."""
    entity_to_idx = {eid: i for i, eid in enumerate(entity_ids)}

    clusters = {}

    # Level 0: individual entities (with cluster membership info)
    level0 = {}
    levels = sorted(level_memberships.keys())

    # Find the entity's finest-grain cluster membership
    entity_to_l1 = {}
    if len(levels) >= 2:
        l1_key = levels[1] if len(levels) > 1 else levels[0]
        for cluster_id, members in level_memberships[l1_key].items():
            for m in members:
                entity_to_l1[m] = cluster_id

    for eid in entity_ids:
        idx = entity_to_idx[eid]
        pos = positions[idx].tolist()
        entity_data = entities.get(eid, {})
        level0[eid] = {
            "entity_id": eid,
            "entity": entity_data,
            "umap_position": pos,
            "position": pos,
            "betweenness": entity_data.get('betweenness', 0.0),
            "relationship_strengths": entity_data.get('relationship_strengths', {}),
        }

    clusters['level_0'] = level0

    # Helper to compute cluster center position
    def cluster_center(member_eids):
        idxs = [entity_to_idx[m] for m in member_eids if m in entity_to_idx]
        if not idxs:
            return [0.0, 0.0, 0.0]
        member_positions = positions[idxs]
        center = member_positions.mean(axis=0)
        return [round(float(c), 4) for c in center]

    # Level 1: fine clusters
    if len(levels) >= 2:
        l1_key = levels[1]
        level1 = {}
        for cluster_id, members in level_memberships[l1_key].items():
            center = cluster_center(members)
            # Find parent L2 cluster
            level1[str(cluster_id)] = {
                "name": f"Community {cluster_id}",
                "title": f"Community {cluster_id}",
                "position": center,
                "umap_position": center,
                "children": [],  # Will link to L2 parent
                "entities": members,
                "node_count": len(members),
            }
        clusters['level_1'] = level1

    # Level 2: coarse clusters
    if len(levels) >= 3:
        l2_key = levels[2]
        level2 = {}
        # Build L1->L2 parent mapping
        l1_to_l2 = {}
        for l2_id, l2_members in level_memberships[l2_key].items():
            l2_member_set = set(l2_members)
            for l1_id, l1_members in level_memberships[levels[1]].items():
                if set(l1_members) & l2_member_set:
                    l1_to_l2[l1_id] = l2_id

        for cluster_id, members in level_memberships[l2_key].items():
            center = cluster_center(members)
            # Find child L1 clusters
            child_l1_ids = [l1_id for l1_id, l2_id in l1_to_l2.items() if l2_id == cluster_id]
            level2[str(cluster_id)] = {
                "name": f"Super-Community {cluster_id}",
                "title": f"Super-Community {cluster_id}",
                "position": center,
                "umap_position": center,
                "children": [str(c) for c in child_l1_ids],
                "entities": members,
                "node_count": len(members),
            }
        clusters['level_2'] = level2

        # Update L1 children to reference L2 parents
        for l1_id, l2_id in l1_to_l2.items():
            if str(l1_id) in clusters.get('level_1', {}):
                clusters['level_1'][str(l1_id)]['children'] = [str(l2_id)]

    return clusters


# ============================================================================
# Output Building
# ============================================================================

def build_output(entities: Dict, relationships: List[Dict], positions: np.ndarray,
                 entity_ids: List[str], clusters: Dict) -> Dict:
    """Build the final output JSON structure in full format (not test_mode)."""
    print(f"\n{'='*80}")
    print("STEP 6: Build Output JSON")
    print(f"{'='*80}")

    # Add UMAP positions to entities
    for i, entity_id in enumerate(entity_ids):
        if entity_id in entities:
            pos = positions[i].tolist()
            # Sanitize NaN/Inf values
            pos = [0.0 if (math.isnan(v) or math.isinf(v)) else round(v, 4) for v in pos]
            entities[entity_id]['umap_position'] = pos

    # Sanitize betweenness values
    for entity in entities.values():
        bc = entity.get('betweenness', 0.0)
        if isinstance(bc, float) and (math.isnan(bc) or math.isinf(bc)):
            entity['betweenness'] = 0.0

    output = {
        "entities": entities,
        "relationships": relationships,
        "clusters": clusters,
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "total_entities": len(entities),
            "total_relationships": len(relationships),
            "total_clusters": {
                "level_0": len(clusters.get('level_0', {})),
                "level_1": len(clusters.get('level_1', {})),
                "level_2": len(clusters.get('level_2', {})),
            },
            "source": "entity_registry + koi_relationships",
            "umap": {
                "n_components": UMAP_N_COMPONENTS,
                "n_neighbors": UMAP_N_NEIGHBORS,
                "min_dist": UMAP_MIN_DIST,
                "metric": UMAP_METRIC,
            }
        }
    }

    print(f"  Entities: {len(entities)}")
    print(f"  Relationships: {len(relationships)}")
    print(f"  Clusters L0: {len(clusters.get('level_0', {}))}")
    print(f"  Clusters L1: {len(clusters.get('level_1', {}))}")
    print(f"  Clusters L2: {len(clusters.get('level_2', {}))}")

    return output


def main():
    parser = argparse.ArgumentParser(description="Export KOI graph to visualization format")
    parser.add_argument("--min-occurrences", type=int, default=2,
                        help="Minimum occurrence count for entities (default: 2)")
    parser.add_argument("--max-entities", type=int, default=10000,
                        help="Maximum number of entities to export (default: 10000)")
    parser.add_argument("--output", type=str, default=str(OUTPUT_FILE),
                        help=f"Output file path (default: {OUTPUT_FILE})")
    args = parser.parse_args()

    print("="*80)
    print("KOI Graph Export to GraphRAG 3D Visualization Format")
    print("="*80)
    print(f"\nStarted: {datetime.now().isoformat()}")
    print(f"Output: {args.output}")

    # Load canonical entities to ensure they're included
    print(f"\n{'='*80}")
    print("STEP 0: Load Canonical Entities")
    print(f"{'='*80}")
    alias_to_canonical, canonical_names = load_canonical_entities()

    conn = connect_db()

    try:
        # Step 1: Export entities
        entities, embeddings, entity_ids = export_entities(
            conn,
            min_occurrences=args.min_occurrences,
            max_entities=args.max_entities,
            canonical_names=canonical_names
        )

        entity_id_set = set(entity_ids)

        # Step 2: Export relationships
        relationships = export_relationships(conn, entity_id_set)

        # Step 3: Compute degree centrality
        compute_degree_centrality(entities, relationships)

        # Step 3b: Build NetworkX graph and compute betweenness
        G = build_networkx_graph(entity_ids, relationships)
        compute_betweenness_centrality(G, entities, k=512)

        # Step 3c: Compute relationship strengths
        compute_relationship_strengths(entities, relationships)

        # Step 4: Compute UMAP positions
        positions = compute_umap_positions(embeddings)

        # Step 4b: Center positions on centroid of all nodes
        # This ensures the point cloud is symmetric in the viewport.
        # The JS centerOnEntityName() handles camera focus on Regen Network.
        centroid = positions.mean(axis=0)
        positions = positions - centroid
        print(f"\n  Centered positions on centroid (shifted by [{centroid[0]:.2f}, {centroid[1]:.2f}, {centroid[2]:.2f}])")
        print(f"  New ranges: x[{positions[:,0].min():.2f}, {positions[:,0].max():.2f}]")
        print(f"              y[{positions[:,1].min():.2f}, {positions[:,1].max():.2f}]")
        print(f"              z[{positions[:,2].min():.2f}, {positions[:,2].max():.2f}]")

        # Report where Regen Network ended up
        top_entity = max(entities.items(), key=lambda x: x[1].get('betweenness', 0))
        top_entity_id = top_entity[0]
        if top_entity_id in set(entity_ids):
            top_idx = entity_ids.index(top_entity_id)
            rn_pos = positions[top_idx]
            print(f"  '{top_entity_id}' position: [{rn_pos[0]:.2f}, {rn_pos[1]:.2f}, {rn_pos[2]:.2f}]")

        # Step 5: Compute hierarchical clustering
        clusters = compute_leiden_clustering(G, entity_ids, positions, entities)

        # Step 6: Build output (full format, no test_mode)
        output = build_output(entities, relationships, positions, entity_ids, clusters)

        # Backup existing file
        output_path = Path(args.output)
        if output_path.exists():
            print(f"\n  Creating backup: {BACKUP_FILE}")
            import shutil
            shutil.copy(output_path, BACKUP_FILE)

        # Write output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(output, f)

        file_size = output_path.stat().st_size / (1024 * 1024)

        print(f"\n{'='*80}")
        print("EXPORT COMPLETE")
        print(f"{'='*80}")
        print(f"  Output: {output_path}")
        print(f"  Size: {file_size:.2f} MB")
        print(f"  Entities: {len(entities)}")
        print(f"  Relationships: {len(relationships)}")
        print(f"\nCompleted: {datetime.now().isoformat()}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
