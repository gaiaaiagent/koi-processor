"""DLQ replay CLI — return events from koi_net_events_dead to live queue.

Usage:
    python -m api.dlq_replay --peer <rid> [--limit N]
    python -m api.dlq_replay --all [--limit N]
    python -m api.dlq_replay --id <dlq_id>
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

import asyncpg

from api.dlq import DeadLetterQueue

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dlq_replay")


async def replay_by_peer(pool: asyncpg.Pool, peer: str, limit: int) -> int:
    dlq = DeadLetterQueue(pool)
    rows = await dlq.list_by_peer(peer, limit=limit)
    for row in rows:
        await dlq.replay(row["id"])
    return len(rows)


async def replay_all(pool: asyncpg.Pool, limit: int) -> int:
    dlq = DeadLetterQueue(pool)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM koi_net_events_dead ORDER BY moved_at ASC LIMIT $1", limit
        )
    for row in rows:
        await dlq.replay(row["id"])
    return len(rows)


async def main_async(args: argparse.Namespace) -> int:
    dsn = os.getenv("POSTGRES_URL", "postgresql:///personal_koi")
    pool = await asyncpg.create_pool(dsn)
    try:
        if args.id is not None:
            dlq = DeadLetterQueue(pool)
            new = await dlq.replay(args.id)
            print(f"replayed DLQ {args.id} -> event {new}")
            return 0
        if args.peer:
            count = await replay_by_peer(pool, args.peer, args.limit)
            print(f"replayed {count} events for peer {args.peer}")
            return 0
        if args.all:
            count = await replay_all(pool, args.limit)
            print(f"replayed {count} events (oldest first)")
            return 0
        print("must specify --id, --peer, or --all", file=sys.stderr)
        return 1
    finally:
        await pool.close()


def main() -> int:
    p = argparse.ArgumentParser(description="DLQ replay")
    p.add_argument("--peer", help="target peer node_rid")
    p.add_argument("--id", type=int, help="single DLQ row id")
    p.add_argument("--all", action="store_true", help="replay all DLQ entries")
    p.add_argument("--limit", type=int, default=100, help="max events to replay")
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
