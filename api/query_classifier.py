"""
B9a — Query classifier.

Layer 2 of the router: classifies a user query into a QueryTaxonomy
category via GPT-4o-mini structured output. Returns ClassifierOutput
with confidence score for fallback gating.

See: docs/specs/b9a-query-plan-spec.md §5 for the prompt template.
"""

from __future__ import annotations

import json
import logging

from api.schemas.query_plan import (
    ClassifierOutput,
    DepthTier,
    EntityCandidate,
    QueryTaxonomy,
)

logger = logging.getLogger(__name__)

CLASSIFIER_CONFIDENCE_THRESHOLD = 0.7

CLASSIFIER_PROMPT = """You are a query classifier for a bioregional knowledge commons (BKC).
Given a user question, classify it into exactly one taxonomy category
and extract any entity mentions.

## Taxonomy

- entity_definition: "What is X?" — asking for a definition or description of a single concept, species, place, organization, or person. Examples: "What is eelgrass?", "What is the Salish Sea?", "What is bioregionalism?"
- relationship_path: "How does X relate to Y?" — asking about connections, relationships, or multi-hop paths between entities. Examples: "Which organizations work on restoration?", "What species are connected to Chinook salmon?", "How does cosmolocalism relate to bioregional knowledge?"
- governance_policy: "What is the policy on X?" — asking about protocols, governance rules, data sovereignty, meta-protocol, decision-making frameworks, indigenous data principles, or organizational decision processes. Examples: "What are OCAP principles?", "How does data sovereignty work in the BKC?", "What is the federation membrane governance?", "How does the BKC handle data sovereignty?"
- roadmap_status: "What is the status of X?" — asking about project status, milestones, work items, timelines. Examples: "What is the status of commitment pooling?", "What milestones have been completed?"
- commitment_claim: "What commitments exist for X?" — asking about pledges, claims, evidence, commitment pools, flow funding settlements, or the claims engine. Examples: "What commitments has Victoria Landscape Hub made?", "How does the claims engine work?", "What is a commitment pool?"
- cross_node_provenance: "What does node Y know about X?" — asking about information from a specific bioregional node or cross-node comparison.
- out_of_domain: Questions clearly outside bioregional knowledge commons scope — technology installs, stock prices, pop culture, general knowledge unrelated to ecology/governance/stewardship. If the question mentions bioregional concepts, organizations, or ecological topics, it is NOT out_of_domain even if you are unsure of the specific answer.

## Depth

- shallow: ONLY for simple single-entity lookups where the entity name is explicit and well-known (e.g., "What is eelgrass?"). If the question asks about a complex concept, protocol, framework, or process, use standard or deep even if it looks like "What is X?".
- standard: Typical question requiring entity + document search. DEFAULT choice when unsure.
- deep: Complex question requiring multiple search strategies or synthesis across sources.

## Entities

Extract named entities mentioned in the question. Include:
- Species, ecosystems, locations, bioregions
- Organizations, people, projects
- Concepts, protocols, practices
- Specific items like "commitment pool", "Victoria Landscape Hub"

## Confidence

Rate your confidence in the classification:
- 0.9-1.0: Clear match to one category, no ambiguity
- 0.7-0.9: Good match but could arguably fit another category
- 0.5-0.7: Ambiguous, could reasonably be classified differently
- below 0.5: Very unsure

Default to 0.85 for typical bioregional questions. Only go below 0.7 if genuinely ambiguous.

## Output format (JSON)

{
  "query_taxonomy": "<one of the 7 categories>",
  "depth_tier": "shallow | standard | deep",
  "entities": [
    {"name": "<entity name>", "type": "<entity type or null>"}
  ],
  "reasoning": "<1 sentence explaining classification>",
  "confidence": 0.0-1.0
}"""


async def classify_query(
    query: str,
    openai_client,
    model: str = "gpt-4o-mini",
) -> ClassifierOutput:
    """Classify a query into a QueryTaxonomy category via GPT-4o-mini.

    Returns ClassifierOutput with confidence score.
    On parse error or unexpected output, returns OUT_OF_DOMAIN with confidence=0.0
    to trigger fallback to the baseline retrieval path.
    """
    import asyncio

    try:
        response = await asyncio.to_thread(
            openai_client.chat.completions.create,
            model=model,
            messages=[
                {"role": "system", "content": CLASSIFIER_PROMPT},
                {"role": "user", "content": query},
            ],
            temperature=0.0,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)

        # Parse taxonomy
        taxonomy_str = data.get("query_taxonomy", "out_of_domain")
        try:
            taxonomy = QueryTaxonomy(taxonomy_str)
        except ValueError:
            logger.warning(f"Unknown taxonomy '{taxonomy_str}', defaulting to out_of_domain")
            taxonomy = QueryTaxonomy.OUT_OF_DOMAIN

        # Parse depth tier
        depth_str = data.get("depth_tier", "standard")
        try:
            depth = DepthTier(depth_str)
        except ValueError:
            depth = DepthTier.STANDARD

        # Parse entities
        entities = []
        for e in data.get("entities", []):
            if isinstance(e, dict) and "name" in e:
                entities.append(EntityCandidate(
                    name=e["name"],
                    type=e.get("type"),
                ))

        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))

        return ClassifierOutput(
            query_taxonomy=taxonomy,
            depth_tier=depth,
            entities=entities,
            reasoning=data.get("reasoning", ""),
            confidence=confidence,
        )

    except Exception as e:
        logger.warning(f"Query classification failed, falling back to OUT_OF_DOMAIN: {e}")
        return ClassifierOutput(
            query_taxonomy=QueryTaxonomy.OUT_OF_DOMAIN,
            depth_tier=DepthTier.STANDARD,
            reasoning=f"parse_error: {e}",
            confidence=0.0,
        )
