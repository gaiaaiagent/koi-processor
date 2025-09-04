#!/usr/bin/env python3
"""
Working KOI Processor - Fixed Mistral JSON Parsing
Handles Mistral's quirky JSON response format
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


class WorkingKOIProcessor:
    """Working processor that handles Mistral's JSON response format correctly"""
    
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
        """Extract JSON from Mistral's unusual response format"""
        try:
            # First, try to parse the response as-is
            try:
                result = json.loads(response_text)
                if isinstance(result, list):
                    return result
                elif isinstance(result, dict):
                    # Check if it's our weird format where JSON array is a key
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
            print(f"    JSON extraction error: {e}")
            return []
    
    async def extract_with_mistral(self, content: str, metadata: Dict) -> List[Dict]:
        """Extract entities using Mistral with fixed parsing"""
        try:
            # Even simpler prompt to avoid confusing Mistral
            prompt = f"""Extract entities from this document. Return as JSON array only.

Document: {metadata.get('filename', 'Unknown')}
Content: {content[:600]}

Entity types: Agent, SemanticAsset, EcologicalAsset, GovernanceAct, Question, Claim, Evidence

Example: [{{"type": "Agent", "name": "Person Name"}}, {{"type": "SemanticAsset", "name": "Document Title"}}]

JSON array:"""

            response = self.client.generate(
                model=self.model,
                prompt=prompt,
                format="json",
                options={
                    "temperature": 0.05,  # Very low temperature
                    "num_predict": 800,
                    "top_k": 10,
                    "top_p": 0.7
                },
                stream=False
            )
            
            result_text = response['response']
            entities = self.extract_json_from_mistral(result_text)
            
            if not entities:
                print(f"    No entities extracted from Mistral")
                self.stats.fallback_extractions += 1
                return self.extract_basic(content, metadata)
            
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
                    entity['extractedBy'] = 'mistral-working-v1'
                    entity['foundIn'] = metadata.get('path', '')
                    
                    # Add alignsWith if not present
                    if 'alignsWith' not in entity:
                        # Infer alignment based on type and content
                        entity['alignsWith'] = []
                        if entity_type in ['EcologicalAsset', 'SemanticAsset']:
                            entity['alignsWith'].append("Re-Whole Value")
                        if entity_type in ['Agent']:
                            entity['alignsWith'].append("Nest Caring")
                        if entity_type in ['GovernanceAct']:
                            entity['alignsWith'].append("Harmonize Agency")
                    
                    # Count discourse elements
                    if entity_type in ['Question', 'Hypothesis', 'Claim', 'Evidence', 
                                     'Theory', 'Model', 'Experiment', 'Result']:
                        self.stats.discourse_elements += 1
                    
                    valid_entities.append(entity)
            
            if valid_entities:
                self.stats.mistral_success += 1
                print(f"    ✅ Mistral extracted {len(valid_entities)} entities")
                return valid_entities
            else:
                self.stats.fallback_extractions += 1
                return self.extract_basic(content, metadata)
            
        except Exception as e:
            print(f"    ❌ Mistral error: {e}")
            self.stats.fallback_extractions += 1
            return self.extract_basic(content, metadata)
    
    def extract_basic(self, content: str, metadata: Dict) -> List[Dict]:
        """Smart fallback extraction"""
        entities = []
        
        # Create document entity
        doc_entity = {
            **self.ontology_context,
            "@type": "regen:SemanticAsset",
            "@id": self.generate_rid(metadata.get("source", "document"), metadata.get("id", "unknown")),
            "name": metadata.get("filename", "Unknown Document"),
            "cid": self.generate_cid(content),
            "alignsWith": self._detect_alignments(content),
            "metabolicProcess": "Anchor",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "wasExtractedUsing": self.ontology_version,
            "ontologyVersion": self.ontology_cid,
            "extractedAt": datetime.now(timezone.utc).isoformat(),
            "extractedBy": 'fallback-extractor-smart',
            "foundIn": metadata.get('path', '')
        }
        entities.append(doc_entity)
        
        # Extract additional entities based on content patterns
        content_lower = content.lower()
        
        # Look for organization names
        org_patterns = [
            r"regen network", r"allegheny land trust", r"carbon trust",
            r"verra", r"gold standard", r"climate action reserve"
        ]
        for pattern in org_patterns:
            if re.search(pattern, content_lower):
                org_name = pattern.replace("r", "R").title()
                entities.append({
                    **self.ontology_context,
                    "@type": "regen:Agent",
                    "@id": self.generate_rid("agent", f"org_{len(entities)}"),
                    "name": org_name,
                    "foundIn": doc_entity["@id"],
                    "alignsWith": ["Re-Whole Value", "Harmonize Agency"],
                    "wasExtractedUsing": self.ontology_version,
                    "extractedBy": 'pattern-extractor'
                })
        
        # Look for carbon/ecological references
        if any(word in content_lower for word in ["carbon", "credit", "offset", "emission", "ecological"]):
            entities.append({
                **self.ontology_context,
                "@type": "regen:EcologicalAsset",
                "@id": self.generate_rid("asset", f"eco_{len(entities)}"),
                "name": "Ecological Asset Reference",
                "foundIn": doc_entity["@id"],
                "alignsWith": ["Re-Whole Value"],
                "wasExtractedUsing": self.ontology_version,
                "extractedBy": 'pattern-extractor'
            })
        
        return entities
    
    def _detect_alignments(self, content: str) -> List[str]:
        """Detect essence alignments from content"""
        alignments = []
        content_lower = content.lower()
        
        if any(word in content_lower for word in ["regenerat", "restore", "heal", "ecosystem", "environment", "ecological"]):
            alignments.append("Re-Whole Value")
        
        if any(word in content_lower for word in ["community", "collaborat", "caring", "together", "collective", "network"]):
            alignments.append("Nest Caring")
        
        if any(word in content_lower for word in ["govern", "coordinat", "decision", "autonomy", "vote", "proposal"]):
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
                "processingTime": time.time() - start_time
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
        """Save results"""
        output = {
            "metadata": {
                "processing_date": datetime.now(tz=timezone.utc).isoformat(),
                "processor_version": "working-koi-v1.0",
                "model": self.model,
                "ontology_version": self.ontology_version,
                "total_documents": self.stats.total_documents,
                "processed_documents": self.stats.processed_documents,
                "failed_documents": self.stats.failed_documents,
                "entities_extracted": self.stats.entities_extracted,
                "discourse_elements": self.stats.discourse_elements,
                "mistral_success": self.stats.mistral_success,
                "fallback_extractions": self.stats.fallback_extractions,
                "mistral_success_rate": 100 * self.stats.mistral_success / max(self.stats.processed_documents, 1),
                "processing_time": self.stats.processing_time,
                "avg_time_per_doc": self.stats.processing_time / max(self.stats.processed_documents, 1)
            },
            "entities": self.processed_entities
        }
        
        with open(output_path, 'w') as f:
            json.dump(output, f, indent=2)


