"""Tests for the federation domain event bridge.

Covers:
  1. EventQueue: _koi_domain events bypass rid_types filter
  2. KOIPoller: _process_event dispatches domain events to handlers
  3. DomainEventHandlers: UPSERT for entity, task, claim, attestation, commitment, pool
  4. DomainEventHandlers: FORGET deletes rows
  5. DomainEventHandlers: state log deduplication
  6. FederationEvents: emit/no-op singleton behavior
"""

import json
import os
import uuid

import asyncpg
import pytest

DB_URL = os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")
TEST_SOURCE_NODE = "orn:koi-net.node:test-peer+aaaa"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class _SingleConnPool:
    """Wraps a single asyncpg.Connection to quack like asyncpg.Pool."""
    def __init__(self, conn):
        self._conn = conn

    class _CM:
        def __init__(self, conn):
            self.conn = conn
        async def __aenter__(self):
            return self.conn
        async def __aexit__(self, *a):
            pass

    def acquire(self):
        return self._CM(self._conn)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def conn():
    _conn = await asyncpg.connect(DB_URL)
    tx = _conn.transaction()
    await tx.start()
    yield _conn
    await tx.rollback()
    await _conn.close()


@pytest.fixture
async def pool(conn):
    return _SingleConnPool(conn)


def _uid():
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# 1. EventQueue: _koi_domain bypass
# ---------------------------------------------------------------------------

class TestEventQueueRidTypesFilter:
    """Events with _koi_domain in contents must bypass the rid_types filter."""

    @pytest.mark.anyio
    async def test_domain_event_bypasses_rid_types(self, pool, conn):
        """A domain event should be delivered even when its RID doesn't match rid_types."""
        from api.event_queue import EventQueue

        node_rid = f"orn:koi-net.node:test-{_uid()}"
        eq = EventQueue(pool, node_rid)
        requesting_node = f"orn:koi-net.node:requester-{_uid()}"

        # Queue a domain event with an entity RID (won't match "Vault-file" type)
        entity_rid = f"orn:personal-koi.entity:person-test-{_uid()}"
        await eq.add(
            event_type="NEW",
            rid=entity_rid,
            contents={"_koi_domain": "entity", "payload": {"fuseki_uri": entity_rid}},
        )

        # Poll with large limit (pre-existing events in live DB) and rid_types filter
        events = await eq.poll(requesting_node, limit=5000, rid_types=["Vault-file"])
        rids = [e["rid"] for e in events]
        assert entity_rid in rids, "Domain event should bypass rid_types filter"

    @pytest.mark.anyio
    async def test_non_domain_event_filtered_by_rid_types(self, pool, conn):
        """A non-domain event with extractable type should be filtered by rid_types."""
        from api.event_queue import EventQueue

        node_rid = f"orn:koi-net.node:test-{_uid()}"
        eq = EventQueue(pool, node_rid)
        requesting_node = f"orn:koi-net.node:requester-{_uid()}"

        # Queue a normal (non-domain) event with an entity-like RID
        entity_rid = f"orn:personal-koi.entity:person-test-{_uid()}"
        await eq.add(
            event_type="NEW",
            rid=entity_rid,
            contents={"name": "just a normal event"},
        )

        # Poll with Vault-file filter — should NOT include entity RID
        events = await eq.poll(requesting_node, limit=5000, rid_types=["Vault-file"])
        rids = [e["rid"] for e in events]
        assert entity_rid not in rids, "Non-domain event should be filtered by rid_types"

    @pytest.mark.anyio
    async def test_domain_event_bypasses_rid_types_in_peek(self, pool, conn):
        """peek_undelivered should also bypass rid_types for domain events."""
        from api.event_queue import EventQueue

        node_rid = f"orn:koi-net.node:test-{_uid()}"
        eq = EventQueue(pool, node_rid)
        target = f"orn:koi-net.node:target-{_uid()}"

        task_rid = f"test-task-{_uid()}"
        await eq.add(
            event_type="NEW",
            rid=task_rid,
            contents={"_koi_domain": "task", "payload": {"task_key": task_rid}},
        )

        events = await eq.peek_undelivered(target, limit=5000, rid_types=["Organization"])
        rids = [e["rid"] for e in events]
        assert task_rid in rids, "Domain event should bypass rid_types in peek_undelivered"


# ---------------------------------------------------------------------------
# 2. KOIPoller dispatch
# ---------------------------------------------------------------------------

