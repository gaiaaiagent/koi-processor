"""In-process pytest tests for V2 attestation layer.

Tests attestation CRUD, UPSERT idempotency, self-attestation guard,
policy gates for verify transitions, and grandfathering of pre-migration claims.

Run:  pytest tests/test_claims_attestations.py -v
Requires: PostgreSQL personal_koi running locally (uses rollback transactions).
          Migration 066_attestations must be applied.
"""

import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DB_URL = os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _SingleConnPool:
    """Wraps a single asyncpg.Connection to quack like asyncpg.Pool.acquire()."""

    def __init__(self, conn):
        self._conn = conn

    class _CM:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, *a):
            pass

    def acquire(self):
        return self._CM(self._conn)


async def _setup_test_claimant(conn, uri="urn:test:claimant-org"):
    """Ensure a test claimant entity exists in entity_registry."""
    await conn.execute("""
        INSERT INTO entity_registry (fuseki_uri, entity_text, entity_type, normalized_text)
        VALUES ($1, 'Test Claimant Org', 'Organization', 'test claimant org')
        ON CONFLICT (fuseki_uri) DO NOTHING
    """, uri)
    return uri


async def _setup_test_reviewer(conn, name="Test Reviewer", uri=None):
    """Insert a test reviewer Person in entity_registry. Returns URI."""
    if uri is None:
        uri = f"urn:test:reviewer-{int(time.time() * 1000)}"
    await conn.execute("""
        INSERT INTO entity_registry (fuseki_uri, entity_text, entity_type, normalized_text)
        VALUES ($1, $2, 'Person', $3)
        ON CONFLICT (fuseki_uri) DO NOTHING
    """, uri, name, name.lower())
    return uri


async def _setup_test_claim(conn, claimant_uri="urn:test:claimant-org",
                             verification="self_reported", created_at=None):
    """Insert a minimal claim row. Returns claim_rid."""
    ts = int(time.time() * 1000)
    rid = f"orn:koi-net.claim:test-att-{ts}"

    if created_at is None:
        created_at = datetime.now(timezone.utc)

    await conn.execute("""
        INSERT INTO claims (claim_rid, claimant_uri, statement, claim_type,
                            verification, metadata, created_at, updated_at)
        VALUES ($1, $2, 'Test claim for attestation tests', 'ecological',
                $3, '{}'::jsonb, $4, NOW())
    """, rid, claimant_uri, verification, created_at)

    # Insert initial state log
    await conn.execute("""
        INSERT INTO claim_state_log (claim_rid, from_state, to_state, actor, reason)
        VALUES ($1, NULL, 'self_reported', 'test', 'initial')
    """, rid)

    return rid


async def _ensure_migration_row(conn, applied_at=None):
    """Ensure the 066_attestations migration row exists for policy gate tests."""
    if applied_at is None:
        applied_at = datetime.now(timezone.utc)
    await conn.execute("""
        INSERT INTO koi_migrations (migration_id, checksum, applied_at)
        VALUES ('066_attestations', 'v2_attestation_layer', $1)
        ON CONFLICT (migration_id) DO UPDATE SET applied_at = $1
    """, applied_at)
    return applied_at


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def test_app():
    """Create a FastAPI app with claims router wired to a rollback transaction."""
    conn = await asyncpg.connect(DB_URL)
    tx = conn.transaction()
    await tx.start()

    try:
        pool = _SingleConnPool(conn)

        from api.routers.claims_router import create_router
        router = create_router(pool)

        app = FastAPI()
        app.include_router(router)

        yield app, conn
    finally:
        await tx.rollback()
        await conn.close()


