# Additional Specifications
## Chunking Strategy, Deduplication, and Unified Graph Schema

**Date**: 2025-12-08
**Status**: Design Specifications
**Priority**: Medium (Nice-to-have for Phase 1)

---

## Part 1: Chunking Strategy

### Overview

Parent-child chunking improves extraction quality by providing LLMs with sufficient context (parent chunks) while enabling efficient vector indexing (child chunks).

### Source-Specific Chunking

#### Discourse Forums

```yaml
discourse:
  parent_chunk:
    strategy: "thread"
    max_tokens: 6000          # Full thread up to 6k tokens
    include_metadata: true    # Thread title, category, participants
  child_chunk:
    strategy: "reply"
    max_tokens: 600           # Individual replies
    overlap_tokens: 100       # Context overlap
  metadata:
    - thread_id
    - author
    - timestamp
    - category
    - reply_count
```

**Parent Chunk**: Full thread context (all replies concatenated)
**Child Chunks**: Individual replies with author attribution

#### Notion Pages

```yaml
notion:
  parent_chunk:
    strategy: "section"
    max_tokens: 3000          # Section or logical block
    preserve_hierarchy: true  # Keep page structure
  child_chunk:
    strategy: "block"
    max_tokens: 600           # Individual blocks/paragraphs
    overlap_tokens: 100
  metadata:
    - page_id
    - parent_page
    - last_edited
    - database_properties     # If from Notion database
```

**Parent Chunk**: Full page or major section
**Child Chunks**: Individual blocks, tables, or paragraphs

#### Medium Articles

```yaml
medium:
  parent_chunk:
    strategy: "section"
    max_tokens: 3000          # Article section
    use_headings: true        # Split on H2/H3 headers
  child_chunk:
    strategy: "paragraph"
    max_tokens: 600
    overlap_tokens: 100
  metadata:
    - article_url
    - author
    - publish_date
    - section_title
    - claps                   # If available
```

**Parent Chunk**: Section (split on headers)
**Child Chunks**: Paragraphs within section

### Implementation Example

```python
from dataclasses import dataclass
from typing import List, Dict, Optional

@dataclass
class ChunkConfig:
    parent_max_tokens: int = 3000
    child_max_tokens: int = 600
    overlap_tokens: int = 100
    strategy: str = "fixed"  # fixed, semantic, structural

@dataclass
class Chunk:
    text: str
    chunk_id: str
    parent_id: Optional[str]
    metadata: Dict
    start_char: int
    end_char: int
    token_count: int

class ParentChildChunker:
    def __init__(self, config: ChunkConfig):
        self.config = config

    def chunk_document(
        self,
        text: str,
        metadata: Dict
    ) -> List[Chunk]:
        """Split document into parent and child chunks."""
        parent_chunks = self._create_parent_chunks(text, metadata)
        all_chunks = []

        for parent in parent_chunks:
            all_chunks.append(parent)
            children = self._create_child_chunks(parent)
            all_chunks.extend(children)

        return all_chunks

    def _create_parent_chunks(self, text: str, metadata: Dict) -> List[Chunk]:
        # Implementation based on config.strategy
        pass

    def _create_child_chunks(self, parent: Chunk) -> List[Chunk]:
        # Split parent into overlapping children
        pass
```

---

## Part 2: Deduplication Strategy

### Overview

Entity deduplication reduces graph noise by merging entities that refer to the same real-world object but have different surface forms.

### Two-Phase Deduplication

#### Phase 1: Canonical Alias Resolution

Map known aliases to canonical forms using a curated registry.

```json
{
  "canonical_aliases": {
    "regen.network": "Regen Network",
    "RND": "Regen Network Development",
    "Regen Network Development Inc.": "Regen Network Development",
    "ecocredit": "Regen Ecocredit Module",
    "eco-credit": "Regen Ecocredit Module",
    "@greglandua": "Gregory Landua",
    "Greg Landua": "Gregory Landua"
  },
  "merge_patterns": [
    {
      "pattern": "^y[\\s\\-]*on[\\s\\-]*earth",
      "canonical": "Y on Earth",
      "flags": "IGNORECASE"
    },
    {
      "pattern": "^regen\\s*(network)?\\s*(development)?",
      "canonical": "Regen Network Development",
      "flags": "IGNORECASE"
    }
  ]
}
```

