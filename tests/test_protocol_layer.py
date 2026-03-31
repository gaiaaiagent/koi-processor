"""In-process pytest tests for the Claims × Spore protocol layer.

Tests requirements, coverage_links, signals, gap computation, and
the small router extensions (since filter, offer_type filter, scope on responses).

Run:  pytest tests/test_protocol_layer.py -v
Requires: PostgreSQL personal_koi running locally with migrations 079-082 applied.
          Uses rollback transactions — no persistent side effects.
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DB_URL = os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")

# Mark all async tests in this module
pytestmark = pytest.mark.asyncio


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


def _build_app(fake_pool) -> FastAPI:
    app = FastAPI()
    from api.routers.protocol_router import create_protocol_router
    app.include_router(create_protocol_router(fake_pool), prefix="/protocol")
    return app


def _build_claims_app(fake_pool) -> FastAPI:
    app = FastAPI()
    from api.routers.claims_router import create_router as create_claims_router
    app.include_router(create_claims_router(fake_pool))
    return app


def _build_commitment_app(fake_pool) -> FastAPI:
    app = FastAPI()
    from api.routers.commitment_router import create_router as create_commitment_router, create_pool_router
    app.include_router(create_commitment_router(fake_pool))
    app.include_router(create_pool_router(fake_pool))
    return app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def test_env():
    """Shared env: rollback transaction, fake pool, protocol + claims + commitment apps."""
    c = await asyncpg.connect(DB_URL)
    tx = c.transaction()
    await tx.start()
    try:
        fp = _SingleConnPool(c)
        yield c, fp
    finally:
        await tx.rollback()
        await c.close()


@pytest_asyncio.fixture
async def protocol_client(test_env):
    conn, fp = test_env
    app = _build_app(fp)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, conn


@pytest_asyncio.fixture
async def claims_client(test_env):
    conn, fp = test_env
    app = _build_claims_app(fp)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, conn


@pytest_asyncio.fixture
async def commitment_client(test_env):
    conn, fp = test_env
    app = _build_commitment_app(fp)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, conn


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

async def _seed_pool(conn, pool_rid="orn:koi-net.pool:test-herring"):
    """Insert a minimal commitment pool for testing."""
    await conn.execute("""
        INSERT INTO commitment_pools (pool_rid, name, state, scope)
        VALUES ($1, 'Test Herring Pool', 'forming', 'pool')
        ON CONFLICT (pool_rid) DO NOTHING
    """, pool_rid)
    return pool_rid


async def _seed_requirement(conn, pool_rid, statement="Quarterly herring monitoring",
                            frequency="quarterly", freshness_days=90, severity="high"):
    """Insert a requirement scoped to a pool."""
    ts = int(time.time() * 1_000_000)
    rid = f"orn:koi-net.requirement:test-{ts}"
    await conn.execute("""
        INSERT INTO requirements (
            requirement_rid, scope, scope_ref, policy_source,
            requirement_type, statement, frequency,
            freshness_window_days, severity
        ) VALUES ($1, 'pool', $2, 'test-constitution', 'monitoring', $3, $4, $5, $6)
    """, rid, pool_rid, statement, frequency, freshness_days, severity)
    return rid


async def _seed_coverage(conn, source_rid, target_rid, *,
                         valid_from=None, valid_until=None,
                         coverage_type="commitment_covers_requirement"):
    """Insert a coverage link."""
    ts = int(time.time() * 1_000_000)
    rid = f"orn:koi-net.coverage:test-{ts}"
    vf = valid_from or datetime.now(timezone.utc)
    await conn.execute("""
        INSERT INTO coverage_links (
            coverage_rid, coverage_type, source_rid, target_rid,
            valid_from, valid_until, confidence, provenance
        ) VALUES ($1, $2, $3, $4, $5, $6, 0.9, 'manual')
    """, rid, coverage_type, source_rid, target_rid, vf, valid_until)
    return rid


async def _seed_commitment(conn, pool_rid, commitment_rid=None):
    """Insert a minimal commitment in a pool."""
    ts = int(time.time() * 1_000_000)
    rid = commitment_rid or f"orn:koi-net.commitment:test-{ts}"
    pool_id = await conn.fetchval(
        "SELECT id FROM commitment_pools WHERE pool_rid = $1", pool_rid)
    await conn.execute("""
        INSERT INTO commitments (commitment_rid, pledger_uri, pool_id, title, offer_type)
        VALUES ($1, 'urn:test:pledger', $2, 'Test commitment', 'stewardship')
    """, rid, pool_id)
    return rid


# ===========================================================================
# Test 1: Requirement RID uniqueness across two pools
# ===========================================================================

async def test_requirement_rid_different_pools(protocol_client):
    """Same statement + policy_source in two different pools must produce different RIDs."""
    client, conn = protocol_client
    body = {
        "scope": "pool",
        "policy_source": "test-constitution",
        "requirement_type": "monitoring",
        "statement": "Quarterly habitat monitoring",
        "frequency": "quarterly",
        "severity": "high",
    }

    r1 = await client.post("/protocol/requirements/create",
                           json={**body, "scope_ref": "pool-alpha"})
    assert r1.status_code == 201
    rid_a = r1.json()["requirement_rid"]

    r2 = await client.post("/protocol/requirements/create",
                           json={**body, "scope_ref": "pool-beta"})
    assert r2.status_code == 201
    rid_b = r2.json()["requirement_rid"]

    assert rid_a != rid_b, "Same statement in different pools must produce different RIDs"


# ===========================================================================
# Test 2: Requirement upsert updates mutable fields
# ===========================================================================

async def test_requirement_upsert_mutable_fields(protocol_client):
    """Re-creating the same requirement should update severity, frequency, etc."""
    client, conn = protocol_client
    body = {
        "scope": "pool",
        "scope_ref": "pool-upsert-test",
        "policy_source": "test-constitution",
        "requirement_type": "monitoring",
        "statement": "Monthly water quality check",
        "frequency": "monthly",
        "severity": "medium",
    }

    r1 = await client.post("/protocol/requirements/create", json=body)
    assert r1.status_code == 201
    rid = r1.json()["requirement_rid"]
    assert r1.json()["severity"] == "medium"

    r2 = await client.post("/protocol/requirements/create",
                           json={**body, "severity": "critical"})
    assert r2.status_code == 201
    assert r2.json()["requirement_rid"] == rid
    assert r2.json()["severity"] == "critical"


# ===========================================================================
# Test 3: Gap signal recomputation upserts metadata
# ===========================================================================

async def test_gap_signal_recomputation_upserts(protocol_client):
    """Hitting /gaps twice should update the signal metadata, not silently skip."""
    client, conn = protocol_client
    pool_rid = await _seed_pool(conn)
    req_rid = await _seed_requirement(conn, pool_rid)

    r1 = await client.get(f"/protocol/pools/{pool_rid}/gaps")
    assert r1.status_code == 200
    gaps1 = r1.json()
    assert gaps1["unmet_count"] == 1
    sig_rid = gaps1["gaps"][0]["signal_rid"]

    r2 = await client.get(f"/protocol/pools/{pool_rid}/gaps")
    assert r2.status_code == 200
    assert r2.json()["gaps"][0]["signal_rid"] == sig_rid

    row = await conn.fetchrow(
        "SELECT metadata FROM signals WHERE signal_rid = $1", sig_rid)
    meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
    assert "computed_at" in meta


# ===========================================================================
# Test 4: Future-dated coverage does not suppress a gap
# ===========================================================================

async def test_future_coverage_does_not_suppress_gap(protocol_client):
    """A coverage link with valid_from in the future should not count as coverage."""
    client, conn = protocol_client
    pool_rid = await _seed_pool(conn)
    req_rid = await _seed_requirement(conn, pool_rid)
    commit_rid = await _seed_commitment(conn, pool_rid)

    future = datetime.now(timezone.utc) + timedelta(days=1)
    await _seed_coverage(conn, commit_rid, req_rid, valid_from=future)

    r = await client.get(f"/protocol/pools/{pool_rid}/gaps")
    assert r.status_code == 200
    data = r.json()
    assert data["unmet_count"] == 1, "Future coverage should not suppress a gap"
    assert data["covered_count"] == 0


# ===========================================================================
# Test 5: Wrong coverage type does not suppress a gap
# ===========================================================================

async def test_wrong_coverage_type_does_not_suppress_gap(protocol_client):
    """Only commitment_covers_requirement should count for pool gaps."""
    client, conn = protocol_client
    pool_rid = await _seed_pool(conn)
    req_rid = await _seed_requirement(conn, pool_rid)

    await _seed_coverage(conn, "orn:koi-net.claim:fake", req_rid,
                         coverage_type="claim_covers_condition")

    r = await client.get(f"/protocol/pools/{pool_rid}/gaps")
    assert r.status_code == 200
    assert r.json()["unmet_count"] == 1, "claim_covers_condition should not satisfy a requirement"


# ===========================================================================
# Test 6: Valid coverage suppresses a gap
# ===========================================================================

async def test_valid_coverage_suppresses_gap(protocol_client):
    """A current commitment_covers_requirement link should make the requirement covered."""
    client, conn = protocol_client
    pool_rid = await _seed_pool(conn)
    req_rid = await _seed_requirement(conn, pool_rid)
    commit_rid = await _seed_commitment(conn, pool_rid)

    await _seed_coverage(conn, commit_rid, req_rid,
                         valid_from=datetime.now(timezone.utc) - timedelta(days=1))

    r = await client.get(f"/protocol/pools/{pool_rid}/gaps")
    assert r.status_code == 200
    data = r.json()
    assert data["covered_count"] == 1
    assert data["unmet_count"] == 0
    assert len(data["gaps"]) == 0


# ===========================================================================
# Test 7: Expired coverage produces stale gap
# ===========================================================================

async def test_expired_coverage_produces_stale_gap(protocol_client):
    """Coverage that has expired should produce a 'stale' gap, not 'unmet'."""
    client, conn = protocol_client
    pool_rid = await _seed_pool(conn)
    req_rid = await _seed_requirement(conn, pool_rid)
    commit_rid = await _seed_commitment(conn, pool_rid)

    past_start = datetime.now(timezone.utc) - timedelta(days=120)
    past_end = datetime.now(timezone.utc) - timedelta(days=30)
    await _seed_coverage(conn, commit_rid, req_rid,
                         valid_from=past_start, valid_until=past_end)

    r = await client.get(f"/protocol/pools/{pool_rid}/gaps")
    assert r.status_code == 200
    data = r.json()
    assert data["stale_count"] == 1
    assert data["gaps"][0]["gap_type"] == "stale"


# ===========================================================================
# Test 8: Malformed since returns 422
# ===========================================================================

async def test_claims_since_malformed_returns_422(claims_client):
    """Malformed since parameter should return 422, not a database error."""
    client, conn = claims_client
    r = await client.get("/claims/?since=not-a-date")
    assert r.status_code == 422, f"Expected 422, got {r.status_code}"


# ===========================================================================
# Test 9: Claims since filter works with valid datetime
# ===========================================================================

async def test_claims_since_valid_datetime(claims_client):
    """Valid since parameter should filter claims without error."""
    client, conn = claims_client
    r = await client.get("/claims/?since=2026-01-01T00:00:00Z")
    assert r.status_code == 200


# ===========================================================================
# Test 10: Commitment response includes scope
# ===========================================================================

async def test_commitment_response_includes_scope(commitment_client):
    """After migration 080, commitment responses should include scope."""
    client, conn = commitment_client
    pool_rid = await _seed_pool(conn)
    commit_rid = await _seed_commitment(conn, pool_rid)
    await conn.execute(
        "UPDATE commitments SET scope = 'pool' WHERE commitment_rid = $1", commit_rid)

    r = await client.get(f"/commitments/{commit_rid}")
    assert r.status_code == 200
    assert r.json()["scope"] == "pool"


# ===========================================================================
# Test 11: Pool response includes scope
# ===========================================================================

async def test_pool_response_includes_scope(commitment_client):
    """After migration 080, pool responses should include scope."""
    client, conn = commitment_client
    pool_rid = await _seed_pool(conn)

    r = await client.get(f"/pools/{pool_rid}")
    assert r.status_code == 200
    assert r.json()["scope"] == "pool"


# ===========================================================================
# Test 12: End-to-end gap path
# ===========================================================================

async def test_e2e_gap_path(protocol_client):
    """Full demo path: create pool → create requirement → no coverage → gap + signal."""
    client, conn = protocol_client
    pool_rid = await _seed_pool(conn, pool_rid="orn:koi-net.pool:e2e-test")

    # 1. Create requirement via API
    req_r = await client.post("/protocol/requirements/create", json={
        "scope": "pool",
        "scope_ref": pool_rid,
        "policy_source": "e2e-test-constitution",
        "requirement_type": "monitoring",
        "statement": "Monthly biodiversity survey",
        "frequency": "monthly",
        "freshness_window_days": 35,
        "severity": "high",
    })
    assert req_r.status_code == 201
    req_rid = req_r.json()["requirement_rid"]

    # 2. Verify requirement shows in list
    list_r = await client.get(f"/protocol/requirements/?scope_ref={pool_rid}")
    assert list_r.status_code == 200
    assert len(list_r.json()) == 1

    # 3. Compute gaps — should find 1 unmet
    gaps_r = await client.get(f"/protocol/pools/{pool_rid}/gaps")
    assert gaps_r.status_code == 200
    data = gaps_r.json()
    assert data["total_requirements"] == 1
    assert data["unmet_count"] == 1
    assert data["covered_count"] == 0

    gap = data["gaps"][0]
    assert gap["gap_type"] == "unmet"
    assert gap["severity"] == "high"
    assert gap["next_move"] == "propose_commitment"
    assert gap["signal_rid"] is not None

    # 4. Verify signal was emitted
    sig_r = await client.get(f"/protocol/signals/?source_ref={pool_rid}&signal_type=gap_computed")
    assert sig_r.status_code == 200
    assert len(sig_r.json()) >= 1

    # 5. Add coverage and re-check — gap should resolve
    commit_rid = await _seed_commitment(conn, pool_rid)
    await _seed_coverage(conn, commit_rid, req_rid,
                         valid_from=datetime.now(timezone.utc) - timedelta(hours=1))

    gaps_r2 = await client.get(f"/protocol/pools/{pool_rid}/gaps")
    assert gaps_r2.status_code == 200
    data2 = gaps_r2.json()
    assert data2["covered_count"] == 1
    assert data2["unmet_count"] == 0
    assert len(data2["gaps"]) == 0


# ===========================================================================
# Test 13: Pool not found returns 404
# ===========================================================================

async def test_gaps_pool_not_found(protocol_client):
    client, conn = protocol_client
    r = await client.get("/protocol/pools/nonexistent-pool/gaps")
    assert r.status_code == 404


# ===========================================================================
# Test 14: Commitment offer_type filter
# ===========================================================================

async def test_commitment_offer_type_filter(commitment_client):
    """The new offer_type filter should narrow commitment listings."""
    client, conn = commitment_client
    pool_rid = await _seed_pool(conn)
    await _seed_commitment(conn, pool_rid)  # default: stewardship

    pool_id = await conn.fetchval(
        "SELECT id FROM commitment_pools WHERE pool_rid = $1", pool_rid)
    await conn.execute("""
        INSERT INTO commitments (commitment_rid, pledger_uri, pool_id, title, offer_type)
        VALUES ('orn:koi-net.commitment:labor-test', 'urn:test:pledger', $1, 'Labor work', 'labor')
    """, pool_id)

    r_all = await client.get("/commitments/")
    all_count = len(r_all.json())

    r_stew = await client.get("/commitments/?offer_type=stewardship")
    assert r_stew.status_code == 200
    stew = r_stew.json()
    assert all(c["offer_type"] == "stewardship" for c in stew)
    assert len(stew) < all_count or all_count == 1


# ===========================================================================
# Test 15: Same-pool same-statement different subjects produce different RIDs
# ===========================================================================

async def test_requirement_rid_different_subjects_same_pool(protocol_client):
    """Same statement in same pool but different subject_uri must produce different RIDs."""
    client, conn = protocol_client
    body = {
        "scope": "pool",
        "scope_ref": "pool-multi-subject",
        "policy_source": "test-constitution",
        "requirement_type": "monitoring",
        "statement": "Quarterly monitoring required",
        "frequency": "quarterly",
        "severity": "high",
    }

    r1 = await client.post("/protocol/requirements/create",
                           json={**body, "subject_uri": "urn:species:herring"})
    assert r1.status_code == 201
    rid_a = r1.json()["requirement_rid"]

    r2 = await client.post("/protocol/requirements/create",
                           json={**body, "subject_uri": "urn:species:salmon"})
    assert r2.status_code == 201
    rid_b = r2.json()["requirement_rid"]

    assert rid_a != rid_b, "Same statement for different subjects must produce different RIDs"


# ===========================================================================
# Test 16: Future-dated coverage with finite end is unmet, not stale
# ===========================================================================

async def test_future_coverage_with_end_date_is_unmet(protocol_client):
    """Coverage starting next week and ending next month should be unmet, not stale."""
    client, conn = protocol_client
    pool_rid = await _seed_pool(conn, pool_rid="orn:koi-net.pool:future-end-test")
    req_rid = await _seed_requirement(conn, pool_rid)
    commit_rid = await _seed_commitment(conn, pool_rid)

    # Future-dated coverage with a finite end date
    future_start = datetime.now(timezone.utc) + timedelta(days=7)
    future_end = datetime.now(timezone.utc) + timedelta(days=37)
    await _seed_coverage(conn, commit_rid, req_rid,
                         valid_from=future_start, valid_until=future_end)

    r = await client.get(f"/protocol/pools/{pool_rid}/gaps")
    assert r.status_code == 200
    data = r.json()
    assert data["unmet_count"] == 1, "Future coverage with end date should be unmet, not stale"
    assert data["stale_count"] == 0, "No expired past coverage exists — should not be stale"
    assert data["gaps"][0]["gap_type"] == "unmet"
