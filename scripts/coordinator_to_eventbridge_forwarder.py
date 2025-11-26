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

COORDINATOR_URL = "http://localhost:8200"
EVENT_BRIDGE_URL = "http://localhost:8100"  # Event Bridge v2 port
POLL_INTERVAL = 2  # seconds - faster for testing
NODE_ID = "event-bridge-forwarder"
MAX_CONCURRENT = 10  # Limit concurrent processing to avoid DB connection exhaustion

async def forward_events():
    """Poll coordinator for events and forward to Event Bridge"""

    async with httpx.AsyncClient(timeout=30.0) as client:
        logger.info(f"Starting event forwarder: {COORDINATOR_URL} -> {EVENT_BRIDGE_URL}")

        while True:
            try:
                # Poll coordinator for events (request fewer to limit concurrency)
                response = await client.get(
                    f"{COORDINATOR_URL}/events/poll",
                    params={"node_id": NODE_ID, "max_events": MAX_CONCURRENT}
                )

                if response.status_code == 200:
                    poll_response = response.json()

                    # Extract events and event IDs from the EventPollResponse structure
                    events = poll_response.get("events", [])
                    event_ids = poll_response.get("event_ids", [])

                    if events:
                        logger.info(f"Received {len(events)} events from coordinator")

                        # Track successfully processed events for confirmation
                        successfully_processed = []

                        # Forward events in PARALLEL using asyncio.gather (MAJOR SPEEDUP!)
                        async def forward_single_event(event, event_id):
                            try:
                                eb_response = await client.post(
                                    f"{EVENT_BRIDGE_URL}/process-koi-event",
                                    json=event
                                )
                                if eb_response.status_code == 200:
                                    logger.info(f"Successfully forwarded event: {event.get('rid', 'unknown')}")
                                    return event_id
                                else:
                                    logger.error(f"Event Bridge error {eb_response.status_code}: {eb_response.text}")
                                    return None
                            except Exception as e:
                                logger.error(f"Error forwarding event: {e}")
                                return None

                        # Send all events in parallel
                        results = await asyncio.gather(*[
                            forward_single_event(event, event_ids[i] if i < len(event_ids) else None)
                            for i, event in enumerate(events)
                        ])

                        # Collect successfully processed event IDs
                        successfully_processed = [r for r in results if r is not None]

                        # Confirm delivery for successfully processed events
                        if successfully_processed:
                            try:
                                confirm_response = await client.post(
                                    f"{COORDINATOR_URL}/events/confirm",
                                    json={
                                        "node_id": NODE_ID,
                                        "event_ids": successfully_processed,
                                        "timestamp": datetime.now().isoformat()
                                    }
                                )

                                if confirm_response.status_code == 200:
                                    confirm_data = confirm_response.json()
                                    logger.info(f"Confirmed delivery of {confirm_data.get('confirmed_count', 0)} events")
                                else:
                                    logger.error(f"Failed to confirm delivery: {confirm_response.status_code}")

                            except Exception as e:
                                logger.error(f"Error confirming delivery: {e}")

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