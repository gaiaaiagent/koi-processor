"""Issue #67 — the RECIPIENT side: applying a federated fact retraction.

What is proved here, per requirement, against a scratch database inside one
rolled-back transaction (tests/conftest.py pins POSTGRES_URL to
personal_koi_test; the fixture refuses `personal_koi` by name):

  * A retraction event lands `valid_to` on the local fact to the microsecond
    and is reported as `applied`, with the ledger row and the audit row.
  * Re-delivering the identical event is idempotent (`already_tombstoned`,
    `valid_to_matches=True`, one ledger row, nothing changed).
  * A local tombstone with a DIFFERENT valid_to is kept and the mismatch is
    reported — never overwritten.
  * UPDATE-before-NEW: a retraction for a fact the recipient does not hold yet
    is recorded as a PENDING tombstone (no fact row is minted); when the fact
    later arrives — bundled in an episode NEW, or standalone — it lands already
    tombstoned with the pending value. And a stale NEW carrying valid_to=NULL
    after a tombstone does not resurrect the fact.
  * Malformed input (garbage / null / naive `valid_to`, non-UUID id, a
    `retraction.valid_to` that disagrees with the row) is REJECTED without a
    write and without a raise — `parse_ts` returns None silently on garbage,
    and None must never reach the UPDATE.
  * An un-migrated recipient (ledger tables absent) rejects with
    `ledger_unavailable` and touches nothing; its ordinary episode applies still
    succeed, because `apply_pending_tombstones` degrades instead of raising.
  * The report is RETURNED through `apply_domain_event` (the poller carries it
    back in the confirm payload); non-retraction handlers keep returning None,
    and a `knowledge_fact` payload without a `retraction` block still upserts.
  * The poller's sweep hook is env-gated and OFF by default.

Driven through `apply_domain_event` directly, the way
tests/unit/test_domain_event_handlers.py drives the handlers. The wiring from
poll to confirm is tests/integration/test_federation_retraction.py.
"""

from __future__ import annotations

import asyncio
import inspect
import types
import uuid
from datetime import datetime

import pytest

from api import fact_retraction
from api.domain_event_handlers import (
    _insert_fact,
    _strip_conflict_assignments,
    apply_domain_event,
)
from api import koi_poller as koi_poller_module
from api.koi_poller import KOIPoller

from tests.fact_retraction_testkit import (
    INCIDENT_VALID_TO, NODE_A, NODE_B,
    SingleConnPool,
    apply_ledger_migration, close_scratch_conn, open_scratch_conn,
    episode_payload, fact_payload, retraction_payload, seed_fact,
)

OTHER_VALID_TO = "2026-09-13T09:00:00.000001+00:00"
EP_RID = "orn:personal-koi.knowledge-episode:{}"
FACT_RID = "orn:personal-koi.knowledge-fact:{}"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def conn(monkeypatch):
    monkeypatch.setenv("KOI_FEDERATE_KNOWLEDGE", "true")
    c, tx = await open_scratch_conn()
    yield c
    await close_scratch_conn(c, tx)


@pytest.fixture
async def ledger(conn):
    """The recipient HAS run migration 127 (inside the rolled-back transaction)."""
    await apply_ledger_migration(conn)
    assert await fact_retraction.ledger_available(conn)
    return conn


@pytest.fixture
async def no_ledger(conn):
    """The recipient has NOT run migration 127.

    The scratch database does not carry 127 (tests/test_migration_127.py pins
    that); the DROP is belt-and-braces so this fixture models the un-migrated
    recipient even if that ever changes, and it is rolled back with the rest.
    """
    await conn.execute(
        "DROP TABLE IF EXISTS knowledge_fact_retraction_deliveries, knowledge_fact_retractions")
    assert not await fact_retraction.ledger_available(conn)
    return conn


async def _dispatch_retraction(conn, payload, source_node=NODE_A):
    return await apply_domain_event(
        conn, "knowledge_fact", FACT_RID.format(payload["id"]), "UPDATE", payload, source_node)


async def _dispatch_episode_new(conn, ep_id, facts, event_id=None, source_node=NODE_A):
    return await apply_domain_event(
        conn, "knowledge_episode", EP_RID.format(ep_id), "NEW",
        episode_payload(ep_id, facts, event_id=event_id or str(uuid.uuid4())), source_node)


