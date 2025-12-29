#!/usr/bin/env python3
"""
Export KOI Knowledge Graph to GraphRAG 3D Visualization Format

Exports entity_registry and koi_relationships from PostgreSQL to the JSON format
expected by GraphRAG3D_EmbeddingView.html visualization.

Pipeline:
1. Export entities from entity_registry (with occurrence_count filter)
2. Parse pgvector embeddings
3. Compute UMAP 3D positions
4. Export relationships from koi_relationships
5. Compute degree centrality
6. Output graphrag_hierarchy.json

Usage:
    python scripts/export_graph_hierarchy.py [--min-occurrences 2] [--max-entities 10000]

Output:
    /opt/projects/GAIA/graph/data/graphrag_hierarchy/graphrag_hierarchy_v7.json
"""

import argparse
import json
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
OUTPUT_FILE = OUTPUT_DIR / "graphrag_hierarchy_v7.json"
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
    """
    Load canonical_entities.json and build alias->canonical mapping.

    Returns:
        alias_to_canonical: Dict mapping aliases to canonical names
        canonical_names: Set of all canonical entity names
    """
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
                # Map canonical name to itself
                alias_to_canonical[canonical_name.lower()] = canonical_name
                # Map all aliases
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
        # pgvector format: "[0.1,0.2,0.3,...]"
        cleaned = vec_str.strip('[]')
        values = [float(x) for x in cleaned.split(',')]
        return np.array(values, dtype=np.float32)
    except Exception as e:
        print(f"Warning: Failed to parse embedding: {e}")
        return None


def export_entities(conn, min_occurrences: int = 2, max_entities: int = 10000,
                    canonical_names: set = None) -> Tuple[Dict, np.ndarray, List[str]]:
    """
    Export entities from entity_registry.

    Args:
        conn: Database connection
        min_occurrences: Minimum occurrence count filter
        max_entities: Maximum entities to export
        canonical_names: Set of canonical entity names to always include

    Returns:
        entities_dict: Dict of entity_id -> entity data
        embeddings: np.ndarray of shape (n_entities, embedding_dim)
        entity_ids: List of entity_id strings in same order as embeddings
    """
    print(f"\n{'='*80}")
    print("STEP 1: Export Entities from entity_registry")
    print(f"{'='*80}")

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    canonical_names = canonical_names or set()

    # Get entities ordered by occurrence_count (most frequent first)
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
        entity_id = row['entity_text']  # Use entity_text as ID for visualization

        # Parse embedding
        emb = parse_pgvector(row['embedding']) if row['embedding'] else None

        if emb is not None:
            if embedding_dim is None:
                embedding_dim = len(emb)
            embeddings_list.append(emb)
            entity_ids.append(entity_id)

            entities_dict[entity_id] = {
                "label": row['entity_text'],
                "type": row['entity_type'] or "UNKNOWN",
                "description": "",
                "confidence": 0.8,  # Default confidence
                "degree": 0,  # Will be computed from relationships
                "uri": row['fuseki_uri'] or "",
                "occurrence_count": row['occurrence_count'],
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

    # Filter to only include relationships between entities we're exporting
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


def compute_degree_centrality(entities: Dict, relationships: List[Dict]) -> Dict[str, int]:
    """Compute degree centrality for each entity."""
    print(f"\n{'='*80}")
    print("STEP 3: Compute Degree Centrality")
    print(f"{'='*80}")

    degree_count = defaultdict(int)

    for rel in relationships:
        degree_count[rel['source']] += 1
        degree_count[rel['target']] += 1

    # Update entities with degree
    for entity_id, entity in entities.items():
        entity['degree'] = degree_count.get(entity_id, 0)

    max_degree = max(degree_count.values()) if degree_count else 0
    avg_degree = sum(degree_count.values()) / len(degree_count) if degree_count else 0
    print(f"  Max degree: {max_degree}")
    print(f"  Avg degree: {avg_degree:.2f}")

    return degree_count


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
        n_neighbors=min(UMAP_N_NEIGHBORS, len(embeddings) - 1),  # Handle small datasets
        min_dist=UMAP_MIN_DIST,
        metric=UMAP_METRIC,
        random_state=UMAP_RANDOM_STATE,
        verbose=True,
    )

    positions = reducer.fit_transform(embeddings)

    # Scale positions for visualization
    positions = positions * 50  # Scale to reasonable range for 3D viewer

    print(f"  Output shape: {positions.shape}")
    print(f"  Position ranges: x[{positions[:,0].min():.2f}, {positions[:,0].max():.2f}]")
    print(f"                   y[{positions[:,1].min():.2f}, {positions[:,1].max():.2f}]")
    print(f"                   z[{positions[:,2].min():.2f}, {positions[:,2].max():.2f}]")

    return positions


def build_output(entities: Dict, relationships: List[Dict], positions: np.ndarray, entity_ids: List[str]) -> Dict:
    """Build the final output JSON structure."""
    print(f"\n{'='*80}")
    print("STEP 5: Build Output JSON")
    print(f"{'='*80}")

    # Add UMAP positions to entities
    for i, entity_id in enumerate(entity_ids):
        if entity_id in entities:
            entities[entity_id]['umap_position'] = positions[i].tolist()
            entities[entity_id]['betweenness'] = 0.0  # Placeholder
            entities[entity_id]['relationship_strengths'] = {}

    output = {
        "test_mode": True,  # Required for GraphRAG3D_EmbeddingView.js to parse entities directly
        "entities": entities,
        "relationships": relationships,
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "total_entities": len(entities),
            "total_relationships": len(relationships),
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
        # Step 1: Export entities (includes canonical entities regardless of occurrence)
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

        # Step 4: Compute UMAP positions
        positions = compute_umap_positions(embeddings)

        # Step 5: Build output
        output = build_output(entities, relationships, positions, entity_ids)

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
