# PROMPT 6: Phase 2b - Post-Processing Pipeline Framework

**Agent Role**: Architecture & Implementation Agent
**Phase**: 2b - Modular Post-Processing Pipeline
**Duration**: 1-2 weeks
**Difficulty**: Medium-High
**Prerequisites**: Phase 2a complete (Confidence filtering operational, 99.7% quality)

---

## Context

You are continuing work on the Regen KOI knowledge graph quality improvement project. Phases 1-2a have been extremely successful:

- ✅ **Phase 1**: EntityQualityFilter + CanonicalResolver (99.7% quality)
- ✅ **Week 1**: Production deployment successful
- ✅ **Phase 2a**: ConfidenceFilter deployed (49 tests passing)

**Current Architecture** (3 independent filters):
```
Extract → ConfidenceFilter → CanonicalResolver → EntityQualityFilter → Insert
          (hard-coded sequence in graph_integration.py)
```

**Problems with Current Approach**:
1. **Tightly Coupled**: Filters are hard-coded in graph_integration.py
2. **Hard to Extend**: Adding new filters requires modifying core integration code
3. **No Reusability**: Each filter is specific to current implementation
4. **Testing Complexity**: Hard to test filter interactions in isolation
5. **No Configuration**: Filter order and parameters are hard-coded

**Your Mission**: Build a modular, extensible post-processing pipeline framework that makes adding new quality controls easy, testable, and maintainable.

---

## Objective

Design and implement a post-processing pipeline framework that:
- **Modular**: Each quality control is a separate, self-contained module
- **Composable**: Modules can be combined in any order
- **Extensible**: Adding new modules is trivial (no core code changes)
- **Testable**: Modules can be tested in isolation
- **Configurable**: Pipeline composition and parameters via config file

**Expected Outcome**:
- Pipeline framework operational in production
- 3 existing filters wrapped as modules
- 2+ new modules implemented
- 30+ tests for framework and modules
- Clean separation of concerns

---

## Environment

**Project Location**: `/Users/darrenzal/projects/RegenAI/koi-processor/`

**Key Files**:
```
koi-processor/
├── src/knowledge_graph/
│   ├── postprocessing/                    # NEW: Pipeline framework
│   │   ├── __init__.py
│   │   ├── base.py                        # NEW: Base classes
│   │   ├── pipeline.py                    # NEW: Orchestrator
│   │   ├── context.py                     # NEW: Shared context
│   │   └── modules/                       # NEW: Module library
│   │       ├── __init__.py
│   │       ├── confidence_filter_module.py
│   │       ├── entity_quality_module.py
│   │       ├── canonical_resolver_module.py
│   │       ├── list_splitter_module.py   # NEW
│   │       └── ontology_normalizer_module.py  # NEW
│   ├── config/
│   │   └── pipeline_config.json          # NEW: Pipeline configuration
│   └── graph_integration.py              # MODIFY: Use pipeline
├── tests/
│   ├── test_pipeline_framework.py        # NEW
│   └── test_pipeline_modules.py          # NEW
└── scripts/
    └── test_pipeline.py                  # NEW: Validation
```

**Database**:
- **Production**: `darren@202.61.196.119` (PostgreSQL port 5433, database: eliza)
- **Local**: `localhost:5433` (if available)

---

## Architecture Design

### 1. Core Concepts

#### ProcessingContext
Shared state object passed through all modules:
```python
@dataclass
class ProcessingContext:
    """Shared context for pipeline processing."""

    # Input
    entities: List[Entity]           # Entities to process
    relationships: List[Relationship] # Relationships to process
    metadata: Dict[str, Any]          # Source metadata

    # Processing state
    blocked_entities: List[Entity]    # Blocked during processing
    modified_entities: List[Entity]   # Modified by modules
    statistics: Dict[str, Any]        # Processing statistics

    # Control flow
    should_halt: bool = False         # Stop pipeline early
    errors: List[str] = []            # Errors encountered

    # Methods
    def block_entity(self, entity, reason)
    def modify_entity(self, entity, changes)
    def merge_entities(self, entities, canonical)
    def add_statistic(self, key, value)
```

#### PostProcessingModule (Base Class)
Abstract base class for all modules:
```python
class PostProcessingModule(ABC):
    """Base class for post-processing modules."""

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.stats = defaultdict(int)

    @abstractmethod
    def process(self, context: ProcessingContext) -> ProcessingContext:
        """Process entities/relationships in context."""
        pass

    @abstractmethod
    def get_name(self) -> str:
        """Return module name for logging/debugging."""
        pass

    def get_statistics(self) -> Dict[str, Any]:
        """Return module-specific statistics."""
        return dict(self.stats)

    def reset(self):
        """Reset module state."""
        self.stats.clear()
```

