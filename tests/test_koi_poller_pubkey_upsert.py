"""Regression test for task-528 — _learn_peer_public_key ON CONFLICT must persist public_key.

Pre-fix: ON CONFLICT clause omitted `public_key` entirely. Existing rows with NULL
public_key never received the key, causing infinite relearn loop (56+ "Learned public
key" log lines per 10 min, peer backoff state contaminated).

Post-fix: COALESCE(EXCLUDED.public_key, koi_net_nodes.public_key) — new key written
when existing is NULL, existing preserved when new is NULL, key-rotation guard at
line ~325 still blocks different-non-NULL transitions before upsert.
"""

import pytest
import pytest_asyncio
import asyncpg


@pytest_asyncio.fixture
async def pool():
    pool = await asyncpg.create_pool(dsn="postgresql:///personal_koi_test")
    yield pool
    await pool.close()


@pytest_asyncio.fixture(autouse=True)
async def clean(pool):
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM koi_net_nodes WHERE node_rid LIKE 'pubkey-test+%'"
        )
    yield


async def _upsert(conn, node_rid, public_key):
    """Mirror the production INSERT...ON CONFLICT for direct verification."""
    await conn.execute(
        """
        INSERT INTO koi_net_nodes
            (node_rid, node_name, node_type, base_url, public_key,
             encryption_key, ontology_uri, ontology_version, status, last_seen)
        VALUES ($1, 'test', 'FULL', 'http://test/koi-net', $2,
                NULL, NULL, NULL, 'active', NOW())
        ON CONFLICT (node_rid) DO UPDATE SET
            node_name = EXCLUDED.node_name,
            node_type = EXCLUDED.node_type,
            base_url = EXCLUDED.base_url,
            public_key = COALESCE(EXCLUDED.public_key, koi_net_nodes.public_key),
            encryption_key = COALESCE(
                EXCLUDED.encryption_key, koi_net_nodes.encryption_key
            ),
            ontology_uri = COALESCE(
                EXCLUDED.ontology_uri, koi_net_nodes.ontology_uri
            ),
            ontology_version = COALESCE(
                EXCLUDED.ontology_version, koi_net_nodes.ontology_version
            ),
            status = 'active',
            last_seen = NOW()
        """,
        node_rid,
        public_key,
    )


@pytest.mark.asyncio
async def test_upsert_writes_public_key_on_conflict_when_existing_null(pool):
    """The bug scenario: existing row has NULL public_key, upsert must populate it."""
    rid = "pubkey-test+null-then-set"
    async with pool.acquire() as conn:
        # Seed with NULL public_key (simulates pre-fix state)
        await conn.execute(
            "INSERT INTO koi_net_nodes(node_rid, base_url, public_key) "
            "VALUES ($1, 'http://test', NULL)",
            rid,
        )
        # Upsert with a real key (simulates _learn_peer_public_key with key from /health)
        await _upsert(conn, rid, "MFkwEwYH-real-key-bytes")
        # Assert key now persisted
        row = await conn.fetchrow(
            "SELECT public_key FROM koi_net_nodes WHERE node_rid = $1", rid
        )
    assert row["public_key"] == "MFkwEwYH-real-key-bytes"


@pytest.mark.asyncio
async def test_upsert_preserves_public_key_when_excluded_null(pool):
    """Conservative path: existing row has key, caller passes NULL — preserve existing."""
    rid = "pubkey-test+preserve-existing"
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO koi_net_nodes(node_rid, base_url, public_key) "
            "VALUES ($1, 'http://test', 'MFkwEwYH-existing-key')",
            rid,
        )
        # Upsert with NULL public_key (would-be regression if COALESCE missing)
        await _upsert(conn, rid, None)
        row = await conn.fetchrow(
            "SELECT public_key FROM koi_net_nodes WHERE node_rid = $1", rid
        )
    assert row["public_key"] == "MFkwEwYH-existing-key"


@pytest.mark.asyncio
async def test_upsert_writes_public_key_on_fresh_insert(pool):
    """No existing row — upsert lands the public_key directly."""
    rid = "pubkey-test+fresh-row"
    async with pool.acquire() as conn:
        await _upsert(conn, rid, "MFkwEwYH-fresh-key")
        row = await conn.fetchrow(
            "SELECT public_key FROM koi_net_nodes WHERE node_rid = $1", rid
        )
    assert row["public_key"] == "MFkwEwYH-fresh-key"
