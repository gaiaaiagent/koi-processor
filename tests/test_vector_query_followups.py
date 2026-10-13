"""Vector-query follow-ups R2b and R2c (2026-10-12).

R2b: the /search include_chunks fill ordered by document_rid before its LIMIT, so it
returned the alphabetically first documents (overlap 0.0 with the exact best-chunk
ranking on personal_koi). It now takes HNSW candidates with the already-returned
documents excluded inside the scan, keeps the best chunk per document and sorts by
similarity (_SEARCH_CHUNK_FILL_SQL, run through _fetch_hnsw).

R2c: the /recall-walk seed query was an exact GROUP BY over every embedded live fact,
133-300 s under load, past the pool's 60 s command_timeout, and only DataError was
caught, so the endpoint returned 500. It now groups the nearest facts from the HNSW
index, retries with more candidates or runs the exact form when those may be
incomplete, and returns partial seeds, flagged, when a query times out
(_recall_walk_seeds).

Every test runs against personal_koi_test inside a transaction that is rolled back.
"""

from __future__ import annotations

import asyncio
import time
import json
import os
import uuid

import asyncpg
import httpx
import numpy as np
import pytest
from fastapi import FastAPI

pytestmark = pytest.mark.anyio

DSN = os.environ.get("POSTGRES_URL")
DIM = 3072
CHUNK_INDEX = "idx_koi_memory_chunks_embedding_3072_hnsw"
FACT_INDEX = "idx_knowledge_facts_embedding_3072_hnsw"

# The pre-2026-10-12 fill query, verbatim apart from the no-op source_filter replace.
OLD_FILL_SQL = """
    SELECT DISTINCT ON (c.document_rid)
        c.document_rid as rid,
        m.content->>'title' as title,
        LEFT(c.content->>'text', 500) as content_preview,
        1 - (c.embedding_3072::halfvec(3072) <=> $1::halfvec(3072)) as similarity,
        m.source_sensor,
        m.metadata
    FROM koi_memory_chunks c
    JOIN koi_memories m ON m.rid = c.document_rid
    WHERE c.embedding_3072 IS NOT NULL
      AND c.document_rid NOT IN (SELECT rid FROM unnest($3::text[]) as rid)
    ORDER BY c.document_rid, c.embedding_3072::halfvec(3072) <=> $1::halfvec(3072)
    LIMIT $2
"""


@pytest.fixture
def anyio_backend():
    return "asyncio"


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


def _tag() -> str:
    return uuid.uuid4().hex[:10]


def _rng():
    """A seed drawn per run, printed so a failure can be replayed.

    Rolled-back rows stay in the HNSW indexes until VACUUM, and a concurrent run's
    uncommitted rows sit there too. With a fixed seed every run inserted exact
    duplicates of those entries, which degraded the graph around the fixture, and the
    index-path assertions failed at random. Every fixture vector is placed relative to
    the query, so a fresh query direction keeps the similarity structure and leaves
    the leftovers near cosine 0, outside the fixture's neighbourhood.
    """
    seed = uuid.uuid4().int & 0xFFFFFFFF
    print(f"rng seed {seed}")
    return np.random.default_rng(seed)


async def _exact_recall(conn):
    """hnsw.ef_search at its maximum, so an index scan returns the nearest rows of
    these small fixtures: the tests that use it check the seed logic, not HNSW recall.
    _recall_walk_seeds sets only iterative_scan and plan_cache_mode, so this reaches
    its savepoints."""
    await conn.execute("SET LOCAL hnsw.ef_search = 1000")


def _unit(rng, dim=DIM):
    v = rng.standard_normal(dim)
    return v / np.linalg.norm(v)


def _at_similarity(q, sim, rng):
    o = rng.standard_normal(len(q))
    o -= o.dot(q) * q
    o /= np.linalg.norm(o)
    return sim * q + np.sqrt(1.0 - sim * sim) * o


def _vec(v) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


async def _force_index(conn):
    """Make the planner prefer the HNSW index wherever it can use it: the test
    tables are too small for it otherwise."""
    await conn.execute("SET LOCAL enable_seqscan = off")
    await conn.execute("SET LOCAL enable_bitmapscan = off")
    await conn.execute("SET LOCAL enable_sort = off")


