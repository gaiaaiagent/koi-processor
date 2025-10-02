#!/usr/bin/env python3
"""
KOI Knowledge MCP Server
Provides agents with access to KOI pipeline knowledge via MCP protocol
"""

import asyncio
import json
import asyncpg
import httpx
from typing import List, Dict, Any, Optional, Union
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import logging
import os
import uuid

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI(
    title="KOI Knowledge MCP Server",
    version="3.0.0",
    description="MCP Streamable HTTP Server (2025-03-26) for KOI Knowledge Graph"
)

# Configuration
DB_URL = os.getenv('POSTGRES_URL', 'postgresql://postgres:postgres@localhost:5433/eliza')
BGE_API_URL = os.getenv('BGE_API_URL', 'http://localhost:8090/encode')
HYBRID_RAG_API_URL = os.getenv('HYBRID_RAG_API_URL', 'http://localhost:8301/api/koi/query')

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
    confidence: Optional[float] = None
    triggered_extraction: Optional[bool] = None
    execution_time: Optional[float] = None
    search_method: Optional[str] = None  # 'hybrid_rag' or 'fallback'

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
        "version": "3.0.0",
        "protocol": "MCP Streamable HTTP",
        "protocolVersion": "2025-03-26",
        "specification": "https://modelcontextprotocol.io/specification/2025-03-26",
        "koi_memories": koi_count,
        "endpoints": {
            "mcp": {
                "path": "/mcp",
                "methods": ["POST", "GET"],
                "post": "JSON-RPC 2.0 messages (client→server)",
                "get": "SSE stream (server→client, optional)"
            },
            "legacy_search": "/search (POST)",
            "legacy_memory": "/memory/{rid} (GET)",
            "stats": "/stats (GET)"
        },
        "mcp_tools": [
            {
                "name": "search_knowledge",
                "description": "Hybrid RAG search with confidence scores"
            },
            {
                "name": "get_memory",
                "description": "Retrieve specific document by RID"
            },
            {
                "name": "get_stats",
                "description": "Knowledge base statistics"
            }
        ],
        "features": [
            "MCP Streamable HTTP (2025-03-26)",
            "JSON-RPC 2.0 protocol",
            "Bidirectional communication",
            "SSE streaming support",
            "Hybrid RAG search (RRF + BGE + Keyword)",
            "Adaptive knowledge extraction",
            "Confidence-based query routing",
            "Agent-specific filtering",
            "Real-time knowledge access",
            "Automatic fallback to text search"
        ],
        "search_capabilities": {
            "primary": "Hybrid RAG API (port 8301)",
            "fallback": "Text search (ILIKE)",
            "confidence_threshold": 0.7,
            "adaptive_extraction": True
        }
    }

async def call_hybrid_rag_api(query_text: str, agent_id: str, limit: int) -> Optional[Dict[str, Any]]:
    """
    Call the Hybrid RAG API (RRF + BGE + Adaptive Extraction)
    Returns API response or None if unavailable
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            logger.info(f"🔍 Calling Hybrid RAG API for: {query_text[:50]}...")
            response = await client.post(
                HYBRID_RAG_API_URL,
                json={
                    "question": query_text,
                    "agent_id": agent_id or "mcp-agent",
                    "user_id": "mcp-user"
                }
            )

            if response.status_code == 200:
                result = response.json()
                logger.info(f"✅ Hybrid RAG: {result.get('total_results', 0)} results, confidence: {result.get('confidence', 0):.3f}")
                return result
            else:
                logger.warning(f"⚠️ Hybrid RAG API error: {response.status_code}")
                return None

        except Exception as e:
            logger.warning(f"⚠️ Hybrid RAG API unavailable: {e}")
            return None

async def fallback_text_search(query_text: str, limit: int) -> List[Dict[str, Any]]:
    """
    Fallback to simple text search when Hybrid RAG API is unavailable
    """
    logger.info("📝 Using fallback text search")
    async with db_pool.acquire() as conn:
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
        """, f'%{query_text}%', limit)

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
                "similarity": 0.5  # Lower score for text search
            }
            memories.append(memory)

        return memories

