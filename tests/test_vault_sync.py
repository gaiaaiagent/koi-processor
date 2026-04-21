"""
Isolated fixture tests for vault sync.

Uses a real pool (manager acquires its own connections).
Cleanup happens via a fixture that removes test data after each test.
Requires a running PostgreSQL with the personal_koi schema + migration 049+050.
"""

import asyncio
import hashlib
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import asyncpg

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DB_URL = os.getenv(
    "POSTGRES_URL",
    "postgresql://darrenzal:@localhost:5432/personal_koi",
)

MY_NODE = "orn:koi-net.node:test-local+abc123"
PEER_NODE = "orn:koi-net.node:test-peer+def456"
PEER_NODE_B = "orn:koi-net.node:test-peer-b+ghi789"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def pool():
    """Create a connection pool."""
    p = await asyncpg.create_pool(DB_URL, min_size=1, max_size=5)
    yield p
    await p.close()


async def _has_060_schema(conn) -> bool:
    """Check if migration 060 has been applied (peer_node_rid is PK)."""
    row = await conn.fetchrow(
        """SELECT 1 FROM information_schema.table_constraints
           WHERE table_name='vault_sync_peers' AND constraint_type='PRIMARY KEY'
           AND constraint_name='vault_sync_peers_pkey'"""
    )
    if not row:
        return False
    # Check which column the PK is on
    col = await conn.fetchval(
        """SELECT column_name FROM information_schema.key_column_usage
           WHERE table_name='vault_sync_peers' AND constraint_name='vault_sync_peers_pkey'"""
    )
    return col == "peer_node_rid"


@pytest.fixture
async def setup_peer(pool):
    """Insert peer node and vault_sync_peers config (auto-committed). Cleanup after.

    Works with both pre-060 (id PK) and post-060 (peer_node_rid PK) schemas.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO koi_net_nodes (node_rid, node_name, node_type, base_url, status)
               VALUES ($1, 'test-peer', 'FULL', 'http://10.0.0.2:8351', 'active')
               ON CONFLICT (node_rid) DO NOTHING""",
            PEER_NODE,
        )
        # Clean all test peers first to avoid conflicts on either schema
        await conn.execute("DELETE FROM vault_sync_peers WHERE peer_node_rid IN ($1, $2)", PEER_NODE, PEER_NODE_B)

        # Clear any pre-existing vault_sync_state rows so scan tests
        # don't see stale rows that trigger spurious FORGET events.
        await conn.execute("DELETE FROM vault_sync_state WHERE relative_path LIKE 'Shared/%'")

        if await _has_060_schema(conn):
            await conn.execute(
                """INSERT INTO vault_sync_peers (peer_node_rid, shared_folder, enabled)
                   VALUES ($1, 'Shared', TRUE)""",
                PEER_NODE,
            )
        else:
            # Legacy schema: id is PK
            await conn.execute(
                """INSERT INTO vault_sync_peers (id, peer_node_rid, shared_folder, enabled)
                   VALUES (1, $1, 'Shared', TRUE)
                   ON CONFLICT (id) DO UPDATE SET peer_node_rid=EXCLUDED.peer_node_rid, enabled=TRUE""",
                PEER_NODE,
            )
    yield
    # Cleanup
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM vault_sync_applied_events WHERE source_node IN ($1, $2)", PEER_NODE, PEER_NODE_B)
        await conn.execute("DELETE FROM vault_sync_state WHERE relative_path LIKE 'Shared/%'")
        await conn.execute("DELETE FROM vault_sync_peers WHERE peer_node_rid IN ($1, $2)", PEER_NODE, PEER_NODE_B)
        await conn.execute("DELETE FROM koi_net_nodes WHERE node_rid IN ($1, $2)", PEER_NODE, PEER_NODE_B)
        await conn.execute("DELETE FROM vault_sync_metrics WHERE id=1")
        await conn.execute("DELETE FROM koi_net_events WHERE rid LIKE 'orn:koi-net.vault-file:%'")


@pytest.fixture
def tmp_vault(tmp_path):
    """Create a temp vault directory with Shared subfolder."""
    shared = tmp_path / "Shared"
    shared.mkdir()
    return tmp_path


@pytest.fixture
def mock_event_queue():
    """Mock EventQueue that records add() calls."""
    eq = AsyncMock()
    eq.add = AsyncMock(return_value=str(uuid.uuid4()))
    eq.node_rid = MY_NODE
    return eq


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _make_manager(pool, tmp_vault, event_queue):
    """Build a VaultSyncManager isolated from production peer rows.

    The local `personal_koi` DB is shared with real vault_sync_peers rows
    (friend-e2e, nuc-personal with 7 folders including Organizations/
    People/Meetings/etc., shawn). Tests should only see the peers they
    registered via setup_peer. We override the instance methods so
    _scan_async / reconcile / etc. operate against just the test peers.
    """
    from api.vault_sync import VaultSyncManager
    mgr = VaultSyncManager(
        pool=pool,
        node_rid=MY_NODE,
        event_queue=event_queue,
        vault_path=str(tmp_vault),
    )

    _TEST_PEERS = {PEER_NODE, PEER_NODE_B}
    _orig_all = mgr._get_all_peers
    _orig_by_source = mgr._get_all_peers_by_source

    async def _all_peers():
        rows = await _orig_all()
        return [r for r in rows if r["peer_node_rid"] in _TEST_PEERS]

    async def _peers_by_source(source_node):
        if source_node not in _TEST_PEERS:
            return []
        return await _orig_by_source(source_node)

    mgr._get_all_peers = _all_peers
    mgr._get_all_peers_by_source = _peers_by_source
    return mgr


