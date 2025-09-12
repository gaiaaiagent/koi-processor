#!/usr/bin/env python3
"""
KOI Event Bridge v2 - With Deduplication and Versioning
Processes KOI events and generates embeddings with proper version control
"""

import os
import json
import asyncio
import asyncpg
import httpx
import hashlib
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import logging
import uuid

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI(title="KOI Event Bridge v2", version="2.0.0")

# Configuration
DB_URL = os.getenv('POSTGRES_URL', 'postgresql://postgres:postgres@localhost:5433/eliza')
BGE_API_URL = os.getenv('BGE_API_URL', 'http://localhost:8090/encode')
USE_ISOLATED_TABLES = os.getenv('USE_ISOLATED_TABLES', 'true').lower() == 'true'

# Pydantic models
class KOIBundle(BaseModel):
    rid: str
    cid: str
    content: Dict[str, Any]
    metadata: Dict[str, Any]
    manifest: Dict[str, Any]

class KOIEvent(BaseModel):
    event_type: str  # NEW, UPDATE, FORGET
    source_sensor: str
    timestamp: str
    bundle: KOIBundle

class ProcessingResult(BaseModel):
    success: bool
    rid: str
    cid: str
    chunks_created: int
    embeddings_created: int
    version: Optional[int] = None
    previous_version_id: Optional[str] = None
    error: Optional[str] = None

