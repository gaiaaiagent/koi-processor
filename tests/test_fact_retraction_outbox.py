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
    ("pending", "pending", "fact_absent"),
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
    assert summary[expected_state] == 1
    if expected_state == "applied":
        assert s["applied_at"] is not None
    else:
        assert s["applied_at"] is None, "only `applied` sets applied_at (second review, finding 2)"


@pytest.mark.anyio
async def test_already_tombstoned_with_match_is_applied(conn, eq):
    ep, fid, r, ev = await _setup_queued(conn, eq)
    await fr.record_applications(conn, PEER_AUTH, [{
        "event_id": ev, "status": "already_tombstoned", "valid_to_matches": True,
        "valid_to": r.valid_to}])
    s = await _state(conn, fid)
    assert s["state"] == "applied" and s["state_reason"] == "peer_already_tombstoned"
    # the mismatch case is test_review_mismatched_tombstone_is_rejected_not_applied


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
    assert summary == {"applied": 0, "pending": 0, "rejected": 0, "ignored": 3, "unknown_event": 1,
                       "already_terminal": 0}
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

async def _fake_embed(text, **kwargs):
    """Deterministic 3072-dim embedding (same shape as tests/unit/test_knowledge_router.py).
    Needed so create_episode's dedup/supersession candidate query runs."""
    seed = hash(text)
    return [float(((seed + i) % 97) + 1) / 97.0 for i in range(3072)]


@pytest.fixture
async def knowledge_client(conn, pool, eq, monkeypatch):
    from api.routers.knowledge_router import create_router
    monkeypatch.setenv("KOI_CLAIMS_SERVICE_TOKEN", SVC_TOKEN)
    monkeypatch.setenv("KOI_FEDERATE_KNOWLEDGE", "true")
    prev = federation_events._event_queue
    federation_events.set_event_queue(eq)
    app = FastAPI()
    app.include_router(create_router(pool, generate_document_embedding=_fake_embed), prefix="/knowledge")
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

    # receipt only (what an older peer sends — SIGNED: every peer's poller
    # signs whenever it holds a private key, which setup_koi_net always loads;
    # an unsigned confirm records no receipt, see the r2 tests below)
    env = sign_envelope({"type": "confirm_events", "event_ids": [ev]}, PEER_AUTH, NODE_A, priv)
    resp = await client.post("/koi-net/events/confirm", json=env)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    payload = body["payload"] if "payload" in body else body
    assert payload["confirmed"] == 1
    assert (await _state(conn, fid))["state"] == "received"

    # receipt + application report (what this code's poller sends — SIGNED;
    # an unsigned report is ignored, see the review tests below)
    report = {"application": True, "domain": "knowledge_fact", "event_id": ev, "fact_id": fid,
              "status": "applied", "valid_to": r.valid_to, "valid_to_matches": True,
              "reason": None, "node": PEER_AUTH}
    env = sign_envelope({"type": "confirm_events", "event_ids": [ev], "applications": [report]},
                        PEER_AUTH, NODE_A, priv)
    resp = await client.post("/koi-net/events/confirm", json=env)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    payload = body["payload"] if "payload" in body else body
    assert payload["applications"]["applied"] == 1
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


@pytest.mark.anyio
async def test_unsigned_confirm_cannot_record_applications_under_the_live_policy(
    conn, eq, koi_net_client, monkeypatch,
):
    """Spoofing guard. The live node runs KOI_NET_REQUIRE_APPROVED_EDGE_FOR_POLL=true
    (read from the serving process's environment 2026-09-16), under which
    /koi-net/events/confirm refuses an UNSIGNED request outright
    (IDENTITY_REQUIRED) — so an `applications` report can only ever be
    recorded for the node that signed the envelope. With the flag on, an
    unsigned confirm naming PEER_AUTH must be 403 and move nothing."""
    client, priv = koi_net_client
    ep, fid, r, ev = await _setup_queued(conn, eq)
    await eq.poll(PEER_AUTH, rid_types=["knowledge_fact"])
    monkeypatch.setenv("KOI_NET_REQUIRE_APPROVED_EDGE_FOR_POLL", "true")
    resp = await client.post("/koi-net/events/confirm", json={
        "node_id": PEER_AUTH, "event_ids": [ev],
        "applications": [{"event_id": ev, "status": "applied"}]})
    assert resp.status_code == 403, resp.text
    s = await _state(conn, fid)
    assert s["state"] == "delivered" and s["application"] is None


# ═══════════════════════════════════════════════════════════════════════════
# 7. Review findings (2026-09-16 adversarial review) — each written red first
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_review_mismatched_tombstone_is_rejected_not_applied(conn, eq):
    """AC2 says the SAME valid_to. A peer that reports already_tombstoned with a
    different value has not met it; recording that as `applied` hid the
    violation. It is `rejected: peer_holds_different_valid_to`, report kept."""
    ep, fid, r, ev = await _setup_queued(conn, eq)
    await fr.record_applications(conn, PEER_AUTH, [{
        "event_id": ev, "status": "already_tombstoned", "valid_to_matches": False,
        "valid_to": "2020-01-01T00:00:00+00:00"}])
    s = await _state(conn, fid)
    assert s["state"] == "rejected" and s["state_reason"] == "peer_holds_different_valid_to"
    app = s["application"]
    app = json.loads(app) if isinstance(app, str) else app
    assert app["valid_to"] == "2020-01-01T00:00:00+00:00"