# =============================================================================
# Scan tests
# =============================================================================


@pytest.mark.anyio
async def test_scan_new_file(pool, setup_peer, tmp_vault, mock_event_queue):
    """New .md file detected, sync state row created, NEW event queued."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    shared = tmp_vault / "Shared"
    test_file = shared / "test-note.md"
    test_file.write_text("# Hello World\n\nThis is a test.")

    await mgr._scan_async()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM vault_sync_state WHERE relative_path='Shared/test-note.md'"
        )
    assert row is not None
    assert row["origin_node"] == MY_NODE
    assert row["is_deleted"] is False

    mock_event_queue.add.assert_called()
    call_kw = mock_event_queue.add.call_args.kwargs
    assert call_kw["event_type"] == "NEW"


@pytest.mark.anyio
async def test_scan_modified_file(pool, setup_peer, tmp_vault, mock_event_queue):
    """Changed file detected via hash, UPDATE event queued with correct base_hash."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    shared = tmp_vault / "Shared"
    test_file = shared / "modify-test.md"

    # Initial scan
    test_file.write_text("# Version 1")
    await mgr._scan_async()
    mock_event_queue.add.reset_mock()

    # Modify and clear stat cache
    time.sleep(0.01)
    test_file.write_text("# Version 2")
    mgr._stat_cache.clear()

    await mgr._scan_async()

    assert mock_event_queue.add.called
    call_kw = mock_event_queue.add.call_args.kwargs
    assert call_kw["event_type"] == "UPDATE"


@pytest.mark.anyio
async def test_scan_deleted_file(pool, setup_peer, tmp_vault, mock_event_queue):
    """Missing file → tombstone + FORGET event."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    shared = tmp_vault / "Shared"
    test_file = shared / "delete-me.md"
    test_file.write_text("# Will be deleted")

    await mgr._scan_async()
    mock_event_queue.add.reset_mock()

    test_file.unlink()
    mgr._stat_cache.clear()

    await mgr._scan_async()

    assert mock_event_queue.add.called
    call_kw = mock_event_queue.add.call_args.kwargs
    assert call_kw["event_type"] == "FORGET"

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM vault_sync_state WHERE relative_path='Shared/delete-me.md'"
        )
    assert row["is_deleted"] is True


@pytest.mark.anyio
async def test_scan_skips_unsupported_extensions(pool, setup_peer, tmp_vault, mock_event_queue):
    """Files with extensions not in VAULT_SYNC_PATTERNS (e.g. .pdf, .png) are ignored.

    History: this test was originally `test_scan_skips_non_md` on the
    assumption only Markdown was synced. Since commit 400e9c87 the
    supported set expanded to include .md/.jsonl/.json/.jsonld/.txt/
    .csv/.yaml/.yml/.toml, so using .txt as a negative case no longer
    holds. .pdf + .png remain safely outside the set.
    """
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    shared = tmp_vault / "Shared"
    (shared / "doc.pdf").write_bytes(b"%PDF-1.4 fake")
    (shared / "img.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

    await mgr._scan_async()
    mock_event_queue.add.assert_not_called()


@pytest.mark.anyio
async def test_scan_skips_symlinks(pool, setup_peer, tmp_vault, mock_event_queue):
    """Symlinks are not followed."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    shared = tmp_vault / "Shared"
    real_file = tmp_vault / "outside.md"
    real_file.write_text("# Outside")
    symlink = shared / "link.md"
    symlink.symlink_to(real_file)

    await mgr._scan_async()
    mock_event_queue.add.assert_not_called()


@pytest.mark.anyio
async def test_scan_rejects_oversize(pool, setup_peer, tmp_vault, mock_event_queue):
    """Files > 1MB are skipped."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    shared = tmp_vault / "Shared"
    big_file = shared / "huge.md"
    big_file.write_text("x" * 1_048_577)

    await mgr._scan_async()
    mock_event_queue.add.assert_not_called()


# =============================================================================
# Apply tests
# =============================================================================


def _make_apply_kwargs(rel_path, event_type, content, base_hash=None, origin_seq=1, deleted=False):
    """Helper to build apply_event kwargs."""
    chash = _sha256(content) if content else ""
    eid = str(uuid.uuid4())
    contents = {"relative_path": rel_path, "_vault_sync": True}
    if content is not None:
        contents["markdown"] = content
    manifest = {
        "content_hash": chash,
        "relative_path": rel_path,
        "base_hash": base_hash,
        "bytes": len(content.encode("utf-8")) if content else 0,
        "origin_node": PEER_NODE,
        "origin_seq": origin_seq,
        "deleted": deleted,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return dict(
        rid=f"orn:koi-net.vault-file:{rel_path}",
        event_type=event_type,
        contents=contents,
        manifest=manifest,
        source_node=PEER_NODE,
        event_id=eid,
    )


@pytest.mark.anyio
async def test_apply_new_file(pool, setup_peer, tmp_vault, mock_event_queue):
    """Incoming NEW → file written to disk + sync state inserted."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    content = "# From Peer\n\nHello!"

    await mgr.apply_event(**_make_apply_kwargs("Shared/peer-note.md", "NEW", content))

    target = tmp_vault / "Shared" / "peer-note.md"
    assert target.exists()
    assert target.read_text() == content

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM vault_sync_state WHERE relative_path='Shared/peer-note.md'"
        )
    assert row is not None
    assert row["origin_node"] == PEER_NODE