async def _plan(conn, sql, *args) -> str:
    rows = await conn.fetch("EXPLAIN " + sql, *args)
    return "\n".join(r[0] for r in rows)


async def _setting(conn, name="hnsw.iterative_scan") -> str | None:
    return await conn.fetchval("SELECT current_setting($1, true)", name)


class _SingleConnPool:
    def __init__(self, conn):
        self._conn = conn

    class _CM:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, *args):
            return None

    def acquire(self):
        return self._CM(self._conn)


# ---------------------------------------------------------------------------
# R2b fixtures
# ---------------------------------------------------------------------------

async def _insert_docs(conn, docs):
    """docs: {rid: [chunk vector, ...]}. One koi_memories row per rid."""
    await conn.executemany(
        "INSERT INTO koi_memories (rid, event_type, source_sensor, content, metadata) "
        "VALUES ($1, 'NEW', 'obsidian-sensor', $2::jsonb, '{}'::jsonb)",
        [(rid, json.dumps({"title": f"Title {rid}", "text": f"Text {rid}"})) for rid in docs])
    await conn.executemany(
        "INSERT INTO koi_memory_chunks (chunk_rid, document_rid, chunk_index, total_chunks, "
        "content, embedding_3072) VALUES ($1, $2, $3, $4, $5::jsonb, $6::vector)",
        [(f"{rid}#c{i}", rid, i, len(vs), json.dumps({"text": f"chunk {i} of {rid}"}), _vec(v))
         for rid, vs in docs.items() for i, v in enumerate(vs)])


def _ranked_docs(rng, q, t, n=6):
    """n documents whose rid order is the reverse of their similarity order.

    Document k has rid ...:d<k> and a best chunk at similarity 0.30 + 0.1 k, plus a
    worse chunk, so d0 is first alphabetically and least similar. Returns
    ({rid: [vectors]}, {rid: best similarity}).
    """
    docs, best = {}, {}
    for k in range(n):
        rid = f"orn:test.vqf:{t}:d{k}"
        s = 0.30 + 0.1 * k
        docs[rid] = [_at_similarity(q, s - 0.15, rng), _at_similarity(q, s, rng)]
        best[rid] = s
    return docs, best


# ---------------------------------------------------------------------------
# R2b: the fill query
# ---------------------------------------------------------------------------

async def test_fill_returns_the_most_similar_documents_not_the_first_rids(conn):
    from api import personal_ingest_api as pia

    rng = _rng()
    t = _tag()
    q = _unit(rng)
    docs, best = _ranked_docs(rng, q, t)
    await _insert_docs(conn, docs)
    rids = sorted(docs)                     # d0 .. d5, least to most similar
    excluded = [rids[5]]                    # what the main query already returned

    sql = pia._SEARCH_CHUNK_FILL_SQL.format(source_filter="")
    got = await pia._fetch_hnsw(conn, sql, _vec(q), 3, excluded)
    assert [r["rid"] for r in got] == [rids[4], rids[3], rids[2]]
    # The similarity is the document's best chunk, not the other one.
    for r in got:
        assert r["similarity"] == pytest.approx(best[r["rid"]], abs=2e-3)

    # Negative twin: the old form returns the alphabetically first documents, so the
    # fixture tells the two apart.
    old = await conn.fetch(OLD_FILL_SQL, _vec(q), 3, excluded)
    assert [r["rid"] for r in old] == [rids[0], rids[1], rids[2]]


async def test_fill_excludes_every_already_returned_document(conn):
    from api import personal_ingest_api as pia

    rng = _rng()
    t = _tag()
    q = _unit(rng)
    docs, _ = _ranked_docs(rng, q, t)
    await _insert_docs(conn, docs)
    rids = sorted(docs)
    sql = pia._SEARCH_CHUNK_FILL_SQL.format(source_filter="")

    got = await pia._fetch_hnsw(conn, sql, _vec(q), 10, [rids[5], rids[3], rids[1]])
    assert [r["rid"] for r in got] == [rids[4], rids[2], rids[0]]
    # An empty exclusion list excludes nothing.
    got = await pia._fetch_hnsw(conn, sql, _vec(q), 10, [])
    assert [r["rid"] for r in got] == rids[::-1]


