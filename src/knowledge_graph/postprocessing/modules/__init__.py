"""
Pipeline modules for post-processing.

This package contains all available pipeline modules:
- ConfidenceFilterModule: Filters by confidence scores
- CanonicalResolverModule: Resolves entity aliases
 - EntityQualityFilterModule: Pattern-based quality filtering
 - ListSplitterModule: Splits list-like entities
 - OntologyNormalizerModule: Normalizes entity types and predicates
"""

from .confidence_filter_module import ConfidenceFilterModule
from .canonical_resolver_module import CanonicalResolverModule
from .entity_quality_module import EntityQualityFilterModule
from .list_splitter_module import ListSplitterModule
from .ontology_normalizer_module import OntologyNormalizerModule

__all__ = [
    'ConfidenceFilterModule',
    'CanonicalResolverModule',
    'EntityQualityFilterModule',
    'ListSplitterModule',
    'OntologyNormalizerModule'
]
