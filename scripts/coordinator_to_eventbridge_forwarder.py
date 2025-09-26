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
EVENT_BRIDGE_URL = "http://localhost:8100"  # Event Bridge v2 port
POLL_INTERVAL = 2  # seconds - faster for testing
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
                    poll_response = response.json()

                    # Extract events and event IDs from the EventPollResponse structure
                    events = poll_response.get("events", [])
                    event_ids = poll_response.get("event_ids", [])

                    if events:
                        logger.info(f"Received {len(events)} events from coordinator")

                        # Track successfully processed events for confirmation
                        successfully_processed = []

                        # Forward each event to Event Bridge
                        for i, event in enumerate(events):
                            try:
                                # Post to Event Bridge
                                eb_response = await client.post(
                                    f"{EVENT_BRIDGE_URL}/events/process",
                                    json=event
                                )

                                if eb_response.status_code == 200:
                                    logger.info(f"Successfully forwarded event: {event.get('rid', 'unknown')}")
                                    # Track this event as successfully processed
                                    if i < len(event_ids):
                                        successfully_processed.append(event_ids[i])
                                else:
                                    logger.error(f"Event Bridge error {eb_response.status_code}: {eb_response.text}")

                            except Exception as e:
                                logger.error(f"Error forwarding event: {e}")

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