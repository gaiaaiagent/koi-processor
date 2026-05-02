#!/usr/bin/env python3
"""
Backfill missing entity embeddings in entity_registry.

Usage:
  python3 scripts/backfill_entity_embeddings.py              # embed all missing
  python3 scripts/backfill_entity_embeddings.py --limit 100   # first 100 only
  python3 scripts/backfill_entity_embeddings.py --entity-type Concept
  python3 scripts/backfill_entity_embeddings.py --dry-run     # count only
  python3 scripts/backfill_entity_embeddings.py --re-embed-enriched  # re-embed B8a-enriched entities
"""

import argparse
import asyncio
import logging
import os
import sys
import time

# Add parent dir so api/ imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg
from api.embedding_provider import create_embedding_provider

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BATCH_SIZE = 100


async def main(args):
    provider = create_embedding_provider()
    if provider is None:
        logger.error("No embedding provider configured. Set OPENAI_API_KEY.")
        sys.exit(1)

    logger.info(f"Embedding provider: {provider.model_name} (dim={provider.dimension})")

    db_url = os.environ.get("POSTGRES_URL")
    if not db_url:
        logger.error("POSTGRES_URL not set")
        sys.exit(1)

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)

    # Build query — entity_registry uses fuseki_uri and entity_text (not uri/name)
    if args.re_embed_enriched:
        where = "WHERE metadata->>'context' IS NOT NULL"
        logger.info("Mode: re-embed B8a-enriched entities (metadata->>'context' IS NOT NULL)")
    else:
        where = "WHERE embedding IS NULL"
    params = []
    if args.entity_type:
        where += f" AND entity_type = ${1}"
        params.append(args.entity_type)

    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT COUNT(*) FROM entity_registry {where}", *params)

    label = "enriched entities to re-embed" if args.re_embed_enriched else "entities missing embeddings"
    logger.info(f"{label}: {total}")
    if args.entity_type:
        logger.info(f"  (filtered to type: {args.entity_type})")

    if args.dry_run:
        # Show breakdown
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT entity_type, COUNT(*) as cnt FROM entity_registry "
                f"{where} GROUP BY entity_type ORDER BY cnt DESC",
                *params
            )
            for r in rows:
                logger.info(f"  {r['entity_type']}: {r['cnt']}")
            if args.re_embed_enriched:
                with_ctx = await conn.fetchval(
                    f"SELECT COUNT(*) FROM entity_registry {where} "
                    "AND LENGTH(metadata->>'context') > 10", *params
                )
                logger.info(f"  With context > 10 chars: {with_ctx}")
            else:
                with_desc = await conn.fetchval(
                    "SELECT COUNT(*) FROM entity_registry WHERE embedding IS NULL "
                    "AND description IS NOT NULL AND description != ''"
                )
                logger.info(f"  With description: {with_desc}, Name-only: {total - with_desc}")
        return

    limit_clause = f"LIMIT {args.limit}" if args.limit else ""
    order = "ORDER BY entity_type, entity_text"

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT fuseki_uri, entity_text, description, metadata->>'context' AS context FROM entity_registry {where} {order} {limit_clause}",
            *params
        )

    logger.info(f"Processing {len(rows)} entities in batches of {BATCH_SIZE}")

    embedded = 0
    name_only = 0
    with_desc = 0
    failures = 0
    start_time = time.time()

    for batch_start in range(0, len(rows), BATCH_SIZE):
        batch = rows[batch_start:batch_start + BATCH_SIZE]
        texts = []
        uris = []

        for row in batch:
            name = row["entity_text"] or ""
            ctx = (row["context"] or "").strip()
            desc = (row["description"] or "").strip()
            # Combine: context (B8a LLM-generated from doc links) + description (original metadata)
            combined = ". ".join(filter(None, [ctx, desc]))
            if combined:
                text = f"{name}: {combined}"
                with_desc += 1
            else:
                text = name
                name_only += 1
            texts.append(text[:8000])  # truncate to avoid token limits
            uris.append(row["fuseki_uri"])

        # Pack 2 (2026-04-28): migrated to embed_batch_or_none for
        # B2/C4 token-tracking metric emission. Returns None on
        # whole-batch failure (matches embed_or_none semantics); same
        # back-off/continue behavior as the prior try/except.
        embeddings = await provider.embed_batch_or_none(
            texts, prompt_type="extraction"
        )
        if embeddings is None:
            logger.error(
                f"Batch {batch_start//BATCH_SIZE + 1} failed "
                f"(embed_batch_or_none returned None)"
            )
            failures += len(batch)
            await asyncio.sleep(5)  # back off on failure
            continue

        # Write to DB
        async with pool.acquire() as conn:
            async with conn.transaction():
                for uri, emb in zip(uris, embeddings):
                    await conn.execute(
                        "UPDATE entity_registry SET embedding = $1 WHERE fuseki_uri = $2",
                        str(emb), uri
                    )

        embedded += len(batch)
        elapsed = time.time() - start_time
        rate = embedded / elapsed * 60 if elapsed > 0 else 0
        logger.info(
            f"  Batch {batch_start//BATCH_SIZE + 1}: "
            f"{embedded}/{len(rows)} done ({rate:.0f}/min), "
            f"failures: {failures}"
        )

        # Small delay between batches to avoid rate limits
        await asyncio.sleep(0.5)

    elapsed = time.time() - start_time
    logger.info(f"\nDone in {elapsed:.1f}s")
    logger.info(f"  Embedded: {embedded}")
    logger.info(f"  With description: {with_desc}")
    logger.info(f"  Name-only: {name_only}")
    logger.info(f"  Failures: {failures}")
    logger.info(f"  Rate: {embedded / elapsed * 60:.0f}/min")

    # Final check
    async with pool.acquire() as conn:
        remaining = await conn.fetchval(
            "SELECT COUNT(*) FROM entity_registry WHERE embedding IS NULL"
        )
    logger.info(f"  Remaining without embedding: {remaining}")
    if args.re_embed_enriched:
        logger.info("  (re-embed mode: entities already had embeddings, they were updated)")

    await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill entity embeddings")
    parser.add_argument("--limit", type=int, help="Max entities to process")
    parser.add_argument("--entity-type", help="Filter to specific entity type")
    parser.add_argument("--dry-run", action="store_true", help="Count only, no embedding")
    parser.add_argument("--re-embed-enriched", action="store_true",
                        help="Re-embed entities with B8a-enriched metadata context (instead of only missing embeddings)")
    args = parser.parse_args()
    asyncio.run(main(args))
