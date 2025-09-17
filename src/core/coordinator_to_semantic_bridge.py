#!/usr/bin/env python3
"""
Bridge to forward KOI Coordinator events to Semantic Event Bridge
"""

import asyncio
import httpx
import json
import logging
from typing import Dict, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

COORDINATOR_URL = "http://localhost:8005"
SEMANTIC_BRIDGE_URL = "http://localhost:8004"
POLL_INTERVAL = 5  # Poll every 5 seconds


async def poll_and_forward():
    """Poll coordinator for events and forward to semantic bridge"""
    node_id = "semantic-bridge-connector"

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Register with coordinator
        logger.info(f"Registering with coordinator as {node_id}")

        while True:
            try:
                # Poll for events from coordinator
                response = await client.get(
                    f"{COORDINATOR_URL}/events/poll",
                    params={"node_id": node_id, "max_events": 10}
                )

                if response.status_code == 200:
                    events = response.json().get("events", [])

                    for event in events:
                        # Skip heartbeat events
                        if "heartbeat" in event.get("rid", "").lower():
                            continue

                        logger.info(f"Forwarding event: {event.get('rid')}")

                        try:
                            # Forward to semantic bridge
                            sem_response = await client.post(
                                f"{SEMANTIC_BRIDGE_URL}/events/process",
                                json=event,
                                timeout=60.0
                            )

                            if sem_response.status_code == 200:
                                result = sem_response.json()
                                logger.info(f"Processed: {result.get('success')} - Chunks: {result.get('chunks_created')}")
                            else:
                                logger.error(f"Semantic bridge error: {sem_response.status_code}")

                        except Exception as e:
                            logger.error(f"Error forwarding event: {e}")
                            logger.error(f"Event structure: {json.dumps(event, indent=2)[:500]}")

            except Exception as e:
                logger.error(f"Poll error: {e}")

            await asyncio.sleep(POLL_INTERVAL)


async def main():
    logger.info("Starting Coordinator to Semantic Bridge connector...")
    await poll_and_forward()


if __name__ == "__main__":
    asyncio.run(main())