async def test_fill_reaches_past_the_excluded_documents_with_iterative_scan(conn):
    """The exclusion sits inside the HNSW scan, so the scan must not stop at ef_search.

    The excluded document owns the 30 chunks nearest the query. With the index
    forced, ef_search = 10 and no iterative scan, every candidate belongs to it and
    the query returns nothing (negative twin). Through _fetch_hnsw it returns the
    other documents, best first, and the caller's setting survives.
    """
    from api import personal_ingest_api as pia

    rng = _rng()
    t = _tag()
    q = _unit(rng)
    docs, _ = _ranked_docs(rng, q, t)
    hub = f"orn:test.vqf:{t}:hub"
    docs[hub] = [_at_similarity(q, float(s), rng) for s in rng.uniform(0.95, 0.99, 30)]
    await _insert_docs(conn, docs)
    rids = sorted(r for r in docs if r != hub)

    await _force_index(conn)
    await conn.execute("SET LOCAL hnsw.ef_search = 10")
    await conn.execute("SET LOCAL hnsw.iterative_scan = off")
    sql = pia._SEARCH_CHUNK_FILL_SQL.format(source_filter="")
    assert CHUNK_INDEX in await _plan(conn, sql, _vec(q), 3, [hub])

    starved = await conn.fetch(sql, _vec(q), 3, [hub])
    assert starved == [], "fixture does not starve the plain scan; test proves nothing"

    got = await pia._fetch_hnsw(conn, sql, _vec(q), 3, [hub])
    assert [r["rid"] for r in got] == [rids[5], rids[4], rids[3]]
    assert await _setting(conn) == "off"
    assert await _setting(conn, "plan_cache_mode") == "auto"


async def test_fill_applies_the_source_filter(conn):
    from api import personal_ingest_api as pia

    rng = _rng()
    t = _tag()
    q = _unit(rng)
    docs, _ = _ranked_docs(rng, q, t)
    await _insert_docs(conn, docs)
    rids = sorted(docs)
    await conn.execute(
        "UPDATE koi_memories SET source_sensor = 'email-sensor' WHERE rid = ANY($1::text[])",
        [rids[4], rids[1]])
    sql = pia._SEARCH_CHUNK_FILL_SQL.format(source_filter=pia.SOURCE_TO_FILTER["email"])
    got = await pia._fetch_hnsw(conn, sql, _vec(q), 10, [rids[5]])
    assert [r["rid"] for r in got] == [rids[4], rids[1]]


async def test_search_endpoint_fills_with_the_nearest_other_documents(conn, monkeypatch):
    """End to end through search_knowledge_base with include_chunks.

    A hub document owns the 200 chunks nearest the query, so the main query's 200
    candidates hold one document and the fill runs for the rest of the page.

    Index scans are off, so both the main query and the fill read exactly and the
    test checks the wiring: exclusion, best chunk per document, similarity order and
    matched_via. The fill's HNSW behaviour (scanning past the excluded documents) is
    test_fill_reaches_past_the_excluded_documents_with_iterative_scan; with the index
    forced here, the main query's own approximate walk sometimes returned d3-d5
    itself, so the fill never ran. Recall on real data is the live gate's job
    (REMEASURE.md).
    """
    from api import personal_ingest_api as pia

    rng = _rng()
    t = _tag()
    q = _unit(rng)
    docs, best = _ranked_docs(rng, q, t)
    hub = f"orn:test.vqf:{t}:hub"
    docs[hub] = [_at_similarity(q, float(s), rng) for s in rng.uniform(0.90, 0.99, 200)]
    await _insert_docs(conn, docs)
    rids = sorted(r for r in docs if r != hub)

    async def fake_embedding(_text, *_a, **_kw):
        return [float(x) for x in q]

    monkeypatch.setattr(pia, "db_pool", _SingleConnPool(conn))
    monkeypatch.setattr(pia, "generate_query_embedding", fake_embedding)
    await conn.execute("SET LOCAL enable_indexscan = off")
    resp = await pia.search_knowledge_base(
        pia.SearchRequest(query="anything", limit=4, include_chunks=True))

    got = resp["results"]
    assert [r["rid"] for r in got] == [hub, rids[5], rids[4], rids[3]]
    assert "matched_via" not in got[0]
    assert all(r["matched_via"] == "chunk" for r in got[1:])
    for r in got[1:]:
        assert r["similarity"] == pytest.approx(best[r["rid"]], abs=2e-3)


