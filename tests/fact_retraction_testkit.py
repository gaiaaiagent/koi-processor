"""Shared fixtures for the #67 fact-retraction suites.

Every helper here writes ONLY on the connection it is given, inside the
caller's rolled-back transaction. Nothing touches personal_koi, :8351, or a
live peer. Used by:

    tests/test_fact_retraction_boundary.py      (boundary pins + requirements)
    tests/test_fact_retraction_outbox.py        (publisher side)
    tests/test_fact_retraction_apply.py         (recipient side)
    tests/integration/test_federation_retraction.py
    tests/test_audit_fact_retractions.py
"""

from __future__ import annotations

import os
import pathlib
import uuid
from typing import Optional

import asyncpg

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATIONS = REPO_ROOT / "migrations"
UP_127 = MIGRATIONS / "127_fact_retraction_ledger.sql"

DB_URL = os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi_test")

NODE_A = "orn:koi-net.node:test-retract-a+aaaa"          # publisher / retracting node
NODE_B = "orn:koi-net.node:test-retract-b+bbbb"          # a recipient (integration)
PEER_AUTH = "orn:koi-net.node:test-retract-auth+b1"      # approved edge, knowledge_fact in scope
PEER_NARROW = "orn:koi-net.node:test-retract-narrow+b2"  # approved edge, scope lacks knowledge_fact
PEER_REVOKED = "orn:koi-net.node:test-retract-revoked+b3"  # edge REVOKED
ALL_PEERS = (PEER_AUTH, PEER_NARROW, PEER_REVOKED, NODE_B)

SVC_TOKEN = "test-retract-service-token"
SVC_AUTH = {"Authorization": f"Bearer {SVC_TOKEN}"}

# The incident's committed valid_to for fact aaca4875…, to the microsecond.
INCIDENT_VALID_TO = "2026-09-14T15:13:07.924484+00:00"


class SingleConnPool:
    """One asyncpg.Connection quacking like asyncpg.Pool (house pattern)."""

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


async def open_scratch_conn() -> tuple[asyncpg.Connection, asyncpg.transaction.Transaction]:
    """Connect, refuse the live database by name, start the transaction, pre-scope the queue.

    Returns (conn, tx); pass both to close_scratch_conn. (asyncpg.Connection
    uses __slots__, so the transaction cannot be hung on the connection.)
    """
    c = await asyncpg.connect(DB_URL)
    db = await c.fetchval("SELECT current_database()")
    assert db != "personal_koi", (
        f"refusing to run: connected to {db!r}; these tests write koi_net_events, "
        f"koi_net_edges, knowledge_facts and the retraction ledger")
    tx = c.transaction()
    await tx.start()
    # Pre-scope the queue exactly as tests/integration/test_federation_knowledge.py
    # does, so a real poll() only ever surfaces rows this test queued.
    for peer in ALL_PEERS:
        await c.execute(
            """
            UPDATE koi_net_events SET delivered_to = array_append(delivered_to, $1)
            WHERE NOT ($1 = ANY(delivered_to)) AND expires_at > NOW()
            """,
            peer,
        )
    return c, tx


async def close_scratch_conn(c: asyncpg.Connection, tx) -> None:
    await tx.rollback()
    await c.close()


_META = ("BEGIN;", "COMMIT;")


def migration_body(path: pathlib.Path) -> str:
    """The migration minus its own BEGIN/COMMIT and psql meta-commands (see test_migration_126)."""
    kept = []
    for ln in path.read_text().splitlines():
        s = ln.strip()
        if s in _META or s.startswith("\\"):
            continue
        kept.append(ln)
    return "\n".join(kept)


async def apply_ledger_migration(conn: asyncpg.Connection) -> None:
    """Run migration 127's body on `conn` (inside the rolled-back transaction).

    The scratch database does not carry 127 (tests/test_migration_127.py
    asserts that), so this really creates the tables, and teardown removes them.
    """
    if await conn.fetchval("SELECT to_regclass('knowledge_fact_retractions')") is not None:
        return
    await conn.execute(migration_body(UP_127))


async def neutralize_edge_scope_drift(conn: asyncpg.Connection) -> None:
    """personal_koi_test carries `119_edge_scope_contexts` from an UNMERGED branch.

    Verified 2026-09-15: the scratch database's koi_migrations lists
    `119_edge_scope_contexts` (no such file exists in this checkout; the commit
    is on `indigenomics/extraction-resilience` and siblings) and its
    koi_net_edges carries CHECK `koi_is_rid_context_array(rid_types)`, which
    rejects every legacy-style scope value (`Person`, `knowledge_fact`, …). The
    LIVE database has neither the columns nor the constraint, and the code in
    this checkout — `events_poll` and `EventQueue.poll` — reads `rid_types` as
    the scope. Tests of THIS code therefore seed edges the way the live table
    holds them, and drop the foreign constraint for the duration of the
    rolled-back transaction (DDL is transactional in PostgreSQL, so the scratch
    schema is untouched after teardown).
    """
    await conn.execute(
        "ALTER TABLE koi_net_edges DROP CONSTRAINT IF EXISTS koi_net_edges_rid_types_are_contexts")


