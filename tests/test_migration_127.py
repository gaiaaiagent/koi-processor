"""Migration 127 (fact_retraction_ledger) — house pattern and dry run.

Same two layers as tests/test_migration_126.py, whose checker this file imports
so the pattern is enforced from one place:

1. STRUCTURAL — the up/down files follow the 124/125/126 house pattern
   (BEGIN/COMMIT, ledger row AFTER the assertion, ON_ERROR_STOP + ledger delete
   on the down file). 126 is the positive control; a doctored 127-up with the
   ledger line removed is the negative control.
2. DRY RUN — the migration bodies execute on the scratch database inside ONE
   rolled-back transaction. Asserts the exact constraints the code relies on
   (state vocabulary, the unique key the recipient upserts on, the
   event-presence check), idempotence, and that the down file leaves nothing.
   Negative control: the up assertion RAISES when the state CHECK is missing a
   state, i.e. it is a vocabulary check and not "tables exist".

ISOLATION. Nothing here talks to :8351 or to personal_koi. The fixture refuses
the live database by name. The scratch DB has no 127 tables (verified at the
start of each test), so `CREATE TABLE IF NOT EXISTS` really creates, and the
rollback removes it again.
"""

from __future__ import annotations

import os
import pathlib
import re

import asyncpg
import pytest

from tests.test_migration_126 import house_pattern_violations, _body

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATIONS = REPO_ROOT / "migrations"

UP_127 = MIGRATIONS / "127_fact_retraction_ledger.sql"
DOWN_127 = MIGRATIONS / "127_fact_retraction_ledger_down.sql"
LEDGER_ID_127 = "personal:127_fact_retraction_ledger"

# The nine states api/fact_retraction.py names. Written out, not imported, so a
# test of the SQL cannot be satisfied by the Python drifting in step with it.
STATES = frozenset({
    "queued", "delivered", "received", "applied", "rejected",
    "retrying", "failed", "unverifiable", "unauthorized",
})

DB_URL = os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi_test")


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ═══════════════════════════════════════════════════════════════════════════
# 1. Structural
# ═══════════════════════════════════════════════════════════════════════════

class TestHousePattern:

    def test_positive_control_126_conforms(self):
        assert house_pattern_violations(MIGRATIONS / "126_document_extraction_type_registry.sql") == []

    def test_negative_control_checker_rejects_a_missing_ledger_line(self):
        text = UP_127.read_text()
        doctored = re.sub(
            r"INSERT\s+INTO\s+koi_migrations.*?DO\s+NOTHING\s*;", "", text, flags=re.S | re.I)
        bad = house_pattern_violations(UP_127, doctored)
        assert any("INSERT INTO koi_migrations" in b for b in bad), bad

    def test_127_up_conforms(self):
        assert house_pattern_violations(UP_127) == [], house_pattern_violations(UP_127)

    def test_127_down_conforms(self):
        assert house_pattern_violations(DOWN_127) == [], house_pattern_violations(DOWN_127)

    def test_127_down_exports_before_dropping(self):
        """The rows live nowhere else; the down file must export first, below ON_ERROR_STOP."""
        text = DOWN_127.read_text()
        i_stop = text.index("\\set ON_ERROR_STOP on")
        i_copy = text.index("\\copy (SELECT * FROM knowledge_fact_retractions")
        i_copy2 = text.index("\\copy (SELECT * FROM knowledge_fact_retraction_deliveries")
        i_drop = text.index("DROP TABLE IF EXISTS knowledge_fact_retraction_deliveries")
        assert i_stop < i_copy < i_drop and i_stop < i_copy2 < i_drop

    def test_127_down_uses_literal_copy_paths(self):
        """`\\copy` does not interpolate :'var' in a filename (125's lesson)."""
        for ln in DOWN_127.read_text().splitlines():
            if ln.startswith("\\copy"):
                assert re.search(r"TO '/tmp/[^']+'", ln), ln
                assert ":'" not in ln and ":\"" not in ln, ln


# ═══════════════════════════════════════════════════════════════════════════
# 2. Dry run
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
async def conn():
    c = await asyncpg.connect(DB_URL)
    db = await c.fetchval("SELECT current_database()")
    assert db != "personal_koi", f"refusing to run against {db!r}"
    tx = c.transaction()
    await tx.start()
    # Precondition for every test below: the scratch DB does not carry 127.
    assert await c.fetchval("SELECT to_regclass('knowledge_fact_retractions')") is None, (
        "scratch DB already has knowledge_fact_retractions — 127 was applied there; "
        "these tests assume a clean slate")
    yield c
    await tx.rollback()
    await c.close()


async def _ledger_rows(conn):
    return await conn.fetch(
        "SELECT migration_id, checksum FROM koi_migrations WHERE migration_id = $1",
        LEDGER_ID_127)


async def _state_check(conn) -> str | None:
    return await conn.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'knowledge_fact_retraction_deliveries_state_check'")