#### Phase 2: Fuzzy Matching

For entities not in the canonical registry, use fuzzy string matching.

```python
from typing import Dict, List, Set, Tuple
from collections import defaultdict

class FuzzyDeduplicator:
    """Merge similar entities using string similarity."""

    SIMILARITY_THRESHOLD = 0.85

    def _similarity(self, s1: str, s2: str) -> float:
        """Jaccard similarity on character bigrams."""
        def bigrams(s):
            s = s.lower().strip()
            return set(s[i:i+2] for i in range(len(s)-1))

        b1, b2 = bigrams(s1), bigrams(s2)
        if not b1 or not b2:
            return 0.0

        return len(b1 & b2) / len(b1 | b2)

    def deduplicate(
        self,
        entities: List[Dict],
        threshold: float = None
    ) -> List[Dict]:
        """Group and merge similar entities."""
        threshold = threshold or self.SIMILARITY_THRESHOLD

        # Group by type (only merge same types)
        by_type = defaultdict(list)
        for entity in entities:
            by_type[entity.get("type", "UNKNOWN")].append(entity)

        result = []
        for entity_type, type_entities in by_type.items():
            merged = self._merge_similar(type_entities, threshold)
            result.extend(merged)

        return result

    def _merge_similar(
        self,
        entities: List[Dict],
        threshold: float
    ) -> List[Dict]:
        """Merge entities within similarity threshold."""
        # Union-Find for grouping
        groups = []
        used = set()

        for i, e1 in enumerate(entities):
            if i in used:
                continue

            group = [e1]
            used.add(i)

            for j, e2 in enumerate(entities[i+1:], i+1):
                if j in used:
                    continue

                sim = self._similarity(e1.get("name", ""), e2.get("name", ""))
                if sim >= threshold:
                    group.append(e2)
                    used.add(j)

            groups.append(group)

        return [self._merge_group(g) for g in groups]

    def _merge_group(self, entities: List[Dict]) -> Dict:
        """Merge a group of similar entities."""
        if len(entities) == 1:
            return entities[0]

        # Keep entity with longest description as base
        base = max(entities, key=lambda e: len(e.get("description", "")))
        merged = base.copy()

        # Union aliases
        all_aliases = set()
        for e in entities:
            all_aliases.update(e.get("aliases", []))
            if e.get("name") != merged.get("name"):
                all_aliases.add(e["name"])

        merged["aliases"] = list(all_aliases)
        merged["mention_count"] = len(entities)

        return merged
```

### Merge Strategy Rules

1. **Type-aware**: Only merge entities of the same type
2. **Confidence-weighted**: Higher confidence entities become canonical
3. **Alias preservation**: Original names become aliases
4. **Source aggregation**: Merge source lists from all variants
5. **Privacy override**: Private entity stays private after merge
6. **Longest description wins**: Best description is preserved

---

## Part 3: Unified Graph Schema

### Entity Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "RegenKOIEntity",
  "type": "object",
  "required": ["id", "name", "type"],
  "properties": {
    "id": {
      "type": "string",
      "format": "uuid",
      "description": "Unique entity identifier"
    },
    "name": {
      "type": "string",
      "minLength": 1,
      "maxLength": 200,
      "description": "Canonical entity name"
    },
    "type": {
      "type": "string",
      "enum": [
        "PERSON", "FORMAL_ORGANIZATION", "COMMUNITY", "PROJECT",
        "CONCEPT", "PLACE", "EVENT", "PRODUCT", "RESOURCE",
        "PROPOSAL", "METRIC", "MODULE", "API_ENDPOINT"
      ]
    },
    "aliases": {
      "type": "array",
      "items": {"type": "string"},
      "description": "Alternative names for this entity"
    },
    "description": {
      "type": "string",
      "maxLength": 2000,
      "description": "Brief description of the entity"
    },
    "is_private": {
      "type": "boolean",
      "default": false,
      "description": "Internal/sensitive content marker"
    },
    "confidence": {
      "type": "number",
      "minimum": 0,
      "maximum": 1,
      "description": "Extraction confidence score"
    },
    "sources": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "source_type": {"type": "string"},
          "source_id": {"type": "string"},
          "source_url": {"type": "string", "format": "uri"},
          "extracted_at": {"type": "string", "format": "date-time"}
        }
      }
    },
    "mention_count": {
      "type": "integer",
      "minimum": 1,
      "description": "Number of times entity was extracted"
    },
    "created_at": {
      "type": "string",
      "format": "date-time"
    },
    "updated_at": {
      "type": "string",
      "format": "date-time"
    }
  }
}
```

### Relationship Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "RegenKOIRelationship",
  "type": "object",
  "required": ["id", "subject_id", "predicate", "object_id"],
  "properties": {
    "id": {
      "type": "string",
      "format": "uuid"
    },
    "subject_id": {
      "type": "string",
      "format": "uuid",
      "description": "Source entity ID"
    },
    "predicate": {
      "type": "string",
      "enum": [
        "authored", "proposed", "supports", "opposes",
        "works_for", "leads", "participates_in", "develops",
        "implements", "depends_on", "located_in", "relates_to",
        "references", "partners_with", "measures", "targets"
      ]
    },
    "object_id": {
      "type": "string",
      "format": "uuid",
      "description": "Target entity ID"
    },
    "confidence": {
      "type": "number",
      "minimum": 0,
      "maximum": 1
    },
    "sources": {
      "type": "array",
      "items": {"type": "object"}
    },
    "extracted_at": {
      "type": "string",
      "format": "date-time"
    }
  }
}
```