# Helper functions
async def generate_embedding_bge(text: str) -> List[float]:
    """Generate BGE embedding via API"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(
                BGE_API_URL,
                json={"text": text}  # Some servers use "text"
            )
            if response.status_code != 200:
                # Try with "input" field
                response = await client.post(
                    BGE_API_URL,
                    json={"input": text}
                )
            
            if response.status_code == 200:
                result = response.json()
                return result.get("embedding", [])
            else:
                logger.warning(f"BGE API error: {response.status_code}")
                return []
        except Exception as e:
            logger.error(f"Error calling BGE API: {e}")
            return []

def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> List[str]:
    """Split text into overlapping chunks"""
    if not text:
        return []
    
    chunks = []
    start = 0
    text_length = len(text)
    
    while start < text_length:
        end = min(start + chunk_size, text_length)
        chunk = text[start:end]
        chunks.append(chunk)
        start += (chunk_size - overlap)
    
    return chunks

async def extract_text_from_bundle(bundle: KOIBundle) -> str:
    """Extract text content from KOI bundle"""
    content = bundle.content
    
    if isinstance(content, dict):
        # Try common content keys
        for key in ['text', 'content', 'body', 'description']:
            if key in content:
                return str(content[key])
        
        # Try HTML content
        if 'html' in content:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(content['html'], 'html.parser')
            return soup.get_text(strip=True)
        
        # Concatenate all string values
        text_parts = []
        for value in content.values():
            if isinstance(value, str):
                text_parts.append(value)
        return ' '.join(text_parts)
    
    elif isinstance(content, str):
        return content
    
    return json.dumps(content)

async def check_existing_memory(conn: asyncpg.Connection, rid: str) -> Optional[Dict]:
    """Check if a memory with this RID already exists"""
    if USE_ISOLATED_TABLES:
        query = """
            SELECT id, version, superseded_at 
            FROM koi_memories 
            WHERE rid = $1 
            ORDER BY version DESC 
            LIMIT 1
        """
    else:
        # Legacy table structure
        query = """
            SELECT id, content 
            FROM memories 
            WHERE content->>'rid' = $1 
            ORDER BY "createdAt" DESC 
            LIMIT 1
        """
    
    result = await conn.fetchrow(query, rid)
    return dict(result) if result else None

async def create_new_version(conn: asyncpg.Connection, event: KOIEvent, 
                           previous: Optional[Dict], text_content: str) -> str:
    """Create a new version of a memory"""
    memory_id = str(uuid.uuid4())
    
    if USE_ISOLATED_TABLES:
        # Determine version number
        version = (previous['version'] + 1) if previous else 1
        previous_id = previous['id'] if previous else None
        
        # If updating, mark previous version as superseded
        if previous and event.event_type == 'UPDATE':
            await conn.execute("""
                UPDATE koi_memories 
                SET superseded_at = $1 
                WHERE id = $2
            """, datetime.now(tz=timezone.utc), previous['id'])
        
        # Extract publication date from metadata or content
        published_at = None
        published_confidence = 0.0
        content_hash = None
        
        # Try to extract from metadata first
        if 'published_at' in event.bundle.metadata:
            published_at = event.bundle.metadata['published_at']
            published_confidence = event.bundle.metadata.get('published_confidence', 0.9)
        elif 'created_at' in event.bundle.metadata:
            published_at = event.bundle.metadata['created_at']
            published_confidence = 0.8
        
        # Calculate content hash for deduplication
        import hashlib
        content_hash = hashlib.sha256(text_content.encode('utf-8')).hexdigest()
        
        # Insert new version with publication tracking
        await conn.execute("""
            INSERT INTO koi_memories (
                id, rid, cid, version, previous_version_id,
                event_type, source_sensor, content, metadata,
                published_at, published_confidence, content_hash
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        """, 
            memory_id,
            event.bundle.rid,
            event.bundle.cid,
            version,
            previous_id,
            event.event_type,
            event.source_sensor,
            json.dumps({
                "text": text_content,
                **event.bundle.content
            }),
            json.dumps({
                **event.bundle.metadata,
                "koi_timestamp": event.timestamp,
                "koi_manifest": event.bundle.manifest
            }),
            published_at,
            published_confidence,
            content_hash
        )
    else:
        # Legacy table structure - just insert without version control
        # TODO: Update legacy structure to support versioning
        agent_id = "8e1e4498-b3c8-0fae-ad1f-e90d1c1a4331"  # RegenAI agent
        
        await conn.execute("""
            INSERT INTO memories (id, type, content, "agentId", "createdAt")
            VALUES ($1::uuid, 'koi_document', $2::jsonb, $3::uuid, CURRENT_TIMESTAMP)
        """, 
            memory_id,
            json.dumps({
                "text": text_content,
                "rid": event.bundle.rid,
                "cid": event.bundle.cid,
                "source_sensor": event.source_sensor,
                "event_type": event.event_type,
                **event.bundle.metadata
            }),
            agent_id
        )
    
    return memory_id

async def store_embedding(conn: asyncpg.Connection, memory_id: str, 
                         embedding: List[float]) -> bool:
    """Store embedding in appropriate table"""
    if not embedding:
        return False
    
    embedding_str = '[' + ','.join(map(str, embedding)) + ']'
    embedding_dim = len(embedding)
    
    try:
        if USE_ISOLATED_TABLES:
            # Store in isolated koi_embeddings table
            if embedding_dim == 768:
                await conn.execute("""
                    INSERT INTO koi_embeddings (memory_id, dim_768)
                    VALUES ($1, $2::vector(768))
                    ON CONFLICT (memory_id) 
                    DO UPDATE SET dim_768 = $2::vector(768)
                """, memory_id, embedding_str)
            elif embedding_dim == 1024:
                await conn.execute("""
                    INSERT INTO koi_embeddings (memory_id, dim_1024)
                    VALUES ($1, $2::vector(1024))
                    ON CONFLICT (memory_id)
                    DO UPDATE SET dim_1024 = $2::vector(1024)
                """, memory_id, embedding_str)
            else:
                logger.warning(f"Unsupported embedding dimension: {embedding_dim}")
                return False
        else:
            # Store in legacy embeddings table
            existing = await conn.fetchval("""
                SELECT memory_id FROM embeddings WHERE memory_id = $1
            """, memory_id)
            
            if embedding_dim == 1024:
                if existing:
                    await conn.execute("""
                        UPDATE embeddings SET dim_1024 = $2::vector(1024)
                        WHERE memory_id = $1
                    """, memory_id, embedding_str)
                else:
                    await conn.execute("""
                        INSERT INTO embeddings (memory_id, dim_1024)
                        VALUES ($1, $2::vector(1024))
                    """, memory_id, embedding_str)
            else:
                logger.warning(f"Legacy table only supports 1024-dim embeddings")
                return False
        
        return True
        
    except Exception as e:
        logger.error(f"Error storing embedding: {e}")
        return False

async def process_koi_event(event: KOIEvent) -> ProcessingResult:
    """Process a KOI event with deduplication and versioning"""
    try:
        async with asyncpg.create_pool(DB_URL) as pool:
            async with pool.acquire() as conn:
                # Check for existing memory with this RID
                existing = await check_existing_memory(conn, event.bundle.rid)
                
                # Handle based on event type
                if event.event_type == "FORGET":
                    if existing and USE_ISOLATED_TABLES:
                        # Mark as superseded without creating new version
                        await conn.execute("""
                            UPDATE koi_memories 
                            SET superseded_at = $1 
                            WHERE rid = $2 AND superseded_at IS NULL
                        """, datetime.now(tz=timezone.utc), event.bundle.rid)
                        
                        return ProcessingResult(
                            success=True,
                            rid=event.bundle.rid,
                            cid=event.bundle.cid,
                            chunks_created=0,
                            embeddings_created=0
                        )
                    else:
                        # TODO: Implement deletion for legacy tables
                        return ProcessingResult(
                            success=True,
                            rid=event.bundle.rid,
                            cid=event.bundle.cid,
                            chunks_created=0,
                            embeddings_created=0
                        )
                
                elif event.event_type == "NEW" and existing:
                    # Content already exists, skip
                    logger.info(f"RID {event.bundle.rid} already exists, skipping NEW event")
                    return ProcessingResult(
                        success=True,
                        rid=event.bundle.rid,
                        cid=event.bundle.cid,
                        chunks_created=0,
                        embeddings_created=0,
                        error="Already exists"
                    )
                
                elif event.event_type == "UPDATE" and not existing:
                    # No previous version to update, treat as NEW
                    logger.info(f"No existing version for RID {event.bundle.rid}, treating UPDATE as NEW")
                    event.event_type = "NEW"
                
                # Extract text content
                text_content = await extract_text_from_bundle(event.bundle)
                
                if not text_content or len(text_content.strip()) < 50:
                    return ProcessingResult(
                        success=False,
                        rid=event.bundle.rid,
                        cid=event.bundle.cid,
                        chunks_created=0,
                        embeddings_created=0,
                        error="Content too short or empty"
                    )
                
                # Chunk the text
                chunks = chunk_text(text_content)
                
                if not chunks:
                    return ProcessingResult(
                        success=False,
                        rid=event.bundle.rid,
                        cid=event.bundle.cid,
                        chunks_created=0,
                        embeddings_created=0,
                        error="No chunks created"
                    )
                
                # Process each chunk
                embeddings_created = 0
                memory_ids = []
                
                for i, chunk in enumerate(chunks):
                    # Create memory for chunk
                    chunk_rid = f"{event.bundle.rid}#chunk{i}"
                    chunk_event = KOIEvent(
                        event_type=event.event_type,
                        source_sensor=event.source_sensor,
                        timestamp=event.timestamp,
                        bundle=KOIBundle(
                            rid=chunk_rid,
                            cid=f"{event.bundle.cid}#chunk{i}",
                            content={"text": chunk},
                            metadata={
                                **event.bundle.metadata,
                                "chunk_index": i,
                                "chunk_total": len(chunks),
                                "parent_rid": event.bundle.rid
                            },
                            manifest=event.bundle.manifest
                        )
                    )
                    
                    # Check if chunk already exists
                    chunk_existing = await check_existing_memory(conn, chunk_rid)
                    
                    # Create new version if needed
                    if event.event_type == "NEW" and not chunk_existing:
                        memory_id = await create_new_version(conn, chunk_event, None, chunk)
                    elif event.event_type == "UPDATE":
                        memory_id = await create_new_version(conn, chunk_event, chunk_existing, chunk)
                    else:
                        continue  # Skip if already exists
                    
                    memory_ids.append(memory_id)
                    
                    # Generate and store embedding
                    embedding = await generate_embedding_bge(chunk)
                    if embedding and await store_embedding(conn, memory_id, embedding):
                        embeddings_created += 1
                    
                    # Brief delay to avoid overwhelming the embedding server
                    await asyncio.sleep(0.05)
                
                # Get version info for response
                version = None
                previous_version_id = None
                if USE_ISOLATED_TABLES and memory_ids:
                    result = await conn.fetchrow("""
                        SELECT version, previous_version_id 
                        FROM koi_memories 
                        WHERE id = $1
                    """, memory_ids[0])
                    if result:
                        version = result['version']
                        previous_version_id = str(result['previous_version_id']) if result['previous_version_id'] else None
                
                return ProcessingResult(
                    success=True,
                    rid=event.bundle.rid,
                    cid=event.bundle.cid,
                    chunks_created=len(memory_ids),
                    embeddings_created=embeddings_created,
                    version=version,
                    previous_version_id=previous_version_id
                )
                
    except Exception as e:
        logger.error(f"Error processing event: {e}", exc_info=True)
        return ProcessingResult(
            success=False,
            rid=event.bundle.rid,
            cid=event.bundle.cid,
            chunks_created=0,
            embeddings_created=0,
            error=str(e)
        )

# API Endpoints
@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "service": "KOI Event Bridge v2",
        "status": "operational",
        "version": "2.0.0",
        "features": [
            "RID-based deduplication",
            "Version control for updates",
            "Isolated KOI tables",
            "BGE embedding generation"
        ],
        "isolated_tables": USE_ISOLATED_TABLES
    }

@app.post("/process-koi-event", response_model=ProcessingResult)
async def process_event_endpoint(event: KOIEvent):
    """Process a KOI event from the coordinator"""
    logger.info(f"[KOI Bridge v2] Received {event.event_type} event for RID: {event.bundle.rid}")
    
    # Process the event
    result = await process_koi_event(event)
    
    if result.success:
        logger.info(f"[KOI Bridge v2] Successfully processed: {result.chunks_created} chunks, "
                   f"{result.embeddings_created} embeddings, version: {result.version}")
    else:
        logger.error(f"[KOI Bridge v2] Processing failed: {result.error}")
    
    return result

@app.get("/stats")
async def get_stats():
    """Get pipeline statistics"""
    try:
        async with asyncpg.create_pool(DB_URL) as pool:
            async with pool.acquire() as conn:
                if USE_ISOLATED_TABLES:
                    result = await conn.fetchrow("""
                        SELECT 
                            COUNT(DISTINCT rid) as unique_documents,
                            COUNT(*) as total_versions,
                            COUNT(CASE WHEN event_type = 'NEW' THEN 1 END) as new_events,
                            COUNT(CASE WHEN event_type = 'UPDATE' THEN 1 END) as update_events,
                            COUNT(DISTINCT source_sensor) as active_sensors,
                            MAX(created_at) as latest_event
                        FROM koi_memories
                    """)
                    
                    embeddings = await conn.fetchrow("""
                        SELECT 
                            COUNT(dim_768) as gemma_embeddings,
                            COUNT(dim_1024) as bge_embeddings
                        FROM koi_embeddings
                    """)
                    
                    return {
                        "unique_documents": result['unique_documents'],
                        "total_versions": result['total_versions'],
                        "new_events": result['new_events'],
                        "update_events": result['update_events'],
                        "active_sensors": result['active_sensors'],
                        "latest_event": result['latest_event'].isoformat() if result['latest_event'] else None,
                        "embeddings": {
                            "bge": embeddings['bge_embeddings'],
                            "gemma": embeddings['gemma_embeddings']
                        }
                    }
                else:
                    # Legacy stats
                    result = await conn.fetchrow("""
                        SELECT 
                            COUNT(*) as total_memories,
                            COUNT(DISTINCT content->>'rid') as unique_rids,
                            COUNT(CASE WHEN type = 'koi_document' THEN 1 END) as koi_documents
                        FROM memories
                    """)
                    
                    return {
                        "total_memories": result['total_memories'],
                        "unique_rids": result['unique_rids'],
                        "koi_documents": result['koi_documents'],
                        "isolated_tables": False
                    }
                    
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    
    print("[KOI Event Bridge v2] Starting...")
    print(f"[KOI Event Bridge v2] Database: {DB_URL}")
    print(f"[KOI Event Bridge v2] BGE API: {BGE_API_URL}")
    print(f"[KOI Event Bridge v2] Using isolated tables: {USE_ISOLATED_TABLES}")
    print("[KOI Event Bridge v2] Features: Deduplication, Versioning, Isolated Tables")
    
    uvicorn.run(app, host="0.0.0.0", port=8100, log_level="info")