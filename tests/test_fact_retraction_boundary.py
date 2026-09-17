"""Issue #67 — the retraction/federation boundary, reproduced before it is changed.

Two kinds of test live here, deliberately in one file so the boundary reads as
one thing:

PINS — behaviour that is true today and stays true after #67. Each is a fact
the design leans on, and each was read out of the source and then PROVED here
rather than assumed:

  * `EventQueue.confirm()` appends to `confirmed_by` and touches nothing else.
    It is receipt. It cannot be reinterpreted as application.
  * `EventQueue.poll()` appends `delivered_to` for events it EXCLUDED by edge
    scope as well as for events it handed over. `delivered_to` is therefore
    not evidence of transmission (memory `feedback_delivered_to_is_not_
    transmission`, 2026-09-07 — and the incident handoff's "delivery 4/4" is
    that mistake recurring: only one edge lists `knowledge_episode`).
  * An event that was polled but never confirmed is NOT redelivered. The
    comment in `koi_poller._poll_peer` ("Don't confirm — will re-deliver on
    next poll") and the `FederationDeferred` docstring both say otherwise, and
    both are false: `poll()` marks `delivered_to` at hand-over, and the WHERE
    clause excludes on that column, not on `confirmed_by`.
  * `emit_domain_event` is best-effort: a queue failure is logged and
    swallowed. A retraction cannot ride on it and claim durability.

REQUIREMENTS — behaviour #67 needs that does not hold at the merge-base
`0c39fa1`. Written first, run red, then implemented. Each names the exact
assertion that fails today.

  * `EventQueue.add()` accepts a caller-supplied connection, so the outbox row
    joins the caller's transaction (today it always acquires its own).
  * Applying a NEW/UPDATE carrying `valid_to = NULL` after a tombstone must
    not clear the tombstone (today `_insert_fact`'s upsert does
    `valid_to = EXCLUDED.valid_to`, which resurrects).
  * `POST /knowledge/facts/{id}/retract` must queue one durable, unicast
    `knowledge_fact` event per authorized recipient, in the SAME transaction as
    the `valid_to` write, carrying the committed `valid_to` byte-for-byte
    (today it queues nothing).

Isolation: one asyncpg connection to POSTGRES_URL (tests/conftest.py pins it
to personal_koi_test), one transaction, rolled back at teardown. The fixture
refuses to run against `personal_koi`.
"""

from __future__ import annotations

import inspect
import uuid

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import api.federation_events as federation_events
from api.domain_event_handlers import apply_domain_event
from api.event_queue import EventQueue

