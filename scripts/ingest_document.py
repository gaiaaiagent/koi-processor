#!/usr/bin/env python3
"""
Arbitrary-document ingestion into KOI — unified depth-dial orchestrator.

The unified "thorough content ingestion" capability
(`~/.claude/plans/plan-a-unified-thorough-wiggly-plum.md`).

Unlike `doc_scanner.py` (which is scoped to governed-repo markdown — requires
YAML frontmatter + a `doc_id` + a repo path), this module ingests ANY markdown
text under a content-addressed RID, with no frontmatter/repo requirement. It
reuses `TextChunker` + `OpenAIEmbeddingProvider` verbatim and writes the same
`koi_memories` + `koi_memory_chunks` (embedding_3072) tables, namespaced by
`source_sensor='document-ingest'` and `rid='document:<sha256(markdown)>'`.

`doc_scanner.py` and its `is_governed()` gate are intentionally left untouched.

Tiers (depth dial): `rag` = chunk + embed → RAG-searchable full text; `standard`
= rag + facts (deep-extract) + impact claims; `thorough` = standard + discourse
moves (Phase 3, written to session_discourse_moves source_type='document'). Every
stage is idempotent and content-addressed by `document_rid`. The run emits a flat
gate-evidence `response_summary` (`--gate-evidence-out`) the document-ingest
completion gate asserts on.

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


def effective_field_membership(group_id: str, fields: Optional[List[str]]) -> List[str]:
    """Compute the dedup(union([group_id] + fields)) membership set (Piece B / G2).

    Contract: `group_id` is ALWAYS the primary membership row (first, never
    dropped); `--fields` only ADDS rows. Order-preserving dedup keeps group_id
    first. Empty/whitespace tokens are filtered. When `fields` is None/empty the
    result is exactly `[group_id]` — byte-identical to the pre-`--fields` behavior.
    Unknown field IDs are valid by design (fields are created-by-use; no registry).
    """
    primary = (group_id or "personal").strip() or "personal"
    effective: List[str] = [primary]
    for raw in (fields or []):
        fid = (raw or "").strip()
        if fid and fid not in effective:
            effective.append(fid)
    return effective


async def upsert_field_membership(
    conn: asyncpg.Connection,
    document_rid: str,
    source_meta: Dict[str, Any],
) -> str:
    """Record this document's membership in the run's learning field(s).

    Option B multi-field membership: chunk rows are content-addressed and
    field-agnostic (the chunk DELETE+reinsert-by-rid is harmless), so field
    membership lives in this separate, ADDITIVE, authoritative layer. Re-ingesting
    a doc into field B must NOT drop its field-A membership — hence INSERT ...
    ON CONFLICT DO NOTHING and NEVER a delete-by-rid here.

    Piece B (`--fields`): the effective set = dedup(union([group_id] + fields)).
    With no `--fields` this collapses to exactly `{group_id}` (unchanged). Each
    field is inserted additively; duplicates collapse via the dedup + ON CONFLICT.
    """
    effective = effective_field_membership(
        source_meta.get("group_id", "personal"), source_meta.get("fields"))
    for field_id in effective:
        await conn.execute(
            """
            INSERT INTO document_field_membership (document_rid, field_id, added_by)
            VALUES ($1, $2, 'ingest')
            ON CONFLICT DO NOTHING
            """,
            document_rid, field_id,
        )
    # Return the PRIMARY field (group_id) — stable, backward-compatible str
    # contract. The full effective set is `effective` (all rows were inserted).
    return effective[0]


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
            # Additive field membership (Option B): never deletes other fields' rows.
            await upsert_field_membership(conn, document_rid, source_meta)

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


# /claims/extract caps its input at ~10K chars (api/claim_extractor.py), so a single
# call on a long document only sees the intro. Window the text so the WHOLE document
# is covered; server-side content_hash dedup collapses overlap. Windows run with
# bounded concurrency. Auth failures (401/403) are raised LOUDLY — a silent auth fail
# is the 0-claims failure mode that bit project_bridge_notes.py; a per-window blip is
# logged and counted 0 so one transient error never kills the claims stage.
CLAIMS_WINDOW_CHARS = int(os.getenv("DOC_CLAIMS_WINDOW_CHARS", "9000"))
CLAIMS_WINDOW_OVERLAP = int(os.getenv("DOC_CLAIMS_WINDOW_OVERLAP", "500"))
CLAIMS_MAX_CONCURRENCY = int(os.getenv("DOC_CLAIMS_MAX_CONCURRENCY", "4"))


async def extract_document_claims(
    http: httpx.AsyncClient, *, document_text: str, source_document: str,
    confidence_threshold: float = 0.7,
) -> Dict[str, Any]:
    """Extract impact claims over the FULL document via windowed POSTs to
    /claims/extract (auth-gated). The endpoint truncates to ~10K chars internally, so
    we window the text ourselves (overlap + server content_hash dedup) — otherwise only
    the first 10K (the intro) is ever seen. The completion gate asserts claims_created
    >= 1 for standard/thorough; the windowing is what makes that count reflect the
    whole document."""
    token = _claims_service_token()
    if not token:
        raise RuntimeError(
            "KOI_CLAIMS_SERVICE_TOKEN not found (env or "
            "~/.config/personal-koi/koi-state/claims_service_token) — /claims/extract is auth-gated.")
    headers = {"Authorization": f"Bearer {token}"}

    stride = max(1, CLAIMS_WINDOW_CHARS - CLAIMS_WINDOW_OVERLAP)
    windows = [document_text[i:i + CLAIMS_WINDOW_CHARS]
               for i in range(0, len(document_text), stride)] or [document_text]
    sem = asyncio.Semaphore(CLAIMS_MAX_CONCURRENCY)

    async def _one(chunk: str) -> int:
        async with sem:
            payload = {"document_text": chunk, "source_document": source_document,
                       "auto_create": True, "confidence_threshold": confidence_threshold}
            try:
                r = await http.post(f"{KOI_BASE_URL}/claims/extract", json=payload,
                                    headers=headers, timeout=240.0)
            except Exception as e:  # noqa: BLE001 — a transient blip on one window != stage failure
                logger.warning("claims window error (counted 0): %s", e)
                return 0
            if r.status_code in (401, 403):
                raise RuntimeError(
                    f"/claims/extract auth failed ({r.status_code}); token rejected. NOT swallowing — "
                    f"a silent {r.status_code} yields 0 claims with no error.")
            if r.status_code >= 400:
                logger.warning("claims window non-2xx %s (counted 0): %s", r.status_code, r.text[:120])
                return 0
            data = r.json()
            created = data.get("auto_created")
            if created is None:
                created = data.get("claims_created", [])
            return len(created) if isinstance(created, list) else int(created or 0)

    counts = await asyncio.gather(*(_one(w) for w in windows))
    return {"claims_created": sum(counts), "claims_windows": len(windows)}


def _load_extractor():
    """Import the Phase-1 document extractor module by path (sibling script)."""
    import importlib.util
    p = Path(__file__).parent / "extract_deep_documents.py"
    spec = importlib.util.spec_from_file_location("extract_deep_documents", str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_gate_evidence(result: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten the nested ingest result into the gate's response_summary — the flat
    set of counts the document-ingest completion gate asserts on. `embeds_ok`/`dups_ok`
    are pre-derived booleans (1=ok) so the gate can express the "must be zero" checks
    as plain floors (a gate evaluator with only >= is enough)."""
    rag = result.get("rag") or {}
    ext = result.get("extract") or {}
    cl = result.get("claims") or {}
    rag_null = int(rag.get("null_embeds") or 0)
    facts_null = int(ext.get("facts_null_embed") or 0)
    dups = int(ext.get("facts_dup_removed") or 0)
    ent_created = int(ext.get("entities_created") or 0)
    ent_resolved = int(ext.get("entities_resolved") or 0)
    return {
        "tier": result.get("tier"),
        "document_rid": result.get("document_rid"),
        "chunks_written": int(rag.get("chunks_written") or 0),
        "chunks_total": int(rag.get("chunks_total") or 0),
        "rag_null_embeds": rag_null,
        "facts_created": int(ext.get("facts_created") or 0),
        "facts_skipped": int(ext.get("facts_skipped") or 0),
        "facts_dup_removed": dups,
        "semantic_dups_retracted": int(ext.get("semantic_dups_retracted") or 0),
        "facts_null_embed": facts_null,
        "entities_created": ent_created,
        "entities_resolved": ent_resolved,
        "entities_total": ent_created + ent_resolved,
        "entity_links": int(ext.get("entity_links") or 0),
        "type_mismatches": int(ext.get("type_mismatches") or 0),
        "discourse_moves_created": int(ext.get("discourse_moves_created") or 0),
        "claims_created": int(cl.get("claims_created") or 0),
        # DERIVED keys the document-ingest gate actually asserts on. Added 2026-07-31:
        # phase_expectations.yaml floors on `facts_available` and `claims_available`, but
        # this function never emitted them, so the gate reported "key ABSENT from evidence"
        # and exited 2 for EVERY standard/thorough ingest — including runs that landed
        # hundreds of facts and discourse moves with zero null embeds. The gate was
        # effectively dead above `rag` tier. Derivations follow the catalog's own comments:
        #   facts_available  = "facts created or intentionally skipped as known duplicates"
        #   claims_available = "impact claims or document-argument claims" (discourse moves
        #                      ARE the document-argument taxonomy, per migrations 103/104)
        # Purely additive — no existing key changes — so nothing downstream can regress.
        # 1 = the whole document was windowed; 0 = an explicit DOC_MAX_WINDOWS cap cut the
        # tail off. Truncation previously passed every floor, so a half-ingested book could
        # be reported complete. Gate floors on this.
        "not_truncated": 0 if ext.get("budget_exhausted") else 1,
        # 1 = every window extracted; 0 = one or more were dead-lettered (#40). A
        # document that lost windows must not pass as complete.
        "all_windows_ok": 0 if int(ext.get("windows_failed") or 0) > 0 else 1,
        "windows_failed": int(ext.get("windows_failed") or 0),
        "facts_available": int(ext.get("facts_created") or 0) + int(ext.get("facts_skipped") or 0),
        "claims_available": int(cl.get("claims_created") or 0)
                            + int(ext.get("discourse_moves_created") or 0),
        "embeds_ok": 1 if (rag_null == 0 and facts_null == 0) else 0,
        "dups_ok": 1 if dups == 0 else 0,
        # End-state invariant (the right "0 residual dups" gate floor): 1 = no duplicate
        # triples remain after the sweeps; 0 only if a sweep was skipped/broken.
        "no_residual_dups": int(ext.get("no_residual_dups") if ext.get("no_residual_dups") is not None else 1),
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
    fields: Optional[List[str]] = None,
    claims: bool = True,
    force: bool = False,
    dry_run: bool = False,
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
        "fields": list(fields) if fields else [],
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
            result["gate_evidence"] = build_gate_evidence(result)
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
    result["gate_evidence"] = build_gate_evidence(result)
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
    parser.add_argument("--group-id", default="personal", help="Learning field / group_id (always the primary membership)")
    parser.add_argument("--fields",
                        help="Comma-separated ADDITIONAL field IDs for multi-field membership "
                             "(Option B). Effective membership = dedup(union of --group-id + these). "
                             "--group-id is always primary; omit to keep single-membership behavior. "
                             "Unknown field IDs are valid (fields are created-by-use).")
    parser.add_argument("--no-claims", action="store_true", help="Skip the claims stage (standard/thorough)")
    parser.add_argument("--force", action="store_true", help="Re-extract all windows (ignore cache)")
    parser.add_argument("--dry-run", action="store_true", help="Chunk + report without writing/embedding")
    parser.add_argument("--gate-evidence-out",
                        help="Write the flat gate-evidence JSON (response_summary) to this path for the gate")
    args = parser.parse_args()

    claims = (args.tier != "rag") and not args.no_claims
    fields = [t.strip() for t in (args.fields or "").split(",") if t.strip()]
    try:
        result = asyncio.run(ingest_path(
            source_path=args.source_path, tier=args.tier, slug=args.slug, name=args.name,
            source_url=args.source_url, retrieval_method=args.retrieval_method,
            group_id=args.group_id, fields=fields, claims=claims, force=args.force, dry_run=args.dry_run,
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
        if result.get("tier") == "thorough":
            print(f"  discourse_moves:{ext.get('discourse_moves_created')}")
    if cl:
        print(f"  claims_created: {cl.get('claims_created')}")
    if args.gate_evidence_out:
        ev = result.get("gate_evidence") or build_gate_evidence(result)
        Path(args.gate_evidence_out).write_text(json.dumps(ev, indent=2))
        print(f"  gate-evidence:  {args.gate_evidence_out}")


if __name__ == "__main__":
    main()
