#!/usr/bin/env python3
"""Import re-embedded vectors into personal_koi after H200 processing.

Reads JSONL files with {"id": ..., "embedding": [...]} and UPDATEs the
corresponding rows in the database.

Usage:
  python3 scripts/import_reembeddings.py --input-dir ./reembed_results [--db-url postgresql://...]
"""

import argparse
import asyncio
import json
import os
import time

import asyncpg


# Mapping: filename prefix -> (table, id_column, embedding_column)
TABLE_MAP = {
    "entity_registry": ("entity_registry", "id", "embedding"),
    "session_chunks": ("session_chunks", "id", "embedding"),
    "knowledge_facts": ("knowledge_facts", "id", "fact_embedding"),
}


async def import_file(conn, input_path: str, table: str, id_col: str, emb_col: str):
    """Import embeddings from JSONL into database table."""
    records = []
    with open(input_path) as f:
        for line in f:
            records.append(json.loads(line))

    if not records:
        print(f"  {os.path.basename(input_path)}: 0 records, skipping")
        return 0

    print(f"  {os.path.basename(input_path)}: {len(records)} records -> {table}.{emb_col}")
    t0 = time.time()

    # Batch UPDATE
    updated = 0
    batch_size = 100
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        async with conn.transaction():
            for rec in batch:
                result = await conn.execute(f"""
                    UPDATE {table}
                    SET {emb_col} = $1::vector
                    WHERE {id_col} = $2
                """, str(rec["embedding"]), int(rec["id"]))
                if "UPDATE 1" in result:
                    updated += 1

    elapsed = time.time() - t0
    print(f"    Updated {updated}/{len(records)} rows in {elapsed:.1f}s")
    return updated


async def main():
    parser = argparse.ArgumentParser(description="Import re-embeddings into personal_koi")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--db-url", default=os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi"))
    args = parser.parse_args()

    conn = await asyncpg.connect(args.db_url)
    try:
        total = 0
        for fname in sorted(os.listdir(args.input_dir)):
            if not fname.endswith("_embeddings.jsonl"):
                continue

            # Determine table from filename
            prefix = fname.replace("_embeddings.jsonl", "")
            if prefix not in TABLE_MAP:
                print(f"  WARNING: Unknown file prefix '{prefix}', skipping {fname}")
                continue

            table, id_col, emb_col = TABLE_MAP[prefix]
            input_path = os.path.join(args.input_dir, fname)
            total += await import_file(conn, input_path, table, id_col, emb_col)

        print(f"\nTotal: {total} embeddings imported")

        # Verify
        print("\nVerification:")
        for prefix, (table, _, emb_col) in TABLE_MAP.items():
            count = await conn.fetchval(f"SELECT count(*) FROM {table} WHERE {emb_col} IS NOT NULL")
            print(f"  {table}.{emb_col}: {count} non-null embeddings")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
