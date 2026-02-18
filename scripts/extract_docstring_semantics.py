#!/usr/bin/env python3
"""
Extract semantic entities from code docstrings via LLM.

This script bridges the gap between the structural code graph (tree-sitter/AGE)
and the semantic knowledge graph (koi_kg_extractions/entity_registry). Docstrings
contain domain knowledge — references to standards, protocols, architectural
concepts — that the semantic KG never sees because the LLM extraction pipeline
only processes documentation files.

Pipeline:
1. Walk repo files (same extension filter as load_to_staging.py)
2. TreeSitterExtractor.extract() per file
3. Filter with is_meaningful_docstring()
4. Aggregate into batches (3000 char cap)
5. Check idempotency via koi_code_docstring_extractions
6. OpenAIExtractor.extract_metadata(batch, "code_docstring")
7. Quality pipeline (ConfidenceFilter → CanonicalResolver → EntityQualityFilter →
   ListSplitter → OntologyNormalizer)
8. Create shadow koi_memories row (source_sensor='code_docstring')
9. Store in koi_kg_extractions (extraction_type='passA')
10. Store provenance in koi_code_docstring_extractions
11. EntityResolver.resolve() for deduplication into entity_registry

Usage:
    cd /opt/projects/koi-processor

    # Dry run on koi-processor's own Python files
    PYTHONPATH=src ./.venv/bin/python scripts/extract_docstring_semantics.py \\
        --repo koi-processor --path . --dry-run

    # Full run on regen-ledger
    PYTHONPATH=src ./.venv/bin/python scripts/extract_docstring_semantics.py \\
        --repo regen-ledger --path /opt/repos/regen-ledger

    # Single file
    PYTHONPATH=src ./.venv/bin/python scripts/extract_docstring_semantics.py \\
        --repo regen-ledger --path /opt/repos/regen-ledger \\
        --file-filter x/ecocredit/server/msg_server.go

    # Force re-extraction (bypass idempotency)
    PYTHONPATH=src ./.venv/bin/python scripts/extract_docstring_semantics.py \\
        --repo regen-ledger --path /opt/repos/regen-ledger --force

Environment:
    OPENAI_API_KEY          - Required
    OPENAI_EXTRACT_MODEL    - Optional (default: gpt-4o-mini)
    POSTGRES_HOST/PORT/DB/USER/PASSWORD - PostgreSQL connection
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from uuid import uuid4

# Load environment variables
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / '.env')

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import psycopg2
from psycopg2.extras import RealDictCursor, Json

from core.tree_sitter_extractor import TreeSitterExtractor, CodeEntity
from core.docstring_filter import is_meaningful_docstring, aggregate_docstrings_for_file
from extraction.openai_extractor import OpenAIExtractor
from extraction.predicate_guard import validate_predicate
from knowledge_graph.graph_integration import KnowledgeGraphIntegrator, normalize_predicate

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('docstring_extraction.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Matches load_to_staging.py:471
CODE_EXTENSIONS = ["*.go", "*.py", "*.ts", "*.tsx", "*.js"]

# Extension-to-language map (matches load_to_staging.py:446-454)
EXT_TO_LANGUAGE = {
    ".go": "go",
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
}

# Directories to skip (matches load_to_staging.py:476-484)
SKIP_DIRS = {"vendor", "node_modules", "venv", ".venv", ".git"}

# Prompt version — bump when prompt_builder code_docstring rules change
PROMPT_VERSION = "v1.0"

# Rate limiting
SEMAPHORE_LIMIT = 6
INTER_FILE_DELAY = 1.0  # seconds between file batches


def get_repo_files(repo_path: Path, file_filter: Optional[str] = None) -> List[Path]:
    """Collect code files from repo, matching load_to_staging.py extension filter."""
    files = []
    for ext in CODE_EXTENSIONS:
        files.extend(repo_path.glob(f"**/{ext}"))

    files = [
        f for f in files
        if not any(skip in f.parts for skip in SKIP_DIRS)
        and not str(f).endswith("_test.go")
    ]

    if file_filter:
        files = [f for f in files if file_filter in str(f)]

    return sorted(files)


def compute_file_hash(file_path: Path) -> str:
    """SHA-256 of file content."""
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def get_commit_sha(repo_path: Path) -> Optional[str]:
    """Get current git commit SHA, or None if not a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def build_extraction_rid(repo: str, file_path: str, file_hash: str, batch_index: int,
                         prompt_version: str, model: str) -> str:
    """Build deterministic RID for idempotency. Includes file_path to avoid collision."""
    # Hash file_path to keep RID length reasonable
    path_hash = hashlib.sha256(file_path.encode()).hexdigest()[:12]
    return f"code_docstring:{repo}:{path_hash}:{file_hash[:16]}:{batch_index}:{prompt_version}:{model}"


