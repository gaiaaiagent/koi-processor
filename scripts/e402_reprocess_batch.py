#!/usr/bin/env python3
"""
E402 Reprocess Batch - Platform Relationship Extraction

Reprocesses a batch of documents with the enhanced prompt to extract
platform/tool relationships (uses, documents_on, communicates_via, etc.)

Results are written to a temp table for validation before prod merge.

Usage:
    python scripts/e402_reprocess_batch.py --batch data/e402_reprocess_batch.txt --limit 40
"""

import os
import sys
import json
import asyncio
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

# Target platform entities
TARGET_PLATFORMS = ["notion", "discord", "telegram", "koi", "slack", "github", "medium"]

# Platform predicates we're looking for
PLATFORM_PREDICATES = ["uses", "hosted_on", "powered_by", "integrates_with",
                       "documents_on", "published_on", "communicates_via"]


async def fetch_doc_content(rid: str, db_url: str) -> Optional[Dict[str, Any]]:
    """Fetch document content from koi_memories."""
    import asyncpg

    conn = await asyncpg.connect(db_url)
    try:
        row = await conn.fetchrow("""
            SELECT
                rid,
                content->>'title' as title,
                content->>'text' as text,
                source_sensor,
                is_private
            FROM koi_memories
            WHERE rid LIKE $1
              AND superseded_at IS NULL
            ORDER BY created_at DESC
            LIMIT 1
        """, rid.strip() + "%")

        if row:
            return {
                "rid": row["rid"],
                "title": row["title"],
                "text": row["text"],
                "source": row["source_sensor"],
                "is_private": row["is_private"]
            }
        return None
    finally:
        await conn.close()