#### PipelineOrchestrator
Runs modules in sequence:
```python
class PipelineOrchestrator:
    """Orchestrates multi-stage post-processing."""

    def __init__(self, modules: List[PostProcessingModule]):
        self.modules = modules
        self.stats = {}

    def process(self, context: ProcessingContext) -> ProcessingContext:
        """Run all modules in sequence."""
        for module in self.modules:
            logger.info(f"Running module: {module.get_name()}")
            context = module.process(context)

            # Track module statistics
            self.stats[module.get_name()] = module.get_statistics()

            # Early exit if requested
            if context.should_halt:
                logger.warning(f"Pipeline halted by {module.get_name()}")
                break

        return context

    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics from all modules."""
        return self.stats
```

---

## Tasks

### Task 1: Create Base Framework (3-4 hours)

**Objective**: Implement core pipeline architecture

#### 1.1 Create ProcessingContext

Create `src/knowledge_graph/postprocessing/context.py`:

```python
"""
Processing context for pipeline framework.

The context object is passed through all pipeline modules and contains:
- Input data (entities, relationships)
- Processing state (blocked, modified)
- Statistics and metadata
- Control flow (halt, errors)
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime


@dataclass
class Entity:
    """Entity to be processed."""
    name: str
    type: str
    confidence: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __hash__(self):
        return hash((self.name, self.type))


@dataclass
class Relationship:
    """Relationship to be processed."""
    source: str
    predicate: str
    target: str
    confidence: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessingContext:
    """
    Shared context for pipeline processing.

    This object is passed through all modules in the pipeline and contains
    the current state of processing.
    """

    # Input data
    entities: List[Entity] = field(default_factory=list)
    relationships: List[Relationship] = field(default_factory=list)
    source_metadata: Dict[str, Any] = field(default_factory=dict)

    # Processing state
    blocked_entities: List[Entity] = field(default_factory=list)
    blocked_relationships: List[Relationship] = field(default_factory=list)
    modified_entities: Dict[str, Entity] = field(default_factory=dict)
    merged_groups: List[List[Entity]] = field(default_factory=list)

    # Statistics
    statistics: Dict[str, Any] = field(default_factory=dict)
    module_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Control flow
    should_halt: bool = False
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    # Metadata
    timestamp: datetime = field(default_factory=datetime.now)
    pipeline_version: str = "1.0.0"

    def block_entity(self, entity: Entity, reason: str, module_name: str):
        """Block an entity from being inserted."""
        self.blocked_entities.append(entity)
        self.entities = [e for e in self.entities if e != entity]

        # Track in statistics
        key = f"{module_name}_blocked"
        self.statistics[key] = self.statistics.get(key, 0) + 1

        # Store reason
        entity.metadata['blocked_by'] = module_name
        entity.metadata['blocked_reason'] = reason

    def block_relationship(self, relationship: Relationship, reason: str, module_name: str):
        """Block a relationship from being inserted."""
        self.blocked_relationships.append(relationship)
        self.relationships = [r for r in self.relationships if r != relationship]

        key = f"{module_name}_blocked_relationships"
        self.statistics[key] = self.statistics.get(key, 0) + 1

    def modify_entity(self, original: Entity, modified: Entity, module_name: str):
        """Modify an entity (e.g., canonicalize name)."""
        # Remove original, add modified
        self.entities = [e if e != original else modified for e in self.entities]

        # Track modification
        self.modified_entities[original.name] = modified

        key = f"{module_name}_modified"
        self.statistics[key] = self.statistics.get(key, 0) + 1

    def merge_entities(self, entities: List[Entity], canonical: Entity, module_name: str):
        """Merge multiple entities into a canonical entity."""
        self.merged_groups.append(entities)

        # Remove originals, add canonical
        entity_set = set(entities)
        self.entities = [e for e in self.entities if e not in entity_set]
        self.entities.append(canonical)

        key = f"{module_name}_merged"
        self.statistics[key] = self.statistics.get(key, 0) + 1

    def add_entities(self, entities: List[Entity], module_name: str):
        """Add new entities (e.g., from splitting)."""
        self.entities.extend(entities)

        key = f"{module_name}_added"
        self.statistics[key] = self.statistics.get(key, 0) + 1

    def add_statistic(self, key: str, value: Any):
        """Add a custom statistic."""
        self.statistics[key] = value

    def halt(self, reason: str):
        """Stop pipeline processing."""
        self.should_halt = True
        self.errors.append(f"Pipeline halted: {reason}")

    def get_summary(self) -> Dict[str, Any]:
        """Get processing summary."""
        return {
            'timestamp': self.timestamp.isoformat(),
            'total_entities': len(self.entities),
            'total_relationships': len(self.relationships),
            'blocked_entities': len(self.blocked_entities),
            'blocked_relationships': len(self.blocked_relationships),
            'modified_entities': len(self.modified_entities),
            'merged_groups': len(self.merged_groups),
            'errors': len(self.errors),
            'warnings': len(self.warnings),
            'statistics': self.statistics
        }
```

