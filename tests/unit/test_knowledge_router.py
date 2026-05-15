"""Unit tests for knowledge_router.py — Federation Phase 1 step 2e.

Covers the `knowledge_episode` bundled emit added to `POST /knowledge/episodes`:
the post-commit emit ordering, the feature-flag gate, the publisher→subscriber
payload roundtrip, the `_federation_event_id` chain, the embedding-column
discriminator, and a regression check that the endpoint's existing contract is
unchanged with the flag off.

Plan: ~/.claude/plans/koi-graph-graceful-toucan.md (Phase 1 step 8 / 2e).

Style mirrors tests/unit/test_domain_event_handlers.py — a single asyncpg
connection wrapped in a transaction that is rolled back at teardown, so no DB
state leaks. The endpoint opens no explicit transaction of its own (asyncpg
auto-commits per statement); wrapping the one connection in an outer
transaction keeps every write inside the rollback boundary.
"""

import os
import uuid

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import api.federation_events as federation_events

DB_URL = os.getenv(
    "POSTGRES_URL",
    "postgresql://darrenzal:@localhost:5432/personal_koi",
)
TEST_SOURCE_NODE = "orn:koi-net.node:test-peer+aaaa"
EMBED_DIM = 3072


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _InstrumentedPool:
    """Wraps a single asyncpg.Connection to quack like asyncpg.Pool.

    Records whether the `async with pool.acquire()` block has exited — used by
    `test_emit_fires_after_commit` to prove the emit happens OUTSIDE that block.
    """

    def __init__(self, conn):
        self._conn = conn
        self.acquire_exited = False

    class _CM:
        def __init__(self, pool):
            self.pool = pool

        async def __aenter__(self):
            return self.pool._conn

        async def __aexit__(self, *exc):
            self.pool.acquire_exited = True

    def acquire(self):
        return self._CM(self)


class _FakeQueue:
    """Stand-in for EventQueue. Records `.add()` calls instead of touching the
    DB, and snapshots the pool's acquire-exited flag at call time so a test can
    assert the emit fired post-commit (outside the acquire block).
    """

    def __init__(self, pool):
        self._pool = pool
        self.added = []
        self.acquire_exited_at_add = None

    async def add(self, **kwargs):
        self.acquire_exited_at_add = self._pool.acquire_exited
        self.added.append(kwargs)
        return kwargs.get("event_id")


async def _fake_embed(text, **kwargs):
    """Deterministic, distinct-per-text, non-zero 3072-dim embedding."""
    seed = hash(text)
    return [float(((seed + i) % 97) + 1) / 97.0 for i in range(EMBED_DIM)]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def harness():
    """Yield (client, conn, fake_queue, pool) wired against a rolled-back txn."""
    from api.routers.knowledge_router import create_router

    conn = await asyncpg.connect(DB_URL)
    tx = conn.transaction()
    await tx.start()

    pool = _InstrumentedPool(conn)
    fake_queue = _FakeQueue(pool)

    # Wire the publisher's module-global event queue to the fake.
    prev_queue = federation_events._event_queue
    federation_events.set_event_queue(fake_queue)

    router = create_router(pool, generate_document_embedding=_fake_embed)
    app = FastAPI()
    app.include_router(router, prefix="/knowledge")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, conn, fake_queue, pool

    federation_events.set_event_queue(prev_queue)
    await tx.rollback()
    await conn.close()


def _episode_body(n_facts=2, *, group_id=None, source_document=None):
    """Build a POST /knowledge/episodes request body with unique entity names
    so entity resolution always mints fresh entities (no collision with
    committed production rows visible through the rolled-back txn).
    """
    tag = uuid.uuid4().hex[:10]
    facts = []
    for i in range(n_facts):
        facts.append({
            "subject": f"TestSubj_{tag}_{i}",
            "predicate": "TEST_PREDICATE",
            "object": f"TestObj_{tag}_{i}",
            "fact_text": f"Test fact {i} for {tag} — unique sentence {uuid.uuid4()}.",
            "valid_from": "2026-05-13T00:00:00+00:00",
        })
    return {
        "name": f"Test Episode {tag}",
        "content": f"content {tag}",
        "source_description": "unit-test",
        "source_document": source_document or f"test-doc-{tag}.md",
        "group_id": group_id or f"test-{tag}",
        "valid_at": "2026-05-13T00:00:00+00:00",
        "metadata": {"test": True, "tag": tag},
        "facts": facts,
        "create_entities": True,
    }


# ---------------------------------------------------------------------------
# _fact_embedding_discriminator — pure unit, no DB
# ---------------------------------------------------------------------------

