"""Issue #67 — a fact retraction, end to end through the real transport.

tests/test_fact_retraction_apply.py proves the recipient handler in isolation.
This file proves the WIRING: a retraction recorded on node A by
`fact_retraction.retract_fact_transactional` is queued as one unicast event,
handed over by the REAL `EventQueue.poll` under the recipient edge's scope,
dispatched by the REAL `KOIPoller._process_event`, applied by the real handler,
and its application report — carried back in the confirm payload in production
— moves the publisher's delivery ledger to `applied`.

Harness (copied from tests/integration/test_federation_knowledge.py):
  - One scratch database (tests/conftest.py pins POSTGRES_URL to
    personal_koi_test; the fixture refuses `personal_koi`), one connection, one
    transaction rolled back at teardown. `open_scratch_conn` pre-scopes the
    queue so the real poll() only ever surfaces rows this test queued.
  - "Node A" (publisher) and "PEER_AUTH" (recipient) are distinct koi-net RIDs
    with a seeded APPROVED edge whose scope admits `knowledge_fact`.
  - The only thing skipped vs. production is `_poll_peer`'s httpx shell and
    envelope signing: poll → dispatch → confirm are exercised at the function
    level. (The httpx shell itself is covered, with httpx faked, in
    tests/test_fact_retraction_apply.py::test_poll_peer_carries_the_report_in_the_confirm_payload.)

ONE DATABASE, TWO ROLES — read this before trusting an assertion here.
The sibling file's footprints are disjoint (emit writes koi_net_events, apply
writes knowledge_*), so a shared database cannot let A's state pose as B's.
A retraction's footprints are NOT disjoint: the publisher's transaction writes
`knowledge_facts.valid_to` and a `knowledge_fact_retractions` row keyed
(fact_id, valid_to, origin_node=A), and the recipient's handler writes the
same column on the same physical row and a ledger row with the same key
(origin_node is the authenticated sender, which is A). In production those are
two databases. Here they are one row each, so each scenario states explicitly
how it models the recipient's pre-state:

  * Scenario 1 clears `valid_to` on the shared fact row after the publisher's
    transaction ("B's copy is live"), so the recipient path exercised is the
    headline `applied`, and "recipient applied" is asserted on that same row.
    The ledger row is shared; its `applied_at` was already set by the
    publisher, so the recipient's ledger write is NOT independently proved
    here — tests/test_fact_retraction_apply.py proves it.
  * Scenario 2 deletes the publisher's fact, episode and ledger rows after
    the event is queued ("B never had the episode") — the deliveries row goes
    with the ledger row (ON DELETE CASCADE), so scenario 2 makes no
    publisher-side assertion.
  * Scenario 3 dispatches the same polled event twice; no nudge needed.
"""

from __future__ import annotations

import json
import uuid

import pytest

from api import fact_retraction
from api.event_queue import EventQueue
from api.koi_poller import KOIPoller

from tests.fact_retraction_testkit import (
    NODE_A, PEER_AUTH, PEER_NARROW,
    SingleConnPool,
    apply_ledger_migration, close_scratch_conn, open_scratch_conn,
    episode_payload, fact_payload, seed_edges, seed_fact,
)

# The scope `seed_edges` gives NODE_A → PEER_AUTH; what `events_poll` would pass
# through verbatim from the edge row.
AUTH_SCOPE = ["Person", "knowledge_episode", "knowledge_fact"]
FACT_RID = "orn:personal-koi.knowledge-fact:{}"
EP_RID = "orn:personal-koi.knowledge-episode:{}"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def conn(monkeypatch):
    monkeypatch.setenv("KOI_FEDERATE_KNOWLEDGE", "true")
    c, tx = await open_scratch_conn()
    await apply_ledger_migration(c)
    await seed_edges(c)
    yield c
    await close_scratch_conn(c, tx)


@pytest.fixture
def pool(conn):
    return SingleConnPool(conn)


