#!/usr/bin/env python3
"""
Monitor Full Dataset Processing Progress
"""

import time
import json
from pathlib import Path
from datetime import datetime

def monitor_progress():
    """Monitor the progress of full dataset processing"""
    log_path = Path("/Users/darrenzal/projects/RegenAI/koi-processor/full_dataset_processing.log")
    checkpoint_path = Path("/Users/darrenzal/projects/RegenAI/koi-processor/full-dataset-checkpoint.json")
    
    print("🔍 KOI Full Dataset Processing Monitor")
    print("=" * 50)
    
    while True:
        try:
            # Check if log file exists
            if not log_path.exists():
                print("❌ Log file not found")
                break
            
            # Read log file
            with open(log_path, 'r') as f:
                lines = f.readlines()
            
            # Look for progress indicators
            total_docs = 0
            processed_docs = 0
            entities_extracted = 0
            current_batch = 0
            mistral_success_rate = 0
            
            for line in lines:
                if "Full dataset contains" in line:
                    # Extract total documents: "Full dataset contains 1116 documents:"
                    try:
                        total_docs = int(line.split("contains")[1].split("documents")[0].strip())
                    except:
                        pass
                
                elif line.strip().startswith("[") and "]" in line:
                    # Document processing line: "[   1/1116] [progress] filename"
                    try:
                        doc_num_part = line.split("]")[0].strip("[").strip()
                        if "/" in doc_num_part:
                            processed_docs = int(doc_num_part.split("/")[0].strip())
                    except:
                        pass
                
                elif "Batch" in line and "/" in line:
                    # Batch info: "📦 Batch 1/112 (docs 1-10)"
                    try:
                        batch_part = line.split("Batch")[1].split("(")[0].strip()
                        if "/" in batch_part:
                            current_batch = int(batch_part.split("/")[0].strip())
                    except:
                        pass
                
                elif "Entities:" in line:
                    # Entity count: "🎯 Entities: 156 | Avg/doc: 2.1"
                    try:
                        entities_part = line.split("Entities:")[1].split("|")[0].strip()
                        entities_extracted = int(entities_part)
                    except:
                        pass
                
                elif "Mistral:" in line and "%" in line:
                    # Mistral success: "📊 Batch: 45.2s | Total: 12.3m | Mistral: 85.5%"
                    try:
                        mistral_part = line.split("Mistral:")[1].strip()
                        if "%" in mistral_part:
                            mistral_success_rate = float(mistral_part.split("%")[0].strip())
                    except:
                        pass
            
            # Check checkpoint file for more detailed info
            checkpoint_info = None
            if checkpoint_path.exists():
                try:
                    with open(checkpoint_path, 'r') as f:
                        checkpoint_info = json.load(f)
                except:
                    pass
            
            # Display current status
            now = datetime.now().strftime("%H:%M:%S")
            print(f"\n⏰ {now} - Processing Status:")
            
            if total_docs > 0:
                progress_pct = 100 * processed_docs / total_docs
                progress_bar = "█" * int(progress_pct / 3.33) + "░" * (30 - int(progress_pct / 3.33))
                print(f"📊 Progress: [{progress_bar}] {processed_docs:4d}/{total_docs} ({progress_pct:.1f}%)")
            
            if current_batch > 0:
                print(f"📦 Current batch: {current_batch}/112")
            
            if entities_extracted > 0:
                avg_entities = entities_extracted / max(processed_docs, 1)
                print(f"🎯 Entities: {entities_extracted} total ({avg_entities:.1f} avg/doc)")
            
            if mistral_success_rate > 0:
                print(f"🧠 Mistral success: {mistral_success_rate:.1f}%")
            
            if checkpoint_info:
                print(f"💾 Last checkpoint: {checkpoint_info.get('processed_documents', 0)} docs")
            
            # Estimate completion time
            if processed_docs > 0 and total_docs > 0:
                # Estimate based on current progress (rough calculation)
                lines_per_doc = len(lines) / max(processed_docs, 1)
                estimated_total_lines = total_docs * lines_per_doc
                remaining_docs = total_docs - processed_docs
                
                # Very rough time estimate (this is just for indication)
                if processed_docs > 10:  # Only estimate after some docs are processed
                    print(f"📈 Remaining: {remaining_docs} documents")
            
            # Wait before next check
            time.sleep(30)  # Check every 30 seconds
            
        except KeyboardInterrupt:
            print("\n🛑 Monitoring stopped by user")
            break
        except Exception as e:
            print(f"❌ Error monitoring: {e}")
            time.sleep(10)

if __name__ == "__main__":
    monitor_progress()