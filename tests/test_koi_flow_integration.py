#!/usr/bin/env python3
"""
Integration test for KOI flow: broadcast -> poll -> forward -> confirm.

P0 Alignment: Tests extended to verify backward compatibility with dual-hash support.
Reference: koi-research/docs/KOI_PROTOCOL_ALIGNMENT_REFERENCE.md
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
from koi_protocol.core.rid_system import GenericRID


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
    rid_obj = GenericRID("rid", "test-flow:1")
    rid = rid_obj.to_string()

    # Use Bundle.generate() for proper dual-hash generation
    bundle = Bundle.generate(
        rid=rid_obj,
        contents=contents,
        content_type="application/json",
        metadata={"source_type": "test"}
    )

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


@pytest.mark.anyio
async def test_koi_flow_bridge_failure_no_confirm(monkeypatch):
    """Test that events are not confirmed when bridge returns failure (HTTP 500)."""
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

    # Event bridge that fails with HTTP 500 and success=false
    event_bridge_app = FastAPI()

    @event_bridge_app.post("/events/process")
    async def process_event_fail(event: dict):
        from fastapi.responses import JSONResponse
        rid = event.get("rid") or event.get("bundle", {}).get("rid", "")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "rid": rid,
                "cid": "",
                "chunks_created": 0,
                "embeddings_created": 0,
                "error": "Test failure"
            }
        )

    code_graph_app = FastAPI()

    @code_graph_app.post("/process-koi-event")
    async def process_code_graph_event(event: dict):
        return {"success": True}

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

    contents = {"text": f"fail test {uuid.uuid4().hex}"}
    rid_obj = GenericRID("rid", "test-fail:1")
    rid = rid_obj.to_string()

    # Use Bundle.generate() for proper dual-hash generation
    bundle = Bundle.generate(
        rid=rid_obj,
        contents=contents,
        content_type="application/json",
        metadata={"source_type": "test"}
    )

    event_payload = {
        "event_type": "NEW",
        "rid": rid,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_node": "test-sensor",
        "bundle": bundle.to_dict()
    }

    async with httpx.AsyncClient(transport=transport) as client:
        # Broadcast event
        broadcast_response = await client.post(
            "http://coordinator/events/broadcast",
            json=event_payload
        )
        assert broadcast_response.status_code == 200

        # Poll and forward - should NOT confirm because bridge failed (HTTP 500)
        confirmed = await forwarder.poll_once(client)
        assert confirmed == 0  # No events confirmed due to HTTP 500 failure

        # Key assertion: when bridge returns 500 with success=false,
        # forwarder does NOT call /events/confirm.
        # (Event lifecycle in coordinator is separate from this test)


# =============================================================================
# P0 Alignment: Dual-Hash Backward Compatibility Tests
# =============================================================================

class TestP0DualHashBackwardCompatibility:
    """
    Tests for P0 alignment backward compatibility.

    Critical constraint: Preserve existing internal /events/* behavior.
    The clients in Appendix F must continue working during the transition.
    """

    def test_manifest_generate_produces_dual_hashes(self):
        """Manifest.generate() should produce both sha256_hash and legacy_content_hash."""
        contents = {"text": "test content", "value": 1.0}
        rid = GenericRID("test", "dual-hash")

        manifest = Manifest.generate(rid, contents)

        # Both hash fields should exist
        assert hasattr(manifest, 'sha256_hash')
        assert hasattr(manifest, 'legacy_content_hash')
        assert len(manifest.sha256_hash) == 64
        assert len(manifest.legacy_content_hash) == 64

    def test_manifest_content_hash_property_returns_sha256(self):
        """content_hash property should return sha256_hash for backward compat."""
        contents = {"key": "value"}
        rid = GenericRID("test", "compat")

        manifest = Manifest.generate(rid, contents)

        # content_hash should equal sha256_hash
        assert manifest.content_hash == manifest.sha256_hash

    def test_manifest_from_dict_with_legacy_format(self):
        """Manifests with only content_hash (legacy) should still work."""
        # Old-style manifest dict with only content_hash
        legacy_dict = {
            "rid": "test:legacy",
            "timestamp": "2024-12-22T10:00:00Z",
            "content_hash": "a" * 64,
            "size_bytes": 100,
            "content_type": "application/json",
            "version": "1.0",
            "metadata": {}
        }

        manifest = Manifest.from_dict(legacy_dict)

        # Should work without sha256_hash in input
        assert manifest.sha256_hash == legacy_dict["content_hash"]
        assert manifest.content_hash == legacy_dict["content_hash"]

    def test_bundle_from_dict_with_legacy_manifest(self):
        """Bundle.from_dict should work with legacy manifest format."""
        legacy_bundle = {
            "rid": "test:legacy-bundle",
            "manifest": {
                "rid": "test:legacy-bundle",
                "timestamp": "2024-12-22T10:00:00Z",
                "content_hash": "b" * 64,
                "size_bytes": 50,
                "content_type": "application/json",
                "version": "1.0",
                "metadata": {}
            },
            "contents": {"test": "data"}
        }

        bundle = Bundle.from_dict(legacy_bundle)

        assert bundle.rid == legacy_bundle["rid"]
        assert bundle.manifest.content_hash == legacy_bundle["manifest"]["content_hash"]

    def test_bundle_to_dict_includes_dual_hashes(self):
        """bundle.to_dict() should include both hash fields."""
        contents = {"key": "value"}
        rid = GenericRID("test", "dict")

        bundle = Bundle.generate(rid, contents)
        bundle_dict = bundle.to_dict()

        manifest_dict = bundle_dict["manifest"]
        assert "sha256_hash" in manifest_dict
        assert "legacy_content_hash" in manifest_dict
        assert "content_hash" in manifest_dict  # For backward compat

    def test_hashes_differ_for_float_content(self):
        """For content with floats, legacy and sha256 hashes should differ (JCS 1.0 -> 1)."""
        # This is the critical case that caused 8.79% mismatch in the spike
        contents = {"value": 1.0}
        rid = GenericRID("test", "float")

        manifest = Manifest.generate(rid, contents)

        # JCS normalizes 1.0 to 1, so hashes should differ
        assert manifest.sha256_hash != manifest.legacy_content_hash, \
            "Hashes should differ for content with float values (JCS normalizes 1.0 to 1)"

    def test_bundle_verify_integrity_with_dual_hash(self):
        """Bundle.verify_integrity() should work with dual-hash manifests."""
        contents = {"key": "value", "nested": {"a": 1}}
        rid = GenericRID("test", "verify")

        bundle = Bundle.generate(rid, contents)

        # Both verification methods should pass
        assert bundle.verify_integrity() is True
        assert bundle.verify_legacy_integrity() is True

    def test_rid_parsing_orn_format(self):
        """ORN format RIDs should parse correctly (P0 requirement)."""
        from koi_protocol.core.rid_system import RID

        # ORN with multiple colons (namespace:reference format)
        rid_string = "orn:slack.message:T123/C456/1234.5678"
        rid = RID.parse(rid_string)

        assert rid is not None
        assert rid.to_string() == rid_string

    def test_rid_parsing_uri_with_port(self):
        """URIs with ports should parse correctly (P0 requirement)."""
        from koi_protocol.core.rid_system import RID

        rid_string = "https://example.com:8080/path"
        rid = RID.parse(rid_string)

        assert rid is not None
        assert rid.to_string() == rid_string
