"""scripts/audit_fact_retractions.py — the #67 historical audit + dry-run repair plan.

What is pinned here, and why each pin exists:

  * The audit is READ-ONLY by mechanism, not by convention: the connection
    `main()` opens has `default_transaction_read_only = on`, so a write inside
    it RAISES (`ReadOnlySQLTransactionError`). Proved by attempting one.
  * `delivered_to` is never evidence. A live copy whose only mark for a peer
    is `delivered_to` classifies as `unverifiable`, never `possibly_live` —
    the incident's "delivery 4/4" was 1 hand-over + 3 exclusion marks
    (tests/test_fact_retraction_boundary.py::test_pin_delivered_to_marks_scope_excluded_events).
  * `confirmed_by` is receipt. A confirmed exact tombstone is
    `tombstone_confirmed`, with `application_proven=False` — never `applied`.
    Only the migration-127 ledger can say `applied`.
  * The plan line carries the committed `valid_to` exactly — `.isoformat()`
    of the DB value, microseconds included — never a reconstruction.
  * The scope rule is `api.fact_retraction.scope_admits`, not a copy: a peer
    whose approved edge lacks `knowledge_fact` gets `unauthorized` and no
    plan line, whatever it holds.
  * `--apply` does not exist as a working path in this branch (exit 2).
  * Positive control: a fact with `valid_to IS NULL` never appears.

Isolation: scratch DB (tests/conftest.py pins POSTGRES_URL to
personal_koi_test), one transaction per test, rolled back. `audit()` is
called in-process on that connection; `main()` is exercised only against
the scratch DSN (read-only) and against an unreachable DSN (exit 3).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

import asyncpg
import pytest

from tests.fact_retraction_testkit import (
    DB_URL, NODE_A, NODE_B, PEER_AUTH, PEER_NARROW, PEER_REVOKED,
    apply_ledger_migration, close_scratch_conn, episode_payload, fact_payload,
    open_scratch_conn, retraction_payload, seed_edges, seed_fact,
)

from scripts import audit_fact_retractions as audit_mod

# A committed-looking valid_to with a non-zero microsecond field, so an
# equality assertion on `.isoformat()` cannot pass by truncation.
VALID_TO = datetime(2026, 9, 14, 15, 13, 7, 924484, tzinfo=timezone.utc)
OTHER_VALID_TO = datetime(2026, 9, 14, 15, 13, 8, 111111, tzinfo=timezone.utc)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def conn():
    c, tx = await open_scratch_conn()
    yield c
    await close_scratch_conn(c, tx)


async def seed_event(
    conn, *, domain: str, event_type: str, rid: str, payload: dict,
    source_node: str = NODE_A, target_node: Optional[str] = None,
    delivered_to: Sequence[str] = (), confirmed_by: Sequence[str] = (),
    expires_in_hours: float = 24.0, queued_at: Optional[datetime] = None,
) -> str:
    """One koi_net_events row, written directly, with the transport marks set."""
    event_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO koi_net_events
            (event_id, event_type, rid, contents, source_node, target_node,
             delivered_to, confirmed_by, queued_at, expires_at)
        VALUES ($1::uuid, $2, $3, $4::jsonb, $5, $6, $7::text[], $8::text[],
                COALESCE($9, NOW()), NOW() + ($10 || ' hours')::interval)
        """,
        event_id, event_type, rid, json.dumps({"_koi_domain": domain, "payload": payload}),
        source_node, target_node, list(delivered_to), list(confirmed_by), queued_at,
        str(expires_in_hours),
    )
    return event_id


def episode_rid(ep: str) -> str:
    return f"orn:personal-koi.knowledge-episode:{ep}"


def fact_rid(fid: str) -> str:
    return f"orn:personal-koi.knowledge-fact:{fid}"


async def seed_retracted_fact(conn, valid_to: datetime = VALID_TO):
    """A retracted fact whose valid_to is a known microsecond value. Returns (ep, fid, db_valid_to)."""
    ep, fid = await seed_fact(conn, valid_to=valid_to)
    db_valid_to = await conn.fetchval(
        "SELECT valid_to FROM knowledge_facts WHERE id = $1::uuid", fid)
    assert db_valid_to == valid_to
    return ep, fid, db_valid_to


async def seed_live_copy(conn, ep: str, fid: str, *, delivered_to=(), confirmed_by=()):
    """The original knowledge_episode NEW that carried the fact live (valid_to null)."""
    return await seed_event(
        conn, domain="knowledge_episode", event_type="NEW", rid=episode_rid(ep),
        payload=episode_payload(ep, [fact_payload(fid, ep, valid_to=None)]),
        delivered_to=delivered_to, confirmed_by=confirmed_by,
    )


async def seed_episode_tombstone(conn, ep: str, fid: str, valid_to_iso: str, *,
                                 delivered_to=(), confirmed_by=()):
    """The incident shape: a manual knowledge_episode UPDATE carrying the tombstone."""
    return await seed_event(
        conn, domain="knowledge_episode", event_type="UPDATE", rid=episode_rid(ep),
        payload=episode_payload(ep, [fact_payload(fid, ep, valid_to=valid_to_iso)]),
        delivered_to=delivered_to, confirmed_by=confirmed_by,
    )


def peer_row(report: dict, fid: str, peer: str) -> dict:
    facts = [f for f in report["facts"] if f["fact_id"] == fid]
    assert len(facts) == 1, f"fact {fid} appears {len(facts)} times"
    peers = [p for p in facts[0]["peers"] if p["peer"] == peer]
    assert len(peers) == 1, f"peer {peer} appears {len(peers)} times for {fid}"
    return peers[0]