class TestEmbeddingDiscriminator:

    def test_3072_dim(self):
        from api.routers.knowledge_router import _fact_embedding_discriminator

        vec = [0.1] * 3072
        col, val = _fact_embedding_discriminator(fact_embedding_3072=vec)
        assert col == "fact_embedding_3072"
        assert val == vec
        assert isinstance(val, list)

    def test_1024_dim(self):
        from api.routers.knowledge_router import _fact_embedding_discriminator

        vec = [0.2] * 1024
        col, val = _fact_embedding_discriminator(fact_embedding=vec)
        assert col == "fact_embedding"
        assert val == vec

    def test_neither(self):
        from api.routers.knowledge_router import _fact_embedding_discriminator

        col, val = _fact_embedding_discriminator()
        assert col is None
        assert val is None

        col, val = _fact_embedding_discriminator(
            fact_embedding_3072=None, fact_embedding=[]
        )
        assert col is None
        assert val is None

    def test_3072_preferred_over_1024(self):
        from api.routers.knowledge_router import _fact_embedding_discriminator

        col, val = _fact_embedding_discriminator(
            fact_embedding_3072=[0.3] * 3072, fact_embedding=[0.4] * 1024
        )
        assert col == "fact_embedding_3072"
        assert len(val) == 3072


# ---------------------------------------------------------------------------
# Emit behaviour
# ---------------------------------------------------------------------------

