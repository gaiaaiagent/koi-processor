#!/usr/bin/env python3
"""
Natural Language to SPARQL Query Translator
Uses LLM to convert natural language questions to SPARQL queries
"""

import json
import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
import httpx
import os

logger = logging.getLogger(__name__)

# Ontology prefixes for SPARQL
PREFIXES = """
PREFIX regen: <https://regen.network/ontology#>
PREFIX koi: <https://regen.network/koi#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX schema: <http://schema.org/>
PREFIX dc: <http://purl.org/dc/elements/1.1/>
PREFIX discourse: <https://regen.network/ontology/discourse#>
PREFIX twitter: <https://regen.network/ontology/twitter#>
PREFIX medium: <https://regen.network/ontology/medium#>
PREFIX github: <https://regen.network/ontology/github#>
""".strip()

# Common ontology classes for reference
ONTOLOGY_CLASSES = {
    # Core metabolic
    "System": "regen:System",
    "Organ": "regen:Organ",
    "MetabolicFlow": "regen:MetabolicFlow",
    "Transformation": "regen:Transformation",
    "FeedbackLoop": "regen:FeedbackLoop",

    # Actors and assets
    "Agent": "regen:Agent",
    "HumanActor": "regen:HumanActor",
    "AIAgent": "regen:AIAgent",
    "SemanticAsset": "regen:SemanticAsset",
    "EcologicalAsset": "regen:EcologicalAsset",

    # Discourse elements
    "Question": "regen:Question",
    "Claim": "regen:Claim",
    "Evidence": "regen:Evidence",
    "Argument": "regen:Argument",
    "Solution": "regen:Solution",

    # KOI artifacts
    "Document": "koi:Document",
    "Embedding": "koi:Embedding",
    "ExtractedEntity": "koi:ExtractedEntity",
    "ExtractedRelation": "koi:ExtractedRelation"
}

# Common predicates/relationships
ONTOLOGY_PREDICATES = {
    "hasOrgan": "regen:hasOrgan",
    "produces": "regen:produces",
    "derivesFrom": "regen:derivesFrom",
    "alignsWith": "regen:alignsWith",
    "references": "regen:references",
    "respondsTo": "regen:respondsTo",
    "supports": "regen:supports",
    "challenges": "regen:challenges",
    "hasEvidence": "regen:hasEvidence"
}