def plan_for(report: dict, fid: str, peer: str) -> list:
    return [p for p in report["plan"] if p["fact_id"] == fid and p["peer"] == peer]


# ═══════════════════════════════════════════════════════════════════════════
# Read-only by mechanism
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_read_only_connection_refuses_a_write():
    """The connection `main()` uses raises on any write; nothing is left behind."""
    c = await audit_mod.connect_read_only(DB_URL)
    try:
        assert await c.fetchval("SELECT current_database()") != "personal_koi"
        async with c.transaction():
            assert await c.fetchval("SHOW transaction_read_only") == "on"
            with pytest.raises(asyncpg.exceptions.ReadOnlySQLTransactionError):
                await c.execute(
                    """
                    INSERT INTO koi_net_edges (edge_rid, source_node, target_node, edge_type, status)
                    VALUES ('orn:koi-net.edge:audit-readonly-probe', 'a', 'b', 'POLL', 'PROPOSED')
                    """)
    finally:
        await c.close()
    # And the probe never landed (belt: the transaction above was aborted by the error).
    probe = await asyncpg.connect(DB_URL)
    try:
        assert await probe.fetchval(
            "SELECT 1 FROM koi_net_edges WHERE edge_rid = 'orn:koi-net.edge:audit-readonly-probe'") is None
    finally:
        await probe.close()


def test_read_only_set_statement_is_the_one_documented():
    """The exact SET the docs quote. A rename here must update docs/federation/fact-retractions.md."""
    assert audit_mod.READ_ONLY_SQL == "SET default_transaction_read_only = on"


# ═══════════════════════════════════════════════════════════════════════════
# Classification
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_positive_control_live_fact_never_appears(conn):
    """A fact with valid_to IS NULL is not "retracted" and must not be audited."""
    await seed_edges(conn)
    _, live_fid = await seed_fact(conn, valid_to=None)
    _, ret_fid, _ = await seed_retracted_fact(conn)
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[live_fid, ret_fid])
    ids = {f["fact_id"] for f in report["facts"]}
    assert ret_fid in ids
    assert live_fid not in ids, "a live fact was reported as retracted"


@pytest.mark.anyio
async def test_future_valid_to_is_audited_but_labelled_as_a_validity_interval(conn):
    """valid_to > NOW() is a validity interval set at creation, not a retraction.

    It is hidden by retrieval exactly like a retraction and audited the same way,
    but flagged so the operator never counts it as one. Live 2026-09-15: 3 of 4,188.
    """
    await seed_edges(conn)
    future = datetime.now(timezone.utc) + timedelta(days=10)
    _, fid, _ = await seed_retracted_fact(conn, future)
    _, past_fid, _ = await seed_retracted_fact(conn, VALID_TO)
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid, past_fid])
    flags = {f["fact_id"]: f["valid_to_in_future"] for f in report["facts"]}
    assert flags == {fid: True, past_fid: False}
    assert report["summary"]["selected_with_future_valid_to"] == 1
    assert "validity interval, not a retraction" in audit_mod.render_human(report)


@pytest.mark.anyio
async def test_never_sent_when_no_history(conn):
    """No carrying event at all → every peer is never_sent; the plan is empty."""
    await seed_edges(conn)
    _, fid, _ = await seed_retracted_fact(conn)
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid])
    assert report["ledger_available"] is False
    fact = report["facts"][0]
    assert fact["history"] == "none"
    assert fact["events"] == []
    peers = {p["peer"]: p["classification"] for p in fact["peers"]}
    # every outbound edge target is examined, whatever the edge status
    assert peers == {PEER_AUTH: "never_sent", PEER_NARROW: "never_sent", PEER_REVOKED: "never_sent"}
    assert report["plan"] == []
    assert report["summary"]["outstanding"] is False


@pytest.mark.anyio
async def test_possibly_live_plan_carries_the_exact_valid_to(conn):
    """Live copy CONFIRMED by an authorized peer, no tombstone → possibly_live + one plan line.

    The plan's valid_to is `.isoformat()` of the DB value, microseconds and all.
    """
    await seed_edges(conn)
    ep, fid, db_valid_to = await seed_retracted_fact(conn)
    await seed_live_copy(conn, ep, fid, delivered_to=[PEER_AUTH], confirmed_by=[PEER_AUTH])

    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid])
    row = peer_row(report, fid, PEER_AUTH)
    assert row["classification"] == "possibly_live"
    assert row["live_copy_confirmed"] is True
    assert row["scope_admits_now"] is True
    assert row["application_proven"] is False

    lines = plan_for(report, fid, PEER_AUTH)
    assert len(lines) == 1
    line = lines[0]
    assert line["action"] == "would_queue"
    assert line["valid_to"] == db_valid_to.isoformat()
    assert line["valid_to"] == "2026-09-14T15:13:07.924484+00:00"
    assert line["event_type"] == "UPDATE" and line["domain"] == "knowledge_fact"
    assert db_valid_to.isoformat() in line["text"]
    assert report["summary"]["outstanding"] is True
    assert report["summary"]["by_classification"]["possibly_live"] == 1


