"""Startup sync sweep tests."""

import pytest
import pytest_asyncio
from unittest.mock import patch, AsyncMock
import asyncpg

from api.startup_sweep import StartupSweep


@pytest_asyncio.fixture
async def pool():
    pool = await asyncpg.create_pool(dsn="postgresql:///personal_koi_test")
    yield pool
    await pool.close()


@pytest_asyncio.fixture(autouse=True)
async def clean(pool):
    """Strip every peer with a base_url so each test runs against a known set."""
    async with pool.acquire() as conn:
        # Save and null out any pre-existing base_urls so the sweep won't pick them up.
        await conn.execute(
            "UPDATE koi_net_nodes SET base_url = NULL "
            "WHERE node_rid NOT LIKE 'sweep+%'"
        )
        await conn.execute("DELETE FROM koi_net_nodes WHERE node_rid LIKE 'sweep+%'")
        await conn.execute(
            "DELETE FROM koi_net_runtime_state WHERE key = 'last_sweep_at'"
        )
    yield
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM koi_net_nodes WHERE node_rid LIKE 'sweep+%'")


@pytest.mark.asyncio
async def test_sweep_polls_each_active_peer_once(pool):
    sweep = StartupSweep(pool, node_rid="self+test")
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO koi_net_nodes(node_rid, base_url) "
            "VALUES ('sweep+a', 'http://a/koi-net'), ('sweep+b', 'http://b/koi-net')"
        )

    called = []

    async def fake_poll(self, peer_rid):
        called.append(peer_rid)

    with patch.object(StartupSweep, "_poll_peer", new=fake_poll):
        count = await sweep.run()
    assert count == 2
    assert sorted(called) == ["sweep+a", "sweep+b"]


@pytest.mark.asyncio
async def test_sweep_skips_peer_in_backoff(pool):
    sweep = StartupSweep(pool, node_rid="self+test")
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO koi_net_nodes(node_rid, base_url, backoff_until) "
            "VALUES ('sweep+c', 'http://c/koi-net', NOW() + interval '60 seconds')"
        )

    called = []

    async def fake_poll(self, peer_rid):
        called.append(peer_rid)

    with patch.object(StartupSweep, "_poll_peer", new=fake_poll):
        count = await sweep.run()
    assert count == 0
    assert called == []


@pytest.mark.asyncio
async def test_sweep_records_last_sweep_at(pool):
    sweep = StartupSweep(pool, node_rid="self+test")
    with patch.object(StartupSweep, "_poll_peer", new=AsyncMock()):
        await sweep.run()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT value FROM koi_net_runtime_state WHERE key = 'last_sweep_at'"
        )
    assert row is not None