@app.post("/search", response_model=KnowledgeResponse)
async def search_knowledge(query: KnowledgeQuery):
    """
    Search KOI knowledge base using Hybrid RAG API
    Falls back to simple text search if API unavailable
    """
    logger.info(f"Searching for: {query.query[:100]}...")

    try:
        # Try Hybrid RAG API first (RRF + BGE + Adaptive)
        hybrid_result = await call_hybrid_rag_api(
            query.query,
            query.agent_id,
            query.limit
        )

        if hybrid_result:
            # Transform Hybrid RAG response to MCP format
            memories = []
            for result in hybrid_result.get('results', [])[:query.limit]:
                memory = {
                    "rid": result.get('rid', 'unknown'),
                    "cid": None,  # Not provided by Hybrid RAG
                    "content": result.get('content', ''),
                    "metadata": {"source": result.get('source', 'unknown')},
                    "created_at": None,
                    "source_sensor": result.get('source', 'unknown'),
                    "version": 1,
                    "similarity": result.get('score', 0.0)
                }
                memories.append(memory)

            return KnowledgeResponse(
                success=True,
                memories=memories,
                count=len(memories),
                query_embedding_generated=True,
                confidence=hybrid_result.get('confidence'),
                triggered_extraction=hybrid_result.get('triggered_extraction'),
                execution_time=hybrid_result.get('execution_time'),
                search_method='hybrid_rag'
            )

        # Fallback to simple text search
        logger.info("⚠️ Hybrid RAG unavailable, using fallback")
        memories = await fallback_text_search(query.query, query.limit)

        return KnowledgeResponse(
            success=True,
            memories=memories,
            count=len(memories),
            query_embedding_generated=False,
            search_method='fallback'
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

# ============================================================================
# MCP Protocol Implementation (JSON-RPC 2.0 with Streamable HTTP)
# ============================================================================

class MCPMessage(BaseModel):
    """Base MCP JSON-RPC 2.0 message"""
    jsonrpc: str = "2.0"
    id: Optional[Union[str, int]] = None

class MCPRequest(MCPMessage):
    """MCP request message"""
    method: str
    params: Optional[Dict[str, Any]] = None

class MCPResponse(MCPMessage):
    """MCP response message"""
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None

class MCPCapabilities(BaseModel):
    """MCP server capabilities"""
    tools: Dict[str, bool] = {"listChanged": False}

class MCPServerInfo(BaseModel):
    """MCP server information"""
    name: str = "koi-knowledge"
    version: str = "3.0.0"

class MCPImplementation(BaseModel):
    """MCP implementation details"""
    name: str = "koi-knowledge-mcp"
    version: str = "3.0.0"

class MCPInitializeResult(BaseModel):
    """Result of initialize request"""
    protocolVersion: str = "2025-03-26"
    capabilities: MCPCapabilities
    serverInfo: MCPServerInfo
    instructions: Optional[str] = None

# MCP Tool definitions
MCP_TOOLS = [
    {
        "name": "search_knowledge",
        "description": "Search the KOI knowledge graph using hybrid RAG (RRF + BGE embeddings + keyword search). Returns relevant documents with confidence scores.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query text"
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent identifier (optional)"
                },
                "limit": {
                    "type": "number",
                    "description": "Maximum number of results (default: 10)",
                    "default": 10
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_memory",
        "description": "Retrieve a specific memory by its RID (Resource Identifier)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rid": {
                    "type": "string",
                    "description": "The Resource ID (RID) of the memory to retrieve"
                }
            },
            "required": ["rid"]
        }
    },
    {
        "name": "get_stats",
        "description": "Get statistics about the KOI knowledge base (total memories, sensors, embedding coverage, etc.)",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]

