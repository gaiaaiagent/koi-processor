#!/usr/bin/env python3
"""
Integration test for KOI flow: broadcast -> poll -> forward -> confirm.
"""

import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_PATH = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(REPO_ROOT / "koi-sensors"))
sys.path.insert(0, str(SCRIPTS_PATH))

from koi_protocol.coordinator.koi_coordinator import KOICoordinator
from koi_protocol.core.bundle_system import Bundle, Manifest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_koi_flow_broadcast_poll_forward_confirm(monkeypatch):
    monkeypatch.setenv("COORDINATOR_URL", "http://coordinator")
    monkeypatch.setenv("EVENT_BRIDGE_URL", "http://event-bridge")
    monkeypatch.setenv("EVENT_BRIDGE_ENDPOINT", "/events/process")
    monkeypatch.setenv("CODE_GRAPH_URL", "http://code-graph")
    monkeypatch.setenv("CODE_GRAPH_ENDPOINT", "/process-koi-event")
    monkeypatch.setenv("NODE_ID", "test-forwarder")
    monkeypatch.setenv("POLL_INTERVAL", "1")
    monkeypatch.setenv("MAX_CONCURRENT", "5")
    monkeypatch.delenv("KOI_PRIVATE_KEY_PEM", raising=False)
    monkeypatch.delenv("KOI_PRIVATE_KEY_PEM_PATH", raising=False)
    monkeypatch.delenv("KOI_PRIVATE_KEY_PASSWORD", raising=False)
    monkeypatch.delenv("KOI_ENVELOPE_SIGN", raising=False)

    import importlib
    import coordinator_to_eventbridge_forwarder as forwarder

    forwarder = importlib.reload(forwarder)

    coordinator = KOICoordinator(node_name="test-coordinator", port=0)
    coordinator_app = coordinator.app

    event_bridge_app = FastAPI()

    @event_bridge_app.post("/events/process")
    async def process_event(event: dict):
        rid = event.get("rid") or event.get("bundle", {}).get("rid", "")
        return {
            "success": True,
            "rid": rid,
            "cid": rid,
            "chunks_created": 1,
            "embeddings_created": 1
        }

    code_graph_app = FastAPI()

    @code_graph_app.post("/process-koi-event")
    async def process_code_graph_event(event: dict):
        return {
            "success": True,
            "entities_extracted": 1,
            "cat_receipts_created": 1
        }

    coordinator_transport = httpx.ASGITransport(app=coordinator_app)
    event_bridge_transport = httpx.ASGITransport(app=event_bridge_app)
    code_graph_transport = httpx.ASGITransport(app=code_graph_app)

    async def dispatch(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "coordinator":
            return await coordinator_transport.handle_async_request(request)
        if host == "event-bridge":
            return await event_bridge_transport.handle_async_request(request)
        if host == "code-graph":
            return await code_graph_transport.handle_async_request(request)
        return httpx.Response(404)

    transport = httpx.MockTransport(dispatch)

    contents = {"text": f"hello koi {uuid.uuid4().hex}"}
    content_bytes = json.dumps(contents, sort_keys=True).encode()
    content_hash = hashlib.sha256(content_bytes).hexdigest()
    rid = "rid:test-flow:1"

    manifest = Manifest(
        rid=rid,
        timestamp=datetime.now(timezone.utc).isoformat(),
        content_hash=content_hash,
        size_bytes=len(content_bytes),
        content_type="application/json",
        version="1.0",
        metadata={"source_type": "test"}
    )
    bundle = Bundle(rid=rid, manifest=manifest, contents=contents)

    event_payload = {
        "event_type": "NEW",
        "rid": rid,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_node": "test-sensor",
        "bundle": bundle.to_dict()
    }

    async with httpx.AsyncClient(transport=transport) as client:
        broadcast_response = await client.post(
            "http://coordinator/events/broadcast",
            json=event_payload
        )
        assert broadcast_response.status_code == 200

        confirmed = await forwarder.poll_once(client)
        assert confirmed == 1

        poll_response = await client.post(
            "http://coordinator/events/poll",
            json={
                "type": "poll_events",
                "node_id": "test-forwarder",
                "limit": 5,
                "include_event_ids": True
            }
        )
        assert poll_response.status_code == 200
        poll_payload = poll_response.json()
        if isinstance(poll_payload, dict) and "payload" in poll_payload:
            poll_payload = poll_payload["payload"]
        assert poll_payload.get("events") == []