@pytest.fixture
def queue_a(pool):
    """Node A's queue, with the delivery observer the service installs at startup."""
    eq = EventQueue(pool, NODE_A)
    eq.delivery_observer = fact_retraction.make_delivery_observer()
    return eq


def _make_poller(pool, node_rid):
    """A real KOIPoller with just enough wired to run _process_event."""
    poller = KOIPoller.__new__(KOIPoller)
    poller.pool = pool
    poller.node_rid = node_rid
    poller.vault_sync = None
    return poller


async def _dispatch(poller, ev):
    """Feed one polled event dict through the real poller dispatch path; return its report."""
    return await poller._process_event(
        rid=ev["rid"],
        event_type=ev["event_type"],
        contents=ev["contents"],
        manifest=ev["manifest"],
        source_node=ev["source_node"],
        event_id=ev["event_id"],
    )


async def _retract_on_a(conn, queue_a, fact_id):
    """The publisher's transaction, exactly as POST /knowledge/facts/{id}/retract runs it."""
    async with conn.transaction():
        return await fact_retraction.retract_fact_transactional(
            conn, fact_id=uuid.UUID(fact_id), event_queue=queue_a,
            federation_enabled=True, reason="integration", retracted_by="service:test")


async def _poll_as_recipient(queue_a, fact_id):
    """PEER_AUTH polls A with its edge's scope; returns the retraction events for `fact_id`."""
    events = await queue_a.poll(PEER_AUTH, rid_types=AUTH_SCOPE)
    return [e for e in events
            if e["source_node"] == NODE_A and e["rid"] == FACT_RID.format(fact_id)]


async def _delivery(conn, event_id):
    row = await conn.fetchrow(
        "SELECT state, state_reason, application, target_node FROM "
        "knowledge_fact_retraction_deliveries WHERE event_id = $1::uuid", event_id)
    assert row is not None, f"no deliveries row for event {event_id}"
    app = row["application"]
    if isinstance(app, str):
        app = json.loads(app)
    return row["state"], row["state_reason"], app, row["target_node"]


async def _simulate_confirm(conn, recipient, event_id, report):
    """What /koi-net/events/confirm does with the recipient's confirm payload."""
    await fact_retraction.record_receipts(conn, recipient, [event_id])
    return await fact_retraction.record_applications(
        conn, recipient, [{**report, "node": recipient}])


