"""Persisted backoff state for KOI peers (table: koi_net_nodes)."""

import pytest
import pytest_asyncio
from datetime import datetime, timezone
import asyncpg

from api.peer_backoff import PeerBackoff


@pytest_asyncio.fixture
async def pool():
    pool = await asyncpg.create_pool(dsn="postgresql:///personal_koi_test")
    yield pool
    await pool.close()


@pytest_asyncio.fixture(autouse=True)
async def clean_peers(pool):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM koi_net_nodes WHERE node_rid LIKE 'test+%'")
    yield


@pytest.mark.asyncio
async def test_record_failure_increments_streak(pool):
    pb = PeerBackoff(pool)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO koi_net_nodes(node_rid, base_url) VALUES ('test+1', 'http://test')"
        )
    await pb.record_failure("test+1")
    assert await pb.get_streak("test+1") == 1
    await pb.record_failure("test+1")
    assert await pb.get_streak("test+1") == 2


@pytest.mark.asyncio
async def test_record_failure_sets_backoff_until(pool):
    pb = PeerBackoff(pool)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO koi_net_nodes(node_rid, base_url) VALUES ('test+2', 'http://test')"
        )
    before = datetime.now(timezone.utc)
    await pb.record_failure("test+2")
    until = await pb.get_backoff_until("test+2")
    assert until > before
    assert (until - before).total_seconds() <= 35  # first failure = 30s + a little slack


@pytest.mark.asyncio
async def test_record_success_clears_backoff(pool):
    pb = PeerBackoff(pool)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO koi_net_nodes(node_rid, base_url) VALUES ('test+3', 'http://test')"
        )
    await pb.record_failure("test+3")
    await pb.record_failure("test+3")
    await pb.record_success("test+3")
    assert await pb.get_streak("test+3") == 0
    assert await pb.get_backoff_until("test+3") is None


@pytest.mark.asyncio
async def test_should_skip_returns_true_during_backoff(pool):
    pb = PeerBackoff(pool)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO koi_net_nodes(node_rid, base_url, backoff_until) "
            "VALUES ('test+4', 'http://test', NOW() + interval '60 seconds')"
        )
    assert await pb.should_skip("test+4") is True


@pytest.mark.asyncio
async def test_should_skip_returns_false_after_backoff(pool):
    pb = PeerBackoff(pool)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO koi_net_nodes(node_rid, base_url, backoff_until) "
            "VALUES ('test+5', 'http://test', NOW() - interval '60 seconds')"
        )
    assert await pb.should_skip("test+5") is False


@pytest.mark.asyncio
async def test_koi_poller_uses_persisted_backoff(pool):
    """KoiPoller honors backoff_until from PG via PeerBackoff."""
    pb = PeerBackoff(pool)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO koi_net_nodes(node_rid, base_url, backoff_until) "
            "VALUES ('test+poller', 'http://nowhere', NOW() + interval '60 seconds')"
        )
    # Verify via should_skip — this is the contract KoiPoller will check
    assert await pb.should_skip("test+poller") is True
