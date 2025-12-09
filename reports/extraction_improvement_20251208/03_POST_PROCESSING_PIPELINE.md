# Modular Post-Processing Pipeline Design

**Date**: 2025-12-08
**Status**: Design Specification
**Reference**: YonEarth `src/knowledge_graph/postprocessing/`

---

## Overview

This document specifies an 8-module post-processing pipeline for the Regen KOI knowledge graph extraction system. The design follows the proven YonEarth modular architecture where each module:
- Has a clear single responsibility
- Executes in priority order
- Tracks statistics
- Can be enabled/disabled independently

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                  Raw LLM Extraction Output                  │
│            List[Entity], List[Relationship]                 │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Module 1: EntityQualityFilter (Priority: 10)               │
│  - Blocks pronouns, generic nouns, sentence fragments       │
│  - CRITICAL: Addresses 3,690+ quality issues                │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Module 2: ListSplitter (Priority: 20)                      │
│  - Splits "A, B, and C" into separate entities              │
│  - POS-aware conjunction handling                           │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Module 3: PrivacyTagger (Priority: 30)                     │
│  - Tags internal/sensitive content                          │
│  - Source-based default tagging                             │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Module 4: OntologyNormalizer (Priority: 40)                │
│  - Standardizes entity types and predicates                 │
│  - Maps variants to canonical types                         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Module 5: CanonicalEntityResolver (Priority: 50)           │
│  - Maps aliases to canonical forms                          │
│  - Regex pattern matching for known entities                │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Module 6: FuzzyDeduplicator (Priority: 60)                 │
│  - Merges similar entities (85%+ threshold)                 │
│  - Type-aware matching                                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Module 7: RelationshipValidator (Priority: 70)             │
│  - Ensures edge targets exist                               │
│  - Removes self-loops and duplicates                        │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Module 8: DiscourseEnricher (Priority: 80)                 │
│  - Source-specific enrichment                               │
│  - Thread structure, quote attribution                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Cleaned Extraction Output                      │
│       Ready for RDF Integration / Graph Build               │
└─────────────────────────────────────────────────────────────┘
```

---

## Base Classes

### ProcessingContext

```python
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from datetime import datetime

@dataclass
class ProcessingContext:
    """Shared context passed to all modules."""

    # Source information
    source_type: str                    # discourse, notion, medium, etc.
    source_id: str                      # unique identifier for source
    source_url: Optional[str] = None

    # Document metadata
    document_metadata: Dict[str, Any] = field(default_factory=dict)

    # Processing configuration
    config: Dict[str, Any] = field(default_factory=dict)

    # Run tracking
    run_id: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d_%H%M%S"))
    extraction_version: Optional[str] = None

    # Accumulated state (modules can add data here)
    state: Dict[str, Any] = field(default_factory=dict)
```

### PostProcessingModule (Abstract Base)

```python
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Tuple, Optional

class PostProcessingModule(ABC):
    """Base class for all post-processing modules."""

    # Module metadata (override in subclasses)
    name: str = "BaseModule"
    description: str = "Base post-processing module"
    priority: int = 50              # Lower = earlier execution
    dependencies: List[str] = []   # Module names this depends on
    version: str = "1.0.0"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.stats = {
            "processed_count": 0,
            "modified_count": 0,
            "filtered_count": 0,
        }

    @abstractmethod
    def process_entities(
        self,
        entities: List[Dict],
        context: ProcessingContext
    ) -> List[Dict]:
        """Process entities. Return filtered/modified list."""
        pass

    def process_relationships(
        self,
        relationships: List[Dict],
        entities: List[Dict],
        context: ProcessingContext
    ) -> List[Dict]:
        """Process relationships. Default: pass through unchanged."""
        return relationships

    def get_stats(self) -> Dict[str, Any]:
        """Return processing statistics."""
        return self.stats.copy()

    def reset_stats(self) -> None:
        """Reset statistics for new run."""
        self.stats = {
            "processed_count": 0,
            "modified_count": 0,
            "filtered_count": 0,
        }
```

### PipelineOrchestrator

```python
from typing import List, Dict, Any, Tuple
import logging

logger = logging.getLogger(__name__)

