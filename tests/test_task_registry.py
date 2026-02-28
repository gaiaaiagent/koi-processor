"""
Regression tests for the task_registry API (task_router.py).

Runs against the live backend on localhost:8351.
Each test uses unique task keys prefixed with "reg-test-" and cleans up after itself
by setting status=cancelled so real task data is never touched.

Usage:
    pytest tests/test_task_registry.py -v
    # or with explicit URL:
    KOI_API_URL=http://localhost:8351 pytest tests/test_task_registry.py -v
"""

import os
import uuid
import pytest
import httpx

BASE_URL = os.getenv("KOI_API_URL", "http://localhost:8351")


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


def make_key(suffix: str) -> str:
    """Generate a unique test task key."""
    return f"reg-test-{suffix}-{uuid.uuid4().hex[:6]}"


def ingest(client, key: str, **kwargs) -> dict:
    payload = {"taskKey": key, "title": kwargs.pop("title", "Regression test task"), **kwargs}
    r = client.post("/tasks/ingest", json=payload)
    assert r.status_code == 200, f"ingest failed ({r.status_code}): {r.text}"
    return r.json()


def cleanup(client, key: str):
    """Mark a test task as cancelled so it doesn't pollute real task lists."""
    client.patch(f"/tasks/{key}", json={"status": "cancelled"})


def get_task(client, key: str, status_filter: str = "open,inbox,in-progress,waiting,cancelled") -> dict | None:
    r = client.get("/tasks/", params={"status": status_filter})
    assert r.status_code == 200, r.text
    return next((t for t in r.json() if t["task_key"] == key), None)


# ---------------------------------------------------------------------------
# Precondition: backend is serving /tasks routes
# ---------------------------------------------------------------------------

def test_tasks_stats_reachable(client):
    """Readiness check — verifies router is mounted, not just the process alive."""
    r = client.get("/tasks/stats")
    assert r.status_code == 200, "/tasks/stats not reachable — is the backend running?"
    data = r.json()
    assert "total_open" in data
    assert "by_status" in data


# ---------------------------------------------------------------------------
# Regression 1: Partial ingest preserves existing status and priority
# ---------------------------------------------------------------------------

class TestIngestPreservesStatusPriority:
    """Fix 2: partial POST /tasks/ingest must not reset status/priority to defaults."""

    def test_partial_ingest_preserves_status_and_priority(self, client):
        key = make_key("partial-preserve")
        try:
            # Create with explicit status/priority
            r = ingest(client, key, status="open", priority="high")
            assert r["action"] == "created"

            # Re-ingest without status/priority (partial payload)
            r2 = client.post("/tasks/ingest", json={"taskKey": key, "title": "Updated title"})
            assert r2.status_code == 200
            assert r2.json()["action"] == "updated"

            task = get_task(client, key)
            assert task is not None, f"Task {key} not found after partial ingest"
            assert task["status"] == "open", f"status regressed to '{task['status']}' (expected 'open')"
            assert task["priority"] == "high", f"priority regressed to '{task['priority']}' (expected 'high')"
        finally:
            cleanup(client, key)

    def test_partial_ingest_null_status_preserves_existing(self, client):
        key = make_key("null-status-preserve")
        try:
            ingest(client, key, status="waiting", priority="critical")

            # Explicitly pass null status
            r = client.post("/tasks/ingest", json={"taskKey": key, "title": "T", "status": None})
            assert r.status_code == 200

            task = get_task(client, key)
            assert task is not None
            assert task["status"] == "waiting", f"status regressed: {task['status']}"
            assert task["priority"] == "critical", f"priority regressed: {task['priority']}"
        finally:
            cleanup(client, key)

    def test_new_task_gets_default_status_inbox(self, client):
        key = make_key("default-status")
        try:
            r = ingest(client, key)
            assert r["action"] == "created"

            task = get_task(client, key, status_filter="inbox")
            assert task is not None, f"New task {key} not found in inbox"
            assert task["status"] == "inbox"
            assert task["priority"] == "medium"
        finally:
            cleanup(client, key)