class TestPollerDomainDispatch:
    """_process_event routes _koi_domain events to apply_domain_event."""

    @pytest.mark.anyio
    async def test_domain_event_creates_entity(self, pool, conn):
        """Poller should dispatch _koi_domain=entity to domain handler, creating a row."""
        from api.koi_poller import KOIPoller

        poller = KOIPoller.__new__(KOIPoller)
        poller.pool = pool
        poller.vault_sync = None

        uid = _uid()
        fuseki_uri = f"orn:personal-koi.entity:person-poller-test-{uid}"
        contents = {
            "_koi_domain": "entity",
            "payload": {
                "fuseki_uri": fuseki_uri,
                "entity_text": f"Poller Test {uid}",
                "entity_type": "Person",
                "normalized_text": f"poller test {uid}",
            },
        }

        await poller._process_event(
            rid=fuseki_uri,
            event_type="NEW",
            contents=contents,
            manifest=None,
            source_node=TEST_SOURCE_NODE,
        )

        row = await conn.fetchrow(
            "SELECT entity_text, entity_type FROM entity_registry WHERE fuseki_uri = $1",
            fuseki_uri,
        )
        assert row is not None, "Poller should have created entity via domain handler"
        assert row["entity_text"] == f"Poller Test {uid}"
        assert row["entity_type"] == "Person"

    @pytest.mark.anyio
    async def test_non_domain_event_falls_through(self, pool, conn):
        """Events without _koi_domain should not invoke domain handlers."""
        from api.koi_poller import KOIPoller

        poller = KOIPoller.__new__(KOIPoller)
        poller.pool = pool
        poller.vault_sync = None

        uid = _uid()
        fuseki_uri = f"orn:personal-koi.entity:person-fallthrough-{uid}"
        # No _koi_domain marker — this is a normal share/cross-ref event
        contents = {
            "name": "Some Shared Doc",
            "_koi_share": True,
        }

        await poller._process_event(
            rid=f"orn:test:doc-{uid}",
            event_type="NEW",
            contents=contents,
            manifest=None,
            source_node=TEST_SOURCE_NODE,
        )

        # Entity should NOT have been created — it went through share path
        row = await conn.fetchrow(
            "SELECT 1 FROM entity_registry WHERE fuseki_uri = $1", fuseki_uri,
        )
        assert row is None


# ---------------------------------------------------------------------------
# 3. DomainEventHandlers: UPSERT for each domain
# ---------------------------------------------------------------------------

class TestApplyEntity:

    @pytest.mark.anyio
    async def test_insert_new_entity(self, conn):
        from api.domain_event_handlers import apply_domain_event

        uid = _uid()
        fuseki_uri = f"orn:personal-koi.entity:person-test-{uid}"
        payload = {
            "fuseki_uri": fuseki_uri,
            "entity_text": f"Test Person {uid}",
            "entity_type": "Person",
            "normalized_text": f"test person {uid}",
            "aliases": ["TP"],
            "metadata": {"source": "test"},
        }

        await apply_domain_event(conn, "entity", fuseki_uri, "NEW", payload, TEST_SOURCE_NODE)

        row = await conn.fetchrow(
            "SELECT entity_text, entity_type, aliases, metadata FROM entity_registry WHERE fuseki_uri = $1",
            fuseki_uri,
        )
        assert row is not None
        assert row["entity_text"] == f"Test Person {uid}"
        assert row["entity_type"] == "Person"
        assert "TP" in row["aliases"]

    @pytest.mark.anyio
    async def test_upsert_existing_entity(self, conn):
        from api.domain_event_handlers import apply_domain_event

        uid = _uid()
        fuseki_uri = f"orn:personal-koi.entity:org-test-{uid}"
        payload_v1 = {
            "fuseki_uri": fuseki_uri,
            "entity_text": "Org V1",
            "entity_type": "Organization",
            "normalized_text": "org v1",
        }
        await apply_domain_event(conn, "entity", fuseki_uri, "NEW", payload_v1, TEST_SOURCE_NODE)

        # Update same entity
        payload_v2 = {
            "fuseki_uri": fuseki_uri,
            "entity_text": "Org V2",
            "entity_type": "Organization",
            "normalized_text": "org v2",
        }
        await apply_domain_event(conn, "entity", fuseki_uri, "UPDATE", payload_v2, TEST_SOURCE_NODE)

        row = await conn.fetchrow(
            "SELECT entity_text FROM entity_registry WHERE fuseki_uri = $1", fuseki_uri,
        )
        assert row["entity_text"] == "Org V2"

    @pytest.mark.anyio
    async def test_entity_with_relationships(self, conn):
        """Entity handler should upsert relationships when included in payload."""
        from api.domain_event_handlers import apply_domain_event

        uid = _uid()
        uri_a = f"orn:personal-koi.entity:person-a-{uid}"
        uri_b = f"orn:personal-koi.entity:org-b-{uid}"

        # Create both entities first (FK targets for entity_relationships)
        for uri, name, etype in [(uri_a, "PersonA", "Person"), (uri_b, "OrgB", "Organization")]:
            await apply_domain_event(conn, "entity", uri, "NEW", {
                "fuseki_uri": uri, "entity_text": name, "entity_type": etype,
                "normalized_text": name.lower(),
            }, TEST_SOURCE_NODE)

        # Both entities exist, so FK should succeed — no savepoint needed
        await apply_domain_event(conn, "entity", uri_a, "UPDATE", {
            "fuseki_uri": uri_a, "entity_text": "PersonA", "entity_type": "Person",
            "normalized_text": "persona",
            "relationships": [{
                "subject_uri": uri_a,
                "predicate": "affiliated_with",
                "object_uri": uri_b,
                "confidence": 0.95,
            }],
        }, TEST_SOURCE_NODE)

        rel = await conn.fetchrow(
            "SELECT confidence FROM entity_relationships WHERE subject_uri = $1 AND object_uri = $2",
            uri_a, uri_b,
        )
        assert rel is not None
        assert float(rel["confidence"]) == pytest.approx(0.95)


