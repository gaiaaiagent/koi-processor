"""
Canonical entity types and normalization for KOI processor.

This module provides:
- ALL_CANONICAL_TYPES: Complete set of canonical entity types
- LLM_ALLOWED_TYPES: Subset of types that LLMs can emit
- TYPE_ALIASES_TO_CANONICAL: Mapping from aliases to canonical types
- normalize_type(): Function to normalize raw type strings

FIX-002: Extractor/Schema Unification + Prompt Hardening
"""

import re
from typing import Set, Dict, Optional

# ============================================================================
# Canonical Entity Types
# ============================================================================
# The 12 canonical types used throughout the knowledge graph system.
# ENTITY is the fallback default, FUNCTION is for code graphs only.

ALL_CANONICAL_TYPES: Set[str] = {
    "ENTITY",        # Fallback default for unknown types
    "PERSON",        # Named individuals with proper names
    "ORGANIZATION",  # Companies, foundations, networks, institutions
    "PROJECT",       # Named initiatives, platforms (non-software)
    "CONCEPT",       # Abstract ideas, methodologies, frameworks
    "TECHNOLOGY",    # Technical systems, tools, AI systems, software
    "CLAIM",         # Assertions and statements
    "EVIDENCE",      # Supporting data and proof
    "QUESTION",      # Questions and inquiries
    "LOCATION",      # Geographic places (countries, cities, regions)
    "EVENT",         # Named events (calls, conferences, workshops)
    "FUNCTION",      # Code functions (code graph only, NOT LLM-allowed)
}

# ============================================================================
# LLM-Allowed Types
# ============================================================================
# Types that LLMs are allowed to emit. FUNCTION and ENTITY are excluded:
# - FUNCTION: Only used in code graph extraction, not LLM extraction
# - ENTITY: Should never be emitted by LLM; it's a fallback default

LLM_ALLOWED_TYPES: Set[str] = {
    "PERSON",
    "ORGANIZATION",
    "PROJECT",
    "CONCEPT",
    "TECHNOLOGY",
    "CLAIM",
    "EVIDENCE",
    "QUESTION",
    "LOCATION",
    "EVENT",
}

# ============================================================================
# Type Aliases to Canonical Mapping
# ============================================================================
# Maps common aliases, variations, and legacy types to their canonical forms.
# All keys should be UPPERCASE for case-insensitive matching.

TYPE_ALIASES_TO_CANONICAL: Dict[str, str] = {
    # PERSON aliases
    "HUMANACTOR": "PERSON",
    "HUMAN_ACTOR": "PERSON",
    "HUMAN": "PERSON",
    "INDIVIDUAL": "PERSON",
    "ACTOR": "PERSON",
    "AUTHOR": "PERSON",
    "USER": "PERSON",
    "MEMBER": "PERSON",
    "CONTRIBUTOR": "PERSON",

    # ORGANIZATION aliases
    "ORG": "ORGANIZATION",
    "COMPANY": "ORGANIZATION",
    "FOUNDATION": "ORGANIZATION",
    "INSTITUTION": "ORGANIZATION",
    "NETWORK": "ORGANIZATION",
    "DAO": "ORGANIZATION",
    "AGENCY": "ORGANIZATION",
    "GROUP": "ORGANIZATION",
    "TEAM": "ORGANIZATION",

    # PROJECT aliases
    "REPO": "PROJECT",
    "REPOSITORY": "PROJECT",
    "INITIATIVE": "PROJECT",
    "PROGRAM": "PROJECT",
    "PRODUCT": "PROJECT",

    # CONCEPT aliases
    "IDEA": "CONCEPT",
    "TOPIC": "CONCEPT",
    "METHODOLOGY": "CONCEPT",
    "FRAMEWORK": "CONCEPT",
    "THEORY": "CONCEPT",
    "PRINCIPLE": "CONCEPT",
    "STANDARD": "CONCEPT",
    "LICENSE": "CONCEPT",

    # TECHNOLOGY aliases
    "TECH": "TECHNOLOGY",
    "TOOL": "TECHNOLOGY",
    "SYSTEM": "TECHNOLOGY",
    "SOFTWARE": "TECHNOLOGY",
    "PLATFORM": "TECHNOLOGY",
    "AI": "TECHNOLOGY",
    "MODEL": "TECHNOLOGY",
    "SERVICE": "TECHNOLOGY",
    "API": "TECHNOLOGY",

    # LOCATION aliases
    "PLACE": "LOCATION",
    "CITY": "LOCATION",
    "COUNTRY": "LOCATION",
    "REGION": "LOCATION",
    "GPE": "LOCATION",  # Geo-Political Entity (common NER tag)
    "GEO": "LOCATION",
    "ADDRESS": "LOCATION",
    "TERRITORY": "LOCATION",

    # EVENT aliases
    "MEETING": "EVENT",
    "CONFERENCE": "EVENT",
    "WORKSHOP": "EVENT",
    "SUMMIT": "EVENT",
    "CALL": "EVENT",
    "WEBINAR": "EVENT",
    "SESSION": "EVENT",
    "GATHERING": "EVENT",
    "SYMPOSIUM": "EVENT",

    # CLAIM aliases
    "ASSERTION": "CLAIM",
    "STATEMENT": "CLAIM",
    "PROPOSITION": "CLAIM",

    # EVIDENCE aliases
    "PROOF": "EVIDENCE",
    "DATA": "EVIDENCE",
    "FACT": "EVIDENCE",
    "SUPPORT": "EVIDENCE",

    # QUESTION aliases
    "INQUIRY": "QUESTION",
    "QUERY": "QUESTION",
    "ASK": "QUESTION",

    # FUNCTION aliases (code graph only)
    "METHOD": "FUNCTION",
    "PROCEDURE": "FUNCTION",
    "ROUTINE": "FUNCTION",
    "SUBROUTINE": "FUNCTION",
}

