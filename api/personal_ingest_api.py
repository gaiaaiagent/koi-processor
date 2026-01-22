#!/usr/bin/env python3
"""
Personal KOI Ingest API

FastAPI server for ingesting pre-extracted entities from Claude Code.
Runs on port 8351 as part of the personal KOI-net.

This endpoint accepts entities already extracted by Claude (no LLM cost)
and performs:
1. Entity deduplication against the personal knowledge base
2. Canonical URI assignment
3. Storage in PostgreSQL with pgvector embeddings
4. Returns resolved entities with URIs for vault linking

Entity Resolution Tiers:
- Tier 1: Exact match (normalized text, B-Tree index)
- Tier 1.x: Fuzzy string match (Jaro-Winkler similarity)
- Tier 2: Semantic match (BGE embeddings + pgvector HNSW)
- Tier 3: Create new entity with deterministic URI
"""

import os
import asyncio
import asyncpg
import hashlib
import httpx
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import logging
import uuid

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI(
    title="Personal KOI Ingest API",
    version="1.0.0",
    description="Ingests pre-extracted entities from Claude Code into personal knowledge base"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
DB_URL = os.getenv('POSTGRES_URL', 'postgresql://darrenzal:@localhost:5432/personal_koi')
KOI_MODE = os.getenv('KOI_MODE', 'personal')
BGE_API_URL = os.getenv('BGE_API_URL', 'http://localhost:8090/encode')
ENABLE_SEMANTIC_MATCHING = os.getenv('ENABLE_SEMANTIC_MATCHING', 'true').lower() == 'true'

# Semantic similarity thresholds by entity type (Tier 2)
SEMANTIC_THRESHOLDS = {
    'Person': 0.92,
    'Organization': 0.95,
    'Project': 0.93,
    'Location': 0.95,
    'Concept': 0.90,
    'Event': 0.94,
    'Technology': 0.93,
}

# Global connection pool
db_pool: Optional[asyncpg.Pool] = None
bge_available: bool = False


# =============================================================================
# Pydantic Models
# =============================================================================

class ExtractedEntity(BaseModel):
    """Entity extracted by Claude Code"""
    name: str
    type: str  # Person, Organization, Location, Project, Concept
    mentions: List[str] = []
    confidence: float = 0.9
    context: Optional[str] = None


class ExtractedRelationship(BaseModel):
    """Relationship between entities"""
    subject: str
    predicate: str
    object: str
    confidence: float = 0.9


class IngestRequest(BaseModel):
    """Request to ingest extracted entities"""
    document_rid: str  # e.g., "vault:notes/salish-sea-herring"
    content: Optional[str] = None
    entities: List[ExtractedEntity]
    relationships: List[ExtractedRelationship] = []
    source: str = "obsidian-vault"


class CanonicalEntity(BaseModel):
    """Resolved canonical entity"""
    name: str
    uri: str
    type: str
    is_new: bool
    merged_with: Optional[str] = None  # If deduplicated
    confidence: float = 1.0


class IngestResponse(BaseModel):
    """Response from ingest endpoint"""
    success: bool
    canonical_entities: List[CanonicalEntity]
    receipt_rid: str
    stats: Dict[str, int]


# =============================================================================
# BGE Embedding Service
# =============================================================================

async def generate_embedding(text: str) -> Optional[List[float]]:
    """Generate BGE embedding via API"""
    if not bge_available or not ENABLE_SEMANTIC_MATCHING:
        return None

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # Normalize text before embedding (same as entity_resolver.py)
            normalized = normalize_entity_text(text)

            response = await client.post(
                BGE_API_URL,
                json={"text": normalized}
            )
            if response.status_code != 200:
                # Try with "input" field (some BGE servers use this)
                response = await client.post(
                    BGE_API_URL,
                    json={"input": normalized}
                )

            if response.status_code == 200:
                result = response.json()
                embedding = result.get("embedding", [])
                if embedding:
                    return embedding
                # Try alternative response format
                if "embeddings" in result and len(result["embeddings"]) > 0:
                    return result["embeddings"][0]
            else:
                logger.warning(f"BGE API error: {response.status_code}")
                return None
        except Exception as e:
            logger.warning(f"Error calling BGE API: {e}")
            return None


async def check_bge_availability() -> bool:
    """Check if BGE server is available"""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(BGE_API_URL.replace('/encode', '/health'))
            if response.status_code == 200:
                return True
            # Try a test embedding
            response = await client.post(BGE_API_URL, json={"text": "test"})
            return response.status_code == 200
    except Exception:
        return False


# =============================================================================
# Entity Resolution
# =============================================================================

def normalize_entity_text(text: str) -> str:
    """Normalize entity text for comparison"""
    return (
        text.lower()
        .strip()
        .replace('_', ' ')
        .replace('-', ' ')
        .replace('  ', ' ')
        .lstrip('@')
    )


def jaro_winkler_similarity(s1: str, s2: str) -> float:
    """Calculate Jaro-Winkler similarity between two strings"""
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0

    len1, len2 = len(s1), len(s2)
    match_distance = max(len1, len2) // 2 - 1
    if match_distance < 0:
        match_distance = 0

    s1_matches = [False] * len1
    s2_matches = [False] * len2
    matches = 0
    transpositions = 0

    # Find matches
    for i in range(len1):
        start = max(0, i - match_distance)
        end = min(i + match_distance + 1, len2)

        for j in range(start, end):
            if s2_matches[j] or s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break

    if matches == 0:
        return 0.0

    # Count transpositions
    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1

    jaro = (
        matches / len1 +
        matches / len2 +
        (matches - transpositions / 2) / matches
    ) / 3

    # Winkler adjustment (common prefix)
    prefix_len = 0
    for i in range(min(4, min(len1, len2))):
        if s1[i] == s2[i]:
            prefix_len += 1
        else:
            break

    return jaro + prefix_len * 0.1 * (1 - jaro)


# Type-specific similarity thresholds
SIMILARITY_THRESHOLDS = {
    'Person': 0.92,
    'Organization': 0.85,
    'Location': 0.80,
    'Project': 0.80,
    'Concept': 0.75,
}


async def resolve_entity(
    conn: asyncpg.Connection,
    entity: ExtractedEntity
) -> Tuple[CanonicalEntity, bool]:
    """
    Resolve an entity against the knowledge base.

    Returns: (CanonicalEntity, is_new)
    """
    normalized = normalize_entity_text(entity.name)
    threshold = SIMILARITY_THRESHOLDS.get(entity.type, 0.85)

    # Tier 1: Exact match (normalized text)
    if entity.type:
        exact_match = await conn.fetchrow("""
            SELECT id, fuseki_uri, entity_text, entity_type, normalized_text
            FROM entity_registry
            WHERE normalized_text = $1
            AND entity_type = $2
            LIMIT 1
        """, normalized, entity.type)
    else:
        exact_match = await conn.fetchrow("""
            SELECT id, fuseki_uri, entity_text, entity_type, normalized_text
            FROM entity_registry
            WHERE normalized_text = $1
            LIMIT 1
        """, normalized)

    if exact_match:
        return CanonicalEntity(
            name=exact_match['entity_text'],
            uri=exact_match['fuseki_uri'],
            type=exact_match['entity_type'] or entity.type,
            is_new=False,
            merged_with=entity.name if exact_match['entity_text'] != entity.name else None,
            confidence=1.0
        ), False

    # Tier 2: Fuzzy match (same type)
    if entity.type:
        candidates = await conn.fetch("""
            SELECT id, fuseki_uri, entity_text, entity_type, normalized_text
            FROM entity_registry
            WHERE entity_type = $1
        """, entity.type)
    else:
        candidates = await conn.fetch("""
            SELECT id, fuseki_uri, entity_text, entity_type, normalized_text
            FROM entity_registry
        """)

    best_match = None
    best_score = 0.0

    for candidate in candidates:
        score = jaro_winkler_similarity(normalized, candidate['normalized_text'])
        if score >= threshold and score > best_score:
            best_score = score
            best_match = candidate

    if best_match:
        return CanonicalEntity(
            name=best_match['entity_text'],
            uri=best_match['fuseki_uri'],
            type=best_match['entity_type'] or entity.type,
            is_new=False,
            merged_with=entity.name if best_match['entity_text'] != entity.name else None,
            confidence=best_score
        ), False

    # Tier 2: Semantic match (BGE embeddings + pgvector)
    if bge_available and ENABLE_SEMANTIC_MATCHING:
        embedding = await generate_embedding(entity.name)
        if embedding:
            semantic_threshold = SEMANTIC_THRESHOLDS.get(entity.type, 0.93)

            # Query for semantic matches using pgvector cosine similarity
            if entity.type:
                semantic_match = await conn.fetchrow("""
                    SELECT id, fuseki_uri, entity_text, entity_type,
                           1 - (embedding <=> $1::vector) AS similarity
                    FROM entity_registry
                    WHERE embedding IS NOT NULL
                      AND entity_type = $2
                      AND 1 - (embedding <=> $1::vector) > $3
                    ORDER BY similarity DESC
                    LIMIT 1
                """, str(embedding), entity.type, semantic_threshold)
            else:
                semantic_match = await conn.fetchrow("""
                    SELECT id, fuseki_uri, entity_text, entity_type,
                           1 - (embedding <=> $1::vector) AS similarity
                    FROM entity_registry
                    WHERE embedding IS NOT NULL
                      AND 1 - (embedding <=> $1::vector) > $2
                    ORDER BY similarity DESC
                    LIMIT 1
                """, str(embedding), semantic_threshold)

            if semantic_match:
                logger.info(f"Tier 2 semantic match: '{entity.name}' -> '{semantic_match['entity_text']}' "
                           f"(similarity: {semantic_match['similarity']:.3f})")
                return CanonicalEntity(
                    name=semantic_match['entity_text'],
                    uri=semantic_match['fuseki_uri'],
                    type=semantic_match['entity_type'] or entity.type,
                    is_new=False,
                    merged_with=entity.name if semantic_match['entity_text'] != entity.name else None,
                    confidence=float(semantic_match['similarity'])
                ), False

    # Tier 3: Create new entity
    new_uri = generate_entity_uri(entity.name, entity.type)

    return CanonicalEntity(
        name=entity.name,
        uri=new_uri,
        type=entity.type,
        is_new=True,
        confidence=entity.confidence
    ), True


def generate_entity_uri(name: str, entity_type: str) -> str:
    """Generate a deterministic URI for a new entity"""
    normalized = normalize_entity_text(name)
    # Create a stable hash-based ID
    hash_input = f"{entity_type}:{normalized}"
    hash_id = hashlib.sha256(hash_input.encode()).hexdigest()[:12]

    type_prefix = entity_type.lower()
    safe_name = normalized.replace(' ', '-').replace("'", '')[:50]

    return f"orn:personal-koi.entity:{type_prefix}-{safe_name}-{hash_id}"


async def store_new_entity(
    conn: asyncpg.Connection,
    entity: ExtractedEntity,
    canonical: CanonicalEntity,
    document_rid: str
) -> None:
    """Store a new entity in the registry with embedding"""
    normalized = normalize_entity_text(entity.name)

    import json as json_module
    metadata = json_module.dumps({
        'mentions': entity.mentions,
        'context': entity.context,
        'confidence': entity.confidence
    })

    # Generate embedding for new entity (enables future Tier 2 matching)
    embedding = None
    if bge_available and ENABLE_SEMANTIC_MATCHING:
        embedding = await generate_embedding(entity.name)
        if embedding:
            logger.info(f"Generated embedding for new entity: {entity.name}")

    if embedding:
        await conn.execute("""
            INSERT INTO entity_registry (
                fuseki_uri, entity_text, entity_type, normalized_text,
                source, first_seen_rid, metadata, embedding
            ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::vector)
            ON CONFLICT (fuseki_uri) DO NOTHING
        """,
            canonical.uri,
            entity.name,
            entity.type,
            normalized,
            'personal-vault',
            document_rid,
            metadata,
            str(embedding)
        )
    else:
        await conn.execute("""
            INSERT INTO entity_registry (
                fuseki_uri, entity_text, entity_type, normalized_text,
                source, first_seen_rid, metadata
            ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            ON CONFLICT (fuseki_uri) DO NOTHING
        """,
            canonical.uri,
            entity.name,
            entity.type,
            normalized,
            'personal-vault',
            document_rid,
            metadata
        )


# =============================================================================
# API Endpoints
# =============================================================================

@app.on_event("startup")
async def startup():
    """Initialize database connection pool and check BGE availability"""
    global db_pool, bge_available
    try:
        db_pool = await asyncpg.create_pool(
            DB_URL,
            min_size=2,
            max_size=10,
            command_timeout=60
        )
        logger.info(f"Connected to database (mode: {KOI_MODE})")

        # Ensure schema exists
        async with db_pool.acquire() as conn:
            await ensure_schema(conn)

        # Check BGE server availability
        bge_available = await check_bge_availability()
        if bge_available:
            logger.info(f"BGE embedding server available at {BGE_API_URL}")
            logger.info("Tier 2 semantic matching: ENABLED")
        else:
            logger.warning(f"BGE embedding server not available at {BGE_API_URL}")
            logger.info("Tier 2 semantic matching: DISABLED (falling back to fuzzy matching)")

    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        raise


@app.on_event("shutdown")
async def shutdown():
    """Close database connection pool"""
    global db_pool
    if db_pool:
        await db_pool.close()


async def ensure_schema(conn: asyncpg.Connection):
    """Ensure the entity_registry table exists"""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS entity_registry (
            id SERIAL PRIMARY KEY,
            fuseki_uri TEXT UNIQUE NOT NULL,
            entity_text TEXT NOT NULL,
            entity_type TEXT,
            normalized_text TEXT NOT NULL,
            ledger_id TEXT,
            metadata_iri TEXT,
            admin_address TEXT,
            aliases TEXT[],
            jurisdiction TEXT,
            class_id TEXT,
            source TEXT DEFAULT 'personal-vault',
            first_seen_rid TEXT,
            metadata JSONB,
            embedding vector(1024),
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)

    # Create index on normalized_text for fast lookups
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_entity_registry_normalized
        ON entity_registry(normalized_text)
    """)

    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_entity_registry_type
        ON entity_registry(entity_type)
    """)

    # Create document_entity_links table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS document_entity_links (
            id SERIAL PRIMARY KEY,
            document_rid TEXT NOT NULL,
            entity_uri TEXT NOT NULL,
            mention_count INT DEFAULT 1,
            context TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(document_rid, entity_uri)
        )
    """)

    logger.info("Schema verified/created")


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        async with db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return {
            "status": "healthy",
            "mode": KOI_MODE,
            "database": "connected",
            "bge_available": bge_available,
            "semantic_matching": bge_available and ENABLE_SEMANTIC_MATCHING,
            "resolution_tiers": {
                "tier1_exact": True,
                "tier1x_fuzzy": True,
                "tier2_semantic": bge_available and ENABLE_SEMANTIC_MATCHING,
                "tier3_create": True
            }
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)}
        )


@app.post("/ingest", response_model=IngestResponse)
async def ingest_extraction(request: IngestRequest):
    """
    Ingest pre-extracted entities from Claude Code.

    This endpoint:
    1. Deduplicates entities against the personal KB
    2. Assigns canonical URIs to new entities
    3. Stores entities and document links
    4. Returns resolved entities for vault linking
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    canonical_entities: List[CanonicalEntity] = []
    new_count = 0
    resolved_count = 0

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            for entity in request.entities:
                try:
                    logger.info(f"Processing entity: {entity.name} ({entity.type})")
                    canonical, is_new = await resolve_entity(conn, entity)
                    logger.info(f"Resolved: {canonical.name} -> {canonical.uri} (new={is_new})")
                    canonical_entities.append(canonical)

                    if is_new:
                        new_count += 1
                        await store_new_entity(conn, entity, canonical, request.document_rid)
                        logger.info(f"Stored new entity: {canonical.uri}")
                    else:
                        resolved_count += 1
                        logger.info(f"Resolved to existing: {canonical.uri}")

                    # Link entity to document
                    await conn.execute("""
                        INSERT INTO document_entity_links (document_rid, entity_uri, context)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (document_rid, entity_uri)
                        DO UPDATE SET mention_count = document_entity_links.mention_count + 1
                    """, request.document_rid, canonical.uri, entity.context)
                    logger.info(f"Linked entity to document")

                except Exception as e:
                    import traceback
                    logger.error(f"Error processing entity {entity.name}: {e}")
                    logger.error(traceback.format_exc())
                    # Continue with other entities

    # Generate receipt RID
    receipt_rid = f"orn:personal-koi.receipt:{uuid.uuid4().hex[:16]}"

    return IngestResponse(
        success=True,
        canonical_entities=canonical_entities,
        receipt_rid=receipt_rid,
        stats={
            "entities_processed": len(request.entities),
            "new_entities": new_count,
            "resolved_entities": resolved_count,
            "relationships_processed": len(request.relationships)
        }
    )


