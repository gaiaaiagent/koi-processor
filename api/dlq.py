"""Dead-letter queue for KOI events that exhausted retry budget or TTL.

Moves rows from koi_net_events -> koi_net_events_dead with a reason tag.
Replay returns a row to the live queue with reset attempts.

Schema notes:
- koi_net_events has manifest + contents (jsonb), source_node TEXT (NOT a single payload column).
- koi_net_events_dead has payload (jsonb NOT NULL) + manifest (jsonb) + source_node (TEXT).
- On move: payload = contents (fallback manifest, then '{}'); manifest + source_node preserved
  in their own columns (post-migration 082) for lossless replay.
- On replay: contents = payload (back to live), manifest + source_node restored.
"""

from __future__ import annotations

import json
import logging
from typing import Optional, Any, Dict, List

import asyncpg

from api.event_queue import DEFAULT_TTL_HOURS

logger = logging.getLogger(__name__)

VALID_REASONS = {"max_attempts", "ttl_expired", "manual"}


def _coerce_payload(row: Any) -> str:
    """Pick best jsonb field from a live event row and return JSON string."""
    contents = row["contents"]
    manifest = row["manifest"] if "manifest" in row.keys() else None
    chosen = contents if contents is not None else manifest
    if chosen is None:
        return "{}"
    # asyncpg returns jsonb as str (when no codec set) or already-parsed dict.
    if isinstance(chosen, (dict, list)):
        return json.dumps(chosen)
    return chosen


def _coerce_jsonb(val: Any) -> Optional[str]:
    """Serialize a jsonb-ish value to a JSON string, or None."""
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return json.dumps(val)
    return val


class DeadLetterQueue:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def move_event(self, event_id: int, *, reason: str) -> None:
        if reason not in VALID_REASONS:
            raise ValueError(f"invalid dlq_reason: {reason}")
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "DELETE FROM koi_net_events WHERE id = $1 RETURNING *", event_id
            )
            if row is None:
                logger.warning("move_event: event %s not found", event_id)
                return
            payload_json = _coerce_payload(row)
            manifest_json = _coerce_jsonb(row["manifest"] if "manifest" in row.keys() else None)
            source_node = row["source_node"] if "source_node" in row.keys() else None
            await conn.execute(
                "INSERT INTO koi_net_events_dead "
                "(original_event_id, rid, event_type, target_node, payload, "
                " delivery_attempts, original_expires_at, dlq_reason, "
                " manifest, source_node) "
                "VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9::jsonb,$10)",
                row["id"], row["rid"], row["event_type"], row["target_node"],
                payload_json, row["delivery_attempts"], row["expires_at"], reason,
                manifest_json, source_node,
            )
            logger.info("moved event %s to DLQ (reason=%s)", event_id, reason)

    async def sweep_expired(self) -> int:
        """Move all TTL-expired undelivered events to DLQ. Returns count moved.

        Definition of 'undelivered': delivered_to is NULL or an empty array.
        koi_net_events.delivered_to is text[].
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id FROM koi_net_events "
                "WHERE expires_at < NOW() "
                "  AND (delivered_to IS NULL OR cardinality(delivered_to) = 0)"
            )
        count = 0
        for r in rows:
            await self.move_event(r["id"], reason="ttl_expired")
            count += 1
        return count

    async def fetch_by_original_id(self, original_event_id: int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM koi_net_events_dead WHERE original_event_id = $1 "
                "ORDER BY moved_at DESC LIMIT 1",
                original_event_id,
            )
            return dict(row) if row else None

    async def list_by_peer(self, target_node: str, *, limit: int = 100) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM koi_net_events_dead "
                "WHERE target_node = $1 ORDER BY moved_at DESC LIMIT $2",
                target_node, limit,
            )
            return [dict(r) for r in rows]

    async def replay(self, dlq_id: int) -> int:
        """Move event from DLQ back to live queue with reset attempts. Returns new event id."""
        async with self.pool.acquire() as conn, conn.transaction():
            dead = await conn.fetchrow(
                "DELETE FROM koi_net_events_dead WHERE id = $1 RETURNING *", dlq_id
            )
            if dead is None:
                raise ValueError(f"DLQ row {dlq_id} not found")
            payload = dead["payload"]
            if isinstance(payload, (dict, list)):
                payload_json = json.dumps(payload)
            elif payload is None:
                payload_json = "{}"
            else:
                payload_json = payload
            manifest_json = _coerce_jsonb(dead["manifest"] if "manifest" in dead.keys() else None)
            source_node = dead["source_node"] if "source_node" in dead.keys() else None
            new = await conn.fetchrow(
                "INSERT INTO koi_net_events"
                "(rid, event_type, manifest, contents, target_node, source_node, "
                " expires_at, delivery_attempts) "
                "VALUES ($1,$2,$3::jsonb,$4::jsonb,$5,$6,"
                " NOW() + ($7 || ' hours')::interval,0) RETURNING id",
                dead["rid"], dead["event_type"], manifest_json, payload_json,
                dead["target_node"], source_node, str(DEFAULT_TTL_HOURS),
            )
            logger.info("replayed DLQ %s -> event %s", dlq_id, new["id"])
            return new["id"]