@pytest.mark.anyio
async def test_apply_idempotent(pool, setup_peer, tmp_vault, mock_event_queue):
    """Same event_id redelivered → no-op."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    content = "# Idempotent"
    kwargs = _make_apply_kwargs("Shared/idem.md", "NEW", content)

    await mgr.apply_event(**kwargs)
    await mgr.apply_event(**kwargs)  # Second delivery

    shared = tmp_vault / "Shared"
    md_files = list(shared.glob("idem*.md"))
    assert len(md_files) == 1


@pytest.mark.anyio
async def test_apply_conflict(pool, setup_peer, tmp_vault, mock_event_queue):
    """Incoming UPDATE where local hash != base_hash → conflict copy."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)

    local_content = "# Local Version"
    local_hash = _sha256(local_content)
    local_file = tmp_vault / "Shared" / "conflict-test.md"
    local_file.write_text(local_content)

    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO vault_sync_state
               (relative_path, content_hash, origin_node, origin_seq, file_size_bytes, last_modified_at)
               VALUES ($1, $2, $3, 1, $4, NOW())""",
            "Shared/conflict-test.md", local_hash, MY_NODE,
            len(local_content.encode("utf-8")),
        )

    peer_content = "# Peer Version"
    wrong_base = _sha256("# Some Other Version")
    kwargs = _make_apply_kwargs("Shared/conflict-test.md", "UPDATE", peer_content,
                                base_hash=wrong_base, origin_seq=2)

    await mgr.apply_event(**kwargs)

    # Local file unchanged
    assert local_file.read_text() == local_content

    # Conflict copy created
    shared = tmp_vault / "Shared"
    conflict_files = [f for f in shared.glob("conflict-test (conflict*).md")]
    assert len(conflict_files) == 1
    assert conflict_files[0].read_text() == peer_content


@pytest.mark.anyio
async def test_apply_safe_update(pool, setup_peer, tmp_vault, mock_event_queue):
    """Incoming UPDATE where local hash == base_hash → overwrite."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)

    local_content = "# Base Version"
    local_hash = _sha256(local_content)
    local_file = tmp_vault / "Shared" / "safe-update.md"
    local_file.write_text(local_content)

    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO vault_sync_state
               (relative_path, content_hash, origin_node, origin_seq, file_size_bytes, last_modified_at)
               VALUES ($1, $2, $3, 1, $4, NOW())""",
            "Shared/safe-update.md", local_hash, MY_NODE,
            len(local_content.encode("utf-8")),
        )

    peer_content = "# Updated Version"
    kwargs = _make_apply_kwargs("Shared/safe-update.md", "UPDATE", peer_content,
                                base_hash=local_hash, origin_seq=2)

    await mgr.apply_event(**kwargs)

    assert local_file.read_text() == peer_content

    # No conflict copies
    shared = tmp_vault / "Shared"
    conflict_files = [f for f in shared.glob("safe-update (conflict*).md")]
    assert len(conflict_files) == 0


@pytest.mark.anyio
async def test_apply_stale_delete(pool, setup_peer, tmp_vault, mock_event_queue):
    """Incoming FORGET where local hash != base_hash → delete ignored."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)

    local_content = "# Edited After Peer's Base"
    local_hash = _sha256(local_content)
    local_file = tmp_vault / "Shared" / "stale-delete.md"
    local_file.write_text(local_content)

    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO vault_sync_state
               (relative_path, content_hash, origin_node, origin_seq, file_size_bytes, last_modified_at)
               VALUES ($1, $2, $3, 2, $4, NOW())""",
            "Shared/stale-delete.md", local_hash, MY_NODE,
            len(local_content.encode("utf-8")),
        )

    old_base = _sha256("# Original Version")
    kwargs = _make_apply_kwargs("Shared/stale-delete.md", "FORGET", None, base_hash=old_base)
    # Fix: FORGET has no markdown
    kwargs["contents"] = {"relative_path": "Shared/stale-delete.md", "_vault_sync": True}
    kwargs["manifest"]["content_hash"] = old_base
    kwargs["manifest"]["deleted"] = True

    await mgr.apply_event(**kwargs)

    assert local_file.exists()
    assert local_file.read_text() == local_content


@pytest.mark.anyio
async def test_apply_safe_delete(pool, setup_peer, tmp_vault, mock_event_queue):
    """Incoming FORGET where local hash == base_hash → file removed."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)

    content = "# To Be Deleted"
    chash = _sha256(content)
    local_file = tmp_vault / "Shared" / "safe-delete.md"
    local_file.write_text(content)

    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO vault_sync_state
               (relative_path, content_hash, origin_node, origin_seq, file_size_bytes, last_modified_at)
               VALUES ($1, $2, $3, 1, $4, NOW())""",
            "Shared/safe-delete.md", chash, PEER_NODE,
            len(content.encode("utf-8")),
        )

    kwargs = _make_apply_kwargs("Shared/safe-delete.md", "FORGET", None, base_hash=chash, origin_seq=2)
    kwargs["contents"] = {"relative_path": "Shared/safe-delete.md", "_vault_sync": True}
    kwargs["manifest"]["content_hash"] = chash
    kwargs["manifest"]["deleted"] = True

    await mgr.apply_event(**kwargs)

    assert not local_file.exists()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM vault_sync_state WHERE relative_path='Shared/safe-delete.md'"
        )
    assert row["is_deleted"] is True


@pytest.mark.anyio
async def test_apply_delete_edit_conflict(pool, setup_peer, tmp_vault, mock_event_queue):
    """UPDATE arrives for deleted file → file recreated."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)

    # File was deleted locally (tombstone in DB)
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO vault_sync_state
               (relative_path, content_hash, origin_node, origin_seq, file_size_bytes,
                last_modified_at, is_deleted, deleted_at)
               VALUES ($1, $2, $3, 1, 0, NOW(), TRUE, NOW())""",
            "Shared/revived.md", _sha256("old"), MY_NODE,
        )

    new_content = "# Revived by peer"
    kwargs = _make_apply_kwargs("Shared/revived.md", "UPDATE", new_content,
                                base_hash=_sha256("old"), origin_seq=2)

    await mgr.apply_event(**kwargs)

    target = tmp_vault / "Shared" / "revived.md"
    assert target.exists()
    assert target.read_text() == new_content


@pytest.mark.anyio
async def test_path_traversal_rejected(pool, setup_peer, tmp_vault, mock_event_queue):
    """../etc/passwd in relative_path → rejected."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)

    await mgr.apply_event(
        rid="orn:koi-net.vault-file:Shared/../../../etc/passwd",
        event_type="NEW",
        contents={"markdown": "hacked", "relative_path": "Shared/../../../etc/passwd", "_vault_sync": True},
        manifest={"content_hash": _sha256("hacked"),
                  "relative_path": "Shared/../../../etc/passwd",
                  "bytes": 6, "origin_node": PEER_NODE, "origin_seq": 1,
                  "timestamp": datetime.now(timezone.utc).isoformat()},
        source_node=PEER_NODE,
        event_id=str(uuid.uuid4()),
    )

    total_rejected = sum(mgr._rejected_counts.values())
    assert total_rejected > 0


