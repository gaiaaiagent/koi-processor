"""
TerminusDB outbox worker — drains terminusdb_outbox rows to TerminusDB.

Runs as a standalone process or as an asyncio background task.

Usage:
    python -m scripts.terminusdb.outbox_worker

Environment:
    POSTGRES_URL          PostgreSQL connection string
    TERMINUSDB_URL        TerminusDB server URL (default: http://127.0.0.1:6363/)
    TERMINUSDB_DB         Database name (default: koi_knowledge_graph)
    TERMINUSDB_TEAM       Team (default: admin)
    TERMINUSDB_KEY        API key
    OUTBOX_POLL_INTERVAL  Seconds between poll cycles (default: 2)
    OUTBOX_BATCH_SIZE     Rows to claim per cycle (default: 10)
    OUTBOX_MAX_ATTEMPTS   Max retries before dead_letter (default: 5)
    OUTBOX_LEASE_TIMEOUT  Seconds before stale lease reclaim (default: 300)
"""

import asyncio
import json
import logging
import os
import platform
import sys
from pathlib import Path

import asyncpg

# Add project root for imports
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from api.terminusdb_adapter import TerminusDBAdapter

logger = logging.getLogger(__name__)

# Configuration
POLL_INTERVAL = float(os.getenv("OUTBOX_POLL_INTERVAL", "2"))
BATCH_SIZE = int(os.getenv("OUTBOX_BATCH_SIZE", "10"))
MAX_ATTEMPTS = int(os.getenv("OUTBOX_MAX_ATTEMPTS", "5"))
LEASE_TIMEOUT_S = int(os.getenv("OUTBOX_LEASE_TIMEOUT", "300"))

WORKER_ID = f"{platform.node()}:{os.getpid()}"


def _load_env():
    """Load config/personal.env if needed."""
    env_path = PROJECT_ROOT / "config" / "personal.env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())


def _get_adapter() -> TerminusDBAdapter:
    return TerminusDBAdapter(
        url=os.getenv("TERMINUSDB_URL", "http://127.0.0.1:6363/"),
        db_name=os.getenv("TERMINUSDB_DB", "koi_knowledge_graph"),
        team=os.getenv("TERMINUSDB_TEAM", "admin"),
        key=os.getenv("TERMINUSDB_KEY", "root"),
    )


# --------------------------------------------------------------------------
# SQL
# --------------------------------------------------------------------------

CLAIM_SQL = """
UPDATE terminusdb_outbox
SET status = 'processing', claimed_at = NOW(), claimed_by = $1
WHERE id IN (
    SELECT id FROM terminusdb_outbox
    WHERE status = 'pending' AND next_attempt_at <= NOW()
    ORDER BY id LIMIT $2
    FOR UPDATE SKIP LOCKED
)
RETURNING id, operation, payload, rid, source_rid, attempts;
"""

RECLAIM_STALE_SQL = """
UPDATE terminusdb_outbox
SET status = 'pending', claimed_at = NULL, claimed_by = NULL
WHERE status = 'processing'
  AND claimed_at < NOW() - ($1 || ' seconds')::INTERVAL;
"""

MARK_APPLIED_SQL = """
UPDATE terminusdb_outbox
SET status = 'applied', applied_at = NOW(), claimed_at = NULL, claimed_by = NULL
WHERE id = $1;
"""

MARK_RETRY_SQL = """
UPDATE terminusdb_outbox
SET status = 'pending',
    attempts = attempts + 1,
    error = $1,
    next_attempt_at = NOW() + (INTERVAL '2 seconds' * POWER(2, attempts))
                            * (0.7 + random() * 0.6),
    claimed_at = NULL,
    claimed_by = NULL
WHERE id = $2;
"""

MARK_DEAD_LETTER_SQL = """
UPDATE terminusdb_outbox
SET status = 'dead_letter', error = $1, claimed_at = NULL, claimed_by = NULL
WHERE id = $2;
"""


# --------------------------------------------------------------------------
# Processing
# --------------------------------------------------------------------------

def _apply_operation(adapter: TerminusDBAdapter, operation: str,
                     payload: dict, rid: str, source_rid: str) -> None:
    """Apply a single outbox operation to TerminusDB. Raises on failure."""
    if operation == "entity_upsert":
        adapter.upsert_entity(payload)
    elif operation == "assertion_upsert":
        adapter.upsert_assertion(payload)
    elif operation == "assertion_retract":
        adapter.retract_assertions_by_source(source_rid)
    else:
        raise ValueError(f"Unknown outbox operation: {operation}")


async def poll_cycle(pool: asyncpg.Pool, adapter: TerminusDBAdapter) -> int:
    """Run one poll cycle. Returns number of rows processed."""
    async with pool.acquire() as conn:
        # Reclaim stale leases
        await conn.execute(RECLAIM_STALE_SQL, str(LEASE_TIMEOUT_S))

        # Claim batch
        rows = await conn.fetch(CLAIM_SQL, WORKER_ID, BATCH_SIZE)

    if not rows:
        return 0

    processed = 0
    for row in rows:
        row_id = row["id"]
        operation = row["operation"]
        raw_payload = row["payload"]
        # asyncpg returns JSONB as dict, but handle string case defensively
        payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        rid = row["rid"] or ""
        source_rid = row["source_rid"] or ""
        attempts = row["attempts"]

        try:
            _apply_operation(adapter, operation, payload, rid, source_rid)

            async with pool.acquire() as conn:
                await conn.execute(MARK_APPLIED_SQL, row_id)
            processed += 1
            logger.debug(f"Applied outbox row {row_id}: {operation} rid={rid[:40]}")

        except Exception as e:
            error_msg = str(e)[:500]
            async with pool.acquire() as conn:
                if attempts + 1 >= MAX_ATTEMPTS:
                    await conn.execute(MARK_DEAD_LETTER_SQL, error_msg, row_id)
                    logger.error(f"Dead-lettered outbox row {row_id} after {attempts + 1} attempts: {error_msg}")
                else:
                    await conn.execute(MARK_RETRY_SQL, error_msg, row_id)
                    logger.warning(f"Retry outbox row {row_id} (attempt {attempts + 1}): {error_msg}")

    return processed


async def run_worker_loop(pool: asyncpg.Pool, adapter: TerminusDBAdapter,
                          stop_event: asyncio.Event | None = None):
    """Main worker loop. Runs until stop_event is set or KeyboardInterrupt."""
    logger.info(f"Outbox worker started (id={WORKER_ID}, poll={POLL_INTERVAL}s, batch={BATCH_SIZE})")

    while True:
        if stop_event and stop_event.is_set():
            break
        try:
            count = await poll_cycle(pool, adapter)
            if count:
                logger.info(f"Processed {count} outbox rows")
        except Exception as e:
            logger.error(f"Poll cycle error: {e}")

        if stop_event:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL)
                break
            except asyncio.TimeoutError:
                pass
        else:
            await asyncio.sleep(POLL_INTERVAL)

    logger.info("Outbox worker stopped")


async def main():
    """Standalone entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    _load_env()

    db_url = os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3)

    adapter = _get_adapter()
    health = adapter.health()
    if not health.get("terminusdb_reachable"):
        logger.error(f"TerminusDB not reachable: {health.get('error', 'unknown')}")
        logger.info("Worker will start anyway — rows will accumulate and drain on recovery")

    try:
        await run_worker_loop(pool, adapter)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
