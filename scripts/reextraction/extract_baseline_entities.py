#!/usr/bin/env python3
"""
Extract current entities from knowledge graph for pilot documents.

Retrieves all existing entities and their metadata from koi_kg_extractions
for the documents selected in pilot_documents.json.

Input: pilot_documents.json
Output: baseline_entities.json

Usage:
    python scripts/reextraction/extract_baseline_entities.py
    python scripts/reextraction/extract_baseline_entities.py --input custom_docs.json
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: psycopg2 not installed. Install with: pip install psycopg2-binary")
    sys.exit(1)


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


def load_pilot_documents(input_path: str) -> List[Dict]:
    """Load pilot documents from JSON."""
    with open(input_path, 'r') as f:
        data = json.load(f)

    # Handle both formats (list or dict with 'documents' key)
    if isinstance(data, list):
        return data
    return data.get('documents', [])


def extract_entities_for_document(conn, document_rid: str) -> Dict[str, Any]:
    """
    Extract all entities and relationships for a document from koi_kg_extractions.

    The extraction table stores entities as JSONB arrays in the 'entities' column.
    Each extraction record is linked to a chunk via memory_rid (e.g., doc_rid#chunk0).

    Args:
        conn: Database connection
        document_rid: The document RID (without chunk suffix)

    Returns:
        Dictionary with entities, relationships, and metadata
    """
    cursor = conn.cursor()

    # Get all extractions for this document (across all chunks)
    query = """
    SELECT
        e.id as extraction_id,
        e.memory_rid,
        e.extraction_rid,
        e.extraction_type,
        e.entities,
        e.relations,
        e.statements,
        e.confidence_score,
        e.ontology_version,
        e.extractor_version,
        e.tokens_consumed,
        e.cost_usd,
        e.created_at
    FROM koi_kg_extractions e
    WHERE e.memory_rid LIKE %s
    ORDER BY e.memory_rid, e.created_at;
    """

    # Match document and all its chunks
    cursor.execute(query, (f"{document_rid}%",))
    extractions = cursor.fetchall()

    # Aggregate results
    all_entities = []
    all_relations = []
    all_statements = []
    total_tokens = 0
    total_cost = 0.0
    confidence_scores = []

    for ext in extractions:
        # Parse entities from JSONB
        entities = ext.get('entities', []) or []
        if isinstance(entities, list):
            for entity in entities:
                if isinstance(entity, dict):
                    all_entities.append({
                        'name': entity.get('name', entity.get('entity', '')),
                        'type': entity.get('type', entity.get('entity_type', 'UNKNOWN')),
                        'confidence': entity.get('confidence', entity.get('score')),
                        'chunk_rid': ext['memory_rid'],
                        'extraction_id': ext['extraction_id'],
                        'metadata': {k: v for k, v in entity.items()
                                     if k not in ['name', 'entity', 'type', 'entity_type', 'confidence', 'score']}
                    })

        # Parse relations
        relations = ext.get('relations', []) or []
        if isinstance(relations, list):
            for rel in relations:
                if isinstance(rel, dict):
                    all_relations.append({
                        'subject': rel.get('subject', rel.get('source', '')),
                        'predicate': rel.get('predicate', rel.get('relationship', '')),
                        'object': rel.get('object', rel.get('target', '')),
                        'confidence': rel.get('confidence'),
                        'chunk_rid': ext['memory_rid'],
                        'extraction_id': ext['extraction_id']
                    })

        # Parse statements
        statements = ext.get('statements', []) or []
        if isinstance(statements, list):
            all_statements.extend(statements)

        # Aggregate metrics
        total_tokens += ext.get('tokens_consumed', 0) or 0
        total_cost += float(ext.get('cost_usd', 0) or 0)
        if ext.get('confidence_score'):
            confidence_scores.append(float(ext['confidence_score']))

    return {
        'document_rid': document_rid,
        'extraction_count': len(extractions),
        'entities': all_entities,
        'entity_count': len(all_entities),
        'relations': all_relations,
        'relation_count': len(all_relations),
        'statements': all_statements,
        'statement_count': len(all_statements),
        'total_tokens': total_tokens,
        'total_cost_usd': total_cost,
        'avg_confidence': sum(confidence_scores) / len(confidence_scores) if confidence_scores else None,
        'min_confidence': min(confidence_scores) if confidence_scores else None,
        'max_confidence': max(confidence_scores) if confidence_scores else None
    }


def extract_baseline_entities(conn, pilot_docs: List[Dict]) -> Dict[str, Dict]:
    """
    Extract current entities for all pilot documents.

    Args:
        conn: Database connection
        pilot_docs: List of pilot document dictionaries

    Returns:
        Dict with document_rid -> extraction data mapping
    """
    print("=" * 70)
    print("BASELINE ENTITY EXTRACTION")
    print("=" * 70)
    print()
    print(f"Processing {len(pilot_docs)} documents...")
    print()

    baseline = {}
    total_entities = 0
    total_relations = 0
    errors = []

    for i, doc in enumerate(pilot_docs, 1):
        doc_rid = doc.get('document_rid', doc.get('rid', ''))
        title = (doc.get('title') or 'Untitled')[:50]

        # Progress indicator
        if i % 10 == 0 or i == len(pilot_docs):
            print(f"  [{i:3d}/{len(pilot_docs)}] Processing...")

        try:
            extraction_data = extract_entities_for_document(conn, doc_rid)

            # Store with document metadata
            baseline[doc_rid] = {
                'document': {
                    'rid': doc_rid,
                    'title': doc.get('title'),
                    'url': doc.get('url'),
                    'source_sensor': doc.get('source_sensor'),
                    'quality_tier': doc.get('quality_tier'),
                    'avg_confidence': doc.get('avg_confidence')
                },
                'extraction': extraction_data
            }

            total_entities += extraction_data['entity_count']
            total_relations += extraction_data['relation_count']

        except Exception as e:
            errors.append({'document_rid': doc_rid, 'error': str(e)})
            print(f"    ERROR processing {doc_rid}: {e}")

    print()
    print(f"Extracted data from {len(baseline)} documents")
    print(f"Total entities: {total_entities}")
    print(f"Total relations: {total_relations}")
    if errors:
        print(f"Errors: {len(errors)}")

    return baseline


def save_baseline(baseline: Dict[str, Dict], output_path: str):
    """Save baseline entities to JSON."""
    # Create serializable output
    output = {
        'generated_at': datetime.utcnow().isoformat(),
        'document_count': len(baseline),
        'total_entities': sum(d['extraction']['entity_count'] for d in baseline.values()),
        'total_relations': sum(d['extraction']['relation_count'] for d in baseline.values()),
        'documents': baseline
    }

    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2, default=str)

    print()
    print(f"Saved to: {output_path}")


def generate_stats(baseline: Dict[str, Dict]):
    """Generate baseline statistics."""
    print()
    print("-" * 70)
    print("BASELINE STATISTICS")
    print("-" * 70)

    if not baseline:
        print("No data to analyze!")
        return

    docs = list(baseline.values())
    total_docs = len(docs)

    # Entity counts
    entity_counts = [d['extraction']['entity_count'] for d in docs]
    total_entities = sum(entity_counts)

    print(f"\nDocuments: {total_docs}")
    print(f"Total entities: {total_entities}")
    print(f"Average entities per doc: {total_entities / total_docs:.1f}")
    print(f"Min entities: {min(entity_counts)}")
    print(f"Max entities: {max(entity_counts)}")

    # Entity type distribution
    type_counts = {}
    for doc_data in docs:
        for entity in doc_data['extraction']['entities']:
            entity_type = entity.get('type', 'UNKNOWN')
            type_counts[entity_type] = type_counts.get(entity_type, 0) + 1

    print(f"\nEntity types (top 10):")
    sorted_types = sorted(type_counts.items(), key=lambda x: -x[1])[:10]
    for entity_type, count in sorted_types:
        pct = count / total_entities * 100 if total_entities > 0 else 0
        print(f"  {entity_type:20s}: {count:5d} ({pct:5.1f}%)")

    # Confidence distribution
    confidences = [
        e.get('confidence') for d in docs
        for e in d['extraction']['entities']
        if e.get('confidence') is not None
    ]
    if confidences:
        print(f"\nConfidence scores (entity level):")
        print(f"  Count with confidence: {len(confidences)}")
        print(f"  Average: {sum(confidences) / len(confidences):.3f}")
        print(f"  Min: {min(confidences):.3f}")
        print(f"  Max: {max(confidences):.3f}")

        # Confidence buckets
        buckets = {'high (>0.85)': 0, 'medium (0.70-0.85)': 0, 'low (<0.70)': 0}
        for conf in confidences:
            if conf > 0.85:
                buckets['high (>0.85)'] += 1
            elif conf >= 0.70:
                buckets['medium (0.70-0.85)'] += 1
            else:
                buckets['low (<0.70)'] += 1

        print(f"\n  By bucket:")
        for bucket, count in buckets.items():
            pct = count / len(confidences) * 100
            print(f"    {bucket}: {count} ({pct:.1f}%)")

    # By quality tier
    tier_stats = {'high': [], 'medium': [], 'low': []}
    for doc_data in docs:
        tier = doc_data['document'].get('quality_tier', 'unknown')
        if tier in tier_stats:
            tier_stats[tier].append(doc_data['extraction']['entity_count'])

    print(f"\nBy quality tier:")
    for tier, counts in tier_stats.items():
        if counts:
            avg = sum(counts) / len(counts)
            print(f"  {tier:8s}: {len(counts):3d} docs, {sum(counts):5d} entities, {avg:.1f} avg")

    # Relations
    total_relations = sum(d['extraction']['relation_count'] for d in docs)
    print(f"\nRelations: {total_relations}")

    # Cost estimate
    total_tokens = sum(d['extraction']['total_tokens'] for d in docs)
    total_cost = sum(d['extraction']['total_cost_usd'] for d in docs)
    print(f"\nOriginal extraction cost:")
    print(f"  Total tokens: {total_tokens:,}")
    print(f"  Total cost: ${total_cost:.4f}")


def main():
    """Main execution."""
    parser = argparse.ArgumentParser(
        description="Extract baseline entities from knowledge graph"
    )
    parser.add_argument(
        '--input', '-i', type=str, default=None,
        help='Input file path (default: scripts/reextraction/pilot_documents.json)'
    )
    parser.add_argument(
        '--output', '-o', type=str, default=None,
        help='Output file path (default: scripts/reextraction/baseline_entities.json)'
    )
    parser.add_argument(
        '--host', type=str, default='localhost',
        help='Database host (default: localhost)'
    )
    parser.add_argument(
        '--port', type=int, default=5433,
        help='Database port (default: 5433)'
    )

    args = parser.parse_args()

    # Set default paths
    script_dir = Path(__file__).parent
    input_path = Path(args.input) if args.input else script_dir / 'pilot_documents.json'
    output_path = Path(args.output) if args.output else script_dir / 'baseline_entities.json'

    # Validate input exists
    if not input_path.exists():
        print(f"ERROR: {input_path} not found")
        print("Run select_pilot_documents.py first")
        return 1

    try:
        # Load pilot documents
        print(f"Loading pilot documents from: {input_path}")
        pilot_docs = load_pilot_documents(str(input_path))
        print(f"Loaded {len(pilot_docs)} documents")
        print()

        # Connect to database
        print(f"Connecting to database at {args.host}:{args.port}...")
        conn = connect_db(host=args.host, port=args.port)
        print("Connected successfully")

        # Extract baseline
        baseline = extract_baseline_entities(conn, pilot_docs)
        conn.close()

        if not baseline:
            print("ERROR: No entities extracted")
            return 1

        # Save results
        save_baseline(baseline, str(output_path))

        # Generate stats
        generate_stats(baseline)

        print()
        print("=" * 70)
        print("EXTRACTION COMPLETE")
        print("=" * 70)
        print()
        print("Next steps:")
        print(f"  1. Review {output_path}")
        print("  2. Run reextract_pilot.py")

        return 0

    except psycopg2.OperationalError as e:
        print(f"ERROR: Database connection failed: {e}")
        print()
        print("Make sure you can access the database:")
        print(f"  ssh -L {args.port}:localhost:5433 darren@202.61.196.119")
        return 1
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
