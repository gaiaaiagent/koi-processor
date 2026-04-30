"""Knowledge graph endpoints — episodes and temporal facts.

Provides storage and retrieval for knowledge episodes (grouping unit)
and facts (searchable natural-language statements with entity references,
temporal validity, and pgvector embeddings).

Routes are prefix-relative — prefix "/knowledge" is applied at mount
in personal_ingest_api.py.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import time

import asyncpg
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class FactInput(BaseModel):
    subject: str = Field(..., description="Entity name for the subject")
    predicate: str = Field(..., description="Relationship type (UPPER_CASE)")
    object: Optional[str] = Field(None, description="Entity name for the object (if entity)")
    object_literal: Optional[str] = Field(None, description="Free text value (if not entity)")
    fact_text: str = Field(..., description="Natural language sentence")
    valid_from: Optional[str] = Field(None, description="ISO datetime when fact became true")
    valid_to: Optional[str] = Field(None, description="ISO datetime when fact stopped being true")


class EpisodeCreateRequest(BaseModel):
    name: str = Field(..., description="Episode title")
    content: Optional[str] = None
    source_description: Optional[str] = None
    source_document: Optional[str] = None
    group_id: str = "personal"
    valid_at: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    facts: List[FactInput] = Field(default_factory=list)
    create_entities: bool = Field(True, description="Create missing entities in entity_registry")


class FactRecord(BaseModel):
    id: str
    episode_id: Optional[str] = None
    episode_name: Optional[str] = None
    subject_uri: str
    subject_name: Optional[str] = None
    predicate: str
    object_uri: Optional[str] = None
    object_name: Optional[str] = None
    object_literal: Optional[str] = None
    fact_text: str
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    created_at: Optional[str] = None
    similarity: Optional[float] = None


class EpisodeCreateResponse(BaseModel):
    episode_id: str
    episode_reused: bool = False
    facts_created: int
    facts_skipped: int = 0
    facts_superseded: int = 0
    entities_resolved: int
    entities_created: int


class EpisodeRecord(BaseModel):
    id: str
    name: str
    content: Optional[str] = None
    source_description: Optional[str] = None
    source_document: Optional[str] = None
    group_id: Optional[str] = None
    valid_at: Optional[str] = None
    created_at: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    fact_count: Optional[int] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dt(val: Optional[str]) -> Optional[datetime]:
    """Parse an ISO datetime string, returning None on failure."""
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _row_to_dict(row) -> Dict[str, Any]:
    """Convert an asyncpg Record to a serializable dict."""
    from datetime import date, datetime as dt_type
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, (date, dt_type)):
            d[k] = v.isoformat()
        elif isinstance(v, UUID):
            d[k] = str(v)
    return d


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

EmbedFn = Callable[[str], Coroutine[Any, Any, Optional[List[float]]]]


def _parse_jsonb(value) -> Dict:
    """Safely parse a JSONB column value — handles both dict and string returns from asyncpg."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def create_router(
    pool,
    generate_embedding: Optional[EmbedFn] = None,
    *,
    generate_query_embedding: Optional[EmbedFn] = None,
    generate_document_embedding: Optional[EmbedFn] = None,
) -> APIRouter:
    """Return an APIRouter for knowledge graph endpoints.

    Parameters
    ----------
    pool : asyncpg.Pool
        Database connection pool.
    generate_embedding : callable, optional
        DEPRECATED fallback: text -> Optional[List[float]].
    generate_query_embedding : callable, optional
        QUERY mode embedding (with instruction prefix).
    generate_document_embedding : callable, optional
        DOCUMENT mode embedding (no instruction prefix).
    """
    # Resolve to explicit query/document or fall back to unified
    _query_embed = generate_query_embedding or generate_embedding
    _doc_embed = generate_document_embedding or generate_embedding
    router = APIRouter(tags=["knowledge"])

    def _facts_surface_available(request: Request) -> bool:
        return bool(getattr(request.app.state, "facts_surface_available", True))

    def _facts_surface_headers(request: Request) -> Dict[str, str]:
        return {
            "X-Facts-Surface": (
                "available" if _facts_surface_available(request) else "unavailable"
            )
        }

    # -------------------------------------------------------------------
    # POST /episodes — create episode with facts
    # -------------------------------------------------------------------
    @router.post("/episodes", response_model=EpisodeCreateResponse, status_code=201)
    async def create_episode(request: Request, body: EpisodeCreateRequest):
        if not _facts_surface_available(request):
            raise HTTPException(
                status_code=503,
                detail={"error": "facts surface not configured on this node"},
            )

        valid_at = _dt(body.valid_at)
        metadata = body.metadata or {}

        entities_resolved = 0
        entities_created = 0
        seen_uris: dict = {}  # cache name->uri within this request

        episode_reused = False

        async with pool.acquire() as conn:
            # 1. Check for existing episode by source_document (dedup)
            import json as json_mod
            episode_id = None
            if body.source_document:
                episode_id = await conn.fetchval("""
                    SELECT id FROM knowledge_episodes
                    WHERE source_document = $1
                    LIMIT 1
                """, body.source_document)

            if episode_id:
                episode_reused = True
                logger.info(
                    f"Reusing existing episode {episode_id} "
                    f"for source_document: {body.source_document}")
            else:
                episode_id = await conn.fetchval("""
                    INSERT INTO knowledge_episodes
                        (name, content, source_description, source_document,
                         group_id, valid_at, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                    RETURNING id
                """, body.name, body.content, body.source_description,
                    body.source_document, body.group_id, valid_at,
                    json_mod.dumps(metadata))

            # 2. Process each fact
            facts_created = 0
            facts_skipped = 0
            facts_superseded = 0
            for fact in body.facts:
                # Resolve subject
                subject_uri, is_new = await _resolve_or_create(
                    conn, fact.subject, body.create_entities,
                    _doc_embed, seen_uris)
                if not subject_uri:
                    logger.warning(f"Could not resolve subject: {fact.subject}")
                    continue
                entities_resolved += 1
                if is_new:
                    entities_created += 1

                # Resolve object (if entity name provided)
                object_uri = None
                if fact.object:
                    object_uri, obj_new = await _resolve_or_create(
                        conn, fact.object, body.create_entities,
                        _doc_embed, seen_uris)
                    if object_uri:
                        entities_resolved += 1
                        if obj_new:
                            entities_created += 1

                # Generate fact embedding
                fact_embedding = None
                if _doc_embed:
                    fact_embedding = await _doc_embed(fact.fact_text)

                # --- Dedup + invalidation ---
                # Reads from fact_embedding_3072 (post-migration 096); halfvec
                # cast required because pgvector full-precision indexes cap at
                # 2000 dims (see migration 097).
                if fact_embedding:
                    existing = await conn.fetch("""
                        SELECT id, fact_text, predicate, object_uri,
                               1 - (fact_embedding_3072::halfvec(3072)
                                    <=> $1::halfvec(3072)) AS similarity
                        FROM knowledge_facts
                        WHERE subject_uri = $2 AND valid_to IS NULL
                          AND fact_embedding_3072 IS NOT NULL
                        ORDER BY fact_embedding_3072::halfvec(3072)
                                 <=> $1::halfvec(3072)
                        LIMIT 5
                    """, str(fact_embedding), subject_uri)

                    # Check for near-duplicate (similarity > 0.95)
                    skip = False
                    for row in existing:
                        sim = float(row['similarity'])
                        if sim > 0.95:
                            logger.info(
                                f"Skipped duplicate fact: {fact.fact_text} "
                                f"(similarity: {sim:.3f} with fact {row['id']})")
                            facts_skipped += 1
                            skip = True
                            break

                    if skip:
                        continue

                    # Invalidation: same subject + same predicate + different object → retire old
                    predicate_upper = fact.predicate.upper()
                    for row in existing:
                        sim = float(row['similarity'])
                        if (row['predicate'] == predicate_upper
                                and row['object_uri'] != object_uri
                                and sim > 0.5):
                            await conn.execute("""
                                UPDATE knowledge_facts
                                SET valid_to = NOW()
                                WHERE id = $1
                            """, row['id'])
                            logger.info(
                                f"Superseded fact {row['id']}: "
                                f"{row['fact_text']} → {fact.fact_text}")
                            facts_superseded += 1

                await conn.execute("""
                    INSERT INTO knowledge_facts
                        (episode_id, subject_uri, predicate, object_uri,
                         object_literal, fact_text, fact_embedding_3072,
                         valid_from, valid_to, group_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7::vector, $8, $9, $10)
                """, episode_id, subject_uri, fact.predicate.upper(),
                    object_uri, fact.object_literal, fact.fact_text,
                    str(fact_embedding) if fact_embedding else None,
                    _dt(fact.valid_from), _dt(fact.valid_to), body.group_id)
                facts_created += 1

        return EpisodeCreateResponse(
            episode_id=str(episode_id),
            episode_reused=episode_reused,
            facts_created=facts_created,
            facts_skipped=facts_skipped,
            facts_superseded=facts_superseded,
            entities_resolved=entities_resolved,
            entities_created=entities_created,
        )

    async def _resolve_or_create(
        conn, name: str, create_if_missing: bool,
        embed_fn: Optional[EmbedFn],
        seen: dict,
    ) -> tuple[Optional[str], bool]:
        """Resolve entity name → (uri, is_new). Uses per-request cache."""
        from api.personal_ingest_api import (
            normalize_entity_text, generate_entity_uri
        )

        normalized = normalize_entity_text(name)

        # Per-request cache hit
        if normalized in seen:
            return seen[normalized], False

        # Tier 1: exact match on normalized_text
        uri = await conn.fetchval("""
            SELECT fuseki_uri FROM entity_registry
            WHERE normalized_text = $1
            LIMIT 1
        """, normalized)
        if uri:
            seen[normalized] = uri
            return uri, False

        # Tier 1b: case-insensitive alias match
        uri = await conn.fetchval("""
            SELECT fuseki_uri FROM entity_registry
            WHERE $1 = ANY(SELECT LOWER(unnest(aliases)))
            LIMIT 1
        """, normalized)
        if uri:
            seen[normalized] = uri
            return uri, False

        if not create_if_missing:
            return None, False

        # Tier 3: create new entity (minimal — name + type guess)
        entity_type = "Concept"  # default; caller can enrich later
        new_uri = generate_entity_uri(name, entity_type)

        embedding = None
        if embed_fn:
            embedding = await embed_fn(name)

        # Writes to embedding_3072 (post-migration 089); legacy embedding
        # column (1024) retained for rollback only — do not write to it.
        await conn.execute("""
            INSERT INTO entity_registry
                (fuseki_uri, entity_text, normalized_text, entity_type,
                 source, embedding_3072)
            VALUES ($1, $2, $3, $4, $5, $6::vector)
            ON CONFLICT (fuseki_uri) DO NOTHING
        """, new_uri, name, normalized, entity_type,
            'knowledge-add', str(embedding) if embedding else None)

        seen[normalized] = new_uri
        logger.info(f"Created new entity: {name} -> {new_uri}")
        return new_uri, True

    # -------------------------------------------------------------------
    # GET /facts/search — semantic search over facts
    # -------------------------------------------------------------------
    @router.get("/facts/search")
    async def search_facts(
        request: Request,
        query: str = Query(..., description="Search query"),
        limit: int = Query(10, ge=1, le=100),
        group_id: Optional[str] = Query(None),
        include_expired: bool = Query(False),
    ):
        if not _facts_surface_available(request):
            return JSONResponse(
                content={"facts": [], "count": 0},
                headers=_facts_surface_headers(request),
            )

        if not _query_embed:
            raise HTTPException(
                status_code=503,
                detail="Embedding provider not configured")

        query_embedding = await _query_embed(query)
        if not query_embedding:
            raise HTTPException(
                status_code=500,
                detail="Failed to generate query embedding")

        async with pool.acquire() as conn:
            validity_filter = "" if include_expired else "AND f.valid_to IS NULL"
            group_filter = "AND f.group_id = $3" if group_id else ""

            params: list = [str(query_embedding), limit]
            if group_id:
                params.append(group_id)

            rows = await conn.fetch(f"""
                SELECT f.id, f.episode_id, e.name AS episode_name,
                       f.subject_uri, f.predicate, f.object_uri,
                       f.object_literal, f.fact_text,
                       f.valid_from, f.valid_to, f.created_at,
                       1 - (f.fact_embedding_3072::halfvec(3072)
                            <=> $1::halfvec(3072)) AS similarity
                FROM knowledge_facts f
                LEFT JOIN knowledge_episodes e ON e.id = f.episode_id
                WHERE f.fact_embedding_3072 IS NOT NULL
                  {validity_filter}
                  {group_filter}
                ORDER BY f.fact_embedding_3072::halfvec(3072)
                         <=> $1::halfvec(3072)
                LIMIT $2
            """, *params)

            results = []
            for row in rows:
                d = _row_to_dict(row)
                # Resolve entity names for display
                d['subject_name'] = await _get_entity_name(conn, d.get('subject_uri'))
                d['object_name'] = await _get_entity_name(conn, d.get('object_uri'))
                results.append(d)

            return {"facts": results, "count": len(results)}

    # -------------------------------------------------------------------
    # GET /episodes — list/search episodes
    # -------------------------------------------------------------------
    @router.get("/episodes")
    async def list_episodes(
        request: Request,
        source_document: Optional[str] = Query(None),
        query: Optional[str] = Query(None),
        group_id: Optional[str] = Query(None),
        created_after: Optional[str] = Query(None, description="ISO datetime — only return episodes created after this timestamp"),
        limit: int = Query(20, ge=1, le=100),
    ):
        if not _facts_surface_available(request):
            return JSONResponse(
                content={"episodes": [], "count": 0},
                headers=_facts_surface_headers(request),
            )

        async with pool.acquire() as conn:
            conditions = []
            params: list = []
            idx = 1

            if source_document:
                conditions.append(f"e.source_document ILIKE ${idx}")
                params.append(f"%{source_document}%")
                idx += 1

            if query:
                conditions.append(f"(e.name ILIKE ${idx} OR e.content ILIKE ${idx})")
                params.append(f"%{query}%")
                idx += 1

            if group_id:
                conditions.append(f"e.group_id = ${idx}")
                params.append(group_id)
                idx += 1

            if created_after:
                ca_dt = _dt(created_after)
                if ca_dt:
                    conditions.append(f"e.created_at > ${idx}")
                    params.append(ca_dt)
                    idx += 1

            where = "WHERE " + " AND ".join(conditions) if conditions else ""
            params.append(limit)

            rows = await conn.fetch(f"""
                SELECT e.*, COUNT(f.id) AS fact_count
                FROM knowledge_episodes e
                LEFT JOIN knowledge_facts f ON f.episode_id = e.id
                {where}
                GROUP BY e.id
                ORDER BY e.created_at DESC
                LIMIT ${idx}
            """, *params)

            return {"episodes": [_row_to_dict(r) for r in rows],
                    "count": len(rows)}

    # -------------------------------------------------------------------
    # GET /entity/{uri}/facts — all facts for an entity
    # -------------------------------------------------------------------
    @router.get("/entity/{uri:path}/facts")
    async def entity_facts(
        request: Request,
        uri: str,
        include_expired: bool = Query(False),
        limit: int = Query(50, ge=1, le=200),
    ):
        if not _facts_surface_available(request):
            return JSONResponse(
                content={
                    "entity_uri": uri,
                    "entity_name": None,
                    "facts": [],
                    "count": 0,
                },
                headers=_facts_surface_headers(request),
            )

        async with pool.acquire() as conn:
            validity_filter = "" if include_expired else "AND f.valid_to IS NULL"

            rows = await conn.fetch(f"""
                SELECT f.id, f.episode_id, e.name AS episode_name,
                       f.subject_uri, f.predicate, f.object_uri,
                       f.object_literal, f.fact_text,
                       f.valid_from, f.valid_to, f.created_at
                FROM knowledge_facts f
                LEFT JOIN knowledge_episodes e ON e.id = f.episode_id
                WHERE (f.subject_uri = $1 OR f.object_uri = $1)
                  {validity_filter}
                ORDER BY f.valid_from DESC NULLS LAST
                LIMIT $2
            """, uri, limit)

            results = []
            for row in rows:
                d = _row_to_dict(row)
                d['subject_name'] = await _get_entity_name(conn, d.get('subject_uri'))
                d['object_name'] = await _get_entity_name(conn, d.get('object_uri'))
                results.append(d)

            entity_name = await _get_entity_name(conn, uri)
            return {"entity_uri": uri, "entity_name": entity_name,
                    "facts": results, "count": len(results)}

    # -------------------------------------------------------------------
    # GET /unified-search — RRF fusion over entities, facts, sessions, docs
    # -------------------------------------------------------------------
    @router.get("/unified-search")
    async def unified_search(
        request: Request,
        response: Response,
        query: str = Query(..., description="Search query"),
        limit: int = Query(10, ge=1, le=50),
        include: str = Query(
            "entities,facts,sessions,wiki,vault",
            description="Comma-separated surfaces to query: entities,facts,sessions,docs,wiki,vault"),
        doc_kind: Optional[str] = Query(None, description="Filter docs by doc_kind (e.g. architecture, spec, operations)"),
        status: Optional[str] = Query(None, description="Filter docs by status (e.g. active, draft)"),
        is_governed: Optional[bool] = Query(None, description="Filter docs by governed flag (has doc_id)"),
        repo: Optional[str] = Query(None, description="Filter docs by repo name (e.g. darren-workflow)"),
    ):
        # Tier-2 instrumentation: per-route latency_ms (Step 6).
        _t_route_start = time.monotonic()

        surfaces = [s.strip() for s in include.split(",")]
        k = 60  # RRF constant
        facts_surface_available = _facts_surface_available(request)
        response.headers.update(_facts_surface_headers(request))

        # ── Attempt embedding; degrade gracefully on any failure ──────
        degraded = False
        degraded_reason: Optional[str] = None
        query_embedding: Optional[List[float]] = None

        if not _query_embed:
            degraded = True
            degraded_reason = "embedding_unavailable"
            logger.warning(
                "unified-search degraded: no embedding provider configured")
        else:
            try:
                query_embedding = await _query_embed(query)
                if not query_embedding:
                    degraded = True
                    degraded_reason = "embedding_failed"
                    logger.warning(
                        "unified-search degraded: embedding returned None")
            except Exception as exc:
                degraded = True
                degraded_reason = "embedding_failed"
                logger.warning(
                    "unified-search degraded: embedding raised %s", exc)

        all_results: list[dict] = []
        facts_results: list[dict] = []

        async with pool.acquire() as conn:
            if degraded:
                # ── Text-first fallback (no vectors) ─────────────────
                words = [w for w in query.lower().split() if len(w) >= 3]
                if words:
                    # Entities: ILIKE on normalized_text (prefer shorter names)
                    if "entities" in surfaces:
                        conditions = " OR ".join(
                            f"normalized_text ILIKE ${i + 1}"
                            for i in range(len(words)))
                        e_params: list = [f"%{w}%" for w in words] + [20]
                        rows = await conn.fetch(f"""
                            SELECT fuseki_uri, entity_text, entity_type
                            FROM entity_registry
                            WHERE ({conditions}) AND NOT node_private
                            ORDER BY LENGTH(entity_text)
                            LIMIT ${len(words) + 1}
                        """, *e_params)
                        for rank, row in enumerate(rows):
                            all_results.append({
                                "text": row["entity_text"],
                                "score": 1.0 / (k + rank + 1),
                                "source": "entity",
                                "type": row["entity_type"],
                                "uri": row["fuseki_uri"],
                                "metadata": {"match_mode": "text"},
                            })

                    # Facts: ILIKE on fact_text (offset to rank below entities)
                    if "facts" in surfaces and facts_surface_available:
                        conditions = " OR ".join(
                            f"fact_text ILIKE ${i + 1}"
                            for i in range(len(words)))
                        f_params: list = [f"%{w}%" for w in words] + [20]
                        rows = await conn.fetch(f"""
                            SELECT f.id, f.subject_uri, f.predicate,
                                   f.object_uri, f.fact_text,
                                   e.name AS episode_name
                            FROM knowledge_facts f
                            LEFT JOIN knowledge_episodes e
                                   ON f.episode_id = e.id
                            WHERE ({conditions}) AND f.valid_to IS NULL
                            ORDER BY f.created_at DESC
                            LIMIT ${len(words) + 1}
                        """, *f_params)
                        for rank, row in enumerate(rows):
                            fact_result = {
                                "text": row["fact_text"],
                                "score": 1.0 / (k + 20 + rank + 1),
                                "source": "fact",
                                "episode": row["episode_name"],
                                "metadata": {
                                    "subject": row["subject_uri"],
                                    "predicate": row["predicate"],
                                    "object": row["object_uri"],
                                    "match_mode": "text",
                                },
                            }
                            facts_results.append(fact_result)
                            all_results.append(fact_result)
                    else:
                        facts_results = []

                    # Sessions: ILIKE on chunk_text (lowest priority)
                    if "sessions" in surfaces:
                        table_exists = await conn.fetchval("""
                            SELECT EXISTS (
                                SELECT FROM information_schema.tables
                                WHERE table_name = 'session_chunks'
                            )
                        """)
                        if table_exists:
                            conditions = " OR ".join(
                                f"chunk_text ILIKE ${i + 1}"
                                for i in range(len(words)))
                            s_params: list = [f"%{w}%" for w in words] + [20]
                            rows = await conn.fetch(f"""
                                SELECT sc.id, sc.session_id, sc.chunk_text
                                FROM session_chunks sc
                                WHERE ({conditions})
                                ORDER BY sc.created_at DESC
                                LIMIT ${len(words) + 1}
                            """, *s_params)
                            for rank, row in enumerate(rows):
                                all_results.append({
                                    "text": row["chunk_text"][:500],
                                    "score": 1.0 / (k + 40 + rank + 1),
                                    "source": "session",
                                    "session_id": row["session_id"],
                                    "metadata": {"match_mode": "text"},
                                })

                    # Docs: ILIKE on chunk text from doc-scanner (lowest priority after sessions)
                    if "docs" in surfaces:
                        conditions = " OR ".join(
                            f"(mc.content->>'text') ILIKE ${i + 1}"
                            for i in range(len(words)))
                        d_filter = ""
                        d_params: list = [f"%{w}%" for w in words]
                        if doc_kind:
                            d_params.append(doc_kind)
                            d_filter += f" AND mc.metadata->>'doc_kind' = ${len(d_params)}"
                        if status:
                            d_params.append(status)
                            d_filter += f" AND mc.metadata->>'status' = ${len(d_params)}"
                        if is_governed is not None:
                            d_params.append(str(is_governed).lower())
                            d_filter += f" AND mc.metadata->>'is_governed' = ${len(d_params)}"
                        if repo:
                            d_params.append(repo)
                            d_filter += f" AND mc.metadata->>'repo' = ${len(d_params)}"
                        d_params.append(20)
                        rows = await conn.fetch(f"""
                            SELECT mc.chunk_rid, mc.content->>'text' AS chunk_text,
                                   mc.metadata
                            FROM koi_memory_chunks mc
                            WHERE ({conditions})
                              AND mc.metadata->>'repo' IS NOT NULL
                              {d_filter}
                            ORDER BY mc.created_at DESC
                            LIMIT ${len(d_params)}
                        """, *d_params)
                        for rank, row in enumerate(rows):
                            meta = _parse_jsonb(row["metadata"])
                            meta["match_mode"] = "text"
                            all_results.append({
                                "text": row["chunk_text"][:500],
                                "score": 1.0 / (k + 60 + rank + 1),
                                "source": "doc",
                                "doc_id": meta.get("doc_id"),
                                "doc_kind": meta.get("doc_kind"),
                                "repo": meta.get("repo"),
                                "metadata": meta,
                            })

            else:
                # ── Normal semantic RRF mode ──────────────────────────
                emb_str = str(query_embedding)

                # Raise ivfflat.probes so recently-inserted chunks in
                # non-nearest centroids are still considered. Default probes=1
                # misses new rows until the index is rebuilt. Session-level
                # SET persists on the pooled connection, which is fine — we
                # always want higher recall on retrieval paths.
                await conn.execute("SET ivfflat.probes = 10")

                # Entities (vector similarity, exclude private)
                # OpenAI text-embedding-3-large @ 3072-dim via halfvec HNSW index.
                # Rollback: see migrations 089/090 + config/personal.env.
                facts_results = []
                if "entities" in surfaces:
                    rows = await conn.fetch("""
                        SELECT fuseki_uri, entity_text, entity_type,
                               1 - (embedding_3072::halfvec(3072) <=> $1::halfvec(3072)) AS score
                        FROM entity_registry
                        WHERE embedding_3072 IS NOT NULL AND NOT node_private
                        ORDER BY embedding_3072::halfvec(3072) <=> $1::halfvec(3072)
                        LIMIT 20
                    """, emb_str)
                    for rank, row in enumerate(rows):
                        all_results.append({
                            "text": row["entity_text"],
                            "score": 1.0 / (k + rank + 1),
                            "source": "entity",
                            "type": row["entity_type"],
                            "uri": row["fuseki_uri"],
                            "metadata": {"vector_score": float(row["score"])},
                        })

                # Facts (vector similarity, joined with episodes).
                # Reads from fact_embedding_3072 (post-migration 096); halfvec
                # cast required because pgvector full-precision indexes cap at
                # 2000 dims (see migration 097). Old 1024-dim rows backfilled
                # NULL — they're filtered by the IS NOT NULL guard.
                if "facts" in surfaces and facts_surface_available:
                    try:
                        rows = await conn.fetch("""
                            SELECT f.id, f.subject_uri, f.predicate, f.object_uri,
                                   f.fact_text, e.name AS episode_name,
                                   1 - (f.fact_embedding_3072::halfvec(3072)
                                        <=> $1::halfvec(3072)) AS score
                            FROM knowledge_facts f
                            LEFT JOIN knowledge_episodes e ON f.episode_id = e.id
                            WHERE f.valid_to IS NULL
                              AND f.fact_embedding_3072 IS NOT NULL
                            ORDER BY f.fact_embedding_3072::halfvec(3072)
                                     <=> $1::halfvec(3072)
                            LIMIT 20
                        """, emb_str)
                    except asyncpg.exceptions.DataError as e:
                        logger.warning("facts surface vector query skipped: %s", e)
                        rows = []
                    for rank, row in enumerate(rows):
                        fact_result = {
                            "text": row["fact_text"],
                            "score": 1.0 / (k + rank + 1),
                            "source": "fact",
                            "episode": row["episode_name"],
                            "metadata": {
                                "subject": row["subject_uri"],
                                "predicate": row["predicate"],
                                "object": row["object_uri"],
                                "vector_score": float(row["score"]),
                            },
                        }
                        facts_results.append(fact_result)
                        all_results.append(fact_result)

                # Sessions (HYBRID retrieval: pgvector dense + tsvector lexical
                # fused by RRF inside SQL).
                #
                # P2 hybrid refactor (plan session-recall-tier-1-expanded
                # 2026-04-28). Two ranked legs:
                #   (a) pgvector cosine over embedding_3072 (OpenAI 3072-dim,
                #       text-embedding-3-large; HNSW halfvec(3072) index)
                #   (b) tsvector ts_rank_cd over chunk_tsv (GENERATED STORED
                #       to_tsvector('english', chunk_text); GIN index)
                #
                # Reciprocal Rank Fusion combines per-leg ranks at k=60 (Octo
                # Pattern B6). Each leg fetches top 100 chunks; FULL OUTER JOIN
                # on chunk id yields the union; rrf_score sums the two
                # contributions. Caller sees a single ranked list of top 20
                # chunks, with internal vector_score + lex_score for diagnostics.
                #
                # Lexical query: convert user query to OR-disjunctive
                # websearch_to_tsquery to maximize partial-match recall on
                # natural-language queries (e.g. "When did F2 transition...");
                # AND-conjunctive plainto_tsquery is too restrictive for the
                # benchmark's recall-shape.
                if "sessions" in surfaces:
                    table_exists = await conn.fetchval("""
                        SELECT EXISTS (
                            SELECT FROM information_schema.tables
                            WHERE table_name = 'session_chunks'
                        )
                    """)
                    rows = []
                    if table_exists:
                        # Build OR-disjunctive tsquery string from user query.
                        # Hyphens preserved (websearch handles ADR-0080 etc).
                        # Stopwords filtered to reduce 0-recall on natural-
                        # language phrasings.
                        _stopwords = {
                            "the", "what", "when", "where", "how", "why",
                            "which", "who", "is", "are", "was", "were", "be",
                            "been", "being", "do", "did", "does", "done",
                            "can", "could", "should", "would", "may", "might",
                            "must", "shall", "will", "or", "and", "but",
                            "not", "of", "to", "for", "on", "at", "in", "by",
                            "with", "from", "as", "into", "that", "this",
                            "these", "those", "there", "here", "then", "than",
                            "such", "also", "very", "more", "most", "just",
                            "only", "over", "under", "have", "has", "had",
                        }
                        _toks = re.findall(r"[A-Za-z0-9_-]{2,}", query.lower())
                        _toks = [t for t in _toks if t not in _stopwords]
                        ts_query_str = " OR ".join(_toks) if _toks else query

                        try:
                            rows = await conn.fetch("""
                                WITH vec_ranked AS (
                                    SELECT id, session_id, chunk_text,
                                           ROW_NUMBER() OVER (
                                               ORDER BY embedding_3072::halfvec(3072) <=> $1::halfvec(3072)
                                           ) AS rnk,
                                           1 - (embedding_3072::halfvec(3072) <=> $1::halfvec(3072)) AS vec_score
                                    FROM session_chunks
                                    WHERE embedding_3072 IS NOT NULL
                                    ORDER BY embedding_3072::halfvec(3072) <=> $1::halfvec(3072)
                                    LIMIT 100
                                ),
                                lex_ranked AS (
                                    SELECT sc.id, sc.session_id, sc.chunk_text,
                                           ROW_NUMBER() OVER (
                                               ORDER BY ts_rank_cd(sc.chunk_tsv, q) DESC
                                           ) AS rnk,
                                           ts_rank_cd(sc.chunk_tsv, q) AS lex_score
                                    FROM session_chunks sc,
                                         websearch_to_tsquery('english', $2) q
                                    WHERE sc.chunk_tsv @@ q
                                    ORDER BY ts_rank_cd(sc.chunk_tsv, q) DESC
                                    LIMIT 100
                                ),
                                fused AS (
                                    -- Unweighted RRF (1.0 / (k + rank) per leg).
                                    -- Tuning attempt (2026-04-28): tried vector-leg 1.5×
                                    -- to recover MRR drop on q01/q03 hits; result was
                                    -- recall regression (0.318 → 0.200) because higher
                                    -- vector weight displaced lex-rescued sessions
                                    -- (q01's 585633a5 found via vec_rank=20 + lex_rank=8).
                                    -- Reverted to unweighted; B2 0.318 is the optimal
                                    -- balance for this corpus.
                                    SELECT
                                        COALESCE(v.id, l.id)            AS id,
                                        COALESCE(v.session_id, l.session_id) AS session_id,
                                        COALESCE(v.chunk_text, l.chunk_text) AS chunk_text,
                                        v.vec_score                      AS vec_score,
                                        l.lex_score                      AS lex_score,
                                        v.rnk                            AS vec_rank,
                                        l.rnk                            AS lex_rank,
                                        COALESCE(1.0 / (60 + v.rnk), 0)
                                        + COALESCE(1.0 / (60 + l.rnk), 0) AS rrf_score
                                    FROM vec_ranked v
                                    FULL OUTER JOIN lex_ranked l USING (id)
                                )
                                SELECT id, session_id, chunk_text,
                                       vec_score, lex_score, vec_rank, lex_rank, rrf_score
                                FROM fused
                                ORDER BY rrf_score DESC
                                LIMIT 20
                            """, emb_str, ts_query_str)
                        except asyncpg.exceptions.DataError as e:
                            logger.warning("sessions surface hybrid query skipped: %s", e)
                            rows = []
                        for rank, row in enumerate(rows):
                            all_results.append({
                                "text": (row["chunk_text"] or "")[:500],
                                "score": 1.0 / (k + rank + 1),
                                "source": "session",
                                "session_id": row["session_id"],
                                "metadata": {
                                    "vec_score": float(row["vec_score"]) if row["vec_score"] is not None else None,
                                    "lex_score": float(row["lex_score"]) if row["lex_score"] is not None else None,
                                    "vec_rank": int(row["vec_rank"]) if row["vec_rank"] is not None else None,
                                    "lex_rank": int(row["lex_rank"]) if row["lex_rank"] is not None else None,
                                    "rrf_score": float(row["rrf_score"]),
                                },
                            })

                # Docs (vector similarity on koi_memory_chunks from doc-scanner)
                if "docs" in surfaces:
                    d_filter = ""
                    d_params_vec: list = [emb_str]
                    if doc_kind:
                        d_params_vec.append(doc_kind)
                        d_filter += f" AND mc.metadata->>'doc_kind' = ${len(d_params_vec)}"
                    if status:
                        d_params_vec.append(status)
                        d_filter += f" AND mc.metadata->>'status' = ${len(d_params_vec)}"
                    if is_governed is not None:
                        d_params_vec.append(str(is_governed).lower())
                        d_filter += f" AND mc.metadata->>'is_governed' = ${len(d_params_vec)}"
                    if repo:
                        d_params_vec.append(repo)
                        d_filter += f" AND mc.metadata->>'repo' = ${len(d_params_vec)}"
                    rows = await conn.fetch(f"""
                        SELECT mc.chunk_rid,
                               mc.content->>'text' AS chunk_text,
                               mc.metadata,
                               1 - (mc.embedding_3072::halfvec(3072) <=> $1::halfvec(3072)) AS score
                        FROM koi_memory_chunks mc
                        WHERE mc.embedding_3072 IS NOT NULL
                          AND mc.metadata->>'repo' IS NOT NULL
                          {d_filter}
                        ORDER BY mc.embedding_3072::halfvec(3072) <=> $1::halfvec(3072)
                        LIMIT 20
                    """, *d_params_vec)
                    for rank, row in enumerate(rows):
                        meta = _parse_jsonb(row["metadata"])
                        meta["vector_score"] = float(row["score"])
                        all_results.append({
                            "text": row["chunk_text"][:500],
                            "score": 1.0 / (k + rank + 1),
                            "source": "doc",
                            "doc_id": meta.get("doc_id"),
                            "doc_kind": meta.get("doc_kind"),
                            "repo": meta.get("repo"),
                            "metadata": meta,
                        })

                # Wiki (vector similarity on koi_memory_chunks from mediawiki-sensor)
                if "wiki" in surfaces:
                    rows = await conn.fetch("""
                        SELECT mc.chunk_rid,
                               mc.document_rid,
                               mc.content->>'text' AS chunk_text,
                               mc.content->>'title' AS title,
                               mc.content->>'wiki_url' AS wiki_url,
                               mc.content->>'section_title' AS section_title,
                               1 - (mc.embedding_3072::halfvec(3072) <=> $1::halfvec(3072)) AS score
                        FROM koi_memory_chunks mc
                        WHERE mc.embedding_3072 IS NOT NULL
                          AND mc.document_rid LIKE 'mediawiki:%'
                        ORDER BY mc.embedding_3072::halfvec(3072) <=> $1::halfvec(3072)
                        LIMIT 20
                    """, emb_str)
                    for rank, row in enumerate(rows):
                        all_results.append({
                            "text": (row["chunk_text"] or "")[:500],
                            "score": 1.0 / (k + rank + 1),
                            "source": "wiki",
                            "title": row["title"],
                            "section_title": row["section_title"],
                            "wiki_url": row["wiki_url"],
                            "document_rid": row["document_rid"],
                            "chunk_rid": row["chunk_rid"],
                            "metadata": {"vector_score": float(row["score"])},
                        })

        # Vault BM25 (pageindex — Mac only, graceful skip if venv not present)
        if "vault" in surfaces:
            _venv_py = os.path.expanduser(
                "~/.claude/local/darren-workflow/pageindex/venv/bin/python3")
            _script = os.path.expanduser(
                "~/projects/darren-workflow/scripts/pageindex.py")
            if os.path.exists(_venv_py) and os.path.exists(_script):
                try:
                    proc = await asyncio.create_subprocess_exec(
                        _venv_py, _script, "query", query,
                        "--json", "--limit", "10",
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    )
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
                    if stdout:
                        pi_data = json.loads(stdout)
                        k_vault = 60
                        for rank, hit in enumerate(pi_data.get("results", [])):
                            all_results.append({
                                "text": hit.get("snippet") or hit.get("title", ""),
                                "score": 1.0 / (k_vault + rank + 1),
                                "source": "vault",
                                "title": hit.get("title"),
                                "path": hit.get("path"),
                                "folder": hit.get("folder"),
                                "metadata": {
                                    "bm25_score": hit.get("score"),
                                    "match_mode": "bm25",
                                },
                            })
                except Exception as _e:
                    logger.warning("unified-search vault surface failed: %s", _e)

        # Sort by RRF score descending, take top N
        all_results.sort(key=lambda x: x["score"], reverse=True)
        all_results = all_results[:limit]

        # Tier-2: latency_ms field on response (Step 6 instrumentation).
        _latency_ms = round((time.monotonic() - _t_route_start) * 1000, 1)

        response: dict = {
            "results": all_results,
            "facts": facts_results,
            "query": query,
            "surfaces_queried": surfaces,
            "total_results": len(all_results),
            "embedding_available": not degraded,
            "latency_ms": _latency_ms,
        }
        if degraded:
            response["degraded"] = True
            response["degraded_reason"] = degraded_reason
        return response

    # -------------------------------------------------------------------
    # Shared helper
    # -------------------------------------------------------------------
    async def _get_entity_name(conn, uri: Optional[str]) -> Optional[str]:
        if not uri:
            return None
        return await conn.fetchval(
            "SELECT entity_text FROM entity_registry WHERE fuseki_uri = $1",
            uri)

    return router
