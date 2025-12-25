#!/usr/bin/env python3
"""
Week 15 Canary Re-Extraction Script

Re-extracts entities and relationships for a small canary set of documents
containing carbon/NCT concepts to validate the updated extraction prompt.

This script:
1. Fetches specified documents from koi_memories
2. Re-runs LLM extraction with the updated prompt_builder
3. Compares new relationships for carbon/NCT entities
4. Reports on new relationship creation

Usage:
    python scripts/reextraction/week15_canary_reextract.py
    python scripts/reextraction/week15_canary_reextract.py --dry-run
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import extraction components
try:
    from extraction.openai_extractor import OpenAIExtractor
    from extraction.prompt_builder import build_extraction_prompt, get_system_message
    from knowledge_graph.entity_resolver import EntityResolver
    from knowledge_graph.graph_integration import KnowledgeGraphIntegrator
    HAS_EXTRACTION = True
except ImportError as e:
    logger.warning(f"Import error: {e}")
    HAS_EXTRACTION = False

# Database connection
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    HAS_DB = True
except ImportError:
    HAS_DB = False
    logger.warning("psycopg2 not available")


# Canary document IDs (Week 15)
CANARY_DOCS = [
    "06034a2d-75be-4268-a130-e2c693cf9c5d",
    "0ac78dc9-f095-44be-b647-09bd12fad7f4",
    "191b9d5d-d00f-4bbc-9b55-e541e0c7d3f0",
    "ab95df9b-52bf-4f4f-8946-b092d3d01cff",
    "1b6f2efb-20b8-40a2-923c-0a8d028dfe78",
    "ea2a09fd-c019-45f4-baa5-92df4aff0141",
    "ca1a3ccd-fc7b-4861-9a92-fe10f83197e6",
    "4a903c67-2d82-45bf-a4cc-47fc67637863",
    "106daea8-8d6e-4329-8080-9120007fd053",
    "22558fef-bfff-4522-b2a5-07d9844c1112",
    "60204010-db53-49c5-8cf4-e61856f5071e",
    "6d8e74b8-aca7-4a31-a79c-eb289a8b4fd7",
    "5a976bb1-34a1-4106-b9fd-801352480434",
    "bbe6a3ca-ec42-4a1d-8867-c6f4ce338efd",
    "25c5c6f3-66ea-487b-80b6-b581579e6822",
]

# Target concepts to track relationships for
TARGET_CONCEPTS = [
    "carbon",
    "carbon credits",
    "carbon credit",
    "nct",
    "nct token",
    "nature carbon tonne",
    "carbon sequestration",
    "carbon markets",
]


def get_db_connection():
    """Get database connection."""
    db_url = os.getenv("POSTGRES_URL", "postgresql://postgres:postgres@localhost:5433/eliza")

    # Parse connection string
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
    else:
        # Try direct connection
        return psycopg2.connect(db_url)


def fetch_documents(doc_ids: List[str]) -> List[Dict]:
    """Fetch documents from database."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    placeholders = ','.join(['%s'] * len(doc_ids))
    query = f"""
        SELECT id, content, metadata
        FROM koi_memories
        WHERE id IN ({placeholders})
    """

    cursor.execute(query, doc_ids)
    docs = cursor.fetchall()

    cursor.close()
    conn.close()

    return [dict(d) for d in docs]


def extract_text_from_content(content: Any) -> str:
    """Extract text from content field (may be JSON or text)."""
    if isinstance(content, dict):
        return content.get('text', '')
    elif isinstance(content, str):
        try:
            parsed = json.loads(content)
            return parsed.get('text', content)
        except json.JSONDecodeError:
            return content
    return str(content)


def get_source_type(metadata: Any) -> str:
    """Extract source type from metadata."""
    if isinstance(metadata, dict):
        sensor = metadata.get('sensor', {})
        if isinstance(sensor, dict):
            source = sensor.get('source', 'unknown')
            if ':' in source:
                return source.split(':')[0]
            return source
    return 'unknown'


