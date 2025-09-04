#!/usr/bin/env python3
"""
Resume KOI processing from checkpoint
"""

import json
import asyncio
from pathlib import Path
from full_dataset_pipeline import FullDatasetKOIProcessor
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('full_dataset_processing.log', mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

async def resume_processing():
    """Resume processing from last checkpoint"""
    
    checkpoint_path = Path("full-dataset-checkpoint.json")
    
    if not checkpoint_path.exists():
        print("❌ No checkpoint file found. Starting from beginning...")
        return
    
    # Load checkpoint
    with open(checkpoint_path, 'r') as f:
        checkpoint = json.load(f)
    
    # Use processed_documents as the resume point since last_processed may be outdated
    last_processed = checkpoint.get('processed_documents', 0)
    print(f"🔄 Resuming from checkpoint: document {last_processed}")
    print(f"📊 Previous progress: {checkpoint['processed_documents']} docs, {checkpoint['entities_extracted']} entities")
    
    # Get all documents from GAIA data directory (same as original pipeline)
    data_dir = Path("/Users/darrenzal/projects/RegenAI/GAIA/data")
    patterns = ["*.md", "*.json", "*.txt"]
    files = []
    
    print("📂 Scanning complete dataset...")
    for pattern in patterns:
        found_files = list(data_dir.rglob(pattern))
        found_files = [f for f in found_files if "twitter" not in str(f).lower()]
        files.extend(found_files)
    
    files = sorted(files)  # Ensure consistent ordering
    
    if not data_dir.exists():
        print(f"❌ Data directory not found: {data_dir}")
        return
        
    print(f"📂 Total documents available: {len(files)}")
    
    if last_processed >= len(files):
        print("✅ All documents already processed!")
        return
    
    # Resume from checkpoint
    remaining_files = files[last_processed:]
    print(f"📈 Resuming processing of remaining {len(remaining_files)} documents...")
    print(f"🎯 Next document: {remaining_files[0].name if remaining_files else 'None'}")
    
    # Add restart entry to log
    logger.info(f"RESTART: Resuming from checkpoint at document {last_processed}")
    logger.info(f"RESTART: Previous stats - {checkpoint['processed_documents']} docs, {checkpoint['entities_extracted']} entities")
    logger.info(f"RESTART: Remaining documents: {len(remaining_files)}")
    
    # Initialize processor with existing stats
    processor = FullDatasetKOIProcessor()
    processor.stats.processed_documents = checkpoint['processed_documents']
    processor.stats.entities_extracted = checkpoint['entities_extracted'] 
    processor.stats.mistral_success = checkpoint['mistral_success']
    processor.stats.fallback_extractions = checkpoint['fallback_extractions']
    processor.stats.source_stats = checkpoint['source_stats']
    processor.last_checkpoint = checkpoint['processed_documents']
    
    print(f"\n🚀 Resuming processing from document {last_processed + 1}...")
    
    # Continue processing from where we left off
    import time
    from datetime import datetime, timedelta
    
    batch_size = 10
    start_time = time.time()
    
    for i in range(0, len(remaining_files), batch_size):
        batch = remaining_files[i:i+batch_size]
        batch_start = time.time()
        
        # Adjust batch numbers to continue from where we left off
        batch_num = (last_processed + i) // batch_size + 1
        total_batches = (len(files) + batch_size - 1) // batch_size
        current_doc_start = last_processed + i + 1
        
        print(f"\n📦 Batch {batch_num}/{total_batches} (docs {current_doc_start}-{min(current_doc_start + batch_size - 1, len(files))})")
        
        for j, file_path in enumerate(batch):
            doc_num = last_processed + i + j + 1  # Continue document numbering
            progress_pct = 100 * doc_num / len(files)
            progress_bar = "█" * int(progress_pct / 3.33) + "░" * (30 - int(progress_pct / 3.33))
            
            print(f"  [{doc_num:4d}/{len(files)}] [{progress_bar}] {file_path.name[:30]:<30}", end=" ", flush=True)
            
            doc_start = time.time()
            
            try:
                # Process document directly using the processor's process_document method
                result = await processor.process_document(file_path)
                
                if result:
                    entities = result['entities']
                    entity_count = len(entities)
                    
                    doc_time = time.time() - doc_start
                    mistral_symbol = "🧠" if any(e.get('extractedBy') == 'mistral-full-dataset-v1' for e in entities) else "🔧"
                else:
                    entity_count = 0
                    doc_time = time.time() - doc_start
                    mistral_symbol = "❌"
                print(f"{mistral_symbol} {entity_count:2d}e {doc_time:5.1f}s")
                
            except Exception as e:
                print(f"❌ Error: {str(e)[:50]}...")
                logger.error(f"Error processing {file_path.name}: {e}")
        
        # Batch statistics
        batch_time = time.time() - batch_start
        total_time = (time.time() - start_time) / 60
        mistral_rate = 100 * processor.stats.mistral_success / max(processor.stats.processed_documents, 1)
        
        print(f"  📊 Batch: {batch_time:.1f}s | Total: {total_time:.1f}m | Mistral: {mistral_rate:.1f}%")
        print(f"  🎯 Entities: {processor.stats.entities_extracted} | Avg/doc: {processor.stats.entities_extracted/max(processor.stats.processed_documents,1):.1f}")
        
        # ETA calculation
        if processor.stats.processed_documents > last_processed + 10:
            avg_per_doc = total_time * 60 / (processor.stats.processed_documents - checkpoint['processed_documents'])
            remaining_docs = len(files) - processor.stats.processed_documents
            eta_hours = (remaining_docs * avg_per_doc) / 3600
            completion_time = datetime.now() + timedelta(hours=eta_hours)
            print(f"  ⏰ ETA: {eta_hours:.1f}h (completion ~{completion_time.strftime('%H:%M')})")
        
        # Save checkpoint every 50 documents
        if processor.stats.processed_documents >= processor.last_checkpoint + processor.checkpoint_interval:
            processor.save_checkpoint(checkpoint_path)
            processor.last_checkpoint = processor.stats.processed_documents
            print(f"  💾 Checkpoint saved at {processor.stats.processed_documents} documents")
    
    print(f"\n🎯 Processing complete! Final stats:")
    print(f"📊 Documents: {processor.stats.processed_documents}/{len(files)}")
    print(f"🎯 Entities: {processor.stats.entities_extracted}")
    print(f"🧠 Mistral success: {100 * processor.stats.mistral_success / max(processor.stats.processed_documents, 1):.1f}%")

if __name__ == "__main__":
    asyncio.run(resume_processing())