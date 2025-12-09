"""
Entity Quality Filter Module for Regen KOI

Filters out low-quality entities extracted by LLMs, including:
- Pronouns (we, they, it)
- Generic nouns (people, user, member)
- Numeric-only entities (2030, 35)
- Tautological entities (name equals type)
- Lowercase single-word PERSON entities
- Generic person patterns (the character, our friends)
- Sentence-like entities (contains verbs, too long)

This module is adapted from the YonEarth knowledge graph extraction system
with Regen-specific additions for forum and community content.

Usage:
    from src.knowledge_graph.improvements import EntityQualityFilter

    filter = EntityQualityFilter()

    # Single entity
    passes, reason = filter.filter_entity({"name": "we", "type": "PERSON"})
    # passes = False, reason = "stop_word"

    # Batch filtering
    entities = [{"name": "Gregory Landua", "type": "PERSON"}, {"name": "they", "type": "PERSON"}]
    clean_entities = filter.filter_batch(entities)
    # clean_entities = [{"name": "Gregory Landua", "type": "PERSON"}]

    # Get statistics
    stats = filter.get_stats()
    print(f"Filtered: {stats['total_filtered']} / {stats['total_checked']}")

Author: Claude Code (adapted from YonEarth)
Date: 2025-12-08
Version: 1.0.0
"""

import re
from typing import Dict, List, Set, Tuple, Optional
from dataclasses import dataclass, field


@dataclass
class FilterConfig:
    """Configuration for entity quality filter."""

    # Additional stop words to block (merged with defaults)
    additional_stop_words: Set[str] = field(default_factory=set)

    # Words to whitelist (never block, even if they match patterns)
    whitelist: Set[str] = field(default_factory=set)

    # Maximum entity name length (characters)
    max_name_length: int = 80

    # Maximum word count in entity name
    max_word_count: int = 8

    # Enable verbose logging
    verbose: bool = False


