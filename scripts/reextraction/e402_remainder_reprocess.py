#!/usr/bin/env python3
"""
E402 Remainder Reprocess - Phase 1

Reprocesses the remaining ~890 docs from the E402 broader batch
to apply platform predicate improvements.

Uses the same extraction and persistence logic as week15_targeted_reprocess.py.

Usage:
    cd /opt/projects/koi-processor
    set -a; source .env; set +a

    # Step 1: Check remaining docs (if batch file exists)
    PYTHONPATH=src ./.venv/bin/python scripts/reextraction/e402_remainder_reprocess.py --check

    # Step 2: Regenerate batch if needed
    PYTHONPATH=src ./.venv/bin/python scripts/reextraction/e402_remainder_reprocess.py --regenerate

    # Step 3: Dry run
    PYTHONPATH=src ./.venv/bin/python scripts/reextraction/e402_remainder_reprocess.py --dry-run

    # Step 4: Full reprocess (with limit for safety)
    PYTHONPATH=src ./.venv/bin/python scripts/reextraction/e402_remainder_reprocess.py --max-docs 100

    # Step 5: Full reprocess all remaining
    PYTHONPATH=src ./.venv/bin/python scripts/reextraction/e402_remainder_reprocess.py

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
from typing import Dict, Any, Optional, List, Set

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import psycopg2
from psycopg2.extras import RealDictCursor

from extraction.openai_extractor import OpenAIExtractor
from knowledge_graph.graph_integration import KnowledgeGraphIntegrator, normalize_predicate

# File paths
E402_BROADER_BATCH = Path(__file__).parent.parent.parent / "data" / "e402_broader_batch.txt"
E402_PROCESSED_LOG = Path(__file__).parent.parent.parent / "data" / "e402_processed_rids.txt"
RESULTS_DIR = Path(__file__).parent

# Target platforms for E402
TARGET_PLATFORMS = ["notion", "discord", "telegram", "koi", "slack", "github", "medium"]

# Run ID for this batch
RUN_ID = f"e402_remainder_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"


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


def load_processed_rids() -> Set[str]:
    """Load set of already-processed RIDs."""
    if not E402_PROCESSED_LOG.exists():
        return set()

    processed = set()
    with open(E402_PROCESSED_LOG, 'r') as f:
        for line in f:
            rid = line.strip()
            if rid and not rid.startswith('#'):
                processed.add(rid)
    return processed


def save_processed_rid(rid: str):
    """Append a processed RID to the log."""
    E402_PROCESSED_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(E402_PROCESSED_LOG, 'a') as f:
        f.write(f"{rid}\n")


def load_target_rids(file_path: Path) -> List[str]:
    """Load target document RIDs from file."""
    if not file_path.exists():
        return []

    rids = []
    with open(file_path, 'r') as f:
        for line in f:
            rid = line.strip()
            if rid and not rid.startswith('#'):
                rids.append(rid)

    return rids


def regenerate_batch(conn) -> List[str]:
    """
    Regenerate the E402 batch by finding docs that mention platform entities.

    Criteria:
    - Public docs only
    - Contains platform mentions (notion, discord, telegram, etc.)
    - Not already in koi_relationships for these platforms
    """
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Build platform pattern for ILIKE
    platform_patterns = " OR ".join([
        f"(content::text ILIKE '%{p}%')" for p in TARGET_PLATFORMS
    ])

    query = f"""
        SELECT DISTINCT m.rid
        FROM koi_memories m
        WHERE COALESCE(m.is_private, false) = false
          AND m.superseded_at IS NULL
          AND ({platform_patterns})
        ORDER BY m.rid
    """

    cursor.execute(query)
    rows = cursor.fetchall()
    cursor.close()

    return [row['rid'] for row in rows]


def check_remaining(batch_rids: List[str], processed_rids: Set[str]) -> List[str]:
    """Return RIDs that haven't been processed yet."""
    return [rid for rid in batch_rids if rid not in processed_rids]


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

            pg_cur.execute("SAVEPOINT e402_rel")
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
                pg_cur.execute("RELEASE SAVEPOINT e402_rel")
                relationships_persisted += 1
            except Exception as e:
                pg_cur.execute("ROLLBACK TO SAVEPOINT e402_rel")

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