async def _dispatch_standalone_new(conn, fact_id, ep_id, valid_to=None, source_node=NODE_A):
    p = fact_payload(fact_id, ep_id, valid_to=valid_to)
    p["_federation_event_id"] = str(uuid.uuid4())
    return await apply_domain_event(
        conn, "knowledge_fact", FACT_RID.format(fact_id), "NEW", p, source_node)


async def _valid_to(conn, fact_id):
    return await conn.fetchval(
        "SELECT valid_to FROM knowledge_facts WHERE id = $1::uuid", fact_id)


async def _ledger_rows(conn, fact_id):
    return await conn.fetch(
        "SELECT valid_to, origin_node, applied_at, reason, retracted_by, source_document, "
        "       episode_id, fact_snapshot "
        "FROM knowledge_fact_retractions WHERE fact_id = $1::uuid ORDER BY id",
        fact_id)


async def _audit_rows(conn, event_id):
    return await conn.fetchval(
        "SELECT COUNT(*) FROM federation_applied_events "
        "WHERE domain = 'knowledge_fact' AND event_id = $1::uuid", event_id)


# ═══════════════════════════════════════════════════════════════════════════
# The report shape
# ═══════════════════════════════════════════════════════════════════════════

REPORT_KEYS = {
    "application", "domain", "event_id", "fact_id", "status",
    "valid_to", "valid_to_matches", "reason", "applied_at",
}


def _assert_report_shape(report, *, status, event_id, fact_id):
    assert isinstance(report, dict), f"handler returned {report!r}, not a report"
    assert set(report) == REPORT_KEYS, set(report) ^ REPORT_KEYS
    assert report["application"] is True
    assert report["domain"] == "knowledge_fact"
    assert report["event_id"] == event_id
    assert report["fact_id"] == fact_id
    assert report["status"] == status
    assert report["status"] in fact_retraction.REPORT_STATUSES
    # applied_at must be a real timestamp, so the publisher can order reports.
    datetime.fromisoformat(report["applied_at"])


