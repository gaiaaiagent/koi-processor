#!/usr/bin/env python3
"""
Improved KOI Processor with Better JSON Parsing
Enhanced error handling and more robust Mistral response processing
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


class ImprovedKOIProcessor:
    """Improved processor with robust JSON parsing"""
    
    def __init__(self, model: str = "mistral:7b"):
        self.model = model
        self.stats = ProcessingStats()
        self.processed_entities = []
        self.client = ollama.Client()
        
        # Unified ontology context (v1.0)
        self.ontology_context = {
            "@context": {
                "regen": "https://regen.network/ontology#",
                "koi": "https://regen.network/koi#",
                "schema": "http://schema.org/",
                "prov": "http://www.w3.org/ns/prov#"
            }
        }
        
        # Ontology provenance tracking
        self.ontology_version = "orn:regen.ontology:unified-v1"
        self.ontology_cid = "cid:sha256:e002e2e94b5cc9057e16fe0173854c88af1d1ba307986c0337066ddcbfdeb4a7"
    
    def generate_rid(self, source: str, identifier: str) -> str:
        """Generate Resource Identifier"""
        return f"orn:regen.{source}:{identifier}"
    
    def generate_cid(self, content: str) -> str:
        """Generate Content Identifier"""
        hash_obj = hashlib.sha256(content.encode())
        return f"cid:sha256:{hash_obj.hexdigest()[:16]}"
    
    def clean_mistral_response(self, response_text: str) -> str:
        """Clean and prepare Mistral response for JSON parsing"""
        # Remove markdown code blocks
        response_text = re.sub(r'```json\s*', '', response_text, flags=re.IGNORECASE)
        response_text = re.sub(r'```\s*', '', response_text)
        
        # Remove any leading/trailing explanatory text
        response_text = response_text.strip()
        
        # Try to extract JSON from the response
        # Look for array first
        array_match = re.search(r'\[[\s\S]*?\]', response_text)
        if array_match:
            return array_match.group()
        
        # Look for single object and wrap in array
        object_match = re.search(r'\{[\s\S]*?\}', response_text)
        if object_match:
            return f"[{object_match.group()}]"
        
        # If no clear JSON structure, try to fix common issues
        # Fix unquoted keys
        response_text = re.sub(r'(\w+):', r'"\1":', response_text)
        
        # Ensure it's wrapped in array
        if not response_text.strip().startswith('['):
            response_text = f"[{response_text}]"
        
        return response_text
    
    def parse_mistral_json(self, response_text: str) -> List[Dict]:
        """Robustly parse Mistral JSON response"""
        try:
            # Clean the response
            cleaned = self.clean_mistral_response(response_text)
            
            # Try to parse
            entities = json.loads(cleaned)
            
            # Ensure we have a list
            if isinstance(entities, dict):
                if 'entities' in entities:
                    entities = entities['entities']
                else:
                    entities = [entities]
            
            # Validate each entity has required fields
            valid_entities = []
            for entity in entities:
                if isinstance(entity, dict) and entity.get('name'):
                    valid_entities.append(entity)
            
            return valid_entities
            
        except json.JSONDecodeError as e:
            # Try alternative parsing strategies
            try:
                # Attempt to fix common JSON issues
                fixed_text = response_text.replace("'", '"')  # Replace single quotes
                fixed_text = re.sub(r'(\w+):', r'"\1":', fixed_text)  # Quote keys
                fixed_text = re.sub(r',\s*}', '}', fixed_text)  # Remove trailing commas
                fixed_text = re.sub(r',\s*]', ']', fixed_text)  # Remove trailing commas
                
                # Wrap in array if needed
                if not fixed_text.strip().startswith('['):
                    fixed_text = f"[{fixed_text}]"
                
                entities = json.loads(fixed_text)
                return entities if isinstance(entities, list) else [entities]
                
            except:
                print(f"    JSON parsing failed: {str(e)[:100]}")
                return []
    
    async def extract_with_mistral(self, content: str, metadata: Dict) -> List[Dict]:
        """Extract entities using Mistral 7B with improved parsing"""
        try:
            # Simplified prompt for better JSON output
            prompt = f"""Extract key entities from this document as a JSON array.

Use these entity types from Regen Network Unified Ontology:
- Agent (people, organizations, AI agents)
- SemanticAsset (documents, proposals, knowledge)
- EcologicalAsset (carbon credits, ecological resources)
- GovernanceAct (votes, decisions, policies)
- MetabolicFlow (processes, data flows)

For discourse elements use:
- Question, Hypothesis, Claim, Evidence, Theory, Model

Document: {metadata.get('filename', 'Unknown')}
Content: {content[:800]}

Return ONLY a JSON array with this structure:
[
  {{
    "@type": "regen:Agent",
    "name": "Entity Name",
    "alignsWith": ["Re-Whole Value"]
  }}
]