class PipelineOrchestrator:
    """Orchestrates execution of post-processing modules."""

    def __init__(self, modules: List[PostProcessingModule]):
        # Sort modules by priority
        self.modules = sorted(modules, key=lambda m: m.priority)
        self._validate_dependencies()

    def _validate_dependencies(self) -> None:
        """Ensure all module dependencies are satisfied."""
        available = set()
        for module in self.modules:
            for dep in module.dependencies:
                if dep not in available:
                    raise ValueError(
                        f"Module {module.name} depends on {dep}, "
                        f"but {dep} is not available or runs later"
                    )
            available.add(module.name)

    def run(
        self,
        entities: List[Dict],
        relationships: List[Dict],
        context: ProcessingContext
    ) -> Tuple[List[Dict], List[Dict], Dict[str, Any]]:
        """
        Run all modules in priority order.

        Returns:
            (processed_entities, processed_relationships, stats)
        """
        stats = {
            "input_entities": len(entities),
            "input_relationships": len(relationships),
            "modules": {},
        }

        current_entities = entities
        current_relationships = relationships

        for module in self.modules:
            logger.info(f"Running {module.name} (priority {module.priority})...")
            module.reset_stats()

            # Process entities
            current_entities = module.process_entities(
                current_entities, context
            )

            # Process relationships
            current_relationships = module.process_relationships(
                current_relationships, current_entities, context
            )

            # Collect stats
            stats["modules"][module.name] = module.get_stats()
            logger.info(f"  {module.name}: {module.get_stats()}")

        stats["output_entities"] = len(current_entities)
        stats["output_relationships"] = len(current_relationships)

        return current_entities, current_relationships, stats
```

---

## Module 1: EntityQualityFilter

**Priority**: 10 (runs first)
**Purpose**: Block low-quality entities that are pronouns, generic nouns, sentence fragments, etc.

```python
import re
from typing import Dict, List, Set, Tuple, Optional

class EntityQualityFilter(PostProcessingModule):
    """
    Filters out low-quality entities.

    Blocks:
    - Pronouns (we, they, it, etc.)
    - Generic nouns (people, person, user, etc.)
    - Numeric-only entities (2030, 35)
    - Tautological entities (name equals type)
    - Lowercase single-word PERSON entities
    - Generic person patterns (the character, our friends)
    - Sentence-like entities (contains verbs, too long)
    """

    name = "EntityQualityFilter"
    description = "Blocks pronouns, generic nouns, and sentence fragments"
    priority = 10
    version = "1.0.0"

    # Stop words to block
    STOP_WORDS: Set[str] = {
        # Pronouns
        'we', 'she', 'he', 'they', 'it', 'i', 'you', 'us', 'them',
        # Generic collective nouns
        'people', 'person', 'individual', 'individuals',
        'everyone', 'someone', 'anyone', 'nobody', 'somebody',
        # Generic familial/social
        'mom', 'dad', 'mother', 'father', 'friend', 'friends',
        'guy', 'woman', 'man', 'kid', 'kids',
        # Generic occupational (lowercase)
        'farmer', 'teacher', 'scientist', 'activist',
        # Regen-specific generics
        'user', 'member', 'participant', 'validator', 'delegator',
        'community', 'team', 'group', 'project', 'organization',
    }

    # Generic person patterns
    GENERIC_PATTERNS = [
        re.compile(r'^(the |a |an |our |their |my |your |his |her )', re.IGNORECASE),
        re.compile(r'(friends|teachers|officials|people|generations|character|speaker)s?$', re.IGNORECASE),
        re.compile(r'^(who|which|that|those|these|some|many|few|all) ', re.IGNORECASE),
        re.compile(r'^(someone|anyone|everyone|nobody|somebody) ', re.IGNORECASE),
    ]

    # Sentence patterns
    SENTENCE_PATTERNS = [
        re.compile(r'\b(is|are|was|were|has|have|had|will|would|could|should|can|may|might)\b', re.IGNORECASE),
        re.compile(r'\b(the most|in order to|according to|in terms of|as well as)\b', re.IGNORECASE),
        re.compile(r'[.!?;]'),  # Sentence punctuation
        re.compile(r',.*,.*,'),  # Multiple commas
    ]

    MAX_NAME_LENGTH = 80
    MAX_WORD_COUNT = 8

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        # Allow custom stop words
        custom_stops = self.config.get("additional_stop_words", [])
        self.STOP_WORDS = self.STOP_WORDS.union(set(custom_stops))
        # Reason tracking
        self.stats["reasons"] = {}

    def filter_entity(self, entity: Dict) -> Tuple[bool, str]:
        """
        Check entity against all filters.

        Returns:
            (passes, rejection_reason)
        """
        name = entity.get("name", "").strip()
        entity_type = entity.get("type", "").upper()

        # 1. Stop word check
        if name.lower() in self.STOP_WORDS:
            return (False, "stop_word")

        # 2. Numeric only
        if re.match(r'^\d+$', name):
            return (False, "numeric_only")

        # 3. Tautological (name equals type)
        name_norm = name.lower().rstrip('s')
        type_norm = entity_type.lower().rstrip('s')
        if name_norm == type_norm:
            return (False, "tautological")

        # 4. Lowercase single-word PERSON
        if entity_type == "PERSON" and ' ' not in name and name[0:1].islower():
            return (False, "lowercase_person")

        # 5. Generic person patterns
        for pattern in self.GENERIC_PATTERNS:
            if pattern.search(name):
                return (False, "generic_pattern")

        # 6. Sentence-like
        for pattern in self.SENTENCE_PATTERNS:
            if pattern.search(name):
                return (False, "sentence_like")

        # 7. Length limits
        if len(name) > self.MAX_NAME_LENGTH or len(name.split()) > self.MAX_WORD_COUNT:
            return (False, "too_long")

        return (True, "")

    def process_entities(
        self,
        entities: List[Dict],
        context: ProcessingContext
    ) -> List[Dict]:
        """Filter entities, tracking statistics."""
        self.stats["processed_count"] = len(entities)
        passed = []

        for entity in entities:
            passes, reason = self.filter_entity(entity)
            if passes:
                passed.append(entity)
            else:
                self.stats["filtered_count"] += 1
                self.stats["reasons"][reason] = self.stats["reasons"].get(reason, 0) + 1

        return passed
