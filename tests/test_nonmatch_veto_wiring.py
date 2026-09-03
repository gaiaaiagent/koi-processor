"""The veto must be REFUSED BY resolve_entity(), not merely present in the table.

This file exists because of a specific failure shape: entity_non_match was
seeded with 44 operator-adjudicated pairs and, for one commit, nothing consulted
it. A test asserting "the row is in the table" would have passed the entire time
while every ingest happily re-merged the pairs an operator had separated.

So every assertion here drives the REAL entry point.
"""

import os

import asyncpg
import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


DSN = os.environ.get("POSTGRES_URL")


@pytest.fixture
async def conn():
    if not DSN:
        pytest.skip("POSTGRES_URL not set")
    c = await asyncpg.connect(DSN)
    tx = c.transaction()
    await tx.start()
    yield c
    await tx.rollback()
    await c.close()


async def _seed_pair(conn, a_uri, a_text, b_uri, b_text, reason="test veto"):
    for uri, text in ((a_uri, a_text), (b_uri, b_text)):
        await conn.execute(
            "INSERT INTO entity_registry (fuseki_uri, entity_text, entity_type, normalized_text) "
            "VALUES ($1,$2,'Organization',$3) ON CONFLICT (fuseki_uri) DO NOTHING",
            uri, text, text.lower())
    await conn.execute(
        "INSERT INTO entity_non_match (uri_lo, uri_hi, asserted_by, reason) "
        "VALUES ($1,$2,'test',$3) ON CONFLICT DO NOTHING", a_uri, b_uri, reason)


async def test_the_veto_has_a_reader_at_all(conn):
    """The guard against 'correctly-built table with no reader'."""
    from api.personal_ingest_api import is_vetoed_match, resolve_entity
    import inspect

    src = inspect.getsource(resolve_entity)
    assert "is_vetoed_match" in src, \
        "resolve_entity() does not consult entity_non_match -- the table has no reader"
    # Every tier that can accept an existing entity must consult it.
    for tier in ("tier1_exact", "tier1_1_alias", "tier1_1b_cross_type",
                 "tier1_5_contextual", "tier2a_fuzzy", "tier2b_semantic"):
        assert tier in src, f"{tier} accept point missing from resolve_entity"
    assert src.count("is_vetoed_match") >= 6, (
        f"only {src.count('is_vetoed_match')} veto checks for 6 accept tiers -- "
        "a veto consulted at some tiers and not others is a partial guard")


async def test_vetoed_pair_is_refused_by_resolve_entity(conn):
    """The assertion that matters: the real resolver refuses, not just the table."""
    from api.personal_ingest_api import resolve_entity, ExtractedEntity

    A = "orn:personal-koi.entity:organization-veto-alpha-aaaa11112222"
    B = "orn:personal-koi.entity:organization-veto-beta-bbbb33334444"
    await _seed_pair(conn, A, "Veto Alpha Corp", B, "Veto Alpha Corp Holdings")

    # Without a veto, an exact-name query resolves to the matching row.
    ent = ExtractedEntity(name="Veto Alpha Corp", type="Organization")
    canonical, is_new = await resolve_entity(conn, ent)
    assert canonical.uri == A and not is_new

    # Now veto A against itself-by-name via a second row carrying the same name.
    C = "orn:personal-koi.entity:organization-veto-gamma-cccc55556666"
    await conn.execute(
        "INSERT INTO entity_registry (fuseki_uri, entity_text, entity_type, normalized_text) "
        "VALUES ($1,'Veto Alpha Corp','Organization','veto alpha corp') "
        "ON CONFLICT (fuseki_uri) DO NOTHING", C)
    await conn.execute(
        "INSERT INTO entity_non_match (uri_lo, uri_hi, asserted_by, reason) "
        "VALUES ($1,$2,'test','C and A are different real orgs') ON CONFLICT DO NOTHING",
        min(A, C), max(A, C))

    canonical2, is_new2 = await resolve_entity(conn, ExtractedEntity(
        name="Veto Alpha Corp", type="Organization"))
    assert canonical2.uri != A or is_new2, (
        "resolve_entity accepted a candidate that entity_non_match forbids")


async def test_a_real_seeded_override_is_refused(conn):
    """Cascadia North -> CNSS is operator testimony; the resolver must honour it.

    This asserts against the 44 REAL seeded pairs, which live in personal_koi.
    conftest points tests at personal_koi_test, which has the table and none of
    the data -- so this SKIPS there rather than passing vacuously, and is
    meaningful when run against the live database.
    """
    from api.personal_ingest_api import is_vetoed_match

    if not await conn.fetchval(
            "SELECT count(*) FROM entity_non_match WHERE asserted_by LIKE '%ADJUDICATION%'"):
        pytest.skip("operator overrides not seeded in this database (expected in personal_koi_test)")

    CNSS = "orn:personal-koi.entity:organization-cnss-27e28c2235a8"
    reason = await is_vetoed_match(conn, "cascadia north", CNSS)
    assert reason, "the Cascadia North / CNSS operator override is not being enforced"
    assert "Override 1" in reason


async def test_uri_that_does_not_reproduce_is_still_vetoed(conn):
    """28% of vetoed URIs do not regenerate from (name, type).

    Salt Spring Digital Ecologies is one. A veto keyed on a synthesised URI
    would silently miss it -- which is why the lookup goes through the registry.
    """
    from api.personal_ingest_api import is_vetoed_match

    if not await conn.fetchval(
            "SELECT count(*) FROM entity_non_match WHERE asserted_by LIKE '%ADJUDICATION%'"):
        pytest.skip("operator overrides not seeded in this database")

    SSAI = "orn:personal-koi.entity:organization-salt-spring-ai-0d9fe1ba1105"
    reason = await is_vetoed_match(conn, "salt spring digital ecologies", SSAI)
    assert reason and "Override 2" in reason