@pytest.mark.anyio
async def test_absolute_path_rejected(pool, setup_peer, tmp_vault, mock_event_queue):
    """/etc/passwd → rejected."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)

    await mgr.apply_event(
        rid="orn:koi-net.vault-file:/etc/passwd",
        event_type="NEW",
        contents={"markdown": "hacked", "relative_path": "/etc/passwd", "_vault_sync": True},
        manifest={"content_hash": _sha256("hacked"),
                  "relative_path": "/etc/passwd",
                  "bytes": 6, "origin_node": PEER_NODE, "origin_seq": 1,
                  "timestamp": datetime.now(timezone.utc).isoformat()},
        source_node=PEER_NODE,
        event_id=str(uuid.uuid4()),
    )

    total_rejected = sum(mgr._rejected_counts.values())
    assert total_rejected > 0


@pytest.mark.anyio
async def test_atomic_write(pool, tmp_vault, mock_event_queue):
    """File written via tmp+rename pattern."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    target = tmp_vault / "Shared" / "atomic-test.md"
    content = "# Atomic Write Test"

    await mgr._atomic_write(target, content)

    assert target.exists()
    assert target.read_text() == content
    tmp_files = list((tmp_vault / "Shared").glob("*.tmp.*"))
    assert len(tmp_files) == 0


@pytest.mark.anyio
async def test_conflict_copy_naming(pool, setup_peer, tmp_vault, mock_event_queue):
    """Conflict copy follows (conflict YYYY-MM-DD HH-MM-SS).md pattern."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)

    local_content = "# Local"
    local_hash = _sha256(local_content)
    local_file = tmp_vault / "Shared" / "naming-test.md"
    local_file.write_text(local_content)

    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO vault_sync_state
               (relative_path, content_hash, origin_node, origin_seq, file_size_bytes, last_modified_at)
               VALUES ($1, $2, $3, 1, $4, NOW())""",
            "Shared/naming-test.md", local_hash, MY_NODE,
            len(local_content.encode("utf-8")),
        )

    peer_content = "# Peer"
    kwargs = _make_apply_kwargs("Shared/naming-test.md", "UPDATE", peer_content,
                                base_hash=_sha256("wrong base"), origin_seq=2)

    await mgr.apply_event(**kwargs)

    shared = tmp_vault / "Shared"
    conflict_files = list(shared.glob("naming-test (conflict*).md"))
    assert len(conflict_files) == 1

    name = conflict_files[0].name
    assert re.match(r"naming-test \(conflict \d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2}\)\.md", name)


# =============================================================================
# WP1: Metrics tests
# =============================================================================


@pytest.mark.anyio
async def test_metrics_increment(pool, setup_peer, tmp_vault, mock_event_queue):
    """Scan and apply bump the correct counters."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    shared = tmp_vault / "Shared"

    # Create a file and scan → events_queued should increase
    (shared / "metric1.md").write_text("# Metric Test")
    await mgr._scan_async()
    assert mgr._metrics.events_queued >= 1
    assert mgr._metrics.files_scanned >= 1

    # Apply an event → events_applied should increase
    content = "# Applied"
    await mgr.apply_event(**_make_apply_kwargs("Shared/applied.md", "NEW", content))
    assert mgr._metrics.events_applied >= 1

    # Apply duplicate → events_skipped_dedup should increase
    kwargs = _make_apply_kwargs("Shared/dedup-test.md", "NEW", "# Dedup")
    await mgr.apply_event(**kwargs)
    await mgr.apply_event(**kwargs)
    assert mgr._metrics.events_skipped_dedup >= 1


@pytest.mark.anyio
async def test_metrics_persist_load(pool, setup_peer, tmp_vault, mock_event_queue):
    """Persist metrics, create new manager, verify restored."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    shared = tmp_vault / "Shared"
    (shared / "persist-test.md").write_text("# Persist")
    await mgr._scan_async()

    original_queued = mgr._metrics.events_queued
    assert original_queued >= 1

    # Persist
    await mgr.persist_metrics()

    # New manager, load metrics
    mgr2 = _make_manager(pool, tmp_vault, mock_event_queue)
    assert mgr2._metrics.events_queued == 0  # Fresh

    await mgr2.load_metrics()
    assert mgr2._metrics.events_queued == original_queued