```

---

## Module 2: ListSplitter

**Priority**: 20
**Purpose**: Split entities that are actually lists ("A, B, and C") into separate entities

```python
import re
from typing import Dict, List, Optional

class ListSplitter(PostProcessingModule):
    """
    Splits list entities into separate entities.

    Handles:
    - "A, B, and C" patterns (Oxford comma)
    - "A, B and C" patterns (no Oxford comma)
    - "A and B" patterns
    - Comma-separated lists
    """

    name = "ListSplitter"
    description = "Splits comma-separated and conjunction lists"
    priority = 20
    version = "1.0.0"

    # Compound terms to NOT split
    COMPOUND_TERMS = {
        'research and development', 'trial and error', 'supply and demand',
        'law and order', 'bread and butter', 'give and take',
        'regen network development', 'cosmos sdk',
    }

    MIN_LIST_LENGTH = 15  # Don't try to split very short strings

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        self.stats["lists_split"] = 0
        self.stats["new_entities_created"] = 0

    def is_list_entity(self, name: str) -> bool:
        """Check if entity name looks like a list."""
        if len(name) < self.MIN_LIST_LENGTH:
            return False

        # Check for compound terms
        if name.lower() in self.COMPOUND_TERMS:
            return False

        # Multiple commas = likely list
        if name.count(',') >= 2:
            return True

        # "A, B, and C" pattern
        if re.search(r'.+,\s*.+,?\s*and\s+.+', name, re.IGNORECASE):
            return True

        # "A and B" with both parts substantial
        if ' and ' in name.lower():
            parts = re.split(r'\s+and\s+', name, flags=re.IGNORECASE)
            if len(parts) == 2 and all(len(p.strip()) > 3 for p in parts):
                # Check if both parts look like entities (capitalized)
                if all(p.strip()[0].isupper() for p in parts if p.strip()):
                    return True

        return False

    def split_list(self, name: str) -> List[str]:
        """Split a list entity name into components."""
        # Split on commas and "and"
        parts = re.split(r',\s*(?:and\s+)?|\s+and\s+', name, flags=re.IGNORECASE)
        parts = [p.strip() for p in parts if p.strip()]

        # Filter out very short fragments
        parts = [p for p in parts if len(p) >= 2]

        return parts if len(parts) > 1 else [name]

    def process_entities(
        self,
        entities: List[Dict],
        context: ProcessingContext
    ) -> List[Dict]:
        """Split list entities into separate entities."""
        self.stats["processed_count"] = len(entities)
        result = []

        for entity in entities:
            name = entity.get("name", "")

            if self.is_list_entity(name):
                parts = self.split_list(name)

                if len(parts) > 1:
                    self.stats["lists_split"] += 1
                    self.stats["modified_count"] += 1

                    for i, part in enumerate(parts):
                        new_entity = entity.copy()
                        new_entity["name"] = part
                        new_entity["_split_from"] = name
                        new_entity["_split_index"] = i
                        result.append(new_entity)
                        self.stats["new_entities_created"] += 1
                else:
                    result.append(entity)
            else:
                result.append(entity)

        return result