# ---------------------------------------------------------------------------
# R2c fixtures
# ---------------------------------------------------------------------------

async def _insert_episodes(conn, t, episodes, group="personal"):
    """episodes: list of lists of fact similarities, one list per episode.

    Returns the episode ids in the given order. Every fact is live and embedded.
    """
    ids = [await conn.fetchval(
        "INSERT INTO knowledge_episodes (name, group_id) VALUES ($1, $2) RETURNING id",
        f"vqf {t} ep{i}", group) for i in range(len(episodes))]
    return ids


async def _insert_facts(conn, t, q, rng, ep_ids, episodes, group="personal"):
    rows = []
    for ep, sims in zip(ep_ids, episodes):
        for j, s in enumerate(sims):
            rows.append((ep, f"orn:test.vqf:{t}:s", "RELATES_TO",
                         f"vqf {t} fact {ep} {j}", _vec(_at_similarity(q, float(s), rng)),
                         group))
    await conn.executemany(
        "INSERT INTO knowledge_facts (episode_id, subject_uri, predicate, fact_text, "
        "fact_embedding_3072, group_id) VALUES ($1, $2, $3, $4, $5::vector, $6)", rows)


async def _episodes(conn, t, q, rng, episodes, group="personal"):
    ids = await _insert_episodes(conn, t, episodes, group)
    await _insert_facts(conn, t, q, rng, ids, episodes, group)
    return ids


def _ids(rows):
    return [str(r["episode_id"]) for r in rows]


def _spread(rng, n, lo, hi):
    return [float(x) for x in rng.uniform(lo, hi, n)]


def _others(rng, n_eps, n_rest):
    """n_eps episodes, each with one best fact at a distinct similarity (0.88 down by
    0.02, so episode k ranks k) and n_rest facts far below every best fact."""
    return [[0.88 - 0.02 * k] + _spread(rng, n_rest, 0.05, 0.25) for k in range(n_eps)]


@pytest.fixture
def kr(monkeypatch):
    """The router module with the ANN limits scaled down from (200, 1000) to (15, 60),
    so the fixtures stay small: each inserted fact is an HNSW insert of a 3072-dim
    vector, seconds apiece on a loaded machine. The logic does not depend on the
    numbers, and test_default_limits pins the shipped values."""
    from api.routers import knowledge_router

    monkeypatch.setattr(knowledge_router, "RECALL_SEED_ANN_LIMITS", (15, 60))
    return knowledge_router


def _hub(rng, n):
    """One episode owning the n facts nearest the query, above every other fact."""
    return [_spread(rng, n, 0.93, 0.99)]


# ---------------------------------------------------------------------------
# R2c: the seed query
# ---------------------------------------------------------------------------

def test_default_limits():
    from api.routers import knowledge_router

    assert knowledge_router.RECALL_SEED_K == 12
    assert knowledge_router.RECALL_SEED_ANN_LIMITS == (200, 1000)
    sql = knowledge_router._recall_seed_ann_sql(200, "")
    assert "LIMIT 200\n" in sql and "LIMIT 12\n" in sql


async def test_seed_ann_plans_the_fact_index(conn, kr):
    await _force_index(conn)
    await conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
    q = _vec(_unit(np.random.default_rng(1)))
    assert FACT_INDEX in await _plan(conn, kr._recall_seed_ann_sql(200, ""), q)
    assert FACT_INDEX in await _plan(
        conn, kr._recall_seed_ann_sql(200, "AND f.group_id = $2"), q, "personal")


