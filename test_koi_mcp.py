#!/usr/bin/env python3
"""
Test script for KOI-MCP Server
Tests PostgreSQL connection and BGE embedding functionality
"""

import asyncio
import asyncpg
import json
import os
from sentence_transformers import SentenceTransformer
import numpy as np
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_postgres_connection():
    """Test PostgreSQL connection and pgvector extension"""
    logger.info("Testing PostgreSQL connection...")
    
    postgres_url = os.getenv("POSTGRES_URL", "postgresql://postgres:postgres@localhost:5433/eliza")
    
    try:
        # Connect to database
        conn = await asyncpg.connect(postgres_url)
        logger.info("✅ Connected to PostgreSQL")
        
        # Check pgvector extension
        result = await conn.fetchval("SELECT COUNT(*) FROM pg_extension WHERE extname = 'vector'")
        if result > 0:
            logger.info("✅ pgvector extension is installed")
        else:
            logger.warning("⚠️ pgvector extension not found, attempting to create...")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            logger.info("✅ pgvector extension created")
        
        # Check embeddings table
        table_exists = await conn.fetchval("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'embeddings'
            )
        """)
        
        if table_exists:
            # Get statistics
            count = await conn.fetchval("SELECT COUNT(*) FROM embeddings")
            logger.info(f"✅ Embeddings table exists with {count} records")
            
            # Check for BGE embeddings (1024 dimensions)
            bge_count = await conn.fetchval("""
                SELECT COUNT(*) FROM embeddings 
                WHERE array_length(embedding::real[], 1) = 1024
            """)
            logger.info(f"   Found {bge_count} BGE embeddings (1024-dim)")
            
            # Get sample embedding metadata
            sample = await conn.fetchrow("""
                SELECT id, metadata, array_length(embedding::real[], 1) as dim
                FROM embeddings 
                LIMIT 1
            """)
            if sample:
                logger.info(f"   Sample: ID={sample['id'][:8]}..., Dimension={sample['dim']}")
                if sample['metadata']:
                    metadata = json.loads(sample['metadata'])
                    logger.info(f"   Metadata keys: {list(metadata.keys())}")
        else:
            logger.warning("⚠️ Embeddings table not found, creating...")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS embeddings (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    embedding vector(1024),
                    metadata JSONB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS embeddings_embedding_idx 
                ON embeddings USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 100)
            """)
            logger.info("✅ Created embeddings table with indexes")
        
        await conn.close()
        return True
        
    except Exception as e:
        logger.error(f"❌ PostgreSQL connection failed: {e}")
        return False

async def test_bge_embeddings():
    """Test BGE embedding model"""
    logger.info("\nTesting BGE embeddings...")
    
    try:
        # Load model
        model_name = "BAAI/bge-large-en-v1.5"
        logger.info(f"Loading {model_name}...")
        model = SentenceTransformer(model_name)
        logger.info("✅ Model loaded successfully")
        
        # Test embedding generation
        test_texts = [
            "What is regenerative agriculture?",
            "How do carbon credits work?",
            "Tell me about soil health"
        ]
        
        embeddings = model.encode(test_texts, normalize_embeddings=True)
        logger.info(f"✅ Generated {len(embeddings)} embeddings")
        logger.info(f"   Embedding shape: {embeddings[0].shape}")
        logger.info(f"   Dimension: {len(embeddings[0])}")
        
        # Test similarity
        similarities = np.dot(embeddings, embeddings.T)
        logger.info("   Similarity matrix:")
        for i, text in enumerate(test_texts):
            logger.info(f"   '{text[:30]}...'")
            for j, other in enumerate(test_texts):
                if i != j:
                    logger.info(f"      → '{other[:30]}...': {similarities[i][j]:.3f}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ BGE embedding test failed: {e}")
        return False

async def test_vector_search():
    """Test vector similarity search"""
    logger.info("\nTesting vector search...")
    
    postgres_url = os.getenv("POSTGRES_URL", "postgresql://postgres:postgres@localhost:5433/eliza")
    
    try:
        conn = await asyncpg.connect(postgres_url)
        
        # Check if we have any embeddings
        count = await conn.fetchval("SELECT COUNT(*) FROM embeddings WHERE embedding IS NOT NULL")
        
        if count == 0:
            logger.warning("⚠️ No embeddings found in database, inserting test data...")
            
            # Generate test embeddings
            model = SentenceTransformer("BAAI/bge-large-en-v1.5")
            test_docs = [
                "Regenerative agriculture is a holistic farming approach",
                "Carbon credits represent one ton of CO2 removed from atmosphere",
                "Healthy soil contains billions of microorganisms"
            ]
            
            for i, doc in enumerate(test_docs):
                embedding = model.encode(doc, normalize_embeddings=True)
                await conn.execute("""
                    INSERT INTO embeddings (id, content, embedding, metadata)
                    VALUES ($1, $2, $3::vector, $4)
                    ON CONFLICT (id) DO UPDATE
                    SET embedding = $3::vector, updated_at = CURRENT_TIMESTAMP
                """, f"test_{i}", doc, embedding.tolist(), json.dumps({"source": "test"}))
            
            logger.info(f"✅ Inserted {len(test_docs)} test embeddings")
        
        # Perform similarity search
        query = "What is carbon sequestration?"
        model = SentenceTransformer("BAAI/bge-large-en-v1.5")
        query_embedding = model.encode(query, normalize_embeddings=True)
        
        results = await conn.fetch("""
            SELECT 
                id,
                content,
                1 - (embedding <=> $1::vector) as similarity
            FROM embeddings
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> $1::vector
            LIMIT 5
        """, query_embedding.tolist())
        
        logger.info(f"✅ Search results for '{query}':")
        for r in results:
            logger.info(f"   [{r['similarity']:.3f}] {r['content'][:60]}...")
        
        await conn.close()
        return True
        
    except Exception as e:
        logger.error(f"❌ Vector search test failed: {e}")
        return False

async def main():
    """Run all tests"""
    logger.info("=" * 60)
    logger.info("KOI-MCP Server Test Suite")
    logger.info("=" * 60)
    
    results = {
        "PostgreSQL Connection": await test_postgres_connection(),
        "BGE Embeddings": await test_bge_embeddings(),
        "Vector Search": await test_vector_search()
    }
    
    logger.info("\n" + "=" * 60)
    logger.info("Test Results:")
    logger.info("=" * 60)
    
    for test, passed in results.items():
        status = "✅ PASSED" if passed else "❌ FAILED"
        logger.info(f"{test:30} {status}")
    
    all_passed = all(results.values())
    logger.info("\n" + ("✅ All tests passed!" if all_passed else "❌ Some tests failed"))
    
    return 0 if all_passed else 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)