# ---------------------------------------------------------------------------
# Scenario 1 — retract on A, poll, apply, confirm: the ledger reaches `applied`
# ---------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.federation
async def test_retraction_federates_and_ledger_reaches_applied(conn, pool, queue_a):
    ep_id, fact_id = await seed_fact(conn, valid_to=None)

    result = await _retract_on_a(conn, queue_a, fact_id)
    assert result.retracted is True
    committed = await conn.fetchval(
        "SELECT valid_to FROM knowledge_facts WHERE id = $1::uuid", fact_id)
    assert committed is not None and result.valid_to == committed.isoformat()

    # Exactly one unicast event, to the one peer whose edge admits knowledge_fact.
    rows = await conn.fetch(
        "SELECT event_id::text AS event_id, target_node FROM koi_net_events "
        "WHERE rid = $1 AND contents->>'_koi_domain' = 'knowledge_fact'",
        FACT_RID.format(fact_id))
    assert [r["target_node"] for r in rows] == [PEER_AUTH]
    event_id = rows[0]["event_id"]
    assert [d["target_node"] for d in result.deliveries] == [PEER_AUTH]
    assert (await _delivery(conn, event_id))[0] == "queued"

    # The narrow peer polls first and must not see it (unicast + scope).
    assert [e for e in await queue_a.poll(PEER_NARROW, rid_types=["Person", "Concept"])
            if e["rid"] == FACT_RID.format(fact_id)] == []

    # The real poll hands it over under the edge's scope; the observer records that.
    events = await _poll_as_recipient(queue_a, fact_id)
    assert len(events) == 1 and events[0]["event_id"] == event_id
    ev = events[0]
    assert ev["event_type"] == "UPDATE"
    assert ev["contents"]["payload"]["valid_to"] == committed.isoformat()
    assert (await _delivery(conn, event_id))[0] == "delivered"

    # Shared-database nudge (module docstring): B's copy of the fact is live.
    await conn.execute(
        "UPDATE knowledge_facts SET valid_to = NULL WHERE id = $1::uuid", fact_id)

    # The real poller dispatch applies it and returns the report.
    report = await _dispatch(_make_poller(pool, PEER_AUTH), ev)
    assert report["application"] is True and report["status"] == "applied"
    assert report["event_id"] == event_id and report["fact_id"] == fact_id
    assert report["valid_to"] == committed.isoformat()
    landed = await conn.fetchval(
        "SELECT valid_to FROM knowledge_facts WHERE id = $1::uuid", fact_id)
    assert landed == committed, "the recipient did not land the committed valid_to byte-for-byte"
    assert await conn.fetchval(
        "SELECT 1 FROM federation_applied_events WHERE domain = 'knowledge_fact' "
        "AND event_id = $1::uuid", event_id) == 1

    # The confirm the publisher receives: receipt, then the application report.
    assert await fact_retraction.record_receipts(conn, PEER_AUTH, [event_id]) == 1
    assert (await _delivery(conn, event_id))[0] == "received"
    summary = await fact_retraction.record_applications(
        conn, PEER_AUTH, [{**report, "node": PEER_AUTH}])
    assert summary["applied"] == 1 and summary["rejected"] == 0

    state, why, app, target = await _delivery(conn, event_id)
    assert (state, why, target) == ("applied", "peer_reported_applied", PEER_AUTH)
    assert app["status"] == "applied" and app["node"] == PEER_AUTH
    assert app["event_id"] == event_id and app["valid_to"] == committed.isoformat()

    status = await fact_retraction.tombstone_status(conn, uuid.UUID(fact_id))
    assert status["tombstoned"] is True and status["valid_to"] == committed.isoformat()
    assert status["pending_tombstone"] is False
    assert [(d["target_node"], d["state"]) for d in status["deliveries"]] == [(PEER_AUTH, "applied")]


# ---------------------------------------------------------------------------
# Scenario 2 — UPDATE-before-NEW, end to end
# ---------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.federation
async def test_retraction_before_episode_new_lands_tombstoned(conn, pool, queue_a):
    ep_id, fact_id = await seed_fact(conn, valid_to=None)
    await _retract_on_a(conn, queue_a, fact_id)
    events = await _poll_as_recipient(queue_a, fact_id)
    assert len(events) == 1
    retraction_ev = events[0]
    committed_iso = retraction_ev["contents"]["payload"]["valid_to"]

    # Shared-database nudge (module docstring): B never had the episode, the
    # fact, or a ledger row for it. The publisher's deliveries row cascades
    # away with the ledger row, so nothing publisher-side is asserted below.
    await conn.execute("DELETE FROM knowledge_facts WHERE id = $1::uuid", fact_id)
    await conn.execute("DELETE FROM knowledge_episodes WHERE id = $1::uuid", ep_id)
    await conn.execute("DELETE FROM knowledge_fact_retractions WHERE fact_id = $1::uuid", fact_id)

    poller = _make_poller(pool, PEER_AUTH)

    # 1. The retraction arrives first: pending, no fact row minted.
    report = await _dispatch(poller, retraction_ev)
    assert report["status"] == "pending"
    assert await conn.fetchval(
        "SELECT 1 FROM knowledge_facts WHERE id = $1::uuid", fact_id) is None
    pending = await conn.fetchrow(
        "SELECT valid_to, applied_at, origin_node FROM knowledge_fact_retractions "
        "WHERE fact_id = $1::uuid", fact_id)
    assert pending["applied_at"] is None and pending["origin_node"] == NODE_A
    assert pending["valid_to"].isoformat() == committed_iso

    # 2. The episode NEW arrives second, carrying the fact live (valid_to NULL),
    #    through the same dispatch path.
    new_ev = {
        "event_id": str(uuid.uuid4()), "event_type": "NEW", "rid": EP_RID.format(ep_id),
        "manifest": None, "source_node": NODE_A, "queued_at": None,
        "contents": {"_koi_domain": "knowledge_episode",
                     "payload": episode_payload(ep_id, [fact_payload(fact_id, ep_id, valid_to=None)],
                                                event_id=str(uuid.uuid4()))},
    }
    assert await _dispatch(poller, new_ev) is None

    landed = await conn.fetchval(
        "SELECT valid_to FROM knowledge_facts WHERE id = $1::uuid", fact_id)
    assert landed is not None, "the late NEW landed the fact LIVE despite the pending tombstone"
    assert landed.isoformat() == committed_iso
    assert await conn.fetchval(
        "SELECT applied_at FROM knowledge_fact_retractions WHERE fact_id = $1::uuid",
        fact_id) is not None
    status = await fact_retraction.tombstone_status(conn, uuid.UUID(fact_id))
    assert status["tombstoned"] is True and status["pending_tombstone"] is False