from tests.fact_retraction_testkit import (
    NODE_A, PEER_AUTH, PEER_NARROW, PEER_REVOKED, SVC_TOKEN, SVC_AUTH,
    SingleConnPool as _SingleConnPool,
    apply_ledger_migration, close_scratch_conn, open_scratch_conn,
    seed_edges as _seed_edges, seed_fact as _seed_fact,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def conn():
    c, tx = await open_scratch_conn()
    yield c
    await close_scratch_conn(c, tx)


@pytest.fixture
def pool(conn):
    return _SingleConnPool(conn)


# ═══════════════════════════════════════════════════════════════════════════
# PINS — true today, still true after #67
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_pin_confirm_is_receipt_only(conn, pool):
    """confirm() appends the confirming node to confirmed_by and nothing else.

    Nothing about application is recorded anywhere by this call — there is no
    column for it. This is why `confirmed_by` must not be read as "applied".
    """
    eq = EventQueue(pool, NODE_A)
    ev = await eq.add(event_type="UPDATE", rid="orn:personal-koi.knowledge-fact:x",
                      contents={"_koi_domain": "knowledge_fact", "payload": {}},
                      target_node=PEER_AUTH, event_id=str(uuid.uuid4()))
    before = await conn.fetchrow(
        "SELECT * FROM koi_net_events WHERE event_id = $1::uuid", ev)
    n = await eq.confirm([ev], PEER_AUTH)
    assert n == 1
    after = await conn.fetchrow(
        "SELECT * FROM koi_net_events WHERE event_id = $1::uuid", ev)
    assert after["confirmed_by"] == [PEER_AUTH]
    changed = {k for k in before.keys() if before[k] != after[k]}
    assert changed == {"confirmed_by"}, f"confirm() changed more than confirmed_by: {changed}"


@pytest.mark.anyio
async def test_pin_delivered_to_marks_scope_excluded_events(conn, pool):
    """poll() with a narrow scope marks an out-of-scope event delivered_to WITHOUT returning it.

    So `delivered_to` ∋ peer does not mean the peer ever saw the event. The
    incident handoff's "3 UPDATEs delivered to the same 4 peers" is exactly
    this column read as transmission.
    """
    eq = EventQueue(pool, NODE_A)
    ev = await eq.add(event_type="UPDATE", rid="orn:personal-koi.knowledge-fact:y",
                      contents={"_koi_domain": "knowledge_fact", "payload": {}},
                      event_id=str(uuid.uuid4()))  # broadcast, like every domain emit today
    got = await eq.poll(PEER_NARROW, rid_types=["Person", "Concept"])
    assert [e for e in got if e["event_id"] == ev] == [], "event was in scope?"
    row = await conn.fetchrow(
        "SELECT delivered_to FROM koi_net_events WHERE event_id = $1::uuid", ev)
    assert PEER_NARROW in row["delivered_to"], (
        "poll() no longer marks excluded events delivered — the starvation fix "
        "was removed; re-read event_queue.py before trusting this pin")


@pytest.mark.anyio
async def test_pin_unconfirmed_events_are_not_redelivered(conn, pool):
    """A polled-but-unconfirmed event does not come back on the next poll.

    koi_poller._poll_peer says "Don't confirm — will re-deliver on next poll".
    It will not: poll() excludes on delivered_to, which it set at hand-over.
    """
    eq = EventQueue(pool, NODE_A)
    ev = await eq.add(event_type="UPDATE", rid="orn:personal-koi.knowledge-fact:z",
                      contents={"_koi_domain": "knowledge_fact", "payload": {}},
                      target_node=PEER_AUTH, event_id=str(uuid.uuid4()))
    first = await eq.poll(PEER_AUTH)
    assert [e["event_id"] for e in first] == [ev]
    # deliberately NOT confirmed
    second = await eq.poll(PEER_AUTH)
    assert second == [], (
        "unconfirmed event was redelivered — the poller's comment became true; "
        "the retraction retry design assumes it is false")
    row = await conn.fetchrow(
        "SELECT delivered_to, confirmed_by FROM koi_net_events WHERE event_id = $1::uuid", ev)
    assert row["delivered_to"] == [PEER_AUTH] and row["confirmed_by"] == []


@pytest.mark.anyio
async def test_pin_emit_domain_event_swallows_queue_failure(monkeypatch):
    """emit_domain_event returns None on a failing queue. It never raises.

    Correct for the fire-and-forget domains it was written for; disqualifying
    for a retraction, whose whole point is that the intent is durable.
    """
    class _Boom:
        async def add(self, **kw):
            raise RuntimeError("queue down")

    monkeypatch.setenv("KOI_FEDERATE_KNOWLEDGE", "true")
    prev = federation_events._event_queue
    federation_events.set_event_queue(_Boom())
    try:
        result = await federation_events.emit_domain_event(
            "knowledge_fact", "UPDATE", "orn:personal-koi.knowledge-fact:q", {"id": "q"},
            payload_event_id=str(uuid.uuid4()))
    finally:
        federation_events.set_event_queue(prev)
    assert result is None


# (A fifth "pin" — that create_episode's emit runs after COMMIT — was a
# comment-ordering tautology and was removed after the 2026-09-16 review. The
# real proof is tests/unit/test_knowledge_router.py::test_emit_fires_after_commit,
# which instruments the pool and asserts the emit runs outside the acquire block.)


# ═══════════════════════════════════════════════════════════════════════════
# REQUIREMENTS — red at 0c39fa1, green after #67
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_req_event_queue_add_accepts_caller_connection(conn, pool):
    """add(conn=...) exists and writes inside the caller's savepoint.

    The signature assertion is what fails at the merge-base. The savepoint
    half is a WEAK proof under this file's single-connection pool (the pool
    path is the same connection); the strong proof — a pool that hands out a
    different real connection — is
    tests/test_fact_retraction_outbox.py::test_add_with_conn_writes_on_the_caller_connection_not_the_pool,
    which fails when add() ignores `conn` (revert-proven 2026-09-15).
    """
    eq = EventQueue(pool, NODE_A)
    sig = inspect.signature(eq.add)
    assert "conn" in sig.parameters, "EventQueue.add has no caller-supplied connection parameter"

    await conn.execute("SAVEPOINT sp_add")
    ev = await eq.add(event_type="UPDATE", rid="orn:personal-koi.knowledge-fact:c",
                      contents={"_koi_domain": "knowledge_fact", "payload": {}},
                      target_node=PEER_AUTH, event_id=str(uuid.uuid4()), conn=conn)
    assert await conn.fetchval(
        "SELECT 1 FROM koi_net_events WHERE event_id = $1::uuid", ev) == 1
    await conn.execute("ROLLBACK TO SAVEPOINT sp_add")
    assert await conn.fetchval(
        "SELECT 1 FROM koi_net_events WHERE event_id = $1::uuid", ev) is None, (
        "row survived the savepoint rollback — add() used a different connection")


@pytest.mark.anyio
async def test_req_new_after_tombstone_does_not_resurrect(conn, monkeypatch):
    """UPDATE-before-NEW: a stale NEW with valid_to=NULL must not clear a tombstone.

    Today `_insert_fact` upserts `valid_to = EXCLUDED.valid_to`, so the second
    apply below sets the tombstoned fact live again.
    """
    monkeypatch.setenv("KOI_FEDERATE_KNOWLEDGE", "true")
    ep_id, fact_id = await _seed_fact(conn, valid_to=None)
    # Tombstone it locally (what the recipient of a retraction has done).
    await conn.execute(
        "UPDATE knowledge_facts SET valid_to = '2026-09-14T15:13:07.924484+00:00' WHERE id = $1::uuid",
        fact_id)
    stale_new = {
        "id": ep_id, "name": "boundary episode", "content": "c",
        "source_description": "boundary", "source_document": "boundary.md",
        "group_id": "personal", "valid_at": "2026-09-14T00:00:00Z",
        "created_at": "2026-09-14T00:00:00Z", "metadata": {},
        "facts": [{
            "id": fact_id, "subject_uri": "orn:entity:s", "predicate": "TEST_PRED",
            "object_uri": "orn:entity:o", "object_literal": None, "fact_text": "boundary fact",
            "valid_from": "2026-09-14T00:00:00Z", "valid_to": None,
            "created_at": "2026-09-14T00:00:00Z", "group_id": "personal",
            "source_node_rid": "session:boundary", "turn_range_start": None,
            "turn_range_end": None, "embedding_column": None, "embedding_value": None,
        }],
        "_federation_event_id": str(uuid.uuid4()),
    }
    await apply_domain_event(
        conn, "knowledge_episode", f"orn:personal-koi.knowledge-episode:{ep_id}",
        "NEW", stale_new, NODE_A)
    vt = await conn.fetchval("SELECT valid_to FROM knowledge_facts WHERE id = $1::uuid", fact_id)
    assert vt is not None, "a stale NEW resurrected a tombstoned fact"
    assert vt.isoformat() == "2026-09-14T15:13:07.924484+00:00"


@pytest.fixture
async def retract_client(conn, pool, monkeypatch):
    """The knowledge router mounted in-process with a REAL EventQueue on `conn`."""
    from api.routers.knowledge_router import create_router

    monkeypatch.setenv("KOI_CLAIMS_SERVICE_TOKEN", SVC_TOKEN)
    monkeypatch.setenv("KOI_FEDERATE_KNOWLEDGE", "true")
    await apply_ledger_migration(conn)
    eq = EventQueue(pool, NODE_A)
    prev = federation_events._event_queue
    federation_events.set_event_queue(eq)
    router = create_router(pool)
    app = FastAPI()
    app.include_router(router, prefix="/knowledge")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers=SVC_AUTH) as client:
        yield client
    federation_events.set_event_queue(prev)


