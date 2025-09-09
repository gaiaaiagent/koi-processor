#!/usr/bin/env python3
"""
MCP Server for BGE Semantic Search - Final Version
Provides semantic search capabilities using BGE embeddings stored in PostgreSQL
"""
import os
import sys
import json
import asyncio
import logging
from typing import List, Dict, Any, Optional
import asyncpg
from sentence_transformers import SentenceTransformer
from mcp.server.stdio import stdio_server
from mcp.server import Server
from mcp.types import TextContent, Tool

# Set up logging to stderr
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

class BGESearchMCPServer:
    def __init__(self):
        self.db_pool = None
        self.bge_model = None
        self.initialized = False
        self.initialization_lock = asyncio.Lock()
        self.server = Server("bge-search")
        self._register_handlers()
        
    def _register_handlers(self):
        """Register MCP handlers"""
        
        @self.server.list_tools()
        async def list_tools() -> List[Tool]:
            """List available tools"""
            await self.ensure_initialized()
            return [
                Tool(
                    name="bge_search",
                    description="Search for semantically similar content using BGE embeddings",
                    inputSchema={
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
                                "description": "Optional: Filter by agent ID"
                            },
                            "room_id": {
                                "type": "string",
                                "description": "Optional: Filter by room ID"
                            }
                        },
                        "required": ["query"]
                    }
                ),
                Tool(
                    name="bge_stats",
                    description="Get statistics about BGE embeddings in the database",
                    inputSchema={
                        "type": "object",
                        "properties": {}
                    }
                )
            ]
        
        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict) -> List[TextContent]:
            """Handle tool calls"""
            await self.ensure_initialized()
            try:
                if name == "bge_search":
                    result = await self.search_embeddings(
                        query=arguments["query"],
                        top_k=arguments.get("top_k", 10),
                        agent_id=arguments.get("agent_id"),
                        room_id=arguments.get("room_id")
                    )
                elif name == "bge_stats":
                    result = await self.get_stats()
                else:
                    result = {"error": f"Unknown tool: {name}"}
                
                return [TextContent(
                    type="text",
                    text=json.dumps(result, indent=2)
                )]
            except Exception as e:
                logger.error(f"Tool execution failed: {e}", exc_info=True)
                return [TextContent(
                    type="text",
                    text=json.dumps({"error": str(e)})
                )]
    
    async def ensure_initialized(self):
        """Ensure server is initialized before handling requests"""
        if not self.initialized:
            await self.initialize()
    
    async def initialize(self):
        """Initialize the server resources"""
        async with self.initialization_lock:
            if self.initialized:
                return
                
            try:
                logger.info("Starting BGE MCP server initialization...")
                
                # Load BGE model first (this takes time)
                logger.info("Loading BGE model (this may take a moment)...")
                self.bge_model = SentenceTransformer('BAAI/bge-large-en-v1.5')
                logger.info("BGE model loaded successfully")
                
                # Connect to database
                db_url = os.getenv('POSTGRES_URL', 'postgresql://postgres:postgres@localhost:5433/eliza')
                logger.info(f"Connecting to database...")
                
                self.db_pool = await asyncpg.create_pool(
                    db_url,
                    min_size=1,
                    max_size=5,
                    command_timeout=60
                )
                
                # Test the connection
                async with self.db_pool.acquire() as conn:
                    result = await conn.fetchval("SELECT COUNT(*) FROM embeddings WHERE dim_1024 IS NOT NULL")
                    logger.info(f"Database connected. Found {result} BGE embeddings")
                
                self.initialized = True
                logger.info("BGE MCP server initialization complete")
                
            except Exception as e:
                logger.error(f"Initialization failed: {e}", exc_info=True)
                raise
    
    async def cleanup(self):
        """Clean up resources"""
        if self.db_pool:
            await self.db_pool.close()
            logger.info("Database pool closed")
    
    async def search_embeddings(self, query: str, top_k: int = 10, 
                               agent_id: Optional[str] = None,
                               room_id: Optional[str] = None) -> Dict[str, Any]:
        """Search for similar embeddings using BGE"""
        try:
            # Generate query embedding
            logger.debug(f"Generating embedding for query: {query[:100]}...")
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
            
            params = []
            param_count = 1
            
            # Convert embedding to PostgreSQL vector format
            embedding_str = '[' + ','.join(map(str, query_embedding.tolist())) + ']'
            params.append(embedding_str)
            param_count += 1
            
            # Add optional filters
            if agent_id:
                sql += f" AND m.\"agentId\" = ${param_count}"
                params.append(agent_id)
                param_count += 1
            
            if room_id:
                sql += f" AND m.\"roomId\" = ${param_count}"
                params.append(room_id)
                param_count += 1
            
            # Order and limit
            sql += f"""
                ORDER BY similarity DESC
                LIMIT ${param_count}
            """
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
            
            return {
                "query": query,
                "count": len(results),
                "results": results
            }
            
        except Exception as e:
            logger.error(f"Search failed: {e}", exc_info=True)
            return {"error": str(e)}
    
    async def get_stats(self) -> Dict[str, Any]:
        """Get statistics about the BGE embeddings"""
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
                    SELECT m."agentId", COUNT(*) as count
                    FROM embeddings e
                    JOIN memories m ON e.memory_id = m.id
                    WHERE e.dim_1024 IS NOT NULL AND m."agentId" IS NOT NULL
                    GROUP BY m."agentId"
                    ORDER BY count DESC
                    LIMIT 5
                """)
                
                return {
                    "total_bge_embeddings": stats["total_embeddings"],
                    "unique_agents": stats["unique_agents"],
                    "unique_rooms": stats["unique_rooms"],
                    "unique_entities": stats["unique_entities"],
                    "top_agents": [
                        {"agent_id": str(a["agentId"]), "count": a["count"]}
                        for a in agents
                    ],
                    "embedding_dimension": 1024,
                    "model": "BAAI/bge-large-en-v1.5",
                    "initialized": self.initialized
                }
        except Exception as e:
            logger.error(f"Stats failed: {e}", exc_info=True)
            return {"error": str(e)}
    
    async def run(self):
        """Run the MCP server"""
        # Pre-initialize before starting stdio
        await self.initialize()
        
        try:
            # Run the stdio server
            async with stdio_server() as (read_stream, write_stream):
                logger.info("BGE MCP server ready and waiting for requests...")
                await self.server.run(read_stream, write_stream)
        finally:
            await self.cleanup()

async def main():
    """Main entry point for the MCP server"""
    server = BGESearchMCPServer()
    await server.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server shutdown requested")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)