JSON array:"""

            # Call Mistral with conservative settings
            response = self.client.generate(
                model=self.model,
                prompt=prompt,
                format="json",
                options={
                    "temperature": 0.1,  # Lower temperature for more consistent output
                    "num_predict": 1500,
                    "top_k": 20,
                    "top_p": 0.8,
                    "repeat_penalty": 1.1
                },
                stream=False
            )
            
            # Parse response
            result_text = response['response']
            entities = self.parse_mistral_json(result_text)
            
            if not entities:
                print(f"    No valid entities parsed from Mistral response")
                return self.extract_basic(content, metadata)
            
            # Post-process entities
            valid_entities = []
            for i, entity in enumerate(entities):
                if isinstance(entity, dict):
                    # Ensure required fields
                    if '@type' not in entity and 'type' in entity:
                        entity['@type'] = f"regen:{entity['type']}"
                    elif '@type' not in entity:
                        entity['@type'] = "regen:SemanticAsset"  # Default type
                    
                    # Generate @id if missing
                    if '@id' not in entity:
                        entity['@id'] = self.generate_rid(
                            metadata.get('source', 'document'),
                            f"{metadata.get('id', 'unknown')}_{i}"
                        )
                    
                    # Ensure name exists
                    if 'name' not in entity or not entity['name']:
                        entity['name'] = f"Entity from {metadata.get('filename', 'Unknown')}"
                    
                    # Add provenance
                    entity['wasExtractedUsing'] = self.ontology_version
                    entity['ontologyVersion'] = self.ontology_cid
                    entity['extractedAt'] = datetime.now(timezone.utc).isoformat()
                    entity['extractedBy'] = 'mistral-processor-v2'
                    entity['foundIn'] = metadata.get('path', '')
                    
                    # Ensure alignsWith is a list
                    if 'alignsWith' not in entity:
                        entity['alignsWith'] = []
                    elif isinstance(entity['alignsWith'], str):
                        entity['alignsWith'] = [entity['alignsWith']]
                    
                    # Count discourse elements
                    entity_type = entity.get('@type', '').split(':')[-1]
                    if entity_type in ['Question', 'Hypothesis', 'Claim', 'Evidence', 
                                       'Theory', 'Model', 'Experiment', 'Result']:
                        self.stats.discourse_elements += 1
                    
                    valid_entities.append(entity)
            
            if valid_entities:
                self.stats.mistral_success += 1
                print(f"    ✅ Mistral extracted {len(valid_entities)} entities")
            
            return valid_entities if valid_entities else self.extract_basic(content, metadata)
            
        except Exception as e:
            print(f"    ❌ Mistral extraction error: {e}")
            self.stats.fallback_extractions += 1
            return self.extract_basic(content, metadata)
    
    def extract_basic(self, content: str, metadata: Dict) -> List[Dict]:
        """Enhanced fallback extraction"""
        entities = []
        
        # Always create document as SemanticAsset
        doc_entity = {
            **self.ontology_context,
            "@type": "regen:SemanticAsset",
            "@id": self.generate_rid(metadata.get("source", "document"), metadata.get("id", "unknown")),
            "name": metadata.get("filename", "Unknown Document"),
            "cid": self.generate_cid(content),
            "alignsWith": [],
            "metabolicProcess": "Anchor",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "wasExtractedUsing": self.ontology_version,
            "ontologyVersion": self.ontology_cid,
            "extractedAt": datetime.now(timezone.utc).isoformat(),
            "extractedBy": 'fallback-extractor-v2',
            "foundIn": metadata.get('path', '')
        }
        
        # Detect alignments from content
        content_lower = content.lower()
        
        if any(word in content_lower for word in ["regenerat", "restore", "heal", "ecosystem", "environment"]):
            doc_entity["alignsWith"].append("Re-Whole Value")
        
        if any(word in content_lower for word in ["community", "collaborat", "caring", "together", "collective"]):
            doc_entity["alignsWith"].append("Nest Caring")
        
        if any(word in content_lower for word in ["govern", "coordinat", "decision", "autonomy", "vote"]):
            doc_entity["alignsWith"].append("Harmonize Agency")
        
        entities.append(doc_entity)
        
        # Extract specific entity types based on content
        if any(word in content_lower for word in ["carbon", "credit", "offset", "emission"]):
            entities.append({
                **self.ontology_context,
                "@type": "regen:EcologicalAsset",
                "@id": self.generate_rid("asset", f"carbon_{metadata.get('id', 'unknown')}"),
                "name": "Carbon-related Asset",
                "foundIn": doc_entity["@id"],
                "alignsWith": ["Re-Whole Value"],
                "wasExtractedUsing": self.ontology_version,
                "extractedBy": 'fallback-extractor-v2'
            })
        
        if any(word in content_lower for word in ["regen network", "regen.network", "regenerative"]):
            entities.append({
                **self.ontology_context,
                "@type": "regen:Agent",
                "@id": self.generate_rid("agent", f"regen_{metadata.get('id', 'unknown')}"),
                "name": "Regen Network Reference",
                "foundIn": doc_entity["@id"],
                "alignsWith": ["Re-Whole Value", "Harmonize Agency"],
                "wasExtractedUsing": self.ontology_version,
                "extractedBy": 'fallback-extractor-v2'
            })
        
        return entities
    
    async def process_document(self, file_path: Path) -> Optional[Dict]:
        """Process a single document with better error handling"""
        start_time = time.time()
        
        try:
            # Read document
            content = file_path.read_text(encoding='utf-8', errors='ignore')
            
            # Skip very small files
            if len(content.strip()) < 30:
                print(f"  ⏭️  Skipping (too small): {file_path.name}")
                return None
            
            # Create metadata
            metadata = {
                "filename": file_path.name,
                "path": str(file_path),
                "id": file_path.stem,
                "source": self._identify_source(file_path),
                "size": len(content)
            }
            
            print(f"  📄 Processing: {metadata['filename']} ({metadata['size']} chars, {metadata['source']})")
            
            # Extract entities with improved Mistral
            entities = await self.extract_with_mistral(content, metadata)
            
            self.stats.entities_extracted += len(entities)
            self.stats.processed_documents += 1
            
            # Create transformation record
            transformation = {
                "@type": "regen:Transformation",
                "@id": self.generate_rid("transform", f"{metadata['id']}_extraction"),
                "fromState": metadata["path"],
                "toState": [e["@id"] for e in entities],
                "process": "Extract",
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "processingTime": time.time() - start_time,
                "method": "mistral" if self.stats.mistral_success > self.stats.fallback_extractions else "fallback"
            }
            
            return {
                "metadata": metadata,
                "entities": entities,
                "transformation": transformation
            }
            
        except Exception as e:
            print(f"  ❌ Error processing {file_path.name}: {e}")
            self.stats.failed_documents += 1
            return None
    
    def _identify_source(self, file_path: Path) -> str:
        """Identify source from file path"""
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
    
    def save_results(self, output_path: Path) -> None:
        """Save processing results with enhanced metadata"""
        output = {
            "metadata": {
                "processing_date": datetime.now(tz=timezone.utc).isoformat(),
                "processor_version": "improved-koi-v2.0",
                "model": self.model,
                "ontology_version": self.ontology_version,
                "total_documents": self.stats.total_documents,
                "processed_documents": self.stats.processed_documents,
                "failed_documents": self.stats.failed_documents,
                "entities_extracted": self.stats.entities_extracted,
                "discourse_elements": self.stats.discourse_elements,
                "mistral_success_rate": self.stats.mistral_success / max(self.stats.processed_documents, 1),
                "fallback_extractions": self.stats.fallback_extractions,
                "processing_time": self.stats.processing_time,
                "avg_time_per_doc": self.stats.processing_time / max(self.stats.processed_documents, 1)
            },
            "entities": self.processed_entities
        }
        
        with open(output_path, 'w') as f:
            json.dump(output, f, indent=2)
        
        print(f"✅ Results saved to {output_path}")


async def test_improved_processor():
    """Test the improved processor on 20 documents"""
    print("🌿 Testing Improved KOI Processor")
    print("🧪 Enhanced JSON parsing and error handling")
    print("=" * 60)
    
    processor = ImprovedKOIProcessor(model="mistral:7b")
    
    # Find test documents
    data_dir = Path("/Users/darrenzal/projects/RegenAI/GAIA/data")
    patterns = ["*.md", "*.json", "*.txt"]
    files = []
    
    for pattern in patterns:
        found_files = list(data_dir.rglob(pattern))
        found_files = [f for f in found_files if "twitter" not in str(f).lower()]
        files.extend(found_files)
    
    # Select diverse test files
    import random
    random.seed(42)
    test_files = random.sample(files, min(20, len(files)))
    
    print(f"📊 Testing on {len(test_files)} documents")
    print("🚀 Starting enhanced processing...\n")
    
    # Process documents
    start_time = time.time()
    for file_path in test_files:
        result = await processor.process_document(file_path)
        if result:
            processor.processed_entities.append(result)
    
    processor.stats.processing_time = time.time() - start_time
    
    # Print results
    print("\n" + "=" * 60)
    print("📊 IMPROVED PROCESSOR TEST RESULTS")
    print("=" * 60)
    print(f"Documents processed: {processor.stats.processed_documents}/{len(test_files)}")
    print(f"Success rate: {100 * processor.stats.processed_documents / len(test_files):.1f}%")
    print(f"Total entities: {processor.stats.entities_extracted}")
    print(f"Mistral extractions: {processor.stats.mistral_success}")
    print(f"Fallback extractions: {processor.stats.fallback_extractions}")
    print(f"Mistral success rate: {100 * processor.stats.mistral_success / max(processor.stats.processed_documents, 1):.1f}%")
    print(f"Discourse elements: {processor.stats.discourse_elements}")
    print(f"Processing time: {processor.stats.processing_time/60:.1f} minutes")
    print(f"Avg per document: {processor.stats.processing_time/max(processor.stats.processed_documents, 1):.1f} seconds")
    
    # Save results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(f"/Users/darrenzal/projects/RegenAI/koi-processor/improved-test-{timestamp}.json")
    processor.save_results(output_path)
    
    print(f"\n✅ Enhanced test complete!")
    print(f"📁 Results: {output_path}")


if __name__ == "__main__":
    asyncio.run(test_improved_processor())