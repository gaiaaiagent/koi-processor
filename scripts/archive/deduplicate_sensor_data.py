#!/usr/bin/env python3
"""
Deduplicate Sensor Data
Removes duplicate documents from sensor outputs based on URL (primary) or RID (fallback)
Preserves the most recent/complete version of each document
"""
import json
import shutil
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from typing import List, Dict, Any, Tuple

def get_dedup_key(doc: Dict[str, Any]) -> str:
    """
    Generate deduplication key for a document
    Priority: URL > RID > ID > content hash
    """
    # Priority 1: URL (most reliable for web content)
    if doc.get('url'):
        return f"url:{doc['url']}"

    # Priority 2: RID (Discourse, Telegram use this)
    if doc.get('rid'):
        return f"rid:{doc['rid']}"

    # Priority 3: ID (generic identifier)
    if doc.get('id'):
        return f"id:{doc['id']}"

    # Priority 4: Content hash (last resort)
    content = doc.get('content', '') or doc.get('text', '')
    if content:
        import hashlib
        content_hash = hashlib.md5(content.encode()).hexdigest()
        return f"hash:{content_hash}"

    # No reliable key - treat as unique
    return f"unique:{id(doc)}"

def select_best_version(docs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    When multiple versions of a document exist, select the best one
    Criteria: Most complete content, most metadata, most recent
    """
    if len(docs) == 1:
        return docs[0]

    # Score each document
    scores = []
    for doc in docs:
        score = 0

        # Content length (more is better)
        content = doc.get('content', '') or doc.get('text', '')
        score += len(content)

        # Metadata completeness
        score += len(doc.keys()) * 100

        # Has title
        if doc.get('title'):
            score += 500

        # Has metadata dict
        if doc.get('metadata'):
            score += 200

        # Has timestamp (prefer newer)
        if doc.get('timestamp'):
            score += 100

        scores.append((score, doc))

    # Return highest scoring document
    scores.sort(key=lambda x: x[0], reverse=True)
    return scores[0][1]

def deduplicate_sensor(
    input_dir: Path,
    output_dir: Path,
    sensor_name: str,
    backup: bool = True
) -> Tuple[int, int, int]:
    """
    Deduplicate a single sensor's output

    Returns:
        (total_docs, unique_docs, duplicates_removed)
    """
    print(f"\n{'='*70}")
    print(f"Deduplicating: {sensor_name}")
    print(f"Input: {input_dir}")
    print(f"Output: {output_dir}")
    print('='*70)

    if not input_dir.exists():
        print(f"⚠️  Input directory does not exist")
        return (0, 0, 0)

    # Load all documents
    all_docs = []
    file_count = 0

    for json_file in sorted(input_dir.glob('*.json')):
        try:
            with open(json_file) as f:
                data = json.load(f)

                # Handle different JSON structures
                if 'documents' in data:
                    docs = data['documents']
                elif isinstance(data, list):
                    docs = data
                elif isinstance(data, dict) and 'content' in data:
                    docs = [data]
                else:
                    print(f"  ⚠️  Unknown structure in {json_file.name}, skipping")
                    continue

                all_docs.extend(docs)
                file_count += 1
        except Exception as e:
            print(f"  ⚠️  Error loading {json_file.name}: {e}")

    if not all_docs:
        print(f"  No documents found in {file_count} files")
        return (0, 0, 0)

    print(f"\n📊 Before deduplication:")
    print(f"  Files: {file_count}")
    print(f"  Total documents: {len(all_docs)}")

    # Backup original directory
    if backup and input_dir.exists():
        backup_dir = input_dir.parent / f"{input_dir.name}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        print(f"\n💾 Creating backup: {backup_dir}")
        shutil.copytree(input_dir, backup_dir)

    # Group documents by dedup key
    doc_groups = defaultdict(list)
    for doc in all_docs:
        key = get_dedup_key(doc)
        doc_groups[key].append(doc)

    # Select best version for each group
    unique_docs = []
    duplicates_removed = 0

    for key, docs in doc_groups.items():
        if len(docs) > 1:
            duplicates_removed += len(docs) - 1
        best_doc = select_best_version(docs)
        unique_docs.append(best_doc)

    print(f"\n📊 After deduplication:")
    print(f"  Unique documents: {len(unique_docs)}")
    print(f"  Duplicates removed: {duplicates_removed}")
    print(f"  Deduplication rate: {duplicates_removed/len(all_docs)*100:.1f}%")

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write deduplicated documents
    output_file = output_dir / f"{sensor_name}_deduplicated_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    output_data = {
        'metadata': {
            'source': sensor_name,
            'deduplicated_at': datetime.now().isoformat(),
            'original_count': len(all_docs),
            'unique_count': len(unique_docs),
            'duplicates_removed': duplicates_removed,
            'deduplication_rate': f"{duplicates_removed/len(all_docs)*100:.1f}%"
        },
        'documents': unique_docs
    }

    with open(output_file, 'w') as f:
        json.dump(output_data, f, indent=2, default=str)

    print(f"\n✅ Wrote deduplicated data:")
    print(f"   {output_file}")
    print(f"   Size: {output_file.stat().st_size / 1024 / 1024:.2f} MB")

    return (len(all_docs), len(unique_docs), duplicates_removed)

def main():
    print("="*70)
    print("SENSOR DATA DEDUPLICATION")
    print("="*70)

    base_dir = Path('/opt/projects/koi-sensors/sensors')

    # Define sensors to deduplicate
    sensors = {
        'discourse': {
            'input': base_dir / 'discourse' / 'output',
            'output': base_dir / 'discourse' / 'deduplicated',
            'name': 'discourse'
        },
        'telegram': {
            'input': base_dir / 'telegram' / 'output',
            'output': base_dir / 'telegram' / 'deduplicated',
            'name': 'telegram'
        },
        # Add more sensors as needed
    }

    results = {}

    for sensor_id, config in sensors.items():
        total, unique, dupes = deduplicate_sensor(
            input_dir=config['input'],
            output_dir=config['output'],
            sensor_name=config['name'],
            backup=True
        )

        if total > 0:
            results[sensor_id] = {
                'total': total,
                'unique': unique,
                'duplicates': dupes
            }

    # Overall summary
    print("\n" + "="*70)
    print("OVERALL SUMMARY")
    print("="*70)

    if not results:
        print("\n⚠️  No sensors processed")
        return

    total_docs = sum(r['total'] for r in results.values())
    total_unique = sum(r['unique'] for r in results.values())
    total_dupes = sum(r['duplicates'] for r in results.values())

    print(f"\nTotal documents processed: {total_docs:,}")
    print(f"Unique documents: {total_unique:,}")
    print(f"Duplicates removed: {total_dupes:,}")
    print(f"Overall deduplication rate: {total_dupes/total_docs*100:.1f}%")

    print(f"\n💰 Cost Savings:")
    print(f"  Documents saved: {total_dupes:,}")
    print(f"  Batch API savings (~$0.003/doc): ${total_dupes * 0.003:.2f}")
    print(f"  Real-time API savings (~$0.007/doc): ${total_dupes * 0.007:.2f}")

    print("\n📋 By Sensor:")
    for sensor_id, result in results.items():
        print(f"  {sensor_id}:")
        print(f"    Before: {result['total']:,} docs")
        print(f"    After: {result['unique']:,} docs")
        print(f"    Removed: {result['duplicates']:,} duplicates ({result['duplicates']/result['total']*100:.1f}%)")

    print("\n✅ Deduplication complete!")
    print("\nNext steps:")
    print("  1. Review deduplicated files in sensors/*/deduplicated/")
    print("  2. If satisfied, update extraction scripts to use deduplicated data")
    print("  3. Original data backed up to sensors/*/output_backup_*/")

if __name__ == '__main__':
    main()
