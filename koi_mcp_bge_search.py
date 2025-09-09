#!/usr/bin/env python3
"""
KOI-MCP BGE Search Server
Connects to existing BGE embeddings in ElizaOS database for RAG operations
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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class BGESearchServer:
    """MCP Server for searching BGE embeddings in ElizaOS"""
    
    def __init__(self, postgres_url: str = "postgresql://postgres:postgres@localhost:5433/eliza"):
        self.postgres_url = postgres_url
        self.db_pool: Optional[asyncpg.Pool] = None
        self.bge_model: Optional[SentenceTransformer] = None
        self.device = torch.device("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
        logger.info(f"Using device: {self.device}")
    
    async def initialize(self):
        """Initialize database connection and load BGE model"""
        logger.info("Initializing BGE Search Server...")
        
        # Create database pool
        self.db_pool = await asyncpg.create_pool(
            self.postgres_url,
            min_size=2,
            max_size=10
        )
        
        # Test connection and get stats
        async with self.db_pool.acquire() as conn:
            # Count BGE embeddings
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM embeddings WHERE dim_1024 IS NOT NULL"
            )
            logger.info(f"Connected to database with {count} BGE embeddings")
        
        # Load BGE model
        logger.info("Loading BGE model: BAAI/bge-large-en-v1.5")
        self.bge_model = SentenceTransformer("BAAI/bge-large-en-v1.5", device=self.device)
        logger.info("BGE model loaded successfully")
    
    async def search(self, query: str, top_k: int = 10, 
                     agent_id: Optional[str] = None,
                     room_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search for similar documents using BGE embeddings"""
        
        # Generate query embedding
        logger.info(f"Searching for: {query[:100]}...")
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
        
        # Convert embedding to string format for PostgreSQL
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
            # Parse the content JSON
            content_data = json.loads(row["content"]) if isinstance(row["content"], str) else row["content"]
            
            # Extract text from content
            text = content_data.get("text", "")
            if isinstance(text, dict):
                # Handle case where text might be nested
                text = json.dumps(text)
            
            # Extract metadata
            metadata = {
                "embedding_id": str(row["embedding_id"]),
                "memory_id": str(row["memory_id"]),
                "agent_id": row["agent_id"],
                "room_id": row["room_id"],
                "entity_id": str(row["entity_id"]) if row["entity_id"] else None
            }
            
            # Add any additional metadata from content
            for key in ["doc_id", "chunk_id", "chunk_index", "source_file", "source_type", "token_count"]:
                if key in content_data:
                    metadata[key] = content_data[key]
            
            results.append({
                "text": text[:1000],  # Limit text length for display
                "similarity": float(row["similarity"]),
                "metadata": metadata
            })
        
        logger.info(f"Found {len(results)} results")
        return results
    
    async def get_statistics(self) -> Dict[str, Any]:
        """Get statistics about the BGE embeddings"""
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
            
            # Get sample of agent IDs
            agents = await conn.fetch("""
                SELECT DISTINCT m."agentId", COUNT(*) as count
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
                "model": "BAAI/bge-large-en-v1.5"
            }
    
    async def cleanup(self):
        """Clean up resources"""
        if self.db_pool:
            await self.db_pool.close()

async def test_search():
    """Test the BGE search functionality"""
    server = BGESearchServer()
    await server.initialize()
    
    try:
        # Get statistics
        stats = await server.get_statistics()
        logger.info("=== BGE Embeddings Statistics ===")
        logger.info(f"Total embeddings: {stats['total_bge_embeddings']}")
        logger.info(f"Unique agents: {stats['unique_agents']}")
        logger.info(f"Top agents: {stats['top_agents']}")
        
        # Test searches
        test_queries = [
            "What is regenerative agriculture?",
            "How do carbon credits work?",
            "Tell me about Regen Network",
            "What is soil health?",
            "Climate change solutions"
        ]
        
        for query in test_queries:
            logger.info(f"\n=== Searching: {query} ===")
            results = await server.search(query, top_k=3)
            
            for i, result in enumerate(results, 1):
                logger.info(f"\n{i}. Similarity: {result['similarity']:.3f}")
                logger.info(f"   Text: {result['text'][:200]}...")
                if "source_type" in result["metadata"]:
                    logger.info(f"   Source: {result['metadata']['source_type']}")
                if "doc_id" in result["metadata"]:
                    logger.info(f"   Doc ID: {result['metadata']['doc_id']}")
    
    finally:
        await server.cleanup()

if __name__ == "__main__":
    asyncio.run(test_search())