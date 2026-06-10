#!/usr/bin/env python3
"""
Backfill embeddings for the seven municipality Organization rows created by the
job 7 Peninsula Streams remediation.

Default mode is dry-run. Use --apply to write embeddings.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg

from api.embedding_provider import create_embedding_provider

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


TARGETS: list[tuple[str, str]] = [
    ("orn:personal-koi.entity:organization-city-of-victoria-79bafd348d5c", "City of Victoria"),
    ("orn:personal-koi.entity:organization-district-of-oak-bay-1b5fb16cb3c6", "District of Oak Bay"),
    ("orn:personal-koi.entity:organization-city-of-langford-8cf1459caa3c", "City of Langford"),
    ("orn:personal-koi.entity:organization-town-of-view-royal-cf5555b5707b", "Town of View Royal"),
    ("orn:personal-koi.entity:organization-district-of-central-saanich-e84fc9631101", "District of Central Saanich"),
    ("orn:personal-koi.entity:organization-district-of-saanich-39db6e8ee64f", "District of Saanich"),
    ("orn:personal-koi.entity:organization-district-of-north-saanich-fd8134ea8e1f", "District of North Saanich"),
]


async def main(apply: bool) -> None:
    provider = create_embedding_provider()
    if provider is None:
        raise SystemExit("No embedding provider configured.")

    db_url = os.environ.get("POSTGRES_URL")
    if not db_url:
        raise SystemExit("POSTGRES_URL not set.")

    logger.info("Embedding provider: %s (dim=%s)", provider.model_name, provider.dimension)

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT fuseki_uri, entity_text, embedding IS NOT NULL AS has_embedding
                FROM entity_registry
                WHERE fuseki_uri = ANY($1::text[])
                ORDER BY entity_text
                """,
                [uri for uri, _ in TARGETS],
            )

        found = {row["fuseki_uri"]: row for row in rows}
        missing = [uri for uri, _ in TARGETS if uri not in found]
        if missing:
            raise SystemExit(f"Missing target rows: {missing}")

        for uri, name in TARGETS:
            row = found[uri]
            logger.info("Target: %s | %s | has_embedding=%s", row["entity_text"], uri, row["has_embedding"])
            if row["entity_text"] != name:
                raise SystemExit(f"Name mismatch for {uri}: expected '{name}', found '{row['entity_text']}'")

        if not apply:
            logger.info("Dry run only. Re-run with --apply to write embeddings.")
            return

        already_embedded = [uri for uri, _ in TARGETS if found[uri]["has_embedding"]]
        if already_embedded:
            raise SystemExit(
                "Refusing to overwrite existing embeddings for target rows: "
                + ", ".join(already_embedded)
            )

        # Pack 2 (2026-04-28): migrated to embed_batch_or_none for
        # B2/C4 token-tracking metric emission. One-shot script; fail
        # loud on None.
        embeddings = await provider.embed_batch_or_none(
            [name for _, name in TARGETS], prompt_type="extraction"
        )
        if embeddings is None:
            raise SystemExit("embed_batch_or_none returned None — embedding service failed")
        if len(embeddings) != len(TARGETS):
            raise SystemExit(f"Expected {len(TARGETS)} embeddings, got {len(embeddings)}")

        async with pool.acquire() as conn:
            async with conn.transaction():
                for (uri, _name), embedding in zip(TARGETS, embeddings):
                    await conn.execute(
                        """
                        UPDATE entity_registry
                           SET embedding = $1,
                               updated_at = NOW()
                         WHERE fuseki_uri = $2
                        """,
                        str(embedding),
                        uri,
                    )

        async with pool.acquire() as conn:
            embedded = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM entity_registry
                WHERE fuseki_uri = ANY($1::text[])
                  AND embedding IS NOT NULL
                """,
                [uri for uri, _ in TARGETS],
            )
        logger.info("Backfill complete: %s/%s target rows now have embeddings.", embedded, len(TARGETS))
    finally:
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill embeddings for remediated Peninsula municipality entities")
    parser.add_argument("--apply", action="store_true", help="Write embeddings instead of dry-run")
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
