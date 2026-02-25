"""
Vault Sync Manager — bidirectional markdown sync between KOI-net peers.

Phase Sync-1: two peers, poll-based (~60s), conflict copies, markdown only.
Reuses the existing EventQueue, poller, and signing infrastructure.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

logger = logging.getLogger(__name__)

MAX_VAULT_FILE_BYTES = 1_048_576  # 1 MB
DEFAULT_SCAN_INTERVAL = 60  # seconds
DEFAULT_RECONCILE_INTERVAL = 6 * 3600  # 6 hours
TOMBSTONE_CLEANUP_DAYS = 30
DEDUP_CLEANUP_DAYS = 90
WRITE_DEBOUNCE_MS = 500


class VaultSyncManager:
    """Manages bidirectional markdown file sync over KOI-net events."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        node_rid: str,
        event_queue,
        vault_path: str,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
        reconcile_interval: int = DEFAULT_RECONCILE_INTERVAL,
    ):
        self.pool = pool
        self.node_rid = node_rid
        self.event_queue = event_queue
        self.vault_path = Path(vault_path).expanduser().resolve()
        self.scan_interval = scan_interval
        self.reconcile_interval = reconcile_interval
        self._scan_in_progress = False
        self._last_scan_at: Optional[datetime] = None
        self._last_reconcile_at: Optional[datetime] = None
        # Cache of (relative_path -> (mtime_ns, size, content_hash))
        self._stat_cache: Dict[str, Tuple[int, int, str]] = {}
        # Rejection counters
        self._rejected_counts: Dict[str, int] = {
            "path_traversal": 0,
            "oversize": 0,
            "missing_fields": 0,
            "invalid_type": 0,
            "integrity_mismatch": 0,
            "stale_event": 0,
        }

    # ------------------------------------------------------------------
    # Public interface (called from poller)
    # ------------------------------------------------------------------

    async def run_cycle(self):
        """Run a scan cycle if interval has elapsed and no scan is in progress."""
        now = datetime.now(timezone.utc)

        # Scan cycle
        if self._scan_in_progress:
            logger.debug("vault_sync: scan already in progress, skipping")
            return

        should_scan = (
            self._last_scan_at is None
            or (now - self._last_scan_at).total_seconds() >= self.scan_interval
        )
        if should_scan:
            self._scan_in_progress = True
            try:
                await self._scan_async()
            except Exception as e:
                logger.error(f"vault_sync: scan error: {e}")
            finally:
                self._scan_in_progress = False
                self._last_scan_at = now

        # Reconcile cycle
        should_reconcile = (
            self._last_reconcile_at is None
            or (now - self._last_reconcile_at).total_seconds() >= self.reconcile_interval
        )
        if should_reconcile:
            try:
                await self._reconcile()
            except Exception as e:
                logger.error(f"vault_sync: reconcile error: {e}")
            finally:
                self._last_reconcile_at = now

    async def apply_event(
        self,
        rid: str,
        event_type: str,
        contents: Dict[str, Any],
        manifest: Dict[str, Any],
        source_node: str,
        event_id: Optional[str] = None,
    ):
        """Apply an incoming vault-sync event from a peer."""
        # Input validation
        if not isinstance(contents, dict):
            self._reject("invalid_type", rid, source_node, event_id, "contents is not a dict")
            return
        if not event_id:
            self._reject("missing_fields", rid, source_node, event_id, "event_id is missing")
            return
        if not isinstance(manifest, dict):
            self._reject("missing_fields", rid, source_node, event_id, "manifest is not a dict")
            return
        if not manifest.get("content_hash"):
            self._reject("missing_fields", rid, source_node, event_id, "manifest.content_hash missing")
            return

        relative_path = manifest.get("relative_path") or contents.get("relative_path")
        if not relative_path:
            self._reject("missing_fields", rid, source_node, event_id, "relative_path missing")
            return

        # Validate file extension
        if not relative_path.endswith(".md"):
            self._reject("invalid_type", rid, source_node, event_id, f"not a .md file: {relative_path}")
            return

        # Get peer config
        peer = await self._get_peer_config()
        if not peer:
            logger.warning("vault_sync: no peer configured, ignoring event")
            return

        # Path traversal check
        shared_folder = peer["shared_folder"]
        base_dir = (self.vault_path / shared_folder).resolve()
        target_path = (base_dir / relative_path.split("/", 1)[-1] if "/" in relative_path else base_dir / relative_path).resolve()

        # Actually, relative_path is like "Shared/Design Doc.md" — rebuild properly
        target_path = (self.vault_path / relative_path).resolve()
        if not target_path.is_relative_to(base_dir):
            self._reject("path_traversal", rid, source_node, event_id, f"relative_path escapes shared folder: {relative_path}")
            return

        # Size check
        manifest_bytes = manifest.get("bytes", 0)
        if manifest_bytes > MAX_VAULT_FILE_BYTES:
            self._reject("oversize", rid, source_node, event_id, f"manifest.bytes={manifest_bytes}")
            return

        markdown = contents.get("markdown", "")
        if not manifest.get("deleted", False):
            actual_bytes = len(markdown.encode("utf-8"))
            if actual_bytes > MAX_VAULT_FILE_BYTES:
                self._reject("oversize", rid, source_node, event_id, f"actual bytes={actual_bytes}")
                return
            # Integrity check
            if manifest_bytes and actual_bytes != manifest_bytes:
                self._reject("integrity_mismatch", rid, source_node, event_id,
                             f"manifest.bytes={manifest_bytes} != actual={actual_bytes}")
                return

        # Idempotency check
        async with self.pool.acquire() as conn:
            already = await conn.fetchval(
                "SELECT 1 FROM vault_sync_applied_events WHERE source_node=$1 AND event_id=$2::UUID AND rid=$3",
                source_node, event_id, rid,
            )
            if already:
                logger.debug(f"vault_sync: duplicate event {event_id}, skipping")
                return

        # Get local state
        async with self.pool.acquire() as conn:
            local_row = await conn.fetchrow(
                "SELECT * FROM vault_sync_state WHERE relative_path=$1",
                relative_path,
            )

        content_hash = manifest["content_hash"]
        base_hash = manifest.get("base_hash")
        origin_seq = manifest.get("origin_seq", 1)
        origin_node = manifest.get("origin_node", source_node)
        deleted = manifest.get("deleted", False)
        timestamp = manifest.get("timestamp", datetime.now(timezone.utc).isoformat())

        # Stale-event guard: same origin, older or equal seq
        if local_row and not local_row["is_deleted"]:
            if origin_node == local_row["origin_node"] and origin_seq <= local_row["origin_seq"]:
                self._reject("stale_event", rid, source_node, event_id,
                             f"origin_seq {origin_seq} <= local {local_row['origin_seq']}")
                return

        if event_type == "FORGET":
            await self._apply_forget(
                target_path, relative_path, local_row, base_hash,
                origin_node, origin_seq, source_node, event_id, rid,
            )
        elif event_type in ("NEW", "UPDATE"):
            await self._apply_new_or_update(
                target_path, relative_path, local_row, event_type,
                markdown, content_hash, base_hash, origin_node, origin_seq,
                source_node, event_id, rid, manifest_bytes, timestamp,
            )
        else:
            self._reject("invalid_type", rid, source_node, event_id, f"unknown event_type: {event_type}")
            return

    async def get_status(self) -> Dict[str, Any]:
        """Return sync dashboard info."""
        peer = await self._get_peer_config()
        async with self.pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM vault_sync_state WHERE is_deleted=FALSE")
            tombstones = await conn.fetchval("SELECT COUNT(*) FROM vault_sync_state WHERE is_deleted=TRUE")
            pending = await conn.fetchval(
                """SELECT COUNT(*) FROM koi_net_events
                   WHERE rid LIKE 'orn:koi-net.vault-file:%'
                   AND expires_at > NOW()
                   AND array_length(delivered_to, 1) IS NULL""",
            )
        return {
            "enabled": peer is not None and peer.get("enabled", False),
            "peer": peer["peer_node_rid"] if peer else None,
            "shared_folder": peer["shared_folder"] if peer else None,
            "files_tracked": total or 0,
            "tombstones": tombstones or 0,
            "pending_events": pending or 0,
            "last_scan_at": self._last_scan_at.isoformat() if self._last_scan_at else None,
            "last_reconcile_at": self._last_reconcile_at.isoformat() if self._last_reconcile_at else None,
            "rejected_events": dict(self._rejected_counts),
        }

    async def configure(self, peer_name: str, shared_folder: str = "Shared", enabled: bool = True) -> Dict[str, Any]:
        """Configure vault sync for a peer. Resolves peer name to node_rid."""
        async with self.pool.acquire() as conn:
            # Resolve peer name to node_rid
            node_rid = await conn.fetchval(
                "SELECT node_rid FROM koi_net_peer_aliases WHERE LOWER(alias) = LOWER($1)",
                peer_name,
            )
            if not node_rid:
                node_rid = await conn.fetchval(
                    "SELECT node_rid FROM koi_net_nodes WHERE LOWER(node_name) = LOWER($1) AND status = 'active'",
                    peer_name,
                )
            if not node_rid:
                if peer_name.startswith("orn:koi-net.node:"):
                    node_rid = peer_name
                else:
                    return {"error": f"Peer '{peer_name}' not found"}

            await conn.execute(
                """INSERT INTO vault_sync_peers (id, peer_node_rid, shared_folder, enabled)
                   VALUES (1, $1, $2, $3)
                   ON CONFLICT (id) DO UPDATE SET
                       peer_node_rid = EXCLUDED.peer_node_rid,
                       shared_folder = EXCLUDED.shared_folder,
                       enabled = EXCLUDED.enabled""",
                node_rid, shared_folder, enabled,
            )

            # Ensure shared folder exists
            folder_path = self.vault_path / shared_folder
            folder_path.mkdir(parents=True, exist_ok=True)

            return {
                "peer_node_rid": node_rid,
                "shared_folder": shared_folder,
                "enabled": enabled,
            }

    async def trigger_sync(self) -> Dict[str, Any]:
        """Force an immediate sync cycle."""
        self._last_scan_at = None  # Reset timer to force scan
        await self.run_cycle()
        return {"triggered": True, "last_scan_at": self._last_scan_at.isoformat() if self._last_scan_at else None}

    # ------------------------------------------------------------------
    # Internal: Local scan
    # ------------------------------------------------------------------

    async def _scan_async(self):
        """Async scan implementation."""
        peer = await self._get_peer_config()
        if not peer or not peer.get("enabled"):
            return

        shared_folder = peer["shared_folder"]
        peer_node_rid = peer["peer_node_rid"]
        base_dir = self.vault_path / shared_folder

        if not base_dir.exists():
            base_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"vault_sync: created shared folder {base_dir}")

        # Initial sync check
        is_initial = peer.get("last_full_sync_at") is None

        # Glob all .md files
        seen_paths = set()
        for md_file in base_dir.rglob("*.md"):
            if md_file.is_symlink():
                continue
            if not md_file.is_file():
                continue

            try:
                rel_path = f"{shared_folder}/{md_file.relative_to(base_dir)}"
            except ValueError:
                continue

            seen_paths.add(rel_path)

            # stat for mtime + size
            try:
                stat = md_file.stat()
            except OSError:
                continue

            mtime_ns = stat.st_mtime_ns
            size = stat.st_size

            # Size check
            if size > MAX_VAULT_FILE_BYTES:
                logger.warning(f"vault_sync: skipping oversize file {rel_path} ({size} bytes)")
                continue

            # Fast pre-check: skip if mtime+size unchanged
            cached = self._stat_cache.get(rel_path)
            if cached and cached[0] == mtime_ns and cached[1] == size:
                continue

            # Write debounce: wait and re-check before hashing
            await asyncio.sleep(WRITE_DEBOUNCE_MS / 1000)
            try:
                stat2 = md_file.stat()
            except OSError:
                continue
            if stat2.st_mtime_ns != mtime_ns:
                continue  # File still being written

            # Read and hash
            try:
                file_bytes = md_file.read_bytes()
            except OSError as e:
                logger.warning(f"vault_sync: cannot read {rel_path}: {e}")
                continue

            content_hash = hashlib.sha256(file_bytes).hexdigest()

            # Update stat cache
            self._stat_cache[rel_path] = (mtime_ns, size, content_hash)

            # Compare against DB
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM vault_sync_state WHERE relative_path=$1",
                    rel_path,
                )

                if not row:
                    # New file
                    await conn.execute(
                        """INSERT INTO vault_sync_state
                           (relative_path, content_hash, origin_node, origin_seq, file_size_bytes, last_modified_at)
                           VALUES ($1, $2, $3, 1, $4, $5)""",
                        rel_path, content_hash, self.node_rid, size,
                        datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                    )
                    await self._queue_event(
                        "NEW", rel_path, content_hash, None, 1,
                        file_bytes.decode("utf-8", errors="replace"),
                        size, peer_node_rid,
                    )
                elif row["is_deleted"]:
                    # File reappeared after deletion — treat as new
                    await conn.execute(
                        """UPDATE vault_sync_state
                           SET content_hash=$2, origin_node=$3, origin_seq=1,
                               file_size_bytes=$4, last_modified_at=$5,
                               is_deleted=FALSE, deleted_at=NULL, updated_at=NOW()
                           WHERE relative_path=$1""",
                        rel_path, content_hash, self.node_rid, size,
                        datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                    )
                    await self._queue_event(
                        "NEW", rel_path, content_hash, None, 1,
                        file_bytes.decode("utf-8", errors="replace"),
                        size, peer_node_rid,
                    )
                elif row["content_hash"] != content_hash:
                    # Modified file
                    prev_hash = row["content_hash"]
                    new_seq = (row["origin_seq"] + 1) if row["origin_node"] == self.node_rid else 1
                    await conn.execute(
                        """UPDATE vault_sync_state
                           SET content_hash=$2, origin_node=$3, origin_seq=$4,
                               file_size_bytes=$5, last_modified_at=$6, updated_at=NOW()
                           WHERE relative_path=$1""",
                        rel_path, content_hash, self.node_rid, new_seq, size,
                        datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                    )
                    await self._queue_event(
                        "UPDATE", rel_path, content_hash, prev_hash, new_seq,
                        file_bytes.decode("utf-8", errors="replace"),
                        size, peer_node_rid,
                    )

        # Check for deleted files
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT relative_path, content_hash, origin_seq FROM vault_sync_state WHERE is_deleted=FALSE"
            )
            for row in rows:
                if row["relative_path"] not in seen_paths:
                    rel_path = row["relative_path"]
                    # Remove from stat cache
                    self._stat_cache.pop(rel_path, None)
                    await conn.execute(
                        """UPDATE vault_sync_state
                           SET is_deleted=TRUE, deleted_at=NOW(), updated_at=NOW()
                           WHERE relative_path=$1""",
                        rel_path,
                    )
                    await self._queue_event(
                        "FORGET", rel_path, row["content_hash"], row["content_hash"],
                        row["origin_seq"], None, 0, peer_node_rid,
                    )

        # Mark initial sync complete
        if is_initial:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    "UPDATE vault_sync_peers SET last_full_sync_at=NOW() WHERE id=1"
                )

        # Periodic cleanup
        await self._cleanup_tombstones()

    # ------------------------------------------------------------------
    # Internal: Apply incoming events
    # ------------------------------------------------------------------

    async def _apply_new_or_update(
        self, target_path: Path, relative_path: str,
        local_row, event_type: str, markdown: str,
        content_hash: str, base_hash: Optional[str],
        origin_node: str, origin_seq: int,
        source_node: str, event_id: str, rid: str,
        file_size: int, timestamp: str,
    ):
        file_exists = target_path.exists() and not target_path.is_symlink()

        if event_type == "NEW" and file_exists:
            if local_row and local_row["content_hash"] == content_hash:
                # Idempotent — same content
                await self._record_applied(source_node, event_id, rid)
                return
            # Conflict: file exists with different content
            await self._create_conflict_copy(target_path, relative_path, markdown, content_hash,
                                             origin_node, origin_seq, source_node, event_id, rid, file_size, timestamp)
            return

        if event_type == "UPDATE":
            if not file_exists and (not local_row or local_row["is_deleted"]):
                # File was deleted locally — edit wins over delete, recreate
                pass  # Fall through to write
            elif file_exists and local_row:
                if not base_hash:
                    # No causal proof — treat as conflict
                    await self._create_conflict_copy(target_path, relative_path, markdown, content_hash,
                                                     origin_node, origin_seq, source_node, event_id, rid, file_size, timestamp)
                    return
                if local_row["content_hash"] == base_hash:
                    # Safe update — we haven't edited since peer's base
                    pass  # Fall through to write
                else:
                    # We also edited — conflict
                    await self._create_conflict_copy(target_path, relative_path, markdown, content_hash,
                                                     origin_node, origin_seq, source_node, event_id, rid, file_size, timestamp)
                    return

        # Write file atomically
        await self._atomic_write(target_path, markdown)

        # Update sync state
        now = datetime.now(timezone.utc)
        async with self.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO vault_sync_state
                   (relative_path, content_hash, origin_node, origin_seq, file_size_bytes,
                    last_synced_at, last_modified_at, is_deleted, deleted_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, FALSE, NULL)
                   ON CONFLICT (relative_path) DO UPDATE SET
                       content_hash = EXCLUDED.content_hash,
                       origin_node = EXCLUDED.origin_node,
                       origin_seq = EXCLUDED.origin_seq,
                       file_size_bytes = EXCLUDED.file_size_bytes,
                       last_synced_at = EXCLUDED.last_synced_at,
                       last_modified_at = EXCLUDED.last_modified_at,
                       is_deleted = FALSE,
                       deleted_at = NULL,
                       updated_at = NOW()""",
                relative_path, content_hash, origin_node, origin_seq, file_size, now, now,
            )

        # Update stat cache to prevent re-scanning the file we just wrote
        try:
            st = target_path.stat()
            self._stat_cache[relative_path] = (st.st_mtime_ns, st.st_size, content_hash)
        except OSError:
            pass

        await self._record_applied(source_node, event_id, rid)
        logger.info(f"vault_sync: applied {event_type} for {relative_path} from {source_node}")

    async def _apply_forget(
        self, target_path: Path, relative_path: str,
        local_row, base_hash: Optional[str],
        origin_node: str, origin_seq: int,
        source_node: str, event_id: str, rid: str,
    ):
        if not local_row or local_row["is_deleted"]:
            # Already deleted — no-op
            await self._record_applied(source_node, event_id, rid)
            return

        if not base_hash:
            # No causal proof — refuse destructive action
            logger.info(f"vault_sync: ignoring FORGET without base_hash for {relative_path}")
            await self._record_applied(source_node, event_id, rid)
            return

        if local_row["content_hash"] != base_hash:
            # We edited after peer's base — stale delete, ignore
            logger.info(f"vault_sync: ignoring stale FORGET for {relative_path} (local edited)")
            await self._record_applied(source_node, event_id, rid)
            return

        # Safe to delete
        try:
            if target_path.exists():
                target_path.unlink()
        except OSError as e:
            logger.error(f"vault_sync: cannot delete {target_path}: {e}")

        # Remove from stat cache
        self._stat_cache.pop(relative_path, None)

        async with self.pool.acquire() as conn:
            await conn.execute(
                """UPDATE vault_sync_state
                   SET is_deleted=TRUE, deleted_at=NOW(), updated_at=NOW()
                   WHERE relative_path=$1""",
                relative_path,
            )

        await self._record_applied(source_node, event_id, rid)
        logger.info(f"vault_sync: deleted {relative_path} (FORGET from {source_node})")

    async def _create_conflict_copy(
        self, target_path: Path, relative_path: str,
        markdown: str, content_hash: str,
        origin_node: str, origin_seq: int,
        source_node: str, event_id: str, rid: str,
        file_size: int, timestamp: str,
    ):
        """Create a conflict copy of the incoming file."""
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y-%m-%d %H-%M-%S")
        stem = target_path.stem
        suffix = target_path.suffix
        conflict_name = f"{stem} (conflict {ts}){suffix}"
        conflict_path = target_path.parent / conflict_name

        # Derive conflict relative path
        parts = relative_path.rsplit("/", 1)
        if len(parts) == 2:
            conflict_rel = f"{parts[0]}/{conflict_name}"
        else:
            conflict_rel = conflict_name

        await self._atomic_write(conflict_path, markdown)

        # Insert sync state for the conflict copy
        async with self.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO vault_sync_state
                   (relative_path, content_hash, origin_node, origin_seq, file_size_bytes,
                    last_synced_at, last_modified_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)
                   ON CONFLICT (relative_path) DO UPDATE SET
                       content_hash = EXCLUDED.content_hash,
                       origin_node = EXCLUDED.origin_node,
                       origin_seq = EXCLUDED.origin_seq,
                       file_size_bytes = EXCLUDED.file_size_bytes,
                       last_synced_at = EXCLUDED.last_synced_at,
                       last_modified_at = EXCLUDED.last_modified_at,
                       updated_at = NOW()""",
                conflict_rel, content_hash, origin_node, origin_seq,
                file_size, now, now,
            )

        await self._record_applied(source_node, event_id, rid)
        logger.warning(f"vault_sync: conflict copy created: {conflict_rel}")

    # ------------------------------------------------------------------
    # Internal: Reconciliation
    # ------------------------------------------------------------------

    async def _reconcile(self):
        """Send manifest to peer for drift detection."""
        peer = await self._get_peer_config()
        if not peer or not peer.get("enabled"):
            return

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT relative_path, content_hash, origin_node, origin_seq
                   FROM vault_sync_state WHERE is_deleted=FALSE"""
            )

        manifest_entries = [
            {
                "relative_path": r["relative_path"],
                "content_hash": r["content_hash"],
                "origin_node": r["origin_node"],
                "origin_seq": r["origin_seq"],
            }
            for r in rows
        ]

        # Queue reconcile event
        now = datetime.now(timezone.utc).isoformat()
        await self.event_queue.add(
            event_type="UPDATE",
            # Keep reconcile events under the vault-file RID type so edge rid_types
            # filters allow delivery without requiring an extra capability type.
            rid=f"orn:koi-net.vault-file:reconcile/{self.node_rid}",
            manifest={"type": "reconcile", "timestamp": now, "entry_count": len(manifest_entries)},
            contents={"_vault_sync": True, "_reconcile": True, "entries": manifest_entries},
            ttl_hours=24,
            target_node=peer["peer_node_rid"],
        )
        logger.info(f"vault_sync: sent reconcile manifest ({len(manifest_entries)} entries)")

    async def apply_reconcile(self, contents: Dict[str, Any], source_node: str):
        """Process a reconcile manifest from a peer."""
        entries = contents.get("entries", [])
        peer = await self._get_peer_config()
        if not peer:
            return

        shared_folder = peer["shared_folder"]
        base_dir = self.vault_path / shared_folder

        for entry in entries:
            rel_path = entry.get("relative_path")
            remote_hash = entry.get("content_hash")
            if not rel_path or not remote_hash:
                continue

            async with self.pool.acquire() as conn:
                local_row = await conn.fetchrow(
                    "SELECT * FROM vault_sync_state WHERE relative_path=$1",
                    rel_path,
                )

            if not local_row:
                # Peer has file we don't — it will arrive as a NEW event from normal scan
                logger.debug(f"vault_sync: reconcile: peer has {rel_path}, we don't")
            elif local_row["is_deleted"]:
                logger.debug(f"vault_sync: reconcile: peer has {rel_path}, we deleted it")
            elif local_row["content_hash"] != remote_hash:
                logger.info(f"vault_sync: reconcile: hash mismatch for {rel_path}")
                # Mismatch will be resolved by normal scan/event cycle

    # ------------------------------------------------------------------
    # Internal: Helpers
    # ------------------------------------------------------------------

    async def _get_peer_config(self) -> Optional[Dict[str, Any]]:
        """Get the singleton peer config."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM vault_sync_peers WHERE id=1")
            if not row:
                return None
            return dict(row)

    async def _queue_event(
        self, event_type: str, relative_path: str,
        content_hash: str, base_hash: Optional[str],
        origin_seq: int, markdown: Optional[str],
        file_size: int, peer_node_rid: str,
    ):
        """Queue a vault-sync event for delivery."""
        now = datetime.now(timezone.utc).isoformat()
        rid = f"orn:koi-net.vault-file:{relative_path}"

        contents = {
            "relative_path": relative_path,
            "_vault_sync": True,
        }
        if markdown is not None:
            contents["markdown"] = markdown

        manifest = {
            "relative_path": relative_path,
            "content_hash": content_hash,
            "base_hash": base_hash,
            "origin_node": self.node_rid,
            "origin_seq": origin_seq,
            "bytes": file_size,
            "deleted": event_type == "FORGET",
            "timestamp": now,
        }

        await self.event_queue.add(
            event_type=event_type,
            rid=rid,
            manifest=manifest,
            contents=contents,
            ttl_hours=168,  # 7 days
            target_node=peer_node_rid,
        )
        logger.info(f"vault_sync: queued {event_type} for {relative_path}")

    async def _atomic_write(self, path: Path, content: str):
        """Write file atomically via tmp + rename."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.parent / f"{path.name}.tmp.{uuid.uuid4().hex[:8]}"
        try:
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.rename(path)
        except Exception:
            # Clean up tmp on failure
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    async def _record_applied(self, source_node: str, event_id: str, rid: str):
        """Record that we applied an event (for idempotency)."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO vault_sync_applied_events (source_node, event_id, rid)
                   VALUES ($1, $2::UUID, $3)
                   ON CONFLICT (source_node, event_id, rid) DO NOTHING""",
                source_node, event_id, rid,
            )

    async def _cleanup_tombstones(self):
        """Purge old tombstones and dedup entries."""
        async with self.pool.acquire() as conn:
            deleted = await conn.execute(
                f"DELETE FROM vault_sync_state WHERE is_deleted=TRUE AND deleted_at < NOW() - INTERVAL '{TOMBSTONE_CLEANUP_DAYS} days'"
            )
            purged = await conn.execute(
                f"DELETE FROM vault_sync_applied_events WHERE applied_at < NOW() - INTERVAL '{DEDUP_CLEANUP_DAYS} days'"
            )

    def _reject(self, reason: str, rid: str, source_node: str, event_id: Optional[str], detail: str):
        """Log and count a rejected event."""
        self._rejected_counts[reason] = self._rejected_counts.get(reason, 0) + 1
        logger.warning(
            f"vault_sync: rejected event reason={reason} rid={rid} detail={detail}",
        )