async def handle_mcp_initialize(params: Dict[str, Any]) -> MCPInitializeResult:
    """Handle MCP initialize request"""
    client_info = params.get('clientInfo', {})
    client_version = params.get('protocolVersion', 'unknown')

    logger.info(f"MCP initialize - Client: {client_info.get('name', 'unknown')} {client_info.get('version', '')}")
    logger.info(f"MCP initialize - Client protocol version: {client_version}")
    logger.info(f"MCP initialize - Client capabilities: {params.get('capabilities', {})}")

    return MCPInitializeResult(
        protocolVersion="2025-03-26",
        capabilities=MCPCapabilities(
            tools={"listChanged": False}
        ),
        serverInfo=MCPServerInfo(
            name="koi-knowledge",
            version="3.0.0"
        ),
        instructions="KOI Knowledge Graph - Search 6,400+ documents about regenerative agriculture, Regen Network, and ecological credits using hybrid RAG (RRF + BGE embeddings + keyword search) with confidence-based results."
    )

async def handle_mcp_list_tools(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle MCP tools/list request"""
    return {"tools": MCP_TOOLS}

async def handle_mcp_call_tool(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle MCP tools/call request"""
    tool_name = params.get("name")
    arguments = params.get("arguments", {})

    logger.info(f"MCP tool call: {tool_name} with args: {arguments}")

    if tool_name == "search_knowledge":
        # Call hybrid RAG search
        query_text = arguments.get("query", "")
        agent_id = arguments.get("agent_id")
        limit = arguments.get("limit", 10)

        hybrid_result = await call_hybrid_rag_api(query_text, agent_id, limit)

        if hybrid_result:
            # Format results for MCP response
            results = []
            for r in hybrid_result.get('results', [])[:limit]:
                results.append({
                    "rid": r.get('rid', 'unknown'),
                    "content": r.get('content', ''),
                    "source": r.get('source', 'unknown'),
                    "score": r.get('score', 0.0)
                })

            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "query": query_text,
                        "total_results": len(results),
                        "confidence": hybrid_result.get('confidence', 0.0),
                        "search_method": "hybrid_rag",
                        "results": results
                    }, indent=2)
                }],
                "isError": False
            }
        else:
            # Fallback search
            memories = await fallback_text_search(query_text, limit)
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "query": query_text,
                        "total_results": len(memories),
                        "search_method": "fallback",
                        "results": memories
                    }, indent=2)
                }],
                "isError": False
            }

    elif tool_name == "get_memory":
        rid = arguments.get("rid")
        async with db_pool.acquire() as conn:
            result = await conn.fetchrow("""
                SELECT rid, cid, content, metadata, created_at, version
                FROM koi_memories
                WHERE rid = $1
            """, rid)

            if result:
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "success": True,
                            "memory": {
                                "rid": result['rid'],
                                "cid": result['cid'],
                                "content": result['content'],
                                "metadata": result['metadata'],
                                "created_at": result['created_at'].isoformat() if result['created_at'] else None,
                                "version": result['version']
                            }
                        }, indent=2)
                    }],
                    "isError": False
                }
            else:
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "success": False,
                            "error": f"Memory not found: {rid}"
                        })
                    }],
                    "isError": True
                }

    elif tool_name == "get_stats":
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

            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "stats": {
                            "total_memories": stats['total_memories'],
                            "unique_sensors": stats['unique_sensors'],
                            "memories_with_embeddings": stats['memories_with_embeddings'],
                            "oldest_memory": stats['oldest_memory'].isoformat() if stats['oldest_memory'] else None,
                            "newest_memory": stats['newest_memory'].isoformat() if stats['newest_memory'] else None
                        }
                    }, indent=2)
                }],
                "isError": False
            }

    else:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": f"Unknown tool: {tool_name}"
                })
            }],
            "isError": True
        }

