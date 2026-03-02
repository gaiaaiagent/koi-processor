"""
Live-DB smoke tests for graph traversal endpoints.

Runs against the actual personal_koi database. Manual execution only.
Requires API to be running on localhost:8351.

Usage:
    ( set -a; source config/personal.env; set +a; \
      venv/bin/pytest tests/test_graph_traversal_smoke.py -v )
"""

import os
import pytest
import httpx

BASE_URL = os.getenv("KOI_API_URL", "http://localhost:8351")

# Known entity URIs in personal_koi (adjust if needed)
DARREN_URI = "orn:personal-koi.entity:person-darren-zal-42986b9bf8c0"


@pytest.fixture
def client():
    return httpx.Client(base_url=BASE_URL, timeout=10.0)


# =============================================================================
# Existing endpoint with direction param
# =============================================================================


def test_relationships_default_direction(client):
    """Existing endpoint still works with default direction=both."""
    r = client.get(f"/relationships/{DARREN_URI}")
    assert r.status_code == 200
    data = r.json()
    assert "relationships" in data
    assert data["count"] > 0


def test_relationships_outgoing(client):
    """Outgoing relationships for Darren."""
    r = client.get(f"/relationships/{DARREN_URI}", params={"direction": "outgoing"})
    assert r.status_code == 200
    data = r.json()
    # All returned relationships should have Darren as subject
    for rel in data["relationships"]:
        assert rel["subject_uri"] == DARREN_URI


def test_relationships_incoming(client):
    """Incoming relationships for Darren."""
    r = client.get(f"/relationships/{DARREN_URI}", params={"direction": "incoming"})
    assert r.status_code == 200
    data = r.json()
    for rel in data["relationships"]:
        assert rel["object_uri"] == DARREN_URI


def test_relationships_invalid_direction(client):
    """Invalid direction returns 422."""
    r = client.get(f"/relationships/{DARREN_URI}", params={"direction": "sideways"})
    assert r.status_code == 422


# =============================================================================
# Neighborhood
# =============================================================================


def test_neighborhood_darren(client):
    """Darren's 2-hop neighborhood should have multiple nodes."""
    r = client.get(f"/graph/neighborhood/{DARREN_URI}", params={"max_depth": 2})
    assert r.status_code == 200
    data = r.json()
    assert data["root"] == DARREN_URI
    assert data["node_count"] > 1
    assert data["edge_count"] > 0
    # Root should be depth 0
    root_nodes = [n for n in data["nodes"] if n["uri"] == DARREN_URI]
    assert len(root_nodes) == 1
    assert root_nodes[0]["depth"] == 0


def test_neighborhood_depth_1(client):
    """Depth 1 should return fewer nodes than depth 2."""
    r1 = client.get(f"/graph/neighborhood/{DARREN_URI}", params={"max_depth": 1})
    r2 = client.get(f"/graph/neighborhood/{DARREN_URI}", params={"max_depth": 2})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["node_count"] <= r2.json()["node_count"]


def test_neighborhood_nonexistent_entity(client):
    """Nonexistent entity returns 404."""
    r = client.get("/graph/neighborhood/orn:does-not-exist-at-all")
    assert r.status_code == 404


def test_neighborhood_outgoing_only(client):
    """Outgoing-only neighborhood."""
    r = client.get(
        f"/graph/neighborhood/{DARREN_URI}",
        params={"max_depth": 1, "direction": "outgoing"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["direction"] == "outgoing"
    assert data["node_count"] >= 1


# =============================================================================
# Shortest path
# =============================================================================


def test_shortest_path_same_entity(client):
    """Same entity -> path_length=0."""
    r = client.get(
        "/graph/shortest-path",
        params={"source": DARREN_URI, "target": DARREN_URI},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["found"] is True
    assert data["path_length"] == 0
    assert data["steps"] == []


def test_shortest_path_known_connection(client):
    """Find a path from Darren to a connected entity."""
    # First, get Darren's direct neighbors
    r = client.get(f"/relationships/{DARREN_URI}", params={"direction": "outgoing"})
    assert r.status_code == 200
    rels = r.json()["relationships"]
    if not rels:
        pytest.skip("No outgoing relationships for Darren")

    target = rels[0]["object_uri"]
    r2 = client.get(
        "/graph/shortest-path",
        params={"source": DARREN_URI, "target": target},
    )
    assert r2.status_code == 200
    data = r2.json()
    assert data["found"] is True
    assert data["path_length"] >= 1
    assert len(data["steps"]) == data["path_length"]


def test_shortest_path_nonexistent_source(client):
    """Nonexistent source returns 404."""
    r = client.get(
        "/graph/shortest-path",
        params={"source": "orn:does-not-exist", "target": DARREN_URI},
    )
    assert r.status_code == 404


def test_shortest_path_deterministic(client):
    """Same query twice gives same result."""
    params = {"source": DARREN_URI, "target": DARREN_URI}
    r1 = client.get("/graph/shortest-path", params=params)
    r2 = client.get("/graph/shortest-path", params=params)
    assert r1.json() == r2.json()
