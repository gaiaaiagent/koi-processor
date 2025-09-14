#!/usr/bin/env python3
"""
Simple BGE Embedding Server
Provides BGE embeddings via HTTP API for KOI Event Bridge
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import numpy as np
import uvicorn
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="BGE Embedding Server", version="1.0.0")

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

@app.post("/encode", response_model=EmbeddingResponse)
async def generate_embedding(request: EmbeddingRequest):
    """Generate BGE embedding for text"""
    # Get text from either field
    text = request.text or request.input
    
    if not text:
        raise HTTPException(status_code=400, detail="No text provided")
    
    # For testing, generate a mock 1024-dimensional embedding
    # In production, this would use actual BGE model
    # The pattern ensures consistency for same text
    text_hash = hash(text) % 1000000
    np.random.seed(text_hash)
    embedding = np.random.randn(1024).tolist()
    
    logger.info(f"Generated embedding for text of length {len(text)}")
    
    return EmbeddingResponse(
        embedding=embedding,
        dim=1024
    )

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "BGE Embedding Server",
        "model": "mock-bge-large-en-v1.5",
        "embedding_dim": 1024
    }

if __name__ == "__main__":
    print("[BGE Server] Starting on http://localhost:8090")
    print("[BGE Server] Mock embeddings for testing (1024-dimensional)")
    uvicorn.run(app, host="0.0.0.0", port=8090, log_level="info")