def build_memory_rid(repo: str, file_path: str, file_hash: str, batch_index: int) -> str:
    """Build deterministic RID for shadow koi_memories row. Includes file_path to avoid collision."""
    path_hash = hashlib.sha256(file_path.encode()).hexdigest()[:12]
    return f"code_docstring:{repo}:{path_hash}:{file_hash[:16]}:{batch_index}"


def check_idempotency(conn, repo: str, file_path: str, file_hash: str,
                       batch_index: int, prompt_version: str, model: str) -> bool:
    """Check if this exact extraction already exists."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM koi_code_docstring_extractions
            WHERE repo = %s AND file_path = %s AND file_hash = %s
              AND batch_index = %s AND prompt_version = %s AND model = %s
            LIMIT 1
            """,
            (repo, file_path, file_hash, batch_index, prompt_version, model),
        )
        return cur.fetchone() is not None


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


async def process_file_batch(
    batch_text: str,
    source_entities: List[CodeEntity],
    batch_index: int,
    repo: str,
    file_path_str: str,
    file_hash: str,
    commit_sha: Optional[str],
    extractor: OpenAIExtractor,
    kg: KnowledgeGraphIntegrator,
    conn,
    run_id: str,
    dry_run: bool = False,
    semaphore: Optional[asyncio.Semaphore] = None,
) -> Dict[str, Any]:
    """
    Process a single batch of docstrings through the full pipeline.

    Returns dict with processing stats.
    """
    sem = semaphore or asyncio.Semaphore(1)
    model = extractor.model
    extraction_rid = build_extraction_rid(repo, file_path_str, file_hash, batch_index, PROMPT_VERSION, model)
    memory_rid = build_memory_rid(repo, file_path_str, file_hash, batch_index)

    async with sem:
        # Step 1: LLM extraction
        extraction = await extractor.extract_metadata(batch_text, "code_docstring")

        raw_entities = extraction.get("extracted_entities", [])
        raw_relationships = extraction.get("extracted_relationships", [])
        # Note: OpenAI extractor logs token usage but doesn't return it in metadata.
        # Token count is 0 here; actual usage is visible in docstring_extraction.log.
        tokens = 0

        # Step 2: Quality pipeline
        context = kg.pipeline.process_entities(
            raw_entities,
            raw_relationships,
            metadata={
                "memory_rid": memory_rid,
                "run_id": run_id,
                "source_type": "code_docstring",
            },
        )

        passed_entities = context.entities
        passed_rels = context.relationships
        blocked_entities = context.blocked_entities

        stats = {
            "file": file_path_str,
            "batch_index": batch_index,
            "input_chars": len(batch_text),
            "source_entity_count": len(source_entities),
            "raw_entities": len(raw_entities),
            "raw_relationships": len(raw_relationships),
            "passed_entities": len(passed_entities),
            "passed_relationships": len(passed_rels),
            "blocked_entities": len(blocked_entities),
            "tokens": tokens,
            "entities_persisted": 0,
            "relationships_persisted": 0,
        }

        if dry_run:
            return stats

        # Step 3: Create shadow koi_memories row
        memory_content = json.dumps({
            "text": batch_text,
            "repo": repo,
            "file_path": file_path_str,
            "file_hash": file_hash,
        })

        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    INSERT INTO koi_memories (id, rid, cid, version, event_type, source_sensor, content, content_hash)
                    VALUES (%s, %s, %s, 1, 'NEW', 'code_docstring', %s::jsonb, %s)
                    ON CONFLICT (rid) DO UPDATE SET
                        content = EXCLUDED.content,
                        content_hash = EXCLUDED.content_hash
                    """,
                    (str(uuid4()), memory_rid, memory_rid, memory_content, file_hash),
                )
            except Exception as e:
                logger.warning(f"Failed to insert shadow koi_memories row: {e}")
                conn.rollback()
        conn.commit()

        # Step 4: Persist entities via EntityResolver
        seen_entities = set()
        for e in passed_entities:
            key = (e.name, e.type)
            if key in seen_entities:
                continue
            seen_entities.add(key)
            kg.entity_resolver.get_or_create_entity(
                e.name,
                e.type,
                metadata={
                    "doc_rid": memory_rid,
                    "run_id": run_id,
                    "source_type": "code_docstring",
                    "repo": repo,
                    "file_path": file_path_str,
                },
            )
            stats["entities_persisted"] += 1

        # Step 5: Persist relationships
        relationships_for_provenance = []
        with kg.pg_conn.cursor() as pg_cur:
            for r in passed_rels:
                pred = normalize_predicate(r.predicate)
                if not pred:
                    continue
                pred, is_canonical = validate_predicate(pred)
                if not pred:
                    continue

                subj = kg._find_existing_entity_by_name(r.source)
                obj = kg._find_existing_entity_by_name(r.target)
                if not subj or not obj:
                    continue
                if subj.entity_id == obj.entity_id:
                    continue

                pg_cur.execute("SAVEPOINT docstring_rel")
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
                        (subj.entity_id, pred, obj.entity_id, r.confidence, memory_rid, run_id),
                    )
                    stats["relationships_persisted"] += 1
                    relationships_for_provenance.append(relationship_to_dict(r, pred))
                    pg_cur.execute("RELEASE SAVEPOINT docstring_rel")
                except psycopg2.IntegrityError:
                    pg_cur.execute("ROLLBACK TO SAVEPOINT docstring_rel")
                    pg_cur.execute("RELEASE SAVEPOINT docstring_rel")
                    continue
        kg.pg_conn.commit()

        # Step 6: Store provenance in koi_kg_extractions
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
                        memory_rid,
                        extraction_rid,
                        "passA",
                        json.dumps(entities_json),
                        json.dumps(relationships_for_provenance),
                        tokens,
                        0,
                        f"docstring-{model}",
                    ),
                )
            except Exception as e:
                logger.warning(f"Failed to insert koi_kg_extractions: {e}")
                kg.pg_conn.rollback()
        kg.pg_conn.commit()

        # Step 7: Store provenance in koi_code_docstring_extractions
        source_entity_ids = [e.entity_id for e in source_entities]
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    INSERT INTO koi_code_docstring_extractions
                      (extraction_rid, repo, file_path, commit_sha, file_hash,
                       batch_index, model, prompt_version, source_entity_ids, input_chars)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (repo, file_path, file_hash, batch_index, prompt_version, model)
                    DO UPDATE SET
                      extraction_rid = EXCLUDED.extraction_rid,
                      commit_sha = EXCLUDED.commit_sha,
                      source_entity_ids = EXCLUDED.source_entity_ids,
                      input_chars = EXCLUDED.input_chars,
                      created_at = now()
                    """,
                    (
                        extraction_rid,
                        repo,
                        file_path_str,
                        commit_sha,
                        file_hash,
                        batch_index,
                        model,
                        PROMPT_VERSION,
                        json.dumps(source_entity_ids),
                        len(batch_text),
                    ),
                )
            except Exception as e:
                logger.warning(f"Failed to insert provenance: {e}")
                conn.rollback()
        conn.commit()

        return stats


