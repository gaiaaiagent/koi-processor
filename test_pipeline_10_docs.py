#!/usr/bin/env python3
"""
Quick Pipeline Test - 10 Documents
Fast test of KOI processing pipeline
"""

import asyncio
import json
from pathlib import Path
from datetime import datetime, timezone
import sys
import random
import time

# Import our enhanced processor
from process_all_documents_mistral import ProductionMetabolicProcessor

async def quick_test():
    """Quick test with 10 documents"""
    print("🌿 KOI Quick Pipeline Test")
    print("🧪 Testing with 10 documents")
    print("=" * 50)
    
    # Initialize processor
    processor = ProductionMetabolicProcessor(model="mistral:7b")
    
    # Find documents
    data_dir = Path("/Users/darrenzal/projects/RegenAI/GAIA/data")
    patterns = ["*.md", "*.json", "*.txt"]
    files = []
    
    for pattern in patterns:
        found_files = list(data_dir.rglob(pattern))
        found_files = [f for f in found_files if "twitter" not in str(f).lower()]
        files.extend(found_files)
    
    # Select 10 random files
    random.seed(42)
    test_files = random.sample(files, min(10, len(files)))
    
    print(f"📊 Selected {len(test_files)} test documents:")
    for f in test_files:
        source = processor._identify_source(f)
        print(f"  • {f.name} ({source})")
    
    print(f"\n🚀 Starting processing...")
    
    # Process documents
    results = []
    start_time = time.time()
    
    for i, file_path in enumerate(test_files):
        print(f"\n🔄 Processing {i+1}/{len(test_files)}: {file_path.name}")
        
        try:
            result = await processor.process_document(file_path)
            if result:
                results.append(result)
                entities = result.get('entities', [])
                print(f"  ✅ Extracted {len(entities)} entities")
                
                # Show sample entities
                for j, entity in enumerate(entities[:3]):
                    name = entity.get('name', 'Unknown')
                    entity_type = entity.get('@type', 'Unknown').split(':')[-1]
                    print(f"     {j+1}. {entity_type}: {name[:50]}{'...' if len(name) > 50 else ''}")
            else:
                print(f"  ❌ Failed to process")
                
        except Exception as e:
            print(f"  ❌ Error: {e}")
    
    # Summary
    total_time = time.time() - start_time
    total_entities = sum(len(r.get('entities', [])) for r in results)
    
    print(f"\n" + "=" * 50)
    print("📊 QUICK TEST SUMMARY")
    print("=" * 50)
    print(f"Documents processed: {len(results)}/{len(test_files)}")
    print(f"Total entities: {total_entities}")
    print(f"Processing time: {total_time:.1f} seconds")
    print(f"Avg per document: {total_time/max(len(results), 1):.1f} seconds")
    
    if total_entities > 0:
        print(f"Avg entities per doc: {total_entities/max(len(results), 1):.1f}")
    
    # Save quick test results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(f"/Users/darrenzal/projects/RegenAI/koi-processor/quick-test-{timestamp}.json")
    
    output = {
        "metadata": {
            "test_type": "quick_pipeline_test",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "model": processor.model,
            "ontology_version": processor.ontology_version,
            "documents_tested": len(test_files),
            "successful_processing": len(results),
            "total_entities": total_entities,
            "processing_time": total_time
        },
        "results": results
    }
    
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)
    
    print(f"\n✅ Quick test complete!")
    print(f"📁 Results saved to: {output_path}")
    
    return len(results) > 0

if __name__ == "__main__":
    success = asyncio.run(quick_test())
    if success:
        print("\n🎉 Pipeline is working! Ready for full test.")
    else:
        print("\n⚠️  Pipeline needs debugging before full test.")