```

---

## Module 3: PrivacyTagger

**Priority**: 30
**Purpose**: Tag entities as internal/private based on source or keywords

```python
import re
from typing import Dict, List, Set, Optional

class PrivacyTagger(PostProcessingModule):
    """
    Tags entities with privacy/sensitivity markers.

    Tags based on:
    - Source type (Notion = private by default)
    - Keywords (NDA, confidential, internal)
    - Explicit markers in metadata
    """

    name = "PrivacyTagger"
    description = "Tags internal/sensitive content"
    priority = 30
    version = "1.0.0"

    # Sources that are private by default
    PRIVATE_SOURCES: Set[str] = {"notion", "internal_doc", "private_channel"}

    # Keywords indicating sensitive content
    SENSITIVITY_KEYWORDS = [
        re.compile(r'\b(NDA|confidential|internal[\s\-]only|private)\b', re.IGNORECASE),
        re.compile(r'\b(draft|pre[\s\-]?announcement|embargoed)\b', re.IGNORECASE),
        re.compile(r'\b(salary|compensation|personal)\b', re.IGNORECASE),
    ]

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        self.stats["tagged_private"] = 0
        self.stats["tagged_sensitive"] = 0

    def process_entities(
        self,
        entities: List[Dict],
        context: ProcessingContext
    ) -> List[Dict]:
        """Tag entities with privacy markers."""
        self.stats["processed_count"] = len(entities)

        source_type = context.source_type.lower()
        is_private_source = source_type in self.PRIVATE_SOURCES

        for entity in entities:
            # Default from source
            if is_private_source:
                entity["is_private"] = True
                self.stats["tagged_private"] += 1
                self.stats["modified_count"] += 1

            # Check for sensitivity keywords in name or description
            text_to_check = f"{entity.get('name', '')} {entity.get('description', '')}"
            for pattern in self.SENSITIVITY_KEYWORDS:
                if pattern.search(text_to_check):
                    entity["is_sensitive"] = True
                    self.stats["tagged_sensitive"] += 1
                    break

        return entities
```

---

## Module 4: OntologyNormalizer

**Priority**: 40
**Purpose**: Standardize entity types and relationship predicates to canonical forms

```python
from typing import Dict, List, Optional