**Acceptance Criteria**:
- [x] ProcessingContext class created
- [x] Entity and Relationship dataclasses defined
- [x] Methods for blocking, modifying, merging entities
- [x] Statistics tracking
- [x] Control flow (halt, errors)

---

#### 1.2 Create PostProcessingModule Base Class

Create `src/knowledge_graph/postprocessing/base.py`:

```python
"""
Base classes for post-processing pipeline modules.

All pipeline modules inherit from PostProcessingModule and implement
the process() method.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any
from collections import defaultdict
import logging

from .context import ProcessingContext

logger = logging.getLogger(__name__)


class PostProcessingModule(ABC):
    """
    Abstract base class for post-processing modules.

    All modules must implement:
    - process(): Main processing logic
    - get_name(): Module identifier

    Optional overrides:
    - validate_config(): Validate module configuration
    - reset(): Reset module state
    """

    def __init__(self, config: Dict[str, Any] = None):
        """
        Initialize module with configuration.

        Args:
            config: Module-specific configuration dictionary
        """
        self.config = config or {}
        self.stats = defaultdict(int)
        self.enabled = self.config.get('enabled', True)

        # Validate configuration
        self.validate_config()

    @abstractmethod
    def process(self, context: ProcessingContext) -> ProcessingContext:
        """
        Process entities/relationships in context.

        Args:
            context: Processing context with entities and relationships

        Returns:
            Modified context after processing
        """
        pass

    @abstractmethod
    def get_name(self) -> str:
        """
        Return module name for logging/debugging.

        Returns:
            Module identifier (e.g., "ConfidenceFilter")
        """
        pass

    def validate_config(self):
        """
        Validate module configuration.

        Raises:
            ValueError: If configuration is invalid
        """
        # Override in subclasses if needed
        pass

    def get_statistics(self) -> Dict[str, Any]:
        """
        Return module-specific statistics.

        Returns:
            Dictionary of statistics
        """
        return {
            **dict(self.stats),
            'enabled': self.enabled
        }

    def reset(self):
        """Reset module state and statistics."""
        self.stats.clear()

    def __repr__(self) -> str:
        return f"{self.get_name()}(enabled={self.enabled})"
```

**Acceptance Criteria**:
- [x] PostProcessingModule base class created
- [x] Abstract methods defined (process, get_name)
- [x] Configuration handling
- [x] Statistics tracking
- [x] Enable/disable functionality

---

#### 1.3 Create PipelineOrchestrator

Create `src/knowledge_graph/postprocessing/pipeline.py`:

```python
"""
Pipeline orchestrator for multi-stage post-processing.

The orchestrator runs modules in sequence, tracks statistics,
and handles errors.
"""

from typing import List, Dict, Any
import logging
from datetime import datetime

from .base import PostProcessingModule
from .context import ProcessingContext

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """
    Orchestrates multi-stage post-processing of extracted entities.

    Runs modules in sequence, allowing each to:
    - Block entities/relationships
    - Modify entities
    - Merge duplicates
    - Add new entities (e.g., from splitting)

    Example:
        pipeline = PipelineOrchestrator([
            ConfidenceFilterModule(),
            CanonicalResolverModule(),
            EntityQualityFilterModule(),
            ListSplitterModule()
        ])

        context = ProcessingContext(entities=[...])
        result = pipeline.process(context)
    """

    def __init__(
        self,
        modules: List[PostProcessingModule],
        stop_on_error: bool = False
    ):
        """
        Initialize pipeline with modules.

        Args:
            modules: List of modules to run in sequence
            stop_on_error: If True, halt pipeline on first error
        """
        self.modules = modules
        self.stop_on_error = stop_on_error
        self.stats = {}
        self.execution_times = {}

    def process(self, context: ProcessingContext) -> ProcessingContext:
        """
        Run all modules in sequence.

        Args:
            context: Processing context with entities/relationships

        Returns:
            Modified context after all modules
        """
        logger.info(f"Starting pipeline with {len(self.modules)} modules")
        logger.info(f"Input: {len(context.entities)} entities, "
                   f"{len(context.relationships)} relationships")

        for i, module in enumerate(self.modules, 1):
            module_name = module.get_name()

            # Skip if disabled
            if not module.enabled:
                logger.info(f"[{i}/{len(self.modules)}] Skipping {module_name} (disabled)")
                continue

            logger.info(f"[{i}/{len(self.modules)}] Running {module_name}")

            # Measure execution time
            start_time = datetime.now()

            try:
                # Run module
                context = module.process(context)

                # Track execution time
                execution_time = (datetime.now() - start_time).total_seconds()
                self.execution_times[module_name] = execution_time

                # Track module statistics
                self.stats[module_name] = module.get_statistics()

                logger.info(f"  Completed in {execution_time:.3f}s")
                logger.info(f"  Entities: {len(context.entities)}, "
                           f"Blocked: {len(context.blocked_entities)}")

            except Exception as e:
                error_msg = f"Error in {module_name}: {str(e)}"
                logger.error(error_msg)
                context.errors.append(error_msg)

                if self.stop_on_error:
                    context.halt(f"Module {module_name} failed")
                    break

            # Check if pipeline should halt
            if context.should_halt:
                logger.warning(f"Pipeline halted by {module_name}")
                break

        logger.info(f"Pipeline complete. Output: {len(context.entities)} entities")

        return context

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get statistics from all modules.

        Returns:
            Dictionary with per-module statistics and execution times
        """
        return {
            'modules': self.stats,
            'execution_times': self.execution_times,
            'total_time': sum(self.execution_times.values())
        }

    def reset(self):
        """Reset all modules and pipeline statistics."""
        for module in self.modules:
            module.reset()
        self.stats.clear()
        self.execution_times.clear()

    def __repr__(self) -> str:
        module_names = [m.get_name() for m in self.modules]
        return f"PipelineOrchestrator({module_names})"
```