# ═══════════════════════════════════════════════════════════════════════════
# Fresh apply, duplicate delivery, conflicting local tombstone
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_fresh_apply_lands_valid_to_and_reports_applied(ledger):
    conn = ledger
    ep_id, fact_id = await seed_fact(conn, valid_to=None)
    payload = retraction_payload(fact_id, ep_id, valid_to=INCIDENT_VALID_TO)
    ev_id = payload["_federation_event_id"]

    report = await _dispatch_retraction(conn, payload)

    _assert_report_shape(report, status="applied", event_id=ev_id, fact_id=fact_id)
    assert report["valid_to"] == INCIDENT_VALID_TO
    assert report["valid_to_matches"] is True
    assert report["reason"] is None

    vt = await _valid_to(conn, fact_id)
    assert vt is not None and vt.isoformat() == INCIDENT_VALID_TO, (
        "knowledge_facts.valid_to is not the payload's value to the microsecond")

    rows = await _ledger_rows(conn, fact_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["valid_to"].isoformat() == INCIDENT_VALID_TO
    assert row["applied_at"] is not None
    assert row["origin_node"] == NODE_A
    assert row["reason"] == "testkit" and row["retracted_by"] == "service:test"
    assert row["source_document"] == "boundary.md"
    assert str(row["episode_id"]) == ep_id
    snap = row["fact_snapshot"]
    if isinstance(snap, str):
        import json
        snap = json.loads(snap)
    assert "retraction" not in snap and "_federation_event_id" not in snap
    assert snap["id"] == fact_id and snap["valid_to"] == INCIDENT_VALID_TO

    assert await _audit_rows(conn, ev_id) == 1


@pytest.mark.anyio
async def test_duplicate_delivery_is_idempotent(ledger):
    conn = ledger
    ep_id, fact_id = await seed_fact(conn, valid_to=None)
    payload = retraction_payload(fact_id, ep_id)
    ev_id = payload["_federation_event_id"]

    first = await _dispatch_retraction(conn, payload)
    assert first["status"] == "applied"
    before = await conn.fetchrow(
        "SELECT * FROM knowledge_facts WHERE id = $1::uuid", fact_id)
    ledger_before = await _ledger_rows(conn, fact_id)

    second = await _dispatch_retraction(conn, payload)

    _assert_report_shape(second, status="already_tombstoned", event_id=ev_id, fact_id=fact_id)
    assert second["valid_to"] == INCIDENT_VALID_TO
    assert second["valid_to_matches"] is True
    after = await conn.fetchrow(
        "SELECT * FROM knowledge_facts WHERE id = $1::uuid", fact_id)
    assert dict(after) == dict(before), "a duplicate delivery changed the fact row"
    ledger_after = await _ledger_rows(conn, fact_id)
    assert len(ledger_after) == 1, "a duplicate delivery minted a second ledger row"
    assert ledger_after[0]["applied_at"] == ledger_before[0]["applied_at"]
    assert await _audit_rows(conn, ev_id) == 1


@pytest.mark.anyio
async def test_conflicting_local_tombstone_is_kept_and_reported(ledger):
    conn = ledger
    ep_id, fact_id = await seed_fact(conn, valid_to=None)
    await conn.execute(
        "UPDATE knowledge_facts SET valid_to = $2 WHERE id = $1::uuid",
        fact_id, datetime.fromisoformat(OTHER_VALID_TO))
    payload = retraction_payload(fact_id, ep_id, valid_to=INCIDENT_VALID_TO)

    report = await _dispatch_retraction(conn, payload)

    _assert_report_shape(report, status="already_tombstoned",
                         event_id=payload["_federation_event_id"], fact_id=fact_id)
    assert report["valid_to_matches"] is False
    assert report["valid_to"] == OTHER_VALID_TO, "the report must carry the LOCAL value"
    vt = await _valid_to(conn, fact_id)
    assert vt.isoformat() == OTHER_VALID_TO, "an existing tombstone was overwritten"
    # The incoming tombstone is still recorded (with its own valid_to) so the
    # disagreement is auditable on this node too.
    rows = await _ledger_rows(conn, fact_id)
    assert [r["valid_to"].isoformat() for r in rows] == [INCIDENT_VALID_TO]


@pytest.mark.anyio
async def test_origin_node_is_the_authenticated_source_not_the_payload_claim(ledger):
    """`retraction.origin_node` is peer-supplied text; the ledger records who DELIVERED it."""
    conn = ledger
    ep_id, fact_id = await seed_fact(conn, valid_to=None)
    payload = retraction_payload(fact_id, ep_id, origin_node="orn:koi-net.node:claimed+zzzz")

    await _dispatch_retraction(conn, payload, source_node=NODE_B)

    rows = await _ledger_rows(conn, fact_id)
    assert [r["origin_node"] for r in rows] == [NODE_B]


# ═══════════════════════════════════════════════════════════════════════════
# UPDATE-before-NEW: pending tombstones and no resurrection
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_retraction_for_unknown_fact_is_pending_and_mints_no_row(ledger):
    conn = ledger
    ep_id, fact_id = str(uuid.uuid4()), str(uuid.uuid4())
    payload = retraction_payload(fact_id, ep_id)

    report = await _dispatch_retraction(conn, payload)

    _assert_report_shape(report, status="pending",
                         event_id=payload["_federation_event_id"], fact_id=fact_id)
    assert report["valid_to"] is None and report["valid_to_matches"] is None
    assert await conn.fetchval(
        "SELECT 1 FROM knowledge_facts WHERE id = $1::uuid", fact_id) is None, (
        "a pending retraction minted a knowledge_facts row")
    rows = await _ledger_rows(conn, fact_id)
    assert len(rows) == 1 and rows[0]["applied_at"] is None, "pending row must have applied_at NULL"
    assert rows[0]["valid_to"].isoformat() == INCIDENT_VALID_TO
    assert await _audit_rows(conn, payload["_federation_event_id"]) == 1


@pytest.mark.anyio
async def test_pending_tombstone_lands_on_late_episode_new(ledger):
    """The acceptance criterion: retraction first, episode NEW second, fact lands tombstoned."""
    conn = ledger
    ep_id, fact_id = str(uuid.uuid4()), str(uuid.uuid4())
    pending = await _dispatch_retraction(conn, retraction_payload(fact_id, ep_id))
    assert pending["status"] == "pending"

    await _dispatch_episode_new(conn, ep_id, [fact_payload(fact_id, ep_id, valid_to=None)])

    vt = await _valid_to(conn, fact_id)
    assert vt is not None, "the late NEW landed the fact LIVE despite a pending tombstone"
    assert vt.isoformat() == INCIDENT_VALID_TO
    rows = await _ledger_rows(conn, fact_id)
    assert rows[0]["applied_at"] is not None, "the pending row was not marked applied"
    assert not (await fact_retraction.tombstone_status(conn, uuid.UUID(fact_id)))["pending_tombstone"]


@pytest.mark.anyio
async def test_pending_tombstone_lands_on_late_standalone_fact(ledger):
    conn = ledger
    ep_id, _ = await seed_fact(conn)          # the parent episode exists, the fact does not
    fact_id = str(uuid.uuid4())
    pending = await _dispatch_retraction(conn, retraction_payload(fact_id, ep_id))
    assert pending["status"] == "pending"

    await _dispatch_standalone_new(conn, fact_id, ep_id, valid_to=None)

    vt = await _valid_to(conn, fact_id)
    assert vt is not None, "the late standalone NEW landed the fact LIVE despite a pending tombstone"
    assert vt.isoformat() == INCIDENT_VALID_TO
    assert (await _ledger_rows(conn, fact_id))[0]["applied_at"] is not None


@pytest.mark.anyio
async def test_stale_episode_new_after_tombstone_does_not_resurrect(ledger):
    """Same shape as the boundary requirement, through the retraction handler."""
    conn = ledger
    ep_id, fact_id = await seed_fact(conn, valid_to=None)
    assert (await _dispatch_retraction(conn, retraction_payload(fact_id, ep_id)))["status"] == "applied"

    await _dispatch_episode_new(conn, ep_id, [fact_payload(fact_id, ep_id, valid_to=None)])

    vt = await _valid_to(conn, fact_id)
    assert vt is not None, "a stale episode NEW resurrected a tombstoned fact"
    assert vt.isoformat() == INCIDENT_VALID_TO


@pytest.mark.anyio
async def test_stale_standalone_new_after_tombstone_does_not_resurrect(ledger):
    conn = ledger
    ep_id, fact_id = await seed_fact(conn, valid_to=None)
    assert (await _dispatch_retraction(conn, retraction_payload(fact_id, ep_id)))["status"] == "applied"

    await _dispatch_standalone_new(conn, fact_id, ep_id, valid_to=None)

    vt = await _valid_to(conn, fact_id)
    assert vt is not None, "a stale standalone NEW resurrected a tombstoned fact"
    assert vt.isoformat() == INCIDENT_VALID_TO


@pytest.mark.anyio
async def test_non_null_incoming_valid_to_still_lands_on_a_live_fact(ledger):
    """The COALESCE must keep the OLD soft-delete path: a live fact takes a non-null valid_to."""
    conn = ledger
    ep_id, fact_id = await seed_fact(conn, valid_to=None)

    await _dispatch_episode_new(conn, ep_id, [fact_payload(fact_id, ep_id, valid_to=OTHER_VALID_TO)])

    vt = await _valid_to(conn, fact_id)
    assert vt is not None and vt.isoformat() == OTHER_VALID_TO


def test_least_conflict_clause_survives_the_drift_stripper():
    """`_insert_with_drift_retry` re-interpolates the clause and `_strip_conflict_assignments`
    splits it on top-level commas. The LEAST form has a comma inside parentheses."""
    coalesce = "valid_to = LEAST(knowledge_facts.valid_to, EXCLUDED.valid_to)"
    # Bind to the real call site, not a copy of it.
    assert coalesce in inspect.getsource(_insert_fact), "_insert_fact no longer uses the monotone form"
    clause = f"""
        ON CONFLICT (id) DO UPDATE SET
            {coalesce}
    """
    kept = _strip_conflict_assignments(clause, "turn_range_end")
    assert "LEAST(knowledge_facts.valid_to, EXCLUDED.valid_to)" in kept
    dropped = _strip_conflict_assignments(clause, "valid_to")
    assert dropped.strip().upper().endswith("DO NOTHING"), dropped


# ═══════════════════════════════════════════════════════════════════════════
# Rejection: nothing written, nothing raised
# ═══════════════════════════════════════════════════════════════════════════

async def _assert_untouched(conn, fact_id, event_id, *, expect_ledger_table=True):
    assert await _valid_to(conn, fact_id) is None, "a rejected retraction touched the fact"
    if expect_ledger_table:
        assert await _ledger_rows(conn, fact_id) == [], "a rejected retraction wrote a ledger row"
    assert await _audit_rows(conn, event_id) == 0, "a rejected retraction wrote an audit row"


@pytest.mark.anyio
@pytest.mark.parametrize("bad_valid_to, reason", [
    ("not-a-timestamp", "valid_to_unparseable"),
    (None, "valid_to_missing"),
    ("2026-09-14T15:13:07.924484", "valid_to_no_timezone"),
])
async def test_malformed_valid_to_is_rejected(ledger, bad_valid_to, reason):
    conn = ledger
    ep_id, fact_id = await seed_fact(conn, valid_to=None)
    payload = retraction_payload(fact_id, ep_id)
    payload["valid_to"] = bad_valid_to
    payload["retraction"]["valid_to"] = bad_valid_to
    ev_id = payload["_federation_event_id"]

    report = await _dispatch_retraction(conn, payload)

    _assert_report_shape(report, status="rejected", event_id=ev_id, fact_id=fact_id)
    assert report["reason"] == reason
    await _assert_untouched(conn, fact_id, ev_id)


@pytest.mark.anyio
async def test_retraction_block_disagreeing_with_row_is_rejected(ledger):
    conn = ledger
    ep_id, fact_id = await seed_fact(conn, valid_to=None)
    payload = retraction_payload(fact_id, ep_id, valid_to=INCIDENT_VALID_TO)
    payload["retraction"]["valid_to"] = OTHER_VALID_TO

    report = await _dispatch_retraction(conn, payload)

    assert report["status"] == "rejected" and report["reason"] == "retraction_valid_to_mismatch"
    await _assert_untouched(conn, fact_id, payload["_federation_event_id"])


@pytest.mark.anyio
async def test_non_uuid_fact_id_is_rejected(ledger):
    conn = ledger
    ep_id, fact_id = await seed_fact(conn, valid_to=None)
    payload = retraction_payload(fact_id, ep_id)
    payload["id"] = "not-a-uuid"

    report = await _dispatch_retraction(conn, payload)

    assert report["status"] == "rejected" and report["reason"] == "invalid_fact_id"
    assert report["fact_id"] is None
    await _assert_untouched(conn, fact_id, payload["_federation_event_id"])


@pytest.mark.anyio
async def test_unmigrated_recipient_rejects_only_an_absent_fact(no_ledger):
    """Contract changed by the 2026-09-16 review (finding 1): a recipient without
    migration 127 lands the tombstone on a PRESENT fact (reported `applied`,
    reason `ledger_unavailable_not_ledgered`) and rejects only when the fact is
    ABSENT, because a pending tombstone needs the ledger. The earlier version of
    this test asserted the fact stayed untouched — a regression against old
    code, which tombstoned it through the upsert."""
    conn = no_ledger
    ep_id, fact_id = await seed_fact(conn, valid_to=None)
    payload = retraction_payload(fact_id, ep_id)

    report = await _dispatch_retraction(conn, payload)   # must not raise
    _assert_report_shape(report, status="applied",
                         event_id=payload["_federation_event_id"], fact_id=fact_id)
    assert report["reason"] == "ledger_unavailable_not_ledgered"
    assert (await _valid_to(conn, fact_id)).isoformat() == INCIDENT_VALID_TO

    absent = str(uuid.uuid4())
    payload2 = retraction_payload(absent, ep_id)
    report2 = await _dispatch_retraction(conn, payload2)
    _assert_report_shape(report2, status="rejected",
                         event_id=payload2["_federation_event_id"], fact_id=absent)
    assert report2["reason"] == "ledger_unavailable_fact_absent"
    assert await conn.fetchval("SELECT 1 FROM knowledge_facts WHERE id = $1::uuid", absent) is None


@pytest.mark.anyio
async def test_unmigrated_recipient_still_applies_episodes(no_ledger):
    """apply_pending_tombstones must degrade, not break every episode on a peer without 127."""
    conn = no_ledger
    ep_id, fact_id = str(uuid.uuid4()), str(uuid.uuid4())

    result = await _dispatch_episode_new(conn, ep_id, [fact_payload(fact_id, ep_id)])

    assert result is None
    assert await conn.fetchval(
        "SELECT 1 FROM knowledge_facts WHERE id = $1::uuid", fact_id) == 1
    # And the standalone path.
    fact2 = str(uuid.uuid4())
    assert await _dispatch_standalone_new(conn, fact2, ep_id) is None
    assert await conn.fetchval(
        "SELECT 1 FROM knowledge_facts WHERE id = $1::uuid", fact2) == 1


# ═══════════════════════════════════════════════════════════════════════════
# Dispatch: the report is returned; everything else is unchanged
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_apply_domain_event_returns_the_report_and_none_elsewhere(ledger):
    conn = ledger
    ep_id, fact_id = await seed_fact(conn, valid_to=None)

    report = await _dispatch_retraction(conn, retraction_payload(fact_id, ep_id))
    assert isinstance(report, dict) and report.get("application") is True

    other_ep = str(uuid.uuid4())
    assert await _dispatch_episode_new(conn, other_ep, [fact_payload(str(uuid.uuid4()), other_ep)]) is None
    assert await apply_domain_event(
        conn, "knowledge_fact", FACT_RID.format(fact_id), "FORGET", {"id": fact_id}, NODE_A) is None


@pytest.mark.anyio
async def test_plain_knowledge_fact_payload_still_upserts(ledger):
    """No `retraction` block → the pre-#67 standalone upsert, unchanged."""
    conn = ledger
    ep_id, _ = await seed_fact(conn)
    fact_id = str(uuid.uuid4())

    assert await _dispatch_standalone_new(conn, fact_id, ep_id) is None
    row = await conn.fetchrow(
        "SELECT episode_id, valid_to, fact_text FROM knowledge_facts WHERE id = $1::uuid", fact_id)
    assert row is not None and str(row["episode_id"]) == ep_id and row["valid_to"] is None
    assert row["fact_text"] == "boundary fact"
    assert await _ledger_rows(conn, fact_id) == []


# ═══════════════════════════════════════════════════════════════════════════
# Poller: the report rides the confirm payload; the sweep is opt-in
# ═══════════════════════════════════════════════════════════════════════════

class _FakeResponse:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code
        self.text = str(body)

    def json(self):
        return self._body


class _FakeHttpx:
    """Stands in for the `httpx` module inside api.koi_poller: records every POST."""

    class ConnectError(Exception):
        pass

    def __init__(self, poll_events):
        self.poll_events = poll_events
        self.posts = []
        outer = self

        class AsyncClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def post(self, url, json=None):
                outer.posts.append((url, json))
                if url.endswith("/events/poll"):
                    return _FakeResponse({"events": outer.poll_events})
                if url.endswith("/events/confirm"):
                    return _FakeResponse({"confirmed": len(json.get("event_ids", []))})
                raise AssertionError(f"unexpected url {url}")

        self.AsyncClient = AsyncClient


@pytest.mark.anyio
async def test_poll_peer_carries_the_report_in_the_confirm_payload(ledger, monkeypatch):
    """Through `_poll_peer`'s real HTTP shell (httpx faked): the retraction event
    is applied via the real dispatch and its report is in `applications`."""
    conn = ledger
    ep_id, fact_id = await seed_fact(conn, valid_to=None)
    payload = retraction_payload(fact_id, ep_id)
    ev_id = payload["_federation_event_id"]
    fake = _FakeHttpx(poll_events=[{
        "event_id": ev_id, "event_type": "UPDATE", "rid": FACT_RID.format(fact_id),
        "manifest": None, "contents": {"_koi_domain": "knowledge_fact", "payload": payload},
        "source_node": NODE_A, "queued_at": None,
    }])
    monkeypatch.setattr(koi_poller_module, "httpx", fake)
    monkeypatch.setattr(koi_poller_module, "REQUIRE_SIGNED_REQUESTS", False)
    monkeypatch.setattr(koi_poller_module, "REQUIRE_SIGNED_RESPONSES", False)

    poller = KOIPoller(SingleConnPool(conn), NODE_B)
    await poller._poll_peer(source_node=NODE_A, base_url="http://a.test", rid_types=None)

    assert (await _valid_to(conn, fact_id)).isoformat() == INCIDENT_VALID_TO
    confirms = [body for url, body in fake.posts if url.endswith("/events/confirm")]
    assert len(confirms) == 1
    body = confirms[0]
    assert body["event_ids"] == [ev_id]
    assert len(body["applications"]) == 1
    app = body["applications"][0]
    assert app["event_id"] == ev_id and app["status"] == "applied"
    assert app["node"] == NODE_B
    assert app["valid_to"] == INCIDENT_VALID_TO


@pytest.mark.anyio
async def test_confirm_payload_is_unchanged_when_nothing_was_applied(ledger, monkeypatch):
    """A non-retraction event confirms with the pre-#67 payload: no `applications` key at all."""
    conn = ledger
    ep_id = str(uuid.uuid4())
    ev_id = str(uuid.uuid4())
    fake = _FakeHttpx(poll_events=[{
        "event_id": ev_id, "event_type": "NEW", "rid": EP_RID.format(ep_id),
        "manifest": None,
        "contents": {"_koi_domain": "knowledge_episode",
                     "payload": episode_payload(ep_id, [], event_id=ev_id)},
        "source_node": NODE_A, "queued_at": None,
    }])
    monkeypatch.setattr(koi_poller_module, "httpx", fake)
    monkeypatch.setattr(koi_poller_module, "REQUIRE_SIGNED_REQUESTS", False)
    monkeypatch.setattr(koi_poller_module, "REQUIRE_SIGNED_RESPONSES", False)

    poller = KOIPoller(SingleConnPool(conn), NODE_B)
    await poller._poll_peer(source_node=NODE_A, base_url="http://a.test", rid_types=None)

    confirms = [body for url, body in fake.posts if url.endswith("/events/confirm")]
    assert confirms == [{"type": "confirm_events", "event_ids": [ev_id], "node_id": NODE_B}]


@pytest.mark.anyio
@pytest.mark.parametrize("env, expect_calls", [(None, 0), ("false", 0), ("true", 1)])
async def test_poll_loop_sweep_hook_is_env_gated_default_off(monkeypatch, env, expect_calls):
    if env is None:
        monkeypatch.delenv("KOI_FACT_RETRACTION_SWEEP", raising=False)
    else:
        monkeypatch.setenv("KOI_FACT_RETRACTION_SWEEP", env)
    calls = []

    async def fake_sweep(pool, event_queue, **kw):
        calls.append((pool, event_queue))
        return {}

    monkeypatch.setattr(fact_retraction, "sweep_once", fake_sweep)

    poller = KOIPoller(pool=object(), node_rid=NODE_B, event_queue=object())
    poller._running = True

    async def _noop():
        return None

    async def _stop_after_one(_seconds):
        poller._running = False

    monkeypatch.setattr(poller, "_poll_all_peers", _noop)
    monkeypatch.setattr(poller, "_push_webhook_peers", _noop)
    # Replace the module's `asyncio` reference, not asyncio.sleep globally.
    monkeypatch.setattr(koi_poller_module, "asyncio", types.SimpleNamespace(
        sleep=_stop_after_one, CancelledError=asyncio.CancelledError))

    await poller._poll_loop()

    assert len(calls) == expect_calls


# ═══════════════════════════════════════════════════════════════════════════
# Review findings (2026-09-16 adversarial review) — each written red first
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_review_unmigrated_recipient_still_tombstones_a_present_fact(monkeypatch):
    """Finding 1: rejecting BEFORE touching the fact on a recipient without
    migration 127 was a regression against old code, which at least landed
    valid_to on a present fact. Now: present fact → tombstoned, reported
    `applied` with reason `ledger_unavailable_not_ledgered`; only an ABSENT
    fact is rejected (no pending tombstone can be recorded)."""
    from tests.fact_retraction_testkit import open_scratch_conn, close_scratch_conn
    monkeypatch.setenv("KOI_FEDERATE_KNOWLEDGE", "true")
    c, tx = await open_scratch_conn()      # no apply_ledger_migration
    try:
        ep, fid = await seed_fact(c)
        rep = await apply_domain_event(
            c, "knowledge_fact", f"orn:personal-koi.knowledge-fact:{fid}", "UPDATE",
            retraction_payload(fid, ep), NODE_A)
        assert rep["status"] == "applied" and rep["reason"] == "ledger_unavailable_not_ledgered"
        vt = await c.fetchval("SELECT valid_to FROM knowledge_facts WHERE id = $1::uuid", fid)
        assert vt is not None and vt.isoformat() == INCIDENT_VALID_TO
        # absent fact: still rejected, and the reason names the missing ledger
        missing = str(uuid.uuid4())
        rep2 = await apply_domain_event(
            c, "knowledge_fact", f"orn:personal-koi.knowledge-fact:{missing}", "UPDATE",
            retraction_payload(missing, ep), NODE_A)
        assert rep2["status"] == "rejected" and rep2["reason"] == "ledger_unavailable_fact_absent"
        assert await c.fetchval("SELECT 1 FROM knowledge_facts WHERE id = $1::uuid", missing) is None
    finally:
        await close_scratch_conn(c, tx)


@pytest.mark.anyio
async def test_review_valid_to_only_moves_earlier_over_federation(ledger):
    conn = ledger
    """A fact born with a FUTURE validity end (a validity interval) that is
    then retracted must end at the retraction time, on every path:
    a pending tombstone landing on a late NEW carrying valid_to=2030, a NEW
    replay carrying a later valid_to over a tombstone, and a retraction
    arriving on a fact whose local valid_to is later. Never later, never NULL."""
    ep, fid = str(uuid.uuid4()), str(uuid.uuid4())
    T_RET = INCIDENT_VALID_TO
    T_FUTURE = "2030-01-01T00:00:00+00:00"
    # pending tombstone, then a NEW carrying a future valid_to
    rep = await apply_domain_event(conn, "knowledge_fact", f"orn:personal-koi.knowledge-fact:{fid}",
                                   "UPDATE", retraction_payload(fid, ep, valid_to=T_RET), NODE_A)
    assert rep["status"] == "pending"
    await apply_domain_event(conn, "knowledge_episode", f"orn:personal-koi.knowledge-episode:{ep}", "NEW",
                             episode_payload(ep, [fact_payload(fid, ep, valid_to=T_FUTURE)],
                                             event_id=str(uuid.uuid4())), NODE_A)
    vt = await conn.fetchval("SELECT valid_to FROM knowledge_facts WHERE id = $1::uuid", fid)
    assert vt.isoformat() == T_RET, "the pending tombstone must shorten a future validity end"
    # a NEW replay with a LATER valid_to does not move it later
    await apply_domain_event(conn, "knowledge_episode", f"orn:personal-koi.knowledge-episode:{ep}", "NEW",
                             episode_payload(ep, [fact_payload(fid, ep, valid_to=T_FUTURE)],
                                             event_id=str(uuid.uuid4())), NODE_A)
    assert (await conn.fetchval("SELECT valid_to FROM knowledge_facts WHERE id = $1::uuid", fid)).isoformat() == T_RET
    # a fact whose local valid_to is in the future, then a retraction arrives
    ep2, fid2 = str(uuid.uuid4()), str(uuid.uuid4())
    await apply_domain_event(conn, "knowledge_episode", f"orn:personal-koi.knowledge-episode:{ep2}", "NEW",
                             episode_payload(ep2, [fact_payload(fid2, ep2, valid_to=T_FUTURE)],
                                             event_id=str(uuid.uuid4())), NODE_A)
    rep = await apply_domain_event(conn, "knowledge_fact", f"orn:personal-koi.knowledge-fact:{fid2}",
                                   "UPDATE", retraction_payload(fid2, ep2, valid_to=T_RET), NODE_A)
    assert rep["status"] == "applied" and rep["valid_to"] == T_RET
    # and an EARLIER local tombstone is kept, reported as a mismatch
    rep = await apply_domain_event(conn, "knowledge_fact", f"orn:personal-koi.knowledge-fact:{fid2}",
                                   "UPDATE", retraction_payload(fid2, ep2, valid_to=T_FUTURE), NODE_B)
    assert rep["status"] == "already_tombstoned" and rep["valid_to_matches"] is False
    assert rep["valid_to"] == T_RET


@pytest.mark.anyio
async def test_review_episode_update_carrying_an_earlier_valid_to_shortens_a_future_end(ledger):
    """The case that separates LEAST from COALESCE in `_insert_fact`: the fact
    is already present with a FUTURE validity end, and an episode UPDATE
    (the incident's manual-reconstruction shape) re-emits it carrying an
    EARLIER valid_to. COALESCE keeps the future end and loses the retraction;
    LEAST lands the earlier value. Revert-proven 2026-09-16: fails with COALESCE."""
    conn = ledger
    ep, fid = str(uuid.uuid4()), str(uuid.uuid4())
    T_RET, T_FUTURE = INCIDENT_VALID_TO, "2030-01-01T00:00:00+00:00"
    await apply_domain_event(conn, "knowledge_episode", f"orn:personal-koi.knowledge-episode:{ep}", "NEW",
                             episode_payload(ep, [fact_payload(fid, ep, valid_to=T_FUTURE)],
                                             event_id=str(uuid.uuid4())), NODE_A)
    assert (await conn.fetchval("SELECT valid_to FROM knowledge_facts WHERE id = $1::uuid", fid)).isoformat() == T_FUTURE
    await apply_domain_event(conn, "knowledge_episode", f"orn:personal-koi.knowledge-episode:{ep}", "UPDATE",
                             episode_payload(ep, [fact_payload(fid, ep, valid_to=T_RET)],
                                             event_id=str(uuid.uuid4())), NODE_A)
    assert (await conn.fetchval("SELECT valid_to FROM knowledge_facts WHERE id = $1::uuid", fid)).isoformat() == T_RET