class EntityQualityFilter:
    """
    Filters low-quality entities from knowledge graph extraction results.

    Implements seven filter checks:
    1. Stop-word entities (pronouns, generic nouns)
    2. Numeric-only entities
    3. Tautological entities (name equals type)
    4. Lowercase single-word PERSON entities
    5. Generic person patterns
    6. Sentence-like entities
    7. Length limit violations

    Attributes:
        config: FilterConfig instance with customization options
        stats: Dictionary tracking filter statistics
    """

    # Default stop words - pronouns and generic nouns
    DEFAULT_STOP_WORDS: Set[str] = {
        # Personal pronouns
        'i', 'me', 'my', 'mine', 'myself',
        'you', 'your', 'yours', 'yourself', 'yourselves',
        'he', 'him', 'his', 'himself',
        'she', 'her', 'hers', 'herself',
        'it', 'its', 'itself',
        'we', 'us', 'our', 'ours', 'ourselves',
        'they', 'them', 'their', 'theirs', 'themselves',

        # Generic collective nouns
        'people', 'person', 'individual', 'individuals',
        'everyone', 'someone', 'anyone', 'nobody', 'somebody',
        'everybody', 'anybody', 'noone', 'no one',

        # Generic familial/social references
        'mom', 'dad', 'mother', 'father', 'parent', 'parents',
        'friend', 'friends', 'family', 'families',
        'guy', 'guys', 'girl', 'girls', 'woman', 'women', 'man', 'men',
        'kid', 'kids', 'child', 'children',

        # Generic occupational (lowercase singular)
        'farmer', 'farmers', 'teacher', 'teachers',
        'scientist', 'scientists', 'activist', 'activists',
        'developer', 'developers', 'engineer', 'engineers',

        # Regen/forum-specific generics
        'user', 'users', 'member', 'members',
        'participant', 'participants', 'attendee', 'attendees',
        'validator', 'validators', 'delegator', 'delegators',
        'contributor', 'contributors', 'stakeholder', 'stakeholders',

        # Very generic organizational terms (lowercase)
        'team', 'teams', 'group', 'groups',
        'company', 'companies', 'organization', 'organizations',
        'project', 'projects', 'initiative', 'initiatives',
        'community', 'communities',

        # Other low-value entities
        'thing', 'things', 'stuff', 'something', 'anything', 'nothing',
        'way', 'ways', 'kind', 'kinds', 'type', 'types',
        'example', 'examples', 'case', 'cases',
    }

    # Patterns for generic person descriptions
    GENERIC_PERSON_PATTERNS: List[re.Pattern] = [
        # Determiner start
        re.compile(r'^(the |a |an |our |their |my |your |his |her |its )', re.IGNORECASE),
        # Generic group noun endings
        re.compile(r'(friends|teachers|officials|people|generations|characters?|speakers?|participants?|members?|users?)s?$', re.IGNORECASE),
        # Relative/demonstrative pronouns
        re.compile(r'^(who|which|that|those|these|some|many|few|all|most|several) ', re.IGNORECASE),
        # Indefinite references
        re.compile(r'^(someone|anyone|everyone|nobody|somebody|anybody|everybody) ', re.IGNORECASE),
        # Generic descriptive phrases
        re.compile(r'^(other|various|different|certain|specific) ', re.IGNORECASE),
    ]

    # Patterns indicating sentence-like structures
    SENTENCE_PATTERNS: List[re.Pattern] = [
        # Common verbs indicating sentence structure
        re.compile(r'\b(is|are|was|were|has|have|had|will|would|could|should|can|may|might|must)\b', re.IGNORECASE),
        # Phrase structures
        re.compile(r'\b(the most|in order to|according to|in terms of|as well as|such as|rather than)\b', re.IGNORECASE),
        # Sentence punctuation (but not after common abbreviations)
        # Match period/!/? only if NOT preceded by common abbreviations
        re.compile(r'(?<![A-Z]r)(?<!Inc)(?<!Corp)(?<!Ltd)(?<!Jr)(?<!Sr)(?<!Mr)(?<!Ms)(?<!Mrs)(?<!Ph\.D)(?<!M\.D)[.!?;](?!\s*[A-Z])'),
        # Multiple commas (likely a list, not an entity)
        re.compile(r',.*,.*,'),
        # Question patterns
        re.compile(r'^(how|what|why|when|where|who|which)\b', re.IGNORECASE),
    ]

    # Technical patterns that are not valid entities
    TECHNICAL_PATTERNS: List[re.Pattern] = [
        # URLs and URL-like strings
        re.compile(r'^https?://'),
        re.compile(r'^www\.'),
        re.compile(r'\.(com|org|net|io|network|app)$', re.IGNORECASE),  # Domain endings
        # IP addresses and ports
        re.compile(r'^localhost'),
        re.compile(r':\d{2,5}$'),  # Port numbers
        re.compile(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}'),  # IP addresses
        # Blockchain addresses (Cosmos-style regen1...)
        re.compile(r'^regen1[a-z0-9]{38,}$'),
        re.compile(r'^cosmos1[a-z0-9]{38,}$'),
        re.compile(r'^0x[a-fA-F0-9]{40,}$'),  # Ethereum addresses
        # File paths
        re.compile(r'^[/\\]'),
        re.compile(r'\.(md|js|ts|py|go|json|yaml|yml|toml|txt|csv)$', re.IGNORECASE),  # Code file extensions
        # Code patterns
        re.compile(r'^x/[a-z]+$'),  # Cosmos module paths like x/marketplace
        re.compile(r'^[a-z]+\.[a-z]+\.[a-z\-]+', re.IGNORECASE),  # Package paths
        re.compile(r'^@[a-z]+/'),  # npm-style package refs
        # Technical identifiers
        re.compile(r'^[A-Z_]{10,}$'),  # All caps with underscores (constants)
        re.compile(r'_ALGORITHM_|_UNSPECIFIED|_NONE|_DEFAULT'),  # Enum-like patterns
        re.compile(r'^[a-z]+_[a-z_]+$'),  # snake_case identifiers
        # Note: Removed CamelCase pattern - too many false positives with valid function names like MsgCreateBatch
        # Technical single words (command line tools, protocols)
        re.compile(r'^(grpcurl|protoc|curl|wget|npm|yarn|docker|kubectl|grpc|api|sdk|cli|gui|ui|ux)$', re.IGNORECASE),
        # Hash-like strings
        re.compile(r'^[a-f0-9]{32,}$', re.IGNORECASE),  # MD5/SHA hashes
    ]

    def __init__(self, config: Optional[FilterConfig] = None):
        """
        Initialize the entity quality filter.

        Args:
            config: Optional FilterConfig for customization.
                    If None, uses default configuration.
        """
        self.config = config or FilterConfig()

        # Build effective stop word set
        self._stop_words = self.DEFAULT_STOP_WORDS.union(self.config.additional_stop_words)

        # Initialize statistics
        self._stats = {
            'total_checked': 0,
            'total_filtered': 0,
            'total_passed': 0,
            'reasons': {
                'stop_word': 0,
                'numeric_only': 0,
                'tautological': 0,
                'lowercase_person': 0,
                'generic_pattern': 0,
                'sentence_like': 0,
                'too_long': 0,
                'technical_pattern': 0,
            }
        }

    def is_stop_word(self, name: str) -> bool:
        """
        Check if entity name is a stop word.

        Args:
            name: Entity name to check

        Returns:
            True if name is a stop word (should be blocked)
        """
        normalized = name.strip().lower()
        return normalized in self._stop_words

    def is_numeric_only(self, name: str) -> bool:
        """
        Check if entity is purely numeric.

        Blocks: "2030", "35", "1956"
        Allows: "Episode 120", "Project 2025", "CO2"

        Args:
            name: Entity name to check

        Returns:
            True if name is purely numeric (should be blocked)
        """
        normalized = name.strip()
        return bool(re.match(r'^\d+$', normalized))

    def is_tautological(self, name: str, entity_type: str) -> bool:
        """
        Check if entity name equals its type (tautological).

        Blocks: ("organization", "ORGANIZATION"), ("places", "PLACE")
        Allows: ("Regen Network", "ORGANIZATION")

        Args:
            name: Entity name to check
            entity_type: Entity type

        Returns:
            True if name is tautological (should be blocked)
        """
        if not entity_type:
            return False

        # Normalize: lowercase, remove trailing 's', remove underscores
        name_norm = name.strip().lower().rstrip('s').replace('_', ' ')
        type_norm = entity_type.strip().lower().rstrip('s').replace('_', ' ')

        return name_norm == type_norm

    def is_lowercase_person(self, name: str, entity_type: str) -> bool:
        """
        Check if this is a generic lowercase single-word PERSON.

        Blocks: ("mom", "PERSON"), ("friend", "PERSON")
        Allows: ("Aaron", "PERSON"), ("John Smith", "PERSON")

        Args:
            name: Entity name to check
            entity_type: Entity type

        Returns:
            True if invalid lowercase person (should be blocked)
        """
        if not entity_type or entity_type.upper() != 'PERSON':
            return False

        stripped = name.strip()

        # Must be single word
        if ' ' in stripped:
            return False

        # Must start with lowercase
        if not stripped or not stripped[0].islower():
            return False

        return True

    def matches_generic_pattern(self, name: str) -> bool:
        """
        Check if name matches generic person description patterns.

        Blocks: "the character", "our friends", "some people"
        Allows: "Dr. Jane Goodall", "Gregory Landua"

        Args:
            name: Entity name to check

        Returns:
            True if matches generic pattern (should be blocked)
        """
        stripped = name.strip()

        for pattern in self.GENERIC_PERSON_PATTERNS:
            if pattern.search(stripped):
                return True

        return False

    def is_sentence_like(self, name: str) -> bool:
        """
        Check if name looks like a sentence fragment.

        Blocks: "the most important thing is", "according to research"
        Allows: "Regenerative Agriculture", "Carbon Credit"

        Args:
            name: Entity name to check

        Returns:
            True if sentence-like (should be blocked)
        """
        stripped = name.strip()

        for pattern in self.SENTENCE_PATTERNS:
            if pattern.search(stripped):
                return True

        return False

    def exceeds_length_limits(self, name: str) -> bool:
        """
        Check if name exceeds length limits.

        Args:
            name: Entity name to check

        Returns:
            True if too long (should be blocked)
        """
        stripped = name.strip()

        # Check character length
        if len(stripped) > self.config.max_name_length:
            return True

        # Check word count
        word_count = len(stripped.split())
        if word_count > self.config.max_word_count:
            return True

        return False

    def is_technical_pattern(self, name: str) -> bool:
        """
        Check if name matches technical patterns that shouldn't be entities.

        Blocks: URLs, IP addresses, blockchain addresses, file paths, code identifiers
        Allows: Proper entity names

        Args:
            name: Entity name to check

        Returns:
            True if matches technical pattern (should be blocked)
        """
        stripped = name.strip()

        for pattern in self.TECHNICAL_PATTERNS:
            if pattern.search(stripped):
                return True

        return False

    def filter_with_reasons(self, name: str, entity_type: str = '') -> Tuple[bool, List[str]]:
        """
        Apply all filters to an entity and return all reasons.

        Args:
            name: Entity name to check
            entity_type: Entity type (optional)

        Returns:
            Tuple of (is_valid, list_of_rejection_reasons)
            - (True, []) if entity passes all filters
            - (False, ["reason1", "reason2", ...]) if entity should be filtered
        """
        reasons = []

        # 1. Stop word check
        if self.is_stop_word(name):
            reasons.append("stop_word")

        # 2. Numeric only check
        if self.is_numeric_only(name):
            reasons.append("numeric_only")

        # 3. Tautological check
        if self.is_tautological(name, entity_type):
            reasons.append("tautological")

        # 4. Lowercase single-word PERSON check
        if self.is_lowercase_person(name, entity_type):
            reasons.append("lowercase_person")

        # 5. Generic pattern check
        if self.matches_generic_pattern(name):
            reasons.append("generic_pattern")

        # 6. Sentence-like check
        if self.is_sentence_like(name):
            reasons.append("sentence_like")

        # 7. Length limits check
        if self.exceeds_length_limits(name):
            reasons.append("too_long")

        # 8. Technical pattern check
        if self.is_technical_pattern(name):
            reasons.append("technical_pattern")

        is_valid = len(reasons) == 0
        return is_valid, reasons

    def filter_entity(self, entity: Dict) -> Tuple[bool, str]:
        """
        Apply all filters to a single entity.

        Args:
            entity: Dictionary with at least 'name' key, optionally 'type'

        Returns:
            Tuple of (passes_filter, rejection_reason)
            - (True, "") if entity passes all filters
            - (False, "reason") if entity should be filtered out
        """
        name = entity.get('name', '')
        entity_type = entity.get('type', '')

        # Check whitelist first
        if name.strip().lower() in self.config.whitelist:
            return (True, "")

        # 1. Stop word check
        if self.is_stop_word(name):
            return (False, "stop_word")

        # 2. Numeric only check
        if self.is_numeric_only(name):
            return (False, "numeric_only")

        # 3. Tautological check
        if self.is_tautological(name, entity_type):
            return (False, "tautological")

        # 4. Lowercase single-word PERSON check
        if self.is_lowercase_person(name, entity_type):
            return (False, "lowercase_person")

        # 5. Generic pattern check
        if self.matches_generic_pattern(name):
            return (False, "generic_pattern")

        # 6. Sentence-like check
        if self.is_sentence_like(name):
            return (False, "sentence_like")

        # 7. Length limits check
        if self.exceeds_length_limits(name):
            return (False, "too_long")

        # 8. Technical pattern check
        if self.is_technical_pattern(name):
            return (False, "technical_pattern")

        return (True, "")

    def filter_batch(self, entities: List[Dict]) -> List[Dict]:
        """
        Filter a batch of entities, returning only those that pass.

        Updates internal statistics.

        Args:
            entities: List of entity dictionaries

        Returns:
            List of entities that pass all filters
        """
        passed = []

        for entity in entities:
            self._stats['total_checked'] += 1

            passes, reason = self.filter_entity(entity)

            if passes:
                self._stats['total_passed'] += 1
                passed.append(entity)
            else:
                self._stats['total_filtered'] += 1
                if reason in self._stats['reasons']:
                    self._stats['reasons'][reason] += 1

        return passed

    def get_stats(self) -> Dict:
        """
        Get filtering statistics.

        Returns:
            Dictionary with:
            - total_checked: Total entities processed
            - total_filtered: Entities filtered out
            - total_passed: Entities that passed
            - reasons: Breakdown by rejection reason
        """
        return self._stats.copy()

    def reset_stats(self) -> None:
        """Reset all statistics counters."""
        self._stats = {
            'total_checked': 0,
            'total_filtered': 0,
            'total_passed': 0,
            'reasons': {
                'stop_word': 0,
                'numeric_only': 0,
                'tautological': 0,
                'lowercase_person': 0,
                'generic_pattern': 0,
                'sentence_like': 0,
                'too_long': 0,
                'technical_pattern': 0,
            }
        }

    def get_filtered_with_reasons(self, entities: List[Dict]) -> Tuple[List[Dict], List[Tuple[Dict, str]]]:
        """
        Filter entities and return both passed and filtered with reasons.

        Useful for debugging and analysis.

        Args:
            entities: List of entity dictionaries

        Returns:
            Tuple of (passed_entities, filtered_entities_with_reasons)
            where filtered_entities_with_reasons is List[(entity, reason)]
        """
        passed = []
        filtered = []

        for entity in entities:
            passes, reason = self.filter_entity(entity)

            if passes:
                passed.append(entity)
            else:
                filtered.append((entity, reason))

        return passed, filtered

    def generate_report(self, entities: List[Dict]) -> str:
        """
        Generate a human-readable report on entity quality.

        Args:
            entities: List of entity dictionaries to analyze

        Returns:
            Formatted string report
        """
        self.reset_stats()
        passed, filtered = self.get_filtered_with_reasons(entities)

        # Update stats
        self._stats['total_checked'] = len(entities)
        self._stats['total_passed'] = len(passed)
        self._stats['total_filtered'] = len(filtered)

        for _, reason in filtered:
            if reason in self._stats['reasons']:
                self._stats['reasons'][reason] += 1

        # Build report
        lines = [
            "=" * 60,
            "ENTITY QUALITY FILTER REPORT",
            "=" * 60,
            "",
            f"Total entities analyzed: {len(entities)}",
            f"Passed filters: {len(passed)} ({100*len(passed)/len(entities):.1f}%)" if entities else "Passed: 0",
            f"Filtered out: {len(filtered)} ({100*len(filtered)/len(entities):.1f}%)" if entities else "Filtered: 0",
            "",
            "Rejection reasons breakdown:",
        ]

        for reason, count in sorted(self._stats['reasons'].items(), key=lambda x: -x[1]):
            if count > 0:
                pct = 100 * count / len(entities) if entities else 0
                lines.append(f"  - {reason}: {count} ({pct:.1f}%)")

        if filtered:
            lines.extend([
                "",
                "Sample filtered entities (first 10):",
            ])
            for entity, reason in filtered[:10]:
                name = entity.get('name', 'N/A')[:40]
                etype = entity.get('type', 'N/A')
                lines.append(f"  - '{name}' ({etype}) -> {reason}")

        lines.append("")
        lines.append("=" * 60)

        return "\n".join(lines)


# Convenience function for quick filtering
def filter_entities(entities: List[Dict], config: Optional[FilterConfig] = None) -> List[Dict]:
    """
    Quick utility to filter a list of entities.

    Args:
        entities: List of entity dictionaries
        config: Optional FilterConfig

    Returns:
        List of entities that pass all quality filters
    """
    filter_instance = EntityQualityFilter(config)
    return filter_instance.filter_batch(entities)
