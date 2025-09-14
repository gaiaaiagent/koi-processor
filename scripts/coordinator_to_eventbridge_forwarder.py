#!/usr/bin/env python3
"""
KOI Protocol-compliant Event Bridge Poller
Implements the KOI-net protocol by polling the Coordinator (full node) for events
and processing them through the Event Bridge pipeline.

This makes Event Bridge act as a partial node that polls for events per KOI protocol.
"""

import asyncio
import httpx
import logging
import json
from datetime import datetime
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("koi.forwarder")

COORDINATOR_URL = "http://localhost:8005"
EVENT_BRIDGE_URL = "http://localhost:8100"
POLL_INTERVAL = 5  # seconds
NODE_ID = "event-bridge-forwarder"

async def forward_events():
    """Poll coordinator for events and forward to Event Bridge"""
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        logger.info(f"Starting event forwarder: {COORDINATOR_URL} -> {EVENT_BRIDGE_URL}")
        
        while True:
            try:
                # Poll coordinator for events
                response = await client.get(
                    f"{COORDINATOR_URL}/events/poll",
                    params={"node_id": NODE_ID}
                )
                
                if response.status_code == 200:
                    events = response.json()
                    
                    if events and isinstance(events, list):
                        logger.info(f"Received {len(events)} events from coordinator")
                        
                        # Forward each event to Event Bridge
                        for event in events:
                            try:
                                # Post to Event Bridge
                                eb_response = await client.post(
                                    f"{EVENT_BRIDGE_URL}/process_event",
                                    json=event
                                )
                                
                                if eb_response.status_code == 200:
                                    logger.info(f"Successfully forwarded event: {event.get('rid', 'unknown')}")
                                else:
                                    logger.error(f"Event Bridge error {eb_response.status_code}: {eb_response.text}")
                                    
                            except Exception as e:
                                logger.error(f"Error forwarding event: {e}")
                    else:
                        logger.debug("No new events from coordinator")
                        
                elif response.status_code == 404:
                    logger.debug("No events available")
                else:
                    logger.error(f"Coordinator poll error: {response.status_code}")
                    
            except Exception as e:
                logger.error(f"Error polling coordinator: {e}")
            
            await asyncio.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    try:
        asyncio.run(forward_events())
    except KeyboardInterrupt:
        logger.info("Forwarder stopped")