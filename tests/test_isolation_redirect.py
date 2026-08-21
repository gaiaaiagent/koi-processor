"""Regression test: the conftest redirect must point every DSN at personal_koi_test.

Added 2026-08-21 (Plan A step 4). Guards the mechanism described in conftest.py:
if someone removes the module-level env assignment, or a new test builds a
connection from an unconditional literal, these fail immediately rather than
five months later.
"""
import os
import asyncio
import asyncpg


def test_env_was_redirected():
    """The redirect landed before this module was imported."""
    assert "personal_koi_test" in os.environ["POSTGRES_URL"], os.environ["POSTGRES_URL"]
    assert os.environ["POSTGRES_URL"].endswith("personal_koi_test")


def test_default_dsn_pattern_resolves_to_test_db():
    """The dominant in-repo pattern now yields the test DB."""
    dsn = os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")
    assert dsn.endswith("personal_koi_test"), dsn


def test_writes_land_in_test_db_not_live():
    """A real write goes to personal_koi_test."""
    async def _run():
        conn = await asyncpg.connect(os.environ["POSTGRES_URL"])
        try:
            db = await conn.fetchval("select current_database()")
            await conn.execute(
                "insert into entity_registry (fuseki_uri, entity_text, normalized_text, entity_type, source) "
                "values ($1,$2,$3,$4,$5) on conflict do nothing",
                "orn:canary:isolation-probe", "CANARY test entity",
                "canary test entity", "Concept", "pytest",
            )
            n = await conn.fetchval(
                "select count(*) from entity_registry where fuseki_uri='orn:canary:isolation-probe'")
            return db, n
        finally:
            await conn.close()
    db, n = asyncio.run(_run())
    assert db == "personal_koi_test", f"wrote to {db}!"
    assert n == 1