class TestApplyTask:

    @pytest.mark.anyio
    async def test_insert_new_task(self, conn):
        from api.domain_event_handlers import apply_domain_event

        uid = _uid()
        task_key = f"test-task-{uid}"
        payload = {
            "task_key": task_key,
            "title": f"Test Task {uid}",
            "status": "inbox",
            "priority": "high",
        }

        await apply_domain_event(conn, "task", task_key, "NEW", payload, TEST_SOURCE_NODE)

        row = await conn.fetchrow(
            "SELECT title, status, priority FROM task_registry WHERE task_key = $1", task_key,
        )
        assert row is not None
        assert row["title"] == f"Test Task {uid}"
        assert row["status"] == "inbox"
        assert row["priority"] == "high"

    @pytest.mark.anyio
    async def test_upsert_preserves_missing_fields(self, conn):
        """Partial task update should not overwrite fields not in the payload."""
        from api.domain_event_handlers import apply_domain_event

        uid = _uid()
        task_key = f"test-task-{uid}"

        # Initial insert
        await apply_domain_event(conn, "task", task_key, "NEW", {
            "task_key": task_key,
            "title": "Original Title",
            "status": "inbox",
            "priority": "high",
        }, TEST_SOURCE_NODE)

        # Partial update — only title, no status/priority
        await apply_domain_event(conn, "task", task_key, "UPDATE", {
            "task_key": task_key,
            "title": "Updated Title",
        }, TEST_SOURCE_NODE)

        row = await conn.fetchrow(
            "SELECT title, status, priority FROM task_registry WHERE task_key = $1", task_key,
        )
        assert row["title"] == "Updated Title"
        assert row["status"] == "inbox", "Status should be preserved"
        assert row["priority"] == "high", "Priority should be preserved"


class TestApplyClaim:

    @pytest.mark.anyio
    async def test_insert_claim(self, conn):
        from api.domain_event_handlers import apply_domain_event

        uid = _uid()
        claim_rid = f"orn:koi-net.claim:{uid}"
        payload = {
            "claim_rid": claim_rid,
            "claimant_uri": f"orn:personal-koi.entity:person-{uid}",
            "statement": f"Test claim {uid}",
            "claim_type": "observation",
            "verification": "self_reported",
        }

        await apply_domain_event(conn, "claim", claim_rid, "NEW", payload, TEST_SOURCE_NODE)

        row = await conn.fetchrow(
            "SELECT statement, claim_type FROM claims WHERE claim_rid = $1", claim_rid,
        )
        assert row is not None
        assert row["statement"] == f"Test claim {uid}"

    @pytest.mark.anyio
    async def test_claim_with_state_transition(self, conn):
        from api.domain_event_handlers import apply_domain_event

        uid = _uid()
        claim_rid = f"orn:koi-net.claim:{uid}"
        payload = {
            "claim_rid": claim_rid,
            "claimant_uri": f"orn:personal-koi.entity:person-{uid}",
            "statement": "Claim with state",
            "claim_type": "ecological",
            "state_transition": {
                "from_state": "submitted",
                "to_state": "verified",
                "actor": "test-reviewer",
                "created_at": "2026-03-12T00:00:00Z",
            },
        }

        await apply_domain_event(conn, "claim", claim_rid, "NEW", payload, TEST_SOURCE_NODE)

        log = await conn.fetchrow(
            "SELECT from_state, to_state, actor FROM claim_state_log WHERE claim_rid = $1",
            claim_rid,
        )
        assert log is not None
        assert log["to_state"] == "verified"
        assert log["actor"] == "test-reviewer"


