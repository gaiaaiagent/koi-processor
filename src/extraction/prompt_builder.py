"""
Shared prompt builder for LLM-based entity extraction.

This module provides a single source of truth for extraction prompts,
ensuring consistency across all extractors (OpenAI, Ollama/Mistral, etc.).

FIX-002: Extractor/Schema Unification + Prompt Hardening
"""

from typing import Dict, Any, Optional

# Import canonical types
try:
    from core.entity_types import LLM_ALLOWED_TYPES
except ImportError:
    try:
        from src.core.entity_types import LLM_ALLOWED_TYPES
    except ImportError:
        # Fallback for testing
        LLM_ALLOWED_TYPES = {
            "PERSON", "ORGANIZATION", "PROJECT", "CONCEPT",
            "TECHNOLOGY", "CLAIM", "EVIDENCE", "QUESTION",
            "LOCATION", "EVENT"
        }


def build_extraction_prompt(
    content: str,
    source_type: str,
    metadata: Optional[Dict[str, Any]] = None,
    max_content_length: int = 3000
) -> str:
    """
    Build a unified extraction prompt for LLM-based entity extraction.

    This is the single source of truth for all extractors. Changes here
    affect both OpenAI and Ollama/Mistral extractors.

    Args:
        content: Text content to analyze
        source_type: Type of source (discourse, twitter, medium, github, etc.)
        metadata: Optional existing metadata from sensors
        max_content_length: Maximum content length to include (default 3000)

    Returns:
        Complete extraction prompt string

    Notes:
        - Entity types are uppercase from LLM_ALLOWED_TYPES
        - AI systems should be TECHNOLOGY, never PERSON
        - Git commits/changelog lines should NOT be extracted
        - Generic event words should NOT be extracted as EVENT
    """
    # Truncate content for context window
    content_snippet = content[:max_content_length] if len(content) > max_content_length else content

    # Build allowed types string
    allowed_types_str = ", ".join(sorted(LLM_ALLOWED_TYPES))

    prompt = f"""Extract structured data from this {source_type} content.

CONTENT:
{content_snippet}

Extract and return JSON with:
1. Metadata fields with confidence scores (0.0-1.0)
2. Entities based on Regen Network ontology
3. Relationships between entities
4. Summary

## ALLOWED ENTITY TYPES (use UPPERCASE exactly as shown)
{allowed_types_str}

## CRITICAL TYPE RULES

### AI Systems are TECHNOLOGY, never PERSON
AI systems, LLMs, and chatbots MUST be typed as TECHNOLOGY:
- ChatGPT, GPT-4, GPT-3, GPT-4o, Claude, Copilot, Bard, Gemini
- LLaMA, Mistral, OpenAI, Anthropic, Dall-E, Midjourney
- Any AI model, chatbot, or automated assistant
DO NOT type these as PERSON. They are TECHNOLOGY.

### LOCATION: Geographic places only
Use LOCATION for: Countries, cities, regions, states, addresses
Examples: "Boulder, Colorado" (LOCATION), "Colombia" (LOCATION), "United States" (LOCATION)
NOT for: Projects, organizations, or concepts

### EVENT: Only named events with specific titles
ONLY extract as EVENT if the content names a SPECIFIC event with a REAL TITLE.
Valid EVENT examples:
- "Regen Network Community Call" (specific named event)
- "Regen Gathering 2024" (specific named event)
- "COP28" (specific named event)
- "ETHDenver 2024" (specific named event)

DO NOT extract generic event words as EVENT:
- "meeting" - NOT an event
- "call" - NOT an event
- "community call" (without specific title) - NOT an event
- "weekly meeting" - NOT an event
- "workshop" - NOT an event
- "conference" - NOT an event (unless specifically named)
- "webinar" - NOT an event
- "session" - NOT an event
- "discussion" - NOT an event

Rule: If it's a generic word without a specific event name, DO NOT extract it.

## DO NOT EXTRACT (CRITICAL)

### Git commits, changelog entries, version strings
DO NOT extract these patterns as entities:
- "feat(api): add endpoint" - git commit
- "fix: resolve bug" - git commit
- "chore(deps): update dependencies" - git commit
- "Merge pull request #123" - git merge
- "v1.2.3" or "1.0.0-beta" - version strings
- "[Added] New feature" - changelog entry
- "[Fixed] Bug in module" - changelog entry

### Templates and boilerplate
- "Testing Instructions" - template text
- "Acceptance Criteria" - template text
- "Definition of Done" - template text
- "DRY Principles" - template reference
- Issue IDs like "APP-776", "JIRA-123"

### Generic nouns and pronouns
- Pronouns: we, they, it, our, their, you, he, she
- Generic groups: buyers, sellers, partners, users, members, contributors
- Generic roles: validators, participants, stakeholders, developers
- Placeholders: Unknown, Anonymous, Public Users, TBD

### Technical patterns
- URLs: https://example.com
- IP addresses: 192.168.1.1
- File paths: /path/to/file.py
- Code identifiers: snake_case_var, camelCaseVar
- Blockchain addresses: regen1..., cosmos1..., 0x...

## CONCEPT vs PROJECT
- CONCEPT: Abstract ideas, methodologies, frameworks, theories
  Examples: "regenerative agriculture", "proof of stake", "carbon sequestration"
- PROJECT: Concrete initiatives, platforms, named programs
  Examples: "Regen Ledger" (project), "Koi Project" (initiative)
Rule: Abstract idea → CONCEPT. Named implementation → PROJECT or TECHNOLOGY.

## Confidence Scoring
- HIGH (0.85-1.0): Explicit named mention ("Regen Network announced...")
- MEDIUM (0.70-0.84): Implied/contextual ("the network launched...")
- LOW (<0.70): DO NOT EXTRACT (skip entirely)

## JSON Output Structure
Return ONLY valid JSON in this exact format:
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
    {{"type": "CONCEPT", "name": "...", "confidence": 0.85}},
    {{"type": "TECHNOLOGY", "name": "...", "confidence": 0.9}},
    {{"type": "LOCATION", "name": "...", "confidence": 0.9}},
    {{"type": "EVENT", "name": "...", "confidence": 0.9}},
    {{"type": "CLAIM", "name": "...", "confidence": 0.8}},
    {{"type": "EVIDENCE", "name": "...", "confidence": 0.8}},
    {{"type": "QUESTION", "name": "...", "confidence": 0.8}}
  ],
  "relationships": [
    {{"subject": "entity1", "predicate": "supports", "object": "entity2", "confidence": 0.85}}
  ],
  "summary": "one sentence summary"
}}

## Few-Shot Examples

Example 1: Governance Discussion
Input: "Gregory Landua and Sarah Bax discussed regenerative agriculture principles at the Regen Network community meeting."
Extract:
- "Gregory Landua" (PERSON, 0.95)
- "Sarah Bax" (PERSON, 0.95)
- "regenerative agriculture" (CONCEPT, 0.90)
- "Regen Network" (ORGANIZATION, 0.95)
Do NOT extract: "community", "meeting" (generic words)

Example 2: Technical Documentation
Input: "The Regen Ledger blockchain uses proof of stake consensus. ChatGPT and Claude can assist with documentation."
Extract:
- "Regen Ledger" (TECHNOLOGY, 0.95)
- "proof of stake" (CONCEPT, 0.90)
- "ChatGPT" (TECHNOLOGY, 0.95) - AI system, NOT PERSON
- "Claude" (TECHNOLOGY, 0.95) - AI system, NOT PERSON
Do NOT extract: "blockchain", "consensus", "documentation"

Example 3: Event Mention
Input: "Join us at Regen Gathering 2024 in Boulder, Colorado. We also have a weekly meeting every Tuesday."
Extract:
- "Regen Gathering 2024" (EVENT, 0.95) - specific named event
- "Boulder, Colorado" (LOCATION, 0.95)
Do NOT extract: "weekly meeting" (generic, no specific title), "Tuesday" (time reference)

Example 4: Git/Changelog (extract NOTHING)
Input: "feat(api): add endpoint for user registration. Merge pull request #456 from feature/auth"
Extract:
- NOTHING (this is git commit/changelog content)

Focus on regenerative finance, ecological, and commons-oriented content.
Return ONLY valid JSON, no additional text or markdown."""

    return prompt


def get_system_message() -> str:
    """
    Get the system message for LLM extraction.

    Returns:
        System message string for the LLM
    """
    return "You are a semantic extraction system that outputs only valid JSON. Extract entities with their types in UPPERCASE exactly as specified."


# ============================================================================
# Module Exports
# ============================================================================

__all__ = [
    "build_extraction_prompt",
    "get_system_message",
    "LLM_ALLOWED_TYPES",
]
