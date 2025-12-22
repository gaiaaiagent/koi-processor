#!/usr/bin/env python3
"""
Bridge to forward KOI Coordinator events to Semantic Event Bridge
"""

import asyncio
import hashlib
import httpx
import json
import logging
import os
from datetime import datetime
from typing import Dict, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

COORDINATOR_URL = os.getenv("COORDINATOR_URL", "http://localhost:8005")
SEMANTIC_BRIDGE_URL = os.getenv("SEMANTIC_BRIDGE_URL", "http://localhost:8004")
SEMANTIC_BRIDGE_ENDPOINT = os.getenv("SEMANTIC_BRIDGE_ENDPOINT", "/events/process")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "5"))
NODE_ID = os.getenv("NODE_ID", "semantic-bridge-connector")
MAX_EVENTS = int(os.getenv("MAX_EVENTS", "10"))


async def poll_and_forward():
    """Poll coordinator for events and forward to semantic bridge"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Register with coordinator
        logger.info(f"Registering with coordinator as {NODE_ID}")

        while True:
            try:
                # Poll for events from coordinator
                poll_payload = {
                    "type": "poll_events",
                    "limit": MAX_EVENTS,
                    "node_id": NODE_ID,
                    "include_event_ids": True
                }
                response = await client.post(
                    f"{COORDINATOR_URL}/events/poll",
                    json=poll_payload
                )

                if response.status_code == 200:
                    poll_response = response.json()
                    if isinstance(poll_response, dict) and "payload" in poll_response:
                        poll_response = poll_response["payload"]
                    events = poll_response.get("events", [])
                    event_ids = poll_response.get("event_ids", [])

                    def normalize_event(event):
                        if "bundle" in event:
                            return event

                        manifest = event.get("manifest") or {}
                        contents = event.get("contents") or {}
                        content_hash = manifest.get("sha256_hash") or manifest.get("content_hash")
                        if not content_hash:
                            content_hash = hashlib.sha256(json.dumps(contents, sort_keys=True).encode()).hexdigest()

                        bundle = {
                            "rid": event.get("rid"),
                            "manifest": {
                                "rid": event.get("rid"),
                                "timestamp": manifest.get("timestamp") or event.get("timestamp") or datetime.now().isoformat(),
                                "content_hash": content_hash,
                                "size_bytes": manifest.get("size_bytes") or len(json.dumps(contents).encode()),
                                "content_type": manifest.get("content_type") or "application/json",
                                "version": manifest.get("version") or "1.0",
                                "metadata": manifest.get("metadata") or {}
                            },
                            "contents": contents
                        }
                        return {
                            "event_type": event.get("event_type"),
                            "rid": event.get("rid"),
                            "source_node": event.get("source_node", "coordinator"),
                            "timestamp": event.get("timestamp") or datetime.now().isoformat(),
                            "bundle": bundle
                        }

                    confirmed_event_ids = []

                    for idx, event in enumerate(events):
                        event_id = event_ids[idx] if idx < len(event_ids) else None

                        # Skip heartbeat events
                        if "heartbeat" in event.get("rid", "").lower():
                            continue

                        event = normalize_event(event)
                        logger.info(f"Forwarding event: {event.get('rid')}")

                        try:
                            # Forward to semantic bridge
                            sem_response = await client.post(
                                f"{SEMANTIC_BRIDGE_URL}{SEMANTIC_BRIDGE_ENDPOINT}",
                                json=event,
                                timeout=60.0
                            )

                            # Handle both 200 (success) and non-200 (failure) responses
                            result = sem_response.json()
                            success = False
                            if isinstance(result, dict):
                                success = bool(result.get("success", sem_response.status_code == 200))

                            if success:
                                logger.info(f"Processed: {result.get('success')} - Chunks: {result.get('chunks_created')}")
                                if event_id:
                                    confirmed_event_ids.append(event_id)
                            else:
                                error_msg = result.get('error', 'unknown') if isinstance(result, dict) else str(result)
                                logger.error(f"Semantic bridge error (HTTP {sem_response.status_code}): {error_msg}")

                        except Exception as e:
                            logger.error(f"Error forwarding event: {e}")
                            logger.error(f"Event structure: {json.dumps(event, indent=2)[:500]}")

                    if confirmed_event_ids:
                        try:
                            confirm_payload = {
                                "node_id": NODE_ID,
                                "event_ids": confirmed_event_ids,
                                "timestamp": datetime.now().isoformat()
                            }
                            confirm_response = await client.post(
                                f"{COORDINATOR_URL}/events/confirm",
                                json=confirm_payload
                            )
                            if confirm_response.status_code == 200:
                                confirm_data = confirm_response.json()
                                if isinstance(confirm_data, dict) and "payload" in confirm_data:
                                    confirm_data = confirm_data["payload"]
                                logger.info(f"Confirmed delivery of {confirm_data.get('confirmed_count', 0)} events")
                            else:
                                logger.error(f"Failed to confirm delivery: {confirm_response.status_code}")
                        except Exception as e:
                            logger.error(f"Error confirming delivery: {e}")

            except Exception as e:
                logger.error(f"Poll error: {e}")

            await asyncio.sleep(POLL_INTERVAL)


async def main():
    logger.info("Starting Coordinator to Semantic Bridge connector...")
    await poll_and_forward()


if __name__ == "__main__":
    asyncio.run(main())
