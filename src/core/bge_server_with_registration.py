#!/usr/bin/env python3
"""
Example: BGE Server with Self-Registration
Shows how infrastructure components can self-register
"""

import asyncio
import logging
from infrastructure_registry import InfrastructureClient

logger = logging.getLogger(__name__)

async def register_bge_server():
    """Register BGE server with infrastructure registry"""

    # Create registry client
    client = InfrastructureClient(registry_url="http://localhost:8003")

    # Register BGE server
    success = await client.register(
        component_id="bge-embeddings",
        component_type="processor",
        label="BGE Embedding Server",
        endpoint="http://localhost:8090",
        port=8090,
        description="Generates BAAI/bge-large-en-v1.5 1024-dimensional embeddings",
        capabilities=[
            "text-embedding",
            "bge-large-en-v1.5",
            "1024-dimensions",
            "batch-processing"
        ],
        depends_on=["event-bridge"],  # Depends on Event Bridge for input
        metadata={
            "model": "BAAI/bge-large-en-v1.5",
            "dimensions": 1024,
            "max_batch_size": 32,
            "version": "1.0.0"
        }
    )

    if success:
        logger.info("BGE server registered successfully")

        # Start heartbeat loop
        while True:
            await asyncio.sleep(30)  # Send heartbeat every 30 seconds
            await client.heartbeat("bge-embeddings", status="active")
    else:
        logger.error("Failed to register BGE server")

# This would be added to the actual BGE server startup code
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(register_bge_server())