#!/usr/bin/env python3
"""
KOI-MCP BGE Server
MCP server that provides BGE-based semantic search for ElizaOS agents
"""

import asyncio
import json
import hashlib
from typing import List, Dict, Any, Optional
from datetime import datetime
import numpy as np
import asyncpg
from sentence_transformers import SentenceTransformer
import torch
import logging
from mcp import Server, Tool, ToolResult
from mcp.server.stdio import stdio_server

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class KOIMCPBGEServer:
    """MCP Server for BGE-based semantic search in ElizaOS"""
    
    def __init__(self, postgres_url: str = "postgresql://postgres:postgres@localhost:5433/eliza"):
        self.postgres_url = postgres_url
        self.db_pool: Optional[asyncpg.Pool] = None
        self.bge_model: Optional[SentenceTransformer] = None
        self.device = torch.device("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
        self.server = Server("koi-bge-search")
        
        # Register MCP tools
        self._register_tools()
    
    def _register_tools(self):
        """Register MCP tools"""
        
        @self.server.list_tools()
        async def list_tools() -> List[Tool]:
            return [
                Tool(
                    name="bge_search",
                    description="Search for similar documents using BGE embeddings",
                    parameters={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The search query"
                            },
                            "top_k": {
                                "type": "integer",
                                "description": "Number of results to return",
                                "default": 10
                            },
                            "agent_id": {
                                "type": "string",
                                "description": "Filter by agent ID (optional)"
                            },
                            "room_id": {
                                "type": "string",
                                "description": "Filter by room ID (optional)"
                            }
                        },
                        "required": ["query"]
                    }
                ),
                Tool(
                    name="bge_stats",
                    description="Get statistics about BGE embeddings in the database",
                    parameters={"type": "object", "properties": {}}
                )
            ]
        
        @self.server.call_tool()
        async def call_tool(name: str, arguments: Dict[str, Any]) -> ToolResult:
            if name == "bge_search":
                return await self._handle_search(
                    query=arguments["query"],
                    top_k=arguments.get("top_k", 10),
                    agent_id=arguments.get("agent_id"),
                    room_id=arguments.get("room_id")
                )
            elif name == "bge_stats":
                return await self._handle_stats()
            else:
                return ToolResult(error=f"Unknown tool: {name}")
    
    async def initialize(self):
        """Initialize database connection and load BGE model"""
        logger.info("Initializing KOI-MCP BGE Server...")
        
        # Create database pool
        self.db_pool = await asyncpg.create_pool(
            self.postgres_url,
            min_size=2,
            max_size=10
        )
        
        # Test connection and get stats
        async with self.db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM embeddings WHERE dim_1024 IS NOT NULL"
            )
            logger.info(f"Connected to database with {count} BGE embeddings")
        
        # Load BGE model
        logger.info("Loading BGE model: BAAI/bge-large-en-v1.5")
        self.bge_model = SentenceTransformer("BAAI/bge-large-en-v1.5", device=self.device)
        logger.info("BGE model loaded successfully")
    
    async def _handle_search(self, query: str, top_k: int = 10,
                            agent_id: Optional[str] = None,
                            room_id: Optional[str] = None) -> ToolResult:
        """Handle BGE search request"""
        try:
            # Generate query embedding
            query_embedding = self.bge_model.encode(query, normalize_embeddings=True)
            
            # Build SQL query
            sql = """
                SELECT 
                    e.id as embedding_id,
                    m.id as memory_id,
                    m.content,
                    m."entityId" as entity_id,
                    m."agentId" as agent_id,
                    m."roomId" as room_id,
                    1 - (e.dim_1024 <=> $1) as similarity
                FROM embeddings e
                JOIN memories m ON e.memory_id = m.id
                WHERE e.dim_1024 IS NOT NULL
            """
            
            # Convert embedding to PostgreSQL vector format
            embedding_str = '[' + ','.join(map(str, query_embedding.tolist())) + ']'
            params = [embedding_str]
            param_count = 2
            
            # Add filters if provided
            if agent_id:
                sql += f' AND m."agentId" = ${param_count}'
                params.append(agent_id)
                param_count += 1
            
            if room_id:
                sql += f' AND m."roomId" = ${param_count}'
                params.append(room_id)
                param_count += 1
            
            sql += f" ORDER BY e.dim_1024 <=> $1::vector LIMIT ${param_count}"
            params.append(top_k)
            
            # Execute query
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch(sql, *params)
            
            # Format results
            results = []
            for row in rows:
                # Parse content JSON
                content_data = json.loads(row["content"]) if isinstance(row["content"], str) else row["content"]
                
                # Extract text
                text = content_data.get("text", "")
                if isinstance(text, dict):
                    text = json.dumps(text)
                
                # Build metadata
                metadata = {
                    "embedding_id": str(row["embedding_id"]),
                    "memory_id": str(row["memory_id"]),
                    "agent_id": row["agent_id"],
                    "room_id": row["room_id"],
                    "entity_id": str(row["entity_id"]) if row["entity_id"] else None,
                    "similarity": float(row["similarity"])
                }
                
                # Add source metadata if available
                for key in ["doc_id", "chunk_id", "chunk_index", "source_file", "source_type", "token_count"]:
                    if key in content_data:
                        metadata[key] = content_data[key]
                
                results.append({
                    "text": text[:1000],  # Limit text length
                    "metadata": metadata
                })
            
            return ToolResult(
                success=True,
                result={
                    "query": query,
                    "count": len(results),
                    "results": results
                }
            )
            
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return ToolResult(error=str(e))
    
    async def _handle_stats(self) -> ToolResult:
        """Handle statistics request"""
        try:
            async with self.db_pool.acquire() as conn:
                stats = await conn.fetchrow("""
                    SELECT 
                        COUNT(DISTINCT e.id) as total_embeddings,
                        COUNT(DISTINCT m."agentId") as unique_agents,
                        COUNT(DISTINCT m."roomId") as unique_rooms,
                        COUNT(DISTINCT m."entityId") as unique_entities
                    FROM embeddings e
                    JOIN memories m ON e.memory_id = m.id
                    WHERE e.dim_1024 IS NOT NULL
                """)
                
                # Get top agents
                agents = await conn.fetch("""
                    SELECT DISTINCT m."agentId", COUNT(*) as count
                    FROM embeddings e
                    JOIN memories m ON e.memory_id = m.id
                    WHERE e.dim_1024 IS NOT NULL AND m."agentId" IS NOT NULL
                    GROUP BY m."agentId"
                    ORDER BY count DESC
                    LIMIT 5
                """)
                
                return ToolResult(
                    success=True,
                    result={
                        "total_bge_embeddings": stats["total_embeddings"],
                        "unique_agents": stats["unique_agents"],
                        "unique_rooms": stats["unique_rooms"],
                        "unique_entities": stats["unique_entities"],
                        "top_agents": [
                            {"agent_id": str(a["agentId"]), "count": a["count"]}
                            for a in agents
                        ],
                        "embedding_dimension": 1024,
                        "model": "BAAI/bge-large-en-v1.5"
                    }
                )
        except Exception as e:
            logger.error(f"Stats failed: {e}")
            return ToolResult(error=str(e))
    
    async def cleanup(self):
        """Clean up resources"""
        if self.db_pool:
            await self.db_pool.close()
    
    async def run(self):
        """Run the MCP server"""
        await self.initialize()
        
        try:
            # Run the stdio server
            async with stdio_server() as (read_stream, write_stream):
                await self.server.run(read_stream, write_stream)
        finally:
            await self.cleanup()

async def main():
    """Main entry point"""
    server = KOIMCPBGEServer()
    await server.run()

if __name__ == "__main__":
    asyncio.run(main())