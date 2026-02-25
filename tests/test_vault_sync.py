"""
Isolated fixture tests for vault sync.

Uses a real pool (manager acquires its own connections).
Cleanup happens via a fixture that removes test data after each test.
Requires a running PostgreSQL with the personal_koi schema + migration 049.
"""

import hashlib
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

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


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def pool():
    """Create a connection pool."""
    p = await asyncpg.create_pool(DB_URL, min_size=1, max_size=3)
    yield p
    await p.close()


@pytest.fixture
async def setup_peer(pool):
    """Insert peer node and vault_sync_peers config (auto-committed). Cleanup after."""
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO koi_net_nodes (node_rid, node_name, node_type, base_url, status)
               VALUES ($1, 'test-peer', 'FULL', 'http://10.0.0.2:8351', 'active')
               ON CONFLICT (node_rid) DO NOTHING""",
            PEER_NODE,
        )
        await conn.execute(
            """INSERT INTO vault_sync_peers (id, peer_node_rid, shared_folder, enabled)
               VALUES (1, $1, 'Shared', TRUE)
               ON CONFLICT (id) DO UPDATE SET peer_node_rid=EXCLUDED.peer_node_rid, enabled=TRUE""",
            PEER_NODE,
        )
    yield
    # Cleanup
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM vault_sync_applied_events WHERE source_node=$1", PEER_NODE)
        await conn.execute("DELETE FROM vault_sync_state WHERE relative_path LIKE 'Shared/%'")
        await conn.execute("DELETE FROM vault_sync_peers WHERE id=1")
        await conn.execute("DELETE FROM koi_net_nodes WHERE node_rid=$1", PEER_NODE)


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
    from api.vault_sync import VaultSyncManager
    return VaultSyncManager(
        pool=pool,
        node_rid=MY_NODE,
        event_queue=event_queue,
        vault_path=str(tmp_vault),
    )


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
    import time
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
async def test_scan_skips_non_md(pool, setup_peer, tmp_vault, mock_event_queue):
    """.txt, .pdf files in Shared/ are ignored."""
    mgr = _make_manager(pool, tmp_vault, mock_event_queue)
    shared = tmp_vault / "Shared"
    (shared / "notes.txt").write_text("plain text")
    (shared / "doc.pdf").write_bytes(b"%PDF-1.4 fake")

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
