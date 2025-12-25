"""
Predicate validation and normalization for knowledge graph relationships.

Week 13: Shared module to avoid import tangles between prompt_builder.py and llm_extractor.py.
Provides canonical predicate allowlist and validation functions.
"""

import os
import logging
from typing import Tuple, List, Dict, Any

logger = logging.getLogger(__name__)

# ============================================================================
# CANONICAL PREDICATES (Week 12)
# Only these predicates should be emitted by extractors.
# This prevents regrowth of normalized/deprecated predicates.
# ============================================================================
CANONICAL_PREDICATES = {
    # Core relationships (high frequency)
    "supports", "uses", "mentions", "implements", "includes", "manages",
    "enables", "part_of", "requires", "provides", "associated_with",
    "located_in", "defines", "relates_to", "works_with", "represents",
    "contains", "addresses", "hosts", "validates", "governs",
    "participates_in", "leads", "monitors", "promotes", "performs",
    "focuses_on", "affects", "queries", "updates", "aligns_with",
    "is_a", "targets", "interacts_with", "contributes_to", "improves",
    "operates", "creates", "built_on", "proposes", "authored",
    "founded", "discusses",

    # Organization/People
    "member_of", "works_at", "employs", "advises",

    # Process/Action
    "executes", "processes", "generates", "analyzes", "evaluates",
    "measures", "deploys", "maintains", "funds", "connects",

    # Knowledge/Communication
    "describes", "explains", "documents", "announces",

    # Platform/Tool (Week 11 E402)
    "documents_on", "integrates_with", "powered_by", "communicates_via",

    # Regen Domain
    "anchors", "bridges", "delegates", "votes", "credits", "issues",
    "retires", "verifies", "registers", "approves", "mints", "burns",

    # Lifecycle
    "replaces", "upgrades",
}

# ============================================================================
# PREDICATE MAPPINGS (Week 12 normalization)
# Maps deprecated/variant predicates to canonical forms.
# ============================================================================
PREDICATE_MAPPINGS = {
    # Platform predicates
    "hosted_on": "uses",
    "published_on": "documents_on",
    "linked_to": "associated_with",
    "based_in": "located_in",

    # Role consolidation
    "founder_of": "founded",
    "is_founder_of": "founded",
    "is_ceo_of": "leads",
    "ceo_of": "leads",

    # Tense normalization
    "exploring": "discusses",
    "presented": "discusses",
    "presenting": "discusses",
    "discussed": "discusses",

    # Other variants
    "related_to": "relates_to",
    "works_on": "works_with",
    "part-of": "part_of",
    "is_part_of": "part_of",
    "member": "member_of",
    "employed_by": "works_at",
    "employes": "employs",  # typo fix
}


def validate_predicate(predicate: str, strict: bool = False) -> Tuple[str, bool]:
    """
    Validate and optionally normalize a predicate.

    Args:
        predicate: The predicate to validate
        strict: If True, reject non-canonical predicates (return fallback).
                If False, log and allow non-canonical predicates.

    Returns:
        Tuple of (normalized_predicate, is_canonical)
        - normalized_predicate: The predicate to use (canonical or original)
        - is_canonical: True if predicate is in the canonical set
    """
    if not predicate:
        return ("associated_with", False)

    # Normalize: lowercase, strip, replace spaces/hyphens with underscores
    normalized = predicate.lower().strip().replace(" ", "_").replace("-", "_")

    # Check if already canonical
    if normalized in CANONICAL_PREDICATES:
        return (normalized, True)

    # Check if there's a mapping
    if normalized in PREDICATE_MAPPINGS:
        mapped = PREDICATE_MAPPINGS[normalized]
        logger.info(f"[PredicateGuard] Mapped '{predicate}' -> '{mapped}'")
        return (mapped, True)

    # Non-canonical predicate
    logger.warning(f"[PredicateGuard] Non-canonical predicate: '{predicate}'")

    if strict:
        # In strict mode, fallback to generic predicate
        return ("associated_with", False)
    else:
        # In permissive mode, allow but flag as non-canonical
        return (normalized, False)


def filter_relationships(
    relationships: List[Dict[str, Any]],
    strict: bool = False
) -> List[Dict[str, Any]]:
    """
    Filter and validate relationships, applying predicate guard.

    Args:
        relationships: List of relationship dicts with 'predicate' key
        strict: If True, drop or remap non-canonical predicates

    Returns:
        Filtered list of relationships with validated predicates
    """
    if not relationships:
        return []

    validated = []
    rejected_count = 0
    mapped_count = 0

    for rel in relationships:
        if not isinstance(rel, dict):
            continue

        predicate = rel.get("predicate", "")
        original_predicate = predicate

        normalized, is_canonical = validate_predicate(predicate, strict=strict)

        if strict and not is_canonical:
            rejected_count += 1
            continue

        # Track if we mapped to a different predicate
        if normalized != original_predicate.lower().strip().replace(" ", "_").replace("-", "_"):
            mapped_count += 1

        # Update predicate in relationship
        rel_copy = rel.copy()
        rel_copy["predicate"] = normalized
        validated.append(rel_copy)

    # Log summary
    if rejected_count > 0:
        logger.info(f"[PredicateGuard] Rejected {rejected_count} non-canonical predicates (strict mode)")
    if mapped_count > 0:
        logger.info(f"[PredicateGuard] Mapped {mapped_count} predicates to canonical forms")

    return validated


def get_predicate_stats(relationships: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Get statistics about predicates in a relationship list.

    Args:
        relationships: List of relationship dicts with 'predicate' key

    Returns:
        Dict with counts: canonical, mapped, non_canonical, total
    """
    stats = {
        "canonical": 0,
        "mapped": 0,
        "non_canonical": 0,
        "total": 0,
    }

    for rel in relationships:
        if not isinstance(rel, dict):
            continue

        predicate = rel.get("predicate", "")
        normalized = predicate.lower().strip().replace(" ", "_").replace("-", "_")

        stats["total"] += 1

        if normalized in CANONICAL_PREDICATES:
            stats["canonical"] += 1
        elif normalized in PREDICATE_MAPPINGS:
            stats["mapped"] += 1
        else:
            stats["non_canonical"] += 1

    return stats


# ============================================================================
# Module Exports
# ============================================================================

__all__ = [
    "CANONICAL_PREDICATES",
    "PREDICATE_MAPPINGS",
    "validate_predicate",
    "filter_relationships",
    "get_predicate_stats",
]
