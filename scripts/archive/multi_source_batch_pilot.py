#!/usr/bin/env python3
"""
Multi-Source Batch API Pilot - 100 Documents
Stratified sampling across ALL available sources:
- NEW: Discourse sensor data (not yet ingested)
- EXISTING: Sample from already-extracted sources for comparison

This tests the FULL pipeline on diverse content types.
"""
import json
import os
import sys
import random
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime

# Add koi-processor to path
sys.path.insert(0, '/opt/projects/koi-processor')

def load_discourse_documents() -> List[Dict[str, Any]]:
    """Load Discourse documents from sensor output (NOT YET INGESTED)"""
    discourse_dir = Path('/opt/projects/koi-sensors/sensors/discourse/output')
    all_docs = []

    for json_file in sorted(discourse_dir.glob('discourse_*.json')):
        try:
            with open(json_file) as f:
                data = json.load(f)
                if 'documents' in data:
                    for doc in data['documents']:
                        doc['_source_category'] = 'discourse_new'
                        all_docs.append(doc)
        except Exception as e:
            print(f"Error loading {json_file}: {e}")

    return all_docs

def sample_existing_github() -> List[Dict[str, Any]]:
    """Sample from existing GitHub sources (for comparison)"""
    # These are placeholders - in reality we'd query from memories table or raw files
    # For now, return empty and focus on NEW discourse data
    return []

def stratified_sample_multi_source(n: int = 100) -> List[Dict[str, Any]]:
    """
    Create stratified sample across source types:
    - Discourse (new, unprocessed): 100 docs (focus of pilot)

    Note: We could add samples from existing sources, but since they're already
    processed, we focus the pilot on NEW unprocessed data (Discourse).
    """
    print("\n📊 Sampling Strategy:")
    print("  - Focus: NEW Discourse data (9,463 docs)")
    print("  - Target: 100 docs stratified by forum type")
    print("")

    discourse_docs = load_discourse_documents()
    print(f"  Loaded: {len(discourse_docs)} Discourse documents")

    # Stratify by Discourse source
    by_source = {}
    for doc in discourse_docs:
        source = doc.get('source', 'unknown')
        if source not in by_source:
            by_source[source] = []
        by_source[source].append(doc)

    print("\n  Source distribution:")
    for source, docs in by_source.items():
        print(f"    {source}: {len(docs)} docs")

    # Proportional sampling
    total_discourse = len(discourse_docs)
    samples = []

    for source, docs in by_source.items():
        proportion = len(docs) / total_discourse
        target_count = int(n * proportion)
        if target_count > 0:
            sample_size = min(target_count, len(docs))
            source_samples = random.sample(docs, sample_size)
            samples.extend(source_samples)
            print(f"  Sampled {sample_size} from {source}")

    # If we're short, top up from largest source
    if len(samples) < n:
        shortage = n - len(samples)
        largest_source = max(by_source.keys(), key=lambda k: len(by_source[k]))
        remaining = [d for d in by_source[largest_source] if d not in samples]
        if remaining:
            additional = random.sample(remaining, min(shortage, len(remaining)))
            samples.extend(additional)
            print(f"  Added {len(additional)} more from {largest_source} to reach {len(samples)} total")

    return samples