async def test_seeds_come_from_the_first_ann_query(conn, kr):
    """15 episodes, 30 live facts: the 15 nearest are each episode's best fact, so the
    first ANN query answers with the exact top 12 (no ties in this fixture)."""
    rng = _rng()
    t = _tag()
    q = _unit(rng)
    ids = await _episodes(conn, t, q, rng, _others(rng, 15, 1))
    # Expired and episode-less facts never seed, however close.
    await conn.execute(
        "INSERT INTO knowledge_facts (episode_id, subject_uri, predicate, fact_text, "
        "fact_embedding_3072, valid_to) VALUES ($1, 'orn:x', 'RELATES_TO', 'expired', "
        "$2::vector, now())", ids[14], _vec(_at_similarity(q, 0.99, rng)))
    await conn.execute(
        "INSERT INTO knowledge_facts (subject_uri, predicate, fact_text, fact_embedding_3072) "
        "VALUES ('orn:x', 'RELATES_TO', 'no episode', $1::vector)",
        _vec(_at_similarity(q, 0.99, rng)))

    await _exact_recall(conn)
    rows, method, partial = await kr._recall_walk_seeds(conn, _vec(q))
    assert (method, partial) == ("ann15", False)
    assert _ids(rows) == [str(i) for i in ids[:12]]
    exact = await conn.fetch(kr._recall_seed_exact_sql(""), _vec(q))
    assert _ids(rows) == _ids(exact)


async def test_crowded_candidates_retry_with_the_larger_limit(conn, kr, monkeypatch):
    """A hub episode owns the 18 nearest facts: the first 15 candidates cover one
    episode and all 15 came back, so the seed query retries at 60 and answers there."""
    rng = _rng()
    t = _tag()
    q = _unit(rng)
    ids = await _episodes(conn, t, q, rng, _hub(rng, 18) + _others(rng, 14, 3))  # 74 facts

    calls = []
    real = kr._recall_seed_ann_sql
    monkeypatch.setattr(kr, "_recall_seed_ann_sql",
                        lambda n, gf: calls.append(n) or real(n, gf))
    await _exact_recall(conn)
    rows, method, partial = await kr._recall_walk_seeds(conn, _vec(q))
    assert calls == [15, 60]
    assert (method, partial) == ("ann60", False)
    assert _ids(rows) == [str(i) for i in ids[:12]]
    assert _ids(rows) == _ids(await conn.fetch(kr._recall_seed_exact_sql(""), _vec(q)))


async def test_still_short_at_the_largest_limit_runs_the_exact_form(conn, kr):
    """The hub owns the 62 nearest facts: 60 candidates still cover one episode."""
    rng = _rng()
    t = _tag()
    q = _unit(rng)
    ids = await _episodes(conn, t, q, rng, _hub(rng, 62) + _others(rng, 14, 0))  # 76 facts

    await _exact_recall(conn)
    rows, method, partial = await kr._recall_walk_seeds(conn, _vec(q))
    assert (method, partial) == ("exact", False)
    assert _ids(rows) == [str(i) for i in ids[:12]]


def _exhausted(rng):
    """46 facts: the first 15 candidates are all the hub's, and the retry at 60 gets
    fewer than 60 rows back."""
    return _hub(rng, 18) + _others(rng, 14, 1)


async def test_exhausted_candidates_run_the_exact_form(conn, kr):
    """Fewer than N rows back cannot be told apart from a walk stopped by
    hnsw.max_scan_tuples, so the exact form decides."""
    rng = _rng()
    t = _tag()
    q = _unit(rng)
    ids = await _episodes(conn, t, q, rng, _exhausted(rng))

    await _exact_recall(conn)
    rows, method, partial = await kr._recall_walk_seeds(conn, _vec(q))
    assert (method, partial) == ("exact", False)
    assert _ids(rows) == [str(i) for i in ids[:12]]


