#!/usr/bin/env python3
"""
Generate 3D Podcast Visualization Map
Generates UMAP 3D coordinates with multi-level clustering for podcast episodes
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Any, Tuple
import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor
import openai
from sklearn.cluster import KMeans
from umap import UMAP
from collections import defaultdict
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configuration
POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://postgres:postgres@localhost:5433/eliza")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OUTPUT_FILE = Path(__file__).parent.parent / "output" / "web" / "podcast_map_3d.json"

# Ensure output directory exists
OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

# UMAP parameters
UMAP_N_NEIGHBORS = 15
UMAP_MIN_DIST = 0.1
UMAP_METRIC = 'cosine'
UMAP_RANDOM_STATE = 42

# Clustering parameters
CLUSTER_LEVELS = list(range(1, 19))  # 18 levels of clustering (1-18)
COORDINATE_SCALE = 500  # Scale UMAP coordinates to [-500, 500]


def get_db_connection():
    """Create PostgreSQL connection"""
    return psycopg2.connect(POSTGRES_URL)


def fetch_podcast_chunks() -> List[Dict[str, Any]]:
    """
    Fetch all podcast chunks from koi_memories with embeddings
    """
    print("📊 Fetching podcast chunks from database...")

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    query = """
    SELECT
        m.rid,
        m.content->>'text' as text,
        m.metadata->>'parent_rid' as episode_rid,
        m.metadata->>'url' as episode_url,
        m.metadata->>'original_id' as episode_id,
        m.metadata->>'chunk_index' as chunk_index,
        m.metadata->>'chunk_total' as chunk_total,
        m.metadata->>'published_at' as published_at,
        e.dim_1024 as embedding
    FROM koi_memories m
    JOIN koi_embeddings e ON m.id = e.memory_id
    WHERE m.rid LIKE 'regen.podcast:%'
      AND e.dim_1024 IS NOT NULL
      AND m.content->>'text' IS NOT NULL
    ORDER BY m.metadata->>'original_id', (m.metadata->>'chunk_index')::int
    """

    cursor.execute(query)
    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    print(f"✅ Fetched {len(rows)} podcast chunks with embeddings")
    return [dict(row) for row in rows]


def fetch_episode_metadata() -> Dict[str, Dict[str, Any]]:
    """
    Fetch episode metadata including titles from Soundcloud URLs
    """
    print("📝 Fetching episode metadata...")

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    query = """
    SELECT DISTINCT ON (metadata->>'parent_rid')
        metadata->>'parent_rid' as episode_rid,
        metadata->>'url' as url,
        metadata->>'original_id' as episode_id,
        metadata->>'published_at' as published_at,
        COUNT(*) OVER (PARTITION BY metadata->>'parent_rid') as chunk_count
    FROM koi_memories
    WHERE rid LIKE 'regen.podcast:%'
    ORDER BY metadata->>'parent_rid', (metadata->>'published_at')::timestamp
    """

    cursor.execute(query)
    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    # Extract titles from URLs
    episodes = {}
    for row in rows:
        episode_id = row['episode_id']
        url = row['url']

        # Extract title from Soundcloud URL
        # e.g., https://soundcloud.com/planetaryregeneration/ep-41-bill-plotkin
        title = url.split('/')[-1].replace('-', ' ').title() if url else f"Episode {episode_id}"

        episodes[episode_id] = {
            'episode_id': episode_id,
            'episode_rid': row['episode_rid'],
            'title': title,
            'url': url,
            'published_at': row['published_at'],
            'chunk_count': row['chunk_count']
        }

    print(f"✅ Fetched metadata for {len(episodes)} episodes")
    return episodes


def generate_umap_coordinates(embeddings: np.ndarray) -> np.ndarray:
    """
    Generate 3D UMAP coordinates from embeddings
    """
    print(f"🎨 Generating UMAP 3D coordinates for {len(embeddings)} points...")

    umap_model = UMAP(
        n_components=3,
        n_neighbors=UMAP_N_NEIGHBORS,
        min_dist=UMAP_MIN_DIST,
        metric=UMAP_METRIC,
        random_state=UMAP_RANDOM_STATE
    )

    coordinates = umap_model.fit_transform(embeddings)

    # Scale coordinates to [-500, 500] range for better visualization
    coordinates = coordinates - coordinates.mean(axis=0)
    max_abs = np.abs(coordinates).max()
    coordinates = (coordinates / max_abs) * COORDINATE_SCALE

    print(f"✅ Generated UMAP coordinates (range: {coordinates.min():.2f} to {coordinates.max():.2f})")
    return coordinates


def perform_clustering(embeddings: np.ndarray, n_clusters: int) -> np.ndarray:
    """
    Perform K-means clustering on embeddings
    """
    print(f"🔍 Performing K-means clustering with {n_clusters} clusters...")

    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = kmeans.fit_predict(embeddings)

    print(f"✅ Clustering complete")
    return labels


async def generate_cluster_labels(chunks: List[Dict[str, Any]], cluster_labels: np.ndarray, n_clusters: int) -> Dict[int, str]:
    """
    Generate semantic labels for clusters using GPT-4
    """
    print(f"🤖 Generating semantic labels for {n_clusters} clusters...")

    if not OPENAI_API_KEY:
        print("⚠️  No OpenAI API key found, using generic labels")
        return {i: f"Topic {i+1}" for i in range(n_clusters)}

    # Initialize OpenAI client (v1.0+ syntax)
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    cluster_labels_dict = {}

    # Group chunks by cluster
    cluster_texts = defaultdict(list)
    for i, label in enumerate(cluster_labels):
        if chunks[i]['text']:
            cluster_texts[label].append(chunks[i]['text'][:500])  # First 500 chars

    # Generate label for each cluster
    for cluster_id in range(n_clusters):
        texts = cluster_texts[cluster_id][:10]  # Sample up to 10 texts

        if not texts:
            cluster_labels_dict[cluster_id] = f"Cluster {cluster_id + 1}"
            continue

        prompt = f"""Analyze these podcast transcript excerpts and generate a concise 2-4 word topic label:

