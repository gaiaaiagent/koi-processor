#!/usr/bin/env python3
"""
KOI-MCP Server
A Model Context Protocol server that acts as a KOI Processor Node for RAG operations
Integrates BGE embeddings with PostgreSQL pgvector for the RegenAI knowledge system
"""

import asyncio
import json
import hashlib
from typing import List, Dict, Any, Optional
from datetime import datetime
import numpy as np
from dataclasses import dataclass, asdict
import logging

# MCP imports
from mcp.server import Server
from mcp.types import Tool, TextContent, ToolResult, ErrorResult

# Database and embedding imports
import asyncpg
from sentence_transformers import SentenceTransformer
import torch

# KOI system imports
from koi_types import NodeType, Event, FUNState, CATReceipt, RID

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@dataclass
class KOIConfig:
    """Configuration for KOI-MCP Server"""
    node_name: str = "regen-mcp-rag-processor"
    node_type: NodeType = NodeType.FULL
    postgres_url: str = "postgresql://postgres:postgres@localhost:5433/eliza"
    bge_model: str = "BAAI/bge-large-en-v1.5"
    embedding_dim: int = 1024
    max_chunks: int = 10
    similarity_threshold: float = 0.7
    enable_hybrid_search: bool = True
    enable_graph_rag: bool = False
    fuseki_url: Optional[str] = "http://localhost:3030/regen"

@dataclass
class SearchResult:
    """Result from a RAG search operation"""
    rid: str
    content: str
    similarity: float
    metadata: Dict[str, Any]
    source: str
    chunk_index: int

