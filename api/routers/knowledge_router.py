"""Knowledge graph endpoints — episodes and temporal facts.

Provides storage and retrieval for knowledge episodes (grouping unit)
and facts (searchable natural-language statements with entity references,
temporal validity, and pgvector embeddings).

Routes are prefix-relative — prefix "/knowledge" is applied at mount
in personal_ingest_api.py.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
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
    facts_created: int
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


def create_router(pool, generate_embedding: Optional[EmbedFn] = None) -> APIRouter:
    """Return an APIRouter for knowledge graph endpoints.

    Parameters
    ----------
    pool : asyncpg.Pool
        Database connection pool.
    generate_embedding : callable, optional
        Async function: text -> Optional[List[float]].
    """
    router = APIRouter(tags=["knowledge"])

    # -------------------------------------------------------------------
    # POST /episodes — create episode with facts
    # -------------------------------------------------------------------
    @router.post("/episodes", response_model=EpisodeCreateResponse, status_code=201)
    async def create_episode(body: EpisodeCreateRequest):
        valid_at = _dt(body.valid_at)
        metadata = body.metadata or {}

        entities_resolved = 0
        entities_created = 0
        seen_uris: dict = {}  # cache name->uri within this request

        async with pool.acquire() as conn:
            # 1. Create the episode
            import json as json_mod
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
            for fact in body.facts:
                # Resolve subject
                subject_uri, is_new = await _resolve_or_create(
                    conn, fact.subject, body.create_entities,
                    generate_embedding, seen_uris)
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
                        generate_embedding, seen_uris)
                    if object_uri:
                        entities_resolved += 1
                        if obj_new:
                            entities_created += 1

                # Generate fact embedding
                fact_embedding = None
                if generate_embedding:
                    fact_embedding = await generate_embedding(fact.fact_text)

                await conn.execute("""
                    INSERT INTO knowledge_facts
                        (episode_id, subject_uri, predicate, object_uri,
                         object_literal, fact_text, fact_embedding,
                         valid_from, valid_to, group_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7::vector, $8, $9, $10)
                """, episode_id, subject_uri, fact.predicate.upper(),
                    object_uri, fact.object_literal, fact.fact_text,
                    str(fact_embedding) if fact_embedding else None,
                    _dt(fact.valid_from), _dt(fact.valid_to), body.group_id)
                facts_created += 1

        return EpisodeCreateResponse(
            episode_id=str(episode_id),
            facts_created=facts_created,
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

        await conn.execute("""
            INSERT INTO entity_registry
                (fuseki_uri, entity_text, normalized_text, entity_type,
                 source, embedding)
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
        query: str = Query(..., description="Search query"),
        limit: int = Query(10, ge=1, le=100),
        group_id: Optional[str] = Query(None),
        include_expired: bool = Query(False),
    ):
        if not generate_embedding:
            raise HTTPException(
                status_code=503,
                detail="Embedding provider not configured")

        query_embedding = await generate_embedding(query)
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
                       1 - (f.fact_embedding <=> $1::vector) AS similarity
                FROM knowledge_facts f
                LEFT JOIN knowledge_episodes e ON e.id = f.episode_id
                WHERE f.fact_embedding IS NOT NULL
                  {validity_filter}
                  {group_filter}
                ORDER BY f.fact_embedding <=> $1::vector
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
        source_document: Optional[str] = Query(None),
        query: Optional[str] = Query(None),
        group_id: Optional[str] = Query(None),
        limit: int = Query(20, ge=1, le=100),
    ):
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
        uri: str,
        include_expired: bool = Query(False),
        limit: int = Query(50, ge=1, le=200),
    ):
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
    # Shared helper
    # -------------------------------------------------------------------
    async def _get_entity_name(conn, uri: Optional[str]) -> Optional[str]:
        if not uri:
            return None
        return await conn.fetchval(
            "SELECT entity_text FROM entity_registry WHERE fuseki_uri = $1",
            uri)

    return router
