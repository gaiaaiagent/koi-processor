#!/usr/bin/env python3
"""
KOI Full Dataset Pipeline
Production processing of all ~1,100 non-Twitter documents
"""

import asyncio
import json
import hashlib
import re
import concurrent.futures
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import ollama
import time

def with_timeout(timeout_seconds, fallback_result=None):
    """Robust timeout decorator that works with network I/O operations"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            # Use ThreadPoolExecutor for robust timeout handling
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(func, *args, **kwargs)
                try:
                    result = future.result(timeout=timeout_seconds)
                    return result
                except concurrent.futures.TimeoutError:
                    print(f"  ⏰ Timeout after {timeout_seconds}s - using fallback")
                    raise TimeoutError(f"Operation timed out after {timeout_seconds} seconds")
        return wrapper
    return decorator

@dataclass
class ProcessingStats:
    """Track comprehensive processing statistics"""
    total_documents: int = 0
    processed_documents: int = 0
    failed_documents: int = 0
    entities_extracted: int = 0
    discourse_elements: int = 0
    mistral_success: int = 0
    fallback_extractions: int = 0
    processing_time: float = 0.0
    
    # Source breakdown
    source_stats: Dict[str, Dict] = None
    
    def __post_init__(self):
        if self.source_stats is None:
            self.source_stats = {}


class FullDatasetKOIProcessor:
    """Production processor for full dataset with enhanced monitoring"""
    
    def __init__(self, model: str = "mistral:7b"):
        self.model = model
        self.stats = ProcessingStats()
        self.processed_entities = []
        self.client = ollama.Client()
        
        # Unified ontology context
        self.ontology_context = {
            "@context": {
                "regen": "https://regen.network/ontology#",
                "koi": "https://regen.network/koi#",
                "schema": "http://schema.org/",
                "prov": "http://www.w3.org/ns/prov#"
            }
        }
        
        self.ontology_version = "orn:regen.ontology:unified-v1"
        self.ontology_cid = "cid:sha256:e002e2e94b5cc9057e16fe0173854c88af1d1ba307986c0337066ddcbfdeb4a7"
        
        # Progress tracking for full dataset
        self.checkpoint_interval = 50  # Save progress every 50 docs
        self.last_checkpoint = 0
        
        # Skip list for problematic documents that consistently hang
        self.skip_list = {
            'Token_Fee_Split_1_0_abfd0e51.md',  # Known to cause infinite hangs
            'Token_Fee_Split_1_0_abfd0e51.m'    # Same document, different extension
        }
    
    def generate_rid(self, source: str, identifier: str) -> str:
        return f"orn:regen.{source}:{identifier}"
    
    def generate_cid(self, content: str) -> str:
        hash_obj = hashlib.sha256(content.encode())
        return f"cid:sha256:{hash_obj.hexdigest()[:16]}"
    
    def extract_json_from_mistral(self, response_text: str) -> List[Dict]:
        """Extract JSON from Mistral's response - proven working method"""
        try:
            # First, try to parse the response as-is
            try:
                result = json.loads(response_text)
                if isinstance(result, list):
                    return result
                elif isinstance(result, dict):
                    # Check if it's the weird format where JSON array is a key
                    for key in result.keys():
                        if key.startswith('[') and key.endswith(']'):
                            return json.loads(key)
                    
                    if 'entities' in result:
                        return result['entities']
                    
                    return [result]
                
            except json.JSONDecodeError:
                pass
            
            # Try to extract JSON array patterns
            array_pattern = r'\[(?:\s*\{[^}]*\}\s*,?\s*)+\]'
            matches = re.findall(array_pattern, response_text, re.DOTALL)
            
            if matches:
                for match in matches:
                    try:
                        return json.loads(match)
                    except:
                        continue
            
            # Look for individual objects
            object_pattern = r'\{[^{}]*\}'
            objects = re.findall(object_pattern, response_text)
            
            if objects:
                parsed_objects = []
                for obj_str in objects:
                    try:
                        obj = json.loads(obj_str)
                        if isinstance(obj, dict) and obj.get('name'):
                            parsed_objects.append(obj)
                    except:
                        continue
                
                if parsed_objects:
                    return parsed_objects
            
            return []
            
        except Exception:
            return []
    
    async def extract_with_mistral(self, content: str, metadata: Dict) -> List[Dict]:
        """Extract entities using Mistral with robust timeout protection"""
        try:
            # Create a timeout-protected version of the Mistral API call
            @with_timeout(300)  # 5 minute timeout
            def call_mistral():
                content_preview = content[:800] if len(content) > 800 else content
                
                prompt = f"""Extract key entities from this {metadata.get('source', 'document')} document.

Document: {metadata.get('filename', 'Unknown')}
Content: {content_preview}

Return JSON array with important entities:
[{{"type": "Agent", "name": "Person/Organization"}}, {{"type": "SemanticAsset", "name": "Document/Concept"}}]

Types: Agent, SemanticAsset, EcologicalAsset, GovernanceAct, Question, Claim, Evidence

JSON:"""

                return self.client.generate(
                    model=self.model,
                    prompt=prompt,
                    format="json",
                    options={
                        "temperature": 0.1,
                        "num_predict": min(1200, max(400, len(content) // 10)),
                        "top_k": 20,
                        "top_p": 0.8
                    },
                    stream=False
                )
            
            # Call Mistral with robust timeout protection
            response = call_mistral()
            
            result_text = response['response']
            entities = self.extract_json_from_mistral(result_text)
            
            if not entities:
                self.stats.fallback_extractions += 1
                return self.extract_smart_fallback(content, metadata)
            
            # Process extracted entities
            valid_entities = []
            for i, entity in enumerate(entities):
                if isinstance(entity, dict) and entity.get('name'):
                    # Normalize type
                    entity_type = entity.get('type', 'SemanticAsset')
                    if not entity_type.startswith('regen:'):
                        entity['@type'] = f"regen:{entity_type}"
                    else:
                        entity['@type'] = entity_type
                    
                    entity.pop('type', None)
                    
                    # Generate ID
                    entity['@id'] = self.generate_rid(
                        metadata.get('source', 'document'),
                        f"{metadata.get('id', 'unknown')}_{i}"
                    )
                    
                    # Add provenance
                    entity['wasExtractedUsing'] = self.ontology_version
                    entity['ontologyVersion'] = self.ontology_cid
                    entity['extractedAt'] = datetime.now(timezone.utc).isoformat()
                    entity['extractedBy'] = 'mistral-full-dataset-v1'
                    entity['foundIn'] = metadata.get('path', '')
                    
                    # Add alignments
                    entity['alignsWith'] = self._infer_alignments(entity, content)
                    
                    # Count discourse elements
                    if entity_type in ['Question', 'Hypothesis', 'Claim', 'Evidence', 
                                     'Theory', 'Model', 'Experiment', 'Result']:
                        self.stats.discourse_elements += 1
                    
                    valid_entities.append(entity)
            
            if valid_entities:
                self.stats.mistral_success += 1
                return valid_entities
            else:
                self.stats.fallback_extractions += 1
                return self.extract_smart_fallback(content, metadata)
            
        except TimeoutError as e:
            print(f"  ⏰ Timeout after 300s - falling back to simple extraction")
            self.stats.fallback_extractions += 1
            return self.extract_smart_fallback(content, metadata)
        except Exception as e:
            self.stats.fallback_extractions += 1
            return self.extract_smart_fallback(content, metadata)
    
    def _infer_alignments(self, entity: Dict, content: str) -> List[str]:
        """Infer essence alignments"""
        alignments = []
        entity_type = entity.get('@type', '').split(':')[-1]
        content_lower = content.lower()
        entity_name_lower = entity.get('name', '').lower()
        
        # Type-based alignments
        if entity_type in ['EcologicalAsset', 'SemanticAsset']:
            alignments.append("Re-Whole Value")
        elif entity_type in ['Agent']:
            alignments.append("Nest Caring")
        elif entity_type in ['GovernanceAct']:
            alignments.append("Harmonize Agency")
        
        # Content-based additional alignments
        regen_keywords = ["regenerat", "restore", "heal", "ecosystem", "environment", "ecological", "carbon", "climate"]
        community_keywords = ["community", "collaborat", "caring", "together", "collective", "network", "social"]
        governance_keywords = ["govern", "coordinat", "decision", "autonomy", "vote", "proposal", "policy"]
        
        text_to_check = f"{entity_name_lower} {content_lower}"
        
        if any(word in text_to_check for word in regen_keywords) and "Re-Whole Value" not in alignments:
            alignments.append("Re-Whole Value")
        
        if any(word in text_to_check for word in community_keywords) and "Nest Caring" not in alignments:
            alignments.append("Nest Caring")
        
        if any(word in text_to_check for word in governance_keywords) and "Harmonize Agency" not in alignments:
            alignments.append("Harmonize Agency")
        
        return alignments
    
    def extract_smart_fallback(self, content: str, metadata: Dict) -> List[Dict]:
        """Enhanced fallback extraction for full dataset"""
        entities = []
        
        # Create document entity
        doc_entity = {
            **self.ontology_context,
            "@type": "regen:SemanticAsset",
            "@id": self.generate_rid(metadata.get("source", "document"), metadata.get("id", "unknown")),
            "name": metadata.get("filename", "Unknown Document"),
            "cid": self.generate_cid(content),
            "alignsWith": self._detect_alignments_from_content(content),
            "metabolicProcess": "Anchor",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "wasExtractedUsing": self.ontology_version,
            "ontologyVersion": self.ontology_cid,
            "extractedAt": datetime.now(timezone.utc).isoformat(),
            "extractedBy": 'smart-fallback-full-v1',
            "foundIn": metadata.get('path', '')
        }
        entities.append(doc_entity)
        
        content_lower = content.lower()
        
        # Enhanced organization patterns for full dataset
        org_patterns = [
            (r"regen\s+network", "Regen Network"),
            (r"allegheny\s+land\s+trust", "Allegheny Land Trust"),
            (r"verra", "Verra"),
            (r"gold\s+standard", "Gold Standard"),
            (r"climate\s+action\s+reserve", "Climate Action Reserve"),
            (r"verified\s+carbon\s+standard", "Verified Carbon Standard"),
            (r"nature\s+conservancy", "The Nature Conservancy"),
            (r"carbon\s+trust", "Carbon Trust"),
            (r"american\s+carbon\s+registry", "American Carbon Registry"),
            (r"plan\s+vivo", "Plan Vivo")
        ]
        
        for pattern, name in org_patterns:
            if re.search(pattern, content_lower):
                entities.append({
                    **self.ontology_context,
                    "@type": "regen:Agent",
                    "@id": self.generate_rid("agent", f"org_{len(entities)}"),
                    "name": name,
                    "foundIn": doc_entity["@id"],
                    "alignsWith": ["Re-Whole Value", "Harmonize Agency"],
                    "wasExtractedUsing": self.ontology_version,
                    "extractedBy": 'pattern-extractor-full-v1'
                })
        
        # Enhanced ecological asset detection
        eco_keywords = ["carbon credit", "offset", "emission reduction", "biodiversity credit", 
                       "ecosystem service", "natural capital", "ecological asset", "mrv", 
                       "measurement reporting verification"]
        
        for keyword in eco_keywords:
            if keyword in content_lower:
                entities.append({
                    **self.ontology_context,
                    "@type": "regen:EcologicalAsset",
                    "@id": self.generate_rid("asset", f"eco_{len(entities)}"),
                    "name": f"{keyword.title()} Reference",
                    "foundIn": doc_entity["@id"],
                    "alignsWith": ["Re-Whole Value"],
                    "wasExtractedUsing": self.ontology_version,
                    "extractedBy": 'keyword-extractor-full-v1'
                })
                break
        
        return entities
    
    def _detect_alignments_from_content(self, content: str) -> List[str]:
        """Detect essence alignments from content"""
        alignments = []
        content_lower = content.lower()
        
        if any(word in content_lower for word in ["regenerat", "restore", "heal", "ecosystem", "environment", "ecological", "carbon", "climate"]):
            alignments.append("Re-Whole Value")
        
        if any(word in content_lower for word in ["community", "collaborat", "caring", "together", "collective", "network", "social"]):
            alignments.append("Nest Caring")
        
        if any(word in content_lower for word in ["govern", "coordinat", "decision", "autonomy", "vote", "proposal", "policy"]):
            alignments.append("Harmonize Agency")
        
        return alignments
    
    async def process_document(self, file_path: Path) -> Optional[Dict]:
        """Process a single document with full dataset optimizations"""
        start_time = time.time()
        
        # Check if document is in skip list
        if file_path.name in self.skip_list:
            print(f"  ⏭️  Skipping problematic document: {file_path.name}")
            # Create minimal entity to maintain statistics
            entities = [{
                "@type": "regen:SemanticAsset",
                "name": f"Skipped: {file_path.stem}",
                "description": "Document skipped due to processing issues",
                "extractedBy": "skip-mechanism",
                "rid": self.generate_rid("skip", file_path.stem),
                "cid": "skipped"
            }]
            
            self.stats.processed_documents += 1
            self.stats.fallback_extractions += 1
            
            return {
                "metadata": {
                    "filename": file_path.name,
                    "path": str(file_path),
                    "id": file_path.stem,
                    "source": "skipped",
                    "size": 0,
                    "rid": self.generate_rid("skip", file_path.stem)
                },
                "entities": entities,
                "processing_time": time.time() - start_time
            }
        
        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')
            
            if len(content.strip()) < 30:
                return None
            
            metadata = {
                "filename": file_path.name,
                "path": str(file_path),
                "id": file_path.stem,
                "source": self._identify_source(file_path),
                "size": len(content)
            }
            
            # Update source statistics
            source = metadata['source']
            if source not in self.stats.source_stats:
                self.stats.source_stats[source] = {
                    'docs': 0, 'entities': 0, 'mistral_success': 0, 'processing_time': 0
                }
            
            entities = await self.extract_with_mistral(content, metadata)
            
            # Update statistics
            self.stats.entities_extracted += len(entities)
            self.stats.processed_documents += 1
            
            # Update source-specific stats
            self.stats.source_stats[source]['docs'] += 1
            self.stats.source_stats[source]['entities'] += len(entities)
            if entities and entities[0].get('extractedBy') == 'mistral-full-dataset-v1':
                self.stats.source_stats[source]['mistral_success'] += 1
            self.stats.source_stats[source]['processing_time'] += time.time() - start_time
            
            transformation = {
                "@type": "regen:Transformation",
                "@id": self.generate_rid("transform", f"{metadata['id']}_extraction"),
                "fromState": metadata["path"],
                "toState": [e["@id"] for e in entities],
                "process": "Extract",
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "processingTime": time.time() - start_time,
                "method": "mistral" if entities and entities[0].get('extractedBy') == 'mistral-full-dataset-v1' else "fallback"
            }
            
            return {
                "metadata": metadata,
                "entities": entities,
                "transformation": transformation
            }
            
        except Exception as e:
            print(f"  ❌ Error: {e}")
            self.stats.failed_documents += 1
            return None
    
    def _identify_source(self, file_path: Path) -> str:
        path_str = str(file_path).lower()
        if "notion" in path_str:
            return "notion"
        elif "discourse" in path_str:
            return "discourse"
        elif "medium" in path_str:
            return "medium"
        elif "podcast" in path_str:
            return "podcast"
        elif "github" in path_str:
            return "github"
        elif "gitlab" in path_str:
            return "gitlab"
        elif "web" in path_str:
            return "web"
        else:
            return "document"
    
    def save_checkpoint(self, checkpoint_path: Path):
        """Save intermediate progress"""
        checkpoint = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "processed_documents": self.stats.processed_documents,
            "entities_extracted": self.stats.entities_extracted,
            "mistral_success": self.stats.mistral_success,
            "fallback_extractions": self.stats.fallback_extractions,
            "source_stats": self.stats.source_stats,
            "last_processed": len(self.processed_entities)
        }
        
        with open(checkpoint_path, 'w') as f:
            json.dump(checkpoint, f, indent=2)
    
    def save_results(self, output_path: Path):
        """Save comprehensive full dataset results"""
        
        # Calculate detailed statistics
        type_counts = {}
        alignment_counts = {"Re-Whole Value": 0, "Nest Caring": 0, "Harmonize Agency": 0}
        
        for doc in self.processed_entities:
            for entity in doc.get('entities', []):
                entity_type = entity.get('@type', 'Unknown').split(':')[-1]
                type_counts[entity_type] = type_counts.get(entity_type, 0) + 1
                
                alignments = entity.get('alignsWith', [])
                for alignment in alignments:
                    if alignment in alignment_counts:
                        alignment_counts[alignment] += 1
        
        output = {
            "metadata": {
                "processing_date": datetime.now(tz=timezone.utc).isoformat(),
                "processor_version": "full-dataset-koi-v1.0",
                "model": self.model,
                "ontology_version": self.ontology_version,
                "ontology_cid": self.ontology_cid,
                "dataset_type": "full_non_twitter",
                "total_documents": self.stats.total_documents,
                "processed_documents": self.stats.processed_documents,
                "failed_documents": self.stats.failed_documents,
                "success_rate": 100 * self.stats.processed_documents / max(self.stats.total_documents, 1),
                "entities_extracted": self.stats.entities_extracted,
                "avg_entities_per_doc": self.stats.entities_extracted / max(self.stats.processed_documents, 1),
                "discourse_elements": self.stats.discourse_elements,
                "mistral_success": self.stats.mistral_success,
                "fallback_extractions": self.stats.fallback_extractions,
                "mistral_success_rate": 100 * self.stats.mistral_success / max(self.stats.processed_documents, 1),
                "processing_time_hours": self.stats.processing_time / 3600,
                "avg_time_per_doc": self.stats.processing_time / max(self.stats.processed_documents, 1),
                "entity_type_distribution": type_counts,
                "alignment_distribution": alignment_counts,
                "source_breakdown": self.stats.source_stats
            },
            "entities": self.processed_entities
        }
        
        with open(output_path, 'w') as f:
            json.dump(output, f, indent=2)


async def main():
    """Run production pipeline on full dataset"""
    print("🌿 KOI FULL DATASET PROCESSING")
    print("🚀 Production pipeline for ~1,100 non-Twitter documents")
    print("=" * 80)
    
    processor = FullDatasetKOIProcessor(model="mistral:7b")
    
    # Find all non-Twitter documents
    data_dir = Path("/Users/darrenzal/projects/RegenAI/GAIA/data")
    patterns = ["*.md", "*.json", "*.txt"]
    files = []
    
    print("📂 Scanning complete dataset...")
    for pattern in patterns:
        found_files = list(data_dir.rglob(pattern))
        found_files = [f for f in found_files if "twitter" not in str(f).lower()]
        files.extend(found_files)
    
    processor.stats.total_documents = len(files)
    
    # Show comprehensive source distribution
    source_counts = {}
    for f in files:
        source = processor._identify_source(f)
        source_counts[source] = source_counts.get(source, 0) + 1
    
    print(f"📊 Full dataset contains {len(files)} documents:")
    for source, count in sorted(source_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"  {source}: {count} documents")
    
    print(f"\n🤖 Using Mistral 7B with unified ontology v1")
    estimated_hours = len(files) * 16 / 3600  # Based on 16s avg from 100-doc test
    print(f"⏱️  Estimated processing time: {estimated_hours:.1f} hours")
    print(f"📈 Expected entities: ~{len(files) * 3.26:.0f} (based on 3.26 avg from test)")
    print(f"🔄 Checkpoints every 50 documents")
    
    print("\n🚀 Auto-starting full dataset processing (estimated 5 hours)...")
    
    print(f"\n🚀 Starting full dataset processing...")
    
    # Process documents in batches with enhanced monitoring
    start_time = time.time()
    batch_size = 10
    checkpoint_path = Path("/Users/darrenzal/projects/RegenAI/koi-processor/full-dataset-checkpoint.json")
    
    for i in range(0, len(files), batch_size):
        batch = files[i:i+batch_size]
        batch_start = time.time()
        
        batch_num = i // batch_size + 1
        total_batches = (len(files) + batch_size - 1) // batch_size
        
        print(f"\n📦 Batch {batch_num}/{total_batches} (docs {i+1}-{min(i+batch_size, len(files))})")
        
        # Process batch with progress indicators
        for j, file_path in enumerate(batch):
            doc_start = time.time()
            doc_num = i + j + 1
            
            # Show progress bar
            progress = "█" * (doc_num * 30 // len(files)) + "░" * (30 - doc_num * 30 // len(files))
            print(f"  [{doc_num:4d}/{len(files)}] [{progress}] {file_path.name[:35]:<35} ", end="", flush=True)
            
            result = await processor.process_document(file_path)
            if result:
                processor.processed_entities.append(result)
                entities_count = len(result.get('entities', []))
                method = "🧠" if result['transformation']['method'] == 'mistral' else "🔧"
                processing_time = time.time() - doc_start
                print(f"{method} {entities_count:2d}e {processing_time:5.1f}s")
            else:
                print(f"❌ failed ({time.time() - doc_start:.1f}s)")
        
        # Batch summary
        batch_time = time.time() - batch_start
        elapsed_total = time.time() - start_time
        mistral_rate = 100 * processor.stats.mistral_success / max(processor.stats.processed_documents, 1)
        
        print(f"  📊 Batch: {batch_time:.1f}s | Total: {elapsed_total/60:.1f}m | Mistral: {mistral_rate:.1f}%")
        print(f"  🎯 Entities: {processor.stats.entities_extracted} | Avg/doc: {processor.stats.entities_extracted/max(processor.stats.processed_documents, 1):.1f}")
        
        # ETA calculation
        if processor.stats.processed_documents > 0:
            avg_per_doc = elapsed_total / processor.stats.processed_documents
            remaining_docs = len(files) - processor.stats.processed_documents
            eta_hours = (remaining_docs * avg_per_doc) / 3600
            completion_time = datetime.now() + timedelta(hours=eta_hours)
            print(f"  ⏰ ETA: {eta_hours:.1f}h (completion ~{completion_time.strftime('%H:%M')})")
        
        # Save checkpoint every 50 documents
        if processor.stats.processed_documents >= processor.last_checkpoint + processor.checkpoint_interval:
            processor.save_checkpoint(checkpoint_path)
            processor.last_checkpoint = processor.stats.processed_documents
            print(f"  💾 Checkpoint saved at {processor.stats.processed_documents} documents")
    
    processor.stats.processing_time = time.time() - start_time
    
    # Final comprehensive results
    print("\n" + "=" * 80)
    print("🎯 FULL DATASET PROCESSING COMPLETE")
    print("=" * 80)
    print(f"📊 Documents processed: {processor.stats.processed_documents}/{len(files)}")
    print(f"✅ Success rate: {100 * processor.stats.processed_documents / len(files):.1f}%")
    print(f"🎯 Total entities extracted: {processor.stats.entities_extracted}")
    print(f"📈 Average entities per document: {processor.stats.entities_extracted / max(processor.stats.processed_documents, 1):.1f}")
    print(f"🧠 Mistral extractions: {processor.stats.mistral_success} ({100 * processor.stats.mistral_success / max(processor.stats.processed_documents, 1):.1f}%)")
    print(f"🔧 Fallback extractions: {processor.stats.fallback_extractions}")
    print(f"💭 Discourse elements: {processor.stats.discourse_elements}")
    print(f"⏱️  Total processing time: {processor.stats.processing_time/3600:.1f} hours")
    print(f"⚡ Average per document: {processor.stats.processing_time/max(processor.stats.processed_documents, 1):.1f} seconds")
    
    # Source breakdown
    print(f"\n📈 Processing by Source:")
    for source, stats in sorted(processor.stats.source_stats.items(), key=lambda x: x[1]['docs'], reverse=True):
        success_rate = 100 * stats['mistral_success'] / max(stats['docs'], 1)
        avg_entities = stats['entities'] / max(stats['docs'], 1)
        avg_time = stats['processing_time'] / max(stats['docs'], 1)
        print(f"  {source:12s}: {stats['docs']:4d} docs | {avg_entities:4.1f} ent/doc | {success_rate:5.1f}% Mistral | {avg_time:5.1f}s avg")
    
    # Save final results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(f"/Users/darrenzal/projects/RegenAI/koi-processor/full-dataset-{timestamp}.json")
    processor.save_results(output_path)
    
    # Create summary report
    summary_path = Path(f"/Users/darrenzal/projects/RegenAI/koi-processor/full-dataset-summary-{timestamp}.json")
    summary = {
        "summary": {
            "processing_date": datetime.now(tz=timezone.utc).isoformat(),
            "dataset_scope": "full_non_twitter_gaia_data",
            "total_documents": processor.stats.processed_documents,
            "total_entities": processor.stats.entities_extracted,
            "processing_time_hours": processor.stats.processing_time / 3600,
            "mistral_success_rate": 100 * processor.stats.mistral_success / max(processor.stats.processed_documents, 1),
            "ontology_version": processor.ontology_version,
            "processor_version": "full-dataset-koi-v1.0"
        },
        "source_breakdown": processor.stats.source_stats,
        "sample_entities": []
    }
    
    # Add sample entities for review
    sample_count = 0
    for doc in processor.processed_entities[:20]:  # From first 20 docs
        for entity in doc.get('entities', [])[:3]:  # Up to 3 per doc
            if sample_count < 50:  # Limit to 50 samples
                summary["sample_entities"].append({
                    "name": entity.get('name', 'Unknown'),
                    "type": entity.get('@type', 'Unknown'),
                    "alignsWith": entity.get('alignsWith', []),
                    "source_document": doc['metadata'].get('filename', 'Unknown'),
                    "extraction_method": entity.get('extractedBy', 'Unknown')
                })
                sample_count += 1
    
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2)
    
    print(f"\n✅ Full dataset processing completed successfully!")
    print(f"📁 Complete results: {output_path}")
    print(f"📋 Summary report: {summary_path}")
    print(f"🗂️  Results ready for Neo4j import and knowledge graph construction")
    
    # Clean up checkpoint
    if checkpoint_path.exists():
        checkpoint_path.unlink()
        print(f"🧹 Checkpoint file cleaned up")

# Add missing import
from datetime import timedelta

if __name__ == "__main__":
    asyncio.run(main())