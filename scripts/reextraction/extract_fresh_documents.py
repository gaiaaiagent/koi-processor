#!/usr/bin/env python3
"""
Fresh extraction for documents that never had entity extraction.

This script runs entity extraction on documents that have NO existing extractions,
using OpenAI GPT-4o-mini and applying the validated quality pipeline with
cross-document entity deduplication via pgvector.

Features:
- Entity extraction using GPT-4o-mini
- Quality pipeline with 5 modules (97%+ pass rate target)
- Cross-document entity deduplication via EntityResolver (3-tier waterfall)
  - Tier 1: Exact match (B-Tree, microseconds)
  - Tier 2: Semantic match (HNSW vector, milliseconds)
  - Tier 3: Create new entity (deterministic URI)
- CAT receipt generation for complete provenance tracking
- Batch processing with configurable size
- Checkpoint/resume support
- Dry-run mode for testing

Sources covered:
- Discourse (remaining): ~569 documents
- YouTube: ~15 documents
- GitLab: ~30 documents
- GitHub Activity: ~23 documents
- GitHub (markdown only): ~428 documents
- Total: ~1,065 documents

Usage:
    python extract_fresh_documents.py --source discourse --batch-size 50
    python extract_fresh_documents.py --source github-markdown --dry-run
    python extract_fresh_documents.py --all --batch-size 25
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from uuid import uuid4

# Load environment variables FIRST (critical for OpenAI API key)
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / '.env')

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor, Json
except ImportError:
    print("ERROR: psycopg2 not installed. Install with: pip install psycopg2-binary")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('fresh_extraction.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Import EntityResolver for deduplication
try:
    from knowledge_graph.entity_resolver import EntityResolver
    HAS_ENTITY_RESOLVER = True
    logger.info("EntityResolver available for deduplication")
except ImportError:
    HAS_ENTITY_RESOLVER = False
    logger.warning("EntityResolver not available. Deduplication disabled.")


# Source patterns for query
SOURCE_PATTERNS = {
    'discourse': 'discourse-sensor%',
    'youtube': 'youtube-sensor%',
    'gitlab': 'gitlab-sensor%',
    'github-activity': 'github-activity-sensor%',
    'github-markdown': 'github-sensor%',  # Special handling for markdown filter
}

# Markdown patterns for GitHub content filtering
# Note: Using %% to escape % signs for psycopg2 parameter interpolation
MARKDOWN_PATTERNS = [
    "%%.md#%%",
    "%%.mdx#%%",
    "%%README#%%",
    "%%.rst#%%",
    "%%.txt#%%",
    "%%.asciidoc#%%",
    "%%.adoc#%%"
]


def connect_db(host: str = "localhost", port: int = 5433,
               database: str = "eliza", user: str = "postgres",
               password: str = "postgres"):
    """Connect to PostgreSQL database."""
    return psycopg2.connect(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        cursor_factory=RealDictCursor
    )


def get_documents_needing_extraction(conn, source: str, limit: Optional[int] = None,
                                      sensor: Optional[str] = None) -> List[Dict]:
    """
    Get documents that have no entity extractions.

    Args:
        conn: Database connection
        source: Source type (discourse, youtube, gitlab, github-activity, github-markdown)
        limit: Optional limit on number of documents
        sensor: Optional specific sensor ID to target (for deduplication)

    Returns:
        List of documents needing extraction
    """
    cursor = conn.cursor()

    pattern = SOURCE_PATTERNS.get(source)
    if not pattern:
        raise ValueError(f"Unknown source: {source}")

    # Build markdown filter for GitHub
    if source == 'github-markdown':
        markdown_conditions = " OR ".join([f"m.rid LIKE '{p}'" for p in MARKDOWN_PATTERNS])
        extra_where = f"AND ({markdown_conditions})"
    else:
        extra_where = ""

    # If specific sensor provided, use exact match instead of LIKE
    if sensor:
        sensor_condition = "m.source_sensor = %s"
        sensor_param = sensor
    else:
        sensor_condition = "m.source_sensor LIKE %s"
        sensor_param = pattern

    query = f"""
    SELECT
        m.id,
        m.rid,
        m.source_sensor,
        m.content->>'title' as title,
        m.content->>'url' as url,
        m.content->>'text' as text,
        m.content as content_json,
        m.created_at
    FROM koi_memories m
    LEFT JOIN koi_kg_extractions e ON m.rid = e.memory_rid
    WHERE
        {sensor_condition}
        AND e.id IS NULL
        AND m.content->>'text' IS NOT NULL
        AND LENGTH(m.content->>'text') > 50
        {extra_where}
    ORDER BY m.created_at DESC
    {"LIMIT " + str(limit) if limit else ""}
    """

    cursor.execute(query, (sensor_param,))
    rows = cursor.fetchall()

    documents = []
    for row in rows:
        documents.append({
            'id': row['id'],
            'rid': row['rid'],
            'source_sensor': row['source_sensor'],
            'title': row['title'],
            'url': row['url'],
            'text': row['text'],
            'content_json': row['content_json'],
            'created_at': row['created_at'].isoformat() if row['created_at'] else None
        })

    return documents


async def extract_entities_openai(text: str, source_type: str, api_key: str, max_retries: int = 3) -> Dict[str, Any]:
    """
    Extract entities from text using OpenAI GPT-4o-mini with retry logic.

    Args:
        text: Text content to extract from
        source_type: Source type for context
        api_key: OpenAI API key
        max_retries: Maximum retry attempts (default: 3)

    Returns:
        Extraction result with entities and relationships
    """
    from extraction.openai_extractor import OpenAIExtractor

    extractor = OpenAIExtractor(api_key=api_key, model="gpt-4o-mini")
    metadata = {"source_type": source_type}

    # Retry loop with exponential backoff
    for attempt in range(max_retries):
        try:
            result = await extractor.extract_metadata(text, source_type, metadata)
            return result

        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = 2 * (attempt + 1)  # 2s, 4s, 6s
                logger.warning(f"Extraction attempt {attempt + 1}/{max_retries} failed: {e}. Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"Extraction failed after {max_retries} attempts: {e}")
                # Return fallback result instead of crashing
                return {
                    "error": str(e),
                    "entities": [],
                    "relationships": [],
                    "extracted_entities": [],
                    "extracted_relationships": [],
                    "confidence_score": 0.0
                }


def store_extraction(conn, memory_rid: str, extraction: Dict[str, Any],
                    source_type: str, extractor_version: str = "1.0.0-fresh") -> Optional[str]:
    """
    Store extraction result in koi_kg_extractions table.

    Args:
        conn: Database connection
        memory_rid: RID of the source memory
        extraction: Extraction result from LLM
        source_type: Source type
        extractor_version: Version string for tracking

    Returns:
        Extraction RID if successful, None otherwise
    """
    cursor = conn.cursor()

    try:
        # Generate extraction RID
        extraction_rid = f"extraction:fresh:{uuid4()}"

        # Extract entities from result
        entities = extraction.get("extracted_entities", [])
        if not entities:
            entities = extraction.get("entities", [])

        # Extract relations (note: column is 'relations' not 'relationships')
        relations = extraction.get("extracted_relationships", [])
        if not relations:
            relations = extraction.get("relationships", [])

        # Get confidence from extraction
        confidence = extraction.get("confidence_score", 0.8)

        # Get token usage info
        tokens = extraction.get("tokens_consumed", 0)
        cost = extraction.get("cost_usd", 0.0)

        # Prepare JSON
        entities_json = json.dumps(entities)
        relations_json = json.dumps(relations)

        # Use extraction_type 'passA' for fresh extraction (initial pass)
        query = """
        INSERT INTO koi_kg_extractions (
            memory_rid, extraction_rid, extraction_type, entities, relations,
            confidence_score, extractor_version, tokens_consumed, cost_usd, created_at
        ) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, NOW())
        ON CONFLICT (extraction_rid) DO UPDATE SET
            entities = EXCLUDED.entities,
            relations = EXCLUDED.relations,
            confidence_score = EXCLUDED.confidence_score,
            extractor_version = EXCLUDED.extractor_version,
            updated_at = NOW()
        RETURNING id, extraction_rid
        """

        cursor.execute(query, (
            memory_rid,
            extraction_rid,
            'passA',  # Fresh extraction is initial pass
            entities_json,
            relations_json,
            confidence,
            extractor_version,
            tokens,
            cost
        ))

        result = cursor.fetchone()
        conn.commit()

        return result['extraction_rid'] if result else extraction_rid

    except Exception as e:
        logger.error(f"Failed to store extraction for {memory_rid}: {e}")
        conn.rollback()
        return None


def save_checkpoint(source: str, completed: int):
    """Save extraction progress checkpoint."""
    checkpoint_file = Path(__file__).parent / f".checkpoint_{source}.json"
    checkpoint_data = {
        "source": source,
        "completed": completed,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    checkpoint_file.write_text(json.dumps(checkpoint_data, indent=2))
    logger.info(f"Checkpoint saved: {completed} documents completed")


def load_checkpoint(source: str) -> int:
    """Load extraction progress checkpoint."""
    checkpoint_file = Path(__file__).parent / f".checkpoint_{source}.json"
    if checkpoint_file.exists():
        try:
            checkpoint_data = json.loads(checkpoint_file.read_text())
            completed = checkpoint_data.get("completed", 0)
            logger.info(f"Resuming from checkpoint: {completed} documents already completed")
            return completed
        except Exception as e:
            logger.warning(f"Failed to load checkpoint: {e}. Starting from beginning.")
            return 0
    return 0


def clear_checkpoint(source: str):
    """Clear extraction progress checkpoint."""
    checkpoint_file = Path(__file__).parent / f".checkpoint_{source}.json"
    if checkpoint_file.exists():
        checkpoint_file.unlink()
        logger.info("Checkpoint cleared")


def create_cat_receipt_sync(
    conn,
    memory_rid: str,
    extraction_id: str,
    entities_count: int,
    source_type: str,
    model: str = "gpt-4o-mini",
    extractor_version: str = "1.0.0-fresh",
    confidence: float = 0.8,
    tokens: int = 0,
    cost: float = 0.0
) -> Optional[str]:
    """
    Create a CAT (Content Addressable Transformation) receipt for extraction provenance.

    Synchronous version using psycopg2 for compatibility with existing script.

    Args:
        conn: Database connection
        memory_rid: RID of source memory
        extraction_id: ID of extraction record
        entities_count: Number of entities extracted
        source_type: Source type (discourse, github, etc.)
        model: Model used for extraction
        extractor_version: Version of extractor
        confidence: Average confidence score
        tokens: Tokens consumed (if available)
        cost: Cost in USD (if available)

    Returns:
        Receipt ID if successful, None otherwise
    """
    cursor = conn.cursor()

    try:
        # Generate receipt ID
        receipt_content = f"kg_extraction_fresh:{memory_rid}:{extraction_id}:{datetime.now(timezone.utc).isoformat()}"
        receipt_id = hashlib.sha256(receipt_content.encode()).hexdigest()

        # Prepare metadata
        metadata = {
            "model": model,
            "source_type": source_type,
            "ontology_version": "op-v1.1",
            "entities_extracted": entities_count,
            "confidence_avg": confidence,
            "extractor_version": extractor_version,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        if tokens > 0:
            metadata["tokens_consumed"] = tokens
        if cost > 0:
            metadata["cost_usd"] = cost

        # Insert CAT receipt
        query = """
        INSERT INTO koi_transformation_receipts (
            receipt_id,
            transformation_type,
            input_rid,
            output_rid,
            processor_name,
            processor_version,
            entities_extracted,
            source_sensor,
            metadata,
            created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (receipt_id) DO NOTHING
        RETURNING receipt_id
        """

        # extraction_id is now already the extraction_rid (extraction:fresh:uuid)
        cursor.execute(query, (
            receipt_id,
            "kg_extraction_fresh",
            memory_rid,
            extraction_id,  # This is already the extraction_rid
            f"Fresh Extraction ({model})",
            extractor_version,
            entities_count,
            source_type,
            json.dumps(metadata)
        ))

        result = cursor.fetchone()
        conn.commit()

        if result:
            logger.info(f"Created CAT receipt {receipt_id[:16]}... for {memory_rid}")
            return result['receipt_id']
        else:
            logger.warning(f"CAT receipt {receipt_id[:16]}... already exists (skipped)")
            return receipt_id

    except Exception as e:
        logger.error(f"Failed to create CAT receipt for {memory_rid}: {e}")
        conn.rollback()
        return None


async def process_batch(
    documents: List[Dict],
    api_key: str,
    source_type: str,
    conn,
    dry_run: bool = False,
    apply_pipeline: bool = True,
    entity_resolver: Optional['EntityResolver'] = None
) -> Dict[str, Any]:
    """
    Process a batch of documents through extraction, pipeline, and deduplication.

    Args:
        documents: List of documents to process
        api_key: OpenAI API key
        source_type: Source type for context
        conn: Database connection
        dry_run: If True, don't store results
        apply_pipeline: If True, apply quality pipeline
        entity_resolver: EntityResolver instance for cross-document deduplication

    Returns:
        Batch processing results
    """
    results = {
        'processed': 0,
        'succeeded': 0,
        'failed': 0,
        'entities_extracted': 0,
        'entities_passed': 0,
        'entities_blocked': 0,
        'dedup_tier1_hits': 0,
        'dedup_tier2_hits': 0,
        'dedup_tier3_new': 0,
        'errors': []
    }

    # Import pipeline if needed
    pipeline = None
    if apply_pipeline:
        try:
            from knowledge_graph.postprocessing import (
                PipelineOrchestrator,
                ProcessingContext,
                Entity as PipelineEntity,
                create_pipeline_from_config
            )
            from knowledge_graph.postprocessing.modules import (
                ConfidenceFilterModule,
                CanonicalResolverModule,
                EntityQualityFilterModule,
                ListSplitterModule,
                OntologyNormalizerModule
            )

            # Load pipeline config
            config_path = Path(__file__).parent.parent.parent / 'src/knowledge_graph/config/pipeline_config.json'
            if config_path.exists():
                pipeline = create_pipeline_from_config(str(config_path))
            else:
                pipeline = PipelineOrchestrator([
                    ConfidenceFilterModule({'entity_threshold': 0.70, 'relationship_threshold': 0.80}),
                    CanonicalResolverModule(),
                    EntityQualityFilterModule(),
                    ListSplitterModule(),
                    OntologyNormalizerModule()
                ])
            logger.info(f"Pipeline initialized with {len(pipeline)} modules")

        except ImportError as e:
            logger.warning(f"Pipeline not available: {e}. Proceeding without pipeline.")
            pipeline = None

    for doc in documents:
        results['processed'] += 1
        rid = doc['rid']
        text = doc.get('text', '')

        if not text or len(text) < 50:
            logger.warning(f"Skipping {rid}: text too short")
            continue

        try:
            # Extract entities
            logger.info(f"Extracting from {rid}...")
            extraction = await extract_entities_openai(text, source_type, api_key)

            if extraction.get('error'):
                results['failed'] += 1
                results['errors'].append({'rid': rid, 'error': extraction['error']})
                continue

            # Get entities from extraction
            entities = extraction.get('extracted_entities', [])
            if not entities:
                entities = extraction.get('entities', [])

            results['entities_extracted'] += len(entities)

            # Apply pipeline if available
            if pipeline and entities:
                # Convert to pipeline format
                pipeline_entities = []
                for e in entities:
                    pe = PipelineEntity(
                        name=e.get('name', ''),
                        type=e.get('type', 'UNKNOWN'),
                        confidence=e.get('confidence', 0.8)
                    )
                    pipeline_entities.append(pe)

                # Process through pipeline
                context = ProcessingContext(entities=pipeline_entities)
                result_context = pipeline.process(context)

                # Get filtered entities
                passed_entities = [
                    {
                        'name': e.name,
                        'type': e.type,
                        'confidence': e.confidence,
                        'properties': e.metadata.get('properties', {})
                    }
                    for e in result_context.entities
                ]
                blocked_count = len(pipeline_entities) - len(passed_entities)

                results['entities_passed'] += len(passed_entities)
                results['entities_blocked'] += blocked_count

                # Update extraction with filtered entities
                extraction['extracted_entities'] = passed_entities
                extraction['pipeline_applied'] = True
                extraction['entities_blocked'] = blocked_count

                logger.info(f"  Extracted {len(entities)} entities, {len(passed_entities)} passed pipeline")
            else:
                passed_entities = entities
                results['entities_passed'] += len(entities)
                logger.info(f"  Extracted {len(entities)} entities")

            # Apply cross-document deduplication via EntityResolver
            if entity_resolver and passed_entities and not dry_run:
                for entity in passed_entities:
                    try:
                        dedup_result = entity_resolver.get_or_create_entity(
                            entity.get('name', ''),
                            entity.get('type', 'UNKNOWN')
                        )
                        # Track deduplication tier hits
                        match_method = dedup_result.get('match_method', '')
                        if match_method == 'tier1_exact':
                            results['dedup_tier1_hits'] += 1
                        elif match_method == 'tier2_semantic':
                            results['dedup_tier2_hits'] += 1
                            # Log semantic matches for visibility
                            logger.debug(
                                f"    Tier 2 hit: '{entity.get('name')}' -> "
                                f"'{dedup_result.get('entity_text')}' "
                                f"(score: {dedup_result.get('match_score', 0):.3f})"
                            )
                        elif match_method == 'tier3_new':
                            results['dedup_tier3_new'] += 1
                    except Exception as e:
                        logger.warning(f"    Deduplication failed for '{entity.get('name')}': {e}")

            # Store result
            if not dry_run:
                extraction_id = store_extraction(
                    conn,
                    rid,
                    extraction,
                    source_type,
                    extractor_version='1.0.0-fresh'
                )
                if extraction_id:
                    results['succeeded'] += 1

                    # Create CAT receipt for provenance tracking
                    passed_entities = extraction.get('extracted_entities', entities)
                    cat_receipt_id = create_cat_receipt_sync(
                        conn,
                        memory_rid=rid,
                        extraction_id=extraction_id,
                        entities_count=len(passed_entities),
                        source_type=source_type,
                        model="gpt-4o-mini",
                        extractor_version='1.0.0-fresh',
                        confidence=extraction.get('confidence_score', 0.8),
                        tokens=extraction.get('tokens_consumed', 0),
                        cost=extraction.get('cost_usd', 0.0)
                    )

                    if cat_receipt_id:
                        logger.info(f"  CAT receipt created for {rid}")
                else:
                    results['failed'] += 1
            else:
                results['succeeded'] += 1

        except Exception as e:
            logger.error(f"Error processing {rid}: {e}")
            results['failed'] += 1
            results['errors'].append({'rid': rid, 'error': str(e)})

        # Rate limiting: 50ms delay = 1,200 RPM (well within OpenAI limits)
        await asyncio.sleep(0.05)

    return results


async def run_extraction(
    source: str,
    batch_size: int = 50,
    limit: Optional[int] = None,
    dry_run: bool = False,
    host: str = "localhost",
    port: int = 5433,
    sensor: Optional[str] = None,
    enable_deduplication: bool = True
) -> Dict[str, Any]:
    """
    Run fresh extraction for a source.

    Args:
        source: Source type
        batch_size: Number of documents per batch
        limit: Optional limit on total documents
        dry_run: If True, don't store results
        host: Database host
        port: Database port
        sensor: Optional specific sensor ID to target (for deduplication)
        enable_deduplication: If True, use EntityResolver for cross-document deduplication

    Returns:
        Extraction results
    """
    # Get API key
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable not set")

    # Connect to database
    conn = connect_db(host=host, port=port)

    # Initialize EntityResolver for deduplication
    entity_resolver = None
    if enable_deduplication and HAS_ENTITY_RESOLVER and not dry_run:
        try:
            db_config = {
                'host': host,
                'port': port,
                'database': 'eliza',
                'user': 'postgres',
                'password': 'postgres'
            }
            entity_resolver = EntityResolver(db_config=db_config)
            logger.info(
                f"EntityResolver initialized for deduplication "
                f"(Tier 1: exact, Tier 2: {'enabled' if entity_resolver.openai_client else 'disabled'})"
            )
        except Exception as e:
            logger.warning(f"Failed to initialize EntityResolver: {e}. Proceeding without deduplication.")

    try:
        # Load checkpoint to resume from interruption
        checkpoint_start = load_checkpoint(source) if not dry_run else 0

        # Get documents
        logger.info(f"Querying documents for source: {source}" + (f" (sensor: {sensor})" if sensor else ""))
        documents = get_documents_needing_extraction(conn, source, limit, sensor)
        logger.info(f"Found {len(documents)} documents needing extraction")

        if not documents:
            # Clear checkpoint if no documents left
            if checkpoint_start > 0:
                clear_checkpoint(source)
            return {'status': 'complete', 'message': 'No documents to process'}

        # Skip already processed documents (checkpoint resume)
        if checkpoint_start > 0:
            documents = documents[checkpoint_start:]
            logger.info(f"Resuming from document {checkpoint_start}. Processing {len(documents)} remaining documents")

        # Process in batches
        all_results = {
            'source': source,
            'total_documents': len(documents),
            'checkpoint_start': checkpoint_start,
            'processed': 0,
            'succeeded': 0,
            'failed': 0,
            'entities_extracted': 0,
            'entities_passed': 0,
            'entities_blocked': 0,
            'dedup_tier1_hits': 0,
            'dedup_tier2_hits': 0,
            'dedup_tier3_new': 0,
            'dedup_enabled': entity_resolver is not None,
            'errors': []
        }

        for i in range(0, len(documents), batch_size):
            batch = documents[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (len(documents) + batch_size - 1) // batch_size

            logger.info(f"\nProcessing batch {batch_num}/{total_batches} ({len(batch)} documents)")

            batch_results = await process_batch(
                batch,
                api_key,
                source,
                conn,
                dry_run=dry_run,
                apply_pipeline=True,
                entity_resolver=entity_resolver
            )

            # Aggregate results
            all_results['processed'] += batch_results['processed']
            all_results['succeeded'] += batch_results['succeeded']
            all_results['failed'] += batch_results['failed']
            all_results['entities_extracted'] += batch_results['entities_extracted']
            all_results['entities_passed'] += batch_results['entities_passed']
            all_results['entities_blocked'] += batch_results['entities_blocked']
            all_results['dedup_tier1_hits'] += batch_results.get('dedup_tier1_hits', 0)
            all_results['dedup_tier2_hits'] += batch_results.get('dedup_tier2_hits', 0)
            all_results['dedup_tier3_new'] += batch_results.get('dedup_tier3_new', 0)
            all_results['errors'].extend(batch_results['errors'])

            # Progress update
            logger.info(f"Batch {batch_num} complete: {batch_results['succeeded']}/{batch_results['processed']} succeeded")

            # Save checkpoint after each batch (resume capability)
            if not dry_run:
                total_completed = checkpoint_start + i + len(batch)
                save_checkpoint(source, total_completed)

        # Clear checkpoint on successful completion
        if not dry_run:
            clear_checkpoint(source)

        # Calculate rates
        if all_results['entities_extracted'] > 0:
            all_results['pass_rate'] = round(
                all_results['entities_passed'] / all_results['entities_extracted'] * 100, 2
            )
        else:
            all_results['pass_rate'] = 0

        # Calculate deduplication rates
        dedup_total = (
            all_results['dedup_tier1_hits'] +
            all_results['dedup_tier2_hits'] +
            all_results['dedup_tier3_new']
        )
        if dedup_total > 0:
            all_results['dedup_tier1_rate'] = round(
                all_results['dedup_tier1_hits'] / dedup_total * 100, 2
            )
            all_results['dedup_tier2_rate'] = round(
                all_results['dedup_tier2_hits'] / dedup_total * 100, 2
            )
            all_results['dedup_tier3_rate'] = round(
                all_results['dedup_tier3_new'] / dedup_total * 100, 2
            )
            all_results['dedup_rate'] = round(
                (all_results['dedup_tier1_hits'] + all_results['dedup_tier2_hits']) / dedup_total * 100, 2
            )
            logger.info(
                f"Deduplication: {all_results['dedup_rate']}% "
                f"(Tier1: {all_results['dedup_tier1_rate']}%, Tier2: {all_results['dedup_tier2_rate']}%, "
                f"New: {all_results['dedup_tier3_rate']}%)"
            )

        return all_results

    finally:
        conn.close()


def main():
    """Main execution."""
    parser = argparse.ArgumentParser(
        description="Run fresh entity extraction on documents without extractions"
    )
    parser.add_argument(
        '--source', '-s', type=str,
        choices=['discourse', 'youtube', 'gitlab', 'github-activity', 'github-markdown'],
        help='Source to extract from'
    )
    parser.add_argument(
        '--all', '-a', action='store_true',
        help='Extract from all sources'
    )
    parser.add_argument(
        '--batch-size', '-b', type=int, default=50,
        help='Batch size for processing (default: 50)'
    )
    parser.add_argument(
        '--limit', '-l', type=int, default=None,
        help='Limit number of documents per source'
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Run extraction but do not store results'
    )
    parser.add_argument(
        '--host', type=str, default='localhost',
        help='Database host (default: localhost)'
    )
    parser.add_argument(
        '--port', type=int, default=5433,
        help='Database port (default: 5433)'
    )
    parser.add_argument(
        '--output', '-o', type=str, default=None,
        help='Output file for results'
    )
    parser.add_argument(
        '--sensor', type=str, default=None,
        help='Specific sensor ID to target (for deduplication)'
    )
    parser.add_argument(
        '--no-dedup', action='store_true',
        help='Disable cross-document entity deduplication'
    )

    args = parser.parse_args()

    if not args.source and not args.all:
        parser.error("Either --source or --all is required")

    # Determine sources to process
    if args.all:
        sources = list(SOURCE_PATTERNS.keys())
    else:
        sources = [args.source]

    print("=" * 70)
    print("FRESH ENTITY EXTRACTION WITH DEDUPLICATION")
    print("=" * 70)
    print()
    print(f"Sources: {', '.join(sources)}")
    print(f"Batch size: {args.batch_size}")
    print(f"Limit per source: {args.limit or 'None'}")
    print(f"Dry run: {args.dry_run}")
    print(f"Deduplication: {'disabled' if args.no_dedup else 'enabled (Tier 1 + Tier 2)'}")
    print()

    # Run extraction
    all_results = {}
    for source in sources:
        print("-" * 70)
        print(f"Processing: {source}")
        print("-" * 70)

        try:
            results = asyncio.run(run_extraction(
                source=source,
                batch_size=args.batch_size,
                limit=args.limit,
                dry_run=args.dry_run,
                host=args.host,
                port=args.port,
                sensor=args.sensor,
                enable_deduplication=not args.no_dedup
            ))
            all_results[source] = results

            print(f"\nResults for {source}:")
            print(f"  Documents processed: {results.get('processed', 0)}")
            print(f"  Succeeded: {results.get('succeeded', 0)}")
            print(f"  Failed: {results.get('failed', 0)}")
            print(f"  Entities extracted: {results.get('entities_extracted', 0)}")
            print(f"  Entities passed: {results.get('entities_passed', 0)}")
            print(f"  Entities blocked: {results.get('entities_blocked', 0)}")
            print(f"  Pass rate: {results.get('pass_rate', 0)}%")
            if results.get('dedup_enabled'):
                print(f"  Deduplication rate: {results.get('dedup_rate', 0)}%")
                print(f"    Tier 1 (exact): {results.get('dedup_tier1_hits', 0)} ({results.get('dedup_tier1_rate', 0)}%)")
                print(f"    Tier 2 (semantic): {results.get('dedup_tier2_hits', 0)} ({results.get('dedup_tier2_rate', 0)}%)")
                print(f"    Tier 3 (new): {results.get('dedup_tier3_new', 0)} ({results.get('dedup_tier3_rate', 0)}%)")

        except Exception as e:
            logger.error(f"Error processing {source}: {e}")
            all_results[source] = {'error': str(e)}

    # Save results
    output_path = args.output or f"fresh_extraction_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_path, 'w') as f:
        json.dump({
            'generated_at': datetime.utcnow().isoformat(),
            'dry_run': args.dry_run,
            'results': all_results
        }, f, indent=2, default=str)

    print()
    print("=" * 70)
    print("EXTRACTION COMPLETE")
    print("=" * 70)
    print(f"\nResults saved to: {output_path}")

    # Print summary
    total_processed = sum(r.get('processed', 0) for r in all_results.values() if isinstance(r, dict))
    total_succeeded = sum(r.get('succeeded', 0) for r in all_results.values() if isinstance(r, dict))
    total_entities = sum(r.get('entities_extracted', 0) for r in all_results.values() if isinstance(r, dict))
    total_passed = sum(r.get('entities_passed', 0) for r in all_results.values() if isinstance(r, dict))

    print(f"\nSummary:")
    print(f"  Total documents processed: {total_processed}")
    print(f"  Total succeeded: {total_succeeded}")
    print(f"  Total entities extracted: {total_entities}")
    print(f"  Total entities passed pipeline: {total_passed}")
    if total_entities > 0:
        print(f"  Overall pass rate: {round(total_passed / total_entities * 100, 2)}%")

    return 0


if __name__ == '__main__':
    sys.exit(main())