class OntologyNormalizer(PostProcessingModule):
    """
    Normalizes entity types and predicates to canonical ontology.

    Maps variant types to standard types:
    - COMPANY -> FORMAL_ORGANIZATION
    - LOCATION -> PLACE
    - etc.
    """

    name = "OntologyNormalizer"
    description = "Standardizes entity types and predicates"
    priority = 40
    version = "1.0.0"

    # Entity type mappings (variant -> canonical)
    TYPE_MAPPINGS: Dict[str, str] = {
        # Organization variants
        "COMPANY": "FORMAL_ORGANIZATION",
        "CORPORATION": "FORMAL_ORGANIZATION",
        "NONPROFIT": "FORMAL_ORGANIZATION",
        "DAO": "FORMAL_ORGANIZATION",
        "ORGANIZATION": "FORMAL_ORGANIZATION",
        "ORG": "FORMAL_ORGANIZATION",
        # Place variants
        "LOCATION": "PLACE",
        "REGION": "PLACE",
        "COUNTRY": "PLACE",
        "CITY": "PLACE",
        "ECOSYSTEM": "PLACE",
        # Person variants
        "INDIVIDUAL": "PERSON",
        "HUMAN": "PERSON",
        "AUTHOR": "PERSON",
        # Concept variants
        "IDEA": "CONCEPT",
        "PRACTICE": "CONCEPT",
        "TECHNOLOGY": "CONCEPT",
        "METHODOLOGY": "CONCEPT",
        # Product variants
        "SOFTWARE": "PRODUCT",
        "TOOL": "PRODUCT",
        "CREDIT": "PRODUCT",
    }

    # Predicate mappings
    PREDICATE_MAPPINGS: Dict[str, str] = {
        "is_a": "is",
        "is a": "is",
        "related": "relates_to",
        "related to": "relates_to",
        "works at": "works_for",
        "works_at": "works_for",
        "employed_by": "works_for",
        "discusses": "focuses_on",
        "talks_about": "focuses_on",
        "located at": "located_in",
        "located_at": "located_in",
        "based_in": "located_in",
    }

    # Allowed types after normalization
    ALLOWED_TYPES: set = {
        "PERSON", "FORMAL_ORGANIZATION", "COMMUNITY", "PROJECT",
        "CONCEPT", "PLACE", "EVENT", "PRODUCT", "RESOURCE",
        "PROPOSAL", "METRIC", "MODULE", "API_ENDPOINT",
    }

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        self.stats["types_normalized"] = 0
        self.stats["predicates_normalized"] = 0
        self.stats["unknown_types"] = {}

    def normalize_type(self, entity_type: str) -> str:
        """Normalize entity type to canonical form."""
        upper_type = entity_type.upper().strip()

        # Check mapping
        if upper_type in self.TYPE_MAPPINGS:
            return self.TYPE_MAPPINGS[upper_type]

        # Already canonical
        if upper_type in self.ALLOWED_TYPES:
            return upper_type

        # Unknown type - track and return as-is
        self.stats["unknown_types"][upper_type] = \
            self.stats["unknown_types"].get(upper_type, 0) + 1
        return upper_type

    def normalize_predicate(self, predicate: str) -> str:
        """Normalize relationship predicate to canonical form."""
        lower_pred = predicate.lower().strip()

        if lower_pred in self.PREDICATE_MAPPINGS:
            return self.PREDICATE_MAPPINGS[lower_pred]

        return lower_pred

    def process_entities(
        self,
        entities: List[Dict],
        context: ProcessingContext
    ) -> List[Dict]:
        """Normalize entity types."""
        self.stats["processed_count"] = len(entities)

        for entity in entities:
            original_type = entity.get("type", "UNKNOWN")
            normalized = self.normalize_type(original_type)

            if normalized != original_type:
                entity["type"] = normalized
                entity["_original_type"] = original_type
                self.stats["types_normalized"] += 1
                self.stats["modified_count"] += 1

        return entities

    def process_relationships(
        self,
        relationships: List[Dict],
        entities: List[Dict],
        context: ProcessingContext
    ) -> List[Dict]:
        """Normalize relationship predicates."""
        for rel in relationships:
            original_pred = rel.get("predicate", "")
            normalized = self.normalize_predicate(original_pred)

            if normalized != original_pred:
                rel["predicate"] = normalized
                rel["_original_predicate"] = original_pred
                self.stats["predicates_normalized"] += 1

        return relationships
```

---

## Module 5: CanonicalEntityResolver

**Priority**: 50
**Purpose**: Map known aliases to canonical entity names

```python
import re
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

class CanonicalEntityResolver(PostProcessingModule):
    """
    Resolves entity names to canonical forms using alias registry.

    Uses:
    - Exact alias matching
    - Regex pattern matching
    - Type-aware resolution
    """

    name = "CanonicalEntityResolver"
    description = "Maps aliases to canonical entity names"
    priority = 50
    version = "1.0.0"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)

        # Load canonical registry
        registry_path = self.config.get(
            "registry_path",
            "data/canonical_entities.json"
        )
        self.registry = self._load_registry(registry_path)
        self.alias_index = self._build_alias_index()

        self.stats["exact_matches"] = 0
        self.stats["pattern_matches"] = 0

    def _load_registry(self, path: str) -> Dict:
        """Load canonical entity registry."""
        registry_path = Path(path)
        if registry_path.exists():
            with open(registry_path) as f:
                return json.load(f)
        return {"organizations": {}, "people": {}, "products": {}}

    def _build_alias_index(self) -> Dict[str, str]:
        """Build lowercase alias -> canonical name index."""
        index = {}
        for category in self.registry.values():
            for entity_id, data in category.items():
                canonical = data.get("canonical_name", "")
                for alias in data.get("aliases", []):
                    index[alias.lower()] = canonical
                # Also index canonical name itself
                index[canonical.lower()] = canonical
        return index

    def resolve(self, name: str, entity_type: str) -> Tuple[str, float, str]:
        """
        Resolve entity name to canonical form.

        Returns:
            (resolved_name, confidence, method)
        """
        name_lower = name.lower().strip()

        # 1. Exact alias match
        if name_lower in self.alias_index:
            return (self.alias_index[name_lower], 1.0, "exact")

        # 2. Pattern matching (for complex aliases)
        for category in self.registry.values():
            for entity_id, data in category.items():
                patterns = data.get("merge_patterns", [])
                for pattern in patterns:
                    if re.match(pattern, name, re.IGNORECASE):
                        return (data["canonical_name"], 0.95, "pattern")

        # 3. No match
        return (name, 0.0, "unresolved")

    def process_entities(
        self,
        entities: List[Dict],
        context: ProcessingContext
    ) -> List[Dict]:
        """Resolve entities to canonical forms."""
        self.stats["processed_count"] = len(entities)

        for entity in entities:
            name = entity.get("name", "")
            entity_type = entity.get("type", "")

            resolved, confidence, method = self.resolve(name, entity_type)

            if method != "unresolved":
                # Store original as alias
                if resolved != name:
                    aliases = entity.get("aliases", [])
                    if name not in aliases:
                        aliases.append(name)
                    entity["aliases"] = aliases
                    entity["name"] = resolved
                    entity["_resolution_confidence"] = confidence
                    entity["_resolution_method"] = method
                    self.stats["modified_count"] += 1

                    if method == "exact":
                        self.stats["exact_matches"] += 1
                    elif method == "pattern":
                        self.stats["pattern_matches"] += 1

        return entities