@pytest.mark.anyio
async def test_tombstone_confirmed_when_exact_valid_to_confirmed(conn):
    """The incident shape for nuc-personal: an episode UPDATE carrying the exact valid_to, confirmed.

    Receipt only: application_proven stays False (confirm() is receipt).
    """
    await seed_edges(conn)
    ep, fid, db_valid_to = await seed_retracted_fact(conn)
    await seed_live_copy(conn, ep, fid, delivered_to=[PEER_AUTH], confirmed_by=[PEER_AUTH])
    await seed_episode_tombstone(conn, ep, fid, db_valid_to.isoformat(),
                                 delivered_to=[PEER_AUTH], confirmed_by=[PEER_AUTH])

    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid])
    row = peer_row(report, fid, PEER_AUTH)
    assert row["classification"] == "tombstone_confirmed"
    assert row["tombstone_confirmed"] is True
    assert row["tombstone_valid_to_mismatch"] is False
    assert row["application_proven"] is False
    assert plan_for(report, fid, PEER_AUTH) == []
    fact = report["facts"][0]
    assert fact["history"] == "known"
    kinds = sorted((e["event_type"], e["carried"]) for e in fact["events"])
    assert kinds == [("NEW", "live_copy"), ("UPDATE", "tombstone")]


@pytest.mark.anyio
async def test_tombstone_with_different_valid_to_is_flagged_not_confirmed(conn):
    """A confirmed tombstone carrying a DIFFERENT valid_to is a mismatch, not a confirmation."""
    await seed_edges(conn)
    ep, fid, db_valid_to = await seed_retracted_fact(conn)
    await seed_live_copy(conn, ep, fid, delivered_to=[PEER_AUTH], confirmed_by=[PEER_AUTH])
    await seed_episode_tombstone(conn, ep, fid, OTHER_VALID_TO.isoformat(),
                                 delivered_to=[PEER_AUTH], confirmed_by=[PEER_AUTH])

    report = await audit_mod.audit(conn, fact_ids=[fid], node_rid=NODE_A)
    row = peer_row(report, fid, PEER_AUTH)
    assert row["tombstone_valid_to_mismatch"] is True
    assert row["tombstone_confirmed"] is False
    assert row["classification"] == "tombstone_valid_to_mismatch"
    fact = report["facts"][0]
    mism = [e for e in fact["events"] if e["carried"] == "tombstone"]
    assert len(mism) == 1 and mism[0]["carried_valid_to"] == OTHER_VALID_TO.isoformat()
    assert mism[0]["valid_to_matches_local"] is False
    # the repair would carry the committed value, not the mismatching one
    lines = plan_for(report, fid, PEER_AUTH)
    assert len(lines) == 1 and lines[0]["valid_to"] == db_valid_to.isoformat()


@pytest.mark.anyio
async def test_delivered_to_only_is_unverifiable_not_possibly_live(conn):
    """delivered_to without confirmed_by is NOT evidence the peer saw the live copy."""
    await seed_edges(conn)
    ep, fid, db_valid_to = await seed_retracted_fact(conn)
    await seed_live_copy(conn, ep, fid, delivered_to=[PEER_AUTH], confirmed_by=[])

    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid])
    row = peer_row(report, fid, PEER_AUTH)
    assert row["classification"] == "unverifiable"
    assert row["classification"] != "possibly_live"
    assert row["live_copy_confirmed"] is False
    assert row["live_copy_delivered_to_only"] is True
    # unverifiable + scope admits → the dry run would still send the tombstone
    lines = plan_for(report, fid, PEER_AUTH)
    assert len(lines) == 1 and lines[0]["valid_to"] == db_valid_to.isoformat()


@pytest.mark.anyio
async def test_incident_shape_excluded_peer_is_unverifiable_not_tombstone_unconfirmed(conn):
    """The incident's three excluded peers: delivered_to marks on BOTH the live copy and the
    tombstone, confirmed_by on neither. Two non-evidence marks do not add up to evidence a
    tombstone went out — the peer is `unverifiable` (then `unauthorized`, since its scope
    admits nothing), never `tombstone_unconfirmed`."""
    await seed_edges(conn)
    ep, fid, db_valid_to = await seed_retracted_fact(conn)
    await seed_live_copy(conn, ep, fid, delivered_to=[PEER_AUTH, PEER_NARROW], confirmed_by=[PEER_AUTH])
    await seed_episode_tombstone(conn, ep, fid, db_valid_to.isoformat(),
                                 delivered_to=[PEER_AUTH, PEER_NARROW], confirmed_by=[PEER_AUTH])
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid])
    narrow = peer_row(report, fid, PEER_NARROW)
    assert narrow["classification"] == "unauthorized"
    assert narrow["evidence_class"] == "unverifiable"
    assert narrow["tombstone_delivered_to_only"] is True
    assert narrow["live_copy_delivered_to_only"] is True
    assert peer_row(report, fid, PEER_AUTH)["classification"] == "tombstone_confirmed"


@pytest.mark.anyio
async def test_tombstone_delivered_to_only_counts_when_live_copy_was_confirmed(conn):
    """A peer that CONFIRMED the live copy and holds only a delivered_to mark on the exact
    tombstone: the hand-over is plausible and unconfirmed — `tombstone_unconfirmed`, planned."""
    await seed_edges(conn)
    ep, fid, db_valid_to = await seed_retracted_fact(conn)
    await seed_live_copy(conn, ep, fid, delivered_to=[PEER_AUTH], confirmed_by=[PEER_AUTH])
    await seed_episode_tombstone(conn, ep, fid, db_valid_to.isoformat(),
                                 delivered_to=[PEER_AUTH], confirmed_by=[])
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid])
    row = peer_row(report, fid, PEER_AUTH)
    assert row["classification"] == "tombstone_unconfirmed"
    assert row["tombstone_in_flight"] is False
    lines = plan_for(report, fid, PEER_AUTH)
    assert len(lines) == 1 and lines[0]["action"] == "would_queue"


