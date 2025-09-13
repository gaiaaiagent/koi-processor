#!/usr/bin/env python3
"""
KOI Knowledge MCP Server
Provides agents with access to KOI pipeline knowledge via MCP protocol
"""

import asyncio
import json
import asyncpg
import httpx
from typing import List, Dict, Any, Optional
from datetime import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import logging
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI(title="KOI Knowledge MCP Server", version="1.0.0")

# Configuration
DB_URL = os.getenv('POSTGRES_URL', 'postgresql://postgres:postgres@localhost:5433/eliza')
BGE_API_URL = os.getenv('BGE_API_URL', 'http://localhost:8090/encode')

# Pydantic models
class KnowledgeQuery(BaseModel):
    query: str
    agent_id: Optional[str] = None
    limit: int = 10
    similarity_threshold: float = 0.7

class KnowledgeResponse(BaseModel):
    success: bool
    memories: List[Dict[str, Any]]
    count: int
    query_embedding_generated: bool

# Database connection pool
db_pool: Optional[asyncpg.Pool] = None

async def init_db():
    """Initialize database connection pool"""
    global db_pool
    db_pool = await asyncpg.create_pool(
        DB_URL,
        min_size=2,
        max_size=10
    )
    logger.info("Database connection pool initialized")

async def generate_embedding(text: str) -> List[float]:
    """Generate BGE embedding via API"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(
                BGE_API_URL,
                json={"text": text}
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

@app.on_event("startup")
async def startup():
    """Initialize database on startup"""
    await init_db()
    
    # Get KOI memory stats
    async with db_pool.acquire() as conn:
        koi_count = await conn.fetchval(
            "SELECT COUNT(*) FROM koi_memories"
        )
        agent_count = await conn.fetchval(
            "SELECT COUNT(*) FROM memories"
        )
        embedding_count = await conn.fetchval(
            "SELECT COUNT(*) FROM embeddings WHERE dim_1024 IS NOT NULL"
        )
        
        logger.info(f"KOI Memories: {koi_count}")
        logger.info(f"Agent Memories: {agent_count}")
        logger.info(f"Embeddings: {embedding_count}")

@app.on_event("shutdown")
async def shutdown():
    """Clean up on shutdown"""
    if db_pool:
        await db_pool.close()

@app.get("/")
async def root():
    """Health check and stats"""
    async with db_pool.acquire() as conn:
        koi_count = await conn.fetchval("SELECT COUNT(*) FROM koi_memories")
        
    return {
        "service": "KOI Knowledge MCP Server",
        "status": "operational",
        "version": "1.0.0",
        "koi_memories": koi_count,
        "features": [
            "KOI memory search",
            "BGE embedding similarity",
            "Agent-specific filtering",
            "Real-time knowledge access"
        ]
    }

@app.post("/search", response_model=KnowledgeResponse)
async def search_knowledge(query: KnowledgeQuery):
    """Search KOI knowledge base"""
    logger.info(f"Searching for: {query.query[:100]}...")
    
    try:
        # Generate query embedding
        embedding = await generate_embedding(query.query)
        embedding_generated = len(embedding) > 0
        
        async with db_pool.acquire() as conn:
            # For now, use text search on KOI memories
            # TODO: Join with embeddings table once mappings are established
            results = await conn.fetch("""
                SELECT 
                    rid,
                    cid,
                    content,
                    metadata,
                    created_at,
                    source_sensor,
                    version
                FROM koi_memories
                WHERE content::text ILIKE $1
                   OR metadata::text ILIKE $1
                   OR source_sensor ILIKE $1
                ORDER BY created_at DESC
                LIMIT $2
            """, f'%{query.query}%', query.limit)
            
            memories = []
            for row in results:
                memory = {
                    "rid": row['rid'],
                    "cid": row['cid'],
                    "content": row['content'],
                    "metadata": row['metadata'],
                    "created_at": row['created_at'].isoformat() if row['created_at'] else None,
                    "source_sensor": row['source_sensor'],
                    "version": row['version'],
                    "similarity": 0.8  # Default similarity for text search
                }
                memories.append(memory)
            
            return KnowledgeResponse(
                success=True,
                memories=memories,
                count=len(memories),
                query_embedding_generated=embedding_generated
            )
            
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/memory/{rid}")
async def get_memory(rid: str):
    """Get specific memory by RID"""
    async with db_pool.acquire() as conn:
        result = await conn.fetchrow("""
            SELECT 
                rid,
                cid,
                content,
                metadata,
                created_at,
                version
            FROM koi_memories
            WHERE rid = $1
        """, rid)
        
        if not result:
            raise HTTPException(status_code=404, detail="Memory not found")
        
        return {
            "rid": result['rid'],
            "cid": result['cid'],
            "content": result['content'],
            "metadata": result['metadata'],
            "created_at": result['created_at'].isoformat() if result['created_at'] else None,
            "version": result['version']
        }

@app.get("/stats")
async def get_stats():
    """Get KOI knowledge statistics"""
    async with db_pool.acquire() as conn:
        stats = await conn.fetchrow("""
            SELECT 
                COUNT(*) as total_memories,
                COUNT(DISTINCT source_sensor) as unique_sensors,
                COUNT(CASE WHEN embedding IS NOT NULL THEN 1 END) as memories_with_embeddings,
                MIN(created_at) as oldest_memory,
                MAX(created_at) as newest_memory
            FROM koi_memories
        """)
        
        # Get top sensors
        top_sensors = await conn.fetch("""
            SELECT 
                source_sensor,
                COUNT(*) as count
            FROM koi_memories
            GROUP BY source_sensor
            ORDER BY count DESC
            LIMIT 5
        """)
        
        return {
            "total_memories": stats['total_memories'],
            "unique_sensors": stats['unique_sensors'],
            "memories_with_embeddings": stats['memories_with_embeddings'],
            "oldest_memory": stats['oldest_memory'].isoformat() if stats['oldest_memory'] else None,
            "newest_memory": stats['newest_memory'].isoformat() if stats['newest_memory'] else None,
            "top_sensors": [
                {"sensor": row['source_sensor'], "count": row['count']}
                for row in top_sensors
            ]
        }

if __name__ == "__main__":
    import uvicorn
    
    # Run server
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8200,
        log_level="info"
    )