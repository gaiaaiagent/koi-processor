#!/usr/bin/env python3
"""Reconcile missing koi_memory_chunks for specific governed docs.

When the embed service is out or a --watch sensor skips a file, chunks go
missing for individual bridge notes. This script re-indexes just the docs
named via --doc-id or --from-manifest, without scanning a whole repo — much
faster when only a few docs are missing.

Usage:
    # Single doc
    python scripts/reconcile_missing_chunks.py --doc-id spore.connection.protocol-society

    # Multiple from manifest (one doc_id per line, comments after '#' ignored)
    python scripts/reconcile_missing_chunks.py --from-manifest missing.txt

    # Override repo search roots (default: common project roots under ~/projects)
    python scripts/reconcile_missing_chunks.py --doc-id x --repo-root /path/to/repo
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

KOI_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOI_ROOT))
sys.path.insert(0, str(KOI_ROOT / "scripts"))

import asyncpg
from api.embedding_provider import RemoteEmbeddingProvider
from api.chunker import TextChunker
from doc_scanner import (
    parse_frontmatter,
    content_hash,
    is_governed,
    upsert_doc,
    upsert_chunks,
    EXCLUDE_DIRS,
)

POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")
EMBEDDING_REMOTE_URL = os.getenv("EMBEDDING_REMOTE_URL", "http://10.100.0.1:8352")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "1024"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")

DEFAULT_REPO_ROOTS = [
    Path.home() / "projects" / "spore",
    Path.home() / "projects" / "intelligence-commons",
    Path.home() / "projects" / "poietic-match",
    Path.home() / "projects" / "darren-workflow",
    Path.home() / "projects" / "salish-sea-dreaming",
    Path.home() / "projects" / "BioregionKnwoledgeCommons" / "BioregionalKnowledgeCommoning",
    Path.home() / "projects" / "flowcoding",
]


def find_doc(doc_id: str, repo_roots: List[Path]) -> Optional[Tuple[Path, Path, str]]:
    """Return (repo_root, file_path, repo_name) for the first .md file whose
    frontmatter doc_id matches, or None if not found."""
    for root in repo_roots:
        if not root.is_dir():
            continue
        for p in root.rglob("*.md"):
            if any(part in EXCLUDE_DIRS for part in p.parts):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if not text.startswith("---"):
                continue
            fm, _ = parse_frontmatter(text)
            if fm.get("doc_id") == doc_id:
                repo_name = root.name
                # The BKC path has a nested dir; use the leaf name consistently
                if repo_name == "BioregionalKnowledgeCommoning":
                    repo_name = "bkc"
                return root, p, repo_name
    return None


def parse_manifest(path: Path) -> List[str]:
    ids = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            ids.append(line)
    return ids


async def index_doc(
    pool: asyncpg.Pool,
    embedder: RemoteEmbeddingProvider,
    chunker: TextChunker,
    repo_root: Path,
    file_path: Path,
    repo_name: str,
) -> bool:
    rel_path = str(file_path.relative_to(repo_root))
    raw = file_path.read_text(encoding="utf-8", errors="replace")
    fm, body = parse_frontmatter(raw)
    if not is_governed(fm):
        print(f"  SKIP (no doc_id): {rel_path}")
        return False
    chash = content_hash(raw)
    rid = f"doc-scanner:{repo_name}:{rel_path}"

    chunks = chunker.chunk_text(body or raw)
    if not chunks:
        print(f"  no chunks produced for {rel_path}")
        return False

    print(f"  {len(chunks)} chunks; embedding...")
    embeddings: List[Optional[List[float]]] = []
    for i, chunk in enumerate(chunks):
        try:
            emb = await embedder.embed(chunk["text"])
            embeddings.append(emb)
        except Exception as e:
            print(f"    chunk {i+1}/{len(chunks)} FAILED: {e}")
            embeddings.append(None)

    async with pool.acquire() as conn:
        await upsert_doc(conn, rid, repo_name, rel_path, fm, body, chash)
        await upsert_chunks(conn, rid, chunks, embeddings, fm, repo_name, rel_path)
    ok = sum(1 for e in embeddings if e)
    print(f"  wrote {ok}/{len(chunks)} chunks for {fm.get('doc_id')}")
    return ok > 0


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--doc-id", help="Single doc_id to reconcile")
    group.add_argument("--from-manifest",
                       help="File with one doc_id per line (# comments ok)")
    parser.add_argument("--repo-root", action="append", type=Path,
                        help="Repo root to search (repeat; overrides defaults)")
    args = parser.parse_args()

    repo_roots = args.repo_root or DEFAULT_REPO_ROOTS
    doc_ids = [args.doc_id] if args.doc_id else parse_manifest(Path(args.from_manifest))

    print(f"Reconciling {len(doc_ids)} doc_id(s) across {len(repo_roots)} repo root(s)")

    pool = await asyncpg.create_pool(POSTGRES_URL, min_size=1, max_size=3)
    embedder = RemoteEmbeddingProvider(
        base_url=EMBEDDING_REMOTE_URL,
        dimension=EMBEDDING_DIMENSION,
        model=EMBEDDING_MODEL,
    )
    chunker = TextChunker(chunk_size=500, chunk_overlap=50)

    failures: List[str] = []
    for doc_id in doc_ids:
        print(f"\n→ {doc_id}")
        found = find_doc(doc_id, repo_roots)
        if not found:
            print(f"  NOT FOUND in any repo root")
            failures.append(doc_id)
            continue
        repo_root, file_path, repo_name = found
        print(f"  found: {repo_name}:{file_path.relative_to(repo_root)}")
        ok = await index_doc(pool, embedder, chunker, repo_root, file_path, repo_name)
        if not ok:
            failures.append(doc_id)

    await pool.close()

    if failures:
        print(f"\nFAILED: {len(failures)} / {len(doc_ids)}")
        for d in failures:
            print(f"  {d}")
        sys.exit(1)
    print(f"\nDONE ({len(doc_ids)} reconciled)")


if __name__ == "__main__":
    asyncio.run(main())
