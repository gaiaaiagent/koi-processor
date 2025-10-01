#!/usr/bin/env python3
"""
Simple BGE Embedding Server
Provides BGE embeddings via HTTP API for KOI Event Bridge
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict
import numpy as np
import uvicorn
import logging
from sentence_transformers import SentenceTransformer
import torch
import hashlib
from functools import lru_cache

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="BGE Embedding Server", version="1.0.0")

# Global model instance
model = None

# In-memory cache for embeddings
embedding_cache: Dict[str, List[float]] = {}

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class EmbeddingRequest(BaseModel):
    text: Optional[str] = None
    input: Optional[str] = None  # Alternative field name

class EmbeddingResponse(BaseModel):
    embedding: List[float]
    dim: int = 1024

def load_model():
    """Load the BGE model (lazy loading)"""
    global model
    if model is None:
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
        logger.info(f"Loading BGE model BAAI/bge-large-en-v1.5 on {device}...")
        model = SentenceTransformer('BAAI/bge-large-en-v1.5')
        model.to(device)
        logger.info(f"✅ BGE model loaded successfully on {device}")
    return model

@app.post("/encode", response_model=EmbeddingResponse)
async def generate_embedding(request: EmbeddingRequest):
    """Generate BGE embedding for text (with caching)"""
    # Get text from either field
    text = request.text or request.input

    if not text:
        raise HTTPException(status_code=400, detail="No text provided")

    # Check cache first (use hash as key)
    cache_key = hashlib.sha256(text.encode('utf-8')).hexdigest()

    if cache_key in embedding_cache:
        logger.debug(f"Cache hit for text of length {len(text)}")
        return EmbeddingResponse(
            embedding=embedding_cache[cache_key],
            dim=1024
        )

    # Use real BGE model
    bge_model = load_model()
    embedding = bge_model.encode(text, normalize_embeddings=True)
    embedding_list = embedding.tolist()

    # Cache the result (limit cache size to 10000 entries)
    if len(embedding_cache) < 10000:
        embedding_cache[cache_key] = embedding_list
        logger.debug(f"Cached embedding (cache size: {len(embedding_cache)})")

    logger.info(f"Generated embedding for text of length {len(text)} (cache size: {len(embedding_cache)})")

    return EmbeddingResponse(
        embedding=embedding_list,
        dim=len(embedding)
    )

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    model_loaded = model is not None
    return {
        "status": "healthy",
        "service": "BGE Embedding Server",
        "model": "BAAI/bge-large-en-v1.5",
        "embedding_dim": 1024,
        "device": device,
        "model_loaded": model_loaded,
        "cache_size": len(embedding_cache),
        "cache_hit_ratio": "tracked in logs"
    }

@app.get("/cache/stats")
async def cache_stats():
    """Get cache statistics"""
    return {
        "cache_size": len(embedding_cache),
        "max_cache_size": 10000,
        "memory_usage_mb": len(embedding_cache) * 1024 * 4 / 1024 / 1024  # Approximate
    }

@app.post("/cache/clear")
async def clear_cache():
    """Clear the embedding cache"""
    global embedding_cache
    old_size = len(embedding_cache)
    embedding_cache = {}
    return {
        "status": "cleared",
        "entries_removed": old_size
    }

if __name__ == "__main__":
    print("[BGE Server] Starting on http://localhost:8090")
    print("[BGE Server] Real BGE embeddings (BAAI/bge-large-en-v1.5)")
    print(f"[BGE Server] Device: {'CUDA' if torch.cuda.is_available() else 'CPU'}")
    uvicorn.run(app, host="0.0.0.0", port=8090, log_level="info")