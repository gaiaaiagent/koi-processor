"""Persisted exponential backoff per peer.

Replaces KoiPoller._backoff in-memory dict. Survives restart.
Table: koi_net_nodes (the canonical peers table — note: spec referenced
koi_net_peers which does not exist).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)

BASE_BACKOFF_SECONDS = 30
MAX_DOUBLINGS = 5  # cap at 30 * 2^5 = 16 min


class PeerBackoff:
    """Persisted backoff state for KOI peers, stored in koi_net_nodes."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    @staticmethod
    def _compute_seconds(streak: int) -> int:
        return BASE_BACKOFF_SECONDS * (2 ** min(streak - 1, MAX_DOUBLINGS))

    async def record_failure(self, node_rid: str) -> None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE koi_net_nodes "
                "SET backoff_streak = COALESCE(backoff_streak, 0) + 1 "
                "WHERE node_rid = $1 "
                "RETURNING backoff_streak",
                node_rid,
            )
            if row is None:
                logger.warning("record_failure: peer %s not in koi_net_nodes", node_rid)
                return
            streak = row["backoff_streak"]
            seconds = self._compute_seconds(streak)
            until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
            await conn.execute(
                "UPDATE koi_net_nodes SET backoff_until = $1 WHERE node_rid = $2",
                until,
                node_rid,
            )
            logger.info(
                "peer %s backoff streak=%d, retry_at=%s",
                node_rid, streak, until.isoformat(),
            )

    async def record_success(self, node_rid: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE koi_net_nodes "
                "SET backoff_streak = 0, backoff_until = NULL, last_success_at = NOW() "
                "WHERE node_rid = $1",
                node_rid,
            )

    async def get_streak(self, node_rid: str) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT backoff_streak FROM koi_net_nodes WHERE node_rid = $1",
                node_rid,
            )
            return row["backoff_streak"] if row else 0

    async def get_backoff_until(self, node_rid: str) -> Optional[datetime]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT backoff_until FROM koi_net_nodes WHERE node_rid = $1",
                node_rid,
            )
            return row["backoff_until"] if row else None

    async def should_skip(self, node_rid: str) -> bool:
        until = await self.get_backoff_until(node_rid)
        if until is None:
            return False
        return until > datetime.now(timezone.utc)
