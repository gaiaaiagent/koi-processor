#!/usr/bin/env python3
"""Quick dry-run of platform sample with Gemini."""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import psycopg2
from psycopg2.extras import RealDictCursor
import re

from extraction.gemini_extractor import GeminiExtractor
from knowledge_graph.graph_integration import KnowledgeGraphIntegrator

def get_db_connection():
    db_url = os.getenv("POSTGRES_URL")
    match = re.match(r"postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)", db_url)
    if match:
        user, password, host, port, database = match.groups()
        return psycopg2.connect(host=host, port=int(port), user=user, password=password, database=database)
    return psycopg2.connect(db_url)

def infer_source_type(source_sensor):
    s = (source_sensor or "").lower()
    if "discourse" in s: return "discourse"
    if "github" in s: return "github"
    if "medium" in s: return "medium"
    if "notion" in s: return "notion"
    return "unknown"

async def main():
    print("Platform Sample Dry-Run (100 docs)")
    print("="*60)

    # Load RIDs
    rids_file = Path(__file__).parent.parent.parent / "data" / "platform_sample_100.txt"
    rids = [l.strip() for l in open(rids_file) if l.strip()]
    print(f"Loaded {len(rids)} RIDs")

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Fetch docs
    placeholders = ",".join(["%s"] * len(rids))
    query = f"""
        SELECT rid, content->>'text' as text,
               COALESCE(metadata->>'sensor', source_sensor, 'unknown') as source_sensor
        FROM koi_memories WHERE rid IN ({placeholders})
    """
    cur.execute(query, rids)
    docs = [dict(d) for d in cur.fetchall()]
    print(f"Fetched {len(docs)} docs from DB")

    if not docs:
        print("No docs found!")
        return

    extractor = GeminiExtractor()
    kg = KnowledgeGraphIntegrator(conn)

    total_ents = 0
    total_rels = 0
    errors = 0

    for i, doc in enumerate(docs[:100], 1):
        source_type = infer_source_type(doc.get("source_sensor", ""))
        print(f"[{i}/100] {doc['rid'][:50]}...", end=" ", flush=True)

        try:
            extraction = await extractor.extract_metadata(
                doc["text"] or "",
                source_type,
                existing_metadata={"rid": doc["rid"]},
            )

            raw_ents = extraction.get("extracted_entities", [])
            raw_rels = extraction.get("extracted_relationships", [])

            # Run pipeline
            ctx = kg.pipeline.process_entities(
                raw_ents, raw_rels,
                metadata={"memory_rid": doc["rid"], "source_type": source_type}
            )

            passed_ents = len(ctx.entities)
            passed_rels = len(ctx.relationships)
            total_ents += passed_ents
            total_rels += passed_rels
            print(f"ents={passed_ents} rels={passed_rels}")

        except Exception as e:
            print(f"ERROR: {str(e)[:50]}")
            errors += 1

        await asyncio.sleep(0.3)

    print()
    print("="*60)
    print("SUMMARY")
    print(f"Docs: {len(docs)}, Errors: {errors}")
    print(f"Total entities: {total_ents}, Total relationships: {total_rels}")
    rels_per_doc = total_rels/len(docs) if len(docs) > 0 else 0
    print(f"Rels/doc: {rels_per_doc:.2f}")

    if rels_per_doc >= 0.4:
        print("PASS: Rels/doc >= 0.4")
    else:
        print("WARNING: Rels/doc < 0.4 - BELOW THRESHOLD")

    error_rate = errors/len(docs) if len(docs) > 0 else 0
    if error_rate < 0.05:
        print(f"PASS: Error rate {error_rate:.1%} < 5%")
    else:
        print(f"WARNING: Error rate {error_rate:.1%} >= 5% - ABOVE THRESHOLD")

    conn.close()

if __name__ == "__main__":
    asyncio.run(main())
