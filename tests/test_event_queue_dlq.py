"""record_delivery_failure() increments attempts; > MAX_DELIVERY_ATTEMPTS -> DLQ."""

import json
import pytest
import pytest_asyncio
import asyncpg
from api.event_queue import EventQueue


@pytest_asyncio.fixture
async def pool():
    pool = await asyncpg.create_pool(dsn="postgresql:///personal_koi_test")
    yield pool
    await pool.close()


@pytest_asyncio.fixture(autouse=True)
async def clean(pool):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM koi_net_events WHERE rid LIKE 'test-dlq:%'")
        await conn.execute("DELETE FROM koi_net_events_dead WHERE rid LIKE 'test-dlq:%'")
    yield


@pytest.mark.asyncio
async def test_delivery_failure_increments(pool):
    eq = EventQueue(pool, node_rid="self+test")
    async with pool.acquire() as conn:
        evt = await conn.fetchrow(
            "INSERT INTO koi_net_events(rid, event_type, contents, target_node, expires_at) "
            "VALUES ('test-dlq:1', 'UPDATE', $1::jsonb, 'peer+1', "
            "NOW() + interval '1 hour') RETURNING id",
            json.dumps({}),
        )
    await eq.record_delivery_failure(evt["id"])
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT delivery_attempts FROM koi_net_events WHERE id = $1", evt["id"]
        )
        assert row["delivery_attempts"] == 1


@pytest.mark.asyncio
async def test_attempts_exceed_max_moves_to_dlq(pool):
    eq = EventQueue(pool, node_rid="self+test")
    async with pool.acquire() as conn:
        evt = await conn.fetchrow(
            "INSERT INTO koi_net_events(rid, event_type, contents, target_node, expires_at, delivery_attempts) "
            "VALUES ('test-dlq:2', 'UPDATE', $1::jsonb, 'peer+1', "
            "NOW() + interval '1 hour', 5) RETURNING id",
            json.dumps({}),
        )
    await eq.record_delivery_failure(evt["id"])
    async with pool.acquire() as conn:
        live = await conn.fetchrow("SELECT 1 FROM koi_net_events WHERE id = $1", evt["id"])
        assert live is None
        dead = await conn.fetchrow(
            "SELECT dlq_reason FROM koi_net_events_dead WHERE original_event_id = $1",
            evt["id"],
        )
        assert dead["dlq_reason"] == "max_attempts"