### Entity Type Ontology

```
PERSON
├── Individual humans
├── Forum usernames (with @prefix normalized)
└── Authors, speakers, contributors

FORMAL_ORGANIZATION
├── Companies, corporations
├── Nonprofits, NGOs
├── DAOs, protocols
└── Government agencies

COMMUNITY
├── Online communities
├── Working groups
├── Collectives
└── Local chapters

PROJECT
├── Software projects
├── Initiatives, programs
├── Methodologies
└── Campaigns

CONCEPT
├── Ideas, theories
├── Practices, techniques
├── Technologies
└── Standards, frameworks

PLACE
├── Countries, cities
├── Regions, ecosystems
├── Landmarks
└── Venues

EVENT
├── Conferences
├── Governance votes
├── Launches, releases
└── Meetings, calls

PRODUCT
├── Software tools
├── Carbon credits
├── Publications, reports
└── Physical products

RESOURCE
├── Documents, papers
├── URLs, links
├── Data sources
└── Media files

PROPOSAL
├── Governance proposals
├── Feature requests
├── RFCs
└── Signaling proposals

METRIC
├── Statistics
├── KPIs, OKRs
├── Measurements
└── Targets

MODULE
├── Software modules
├── Packages
├── Libraries
└── Plugins

API_ENDPOINT
├── REST endpoints
├── gRPC services
├── Query methods
└── Mutation handlers
```

### Predicate Vocabulary

| Predicate | Domain | Range | Description |
|-----------|--------|-------|-------------|
| authored | PERSON | RESOURCE, PRODUCT | Created content |
| proposed | PERSON | PROPOSAL | Submitted proposal |
| supports | PERSON, ORG | PROPOSAL, CONCEPT | Expresses support |
| opposes | PERSON, ORG | PROPOSAL, CONCEPT | Expresses opposition |
| works_for | PERSON | ORGANIZATION | Employment |
| leads | PERSON | PROJECT, ORG | Leadership role |
| participates_in | PERSON, ORG | COMMUNITY, EVENT | Involvement |
| develops | ORG, PERSON | PROJECT, PRODUCT | Development role |
| implements | MODULE | CONCEPT | Technical implementation |
| depends_on | MODULE, PROJECT | MODULE, PROJECT | Dependency |
| located_in | ORG, PERSON, EVENT | PLACE | Geographic location |
| relates_to | ANY | ANY | General relationship |
| references | RESOURCE | RESOURCE | Citation/link |
| partners_with | ORG | ORG | Partnership |
| measures | METRIC | CONCEPT | Quantification |
| targets | PROJECT, STRATEGY | METRIC, MILESTONE | Goal/objective |

---

## Summary

These specifications complement the main deliverables:

1. **Chunking Strategy**: Source-specific parent-child chunking for optimal LLM context
2. **Deduplication Strategy**: Two-phase (canonical + fuzzy) entity resolution
3. **Unified Graph Schema**: JSON Schema for entities and relationships with full ontology

Together with the EntityQualityFilter POC and post-processing pipeline design, these provide a complete blueprint for improving Regen KOI extraction quality.
