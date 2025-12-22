#!/usr/bin/env python3
"""
KOI Protocol-compliant Code Graph Forwarder
Polls the local Coordinator for events and forwards them to the remote Code Graph Service
for provenance-enabled entity extraction and CAT receipt generation.
"""

import asyncio
import hashlib
import httpx
import logging
import json
import os
from datetime import datetime
import time

from dotenv import load_dotenv

from koi_envelope import load_private_key_from_env, sign_envelope

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("koi.code-graph-forwarder")

COORDINATOR_URL = os.getenv("COORDINATOR_URL", "http://localhost:8005")
CODE_GRAPH_SERVICE_URL = os.getenv("CODE_GRAPH_SERVICE_URL", "http://202.61.196.119:8350")
CODE_GRAPH_ENDPOINT = os.getenv("CODE_GRAPH_ENDPOINT", "/process-koi-event")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "2"))
NODE_ID = os.getenv("NODE_ID", "code-graph-forwarder")
COORDINATOR_NODE_ID = os.getenv("COORDINATOR_NODE_ID")
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "10"))

ENVELOPE_PRIVATE_KEY = load_private_key_from_env()
ENVELOPE_SIGN = bool(ENVELOPE_PRIVATE_KEY) and os.getenv("KOI_ENVELOPE_SIGN", "true").lower() not in ("0", "false", "no")

async def forward_events():
    """Poll coordinator for events and forward to Code Graph Service"""

    async with httpx.AsyncClient(timeout=30.0) as client:
        logger.info(f"Starting Code Graph forwarder: {COORDINATOR_URL} -> {CODE_GRAPH_SERVICE_URL}")

        while True:
            try:
                # Poll coordinator for events
                poll_payload = {
                    "type": "poll_events",
                    "limit": MAX_CONCURRENT,
                    "node_id": NODE_ID,
                    "include_event_ids": True
                }
                if ENVELOPE_SIGN:
                    target_node = COORDINATOR_NODE_ID or "coordinator"
                    poll_payload = sign_envelope(poll_payload, NODE_ID, target_node, ENVELOPE_PRIVATE_KEY)

                response = await client.post(
                    f"{COORDINATOR_URL}/events/poll",
                    json=poll_payload
                )

                if response.status_code == 200:
                    poll_response = response.json()
                    if isinstance(poll_response, dict) and "payload" in poll_response:
                        poll_response = poll_response["payload"]

                    # Extract events and event IDs from the EventPollResponse structure
                    events = poll_response.get("events", [])
                    event_ids = poll_response.get("event_ids", [])

                    if events:
                        logger.info(f"Received {len(events)} events from coordinator")

                        # Track successfully processed events for confirmation
                        successfully_processed = []

                        # Forward events in PARALLEL using asyncio.gather
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

                        async def forward_single_event(event, event_id):
                            try:
                                event = normalize_event(event)
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
                                    f"{CODE_GRAPH_SERVICE_URL}{CODE_GRAPH_ENDPOINT}",
                                    json=event
                                )
                                if cg_response.status_code == 200:
                                    result = cg_response.json()
                                    if isinstance(result, dict) and result.get("success") is False:
                                        logger.error(f"Code Graph Service error: {result.get('error') or 'success=false'}")
                                        return None
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
                                confirm_payload = {
                                    "node_id": NODE_ID,
                                    "event_ids": successfully_processed,
                                    "timestamp": datetime.now().isoformat()
                                }
                                if ENVELOPE_SIGN:
                                    target_node = COORDINATOR_NODE_ID or "coordinator"
                                    confirm_payload = sign_envelope(confirm_payload, NODE_ID, target_node, ENVELOPE_PRIVATE_KEY)

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
