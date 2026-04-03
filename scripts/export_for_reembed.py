#!/usr/bin/env python3
"""Export personal_koi tables for H200 re-embedding with Qwen3-Embedding-0.6B.

Exports 3 JSONL files (one per table that needs re-embedding):
  - entity_registry_for_reembed.jsonl
  - session_chunks_for_reembed.jsonl
  - knowledge_facts_for_reembed.jsonl

Each line: {"id": ..., "text": "..."}
DOCUMENT mode only (no instruction prefix) — stored embeddings are documents.

Usage:
  python3 scripts/export_for_reembed.py [--db-url postgresql://...] [--output-dir ./reembed_data]
"""

import argparse
import asyncio
import json
import os
import sys

import asyncpg


async def export_table(conn, output_dir: str, table: str, id_col: str, text_sql: str, filename: str):
    """Export (id, text) pairs as JSONL."""
    rows = await conn.fetch(f"SELECT {id_col} AS id, {text_sql} AS text FROM {table}")
    path = os.path.join(output_dir, filename)
    count = 0
    with open(path, "w") as f:
        for row in rows:
            text = row["text"]
            if text and text.strip():
                f.write(json.dumps({"id": str(row["id"]), "text": text[:2000]}) + "\n")
                count += 1
    print(f"  {filename}: {count} rows exported")
    return count


async def main():
    parser = argparse.ArgumentParser(description="Export personal_koi for H200 re-embedding")
    parser.add_argument("--db-url", default=os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi"))
    parser.add_argument("--output-dir", default="./reembed_data")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    conn = await asyncpg.connect(args.db_url)
    try:
        print(f"Exporting from {args.db_url} to {args.output_dir}/")

        # entity_registry: combine entity_text + description + context metadata
        await export_table(
            conn, args.output_dir,
            table="entity_registry",
            id_col="id",
            text_sql="COALESCE(entity_text, '') || CASE WHEN description IS NOT NULL AND description != '' THEN ' — ' || description ELSE '' END",
            filename="entity_registry_for_reembed.jsonl",
        )

        # session_chunks: chunk_text
        await export_table(
            conn, args.output_dir,
            table="session_chunks",
            id_col="id",
            text_sql="chunk_text",
            filename="session_chunks_for_reembed.jsonl",
        )

        # knowledge_facts: fact_text
        await export_table(
            conn, args.output_dir,
            table="knowledge_facts",
            id_col="id",
            text_sql="fact_text",
            filename="knowledge_facts_for_reembed.jsonl",
        )

        print("\nDone. Upload these files to H200 and run:")
        print("  python3 scripts/reembed_on_h200.py --input-dir ./reembed_data --output-dir ./reembed_results")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