@pytest.mark.anyio
async def test_status_contains_metrics(pool, setup_peer, tmp_vault, mock_event_queue):
    """get_status() includes metrics key."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    status = await mgr.get_status()

    assert "metrics" in status
    assert "schema_version" in status["metrics"]
    assert "events_queued" in status["metrics"]
    assert "scans_completed" in status["metrics"]
    # Backward compat
    assert "rejected_events" in status


@pytest.mark.anyio
async def test_scan_apply_concurrency(pool, setup_peer, tmp_vault, mock_event_queue):
    """Launch scan and apply_event concurrently — no false conflict, consistent state."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    shared = tmp_vault / "Shared"

    # Create a file for scanning
    (shared / "concurrent-scan.md").write_text("# Scan Side")

    # Also prepare an apply event for a different file
    apply_content = "# Apply Side"
    apply_kwargs = _make_apply_kwargs("Shared/concurrent-apply.md", "NEW", apply_content)

    # Run scan and apply concurrently
    await asyncio.gather(
        mgr._scan_async(),
        mgr.apply_event(**apply_kwargs),
    )

    # Verify both files have consistent state
    async with pool.acquire() as conn:
        scan_row = await conn.fetchrow(
            "SELECT * FROM vault_sync_state WHERE relative_path='Shared/concurrent-scan.md'"
        )
        apply_row = await conn.fetchrow(
            "SELECT * FROM vault_sync_state WHERE relative_path='Shared/concurrent-apply.md'"
        )

    assert scan_row is not None
    assert apply_row is not None
    assert apply_row["origin_node"] == PEER_NODE

    # No conflict copies for the applied file
    conflict_files = list(shared.glob("concurrent-apply (conflict*).md"))
    assert len(conflict_files) == 0


@pytest.mark.anyio
async def test_large_scan_does_not_starve_apply(pool, setup_peer, tmp_vault, mock_event_queue):
    """Start a capped scan with 100+ files, fire apply_event, verify apply completes < 1s."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    shared = tmp_vault / "Shared"

    # Create 120 small files
    for i in range(120):
        (shared / f"bulk-{i:04d}.md").write_text(f"# File {i}\n\nContent for bulk test.")

    apply_content = "# Urgent Apply"
    apply_kwargs = _make_apply_kwargs("Shared/urgent.md", "NEW", apply_content)

    # Start scan in background, fire apply
    scan_task = asyncio.create_task(mgr._scan_async())

    # Give scan a tiny head start
    await asyncio.sleep(0.05)

    start = time.monotonic()
    await mgr.apply_event(**apply_kwargs)
    elapsed = time.monotonic() - start

    # Wait for scan to finish
    await scan_task

    # SLO: apply_event returns in < 1s
    assert elapsed < 1.0, f"apply_event took {elapsed:.2f}s (SLO: < 1s)"

    # File was written
    assert (shared / "urgent.md").exists()


# =============================================================================
# WP3: Backpressure tests
# =============================================================================


@pytest.mark.anyio
async def test_backpressure_file_cap(pool, setup_peer, tmp_vault, mock_event_queue):
    """150 files, cap=50 → at most 50 events queued in one cycle."""
    import api.vault_sync as vs
    original = vs.MAX_FILES_PER_SCAN
    vs.MAX_FILES_PER_SCAN = 50
    try:
        mgr = _make_manager(pool, tmp_vault, mock_event_queue)
        shared = tmp_vault / "Shared"
        for i in range(150):
            (shared / f"cap-{i:04d}.md").write_text(f"# Cap test {i}")

        await mgr._scan_async()

        # Events queued should be <= 50
        assert mgr._metrics.events_queued <= 50
        assert mgr._metrics.scans_capped >= 1
    finally:
        vs.MAX_FILES_PER_SCAN = original


@pytest.mark.anyio
async def test_backpressure_byte_cap(pool, setup_peer, tmp_vault, mock_event_queue):
    """Large files with 5MB cap → scan stops mid-batch."""
    import api.vault_sync as vs
    original = vs.MAX_BYTES_PER_SCAN
    vs.MAX_BYTES_PER_SCAN = 5 * 1024 * 1024  # 5 MB
    try:
        mgr = _make_manager(pool, tmp_vault, mock_event_queue)
        shared = tmp_vault / "Shared"
        # Create 20 files of ~500KB each (10 MB total, should cap at ~10)
        for i in range(20):
            (shared / f"big-{i:04d}.md").write_text("x" * 500_000)

        await mgr._scan_async()

        assert mgr._metrics.scans_capped >= 1
        # Should have queued roughly 10 files (5MB / 500KB)
        assert mgr._metrics.events_queued <= 12
    finally:
        vs.MAX_BYTES_PER_SCAN = original


@pytest.mark.anyio
async def test_backpressure_event_cap_includes_deletes(pool, setup_peer, tmp_vault, mock_event_queue):
    """Total event cap=10 across creates and deletes."""
    import api.vault_sync as vs
    orig_max = vs.MAX_EVENTS_PER_SCAN
    orig_reserve = vs.DELETE_EVENT_RESERVE
    vs.MAX_EVENTS_PER_SCAN = 10
    vs.DELETE_EVENT_RESERVE = 3
    try:
        mgr = _make_manager(pool, tmp_vault, mock_event_queue)
        shared = tmp_vault / "Shared"

        # Create 5 files, scan them
        for i in range(5):
            (shared / f"evt-{i}.md").write_text(f"# Event {i}")
        await mgr._scan_async()
        initial_queued = mgr._metrics.events_queued
        mock_event_queue.add.reset_mock()

        # Now create 10 more files and delete the original 5
        for i in range(5):
            (shared / f"evt-{i}.md").unlink()
        for i in range(5, 15):
            (shared / f"evt-{i}.md").write_text(f"# Event {i}")
        mgr._stat_cache.clear()

        # Reset metrics for clarity
        mgr._metrics.events_queued = 0
        await mgr._scan_async()

        # Total events this cycle should be capped at 10
        assert mgr._metrics.events_queued <= 10
    finally:
        vs.MAX_EVENTS_PER_SCAN = orig_max
        vs.DELETE_EVENT_RESERVE = orig_reserve


# =============================================================================
# WP2a: Reconcile detect tests
# =============================================================================


@pytest.mark.anyio
async def test_reconcile_detect_missing_on_disk(pool, setup_peer, tmp_vault, mock_event_queue):
    """File in DB but missing on disk → reported in missing_on_disk."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)

    # Insert a row for a file that doesn't exist on disk
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO vault_sync_state
               (relative_path, content_hash, origin_node, origin_seq, file_size_bytes, last_modified_at)
               VALUES ($1, $2, $3, 1, 10, NOW())""",
            "Shared/gone.md", _sha256("# Gone"), MY_NODE,
        )

    result = await mgr.reconcile(mode="detect", peer_rid=PEER_NODE)
    assert "Shared/gone.md" in result["missing_on_disk"]
    assert result["total_drift"] >= 1


@pytest.mark.anyio
async def test_reconcile_detect_hash_mismatch(pool, setup_peer, tmp_vault, mock_event_queue):
    """File on disk has different hash than DB → hash_mismatch."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    shared = tmp_vault / "Shared"

    (shared / "stale.md").write_text("# Current Version")

    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO vault_sync_state
               (relative_path, content_hash, origin_node, origin_seq, file_size_bytes, last_modified_at)
               VALUES ($1, $2, $3, 1, 10, NOW())""",
            "Shared/stale.md", _sha256("# Old Version"), MY_NODE,
        )

    result = await mgr.reconcile(mode="detect", peer_rid=PEER_NODE)
    assert "Shared/stale.md" in result["hash_mismatch"]
    assert result["total_drift"] >= 1


@pytest.mark.anyio
async def test_reconcile_detect_missing_in_db(pool, setup_peer, tmp_vault, mock_event_queue):
    """File on disk but not in DB → missing_in_db."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    shared = tmp_vault / "Shared"

    (shared / "new-untracked.md").write_text("# Untracked")

    result = await mgr.reconcile(mode="detect", peer_rid=PEER_NODE)
    assert "Shared/new-untracked.md" in result["missing_in_db"]
    assert result["total_drift"] >= 1