@pytest.mark.anyio
async def test_unauthorized_when_scope_no_longer_admits(conn):
    """Confirmed live copy on a peer whose approved edge lacks knowledge_fact → no plan line."""
    await seed_edges(conn)
    ep, fid, _ = await seed_retracted_fact(conn)
    await seed_live_copy(conn, ep, fid, delivered_to=[PEER_NARROW], confirmed_by=[PEER_NARROW])

    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid])
    row = peer_row(report, fid, PEER_NARROW)
    assert row["classification"] == "unauthorized"
    assert row["evidence_class"] == "possibly_live"
    assert row["scope_admits_now"] is False
    assert row["edge_status"] == "APPROVED"
    assert plan_for(report, fid, PEER_NARROW) == []
    blocked = [b for b in report["blocked"] if b["fact_id"] == fid and b["peer"] == PEER_NARROW]
    assert len(blocked) == 1 and "operator decision" in blocked[0]["text"]
    assert report["summary"]["outstanding"] is True


@pytest.mark.anyio
async def test_revoked_edge_peer_with_confirmed_live_copy_is_unauthorized(conn):
    """A REVOKED edge admits nothing now, whatever its rid_types say."""
    await seed_edges(conn)
    ep, fid, _ = await seed_retracted_fact(conn)
    await seed_live_copy(conn, ep, fid, delivered_to=[PEER_REVOKED], confirmed_by=[PEER_REVOKED])
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid])
    row = peer_row(report, fid, PEER_REVOKED)
    assert row["classification"] == "unauthorized"
    assert row["edge_status"] == "REVOKED"
    assert row["scope_admits_now"] is False
    assert plan_for(report, fid, PEER_REVOKED) == []


@pytest.mark.anyio
async def test_peer_seen_only_in_transport_marks_without_any_edge(conn):
    """A node in confirmed_by with NO outbound edge from this node is still examined — and cannot be sent to."""
    await seed_edges(conn)
    ep, fid, _ = await seed_retracted_fact(conn)
    await seed_live_copy(conn, ep, fid, delivered_to=[NODE_B], confirmed_by=[NODE_B])
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid])
    row = peer_row(report, fid, NODE_B)
    assert row["edge_status"] is None
    assert row["scope_admits_now"] is False
    assert row["classification"] == "unauthorized"
    assert row["evidence_class"] == "possibly_live"


@pytest.mark.anyio
async def test_unicast_tombstone_in_flight_waits_and_expired_would_requeue(conn):
    """An exact unicast knowledge_fact UPDATE that is still pollable is `wait`; an expired one is `would_queue`."""
    await seed_edges(conn)
    ep, fid, db_valid_to = await seed_retracted_fact(conn)
    await seed_live_copy(conn, ep, fid, delivered_to=[PEER_AUTH], confirmed_by=[PEER_AUTH])
    live_event = await seed_event(
        conn, domain="knowledge_fact", event_type="UPDATE", rid=fact_rid(fid),
        payload=retraction_payload(fid, ep, valid_to=db_valid_to.isoformat()),
        target_node=PEER_AUTH, expires_in_hours=72,
    )
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid])
    row = peer_row(report, fid, PEER_AUTH)
    assert row["classification"] == "tombstone_unconfirmed"
    assert row["tombstone_in_flight"] is True
    lines = plan_for(report, fid, PEER_AUTH)
    assert len(lines) == 1 and lines[0]["action"] == "wait"
    assert live_event in lines[0]["text"]

    # Expire it (still inside the rolled-back transaction) → the obligation is open again.
    await conn.execute(
        "UPDATE koi_net_events SET expires_at = NOW() - interval '1 hour' WHERE event_id = $1::uuid",
        live_event)
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid])
    row = peer_row(report, fid, PEER_AUTH)
    assert row["classification"] == "tombstone_unconfirmed"
    assert row["tombstone_in_flight"] is False
    lines = plan_for(report, fid, PEER_AUTH)
    assert len(lines) == 1 and lines[0]["action"] == "would_queue"
    assert lines[0]["valid_to"] == db_valid_to.isoformat()


@pytest.mark.anyio
async def test_history_unknown_when_fact_predates_the_oldest_event(conn):
    """Zero carrying rows AND the fact is older than every koi_net_events row → history_unknown."""
    await seed_edges(conn)
    ep, fid, _ = await seed_retracted_fact(conn)
    await conn.execute(
        "UPDATE knowledge_facts SET created_at = NOW() - interval '30 days' WHERE id = $1::uuid", fid)
    # an unrelated event, so the table's oldest row is newer than the fact
    await seed_event(conn, domain="entity", event_type="NEW", rid="orn:personal-koi.entity:unrelated",
                     payload={"id": "unrelated"})
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid])
    fact = report["facts"][0]
    assert fact["history"] == "unknown"
    assert {p["classification"] for p in fact["peers"]} == {"history_unknown"}
    assert report["plan"] == []
    assert report["summary"]["outstanding"] is True
    assert report["summary"]["by_classification"]["history_unknown"] == 3


