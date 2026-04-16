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


async def export_table(
    conn,
    output_dir: str,
    table: str,
    id_col: str,
    text_sql: str,
    filename: str,
    where: str = "",
):
    """Export (id, text) pairs as JSONL."""
    sql = f"SELECT {id_col} AS id, {text_sql} AS text FROM {table}"
    if where:
        sql += f" WHERE {where}"
    rows = await conn.fetch(sql)
    path = os.path.join(output_dir, filename)
    count = 0
    with open(path, "w") as f:
        for row in rows:
            text = row["text"]
            if text and text.strip():
                f.write(json.dumps({"id": str(row["id"]), "text": text[:2000]}) + "\n")
                count += 1
    print(f"  {filename}: {count} rows exported{' (NULL-only)' if where else ''}")
    return count


async def main():
    parser = argparse.ArgumentParser(description="Export personal_koi for H200 re-embedding")
    parser.add_argument("--db-url", default=os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi"))
    parser.add_argument("--output-dir", default="./reembed_data")
    parser.add_argument("--only-null", action="store_true",
                        help="Only export rows whose embedding column is NULL (incremental backfill).")
    parser.add_argument("--tables", default="entity_registry,session_chunks,knowledge_facts",
                        help="Comma-separated subset of tables to export (default: all three).")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    selected = set(args.tables.split(","))

    conn = await asyncpg.connect(args.db_url)
    try:
        print(f"Exporting from {args.db_url} to {args.output_dir}/{' (only-null)' if args.only_null else ''}")

        if "entity_registry" in selected:
            await export_table(
                conn, args.output_dir,
                table="entity_registry",
                id_col="id",
                text_sql="COALESCE(entity_text, '') || CASE WHEN description IS NOT NULL AND description != '' THEN ' — ' || description ELSE '' END",
                filename="entity_registry_for_reembed.jsonl",
                where="embedding IS NULL" if args.only_null else "",
            )

        if "session_chunks" in selected:
            await export_table(
                conn, args.output_dir,
                table="session_chunks",
                id_col="id",
                text_sql="chunk_text",
                filename="session_chunks_for_reembed.jsonl",
                where="embedding IS NULL" if args.only_null else "",
            )

        if "knowledge_facts" in selected:
            await export_table(
                conn, args.output_dir,
                table="knowledge_facts",
                id_col="id",
                text_sql="fact_text",
                filename="knowledge_facts_for_reembed.jsonl",
                where="fact_embedding IS NULL" if args.only_null else "",
            )

        print("\nDone. Upload these files to H200 and run:")
        print("  python3 scripts/reembed_on_h200.py --input-dir ./reembed_data --output-dir ./reembed_results")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