# ---------------------------------------------------------------------------
# Scenario 3 — the same retraction event dispatched twice is idempotent
# ---------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.federation
async def test_duplicate_retraction_dispatch_is_idempotent(conn, pool, queue_a):
    ep_id, fact_id = await seed_fact(conn, valid_to=None)
    await _retract_on_a(conn, queue_a, fact_id)
    events = await _poll_as_recipient(queue_a, fact_id)
    assert len(events) == 1
    ev = events[0]
    event_id = ev["event_id"]
    poller = _make_poller(pool, PEER_AUTH)

    first = await _dispatch(poller, ev)
    # The shared row is already tombstoned by the publisher's own write, so the
    # first dispatch is itself the already_tombstoned path — and it must agree.
    assert first["status"] == "already_tombstoned" and first["valid_to_matches"] is True
    before = await conn.fetchrow("SELECT * FROM knowledge_facts WHERE id = $1::uuid", fact_id)
    ledger_before = await conn.fetch(
        "SELECT id, applied_at FROM knowledge_fact_retractions WHERE fact_id = $1::uuid ORDER BY id",
        fact_id)

    second = await _dispatch(poller, ev)

    assert second["status"] == "already_tombstoned" and second["valid_to_matches"] is True
    assert second["valid_to"] == first["valid_to"] == ev["contents"]["payload"]["valid_to"]
    after = await conn.fetchrow("SELECT * FROM knowledge_facts WHERE id = $1::uuid", fact_id)
    assert dict(after) == dict(before), "a duplicate dispatch changed the fact row"
    ledger_after = await conn.fetch(
        "SELECT id, applied_at FROM knowledge_fact_retractions WHERE fact_id = $1::uuid ORDER BY id",
        fact_id)
    assert [dict(r) for r in ledger_after] == [dict(r) for r in ledger_before]
    assert len(ledger_after) == 1
    assert await conn.fetchval(
        "SELECT COUNT(*) FROM federation_applied_events WHERE domain = 'knowledge_fact' "
        "AND event_id = $1::uuid", event_id) == 1

    # Both reports, confirmed in order, leave the publisher at `applied` once:
    # the second is `already_terminal`, not a second transition.
    await fact_retraction.record_receipts(conn, PEER_AUTH, [event_id])
    s1 = await fact_retraction.record_applications(conn, PEER_AUTH, [{**first, "node": PEER_AUTH}])
    s2 = await fact_retraction.record_applications(conn, PEER_AUTH, [{**second, "node": PEER_AUTH}])
    assert s1["applied"] == 1 and s2["already_terminal"] == 1
    state, why, _, _ = await _delivery(conn, event_id)
    assert (state, why) == ("applied", "peer_already_tombstoned")