# ═══════════════════════════════════════════════════════════════════════════
# Ledger (migration 127) integration
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_ledger_applied_overrides_transport_evidence(conn):
    """A deliveries row in state `applied` is the recipient's report — the only source of `applied`."""
    await apply_ledger_migration(conn)
    await seed_edges(conn)
    ep, fid, db_valid_to = await seed_retracted_fact(conn)
    await seed_live_copy(conn, ep, fid, delivered_to=[PEER_AUTH], confirmed_by=[PEER_AUTH])
    event_id = str(uuid.uuid4())
    retraction_id = await conn.fetchval(
        """
        INSERT INTO knowledge_fact_retractions
            (fact_id, valid_to, origin_node, episode_id, fact_snapshot, applied_at)
        VALUES ($1::uuid, $2, $3, $4::uuid, '{}'::jsonb, NOW()) RETURNING id
        """,
        fid, db_valid_to, NODE_A, ep)
    await conn.execute(
        """
        INSERT INTO knowledge_fact_retraction_deliveries
            (retraction_id, target_node, event_id, attempt, state, state_reason, application, applied_at)
        VALUES ($1, $2, $3::uuid, 1, 'applied', 'peer_reported_applied',
                '{"event_id": "x", "status": "applied"}'::jsonb, NOW())
        """,
        retraction_id, PEER_AUTH, event_id)

    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid])
    assert report["ledger_available"] is True
    row = peer_row(report, fid, PEER_AUTH)
    assert row["classification"] == "applied"
    assert row["application_proven"] is True
    assert row["ledger"]["state"] == "applied"
    assert row["ledger"]["state_reason"] == "peer_reported_applied"
    assert row["ledger"]["application"] == {"event_id": "x", "status": "applied"}
    assert plan_for(report, fid, PEER_AUTH) == []
    fact = report["facts"][0]
    assert fact["ledger"]["retractions"][0]["origin_node"] == NODE_A
    assert fact["ledger"]["retractions"][0]["valid_to"] == db_valid_to.isoformat()
    assert report["summary"]["outstanding"] is False


@pytest.mark.anyio
async def test_ledger_unverifiable_and_rejected_states_pass_through(conn):
    await apply_ledger_migration(conn)
    await seed_edges(conn)
    ep, fid, db_valid_to = await seed_retracted_fact(conn)
    retraction_id = await conn.fetchval(
        """
        INSERT INTO knowledge_fact_retractions (fact_id, valid_to, origin_node, fact_snapshot, applied_at)
        VALUES ($1::uuid, $2, $3, '{}'::jsonb, NOW()) RETURNING id
        """,
        fid, db_valid_to, NODE_A)
    for peer, state in ((PEER_AUTH, "unverifiable"), (PEER_NARROW, "rejected")):
        await conn.execute(
            """
            INSERT INTO knowledge_fact_retraction_deliveries
                (retraction_id, target_node, event_id, attempt, state, state_reason)
            VALUES ($1, $2, $3::uuid, 1, $4, 'test')
            """,
            retraction_id, peer, str(uuid.uuid4()), state)
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid])
    assert peer_row(report, fid, PEER_AUTH)["classification"] == "unverifiable"
    assert peer_row(report, fid, PEER_NARROW)["classification"] == "rejected"
    # unverifiable + scope admits → in the plan; rejected is terminal → not
    assert len(plan_for(report, fid, PEER_AUTH)) == 1
    assert plan_for(report, fid, PEER_NARROW) == []


@pytest.mark.anyio
async def test_ledger_absent_is_reported_and_classification_still_works(conn):
    await seed_edges(conn)
    ep, fid, _ = await seed_retracted_fact(conn)
    await seed_live_copy(conn, ep, fid, delivered_to=[PEER_AUTH], confirmed_by=[PEER_AUTH])
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid])
    assert report["ledger_available"] is False
    assert report["facts"][0]["ledger"] is None
    assert peer_row(report, fid, PEER_AUTH)["ledger"] is None
    assert peer_row(report, fid, PEER_AUTH)["classification"] == "possibly_live"
    text = audit_mod.render_human(report)
    assert "ledger: absent (migration 127 not applied)" in text


# ═══════════════════════════════════════════════════════════════════════════
# Selection, population counts, output
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_since_and_fact_id_filters_and_population_counts(conn):
    await seed_edges(conn)
    _, older, _ = await seed_retracted_fact(conn, VALID_TO)
    ep2, newer, _ = await seed_retracted_fact(conn, OTHER_VALID_TO)
    await seed_live_copy(conn, ep2, newer, delivered_to=[PEER_AUTH], confirmed_by=[PEER_AUTH])

    both = await audit_mod.audit(conn, NODE_A, fact_ids=[older, newer])
    assert {f["fact_id"] for f in both["facts"]} == {older, newer}
    since = await audit_mod.audit(conn, NODE_A, fact_ids=[older, newer], since=OTHER_VALID_TO)
    assert {f["fact_id"] for f in since["facts"]} == {newer}
    one = await audit_mod.audit(conn, NODE_A, fact_ids=[older])
    assert {f["fact_id"] for f in one["facts"]} == {older}
    limited = await audit_mod.audit(conn, NODE_A, fact_ids=[older, newer], limit=1)
    assert len(limited["facts"]) == 1

    # Population counts are over ALL locally retracted facts, not the selection.
    pop = one["summary"]["population"]
    assert pop["retracted_total"] >= 2
    assert pop["retracted_with_history"] >= 1
    assert pop["retracted_with_history"] <= pop["retracted_total"]
    assert one["summary"]["selected"] == 1


@pytest.mark.anyio
async def test_node_rid_inference_uses_domain_event_majority(conn):
    await seed_event(conn, domain="entity", event_type="NEW", rid="orn:personal-koi.entity:a",
                     payload={"id": "a"}, source_node=NODE_A)
    await seed_event(conn, domain="entity", event_type="NEW", rid="orn:personal-koi.entity:b",
                     payload={"id": "b"}, source_node=NODE_A)
    await seed_event(conn, domain="entity", event_type="NEW", rid="orn:personal-koi.entity:c",
                     payload={"id": "c"}, source_node=NODE_B)
    inferred = await audit_mod.infer_node_rid(conn)
    assert inferred["node_rid"] == NODE_A
    assert inferred["rows_from_node"] >= 2
    assert inferred["rows_total"] >= inferred["rows_from_node"] + 1
    assert "majority" in inferred["how"]


