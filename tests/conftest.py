"""Test isolation for the koi-processor suite.

WHY THIS FILE EXISTS
--------------------
Until 2026-08-21 this repository had no conftest.py at all, while 25 of its test
files name ``personal_koi`` — the live personal-KOI database. Running the test
suite was therefore a write operation against the production knowledge graph.
That is not hypothetical: 646 test-fixture entities accumulated in the live graph
between 2026-03-24 and 2026-08-16 because the intent suite archived the intent
but never the entity row.

THE MECHANISM, AND WHY IT IS AT MODULE LEVEL
--------------------------------------------
The environment MUST be set before pytest imports any test module. A fixture —
including a session-scoped autouse one — cannot do this: pytest imports test
modules during *collection*, and fixtures run afterwards. Several modules in this
suite read the DSN at import time, e.g.

    tests/test_graph_traversal.py:7   DB_URL = os.getenv("POSTGRES_URL", "<live dsn>")
    tests/test_deep_extract_lease.py:32  os.environ.setdefault("POSTGRES_URL", "<live dsn>")

pytest imports the *root* conftest.py before collecting sibling modules, so
assigning here — at import time, not inside a function — is what actually lands
before those reads. The ``setdefault`` cases above are also handled, precisely
because this file sets the variable first and setdefault then declines to
override it.

An audit of the DSN surface (2026-08-21) found every test path reads one of the
variables set below, either via ``os.getenv(VAR, "<hardcoded default>")`` or via
``os.environ.setdefault``. No test constructs a connection from an unconditional
literal. If that ever changes, the tripwire below is what will notice.
"""

from __future__ import annotations

import os

# --------------------------------------------------------------------------
# 1. Redirect. Module level, before any test module is imported.
# --------------------------------------------------------------------------
TEST_DSN = "postgresql://darrenzal:@localhost:5432/personal_koi_test"
LIVE_DB_NAME = "personal_koi"

# Assign unconditionally: an inherited POSTGRES_URL pointing at the live database
# is exactly the situation this file exists to prevent, so it must not win.
os.environ["POSTGRES_URL"] = TEST_DSN
os.environ["DATABASE_URL"] = TEST_DSN
os.environ["POSTGRES_TEST_URL"] = TEST_DSN
os.environ["POSTGRES_DB"] = "personal_koi_test"
os.environ["POSTGRES_HOST"] = "localhost"
os.environ["POSTGRES_PORT"] = "5432"

import pytest  # noqa: E402  (must follow the env assignment above)


def pytest_configure(config):
    """Second belt: re-assert the redirect after plugin loading.

    Harmless if the module-level assignment already held; the point is that a
    plugin or a stray ``.env`` load between import and collection cannot quietly
    put the live DSN back.
    """
    os.environ["POSTGRES_URL"] = TEST_DSN
    os.environ["DATABASE_URL"] = TEST_DSN
    os.environ["POSTGRES_TEST_URL"] = TEST_DSN


# --------------------------------------------------------------------------
# 2. Tripwire. NOT the isolation mechanism — the proof that it held.
# --------------------------------------------------------------------------
# Detection is not isolation: by the time this fires, a leak has already been
# written. It exists so that a redirect failure is loud rather than silent, and
# so that a future test which builds a connection from an unconditional literal
# is caught the first time it runs instead of five months later.

# NOTE ON THE TIMESTAMP: entity_registry.created_at is `timestamp WITHOUT time
# zone`. Passing an aware datetime.now(timezone.utc) makes asyncpg raise
# DataError("can't subtract offset-naive and offset-aware datetimes"). That is
# not theoretical — the first version of this file did exactly that, the broad
# `except Exception: return None` below swallowed it, and a deliberate-leak
# canary WROTE A ROW TO THE LIVE DATABASE while the session reported green.
# The marker is therefore taken from the server (`select now()`) and compared
# in-DB, so no client-side timezone can enter the comparison at all.
_MARKER_SQL = "SELECT clock_timestamp()::timestamp"

# The signature has to match how the leaking writer actually labels its rows,
# and the name-based clauses below do not. Measured against the 25 rows one
# tests/test_intent_registry.py run wrote to the live graph on 2026-08-22:
# only 3 matched ("Test firewood offer" and friends). The other 22 were named
# from their description ("Intent: OFFER", "Looking for salmon"), carry a bare
# blake2b fuseki_uri with no prefix, and inherited source='personal-vault' from
# the column default. The tripwire fired, but reported 3 where the truth was 25 —
# it would have gone fully silent had the three happened to be named differently.
#
# api/routers/intent_router.py now stamps metadata->>'intent_key' on every intent
# entity it mints, which is the exact, zero-false-positive handle these rows
# always lacked. A fixture key is 'reg-test-intent-<suffix>-<hex>'.
_TEST_SIGNATURE_SQL = """
    SELECT count(*) FROM entity_registry
    WHERE created_at > $1::timestamp
      AND (fuseki_uri ILIKE '%test%'
           OR entity_text ILIKE '%test%'
           OR source IN ('pytest', 'test')
           OR metadata->>'intent_key' ILIKE '%test%')
"""

