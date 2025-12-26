#!/usr/bin/env python3
"""
Backfill Broken Extractions - Phase 2

Reprocesses documents ingested after 2025-12-15 that have 0 entities/relationships,
which occurred while the OPENAI_EXTRACT_MODEL was broken.

Uses the same extraction and persistence logic as week15_targeted_reprocess.py.

Usage:
    cd /opt/projects/koi-processor
    set -a; source .env; set +a

    # Step 1: Identify affected docs and save to file
    PYTHONPATH=src ./.venv/bin/python scripts/reextraction/backfill_broken_extractions.py --identify

    # Step 2: Dry run (extract only, no DB writes)
    PYTHONPATH=src ./.venv/bin/python scripts/reextraction/backfill_broken_extractions.py --dry-run

    # Step 3: Full reprocess
    PYTHONPATH=src ./.venv/bin/python scripts/reextraction/backfill_broken_extractions.py

Environment (required):
    OPENAI_API_KEY          - OpenAI API key
    OPENAI_EXTRACT_MODEL    - Should be gpt-4.1-mini (fixed)
    PREDICATE_GUARD_VALIDATE_TYPES=true
    PREDICATE_GUARD_STRICT_TYPES=true
    POSTGRES_*              - PostgreSQL connection vars
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import psycopg2
from psycopg2.extras import RealDictCursor

from extraction.openai_extractor import OpenAIExtractor
from knowledge_graph.graph_integration import KnowledgeGraphIntegrator, normalize_predicate

# Output files
BACKFILL_RIDS_FILE = Path(__file__).parent.parent.parent / "data" / "backfill_rids_2026_01.txt"
RESULTS_DIR = Path(__file__).parent

# Cutoff date for identifying broken extractions
CUTOFF_DATE = "2025-12-15"

# Run ID for this batch
RUN_ID = f"backfill_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"


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
    if "website" in s or "web" in s:
        return "website"
    return "unknown"


def identify_affected_docs(conn) -> List[str]:
    """
    Identify documents ingested after cutoff date with 0 entities.

    SQL logic:
    - koi_memories created after 2025-12-15
    - No matching entities in entity_registry (via source_rid metadata)
    - Exclude heartbeat docs
    """
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    query = """
        SELECT m.rid, m.created_at
        FROM koi_memories m
        LEFT JOIN entity_registry e ON e.metadata->>'source_rid' = m.rid
        WHERE m.created_at > %s
          AND e.id IS NULL
          AND m.rid NOT LIKE '%%heartbeat%%'
          AND COALESCE(m.is_private, false) = false
        ORDER BY m.created_at DESC
    """

    cursor.execute(query, (CUTOFF_DATE,))
    rows = cursor.fetchall()
    cursor.close()

    return [row['rid'] for row in rows]


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
    if not rids:
        return []

    cursor = conn.cursor(cursor_factory=RealDictCursor)

    placeholders = ','.join(['%s'] * len(rids))
    query = f"""
        SELECT
            rid,
            content->>'text' as text,
            content->>'title' as title,
            COALESCE(metadata->>'sensor', source_sensor, 'unknown') as source_sensor
        FROM koi_memories
        WHERE rid IN ({placeholders})
          AND COALESCE(is_private, false) = false
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
            doc["text"] or "",
            source_type,
            existing_metadata={"rid": doc["rid"]},
        )
    except Exception as e:
        return {
            "rid": doc["rid"],
            "title": doc.get("title", "")[:50],
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
            "title": doc.get("title", "")[:50],
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

            pg_cur.execute("SAVEPOINT backfill_rel")
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
                pg_cur.execute("RELEASE SAVEPOINT backfill_rel")
                relationships_persisted += 1
            except Exception as e:
                pg_cur.execute("ROLLBACK TO SAVEPOINT backfill_rel")

        kg.pg_conn.commit()

    return {
        "rid": doc["rid"],
        "title": doc.get("title", "")[:50],
        "status": "success",
        "raw_entities": len(raw_entities),
        "raw_relationships": len(raw_relationships),
        "entities_persisted": entities_persisted,
        "relationships_persisted": relationships_persisted,
        "tokens": tokens,
    }


async def main(identify_only: bool = False, dry_run: bool = False, max_docs: int = None, rate_limit: float = 0.5):
    """Main entry point."""
    print("=" * 70)
    print("PHASE 2: BACKFILL BROKEN EXTRACTIONS")
    print("=" * 70)
    print(f"Run ID: {RUN_ID}")
    print(f"Cutoff date: {CUTOFF_DATE}")
    print(f"Model: {os.getenv('OPENAI_EXTRACT_MODEL', 'NOT SET')}")
    print(f"Predicate guard validate: {os.getenv('PREDICATE_GUARD_VALIDATE_TYPES', 'NOT SET')}")
    print(f"Predicate guard strict: {os.getenv('PREDICATE_GUARD_STRICT_TYPES', 'NOT SET')}")
    print()

    # Verify environment
    model = os.getenv("OPENAI_EXTRACT_MODEL")
    if not model:
        print("WARNING: OPENAI_EXTRACT_MODEL not set")
    elif "gpt-4.1-mini" not in model and "gpt-4o-mini" not in model:
        print(f"WARNING: OPENAI_EXTRACT_MODEL={model} - expected gpt-4.1-mini or gpt-4o-mini")

    # Initialize database connection
    conn = get_db_connection()

    if identify_only:
        # Step 1: Identify affected docs
        print("Identifying affected documents...")
        affected_rids = identify_affected_docs(conn)
        print(f"Found {len(affected_rids)} documents with 0 entities since {CUTOFF_DATE}")

        # Save to file
        BACKFILL_RIDS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(BACKFILL_RIDS_FILE, 'w') as f:
            f.write(f"# Backfill RIDs - Generated {datetime.now().isoformat()}\n")
            f.write(f"# Documents with 0 entities ingested after {CUTOFF_DATE}\n")
            f.write(f"# Total: {len(affected_rids)}\n")
            for rid in affected_rids:
                f.write(f"{rid}\n")

        print(f"Saved to: {BACKFILL_RIDS_FILE}")
        conn.close()
        return

    # Load target RIDs
    if not BACKFILL_RIDS_FILE.exists():
        print(f"ERROR: {BACKFILL_RIDS_FILE} not found")
        print("Run with --identify first to generate the list")
        conn.close()
        return

    target_rids = load_target_rids(BACKFILL_RIDS_FILE)
    if max_docs:
        target_rids = target_rids[:max_docs]
    print(f"Target documents: {len(target_rids)}")
    print(f"Dry run: {dry_run}")
    print(f"Rate limit: {rate_limit}s between docs")
    print()

    # Fetch documents
    print("Fetching documents from database...")
    docs = fetch_documents(conn, target_rids)
    print(f"Fetched {len(docs)} documents")

    if not docs:
        print("No documents found!")
        conn.close()
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
        title = doc.get('title', '')[:40] or doc['rid'][:40]
        print(f"[{i}/{len(docs)}] {title}...", end=" ", flush=True)

        result = await process_document(extractor, kg, doc, RUN_ID, dry_run)
        results.append(result)

        if result["status"] == "error":
            print(f"ERROR: {result.get('error', 'unknown')[:50]}")
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
    print(f"Total entities {'extracted' if dry_run else 'persisted'}: {total_entities}")
    print(f"Total relationships {'extracted' if dry_run else 'persisted'}: {total_relationships}")

    if len(docs) > 0:
        rels_per_doc = total_relationships / len(docs)
        print(f"Relationships per doc: {rels_per_doc:.2f}")

        # Check against thresholds
        if rels_per_doc < 0.3:
            print("⚠️  WARNING: rels/doc < 0.3 - below stop threshold!")
        elif rels_per_doc < 0.4:
            print("⚠️  WARNING: rels/doc < 0.4 - below success threshold")
        else:
            print("✅ rels/doc >= 0.4 - meets success threshold")

        error_rate = errors / len(docs)
        if error_rate > 0.05:
            print(f"⚠️  WARNING: error rate {error_rate:.1%} > 5% - above stop threshold!")
        else:
            print(f"✅ Error rate {error_rate:.1%} - within acceptable range")

    # Save results
    output_file = RESULTS_DIR / f"backfill_results_{RUN_ID}.json"
    with open(output_file, 'w') as f:
        json.dump({
            "run_id": RUN_ID,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "dry_run": dry_run,
            "cutoff_date": CUTOFF_DATE,
            "model": os.getenv("OPENAI_EXTRACT_MODEL"),
            "docs_processed": len(docs),
            "total_entities": total_entities,
            "total_relationships": total_relationships,
            "rels_per_doc": total_relationships / len(docs) if len(docs) > 0 else 0,
            "errors": errors,
            "results": results,
        }, f, indent=2, default=str)
    print(f"\nResults saved to: {output_file}")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill Broken Extractions")
    parser.add_argument("--identify", action="store_true",
                       help="Only identify affected docs and save to file")
    parser.add_argument("--dry-run", action="store_true",
                       help="Extract but don't write to database")
    parser.add_argument("--max-docs", type=int,
                       help="Maximum documents to process")
    parser.add_argument("--rate-limit", type=float, default=0.5,
                       help="Delay between docs (seconds)")

    args = parser.parse_args()

    asyncio.run(main(
        identify_only=args.identify,
        dry_run=args.dry_run,
        max_docs=args.max_docs,
        rate_limit=args.rate_limit,
    ))