class TestKnowledgeEpisodeEmit:

    @pytest.mark.anyio
    async def test_emit_does_not_fire_when_flag_off(self, harness, monkeypatch):
        """KOI_FEDERATE_KNOWLEDGE unset → emit is a perfect no-op (zero adds)."""
        monkeypatch.delenv("KOI_FEDERATE_KNOWLEDGE", raising=False)
        client, conn, fake_queue, pool = harness

        resp = await client.post("/knowledge/episodes", json=_episode_body(2))
        assert resp.status_code == 201
        assert len(fake_queue.added) == 0

    @pytest.mark.anyio
    async def test_emit_fires_after_commit(self, harness, monkeypatch):
        """The emit must fire OUTSIDE the `async with pool.acquire()` block.

        The instrumented pool flips `acquire_exited` True in the context
        manager's `__aexit__`; the fake queue snapshots that flag when `.add()`
        is called. If the emit were still inside the block the snapshot would
        be False.
        """
        monkeypatch.setenv("KOI_FEDERATE_KNOWLEDGE", "true")
        client, conn, fake_queue, pool = harness

        resp = await client.post("/knowledge/episodes", json=_episode_body(3))
        assert resp.status_code == 201
        assert len(fake_queue.added) == 1
        assert fake_queue.acquire_exited_at_add is True

        # And the rows it describes are all present on the connection by then.
        ep_id = resp.json()["episode_id"]
        fact_count = await conn.fetchval(
            "SELECT COUNT(*) FROM knowledge_facts WHERE episode_id = $1::uuid",
            ep_id,
        )
        assert fact_count == 3

    @pytest.mark.anyio
    async def test_emit_payload_event_id_present(self, harness, monkeypatch):
        """Gate 2: the emitted event carries `_federation_event_id` in payload
        contents, equal to the queue-level `event_id` — the idempotency chain.
        """
        monkeypatch.setenv("KOI_FEDERATE_KNOWLEDGE", "true")
        client, conn, fake_queue, pool = harness

        resp = await client.post("/knowledge/episodes", json=_episode_body(1))
        assert resp.status_code == 201

        add = fake_queue.added[0]
        queue_event_id = add["event_id"]
        payload = add["contents"]["payload"]
        assert queue_event_id is not None
        # round-trips: queue event_id == payload._federation_event_id
        assert payload["_federation_event_id"] == queue_event_id
        # and it's a real UUID
        uuid.UUID(queue_event_id)
        assert add["contents"]["_koi_domain"] == "knowledge_episode"

    @pytest.mark.anyio
    async def test_emit_payload_roundtrips_through_subscriber(
        self, harness, monkeypatch
    ):
        """THE integration-point test: the publisher payload feeds cleanly
        through the 2b subscriber handler `_apply_knowledge_episode`.

        The POST already wrote the episode + facts; we DELETE them (cascade),
        then replay the captured payload through the subscriber and assert the
        rows are faithfully reconstructed. A publisher/subscriber shape drift
        (e.g. a missing fact `id`) would surface here as a dropped fact.
        """
        monkeypatch.setenv("KOI_FEDERATE_KNOWLEDGE", "true")
        client, conn, fake_queue, pool = harness
        from api.domain_event_handlers import _apply_knowledge_episode

        resp = await client.post("/knowledge/episodes", json=_episode_body(3))
        assert resp.status_code == 201
        ep_id = resp.json()["episode_id"]
        assert resp.json()["facts_created"] == 3

        add = fake_queue.added[0]
        payload = add["contents"]["payload"]
        rid = add["rid"]
        event_type = add["event_type"]
        assert event_type == "NEW"

        # Wipe the locally-written rows so the subscriber applies onto a clean
        # slate — proving the payload alone reconstructs episode + facts.
        await conn.execute(
            "DELETE FROM knowledge_episodes WHERE id = $1::uuid", ep_id
        )
        gone = await conn.fetchval(
            "SELECT COUNT(*) FROM knowledge_facts WHERE episode_id = $1::uuid",
            ep_id,
        )
        assert gone == 0

        await _apply_knowledge_episode(
            conn, rid, event_type, payload, TEST_SOURCE_NODE
        )

        ep = await conn.fetchrow(
            "SELECT name, content, group_id FROM knowledge_episodes "
            "WHERE id = $1::uuid",
            ep_id,
        )
        assert ep is not None
        assert ep["name"] == payload["name"]
        assert ep["content"] == payload["content"]

        facts = await conn.fetch(
            "SELECT id, subject_uri, predicate, fact_text, group_id, "
            "fact_embedding_3072 IS NOT NULL AS has_embed "
            "FROM knowledge_facts WHERE episode_id = $1::uuid",
            ep_id,
        )
        assert len(facts) == 3
        for f in facts:
            assert f["predicate"] == "TEST_PREDICATE"
            assert f["has_embed"] is True
            assert f["group_id"] == payload["group_id"]

        # idempotency row recorded by the subscriber
        applied = await conn.fetchval(
            "SELECT 1 FROM federation_applied_events "
            "WHERE domain = 'knowledge_episode' AND event_id = $1::uuid",
            payload["_federation_event_id"],
        )
        assert applied == 1

    @pytest.mark.anyio
    async def test_emit_event_type_update_on_reused_episode(
        self, harness, monkeypatch
    ):
        """Reusing an episode (same source_document + group_id) emits UPDATE."""
        monkeypatch.setenv("KOI_FEDERATE_KNOWLEDGE", "true")
        client, conn, fake_queue, pool = harness

        body1 = _episode_body(1)
        resp1 = await client.post("/knowledge/episodes", json=body1)
        assert resp1.status_code == 201
        assert fake_queue.added[0]["event_type"] == "NEW"

        # Second write with same source_document + group_id → reuse → UPDATE.
        body2 = _episode_body(1)
        body2["source_document"] = body1["source_document"]
        body2["group_id"] = body1["group_id"]
        resp2 = await client.post("/knowledge/episodes", json=body2)
        assert resp2.status_code == 201
        assert resp2.json()["episode_reused"] is True
        assert fake_queue.added[1]["event_type"] == "UPDATE"
        assert resp2.json()["episode_id"] == resp1.json()["episode_id"]

    @pytest.mark.anyio
    async def test_existing_episode_endpoint_unchanged(self, harness, monkeypatch):
        """REGRESSION: with the flag off, POST /knowledge/episodes still returns
        the same response shape and still commits episode + facts.
        """
        monkeypatch.delenv("KOI_FEDERATE_KNOWLEDGE", raising=False)
        client, conn, fake_queue, pool = harness

        resp = await client.post("/knowledge/episodes", json=_episode_body(2))
        assert resp.status_code == 201
        data = resp.json()

        # Response shape unchanged — every EpisodeCreateResponse field present.
        for key in (
            "episode_id", "episode_reused", "facts_created", "facts_skipped",
            "facts_superseded", "entities_resolved", "entities_created",
            "facts_null_embed",
        ):
            assert key in data, f"missing response key: {key}"

        assert data["episode_reused"] is False
        assert data["facts_created"] == 2
        assert data["facts_null_embed"] == 0

        # Episode + facts were actually written.
        ep_count = await conn.fetchval(
            "SELECT COUNT(*) FROM knowledge_episodes WHERE id = $1::uuid",
            data["episode_id"],
        )
        assert ep_count == 1
        fact_count = await conn.fetchval(
            "SELECT COUNT(*) FROM knowledge_facts WHERE episode_id = $1::uuid",
            data["episode_id"],
        )
        assert fact_count == 2

        # Flag off → no emit.
        assert len(fake_queue.added) == 0