@app.get("/entities")
async def list_entities(
    entity_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
):
    """List entities in the knowledge base"""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        if entity_type:
            entities = await conn.fetch("""
                SELECT fuseki_uri, entity_text, entity_type, source, created_at
                FROM entity_registry
                WHERE entity_type = $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
            """, entity_type, limit, offset)
        else:
            entities = await conn.fetch("""
                SELECT fuseki_uri, entity_text, entity_type, source, created_at
                FROM entity_registry
                ORDER BY created_at DESC
                LIMIT $1 OFFSET $2
            """, limit, offset)

        return {
            "entities": [dict(e) for e in entities],
            "count": len(entities),
            "limit": limit,
            "offset": offset
        }


@app.get("/entity/{entity_uri:path}")
async def get_entity(entity_uri: str):
    """Get a specific entity by URI"""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        entity = await conn.fetchrow("""
            SELECT fuseki_uri, entity_text, entity_type, normalized_text,
                   source, first_seen_rid, metadata, created_at
            FROM entity_registry
            WHERE fuseki_uri = $1
        """, entity_uri)

        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")

        # Get linked documents
        docs = await conn.fetch("""
            SELECT document_rid, mention_count, context, created_at
            FROM document_entity_links
            WHERE entity_uri = $1
            ORDER BY created_at DESC
        """, entity_uri)

        return {
            "entity": dict(entity),
            "documents": [dict(d) for d in docs]
        }


@app.get("/stats")
async def get_stats():
    """Get knowledge base statistics"""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM entity_registry")

        by_type = await conn.fetch("""
            SELECT entity_type, COUNT(*) as count
            FROM entity_registry
            GROUP BY entity_type
            ORDER BY count DESC
        """)

        recent = await conn.fetch("""
            SELECT entity_text, entity_type, created_at
            FROM entity_registry
            ORDER BY created_at DESC
            LIMIT 10
        """)

        return {
            "total_entities": total,
            "by_type": {r['entity_type']: r['count'] for r in by_type},
            "recent_entities": [dict(r) for r in recent],
            "mode": KOI_MODE
        }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv('KOI_API_PORT', '8351'))
    uvicorn.run(app, host="0.0.0.0", port=port)
