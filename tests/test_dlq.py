"""DLQ move/replay/sweep operations on koi_net_events_dead.

NOTE on schema (discovered live, diverges from prompt):
- koi_net_events has no `payload` column — it has `manifest` (jsonb) + `contents` (jsonb).
- koi_net_events_dead has `payload` (jsonb NOT NULL).
- The DLQ module maps `contents` (or {manifest, contents}) into `payload` on move.
"""

import json
import pytest
import pytest_asyncio
import asyncpg
from api.dlq import DeadLetterQueue


@pytest_asyncio.fixture
async def pool():
    pool = await asyncpg.create_pool(dsn="postgresql:///personal_koi_test")
    yield pool
    await pool.close()


@pytest_asyncio.fixture(autouse=True)
async def clean(pool):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM koi_net_events WHERE rid LIKE 'test:%'")
        await conn.execute("DELETE FROM koi_net_events_dead WHERE rid LIKE 'test:%'")
    yield


@pytest.mark.asyncio
async def test_move_event_max_attempts(pool):
    dlq = DeadLetterQueue(pool)
    async with pool.acquire() as conn:
        evt = await conn.fetchrow(
            "INSERT INTO koi_net_events"
            "(rid, event_type, contents, target_node, expires_at, delivery_attempts) "
            "VALUES ('test:1', 'UPDATE', $1::jsonb, 'peer+1', "
            "NOW() + interval '24 hours', 6) "
            "RETURNING id",
            json.dumps({"foo": "bar"}),
        )
        original_id = evt["id"]
    await dlq.move_event(original_id, reason="max_attempts")
    async with pool.acquire() as conn:
        live = await conn.fetchrow("SELECT 1 FROM koi_net_events WHERE id = $1", original_id)
        assert live is None
        dead = await conn.fetchrow(
            "SELECT * FROM koi_net_events_dead WHERE original_event_id = $1", original_id
        )
        assert dead is not None
        assert dead["dlq_reason"] == "max_attempts"
        assert dead["delivery_attempts"] == 6
        assert dead["target_node"] == "peer+1"


@pytest.mark.asyncio
async def test_sweep_expired(pool):
    dlq = DeadLetterQueue(pool)
    async with pool.acquire() as conn:
        evt = await conn.fetchrow(
            "INSERT INTO koi_net_events"
            "(rid, event_type, contents, target_node, expires_at, delivery_attempts) "
            "VALUES ('test:2', 'UPDATE', $1::jsonb, 'peer+2', "
            "NOW() - interval '1 minute', 2) "
            "RETURNING id",
            json.dumps({"baz": "qux"}),
        )
    moved = await dlq.sweep_expired()
    assert moved >= 1
    async with pool.acquire() as conn:
        dead = await conn.fetchrow(
            "SELECT dlq_reason FROM koi_net_events_dead WHERE original_event_id = $1",
            evt["id"],
        )
        assert dead is not None
        assert dead["dlq_reason"] == "ttl_expired"


@pytest.mark.asyncio
async def test_replay_returns_to_live_queue(pool):
    dlq = DeadLetterQueue(pool)
    async with pool.acquire() as conn:
        evt = await conn.fetchrow(
            "INSERT INTO koi_net_events"
            "(rid, event_type, contents, target_node, expires_at, delivery_attempts) "
            "VALUES ('test:3', 'UPDATE', $1::jsonb, 'peer+3', "
            "NOW() + interval '1 hour', 6) "
            "RETURNING id",
            json.dumps({}),
        )
    await dlq.move_event(evt["id"], reason="max_attempts")
    dlq_row = await dlq.fetch_by_original_id(evt["id"])
    assert dlq_row is not None
    new_id = await dlq.replay(dlq_row["id"])
    async with pool.acquire() as conn:
        re = await conn.fetchrow(
            "SELECT delivery_attempts FROM koi_net_events WHERE id = $1", new_id
        )
        assert re["delivery_attempts"] == 0


def _as_dict(v):
    """asyncpg returns jsonb as str by default; normalize for comparison."""
    if isinstance(v, str):
        return json.loads(v)
    return v


@pytest.mark.asyncio
async def test_replay_preserves_manifest_and_source_node(pool):
    """DLQ round-trip must not lose manifest or source_node."""
    dlq = DeadLetterQueue(pool)
    async with pool.acquire() as conn:
        evt = await conn.fetchrow(
            "INSERT INTO koi_net_events"
            "(rid, event_type, contents, manifest, target_node, source_node, "
            " expires_at, delivery_attempts) "
            "VALUES ('test:roundtrip', 'UPDATE', "
            " $1::jsonb, $2::jsonb, 'peer+1', 'origin+1', "
            " NOW() + interval '1 hour', 6) RETURNING id",
            json.dumps({"body": "preserved"}),
            json.dumps({"hash": "abc123", "share_type": "vault_sync"}),
        )
    await dlq.move_event(evt["id"], reason="max_attempts")

    dlq_row = await dlq.fetch_by_original_id(evt["id"])
    assert _as_dict(dlq_row["manifest"]) == {"hash": "abc123", "share_type": "vault_sync"}
    assert dlq_row["source_node"] == "origin+1"

    new_id = await dlq.replay(dlq_row["id"])
    async with pool.acquire() as conn:
        re = await conn.fetchrow(
            "SELECT contents, manifest, source_node FROM koi_net_events WHERE id = $1",
            new_id,
        )
        assert _as_dict(re["contents"]) == {"body": "preserved"}
        assert _as_dict(re["manifest"]) == {"hash": "abc123", "share_type": "vault_sync"}
        assert re["source_node"] == "origin+1"