async def main(
    check_only: bool = False,
    regenerate: bool = False,
    dry_run: bool = False,
    max_docs: int = None,
    rate_limit: float = 0.5
):
    """Main entry point."""
    print("=" * 70)
    print("PHASE 1: E402 REMAINDER REPROCESS")
    print("=" * 70)
    print(f"Run ID: {RUN_ID}")
    print(f"Model: {os.getenv('OPENAI_EXTRACT_MODEL', 'NOT SET')}")
    print(f"Predicate guard validate: {os.getenv('PREDICATE_GUARD_VALIDATE_TYPES', 'NOT SET')}")
    print(f"Predicate guard strict: {os.getenv('PREDICATE_GUARD_STRICT_TYPES', 'NOT SET')}")
    print()

    # Initialize database connection
    conn = get_db_connection()

    # Load processed RIDs
    processed_rids = load_processed_rids()
    print(f"Previously processed: {len(processed_rids)} docs")

    if regenerate:
        # Regenerate batch
        print("Regenerating E402 batch from database...")
        all_rids = regenerate_batch(conn)
        print(f"Found {len(all_rids)} docs with platform mentions")

        # Save to file
        E402_BROADER_BATCH.parent.mkdir(parents=True, exist_ok=True)
        with open(E402_BROADER_BATCH, 'w') as f:
            f.write(f"# E402 Broader Batch - Generated {datetime.now().isoformat()}\n")
            f.write(f"# Docs with platform mentions: {TARGET_PLATFORMS}\n")
            f.write(f"# Total: {len(all_rids)}\n")
            for rid in all_rids:
                f.write(f"{rid}\n")

        print(f"Saved to: {E402_BROADER_BATCH}")
        conn.close()
        return

    # Load batch
    if not E402_BROADER_BATCH.exists():
        print(f"ERROR: {E402_BROADER_BATCH} not found")
        print("Run with --regenerate to create the batch file")
        conn.close()
        return

    batch_rids = load_target_rids(E402_BROADER_BATCH)
    remaining_rids = check_remaining(batch_rids, processed_rids)

    print(f"Total in batch: {len(batch_rids)}")
    print(f"Already processed: {len(processed_rids)}")
    print(f"Remaining: {len(remaining_rids)}")

    if check_only:
        conn.close()
        return

    if not remaining_rids:
        print("All docs have been processed!")
        conn.close()
        return

    # Apply limit
    target_rids = remaining_rids
    if max_docs:
        target_rids = remaining_rids[:max_docs]

    print(f"\nProcessing: {len(target_rids)} docs")
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

            # Log as processed (unless dry run)
            if not dry_run:
                save_processed_rid(doc["rid"])

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

    # Update remaining count
    remaining_after = len(remaining_rids) - len(docs)
    print(f"\nRemaining after this run: {remaining_after}")

    # Save results
    output_file = RESULTS_DIR / f"e402_remainder_results_{RUN_ID}.json"
    with open(output_file, 'w') as f:
        json.dump({
            "run_id": RUN_ID,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "dry_run": dry_run,
            "model": os.getenv("OPENAI_EXTRACT_MODEL"),
            "docs_processed": len(docs),
            "total_entities": total_entities,
            "total_relationships": total_relationships,
            "rels_per_doc": total_relationships / len(docs) if len(docs) > 0 else 0,
            "errors": errors,
            "remaining_after": remaining_after,
            "results": results,
        }, f, indent=2, default=str)
    print(f"Results saved to: {output_file}")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="E402 Remainder Reprocess")
    parser.add_argument("--check", action="store_true",
                       help="Only check remaining docs count")
    parser.add_argument("--regenerate", action="store_true",
                       help="Regenerate the batch file from database")
    parser.add_argument("--dry-run", action="store_true",
                       help="Extract but don't write to database")
    parser.add_argument("--max-docs", type=int,
                       help="Maximum documents to process")
    parser.add_argument("--rate-limit", type=float, default=0.5,
                       help="Delay between docs (seconds)")

    args = parser.parse_args()

    asyncio.run(main(
        check_only=args.check,
        regenerate=args.regenerate,
        dry_run=args.dry_run,
        max_docs=args.max_docs,
        rate_limit=args.rate_limit,
    ))