async def extract_with_prompt(content: str, model: str = "gpt-4o-mini") -> Dict[str, Any]:
    """Run extraction with enhanced prompt using OpenAI."""
    from openai import OpenAI
    from extraction.prompt_builder import build_extraction_prompt

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY required")

    client = OpenAI(api_key=api_key)
    prompt = build_extraction_prompt(content, "document")

    response = await asyncio.to_thread(
        client.chat.completions.create,
        model=model,
        messages=[
            {"role": "system", "content": "You are a semantic extraction system that outputs only valid JSON."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.1,
        response_format={"type": "json_object"}
    )

    try:
        text = response.choices[0].message.content.strip()
        return json.loads(text)
    except json.JSONDecodeError as e:
        return {"error": str(e), "raw": text[:500]}


def filter_platform_relationships(extraction: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Filter relationships involving platform entities."""
    if "relationships" not in extraction:
        return []

    platform_rels = []
    for rel in extraction.get("relationships", []):
        subject = rel.get("subject", "").lower()
        obj = rel.get("object", "").lower()
        predicate = rel.get("predicate", "")

        # Check if either side is a platform entity or predicate is platform-related
        is_platform = any(p in subject or p in obj for p in TARGET_PLATFORMS)
        is_platform_pred = predicate in PLATFORM_PREDICATES

        if is_platform or is_platform_pred:
            platform_rels.append(rel)

    return platform_rels


async def create_temp_table(db_url: str) -> None:
    """Create temp table for validation results."""
    import asyncpg

    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS e402_extracted_relationships (
                id SERIAL PRIMARY KEY,
                document_rid TEXT,
                subject_text TEXT,
                predicate TEXT,
                object_text TEXT,
                confidence REAL,
                extracted_at TIMESTAMP DEFAULT NOW()
            )
        """)
        # Clear previous run
        await conn.execute("DELETE FROM e402_extracted_relationships")
    finally:
        await conn.close()


async def insert_relationships(db_url: str, doc_rid: str, relationships: List[Dict]) -> int:
    """Insert extracted relationships into temp table."""
    import asyncpg

    if not relationships:
        return 0

    conn = await asyncpg.connect(db_url)
    try:
        count = 0
        for rel in relationships:
            await conn.execute("""
                INSERT INTO e402_extracted_relationships
                (document_rid, subject_text, predicate, object_text, confidence)
                VALUES ($1, $2, $3, $4, $5)
            """,
                doc_rid,
                rel.get("subject", ""),
                rel.get("predicate", ""),
                rel.get("object", ""),
                rel.get("confidence", 0.8)
            )
            count += 1
        return count
    finally:
        await conn.close()


async def main():
    parser = argparse.ArgumentParser(description="E402 Reprocess Batch")
    parser.add_argument("--batch", default="data/e402_reprocess_batch.txt",
                       help="File with RIDs to reprocess")
    parser.add_argument("--db-url", default=os.getenv("POSTGRES_URL"),
                       help="PostgreSQL connection URL")
    parser.add_argument("--limit", type=int, default=40,
                       help="Max docs to process")
    parser.add_argument("--dry-run", action="store_true",
                       help="Extract but don't write to DB")
    args = parser.parse_args()

    if not args.db_url:
        print("Error: POSTGRES_URL required")
        return

    # Load batch RIDs
    batch_file = Path(args.batch)
    if not batch_file.exists():
        print(f"Error: Batch file not found: {batch_file}")
        return

    with open(batch_file) as f:
        rids = [line.strip() for line in f if line.strip()]

    print("=" * 60)
    print("E402 Reprocess Batch - Platform Relationship Extraction")
    print(f"Started: {datetime.now().isoformat()}")
    print(f"Batch size: {len(rids)}, Limit: {args.limit}")
    print("=" * 60)

    # Create temp table
    if not args.dry_run:
        await create_temp_table(args.db_url)
        print("[OK] Created/cleared e402_extracted_relationships table")

    results = []
    total_platform_rels = 0
    errors = 0

    for i, rid in enumerate(rids[:args.limit]):
        print(f"\n[{i+1}/{min(len(rids), args.limit)}] {rid[:60]}...")

        # Fetch doc
        doc = await fetch_doc_content(rid, args.db_url)
        if not doc:
            print("  [SKIP] Doc not found")
            continue

        # Privacy check
        if doc.get("is_private"):
            print("  [SKIP] Private doc")
            continue

        title = doc.get("title") or "No title"
        print(f"  Title: {title[:50]}")

        # Extract
        try:
            extraction = await extract_with_prompt(doc.get("text", ""))

            if "error" in extraction:
                print(f"  [ERROR] {extraction['error']}")
                errors += 1
                continue

            # Filter platform relationships
            platform_rels = filter_platform_relationships(extraction)

            print(f"  Entities: {len(extraction.get('entities', []))}")
            print(f"  All rels: {len(extraction.get('relationships', []))}")
            print(f"  Platform rels: {len(platform_rels)}")

            for rel in platform_rels[:5]:  # Show first 5
                print(f"    ({rel.get('subject')}, {rel.get('predicate')}, {rel.get('object')})")

            if len(platform_rels) > 5:
                print(f"    ... and {len(platform_rels) - 5} more")

            # Write to temp table
            if not args.dry_run and platform_rels:
                inserted = await insert_relationships(args.db_url, rid, platform_rels)
                print(f"  [DB] Inserted {inserted} relationships")

            total_platform_rels += len(platform_rels)
            results.append({
                "rid": rid,
                "title": title,
                "platform_rels": len(platform_rels),
                "all_rels": len(extraction.get("relationships", []))
            })

        except Exception as e:
            print(f"  [ERROR] {e}")
            errors += 1

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Docs processed: {len(results)}")
    print(f"Errors: {errors}")
    print(f"Platform relationships extracted: {total_platform_rels}")

    if not args.dry_run:
        # Show predicate distribution from temp table
        import asyncpg
        conn = await asyncpg.connect(args.db_url)
        rows = await conn.fetch("""
            SELECT predicate, COUNT(*) as cnt
            FROM e402_extracted_relationships
            GROUP BY predicate
            ORDER BY cnt DESC
        """)
        await conn.close()

        print("\nPredicate distribution (in temp table):")
        for row in rows:
            print(f"  {row['predicate']}: {row['cnt']}")

    # Save results
    output = {
        "timestamp": datetime.now().isoformat(),
        "docs_processed": len(results),
        "errors": errors,
        "total_platform_relationships": total_platform_rels,
        "results": results
    }

    output_file = f"e402_reprocess_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to: {output_file}")


if __name__ == "__main__":
    asyncio.run(main())
