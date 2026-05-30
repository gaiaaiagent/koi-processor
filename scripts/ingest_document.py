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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncpg
import httpx

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
# Co-located KOI backend, pinned (see extract_deep_documents.py). A dedicated var so
# it does NOT inherit personal.env's general KOI_BASE_URL (a WireGuard peer).
KOI_BASE_URL = os.getenv("DOC_INGEST_KOI_URL", "http://localhost:8351")

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


# ── Claims + extractor composition (Phase 2 orchestration) ───────────────────────

def _claims_service_token() -> Optional[str]:
    """Token for the auth-gated /claims/ write path — env first, then the koi-state file."""
    tok = os.getenv("KOI_CLAIMS_SERVICE_TOKEN")
    if tok:
        return tok.strip()
    p = Path.home() / ".config/personal-koi/koi-state/claims_service_token"
    if p.exists():
        return p.read_text().strip()
    return None


async def extract_document_claims(
    http: httpx.AsyncClient, *, document_text: str, source_document: str,
    confidence_threshold: float = 0.7,
) -> Dict[str, Any]:
    """POST /claims/extract (auth-gated). SURFACES a non-OK response loudly — a silent
    401/403 would yield 'standard' runs with 0 claims and no error, the exact failure
    mode that bit project_bridge_notes.py. The future completion gate asserts
    claims_created >= 1 for standard/thorough."""
    token = _claims_service_token()
    if not token:
        raise RuntimeError(
            "KOI_CLAIMS_SERVICE_TOKEN not found (env or "
            "~/.config/personal-koi/koi-state/claims_service_token) — /claims/extract is auth-gated.")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"document_text": document_text, "source_document": source_document,
               "auto_create": True, "confidence_threshold": confidence_threshold}
    r = await http.post(f"{KOI_BASE_URL}/claims/extract", json=payload, headers=headers, timeout=240.0)
    if r.status_code in (401, 403):
        raise RuntimeError(
            f"/claims/extract auth failed ({r.status_code}); token rejected. NOT swallowing — "
            f"a silent {r.status_code} yields 0 claims with no error.")
    r.raise_for_status()
    data = r.json()
    created = data.get("auto_created")
    if created is None:
        created = data.get("claims_created", [])
    count = len(created) if isinstance(created, list) else int(created or 0)
    return {"claims_created": count}


def _load_extractor():
    """Import the Phase-1 document extractor module by path (sibling script)."""
    import importlib.util
    p = Path(__file__).parent / "extract_deep_documents.py"
    spec = importlib.util.spec_from_file_location("extract_deep_documents", str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def ingest_path(
    *,
    source_path: str,
    tier: str,
    slug: Optional[str],
    name: Optional[str],
    source_url: Optional[str],
    retrieval_method: Optional[str],
    group_id: str,
    claims: bool,
    force: bool,
    dry_run: bool,
) -> Dict[str, Any]:
    """Unified document-ingest orchestrator: rag -> (standard/thorough) extract -> claims.

    Depth dial: rag = chunk+embed only; standard = rag + facts + claims;
    thorough = standard + discourse (Phase 3). One content-addressed document_rid
    threads every stage; each stage is idempotent.
    """
    if tier not in VALID_TIERS:
        raise ValueError(f"--tier must be one of {VALID_TIERS}, got {tier!r}")

    path = resolve_allowed_source_path(source_path)
    markdown = path.read_text(encoding="utf-8", errors="replace")
    if not markdown.strip():
        raise ValueError(f"source file is empty: {path}")

    content_hash, document_rid = compute_document_rid(markdown)
    source_meta = {
        "slug": slug or path.stem, "name": name, "source_url": source_url,
        "retrieval_method": retrieval_method,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "content_hash": content_hash, "group_id": group_id,
        "source_path": str(path), "tier": tier,
    }
    source_document = source_url or document_rid

    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY not set — required for document-ingest (OpenAI 3072-dim). "
            "Source config/personal.env or export the key.")
    embedder = OpenAIEmbeddingProvider(api_key=OPENAI_API_KEY, model=EMBEDDING_MODEL,
                                       dimension=EMBEDDING_DIMENSION)
    chunker = TextChunker(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)

    result: Dict[str, Any] = {"document_rid": document_rid, "slug": source_meta["slug"],
                              "content_hash": content_hash, "char_count": len(markdown), "tier": tier}
    pool = await asyncpg.create_pool(POSTGRES_URL, min_size=1, max_size=3)
    try:
        # Stage 1 — RAG chunk+embed (all tiers).
        result["rag"] = await ingest_document_rag(
            pool, document_rid=document_rid, markdown=markdown, source_meta=source_meta,
            embedder=embedder, chunker=chunker, dry_run=dry_run)
        if dry_run or tier == "rag":
            return result

        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        async with httpx.AsyncClient() as http:
            # Stage 2 — deep-extract entities + facts (standard/thorough).
            edd = _load_extractor()
            result["extract"] = await edd.extract_deep_document(
                pool, http, document_rid=document_rid, tier=tier,
                group_id=group_id, run_id=run_id, force=force)
            # Stage 3 — impact claims (auth-gated; surfaced loudly, never swallowed).
            if claims:
                result["claims"] = await extract_document_claims(
                    http, document_text=markdown, source_document=source_document)
    finally:
        await pool.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-path", required=True, help="Path to the converted markdown file")
    parser.add_argument("--tier", default="standard", choices=VALID_TIERS,
                        help="Depth dial: rag | standard (rag+facts+claims) | thorough (+discourse, Phase 3)")
    parser.add_argument("--slug", help="Stable slug (default: file stem)")
    parser.add_argument("--name", help="Human-readable document title")
    parser.add_argument("--source-url", help="Canonical source URL (provenance)")
    parser.add_argument("--retrieval-method", help="How it was acquired (download.php|playwright|operator-drop)")
    parser.add_argument("--group-id", default="personal", help="Learning field / group_id")
    parser.add_argument("--no-claims", action="store_true", help="Skip the claims stage (standard/thorough)")
    parser.add_argument("--force", action="store_true", help="Re-extract all windows (ignore cache)")
    parser.add_argument("--dry-run", action="store_true", help="Chunk + report without writing/embedding")
    args = parser.parse_args()

    claims = (args.tier != "rag") and not args.no_claims
    try:
        result = asyncio.run(ingest_path(
            source_path=args.source_path, tier=args.tier, slug=args.slug, name=args.name,
            source_url=args.source_url, retrieval_method=args.retrieval_method,
            group_id=args.group_id, claims=claims, force=args.force, dry_run=args.dry_run,
        ))
    except (ValueError, NotImplementedError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    rag = result.get("rag", {})
    ext = result.get("extract") or {}
    cl = result.get("claims") or {}
    print("\nDocument ingest complete:")
    print(f"  document_rid:   {result['document_rid']}")
    print(f"  slug / tier:    {result.get('slug')} / {result.get('tier')}")
    print(f"  char_count:     {result.get('char_count')}")
    print(f"  rag chunks:     {rag.get('chunks_written')} written / {rag.get('null_embeds')} null-embed")
    if ext:
        print(f"  facts_created:  {ext.get('facts_created')} (skipped {ext.get('facts_skipped')}, "
              f"dup_removed {ext.get('facts_dup_removed')})")
        print(f"  entities:       {ext.get('entities_created')} created / {ext.get('entities_resolved')} resolved")
        print(f"  type_mismatch:  {ext.get('type_mismatches')}  | windows {ext.get('windows_processed')}/{ext.get('windows_total')}")
    if cl:
        print(f"  claims_created: {cl.get('claims_created')}")


if __name__ == "__main__":
    main()