@pytest.mark.anyio
async def test_review_unmigrated_peer_rejection_is_reopened_by_the_sweep(conn, eq):
    """`rejected: ledger_unavailable…` is a peer that has not run migration 127 —
    an infrastructure condition, not a verdict. The sweep re-queues it (fresh
    event, attempt+1) while attempts remain, without waiting for expiry (the
    peer already consumed the event), and fails it terminally after that."""
    ep, fid, r, ev = await _setup_queued(conn, eq)
    await fr.record_applications(conn, PEER_AUTH, [{
        "event_id": ev, "status": "rejected", "reason": "ledger_unavailable_fact_absent"}])
    assert (await _state(conn, fid))["state"] == "rejected"
    plan = [p for p in await fr.plan_requeue(conn, node_rid=NODE_A) if p["fact_id"] == fid]
    assert plan and plan[0]["action"] == "retry", plan
    async with conn.transaction():
        await fr.apply_requeue(conn, plan, event_queue=eq, node_rid=NODE_A)
    s = await _state(conn, fid)
    assert s["state"] == "retrying" and s["attempt"] == 2 and s["event_id"] != ev
    # once the peer has 127 it reports applied on the new event → applied
    await fr.record_applications(conn, PEER_AUTH, [{"event_id": s["event_id"], "status": "applied"}])
    assert (await _state(conn, fid))["state"] == "applied"


@pytest.mark.anyio
async def test_review_scope_failure_is_reopened_when_the_edge_widens_again(conn, eq):
    """`failed: edge_scope_excluded_at_poll` was terminal even after the operator
    widened the edge back. Policy reversal reopens it: the sweep retries."""
    ep, fid, r, ev = await _setup_queued(conn, eq)
    # the operator narrows the edge; the peer's next poll excludes the event
    await seed_edge(conn, NODE_A, PEER_AUTH, "APPROVED", ["Person"])
    assert await eq.poll(PEER_AUTH, rid_types=["Person"]) == []
    assert (await _state(conn, fid))["state"] == "failed"
    plan = [p for p in await fr.plan_requeue(conn, node_rid=NODE_A) if p["fact_id"] == fid]
    assert plan == [], "still narrow: nothing to do"
    await seed_edge(conn, NODE_A, PEER_AUTH, "APPROVED", ["knowledge_fact"])
    plan = [p for p in await fr.plan_requeue(conn, node_rid=NODE_A) if p["fact_id"] == fid]
    assert plan and plan[0]["action"] == "retry" and "edge_admits_again" in plan[0]["reason"]
    async with conn.transaction():
        await fr.apply_requeue(conn, plan, event_queue=eq, node_rid=NODE_A)
    assert (await _state(conn, fid))["state"] == "retrying"


