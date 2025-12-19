#!/usr/bin/env python3
"""
Batch Semantic Consolidation for entity_registry using cosine similarity clustering.

Pipeline:
1) Load all entities (id, entity_text, entity_type, occurrence_count)
2) Generate embeddings in batches (cached to disk)
3) Agglomerative clustering (cosine, average linkage) with threshold 0.88
4) Resolve type conflicts using hierarchy: PERSON > ORGANIZATION > PROJECT > TECHNOLOGY > CONCEPT
5) Deterministic canonical selection per cluster:
   - Longest name
   - Highest occurrence_count
   - Lowest id (earliest)
6) Dry-run: print planned merges; Execute: apply updates/deletes
"""

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import psycopg2
from sklearn.cluster import AgglomerativeClustering

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None

# Configuration
default_threshold = 0.88
embedding_model = "text-embedding-3-small"
embed_batch_size = 100

type_priority = {
    "PERSON": 5,
    "ORGANIZATION": 4,
    "PROJECT": 3,
    "TECHNOLOGY": 2,
    "CONCEPT": 1,
}


def load_entities(conn) -> List[Tuple[int, str, str, int]]:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, entity_text, entity_type, occurrence_count
        FROM entity_registry
        ORDER BY id
        """
    )
    return cursor.fetchall()


def embed_entities(client: OpenAI, entities: List[Tuple[int, str, str, int]], cache_path: Path) -> np.ndarray:
    cache: Dict[str, List[float]] = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
        except Exception:
            cache = {}

    embeddings: List[List[float]] = []
    total = len(entities)
    for start in range(0, total, embed_batch_size):
        batch = entities[start : start + embed_batch_size]
        texts = [e[1] for e in batch]

        # Use cached if all present
        batch_embeds: List[List[float]] = []
        missing_indices = []
        for i, t in enumerate(texts):
            key = t.lower()
            if key in cache:
                batch_embeds.append(cache[key])
            else:
                missing_indices.append(i)

        if missing_indices:
            inputs = [texts[i] for i in missing_indices]
            resp = client.embeddings.create(model=embedding_model, input=inputs)
            for idx, emb in zip(missing_indices, resp.data):
                emb_list = emb.embedding
                batch_embeds.insert(idx, emb_list)
                cache[texts[idx].lower()] = emb_list

        embeddings.extend(batch_embeds)
        print(f"Embeddings: {min(start + embed_batch_size, total)}/{total}")

    # Persist cache
    cache_path.write_text(json.dumps(cache))
    return np.array(embeddings)


def cluster_embeddings(embeddings: np.ndarray, threshold: float) -> np.ndarray:
    distance_threshold = 1 - threshold
    clustering = AgglomerativeClustering(
        n_clusters=None,
        metric="cosine",
        linkage="average",
        distance_threshold=distance_threshold,
    )
    return clustering.fit_predict(embeddings)


def resolve_type_conflict(cluster_entities: List[Tuple[int, str, str, int]]) -> str:
    types = {e[2] for e in cluster_entities}
    if len(types) == 1:
        return cluster_entities[0][2]
    best = max(types, key=lambda t: type_priority.get(t, 0))
    print(f"  Type conflict {types} -> promoting to {best}")
    return best


def pick_canonical(cluster_entities: List[Tuple[int, str, str, int]]) -> Tuple[int, str, str, int]:
    return max(
        cluster_entities,
        key=lambda e: (
            len(e[1]),  # longest name
            e[3],       # highest occurrence_count
            -e[0],      # lowest id (earliest)
        ),
    )


def plan_merges(entities: List[Tuple[int, str, str, int]], labels: np.ndarray) -> List[Dict]:
    clusters: Dict[int, List[Tuple[int, str, str, int]]] = defaultdict(list)
    for ent, label in zip(entities, labels):
        clusters[label].append(ent)

    plans = []
    for cid, members in clusters.items():
        if len(members) <= 1:
            continue
        target_type = resolve_type_conflict(members)
        canonical = pick_canonical(members)
        variants = [m for m in members if m[0] != canonical[0]]
        if not variants:
            continue
        plans.append(
            {
                "cluster_id": cid,
                "canonical": canonical,
                "target_type": target_type,
                "variants": variants,
            }
        )
    return plans


def apply_merges(conn, plans: List[Dict]):
    cursor = conn.cursor()
    total_merged = 0
    for plan in plans:
        cid = plan["cluster_id"]
        canonical = plan["canonical"]
        target_type = plan["target_type"]
        variants = plan["variants"]

        total_count = canonical[3] + sum(v[3] for v in variants)

        # Update canonical type and count
        cursor.execute(
            """
            UPDATE entity_registry
            SET occurrence_count = %s, entity_type = %s
            WHERE id = %s
            """,
            (total_count, target_type, canonical[0]),
        )

        if variants:
            cursor.execute(
                "DELETE FROM entity_registry WHERE id = ANY(%s)",
                ([v[0] for v in variants],),
            )

        print(f"Cluster {cid}: canonical '{canonical[1]}' <- {len(variants)} merges, total={total_count}")
        total_merged += len(variants)

    conn.commit()
    print(f"\n✓ Applied merges: {total_merged}")


def main():
    parser = argparse.ArgumentParser(description="Batch semantic consolidation for entity_registry")
    parser.add_argument("--threshold", type=float, default=default_threshold, help="Similarity threshold (default 0.88)")
    parser.add_argument("--execute", action="store_true", help="Apply changes (otherwise dry-run)")
    parser.add_argument("--cache", default=".cache/entity_embeddings.json", help="Path to embedding cache")
    args = parser.parse_args()

    print("Batch Semantic Consolidation")
    print("=" * 60)
    print(f"Threshold: {args.threshold}")
    print(f"Mode: {'EXECUTE' if args.execute else 'DRY-RUN'}")

    if OpenAI is None:
        raise RuntimeError("openai library not installed")
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5433)),
        database=os.getenv("POSTGRES_DB", "eliza"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )

    entities = load_entities(conn)
    print(f"Loaded {len(entities)} entities")

    cache_path = Path(args.cache)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    embeddings = embed_entities(client, entities, cache_path)
    print(f"Embeddings shape: {embeddings.shape}")

    labels = cluster_embeddings(embeddings, args.threshold)
    n_clusters = len(set(labels))
    print(f"Clusters found: {n_clusters}")

    plans = plan_merges(entities, labels)
    print(f"Clusters with merges: {len(plans)}")

    # Preview merges
    for plan in plans[:50]:
        cid = plan["cluster_id"]
        canonical = plan["canonical"]
        variants = plan["variants"]
        target_type = plan["target_type"]
        print(f"\nCluster {cid} ({len(variants)} variants -> canonical):")
        print(f"  Canonical: {canonical[1]} ({canonical[2]}, {canonical[3]} mentions) [target type: {target_type}]")
        for v in variants:
            print(f"  → {v[1]} ({v[2]}, {v[3]} mentions)")

    if args.execute:
        apply_merges(conn, plans)
    else:
        to_merge = sum(len(p["variants"]) for p in plans)
        print(f"\n[DRY-RUN] Would merge {to_merge} variants across {len(plans)} clusters")
        conn.close()


if __name__ == "__main__":
    main()
