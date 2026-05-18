"""Startup sync sweep.

On service start, reconciles undelivered events for each active peer
with a single bounded poll. Idempotent. Skips peers currently in backoff.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import asyncpg
import httpx

from api.peer_backoff import PeerBackoff

logger = logging.getLogger(__name__)

SWEEP_MAX_EVENTS_PER_PEER = 500
SWEEP_HTTP_TIMEOUT_SECONDS = 30


class StartupSweep:
    def __init__(self, pool: asyncpg.Pool, node_rid: str) -> None:
        self.pool = pool
        self.node_rid = node_rid
        self.backoff = PeerBackoff(pool)

    async def run(self) -> int:
        """Sweep all active peers. Returns count of peers swept."""
        async with self.pool.acquire() as conn:
            peers = await conn.fetch(
                "SELECT node_rid, base_url FROM koi_net_nodes "
                "WHERE base_url IS NOT NULL"
            )
        swept = 0
        for peer in peers:
            if await self.backoff.should_skip(peer["node_rid"]):
                logger.info("sweep: skipping %s (in backoff)", peer["node_rid"])
                continue
            await self._poll_peer(peer["node_rid"])
            swept += 1
        logger.info("startup sweep complete: %d peers swept", swept)
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO koi_net_runtime_state(key, value, updated_at) "
                "VALUES ('last_sweep_at', $1::text, NOW()) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
                datetime.now(timezone.utc).isoformat(),
            )
        return swept

    async def _poll_peer(self, peer_rid: str) -> None:
        async with self.pool.acquire() as conn:
            peer = await conn.fetchrow(
                "SELECT base_url FROM koi_net_nodes WHERE node_rid = $1", peer_rid
            )
        if peer is None or peer["base_url"] is None:
            return
        url = f"{peer['base_url'].rstrip('/')}/events/poll"
        try:
            async with httpx.AsyncClient(timeout=SWEEP_HTTP_TIMEOUT_SECONDS) as client:
                resp = await client.post(url, json={"limit": SWEEP_MAX_EVENTS_PER_PEER})
                resp.raise_for_status()
            await self.backoff.record_success(peer_rid)
            logger.info("sweep: polled %s OK", peer_rid)
        except Exception as exc:
            await self.backoff.record_failure(peer_rid)
            logger.warning("sweep: poll %s failed: %s", peer_rid, exc)
