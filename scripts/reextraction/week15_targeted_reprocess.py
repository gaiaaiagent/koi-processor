#!/usr/bin/env python3
"""
Week 15 Targeted Reprocess Script

Reprocesses a specific list of documents to apply the Week 15 prompt update
for carbon/NCT concept relationships.

Uses the same extraction and persistence logic as stage6_full_reextract_gemini.py
but only processes documents specified in the input file.

Usage:
    cd /opt/projects/koi-processor

    # Generate input file
    psql "postgresql://postgres:postgres@localhost:5433/eliza" -t -A -c "
    SELECT id::text FROM koi_memories
    WHERE COALESCE(is_private, false) = false
      AND (content::text ILIKE '%carbon credit%'
           OR content::text ILIKE '%nct token%'
           OR content::text ILIKE '%carbon sequestration%')
    LIMIT 200;
    " > scripts/reextraction/week15_target_rids.txt

    # Run reprocess
    PYTHONPATH=src ./.venv/bin/python scripts/reextraction/week15_targeted_reprocess.py

    # Dry run
    PYTHONPATH=src ./.venv/bin/python scripts/reextraction/week15_targeted_reprocess.py --dry-run

Environment (required):
    OPENAI_API_KEY          - OpenAI API key
    POSTGRES_*              - PostgreSQL connection vars
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import psycopg2
from psycopg2.extras import RealDictCursor

from extraction.openai_extractor import OpenAIExtractor
from knowledge_graph.graph_integration import KnowledgeGraphIntegrator, normalize_predicate

# Input file with target RIDs
TARGET_RIDS_FILE = Path(__file__).parent / "week15_target_rids.txt"

# Run ID for this batch
RUN_ID = f"week15_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"


def infer_source_type(source_sensor: str) -> str:
    """Infer source_type from source_sensor string."""
    s = (source_sensor or "").lower()
    if "discourse" in s:
        return "discourse"
    if "github" in s:
        return "github"
    if "medium" in s:
        return "medium"
    if "notion" in s:
        return "notion"
    if "twitter" in s or "x.com" in s:
        return "twitter"
    if "telegram" in s:
        return "telegram"
    if "discord" in s:
        return "discord"
    if "youtube" in s:
        return "youtube"
    return "unknown"


def get_db_connection():
    """Get database connection."""
    db_url = os.getenv("POSTGRES_URL", "postgresql://postgres:postgres@localhost:5433/eliza")

    import re
    match = re.match(r'postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)', db_url)
    if match:
        user, password, host, port, database = match.groups()
        return psycopg2.connect(
            host=host,
            port=int(port),
            user=user,
            password=password,
            database=database
        )
    return psycopg2.connect(db_url)


def load_target_rids(file_path: Path) -> List[str]:
    """Load target document RIDs from file."""
    if not file_path.exists():
        raise FileNotFoundError(f"Target RIDs file not found: {file_path}")

    rids = []
    with open(file_path, 'r') as f:
        for line in f:
            rid = line.strip()
            if rid and not rid.startswith('#'):
                rids.append(rid)

    return rids


def fetch_documents(conn, rids: List[str]) -> List[Dict]:
    """Fetch documents from database."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    placeholders = ','.join(['%s'] * len(rids))
    query = f"""
        SELECT
            id::text as rid,
            content->>'text' as text,
            COALESCE(metadata->>'sensor', 'unknown') as source_sensor
        FROM koi_memories
        WHERE id IN ({placeholders})
    """

    cursor.execute(query, rids)
    docs = cursor.fetchall()
    cursor.close()

    return [dict(d) for d in docs]