async def extract_single_document(
    extractor: OpenAIExtractor,
    doc: Dict,
    dry_run: bool = False
) -> Dict[str, Any]:
    """Extract entities and relationships from a single document."""
    doc_id = doc['id']
    content = extract_text_from_content(doc['content'])
    source_type = get_source_type(doc.get('metadata', {}))

    if not content or len(content) < 50:
        return {
            'doc_id': doc_id,
            'status': 'skipped',
            'reason': 'content too short',
            'entities': [],
            'relationships': []
        }

    logger.info(f"Processing document {doc_id[:8]}... ({len(content)} chars)")

    if dry_run:
        return {
            'doc_id': doc_id,
            'status': 'dry_run',
            'content_preview': content[:200],
            'source_type': source_type
        }

    try:
        # Extract using OpenAI
        result = await extractor.extract_metadata(
            content=content,
            source_type=source_type,
            existing_metadata=doc.get('metadata', {})
        )

        # Extract from semantic_extraction structure returned by OpenAI extractor
        semantic = result.get('semantic_extraction', {})
        entities = semantic.get('entities', [])
        relationships = semantic.get('relationships', [])

        # Filter for carbon/NCT related
        carbon_entities = [
            e for e in entities
            if any(t.lower() in e.get('name', '').lower() for t in TARGET_CONCEPTS)
        ]

        carbon_relationships = [
            r for r in relationships
            if any(t.lower() in r.get('subject', '').lower() or
                   t.lower() in r.get('object', '').lower()
                   for t in TARGET_CONCEPTS)
        ]

        return {
            'doc_id': doc_id,
            'status': 'success',
            'total_entities': len(entities),
            'total_relationships': len(relationships),
            'carbon_entities': carbon_entities,
            'carbon_relationships': carbon_relationships,
            'entities': entities,
            'relationships': relationships
        }

    except Exception as e:
        logger.error(f"Error processing {doc_id}: {e}")
        return {
            'doc_id': doc_id,
            'status': 'error',
            'error': str(e)
        }


async def run_canary_extraction(dry_run: bool = False) -> Dict[str, Any]:
    """Run canary extraction on all documents."""
    logger.info("=" * 70)
    logger.info("WEEK 15 CANARY RE-EXTRACTION")
    logger.info("=" * 70)
    logger.info(f"Documents: {len(CANARY_DOCS)}")
    logger.info(f"Target concepts: {TARGET_CONCEPTS}")
    logger.info(f"Dry run: {dry_run}")
    logger.info("")

    # Fetch documents
    logger.info("Fetching documents from database...")
    docs = fetch_documents(CANARY_DOCS)
    logger.info(f"Fetched {len(docs)} documents")

    if not docs:
        return {'error': 'No documents found'}

    # Create extractor
    if not dry_run:
        extractor = OpenAIExtractor()
    else:
        extractor = None

    # Process documents
    results = []
    for doc in docs:
        result = await extract_single_document(extractor, doc, dry_run)
        results.append(result)

    # Aggregate results
    successful = [r for r in results if r['status'] == 'success']
    failed = [r for r in results if r['status'] == 'error']
    skipped = [r for r in results if r['status'] == 'skipped']

    # Aggregate carbon relationships
    all_carbon_rels = []
    for r in successful:
        all_carbon_rels.extend(r.get('carbon_relationships', []))

    # Count predicates
    predicate_counts = {}
    for rel in all_carbon_rels:
        pred = rel.get('predicate', 'unknown')
        predicate_counts[pred] = predicate_counts.get(pred, 0) + 1

    summary = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'dry_run': dry_run,
        'documents': {
            'total': len(CANARY_DOCS),
            'found': len(docs),
            'successful': len(successful),
            'failed': len(failed),
            'skipped': len(skipped)
        },
        'extraction': {
            'total_entities': sum(r.get('total_entities', 0) for r in successful),
            'total_relationships': sum(r.get('total_relationships', 0) for r in successful),
            'carbon_entities': sum(len(r.get('carbon_entities', [])) for r in successful),
            'carbon_relationships': len(all_carbon_rels)
        },
        'predicate_distribution': predicate_counts,
        'sample_carbon_relationships': all_carbon_rels[:20],
        'errors': [{'doc_id': r['doc_id'], 'error': r.get('error')} for r in failed],
        '_results': results  # Include raw results for persistence
    }

    return summary


