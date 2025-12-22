#!/usr/bin/env python3
"""
Stage 6 Canary Script — Validate extraction pipeline on 5 random documents.

This script tests the full Stage 6 extraction flow:
1. Fetch 5 random documents from koi_memories
2. Extract with GeminiExtractor.extract_metadata()
3. Post-process with production pipeline (from pipeline_config.json)
4. Persist entities via EntityResolver.get_or_create_entity()
5. Persist relationships via lookup-only (no double-counting occurrence_count)

Usage:
    cd /opt/projects/koi-processor
    PYTHONPATH=src ./.venv/bin/python scripts/reextraction/stage6_canary_gemini.py 5

Environment (required):
    GEMINI_API_KEY          - Gemini API key
    POSTGRES_HOST           - PostgreSQL host (default: localhost)
    POSTGRES_PORT           - PostgreSQL port (default: 5433)
    POSTGRES_DB             - Database name (default: eliza)
    POSTGRES_USER           - Database user (default: postgres)
    POSTGRES_PASSWORD       - Database password (default: postgres)

Environment (optional, UNSET for Stage 6):
    OPENAI_API_KEY          - UNSET this to disable Tier-2 semantic matching

Canary Pass Criteria:
    - All docs processed without exceptions
    - SELECT COUNT(*) FROM entity_registry WHERE fuseki_uri LIKE 'http://%' = 0
    - SELECT COUNT(*) FROM koi_relationships > 0
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import IntegrityError

from extraction.gemini_extractor import GeminiExtractor
from knowledge_graph.graph_integration import KnowledgeGraphIntegrator, normalize_predicate


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

# Stage 6 corpus filter: natural-language KG only
# - Include all non-repo sources
# - Repo sources (GitHub/GitLab): include ONLY documentation files by file_path
# - Exclude file_path IS NULL rows for repo sources
CORPUS_FILTER_SQL = r"""
  AND (
    (source_sensor NOT ILIKE '%%github%%' AND source_sensor NOT ILIKE '%%gitlab%%')
    OR
    (
      (source_sensor ILIKE '%%github%%' OR source_sensor ILIKE '%%gitlab%%')
      AND (metadata ? 'file_path')
      AND (metadata->>'file_path') IS NOT NULL
      AND (
        (metadata->>'file_path') ~* '[.](md|mdx|rst|txt)$'
        OR (metadata->>'file_path') ~* '(^|/)(readme|license|changelog)([.].*)?$'
        OR (metadata->>'file_path') ILIKE '%%/docs/%%'
      )
      AND (metadata->>'file_path') NOT ILIKE '%%.pb.go'
      AND (metadata->>'file_path') !~* '/(node_modules|vendor|dist|build|generated)/'
      AND (metadata->>'file_path') !~* '/(test|tests|examples)/'
      AND (metadata->>'file_path') !~* '_test[.][^/]+$'
    )
  )
