#!/usr/bin/env python3
"""
Local-path doc scanner for governed markdown docs.

Scans a local repo directory for .md files with YAML frontmatter,
chunks them, embeds with the KOI embedding provider, and stores in
koi_memories (source_sensor='doc-scanner') + koi_memory_chunks.

Usage:
    cd /path/to/koi-processor
    source config/personal.env
    python scripts/doc_scanner.py /path/to/repo [--repo-name NAME] [--dry-run] [--force]

Options:
    --repo-name NAME   Override repo name (default: last path component)
    --dry-run          Parse and report without writing to DB
    --force            Re-index even if content hash unchanged
    --doc-id-only      Only index files that have a doc_id in frontmatter
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import yaml

# Add repo root to path
sys.path.insert(0, str(Path(__file__).parent.parent))
from api.embedding_provider import RemoteEmbeddingProvider
from api.chunker import TextChunker

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")
EMBEDDING_REMOTE_URL = os.getenv("EMBEDDING_REMOTE_URL", "http://10.100.0.1:8352")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "1024"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")

EXCLUDE_DIRS = {"node_modules", "venv", ".venv", "__pycache__", ".git", "dist", "build", "archive"}
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


# ── Frontmatter parsing ───────────────────────────────────────────────────────

def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """
    Extract YAML frontmatter from markdown content.
    Returns (frontmatter_dict, body_text).
    On any parse error, returns ({}, content).
    """
    if not content.startswith("---"):
        return {}, content

    try:
        end = content.index("\n---", 3)
        raw_yaml = content[3:end].strip()
        body = content[end + 4:].strip()
        data = yaml.safe_load(raw_yaml) or {}
        if not isinstance(data, dict):
            return {}, content
        return data, body
    except (ValueError, yaml.YAMLError):
        return {}, content


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def is_governed(fm: Dict[str, Any]) -> bool:
    """A doc is governed if it has a doc_id."""
    return bool(fm.get("doc_id"))


# ── DB helpers ────────────────────────────────────────────────────────────────

async def get_existing_hashes(conn: asyncpg.Connection, repo_name: str) -> Dict[str, str]:
    """Returns {rel_path: content_hash} for already-indexed docs in this repo."""
    rows = await conn.fetch("""
        SELECT metadata->>'rel_path' AS rel_path,
               metadata->>'content_hash' AS content_hash
        FROM koi_memories
        WHERE source_sensor = 'doc-scanner'
          AND metadata->>'repo' = $1
    """, repo_name)
    return {r["rel_path"]: r["content_hash"] for r in rows if r["rel_path"]}


async def upsert_doc(
    conn: asyncpg.Connection,
    rid: str,
    repo_name: str,
    rel_path: str,
    frontmatter: Dict[str, Any],
    body_text: str,
    chash: str,
) -> str:
    """Upsert into koi_memories. Returns the memory UUID."""
    doc_content = {
        "title": frontmatter.get("doc_id") or rel_path,
        "text": body_text,
        "file_path": rel_path,
    }
    doc_metadata: Dict[str, Any] = {
        "repo": repo_name,
        "rel_path": rel_path,
        "content_hash": chash,
        "is_governed": is_governed(frontmatter),
    }
    # Capture governed doc fields
    for field in ("doc_id", "doc_kind", "status", "visibility"):
        if field in frontmatter:
            doc_metadata[field] = frontmatter[field]
    if "depends_on" in frontmatter:
        deps = frontmatter["depends_on"]
        doc_metadata["depends_on"] = deps if isinstance(deps, list) else [deps]

    existing = await conn.fetchrow("SELECT id FROM koi_memories WHERE rid = $1", rid)
    event_type = "NEW" if existing is None else "UPDATE"

    memory_id = await conn.fetchval("""
        INSERT INTO koi_memories (id, rid, event_type, source_sensor, content, metadata)
        VALUES ($1, $2, $3, 'doc-scanner', $4::jsonb, $5::jsonb)
        ON CONFLICT (rid) DO UPDATE SET
            event_type = EXCLUDED.event_type,
            content = EXCLUDED.content,
            metadata = EXCLUDED.metadata,
            updated_at = NOW()
        RETURNING id
    """, uuid.uuid4(), rid, event_type,
        json.dumps(doc_content), json.dumps(doc_metadata))

    return str(memory_id)


async def upsert_chunks(
    conn: asyncpg.Connection,
    rid: str,
    chunks: List[Dict[str, Any]],
    embeddings: List[List[float]],
    frontmatter: Dict[str, Any],
    repo_name: str,
    rel_path: str,
):
    """Delete old chunks and insert new ones with embeddings."""
    await conn.execute("DELETE FROM koi_memory_chunks WHERE document_rid = $1", rid)

    chunk_meta: Dict[str, Any] = {
        "repo": repo_name,
        "rel_path": rel_path,
        "is_governed": is_governed(frontmatter),
    }
    for field in ("doc_id", "doc_kind", "status", "visibility"):
        if field in frontmatter:
            chunk_meta[field] = frontmatter[field]

    for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
        chunk_rid = f"{rid}:chunk:{i}"
        chunk_content = {
            "text": chunk["text"],
            "context": frontmatter.get("doc_id") or rel_path,
        }
        emb_str = json.dumps(emb)
        await conn.execute("""
            INSERT INTO koi_memory_chunks
                (chunk_rid, document_rid, chunk_index, total_chunks, content, embedding, metadata)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::vector, $7::jsonb)
            ON CONFLICT (chunk_rid) DO UPDATE SET
                chunk_index = EXCLUDED.chunk_index,
                total_chunks = EXCLUDED.total_chunks,
                content = EXCLUDED.content,
                embedding = EXCLUDED.embedding,
                metadata = EXCLUDED.metadata
        """, chunk_rid, rid, i, len(chunks),
            json.dumps(chunk_content), emb_str, json.dumps(chunk_meta))


# ── Scanner ───────────────────────────────────────────────────────────────────

async def scan_repo(
    repo_path: Path,
    repo_name: str,
    dry_run: bool,
    force: bool,
    doc_id_only: bool,
):
    pool = await asyncpg.create_pool(POSTGRES_URL, min_size=1, max_size=3)
    embedder = RemoteEmbeddingProvider(
        base_url=EMBEDDING_REMOTE_URL,
        dimension=EMBEDDING_DIMENSION,
        model=EMBEDDING_MODEL,
    )
    chunker = TextChunker(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)

    async with pool.acquire() as conn:
        existing = await get_existing_hashes(conn, repo_name)

    md_files = sorted([
        p for p in repo_path.rglob("*.md")
        if not any(part in EXCLUDE_DIRS for part in p.parts)
    ])

    logger.info("Found %d .md files in %s", len(md_files), repo_path)

    stats = {"scanned": 0, "indexed": 0, "skipped": 0, "errors": 0}

    for fpath in md_files:
        rel_path = str(fpath.relative_to(repo_path))
        try:
            raw = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.warning("Read error %s: %s", rel_path, e)
            stats["errors"] += 1
            continue

        fm, body = parse_frontmatter(raw)
        stats["scanned"] += 1

        if doc_id_only and not is_governed(fm):
            stats["skipped"] += 1
            continue

        chash = content_hash(raw)
        rid = f"doc-scanner:{repo_name}:{rel_path}"

        if not force and existing.get(rel_path) == chash:
            logger.debug("Unchanged %s", rel_path)
            stats["skipped"] += 1
            continue

        governed_marker = " [governed]" if is_governed(fm) else ""
        logger.info("Indexing %s%s", rel_path, governed_marker)

        if dry_run:
            print(f"  DRY-RUN: {rel_path} | doc_id={fm.get('doc_id')} | doc_kind={fm.get('doc_kind')} | status={fm.get('status')}")
            stats["indexed"] += 1
            continue

        # Chunk
        chunks = chunker.chunk_text(body or raw)
        if not chunks:
            logger.warning("No chunks produced for %s, skipping", rel_path)
            stats["skipped"] += 1
            continue

        # Embed chunks one at a time (batch calls can time out on large docs)
        embeddings = []
        for chunk in chunks:
            try:
                emb = await embedder.embed(chunk["text"])
                embeddings.append(emb)
            except Exception as e:
                logger.warning("Embedding failed for %s chunk: %s", rel_path, e)
                embeddings.append([0.0] * EMBEDDING_DIMENSION)

        async with pool.acquire() as conn:
            await upsert_doc(conn, rid, repo_name, rel_path, fm, body, chash)
            await upsert_chunks(conn, rid, chunks, embeddings, fm, repo_name, rel_path)

        stats["indexed"] += 1

    await pool.close()

    print(f"\nScan complete: {stats}")
    print(f"  Scanned:  {stats['scanned']}")
    print(f"  Indexed:  {stats['indexed']}")
    print(f"  Skipped:  {stats['skipped']}")
    print(f"  Errors:   {stats['errors']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_path", help="Path to repo root")
    parser.add_argument("--repo-name", help="Override repo name (default: dir name)")
    parser.add_argument("--dry-run", action="store_true", help="Parse without writing")
    parser.add_argument("--force", action="store_true", help="Re-index unchanged files")
    parser.add_argument("--doc-id-only", action="store_true",
                        help="Only index files with doc_id frontmatter")
    args = parser.parse_args()

    repo_path = Path(args.repo_path).resolve()
    if not repo_path.is_dir():
        print(f"Error: {repo_path} is not a directory")
        sys.exit(1)

    repo_name = args.repo_name or repo_path.name
    asyncio.run(scan_repo(repo_path, repo_name, args.dry_run, args.force, args.doc_id_only))


if __name__ == "__main__":
    main()