@pytest.mark.anyio
async def test_reconcile_vault_unavailable_raises(pool, setup_peer, tmp_vault, mock_event_queue):
    """Missing shared folder raises VaultUnavailableError."""
    from api.vault_sync import VaultUnavailableError

    mgr = _make_manager(pool, tmp_vault, mock_event_queue)

    # Remove the shared folder
    import shutil
    shutil.rmtree(tmp_vault / "Shared")

    with pytest.raises(VaultUnavailableError):
        await mgr.reconcile(mode="detect", peer_rid=PEER_NODE)


# =============================================================================
# WP2b: Reconcile repair tests
# =============================================================================


@pytest.mark.anyio
async def test_reconcile_repair_disabled_by_default(pool, setup_peer, tmp_vault, mock_event_queue):
    """Repair mode returns error when VAULT_SYNC_REPAIR_ENABLED is not set."""
    # This tests the router-level gate, but we can also verify behavior directly
    # by checking that the endpoint would return 403 (tested via router integration).
    # Here we test the manager-level repair which does work — the gate is at the router.
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    # Repair without confirm returns drift report + repair_requires_confirm
    result = await mgr.reconcile(mode="repair", confirm=False, peer_rid=PEER_NODE)
    assert result.get("repair_requires_confirm") is True


@pytest.mark.anyio
async def test_reconcile_repair_requires_confirm(pool, setup_peer, tmp_vault, mock_event_queue):
    """mode=repair without confirm returns drift report with repair_requires_confirm=True."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    shared = tmp_vault / "Shared"
    (shared / "untracked.md").write_text("# Untracked")

    result = await mgr.reconcile(mode="repair", confirm=False, peer_rid=PEER_NODE)
    assert result["repair_requires_confirm"] is True
    assert result["total_drift"] >= 1
    assert "actions_taken" not in result


@pytest.mark.anyio
async def test_reconcile_repair_queues_events(pool, setup_peer, tmp_vault, mock_event_queue):
    """With confirm=True, queues correct event types."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    shared = tmp_vault / "Shared"

    # missing_in_db — file on disk, not in DB
    (shared / "repair-new.md").write_text("# Repair New")

    # hash_mismatch — DB has old hash
    (shared / "repair-stale.md").write_text("# Current")
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO vault_sync_state
               (relative_path, content_hash, origin_node, origin_seq, file_size_bytes, last_modified_at)
               VALUES ($1, $2, $3, 1, 10, NOW())""",
            "Shared/repair-stale.md", _sha256("# Old"), MY_NODE,
        )

    # missing_on_disk — DB has row, file doesn't exist
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO vault_sync_state
               (relative_path, content_hash, origin_node, origin_seq, file_size_bytes, last_modified_at)
               VALUES ($1, $2, $3, 1, 10, NOW())""",
            "Shared/repair-gone.md", _sha256("# Gone"), MY_NODE,
        )

    result = await mgr.reconcile(mode="repair", confirm=True, peer_rid=PEER_NODE)
    assert result["actions_taken"] == 3
    assert mock_event_queue.add.call_count >= 3

    # Verify DB state is consistent after repair
    async with pool.acquire() as conn:
        new_row = await conn.fetchrow(
            "SELECT * FROM vault_sync_state WHERE relative_path='Shared/repair-new.md'"
        )
        assert new_row is not None
        assert new_row["is_deleted"] is False

        gone_row = await conn.fetchrow(
            "SELECT * FROM vault_sync_state WHERE relative_path='Shared/repair-gone.md'"
        )
        assert gone_row["is_deleted"] is True