def create_batch_requests(documents: List[Dict[str, Any]], output_file: str):
    """
    Create JSONL file for Batch API with entity extraction requests
    """
    with open(output_file, 'w') as f:
        for idx, doc in enumerate(documents):
            content = doc.get('content', '')
            title = doc.get('title', 'Untitled')
            url = doc.get('url', '')
            source = doc.get('source', 'unknown')

            custom_id = f"{source.replace(':', '-')}-{doc.get('rid', idx)}"

            # Build extraction prompt
            prompt = f"""Extract entities from this document.

Source: {source}
Title: {title}
URL: {url}

Content:
{content[:5000]}

Extract these entity types:
- PERSON: People, authors, contributors
- ORGANIZATION: Companies, DAOs, foundations
- PROJECT: Initiatives, working groups
- CONCEPT: Ideas, methodologies
- TECHNOLOGY: Tools, blockchains, protocols
- EVENT: Meetings, launches
- LOCATION: Places, regions

Return JSON array with: name, type, confidence (0.7-1.0), properties.

IMPORTANT - Do NOT extract:
- Pronouns (we, they, it)
- Generic terms (validators, users)
- JIRA IDs (APP-123, ERC-20)
- URLs or paths
- Placeholders (Unknown, TBD)
- Template text"""

            request_body = {
                "model": "gpt-4o-2024-08-06",
                "messages": [
                    {"role": "system", "content": "You are an expert entity extraction system for knowledge graphs. Extract meaningful, specific entities only."},
                    {"role": "user", "content": prompt}
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "entity_extraction",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "entities": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "type": {"type": "string"},
                                            "confidence": {"type": "number"},
                                            "properties": {"type": "object"}
                                        },
                                        "required": ["name", "type", "confidence"]
                                    }
                                }
                            },
                            "required": ["entities"]
                        }
                    }
                },
                "temperature": 0.1,
                "max_tokens": 2000
            }

            batch_request = {
                "custom_id": custom_id,
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": request_body
            }

            f.write(json.dumps(batch_request) + '\n')

    print(f"\n✅ Created batch requests: {output_file}")
    print(f"   Total requests: {len(documents)}")

def submit_batch_job(input_file: str) -> str:
    """Submit batch job to OpenAI"""
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

    print(f"\n📤 Uploading batch file...")
    with open(input_file, 'rb') as f:
        batch_input_file = client.files.create(file=f, purpose="batch")

    print(f"   File ID: {batch_input_file.id}")

    print(f"\n🚀 Creating batch job...")
    batch = client.batches.create(
        input_file_id=batch_input_file.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
        metadata={
            "description": "Multi-source pilot - 100 docs with full post-processing",
            "project": "koi-processor",
            "timestamp": datetime.now().isoformat()
        }
    )

    print(f"   Batch ID: {batch.id}")
    print(f"   Status: {batch.status}")

    # Save batch info
    batch_info = {
        "batch_id": batch.id,
        "input_file_id": batch_input_file.id,
        "status": batch.status,
        "created_at": batch.created_at,
        "document_count": 100
    }

    info_file = '/tmp/multi_source_batch_pilot_info.json'
    with open(info_file, 'w') as f:
        json.dump(batch_info, f, indent=2, default=str)

    print(f"\n💾 Batch info saved: {info_file}")
    return batch.id

def main():
    print("=" * 70)
    print("Multi-Source Batch API Pilot")
    print("100 Documents with Full Post-Processing Pipeline")
    print("=" * 70)

    # Sample documents
    print("\n🎲 Creating stratified sample...")
    random.seed(42)
    sample_docs = stratified_sample_multi_source(n=100)
    print(f"\n✅ Final sample: {len(sample_docs)} documents")

    # Save sample
    sample_file = '/tmp/multi_source_pilot_sample.json'
    with open(sample_file, 'w') as f:
        json.dump(sample_docs, f, indent=2, default=str)
    print(f"   Sample saved: {sample_file}")

    # Create batch requests
    print("\n📝 Creating batch API requests...")
    batch_file = '/tmp/multi_source_batch_requests.jsonl'
    create_batch_requests(sample_docs, batch_file)

    file_size = Path(batch_file).stat().st_size
    print(f"   File size: {file_size:,} bytes ({file_size/1024:.1f} KB)")

    # Confirm submission
    print("\n" + "=" * 70)
    print("Ready to submit batch job")
    print("=" * 70)
    print(f"Documents: 100 (stratified across sources)")
    print(f"Estimated cost: ~$0.30 (Batch API)")
    print(f"Processing time: 24-48 hours")
    print(f"Post-processing: Full pipeline (quality filter + dedup)")
    print("")

    response = input("Submit batch job? (yes/no): ").strip().lower()

    if response == 'yes':
        batch_id = submit_batch_job(batch_file)

        print("\n" + "=" * 70)
        print("✅ Batch job submitted!")
        print("=" * 70)
        print(f"Batch ID: {batch_id}")
        print(f"\nCheck status: python3 /tmp/check_batch_status.py {batch_id}")
        print(f"Process results: python3 /tmp/process_batch_results.py {batch_id}")
    else:
        print("\n❌ Batch job NOT submitted")
        print(f"   Batch file: {batch_file}")

if __name__ == "__main__":
    main()
