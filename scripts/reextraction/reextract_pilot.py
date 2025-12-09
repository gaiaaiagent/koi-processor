#!/usr/bin/env python3
"""
Re-process pilot documents through the quality pipeline.

This script takes the baseline entities and re-processes them through
the post-processing pipeline to see what would be filtered/transformed.

NOTE: This script does NOT re-run LLM extraction. It processes existing
entities through the quality pipeline to measure improvement.

For true re-extraction (including LLM), see the full re-extraction plan.

Input: pilot_documents.json, baseline_entities.json
Output: pilot_results.json

Usage:
    python scripts/reextraction/reextract_pilot.py
    python scripts/reextraction/reextract_pilot.py --baseline custom_baseline.json
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

# Import pipeline framework
try:
    from knowledge_graph.postprocessing import (
        PipelineOrchestrator,
        ProcessingContext,
        Entity as PipelineEntity,
        Relationship as PipelineRelationship,
        create_pipeline_from_config
    )
    from knowledge_graph.postprocessing.modules import (
        ConfidenceFilterModule,
        CanonicalResolverModule,
        EntityQualityFilterModule,
        ListSplitterModule,
        OntologyNormalizerModule
    )
    HAS_PIPELINE = True
except ImportError as e:
    print(f"ERROR: Pipeline framework not available: {e}")
    print("Make sure you're running from the koi-processor directory")
    HAS_PIPELINE = False


def load_baseline(input_path: str) -> Dict[str, Dict]:
    """Load baseline entities from JSON."""
    with open(input_path, 'r') as f:
        data = json.load(f)

    # Handle both formats
    if 'documents' in data:
        return data['documents']
    return data


def create_pipeline(config_path: Optional[str] = None) -> PipelineOrchestrator:
    """Create the post-processing pipeline."""
    if config_path and Path(config_path).exists():
        return create_pipeline_from_config(config_path)

    # Default config path
    default_config = Path(__file__).parent.parent.parent / 'src/knowledge_graph/config/pipeline_config.json'
    if default_config.exists():
        return create_pipeline_from_config(str(default_config))

    # Create default pipeline
    return PipelineOrchestrator([
        ConfidenceFilterModule({'entity_threshold': 0.70, 'relationship_threshold': 0.80}),
        CanonicalResolverModule(),
        EntityQualityFilterModule(),
        ListSplitterModule(),
        OntologyNormalizerModule()
    ])


def convert_to_pipeline_entities(entities: List[Dict]) -> List[PipelineEntity]:
    """Convert baseline entities to pipeline Entity objects."""
    pipeline_entities = []

    for e in entities:
        # Extract name - handle different formats
        name = e.get('name', e.get('entity', ''))
        if not name:
            continue

        # Extract type
        entity_type = e.get('type', e.get('entity_type', 'UNKNOWN'))

        # Extract confidence
        confidence = e.get('confidence')
        if confidence is not None:
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                confidence = None

        # Create pipeline entity
        pipeline_entities.append(PipelineEntity(
            name=name,
            type=entity_type,
            confidence=confidence,
            metadata={
                'chunk_rid': e.get('chunk_rid'),
                'extraction_id': e.get('extraction_id'),
                'original_metadata': e.get('metadata', {})
            }
        ))

    return pipeline_entities


def process_document(
    pipeline: PipelineOrchestrator,
    doc_rid: str,
    baseline_data: Dict
) -> Dict[str, Any]:
    """
    Process a single document through the pipeline.

    Args:
        pipeline: The processing pipeline
        doc_rid: Document RID
        baseline_data: Baseline data for this document

    Returns:
        Results dictionary with before/after comparison
    """
    extraction = baseline_data.get('extraction', {})
    baseline_entities = extraction.get('entities', [])
    baseline_relations = extraction.get('relations', [])

    # Convert to pipeline format
    pipeline_entities = convert_to_pipeline_entities(baseline_entities)

    # Create context
    context = ProcessingContext(entities=pipeline_entities)

    # Reset pipeline stats
    pipeline.reset()

    # Process through pipeline
    result_context = pipeline.process(context)

    # Get results
    passed_entities = list(result_context.entities)
    blocked_entities = list(result_context.blocked_entities)

    # Track changes
    changes = {
        'blocked': [],
        'modified': [],
        'split': [],
        'type_normalized': []
    }

    # Map blocked entities
    for blocked in blocked_entities:
        changes['blocked'].append({
            'name': blocked.name,
            'type': blocked.type,
            'confidence': blocked.confidence,
            'reason': blocked.metadata.get('block_reason', 'Unknown'),
            'blocked_by': blocked.metadata.get('blocked_by', 'Unknown')
        })

    # Map passed entities - check for modifications
    original_names = {e.get('name', e.get('entity', '')): e for e in baseline_entities}

    for entity in passed_entities:
        # Check if this came from splitting
        if entity.metadata.get('split_from'):
            changes['split'].append({
                'original': entity.metadata['split_from'],
                'result': entity.name,
                'type': entity.type
            })
        # Check for name modifications (canonical resolution)
        elif entity.metadata.get('original_name'):
            changes['modified'].append({
                'original': entity.metadata['original_name'],
                'resolved': entity.name,
                'type': entity.type
            })
        # Check for type normalization
        if entity.metadata.get('original_type'):
            changes['type_normalized'].append({
                'name': entity.name,
                'original_type': entity.metadata['original_type'],
                'normalized_type': entity.type
            })

    # Get pipeline statistics
    pipeline_stats = pipeline.get_statistics()

    return {
        'document_rid': doc_rid,
        'document': baseline_data.get('document', {}),
        'baseline': {
            'entity_count': len(baseline_entities),
            'relation_count': len(baseline_relations)
        },
        'pipeline_result': {
            'passed_count': len(passed_entities),
            'blocked_count': len(blocked_entities),
            'passed_entities': [
                {
                    'name': e.name,
                    'type': e.type,
                    'confidence': e.confidence
                }
                for e in passed_entities
            ],
            'blocked_entities': changes['blocked']
        },
        'changes': changes,
        'statistics': {
            'block_rate': len(blocked_entities) / len(baseline_entities) * 100 if baseline_entities else 0,
            'pass_rate': len(passed_entities) / len(baseline_entities) * 100 if baseline_entities else 0,
            'modified_count': len(changes['modified']),
            'split_count': len(changes['split']),
            'type_normalized_count': len(changes['type_normalized'])
        },
        'pipeline_stats': pipeline_stats
    }


def reextract_documents(
    baseline: Dict[str, Dict],
    pipeline: PipelineOrchestrator
) -> Dict[str, Any]:
    """
    Re-process all baseline documents through the pipeline.

    Args:
        baseline: Baseline data dictionary
        pipeline: The processing pipeline

    Returns:
        Results for all documents
    """
    print("=" * 70)
    print("PIPELINE RE-PROCESSING")
    print("=" * 70)
    print()
    print(f"Processing {len(baseline)} documents...")
    print()

    results = {}
    total_baseline = 0
    total_passed = 0
    total_blocked = 0
    errors = []

    for i, (doc_rid, doc_data) in enumerate(baseline.items(), 1):
        # Progress indicator
        if i % 10 == 0 or i == len(baseline):
            print(f"  [{i:3d}/{len(baseline)}] Processing...")

        try:
            result = process_document(pipeline, doc_rid, doc_data)
            results[doc_rid] = result

            total_baseline += result['baseline']['entity_count']
            total_passed += result['pipeline_result']['passed_count']
            total_blocked += result['pipeline_result']['blocked_count']

        except Exception as e:
            errors.append({'document_rid': doc_rid, 'error': str(e)})
            print(f"    ERROR processing {doc_rid}: {e}")

    print()
    print(f"Processed {len(results)} documents")
    print(f"Total entities: {total_baseline}")
    print(f"  Passed: {total_passed} ({total_passed/total_baseline*100:.1f}%)" if total_baseline else "  Passed: 0")
    print(f"  Blocked: {total_blocked} ({total_blocked/total_baseline*100:.1f}%)" if total_baseline else "  Blocked: 0")
    if errors:
        print(f"Errors: {len(errors)}")

    return {
        'results': results,
        'summary': {
            'document_count': len(results),
            'total_baseline_entities': total_baseline,
            'total_passed': total_passed,
            'total_blocked': total_blocked,
            'overall_pass_rate': total_passed / total_baseline * 100 if total_baseline else 0,
            'overall_block_rate': total_blocked / total_baseline * 100 if total_baseline else 0
        },
        'errors': errors
    }


def save_results(data: Dict, output_path: str):
    """Save results to JSON."""
    output = {
        'generated_at': datetime.utcnow().isoformat(),
        **data
    }

    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2, default=str)

    print()
    print(f"Saved to: {output_path}")


def generate_summary(data: Dict):
    """Generate and display summary statistics."""
    print()
    print("-" * 70)
    print("RE-PROCESSING SUMMARY")
    print("-" * 70)

    summary = data.get('summary', {})
    results = data.get('results', {})

    print(f"\nOverall:")
    print(f"  Documents processed: {summary.get('document_count', 0)}")
    print(f"  Total baseline entities: {summary.get('total_baseline_entities', 0)}")
    print(f"  Entities passed: {summary.get('total_passed', 0)} ({summary.get('overall_pass_rate', 0):.1f}%)")
    print(f"  Entities blocked: {summary.get('total_blocked', 0)} ({summary.get('overall_block_rate', 0):.1f}%)")

    # Aggregate block reasons
    block_reasons = {}
    modified_count = 0
    split_count = 0
    type_normalized_count = 0

    for doc_result in results.values():
        changes = doc_result.get('changes', {})

        # Count blocked by reason
        for blocked in changes.get('blocked', []):
            reason = blocked.get('blocked_by', 'Unknown')
            block_reasons[reason] = block_reasons.get(reason, 0) + 1

        modified_count += len(changes.get('modified', []))
        split_count += len(changes.get('split', []))
        type_normalized_count += len(changes.get('type_normalized', []))

    if block_reasons:
        print(f"\nBlocked by module:")
        for module, count in sorted(block_reasons.items(), key=lambda x: -x[1]):
            pct = count / summary.get('total_blocked', 1) * 100
            print(f"  {module:30s}: {count:5d} ({pct:5.1f}%)")

    print(f"\nTransformations:")
    print(f"  Names modified (canonical): {modified_count}")
    print(f"  Entities split (list): {split_count}")
    print(f"  Types normalized: {type_normalized_count}")

    # Sample blocked entities
    print(f"\nSample blocked entities (first 10):")
    sample_count = 0
    for doc_result in results.values():
        for blocked in doc_result.get('changes', {}).get('blocked', []):
            if sample_count >= 10:
                break
            print(f"  '{blocked['name']}' ({blocked['type']}) - {blocked['reason']}")
            sample_count += 1
        if sample_count >= 10:
            break


def main():
    """Main execution."""
    if not HAS_PIPELINE:
        print("Pipeline framework not available. Cannot continue.")
        return 1

    parser = argparse.ArgumentParser(
        description="Re-process pilot documents through quality pipeline"
    )
    parser.add_argument(
        '--baseline', '-b', type=str, default=None,
        help='Baseline file path (default: scripts/reextraction/baseline_entities.json)'
    )
    parser.add_argument(
        '--output', '-o', type=str, default=None,
        help='Output file path (default: scripts/reextraction/pilot_results.json)'
    )
    parser.add_argument(
        '--config', '-c', type=str, default=None,
        help='Pipeline config file path (default: src/knowledge_graph/config/pipeline_config.json)'
    )

    args = parser.parse_args()

    # Set default paths
    script_dir = Path(__file__).parent
    baseline_path = Path(args.baseline) if args.baseline else script_dir / 'baseline_entities.json'
    output_path = Path(args.output) if args.output else script_dir / 'pilot_results.json'

    # Validate input exists
    if not baseline_path.exists():
        print(f"ERROR: {baseline_path} not found")
        print("Run extract_baseline_entities.py first")
        return 1

    try:
        # Load baseline
        print(f"Loading baseline from: {baseline_path}")
        baseline = load_baseline(str(baseline_path))
        print(f"Loaded {len(baseline)} documents")
        print()

        # Create pipeline
        print("Creating pipeline...")
        pipeline = create_pipeline(args.config)
        print(f"Pipeline created with {len(pipeline)} modules:")
        for i, module in enumerate(pipeline.modules, 1):
            print(f"  {i}. {module.get_name()}")
        print()

        # Process documents
        results = reextract_documents(baseline, pipeline)

        # Save results
        save_results(results, str(output_path))

        # Generate summary
        generate_summary(results)

        print()
        print("=" * 70)
        print("RE-PROCESSING COMPLETE")
        print("=" * 70)
        print()
        print("Next steps:")
        print(f"  1. Review {output_path}")
        print("  2. Run compare_extractions.py for detailed analysis")

        return 0

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