class TestApplyAttestation:

    @pytest.mark.anyio
    async def test_insert_attestation(self, conn):
        from api.domain_event_handlers import apply_domain_event

        uid = _uid()
        # Create claim first (FK target)
        claim_rid = f"orn:koi-net.claim:{uid}"
        await apply_domain_event(conn, "claim", claim_rid, "NEW", {
            "claim_rid": claim_rid,
            "claimant_uri": f"orn:personal-koi.entity:person-{uid}",
            "statement": "Attested claim",
            "claim_type": "observation",
        }, TEST_SOURCE_NODE)

        # Create reviewer entity (FK target for reviewer_uri)
        reviewer = f"orn:personal-koi.entity:reviewer-{uid}"
        await apply_domain_event(conn, "entity", reviewer, "NEW", {
            "fuseki_uri": reviewer,
            "entity_text": f"Reviewer {uid}",
            "entity_type": "Person",
            "normalized_text": f"reviewer {uid}",
        }, TEST_SOURCE_NODE)

        att_rid = f"orn:koi-net.attestation:{uid}"
        await apply_domain_event(conn, "attestation", att_rid, "NEW", {
            "attestation_rid": att_rid,
            "claim_rid": claim_rid,
            "reviewer_uri": reviewer,
            "verdict": "approved",
            "rationale": "Looks good",
        }, TEST_SOURCE_NODE)

        row = await conn.fetchrow(
            "SELECT verdict, rationale FROM claim_attestations WHERE attestation_rid = $1",
            att_rid,
        )
        assert row is not None
        assert row["verdict"] == "approved"

    @pytest.mark.anyio
    async def test_attestation_upsert_by_claim_reviewer(self, conn):
        """Same (claim_rid, reviewer_uri) should update, not duplicate."""
        from api.domain_event_handlers import apply_domain_event

        uid = _uid()
        claim_rid = f"orn:koi-net.claim:{uid}"
        await apply_domain_event(conn, "claim", claim_rid, "NEW", {
            "claim_rid": claim_rid,
            "claimant_uri": f"orn:personal-koi.entity:person-{uid}",
            "statement": "Claim",
            "claim_type": "observation",
        }, TEST_SOURCE_NODE)

        # Create reviewer entity (FK target)
        reviewer = f"orn:personal-koi.entity:reviewer-{uid}"
        await apply_domain_event(conn, "entity", reviewer, "NEW", {
            "fuseki_uri": reviewer,
            "entity_text": f"Reviewer {uid}",
            "entity_type": "Person",
            "normalized_text": f"reviewer {uid}",
        }, TEST_SOURCE_NODE)

        att_rid = f"orn:koi-net.attestation:{uid}"

        # First attestation
        await apply_domain_event(conn, "attestation", att_rid, "NEW", {
            "attestation_rid": att_rid,
            "claim_rid": claim_rid,
            "reviewer_uri": reviewer,
            "verdict": "pending",
        }, TEST_SOURCE_NODE)

        # Update same reviewer's attestation
        await apply_domain_event(conn, "attestation", att_rid, "UPDATE", {
            "attestation_rid": att_rid,
            "claim_rid": claim_rid,
            "reviewer_uri": reviewer,
            "verdict": "approved",
            "rationale": "Changed my mind",
        }, TEST_SOURCE_NODE)

        count = await conn.fetchval(
            "SELECT count(*) FROM claim_attestations WHERE claim_rid = $1 AND reviewer_uri = $2",
            claim_rid, reviewer,
        )
        assert count == 1, "Should upsert, not create duplicate"

        row = await conn.fetchrow(
            "SELECT verdict FROM claim_attestations WHERE attestation_rid = $1", att_rid,
        )
        assert row["verdict"] == "approved"


