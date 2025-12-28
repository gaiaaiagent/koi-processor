#!/usr/bin/env python3
"""
Stage 6 Full Re-Extraction Script — Rebuild KG from full KOI corpus.

This script performs a complete re-extraction of the knowledge graph:
1. Iterates through all koi_memories documents (stable order by id)
2. Extracts with GeminiExtractor.extract_metadata()
3. Post-processes with production pipeline (pipeline_config.json)
4. Persists to PostgreSQL:
   - entity_registry (nodes) via EntityResolver
   - koi_relationships (edges) via lookup-only upsert
   - koi_kg_extractions (provenance) for each document
5. Supports checkpointing for resumable runs

Usage:
    cd /opt/projects/koi-processor

    # Start fresh run
    PYTHONPATH=src ./.venv/bin/python scripts/reextraction/stage6_full_reextract_gemini.py

    # Resume from checkpoint
    PYTHONPATH=src ./.venv/bin/python scripts/reextraction/stage6_full_reextract_gemini.py --resume

    # Custom batch size
    PYTHONPATH=src ./.venv/bin/python scripts/reextraction/stage6_full_reextract_gemini.py --batch-size 100

    # Dry run (no writes)
    PYTHONPATH=src ./.venv/bin/python scripts/reextraction/stage6_full_reextract_gemini.py --dry-run

Environment (required):
    GEMINI_API_KEY          - Gemini API key
    POSTGRES_*              - PostgreSQL connection vars

Environment (MUST BE UNSET for Stage 6):
    OPENAI_API_KEY          - UNSET this to disable Tier-2 semantic matching

Checkpoint File:
    scripts/reextraction/.stage6_checkpoint.json
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
from psycopg2 import IntegrityError

from extraction.gemini_extractor import GeminiExtractor
from extraction.predicate_guard import validate_predicate
from knowledge_graph.graph_integration import KnowledgeGraphIntegrator, normalize_predicate

# Checkpoint file path
CHECKPOINT_FILE = Path(__file__).parent / ".stage6_checkpoint.json"

# Stage 6 corpus filter: natural-language KG only
# - Include all non-repo sources (discourse/notion/website/youtube/etc.)
# - Repo sources (GitHub/GitLab): include ONLY documentation files by file_path
# - Explicitly EXCLUDE file_path IS NULL rows for repo sources (issues/PRs/discussions can be a later pass)
CORPUS_FILTER_SQL = r"""
  AND (
    -- Non-repo sources: include all
    (source_sensor NOT ILIKE '%%github%%' AND source_sensor NOT ILIKE '%%gitlab%%')
    OR
    -- Repo sources: docs-only by file_path
    (
      (source_sensor ILIKE '%%github%%' OR source_sensor ILIKE '%%gitlab%%')
      AND (metadata ? 'file_path')
      AND (metadata->>'file_path') IS NOT NULL
      AND (
        (metadata->>'file_path') ~* '[.](md|mdx|rst|txt)$'
        OR (metadata->>'file_path') ~* '(^|/)(readme|license|changelog)([.].*)?$'
        OR (metadata->>'file_path') ILIKE '%%/docs/%%'
      )
      -- Exclude generated/vendor/build outputs
      AND (metadata->>'file_path') NOT ILIKE '%%.pb.go'
      AND (metadata->>'file_path') !~* '/(node_modules|vendor|dist|build|generated)/'
      -- Optional noise reduction: exclude tests/examples paths
      AND (metadata->>'file_path') !~* '/(test|tests|examples)/'
      AND (metadata->>'file_path') !~* '_test[.][^/]+$'
    )
  )
