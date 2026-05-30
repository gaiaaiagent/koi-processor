#!/usr/bin/env python3
"""
Arbitrary-document ingestion into KOI (de-gated RAG core).

Phase 0 of the unified "thorough content ingestion" capability
(`~/.claude/plans/plan-a-unified-thorough-wiggly-plum.md`).

Unlike `doc_scanner.py` (which is scoped to governed-repo markdown — requires
YAML frontmatter + a `doc_id` + a repo path), this module ingests ANY markdown
text under a content-addressed RID, with no frontmatter/repo requirement. It
reuses `TextChunker` + `OpenAIEmbeddingProvider` verbatim and writes the same
`koi_memories` + `koi_memory_chunks` (embedding_3072) tables, namespaced by
`source_sensor='document-ingest'` and `rid='document:<sha256(markdown)>'`.

`doc_scanner.py` and its `is_governed()` gate are intentionally left untouched.

Phase 0 implements `--tier rag` only (chunk + embed → RAG-searchable full text).
`standard` (facts) and `thorough` (discourse + claims) arrive in later phases.

Usage:
    cd /path/to/koi-processor
    source config/personal.env
    python scripts/ingest_document.py --source-path ~/Documents/sources/biohubs/biohubs-whitepaper.md \
        --tier rag --source-url https://example.org/paper --slug biohubs-whitepaper

Idempotency: `document_rid = document:<sha256(converted-markdown bytes)>`. Re-running
on identical bytes replaces the same chunk set (net Δ = 0 chunks).
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncpg

# Add repo root to path (mirror doc_scanner.py) so `api.*` imports resolve.
sys.path.insert(0, str(Path(__file__).parent.parent))
from api.embedding_provider import OpenAIEmbeddingProvider  # noqa: E402
from api.chunker import TextChunker  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Config (mirror doc_scanner.py) ──────────────────────────────────────────────

POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "3072"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-large")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
SOURCE_SENSOR = "document-ingest"

# Path-safety allowlist (plan §16). When INGEST_SOURCE_ROOT is set the backend
# endpoint enforces it strictly; the operator CLI defaults to these roots and
# falls back to "any readable file" only when neither root exists.
DEFAULT_ALLOWED_ROOTS = [
    os.path.expanduser("~/Documents/sources"),
    str(Path.cwd() / "tmp" / "incoming"),
]
VALID_TIERS = ("rag", "standard", "thorough")


# ── Identity / provenance ───────────────────────────────────────────────────────

def compute_document_rid(markdown: str) -> tuple[str, str]:
    """Content-address the CONVERTED MARKDOWN (plan §8/§26), not the source PDF/HTML.

    Returns (content_hash, document_rid). Full 64-hex sha256 — re-ingesting
    identical converted bytes yields the same RID and is a no-op upsert.
    """
    chash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    return chash, f"document:{chash}"


def resolve_allowed_source_path(raw_path: str) -> Path:
    """Canonicalize + allowlist-check source_path (plan §16).

    Rejects non-existent / non-file paths and (when a root is configured/exists)
    paths that escape the allowlisted root via `..` or symlink.
    """
    p = Path(raw_path).expanduser().resolve()
    if not p.is_file():
        raise ValueError(f"source_path is not a readable file: {p}")

    env_root = os.getenv("INGEST_SOURCE_ROOT")
    roots = [Path(env_root).expanduser().resolve()] if env_root else [
        Path(r).resolve() for r in DEFAULT_ALLOWED_ROOTS if Path(r).exists()
    ]
    if not roots:
        # No configured/extant root → operator-CLI trust (endpoint sets a root).
        return p
    for root in roots:
        try:
            p.relative_to(root)
            return p
        except ValueError:
            continue
    raise ValueError(
        f"source_path {p} is outside the allowlisted root(s): "
        f"{', '.join(str(r) for r in roots)}"
    )


# ── DB writes (de-gated; mirror doc_scanner's koi_memories/koi_memory_chunks) ────

async def upsert_document_memory(
    conn: asyncpg.Connection,
    document_rid: str,
    markdown: str,
    source_meta: Dict[str, Any],
) -> str:
    """Upsert the document row into koi_memories (source_sensor='document-ingest')."""
    doc_content = {
        "title": source_meta.get("name") or source_meta.get("slug") or document_rid,
        "text": markdown,
        "file_path": source_meta.get("source_path"),
    }
    doc_metadata: Dict[str, Any] = {
        "slug": source_meta.get("slug"),
        "source_url": source_meta.get("source_url"),
        "retrieval_method": source_meta.get("retrieval_method"),
        "retrieved_at": source_meta.get("retrieved_at"),
        "content_hash": source_meta.get("content_hash"),
        "group_id": source_meta.get("group_id", "personal"),
        "char_count": len(markdown),
        "tier": source_meta.get("tier", "rag"),
    }
    # Drop None-valued provenance keys for a clean metadata blob.
    doc_metadata = {k: v for k, v in doc_metadata.items() if v is not None}

    existing = await conn.fetchrow("SELECT id FROM koi_memories WHERE rid = $1", document_rid)
    event_type = "NEW" if existing is None else "UPDATE"

    memory_id = await conn.fetchval(
        """
        INSERT INTO koi_memories (id, rid, event_type, source_sensor, content, metadata)
        VALUES (gen_random_uuid(), $1, $2, $3, $4::jsonb, $5::jsonb)
        ON CONFLICT (rid) DO UPDATE SET
            event_type = EXCLUDED.event_type,
            content = EXCLUDED.content,
            metadata = EXCLUDED.metadata,
            updated_at = NOW()
        RETURNING id
        """,
        document_rid, event_type, SOURCE_SENSOR,
        json.dumps(doc_content), json.dumps(doc_metadata),
    )
    return str(memory_id)


async def upsert_document_chunks(
    conn: asyncpg.Connection,
    document_rid: str,
    chunks: List[Dict[str, Any]],
    embeddings: List[Optional[List[float]]],
    source_meta: Dict[str, Any],
) -> int:
    """Replace this document's chunks with the freshly embedded set.

    Writes embedding_3072 (the 3072-dim primary), never the legacy 1024 column.
    DELETE-then-INSERT keyed on document_rid keeps the chunk set stable across
    re-runs (idempotent: identical bytes → identical chunk count).
    """
    await conn.execute("DELETE FROM koi_memory_chunks WHERE document_rid = $1", document_rid)

    context = source_meta.get("name") or source_meta.get("slug") or document_rid
    base_meta = {
        "slug": source_meta.get("slug"),
        "group_id": source_meta.get("group_id", "personal"),
        "source_sensor": SOURCE_SENSOR,
    }
    base_meta = {k: v for k, v in base_meta.items() if v is not None}

    written = 0
    total = len(chunks)
    for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
        chunk_rid = f"{document_rid}:chunk:{i}"
        chunk_content = {"text": chunk["text"], "context": context}
        chunk_meta = dict(base_meta)
        if emb is None:
            chunk_meta["embedding_failed"] = True
        emb_str = json.dumps(emb) if emb is not None else None
        await conn.execute(
            """
            INSERT INTO koi_memory_chunks
                (chunk_rid, document_rid, chunk_index, total_chunks, content, embedding_3072, metadata)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::vector(3072), $7::jsonb)
            ON CONFLICT (chunk_rid) DO UPDATE SET
                chunk_index = EXCLUDED.chunk_index,
                total_chunks = EXCLUDED.total_chunks,
                content = EXCLUDED.content,
                embedding_3072 = EXCLUDED.embedding_3072,
                metadata = EXCLUDED.metadata
            """,
            chunk_rid, document_rid, i, total,
            json.dumps(chunk_content), emb_str, json.dumps(chunk_meta),
        )
        written += 1
    return written


# ── RAG core ─────────────────────────────────────────────────────────────────────

async def ingest_document_rag(
    pool: asyncpg.Pool,
    *,
    document_rid: str,
    markdown: str,
    source_meta: Dict[str, Any],
    embedder: OpenAIEmbeddingProvider,
    chunker: TextChunker,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Chunk + embed `markdown` and upsert koi_memories + koi_memory_chunks.

    Returns {document_rid, chunks_written, chunks_total, null_embeds}.
    This is the `--tier rag` core; higher tiers compose on top of it.
    """
    chunks = chunker.chunk_text(markdown)
    if not chunks:
        logger.warning("No chunks produced for %s", document_rid)
        return {"document_rid": document_rid, "chunks_written": 0, "chunks_total": 0, "null_embeds": 0}

    if dry_run:
        logger.info("DRY-RUN: %s → %d chunks (no DB write)", document_rid, len(chunks))
        return {"document_rid": document_rid, "chunks_written": 0, "chunks_total": len(chunks), "null_embeds": 0}

    # Embed one at a time (batch calls can time out on long docs) — mirror doc_scanner.
    embeddings: List[Optional[List[float]]] = []
    null_embeds = 0
    for idx, chunk in enumerate(chunks):
        try:
            emb = await embedder.embed(chunk["text"])
            embeddings.append(emb)
        except Exception as e:  # noqa: BLE001 — match doc_scanner's null-on-fail policy
            logger.warning("Embedding failed for %s chunk %d: %s", document_rid, idx, e)
            embeddings.append(None)
            null_embeds += 1

    async with pool.acquire() as conn:
        async with conn.transaction():
            await upsert_document_memory(conn, document_rid, markdown, source_meta)
            written = await upsert_document_chunks(conn, document_rid, chunks, embeddings, source_meta)

    logger.info(
        "Ingested %s: %d chunks written (%d null embeds)",
        document_rid, written, null_embeds,
    )
    return {
        "document_rid": document_rid,
        "chunks_written": written,
        "chunks_total": len(chunks),
        "null_embeds": null_embeds,
    }