@pytest.mark.anyio
class TestDryRun:

    async def test_up_creates_both_tables_with_the_exact_constraints(self, conn):
        await conn.execute(_body(UP_127))

        assert await conn.fetchval("SELECT to_regclass('knowledge_fact_retractions')") is not None
        assert await conn.fetchval(
            "SELECT to_regclass('knowledge_fact_retraction_deliveries')") is not None

        check = await _state_check(conn)
        assert check is not None
        for s in STATES:
            assert f"'{s}'" in check, f"state {s!r} missing from CHECK: {check}"
        # Nothing beyond the nine.
        found = set(re.findall(r"'([a-z_]+)'", check))
        assert found == STATES, found

        assert [dict(r) for r in await _ledger_rows(conn)] == [
            {"migration_id": LEDGER_ID_127, "checksum": "v1_retraction_ledger_and_deliveries"}]

    async def test_up_is_idempotent(self, conn):
        await conn.execute(_body(UP_127))
        await conn.execute(_body(UP_127))
        assert len(await _ledger_rows(conn)) == 1

    async def test_unique_key_is_fact_valid_to_origin(self, conn):
        """The recipient upserts on (fact_id, valid_to, origin_node); a second
        identical tombstone must conflict, a different origin must not."""
        await conn.execute(_body(UP_127))
        fid = await conn.fetchval("SELECT gen_random_uuid()")
        ins = ("INSERT INTO knowledge_fact_retractions (fact_id, valid_to, origin_node) "
               "VALUES ($1, '2026-09-14T15:13:07.924484+00:00', $2)")
        await conn.execute(ins, fid, "orn:koi-net.node:a")
        await conn.execute(ins, fid, "orn:koi-net.node:b")   # different origin: allowed
        await conn.execute("SAVEPOINT dup")
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await conn.execute(ins, fid, "orn:koi-net.node:a")
        await conn.execute("ROLLBACK TO SAVEPOINT dup")

    async def test_state_vocabulary_is_enforced(self, conn):
        await conn.execute(_body(UP_127))
        rid = await conn.fetchval(
            "INSERT INTO knowledge_fact_retractions (fact_id, valid_to, origin_node) "
            "VALUES (gen_random_uuid(), NOW(), 'orn:koi-net.node:a') RETURNING id")
        await conn.execute("SAVEPOINT bad_state")
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "INSERT INTO knowledge_fact_retraction_deliveries "
                "(retraction_id, target_node, event_id, state) "
                "VALUES ($1, 'orn:koi-net.node:p', gen_random_uuid(), 'confirmed')", rid)
        await conn.execute("ROLLBACK TO SAVEPOINT bad_state")

    async def test_event_presence_check(self, conn):
        """unauthorized ⇔ event_id IS NULL. Both directions refused."""
        await conn.execute(_body(UP_127))
        rid = await conn.fetchval(
            "INSERT INTO knowledge_fact_retractions (fact_id, valid_to, origin_node) "
            "VALUES (gen_random_uuid(), NOW(), 'orn:koi-net.node:a') RETURNING id")
        base = ("INSERT INTO knowledge_fact_retraction_deliveries "
                "(retraction_id, target_node, event_id, state) VALUES ($1, $2, $3, $4)")
        # queued without an event: refused
        await conn.execute("SAVEPOINT p1")
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(base, rid, "orn:koi-net.node:p1", None, "queued")
        await conn.execute("ROLLBACK TO SAVEPOINT p1")
        # unauthorized with an event: refused
        await conn.execute("SAVEPOINT p2")
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(base, rid, "orn:koi-net.node:p2",
                               await conn.fetchval("SELECT gen_random_uuid()"), "unauthorized")
        await conn.execute("ROLLBACK TO SAVEPOINT p2")
        # the two legal shapes
        await conn.execute(base, rid, "orn:koi-net.node:p3", None, "unauthorized")
        await conn.execute(base, rid, "orn:koi-net.node:p4",
                           await conn.fetchval("SELECT gen_random_uuid()"), "queued")

    async def test_deliveries_cascade_with_their_retraction(self, conn):
        await conn.execute(_body(UP_127))
        rid = await conn.fetchval(
            "INSERT INTO knowledge_fact_retractions (fact_id, valid_to, origin_node) "
            "VALUES (gen_random_uuid(), NOW(), 'orn:koi-net.node:a') RETURNING id")
        await conn.execute(
            "INSERT INTO knowledge_fact_retraction_deliveries "
            "(retraction_id, target_node, event_id, state) "
            "VALUES ($1, 'orn:koi-net.node:p', gen_random_uuid(), 'queued')", rid)
        await conn.execute("DELETE FROM knowledge_fact_retractions WHERE id = $1", rid)
        assert await conn.fetchval(
            "SELECT count(*) FROM knowledge_fact_retraction_deliveries WHERE retraction_id = $1",
            rid) == 0

    async def test_down_drops_both_and_removes_the_ledger_row(self, conn):
        await conn.execute(_body(UP_127))
        assert len(await _ledger_rows(conn)) == 1
        await conn.execute(_body(DOWN_127))
        assert await conn.fetchval("SELECT to_regclass('knowledge_fact_retractions')") is None
        assert await conn.fetchval(
            "SELECT to_regclass('knowledge_fact_retraction_deliveries')") is None
        assert await _ledger_rows(conn) == []

    async def test_negative_control_the_up_assertion_checks_the_state_vocabulary(self, conn):
        """A CHECK missing one state must make the assertion RAISE and name it.

        Built by running the body with 'unverifiable' removed from the CHECK
        (a plausible typo-class drift) — the tables exist, so a mere existence
        assertion would pass.
        """
        body = _body(UP_127).replace("'unverifiable', ", "", 1)
        assert "'unverifiable'" in body, "the DO-block's expected list must still name it"
        with pytest.raises(asyncpg.exceptions.PostgresError) as exc_info:
            await conn.execute(body)
        assert "unverifiable" in str(exc_info.value)