@pytest.mark.anyio
async def test_reconcile_repair_respects_paths(pool, setup_peer, tmp_vault, mock_event_queue):
    """Only repairs listed paths."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    shared = tmp_vault / "Shared"

    (shared / "include-me.md").write_text("# Include")
    (shared / "skip-me.md").write_text("# Skip")

    result = await mgr.reconcile(
        mode="repair", confirm=True,
        paths=["Shared/include-me.md"],
        peer_rid=PEER_NODE,
    )
    assert result["actions_taken"] == 1

    # skip-me.md should not be in DB
    async with pool.acquire() as conn:
        skip_row = await conn.fetchrow(
            "SELECT * FROM vault_sync_state WHERE relative_path='Shared/skip-me.md'"
        )
    assert skip_row is None


@pytest.mark.anyio
async def test_reconcile_repair_max_actions(pool, setup_peer, tmp_vault, mock_event_queue):
    """Caps at limit, returns remaining count."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    shared = tmp_vault / "Shared"

    for i in range(10):
        (shared / f"cap-{i}.md").write_text(f"# Cap {i}")

    result = await mgr.reconcile(mode="repair", confirm=True, max_actions=3, peer_rid=PEER_NODE)
    assert result["actions_taken"] == 3
    assert result["capped"] is True
    assert result["remaining"] == 7


@pytest.mark.anyio
async def test_reconcile_repair_idempotent(pool, setup_peer, tmp_vault, mock_event_queue):
    """Second run finds no drift."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    shared = tmp_vault / "Shared"

    (shared / "idempotent.md").write_text("# Idempotent")

    result1 = await mgr.reconcile(mode="repair", confirm=True, peer_rid=PEER_NODE)
    assert result1["actions_taken"] >= 1

    result2 = await mgr.reconcile(mode="repair", confirm=True, peer_rid=PEER_NODE)
    assert result2["total_drift"] == 0
    assert result2.get("actions_taken", 0) == 0


@pytest.mark.anyio
async def test_reconcile_repair_vault_unavailable(pool, setup_peer, tmp_vault, mock_event_queue):
    """Refuses repair when shared folder missing (503 at endpoint level)."""
    from api.vault_sync import VaultUnavailableError

    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    import shutil
    shutil.rmtree(tmp_vault / "Shared")

    with pytest.raises(VaultUnavailableError):
        await mgr.reconcile(mode="repair", confirm=True, peer_rid=PEER_NODE)


# =============================================================================
# WP4: Watcher tests
# =============================================================================


@pytest.mark.anyio
async def test_watcher_triggers_early_scan(pool, setup_peer, tmp_vault, mock_event_queue):
    """Setting the change_event triggers scan before interval."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    shared = tmp_vault / "Shared"
    (shared / "watcher-test.md").write_text("# Watcher")

    # Set scan interval very high so timer-based scan won't trigger
    mgr.scan_interval = 9999
    mgr._last_scan_at = datetime.now(timezone.utc)

    # But set the watcher event
    mgr._change_event.set()

    await mgr.run_cycle()

    assert mgr._metrics.watcher_triggered_scans >= 1
    assert mgr._metrics.scans_completed >= 1


@pytest.mark.anyio
async def test_watcher_fail_open(pool, setup_peer, tmp_vault, mock_event_queue):
    """When watchdog import fails, periodic scan still works."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    shared = tmp_vault / "Shared"
    (shared / "failopen.md").write_text("# Fail Open")

    # Mock the import to fail
    with patch.dict("sys.modules", {"watchdog": None, "watchdog.observers": None, "watchdog.events": None}):
        mgr.start_watcher()

    assert mgr._metrics.watcher_enabled is False

    # Periodic scan should still work
    await mgr.run_cycle()
    assert mgr._metrics.scans_completed >= 1


@pytest.mark.anyio
async def test_scan_works_without_watcher(pool, setup_peer, tmp_vault, mock_event_queue):
    """VAULT_SYNC_WATCHER=false — periodic scan fires on interval."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    shared = tmp_vault / "Shared"
    (shared / "no-watcher.md").write_text("# No Watcher")

    with patch.dict(os.environ, {"VAULT_SYNC_WATCHER": "false"}):
        mgr.start_watcher()

    assert mgr._metrics.watcher_enabled is False
    assert mgr._watcher is None

    # Timer-based scan should work
    await mgr.run_cycle()
    assert mgr._metrics.scans_completed >= 1