async def ingest_path(
    *,
    source_path: str,
    tier: str,
    slug: Optional[str],
    name: Optional[str],
    source_url: Optional[str],
    retrieval_method: Optional[str],
    group_id: str,
    dry_run: bool,
) -> Dict[str, Any]:
    """CLI entrypoint: read an on-disk markdown file and ingest it at `tier`."""
    if tier not in VALID_TIERS:
        raise ValueError(f"--tier must be one of {VALID_TIERS}, got {tier!r}")
    if tier != "rag":
        raise NotImplementedError(
            f"tier={tier!r} is not implemented in Phase 0 — only 'rag' (chunk+embed) is live. "
            "facts (standard) and discourse+claims (thorough) arrive in later phases."
        )

    path = resolve_allowed_source_path(source_path)
    markdown = path.read_text(encoding="utf-8", errors="replace")
    if not markdown.strip():
        raise ValueError(f"source file is empty: {path}")

    content_hash, document_rid = compute_document_rid(markdown)
    source_meta = {
        "slug": slug or path.stem,
        "name": name,
        "source_url": source_url,
        "retrieval_method": retrieval_method,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "content_hash": content_hash,
        "group_id": group_id,
        "source_path": str(path),
        "tier": tier,
    }

    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY not set — required for document-ingest (OpenAI 3072-dim). "
            "Source config/personal.env or export the key."
        )
    embedder = OpenAIEmbeddingProvider(
        api_key=OPENAI_API_KEY, model=EMBEDDING_MODEL, dimension=EMBEDDING_DIMENSION,
    )
    chunker = TextChunker(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)

    pool = await asyncpg.create_pool(POSTGRES_URL, min_size=1, max_size=3)
    try:
        result = await ingest_document_rag(
            pool,
            document_rid=document_rid,
            markdown=markdown,
            source_meta=source_meta,
            embedder=embedder,
            chunker=chunker,
            dry_run=dry_run,
        )
    finally:
        await pool.close()

    result.update({"slug": source_meta["slug"], "content_hash": content_hash, "char_count": len(markdown)})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-path", required=True, help="Path to the converted markdown file")
    parser.add_argument("--tier", default="rag", choices=VALID_TIERS,
                        help="Ingestion depth (Phase 0 implements 'rag' only)")
    parser.add_argument("--slug", help="Stable slug (default: file stem)")
    parser.add_argument("--name", help="Human-readable document title")
    parser.add_argument("--source-url", help="Canonical source URL (provenance)")
    parser.add_argument("--retrieval-method", help="How it was acquired (download.php|playwright|operator-drop)")
    parser.add_argument("--group-id", default="personal", help="Learning field / group_id")
    parser.add_argument("--dry-run", action="store_true", help="Chunk + report without writing/embedding")
    args = parser.parse_args()

    try:
        result = asyncio.run(ingest_path(
            source_path=args.source_path,
            tier=args.tier,
            slug=args.slug,
            name=args.name,
            source_url=args.source_url,
            retrieval_method=args.retrieval_method,
            group_id=args.group_id,
            dry_run=args.dry_run,
        ))
    except (ValueError, NotImplementedError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print("\nDocument ingest complete:")
    print(f"  document_rid:   {result['document_rid']}")
    print(f"  slug:           {result.get('slug')}")
    print(f"  content_hash:   {result.get('content_hash')}")
    print(f"  char_count:     {result.get('char_count')}")
    print(f"  chunks_total:   {result.get('chunks_total')}")
    print(f"  chunks_written: {result.get('chunks_written')}")
    print(f"  null_embeds:    {result.get('null_embeds')}")


if __name__ == "__main__":
    main()