@pytest.mark.anyio
async def test_json_output_round_trips(conn):
    await seed_edges(conn)
    ep, fid, _ = await seed_retracted_fact(conn)
    await seed_live_copy(conn, ep, fid, delivered_to=[PEER_AUTH], confirmed_by=[PEER_AUTH])
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid])
    text = audit_mod.render_json(report)
    back = json.loads(text)
    assert back == json.loads(json.dumps(back))
    assert back["node_rid"] == NODE_A
    assert set(back) >= {"node_rid", "ledger_available", "facts", "plan", "blocked", "summary"}
    assert back["plan"][0]["valid_to"] == "2026-09-14T15:13:07.924484+00:00"


@pytest.mark.anyio
async def test_human_output_labels_delivered_to_as_not_evidence(conn):
    await seed_edges(conn)
    ep, fid, _ = await seed_retracted_fact(conn)
    await seed_live_copy(conn, ep, fid, delivered_to=[PEER_AUTH, PEER_NARROW], confirmed_by=[PEER_AUTH])
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid])
    text = audit_mod.render_human(report)
    assert "delivered_to (includes scope-exclusions; not evidence)" in text
    assert "confirmed_by (receipt only)" in text
    assert "would queue knowledge_fact UPDATE retraction event to" in text


# ═══════════════════════════════════════════════════════════════════════════
# main(): argv, exit codes
# ═══════════════════════════════════════════════════════════════════════════

def test_apply_is_refused_with_exit_2(capsys):
    rc = audit_mod.main(["--apply", "--node-rid", NODE_A, "--dsn", DB_URL])
    assert rc == 2
    out = capsys.readouterr()
    assert "not implemented" in (out.out + out.err)
    assert "#67" in (out.out + out.err)


def test_unreachable_dsn_is_exit_3(capsys):
    rc = audit_mod.main(["--node-rid", NODE_A, "--dsn", "postgresql://darrenzal:@127.0.0.1:1/personal_koi_test"])
    assert rc == 3


def test_main_runs_read_only_against_the_scratch_db(capsys):
    """End to end through main(): connects read-only, emits JSON, exits 0 or 1 (never 2/3).

    The scratch database holds no committed retracted facts, so the report is
    structurally complete but empty; other tests' rows are in rolled-back
    transactions and invisible here.
    """
    rc = audit_mod.main(["--node-rid", NODE_A, "--dsn", DB_URL, "--json", "--limit", "5"])
    assert rc in (0, 1)
    out = capsys.readouterr().out
    body = json.loads(out)
    assert body["node_rid"] == NODE_A
    assert body["read_only"] is True
    assert isinstance(body["facts"], list)
    assert body["summary"]["selected"] == len(body["facts"])


# ═══════════════════════════════════════════════════════════════════════════
# 2026-09-16 adversarial review — each written red first
# ═══════════════════════════════════════════════════════════════════════════

async def _seed_ledger_delivery(conn, fid, db_valid_to, peer, state, reason):
    retraction_id = await conn.fetchval(
        """
        INSERT INTO knowledge_fact_retractions (fact_id, valid_to, origin_node, fact_snapshot, applied_at)
        VALUES ($1::uuid, $2, $3, '{}'::jsonb, NOW())
        ON CONFLICT (fact_id, valid_to, origin_node) DO UPDATE SET applied_at = knowledge_fact_retractions.applied_at
        RETURNING id
        """,
        fid, db_valid_to, NODE_A)
    await conn.execute(
        """
        INSERT INTO knowledge_fact_retraction_deliveries
            (retraction_id, target_node, event_id, attempt, state, state_reason)
        VALUES ($1, $2, $3::uuid, 1, $4, $5)
        """,
        retraction_id, peer, str(uuid.uuid4()), state, reason)


@pytest.mark.anyio
async def test_review_post_scoping_exclusion_mark_is_scope_excluded_not_unauthorized(conn):
    """Finding 2. The incident's three narrow peers carry delivered_to marks on
    events queued in 2026-09 — after per-edge scoping (2c497f0) and after their
    edges last changed — and none of their approved scopes admits
    knowledge_episode. By the boundary pin those marks are exclusions: the peer
    was never handed the fact. `unauthorized` ("may hold it; policy forbids
    telling it") overstated that 729 times on live."""
    await seed_edges(conn)
    # the narrow edge was last changed BEFORE the event
    await conn.execute(
        "UPDATE koi_net_edges SET updated_at = '2026-08-30T00:00:00+00:00' WHERE target_node = $1",
        PEER_NARROW)
    ep, fid, _ = await seed_retracted_fact(conn)
    await seed_live_copy(conn, ep, fid, delivered_to=[PEER_NARROW])   # mark only, 2026-09 (NOW)
    # The floor is explicit since the second review (finding 7); the
    # no-floor case is test_r2_scope_excluded_needs_an_explicit_enforcement_floor.
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid],
                                   scope_enforced_since=audit_mod.SCOPE_ENFORCEMENT_COMMIT_TIME)
    row = peer_row(report, fid, PEER_NARROW)
    assert row["classification"] == "scope_excluded", row["note"]
    assert row["evidence_class"] is None
    assert plan_for(report, fid, PEER_NARROW) == []
    assert not [b for b in report["blocked"] if b["peer"] == PEER_NARROW]
    assert report["summary"]["outstanding"] is False