@pytest.mark.anyio
async def test_review_unsigned_confirm_ignores_applications_under_the_default_policy(
    conn, eq, koi_net_client,
):
    """With NO KOI_* policy set, an unsigned confirm is accepted by the
    transport (pre-existing trust model for confirmed_by). It must not be able
    to mint `applied` — nor, since the second review (finding 6), `received` —
    for an arbitrary node_id: the retraction ledger is written only from a
    SIGNED envelope. The report is ignored and counted; the row stays where
    the (real) poll left it."""
    client, priv = koi_net_client
    ep, fid, r, ev = await _setup_queued(conn, eq)
    await eq.poll(PEER_AUTH, rid_types=["knowledge_fact"])
    resp = await client.post("/koi-net/events/confirm", json={
        "node_id": PEER_AUTH, "event_ids": [ev],
        "applications": [{"event_id": ev, "status": "applied", "valid_to": "1999-01-01T00:00:00+00:00"}]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["confirmed"] == 1
    assert body.get("applications", {}).get("ignored_unsigned") == 1
    assert body.get("applications", {}).get("ignored_unsigned_receipts") == 1
    s = await _state(conn, fid)
    assert s["state"] == "delivered" and s["application"] is None
    # the same report inside a signed envelope IS recorded
    env = sign_envelope({"type": "confirm_events", "event_ids": [ev],
                         "applications": [{"event_id": ev, "status": "applied"}]},
                        PEER_AUTH, NODE_A, priv)
    resp = await client.post("/koi-net/events/confirm", json=env)
    assert resp.status_code == 200, resp.text
    assert (await _state(conn, fid))["state"] == "applied"


@pytest.mark.anyio
async def test_review_facts_lookup_discloses_validity_only(conn, eq, koi_net_client):
    """Cross-node lookup answers 'is this UUID tombstoned here', not what the
    fact says: no subject/predicate/object/literal/group/source, no
    reasons or documents from the ledger rows."""
    client, priv = koi_net_client
    await seed_edges(conn)
    ep, fid = await seed_fact(conn)
    await _retract(conn, eq, fid, reason="secret reason")
    env = sign_envelope({"fact_ids": [fid]}, PEER_AUTH, NODE_A, priv)
    body = (await client.post("/koi-net/facts/lookup", json=env)).json()
    payload = body["payload"] if "payload" in body else body
    f = payload["facts"][0]
    assert set(f) == {"fact_id", "exists", "valid_to", "tombstoned", "pending_tombstone",
                      "ledger_available", "retractions"}, set(f)
    assert f["tombstoned"] is True
    assert all(set(r) == {"origin_node", "valid_to", "applied_at"} for r in f["retractions"]), f["retractions"]
    assert "secret reason" not in json.dumps(body)


@pytest.mark.anyio
async def test_review_supersession_in_create_episode_records_obligations(conn, knowledge_client):
    """The #67 defect class had a second API writer: create_episode's
    supersession auto-retire set valid_to on the old fact and bundled only the
    NEW facts, so the superseded fact's tombstone never federated. It now
    records the same ledger row + per-peer obligation as /retract."""
    await seed_edges(conn)
    tag = uuid.uuid4().hex[:8]
    body = {
        "name": f"sup {tag}", "content": "c", "source_description": "review",
        "source_document": f"sup-{tag}.md", "group_id": f"g-{tag}",
        "valid_at": "2026-09-16T00:00:00+00:00", "metadata": {},
        "facts": [{"subject": f"SupSubj {tag}", "predicate": "HAS_STATUS",
                   "object": f"Old {tag}", "fact_text": f"{tag} status is old",
                   "valid_from": "2026-09-16T00:00:00+00:00"}],
        "create_entities": True, "expire_existing": True,
    }
    r1 = await knowledge_client.post("/knowledge/episodes", json=body, headers=SVC_AUTH)
    assert r1.status_code == 201, r1.text
    old_id = r1.json()["fact_ids"][0]
    body["facts"][0].update(object=f"New {tag}", fact_text=f"{tag} status is new")
    body["name"] = f"sup2 {tag}"
    r2 = await knowledge_client.post("/knowledge/episodes", json=body, headers=SVC_AUTH)
    assert r2.status_code == 201, r2.text
    assert r2.json()["facts_superseded"] == 1
    committed = await conn.fetchval("SELECT valid_to FROM knowledge_facts WHERE id = $1::uuid", old_id)
    assert committed is not None
    dels = await _deliveries(conn, old_id)
    assert [(d["target_node"], d["state"]) for d in dels] == [(PEER_AUTH, "queued")]
    events = await _events_for(conn, old_id)
    assert len(events) == 1 and events[0]["contents"]["payload"]["valid_to"] == committed.isoformat()
    assert events[0]["contents"]["payload"]["retraction"]["reason"].startswith("superseded_by:")


@pytest.mark.anyio
async def test_review_operator_email_never_leaves_the_node(conn, eq):
    """Finding 4: a session-token caller's `_identity` is the operator's email.
    It stays in the local ledger; the wire carries an opaque role."""
    await seed_edges(conn)
    ep, fid = await seed_fact(conn)
    r = await _retract(conn, eq, fid, by="someone@example.com", reason="wrong binding")
    ev = (await _events_for(conn, fid))[0]
    wire = json.dumps(ev["contents"])
    assert "someone@example.com" not in wire
    assert ev["contents"]["payload"]["retraction"]["retracted_by"] == "operator"
    assert await conn.fetchval(
        "SELECT retracted_by FROM knowledge_fact_retractions WHERE id = $1", r.retraction_id
    ) == "someone@example.com"
    # service identities are opaque role names and pass through
    ep2, fid2 = await seed_fact(conn)
    await _retract(conn, eq, fid2, by="service:claims-service")
    ev2 = (await _events_for(conn, fid2))[0]
    assert ev2["contents"]["payload"]["retraction"]["retracted_by"] == "service:claims-service"


@pytest.mark.anyio
async def test_review_junk_id_in_a_confirm_batch_does_not_lose_the_batch(conn, eq):
    """Finding 11: `$2::uuid[]` raised on one non-UUID id and the whole batch's
    receipts were lost while the transport had already recorded them."""
    ep, fid, r, ev = await _setup_queued(conn, eq)
    await eq.poll(PEER_AUTH, rid_types=["knowledge_fact"])
    n = await fr.record_receipts(conn, PEER_AUTH, ["not-a-uuid", ev, ""])
    assert n == 1
    assert (await _state(conn, fid))["state"] == "received"
    assert await fr.record_deliveries(conn, PEER_AUTH, ["junk"], ["also-junk"]) == {"delivered": 0, "failed": 0}


@pytest.mark.anyio
async def test_review_no_admitting_peer_is_named_in_the_result(conn, eq):
    """Finding 19: federation on, queue present, no edge admits knowledge_fact →
    the retraction is recorded, reaches nobody, and SAYS so."""
    await seed_edge(conn, NODE_A, PEER_NARROW, "APPROVED", ["Person"])
    ep, fid = await seed_fact(conn)
    r = await _retract(conn, eq, fid)
    assert r.retracted and r.deliveries == [] and r.no_admitting_peers is True


# ═══════════════════════════════════════════════════════════════════════════
# 8. Second review round (session b35cb9db, 2026-09-17) — each written red first
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_r2_pending_report_is_its_own_state_not_applied(conn, eq):
    """Finding 2. A recipient reporting `pending` holds NO fact row — it recorded
    a pending tombstone and is waiting for the fact to arrive. Storing that as
    publisher `applied` (with applied_at set) made AC2 read "met" for a peer
    that stores nothing. It must be a distinct, non-terminal `pending` state:
    no applied_at, and a later GENUINE report (the fact arrived, the pending
    row landed, a re-sent event answers already_tombstoned with the same
    valid_to) may still advance it to `applied`."""
    ep, fid, r, ev = await _setup_queued(conn, eq)
    await eq.poll(PEER_AUTH, rid_types=["knowledge_fact"])
    summary = await fr.record_applications(conn, PEER_AUTH, [{
        "event_id": ev, "status": "pending", "fact_id": fid, "valid_to": None,
        "valid_to_matches": None}])
    assert summary["pending"] == 1 and summary["applied"] == 0
    s = await _state(conn, fid)
    assert s["state"] == "pending", s
    assert s["applied_at"] is None
    assert s["state_reason"] == "peer_recorded_pending_tombstone_fact_absent"
    assert s["received_at"] is not None, "a report implies receipt"
    # pending is NOT terminal: a later genuine application report lands
    later = await fr.record_applications(conn, PEER_AUTH, [{
        "event_id": ev, "status": "already_tombstoned", "valid_to": r.valid_to,
        "valid_to_matches": True}])
    assert later["applied"] == 1 and later["already_terminal"] == 0
    s = await _state(conn, fid)
    assert s["state"] == "applied" and s["applied_at"] is not None
    # and the lookup surface reports the row as pending, never as applied
    ep2, fid2, r2, ev2 = await _setup_queued(conn, eq)
    await fr.record_applications(conn, PEER_AUTH, [{"event_id": ev2, "status": "pending"}])
    status = await fr.tombstone_status(conn, uuid.UUID(fid2))
    d = [d for d in status["deliveries"] if d["target_node"] == PEER_AUTH][0]
    assert d["state"] == "pending" and d["applied_at"] is None


@pytest.mark.anyio
async def test_r2_pending_is_left_alone_by_the_sweep(conn, eq):
    """A pending delivery is neither open (nothing to retry: the peer already
    holds the pending tombstone) nor terminal. The sweep must not fail it, age
    it, or re-queue it — even after the event expires or the edge narrows."""
    ep, fid, r, ev = await _setup_queued(conn, eq)
    await fr.record_applications(conn, PEER_AUTH, [{"event_id": ev, "status": "pending"}])
    await _expire(conn, ev)
    assert [p for p in await fr.plan_requeue(conn, node_rid=NODE_A) if p["fact_id"] == fid] == []
    await seed_edge(conn, NODE_A, PEER_AUTH, "APPROVED", ["Person"])   # narrowed
    assert [p for p in await fr.plan_requeue(conn, node_rid=NODE_A) if p["fact_id"] == fid] == []
    assert (await _state(conn, fid))["state"] == "pending"


@pytest.mark.anyio
async def test_r2_received_row_is_preserved_when_the_edge_narrows(conn, eq):
    """Finding 3. The peer CONFIRMED receipt; a later edge change does not
    un-receive it. The sweep used to close it as `failed`
    (edge_no_longer_admits_knowledge_fact) — after which the peer's genuine
    application report was discarded as already_terminal. It must keep the
    receipt (wait while the event is live, `unverifiable` after expiry) and a
    later report must still land."""
    ep, fid, r, ev = await _setup_queued(conn, eq)
    await eq.poll(PEER_AUTH, rid_types=["knowledge_fact"])
    await fr.record_receipts(conn, PEER_AUTH, [ev])
    await seed_edge(conn, NODE_A, PEER_AUTH, "APPROVED", ["Person"])   # narrowed after receipt
    plan = [p for p in await fr.plan_requeue(conn, node_rid=NODE_A) if p["fact_id"] == fid]
    assert plan and plan[0]["action"] == "wait", plan
    async with conn.transaction():
        await fr.apply_requeue(conn, plan, event_queue=eq, node_rid=NODE_A)
    assert (await _state(conn, fid))["state"] == "received"
    # the peer's application report still lands
    summary = await fr.record_applications(conn, PEER_AUTH, [{"event_id": ev, "status": "applied"}])
    assert summary["applied"] == 1 and summary["already_terminal"] == 0
    assert (await _state(conn, fid))["state"] == "applied"
    # and with no report before expiry it ages to unverifiable, not failed
    ep2, fid2, r2, ev2 = await _setup_queued(conn, eq)
    await eq.poll(PEER_AUTH, rid_types=["knowledge_fact"])   # still narrowed → excluded, failed
    # (that one is the policy-change case; use a fresh receipt-then-narrow instead)
    await seed_edge(conn, NODE_A, PEER_AUTH, "APPROVED", ["knowledge_fact"])
    ep3, fid3, r3, ev3 = await _setup_queued(conn, eq)
    await eq.poll(PEER_AUTH, rid_types=["knowledge_fact"])
    await fr.record_receipts(conn, PEER_AUTH, [ev3])
    await seed_edge(conn, NODE_A, PEER_AUTH, "APPROVED", ["Person"])
    await _expire(conn, ev3)
    plan = [p for p in await fr.plan_requeue(conn, node_rid=NODE_A) if p["fact_id"] == fid3]
    assert plan[0]["action"] == "unverifiable", plan
    async with conn.transaction():
        await fr.apply_requeue(conn, plan, event_queue=eq, node_rid=NODE_A)
    s = await _state(conn, fid3)
    assert s["state"] == "unverifiable" and s["received_at"] is not None


@pytest.mark.anyio
async def test_r2_terminal_failures_and_rejections_are_logged(conn, eq, caplog):
    """Finding 8. `failed` (attempts exhausted) was reachable only per UUID:
    apply_requeue wrote the row and said nothing. Each terminal failure and each
    rejection must produce a WARNING naming the fact, the peer and the reason."""
    import logging
    ep, fid, r, ev = await _setup_queued(conn, eq)
    await seed_edge(conn, NODE_A, PEER_AUTH, "REVOKED", ["knowledge_fact"])
    plan = [p for p in await fr.plan_requeue(conn, node_rid=NODE_A) if p["fact_id"] == fid]
    assert plan[0]["action"] == "fail"
    with caplog.at_level(logging.WARNING, logger="api.fact_retraction"):
        async with conn.transaction():
            await fr.apply_requeue(conn, plan, event_queue=eq, node_rid=NODE_A)
    msgs = [rec.getMessage() for rec in caplog.records if "delivery_failed" in rec.getMessage()]
    assert msgs, [rec.getMessage() for rec in caplog.records]
    assert fid in msgs[0] and PEER_AUTH in msgs[0] and "edge_no_longer_admits_knowledge_fact" in msgs[0]
    caplog.clear()
    ep2, fid2, r2, ev2 = await _setup_queued(conn, eq)
    with caplog.at_level(logging.WARNING, logger="api.fact_retraction"):
        await fr.record_applications(conn, PEER_AUTH, [{"event_id": ev2, "status": "rejected",
                                                         "reason": "valid_to_unparseable"}])
    msgs = [rec.getMessage() for rec in caplog.records if "delivery_rejected" in rec.getMessage()]
    assert msgs and fid2 in msgs[0] and "valid_to_unparseable" in msgs[0]


@pytest.mark.anyio
async def test_r2_retried_payload_carries_the_prior_event_ids(conn, eq):
    """Finding 16. The first event names the episode NEW/UPDATE ids that carried
    the fact (`original_event_ids`); a retry used to send `[]`. The retry must
    carry the earlier attempts' event ids so the recipient can correlate."""
    ep, fid, r, ev = await _setup_queued(conn, eq)
    await _expire(conn, ev)
    item = next(p for p in await fr.plan_requeue(conn, node_rid=NODE_A) if p["fact_id"] == fid)
    async with conn.transaction():
        await fr.apply_requeue(conn, [item], event_queue=eq, node_rid=NODE_A)
    s = await _state(conn, fid)
    new = next(e for e in await _events_for(conn, fid) if e["event_id"] == s["event_id"])
    assert new["contents"]["payload"]["retraction"]["original_event_ids"] == [ev]


@pytest.mark.anyio
async def test_r2_tombstone_status_does_not_disclose_retracted_by(conn, eq):
    """Finding 11. The doc says the operator's email stays in the ledger row;
    GET …/tombstone returned it to any service token. The lookup reports the
    ledger rows without `retracted_by`."""
    await seed_edges(conn)
    ep, fid = await seed_fact(conn)
    await _retract(conn, eq, fid, by="someone@example.com")
    status = await fr.tombstone_status(conn, uuid.UUID(fid))
    assert status["retractions"], status
    assert all("retracted_by" not in row for row in status["retractions"])
    assert "someone@example.com" not in json.dumps(status)


@pytest.mark.anyio
async def test_r2_unsigned_poll_is_not_handed_retraction_events(conn, eq, koi_net_client):
    """Finding 4 (deployment blocker). Under the live policy an UNSIGNED poll
    naming `nuc-personal` was served nuc-personal's unicast retraction events —
    fact_text and the triple — and `delivered_to` was marked, so the real peer
    never saw them. Fail closed, independent of KOI_REQUIRE_SIGNED_ENVELOPES:
    a retraction event is handed only to a SIGNED requester. Unsigned: not
    served, not marked, ledger untouched. Signed by the peer's key: served."""
    client, priv = koi_net_client
    ep, fid, r, ev = await _setup_queued(conn, eq)

    resp = await client.post("/koi-net/events/poll", json={"node_id": PEER_AUTH, "limit": 50})
    assert resp.status_code == 200, resp.text
    assert [e["event_id"] for e in resp.json()["events"]] == []
    row = await conn.fetchrow("SELECT delivered_to FROM koi_net_events WHERE event_id = $1::uuid", ev)
    assert PEER_AUTH not in (row["delivered_to"] or []), "unsigned poll must not mark delivered_to"
    assert (await _state(conn, fid))["state"] == "queued"

    env = sign_envelope({"type": "poll_events", "limit": 50}, PEER_AUTH, NODE_A, priv)
    resp = await client.post("/koi-net/events/poll", json=env)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    payload = body["payload"] if "payload" in body else body
    assert [e["event_id"] for e in payload["events"]] == [ev]
    assert (await _state(conn, fid))["state"] == "delivered"


@pytest.mark.anyio
async def test_r2_unsigned_poll_still_serves_ordinary_events(conn, eq, koi_net_client):
    """The fail-closed rule is scoped to retraction events. An ordinary
    broadcast event is still served to an unsigned poller under the default
    policy (pre-existing behaviour, tracked separately)."""
    client, priv = koi_net_client
    await seed_edges(conn)
    eid = str(uuid.uuid4())
    # an in-scope ordinary domain event (no `retraction` block)
    await eq.add(event_type="NEW", rid=f"orn:personal-koi.knowledge-episode:{uuid.uuid4()}",
                 contents={"_koi_domain": "knowledge_episode", "payload": {"facts": []}},
                 event_id=eid)
    resp = await client.post("/koi-net/events/poll", json={"node_id": PEER_AUTH, "limit": 50})
    assert resp.status_code == 200, resp.text
    assert eid in [e["event_id"] for e in resp.json()["events"]]


@pytest.mark.anyio
async def test_r2_unsigned_confirm_records_no_receipt_for_a_retraction_delivery(
    conn, eq, koi_net_client,
):
    """Finding 6. Under the default policy an unsigned confirm could move a
    never-polled delivery from `queued` to `received`, after which the sweep
    aged it to `unverifiable` instead of retrying. Receipt of a retraction is
    recorded only from a SIGNED envelope; the transport's confirmed_by keeps
    its pre-existing (unsigned-tolerant) semantics."""
    client, priv = koi_net_client
    ep, fid, r, ev = await _setup_queued(conn, eq)
    resp = await client.post("/koi-net/events/confirm", json={"node_id": PEER_AUTH, "event_ids": [ev]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["confirmed"] == 1, "transport receipt is unchanged"
    assert resp.json().get("applications", {}).get("ignored_unsigned_receipts") == 1
    assert (await _state(conn, fid))["state"] == "queued", "ledger must not move on an unsigned confirm"
    env = sign_envelope({"type": "confirm_events", "event_ids": [ev]}, PEER_AUTH, NODE_A, priv)
    resp = await client.post("/koi-net/events/confirm", json=env)
    assert resp.status_code == 200, resp.text
    assert (await _state(conn, fid))["state"] == "received"


# ═══════════════════════════════════════════════════════════════════════════
# 9. Third review round (session b35cb9db re-review of c41fa77, 2026-09-20) —
#    each written red first against c41fa77
# ═══════════════════════════════════════════════════════════════════════════

# The event shapes an UNSIGNED poll must still be served. The first, second and
# fourth were DROPPED at c41fa77 because the retraction predicate was
# three-valued under SQL NULL semantics (`NULL->>'x' = 'y'` is NULL,
# `FALSE OR NOT NULL` is NULL, and a NULL WHERE term filters the row): re-review
# finding N1. The other three evaluated FALSE (not NULL) at c41fa77 and were
# served; they are positive controls for "ordinary".
_ORDINARY_SHAPES = {
    "contents_null_forget": dict(event_type="FORGET", rid="orn:koi-net.vault-file:{}", contents=None),
    "vault_file_no_domain_no_payload": dict(
        event_type="NEW", rid="orn:koi-net.vault-file:{}",
        contents={"_vault_sync": True, "relative_path": "x.md", "content_hash": "abc"}),
    "domain_event_payload_json_null": dict(
        event_type="NEW", rid="orn:koi-net.entity:{}",
        contents={"_koi_domain": "entity", "payload": None}),
    "knowledge_fact_without_payload_key": dict(
        event_type="UPDATE", rid="orn:personal-koi.knowledge-fact:{}",
        contents={"_koi_domain": "knowledge_fact"}),
    "knowledge_fact_payload_without_retraction": dict(
        event_type="UPDATE", rid="orn:personal-koi.knowledge-fact:{}",
        contents={"_koi_domain": "knowledge_fact", "payload": {"id": "x"}}),
    # `retraction` present but not an object: the recipient's is_retraction_payload
    # treats this as an ordinary upsert, so the transport must too.
    "knowledge_fact_retraction_key_null": dict(
        event_type="UPDATE", rid="orn:personal-koi.knowledge-fact:{}",
        contents={"_koi_domain": "knowledge_fact", "payload": {"id": "x", "retraction": None}}),
    "ordinary_domain_event": dict(
        event_type="NEW", rid="orn:koi-net.entity:{}",
        contents={"_koi_domain": "entity", "payload": {"uri": "x"}}),
}


@pytest.mark.anyio
async def test_r3_unsigned_poll_predicate_is_total_under_sql_null_semantics(conn, eq):
    """Finding N1 (third round). Only a fact-retraction event — domain
    knowledge_fact AND a `retraction` key in its payload — is withheld from an
    unsigned poll. Every other shape, including NULL contents, contents without
    `_koi_domain`/`payload`, a JSON-null payload and a knowledge_fact event with
    no payload key, is an ORDINARY event: served and delivered_to-marked. (Three
    of these shapes were dropped at c41fa77; the others are controls.) The
    retraction stays withheld and unmarked."""
    ids = {}
    for name, kw in _ORDINARY_SHAPES.items():
        kw = dict(kw); kw["rid"] = kw["rid"].format(uuid.uuid4())
        ids[name] = await eq.add(target_node=PEER_AUTH, event_id=str(uuid.uuid4()), **kw)
    retraction = await eq.add(
        event_type="UPDATE", rid=FACT_RID(uuid.uuid4()), target_node=PEER_AUTH, event_id=str(uuid.uuid4()),
        contents={"_koi_domain": "knowledge_fact", "payload": {"id": "x", "retraction": {"valid_to": "t"}}})

    served = {e["event_id"] for e in await eq.poll(PEER_AUTH, authenticated=False)}
    for name, eid in ids.items():
        assert eid in served, f"unsigned poll dropped an ordinary event: {name}"
        marked = await conn.fetchval(
            "SELECT $2 = ANY(delivered_to) FROM koi_net_events WHERE event_id = $1::uuid", eid, PEER_AUTH)
        assert marked is True, f"ordinary event not delivered_to-marked: {name}"
    assert retraction not in served
    assert await conn.fetchval(
        "SELECT $2 = ANY(delivered_to) FROM koi_net_events WHERE event_id = $1::uuid",
        retraction, PEER_AUTH) is False, "the retraction must stay unmarked for the real peer"

    # positive control: the signed poll is handed the retraction
    signed = {e["event_id"] for e in await eq.poll(PEER_AUTH, authenticated=True)}
    assert signed == {retraction}


@pytest.mark.anyio
async def test_r3_unsigned_poll_through_the_router_serves_vault_sync_shapes(conn, eq, koi_net_client):
    """The same rule through the real /koi-net/events/poll handler with an edge
    that declares no scope (rid_types NULL, so poll() applies no type filter):
    a vault-file NEW and a NULL-contents FORGET reach an unsigned poller, the
    retraction does not."""
    client, priv = koi_net_client
    await seed_edge(conn, NODE_A, PEER_AUTH, "APPROVED", None)
    vault_new = await eq.add(event_type="NEW", rid=f"orn:koi-net.vault-file:{uuid.uuid4()}",
                             contents={"_vault_sync": True, "relative_path": "n.md"}, event_id=str(uuid.uuid4()))
    vault_forget = await eq.add(event_type="FORGET", rid=f"orn:koi-net.vault-file:{uuid.uuid4()}",
                                contents=None, event_id=str(uuid.uuid4()))
    ep, fid = await seed_fact(conn)
    r = await _retract(conn, eq, fid)
    retraction = r.deliveries[0]["event_id"]

    resp = await client.post("/koi-net/events/poll", json={"node_id": PEER_AUTH, "limit": 50})
    assert resp.status_code == 200, resp.text
    got = {e["event_id"] for e in resp.json()["events"]}
    assert {vault_new, vault_forget} <= got, got
    assert retraction not in got
    assert (await _state(conn, fid))["state"] == "queued"


@pytest.mark.anyio
async def test_r3_sweep_mutations_are_state_qualified(conn, eq):
    """Finding N3 (third round). plan_requeue is a plain read; apply_requeue used
    to write failed/unverifiable/retrying with `WHERE id = $1`, so a confirm or
    application report that COMMITTED between planning and mutation (another
    connection, READ COMMITTED) was overwritten — applied became failed,
    received became retrying. Every sweep mutation must be qualified on the
    state the plan observed, and a moved row must be skipped, counted and left
    exactly as the later writer left it."""
    # (a) planned RETRY of an expired queued row; an `applied` report lands in between
    ep1, fid1, r1, ev1 = await _setup_queued(conn, eq)
    await _expire(conn, ev1)
    plan1 = [p for p in await fr.plan_requeue(conn, node_rid=NODE_A) if p["fact_id"] == fid1]
    assert plan1 and plan1[0]["action"] == "retry"
    await fr.record_applications(conn, PEER_AUTH, [{"event_id": ev1, "status": "applied"}])
    events_before = len(await _events_for(conn, fid1))
    # (b) planned FAIL (edge revoked) of a queued row; a signed receipt lands in between
    await seed_edge(conn, NODE_A, PEER_AUTH, "APPROVED", ["knowledge_fact"])
    ep2, fid2, r2, ev2 = await _setup_queued(conn, eq)
    await seed_edge(conn, NODE_A, PEER_AUTH, "REVOKED", ["knowledge_fact"])
    plan2 = [p for p in await fr.plan_requeue(conn, node_rid=NODE_A) if p["fact_id"] == fid2]
    assert plan2 and plan2[0]["action"] == "fail"
    await fr.record_receipts(conn, PEER_AUTH, [ev2])
    assert (await _state(conn, fid2))["state"] == "received"
    # (c) planned UNVERIFIABLE of an expired received row; an `applied` report lands in between
    await seed_edge(conn, NODE_A, PEER_AUTH, "APPROVED", ["knowledge_fact"])
    ep3, fid3, r3, ev3 = await _setup_queued(conn, eq)
    await eq.poll(PEER_AUTH, rid_types=["knowledge_fact"])
    await fr.record_receipts(conn, PEER_AUTH, [ev3])
    await _expire(conn, ev3)
    plan3 = [p for p in await fr.plan_requeue(conn, node_rid=NODE_A) if p["fact_id"] == fid3]
    assert plan3 and plan3[0]["action"] == "unverifiable"
    await fr.record_applications(conn, PEER_AUTH, [{"event_id": ev3, "status": "applied"}])

    # (d) in-call POSITIVE CONTROL: an expired queued row nobody touched → retried
    ep4, fid4, r4, ev4 = await _setup_queued(conn, eq)
    await _expire(conn, ev4)
    plan4 = [p for p in await fr.plan_requeue(conn, node_rid=NODE_A) if p["fact_id"] == fid4]
    assert plan4 and plan4[0]["action"] == "retry"

    async with conn.transaction():
        summary = await fr.apply_requeue(conn, plan1 + plan2 + plan3 + plan4, event_queue=eq, node_rid=NODE_A)

    assert summary["skipped_state_changed"] == 3, summary
    assert summary["retried"] == 1 and summary["failed"] == 0 and summary["unverifiable"] == 0
    s1 = await _state(conn, fid1)
    assert s1["state"] == "applied" and s1["event_id"] == ev1 and s1["attempt"] == 1
    assert len(await _events_for(conn, fid1)) == events_before, "no retry event may be queued for a moved row"
    assert (await _state(conn, fid2))["state"] == "received"
    assert (await _state(conn, fid3))["state"] == "applied"
    s4 = await _state(conn, fid4)
    assert s4["state"] == "retrying" and s4["attempt"] == 2 and s4["event_id"] != ev4

    # (e) the SAME stale plan applied again (two sweeps planning from one snapshot —
    # retrying → retrying is state-idempotent, so the qualification must include
    # the attempt): skipped, and exactly one retry event exists for fid4
    async with conn.transaction():
        again = await fr.apply_requeue(conn, plan4, event_queue=eq, node_rid=NODE_A)
    assert again["skipped_state_changed"] == 1 and again["retried"] == 0
    s4b = await _state(conn, fid4)
    assert s4b["attempt"] == 2 and s4b["event_id"] == s4["event_id"]
    assert len(await _events_for(conn, fid4)) == 2   # the original + one retry, not two

    # (f) a plan item without the observed state/attempt is a caller error, not a "moved row"
    bad = dict(plan4[0]); bad.pop("state")
    with pytest.raises(ValueError):
        async with conn.transaction():
            await fr.apply_requeue(conn, [bad], event_queue=eq, node_rid=NODE_A)


class _InterposeAfterSelect:
    """Wraps a connection; after the first fetchrow that reads the deliveries
    row, runs `hook` (the 'other connection' committing in between) before
    returning the — now stale — row to the caller."""

    def __init__(self, conn, hook, marker):
        self._c, self._hook, self._marker, self.fired = conn, hook, marker, False

    def __getattr__(self, name):
        return getattr(self._c, name)

    async def fetchrow(self, sql, *args):
        row = await self._c.fetchrow(sql, *args)
        if self._marker in sql and not self.fired:
            self.fired = True
            await self._hook()
        return row


@pytest.mark.anyio
async def test_r3_record_applications_does_not_overwrite_a_verdict_that_landed_after_its_read(conn, eq):
    """Verifier EQ-1 (third round). record_applications was check-then-write: it
    read d.state, checked TERMINAL_STATES, then UPDATEd `WHERE id = $1`. A sweep
    writing `failed` on another connection between the read and the write was
    overwritten by the report. The UPDATE is now qualified on the state that
    was read; the moved row is left alone and accounted as already_terminal."""
    ep, fid, r, ev = await _setup_queued(conn, eq)

    async def sweep_lands_failed():
        await conn.execute(
            "UPDATE knowledge_fact_retraction_deliveries SET state='failed', "
            "state_reason='attempts_exhausted:5', state_changed_at=NOW() WHERE event_id=$1::uuid", ev)

    proxy = _InterposeAfterSelect(conn, sweep_lands_failed, "FROM knowledge_fact_retraction_deliveries d")
    summary = await fr.record_applications(proxy, PEER_AUTH, [{"event_id": ev, "status": "applied"}])
    assert proxy.fired
    assert summary["already_terminal"] == 1 and summary["applied"] == 0, summary
    s = await _state(conn, fid)
    assert s["state"] == "failed" and s["application"] is None and s["applied_at"] is None


@pytest.mark.anyio
async def test_r3_observer_path_scope_failure_is_logged(conn, eq, caplog):
    """Finding N5 (third round). `record_deliveries` closed an obligation as
    `failed: edge_scope_excluded_at_poll` with no log line, so 'every failed is
    a WARNING' held for the sweep path only. The observer path must name the
    fact, the peer and the reason at WARNING too."""
    import logging
    ep, fid, r, ev = await _setup_queued(conn, eq)
    with caplog.at_level(logging.WARNING, logger="api.fact_retraction"):
        got = await eq.poll(PEER_AUTH, rid_types=["Person"])   # narrowed → excluded at poll
    assert got == []
    assert (await _state(conn, fid))["state"] == "failed"
    msgs = [rec.getMessage() for rec in caplog.records if "delivery_failed" in rec.getMessage()]
    assert msgs, [rec.getMessage() for rec in caplog.records]
    assert fid in msgs[0] and PEER_AUTH in msgs[0] and "edge_scope_excluded_at_poll" in msgs[0]


_HttpxAsyncClient = AsyncClient   # the real httpx client; the stand-in below shadows the name


class _AsgiHttpx:
    """Stands in for `httpx` inside api.koi_poller: every POST goes to the real
    router app over ASGI, so KOIPoller's own signing and payload construction
    are exercised end to end."""

    class ConnectError(Exception):
        pass

    def __init__(self, app):
        self.app = app
        self.posts = []
        outer = self

        class _Resp:
            def __init__(self, resp):
                self.status_code = resp.status_code
                self.text = resp.text
                self._resp = resp

            def json(self):
                return self._resp.json()

        class AsyncClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def post(self, url, json=None):
                outer.posts.append((url, json))
                path = url.split("://", 1)[-1].split("/", 1)[1]
                async with _HttpxAsyncClient(transport=ASGITransport(app=outer.app), base_url="http://pub") as c:
                    return _Resp(await c.post("/" + path, json=json))

        self.AsyncClient = AsyncClient


@pytest.mark.anyio
async def test_r3_signed_pending_confirmation_crosses_the_real_endpoint(conn, eq, koi_net_client, monkeypatch):
    """Seam test (re-review critic gap G1). The recipient's `pending` report —
    produced by the real handler for a fact it does not hold — travels inside
    the REAL KOIPoller confirm (signed with the peer's real private key) into
    the REAL /koi-net/events/confirm handler, and lands on the publisher as
    deliveries.state='pending' with applied_at NULL: non-terminal, never
    represented as applied. A later signed already_tombstoned report with the
    same valid_to then advances it to applied through the same endpoint."""
    import api.koi_poller as koi_poller_module
    from api.koi_poller import KOIPoller
    client, priv = koi_net_client
    ep, fid, r, ev = await _setup_queued(conn, eq)
    # Model the RECIPIENT as not holding the fact (one database, two roles).
    await conn.execute("DELETE FROM knowledge_facts WHERE id = $1::uuid", fid)

    app = FastAPI()
    app.include_router(knr.koi_net_router, prefix="/koi-net")
    fake = _AsgiHttpx(app)
    monkeypatch.setattr(koi_poller_module, "httpx", fake)
    monkeypatch.setattr(koi_poller_module, "REQUIRE_SIGNED_RESPONSES", False)
    monkeypatch.setenv("KOI_FEDERATE_KNOWLEDGE", "true")

    poller = KOIPoller(SingleConnPool(conn), PEER_AUTH, private_key=priv)
    await poller._poll_peer(source_node=NODE_A, base_url="http://pub", rid_types=None)

    confirms = [body for url, body in fake.posts if url.endswith("/events/confirm")]
    assert len(confirms) == 1, fake.posts
    env = confirms[0]
    assert env.get("signature") and env["source_node"] == PEER_AUTH, "the poller must sign the confirm"
    apps = env["payload"]["applications"]
    assert len(apps) == 1 and apps[0]["event_id"] == ev and apps[0]["status"] == "pending"

    s = await _state(conn, fid)
    assert s["state"] == "pending", s
    assert s["applied_at"] is None and s["received_at"] is not None
    assert s["state_reason"] == "peer_recorded_pending_tombstone_fact_absent"
    status = await fr.tombstone_status(conn, uuid.UUID(fid))
    d = [d for d in status["deliveries"] if d["target_node"] == PEER_AUTH][0]
    assert d["state"] == "pending" and d["applied_at"] is None

    # non-terminal: a later genuine report through the same signed endpoint advances it
    late = sign_envelope({"type": "confirm_events", "event_ids": [ev],
                          "applications": [{"event_id": ev, "status": "already_tombstoned",
                                            "valid_to": r.valid_to, "valid_to_matches": True}]},
                         PEER_AUTH, NODE_A, priv)
    resp = await client.post("/koi-net/events/confirm", json=late)
    assert resp.status_code == 200, resp.text
    s = await _state(conn, fid)
    assert s["state"] == "applied" and s["applied_at"] is not None