class TestApplyCommitment:

    @pytest.mark.anyio
    async def test_insert_commitment(self, conn):
        from api.domain_event_handlers import apply_domain_event

        uid = _uid()
        c_rid = f"orn:koi-net.commitment:{uid}"
        payload = {
            "commitment_rid": c_rid,
            "pledger_uri": f"orn:personal-koi.entity:person-{uid}",
            "title": f"Commitment {uid}",
            "offer_type": "labor",
            "state": "PROPOSED",
        }

        await apply_domain_event(conn, "commitment", c_rid, "NEW", payload, TEST_SOURCE_NODE)

        row = await conn.fetchrow(
            "SELECT title, state::text FROM commitments WHERE commitment_rid = $1", c_rid,
        )
        assert row is not None
        assert row["title"] == f"Commitment {uid}"
        assert row["state"] == "PROPOSED"


class TestApplyPool:

    @pytest.mark.anyio
    async def test_insert_pool(self, conn):
        from api.domain_event_handlers import apply_domain_event

        uid = _uid()
        p_rid = f"orn:koi-net.commitment-pool:{uid}"
        payload = {
            "pool_rid": p_rid,
            "name": f"Pool {uid}",
            "steward_uri": f"orn:personal-koi.entity:org-{uid}",
            "state": "forming",
        }

        await apply_domain_event(conn, "commitment_pool", p_rid, "NEW", payload, TEST_SOURCE_NODE)

        row = await conn.fetchrow(
            "SELECT name, state FROM commitment_pools WHERE pool_rid = $1", p_rid,
        )
        assert row is not None
        assert row["name"] == f"Pool {uid}"
        assert row["state"] == "forming"


# ---------------------------------------------------------------------------
# 4. FORGET
# ---------------------------------------------------------------------------

class TestForget:

    @pytest.mark.anyio
    async def test_forget_entity(self, conn):
        from api.domain_event_handlers import apply_domain_event

        uid = _uid()
        fuseki_uri = f"orn:personal-koi.entity:person-forget-{uid}"
        await apply_domain_event(conn, "entity", fuseki_uri, "NEW", {
            "fuseki_uri": fuseki_uri,
            "entity_text": "To Be Forgotten",
            "entity_type": "Person",
            "normalized_text": "to be forgotten",
        }, TEST_SOURCE_NODE)

        # Verify exists
        assert await conn.fetchval(
            "SELECT 1 FROM entity_registry WHERE fuseki_uri = $1", fuseki_uri,
        )

        # FORGET
        await apply_domain_event(conn, "entity", fuseki_uri, "FORGET", {
            "fuseki_uri": fuseki_uri,
        }, TEST_SOURCE_NODE)

        assert not await conn.fetchval(
            "SELECT 1 FROM entity_registry WHERE fuseki_uri = $1", fuseki_uri,
        ), "Entity should be deleted after FORGET"

    @pytest.mark.anyio
    async def test_forget_task(self, conn):
        from api.domain_event_handlers import apply_domain_event

        uid = _uid()
        task_key = f"test-forget-task-{uid}"
        await apply_domain_event(conn, "task", task_key, "NEW", {
            "task_key": task_key,
            "title": "Ephemeral Task",
        }, TEST_SOURCE_NODE)

        await apply_domain_event(conn, "task", task_key, "FORGET", {
            "task_key": task_key,
        }, TEST_SOURCE_NODE)

        assert not await conn.fetchval(
            "SELECT 1 FROM task_registry WHERE task_key = $1", task_key,
        )


# ---------------------------------------------------------------------------
# 5. State log deduplication
# ---------------------------------------------------------------------------