_LIVE_DSN = f"postgresql://darrenzal:@localhost:5432/{LIVE_DB_NAME}"

# --------------------------------------------------------------------------
# 1b. The live DSN, published for tests that write to the live graph ON PURPOSE.
# --------------------------------------------------------------------------
# The redirect above is the right default, but it silently broke the one thing
# that was cleaning up after the tests it cannot redirect.
#
# A test that drives the API over HTTP (tests/test_intent_registry.py ->
# POST http://localhost:8351/intents/ingest) writes through a SEPARATE uvicorn
# process holding its own pool against the LIVE database. No environment
# variable can redirect that. Such a test must therefore clean up in the live
# database — and tests/test_intent_registry.py's purge fixture did exactly that,
# reading POSTGRES_URL, until this file started rewriting POSTGRES_URL to the
# test DSN on 2026-08-21. From that moment its ingests went to personal_koi and
# its DELETE went to personal_koi_test, removing nothing.
#
# Result: the isolation fix converted a working teardown into a no-op, and 225
# orphaned Intent rows landed in the live graph over the following 30 hours.
# Publishing the live DSN under its own name is what lets those teardowns target
# the database their writes actually reached, without weakening the redirect.
os.environ["KOI_LIVE_POSTGRES_URL"] = _LIVE_DSN


def _live_marker():
    """Server-side timestamp marking the start of the run. None on failure."""
    import asyncio

    import asyncpg

    async def _run():
        conn = await asyncpg.connect(_LIVE_DSN)
        try:
            return await conn.fetchval(_MARKER_SQL)
        finally:
            await conn.close()

    return asyncio.run(_run())


def _live_test_signature_count(since):
    """Count test-signature rows in the LIVE database since `since`.

    Deliberately does NOT catch exceptions. A tripwire that reports "unknown"
    when it breaks is worse than no tripwire, because the run then looks clean.
    Callers decide what an exception means; this function only reports truth.
    """
    import asyncio

    import asyncpg

    async def _run():
        conn = await asyncpg.connect(_LIVE_DSN)
        try:
            return await conn.fetchval(_TEST_SIGNATURE_SQL, since)
        finally:
            await conn.close()

    return asyncio.run(_run())


@pytest.fixture(scope="session", autouse=True)
def live_graph_tripwire(request):
    """Fail the session if the run wrote test-signature rows to the live graph.

    Fails CLOSED. If the check itself cannot run, that is a failure, not a
    shrug — an unverifiable isolation guarantee is indistinguishable from a
    broken one, and reporting green in that state is how the original 646-row
    leak went unnoticed for five months.

    Set KOI_SKIP_LIVE_TRIPWIRE=1 to opt out deliberately (e.g. on a machine
    with no live database). Opting out is loud and explicit; degrading is not.
    """
    opted_out = os.environ.get("KOI_SKIP_LIVE_TRIPWIRE") == "1"

    marker = None
    before = None
    setup_error = None
    if not opted_out:
        try:
            marker = _live_marker()
            before = _live_test_signature_count(marker)
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            setup_error = exc

    yield

    if opted_out:
        print(
            "\n[live_graph_tripwire] SKIPPED by KOI_SKIP_LIVE_TRIPWIRE=1 — "
            "isolation was NOT verified this run."
        )
        return

    if setup_error is not None:
        pytest.fail(
            f"\n*** TRIPWIRE COULD NOT ARM ***\n"
            f"Could not establish a baseline against '{LIVE_DB_NAME}': "
            f"{type(setup_error).__name__}: {setup_error}\n"
            f"Refusing to report a clean run that was never checked. "
            f"Fix the check, or set KOI_SKIP_LIVE_TRIPWIRE=1 to opt out on purpose.",
            pytrace=False,
        )

    try:
        after = _live_test_signature_count(marker)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"\n*** TRIPWIRE COULD NOT READ BACK ***\n"
            f"Baseline armed but the post-run check failed: "
            f"{type(exc).__name__}: {exc}\n"
            f"Isolation is UNVERIFIED for this run.",
            pytrace=False,
        )

    leaked = after - before
    if leaked > 0:
        pytest.fail(
            f"\n*** TEST ISOLATION BREACH ***\n"
            f"{leaked} test-signature row(s) were written to the LIVE database "
            f"'{LIVE_DB_NAME}' during this run.\n"
            f"Expected 0 — tests must write only to {TEST_DSN}.\n"
            f"Find them with:\n"
            f"  psql -d {LIVE_DB_NAME} -c \"select fuseki_uri, entity_text, source, created_at \"\n"
            f"    \"from entity_registry where created_at > '{marker}' \"\n"
            f"    \"and (fuseki_uri ilike '%test%' or entity_text ilike '%test%' \"\n"
            f"    \"or metadata->>'intent_key' ilike '%test%');\"",
            pytrace=False,
        )

    print(
        f"\n[live_graph_tripwire] OK: 0 test-signature rows written to "
        f"{LIVE_DB_NAME} since {marker}."
    )


@pytest.fixture(scope="session")
def test_dsn():
    """The DSN every DB-touching test should be using."""
    return TEST_DSN