```

---

## Module 6: FuzzyDeduplicator

**Priority**: 60
**Purpose**: Merge similar entities using fuzzy string matching

```python
from typing import Dict, List, Optional, Set
from collections import defaultdict

class FuzzyDeduplicator(PostProcessingModule):
    """
    Deduplicates entities using fuzzy string matching.

    Features:
    - Type-aware matching (only merge same types)
    - 85%+ similarity threshold
    - Merge descriptions, aliases, sources
    """

    name = "FuzzyDeduplicator"
    description = "Merges similar entities (85%+ threshold)"
    priority = 60
    version = "1.0.0"
    dependencies = ["OntologyNormalizer"]  # Need normalized types first

    SIMILARITY_THRESHOLD = 0.85

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        self.threshold = self.config.get("threshold", self.SIMILARITY_THRESHOLD)
        self.stats["duplicates_merged"] = 0
        self.stats["merge_groups"] = []

    def _normalize_for_comparison(self, name: str) -> str:
        """Normalize name for comparison."""
        return name.lower().strip()

    def _similarity(self, s1: str, s2: str) -> float:
        """Calculate string similarity (Jaccard on character bigrams)."""
        if not s1 or not s2:
            return 0.0

        s1_norm = self._normalize_for_comparison(s1)
        s2_norm = self._normalize_for_comparison(s2)

        if s1_norm == s2_norm:
            return 1.0

        # Character bigrams
        def bigrams(s):
            return set(s[i:i+2] for i in range(len(s)-1))

        b1 = bigrams(s1_norm)
        b2 = bigrams(s2_norm)

        if not b1 or not b2:
            return 0.0

        intersection = len(b1 & b2)
        union = len(b1 | b2)

        return intersection / union if union > 0 else 0.0

    def _merge_entities(self, entities: List[Dict]) -> Dict:
        """Merge multiple entities into one."""
        if len(entities) == 1:
            return entities[0]

        # Use entity with longest description as base
        base = max(entities, key=lambda e: len(e.get("description", "")))
        merged = base.copy()

        # Collect all aliases
        all_aliases: Set[str] = set()
        for entity in entities:
            all_aliases.update(entity.get("aliases", []))
            if entity.get("name") != merged.get("name"):
                all_aliases.add(entity["name"])

        merged["aliases"] = list(all_aliases)

        # Collect all sources
        all_sources: Set[str] = set()
        for entity in entities:
            sources = entity.get("sources", [])
            if isinstance(sources, list):
                all_sources.update(sources)
            elif isinstance(sources, str):
                all_sources.add(sources)

        merged["sources"] = list(all_sources)
        merged["mention_count"] = len(entities)

        return merged

    def process_entities(
        self,
        entities: List[Dict],
        context: ProcessingContext
    ) -> List[Dict]:
        """Deduplicate entities using fuzzy matching."""
        self.stats["processed_count"] = len(entities)

        # Group by type first
        by_type: Dict[str, List[Dict]] = defaultdict(list)
        for entity in entities:
            entity_type = entity.get("type", "UNKNOWN")
            by_type[entity_type].append(entity)

        result = []

        for entity_type, type_entities in by_type.items():
            # Find merge groups within type
            merged_indices: Set[int] = set()
            merge_groups: List[List[Dict]] = []

            for i, entity1 in enumerate(type_entities):
                if i in merged_indices:
                    continue

                group = [entity1]
                merged_indices.add(i)

                for j, entity2 in enumerate(type_entities[i+1:], i+1):
                    if j in merged_indices:
                        continue

                    sim = self._similarity(entity1.get("name", ""), entity2.get("name", ""))
                    if sim >= self.threshold:
                        group.append(entity2)
                        merged_indices.add(j)

                merge_groups.append(group)

            # Merge each group
            for group in merge_groups:
                merged = self._merge_entities(group)
                result.append(merged)

                if len(group) > 1:
                    self.stats["duplicates_merged"] += len(group) - 1
                    self.stats["modified_count"] += 1
                    self.stats["merge_groups"].append({
                        "canonical": merged.get("name"),
                        "merged_count": len(group),
                    })

        return result
