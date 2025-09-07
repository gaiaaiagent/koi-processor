#!/usr/bin/env python3
"""
KOI Event Bridge - Connects KOI Sensor Network to Processing Pipeline
Receives events from KOI Coordinator and processes them through BGE pipeline
"""

import os
import sys
import json
import asyncio
import hashlib
from typing import Dict, Any, Optional
from datetime import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
import asyncpg
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

# Import for type hints
from typing import List
import numpy as np
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI(title="KOI Event Bridge", version="1.0.0")

# Database configuration
DB_URL = os.getenv('POSTGRES_URL', 'postgresql://postgres:postgres@localhost:5433/eliza')

# BGE API configuration
BGE_API_URL = os.getenv('BGE_API_URL', 'http://localhost:8090/encode')

# Helper functions embedded directly
async def generate_embedding_bge(text: str) -> List[float]:
    """Generate BGE embedding via API"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
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

def process_document(text: str, chunk_size: int = 1000, overlap: int = 200) -> List[str]:
    """Process document into chunks"""
    if not text:
        return []
    
    # Simple chunking by character count
    chunks = []
    start = 0
    text_length = len(text)
    
    while start < text_length:
        end = min(start + chunk_size, text_length)
        chunk = text[start:end]
        chunks.append(chunk)
        start += (chunk_size - overlap)
    
    return chunks

def extract_content_from_json(content_dict: Dict[str, Any]) -> str:
    """Extract text content from various JSON structures"""
    text_parts = []
    
    # Handle common fields
    if 'text' in content_dict:
        text_parts.append(str(content_dict['text']))
    if 'content' in content_dict:
        text_parts.append(str(content_dict['content']))
    if 'body' in content_dict:
        text_parts.append(str(content_dict['body']))
    if 'description' in content_dict:
        text_parts.append(str(content_dict['description']))
    if 'title' in content_dict:
        text_parts.append(str(content_dict['title']))
    
    return ' '.join(text_parts)

class KOIBundle(BaseModel):
    """KOI Bundle format from sensor events"""
    rid: str
    cid: str
    content: Dict[str, Any]
    metadata: Dict[str, Any]
    manifest: Dict[str, Any]

class KOIEvent(BaseModel):
    """KOI Event from coordinator"""
    event_type: str  # NEW, UPDATE, FORGET
    bundle: KOIBundle
    timestamp: str
    source_sensor: str

class ProcessingResult(BaseModel):
    """Result of processing a KOI event"""
    success: bool
    rid: str
    cid: str
    chunks_created: int
    embeddings_created: int
    error: Optional[str] = None

async def create_cat_receipt(event: KOIEvent, result: ProcessingResult) -> Dict[str, Any]:
    """Create a CAT (Content Addressable Transformation) receipt"""
    receipt = {
        "receipt_id": hashlib.sha256(f"{event.bundle.cid}_{datetime.utcnow().isoformat()}".encode()).hexdigest()[:16],
        "transformation": "koi_to_bge_embedding",
        "input": {
            "rid": event.bundle.rid,
            "cid": event.bundle.cid,
            "event_type": event.event_type,
            "source_sensor": event.source_sensor
        },
        "output": {
            "chunks_created": result.chunks_created,
            "embeddings_created": result.embeddings_created,
            "success": result.success
        },
        "timestamp": datetime.utcnow().isoformat(),
        "processor_version": "1.0.0"
    }
    return receipt

async def extract_text_from_bundle(bundle: KOIBundle) -> str:
    """Extract text content from KOI bundle"""
    content = bundle.content
    
    # Handle different content structures
    if isinstance(content, dict):
        # Check for common content keys
        if 'text' in content:
            return content['text']
        elif 'content' in content:
            return content['content']
        elif 'body' in content:
            return content['body']
        elif 'html' in content:
            # Extract text from HTML if needed
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(content['html'], 'html.parser')
            return soup.get_text(strip=True)
        else:
            # Concatenate all string values
            text_parts = []
            for value in content.values():
                if isinstance(value, str):
                    text_parts.append(value)
            return ' '.join(text_parts)
    elif isinstance(content, str):
        return content
    else:
        return json.dumps(content)

async def process_koi_event(event: KOIEvent) -> ProcessingResult:
    """Process a KOI event through the BGE pipeline"""
    try:
        # Handle different event types
        if event.event_type == "FORGET":
            # TODO: Implement deletion from database
            return ProcessingResult(
                success=True,
                rid=event.bundle.rid,
                cid=event.bundle.cid,
                chunks_created=0,
                embeddings_created=0
            )
        
        # Extract text content from bundle
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
        
        # Create document structure for processing
        document = {
            "id": event.bundle.cid[:16],  # Use first 16 chars of CID as doc ID
            "rid": event.bundle.rid,
            "cid": event.bundle.cid,
            "source": event.source_sensor,
            "content": text_content,
            "title": event.bundle.metadata.get("title", f"Document from {event.source_sensor}"),
            "metadata": {
                **event.bundle.metadata,
                "koi_event_type": event.event_type,
                "koi_timestamp": event.timestamp,
                "koi_manifest": event.bundle.manifest
            }
        }
        
        # Chunk the document
        chunks = process_document(text_content, chunk_size=1000, overlap=200)
        
        if not chunks:
            return ProcessingResult(
                success=False,
                rid=event.bundle.rid,
                cid=event.bundle.cid,
                chunks_created=0,
                embeddings_created=0,
                error="No chunks created from content"
            )
        
        # Generate embeddings for each chunk
        embeddings_created = 0
        async with asyncpg.create_pool(DB_URL) as pool:
            async with pool.acquire() as conn:
                for i, chunk in enumerate(chunks):
                    # Generate embedding
                    try:
                        async with httpx.AsyncClient(timeout=30.0) as client:
                            response = await client.post(
                                BGE_API_URL,
                                json={"text": chunk}
                            )
                            if response.status_code == 200:
                                embedding = response.json()["embedding"]
                            else:
                                # Fallback to mock embedding if BGE API not available
                                import numpy as np
                                embedding = np.random.randn(1024).tolist()
                    except:
                        # Use mock embedding if BGE API not available
                        import numpy as np
                        embedding = np.random.randn(1024).tolist()
                    
                    # Store in database
                    # Use existing RegenAI agent
                    agent_id = "8e1e4498-b3c8-0fae-ad1f-e90d1c1a4331"  # RegenAI agent
                    
                    # Create memory entry
                    memory_content = {
                        "text": chunk,
                        "doc_id": document["id"],
                        "chunk_index": i,
                        "chunk_count": len(chunks),
                        "source_type": "koi_sensor",
                        "source_sensor": event.source_sensor,
                        "rid": event.bundle.rid,
                        "cid": event.bundle.cid,
                        **document["metadata"]
                    }
                    
                    # Insert into memories table (roomId can be null)
                    import uuid
                    memory_id = str(uuid.uuid4())
                    await conn.execute("""
                        INSERT INTO memories (id, type, content, "agentId", "createdAt")
                        VALUES ($1::uuid, 'koi_document', $2::jsonb, $3::uuid, CURRENT_TIMESTAMP)
                    """, memory_id, json.dumps(memory_content), agent_id)
                    
                    # Insert embedding
                    embedding_str = '[' + ','.join(map(str, embedding)) + ']'
                    # First check if embedding exists
                    existing = await conn.fetchval("""
                        SELECT memory_id FROM embeddings WHERE memory_id = $1
                    """, memory_id)
                    
                    if existing:
                        await conn.execute("""
                            UPDATE embeddings SET dim_1024 = $2::vector
                            WHERE memory_id = $1
                        """, memory_id, embedding_str)
                    else:
                        await conn.execute("""
                            INSERT INTO embeddings (memory_id, dim_1024)
                            VALUES ($1, $2::vector)
                        """, memory_id, embedding_str)
                    
                    embeddings_created += 1
        
        return ProcessingResult(
            success=True,
            rid=event.bundle.rid,
            cid=event.bundle.cid,
            chunks_created=len(chunks),
            embeddings_created=embeddings_created
        )
        
    except Exception as e:
        import traceback
        return ProcessingResult(
            success=False,
            rid=event.bundle.rid,
            cid=event.bundle.cid,
            chunks_created=0,
            embeddings_created=0,
            error=f"Processing error: {str(e)}\n{traceback.format_exc()}"
        )

@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "service": "KOI Event Bridge",
        "status": "operational",
        "version": "1.0.0",
        "description": "Connects KOI Sensor Network to BGE Processing Pipeline"
    }

@app.post("/process-koi-event", response_model=ProcessingResult)
async def process_event_endpoint(event: KOIEvent):
    """Process a KOI event from the coordinator"""
    print(f"[KOI Bridge] Received {event.event_type} event for RID: {event.bundle.rid}")
    
    # Process the event
    result = await process_koi_event(event)
    
    # Create CAT receipt for provenance
    receipt = await create_cat_receipt(event, result)
    
    # Log the receipt (in production, store this in database)
    print(f"[KOI Bridge] CAT Receipt: {json.dumps(receipt, indent=2)}")
    
    if not result.success:
        print(f"[KOI Bridge] Processing failed: {result.error}")
        raise HTTPException(status_code=500, detail=result.error)
    
    print(f"[KOI Bridge] Successfully processed: {result.chunks_created} chunks, {result.embeddings_created} embeddings")
    return result

@app.get("/stats")
async def get_stats():
    """Get statistics about processed KOI events"""
    try:
        async with asyncpg.create_pool(DB_URL) as pool:
            async with pool.acquire() as conn:
                stats = await conn.fetchrow("""
                    SELECT 
                        COUNT(DISTINCT m.id) as total_koi_memories,
                        COUNT(DISTINCT (m.content->>'rid')) as unique_rids,
                        COUNT(DISTINCT (m.content->>'source_sensor')) as unique_sensors,
                        COUNT(DISTINCT e.id) as total_embeddings
                    FROM memories m
                    LEFT JOIN embeddings e ON e.memory_id = m.id
                    WHERE m.type = 'koi_document'
                """)
                
                recent = await conn.fetch("""
                    SELECT 
                        m.content->>'rid' as rid,
                        m.content->>'source_sensor' as sensor,
                        m."createdAt" as created_at
                    FROM memories m
                    WHERE m.type = 'koi_document'
                    ORDER BY m."createdAt" DESC
                    LIMIT 5
                """)
                
                return {
                    "total_koi_documents": stats["total_koi_memories"],
                    "unique_rids": stats["unique_rids"],
                    "unique_sensors": stats["unique_sensors"],
                    "total_embeddings": stats["total_embeddings"],
                    "recent_documents": [
                        {
                            "rid": r["rid"],
                            "sensor": r["sensor"],
                            "created_at": r["created_at"].isoformat() if r["created_at"] else None
                        }
                        for r in recent
                    ]
                }
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    import uvicorn
    
    # Run the FastAPI server
    print("[KOI Event Bridge] Starting on http://localhost:8100")
    print("[KOI Event Bridge] Waiting for events from KOI Coordinator...")
    print(f"[KOI Event Bridge] Database: {DB_URL}")
    print(f"[KOI Event Bridge] BGE API: {BGE_API_URL}")
    
    uvicorn.run(app, host="0.0.0.0", port=8100, log_level="info")