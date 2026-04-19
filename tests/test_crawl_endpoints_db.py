"""
DB-backed tests for the /web/crawl-agentic and /web/crawl-jobs/{id} endpoints.

Uses httpx.AsyncClient with ASGITransport (keeps pool + app on the same
event loop). Requires ``POSTGRES_TEST_URL``.

Gating ACs covered:
  - AC6, AC15, AC16, AC21, AC30, AC39, AC50, AC52, AC54, AC64, AC72
  - Ownership check on GET /crawl-jobs/{id}
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

asyncpg = pytest.importorskip("asyncpg")
httpx = pytest.importorskip("httpx")

TEST_DSN = os.getenv("POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DSN,
    reason="POSTGRES_TEST_URL not set; skipping endpoint DB tests",
)


MIGRATION_087 = (ROOT / "migrations" / "087_web_crawl_jobs.sql").read_text()


class _FakeCaps:
    web_sensor = True
    assertion_history = False
    graph_queries = False


@pytest_asyncio.fixture
async def app_and_pool():
    from fastapi import FastAPI
    from api.routers.web_router import create_router

    pool0 = await asyncpg.create_pool(TEST_DSN, min_size=1, max_size=2)
    schema = f"crawl_ep_{uuid.uuid4().hex[:10]}"
    async with pool0.acquire() as conn:
        await conn.execute(f'CREATE SCHEMA "{schema}"')
        await conn.execute(f'SET search_path TO "{schema}", public')
        await conn.execute(MIGRATION_087)
    await pool0.close()

    pool = await asyncpg.create_pool(
        TEST_DSN,
        min_size=1,
        max_size=8,
        server_settings={"search_path": f'"{schema}", public'},
    )
    app = FastAPI()
    app.include_router(create_router(pool, _FakeCaps()))
    app.state.db_pool = pool

    os.environ["AGENTIC_CRAWL_ENABLED"] = "true"
    os.environ["CRAWL_TOKEN__ops__test"] = "ops-test-token"

    try:
        yield app, pool
    finally:
        try:
            async with pool.acquire() as conn:
                await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        except Exception:
            pass
        await pool.close()
        os.environ.pop("AGENTIC_CRAWL_ENABLED", None)
        os.environ.pop("CRAWL_TOKEN__ops__test", None)


def _aclient(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _post(client, body: dict[str, Any], token: str = "ops-test-token"):
    return await client.post(
        "/web/crawl-agentic",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )


async def _get(client, job_id: int, token: str = "ops-test-token"):
    return await client.get(
        f"/web/crawl-jobs/{job_id}",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
async def test_ac39_flag_off_returns_503(app_and_pool):
    app, _ = app_and_pool
    os.environ["AGENTIC_CRAWL_ENABLED"] = "false"
    try:
        async with _aclient(app) as c:
            r = await _post(c, {"url": "https://example.org/"})
        assert r.status_code == 503
    finally:
        os.environ["AGENTIC_CRAWL_ENABLED"] = "true"


@pytest.mark.asyncio
async def test_ac50_no_token_returns_401(app_and_pool):
    app, _ = app_and_pool
    async with _aclient(app) as c:
        r = await c.post("/web/crawl-agentic", json={"url": "https://example.org/"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_enqueue_returns_job_id(app_and_pool):
    app, pool = app_and_pool
    async with _aclient(app) as c:
        r = await _post(c, {"url": "https://example.org/"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "job_id" in body and isinstance(body["job_id"], int)
    assert body["deduped"] is False
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, submitted_by, start_url, budget_json FROM web_crawl_jobs WHERE id=$1",
            body["job_id"],
        )
    assert row["status"] == "queued"
    assert row["submitted_by"] == "ops:test"
    assert row["start_url"] == "https://example.org/"
    snap = json.loads(row["budget_json"]) if isinstance(row["budget_json"], str) else row["budget_json"]
    for k in ("max_pages", "max_vision_calls", "max_seconds", "max_usd"):
        assert k in snap


@pytest.mark.asyncio
async def test_ac16_same_url_dedup_returns_existing(app_and_pool):
    app, _ = app_and_pool
    async with _aclient(app) as c:
        r1 = await _post(c, {"url": "https://example.org/"})
        r2 = await _post(c, {"url": "https://example.org/"})
    assert r1.status_code == 200 and r2.status_code == 200
    body1 = r1.json()
    body2 = r2.json()
    assert body1["job_id"] == body2["job_id"]
    assert body2["deduped"] is True


@pytest.mark.asyncio
async def test_ac23_canonicalization_dedupes_variants(app_and_pool):
    app, _ = app_and_pool
    async with _aclient(app) as c:
        r1 = await _post(c, {"url": "https://Example.ORG:443/"})
        r2 = await _post(c, {"url": "https://example.org/"})
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["job_id"] == r2.json()["job_id"]
    assert r2.json()["deduped"] is True


@pytest.mark.asyncio
async def test_ac23_www_not_stripped(app_and_pool):
    app, _ = app_and_pool
    async with _aclient(app) as c:
        r1 = await _post(c, {"url": "https://www.example.org/"})
        r2 = await _post(c, {"url": "https://example.org/"})
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["job_id"] != r2.json()["job_id"]


@pytest.mark.asyncio
async def test_ac15_per_user_concurrent_cap(app_and_pool):
    app, _ = app_and_pool
    async with _aclient(app) as c:
        r1 = await _post(c, {"url": "https://example.org/a"})
        r2 = await _post(c, {"url": "https://example.org/b"})
        r3 = await _post(c, {"url": "https://example.org/c"})
    assert r1.status_code == 200 and r2.status_code == 200
    assert r3.status_code == 429
    assert "concurrent crawl limit" in r3.json()["detail"]["error"]


@pytest.mark.asyncio
async def test_ac21_per_day_cap(app_and_pool):
    app, pool = app_and_pool
    async with pool.acquire() as conn:
        for i in range(10):
            await conn.execute(
                """
                INSERT INTO web_crawl_jobs (start_url, submitted_by, status, budget_json, created_at)
                VALUES ($1, 'ops:test', 'done', '{}'::jsonb, now() - interval '1 hour')
                """,
                f"https://example.org/seed-{i}",
            )
    async with _aclient(app) as c:
        r = await _post(c, {"url": "https://example.org/new"})
    assert r.status_code == 429, r.text
    assert "daily crawl limit" in r.json()["detail"]["error"]


@pytest.mark.asyncio
async def test_ac22_budget_system_ceiling_rejected(app_and_pool):
    app, _ = app_and_pool
    async with _aclient(app) as c:
        r = await _post(c, {"url": "https://example.org/", "budget": {"max_pages": 100}})
    assert r.status_code == 400
    assert "system ceiling" in r.json()["detail"]["error"]


@pytest.mark.asyncio
async def test_ac70_ssrf_blocks_loopback(app_and_pool):
    app, _ = app_and_pool
    async with _aclient(app) as c:
        r = await _post(c, {"url": "http://127.0.0.1/"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_get_crawl_job_returns_status_and_ownership_enforced(app_and_pool):
    app, _ = app_and_pool
    os.environ["CRAWL_TOKEN__ops__other"] = "ops-other-token"
    try:
        async with _aclient(app) as c:
            r1 = await _post(c, {"url": "https://example.org/"})
            job_id = r1.json()["job_id"]
            own = await _get(c, job_id)
            assert own.status_code == 200
            assert own.json()["status"] == "queued"
            foreign = await _get(c, job_id, token="ops-other-token")
            assert foreign.status_code == 403
    finally:
        os.environ.pop("CRAWL_TOKEN__ops__other", None)


@pytest.mark.asyncio
async def test_ac54_flag_toggle_preserves_queued_jobs(app_and_pool):
    app, pool = app_and_pool
    async with _aclient(app) as c:
        r = await _post(c, {"url": "https://example.org/"})
    job_id = r.json()["job_id"]
    os.environ["AGENTIC_CRAWL_ENABLED"] = "false"
    try:
        async with pool.acquire() as conn:
            status = await conn.fetchval(
                "SELECT status FROM web_crawl_jobs WHERE id=$1", job_id
            )
        assert status == "queued"
    finally:
        os.environ["AGENTIC_CRAWL_ENABLED"] = "true"


@pytest.mark.asyncio
async def test_ac72_atomic_enqueue_under_concurrency(app_and_pool):
    """N simultaneous enqueue tasks for same (user, url) → exactly 1 DB row."""
    app, pool = app_and_pool

    async def fire_one():
        async with _aclient(app) as c:
            r = await _post(c, {"url": "https://example.org/racey"})
        return r.status_code, r.json()

    results = await asyncio.gather(*(fire_one() for _ in range(8)))
    codes = [c for c, _ in results]
    assert all(c == 200 for c in codes), codes
    job_ids = {body["job_id"] for _, body in results}
    assert len(job_ids) == 1
    deduped = [body["deduped"] for _, body in results]
    assert any(deduped)
    async with pool.acquire() as conn:
        n = await conn.fetchval(
            "SELECT COUNT(*) FROM web_crawl_jobs WHERE start_url='https://example.org/racey'"
        )
    assert n == 1