async def seed_edge(conn, source: str, target: str, status: str, scope: Optional[list]) -> None:
    await neutralize_edge_scope_drift(conn)
    await conn.execute(
        """
        INSERT INTO koi_net_edges (edge_rid, source_node, target_node, edge_type, status, rid_types)
        VALUES ($1, $2, $3, 'POLL', $4, $5)
        ON CONFLICT (edge_rid) DO UPDATE SET status = EXCLUDED.status, rid_types = EXCLUDED.rid_types
        """,
        f"orn:koi-net.edge:{source}>{target}:poll", source, target, status, scope,
    )


async def seed_edges(conn, source: str = NODE_A) -> None:
    """Three outbound POLL edges from `source`, one per recipient posture."""
    await seed_edge(conn, source, PEER_AUTH, "APPROVED", ["Person", "knowledge_episode", "knowledge_fact"])
    await seed_edge(conn, source, PEER_NARROW, "APPROVED", ["Person", "Concept"])
    await seed_edge(conn, source, PEER_REVOKED, "REVOKED", ["knowledge_fact", "knowledge_episode"])


async def seed_fact(conn, *, valid_to=None, episode_id: Optional[str] = None,
                    source_node_rid: str = "session:boundary"):
    """A minimal episode + one fact. Returns (episode_id, fact_id) as strings."""
    ep = uuid.UUID(episode_id) if episode_id else uuid.uuid4()
    if not episode_id:
        await conn.execute(
            """
            INSERT INTO knowledge_episodes (id, name, content, source_description, source_document,
                                            group_id, valid_at, created_at, metadata)
            VALUES ($1, 'boundary episode', 'c', 'boundary', 'boundary.md', 'personal',
                    NOW(), NOW(), '{}'::jsonb)
            """,
            ep,
        )
    fid = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO knowledge_facts (id, episode_id, subject_uri, predicate, object_uri,
                                     fact_text, valid_from, valid_to, group_id, source_node_rid)
        VALUES ($1, $2, 'orn:entity:s', 'TEST_PRED', 'orn:entity:o', 'boundary fact',
                NOW(), $3, 'personal', $4)
        """,
        fid, ep, valid_to, source_node_rid,
    )
    return str(ep), str(fid)


def fact_payload(fact_id: str, episode_id: str, *, valid_to=None, source_node=NODE_A):
    """A fact in the exact key set `_insert_fact` reads."""
    return {
        "id": fact_id, "episode_id": episode_id,
        "subject_uri": "orn:entity:s", "predicate": "TEST_PRED", "object_uri": "orn:entity:o",
        "object_literal": None, "fact_text": "boundary fact",
        "valid_from": "2026-09-14T00:00:00+00:00", "valid_to": valid_to,
        "created_at": "2026-09-14T00:00:00+00:00", "group_id": "personal",
        "source_node_rid": "session:boundary", "turn_range_start": None,
        "turn_range_end": None, "embedding_column": None, "embedding_value": None,
    }


def episode_payload(episode_id: str, facts: list, *, event_id: Optional[str] = None):
    p = {
        "id": episode_id, "name": "boundary episode", "content": "c",
        "source_description": "boundary", "source_document": "boundary.md",
        "group_id": "personal", "valid_at": "2026-09-14T00:00:00+00:00",
        "created_at": "2026-09-14T00:00:00+00:00", "metadata": {}, "facts": facts,
    }
    if event_id:
        p["_federation_event_id"] = event_id
    return p


def retraction_payload(fact_id: str, episode_id: str, *, valid_to: str = INCIDENT_VALID_TO,
                       origin_node: str = NODE_A, event_id: Optional[str] = None,
                       retraction_id: int = 1, attempt: int = 1):
    """A retraction event payload in the wire shape api/fact_retraction.py builds."""
    p = fact_payload(fact_id, episode_id, valid_to=valid_to)
    p["retraction"] = {
        "retraction_id": retraction_id, "origin_node": origin_node, "valid_to": valid_to,
        "reason": "testkit", "retracted_by": "service:test", "attempt": attempt,
        "episode_rid": f"orn:personal-koi.knowledge-episode:{episode_id}",
        "source_document": "boundary.md", "original_event_ids": [],
    }
    p["_federation_event_id"] = event_id or str(uuid.uuid4())
    return p


class TwoConnPool:
    """A pool whose acquire() hands out a DIFFERENT real connection than the caller's.

    `SingleConnPool` cannot prove that `EventQueue.add(conn=...)` matters: with
    one connection the pool path and the caller path are the same connection.
    Here `caller` is the connection the code under test holds a transaction on
    and `other` is what `pool.acquire()` returns (its own transaction, also
    rolled back at teardown). A write that was supposed to ride the caller's
    transaction is visible on `caller` and NOT on `other`; a write that leaked
    through the pool is the reverse.
    """

    def __init__(self, caller, other):
        self.caller = caller
        self.other = other

    class _CM:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, *a):
            pass

    def acquire(self):
        return self._CM(self.other)


async def open_second_scratch_conn():
    """A second connection + transaction on the scratch DB (no queue pre-scoping)."""
    c = await asyncpg.connect(DB_URL)
    db = await c.fetchval("SELECT current_database()")
    assert db != "personal_koi", f"refusing to run against {db!r}"
    tx = c.transaction()
    await tx.start()
    return c, tx