async def process_document(
    extractor: OpenAIExtractor,
    kg: KnowledgeGraphIntegrator,
    doc: Dict,
    run_id: str,
    dry_run: bool = False
) -> Dict[str, Any]:
    """Process a single document through the extraction + persistence pipeline."""
    source_type = infer_source_type(doc["source_sensor"])

    # Step 1: Extract with OpenAI
    try:
        extraction = await extractor.extract_metadata(
            doc["text"],
            source_type,
            existing_metadata={"rid": doc["rid"]},
        )
    except Exception as e:
        return {
            "rid": doc["rid"],
            "status": "error",
            "error": f"Extraction failed: {e}",
            "entities_persisted": 0,
            "relationships_persisted": 0,
        }

    # OpenAI extractor returns entities/relationships in semantic_extraction
    semantic = extraction.get("semantic_extraction", {})
    raw_entities = semantic.get("entities", [])
    raw_relationships = semantic.get("relationships", [])
    tokens = extraction.get("token_usage", {}).get("total_tokens", 0)

    # Step 2: Run pipeline
    context = kg.pipeline.process_entities(
        raw_entities,
        raw_relationships,
        metadata={"memory_rid": doc["rid"], "run_id": run_id, "source_type": source_type},
    )

    passed_entities = context.entities
    passed_rels = context.relationships

    if dry_run:
        return {
            "rid": doc["rid"],
            "status": "dry_run",
            "raw_entities": len(raw_entities),
            "raw_relationships": len(raw_relationships),
            "passed_entities": len(passed_entities),
            "passed_relationships": len(passed_rels),
            "tokens": tokens,
        }

    # Step 3: Persist entities
    entities_persisted = 0
    seen_entities = set()
    for e in passed_entities:
        key = (e.name, e.type)
        if key in seen_entities:
            continue
        seen_entities.add(key)

        kg.entity_resolver.get_or_create_entity(
            e.name,
            e.type,
            metadata={"doc_rid": doc["rid"], "run_id": run_id, "source_type": source_type},
        )
        entities_persisted += 1

    # Step 4: Persist relationships
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

            pg_cur.execute("SAVEPOINT week15_rel")
            try:
                pg_cur.execute(
                    """
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
                    """,
                    (subj.entity_id, pred, obj.entity_id, r.confidence, doc["rid"], run_id),
                )
                pg_cur.execute("RELEASE SAVEPOINT week15_rel")
                relationships_persisted += 1
            except Exception as e:
                pg_cur.execute("ROLLBACK TO SAVEPOINT week15_rel")

        kg.pg_conn.commit()

    return {
        "rid": doc["rid"],
        "status": "success",
        "raw_entities": len(raw_entities),
        "raw_relationships": len(raw_relationships),
        "entities_persisted": entities_persisted,
        "relationships_persisted": relationships_persisted,
        "tokens": tokens,
    }


async def main(dry_run: bool = False, max_docs: int = None, rate_limit: float = 0.5):
    """Main entry point."""
    print("=" * 70)
    print("WEEK 15 TARGETED REPROCESS")
    print("=" * 70)
    print(f"Run ID: {RUN_ID}")
    print(f"Dry run: {dry_run}")
    print(f"Rate limit: {rate_limit}s between docs")
    print()

    # Load target RIDs
    target_rids = load_target_rids(TARGET_RIDS_FILE)
    if max_docs:
        target_rids = target_rids[:max_docs]
    print(f"Target documents: {len(target_rids)}")

    # Initialize database connection
    conn = get_db_connection()

    # Fetch documents
    print("Fetching documents from database...")
    docs = fetch_documents(conn, target_rids)
    print(f"Fetched {len(docs)} documents")

    if not docs:
        print("No documents found!")
        return

    # Initialize extractor and integrator
    extractor = OpenAIExtractor()
    kg = KnowledgeGraphIntegrator(conn)

    # Process documents
    results = []
    total_entities = 0
    total_relationships = 0
    errors = 0

    for i, doc in enumerate(docs, 1):
        print(f"[{i}/{len(docs)}] Processing {doc['rid'][:8]}...", end=" ", flush=True)

        result = await process_document(extractor, kg, doc, RUN_ID, dry_run)
        results.append(result)

        if result["status"] == "error":
            print(f"ERROR: {result.get('error', 'unknown')}")
            errors += 1
        else:
            ent = result.get("entities_persisted", 0) or result.get("passed_entities", 0)
            rel = result.get("relationships_persisted", 0) or result.get("passed_relationships", 0)
            total_entities += ent
            total_relationships += rel
            print(f"entities={ent}, relationships={rel}")

        if rate_limit > 0:
            await asyncio.sleep(rate_limit)

    # Print summary
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Documents processed: {len(docs)}")
    print(f"Successful: {len(docs) - errors}")
    print(f"Errors: {errors}")
    print(f"Total entities persisted: {total_entities}")
    print(f"Total relationships persisted: {total_relationships}")

    # Save results
    output_file = Path(__file__).parent / f"week15_reprocess_results_{RUN_ID}.json"
    with open(output_file, 'w') as f:
        json.dump({
            "run_id": RUN_ID,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "dry_run": dry_run,
            "docs_processed": len(docs),
            "total_entities": total_entities,
            "total_relationships": total_relationships,
            "errors": errors,
            "results": results,
        }, f, indent=2, default=str)
    print(f"\nResults saved to: {output_file}")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Week 15 Targeted Reprocess")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to database")
    parser.add_argument("--max-docs", type=int, help="Maximum documents to process")
    parser.add_argument("--rate-limit", type=float, default=0.5, help="Delay between docs (seconds)")

    args = parser.parse_args()

    asyncio.run(main(
        dry_run=args.dry_run,
        max_docs=args.max_docs,
        rate_limit=args.rate_limit,
    ))