async def main(
    repo: str,
    repo_path: str,
    dry_run: bool = False,
    force: bool = False,
    file_filter: Optional[str] = None,
    model: Optional[str] = None,
):
    """Run docstring semantic extraction."""
    path = Path(repo_path).resolve()
    if not path.exists():
        logger.error(f"Repository path does not exist: {path}")
        return

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    commit_sha = get_commit_sha(path)

    logger.info(f"[docstring] Starting run_id={run_id} repo={repo} path={path}")
    logger.info(f"[docstring] commit_sha={commit_sha} dry_run={dry_run} force={force}")

    # Initialize components
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.error("OPENAI_API_KEY not set")
        return

    effective_model = model or os.getenv("OPENAI_EXTRACT_MODEL", "gpt-4o-mini")
    extractor_obj = OpenAIExtractor(api_key=api_key, model=effective_model)
    # Read effective model after init (env var may override)
    effective_model = extractor_obj.model
    logger.info(f"[docstring] OpenAIExtractor model={effective_model}")

    ts_extractor = TreeSitterExtractor()

    kg = KnowledgeGraphIntegrator(
        store_type="memory",
        use_pipeline=True,
        enable_deduplication=True,
    )

    if not kg.pipeline or not kg.entity_resolver:
        logger.error("Pipeline or EntityResolver not initialized")
        return

    logger.info(f"[docstring] KnowledgeGraphIntegrator initialized "
                f"(pipeline modules: {len(getattr(kg.pipeline, 'modules', []))})")

    # Connect to PostgreSQL
    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5433")),
        database=os.getenv("POSTGRES_DB", "eliza"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )

    # Collect files
    files = get_repo_files(path, file_filter)
    logger.info(f"[docstring] Found {len(files)} code files")

    # Stats
    total_stats = {
        "files_scanned": 0,
        "files_with_docstrings": 0,
        "batches_total": 0,
        "batches_skipped": 0,
        "batches_processed": 0,
        "entities_raw": 0,
        "entities_passed": 0,
        "entities_persisted": 0,
        "relationships_persisted": 0,
        "tokens_total": 0,
    }

    semaphore = asyncio.Semaphore(SEMAPHORE_LIMIT)
    start_time = time.time()

    for i, file_path in enumerate(files):
        total_stats["files_scanned"] += 1
        rel_path = str(file_path.relative_to(path))

        # Detect language from extension
        language = EXT_TO_LANGUAGE.get(file_path.suffix)
        if not language:
            continue

        # Extract entities with tree-sitter
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            entities, _ = ts_extractor.extract(language, content, rel_path, repo)
        except Exception as e:
            logger.warning(f"  Tree-sitter failed for {rel_path}: {e}")
            continue

        # Aggregate into batches
        batches = aggregate_docstrings_for_file(entities)
        if not batches:
            continue

        total_stats["files_with_docstrings"] += 1
        file_hash = compute_file_hash(file_path)

        for batch_text, source_entities, batch_index in batches:
            total_stats["batches_total"] += 1

            # Idempotency check
            if not force and not dry_run:
                if check_idempotency(conn, repo, rel_path, file_hash,
                                     batch_index, PROMPT_VERSION, effective_model):
                    total_stats["batches_skipped"] += 1
                    continue

            try:
                stats = await process_file_batch(
                    batch_text=batch_text,
                    source_entities=source_entities,
                    batch_index=batch_index,
                    repo=repo,
                    file_path_str=rel_path,
                    file_hash=file_hash,
                    commit_sha=commit_sha,
                    extractor=extractor_obj,
                    kg=kg,
                    conn=conn,
                    run_id=run_id,
                    dry_run=dry_run,
                    semaphore=semaphore,
                )

                total_stats["batches_processed"] += 1
                total_stats["entities_raw"] += stats["raw_entities"]
                total_stats["entities_passed"] += stats["passed_entities"]
                total_stats["entities_persisted"] += stats["entities_persisted"]
                total_stats["relationships_persisted"] += stats["relationships_persisted"]
                total_stats["tokens_total"] += stats["tokens"]

                logger.info(
                    f"  [{i+1}/{len(files)}] {rel_path} batch={batch_index}: "
                    f"{stats['raw_entities']} raw → {stats['passed_entities']} passed → "
                    f"{stats['entities_persisted']} persisted "
                    f"({stats['input_chars']} chars, {stats['tokens']} tokens)"
                )

            except Exception as e:
                logger.error(f"  Failed: {rel_path} batch={batch_index}: {e}")
                continue

        # Rate limiting between files
        await asyncio.sleep(INTER_FILE_DELAY)

    elapsed = time.time() - start_time

    # Summary
    logger.info("=" * 60)
    logger.info(f"[docstring] Run complete: {run_id}")
    logger.info(f"  Duration: {elapsed:.1f}s")
    logger.info(f"  Files scanned: {total_stats['files_scanned']}")
    logger.info(f"  Files with docstrings: {total_stats['files_with_docstrings']}")
    logger.info(f"  Batches: {total_stats['batches_total']} total, "
                f"{total_stats['batches_skipped']} skipped (idempotent), "
                f"{total_stats['batches_processed']} processed")
    logger.info(f"  Entities: {total_stats['entities_raw']} raw → "
                f"{total_stats['entities_passed']} passed → "
                f"{total_stats['entities_persisted']} persisted")
    logger.info(f"  Relationships persisted: {total_stats['relationships_persisted']}")
    logger.info(f"  Tokens: {total_stats['tokens_total']}")
    logger.info(f"  Dry run: {dry_run}")
    logger.info("=" * 60)

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract semantic entities from code docstrings via LLM"
    )
    parser.add_argument("--repo", required=True, help="Repository name (e.g., regen-ledger)")
    parser.add_argument("--path", required=True, help="Path to repository root")
    parser.add_argument("--dry-run", action="store_true", help="Run without persisting")
    parser.add_argument("--force", action="store_true", help="Bypass idempotency checks")
    parser.add_argument("--file-filter", help="Only process files matching this substring")
    parser.add_argument("--model", help="Override LLM model (default: gpt-4o-mini)")

    args = parser.parse_args()

    asyncio.run(main(
        repo=args.repo,
        repo_path=args.path,
        dry_run=args.dry_run,
        force=args.force,
        file_filter=args.file_filter,
        model=args.model,
    ))