**Acceptance Criteria**:
- [x] PipelineOrchestrator class created
- [x] Runs modules in sequence
- [x] Tracks execution time per module
- [x] Statistics aggregation
- [x] Error handling (stop_on_error option)
- [x] Logging and debugging output

---

### Task 2: Wrap Existing Filters as Modules (3-4 hours)

**Objective**: Convert existing filters into pipeline modules

#### 2.1 ConfidenceFilterModule

Create `src/knowledge_graph/postprocessing/modules/confidence_filter_module.py`:

```python
"""
Confidence filtering module for pipeline.

Wraps the existing ConfidenceFilter to work within the pipeline framework.
"""

from typing import Dict, Any
import logging

from ..base import PostProcessingModule
from ..context import ProcessingContext
from ...improvements import ConfidenceFilter

logger = logging.getLogger(__name__)


class ConfidenceFilterModule(PostProcessingModule):
    """
    Module that filters entities/relationships based on confidence scores.

    Configuration:
        entity_threshold: Minimum confidence for entities (default: 0.70)
        relationship_threshold: Minimum confidence for relationships (default: 0.80)
        allow_null: Allow entities without confidence scores (default: True)
        strict_mode: Require all entities to have confidence (default: False)
    """

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)

        # Create underlying filter
        self.filter = ConfidenceFilter(
            entity_threshold=self.config.get('entity_threshold', 0.70),
            relationship_threshold=self.config.get('relationship_threshold', 0.80),
            allow_null=self.config.get('allow_null', True),
            strict_mode=self.config.get('strict_mode', False)
        )

    def get_name(self) -> str:
        return "ConfidenceFilter"

    def process(self, context: ProcessingContext) -> ProcessingContext:
        """Filter entities and relationships by confidence."""

        # Filter entities
        entities_to_remove = []
        for entity in context.entities:
            is_valid, reason = self.filter.filter_entity(
                entity.name,
                entity.type,
                entity.confidence
            )

            if not is_valid:
                entities_to_remove.append((entity, reason))
                self.stats['entities_blocked'] += 1

        # Block low-confidence entities
        for entity, reason in entities_to_remove:
            context.block_entity(entity, reason, self.get_name())

        # Filter relationships
        relationships_to_remove = []
        for rel in context.relationships:
            is_valid, reason = self.filter.filter_relationship(
                rel.source,
                rel.predicate,
                rel.target,
                rel.confidence
            )

            if not is_valid:
                relationships_to_remove.append((rel, reason))
                self.stats['relationships_blocked'] += 1

        # Block low-confidence relationships
        for rel, reason in relationships_to_remove:
            context.block_relationship(rel, reason, self.get_name())

        return context
```

**Acceptance Criteria**:
- [x] ConfidenceFilterModule wraps existing ConfidenceFilter
- [x] Works within pipeline framework
- [x] Blocks entities/relationships properly
- [x] Statistics tracked

---

#### 2.2 EntityQualityFilterModule

Create `src/knowledge_graph/postprocessing/modules/entity_quality_module.py`:

```python
"""
Entity quality filtering module for pipeline.

Wraps the existing EntityQualityFilter to work within the pipeline framework.
"""

from typing import Dict, Any
import logging

from ..base import PostProcessingModule
from ..context import ProcessingContext
from ...improvements import EntityQualityFilter, FilterConfig

logger = logging.getLogger(__name__)


class EntityQualityFilterModule(PostProcessingModule):
    """
    Module that filters entities using pattern-based rules.

    Blocks:
    - Pronouns (we, they, it, etc.)
    - Generic nouns (user, farmer, project, etc.)
    - Technical patterns (URLs, localhost, function names, etc.)
    - Sentence fragments
    - Numerics
    """

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)

        # Create underlying filter
        filter_config = FilterConfig()
        self.filter = EntityQualityFilter(filter_config)

    def get_name(self) -> str:
        return "EntityQualityFilter"

    def process(self, context: ProcessingContext) -> ProcessingContext:
        """Filter entities using pattern-based rules."""

        entities_to_remove = []
        for entity in context.entities:
            is_valid, reasons = self.filter.filter_with_reasons(
                entity.name,
                entity.type
            )

            if not is_valid:
                entities_to_remove.append((entity, reasons))
                self.stats['entities_blocked'] += 1

                # Track by reason
                for reason in reasons:
                    self.stats[f'blocked_{reason}'] += 1

        # Block low-quality entities
        for entity, reasons in entities_to_remove:
            reason_str = ', '.join(reasons)
            context.block_entity(entity, reason_str, self.get_name())

        return context
```

---

#### 2.3 CanonicalResolverModule

Create `src/knowledge_graph/postprocessing/modules/canonical_resolver_module.py`:

```python
"""
Canonical resolution module for pipeline.

Wraps the existing CanonicalResolver to work within the pipeline framework.
"""

from typing import Dict, Any
import logging

from ..base import PostProcessingModule
from ..context import ProcessingContext, Entity
from ...improvements import CanonicalResolver

logger = logging.getLogger(__name__)


class CanonicalResolverModule(PostProcessingModule):
    """
    Module that resolves entity aliases to canonical names.

    For example:
    - "regen.network" → "Regen Network"
    - "ecocredit" → "Ecocredit Module"
    - "Gregory" → "Gregory Landua"
    """

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)

        # Create underlying resolver
        self.resolver = CanonicalResolver()

    def get_name(self) -> str:
        return "CanonicalResolver"

    def process(self, context: ProcessingContext) -> ProcessingContext:
        """Resolve entity aliases to canonical names."""

        for entity in list(context.entities):  # Copy to allow modification
            canonical_name, was_resolved = self.resolver.resolve(
                entity.name,
                entity.type
            )

            if was_resolved:
                # Create modified entity with canonical name
                canonical_entity = Entity(
                    name=canonical_name,
                    type=entity.type,
                    confidence=entity.confidence,
                    metadata={
                        **entity.metadata,
                        'original_name': entity.name,
                        'resolved_by': self.get_name()
                    }
                )

                context.modify_entity(entity, canonical_entity, self.get_name())
                self.stats['entities_canonicalized'] += 1

        return context
```

**Acceptance Criteria**:
- [x] All 3 existing filters wrapped as modules
- [x] Work within pipeline framework
- [x] Statistics tracked correctly
- [x] Behavior identical to standalone filters

---

### Task 3: Implement New Modules (4-5 hours)

**Objective**: Add new quality control modules

#### 3.1 ListSplitterModule

Create `src/knowledge_graph/postprocessing/modules/list_splitter_module.py`:

```python
"""
List splitter module for pipeline.

Splits entities that are actually lists into individual entities.
For example: "Gregory Landua, Will Szal, and Austin Wade" → 3 separate PERSON entities
"""

from typing import Dict, Any, List
import re
import logging

from ..base import PostProcessingModule
from ..context import ProcessingContext, Entity

logger = logging.getLogger(__name__)


class ListSplitterModule(PostProcessingModule):
    """
    Module that splits list-like entities into individual entities.

    Detects patterns like:
    - "A, B, and C"
    - "A, B, C"
    - "A and B"
    - "A & B"

    Configuration:
        min_confidence_to_split: Only split high-confidence entities (default: 0.75)
        max_items: Maximum list items to split (default: 5)
        enabled_types: Entity types to split (default: ["PERSON", "ORGANIZATION", "PROJECT"])
    """

    # List patterns
    LIST_PATTERNS = [
        r'^([^,]+),\s*([^,]+),\s*and\s+([^,]+)$',  # "A, B, and C"
        r'^([^,]+),\s*([^,]+),\s*([^,]+)$',         # "A, B, C"
        r'^([^,]+)\s+and\s+([^,]+)$',               # "A and B"
        r'^([^,]+)\s*&\s*([^,]+)$',                 # "A & B"
    ]

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)

        self.min_confidence = self.config.get('min_confidence_to_split', 0.75)
        self.max_items = self.config.get('max_items', 5)
        self.enabled_types = self.config.get('enabled_types', [
            "PERSON", "ORGANIZATION", "PROJECT"
        ])

    def get_name(self) -> str:
        return "ListSplitter"

    def process(self, context: ProcessingContext) -> ProcessingContext:
        """Split list-like entities into individual entities."""

        entities_to_remove = []
        entities_to_add = []

        for entity in context.entities:
            # Only split enabled types
            if entity.type not in self.enabled_types:
                continue

            # Only split high-confidence entities (to avoid false positives)
            if entity.confidence is not None and entity.confidence < self.min_confidence:
                continue

            # Try to split
            items = self._try_split(entity.name)

            if items and len(items) <= self.max_items:
                # Split successful
                entities_to_remove.append(entity)

                for item in items:
                    # Create new entity for each item
                    new_entity = Entity(
                        name=item.strip(),
                        type=entity.type,
                        confidence=entity.confidence,
                        metadata={
                            **entity.metadata,
                            'split_from': entity.name,
                            'split_by': self.get_name()
                        }
                    )
                    entities_to_add.append(new_entity)

                self.stats['lists_split'] += 1
                self.stats['entities_added'] += len(items)

        # Remove list entities
        for entity in entities_to_remove:
            context.entities.remove(entity)

        # Add individual entities
        context.add_entities(entities_to_add, self.get_name())

        return context

    def _try_split(self, text: str) -> List[str]:
        """
        Try to split text as a list.

        Returns:
            List of items if split successful, None otherwise
        """
        for pattern in self.LIST_PATTERNS:
            match = re.match(pattern, text, re.IGNORECASE)
            if match:
                return list(match.groups())

        return None
```

**Acceptance Criteria**:
- [x] ListSplitterModule implemented
- [x] Detects list patterns ("A, B, and C", etc.)
- [x] Splits into individual entities
- [x] Only splits high-confidence entities
- [x] Configurable patterns and types

---

#### 3.2 OntologyNormalizerModule

Create `src/knowledge_graph/postprocessing/modules/ontology_normalizer_module.py`:

```python
"""
Ontology normalization module for pipeline.

Normalizes entity types and relationship predicates to standard ontology.
"""

from typing import Dict, Any
import logging

from ..base import PostProcessingModule
from ..context import ProcessingContext, Entity

logger = logging.getLogger(__name__)


class OntologyNormalizerModule(PostProcessingModule):
    """
    Module that normalizes entity types and relationship predicates.

    Standardizes:
    - Entity types: PERSON, ORGANIZATION, PROJECT, CONCEPT, EVENT, LOCATION
    - Relationship predicates: works_at, founded, mentions, etc.

    Configuration:
        type_mappings: Custom type normalization rules
        predicate_mappings: Custom predicate normalization rules
    """

    # Default type mappings
    DEFAULT_TYPE_MAPPINGS = {
        'INDIVIDUAL': 'PERSON',
        'PEOPLE': 'PERSON',
        'HUMAN': 'PERSON',
        'ORG': 'ORGANIZATION',
        'COMPANY': 'ORGANIZATION',
        'FOUNDATION': 'ORGANIZATION',
        'REPO': 'PROJECT',
        'REPOSITORY': 'PROJECT',
        'SOFTWARE': 'PROJECT',
        'IDEA': 'CONCEPT',
        'TOPIC': 'CONCEPT',
        'PLACE': 'LOCATION',
        'CITY': 'LOCATION',
    }

    # Default predicate mappings
    DEFAULT_PREDICATE_MAPPINGS = {
        'employed_by': 'works_at',
        'works_for': 'works_at',
        'member_of': 'part_of',
        'belongs_to': 'part_of',
        'created': 'founded',
        'established': 'founded',
        'refers_to': 'mentions',
        'cites': 'mentions',
    }

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)

        # Merge custom mappings with defaults
        self.type_mappings = {
            **self.DEFAULT_TYPE_MAPPINGS,
            **self.config.get('type_mappings', {})
        }

        self.predicate_mappings = {
            **self.DEFAULT_PREDICATE_MAPPINGS,
            **self.config.get('predicate_mappings', {})
        }

    def get_name(self) -> str:
        return "OntologyNormalizer"

    def process(self, context: ProcessingContext) -> ProcessingContext:
        """Normalize entity types and relationship predicates."""

        # Normalize entity types
        for entity in list(context.entities):
            original_type = entity.type
            normalized_type = self.type_mappings.get(original_type, original_type)

            if normalized_type != original_type:
                # Create normalized entity
                normalized_entity = Entity(
                    name=entity.name,
                    type=normalized_type,
                    confidence=entity.confidence,
                    metadata={
                        **entity.metadata,
                        'original_type': original_type,
                        'normalized_by': self.get_name()
                    }
                )

                context.modify_entity(entity, normalized_entity, self.get_name())
                self.stats['types_normalized'] += 1

        # Normalize relationship predicates
        for rel in context.relationships:
            original_predicate = rel.predicate
            normalized_predicate = self.predicate_mappings.get(
                original_predicate,
                original_predicate
            )

            if normalized_predicate != original_predicate:
                rel.predicate = normalized_predicate
                rel.metadata['original_predicate'] = original_predicate
                self.stats['predicates_normalized'] += 1

        return context
```

