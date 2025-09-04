#!/usr/bin/env python3
"""
KOI Production Pipeline - 100 Document Test
Production-ready processing with working Mistral extraction
"""

import asyncio
import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import ollama
import time
import random

@dataclass
class ProcessingStats:
    """Track processing statistics"""
    total_documents: int = 0
    processed_documents: int = 0
    failed_documents: int = 0
    entities_extracted: int = 0
    discourse_elements: int = 0
    mistral_success: int = 0
    fallback_extractions: int = 0
    processing_time: float = 0.0


class ProductionKOIProcessor:
    """Production-ready KOI processor with proven JSON parsing"""
    
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
                            # The JSON array is the key! Parse it
                            return json.loads(key)
                    
                    # Maybe it has an 'entities' field
                    if 'entities' in result:
                        return result['entities']
                    
                    # Otherwise wrap in array
                    return [result]
                
            except json.JSONDecodeError:
                pass
            
            # Try to extract JSON array from anywhere in the text
            # Look for pattern like [{...},{...}]
            array_pattern = r'\[(?:\s*\{[^}]*\}\s*,?\s*)+\]'
            matches = re.findall(array_pattern, response_text, re.DOTALL)
            
            if matches:
                # Try to parse the first match
                for match in matches:
                    try:
                        return json.loads(match)
                    except:
                        continue
            
            # Look for individual objects and collect them
            object_pattern = r'\{[^{}]*\}'
            objects = re.findall(object_pattern, response_text)
            
            if objects:
                parsed_objects = []
                for obj_str in objects:
                    try:
                        obj = json.loads(obj_str)
                        if isinstance(obj, dict) and obj.get('name'):  # Valid entity
                            parsed_objects.append(obj)
                    except:
                        continue
                
                if parsed_objects:
                    return parsed_objects
            
            return []
            
        except Exception as e:
            return []
    
    async def extract_with_mistral(self, content: str, metadata: Dict) -> List[Dict]:
        """Extract entities using Mistral with proven parsing"""
        try:
            # Optimized prompt for better extraction
            prompt = f"""Extract important entities from this document. Focus on people, organizations, concepts, and resources.

Document: {metadata.get('filename', 'Unknown')}
Content: {content[:700]}

Return JSON array with entities like:
[{{"type": "Agent", "name": "Organization Name"}}, {{"type": "SemanticAsset", "name": "Document Title"}}]

Valid types: Agent, SemanticAsset, EcologicalAsset, GovernanceAct, Question, Claim, Evidence, Theory

JSON:"""

            response = self.client.generate(
                model=self.model,
                prompt=prompt,
                format="json",
                options={
                    "temperature": 0.1,
                    "num_predict": 1000,
                    "top_k": 20,
                    "top_p": 0.8
                },
                stream=False
            )
            
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
                    
                    # Remove old type field
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
                    entity['extractedBy'] = 'mistral-production-v1'
                    entity['foundIn'] = metadata.get('path', '')
                    
                    # Add alignsWith based on type and content
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
            
        except Exception as e:
            self.stats.fallback_extractions += 1
            return self.extract_smart_fallback(content, metadata)
    
    def _infer_alignments(self, entity: Dict, content: str) -> List[str]:
        """Infer essence alignments based on entity type and content"""
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
        """Enhanced fallback extraction with better entity detection"""
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
            "extractedBy": 'smart-fallback-v1',
            "foundIn": metadata.get('path', '')
        }
        entities.append(doc_entity)
        
        content_lower = content.lower()
        
        # Extract organization entities with better patterns
        org_patterns = [
            (r"regen\s+network", "Regen Network"),
            (r"allegheny\s+land\s+trust", "Allegheny Land Trust"),
            (r"verra", "Verra"),
            (r"gold\s+standard", "Gold Standard"),
            (r"climate\s+action\s+reserve", "Climate Action Reserve"),
            (r"verified\s+carbon\s+standard", "Verified Carbon Standard"),
            (r"nature\s+conservancy", "The Nature Conservancy")
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
                    "extractedBy": 'pattern-extractor-v1'
                })
        
        # Extract ecological assets
        eco_keywords = ["carbon credit", "offset", "emission reduction", "biodiversity credit", 
                       "ecosystem service", "natural capital", "ecological asset"]
        
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
                    "extractedBy": 'keyword-extractor-v1'
                })
                break  # Only add one ecological asset per document
        
        # Extract governance concepts
        gov_keywords = ["proposal", "vote", "governance", "decision", "policy", "regulation"]
        
        for keyword in gov_keywords:
            if keyword in content_lower:
                entities.append({
                    **self.ontology_context,
                    "@type": "regen:GovernanceAct",
                    "@id": self.generate_rid("governance", f"gov_{len(entities)}"),
                    "name": f"{keyword.title()} Reference",
                    "foundIn": doc_entity["@id"],
                    "alignsWith": ["Harmonize Agency"],
                    "wasExtractedUsing": self.ontology_version,
                    "extractedBy": 'keyword-extractor-v1'
                })
                break  # Only add one governance entity per document
        
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
        """Process a single document"""
        start_time = time.time()
        
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
            
            entities = await self.extract_with_mistral(content, metadata)
            
            self.stats.entities_extracted += len(entities)
            self.stats.processed_documents += 1
            
            transformation = {
                "@type": "regen:Transformation",
                "@id": self.generate_rid("transform", f"{metadata['id']}_extraction"),
                "fromState": metadata["path"],
                "toState": [e["@id"] for e in entities],
                "process": "Extract",
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "processingTime": time.time() - start_time,
                "method": "mistral" if entities and entities[0].get('extractedBy') == 'mistral-production-v1' else "fallback"
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
        else:
            return "document"
    
    def save_results(self, output_path: Path):
        """Save comprehensive results"""
        
        # Calculate entity type distribution
        type_counts = {}
        for doc in self.processed_entities:
            for entity in doc.get('entities', []):
                entity_type = entity.get('@type', 'Unknown').split(':')[-1]
                type_counts[entity_type] = type_counts.get(entity_type, 0) + 1
        
        # Calculate alignment distribution
        alignment_counts = {"Re-Whole Value": 0, "Nest Caring": 0, "Harmonize Agency": 0}
        for doc in self.processed_entities:
            for entity in doc.get('entities', []):
                alignments = entity.get('alignsWith', [])
                for alignment in alignments:
                    if alignment in alignment_counts:
                        alignment_counts[alignment] += 1
        
        output = {
            "metadata": {
                "processing_date": datetime.now(tz=timezone.utc).isoformat(),
                "processor_version": "production-koi-v1.0",
                "model": self.model,
                "ontology_version": self.ontology_version,
                "ontology_cid": self.ontology_cid,
                "total_documents": self.stats.total_documents,
                "processed_documents": self.stats.processed_documents,
                "failed_documents": self.stats.failed_documents,
                "entities_extracted": self.stats.entities_extracted,
                "discourse_elements": self.stats.discourse_elements,
                "mistral_success": self.stats.mistral_success,
                "fallback_extractions": self.stats.fallback_extractions,
                "mistral_success_rate": 100 * self.stats.mistral_success / max(self.stats.processed_documents, 1),
                "processing_time_minutes": self.stats.processing_time / 60,
                "avg_time_per_doc": self.stats.processing_time / max(self.stats.processed_documents, 1),
                "entity_type_distribution": type_counts,
                "alignment_distribution": alignment_counts
            },
            "entities": self.processed_entities
        }
        
        with open(output_path, 'w') as f:
            json.dump(output, f, indent=2)


async def main():
    """Run production pipeline on 100 documents"""
    print("🌿 KOI Production Pipeline - 100 Document Test")
    print("🚀 Production-ready extraction with unified ontology")
    print("=" * 70)
    
    processor = ProductionKOIProcessor(model="mistral:7b")
    
    # Find and select documents
    data_dir = Path("/Users/darrenzal/projects/RegenAI/GAIA/data")
    patterns = ["*.md", "*.json", "*.txt"]
    files = []
    
    print("📂 Scanning for documents...")
    for pattern in patterns:
        found_files = list(data_dir.rglob(pattern))
        found_files = [f for f in found_files if "twitter" not in str(f).lower()]
        files.extend(found_files)
    
    # Select 100 diverse documents
    random.seed(42)  # Reproducible selection
    test_files = random.sample(files, min(100, len(files)))
    
    # Show source distribution
    source_counts = {}
    for f in test_files:
        source = processor._identify_source(f)
        source_counts[source] = source_counts.get(source, 0) + 1
    
    print(f"📊 Selected {len(test_files)} documents for production test:")
    for source, count in sorted(source_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"  {source}: {count} documents")
    
    print(f"\n🤖 Using Mistral 7B with unified ontology v1")
    print(f"⏱️  Estimated processing time: {len(test_files) * 10 / 60:.1f} minutes")
    print(f"🚀 Starting production processing...\n")
    
    # Process documents in batches
    start_time = time.time()
    batch_size = 10
    
    for i in range(0, len(test_files), batch_size):
        batch = test_files[i:i+batch_size]
        batch_start = time.time()
        
        print(f"📦 Batch {i//batch_size + 1}/{(len(test_files) + batch_size - 1)//batch_size}")
        
        # Process batch sequentially for better monitoring
        for j, file_path in enumerate(batch):
            doc_start = time.time()
            print(f"  [{i+j+1:3d}/100] {file_path.name[:45]:<45} ", end="")
            
            result = await processor.process_document(file_path)
            if result:
                processor.processed_entities.append(result)
                entities_count = len(result.get('entities', []))
                method = "🧠" if result['transformation']['method'] == 'mistral' else "🔧"
                print(f"{method} {entities_count:2d} entities ({time.time() - doc_start:.1f}s)")
            else:
                print(f"❌ failed ({time.time() - doc_start:.1f}s)")
        
        # Batch summary
        batch_time = time.time() - batch_start
        mistral_rate = 100 * processor.stats.mistral_success / max(processor.stats.processed_documents, 1)
        print(f"  📈 Batch time: {batch_time:.1f}s, Mistral success: {mistral_rate:.1f}%")
        
        # Estimate remaining time
        if processor.stats.processed_documents > 0:
            elapsed = time.time() - start_time
            avg_per_doc = elapsed / processor.stats.processed_documents
            remaining_docs = len(test_files) - processor.stats.processed_documents
            eta_minutes = (remaining_docs * avg_per_doc) / 60
            print(f"  ⏰ ETA: {eta_minutes:.1f} minutes remaining\n")
    
    processor.stats.processing_time = time.time() - start_time
    
    # Final results
    print("=" * 70)
    print("🎯 PRODUCTION PIPELINE RESULTS")
    print("=" * 70)
    print(f"Documents processed: {processor.stats.processed_documents}/{len(test_files)}")
    print(f"Success rate: {100 * processor.stats.processed_documents / len(test_files):.1f}%")
    print(f"Total entities extracted: {processor.stats.entities_extracted}")
    print(f"Mistral extractions: {processor.stats.mistral_success}")
    print(f"Fallback extractions: {processor.stats.fallback_extractions}")
    print(f"Mistral success rate: {100 * processor.stats.mistral_success / max(processor.stats.processed_documents, 1):.1f}%")
    print(f"Discourse elements: {processor.stats.discourse_elements}")
    print(f"Total processing time: {processor.stats.processing_time/60:.1f} minutes")
    print(f"Average per document: {processor.stats.processing_time/max(processor.stats.processed_documents, 1):.1f} seconds")
    
    # Save results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(f"/Users/darrenzal/projects/RegenAI/koi-processor/production-pipeline-{timestamp}.json")
    processor.save_results(output_path)
    
    print(f"\n✅ Production pipeline test completed successfully!")
    print(f"📁 Complete results: {output_path}")
    
    # Show sample entities
    print(f"\n🎨 Sample extracted entities (first 8):")
    count = 0
    for result in processor.processed_entities[:10]:
        for entity in result.get('entities', [])[:2]:
            if count < 8:
                name = entity.get('name', 'Unknown')[:35]
                etype = entity.get('@type', 'Unknown').split(':')[-1]
                alignments = entity.get('alignsWith', [])
                method = "🧠" if entity.get('extractedBy') == 'mistral-production-v1' else "🔧"
                print(f"  {method} {etype}: '{name}' → {alignments}")
                count += 1
    
    print(f"\n🔬 Next steps:")
    print("1. Review entity quality and alignment accuracy")
    print("2. Scale up to full dataset processing")
    print("3. Load into Neo4j knowledge graph")
    print("4. Build semantic search and inference")


if __name__ == "__main__":
    asyncio.run(main())