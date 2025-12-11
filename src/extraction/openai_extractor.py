"""
OpenAI GPT-4o-mini based semantic extraction service for KOI processor
Cost-effective extraction using OpenAI API with batch processing support
"""

import asyncio
import json
import logging
import os
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


class OpenAIExtractor:
    """
    Extracts structured metadata and entities from text using OpenAI GPT-4o-mini
    Supports both immediate and batch processing for cost optimization
    """

    def __init__(
        self,
        api_key: str = None,
        model: str = "gpt-4o-mini",
        use_batch_api: bool = False,
        ontology_dir: str = "/opt/projects/koi-research/ontologies"
    ):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model
        self.use_batch_api = use_batch_api
        self.ontology_dir = Path(ontology_dir)
        self.logger = logging.getLogger(__name__)

        # OpenAI API endpoints
        self.base_url = "https://api.openai.com/v1"
        self.chat_endpoint = f"{self.base_url}/chat/completions"
        self.batch_endpoint = f"{self.base_url}/batches"

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
        Extract semantic metadata from content using OpenAI GPT-4o-mini

        Args:
            content: Text content to analyze
            source_type: Type of source (discourse, twitter, medium, etc.)
            existing_metadata: Basic metadata already extracted by sensors

        Returns:
            Enhanced metadata with semantic entities and relationships
        """

        # Build extraction prompt
        prompt = self._build_extraction_prompt(content, source_type, existing_metadata)

        try:
            if self.use_batch_api:
                # Queue for batch processing
                extraction = await self._queue_for_batch(prompt)
            else:
                # Immediate processing
                extraction = await self._call_openai(prompt)

            # Parse and validate extraction
            metadata = self._parse_extraction(extraction, source_type)

            # Merge with existing metadata
            if existing_metadata:
                metadata = {**existing_metadata, **metadata}

            # Add extraction provenance
            metadata["llm_extraction"] = {
                "model": self.model,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source_ontology": source_type,
                "batch_mode": self.use_batch_api
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
        """Build ontology-based extraction prompt for GPT-4o-mini"""

        # Truncate content for context window (GPT-4o-mini has 128k context)
        content_snippet = content[:3000] if len(content) > 3000 else content

        # Ontology-driven prompt optimized for GPT-4o-mini
        prompt = f"""Extract structured data from this {source_type} content.

CONTENT:
{content_snippet}

Extract and return JSON with:
1. Metadata fields with confidence scores (0.0-1.0)
2. Entities based on Regen Network ontology (HumanActor, Claim, Evidence, Question)
3. Relationships between entities
4. Discourse type classification

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
    {{"type": "HumanActor", "name": "...", "properties": {{}}}},
    {{"type": "Claim", "name": "...", "content": "..."}}
  ],
  "relationships": [
    {{"subject": "entity1", "predicate": "supports", "object": "entity2"}}
  ],
  "discourse_type": "claim|evidence|question|discussion",
  "claims": ["claim text"],
  "evidence": ["evidence text"],
  "questions": ["question text"],
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
- "regenerative agriculture" (CONCEPT, 0.90)  # Abstract methodology
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

    async def _call_openai(self, prompt: str) -> Dict[str, Any]:
        """Call OpenAI API for immediate processing"""

        self.logger.info(f"[OPENAI] Starting API call with model {self.model}")
        self.logger.info(f"[OPENAI] Prompt length: {len(prompt)} characters")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        request_data = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a semantic extraction system that outputs only valid JSON."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.3,
            "max_tokens": 1000,
            "response_format": {"type": "json_object"}  # Ensures JSON response
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                import time
                start_time = time.time()

                self.logger.info(f"[OPENAI] Sending request to {self.chat_endpoint}")
                response = await client.post(
                    self.chat_endpoint,
                    headers=headers,
                    json=request_data
                )

                elapsed = time.time() - start_time
                self.logger.info(f"[OPENAI] Response received in {elapsed:.2f} seconds")

                if response.status_code == 200:
                    result = response.json()
                    content = result["choices"][0]["message"]["content"]

                    # Parse JSON response
                    extraction = json.loads(content)

                    # Log usage for cost tracking
                    usage = result.get("usage", {})
                    self.logger.info(f"[OPENAI] Tokens used - Prompt: {usage.get('prompt_tokens', 0)}, "
                                   f"Completion: {usage.get('completion_tokens', 0)}, "
                                   f"Total: {usage.get('total_tokens', 0)}")

                    # Estimate cost (GPT-4o-mini pricing: $0.15/1M input, $0.60/1M output)
                    input_cost = (usage.get('prompt_tokens', 0) / 1_000_000) * 0.15
                    output_cost = (usage.get('completion_tokens', 0) / 1_000_000) * 0.60
                    total_cost = input_cost + output_cost
                    self.logger.info(f"[OPENAI] Estimated cost: ${total_cost:.6f}")

                    return extraction
                else:
                    self.logger.error(f"[OPENAI] API error: {response.status_code}")
                    self.logger.error(f"[OPENAI] Response: {response.text}")
                    return {}

            except json.JSONDecodeError as e:
                self.logger.error(f"[OPENAI] JSON decode error: {e}")
                return {}
            except Exception as e:
                self.logger.error(f"[OPENAI] Unexpected error: {type(e).__name__}: {str(e)}")
                return {}

    async def _queue_for_batch(self, prompt: str) -> Dict[str, Any]:
        """Queue request for batch processing (for cost optimization)"""

        # This would typically write to a JSONL file for batch processing
        # and return a placeholder response
        self.logger.info("[OPENAI] Queuing for batch processing")

        # For now, return empty dict - implement full batch later
        return {
            "batch_queued": True,
            "message": "Queued for batch processing"
        }

    def _parse_extraction(self, extraction: Dict[str, Any], source_type: str) -> Dict[str, Any]:
        """Parse and validate OpenAI extraction with confidence scores"""

        metadata = {
            "semantic_extraction": extraction,
            "source_type": source_type
        }

        # Extract metadata with confidence scores
        if "metadata" in extraction:
            llm_metadata = extraction["metadata"]
            metadata["llm_extracted_metadata"] = {}
            metadata["llm_metadata_confidence"] = {}

            for field, info in llm_metadata.items():
                if isinstance(info, dict) and "value" in info:
                    metadata["llm_extracted_metadata"][field] = info["value"]
                    metadata["llm_metadata_confidence"][field] = info.get("confidence", 0.5)

        # Extract entities
        if "entities" in extraction:
            metadata["extracted_entities"] = extraction["entities"]

        # Extract relationships
        if "relationships" in extraction:
            metadata["extracted_relationships"] = extraction["relationships"]

        # Extract discourse elements
        for field in ["claims", "evidence", "questions", "discourse_type", "summary"]:
            if field in extraction:
                metadata[field] = extraction[field]

        return metadata

    async def extract_batch(
        self,
        documents: List[Dict[str, Any]],
        batch_size: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Extract metadata from multiple documents in batches

        Args:
            documents: List of documents with content and metadata
            batch_size: Number of documents to process per batch

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

            # Add small delay between batches to avoid rate limits
            if i + batch_size < len(documents):
                await asyncio.sleep(1)

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
    """Example of using the OpenAI extractor"""

    extractor = OpenAIExtractor()

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
