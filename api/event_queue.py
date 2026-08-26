"""
Database-backed KOI-net Event Queue

Uses the koi_net_events table (migration 039) for event persistence.
Supports add, poll, peek, mark_delivered, confirm, and cleanup operations with per-edge TTL.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg

from api.koi_protocol import EventType, WireEvent, WireManifest, timestamp_to_z_format

logger = logging.getLogger(__name__)

# Default TTL for events (hours)
DEFAULT_TTL_HOURS = 24
REMOTE_TTL_HOURS = 72


class EventQueue:
    """Database-backed event queue for KOI-net protocol."""

    def __init__(self, pool: asyncpg.Pool, node_rid: str):
        self.pool = pool
        self.node_rid = node_rid

    async def add(
        self,
        event_type: str,
        rid: str,
        manifest: Optional[Dict[str, Any]] = None,
        contents: Optional[Dict[str, Any]] = None,
        source_node: Optional[str] = None,
        ttl_hours: int = DEFAULT_TTL_HOURS,
        event_id: Optional[str] = None,
        target_node: Optional[str] = None,
    ) -> Optional[str]:
        """Add an event to the queue. Returns the event_id.

        If event_id is provided (inbound from a peer), it is preserved and
        used for dedup via the UNIQUE(source_node, event_id, target_node) index.
        Returns None if the event was a duplicate (ON CONFLICT DO NOTHING).

        target_node: If set, only this node can receive the event (unicast).
                     If None, event is available to all polling nodes (broadcast).
        """
        effective_source = source_node or self.node_rid
        async with self.pool.acquire() as conn:
            if event_id:
                # Inbound event with sender-assigned event_id — dedup on insert
                row = await conn.fetchrow(
                    """
                    INSERT INTO koi_net_events
                        (event_id, event_type, rid, manifest, contents, source_node, target_node, expires_at)
                    VALUES
                        ($1::UUID, $2, $3, $4, $5, $6, $7, NOW() + ($8 || ' hours')::INTERVAL)
                    ON CONFLICT (source_node, event_id, COALESCE(target_node, ''))
                        WHERE event_id IS NOT NULL DO NOTHING
                    RETURNING event_id::TEXT
                    """,
                    event_id,
                    event_type,
                    rid,
                    json.dumps(manifest) if manifest else None,
                    json.dumps(contents) if contents else None,
                    effective_source,
                    target_node,
                    str(ttl_hours),
                )
                if row is None:
                    logger.debug(f"Duplicate event {event_id} from {effective_source}, skipped")
                    return None
                logger.info(f"Queued {event_type} event for {rid} (id={event_id}, target={target_node})")
                return event_id
            else:
                # Locally generated event — DB assigns event_id
                row = await conn.fetchrow(
                    """
                    INSERT INTO koi_net_events
                        (event_type, rid, manifest, contents, source_node, target_node, expires_at)
                    VALUES
                        ($1, $2, $3, $4, $5, $6, NOW() + ($7 || ' hours')::INTERVAL)
                    RETURNING event_id::TEXT
                    """,
                    event_type,
                    rid,
                    json.dumps(manifest) if manifest else None,
                    json.dumps(contents) if contents else None,
                    effective_source,
                    target_node,
                    str(ttl_hours),
                )
                new_id = row["event_id"]
                logger.info(f"Queued {event_type} event for {rid} (id={new_id}, target={target_node})")
                return new_id

    async def poll(
        self,
        requesting_node: str,
        limit: int = 50,
        rid_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Poll for events not yet delivered to the requesting node.

        Returns list of event dicts with event_id, event_type, rid, manifest, contents.
        Marks events as delivered_to this node.
        """
        async with self.pool.acquire() as conn:
            # Fetch events not yet delivered to this node and not expired.
            # target_node scoping: NULL = broadcast (visible to all), non-NULL = unicast.
            rows = await conn.fetch(
                """
                SELECT id, event_id::TEXT, event_type, rid, manifest, contents, source_node, queued_at
                FROM koi_net_events
                WHERE NOT ($1 = ANY(delivered_to))
                  AND expires_at > NOW()
                  AND (target_node IS NULL OR target_node = $1)
                ORDER BY queued_at ASC
                LIMIT $2
                """,
                requesting_node,
                limit,
            )

            if not rows:
                return []

            events = []
            ids_to_mark = []

            for row in rows:
                # If rid_types filter specified, gate BOTH rid-typed events and
                # domain events against the edge's declared types.
                #
                # Domain events used to bypass this filter entirely, on the
                # stated assumption that they "carry their own routing". They do
                # not: every domain event is queued with target_node NULL, i.e.
                # broadcast. The bypass therefore delivered the full
                # entity/task/intent/knowledge stream to every peer holding an
                # approved POLL edge, regardless of what that edge declared.
                # The domain name is now matched as a pseudo rid-type, so an
                # edge must opt in by listing it (e.g. "task", "entity").
                if rid_types:
                    contents_raw = row["contents"]
                    is_domain_event = False
                    domain = None
                    if contents_raw:
                        c = json.loads(contents_raw) if isinstance(contents_raw, str) else contents_raw
                        if isinstance(c, dict) and "_koi_domain" in c:
                            is_domain_event = True
                            domain = c.get("_koi_domain")
                    lowered = [rt.lower() for rt in rid_types]
                    excluded = False
                    if is_domain_event:
                        # Fail closed: a malformed domain event carrying no
                        # domain name cannot be matched against the edge, so it
                        # is not delivered.
                        excluded = not domain or str(domain).lower() not in lowered
                    else:
                        # RID format: orn:koi-net.{type}:{slug}+{hash}
                        rid_type = extract_rid_type(row["rid"])
                        excluded = bool(rid_type) and rid_type.lower() not in lowered

                    if excluded:
                        # Mark excluded events delivered rather than skipping
                        # them. A filter decision is PERMANENT for this peer,
                        # not a deferral — if we leave them unmarked they stay
                        # at the head of the peer's queue (ORDER BY queued_at
                        # ASC LIMIT n) and every subsequent poll re-reads and
                        # re-drops the same rows, returning an empty list
                        # forever. That starves the peer of everything behind
                        # them until the 24h TTL lapses.
                        ids_to_mark.append(row["id"])
                        continue

                event = {
                    "event_id": row["event_id"],
                    "event_type": row["event_type"],
                    "rid": row["rid"],
                    "manifest": json.loads(row["manifest"]) if row["manifest"] else None,
                    "contents": json.loads(row["contents"]) if row["contents"] else None,
                    "source_node": row["source_node"],
                    "queued_at": row["queued_at"].isoformat() if row["queued_at"] else None,
                }
                events.append(event)
                ids_to_mark.append(row["id"])

            # Mark as delivered to this node
            if ids_to_mark:
                await conn.execute(
                    """
                    UPDATE koi_net_events
                    SET delivered_to = array_append(delivered_to, $1)
                    WHERE id = ANY($2)
                    """,
                    requesting_node,
                    ids_to_mark,
                )
                logger.info(
                    f"Delivered {len(events)} events to {requesting_node}"
                )

            return events

    async def peek_undelivered(
        self,
        target_node: str,
        limit: int = 50,
        rid_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Get events not yet delivered to target_node WITHOUT marking them.

        Returns list of event dicts with event_id for later marking.
        Used by WEBHOOK push delivery (peek -> push -> mark pattern).
        """
        async with self.pool.acquire() as conn:
            # Same target_node scoping as poll(): NULL = broadcast, non-NULL = unicast.
            rows = await conn.fetch(
                """
                SELECT id, event_id::TEXT, event_type, rid, manifest, contents, source_node, queued_at
                FROM koi_net_events
                WHERE NOT ($1 = ANY(delivered_to))
                  AND expires_at > NOW()
                  AND (target_node IS NULL OR target_node = $1)
                ORDER BY queued_at ASC
                LIMIT $2
                """,
                target_node,
                limit,
            )

            if not rows:
                return []

            events = []
            for row in rows:
                # Same scope rule as poll(): a domain event is matched on its
                # _koi_domain value rather than bypassing the edge filter. The
                # old bypass handed the full entity/task/intent/knowledge stream
                # to any peer holding an approved edge regardless of what that
                # edge declared, because domain events are queued with
                # target_node NULL (broadcast).
                #
                # NOTE: poll() additionally marks excluded events delivered, so a
                # narrow edge cannot starve behind a filtered queue head. peek()
                # is deliberately non-marking (peek -> push -> mark), so that half
                # belongs to the CALLER: WEBHOOK delivery must mark excluded ids
                # too. Zero WEBHOOK edges exist on either node (verified 0/0) —
                # do not approve one until that caller-side marking exists.
                if rid_types:
                    contents_raw = row["contents"]
                    is_domain_event = False
                    domain = None
                    if contents_raw:
                        c = json.loads(contents_raw) if isinstance(contents_raw, str) else contents_raw
                        if isinstance(c, dict) and "_koi_domain" in c:
                            is_domain_event = True
                            domain = c.get("_koi_domain")
                    lowered = [rt.lower() for rt in rid_types]
                    if is_domain_event:
                        if not domain or str(domain).lower() not in lowered:
                            continue
                    else:
                        rid_type = extract_rid_type(row["rid"])
                        if rid_type and rid_type.lower() not in lowered:
                            continue

                events.append({
                    "event_id": row["event_id"],
                    "event_type": row["event_type"],
                    "rid": row["rid"],
                    "manifest": json.loads(row["manifest"]) if row["manifest"] else None,
                    "contents": json.loads(row["contents"]) if row["contents"] else None,
                    "source_node": row["source_node"],
                    "queued_at": row["queued_at"].isoformat() if row["queued_at"] else None,
                })

            return events

    async def mark_delivered(self, event_ids: List[str], target_node: str) -> int:
        """Mark specific events as delivered to target_node.

        Returns count of events actually marked (for verification).
        Idempotent — marking an already-delivered event is a no-op.
        """
        if not event_ids:
            return 0

        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE koi_net_events
                SET delivered_to = array_append(delivered_to, $1)
                WHERE event_id::TEXT = ANY($2)
                  AND NOT ($1 = ANY(delivered_to))
                  AND expires_at > NOW()
                """,
                target_node,
                event_ids,
            )
            count = int(result.split()[-1])
            if count > 0:
                logger.info(f"Marked {count} events as delivered to {target_node}")
            return count

    async def confirm(
        self,
        event_ids: List[str],
        confirming_node: str,
    ) -> int:
        """Confirm receipt of events by a node. Returns count confirmed."""
        if not event_ids:
            return 0

        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE koi_net_events
                SET confirmed_by = array_append(confirmed_by, $1)
                WHERE event_id::TEXT = ANY($2)
                  AND NOT ($1 = ANY(confirmed_by))
                """,
                confirming_node,
                event_ids,
            )
            count = int(result.split()[-1])
            logger.info(f"Confirmed {count} events from {confirming_node}")
            return count

    async def cleanup(self) -> int:
        """Delete expired events. Returns count deleted."""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM koi_net_events
                WHERE expires_at < NOW()
                """
            )
            count = int(result.split()[-1])
            if count > 0:
                logger.info(f"Cleaned up {count} expired events")
            return count

    async def get_queue_size(self) -> int:
        """Get current number of active (non-expired) events."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) as cnt FROM koi_net_events WHERE expires_at > NOW()"
            )
            return row["cnt"]


def extract_rid_type(rid: str) -> Optional[str]:
    """Extract entity type from RID string.

    Expected formats:
    - orn:koi-net.practice:slug+hash -> Practice
    - orn:entity:practice/slug+hash -> Practice
    """
    if "koi-net." in rid:
        # orn:koi-net.{type}:{slug}+{hash}
        try:
            type_part = rid.split("koi-net.")[1].split(":")[0]
            return type_part.capitalize()
        except (IndexError, AttributeError):
            return None
    if "entity:" in rid:
        # orn:entity:{type}/{slug}+{hash}
        try:
            type_part = rid.split("entity:")[1].split("/")[0]
            return type_part.capitalize()
        except (IndexError, AttributeError):
            return None
    return None