@pytest.mark.anyio
async def test_review_mark_from_before_scoping_or_before_edge_change_stays_unverifiable(conn):
    """The conservative side of the same rule: a mark from before the scoping
    floor, or from before the peer's edge last changed, cannot be reclassified
    from the CURRENT scope — it stays unverifiable → unauthorized."""
    await seed_edges(conn)
    ep, fid, _ = await seed_retracted_fact(conn)
    # (a) queued before 2c497f0
    await seed_live_copy_at(conn, ep, fid, delivered_to=[PEER_NARROW],
                            queued_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid],
                                   scope_enforced_since=audit_mod.SCOPE_ENFORCEMENT_COMMIT_TIME)
    row = peer_row(report, fid, PEER_NARROW)
    assert row["classification"] == "unauthorized" and row["evidence_class"] == "unverifiable"
    # (b) queued after the floor but the edge changed AFTER the event
    ep2, fid2, _ = await seed_retracted_fact(conn)
    await seed_live_copy_at(conn, ep2, fid2, delivered_to=[PEER_NARROW],
                            queued_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    await conn.execute(
        "UPDATE koi_net_edges SET updated_at = NOW() WHERE target_node = $1", PEER_NARROW)
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid2],
                                   scope_enforced_since=audit_mod.SCOPE_ENFORCEMENT_COMMIT_TIME)
    row = peer_row(report, fid2, PEER_NARROW)
    assert row["classification"] == "unauthorized" and row["evidence_class"] == "unverifiable"


async def seed_live_copy_at(conn, ep, fid, *, delivered_to=(), confirmed_by=(), queued_at):
    return await seed_event(
        conn, domain="knowledge_episode", event_type="NEW", rid=episode_rid(ep),
        payload=episode_payload(ep, [fact_payload(fid, ep, valid_to=None)]),
        delivered_to=delivered_to, confirmed_by=confirmed_by, queued_at=queued_at,
    )


@pytest.mark.anyio
async def test_review_ledger_unmigrated_rejection_is_sendable(conn):
    """P2. `rejected: ledger_unavailable…` is an un-migrated peer, not a verdict:
    classified peer_unmigrated, planned (with the 127 hint), outstanding."""
    await apply_ledger_migration(conn)
    await seed_edges(conn)
    ep, fid, db_valid_to = await seed_retracted_fact(conn)
    await _seed_ledger_delivery(conn, fid, db_valid_to, PEER_AUTH, "rejected", "ledger_unavailable_fact_absent")
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid])
    row = peer_row(report, fid, PEER_AUTH)
    assert row["classification"] == "peer_unmigrated"
    plan = plan_for(report, fid, PEER_AUTH)
    assert len(plan) == 1 and "migration 127" in plan[0]["text"]
    assert report["summary"]["outstanding"] is True


@pytest.mark.anyio
async def test_review_ledger_scope_failure_is_sendable_once_the_edge_admits_again(conn):
    """P3. `failed: edge_scope_excluded_at_poll` on a peer whose edge admits again."""
    await apply_ledger_migration(conn)
    await seed_edges(conn)
    ep, fid, db_valid_to = await seed_retracted_fact(conn)
    await _seed_ledger_delivery(conn, fid, db_valid_to, PEER_AUTH, "failed", "edge_scope_excluded_at_poll")
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid])
    row = peer_row(report, fid, PEER_AUTH)
    assert row["classification"] == "scope_failed_reopenable"
    assert len(plan_for(report, fid, PEER_AUTH)) == 1
    # still narrow → stays failed, no plan
    ep2, fid2, db2 = await seed_retracted_fact(conn)
    await _seed_ledger_delivery(conn, fid2, db2, PEER_NARROW, "failed", "edge_scope_excluded_at_poll")
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid2])
    assert peer_row(report, fid2, PEER_NARROW)["classification"] == "failed"
    assert plan_for(report, fid2, PEER_NARROW) == []


@pytest.mark.anyio
async def test_review_ledger_mismatch_rejection_is_outstanding_without_a_plan_line(conn):
    """P1. `rejected: peer_holds_different_valid_to`: the recipient keeps its
    earlier tombstone by design, so re-sending is futile — no plan line — but
    the AC2 violation is visible and the run is outstanding."""
    await apply_ledger_migration(conn)
    await seed_edges(conn)
    ep, fid, db_valid_to = await seed_retracted_fact(conn)
    await _seed_ledger_delivery(conn, fid, db_valid_to, PEER_AUTH, "rejected", "peer_holds_different_valid_to")
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid])
    row = peer_row(report, fid, PEER_AUTH)
    assert row["classification"] == "tombstone_valid_to_mismatch"
    assert plan_for(report, fid, PEER_AUTH) == []
    assert report["summary"]["outstanding"] is True


# ═══════════════════════════════════════════════════════════════════════════
# Second review round (session b35cb9db, 2026-09-17) — each written red first
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_r2_ledger_pending_is_its_own_class_and_never_proves_application(conn):
    """Finding 2. A `pending` delivery means the peer holds NO fact — it recorded
    a pending tombstone. It used to be stored as `applied` and the audit set
    application_proven=True for a peer that stores nothing. Now: its own class,
    application_proven False, nothing live to repair (not outstanding, no plan)."""
    await apply_ledger_migration(conn)
    await seed_edges(conn)
    ep, fid, db_valid_to = await seed_retracted_fact(conn)
    await _seed_ledger_delivery(conn, fid, db_valid_to, PEER_AUTH, "pending",
                                "peer_recorded_pending_tombstone_fact_absent")
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid])
    row = peer_row(report, fid, PEER_AUTH)
    assert row["classification"] == "pending", row["note"]
    assert row["application_proven"] is False
    assert "absent" in row["note"]
    assert plan_for(report, fid, PEER_AUTH) == []
    assert report["summary"]["by_classification"].get("pending") == 1
    assert report["summary"]["outstanding"] is False


