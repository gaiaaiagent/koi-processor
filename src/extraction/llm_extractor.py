"""
LLM-based semantic extraction service for KOI processor
Uses Mistral 7B via Ollama for ontology-driven entity and relationship extraction
"""

import asyncio
import json
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
import httpx
from pathlib import Path
import hashlib

# Import ontology utilities
try:
    import rdflib
    from rdflib import Graph, Namespace, URIRef, Literal, RDF, RDFS
except ImportError:
    print("Warning: rdflib not installed. Install with: pip install rdflib")
    rdflib = None


class OntologyLLMExtractor:
    """
    Extracts structured metadata and entities from text using LLM and ontologies
    """

    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        model: str = "mistral:7b",
        ontology_dir: str = "/opt/projects/koi-research/ontologies"
    ):
        self.ollama_url = ollama_url
        self.model = model
        self.ontology_dir = Path(ontology_dir)
        self.logger = logging.getLogger(__name__)

        # Load ontologies
        self.unified_ontology = self._load_ontology("regen-unified-ontology.ttl")
        self.source_ontologies = self._load_source_ontologies()

        # Define namespaces
        self.REGEN = Namespace("https://regen.network/ontology#")
        self.DISCOURSE = Namespace("https://regen.network/ontology/discourse#")
        self.TWITTER = Namespace("https://regen.network/ontology/twitter#")
        self.MEDIUM = Namespace("https://regen.network/ontology/medium#")
        self.GITHUB = Namespace("https://regen.network/ontology/github#")

    def _load_ontology(self, filename: str) -> Optional[Any]:
        """Load an ontology file into RDF graph"""
        if not rdflib:
            return None

        try:
            g = Graph()
            ontology_path = self.ontology_dir / filename
            if ontology_path.exists():
                g.parse(str(ontology_path), format="turtle")
                self.logger.info(f"Loaded ontology: {filename}")
                return g
        except Exception as e:
            self.logger.error(f"Error loading ontology {filename}: {e}")
        return None

    def _load_source_ontologies(self) -> Dict[str, Any]:
        """Load all source-specific ontologies"""
        ontologies = {}
        source_dir = self.ontology_dir / "source-specific"

        if source_dir.exists():
            for file in source_dir.glob("*.ttl"):
                source_name = file.stem.replace("-ontology", "")
                ontologies[source_name] = self._load_ontology(f"source-specific/{file.name}")

        return ontologies

    async def extract_metadata(
        self,
        content: str,
        source_type: str,
        existing_metadata: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Extract semantic metadata from content using LLM and ontologies

        Args:
            content: Text content to analyze
            source_type: Type of source (discourse, twitter, medium, etc.)
            existing_metadata: Basic metadata already extracted by sensors

        Returns:
            Enhanced metadata with semantic entities and relationships
        """

        # Get source-specific ontology
        source_ontology = self.source_ontologies.get(source_type)

        # Build extraction prompt
        prompt = self._build_extraction_prompt(content, source_type, existing_metadata)

        try:
            # Call Ollama API
            extraction = await self._call_ollama(prompt)

            # Parse and validate extraction
            metadata = self._parse_extraction(extraction, source_type)

            # Merge with existing metadata
            if existing_metadata:
                metadata = {**existing_metadata, **metadata}

            # Add extraction provenance
            metadata["llm_extraction"] = {
                "model": self.model,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source_ontology": source_type
            }

            return metadata

        except Exception as e:
            self.logger.error(f"Extraction failed: {e}")
            return existing_metadata or {}

    def _build_extraction_prompt(
        self,
        content: str,
        source_type: str,
        metadata: Dict[str, Any] = None
    ) -> str:
        """Build LLM prompt for extraction based on ontology"""

        # Truncate content for context window
        content_snippet = content[:1500] if len(content) > 1500 else content

        # Ultra-simplified prompt for Mistral 7B
        prompt = f"""Extract structured data from this {source_type} content.

CONTENT:
{content_snippet}

Extract and return JSON with:
1. Metadata fields with confidence scores (0.0-1.0)
2. Entities based on Regen ontology (HumanActor/PERSON, ORGANIZATION, PROJECT, CONCEPT, TECHNOLOGY, CLAIM, EVIDENCE, QUESTION)
3. Relationships between entities
4. Summary

JSON structure required:
{{
  "metadata": {{
    "title": {{"value": "...", "confidence": 0.9}},
    "author": {{"value": "...", "confidence": 0.8}},
    "published_date": {{"value": "ISO date", "confidence": 0.7}},
    "organization": {{"value": "...", "confidence": 0.6}},
    "tags": {{"value": ["tag1", "tag2"], "confidence": 0.8}}
  }},
  "entities": [
    {{"type": "PERSON", "name": "...", "confidence": 0.9}},
    {{"type": "ORGANIZATION", "name": "...", "confidence": 0.9}},
    {{"type": "PROJECT", "name": "...", "confidence": 0.9}},
    {{"type": "CONCEPT", "name": "...", "confidence": 0.9}},
    {{"type": "TECHNOLOGY", "name": "...", "confidence": 0.9}},
    {{"type": "CLAIM", "name": "...", "confidence": 0.8}},
    {{"type": "EVIDENCE", "name": "...", "confidence": 0.8}},
    {{"type": "QUESTION", "name": "...", "confidence": 0.8}}
  ],
  "relationships": [
    {{"subject": "entity1", "predicate": "supports", "object": "entity2"}}
  ],
  "summary": "one sentence summary"
}}

Focus on regenerative finance, ecological, and commons-oriented content.
Return ONLY valid JSON, no additional text.

## Entity Type Selection Guidelines

CONCEPT vs PROJECT:
- CONCEPT: Abstract ideas, methodologies, frameworks, theories, movements.
  Examples: "regenerative agriculture", "proof of stake", "tokenomics", "MRV", "carbon sequestration"
  NOT: Specific organizations or projects implementing the idea.
- PROJECT: Concrete initiatives, platforms, software, named programs.
  Examples: "Regen Ledger" (software), "Koi Project" (initiative), "DeSci Publish" (platform)
  NOT: General concepts like "blockchain" or "carbon credits"
- Rule: If abstract → CONCEPT. If named implementation → PROJECT/ORGANIZATION.

PERSON vs GROUP:
- PERSON: Named individuals with proper names.
  Examples: "Gregory Landua", "Sarah Bax", "Will Szal"
- DO NOT EXTRACT as PERSON:
  - Generic groups: "buyers", "sellers", "partners", "users", "members", "contributors"
  - Plural collectives: "stakeholders", "participants", "investors", "validators"
  - Roles without names: "administrators", "developers", "moderators"
  - Utilities/services: "water utilities", "providers", "suppliers"
  → Extract as ORGANIZATION or omit entirely.

LOCATION vs PROJECT:
- Country codes (UK, US, EU, CA, AU) → LOCATION/ORGANIZATION as context; never PROJECT.

LICENSE/STANDARD as CONCEPT:
- Licenses: "Apache License", "MIT License"
- Technical standards: "ERC-20", "ERC-721" (as standards, not projects)

## Extraction Quality Rules

DO NOT EXTRACT:
- Pronouns (we, they, it, our, their)
- Generic nouns (people, user, member, organization)
- JIRA IDs (APP-776, ERC-123)
- Template text ("Testing Instructions", "Acceptance Criteria", "DRY Principles")
- Placeholders ("Unknown", "Anonymous", "Public Users", "TBD")
- Technical paths (app.regen.claim, api.regen.network)
- Pure numbers (2030, 35)
- URLs or code identifiers

Confidence Scoring:
- HIGH (0.85-1.0): Explicit named mention ("Regen Network announced...")
- MEDIUM (0.70-0.84): Implied/contextual ("the network launched...")
- LOW (<0.70): DO NOT EXTRACT (skip)

## Few-Shot Examples

Example 1: Governance Discussion
Input: "Gregory Landua and Sarah Bax discussed regenerative agriculture principles at the Regen Network community meeting."
Extract:
- "Gregory Landua" (PERSON, 0.95)
- "Sarah Bax" (PERSON, 0.95)
- "regenerative agriculture" (CONCEPT, 0.90)
- "Regen Network" (ORGANIZATION, 0.95)
Do NOT extract: "community", "meeting"

Example 2: Technical Documentation
Input: "The Regen Ledger blockchain uses proof of stake consensus. Carbon credits are tokenized as ERC-20 compatible assets."
Extract:
- "Regen Ledger" (TECHNOLOGY, 0.95)
- "proof of stake" (CONCEPT, 0.90)
- "carbon credits" (CONCEPT, 0.85)
- "ERC-20" (CONCEPT, 0.80)
Do NOT extract: "blockchain", "consensus", "assets"

Example 3: Project Template
Input: "Testing Instructions: Verify APP-776 implements DRY principles. Acceptance Criteria: All buyers can purchase credits."
Extract:
- NOTHING (template/boilerplate)
Do NOT extract: "APP-776", "DRY principles", "Acceptance Criteria", "buyers"
"""

        return prompt

    async def _call_ollama(self, prompt: str) -> Dict[str, Any]:
        """Call Ollama API for LLM inference"""

        # Debug logging
        self.logger.info(f"[OLLAMA] Starting API call at {self.ollama_url}")
        self.logger.info(f"[OLLAMA] Model: {self.model}")
        self.logger.info(f"[OLLAMA] Prompt length: {len(prompt)} characters")
        self.logger.debug(f"[OLLAMA] Full prompt: {prompt[:200]}...")

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                request_data = {
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    # REMOVED "format": "json" - causes Mistral to hang!
                    "options": {
                        "temperature": 0.3,  # Lower temp for structured output
                        "top_p": 0.9,
                        "seed": 42,  # For reproducibility
                        "num_predict": 256  # Reduced for faster response
                    }
                }

                self.logger.info(f"[OLLAMA] Request prepared with num_predict={request_data['options']['num_predict']}")
                self.logger.info(f"[OLLAMA] Sending POST request to {self.ollama_url}/api/generate")

                import time
                start_time = time.time()

                response = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json=request_data,
                    timeout=30.0
                )

                elapsed = time.time() - start_time
                self.logger.info(f"[OLLAMA] Response received in {elapsed:.2f} seconds")
                response.raise_for_status()

                result = response.json()
                self.logger.info(f"[OLLAMA] Raw response keys: {list(result.keys())}")
                self.logger.info(f"[OLLAMA] Response 'done': {result.get('done', 'missing')}")

                # Parse the response text as JSON
                try:
                    response_text = result.get("response", "{}")
                    self.logger.info(f"[OLLAMA] Response text length: {len(response_text)} chars")
                    self.logger.debug(f"[OLLAMA] Response text (first 500): {response_text[:500]}")

                    extraction = json.loads(response_text)
                    self.logger.info(f"[OLLAMA] JSON parsed successfully!")
                    self.logger.info(f"[OLLAMA] Extracted keys: {list(extraction.keys())}")
                    self.logger.info(f"[OLLAMA] Entities found: {len(extraction.get('entities', []))}")
                    return extraction
                except json.JSONDecodeError as e:
                    self.logger.error(f"[OLLAMA] JSON decode error: {e}")
                    self.logger.error(f"[OLLAMA] Failed text was: {response_text[:100]}...")
                    # Try to extract JSON from response
                    text = result.get("response", "")
                    start = text.find("{")
                    end = text.rfind("}") + 1
                    if start >= 0 and end > start:
                        try:
                            extracted = json.loads(text[start:end])
                            self.logger.info(f"[OLLAMA] Recovered JSON from malformed response")
                            return extracted
                        except Exception as recover_err:
                            self.logger.error(f"[OLLAMA] Recovery failed: {recover_err}")
                    return {}

            except httpx.TimeoutException as timeout_err:
                self.logger.error(f"[OLLAMA] TIMEOUT after 30 seconds!")
                self.logger.error(f"[OLLAMA] Timeout details: {str(timeout_err)}")
                self.logger.error(f"[OLLAMA] This usually means Ollama is stuck processing")
                self.logger.error(f"[OLLAMA] Check with: ps aux | grep ollama")
                return {}
            except httpx.HTTPStatusError as http_err:
                self.logger.error(f"[OLLAMA] HTTP error: {http_err.response.status_code}")
                self.logger.error(f"[OLLAMA] Response: {http_err.response.text[:200]}")
                return {}
            except Exception as e:
                self.logger.error(f"[OLLAMA] Unexpected error: {type(e).__name__}: {str(e)}")
                self.logger.error(f"[OLLAMA] Failed prompt was: {prompt[:200]}...")
                import traceback
                self.logger.error(f"[OLLAMA] Traceback: {traceback.format_exc()}")
                return {}

    def _parse_extraction(self, extraction: Dict[str, Any], source_type: str) -> Dict[str, Any]:
        """Parse and validate LLM extraction with confidence scores"""

        metadata = {
            "semantic_extraction": extraction,
            "source_type": source_type
        }

        # Extract metadata with confidence scores
        if "metadata" in extraction:
            llm_metadata = extraction["metadata"]
            # Store both the extracted values and confidence scores
            metadata["llm_extracted_metadata"] = {}
            metadata["llm_metadata_confidence"] = {}

            for field, info in llm_metadata.items():
                if isinstance(info, dict) and "value" in info:
                    metadata["llm_extracted_metadata"][field] = info["value"]
                    metadata["llm_metadata_confidence"][field] = info.get("confidence", 0.5)
                    # Also store reasoning if needed for debugging
                    if "reasoning" in info:
                        metadata.setdefault("llm_metadata_reasoning", {})[field] = info["reasoning"]

        # Extract entities
        entities = extraction.get("entities", [])
        if entities:
            metadata["extracted_entities"] = [
                {
                    "type": e.get("type", "unknown"),
                    "name": e.get("name", ""),
                    "properties": e.get("properties", {})
                }
                for e in entities
            ]

        # Extract relationships
        relationships = extraction.get("relationships", [])
        if relationships:
            metadata["extracted_relationships"] = relationships

        # Extract discourse elements (including questions now)
        if extraction.get("claims"):
            metadata["claims"] = extraction["claims"]
        if extraction.get("evidence"):
            metadata["evidence"] = extraction["evidence"]
        if extraction.get("questions"):
            metadata["questions"] = extraction["questions"]

        # Extract essence alignment
        if extraction.get("essence_alignment"):
            metadata["essence_alignment"] = extraction["essence_alignment"]

        # Add discourse type
        if extraction.get("discourse_type"):
            metadata["discourse_type"] = extraction["discourse_type"]

        # Add summary
        if extraction.get("summary"):
            metadata["semantic_summary"] = extraction["summary"]

        return metadata

    async def extract_batch(
        self,
        documents: List[Dict[str, Any]],
        batch_size: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Extract metadata from multiple documents in batches

        Args:
            documents: List of documents with content and metadata
            batch_size: Number of documents to process concurrently

        Returns:
            List of documents with enhanced metadata
        """

        enhanced_docs = []

        for i in range(0, len(documents), batch_size):
            batch = documents[i:i + batch_size]

            # Process batch concurrently
            tasks = [
                self.extract_metadata(
                    doc.get("content", ""),
                    doc.get("source_type", "unknown"),
                    doc.get("metadata", {})
                )
                for doc in batch
            ]

            results = await asyncio.gather(*tasks)

            # Merge results back into documents
            for doc, metadata in zip(batch, results):
                doc["metadata"] = metadata
                enhanced_docs.append(doc)

            self.logger.info(f"Processed batch {i//batch_size + 1}: {len(batch)} documents")

        return enhanced_docs

    def generate_cat_receipt(
        self,
        original_rid: str,
        extraction_metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Generate CAT receipt for LLM extraction transformation

        Args:
            original_rid: RID of original content
            extraction_metadata: Metadata from extraction

        Returns:
            CAT receipt documenting the transformation
        """

        # Create transformation hash
        transformation_data = {
            "original_rid": original_rid,
            "model": self.model,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "extraction": extraction_metadata
        }

        transformation_hash = hashlib.sha256(
            json.dumps(transformation_data, sort_keys=True).encode()
        ).hexdigest()

        receipt = {
            "rid": f"orn:cat:extraction:{transformation_hash[:16]}",
            "type": "llm_extraction",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "parent_rid": original_rid,
            "transformation": {
                "model": self.model,
                "ontology": extraction_metadata.get("source_type"),
                "entities_extracted": len(extraction_metadata.get("extracted_entities", [])),
                "relationships_extracted": len(extraction_metadata.get("extracted_relationships", [])),
            },
            "hash": transformation_hash
        }

        return receipt


# Example usage
async def main():
    """Example of using the LLM extractor"""

    extractor = OntologyLLMExtractor()

    # Example discourse content
    content = """
    I've been thinking about the carbon credit methodology we're using.
    The current approach seems to undervalue biodiversity co-benefits.
    We should consider adopting the Verra VM0042 standard which better
    captures ecosystem services beyond just carbon sequestration.

    @alice mentioned in the last meeting that their project saw a 30%
    increase in credit value after switching methodologies. This could
    be significant for our farmers.
    """

    metadata = await extractor.extract_metadata(
        content,
        "discourse",
        {"author": "bob", "post_id": "123", "topic": "Methodology Discussion"}
    )

    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