# ============================================================================
# URI/Prefix Pattern for Stripping
# ============================================================================
# Regex patterns to strip URI prefixes and namespace prefixes from type strings.

# Matches: https://regen.network/ontology#Person, http://schema.org/Thing, etc.
# Handles both # and / as the final separator before the type name
URI_PATTERN = re.compile(r'^https?://.*[/#]', re.IGNORECASE)

# Matches: regen:Person, koi#PERSON, schema:Thing, etc.
PREFIX_PATTERN = re.compile(r'^[a-zA-Z][a-zA-Z0-9_-]*[:#]', re.IGNORECASE)


def normalize_type(raw_type: Optional[str]) -> str:
    """
    Normalize a raw entity type string to canonical uppercase form.

    Processing steps:
    1. Handle None/empty -> "ENTITY"
    2. Strip full URIs (https://...#Type -> Type)
    3. Strip namespace prefixes (regen:Type, koi#TYPE -> TYPE)
    4. Uppercase
    5. Apply alias mapping
    6. Default to "ENTITY" for unknown types

    Args:
        raw_type: Raw type string from LLM or other source

    Returns:
        Canonical uppercase type string (one of ALL_CANONICAL_TYPES)

    Examples:
        >>> normalize_type("HumanActor")
        'PERSON'
        >>> normalize_type("regen:Person")
        'PERSON'
        >>> normalize_type("https://regen.network/ontology#Organization")
        'ORGANIZATION'
        >>> normalize_type("GPE")
        'LOCATION'
        >>> normalize_type("MEETING")
        'EVENT'
        >>> normalize_type(None)
        'ENTITY'
        >>> normalize_type("unknown_garbage")
        'ENTITY'
    """
    # 1. Handle None/empty
    if not raw_type or not raw_type.strip():
        return "ENTITY"

    cleaned = raw_type.strip()

    # 2. Strip full URIs
    cleaned = URI_PATTERN.sub('', cleaned)

    # 3. Strip namespace prefixes
    cleaned = PREFIX_PATTERN.sub('', cleaned)

    # 4. Uppercase for case-insensitive matching
    upper = cleaned.upper()

    # 5. Check if already canonical
    if upper in ALL_CANONICAL_TYPES:
        return upper

    # 6. Apply alias mapping
    if upper in TYPE_ALIASES_TO_CANONICAL:
        return TYPE_ALIASES_TO_CANONICAL[upper]

    # 7. Default to ENTITY for unknown types
    return "ENTITY"


def is_llm_allowed_type(entity_type: str) -> bool:
    """
    Check if an entity type is allowed to be emitted by LLMs.

    Args:
        entity_type: The entity type to check (should be canonical)

    Returns:
        True if the type is LLM-allowed, False otherwise

    Examples:
        >>> is_llm_allowed_type("PERSON")
        True
        >>> is_llm_allowed_type("FUNCTION")
        False
        >>> is_llm_allowed_type("ENTITY")
        False
    """
    return entity_type.upper() in LLM_ALLOWED_TYPES


def get_canonical_description(entity_type: str) -> str:
    """
    Get a human-readable description of a canonical entity type.

    Args:
        entity_type: Canonical entity type

    Returns:
        Description string
    """
    descriptions = {
        "ENTITY": "Generic entity (fallback default)",
        "PERSON": "Named individuals with proper names",
        "ORGANIZATION": "Companies, foundations, networks, institutions",
        "PROJECT": "Named initiatives, platforms (non-software)",
        "CONCEPT": "Abstract ideas, methodologies, frameworks",
        "TECHNOLOGY": "Technical systems, tools, AI systems, software",
        "CLAIM": "Assertions and statements",
        "EVIDENCE": "Supporting data and proof",
        "QUESTION": "Questions and inquiries",
        "LOCATION": "Geographic places (countries, cities, regions)",
        "EVENT": "Named events (calls, conferences, workshops)",
        "FUNCTION": "Code functions (code graph only)",
    }
    return descriptions.get(entity_type.upper(), "Unknown entity type")


# ============================================================================
# Module Exports
# ============================================================================

__all__ = [
    "ALL_CANONICAL_TYPES",
    "LLM_ALLOWED_TYPES",
    "TYPE_ALIASES_TO_CANONICAL",
    "normalize_type",
    "is_llm_allowed_type",
    "get_canonical_description",
]