async def handle_mcp_request(request: MCPRequest) -> MCPResponse:
    """Handle MCP JSON-RPC 2.0 request"""
    method = request.method
    params = request.params or {}

    logger.info(f"MCP request: {method}")

    try:
        if method == "initialize":
            result = await handle_mcp_initialize(params)
            return MCPResponse(
                jsonrpc="2.0",
                id=request.id,
                result=result.dict()
            )

        elif method == "tools/list":
            result = await handle_mcp_list_tools(params)
            return MCPResponse(
                jsonrpc="2.0",
                id=request.id,
                result=result
            )

        elif method == "tools/call":
            result = await handle_mcp_call_tool(params)
            return MCPResponse(
                jsonrpc="2.0",
                id=request.id,
                result=result
            )

        elif method == "ping":
            return MCPResponse(
                jsonrpc="2.0",
                id=request.id,
                result={}
            )

        else:
            return MCPResponse(
                jsonrpc="2.0",
                id=request.id,
                error={
                    "code": -32601,
                    "message": f"Method not found: {method}"
                }
            )

    except Exception as e:
        logger.error(f"MCP request error: {e}")
        return MCPResponse(
            jsonrpc="2.0",
            id=request.id,
            error={
                "code": -32603,
                "message": f"Internal error: {str(e)}"
            }
        )

@app.post("/mcp")
async def mcp_post_endpoint(request: Request):
    """
    MCP Streamable HTTP POST endpoint (2025-03-26 specification)
    Handles JSON-RPC 2.0 messages from clients

    Client MUST:
    - Use POST method
    - Include Accept header with both application/json and text/event-stream
    - Send JSON-RPC 2.0 message(s) in body

    Server responds with:
    - Content-Type: application/json for simple responses
    - Content-Type: text/event-stream for streaming (multiple messages)
    """
    accept_header = request.headers.get("accept", "application/json")
    logger.info(f"POST /mcp - Accept header: {accept_header}")

    try:
        body = await request.json()
        logger.info(f"POST /mcp - Request body type: {type(body)}")

        # Handle batch requests
        if isinstance(body, list):
            responses = []
            for req_data in body:
                mcp_req = MCPRequest(**req_data)
                mcp_res = await handle_mcp_request(mcp_req)
                responses.append(mcp_res.dict(exclude_none=True))

            # For batch requests, could use SSE if client prefers
            if "text/event-stream" in accept_header and len(responses) > 1:
                async def sse_stream():
                    for response in responses:
                        yield f"data: {json.dumps(response)}\n\n"
                    yield "event: done\ndata: {}\n\n"

                return StreamingResponse(
                    sse_stream(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no"
                    }
                )
            else:
                return Response(
                    content=json.dumps(responses),
                    media_type="application/json"
                )

        # Handle single request
        else:
            mcp_req = MCPRequest(**body)
            mcp_res = await handle_mcp_request(mcp_req)

            return Response(
                content=json.dumps(mcp_res.dict(exclude_none=True)),
                media_type="application/json"
            )

    except Exception as e:
        logger.error(f"MCP POST endpoint error: {e}", exc_info=True)
        error_response = MCPResponse(
            jsonrpc="2.0",
            error={
                "code": -32700,
                "message": f"Parse error: {str(e)}"
            }
        )
        return Response(
            content=json.dumps(error_response.dict(exclude_none=True)),
            media_type="application/json",
            status_code=400
        )

@app.get("/mcp")
async def mcp_get_endpoint(request: Request):
    """
    MCP Streamable HTTP GET endpoint (optional SSE stream)
    Allows server to send notifications to client

    This is OPTIONAL per spec - servers MAY support it
    Returns SSE stream or 405 Method Not Allowed
    """
    logger.info("GET /mcp - Opening SSE stream for server-to-client messages")

    async def server_event_stream():
        # Per MCP spec, GET SSE stream should only send JSON-RPC messages or keepalive comments
        # Do NOT send custom events - they confuse clients
        # Keep connection alive with pings (SSE comment format)
        while True:
            await asyncio.sleep(30)
            yield ": keepalive\n\n"

    return StreamingResponse(
        server_event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

if __name__ == "__main__":
    import uvicorn

    # Run server
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8200,
        log_level="info"
    )