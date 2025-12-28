#!/usr/bin/env python3
"""
Platform Doc Full Run with Gemini.

Processes all unprocessed platform-mention docs and persists entities/relationships.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import psycopg2
from psycopg2.extras import RealDictCursor
import re

from extraction.gemini_extractor import GeminiExtractor
from knowledge_graph.graph_integration import KnowledgeGraphIntegrator, normalize_predicate

RUN_ID = f"platform_full_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
RESULTS_DIR = Path(__file__).parent

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
    if "twitter" in s or "x.com" in s: return "twitter"
    if "telegram" in s: return "telegram"
    if "discord" in s: return "discord"
    if "youtube" in s: return "youtube"
    return "unknown"

def get_unprocessed_platform_docs(conn, limit=None):
    """Get platform docs with 0 relationships."""
    cur = conn.cursor(cursor_factory=RealDictCursor)

    query = """
        SELECT m.rid, m.content->>'text' as text,
               COALESCE(m.metadata->>'sensor', m.source_sensor, 'unknown') as source_sensor
        FROM koi_memories m
        LEFT JOIN koi_relationships r ON r.last_doc_rid = m.rid
        WHERE COALESCE(m.is_private, false) = false
          AND m.superseded_at IS NULL
          AND r.id IS NULL
          AND m.rid NOT LIKE '%heartbeat%'
          AND (m.content::text ILIKE '%notion%'
            OR m.content::text ILIKE '%discord%'
            OR m.content::text ILIKE '%telegram%'
            OR m.content::text ILIKE '%github%')
        ORDER BY m.created_at DESC
    """
    if limit:
        query += f" LIMIT {limit}"

    cur.execute(query)
    docs = [dict(d) for d in cur.fetchall()]
    cur.close()
    return docs

async def process_document(extractor, kg, doc, run_id):
    """Process a single document and persist entities/relationships."""
    source_type = infer_source_type(doc.get("source_sensor", ""))

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
            metadata={"memory_rid": doc["rid"], "run_id": run_id, "source_type": source_type}
        )

        passed_ents = ctx.entities
        passed_rels = ctx.relationships

        # Persist entities
        entities_persisted = 0
        seen_entities = set()
        for e in passed_ents:
            key = (e.name, e.type)
            if key in seen_entities:
                continue
            seen_entities.add(key)

            kg.entity_resolver.get_or_create_entity(
                e.name, e.type,
                metadata={"doc_rid": doc["rid"], "run_id": run_id, "source_type": source_type}
            )
            entities_persisted += 1

        # Persist relationships
        relationships_persisted = 0
        with kg.pg_conn.cursor() as pg_cur:
            for r in passed_rels:
                pred = normalize_predicate(r.predicate)
                if not pred:
                    continue

                subj = kg._find_existing_entity_by_name(r.source)
                obj = kg._find_existing_entity_by_name(r.target)

                if not subj or not obj:
                    continue
                if subj.entity_id == obj.entity_id:
                    continue

                pg_cur.execute("SAVEPOINT platform_rel")
                try:
                    pg_cur.execute("""
                        INSERT INTO koi_relationships
                          (subject_entity_id, predicate, object_entity_id, confidence, last_doc_rid, last_run_id)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (subject_entity_id, predicate, object_entity_id) DO UPDATE SET
                          occurrence_count = koi_relationships.occurrence_count + 1,
                          last_seen_at = now(),
                          last_doc_rid = EXCLUDED.last_doc_rid,
                          last_run_id = EXCLUDED.last_run_id,
                          confidence = GREATEST(
                            COALESCE(koi_relationships.confidence, 0),
                            COALESCE(EXCLUDED.confidence, 0)
                          )
                    """, (subj.entity_id, pred, obj.entity_id, r.confidence, doc["rid"], run_id))
                    pg_cur.execute("RELEASE SAVEPOINT platform_rel")
                    relationships_persisted += 1
                except Exception:
                    pg_cur.execute("ROLLBACK TO SAVEPOINT platform_rel")

            kg.pg_conn.commit()

        return {
            "rid": doc["rid"],
            "status": "success",
            "entities_persisted": entities_persisted,
            "relationships_persisted": relationships_persisted,
        }

    except Exception as e:
        return {
            "rid": doc["rid"],
            "status": "error",
            "error": str(e)[:100],
            "entities_persisted": 0,
            "relationships_persisted": 0,
        }

async def main():
    print("="*70)
    print("PLATFORM DOC FULL RUN")
    print("="*70)
    print(f"Run ID: {RUN_ID}")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")
    print()

    conn = get_db_connection()

    # Get unprocessed platform docs
    print("Fetching unprocessed platform docs...")
    docs = get_unprocessed_platform_docs(conn)
    print(f"Found {len(docs)} documents to process")
    print()

    if not docs:
        print("No documents to process!")
        conn.close()
        return

    extractor = GeminiExtractor()
    kg = KnowledgeGraphIntegrator(conn)

    results = []
    total_ents = 0
    total_rels = 0
    errors = 0

    for i, doc in enumerate(docs, 1):
        print(f"[{i}/{len(docs)}] {doc['rid'][:55]}...", end=" ", flush=True)

        result = await process_document(extractor, kg, doc, RUN_ID)
        results.append(result)

        if result["status"] == "error":
            print(f"ERROR: {result.get('error', 'unknown')[:40]}")
            errors += 1
        else:
            ents = result["entities_persisted"]
            rels = result["relationships_persisted"]
            total_ents += ents
            total_rels += rels
            print(f"ents={ents} rels={rels}")

        # Rate limit
        await asyncio.sleep(0.3)

        # Progress checkpoint every 500 docs
        if i % 500 == 0:
            print(f"\n--- Checkpoint at {i} docs ---")
            print(f"Total entities: {total_ents}, relationships: {total_rels}, errors: {errors}")
            print(f"Rels/doc so far: {total_rels/i:.2f}")
            print()

    # Summary
    print()
    print("="*70)
    print("SUMMARY")
    print("="*70)
    print(f"Documents processed: {len(docs)}")
    print(f"Successful: {len(docs) - errors}")
    print(f"Errors: {errors}")
    print(f"Total entities persisted: {total_ents}")
    print(f"Total relationships persisted: {total_rels}")

    rels_per_doc = total_rels / len(docs) if len(docs) > 0 else 0
    print(f"Rels/doc: {rels_per_doc:.2f}")

    if rels_per_doc >= 0.4:
        print("PASS: Rels/doc >= 0.4")
    else:
        print("WARNING: Rels/doc < 0.4 - BELOW THRESHOLD")

    error_rate = errors / len(docs) if len(docs) > 0 else 0
    if error_rate < 0.05:
        print(f"PASS: Error rate {error_rate:.1%} < 5%")
    else:
        print(f"WARNING: Error rate {error_rate:.1%} >= 5%")

    # Save results
    output_file = RESULTS_DIR / f"platform_full_results_{RUN_ID}.json"
    with open(output_file, 'w') as f:
        json.dump({
            "run_id": RUN_ID,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "docs_processed": len(docs),
            "total_entities": total_ents,
            "total_relationships": total_rels,
            "rels_per_doc": rels_per_doc,
            "errors": errors,
            "results": results[:100],  # Only save first 100 results to limit file size
        }, f, indent=2, default=str)
    print(f"\nResults saved to: {output_file}")

    conn.close()

if __name__ == "__main__":
    asyncio.run(main())