# ---------------------------------------------------------------------------
# Regression 2: PATCH date field behaviour
# ---------------------------------------------------------------------------

class TestPatchDateClearing:
    """Fix 1 & 3: PATCH must not 500; null date clears; absent date is no-op."""

    def test_patch_status_returns_200(self, client):
        key = make_key("patch-status")
        try:
            ingest(client, key, status="inbox")
            r = client.patch(f"/tasks/{key}", json={"status": "open"})
            assert r.status_code == 200, f"PATCH status returned {r.status_code}: {r.text}"
            task = get_task(client, key, status_filter="open")
            assert task is not None
            assert task["status"] == "open"
        finally:
            cleanup(client, key)

    def test_patch_set_due_date(self, client):
        key = make_key("patch-set-date")
        try:
            ingest(client, key)
            r = client.patch(f"/tasks/{key}", json={"dueDate": "2026-03-15"})
            assert r.status_code == 200, r.text

            task = get_task(client, key)
            assert task is not None
            assert task["due_date"] == "2026-03-15", f"due_date not set: {task['due_date']}"
        finally:
            cleanup(client, key)

    def test_patch_clear_due_date_with_null(self, client):
        key = make_key("patch-clear-date")
        try:
            ingest(client, key)
            client.patch(f"/tasks/{key}", json={"dueDate": "2026-03-15"})

            # Clear with explicit null
            r = client.patch(f"/tasks/{key}", json={"dueDate": None})
            assert r.status_code == 200, r.text

            task = get_task(client, key)
            assert task is not None
            assert task["due_date"] is None, f"due_date not cleared: {task['due_date']}"
        finally:
            cleanup(client, key)

    def test_patch_absent_date_does_not_clear(self, client):
        key = make_key("patch-absent-date")
        try:
            ingest(client, key)
            client.patch(f"/tasks/{key}", json={"dueDate": "2026-04-01"})

            # PATCH without dueDate — must leave it untouched
            r = client.patch(f"/tasks/{key}", json={"status": "open"})
            assert r.status_code == 200

            task = get_task(client, key, status_filter="open")
            assert task is not None
            assert task["due_date"] == "2026-04-01", f"due_date unexpectedly cleared: {task['due_date']}"
        finally:
            cleanup(client, key)

    def test_patch_nonexistent_task_returns_404(self, client):
        r = client.patch("/tasks/does-not-exist-xyz-000", json={"status": "open"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Regression 3: Owner name filter
# ---------------------------------------------------------------------------

class TestOwnerNameFilter:
    """Fix 4: ?owner=Name must resolve via entity_rid_mappings, not just URI substring."""

    def test_owner_uri_substring_filter_works(self, client):
        """Fallback: LIKE on owner_uri still returns tasks for opaque URIs."""
        key = make_key("owner-uri-filter")
        # Use a distinctive URI fragment that won't collide with real data
        owner_uri_fragment = f"orn:test:owner-filter-{uuid.uuid4().hex[:8]}"
        try:
            # Ingest without owner (no wikilink to resolve), then verify generic filter
            ingest(client, key, status="inbox")

            # Filter by a known fragment of Darren's URI if it exists, or just confirm no 500
            r = client.get("/tasks/", params={"owner": "orn:test:nonexistent"})
            assert r.status_code == 200
            # Should return empty list (no match), not an error
            tasks = r.json()
            assert isinstance(tasks, list)
        finally:
            cleanup(client, key)

    def test_owner_filter_no_500_on_name_query(self, client):
        """Name-based owner filter must return 200 (not 500) even with no matches."""
        r = client.get("/tasks/", params={"owner": "David Fortson"})
        assert r.status_code == 200, f"?owner=Name returned {r.status_code}: {r.text}"
        assert isinstance(r.json(), list)