**Acceptance Criteria**:
- [x] OntologyNormalizerModule implemented
- [x] Normalizes entity types
- [x] Normalizes relationship predicates
- [x] Configurable mappings
- [x] Tracks original values in metadata

---

### Task 4: Create Pipeline Configuration (1 hour)

**Objective**: Configuration system for pipeline composition

Create `src/knowledge_graph/config/pipeline_config.json`:

```json
{
  "version": "1.0.0",
  "pipeline": {
    "stop_on_error": false,
    "modules": [
      {
        "name": "ConfidenceFilter",
        "enabled": true,
        "config": {
          "entity_threshold": 0.70,
          "relationship_threshold": 0.80,
          "allow_null": true,
          "strict_mode": false
        }
      },
      {
        "name": "CanonicalResolver",
        "enabled": true,
        "config": {}
      },
      {
        "name": "EntityQualityFilter",
        "enabled": true,
        "config": {}
      },
      {
        "name": "ListSplitter",
        "enabled": true,
        "config": {
          "min_confidence_to_split": 0.75,
          "max_items": 5,
          "enabled_types": ["PERSON", "ORGANIZATION", "PROJECT"]
        }
      },
      {
        "name": "OntologyNormalizer",
        "enabled": true,
        "config": {
          "type_mappings": {},
          "predicate_mappings": {}
        }
      }
    ]
  },
  "metadata": {
    "description": "Production post-processing pipeline configuration",
    "last_updated": "2025-12-09",
    "updated_by": "Phase 2b Implementation"
  }
}
```

**Acceptance Criteria**:
- [x] Pipeline configuration file created
- [x] All modules configured
- [x] Module order specified
- [x] Enable/disable per module
- [x] Per-module configuration

---

### Task 5: Comprehensive Testing (4-5 hours)

**Objective**: Test framework and all modules

#### 5.1 Framework Tests

Create `tests/test_pipeline_framework.py`:

Should include tests for:
- ProcessingContext operations
- PostProcessingModule base class
- PipelineOrchestrator
- Module execution order
- Statistics tracking
- Error handling
- Early halt functionality

**Target**: 20+ tests

---

#### 5.2 Module Tests

Create `tests/test_pipeline_modules.py`:

Should include tests for:
- ConfidenceFilterModule
- EntityQualityFilterModule
- CanonicalResolverModule
- ListSplitterModule
- OntologyNormalizerModule
- Module interactions

**Target**: 25+ tests

---

### Task 6: Integration with Graph Integration (2-3 hours)

**Objective**: Replace hard-coded filters with pipeline

Modify `src/knowledge_graph/graph_integration.py`:

```python
# OLD: Hard-coded filters
if self.confidence_filter:
    is_valid, reason = self.confidence_filter.filter_entity(...)
    if not is_valid:
        return None

canonical_name, was_resolved = self.canonical_resolver.resolve(...)

is_valid, reasons = self.entity_filter.filter_with_reasons(...)

# NEW: Use pipeline
from src.knowledge_graph.postprocessing import create_pipeline_from_config

self.pipeline = create_pipeline_from_config('pipeline_config.json')

def process_entities_batch(self, entities: List[Dict]) -> List[Dict]:
    """Process a batch of entities through pipeline."""

    # Convert to pipeline entities
    pipeline_entities = [
        Entity(name=e['name'], type=e['type'], confidence=e.get('confidence'))
        for e in entities
    ]

    # Create context
    context = ProcessingContext(entities=pipeline_entities)

    # Run pipeline
    result = self.pipeline.process(context)

    # Extract valid entities
    return [
        {'name': e.name, 'type': e.type, 'confidence': e.confidence}
        for e in result.entities
    ]
```

**Acceptance Criteria**:
- [x] Pipeline integrated into graph_integration.py
- [x] Existing functionality preserved
- [x] Statistics accessible
- [x] Backward compatible

---

### Task 7: Validation and Production Deployment (2-3 hours)