async def test_group_filter_reaches_past_closer_facts_of_other_groups(conn, kr):
    """With the index forced, ef_search = 10 and iterative scan off in the caller,
    the 20 nearest facts belong to another group, so a plain scan returns nothing.
    The seed query's own SET LOCAL lets the scan reach the group's facts, and the
    caller's settings survive.

    How many of the 15 candidates the relaxed scan returns depends on the graph: all
    15 answer at ann15, fewer send the query to the exact form, and the seeds are the
    same either way. So the test checks that the first ANN query reached the group,
    not which method answered."""
    rng = _rng()
    t = _tag()
    q = _unit(rng)
    group = f"vqf-{t}"
    await _episodes(conn, t, q, rng, _hub(rng, 20), group=f"other-{t}")
    ids = await _episodes(conn, t, q, rng, _others(rng, 14, 1), group=group)  # 28 facts

    await _force_index(conn)
    await conn.execute("SET LOCAL hnsw.ef_search = 10")
    await conn.execute("SET LOCAL hnsw.iterative_scan = off")
    starved = await conn.fetch(kr._recall_seed_ann_sql(15, "AND f.group_id = $2"), _vec(q), group)
    assert starved == [], "fixture does not starve the plain scan; test proves nothing"

    proxy = _Recording(conn)
    rows, method, partial = await kr._recall_walk_seeds(proxy, _vec(q), group)
    first_sql, first = proxy.fetched[0]
    assert "LIMIT 15\n" in first_sql
    assert first, "the seed query's ANN scan did not reach past the other group"
    assert set(_ids(first)) <= {str(i) for i in ids}
    assert method in ("ann15", "exact") and partial is False
    assert _ids(rows) == [str(i) for i in ids[:12]]
    assert await _setting(conn) == "off"
    assert await _setting(conn, "plan_cache_mode") == "auto"


class _Recording:
    """Delegate to a real connection; keep (sql, rows) for each fetch."""

    def __init__(self, conn):
        self._conn = conn
        self.fetched = []

    def transaction(self):
        return self._conn.transaction()

    async def execute(self, *a, **kw):
        return await self._conn.execute(*a, **kw)

    async def fetch(self, sql, *args, **kw):
        rows = await self._conn.fetch(sql, *args, **kw)
        self.fetched.append((sql, rows))
        return rows


class _RaiseOn(_Recording):
    """_Recording that raises exc from fetch when match(sql)."""

    def __init__(self, conn, match, exc):
        super().__init__(conn)
        self._match, self._exc = match, exc

    async def fetch(self, sql, *args, **kw):
        if self._match(sql):
            raise self._exc
        return await super().fetch(sql, *args, **kw)


@pytest.mark.parametrize("exc", [
    asyncio.TimeoutError(),
    asyncpg.exceptions.QueryCanceledError("canceling statement due to statement timeout"),
])
async def test_exact_fallback_timeout_returns_partial_ann_seeds(conn, kr, exc):
    rng = _rng()
    t = _tag()
    q = _unit(rng)
    ids = await _episodes(conn, t, q, rng, _exhausted(rng))

    proxy = _RaiseOn(conn, lambda sql: "GROUP BY f.episode_id" in sql, exc)
    await _exact_recall(conn)
    rows, method, partial = await kr._recall_walk_seeds(proxy, _vec(q))
    assert (method, partial) == ("ann60", True)
    assert _ids(rows) == [str(i) for i in ids[:12]]
    assert await conn.fetchval("SELECT 1") == 1


async def test_larger_ann_timeout_returns_the_first_ann_seeds(conn, kr):
    """The timeout path returns what the first ANN query returned. Which episodes
    those are depends on HNSW recall, so the test compares against the recorded rows
    rather than the fixture's exact top 15."""
    rng = _rng()
    t = _tag()
    q = _unit(rng)
    ids = await _episodes(conn, t, q, rng, _exhausted(rng))

    proxy = _RaiseOn(conn, lambda sql: "LIMIT 60\n" in sql, asyncio.TimeoutError())
    await _exact_recall(conn)
    rows, method, partial = await kr._recall_walk_seeds(proxy, _vec(q))
    assert (method, partial) == ("ann15", True)
    assert len(proxy.fetched) == 1 and "LIMIT 15\n" in proxy.fetched[0][0]
    assert rows == proxy.fetched[0][1]
    assert _ids(rows)[0] == str(ids[0])
    assert len(rows) < kr.RECALL_SEED_K


async def test_real_statement_timeout_in_the_exact_fallback(conn, kr, monkeypatch):
    """A server-side cancel inside the fallback's savepoint: the partial seeds come
    back and the caller's transaction is still usable. The timeout is generous so
    the two ANN queries finish on a loaded machine."""
    monkeypatch.setattr(
        kr, "_recall_seed_exact_sql",
        lambda gf: "SELECT NULL::uuid AS episode_id, 0.0 AS dist "
                   "FROM pg_sleep(120) WHERE $1::text IS NOT NULL")
    rng = _rng()
    t = _tag()
    q = _unit(rng)
    ids = await _episodes(conn, t, q, rng, _exhausted(rng))

    await conn.execute("SET LOCAL statement_timeout = '20s'")
    await _exact_recall(conn)
    rows, method, partial = await kr._recall_walk_seeds(conn, _vec(q))
    assert (method, partial) == ("ann60", True)
    assert _ids(rows) == [str(i) for i in ids[:12]]
    assert await conn.fetchval("SELECT 1") == 1
    assert await _setting(conn, "statement_timeout") == "20s"


