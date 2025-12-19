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
import os
from datetime import datetime
import time
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("koi.forwarder")

# Configuration from environment with sensible defaults
COORDINATOR_URL = os.getenv("COORDINATOR_URL", "http://localhost:8005")
EVENT_BRIDGE_URL = os.getenv("EVENT_BRIDGE_URL", "http://localhost:8004")  # Semantic Event Bridge
EVENT_BRIDGE_ENDPOINT = os.getenv("EVENT_BRIDGE_ENDPOINT", "/events/process")
CODE_GRAPH_URL = os.getenv("CODE_GRAPH_URL", "http://localhost:8350")
CODE_GRAPH_ENDPOINT = os.getenv("CODE_GRAPH_ENDPOINT", "/process-koi-event")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "2"))
NODE_ID = os.getenv("NODE_ID", "event-bridge-forwarder")
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "10"))
HEALTH_CHECK_INTERVAL = int(os.getenv("HEALTH_CHECK_INTERVAL", "300"))  # 5 minutes


async def check_endpoint_health(client: httpx.AsyncClient, name: str, url: str) -> bool:
    """Check if an endpoint is healthy and responding"""
    try:
        # Try health endpoint first, then root
        for endpoint in ["/health", "/"]:
            try:
                response = await client.get(f"{url}{endpoint}", timeout=5.0)
                if response.status_code == 200:
                    logger.info(f"✅ {name} is healthy at {url}")
                    return True
            except:
                continue
        logger.error(f"❌ {name} at {url} is NOT responding!")
        return False
    except Exception as e:
        logger.error(f"❌ {name} health check failed: {e}")
        return False


async def startup_health_checks(client: httpx.AsyncClient) -> bool:
    """Run health checks on all endpoints at startup"""
    logger.info("=" * 60)
    logger.info("Running startup health checks...")
    logger.info("=" * 60)

    all_healthy = True

    # Check coordinator
    if not await check_endpoint_health(client, "Coordinator", COORDINATOR_URL):
        all_healthy = False

    # Check event bridge
    if not await check_endpoint_health(client, "Event Bridge", EVENT_BRIDGE_URL):
        all_healthy = False
        logger.error(f"⚠️  CRITICAL: Event Bridge not responding! Events will NOT be processed!")

    # Check code graph (optional, don't fail if missing)
    await check_endpoint_health(client, "Code Graph", CODE_GRAPH_URL)

    logger.info("=" * 60)
    if all_healthy:
        logger.info("✅ All critical endpoints are healthy")
    else:
        logger.error("❌ Some critical endpoints are NOT healthy!")
    logger.info("=" * 60)

    return all_healthy

async def forward_events():
    """Poll coordinator for events and forward to Event Bridge"""

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Log configuration
        logger.info("=" * 60)
        logger.info("KOI Event Forwarder Configuration:")
        logger.info(f"  Coordinator: {COORDINATOR_URL}")
        logger.info(f"  Event Bridge: {EVENT_BRIDGE_URL}{EVENT_BRIDGE_ENDPOINT}")
        logger.info(f"  Code Graph: {CODE_GRAPH_URL}{CODE_GRAPH_ENDPOINT}")
        logger.info(f"  Poll Interval: {POLL_INTERVAL}s")
        logger.info(f"  Max Concurrent: {MAX_CONCURRENT}")
        logger.info("=" * 60)

        # Run startup health checks
        await startup_health_checks(client)

        logger.info(f"Starting event forwarder: {COORDINATOR_URL} -> {EVENT_BRIDGE_URL}")
        last_health_check = time.time()

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
                                # Send to both Event Bridge AND Code Graph Service in parallel
                                eb_task = client.post(f"{EVENT_BRIDGE_URL}{EVENT_BRIDGE_ENDPOINT}", json=event)
                                cg_task = client.post(f"{CODE_GRAPH_URL}{CODE_GRAPH_ENDPOINT}", json=event)
                                
                                eb_response, cg_response = await asyncio.gather(eb_task, cg_task, return_exceptions=True)
                                
                                # Check Event Bridge response
                                eb_success = not isinstance(eb_response, Exception) and eb_response.status_code == 200
                                
                                # Check Code Graph response (allow it to fail silently if not a code file)
                                if isinstance(cg_response, Exception):
                                    logger.debug(f"Code Graph Service connection error: {cg_response}")
                                elif cg_response.status_code != 200:
                                    logger.debug(f"Code Graph Service error {cg_response.status_code}: {cg_response.text[:100]}")
                                
                                if eb_success:
                                    logger.info(f"Successfully forwarded event: {event.get('rid', 'unknown')}")
                                    return event_id
                                else:
                                    logger.error(f"Event Bridge error {eb_response.status_code if not isinstance(eb_response, Exception) else eb_response}: {eb_response.text if not isinstance(eb_response, Exception) else ''}")
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

            # Periodic health checks
            if time.time() - last_health_check > HEALTH_CHECK_INTERVAL:
                logger.info("Running periodic health check...")
                await startup_health_checks(client)
                last_health_check = time.time()

if __name__ == "__main__":
    try:
        asyncio.run(forward_events())
    except KeyboardInterrupt:
        logger.info("Forwarder stopped")