**Objective**: Test and deploy pipeline framework

#### 7.1 Create Validation Script

Create `scripts/test_pipeline.py`:

Should demonstrate:
- Pipeline with all modules
- Sample entities with various issues
- Statistics reporting
- Module execution times
- Before/after comparison

---

#### 7.2 Deploy to Production

```bash
# Sync pipeline framework
scp -r src/knowledge_graph/postprocessing/ darren@202.61.196.119:/opt/projects/koi-processor/src/knowledge_graph/

# Sync configuration
scp src/knowledge_graph/config/pipeline_config.json darren@202.61.196.119:/opt/projects/koi-processor/src/knowledge_graph/config/

# Sync modified graph_integration
scp src/knowledge_graph/graph_integration.py darren@202.61.196.119:/opt/projects/koi-processor/src/knowledge_graph/

# Sync tests
scp tests/test_pipeline_*.py darren@202.61.196.119:/opt/projects/koi-processor/tests/

# Run tests on production
ssh darren@202.61.196.119 "cd /opt/projects/koi-processor && python3 -m pytest tests/test_pipeline_framework.py tests/test_pipeline_modules.py -v"

# Run validation
ssh darren@202.61.196.119 "cd /opt/projects/koi-processor && python3 scripts/test_pipeline.py"
```

---

### Task 8: Generate Phase 2b Report (30 min)

Create `reports/PHASE2B_PIPELINE_FRAMEWORK_REPORT.md`:

Include:
1. Architecture overview
2. Implementation details (all modules)
3. Test results
4. Production deployment
5. Performance analysis
6. Usage examples
7. Next steps (Phase 3)

---

## Success Criteria

### Functionality
- [x] ProcessingContext, PostProcessingModule, PipelineOrchestrator implemented
- [x] 3 existing filters wrapped as modules
- [x] 2 new modules implemented (ListSplitter, OntologyNormalizer)
- [x] Pipeline configuration system working
- [x] Integrated into graph_integration.py

### Quality
- [x] 45+ tests passing (20 framework + 25 modules)
- [x] All modules tested in isolation
- [x] Pipeline integration tested
- [x] No regressions in existing functionality

### Production
- [x] Deployed to production successfully
- [x] Tests passing on production server
- [x] Validation script demonstrates correct behavior
- [x] No errors in first 24 hours

### Documentation
- [x] Code well-commented
- [x] Phase 2b report generated
- [x] Configuration documented
- [x] Usage examples provided

---

## Expected Deliverables

1. **Framework Code**:
   - `src/knowledge_graph/postprocessing/context.py`
   - `src/knowledge_graph/postprocessing/base.py`
   - `src/knowledge_graph/postprocessing/pipeline.py`

2. **Module Library**:
   - `modules/confidence_filter_module.py`
   - `modules/entity_quality_module.py`
   - `modules/canonical_resolver_module.py`
   - `modules/list_splitter_module.py`
   - `modules/ontology_normalizer_module.py`

3. **Configuration**:
   - `config/pipeline_config.json`

4. **Tests**:
   - `tests/test_pipeline_framework.py` (20+ tests)
   - `tests/test_pipeline_modules.py` (25+ tests)
   - `scripts/test_pipeline.py` (validation)

5. **Reports**:
   - `reports/PHASE2B_PIPELINE_FRAMEWORK_REPORT.md`

---

## Grading Rubric

**A+ (95-100)**:
- All tests passing (45+)
- All 5 modules operational
- Clean production deployment
- Excellent performance (< 10% overhead)
- Comprehensive documentation

**A (90-94)**:
- Most tests passing (40+)
- All modules working
- Production deployment successful
- Good performance

**B (80-89)**:
- Basic functionality working
- Some modules need refinement
- Minor issues in production

---

## Resources

### Reference Files
- Phase 2a Report: `reports/PHASE2A_CONFIDENCE_FILTERING_REPORT.md`
- Existing Filters: `src/knowledge_graph/improvements/`
- Quality Config: `src/knowledge_graph/config/quality_config.json`

### Design Patterns
- **Pipeline Pattern**: Sequential processing with shared context
- **Strategy Pattern**: Interchangeable modules
- **Template Method**: PostProcessingModule base class

---

## Notes

- **Modularity**: Each module should be completely independent
- **Configuration**: All behavior should be configurable via JSON
- **Testing**: Test modules in isolation first, then integration
- **Performance**: Pipeline should add < 10% overhead vs direct calls
- **Backward Compatibility**: Existing extraction code should work unchanged

---

**When Complete**: Generate Phase 2b report, sync to production, and notify user. If successful, user can proceed to Phase 3 (Fuzzy Deduplication) or other advanced features.

**Good luck!** 🚀
