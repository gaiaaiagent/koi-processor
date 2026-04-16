#!/usr/bin/env python3
"""Backfill entity extraction for sessions that have chunks but no extraction.

Reads the same extractor-prompt.md as the sensor and the /extract-session-entities
skill — single source of truth. Safe to interrupt and resume: picks up from
extraction status columns on session_ingestion_log.

Usage:
  python3 scripts/backfill_session_entities.py [--config CONFIG] [--new-only] [--limit N] [--dry-run]
"""

import argparse
import asyncio
import logging
import os
import sys
import time

# Add the sensor to the path so we can reuse the ClaudeSessionSensor class.
# Note: koi-sensors and koi-processor live under differently-cased parent dirs
# (RegenAI/ vs regenai/) — use absolute path to avoid case confusion.
SENSOR_DIR = '/Users/darrenzal/projects/RegenAI/koi-sensors'
sys.path.insert(0, SENSOR_DIR)
from sensors.claude_sessions.claude_session_sensor import ClaudeSessionSensor

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('backfill_session_entities')


async def main():
    parser = argparse.ArgumentParser(description='Backfill session entity extraction')
    parser.add_argument('--config', type=str,
                        default=os.path.join(SENSOR_DIR, 'sensors/claude_sessions/config.personal.yaml'),
                        help='Sensor config file')
    parser.add_argument('--new-only', action='store_true',
                        help='Only extract sessions that have NEVER been extracted (skip re-extractions)')
    parser.add_argument('--limit', type=int, default=0,
                        help='Max sessions to process (0=all eligible)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show eligible sessions without processing')
    parser.add_argument('--batch-size', type=int, default=20,
                        help='Sessions per DB fetch round (FOR UPDATE SKIP LOCKED)')
    args = parser.parse_args()

    sensor = ClaudeSessionSensor(config_path=args.config)
    await sensor.initialize()

    ext_cfg = sensor.config.get('entity_extraction', {})
    max_attempts = ext_cfg.get('max_attempts', 5)

    try:
        async with sensor.db_pool.acquire() as conn:
            # Build eligibility query
            where_clause = """
                WHERE entities_extracted_at IS NULL AND extraction_attempts < $1
            """ if args.new_only else """
                WHERE (entities_extracted_at IS NULL AND extraction_attempts < $1)
                   OR (entities_extracted_at IS NOT NULL AND entities_extracted_at < last_ingested_at)
            """

            total = await conn.fetchval(f"""
                SELECT COUNT(*) FROM session_ingestion_log {where_clause}
            """, max_attempts)

        effective_limit = args.limit if args.limit > 0 else total
        logger.info(f"Eligible: {total} sessions. Processing up to {effective_limit}.")

        if args.dry_run:
            async with sensor.db_pool.acquire() as conn:
                rows = await conn.fetch(f"""
                    SELECT session_id, transcript_path, source_host,
                           extraction_attempts, extraction_last_error
                    FROM session_ingestion_log
                    {where_clause}
                    ORDER BY last_ingested_at DESC
                    LIMIT $2
                """, max_attempts, min(effective_limit, 50))
                for r in rows:
                    print(f"  {r['session_id'][:8]}  host={r['source_host'] or '?':<8}  "
                          f"attempts={r['extraction_attempts']}  "
                          f"error={r['extraction_last_error'] or 'none'}")
            return

        processed = 0
        succeeded = 0
        t0 = time.time()

        while processed < effective_limit:
            batch = min(args.batch_size, effective_limit - processed)
            n = await sensor._run_extraction_batch(limit=batch, time_budget=None)
            if n == 0:
                # No more eligible sessions or all exhausted
                break
            succeeded += n
            processed += batch

            elapsed = time.time() - t0
            rate = succeeded / elapsed if elapsed > 0 else 0
            remaining = (total - processed) / rate if rate > 0 else 0
            logger.info(
                f"Progress: {processed}/{total} attempted, {succeeded} succeeded, "
                f"{elapsed:.0f}s elapsed, ~{remaining/60:.0f}min remaining"
            )

        elapsed = time.time() - t0
        logger.info(f"Backfill complete: {succeeded}/{processed} succeeded in {elapsed:.0f}s")

    finally:
        await sensor.close()


if __name__ == '__main__':
    asyncio.run(main())
