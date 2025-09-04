#!/usr/bin/env python3
"""
Test KOI Processing Pipeline on 100 Documents
Tests the complete pipeline with provenance tracking and unified ontology
"""

import asyncio
import json
from pathlib import Path
from datetime import datetime, timezone
import sys
import random

# Import our enhanced processor
from process_all_documents_mistral import ProductionMetabolicProcessor

class TestPipelineProcessor(ProductionMetabolicProcessor):
    """Test processor limited to 100 documents"""
    
    def __init__(self, model: str = "mistral:7b"):
        super().__init__(model)
        self.max_documents = 100
        
    async def process_directory_limited(self, directory: Path, max_docs: int = 100) -> None:
        """Process limited number of documents for testing"""
        overall_start = time.time()
        
        # Find all documents excluding Twitter
        patterns = ["*.md", "*.json", "*.txt"]
        files = []
        
        print("📂 Scanning for documents...")
        for pattern in patterns:
            found_files = list(directory.rglob(pattern))
            # Exclude Twitter and test documents
            found_files = [f for f in found_files 
                          if "twitter" not in str(f).lower() 
                          and "test-documents" not in str(f)]
            files.extend(found_files)
        
        # Randomly sample max_docs files for diverse testing
        if len(files) > max_docs:
            files = random.sample(files, max_docs)
        
        self.stats.total_documents = len(files)
        
        print(f"📊 Selected {len(files)} documents for pipeline test")
        print(f"🤖 Using Mistral 7B with unified ontology v1")
        print(f"⏱️  Estimated time: {len(files) * 8 / 60:.1f} minutes\n")
        
        # Show document distribution by source
        source_counts = {}
        for f in files:
            source = self._identify_source(f)
            source_counts[source] = source_counts.get(source, 0) + 1
        
        print("📈 Test Document Distribution:")
        for source, count in sorted(source_counts.items(), key=lambda x: x[1], reverse=True):
            print(f"  {source}: {count} documents")
        print()
        
        # Process in smaller batches for better monitoring
        batch_size = 5
        for i in range(0, len(files), batch_size):
            batch = files[i:i+batch_size]
            batch_start = time.time()
            
            print(f"🔄 Processing batch {i//batch_size + 1}/{(len(files) + batch_size - 1)//batch_size}")
            print(f"   Files: {', '.join([f.name for f in batch])}")
            
            # Process batch
            tasks = [self.process_document(f) for f in batch]
            results = await asyncio.gather(*tasks)
            
            # Store results
            for result in results:
                if result:
                    self.processed_entities.append(result)
            
            # Progress update
            batch_time = time.time() - batch_start
            print(f"  ✅ Batch completed in {batch_time:.1f}s")
            print(f"  📊 Progress: {self.stats.processed_documents}/{self.stats.total_documents} docs")
            print(f"  🎯 Entities extracted: {self.stats.entities_extracted} total")
            print(f"  🧠 Discourse elements: {self.stats.discourse_elements} total")
            
            # Show recent entities from last batch
            if results and any(results):
                recent_entities = []
                for result in results[-3:]:  # Show last 3 successful results
                    if result and result.get('entities'):
                        for entity in result['entities'][:2]:  # Show 2 entities per doc
                            recent_entities.append({
                                'name': entity.get('name', 'Unknown')[:40] + ('...' if len(entity.get('name', '')) > 40 else ''),
                                'type': entity.get('@type', 'Unknown').split(':')[-1],
                                'source': result['metadata'].get('filename', 'Unknown')
                            })
                
                if recent_entities:
                    print(f"  🎨 Recent extractions:")
                    for ent in recent_entities[:4]:  # Show max 4
                        print(f"     • {ent['type']}: '{ent['name']}' from {ent['source']}")
            
            print()
        
        self.stats.processing_time = time.time() - overall_start
        
    def print_test_summary(self):
        """Print detailed test summary"""
        print("\n" + "=" * 70)
        print("🧪 TEST PIPELINE SUMMARY")
        print("=" * 70)
        print(f"Model: {self.model}")
        print(f"Ontology: {self.ontology_version}")
        print(f"Documents processed: {self.stats.processed_documents}/{self.stats.total_documents}")
        print(f"Success rate: {100 * self.stats.processed_documents / max(self.stats.total_documents, 1):.1f}%")
        print(f"Failed documents: {self.stats.failed_documents}")
        print(f"Total entities: {self.stats.entities_extracted}")
        print(f"Discourse elements: {self.stats.discourse_elements}")
        print(f"Total time: {self.stats.processing_time/60:.1f} minutes")
        print(f"Avg time per doc: {self.stats.processing_time / max(self.stats.processed_documents, 1):.1f} seconds")
        
        if self.stats.entities_extracted > 0:
            print(f"Avg entities per doc: {self.stats.entities_extracted / max(self.stats.processed_documents, 1):.1f}")
            if self.stats.discourse_elements > 0:
                print(f"Discourse ratio: {100 * self.stats.discourse_elements / self.stats.entities_extracted:.1f}%")
        
        # Show entity type distribution
        type_counts = {}
        for doc in self.processed_entities:
            if doc and 'entities' in doc:
                for entity in doc['entities']:
                    entity_type = entity.get('@type', 'Unknown').split(':')[-1]
                    type_counts[entity_type] = type_counts.get(entity_type, 0) + 1
        
        print(f"\n🎯 Entity Type Distribution:")
        for entity_type, count in sorted(type_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
            percentage = 100 * count / max(self.stats.entities_extracted, 1)
            print(f"  {entity_type}: {count} ({percentage:.1f}%)")
        
        # Show alignment distribution
        alignment_counts = {"Re-Whole Value": 0, "Nest Caring": 0, "Harmonize Agency": 0}
        for doc in self.processed_entities:
            if doc and 'entities' in doc:
                for entity in doc['entities']:
                    alignments = entity.get('alignsWith', [])
                    for alignment in alignments:
                        if alignment in alignment_counts:
                            alignment_counts[alignment] += 1
        
        print(f"\n🎨 Essence Alignment Distribution:")
        total_alignments = sum(alignment_counts.values())
        for alignment, count in alignment_counts.items():
            percentage = 100 * count / max(total_alignments, 1)
            print(f"  {alignment}: {count} ({percentage:.1f}%)")

# Add missing import
import time

async def main():
    """Test the complete processing pipeline"""
    print("🌿 KOI Processing Pipeline Test")
    print("🧪 Testing with 100 documents from real data")
    print("=" * 70)
    
    # Initialize test processor
    processor = TestPipelineProcessor(model="mistral:7b")
    
    # Set random seed for reproducible testing
    random.seed(42)
    
    # Process limited documents from GAIA data
    data_dir = Path("/Users/darrenzal/projects/RegenAI/GAIA/data")
    
    print("🚀 Starting pipeline test...")
    print("This will test our unified ontology extraction with provenance tracking\n")
    
    await processor.process_directory_limited(data_dir, max_docs=100)
    
    # Print detailed test summary
    processor.print_test_summary()
    
    # Save test results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    test_output = Path(f"/Users/darrenzal/projects/RegenAI/koi-processor/pipeline-test-{timestamp}.json")
    processor.save_results(test_output)
    
    # Create a test report with sample entities
    test_report = {
        "test_metadata": {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "pipeline_version": "koi-v1.0",
            "ontology_version": processor.ontology_version,
            "model": processor.model,
            "test_size": 100
        },
        "test_results": {
            "documents_processed": processor.stats.processed_documents,
            "entities_extracted": processor.stats.entities_extracted,
            "discourse_elements": processor.stats.discourse_elements,
            "processing_time_minutes": processor.stats.processing_time / 60,
            "success_rate": processor.stats.processed_documents / max(processor.stats.total_documents, 1)
        },
        "sample_entities": []
    }
    
    # Add sample entities for review
    sample_count = 0
    for doc in processor.processed_entities[:10]:  # From first 10 docs
        if doc and 'entities' in doc:
            for entity in doc['entities'][:3]:  # Up to 3 entities per doc
                if sample_count < 20:  # Limit to 20 samples
                    test_report["sample_entities"].append({
                        "name": entity.get('name', 'Unknown'),
                        "type": entity.get('@type', 'Unknown'),
                        "alignsWith": entity.get('alignsWith', []),
                        "source_document": doc['metadata'].get('filename', 'Unknown'),
                        "ontology_version": entity.get('ontologyVersion', 'Unknown')
                    })
                    sample_count += 1
    
    # Save test report
    report_path = Path(f"/Users/darrenzal/projects/RegenAI/koi-processor/pipeline-test-report-{timestamp}.json")
    with open(report_path, 'w') as f:
        json.dump(test_report, f, indent=2)
    
    print(f"\n" + "=" * 70)
    print("✅ Pipeline test completed successfully!")
    print(f"📁 Full results: {test_output}")
    print(f"📁 Test report: {report_path}")
    print("\n🔍 Next steps:")
    print("1. Review sample entities in the test report")
    print("2. Check ontology alignment accuracy")  
    print("3. Validate provenance tracking")
    print("4. Scale up to full dataset processing")

if __name__ == "__main__":
    asyncio.run(main())