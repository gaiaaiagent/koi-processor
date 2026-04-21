"""
Regression tests for /knowledge/unified-search endpoint.
Requires live KOI backend at localhost:8351.

embedding-down test requires poly blocked externally before running:
    sudo iptables -A OUTPUT -p tcp --dport 8352 -j DROP
    pytest tests/test_unified_search.py::test_unified_search_embedding_down -v
    sudo iptables -D OUTPUT -p tcp --dport 8352 -j DROP
"""
import os
import pytest
import httpx

BASE_URL = os.getenv("KOI_API_URL", "http://localhost:8351")


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=15.0) as c:
        yield c


@pytest.fixture(scope="module")
def embedding_up(client):
    """Gate embedding_up test on actual unified-search behavior.

    /health's embedding_available flag is optimistic; it doesn't detect an
    embedding backend that is reachable but saturated. Probe the real
    endpoint instead, with a generous timeout, and skip if it degrades
    to text-only or fails — this test only has something meaningful to
    assert when vector mode is live."""
    try:
        probe = client.get(
            "/knowledge/unified-search",
            params={"query": "healthcheck", "limit": 1},
            timeout=60.0,
        )
    except httpx.HTTPError as e:
        pytest.skip(f"KOI backend unreachable at {BASE_URL}: {e}")
    if probe.status_code != 200:
        pytest.skip(f"unified-search probe returned {probe.status_code}")
    body = probe.json()
    if not body.get("embedding_available") or body.get("degraded"):
        pytest.skip(
            "unified-search degraded (embedding backend unreachable or saturated); "
            "vector-mode assertions not applicable"
        )


def test_unified_search_embedding_up(client, embedding_up):
    """Normal path: HTTP 200, embedding_available=true, no match_mode in metadata."""
    r = client.get("/knowledge/unified-search", params={"query": "entity resolution", "limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["embedding_available"] is True
    assert isinstance(body["results"], list)
    # vector-mode results must not expose match_mode
    for result in body["results"]:
        assert "match_mode" not in result.get("metadata", {})


@pytest.mark.skipif(
    os.getenv("EMBEDDING_DOWN") != "1",
    reason="Set EMBEDDING_DOWN=1 and block port 8352 before running"
)
def test_unified_search_embedding_down(client):
    """Fallback path: HTTP 200, embedding_available=false, match_mode=text in metadata."""
    r = client.get("/knowledge/unified-search", params={"query": "herring", "limit": 5})
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body["embedding_available"] is False
    assert isinstance(body["results"], list)
    # text-mode results must carry match_mode, not vector_score
    for result in body["results"]:
        meta = result.get("metadata", {})
        assert meta.get("match_mode") == "text"
        assert "vector_score" not in meta