class TestStateLogDedup:

    @pytest.mark.anyio
    async def test_duplicate_state_log_skipped(self, conn):
        """Replaying the same state transition should not create duplicate log entries."""
        from api.domain_event_handlers import apply_domain_event

        uid = _uid()
        claim_rid = f"orn:koi-net.claim:{uid}"
        st = {
            "from_state": "submitted",
            "to_state": "verified",
            "actor": "test",
            "created_at": "2026-03-12T01:00:00Z",
        }
        base_payload = {
            "claim_rid": claim_rid,
            "claimant_uri": f"orn:entity:{uid}",
            "statement": "Dedup test",
            "claim_type": "observation",
            "state_transition": st,
        }

        # Apply twice (simulating replay)
        await apply_domain_event(conn, "claim", claim_rid, "NEW", base_payload, TEST_SOURCE_NODE)
        await apply_domain_event(conn, "claim", claim_rid, "UPDATE", base_payload, TEST_SOURCE_NODE)

        count = await conn.fetchval(
            "SELECT count(*) FROM claim_state_log WHERE claim_rid = $1 AND to_state = $2",
            claim_rid, "verified",
        )
        assert count == 1, "Duplicate state transition should be skipped"

    @pytest.mark.anyio
    async def test_state_log_without_created_at_inserts_each_time(self, conn):
        """Without created_at, dedup is not possible — each replay inserts."""
        from api.domain_event_handlers import apply_domain_event

        uid = _uid()
        claim_rid = f"orn:koi-net.claim:{uid}"
        st_no_ts = {
            "from_state": "submitted",
            "to_state": "verified",
            "actor": "test",
            # No created_at — dedup is skipped
        }
        payload = {
            "claim_rid": claim_rid,
            "claimant_uri": f"orn:entity:{uid}",
            "statement": "No-ts test",
            "claim_type": "observation",
            "state_transition": st_no_ts,
        }

        await apply_domain_event(conn, "claim", claim_rid, "NEW", payload, TEST_SOURCE_NODE)
        await apply_domain_event(conn, "claim", claim_rid, "UPDATE", payload, TEST_SOURCE_NODE)

        count = await conn.fetchval(
            "SELECT count(*) FROM claim_state_log WHERE claim_rid = $1 AND to_state = $2",
            claim_rid, "verified",
        )
        # Without created_at, each call inserts (no dedup possible)
        assert count == 2


# ---------------------------------------------------------------------------
# 6. FederationEvents singleton
# ---------------------------------------------------------------------------

class TestFederationEventsSingleton:

    @pytest.mark.anyio
    async def test_emit_noop_when_no_queue(self):
        """emit_domain_event should silently no-op when event queue is not set."""
        import api.federation_events as fe

        saved = fe._event_queue
        fe.set_event_queue(None)
        try:
            # Should not raise
            await fe.emit_domain_event("entity", "NEW", "orn:test:noop", {"fuseki_uri": "x"})
        finally:
            fe.set_event_queue(saved)

    @pytest.mark.anyio
    async def test_emit_queues_event(self, pool, conn):
        """emit_domain_event should add an event to the queue with _koi_domain marker."""
        from api.event_queue import EventQueue
        import api.federation_events as fe

        saved = fe._event_queue
        node_rid = f"orn:koi-net.node:emit-test-{_uid()}"
        eq = EventQueue(pool, node_rid)
        fe.set_event_queue(eq)
        try:
            uid = _uid()
            rid = f"orn:test:emit-{uid}"
            await fe.emit_domain_event("entity", "NEW", rid, {"fuseki_uri": rid})

            # Check event was queued
            row = await conn.fetchrow(
                "SELECT contents FROM koi_net_events WHERE rid = $1", rid,
            )
            assert row is not None
            contents = json.loads(row["contents"]) if isinstance(row["contents"], str) else row["contents"]
            assert contents["_koi_domain"] == "entity"
            assert contents["payload"]["fuseki_uri"] == rid
        finally:
            fe.set_event_queue(saved)

    @pytest.mark.anyio
    async def test_emit_swallows_exception(self, pool, conn):
        """emit_domain_event should log but not raise on errors."""
        import api.federation_events as fe

        saved = fe._event_queue

        class _BrokenQueue:
            async def add(self, **kw):
                raise RuntimeError("boom")

        fe.set_event_queue(_BrokenQueue())
        try:
            # Should not raise
            await fe.emit_domain_event("entity", "NEW", "orn:test:broken", {"x": 1})
        finally:
            fe.set_event_queue(saved)


# ---------------------------------------------------------------------------
# 7. Unknown domain
# ---------------------------------------------------------------------------

class TestUnknownDomain:

    @pytest.mark.anyio
    async def test_unknown_domain_is_noop(self, conn):
        """An unrecognized _koi_domain should log a warning but not raise."""
        from api.domain_event_handlers import apply_domain_event

        # Should not raise
        await apply_domain_event(
            conn, "unicorn", "orn:test:unknown", "NEW", {"x": 1}, TEST_SOURCE_NODE,
        )
