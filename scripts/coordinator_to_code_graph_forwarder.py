#!/usr/bin/env python3
"""
KOI Protocol-compliant Code Graph Forwarder
Polls the local Coordinator for events and forwards them to the remote Code Graph Service
for provenance-enabled entity extraction and CAT receipt generation.
"""

import asyncio
import httpx
import logging
import json
from datetime import datetime
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("koi.code-graph-forwarder")

COORDINATOR_URL = "http://localhost:8005"
CODE_GRAPH_SERVICE_URL = "http://202.61.196.119:8350"  # Remote Code Graph Service
POLL_INTERVAL = 2  # seconds
NODE_ID = "code-graph-forwarder"
MAX_CONCURRENT = 10  # Limit concurrent processing

async def forward_events():
    """Poll coordinator for events and forward to Code Graph Service"""

    async with httpx.AsyncClient(timeout=30.0) as client:
        logger.info(f"Starting Code Graph forwarder: {COORDINATOR_URL} -> {CODE_GRAPH_SERVICE_URL}")

        while True:
            try:
                # Poll coordinator for events
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

                        # Forward events in PARALLEL using asyncio.gather
                        async def forward_single_event(event, event_id):
                            try:
                                # FILTER: Only forward events with file_path metadata
                                metadata = event.get('bundle', {}).get('manifest', {}).get('metadata', {})
                                file_path = metadata.get('file_path', '')

                                if not file_path:
                                    # Skip heartbeat/summary events - mark as processed but don't forward
                                    event_type = metadata.get('source_type', 'unknown')
                                    logger.debug(f"Skipping non-file event (type: {event_type}, rid: {event.get('rid', 'unknown')})")
                                    return event_id  # Mark as processed

                                # Only forward file events with code
                                cg_response = await client.post(
                                    f"{CODE_GRAPH_SERVICE_URL}/process-koi-event",
                                    json=event
                                )
                                if cg_response.status_code == 200:
                                    result = cg_response.json()
                                    entities = result.get('entities_extracted', 0)
                                    receipts = result.get('cat_receipts_created', 0)
                                    logger.info(f"✓ Event {event.get('rid', 'unknown')}: {entities} entities, {receipts} CAT receipts")
                                    return event_id
                                else:
                                    logger.error(f"Code Graph Service error {cg_response.status_code}: {cg_response.text[:200]}")
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
        logger.info("Code Graph forwarder stopped")