async def test_one_deadline_bounds_all_seed_stages(conn, kr, monkeypatch):
    """The stages share RECALL_SEED_BUDGET_S: an exact fallback that would run for
    a minute is cut at the deadline client-side, the ANN seeds come back partial,
    and the connection is still usable."""
    monkeypatch.setattr(kr, "RECALL_SEED_BUDGET_S", 3.0)
    monkeypatch.setattr(
        kr, "_recall_seed_exact_sql",
        lambda gf: "SELECT NULL::uuid AS episode_id, 0.0 AS dist "
                   "FROM pg_sleep(60) WHERE $1::text IS NOT NULL")
    rng = _rng()
    t = _tag()
    q = _unit(rng)
    ids = await _episodes(conn, t, q, rng, _exhausted(rng))

    await _exact_recall(conn)
    t0 = time.monotonic()
    rows, method, partial = await kr._recall_walk_seeds(conn, _vec(q))
    assert time.monotonic() - t0 < 15
    assert (method, partial) == ("ann60", True)
    assert _ids(rows) == [str(i) for i in ids[:12]]
    assert await conn.fetchval("SELECT 1") == 1


async def test_spent_deadline_skips_the_query(conn, kr, monkeypatch):
    """No budget left: no query runs, nothing is returned, the result is partial."""
    monkeypatch.setattr(kr, "RECALL_SEED_BUDGET_S", 0.0)
    proxy = _Recording(conn)
    rows, method, partial = await kr._recall_walk_seeds(proxy, _vec(_unit(_rng())))
    assert (rows, method, partial) == ([], "none", True)
    assert proxy.fetched == []


# ---------------------------------------------------------------------------
# R2c: the endpoint
# ---------------------------------------------------------------------------

async def _recall_walk(conn, kr, q, body=None):
    async def fake_embedding(_text, *_a, **_kw):
        return [float(x) for x in q]

    app = FastAPI()
    app.include_router(
        kr.create_router(_SingleConnPool(conn), generate_query_embedding=fake_embedding),
        prefix="/knowledge")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/knowledge/recall-walk", json=body or {"query": "anything"})


async def test_endpoint_marks_partial_seeds_in_walk_path(conn, kr, monkeypatch):
    rng = _rng()
    t = _tag()
    q = _unit(rng)
    ids = await _episodes(conn, t, q, rng, _others(rng, 2, 1))

    async def partial_seeds(_conn, _emb, _group=None):
        return [{"episode_id": ids[0], "dist": 0.1}], "ann200", True

    monkeypatch.setattr(kr, "_recall_walk_seeds", partial_seeds)
    resp = await _recall_walk(conn, kr, q)
    assert resp.status_code == 200
    walk_path = resp.json()["walk_path"]
    assert walk_path[0] == {"phase": "seed", "n_episodes": 1, "method": "ann200",
                            "seed_partial": True}
    assert walk_path[1]["phase"] == "walk"


async def test_endpoint_runs_the_real_seed_query(conn, kr):
    rng = _rng()
    t = _tag()
    q = _unit(rng)
    await _episodes(conn, t, q, rng, _others(rng, 3, 1))
    resp = await _recall_walk(conn, kr, q)
    assert resp.status_code == 200
    seed = resp.json()["walk_path"][0]
    # 6 facts: fewer than 15 candidates, so the exact form decides.
    assert seed == {"phase": "seed", "n_episodes": 3, "method": "exact"}


async def test_endpoint_still_skips_a_wrong_dimension_embedding(conn, kr):
    resp = await _recall_walk(conn, kr, np.ones(10) / np.sqrt(10))
    assert resp.status_code == 200
    assert resp.json()["walk_path"] == [{"phase": "seed", "n_episodes": 0, "method": "none"}]
    assert await conn.fetchval("SELECT 1") == 1