{chr(10).join(f"- {text}" for text in texts)}

Respond with ONLY the topic label, nothing else."""

        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=20,
                temperature=0.3
            )
            label = response.choices[0].message.content.strip()
            cluster_labels_dict[cluster_id] = label
            print(f"  Cluster {cluster_id}: {label}")
        except Exception as e:
            print(f"  ⚠️  Error generating label for cluster {cluster_id}: {e}")
            cluster_labels_dict[cluster_id] = f"Topic {cluster_id + 1}"

        await asyncio.sleep(0.5)  # Rate limiting

    return cluster_labels_dict


def map_transcript_to_audio_timestamp(chunk_index: int, transcript_data: Dict[str, Any]) -> float:
    """
    Map chunk index to audio timestamp using word-level timestamps from transcript
    """
    if not transcript_data or 'segments' not in transcript_data:
        # Fallback: estimate timestamp based on chunk index
        return chunk_index * 30.0  # Assume ~30 seconds per chunk

    segments = transcript_data['segments']

    # Calculate cumulative word count per segment
    cumulative_words = 0
    words_per_chunk = 200  # Approximate words per chunk
    target_word = chunk_index * words_per_chunk

    for segment in segments:
        segment_words = len(segment.get('words', []))
        if cumulative_words + segment_words >= target_word:
            # This segment contains our target word
            return segment.get('start', chunk_index * 30.0)
        cumulative_words += segment_words

    # Fallback to last segment
    if segments:
        return segments[-1].get('end', chunk_index * 30.0)

    return chunk_index * 30.0


def load_transcript_data(episode_id: str) -> Dict[str, Any]:
    """
    Load transcript JSON file for an episode
    """
    transcript_path = Path(f"/opt/projects/koi-sensors/sensors/podcast/transcripts/episode_{episode_id}.json")

    if not transcript_path.exists():
        return {}

    try:
        with open(transcript_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"  ⚠️  Error loading transcript for episode {episode_id}: {e}")
        return {}


def get_audio_url(episode_id: str, soundcloud_url: str) -> str:
    """
    Get local audio file path if available, otherwise return Soundcloud URL
    """
    # Strip 'podcast_' prefix if present
    clean_id = episode_id.replace('podcast_', '')

    # Check if local audio file exists
    audio_path = Path(f"/opt/projects/koi-sensors/sensors/podcast/temp_audio/episode_{clean_id}.mp3")

    if audio_path.exists():
        # Return relative URL that can be served by Flask
        return f"/podcast_audio/episode_{clean_id}.mp3"

    # Fallback to Soundcloud URL
    return soundcloud_url


async def main():
    """
    Main function to generate 3D podcast map data
    """
    print("\n🎧 === Podcast 3D Map Generator ===\n")

    # Step 1: Fetch data
    chunks = fetch_podcast_chunks()
    episodes = fetch_episode_metadata()

    if not chunks:
        print("❌ No podcast chunks found in database")
        return

    # Step 2: Prepare embeddings
    print("\n📐 Preparing embeddings...")
    embeddings = []
    valid_chunks = []

    for chunk in chunks:
        if chunk['embedding']:
            # Parse embedding (PostgreSQL vector type returns as string)
            emb_str = chunk['embedding']
            if isinstance(emb_str, str):
                # Remove brackets and parse
                emb_str = emb_str.strip('[]')
                emb = np.array([float(x) for x in emb_str.split(',')])
            else:
                emb = np.array(chunk['embedding'])

            embeddings.append(emb)
            valid_chunks.append(chunk)

    embeddings = np.array(embeddings)
    print(f"✅ Prepared {len(embeddings)} embeddings (shape: {embeddings.shape})")

    # Step 3: Generate UMAP coordinates
    coordinates = generate_umap_coordinates(embeddings)

    # Step 4: Multi-level clustering
    clustering_results = {}
    for n_clusters in CLUSTER_LEVELS:
        labels = perform_clustering(embeddings, n_clusters)
        cluster_labels = await generate_cluster_labels(valid_chunks, labels, n_clusters)
        clustering_results[n_clusters] = {
            'labels': labels,
            'names': cluster_labels
        }

    # Step 5: Build output data structure
    print("\n📦 Building output data structure...")

    points = []
    episode_id_to_int = {ep_id: idx for idx, ep_id in enumerate(sorted(episodes.keys()))}

    # Load all transcripts once
    transcript_cache = {}
    for episode_id in episodes.keys():
        transcript_cache[episode_id] = load_transcript_data(episode_id)

    for i, chunk in enumerate(valid_chunks):
        episode_id = chunk['episode_id']
        episode_info = episodes.get(episode_id, {})
        chunk_index = int(chunk.get('chunk_index', i))

        # Get audio timestamp from transcript
        transcript_data = transcript_cache.get(episode_id, {})
        audio_timestamp = map_transcript_to_audio_timestamp(chunk_index, transcript_data)

        # Get audio URL (local file if available, otherwise Soundcloud)
        audio_url = get_audio_url(episode_id, episode_info.get('url', ''))

        point = {
            'id': chunk['rid'],
            'text': chunk['text'][:500],  # First 500 chars for preview
            'x': float(coordinates[i][0]),
            'y': float(coordinates[i][1]),
            'z': float(coordinates[i][2]),
            'episode_id': episode_id,
            'episode_int_id': episode_id_to_int.get(episode_id, 0),
            'episode_title': episode_info.get('title', f'Episode {episode_id}'),
            'episode_url': episode_info.get('url', ''),
            'audio_url': audio_url,
            'chunk_index': chunk_index,
            'timestamp': audio_timestamp,
        }

        # Add cluster assignments for all levels
        for n_clusters in CLUSTER_LEVELS:
            cluster_label = int(clustering_results[n_clusters]['labels'][i])
            point[f'cluster_{n_clusters}'] = cluster_label
            point[f'cluster_{n_clusters}_name'] = clustering_results[n_clusters]['names'][cluster_label]

        points.append(point)

    # Build episodes list
    episodes_list = [
        {
            'episode_id': ep_id,
            'episode_int_id': episode_id_to_int[ep_id],
            'title': ep_info['title'],
            'url': ep_info['url'],
            'published_at': ep_info['published_at'],
            'chunk_count': ep_info['chunk_count']
        }
        for ep_id, ep_info in episodes.items()
    ]

    # Generate distinct colors for clusters
    def generate_cluster_colors(n_clusters: int) -> List[str]:
        """Generate visually distinct colors for clusters"""
        colors = [
            '#4CAF50',  # 1. Green
            '#2196F3',  # 2. Blue
            '#FF9800',  # 3. Orange
            '#9C27B0',  # 4. Purple
            '#F44336',  # 5. Red
            '#00BCD4',  # 6. Cyan
            '#FFEB3B',  # 7. Yellow
            '#E91E63',  # 8. Pink
            '#3F51B5',  # 9. Indigo
            '#8BC34A',  # 10. Light Green
            '#FF5722',  # 11. Deep Orange
            '#009688',  # 12. Teal
            '#FFC107',  # 13. Amber
            '#673AB7',  # 14. Deep Purple
            '#CDDC39',  # 15. Lime
            '#795548',  # 16. Brown
            '#607D8B',  # 17. Blue Grey
            '#FF4081',  # 18. Hot Pink
        ]
        return colors[:n_clusters]

    # Build clusters metadata
    clusters_by_level = {
        str(n_clusters): [
            {
                'id': i,
                'cluster_id': i,
                'name': name,
                'count': int((clustering_results[n_clusters]['labels'] == i).sum()),
                'color': generate_cluster_colors(n_clusters)[i]
            }
            for i, name in clustering_results[n_clusters]['names'].items()
        ]
        for n_clusters in CLUSTER_LEVELS
    }

    # Build links (sequential chunks in same episode)
    print("🔗 Building links between sequential chunks...")

    # Build color map for quick lookup
    cluster_color_map = {}
    for n_clusters in CLUSTER_LEVELS:
        for cluster_info in clusters_by_level[str(n_clusters)]:
            cluster_color_map[(n_clusters, cluster_info['id'])] = cluster_info['color']

    links = []
    default_cluster_level = min(CLUSTER_LEVELS)  # Use smallest cluster level for link colors
    for ep_id in episodes.keys():
        ep_chunks = [p for p in points if p['episode_id'] == ep_id]
        ep_chunks.sort(key=lambda p: p['chunk_index'])

        for i in range(len(ep_chunks) - 1):
            # Get the source chunk's cluster color (use smallest cluster level)
            source_cluster = ep_chunks[i][f'cluster_{default_cluster_level}']
            link_color = cluster_color_map.get((default_cluster_level, source_cluster), '#666666')

            links.append({
                'source': ep_chunks[i]['id'],
                'target': ep_chunks[i + 1]['id'],
                'episode_id': ep_id,
                'color': link_color,
                'opacity': 0.2
            })

    # Step 6: Save output
    output_data = {
        'points': points,
        'episodes': episodes_list,
        'clusters_by_level': clusters_by_level,
        'links': links,
        'metadata': {
            'total_points': len(points),
            'total_episodes': len(episodes),
            'umap_params': {
                'n_neighbors': UMAP_N_NEIGHBORS,
                'min_dist': UMAP_MIN_DIST,
                'metric': UMAP_METRIC
            },
            'cluster_levels': CLUSTER_LEVELS,
            'generated_at': str(np.datetime64('now'))
        }
    }

    print(f"\n💾 Saving to {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(output_data, f, indent=2)

    print(f"\n✅ Successfully generated 3D podcast map!")
    print(f"   Points: {len(points)}")
    print(f"   Episodes: {len(episodes)}")
    print(f"   Links: {len(links)}")
    print(f"   Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