```

---

## Module 7: RelationshipValidator

**Priority**: 70
**Purpose**: Ensure relationship integrity (targets exist, no self-loops)

```python
from typing import Dict, List, Set, Optional

class RelationshipValidator(PostProcessingModule):
    """
    Validates relationships for graph integrity.

    Checks:
    - Target entities exist
    - No self-loops
    - No duplicate relationships
    """

    name = "RelationshipValidator"
    description = "Validates relationship integrity"
    priority = 70
    version = "1.0.0"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        self.stats["orphaned_removed"] = 0
        self.stats["self_loops_removed"] = 0
        self.stats["duplicates_removed"] = 0

    def process_relationships(
        self,
        relationships: List[Dict],
        entities: List[Dict],
        context: ProcessingContext
    ) -> List[Dict]:
        """Validate and clean relationships."""
        # Build entity name index
        entity_names: Set[str] = set()
        for entity in entities:
            entity_names.add(entity.get("name", "").lower())
            for alias in entity.get("aliases", []):
                entity_names.add(alias.lower())

        seen: Set[tuple] = set()
        valid = []

        for rel in relationships:
            subject = rel.get("subject", "").lower()
            obj = rel.get("object", "").lower()
            predicate = rel.get("predicate", "")

            # Check for self-loops
            if subject == obj:
                self.stats["self_loops_removed"] += 1
                continue

            # Check targets exist
            if subject not in entity_names or obj not in entity_names:
                self.stats["orphaned_removed"] += 1
                continue

            # Check for duplicates
            rel_key = (subject, predicate, obj)
            if rel_key in seen:
                self.stats["duplicates_removed"] += 1
                continue

            seen.add(rel_key)
            valid.append(rel)

        self.stats["filtered_count"] = len(relationships) - len(valid)
        return valid
```

---

## Module 8: DiscourseEnricher

**Priority**: 80
**Purpose**: Source-specific enrichment (thread structure, quote attribution)

```python
import re
from typing import Dict, List, Optional