def print_summary(summary: Dict):
    """Print extraction summary."""
    logger.info("")
    logger.info("=" * 70)
    logger.info("EXTRACTION SUMMARY")
    logger.info("=" * 70)

    docs = summary.get('documents', {})
    extraction = summary.get('extraction', {})

    logger.info(f"Documents: {docs.get('successful', 0)}/{docs.get('total', 0)} successful")
    logger.info(f"Total entities extracted: {extraction.get('total_entities', 0)}")
    logger.info(f"Total relationships extracted: {extraction.get('total_relationships', 0)}")
    logger.info(f"Carbon/NCT entities: {extraction.get('carbon_entities', 0)}")
    logger.info(f"Carbon/NCT relationships: {extraction.get('carbon_relationships', 0)}")

    predicates = summary.get('predicate_distribution', {})
    if predicates:
        logger.info("")
        logger.info("Carbon/NCT Predicate Distribution:")
        for pred, count in sorted(predicates.items(), key=lambda x: -x[1]):
            logger.info(f"  {pred}: {count}")

    sample_rels = summary.get('sample_carbon_relationships', [])
    if sample_rels:
        logger.info("")
        logger.info("Sample Carbon/NCT Relationships:")
        for rel in sample_rels[:10]:
            subj = rel.get('subject', 'unknown')
            pred = rel.get('predicate', 'unknown')
            obj = rel.get('object', 'unknown')
            logger.info(f"  ({subj}, {pred}, {obj})")


async def persist_extraction_results(results: List[Dict], run_id: str) -> Dict[str, int]:
    """Persist extraction results to database using KnowledgeGraphIntegrator."""
    conn = get_db_connection()
    integrator = KnowledgeGraphIntegrator(conn)

    stats = {'entities_saved': 0, 'relationships_saved': 0, 'docs_processed': 0}

    for result in results:
        if result.get('status') != 'success':
            continue

        doc_id = result['doc_id']
        entities = result.get('entities', [])
        relationships = result.get('relationships', [])

        try:
            # Save entities and relationships via integrator
            integrator.process_extraction(
                doc_id=doc_id,
                entities=entities,
                relationships=relationships,
                run_id=run_id
            )
            stats['entities_saved'] += len(entities)
            stats['relationships_saved'] += len(relationships)
            stats['docs_processed'] += 1
            logger.info(f"  Persisted {len(entities)} entities, {len(relationships)} relationships for {doc_id[:8]}")
        except Exception as e:
            logger.error(f"  Error persisting {doc_id[:8]}: {e}")

    conn.close()
    return stats


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Week 15 Canary Re-Extraction')
    parser.add_argument('--dry-run', action='store_true', help='Dry run without extraction')
    parser.add_argument('--persist', action='store_true', help='Persist results to database')
    parser.add_argument('--output', type=str, default=None, help='Output JSON file')

    args = parser.parse_args()

    if not HAS_DB:
        logger.error("psycopg2 not available. Install with: pip install psycopg2-binary")
        return 1

    if not args.dry_run and not HAS_EXTRACTION:
        logger.error("Extraction components not available")
        return 1

    try:
        # Run extraction
        summary = asyncio.run(run_canary_extraction(dry_run=args.dry_run))

        # Print summary
        print_summary(summary)

        # Persist if requested
        if args.persist and not args.dry_run:
            logger.info("")
            logger.info("=" * 70)
            logger.info("PERSISTING RESULTS TO DATABASE")
            logger.info("=" * 70)
            results = summary.pop('_results', [])
            run_id = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            persist_stats = asyncio.run(persist_extraction_results(results, run_id))
            logger.info(f"Persistence complete: {persist_stats}")
            summary['persistence'] = persist_stats
        else:
            summary.pop('_results', None)  # Remove raw results from output

        # Save output
        output_path = args.output or Path(__file__).parent / 'week15_canary_results.json'
        with open(output_path, 'w') as f:
            json.dump(summary, f, indent=2, default=str)
        logger.info(f"\nResults saved to: {output_path}")

        return 0

    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