@pytest.mark.anyio
async def test_r2_exhausted_failed_and_terminal_rejected_are_outstanding_and_listed(conn):
    """Finding 8. `failed` (attempts exhausted) and a non-reopenable `rejected`
    were excluded from OUTSTANDING, so a run with only terminal failures exited
    0 and the human output listed them only under their fact. They are
    obligations the transport gave up on: outstanding (exit 1), and named in an
    aggregate "terminal failures" section."""
    await apply_ledger_migration(conn)
    await seed_edges(conn)
    ep, fid, db_valid_to = await seed_retracted_fact(conn)
    await _seed_ledger_delivery(conn, fid, db_valid_to, PEER_AUTH, "failed", "attempts_exhausted:5")
    ep2, fid2, db_valid_to2 = await seed_retracted_fact(conn)
    await _seed_ledger_delivery(conn, fid2, db_valid_to2, PEER_AUTH, "rejected", "valid_to_unparseable")
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid, fid2])
    assert peer_row(report, fid, PEER_AUTH)["classification"] == "failed"
    assert peer_row(report, fid2, PEER_AUTH)["classification"] == "rejected"
    assert report["plan"] == []
    assert report["summary"]["outstanding"] is True
    assert report["summary"]["exit_code"] == 1
    terminal = report["summary"]["terminal_failures"]
    assert {(t["fact_id"], t["peer"], t["state"]) for t in terminal} == {
        (fid, PEER_AUTH, "failed"), (fid2, PEER_AUTH, "rejected")}
    text = audit_mod.render_human(report)
    assert "terminal failures" in text
    assert "attempts_exhausted:5" in text and "valid_to_unparseable" in text


@pytest.mark.anyio
async def test_r2_scope_excluded_needs_an_explicit_enforcement_floor(conn):
    """Finding 7. `scope_excluded` rested on a hardcoded floor — the commit time
    of 2c497f0 — which assumed the node running the audit has run that code.
    The NUC's checkout (aa4be29) does not contain it, so there every
    delivered_to mark is a possible hand-over. Without an explicit floor the
    class is unavailable and the mark stays `unverifiable` (→ unauthorized);
    with one, the same shape is `scope_excluded`. The floor is reported."""
    await seed_edges(conn)
    await conn.execute(
        "UPDATE koi_net_edges SET updated_at = '2026-08-30T00:00:00+00:00' WHERE target_node = $1",
        PEER_NARROW)
    ep, fid, _ = await seed_retracted_fact(conn)
    await seed_live_copy(conn, ep, fid, delivered_to=[PEER_NARROW])   # mark only, queued NOW

    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid])
    row = peer_row(report, fid, PEER_NARROW)
    assert row["classification"] == "unauthorized" and row["evidence_class"] == "unverifiable", row["note"]
    assert report["scope_enforced_since"] is None
    assert report["summary"]["outstanding"] is True

    floor = datetime(2026, 8, 26, 2, 50, 25, tzinfo=timezone.utc)
    report = await audit_mod.audit(conn, NODE_A, fact_ids=[fid], scope_enforced_since=floor)
    row = peer_row(report, fid, PEER_NARROW)
    assert row["classification"] == "scope_excluded", row["note"]
    assert report["scope_enforced_since"] == floor.isoformat()
    assert report["summary"]["outstanding"] is False
    text = audit_mod.render_human(report)
    assert "scope enforced since: 2026-08-26T02:50:25+00:00" in text


def test_r2_scope_floor_resolution_is_explicit_or_verified(tmp_path):
    """`--scope-enforced-since` takes an ISO instant (operator asserts it), `auto`
    (verified: the checkout the audit runs from must contain 2c497f0, and the
    floor is that commit's time), or is absent (no floor). `auto` in a
    checkout without the commit is a misconfiguration, not a silent default."""
    none = audit_mod.resolve_scope_floor(None)
    assert none == (None, "not given: scope_excluded classification disabled")
    explicit, how = audit_mod.resolve_scope_floor("2026-08-26T02:50:25+00:00")
    assert explicit == datetime(2026, 8, 26, 2, 50, 25, tzinfo=timezone.utc)
    assert how.startswith("given on the command line")
    with pytest.raises(audit_mod.ScopeFloorError):
        audit_mod.resolve_scope_floor("not-a-timestamp")
    # auto, verified against THIS repo (positive control: 2c497f0 is an ancestor of HEAD here)
    auto, how = audit_mod.resolve_scope_floor("auto", repo_root=audit_mod.REPO_ROOT)
    assert auto == datetime(2026, 8, 26, 2, 50, 25, tzinfo=timezone.utc)
    assert "2c497f0" in how and "ancestor" in how
    # auto in a directory that is not that repo → refused, never defaulted
    with pytest.raises(audit_mod.ScopeFloorError) as exc_info:
        audit_mod.resolve_scope_floor("auto", repo_root=tmp_path)
    assert "2c497f0" in str(exc_info.value)


def test_r2_cli_auto_floor_outside_the_repo_is_exit_3(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(audit_mod, "REPO_ROOT", tmp_path)
    rc = audit_mod.main(["--dsn", DB_URL, "--node-rid", NODE_A, "--scope-enforced-since", "auto",
                         "--limit", "1"])
    assert rc == audit_mod.EXIT_MISCONFIGURED
    assert "2c497f0" in capsys.readouterr().err