"""


async def main(limit: int = 5):
    """Run canary extraction on N random documents."""
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    print(f"[canary] Starting Stage 6 canary run_id={run_id} limit={limit}")

    # Warn if OPENAI_API_KEY is set (Tier-2 semantic matching would be enabled)
    if os.getenv("OPENAI_API_KEY"):
        print("[WARNING] OPENAI_API_KEY is set. Tier-2 semantic matching is ENABLED.")
        print("[WARNING] For Stage 6, you should `unset OPENAI_API_KEY` to avoid OpenAI calls.")

    # Initialize extractor
    extractor = GeminiExtractor()
    print(f"[canary] GeminiExtractor initialized (model={os.getenv('GEMINI_MODEL', 'gemini-3-flash-preview')})")

    # Initialize KnowledgeGraphIntegrator (gets us the pipeline + entity_resolver + pg_conn)
    # use_pipeline=True loads from pipeline_config.json
    # enable_deduplication=True gives us EntityResolver
    kg = KnowledgeGraphIntegrator(
        store_type="memory",
        use_pipeline=True,
        enable_deduplication=True
    )
    pipeline_modules = getattr(kg.pipeline, "modules", None)
    pipeline_len = len(pipeline_modules) if pipeline_modules is not None else 0
    print(f"[canary] KnowledgeGraphIntegrator initialized (pipeline modules: {pipeline_len})")

    # Verify pipeline is loaded
    if not kg.pipeline:
        print("[ERROR] Pipeline not initialized")
        return

    # Verify entity_resolver is available
    if not kg.entity_resolver:
        print("[ERROR] EntityResolver not initialized")
        return

    # Connect to PostgreSQL for document fetch
    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5433)),
        database=os.getenv("POSTGRES_DB", "eliza"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )
    print(f"[canary] Connected to PostgreSQL")

    # Fetch random documents
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
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
              AND LENGTH(content->>'text') > 200
              {CORPUS_FILTER_SQL}
            ORDER BY RANDOM()
            LIMIT %s
            """,
            (limit,),
        )
        docs = cur.fetchall()

    print(f"[canary] Fetched {len(docs)} documents")

    ok = 0
    entity_count = 0
    relationship_count = 0
    blocked_count = 0

    for i, doc in enumerate(docs, 1):
        fp = (doc.get("file_path") or "").strip()
        print(f"\n[canary] [{i}/{len(docs)}] Processing rid={doc['rid'][:50]} file_path={fp[:120]}...")

        try:
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

            print(f"  Gemini: entities={len(raw_entities)} rels={len(raw_relationships)} tokens={tokens}")

            # Step 2: Run pipeline
            context = kg.pipeline.process_entities(
                raw_entities,
                raw_relationships,
                metadata={"memory_rid": doc["rid"], "run_id": run_id, "source_type": source_type},
            )

            passed_entities = context.entities
            passed_rels = context.relationships
            blocked_entities = context.blocked_entities

            print(f"  Pipeline: passed_e={len(passed_entities)} passed_r={len(passed_rels)} blocked_e={len(blocked_entities)}")

            # Step 3: Persist entities (increments occurrence_count)
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
                entity_count += 1

            # Step 4: Persist relationships via lookup-only (NO increment)
            rels_inserted = 0
            with kg.pg_conn.cursor() as pg_cur:
                for r in passed_rels:
                    # Normalize predicate
                    pred = normalize_predicate(r.predicate)
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
                        rels_inserted += 1
                        pg_cur.execute("RELEASE SAVEPOINT stage6_rel")
                    except IntegrityError as e:
                        # Handle constraint violations (predicate format, etc.) without losing prior inserts
                        pg_cur.execute("ROLLBACK TO SAVEPOINT stage6_rel")
                        pg_cur.execute("RELEASE SAVEPOINT stage6_rel")
                        print(f"  [WARN] Relationship skipped due to constraint: {e}")
                        continue

            kg.pg_conn.commit()
            relationship_count += rels_inserted
            blocked_count += len(blocked_entities)

            print(f"  Persisted: entities={len(passed_entities)} rels={rels_inserted}")
            ok += 1

        except Exception as e:
            print(f"  [ERROR] Failed: {e}")
            import traceback
            traceback.print_exc()

    conn.close()

    # Log entity resolver stats
    print("\n" + "=" * 60)
    kg.log_entity_stats()

    # Validation queries
    print("\n" + "=" * 60)
    print("[canary] Validation Queries:")

    with kg.pg_conn.cursor() as cur:
        # Check for HTTP URIs (should be 0)
        cur.execute("SELECT COUNT(*) FROM entity_registry WHERE fuseki_uri LIKE 'http://%'")
        http_count = cur.fetchone()[0]
        print(f"  HTTP URIs in entity_registry: {http_count} {'[PASS]' if http_count == 0 else '[FAIL]'}")

        # Check relationship count
        cur.execute("SELECT COUNT(*) FROM koi_relationships")
        rel_count = cur.fetchone()[0]
        print(f"  Total relationships: {rel_count} {'[PASS]' if rel_count > 0 else '[FAIL]'}")

        # Entity count
        cur.execute("SELECT COUNT(*) FROM entity_registry")
        ent_count = cur.fetchone()[0]
        print(f"  Total entities: {ent_count}")

        # Type distribution
        cur.execute("""
            SELECT entity_type, COUNT(*) AS count
            FROM entity_registry
            GROUP BY entity_type
            ORDER BY count DESC
            LIMIT 10
        """)
        print("  Type distribution (top 10):")
        for row in cur.fetchall():
            print(f"    {row[0]}: {row[1]}")

    print("\n" + "=" * 60)
    print(f"[canary] Summary: ok={ok}/{len(docs)} entities={entity_count} rels={relationship_count} blocked={blocked_count} run_id={run_id}")

    if ok == len(docs) and http_count == 0:
        print("[canary] CANARY PASSED - Ready for full extraction")
    else:
        print("[canary] CANARY FAILED - Review errors above")


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    asyncio.run(main(limit))