async def test_working_processor():
    """Test the working processor on 30 documents"""
    print("🌿 Working KOI Processor Test")
    print("🔧 Fixed Mistral JSON parsing")
    print("=" * 60)
    
    processor = WorkingKOIProcessor(model="mistral:7b")
    
    # Find test documents
    data_dir = Path("/Users/darrenzal/projects/RegenAI/GAIA/data")
    patterns = ["*.md", "*.json", "*.txt"]
    files = []
    
    for pattern in patterns:
        found_files = list(data_dir.rglob(pattern))
        found_files = [f for f in found_files if "twitter" not in str(f).lower()]
        files.extend(found_files)
    
    # Select 30 diverse documents
    random.seed(42)
    test_files = random.sample(files, min(30, len(files)))
    
    # Show distribution
    source_counts = {}
    for f in test_files:
        source = processor._identify_source(f)
        source_counts[source] = source_counts.get(source, 0) + 1
    
    print(f"📊 Testing on {len(test_files)} documents:")
    for source, count in source_counts.items():
        print(f"  {source}: {count} docs")
    print()
    
    # Process documents
    start_time = time.time()
    results = []
    
    for i, file_path in enumerate(test_files):
        print(f"🔄 [{i+1:2d}/{len(test_files)}] {file_path.name[:50]}")
        result = await processor.process_document(file_path)
        if result:
            processor.processed_entities.append(result)
            results.append(result)
        
        # Show progress every 10 docs
        if (i + 1) % 10 == 0:
            current_rate = 100 * processor.stats.mistral_success / max(processor.stats.processed_documents, 1)
            print(f"  📈 Progress: {processor.stats.processed_documents} docs, {processor.stats.entities_extracted} entities")
            print(f"  🧠 Mistral success rate: {current_rate:.1f}%\n")
    
    processor.stats.processing_time = time.time() - start_time
    
    # Final results
    print("=" * 60)
    print("🎯 WORKING PROCESSOR RESULTS")
    print("=" * 60)
    print(f"Documents processed: {processor.stats.processed_documents}/{len(test_files)}")
    print(f"Success rate: {100 * processor.stats.processed_documents / len(test_files):.1f}%")
    print(f"Total entities: {processor.stats.entities_extracted}")
    print(f"Mistral extractions: {processor.stats.mistral_success}")
    print(f"Fallback extractions: {processor.stats.fallback_extractions}")
    print(f"Mistral success rate: {100 * processor.stats.mistral_success / max(processor.stats.processed_documents, 1):.1f}%")
    print(f"Discourse elements: {processor.stats.discourse_elements}")
    print(f"Processing time: {processor.stats.processing_time/60:.1f} minutes")
    print(f"Avg per doc: {processor.stats.processing_time/max(processor.stats.processed_documents, 1):.1f} seconds")
    
    # Save results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(f"/Users/darrenzal/projects/RegenAI/koi-processor/working-test-{timestamp}.json")
    processor.save_results(output_path)
    
    print(f"\n✅ Working processor test complete!")
    print(f"📁 Results: {output_path}")
    
    # Show sample entities
    if results:
        print(f"\n🎨 Sample extracted entities:")
        count = 0
        for result in results[:5]:
            for entity in result.get('entities', [])[:2]:
                if count < 8:
                    name = entity.get('name', 'Unknown')[:40]
                    etype = entity.get('@type', 'Unknown').split(':')[-1]
                    alignments = entity.get('alignsWith', [])
                    print(f"  • {etype}: '{name}' → {alignments}")
                    count += 1


if __name__ == "__main__":
    asyncio.run(test_working_processor())