"""


def infer_source_type(source_sensor: str) -> str:
    """Infer source_type from source_sensor string."""
    s = (source_sensor or "").lower()
    if "discourse" in s:
        return "discourse"
    if "github" in s:
        return "github"
    if "gitlab" in s:
        return "github"
    if "medium" in s:
        return "medium"
    if "twitter" in s:
        return "twitter"
    return "website"


def load_checkpoint() -> Optional[Dict[str, Any]]:
    """Load checkpoint from file if exists."""
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE) as f:
            return json.load(f)
    return None


def save_checkpoint(checkpoint: Dict[str, Any]):
    """Save checkpoint to file."""
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(checkpoint, f, indent=2)


def entity_to_dict(e) -> Dict[str, Any]:
    """Convert pipeline Entity to serializable dict."""
    return {
        "name": e.name,
        "type": e.type,
        "confidence": e.confidence,
        "metadata": getattr(e, "metadata", {}) or {},
    }


def relationship_to_dict(r, normalized_pred: str) -> Dict[str, Any]:
    """Convert pipeline Relationship to serializable dict."""
    return {
        "subject": r.source,
        "predicate": normalized_pred,
        "object": r.target,
        "confidence": r.confidence,
        "metadata": getattr(r, "metadata", {}) or {},
    }


async def process_document(
    doc: Dict[str, Any],
    extractor: GeminiExtractor,
    kg: KnowledgeGraphIntegrator,
    run_id: str,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Process a single document through the Stage 6 pipeline.

    Returns:
        Dict with processing stats
    """
    source_type = infer_source_type(doc["source_sensor"])

    # Step 1: Extract with Gemini
    extraction = await extractor.extract_metadata(
        doc["text"],
        source_type,
        existing_metadata={"rid": doc["rid"]},
    )

    raw_entities = extraction.get("extracted_entities", [])
    raw_relationships = extraction.get("extracted_relationships", [])
    tokens = extraction.get("token_usage", {}).get("total_tokens", 0)

    # Step 2: Run pipeline
    context = kg.pipeline.process_entities(
        raw_entities,
        raw_relationships,
        metadata={"memory_rid": doc["rid"], "run_id": run_id, "source_type": source_type},
    )

    passed_entities = context.entities
    passed_rels = context.relationships
    blocked_entities = context.blocked_entities

    if dry_run:
        return {
            "raw_entities": len(raw_entities),
            "raw_relationships": len(raw_relationships),
            "passed_entities": len(passed_entities),
            "passed_relationships": len(passed_rels),
            "blocked_entities": len(blocked_entities),
            "tokens": tokens,
            "entities_persisted": 0,
            "relationships_persisted": 0,
        }

    # Step 3: Persist entities (increments occurrence_count)
    entities_persisted = 0
    # Stage 6 rule: at most once per (name,type) per document.
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

    # Step 4: Persist relationships via lookup-only (NO increment)
    relationships_persisted = 0
    relationships_for_provenance = []

    with kg.pg_conn.cursor() as pg_cur:
        for r in passed_rels:
            # Normalize predicate: first snake_case, then canonical mapping
            pred = normalize_predicate(r.predicate)
            if not pred:
                continue
            # Apply predicate guard mapping (e.g., exploring -> discusses)
            pred, is_canonical = validate_predicate(pred)
            if not pred:
                continue

            # Lookup subject and object (no increment)
            subj = kg._find_existing_entity_by_name(r.source)
            obj = kg._find_existing_entity_by_name(r.target)

            if not subj or not obj:
                continue

            # Skip self-relationships
            if subj.entity_id == obj.entity_id:
                continue

            # Use a savepoint so a single bad relationship doesn't wipe earlier inserts.
            pg_cur.execute("SAVEPOINT stage6_rel")
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
                      confidence = COALESCE(
                        GREATEST(koi_relationships.confidence, EXCLUDED.confidence),
                        koi_relationships.confidence,
                        EXCLUDED.confidence
                      )
                    """,
                    (subj.entity_id, pred, obj.entity_id, r.confidence, doc["rid"], run_id),
                )
                relationships_persisted += 1
                relationships_for_provenance.append(relationship_to_dict(r, pred))
                pg_cur.execute("RELEASE SAVEPOINT stage6_rel")
            except IntegrityError:
                # Handle constraint violations (predicate format, etc.) without losing prior inserts
                pg_cur.execute("ROLLBACK TO SAVEPOINT stage6_rel")
                pg_cur.execute("RELEASE SAVEPOINT stage6_rel")
                continue

    kg.pg_conn.commit()

    # Step 5: Insert provenance record into koi_kg_extractions
    extraction_rid = f"{doc['rid']}:kg:passA:stage6:{run_id}"
    entities_json = [entity_to_dict(e) for e in passed_entities]

    with kg.pg_conn.cursor() as pg_cur:
        try:
            pg_cur.execute(
                """
                INSERT INTO koi_kg_extractions
                  (memory_rid, extraction_rid, extraction_type, entities, relations,
                   tokens_consumed, cost_usd, extractor_version)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (extraction_rid) DO UPDATE SET
                  entities = EXCLUDED.entities,
                  relations = EXCLUDED.relations,
                  tokens_consumed = EXCLUDED.tokens_consumed,
                  updated_at = NOW()
                """,
                (
                    doc["rid"],
                    extraction_rid,
                    "passA",
                    json.dumps(entities_json),
                    json.dumps(relationships_for_provenance),
                    tokens,
                    0,  # cost_usd - not computed
                    "stage6-gemini",
                ),
            )
        except Exception as e:
            # Log but don't fail on provenance insert errors
            print(f"  [WARN] Failed to insert provenance: {e}")
            kg.pg_conn.rollback()

    kg.pg_conn.commit()

    return {
        "raw_entities": len(raw_entities),
        "raw_relationships": len(raw_relationships),
        "passed_entities": len(passed_entities),
        "passed_relationships": len(passed_rels),
        "blocked_entities": len(blocked_entities),
        "tokens": tokens,
        "entities_persisted": entities_persisted,
        "relationships_persisted": relationships_persisted,
    }


async def main(
    batch_size: int = 50,
    resume: bool = False,
    dry_run: bool = False,
    max_docs: Optional[int] = None,
    rate_limit_delay: float = 0.5,
):
    """Run full re-extraction."""
    # Check for OPENAI_API_KEY
    if os.getenv("OPENAI_API_KEY"):
        print("[WARNING] OPENAI_API_KEY is set. Tier-2 semantic matching is ENABLED.")
        print("[WARNING] For Stage 6, you should `unset OPENAI_API_KEY` to avoid OpenAI calls.")
        response = input("Continue anyway? [y/N]: ")
        if response.lower() != "y":
            print("Aborted.")
            return

    # Load or create checkpoint
    checkpoint = None
    if resume:
        checkpoint = load_checkpoint()
        if checkpoint:
            print(f"[stage6] Resuming from checkpoint: run_id={checkpoint['run_id']} last_id={checkpoint['last_koi_memories_id']}")
        else:
            print("[stage6] No checkpoint found, starting fresh")

    if checkpoint is None:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        checkpoint = {
            "run_id": run_id,
            "last_koi_memories_id": None,  # UUID column - use None for first run
            "processed_count": 0,
            "error_count": 0,
            "total_entities": 0,
            "total_relationships": 0,
            "total_tokens": 0,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
    else:
        run_id = checkpoint["run_id"]

    print(f"[stage6] Starting run_id={run_id} batch_size={batch_size} dry_run={dry_run}")

    # Initialize extractor
    extractor = GeminiExtractor()
    print(f"[stage6] GeminiExtractor initialized (model={os.getenv('GEMINI_MODEL', 'gemini-3-flash-preview')})")

    # Initialize KnowledgeGraphIntegrator
    kg = KnowledgeGraphIntegrator(
        store_type="memory",
        use_pipeline=True,
        enable_deduplication=True
    )
    pipeline_modules = getattr(kg.pipeline, "modules", None)
    pipeline_len = len(pipeline_modules) if pipeline_modules is not None else 0
    print(f"[stage6] KnowledgeGraphIntegrator initialized (pipeline modules: {pipeline_len})")

    if not kg.pipeline or not kg.entity_resolver:
        print("[ERROR] Pipeline or EntityResolver not initialized")
        return

    # Connect to PostgreSQL
    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5433)),
        database=os.getenv("POSTGRES_DB", "eliza"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )

    # Get total document count
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT COUNT(*) FROM koi_memories
            WHERE superseded_at IS NULL
              AND content->>'text' IS NOT NULL
              AND LENGTH(content->>'text') > 50
              {CORPUS_FILTER_SQL}
        """)
        total_docs = cur.fetchone()[0]

    print(f"[stage6] Total documents to process: {total_docs}")

    if max_docs:
        print(f"[stage6] Limiting to {max_docs} documents")

    batch_num = 0
    start_time = time.time()

    try:
        while True:
            batch_num += 1

            # Fetch next batch (ordered by id for stable iteration)
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                limit = min(batch_size, max_docs - checkpoint["processed_count"]) if max_docs else batch_size
                if limit <= 0:
                    break

                # Handle first run (None) vs resume (UUID)
                last_id = checkpoint["last_koi_memories_id"]
                if last_id is None:
                    cur.execute(
                        f"""
                        SELECT
                          id,
                          rid,
                          source_sensor,
                          metadata->>'file_path' AS file_path,
                          content->>'text' AS text
                        FROM koi_memories
                        WHERE superseded_at IS NULL
                          AND content->>'text' IS NOT NULL
                          AND LENGTH(content->>'text') > 50
                          {CORPUS_FILTER_SQL}
                        ORDER BY id ASC
                        LIMIT %s
                        """,
                        (limit,),
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT
                          id,
                          rid,
                          source_sensor,
                          metadata->>'file_path' AS file_path,
                          content->>'text' AS text
                        FROM koi_memories
                        WHERE superseded_at IS NULL
                          AND content->>'text' IS NOT NULL
                          AND LENGTH(content->>'text') > 50
                          AND id > %s
                          {CORPUS_FILTER_SQL}
                        ORDER BY id ASC
                        LIMIT %s
                        """,
                        (last_id, limit),
                    )
                docs = cur.fetchall()

            if not docs:
                print("[stage6] No more documents to process")
                break

            print(f"\n[stage6] Batch {batch_num}: processing {len(docs)} docs (ids {docs[0]['id']}-{docs[-1]['id']})")

            for doc in docs:
                try:
                    stats = await process_document(doc, extractor, kg, run_id, dry_run)

                    checkpoint["last_koi_memories_id"] = str(doc["id"])  # Convert UUID to string for JSON
                    checkpoint["processed_count"] += 1
                    checkpoint["total_entities"] += stats["entities_persisted"]
                    checkpoint["total_relationships"] += stats["relationships_persisted"]
                    checkpoint["total_tokens"] += stats["tokens"]

                    # Progress log every 10 docs
                    if checkpoint["processed_count"] % 10 == 0:
                        elapsed = time.time() - start_time
                        rate = checkpoint["processed_count"] / elapsed if elapsed > 0 else 0
                        eta = (total_docs - checkpoint["processed_count"]) / rate if rate > 0 else 0
                        print(f"  [{checkpoint['processed_count']}/{total_docs}] "
                              f"e={stats['passed_entities']} r={stats['relationships_persisted']} "
                              f"rate={rate:.1f}/s ETA={eta/3600:.1f}h")

                    # Rate limiting for Gemini API
                    await asyncio.sleep(rate_limit_delay)

                except Exception as e:
                    checkpoint["error_count"] += 1
                    checkpoint["last_koi_memories_id"] = str(doc["id"])  # Convert UUID to string for JSON
                    file_path = (doc.get("file_path") or "")[:120]
                    print(f"  [ERROR] doc_id={doc['id']} rid={doc['rid'][:40]} file_path={file_path}: {e}")

                    # Save checkpoint on error
                    if not dry_run:
                        save_checkpoint(checkpoint)

                    # Continue to next doc
                    continue

            # Save checkpoint after each batch
            if not dry_run:
                save_checkpoint(checkpoint)
                print(f"  Checkpoint saved: processed={checkpoint['processed_count']} errors={checkpoint['error_count']}")

            # Check max_docs limit
            if max_docs and checkpoint["processed_count"] >= max_docs:
                print(f"[stage6] Reached max_docs limit ({max_docs})")
                break

    except KeyboardInterrupt:
        print("\n[stage6] Interrupted by user")
        if not dry_run:
            save_checkpoint(checkpoint)
            print(f"[stage6] Checkpoint saved. Resume with --resume")

    finally:
        conn.close()

    # Final summary
    elapsed = time.time() - start_time
    checkpoint["completed_at"] = datetime.now(timezone.utc).isoformat()
    checkpoint["elapsed_seconds"] = elapsed

    if not dry_run:
        save_checkpoint(checkpoint)

    print("\n" + "=" * 60)
    print("[stage6] Final Summary")
    print("=" * 60)
    print(f"  Run ID: {run_id}")
    print(f"  Documents processed: {checkpoint['processed_count']}")
    print(f"  Errors: {checkpoint['error_count']}")
    print(f"  Entities created/updated: {checkpoint['total_entities']}")
    print(f"  Relationships created/updated: {checkpoint['total_relationships']}")
    print(f"  Total tokens: {checkpoint['total_tokens']}")
    print(f"  Elapsed time: {elapsed/3600:.2f} hours")
    print(f"  Rate: {checkpoint['processed_count']/elapsed:.2f} docs/sec" if elapsed > 0 else "")

    # Log entity resolver stats
    kg.log_entity_stats()

    if not dry_run:
        print(f"\n[stage6] Checkpoint saved to: {CHECKPOINT_FILE}")
        print("[stage6] To resume: python stage6_full_reextract_gemini.py --resume")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stage 6 Full Re-Extraction")
    parser.add_argument("--batch-size", type=int, default=50, help="Documents per batch")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to database")
    parser.add_argument("--max-docs", type=int, help="Maximum documents to process")
    parser.add_argument("--rate-limit", type=float, default=0.5, help="Delay between docs (seconds)")

    args = parser.parse_args()

    asyncio.run(main(
        batch_size=args.batch_size,
        resume=args.resume,
        dry_run=args.dry_run,
        max_docs=args.max_docs,
        rate_limit_delay=args.rate_limit,
    ))