@pytest.fixture
async def client(test_app):
    """Async httpx client using ASGI transport."""
    app, _ = test_app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def conn(test_app):
    """Direct DB connection (same transaction as the app)."""
    _, conn = test_app
    return conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_create_attestation(client, conn):
    """POST /claims/{rid}/attestations → 201 with attestation_rid."""
    claimant_uri = await _setup_test_claimant(conn)
    reviewer_uri = await _setup_test_reviewer(conn, "Reviewer Alpha", "urn:test:reviewer-alpha")
    rid = await _setup_test_claim(conn, claimant_uri)

    resp = await client.post(f"/claims/{rid}/attestations", json={
        "reviewer_uri": reviewer_uri,
        "verdict": "approved",
        "rationale": "Reviewed source docs",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["attestation_rid"].startswith("orn:koi-net.attestation:")
    assert data["claim_rid"] == rid
    assert data["reviewer_uri"] == reviewer_uri
    assert data["verdict"] == "approved"
    assert data["rationale"] == "Reviewed source docs"
    assert data["reviewer_name"] == "Reviewer Alpha"


@pytest.mark.anyio
async def test_get_attestation(client, conn):
    """GET /claims/{rid}/attestations/{att_rid} → matches input."""
    claimant_uri = await _setup_test_claimant(conn)
    reviewer_uri = await _setup_test_reviewer(conn, "Reviewer Beta", "urn:test:reviewer-beta")
    rid = await _setup_test_claim(conn, claimant_uri)

    create_resp = await client.post(f"/claims/{rid}/attestations", json={
        "reviewer_uri": reviewer_uri,
        "verdict": "needs_info",
        "rationale": "Need more data",
    })
    att_rid = create_resp.json()["attestation_rid"]

    resp = await client.get(f"/claims/{rid}/attestations/{att_rid}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["attestation_rid"] == att_rid
    assert data["verdict"] == "needs_info"


@pytest.mark.anyio
async def test_list_attestations(client, conn):
    """GET /claims/{rid}/attestations → includes created attestation."""
    claimant_uri = await _setup_test_claimant(conn)
    rev1 = await _setup_test_reviewer(conn, "Rev List 1", "urn:test:rev-list-1")
    rev2 = await _setup_test_reviewer(conn, "Rev List 2", "urn:test:rev-list-2")
    rid = await _setup_test_claim(conn, claimant_uri)

    await client.post(f"/claims/{rid}/attestations", json={
        "reviewer_uri": rev1, "verdict": "approved",
    })
    await client.post(f"/claims/{rid}/attestations", json={
        "reviewer_uri": rev2, "verdict": "rejected",
    })

    resp = await client.get(f"/claims/{rid}/attestations")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2

    # Test verdict filter
    resp2 = await client.get(f"/claims/{rid}/attestations?verdict=approved")
    assert len(resp2.json()) == 1


@pytest.mark.anyio
async def test_upsert_updates_verdict(client, conn):
    """Duplicate reviewer → UPSERT updates verdict, same attestation_rid."""
    claimant_uri = await _setup_test_claimant(conn)
    reviewer_uri = await _setup_test_reviewer(conn, "Upsert Rev", "urn:test:rev-upsert")
    rid = await _setup_test_claim(conn, claimant_uri)

    resp1 = await client.post(f"/claims/{rid}/attestations", json={
        "reviewer_uri": reviewer_uri, "verdict": "pending", "rationale": "Initial review",
    })
    att_rid = resp1.json()["attestation_rid"]
    assert resp1.json()["verdict"] == "pending"

    resp2 = await client.post(f"/claims/{rid}/attestations", json={
        "reviewer_uri": reviewer_uri, "verdict": "approved", "rationale": "Confirmed",
    })
    assert resp2.json()["attestation_rid"] == att_rid  # Same RID
    assert resp2.json()["verdict"] == "approved"  # Updated
    assert resp2.json()["rationale"] == "Confirmed"


@pytest.mark.anyio
async def test_self_attestation_rejected(client, conn):
    """Self-attestation (reviewer = claimant) → 409."""
    claimant_uri = await _setup_test_claimant(conn)
    rid = await _setup_test_claim(conn, claimant_uri)

    resp = await client.post(f"/claims/{rid}/attestations", json={
        "reviewer_uri": claimant_uri,
        "verdict": "approved",
    })
    assert resp.status_code == 409
    assert "Self-attestation" in resp.json()["detail"]


@pytest.mark.anyio
async def test_invalid_reviewer_uri(client, conn):
    """Nonexistent reviewer_uri → 422."""
    claimant_uri = await _setup_test_claimant(conn)
    rid = await _setup_test_claim(conn, claimant_uri)

    resp = await client.post(f"/claims/{rid}/attestations", json={
        "reviewer_uri": "urn:test:doesnotexist",
        "verdict": "approved",
    })
    assert resp.status_code == 422
    assert "not found" in resp.json()["detail"]


@pytest.mark.anyio
async def test_wrong_reviewer_type_rejected(client, conn):
    """Reviewer with disallowed entity type (e.g. Evidence) → 422."""
    claimant_uri = await _setup_test_claimant(conn)
    rid = await _setup_test_claim(conn, claimant_uri)

    # Create an Evidence entity — not allowed as a reviewer
    evidence_uri = f"urn:test:evidence-{int(time.time() * 1000)}"
    await conn.execute("""
        INSERT INTO entity_registry (fuseki_uri, entity_text, entity_type, normalized_text)
        VALUES ($1, 'Some Evidence Doc', 'Evidence', 'some evidence doc')
        ON CONFLICT (fuseki_uri) DO NOTHING
    """, evidence_uri)

    resp = await client.post(f"/claims/{rid}/attestations", json={
        "reviewer_uri": evidence_uri,
        "verdict": "approved",
    })
    assert resp.status_code == 422
    assert "not valid for attestation" in resp.json()["detail"]


@pytest.mark.anyio
async def test_verify_with_policy_met(client, conn):
    """Verify to peer_reviewed with >= 1 approved attestation → 200."""
    claimant_uri = await _setup_test_claimant(conn)
    reviewer_uri = await _setup_test_reviewer(conn, "Policy Rev", "urn:test:rev-policy")
    # Migration must exist BEFORE the claim is created to avoid grandfathering
    migration_ts = datetime.now(timezone.utc) - timedelta(seconds=5)
    await _ensure_migration_row(conn, applied_at=migration_ts)
    rid = await _setup_test_claim(conn, claimant_uri)

    # Create approved attestation
    await client.post(f"/claims/{rid}/attestations", json={
        "reviewer_uri": reviewer_uri, "verdict": "approved",
    })

    resp = await client.patch(f"/claims/{rid}/verify", json={
        "new_level": "peer_reviewed", "actor": "test", "reason": "test",
    })
    assert resp.status_code == 200
    assert resp.json()["verification"] == "peer_reviewed"


@pytest.mark.anyio
async def test_verify_without_attestation_blocked(client, conn):
    """Verify to peer_reviewed without attestation → 409 (post-migration claim)."""
    claimant_uri = await _setup_test_claimant(conn)
    # Migration must exist BEFORE the claim is created to avoid grandfathering
    migration_ts = datetime.now(timezone.utc) - timedelta(seconds=5)
    await _ensure_migration_row(conn, applied_at=migration_ts)
    rid = await _setup_test_claim(conn, claimant_uri)

    resp = await client.patch(f"/claims/{rid}/verify", json={
        "new_level": "peer_reviewed", "actor": "test", "reason": "test",
    })
    assert resp.status_code == 409
    assert "attestation" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_grandfathered_claim_bypasses_policy(client, conn):
    """Pre-migration claim → verify without attestation → 200."""
    claimant_uri = await _setup_test_claimant(conn)
    # Create claim with timestamp BEFORE the migration
    old_time = datetime.now(timezone.utc) - timedelta(days=1)
    rid = await _setup_test_claim(conn, claimant_uri, created_at=old_time)
    # Apply migration AFTER the claim was created
    await _ensure_migration_row(conn, applied_at=datetime.now(timezone.utc))

    resp = await client.patch(f"/claims/{rid}/verify", json={
        "new_level": "peer_reviewed", "actor": "test", "reason": "grandfathered",
    })
    assert resp.status_code == 200
    assert resp.json()["verification"] == "peer_reviewed"


@pytest.mark.anyio
async def test_attestation_rid_stability(client, conn):
    """Same (claim, reviewer) always yields same RID."""
    from api.routers.claims_router import _attestation_rid

    rid1 = _attestation_rid("claim-A", "reviewer-X")
    rid2 = _attestation_rid("claim-A", "reviewer-X")
    rid3 = _attestation_rid("claim-A", "reviewer-Y")

    assert rid1 == rid2  # Stable
    assert rid1 != rid3  # Different reviewer → different RID
    assert rid1.startswith("orn:koi-net.attestation:")


@pytest.mark.anyio
async def test_content_hash_populated_on_create(client, conn):
    """Attestation content_hash should be populated immediately on create."""
    claimant_uri = await _setup_test_claimant(conn)
    reviewer_uri = await _setup_test_reviewer(conn, "Hash Rev", "urn:test:rev-hash")
    rid = await _setup_test_claim(conn, claimant_uri)

    resp = await client.post(f"/claims/{rid}/attestations", json={
        "reviewer_uri": reviewer_uri,
        "verdict": "approved",
        "rationale": "Hash test",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["content_hash"] is not None
    assert len(data["content_hash"]) == 64  # BLAKE2b-256 hex


@pytest.mark.anyio
async def test_content_hash_updates_on_verdict_change(client, conn):
    """Content_hash should change when verdict changes."""
    claimant_uri = await _setup_test_claimant(conn)
    reviewer_uri = await _setup_test_reviewer(conn, "Hash Change Rev", "urn:test:rev-hash-change")
    rid = await _setup_test_claim(conn, claimant_uri)

    resp1 = await client.post(f"/claims/{rid}/attestations", json={
        "reviewer_uri": reviewer_uri,
        "verdict": "pending",
    })
    hash1 = resp1.json()["content_hash"]

    resp2 = await client.post(f"/claims/{rid}/attestations", json={
        "reviewer_uri": reviewer_uri,
        "verdict": "approved",
    })
    hash2 = resp2.json()["content_hash"]

    assert hash1 != hash2  # Different verdict → different hash


@pytest.mark.anyio
async def test_anchor_attestation_rejects_pending_verdict(client, conn):
    """Anchor should reject attestations with pending verdict."""
    claimant_uri = await _setup_test_claimant(conn)
    reviewer_uri = await _setup_test_reviewer(conn, "Pending Rev", "urn:test:rev-pending-anchor")
    rid = await _setup_test_claim(conn, claimant_uri, verification="ledger_anchored")

    # Create pending attestation
    create_resp = await client.post(f"/claims/{rid}/attestations", json={
        "reviewer_uri": reviewer_uri,
        "verdict": "pending",
    })
    att_rid = create_resp.json()["attestation_rid"]

    # Attempt to anchor
    resp = await client.post(f"/claims/{rid}/attestations/{att_rid}/anchor")
    assert resp.status_code == 409
    assert "pending" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_anchor_attestation_rejects_non_anchored_parent(client, conn):
    """Anchor should reject when parent claim is not ledger_anchored."""
    claimant_uri = await _setup_test_claimant(conn)
    reviewer_uri = await _setup_test_reviewer(conn, "Non-Anch Rev", "urn:test:rev-non-anch")
    rid = await _setup_test_claim(conn, claimant_uri, verification="verified")

    create_resp = await client.post(f"/claims/{rid}/attestations", json={
        "reviewer_uri": reviewer_uri,
        "verdict": "approved",
    })
    att_rid = create_resp.json()["attestation_rid"]

    resp = await client.post(f"/claims/{rid}/attestations/{att_rid}/anchor")
    assert resp.status_code == 409
    assert "ledger_anchored" in resp.json()["detail"]


@pytest.mark.anyio
async def test_reconcile_attestation_no_tx_hash(client, conn):
    """Reconcile should return 409 when attestation has no tx_hash."""
    claimant_uri = await _setup_test_claimant(conn)
    reviewer_uri = await _setup_test_reviewer(conn, "Recon Rev", "urn:test:rev-recon")
    rid = await _setup_test_claim(conn, claimant_uri, verification="ledger_anchored")

    create_resp = await client.post(f"/claims/{rid}/attestations", json={
        "reviewer_uri": reviewer_uri,
        "verdict": "approved",
    })
    att_rid = create_resp.json()["attestation_rid"]

    resp = await client.post(f"/claims/{rid}/attestations/{att_rid}/reconcile")
    assert resp.status_code == 409
    assert "tx_hash" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_anchored_attestation_immutable(client, conn):
    """Updating an anchored attestation should return 409."""
    claimant_uri = await _setup_test_claimant(conn)
    reviewer_uri = await _setup_test_reviewer(conn, "Immut Rev", "urn:test:rev-immut")
    rid = await _setup_test_claim(conn, claimant_uri)

    # Create attestation
    resp1 = await client.post(f"/claims/{rid}/attestations", json={
        "reviewer_uri": reviewer_uri,
        "verdict": "approved",
        "rationale": "Initial review",
    })
    assert resp1.status_code == 201

    # Simulate anchoring by setting attest_tx_hash directly
    await conn.execute("""
        UPDATE claim_attestations
        SET attest_tx_hash = 'ABCDEF1234567890ABCDEF1234567890',
            ledger_iri = 'regen:13toVgf5aZqSVSeJQv562xkXKmjBcN'
        WHERE claim_rid = $1 AND reviewer_uri = $2
    """, rid, reviewer_uri)

    # Attempt to update — should be rejected
    resp2 = await client.post(f"/claims/{rid}/attestations", json={
        "reviewer_uri": reviewer_uri,
        "verdict": "rejected",
        "rationale": "Trying to change anchored attestation",
    })
    assert resp2.status_code == 409
    assert "immutable" in resp2.json()["detail"].lower()


@pytest.mark.anyio
async def test_unanchored_attestation_updatable(client, conn):
    """Unanchored attestation can still be updated via UPSERT."""
    claimant_uri = await _setup_test_claimant(conn)
    reviewer_uri = await _setup_test_reviewer(conn, "Update Rev", "urn:test:rev-update")
    rid = await _setup_test_claim(conn, claimant_uri)

    resp1 = await client.post(f"/claims/{rid}/attestations", json={
        "reviewer_uri": reviewer_uri,
        "verdict": "pending",
        "rationale": "Initial",
    })
    assert resp1.status_code == 201
    assert resp1.json()["verdict"] == "pending"

    # Update without anchoring — should succeed
    resp2 = await client.post(f"/claims/{rid}/attestations", json={
        "reviewer_uri": reviewer_uri,
        "verdict": "approved",
        "rationale": "Now confirmed",
    })
    assert resp2.status_code == 201
    assert resp2.json()["verdict"] == "approved"
    assert resp2.json()["rationale"] == "Now confirmed"


@pytest.mark.anyio
async def test_attestation_response_includes_anchor_fields(client, conn):
    """AttestationResponse should include ledger_iri, attest_timestamp, attestor_address."""
    claimant_uri = await _setup_test_claimant(conn)
    reviewer_uri = await _setup_test_reviewer(conn, "Fields Rev", "urn:test:rev-fields")
    rid = await _setup_test_claim(conn, claimant_uri)

    resp = await client.post(f"/claims/{rid}/attestations", json={
        "reviewer_uri": reviewer_uri,
        "verdict": "approved",
    })
    data = resp.json()
    # All anchor fields should be present (but null for non-anchored attestation)
    assert "ledger_iri" in data
    assert "attest_timestamp" in data
    assert "attestor_address" in data
    assert data["ledger_iri"] is None
    assert data["attest_timestamp"] is None
    assert data["attestor_address"] is None