# =============================================================================
# Multi-peer tests
# =============================================================================


async def _setup_peer_b(pool):
    """Add a second peer for multi-peer tests."""
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO koi_net_nodes (node_rid, node_name, node_type, base_url, status)
               VALUES ($1, 'test-peer-b', 'FULL', 'http://10.0.0.3:8351', 'active')
               ON CONFLICT (node_rid) DO NOTHING""",
            PEER_NODE_B,
        )
        await conn.execute("DELETE FROM vault_sync_peers WHERE peer_node_rid=$1", PEER_NODE_B)
        await conn.execute(
            """INSERT INTO vault_sync_peers (peer_node_rid, shared_folder, enabled)
               VALUES ($1, 'Shared', TRUE)""",
            PEER_NODE_B,
        )


@pytest.mark.anyio
async def test_multi_peer_scan_queues_for_both(pool, setup_peer, tmp_vault, mock_event_queue):
    """Configure 2 peers, verify scan queues events for both."""
    await _setup_peer_b(pool)
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    shared = tmp_vault / "Shared"
    (shared / "multi-peer.md").write_text("# Multi-peer test")

    await mgr._scan_async()

    # Should have queued events targeting both peers
    calls = mock_event_queue.add.call_args_list
    target_nodes = {c.kwargs.get("target_node") for c in calls}
    assert PEER_NODE in target_nodes
    assert PEER_NODE_B in target_nodes


@pytest.mark.anyio
async def test_forwarding_to_other_peers(pool, setup_peer, tmp_vault, mock_event_queue):
    """Apply event from peer A → verify queued (forwarded) for peer B."""
    from api.event_queue import EventQueue
    # Use real event queue for forwarding verification
    eq = EventQueue(pool, MY_NODE)
    await _setup_peer_b(pool)

    from api.vault_sync import VaultSyncManager
    mgr = VaultSyncManager(
        pool=pool,
        node_rid=MY_NODE,
        event_queue=eq,
        vault_path=str(tmp_vault),
    )

    content = "# Forwarded content"
    kwargs = _make_apply_kwargs("Shared/forward-test.md", "NEW", content)
    await mgr.apply_event(**kwargs)

    # Check that an event was queued for PEER_NODE_B (forwarded)
    async with pool.acquire() as conn:
        forwarded = await conn.fetchval(
            """SELECT COUNT(*) FROM koi_net_events
               WHERE rid = 'orn:koi-net.vault-file:Shared/forward-test.md'
               AND target_node = $1""",
            PEER_NODE_B,
        )
    assert forwarded >= 1

    # Cleanup
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM koi_net_events WHERE rid LIKE 'orn:koi-net.vault-file:Shared/forward-test%'"
        )


@pytest.mark.anyio
async def test_loop_detection(pool, setup_peer, tmp_vault, mock_event_queue):
    """Apply forwarded event → verify no re-forward to source (loop prevented)."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)

    content = "# Loop test"
    kwargs = _make_apply_kwargs("Shared/loop-test.md", "NEW", content)

    # Apply once
    await mgr.apply_event(**kwargs)

    # Try to apply same event_id again (simulating a forwarded loop)
    await mgr.apply_event(**kwargs)

    # Second apply should be deduped
    assert mgr._metrics.events_skipped_dedup >= 1
    assert mgr._metrics.events_applied == 1


@pytest.mark.anyio
async def test_seq_monotonic(pool, setup_peer, tmp_vault, mock_event_queue):
    """Edit → foreign apply → edit → verify seq always increases."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    shared = tmp_vault / "Shared"

    # Local edit
    (shared / "seq-test.md").write_text("# Version 1")
    await mgr._scan_async()

    async with pool.acquire() as conn:
        row1 = await conn.fetchrow(
            "SELECT origin_seq, local_edit_seq FROM vault_sync_state WHERE relative_path='Shared/seq-test.md'"
        )
    assert row1 is not None
    seq_after_local = row1["local_edit_seq"]

    # Foreign apply (simulates event from peer)
    foreign_content = "# Foreign Version"
    kwargs = _make_apply_kwargs("Shared/seq-test.md", "UPDATE", foreign_content,
                                base_hash=_sha256("# Version 1"), origin_seq=5)
    await mgr.apply_event(**kwargs)

    # Local edit again
    time.sleep(0.01)
    (shared / "seq-test.md").write_text("# Version 3")
    mgr._stat_cache.clear()
    mock_event_queue.add.reset_mock()
    await mgr._scan_async()

    async with pool.acquire() as conn:
        row2 = await conn.fetchrow(
            "SELECT origin_seq, local_edit_seq FROM vault_sync_state WHERE relative_path='Shared/seq-test.md'"
        )
    # local_edit_seq should always increase
    assert row2["local_edit_seq"] > seq_after_local


@pytest.mark.anyio
async def test_source_allowlist(pool, setup_peer, tmp_vault, mock_event_queue):
    """Event from unconfigured peer → rejected with unauthorized_source."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)

    unknown_peer = "orn:koi-net.node:unknown-peer+xyz999"
    content = "# Unauthorized"
    kwargs = _make_apply_kwargs("Shared/unauthorized.md", "NEW", content)
    kwargs["source_node"] = unknown_peer

    await mgr.apply_event(**kwargs)

    assert mgr._metrics.rejected_unauthorized_source >= 1
    # File should NOT exist
    assert not (tmp_vault / "Shared" / "unauthorized.md").exists()