class DiscourseEnricher(PostProcessingModule):
    """
    Enriches entities with source-specific metadata.

    For Discourse:
    - Thread structure
    - Quote attribution
    - Category context

    For other sources:
    - Author attribution
    - Section context
    """

    name = "DiscourseEnricher"
    description = "Source-specific enrichment"
    priority = 80
    version = "1.0.0"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        self.stats["enriched"] = 0

    def process_entities(
        self,
        entities: List[Dict],
        context: ProcessingContext
    ) -> List[Dict]:
        """Enrich entities based on source type."""
        self.stats["processed_count"] = len(entities)

        source_type = context.source_type.lower()

        if source_type == "discourse":
            entities = self._enrich_discourse(entities, context)
        elif source_type == "medium":
            entities = self._enrich_medium(entities, context)
        elif source_type == "notion":
            entities = self._enrich_notion(entities, context)

        return entities

    def _enrich_discourse(
        self,
        entities: List[Dict],
        context: ProcessingContext
    ) -> List[Dict]:
        """Add Discourse-specific metadata."""
        metadata = context.document_metadata

        thread_id = metadata.get("thread_id")
        category = metadata.get("category")

        for entity in entities:
            if thread_id:
                entity["_discourse_thread"] = thread_id
            if category:
                entity["_discourse_category"] = category

            # Mark forum participants
            if entity.get("type") == "PERSON":
                if "@" in entity.get("name", ""):
                    entity["_is_forum_user"] = True

            self.stats["enriched"] += 1
            self.stats["modified_count"] += 1

        return entities

    def _enrich_medium(
        self,
        entities: List[Dict],
        context: ProcessingContext
    ) -> List[Dict]:
        """Add Medium-specific metadata."""
        metadata = context.document_metadata

        author = metadata.get("author")
        publish_date = metadata.get("publish_date")
        article_url = context.source_url

        for entity in entities:
            if author:
                entity["_article_author"] = author
            if publish_date:
                entity["_publish_date"] = publish_date
            if article_url:
                entity["_source_url"] = article_url

            self.stats["enriched"] += 1
            self.stats["modified_count"] += 1

        return entities

    def _enrich_notion(
        self,
        entities: List[Dict],
        context: ProcessingContext
    ) -> List[Dict]:
        """Add Notion-specific metadata."""
        metadata = context.document_metadata

        page_id = metadata.get("page_id")
        parent_page = metadata.get("parent_page")

        for entity in entities:
            if page_id:
                entity["_notion_page_id"] = page_id
            if parent_page:
                entity["_notion_parent"] = parent_page

            self.stats["enriched"] += 1
            self.stats["modified_count"] += 1

        return entities
```

---

## Pipeline Configuration

### Default Pipeline

```python
def get_default_pipeline(config: Optional[Dict] = None) -> PipelineOrchestrator:
    """Get default post-processing pipeline with all modules."""
    config = config or {}

    modules = [
        EntityQualityFilter(config.get("quality_filter", {})),
        ListSplitter(config.get("list_splitter", {})),
        PrivacyTagger(config.get("privacy_tagger", {})),
        OntologyNormalizer(config.get("ontology", {})),
        CanonicalEntityResolver(config.get("resolver", {})),
        FuzzyDeduplicator(config.get("deduplicator", {})),
        RelationshipValidator(config.get("validator", {})),
        DiscourseEnricher(config.get("enricher", {})),
    ]

    return PipelineOrchestrator(modules)
```

### Custom Pipeline Example

```python
# Minimal pipeline for quick processing
def get_quick_pipeline() -> PipelineOrchestrator:
    """Get minimal pipeline (quality + dedup only)."""
    return PipelineOrchestrator([
        EntityQualityFilter(),
        OntologyNormalizer(),
        FuzzyDeduplicator(),
    ])

# Privacy-focused pipeline for internal docs
def get_private_pipeline() -> PipelineOrchestrator:
    """Get pipeline optimized for internal/private content."""
    return PipelineOrchestrator([
        EntityQualityFilter(),
        PrivacyTagger({"strict_mode": True}),
        OntologyNormalizer(),
        CanonicalEntityResolver(),
    ])
```

---

## Usage Example

```python
from postprocessing import get_default_pipeline, ProcessingContext

# Create context
context = ProcessingContext(
    source_type="discourse",
    source_id="forum.regen.network",
    document_metadata={
        "thread_id": "123",
        "category": "Governance",
    }
)

# Raw extraction output
entities = [
    {"name": "we", "type": "PERSON"},  # Will be filtered
    {"name": "Gregory Landua", "type": "PERSON"},
    {"name": "Regen Network, Toucan Protocol, and Verra", "type": "ORGANIZATION"},  # Will be split
    {"name": "regen.network", "type": "ORGANIZATION"},  # Will be resolved
]

relationships = [
    {"subject": "Gregory Landua", "predicate": "works at", "object": "Regen Network"},
]

# Run pipeline
pipeline = get_default_pipeline()
clean_entities, clean_relationships, stats = pipeline.run(
    entities, relationships, context
)

print(f"Input: {stats['input_entities']} entities")
print(f"Output: {stats['output_entities']} entities")
print(f"Module stats: {stats['modules']}")
```

---

## Next Steps

1. **Implement POC**: Start with EntityQualityFilter module
2. **Create canonical registry**: `data/canonical_entities.json` for Regen
3. **Test on real data**: Run pipeline on sample Regen extraction
4. **Integrate with graph_integration.py**: Call pipeline before RDF insertion
5. **Add metrics dashboard**: Track quality improvements over time