@pytest.mark.anyio
async def test_req_retract_queues_one_unicast_event_per_authorized_recipient(
    conn, retract_client,
):
    """The load-bearing #67 requirement, end to end at the endpoint.

    After a 200 with retracted=true there must be exactly one koi_net_events
    row per AUTHORIZED recipient — unicast (target_node set), domain
    knowledge_fact, carrying the committed valid_to byte-for-byte — and none
    for the narrow-scope or revoked peers.
    """
    await _seed_edges(conn)
    ep_id, fact_id = await _seed_fact(conn)

    resp = await retract_client.post(f"/knowledge/facts/{fact_id}/retract",
                                     json={"reason": "boundary test"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["retracted"] is True

    committed = await conn.fetchval(
        "SELECT valid_to FROM knowledge_facts WHERE id = $1::uuid", fact_id)
    assert committed is not None

    rows = await conn.fetch(
        """
        SELECT target_node, event_type, contents FROM koi_net_events
        WHERE rid = $1 AND contents->>'_koi_domain' = 'knowledge_fact'
        """,
        f"orn:personal-koi.knowledge-fact:{fact_id}",
    )
    targets = sorted(r["target_node"] for r in rows)
    assert targets == [PEER_AUTH], (
        f"expected exactly one unicast event, to the authorized peer; got targets={targets}")
    payload = rows[0]["contents"]
    if isinstance(payload, str):
        import json
        payload = json.loads(payload)
    payload = payload["payload"]
    assert payload["id"] == fact_id
    assert payload["valid_to"] == committed.isoformat(), (
        "the event does not carry the committed valid_to byte-for-byte")
    assert rows[0]["event_type"] == "UPDATE"