class KOIMCPServer:
    """MCP Server that operates as a KOI Processor Node"""
    
    def __init__(self, config: KOIConfig):
        self.config = config
        self.server = Server("koi-mcp-rag")
        self.db_pool: Optional[asyncpg.Pool] = None
        self.bge_model: Optional[SentenceTransformer] = None
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        
        # KOI state
        self.node_rid = f"orn:koi.node:{config.node_name}"
        self.events_emitted = 0
        self.last_cat_receipt: Optional[CATReceipt] = None
        
        # Register MCP tools
        self._register_tools()
    
    def _register_tools(self):
        """Register MCP tools for RAG operations"""
        
        # Main search tool
        self.server.add_tool(Tool(
            name="koi_search",
            description="Search the RegenAI knowledge base using BGE embeddings",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return",
                        "default": 10
                    },
                    "strategy": {
                        "type": "string",
                        "enum": ["bge", "hybrid", "graph"],
                        "description": "Search strategy to use",
                        "default": "bge"
                    },
                    "filters": {
                        "type": "object",
                        "description": "Optional metadata filters"
                    }
                },
                "required": ["query"]
            }
        ))
        
        # Embedding generation tool
        self.server.add_tool(Tool(
            name="generate_embedding",
            description="Generate BGE embedding for text",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to embed"
                    },
                    "normalize": {
                        "type": "boolean",
                        "description": "Whether to normalize the embedding",
                        "default": True
                    }
                },
                "required": ["text"]
            }
        ))
        
        # KOI event inspection tool
        self.server.add_tool(Tool(
            name="koi_status",
            description="Get KOI node status and statistics",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ))
        
        # Knowledge fragment retrieval
        self.server.add_tool(Tool(
            name="get_fragment",
            description="Retrieve a specific knowledge fragment by RID",
            inputSchema={
                "type": "object",
                "properties": {
                    "rid": {
                        "type": "string",
                        "description": "Resource Identifier of the fragment"
                    }
                },
                "required": ["rid"]
            }
        ))
    
    async def initialize(self):
        """Initialize database connection and load models"""
        logger.info(f"Initializing KOI-MCP Server: {self.node_rid}")
        
        # Create database pool
        self.db_pool = await asyncpg.create_pool(
            self.config.postgres_url,
            min_size=2,
            max_size=10
        )
        
        # Load BGE model
        logger.info(f"Loading BGE model: {self.config.bge_model}")
        self.bge_model = SentenceTransformer(self.config.bge_model, device=self.device)
        
        # Emit initialization event
        await self._emit_event("NodeInitialized", {
            "node_rid": self.node_rid,
            "capabilities": ["RAGQuery", "Embedding", "KnowledgeFragment"],
            "embedding_model": self.config.bge_model,
            "embedding_dim": self.config.embedding_dim
        })
        
        logger.info("KOI-MCP Server initialized successfully")
    
    async def _emit_event(self, event_type: str, data: Dict[str, Any]):
        """Emit a KOI event"""
        event = Event(
            rid=f"orn:koi.event:{hashlib.sha256(f'{event_type}{self.events_emitted}'.encode()).hexdigest()[:8]}",
            event_type=event_type,
            timestamp=datetime.utcnow().isoformat(),
            source_node=self.node_rid,
            data=data
        )
        self.events_emitted += 1
        
        # In production, this would publish to KOI event stream
        logger.debug(f"Emitted event: {event_type} - {event.rid}")
        return event
    
    def _create_cat_receipt(self, input_rid: str, output_rids: List[str], operation: str) -> CATReceipt:
        """Create a CAT receipt for provenance tracking"""
        receipt = CATReceipt(
            receipt_id=f"orn:cat:{hashlib.sha256(f'{input_rid}{operation}{datetime.utcnow().isoformat()}'.encode()).hexdigest()[:8]}",
            timestamp=datetime.utcnow().isoformat(),
            operation=operation,
            input_rids=[input_rid],
            output_rids=output_rids,
            metadata={
                "node": self.node_rid,
                "embedding_model": self.config.bge_model,
                "dimension": self.config.embedding_dim
            }
        )
        self.last_cat_receipt = receipt
        return receipt
    
    async def handle_koi_search(self, query: str, top_k: int = 10, 
                               strategy: str = "bge", filters: Optional[Dict] = None) -> ToolResult:
        """Handle RAG search requests"""
        try:
            # Generate query RID
            query_rid = f"orn:regen.query:{hashlib.sha256(query.encode()).hexdigest()[:8]}"
            
            # Emit query event
            await self._emit_event("RAGQueryStarted", {
                "query_rid": query_rid,
                "query": query,
                "strategy": strategy,
                "top_k": top_k
            })
            
            # Perform search based on strategy
            if strategy == "bge":
                results = await self._search_bge(query, top_k, filters)
            elif strategy == "hybrid":
                results = await self._search_hybrid(query, top_k, filters)
            elif strategy == "graph":
                results = await self._search_graph(query, top_k, filters)
            else:
                raise ValueError(f"Unknown search strategy: {strategy}")
            
            # Create CAT receipt
            output_rids = [r.rid for r in results]
            cat_receipt = self._create_cat_receipt(query_rid, output_rids, f"rag_search_{strategy}")
            
            # Emit completion event
            await self._emit_event("RAGQueryCompleted", {
                "query_rid": query_rid,
                "results_count": len(results),
                "cat_receipt": asdict(cat_receipt)
            })
            
            # Format response
            response = {
                "query_rid": query_rid,
                "chunks": [asdict(r) for r in results],
                "cat_receipt": asdict(cat_receipt),
                "metadata": {
                    "strategy": strategy,
                    "model": self.config.bge_model,
                    "dimension": self.config.embedding_dim,
                    "timestamp": datetime.utcnow().isoformat()
                }
            }
            
            return ToolResult(content=[TextContent(text=json.dumps(response, indent=2))])
            
        except Exception as e:
            logger.error(f"Search error: {e}")
            return ErrorResult(error=str(e))
    
    async def _search_bge(self, query: str, top_k: int, filters: Optional[Dict]) -> List[SearchResult]:
        """Perform BGE embedding-based search"""
        # Generate query embedding
        query_embedding = self.bge_model.encode(query, normalize_embeddings=True)
        
        # Build SQL query
        sql = """
            SELECT 
                id,
                content,
                metadata,
                embedding <=> $1::vector as distance,
                1 - (embedding <=> $1::vector) as similarity
            FROM embeddings
            WHERE 1=1
        """
        
        params = [query_embedding.tolist()]
        param_count = 2
        
        # Add filters if provided
        if filters:
            if "agent_id" in filters:
                sql += f" AND metadata->>'agent_id' = ${param_count}"
                params.append(filters["agent_id"])
                param_count += 1
            if "source" in filters:
                sql += f" AND metadata->>'source' = ${param_count}"
                params.append(filters["source"])
                param_count += 1
        
        sql += f" ORDER BY distance LIMIT ${param_count}"
        params.append(top_k)
        
        # Execute query
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        
        # Convert to SearchResult objects
        results = []
        for row in rows:
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
            results.append(SearchResult(
                rid=f"orn:regen.fragment:{row['id']}",
                content=row["content"],
                similarity=float(row["similarity"]),
                metadata=metadata,
                source=metadata.get("source", "unknown"),
                chunk_index=metadata.get("chunk_index", 0)
            ))
        
        return results
    
    async def _search_hybrid(self, query: str, top_k: int, filters: Optional[Dict]) -> List[SearchResult]:
        """Perform hybrid search combining BGE embeddings and BM25"""
        # Get BGE results (70% weight)
        bge_results = await self._search_bge(query, top_k * 2, filters)
        
        # Get BM25 results (30% weight) - simplified text search for now
        sql = """
            SELECT 
                id,
                content,
                metadata,
                ts_rank(to_tsvector('english', content), plainto_tsquery('english', $1)) as rank
            FROM embeddings
            WHERE to_tsvector('english', content) @@ plainto_tsquery('english', $1)
        """
        
        params = [query]
        if filters:
            if "agent_id" in filters:
                sql += " AND metadata->>'agent_id' = $2"
                params.append(filters["agent_id"])
        
        sql += " ORDER BY rank DESC LIMIT $" + str(len(params) + 1)
        params.append(top_k * 2)
        
        async with self.db_pool.acquire() as conn:
            bm25_rows = await conn.fetch(sql, *params)
        
        # Combine and rerank
        combined_scores = {}
        
        # Add BGE results with 0.7 weight
        for i, result in enumerate(bge_results):
            combined_scores[result.rid] = {
                "result": result,
                "score": result.similarity * 0.7
            }
        
        # Add BM25 results with 0.3 weight
        for row in bm25_rows:
            rid = f"orn:regen.fragment:{row['id']}"
            if rid in combined_scores:
                combined_scores[rid]["score"] += float(row["rank"]) * 0.3
            else:
                metadata = json.loads(row["metadata"]) if row["metadata"] else {}
                combined_scores[rid] = {
                    "result": SearchResult(
                        rid=rid,
                        content=row["content"],
                        similarity=float(row["rank"]),
                        metadata=metadata,
                        source=metadata.get("source", "unknown"),
                        chunk_index=metadata.get("chunk_index", 0)
                    ),
                    "score": float(row["rank"]) * 0.3
                }
        
        # Sort by combined score and return top_k
        sorted_results = sorted(combined_scores.values(), key=lambda x: x["score"], reverse=True)
        return [r["result"] for r in sorted_results[:top_k]]
    
    async def _search_graph(self, query: str, top_k: int, filters: Optional[Dict]) -> List[SearchResult]:
        """Perform graph-based RAG search using SPARQL"""
        # This would integrate with Apache Jena Fuseki
        # For now, returning empty as it requires SPARQL setup
        logger.warning("Graph RAG not yet implemented, falling back to BGE search")
        return await self._search_bge(query, top_k, filters)
    
    async def handle_generate_embedding(self, text: str, normalize: bool = True) -> ToolResult:
        """Generate BGE embedding for text"""
        try:
            # Generate embedding
            embedding = self.bge_model.encode(text, normalize_embeddings=normalize)
            
            # Create RID for this embedding
            text_rid = f"orn:regen.text:{hashlib.sha256(text.encode()).hexdigest()[:8]}"
            embedding_rid = f"orn:regen.embedding:{hashlib.sha256(embedding.tobytes()).hexdigest()[:8]}"
            
            # Create CAT receipt
            cat_receipt = self._create_cat_receipt(text_rid, [embedding_rid], "bge_embedding")
            
            response = {
                "text_rid": text_rid,
                "embedding_rid": embedding_rid,
                "embedding": embedding.tolist(),
                "dimension": len(embedding),
                "normalized": normalize,
                "model": self.config.bge_model,
                "cat_receipt": asdict(cat_receipt)
            }
            
            return ToolResult(content=[TextContent(text=json.dumps(response, indent=2))])
            
        except Exception as e:
            logger.error(f"Embedding generation error: {e}")
            return ErrorResult(error=str(e))
    
    async def handle_koi_status(self) -> ToolResult:
        """Get KOI node status"""
        try:
            # Get database statistics
            async with self.db_pool.acquire() as conn:
                embedding_count = await conn.fetchval("SELECT COUNT(*) FROM embeddings")
                agent_count = await conn.fetchval("SELECT COUNT(DISTINCT metadata->>'agent_id') FROM embeddings")
            
            status = {
                "node_rid": self.node_rid,
                "node_name": self.config.node_name,
                "node_type": self.config.node_type.value,
                "status": "operational",
                "statistics": {
                    "embeddings_stored": embedding_count,
                    "agents_served": agent_count,
                    "events_emitted": self.events_emitted,
                    "embedding_model": self.config.bge_model,
                    "embedding_dimension": self.config.embedding_dim,
                    "device": str(self.device)
                },
                "capabilities": {
                    "bge_search": True,
                    "hybrid_search": self.config.enable_hybrid_search,
                    "graph_rag": self.config.enable_graph_rag
                },
                "last_cat_receipt": asdict(self.last_cat_receipt) if self.last_cat_receipt else None
            }
            
            return ToolResult(content=[TextContent(text=json.dumps(status, indent=2))])
            
        except Exception as e:
            logger.error(f"Status check error: {e}")
            return ErrorResult(error=str(e))
    
    async def handle_get_fragment(self, rid: str) -> ToolResult:
        """Retrieve a specific knowledge fragment"""
        try:
            # Extract fragment ID from RID
            fragment_id = rid.split(":")[-1]
            
            sql = """
                SELECT id, content, metadata, embedding
                FROM embeddings
                WHERE id = $1
            """
            
            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow(sql, fragment_id)
            
            if not row:
                return ErrorResult(error=f"Fragment not found: {rid}")
            
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
            
            response = {
                "rid": rid,
                "content": row["content"],
                "metadata": metadata,
                "has_embedding": row["embedding"] is not None,
                "source": metadata.get("source", "unknown"),
                "timestamp": metadata.get("timestamp", "unknown")
            }
            
            return ToolResult(content=[TextContent(text=json.dumps(response, indent=2))])
            
        except Exception as e:
            logger.error(f"Fragment retrieval error: {e}")
            return ErrorResult(error=str(e))
    
    async def run(self):
        """Run the MCP server"""
        await self.initialize()
        
        # Set up tool handlers
        @self.server.tool_handler("koi_search")
        async def search_handler(arguments):
            return await self.handle_koi_search(**arguments)
        
        @self.server.tool_handler("generate_embedding")
        async def embedding_handler(arguments):
            return await self.handle_generate_embedding(**arguments)
        
        @self.server.tool_handler("koi_status")
        async def status_handler(arguments):
            return await self.handle_koi_status()
        
        @self.server.tool_handler("get_fragment")
        async def fragment_handler(arguments):
            return await self.handle_get_fragment(**arguments)
        
        # Start server
        logger.info(f"Starting KOI-MCP Server on stdio")
        async with self.server:
            await self.server.run()

async def main():
    """Main entry point"""
    # Load configuration (could be from environment or config file)
    config = KOIConfig()
    
    # Override from environment if available
    import os
    if "POSTGRES_URL" in os.environ:
        config.postgres_url = os.environ["POSTGRES_URL"]
    if "BGE_MODEL" in os.environ:
        config.bge_model = os.environ["BGE_MODEL"]
    
    # Create and run server
    server = KOIMCPServer(config)
    await server.run()

if __name__ == "__main__":
    asyncio.run(main())