class NaturalLanguageToSPARQL:
    """Translates natural language queries to SPARQL"""

    def __init__(
        self,
        llm_provider: str = "ollama",
        model: str = "mistral:7b",
        ollama_url: str = None,
        openai_api_key: str = None
    ):
        self.llm_provider = llm_provider
        self.model = model
        self.ollama_url = ollama_url or os.getenv("OLLAMA_URL", "http://localhost:11434")
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY")

    async def translate(self, question: str) -> Tuple[str, Dict[str, Any]]:
        """
        Translate natural language question to SPARQL query

        Args:
            question: Natural language question

        Returns:
            Tuple of (sparql_query, metadata)
        """
        # Build prompt
        prompt = self._build_prompt(question)

        # Call LLM
        if self.llm_provider == "ollama":
            sparql_query = await self._call_ollama(prompt)
        elif self.llm_provider == "openai":
            sparql_query = await self._call_openai(prompt)
        else:
            sparql_query = self._generate_default_query(question)

        # Validate and clean
        sparql_query = self._validate_and_clean(sparql_query)

        # Extract metadata
        metadata = self._extract_metadata(question, sparql_query)

        return sparql_query, metadata

    def _build_prompt(self, question: str) -> str:
        """Build LLM prompt for translation"""

        # For simple models like Mistral 7B, use minimal prompt
        if "mistral" in self.model.lower():
            return f"""Convert to SPARQL:
Question: {question}

Use these prefixes:
{PREFIXES}

Return only the SELECT query, no explanation.
SPARQL:"""

        # For more capable models, provide examples
        return f"""You are a SPARQL query generator for a knowledge graph about regenerative systems.

ONTOLOGY CLASSES:
{json.dumps(ONTOLOGY_CLASSES, indent=2)}

PREDICATES:
{json.dumps(ONTOLOGY_PREDICATES, indent=2)}

EXAMPLES:

Question: What agents are in the system?
SPARQL:
SELECT ?agent ?name WHERE {{
  ?agent a regen:Agent .
  OPTIONAL {{ ?agent rdfs:label ?name }}
}}

Question: What claims have evidence?
SPARQL:
SELECT ?claim ?evidence WHERE {{
  ?claim a regen:Claim .
  ?claim regen:hasEvidence ?evidence .
}}

Question: What produces semantic assets?
SPARQL:
SELECT ?producer ?asset WHERE {{
  ?producer regen:produces ?asset .
  ?asset a regen:SemanticAsset .
}}

Now convert this question to SPARQL:
Question: {question}

{PREFIXES}

SPARQL:"""

    async def _call_ollama(self, prompt: str) -> str:
        """Call Ollama API"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "stream": False,
                        "temperature": 0.1,
                        "max_tokens": 500
                    }
                )

                if response.status_code == 200:
                    result = response.json()
                    return result.get("response", "")
                else:
                    logger.error(f"Ollama API error: {response.status_code}")
                    return ""

        except Exception as e:
            logger.error(f"Error calling Ollama: {e}")
            return ""

    async def _call_openai(self, prompt: str) -> str:
        """Call OpenAI API"""
        if not self.openai_api_key:
            logger.warning("OpenAI API key not set, using default query")
            return ""

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.openai_api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [
                            {"role": "system", "content": "You are a SPARQL query generator."},
                            {"role": "user", "content": prompt}
                        ],
                        "temperature": 0.1,
                        "max_tokens": 500
                    }
                )

                if response.status_code == 200:
                    result = response.json()
                    return result["choices"][0]["message"]["content"]
                else:
                    logger.error(f"OpenAI API error: {response.status_code}")
                    return ""

        except Exception as e:
            logger.error(f"Error calling OpenAI: {e}")
            return ""

    def _generate_default_query(self, question: str) -> str:
        """Generate a default SPARQL query based on keywords"""

        question_lower = question.lower()

        # Look for entity type keywords
        if "agent" in question_lower:
            return f"""
{PREFIXES}
SELECT ?agent ?name ?type WHERE {{
  ?agent a regen:Agent .
  OPTIONAL {{ ?agent rdfs:label ?name }}
  OPTIONAL {{ ?agent a ?type }}
}} LIMIT 20"""

        elif "claim" in question_lower:
            return f"""
{PREFIXES}
SELECT ?claim ?label ?evidence WHERE {{
  ?claim a regen:Claim .
  OPTIONAL {{ ?claim rdfs:label ?label }}
  OPTIONAL {{ ?claim regen:hasEvidence ?evidence }}
}} LIMIT 20"""

        elif "document" in question_lower:
            return f"""
{PREFIXES}
SELECT ?doc ?title ?type WHERE {{
  ?doc a koi:Document .
  OPTIONAL {{ ?doc dc:title ?title }}
  OPTIONAL {{ ?doc a ?type }}
}} LIMIT 20"""

        elif "relationship" in question_lower or "relation" in question_lower:
            return f"""
{PREFIXES}
SELECT ?subject ?predicate ?object WHERE {{
  ?subject ?predicate ?object .
  FILTER(?predicate != rdf:type)
  FILTER(STRSTARTS(STR(?predicate), STR(regen:)))
}} LIMIT 50"""

        # Default: Get all entities
        return f"""
{PREFIXES}
SELECT ?entity ?type ?label WHERE {{
  ?entity a ?type .
  OPTIONAL {{ ?entity rdfs:label ?label }}
  FILTER(STRSTARTS(STR(?type), STR(regen:)) || STRSTARTS(STR(?type), STR(koi:)))
}} LIMIT 20"""

    def _validate_and_clean(self, query: str) -> str:
        """Validate and clean SPARQL query"""

        if not query:
            return self._generate_default_query("")

        # Remove markdown code blocks if present
        query = query.replace("```sparql", "").replace("```", "")

        # Ensure prefixes are included
        if "PREFIX" not in query:
            query = f"{PREFIXES}\n{query}"

        # Basic validation
        if "SELECT" not in query.upper():
            logger.warning("Invalid SPARQL: missing SELECT")
            return self._generate_default_query("")

        return query.strip()

    def _extract_metadata(self, question: str, sparql: str) -> Dict[str, Any]:
        """Extract metadata about the query"""

        # Count query complexity
        complexity = 0
        if "OPTIONAL" in sparql:
            complexity += 1
        if "FILTER" in sparql:
            complexity += 1
        if "UNION" in sparql:
            complexity += 2
        if "GROUP BY" in sparql:
            complexity += 2

        # Identify query type
        query_type = "simple"
        if complexity >= 2:
            query_type = "complex"
        if "?subject ?predicate ?object" in sparql:
            query_type = "exploration"

        # Extract entity types mentioned
        entity_types = []
        for class_name, uri in ONTOLOGY_CLASSES.items():
            if uri in sparql:
                entity_types.append(class_name)

        return {
            "original_question": question,
            "query_type": query_type,
            "complexity": complexity,
            "entity_types": entity_types,
            "timestamp": datetime.utcnow().isoformat()
        }


# Example usage function
async def example_usage():
    """Example of using the NL to SPARQL translator"""

    translator = NaturalLanguageToSPARQL(
        llm_provider="ollama",
        model="mistral:7b"
    )

    questions = [
        "What agents are in the system?",
        "Show me all claims with evidence",
        "What produces semantic assets?",
        "Find all relationships between entities",
        "What documents are about regenerative agriculture?"
    ]

    for question in questions:
        print(f"\nQuestion: {question}")
        sparql, metadata = await translator.translate(question)
        print(f"SPARQL:\n{sparql}")
        print(f"Metadata: {json.dumps(metadata, indent=2)}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(example_usage())