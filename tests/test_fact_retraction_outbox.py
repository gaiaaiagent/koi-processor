"""Issue #67 — the PUBLISHER side: transactional retraction, recipient scope,
the delivery ledger and its state transitions, retry, and the lookup surfaces.

Every test runs on one asyncpg connection to POSTGRES_URL (conftest pins it to
personal_koi_test) inside a transaction rolled back at teardown; migration 127's
body is applied inside that transaction. No test talks to :8351 or a peer.

Sections
  1. Atomicity      — a queue failure mid-fan-out rolls back valid_to too;
                      a missing ledger refuses before any write.
  2. Recipients     — scope_admits is pinned against a REAL poll (positive
                      control), narrow/revoked peers get nothing, a confirmed
                      original recipient that lost scope is recorded
                      `unauthorized` with no event.
  3. Payload        — valid_to byte-for-byte; original_event_ids carried.
  4. Ledger states  — delivered (observer), received (confirm), applied /
                      rejected (applications), wrong-node reports ignored,
                      terminal states not overwritten.
  5. Retry          — plan/apply/sweep: retry with a fresh event, exhaustion →
                      failed, receipt-without-report → unverifiable, revoked
                      edge → failed, live event → wait.
  6. Surfaces       — GET /knowledge/facts/{id}/tombstone (service token),
                      ordinary entity-facts read hides the fact, POST
                      /koi-net/facts/lookup (signed, edge-scoped), and the
                      confirm endpoint recording `applications`.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import api.federation_events as federation_events
import api.koi_net_router as knr
from api import fact_retraction as fr
from api.event_queue import EventQueue
from api.koi_envelope import public_key_to_der_b64, sign_envelope
from api.node_identity import generate_keypair

from tests.fact_retraction_testkit import (
    NODE_A, PEER_AUTH, PEER_NARROW, PEER_REVOKED, SVC_TOKEN, SVC_AUTH,
    SingleConnPool, TwoConnPool, apply_ledger_migration, close_scratch_conn,
    open_scratch_conn, open_second_scratch_conn,
    seed_edge, seed_edges, seed_fact, episode_payload, fact_payload,
)

FACT_RID = fr.fact_rid


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def conn():
    c, tx = await open_scratch_conn()
    await apply_ledger_migration(c)
    yield c
    await close_scratch_conn(c, tx)


@pytest.fixture
def pool(conn):
    return SingleConnPool(conn)


@pytest.fixture
def eq(pool):
    q = EventQueue(pool, NODE_A)
    q.delivery_observer = fr.make_delivery_observer()
    return q


async def _retract(conn, eq, fact_id, *, enabled=True, reason="t", by="service:test"):
    async with conn.transaction():
        return await fr.retract_fact_transactional(
            conn, fact_id=uuid.UUID(fact_id), event_queue=eq,
            federation_enabled=enabled, reason=reason, retracted_by=by)


async def _events_for(conn, fact_id):
    rows = await conn.fetch(
        "SELECT event_id::TEXT AS event_id, target_node, event_type, contents, expires_at "
        "FROM koi_net_events WHERE rid = $1 ORDER BY queued_at", FACT_RID(fact_id))
    out = []
    for r in rows:
        c = r["contents"]
        if isinstance(c, str):
            c = json.loads(c)
        out.append({**dict(r), "contents": c})
    return out


async def _deliveries(conn, fact_id):
    return await conn.fetch(
        """
        SELECT d.* , d.event_id::TEXT AS event_id_text
        FROM knowledge_fact_retraction_deliveries d
        JOIN knowledge_fact_retractions r ON r.id = d.retraction_id
        WHERE r.fact_id = $1::uuid ORDER BY d.target_node
        """, fact_id)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Atomicity
# ═══════════════════════════════════════════════════════════════════════════

class _FailingQueue(EventQueue):
    """add(conn=...) succeeds `ok_adds` times, then raises."""

    def __init__(self, pool, node_rid, ok_adds):
        super().__init__(pool, node_rid)
        self.ok_adds = ok_adds
        self.calls = 0

    async def add(self, *a, **kw):
        self.calls += 1
        if self.calls > self.ok_adds:
            raise RuntimeError("simulated queue outage")
        return await super().add(*a, **kw)


@pytest.fixture
async def other_conn():
    c, tx = await open_second_scratch_conn()
    yield c
    await close_scratch_conn(c, tx)


@pytest.mark.anyio
async def test_add_with_conn_writes_on_the_caller_connection_not_the_pool(conn, other_conn):
    """The proof SingleConnPool cannot give: the pool hands out a DIFFERENT
    connection, and the row appears only where the write actually ran."""
    q = EventQueue(TwoConnPool(conn, other_conn), NODE_A)
    via_conn = str(uuid.uuid4())
    via_pool = str(uuid.uuid4())
    await q.add(event_type="UPDATE", rid=FACT_RID("c"), contents={"_koi_domain": "knowledge_fact", "payload": {}},
                target_node=PEER_AUTH, event_id=via_conn, conn=conn)
    await q.add(event_type="UPDATE", rid=FACT_RID("p"), contents={"_koi_domain": "knowledge_fact", "payload": {}},
                target_node=PEER_AUTH, event_id=via_pool)
    seen = "SELECT 1 FROM koi_net_events WHERE event_id = $1::uuid"
    assert await conn.fetchval(seen, via_conn) == 1 and await other_conn.fetchval(seen, via_conn) is None
    assert await other_conn.fetchval(seen, via_pool) == 1 and await conn.fetchval(seen, via_pool) is None


@pytest.mark.anyio
async def test_queue_failure_mid_fanout_rolls_back_the_retraction(conn, other_conn):
    """Two authorized peers; the second add() raises → NOTHING is committed.

    valid_to stays NULL, no ledger rows, no event rows — on the caller's
    connection AND on the pool's other connection. The second assertion is
    the one that matters: had the queue written through the pool instead of
    `conn=`, the first peer's event would have survived the rollback there.
    """
    await seed_edges(conn)
    await seed_edge(conn, NODE_A, "orn:koi-net.node:second-auth+c1", "APPROVED", ["knowledge_fact"])
    ep, fid = await seed_fact(conn)
    q = _FailingQueue(TwoConnPool(conn, other_conn), NODE_A, ok_adds=1)

    with pytest.raises(RuntimeError, match="simulated queue outage"):
        async with conn.transaction():
            await fr.retract_fact_transactional(
                conn, fact_id=uuid.UUID(fid), event_queue=q, federation_enabled=True,
                reason="atomicity", retracted_by="service:test")
    # asyncpg's nested transaction() is a savepoint that rolled back on raise.
    assert q.calls == 2
    assert await conn.fetchval("SELECT valid_to FROM knowledge_facts WHERE id = $1::uuid", fid) is None
    assert await conn.fetchval(
        "SELECT count(*) FROM knowledge_fact_retractions WHERE fact_id = $1::uuid", fid) == 0
    assert await _events_for(conn, fid) == []
    assert await other_conn.fetchval(
        "SELECT count(*) FROM koi_net_events WHERE rid = $1", FACT_RID(fid)) == 0, (
        "an event leaked through the pool connection and survived the rollback")


@pytest.mark.anyio
async def test_missing_ledger_refuses_before_any_write(pool):
    """Without migration 127 the call raises LedgerUnavailable and valid_to is untouched."""
    c, tx = await open_scratch_conn()       # NO apply_ledger_migration here
    try:
        ep, fid = await seed_fact(c)
        q = EventQueue(SingleConnPool(c), NODE_A)
        with pytest.raises(fr.LedgerUnavailable):
            async with c.transaction():
                await fr.retract_fact_transactional(
                    c, fact_id=uuid.UUID(fid), event_queue=q, federation_enabled=True,
                    reason=None, retracted_by=None)
        assert await c.fetchval("SELECT valid_to FROM knowledge_facts WHERE id = $1::uuid", fid) is None
    finally:
        await close_scratch_conn(c, tx)


@pytest.mark.anyio
async def test_second_retract_is_a_noop_with_no_new_obligations(conn, eq):
    await seed_edges(conn)
    ep, fid = await seed_fact(conn)
    first = await _retract(conn, eq, fid)
    second = await _retract(conn, eq, fid)
    assert first.retracted and not first.already_retracted
    assert second.already_retracted and not second.retracted
    assert second.valid_to == first.valid_to
    assert second.retraction_id is None and second.deliveries == []
    assert len(await _events_for(conn, fid)) == 1
    assert await conn.fetchval(
        "SELECT count(*) FROM knowledge_fact_retractions WHERE fact_id = $1::uuid", fid) == 1


@pytest.mark.anyio
async def test_flag_off_records_the_ledger_row_but_queues_nothing(conn, eq):
    await seed_edges(conn)
    ep, fid = await seed_fact(conn)
    r = await _retract(conn, eq, fid, enabled=False)
    assert r.retracted and r.retraction_id is not None
    assert r.federation_enabled is False and r.deliveries == []
    assert await _events_for(conn, fid) == []
    row = await conn.fetchrow(
        "SELECT origin_node, valid_to FROM knowledge_fact_retractions WHERE id = $1", r.retraction_id)
    assert row["origin_node"] == NODE_A
    assert row["valid_to"].isoformat() == r.valid_to


@pytest.mark.anyio
async def test_no_event_queue_means_local_origin(conn):
    ep, fid = await seed_fact(conn)
    async with conn.transaction():
        r = await fr.retract_fact_transactional(
            conn, fact_id=uuid.UUID(fid), event_queue=None, federation_enabled=True,
            reason=None, retracted_by=None)
    assert r.retracted and r.node_rid is None and r.deliveries == []
    assert await conn.fetchval(
        "SELECT origin_node FROM knowledge_fact_retractions WHERE id = $1", r.retraction_id) == fr.LOCAL_ORIGIN


@pytest.mark.anyio
async def test_unknown_fact_returns_none(conn, eq):
    async with conn.transaction():
        assert await fr.retract_fact_transactional(
            conn, fact_id=uuid.uuid4(), event_queue=eq, federation_enabled=True,
            reason=None, retracted_by=None) is None


# ═══════════════════════════════════════════════════════════════════════════
# 2. Recipients
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("scope,expected", [
    (None, True),
    ([], False),
    (["knowledge_fact"], True),
    (["KNOWLEDGE_FACT"], True),
    (["knowledge_episode"], False),
    (["Person", "Concept"], False),
])
def test_scope_admits_rule(scope, expected):
    assert fr.scope_admits(scope) is expected


@pytest.mark.anyio
async def test_scope_admits_agrees_with_a_real_poll(conn, pool):
    """Positive control for the selector: whatever scope_admits says, poll() does.

    For each scope, queue a unicast knowledge_fact event and poll with that
    exact scope; the event is handed over iff scope_admits(scope). If these
    ever disagree the selector would queue events the transport silently
    drops (and marks delivered_to) — the #67 artifact all over again.
    """
    q = EventQueue(pool, NODE_A)
    cases = [None, [], ["knowledge_fact"], ["KNOWLEDGE_FACT"], ["knowledge_episode"], ["Person"]]
    for i, scope in enumerate(cases):
        peer = f"orn:koi-net.node:scope-probe-{i}+p{i}"
        ev = await q.add(event_type="UPDATE", rid=FACT_RID(uuid.uuid4()),
                         contents={"_koi_domain": "knowledge_fact", "payload": {}},
                         target_node=peer, event_id=str(uuid.uuid4()))
        got = [e["event_id"] for e in await q.poll(peer, rid_types=scope)]
        assert (ev in got) is fr.scope_admits(scope), f"scope={scope!r}: poll={ev in got}"


@pytest.mark.anyio
async def test_only_admitting_approved_edges_get_an_event(conn, eq):
    await seed_edges(conn)
    ep, fid = await seed_fact(conn)
    r = await _retract(conn, eq, fid)
    events = await _events_for(conn, fid)
    assert [e["target_node"] for e in events] == [PEER_AUTH]
    assert all(e["target_node"] is not None for e in events), "retraction events must be unicast"
    dels = await _deliveries(conn, fid)
    assert [(d["target_node"], d["state"]) for d in dels] == [(PEER_AUTH, "queued")]
    assert r.deliveries == [{"target_node": PEER_AUTH, "state": "queued",
                             "event_id": events[0]["event_id"], "attempt": 1}]


@pytest.mark.anyio
async def test_confirmed_original_recipient_without_scope_is_recorded_unauthorized(conn, eq):
    """PEER_NARROW confirmed the original episode NEW (it held knowledge_episode
    scope back then) but its edge no longer admits knowledge_fact → an
    `unauthorized` row with NO event, so the operator sees the gap and the
    tombstone never leaves policy."""
    await seed_edges(conn)
    ep, fid = await seed_fact(conn)
    # The original NEW that carried the fact, confirmed by PEER_NARROW and PEER_AUTH.
    orig = str(uuid.uuid4())
    await eq.add(event_type="NEW", rid=fr.episode_rid(ep),
                 contents={"_koi_domain": "knowledge_episode",
                           "payload": episode_payload(ep, [fact_payload(fid, ep)], event_id=orig)},
                 event_id=orig)
    await conn.execute(
        "UPDATE koi_net_events SET confirmed_by = $2 WHERE event_id = $1::uuid",
        orig, [PEER_NARROW, PEER_AUTH])

    r = await _retract(conn, eq, fid)
    dels = {d["target_node"]: d for d in await _deliveries(conn, fid)}
    assert set(dels) == {PEER_AUTH, PEER_NARROW}
    assert dels[PEER_AUTH]["state"] == "queued"
    assert dels[PEER_NARROW]["state"] == "unauthorized"
    assert dels[PEER_NARROW]["event_id"] is None
    assert "no_longer_admits" in dels[PEER_NARROW]["state_reason"]
    assert [e["target_node"] for e in await _events_for(conn, fid)] == [PEER_AUTH]
    # and the payload names the original event
    ev = (await _events_for(conn, fid))[0]
    assert ev["contents"]["payload"]["retraction"]["original_event_ids"] == [orig]


@pytest.mark.anyio
async def test_delivered_to_only_is_not_treated_as_original_recipient(conn, eq):
    """A peer present only in delivered_to (an exclusion mark) is NOT evidence."""
    await seed_edges(conn)
    ep, fid = await seed_fact(conn)
    orig = str(uuid.uuid4())
    await eq.add(event_type="NEW", rid=fr.episode_rid(ep),
                 contents={"_koi_domain": "knowledge_episode",
                           "payload": episode_payload(ep, [fact_payload(fid, ep)], event_id=orig)},
                 event_id=orig)
    await conn.execute(
        "UPDATE koi_net_events SET delivered_to = $2 WHERE event_id = $1::uuid",
        orig, [PEER_NARROW, PEER_REVOKED])
    await _retract(conn, eq, fid)
    assert [(d["target_node"], d["state"]) for d in await _deliveries(conn, fid)] == [(PEER_AUTH, "queued")]


# ═══════════════════════════════════════════════════════════════════════════
# 3. Payload
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_payload_carries_committed_valid_to_byte_for_byte(conn, eq):
    await seed_edges(conn)
    ep, fid = await seed_fact(conn)
    r = await _retract(conn, eq, fid, reason="why", by="service:me")
    committed = await conn.fetchval("SELECT valid_to FROM knowledge_facts WHERE id = $1::uuid", fid)
    ev = (await _events_for(conn, fid))[0]
    p = ev["contents"]["payload"]
    assert p["valid_to"] == committed.isoformat() == r.valid_to
    assert p["retraction"]["valid_to"] == committed.isoformat()
    assert p["id"] == fid and p["episode_id"] == ep
    assert p["retraction"]["origin_node"] == NODE_A
    assert p["retraction"]["reason"] == "why" and p["retraction"]["retracted_by"] == "service:me"
    assert p["retraction"]["attempt"] == 1
    assert p["retraction"]["episode_rid"] == fr.episode_rid(ep)
    assert p["_federation_event_id"] == ev["event_id"]
    assert ev["event_type"] == "UPDATE" and ev["contents"]["_koi_domain"] == "knowledge_fact"
    # every key _insert_fact reads is present
    for k in ("subject_uri", "predicate", "object_uri", "object_literal", "fact_text",
              "valid_from", "created_at", "group_id", "source_node_rid",
              "turn_range_start", "turn_range_end", "embedding_column", "embedding_value"):
        assert k in p, k
    # ledger snapshot equals the payload minus the retraction envelope
    snap = await conn.fetchval(
        "SELECT fact_snapshot FROM knowledge_fact_retractions WHERE id = $1", r.retraction_id)
    snap = json.loads(snap) if isinstance(snap, str) else snap
    assert snap["valid_to"] == committed.isoformat()
    # 72h TTL, not the 24h default
    ttl_h = await conn.fetchval(
        "SELECT EXTRACT(EPOCH FROM (expires_at - queued_at))/3600 FROM koi_net_events WHERE event_id = $1::uuid",
        ev["event_id"])
    assert 71.9 < float(ttl_h) < 72.1


def test_build_payload_refuses_a_snapshot_that_disagrees_with_the_committed_value():
    snap = {"id": "x", "episode_id": None, "valid_to": "2026-01-01T00:00:00+00:00"}
    with pytest.raises(fr.RetractionError):
        fr.build_retraction_payload(
            snap, retraction_id=1, origin_node=NODE_A, valid_to_iso="2026-01-01T00:00:01+00:00",
            reason=None, retracted_by=None, attempt=1, source_document=None,
            original_event_ids=[], event_id=str(uuid.uuid4()))


# ═══════════════════════════════════════════════════════════════════════════
# 4. Ledger states
# ═══════════════════════════════════════════════════════════════════════════

async def _setup_queued(conn, eq):
    await seed_edges(conn)
    ep, fid = await seed_fact(conn)
    r = await _retract(conn, eq, fid)
    ev = r.deliveries[0]["event_id"]
    return ep, fid, r, ev


async def _state(conn, fid, peer=PEER_AUTH):
    return await conn.fetchrow(
        """
        SELECT d.state, d.state_reason, d.application, d.attempt, d.event_id::TEXT AS event_id,
               d.delivered_at, d.received_at, d.applied_at
        FROM knowledge_fact_retraction_deliveries d
        JOIN knowledge_fact_retractions r ON r.id = d.retraction_id
        WHERE r.fact_id = $1::uuid AND d.target_node = $2
        """, fid, peer)


@pytest.mark.anyio
async def test_poll_moves_queued_to_delivered_via_the_observer(conn, eq):
    ep, fid, r, ev = await _setup_queued(conn, eq)
    got = await eq.poll(PEER_AUTH, rid_types=["knowledge_fact"])
    assert [e["event_id"] for e in got] == [ev]
    s = await _state(conn, fid)
    assert s["state"] == "delivered" and s["delivered_at"] is not None


@pytest.mark.anyio
async def test_scope_narrowed_after_queueing_is_failed_not_delivered(conn, eq):
    """The edge changed between queue and poll: poll() excludes (and marks
    delivered_to). The ledger must say `failed`/edge_scope_excluded, never
    `delivered`. This is the access-policy-change case."""
    ep, fid, r, ev = await _setup_queued(conn, eq)
    got = await eq.poll(PEER_AUTH, rid_types=["Person"])   # narrowed
    assert got == []
    row = await conn.fetchrow(
        "SELECT delivered_to FROM koi_net_events WHERE event_id = $1::uuid", ev)
    assert PEER_AUTH in row["delivered_to"], "poll should have exclusion-marked it"
    s = await _state(conn, fid)
    assert s["state"] == "failed" and s["state_reason"] == "edge_scope_excluded_at_poll"


@pytest.mark.anyio
async def test_confirm_records_receipt_not_application(conn, eq):
    ep, fid, r, ev = await _setup_queued(conn, eq)
    await eq.poll(PEER_AUTH, rid_types=["knowledge_fact"])
    n = await fr.record_receipts(conn, PEER_AUTH, [ev])
    assert n == 1
    s = await _state(conn, fid)
    assert s["state"] == "received" and s["received_at"] is not None and s["applied_at"] is None


@pytest.mark.anyio
@pytest.mark.parametrize("status,expected_state,reason_fragment", [
    ("applied", "applied", "peer_reported_applied"),
    ("already_tombstoned", "applied", "peer_already_tombstoned"),
    ("pending", "applied", "fact_absent"),
    ("rejected", "rejected", "ledger_unavailable"),
    ("bogus", "rejected", "unrecognized_report_status"),
])
async def test_applications_map_to_ledger_states(conn, eq, status, expected_state, reason_fragment):
    ep, fid, r, ev = await _setup_queued(conn, eq)
    report = {"application": True, "domain": "knowledge_fact", "event_id": ev, "fact_id": fid,
              "status": status, "valid_to": r.valid_to, "valid_to_matches": True,
              "reason": "ledger_unavailable" if status == "rejected" else None,
              "node": PEER_AUTH}
    summary = await fr.record_applications(conn, PEER_AUTH, [report])
    s = await _state(conn, fid)
    assert s["state"] == expected_state, s
    assert reason_fragment in s["state_reason"]
    app = s["application"]
    app = json.loads(app) if isinstance(app, str) else app
    assert app == report, "the report must be stored verbatim"
    assert summary["applied" if expected_state == "applied" else "rejected"] == 1
    if expected_state == "applied":
        assert s["applied_at"] is not None


@pytest.mark.anyio
async def test_already_tombstoned_with_mismatch_is_named(conn, eq):
    ep, fid, r, ev = await _setup_queued(conn, eq)
    await fr.record_applications(conn, PEER_AUTH, [{
        "event_id": ev, "status": "already_tombstoned", "valid_to_matches": False,
        "valid_to": "2020-01-01T00:00:00+00:00"}])
    s = await _state(conn, fid)
    assert s["state"] == "applied" and s["state_reason"] == "peer_already_tombstoned_valid_to_mismatch"


@pytest.mark.anyio
async def test_report_from_the_wrong_node_is_ignored(conn, eq):
    ep, fid, r, ev = await _setup_queued(conn, eq)
    summary = await fr.record_applications(conn, PEER_NARROW, [{"event_id": ev, "status": "applied"}])
    assert summary["ignored"] == 1
    assert (await _state(conn, fid))["state"] == "queued"


@pytest.mark.anyio
async def test_unknown_and_malformed_reports_are_counted_not_raised(conn, eq):
    ep, fid, r, ev = await _setup_queued(conn, eq)
    summary = await fr.record_applications(conn, PEER_AUTH, [
        {"event_id": str(uuid.uuid4()), "status": "applied"},   # unknown event
        {"status": "applied"},                                   # no event_id
        {"event_id": "not-a-uuid", "status": "applied"},
        "garbage",
    ])
    assert summary == {"applied": 0, "rejected": 0, "ignored": 3, "unknown_event": 1, "already_terminal": 0}
    assert (await _state(conn, fid))["state"] == "queued"


@pytest.mark.anyio
async def test_terminal_states_keep_their_first_verdict(conn, eq):
    ep, fid, r, ev = await _setup_queued(conn, eq)
    await fr.record_applications(conn, PEER_AUTH, [{"event_id": ev, "status": "applied"}])
    summary = await fr.record_applications(conn, PEER_AUTH, [{"event_id": ev, "status": "rejected"}])
    assert summary["already_terminal"] == 1
    assert (await _state(conn, fid))["state"] == "applied"
    # a later receipt confirm does not regress it either
    assert await fr.record_receipts(conn, PEER_AUTH, [ev]) == 0
    assert (await _state(conn, fid))["state"] == "applied"


@pytest.mark.anyio
async def test_ledger_absent_makes_the_observations_noops(pool):
    c, tx = await open_scratch_conn()
    try:
        assert await fr.record_deliveries(c, PEER_AUTH, [str(uuid.uuid4())], []) == {"delivered": 0, "failed": 0}
        assert await fr.record_receipts(c, PEER_AUTH, [str(uuid.uuid4())]) == 0
        s = await fr.record_applications(c, PEER_AUTH, [{"event_id": str(uuid.uuid4()), "status": "applied"}])
        assert s["ignored"] == 1
    finally:
        await close_scratch_conn(c, tx)


# ═══════════════════════════════════════════════════════════════════════════
# 5. Retry
# ═══════════════════════════════════════════════════════════════════════════

async def _expire(conn, event_id):
    await conn.execute(
        "UPDATE koi_net_events SET expires_at = NOW() - INTERVAL '1 second' WHERE event_id = $1::uuid",
        event_id)


@pytest.mark.anyio
async def test_live_event_waits(conn, eq):
    ep, fid, r, ev = await _setup_queued(conn, eq)
    plan = await fr.plan_requeue(conn, node_rid=NODE_A)
    mine = [p for p in plan if p["fact_id"] == fid]
    assert mine and mine[0]["action"] == "wait"


@pytest.mark.anyio
async def test_expired_unconfirmed_event_is_requeued_with_a_fresh_id(conn, eq):
    """The offline-peer case: the 72h event lapsed with no confirm. The sweep
    queues a NEW event (fresh event_id — the dedup index would swallow a
    reuse), attempt 2, same committed valid_to, state `retrying`."""
    ep, fid, r, ev = await _setup_queued(conn, eq)
    await _expire(conn, ev)
    plan = await fr.plan_requeue(conn, node_rid=NODE_A)
    item = next(p for p in plan if p["fact_id"] == fid)
    assert item["action"] == "retry" and item["next_attempt"] == 2
    async with conn.transaction():
        summary = await fr.apply_requeue(conn, [item], event_queue=eq, node_rid=NODE_A)
    assert summary["retried"] == 1
    s = await _state(conn, fid)
    assert s["state"] == "retrying" and s["attempt"] == 2 and s["event_id"] != ev
    events = await _events_for(conn, fid)
    assert len(events) == 2
    new = next(e for e in events if e["event_id"] == s["event_id"])
    assert new["contents"]["payload"]["retraction"]["attempt"] == 2
    assert new["contents"]["payload"]["valid_to"] == r.valid_to
    assert new["target_node"] == PEER_AUTH
    hist = await conn.fetchval(
        "SELECT attempt_history FROM knowledge_fact_retraction_deliveries WHERE event_id = $1::uuid",
        s["event_id"])
    hist = json.loads(hist) if isinstance(hist, str) else hist
    assert [h["attempt"] for h in hist] == [1, 2] and hist[0]["event_id"] == ev
    # the peer reconnects: polls, applies, reports → applied
    got = await eq.poll(PEER_AUTH, rid_types=["knowledge_fact"])
    assert [e["event_id"] for e in got] == [s["event_id"]]
    assert (await _state(conn, fid))["state"] == "delivered"
    await fr.record_receipts(conn, PEER_AUTH, [s["event_id"]])
    await fr.record_applications(conn, PEER_AUTH, [{"event_id": s["event_id"], "status": "applied"}])
    assert (await _state(conn, fid))["state"] == "applied"


@pytest.mark.anyio
async def test_attempts_exhausted_is_terminal_failure(conn, eq):
    ep, fid, r, ev = await _setup_queued(conn, eq)
    for expected_attempt in (2, 3):
        cur = (await _state(conn, fid))["event_id"]
        await _expire(conn, cur)
        plan = [p for p in await fr.plan_requeue(conn, node_rid=NODE_A, max_attempts=3) if p["fact_id"] == fid]
        async with conn.transaction():
            await fr.apply_requeue(conn, plan, event_queue=eq, node_rid=NODE_A)
        assert (await _state(conn, fid))["attempt"] == expected_attempt
    await _expire(conn, (await _state(conn, fid))["event_id"])
    plan = [p for p in await fr.plan_requeue(conn, node_rid=NODE_A, max_attempts=3) if p["fact_id"] == fid]
    assert plan[0]["action"] == "fail" and "attempts_exhausted" in plan[0]["reason"]
    async with conn.transaction():
        summary = await fr.apply_requeue(conn, plan, event_queue=eq, node_rid=NODE_A)
    assert summary["failed"] == 1
    assert (await _state(conn, fid))["state"] == "failed"


@pytest.mark.anyio
async def test_receipt_without_report_ages_to_unverifiable(conn, eq):
    ep, fid, r, ev = await _setup_queued(conn, eq)
    await eq.poll(PEER_AUTH, rid_types=["knowledge_fact"])
    await fr.record_receipts(conn, PEER_AUTH, [ev])
    plan = [p for p in await fr.plan_requeue(conn, node_rid=NODE_A) if p["fact_id"] == fid]
    assert plan[0]["action"] == "wait"
    await _expire(conn, ev)
    plan = [p for p in await fr.plan_requeue(conn, node_rid=NODE_A) if p["fact_id"] == fid]
    assert plan[0]["action"] == "unverifiable"
    async with conn.transaction():
        summary = await fr.apply_requeue(conn, plan, event_queue=eq, node_rid=NODE_A)
    assert summary["unverifiable"] == 1
    s = await _state(conn, fid)
    assert s["state"] == "unverifiable" and "no_application_report" in s["state_reason"]


@pytest.mark.anyio
async def test_revoked_edge_fails_the_open_obligation(conn, eq):
    ep, fid, r, ev = await _setup_queued(conn, eq)
    await seed_edge(conn, NODE_A, PEER_AUTH, "REVOKED", ["knowledge_fact"])
    plan = [p for p in await fr.plan_requeue(conn, node_rid=NODE_A) if p["fact_id"] == fid]
    assert plan[0]["action"] == "fail" and plan[0]["reason"] == "edge_no_longer_admits_knowledge_fact"


@pytest.mark.anyio
async def test_sweep_once_runs_plan_and_apply(conn, pool, eq):
    ep, fid, r, ev = await _setup_queued(conn, eq)
    await _expire(conn, ev)
    summary = await fr.sweep_once(pool, eq)
    assert summary["retried"] >= 1
    assert (await _state(conn, fid))["state"] == "retrying"


# ═══════════════════════════════════════════════════════════════════════════
# 6. Surfaces
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
async def knowledge_client(conn, pool, eq, monkeypatch):
    from api.routers.knowledge_router import create_router
    monkeypatch.setenv("KOI_CLAIMS_SERVICE_TOKEN", SVC_TOKEN)
    monkeypatch.setenv("KOI_FEDERATE_KNOWLEDGE", "true")
    prev = federation_events._event_queue
    federation_events.set_event_queue(eq)
    app = FastAPI()
    app.include_router(create_router(pool), prefix="/knowledge")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    federation_events.set_event_queue(prev)


@pytest.mark.anyio
async def test_endpoint_retract_then_tombstone_lookup_and_ordinary_read(conn, knowledge_client):
    await seed_edges(conn)
    ep, fid = await seed_fact(conn)

    # ordinary read shows the live fact
    before = await knowledge_client.get(f"/knowledge/entity/orn:entity:s/facts")
    assert before.status_code == 200
    assert fid in {f["id"] for f in before.json()["facts"]}

    resp = await knowledge_client.post(f"/knowledge/facts/{fid}/retract",
                                       json={"reason": "surface"}, headers=SVC_AUTH)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["retracted"] is True and body["retraction_id"] is not None
    assert body["federation"]["enabled"] is True and body["federation"]["node_rid"] == NODE_A
    assert [(d["target_node"], d["state"]) for d in body["federation"]["deliveries"]] == [(PEER_AUTH, "queued")]

    # ordinary read now hides it
    after = await knowledge_client.get(f"/knowledge/entity/orn:entity:s/facts")
    assert fid not in {f["id"] for f in after.json()["facts"]}

    # authorized lookup proves the tombstone and shows the obligation
    assert (await knowledge_client.get(f"/knowledge/facts/{fid}/tombstone")).status_code == 401
    look = await knowledge_client.get(f"/knowledge/facts/{fid}/tombstone", headers=SVC_AUTH)
    assert look.status_code == 200, look.text
    t = look.json()
    assert t["exists"] and t["tombstoned"] and t["valid_to"] == body["valid_to"]
    assert t["ledger_available"] is True and t["pending_tombstone"] is False
    assert [r["retraction_id"] for r in t["retractions"]] == [body["retraction_id"]]
    assert [(d["target_node"], d["state"]) for d in t["deliveries"]] == [(PEER_AUTH, "queued")]

    # unknown fact
    miss = await knowledge_client.get(f"/knowledge/facts/{uuid.uuid4()}/tombstone", headers=SVC_AUTH)
    assert miss.status_code == 200 and miss.json()["exists"] is False
    assert (await knowledge_client.get("/knowledge/facts/not-a-uuid/tombstone", headers=SVC_AUTH)).status_code == 422


@pytest.mark.anyio
async def test_endpoint_refuses_without_ledger(pool, monkeypatch):
    """503 before any write when migration 127 is absent."""
    from api.routers.knowledge_router import create_router
    c, tx = await open_scratch_conn()
    try:
        monkeypatch.setenv("KOI_CLAIMS_SERVICE_TOKEN", SVC_TOKEN)
        p = SingleConnPool(c)
        prev = federation_events._event_queue
        federation_events.set_event_queue(EventQueue(p, NODE_A))
        ep, fid = await seed_fact(c)
        app = FastAPI()
        app.include_router(create_router(p), prefix="/knowledge")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(f"/knowledge/facts/{fid}/retract", headers=SVC_AUTH)
        federation_events.set_event_queue(prev)
        assert resp.status_code == 503 and "127" in resp.text
        assert await c.fetchval("SELECT valid_to FROM knowledge_facts WHERE id = $1::uuid", fid) is None
    finally:
        await close_scratch_conn(c, tx)


@pytest.fixture
async def koi_net_client(conn, pool, eq, monkeypatch):
    """koi_net_router with its module globals pointed at the scratch connection.

    Unsigned confirm is accepted with KOI_STRICT_MODE unset (the module's
    default); facts/lookup always requires a signed envelope, so the fixture
    also mints a keypair for PEER_AUTH and registers its public key.
    """
    for var in ("KOI_STRICT_MODE", "KOI_REQUIRE_SIGNED_ENVELOPES", "KOI_ENFORCE_TARGET_MATCH",
                "KOI_ENFORCE_SOURCE_KEY_RID_BINDING", "KOI_NET_REQUIRE_APPROVED_EDGE_FOR_POLL"):
        monkeypatch.delenv(var, raising=False)
    saved = (knr._db_pool, knr._event_queue, knr._node_profile)
    knr._db_pool = pool
    knr._event_queue = eq
    knr._node_profile = SimpleNamespace(node_rid=NODE_A)
    priv = generate_keypair()
    await conn.execute(
        "INSERT INTO koi_net_nodes (node_rid, node_name, public_key, status) VALUES ($1, 'peer-auth', $2, 'active') "
        "ON CONFLICT (node_rid) DO UPDATE SET public_key = EXCLUDED.public_key",
        PEER_AUTH, public_key_to_der_b64(priv.public_key()))
    app = FastAPI()
    app.include_router(knr.koi_net_router, prefix="/koi-net")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, priv
    knr._db_pool, knr._event_queue, knr._node_profile = saved


@pytest.mark.anyio
async def test_confirm_endpoint_records_receipts_and_applications(conn, eq, koi_net_client):
    client, priv = koi_net_client
    ep, fid, r, ev = await _setup_queued(conn, eq)
    await eq.poll(PEER_AUTH, rid_types=["knowledge_fact"])

    # receipt only (what an older peer sends)
    resp = await client.post("/koi-net/events/confirm", json={"node_id": PEER_AUTH, "event_ids": [ev]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["confirmed"] == 1
    assert (await _state(conn, fid))["state"] == "received"

    # receipt + application report (what this code's poller sends)
    report = {"application": True, "domain": "knowledge_fact", "event_id": ev, "fact_id": fid,
              "status": "applied", "valid_to": r.valid_to, "valid_to_matches": True,
              "reason": None, "node": PEER_AUTH}
    resp = await client.post("/koi-net/events/confirm",
                             json={"node_id": PEER_AUTH, "event_ids": [ev], "applications": [report]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["applications"]["applied"] == 1
    s = await _state(conn, fid)
    assert s["state"] == "applied"
    app = s["application"]
    app = json.loads(app) if isinstance(app, str) else app
    assert app == report


@pytest.mark.anyio
async def test_facts_lookup_is_signed_and_edge_scoped(conn, eq, koi_net_client):
    client, priv = koi_net_client
    await seed_edges(conn)
    ep, fid = await seed_fact(conn)
    r = await _retract(conn, eq, fid)

    # unsigned → identity required
    resp = await client.post("/koi-net/facts/lookup", json={"fact_ids": [fid]})
    assert resp.status_code == 403, resp.text

    # signed by an authorized peer → tombstone visible, deliveries NOT exposed
    env = sign_envelope({"fact_ids": [fid, str(uuid.uuid4())]}, PEER_AUTH, NODE_A, priv)
    resp = await client.post("/koi-net/facts/lookup", json=env)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    payload = body["payload"] if "payload" in body else body
    facts = {f["fact_id"]: f for f in payload["facts"]}
    assert facts[fid]["tombstoned"] is True and facts[fid]["valid_to"] == r.valid_to
    assert "deliveries" not in facts[fid]
    assert [f for f in payload["facts"] if not f["exists"]], "unknown id reported as exists=false"

    # signed by a peer whose edge does not admit knowledge_fact → refused
    priv_narrow = generate_keypair()
    await conn.execute(
        "INSERT INTO koi_net_nodes (node_rid, node_name, public_key, status) VALUES ($1, 'narrow', $2, 'active') "
        "ON CONFLICT (node_rid) DO UPDATE SET public_key = EXCLUDED.public_key",
        PEER_NARROW, public_key_to_der_b64(priv_narrow.public_key()))
    env = sign_envelope({"fact_ids": [fid]}, PEER_NARROW, NODE_A, priv_narrow)
    resp = await client.post("/koi-net/facts/lookup", json=env)
    assert resp.status_code == 403, resp.text

    # bad ids
    env = sign_envelope({"fact_ids": ["nope"]}, PEER_AUTH, NODE_A, priv)
    assert (await client.post("/koi-net/facts/lookup", json=env)).status_code == 400
    env = sign_envelope({"fact_ids": []}, PEER_AUTH, NODE_A, priv)
    assert (await client.post("/koi-net/facts/lookup", json=env)).status_code == 400
