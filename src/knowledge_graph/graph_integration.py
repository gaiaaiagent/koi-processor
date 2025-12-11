"""
Knowledge Graph Integration Module for KOI
Connects extracted entities and relationships into unified RDF knowledge graph

Includes quality controls via the post-processing pipeline:
- ConfidenceFilterModule: Filters entities/relationships by confidence scores
- CanonicalResolverModule: Maps aliases to canonical entity names
- EntityQualityFilterModule: Blocks low-quality entities (pronouns, generics, etc.)
- ListSplitterModule: Splits list-like entities into individuals
- OntologyNormalizerModule: Normalizes entity types and predicates

The pipeline framework provides modular, configurable quality control.
"""

import json
import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import os

try:
    from rdflib import Graph, Namespace, URIRef, Literal, RDF, RDFS, OWL, XSD
    from rdflib.plugins.stores import sparqlstore
    HAS_RDFLIB = True
except ImportError:
    print("Warning: rdflib not installed. Install with: pip install rdflib SPARQLWrapper")
    HAS_RDFLIB = False

# Import quality control modules (legacy)
try:
    from knowledge_graph.improvements import EntityQualityFilter, FilterConfig, CanonicalResolver, ConfidenceFilter
    HAS_QUALITY_CONTROLS = True
except ImportError:
    try:
        from src.knowledge_graph.improvements import EntityQualityFilter, FilterConfig, CanonicalResolver, ConfidenceFilter
        HAS_QUALITY_CONTROLS = True
    except ImportError:
        print("Warning: Quality control modules not found. Quality filtering disabled.")
        HAS_QUALITY_CONTROLS = False

# Import entity resolver for deduplication
try:
    from knowledge_graph.entity_resolver import EntityResolver
    HAS_ENTITY_RESOLVER = True
except ImportError:
    try:
        from src.knowledge_graph.entity_resolver import EntityResolver
        HAS_ENTITY_RESOLVER = True
    except ImportError:
        print("Warning: EntityResolver not found. Deduplication disabled.")
        HAS_ENTITY_RESOLVER = False

# Import pipeline framework (new)
try:
    from knowledge_graph.postprocessing import (
        PipelineOrchestrator,
        ProcessingContext,
        Entity as PipelineEntity,
        Relationship as PipelineRelationship,
        create_pipeline_from_config
    )
    from knowledge_graph.postprocessing.modules import (
        ConfidenceFilterModule,
        CanonicalResolverModule,
        DocumentLevelDeduplicator,
        EntityQualityFilterModule,
        ListSplitterModule,
        OntologyNormalizerModule
    )
    HAS_PIPELINE = True
except ImportError:
    try:
        from src.knowledge_graph.postprocessing import (
            PipelineOrchestrator,
            ProcessingContext,
            Entity as PipelineEntity,
            Relationship as PipelineRelationship,
            create_pipeline_from_config
        )
        from src.knowledge_graph.postprocessing.modules import (
            ConfidenceFilterModule,
            CanonicalResolverModule,
            DocumentLevelDeduplicator,
            EntityQualityFilterModule,
            ListSplitterModule,
            OntologyNormalizerModule
        )
        HAS_PIPELINE = True
    except ImportError:
        print("Warning: Pipeline framework not found. Using legacy quality controls.")
        HAS_PIPELINE = False


class KnowledgeGraphIntegrator:
    """
    Integrates extracted entities and relationships into RDF knowledge graph.

    Supports two modes for quality control:
    1. Pipeline mode (default): Uses modular post-processing pipeline
    2. Legacy mode: Uses individual filter classes directly

    Additionally supports entity deduplication via EntityResolver (pgvector-based):
    - Tier 1: Exact match (B-Tree, microseconds)
    - Tier 2: Semantic match (HNSW vector, milliseconds)
    - Tier 3: Create new (deterministic URI)

    Args:
        store_type: RDF store type ("memory", "postgresql", or "sparql")
        store_config: Configuration for the store
        enable_quality_controls: Enable entity quality filtering
        use_pipeline: Use pipeline framework (True) or legacy filters (False)
        pipeline_config_path: Path to pipeline configuration JSON
        enable_deduplication: Enable pgvector-based entity deduplication
        dedup_db_config: Database config for entity_registry (defaults to env vars)
        dedup_threshold: Similarity threshold for semantic matching (default: 0.95)
    """

    def __init__(
        self,
        store_type: str = "memory",  # memory, postgresql, or sparql
        store_config: Dict[str, Any] = None,
        enable_quality_controls: bool = True,
        use_pipeline: bool = True,
        pipeline_config_path: Optional[str] = None,
        enable_deduplication: bool = True,
        dedup_db_config: Dict[str, Any] = None,
        dedup_threshold: float = 0.95
    ):
        self.logger = logging.getLogger(__name__)
        self.store_type = store_type
        self.store_config = store_config or {}
        self.enable_quality_controls = enable_quality_controls
        self.use_pipeline = use_pipeline and HAS_PIPELINE
        self.enable_deduplication = enable_deduplication and HAS_ENTITY_RESOLVER

        if not HAS_RDFLIB:
            raise ImportError("rdflib is required for knowledge graph integration")

        # Initialize RDF graph
        self.graph = self._initialize_graph()

        # Define namespaces
        self.REGEN = Namespace("https://regen.network/ontology#")
        self.KOI = Namespace("https://regen.network/koi#")
        self.PROV = Namespace("http://www.w3.org/ns/prov#")
        self.SCHEMA = Namespace("http://schema.org/")
        self.DC = Namespace("http://purl.org/dc/elements/1.1/")

        # Source-specific namespaces
        self.DISCOURSE = Namespace("https://regen.network/ontology/discourse#")
        self.TWITTER = Namespace("https://regen.network/ontology/twitter#")
        self.MEDIUM = Namespace("https://regen.network/ontology/medium#")
        self.GITHUB = Namespace("https://regen.network/ontology/github#")

        # Bind namespaces to prefixes
        self._bind_namespaces()

        # Entity URI cache to avoid duplicates
        self.entity_cache: Dict[str, URIRef] = {}

        # Initialize quality controls
        self.pipeline = None
        self.entity_filter = None
        self.canonical_resolver = None
        self.confidence_filter = None
        self.entity_resolver = None
        self.quality_stats = {
            'total_extracted': 0,
            'blocked_by_filter': 0,
            'blocked_by_confidence': 0,
            'resolved_to_canonical': 0,
            'inserted_to_graph': 0,
            'pipeline_processed': 0,
            'dedup_exact_hits': 0,
            'dedup_semantic_hits': 0,
            'dedup_new_entities': 0
        }

        if self.enable_quality_controls:
            if self.use_pipeline:
                self._initialize_pipeline(pipeline_config_path)
            else:
                self._initialize_legacy_controls()

        # Initialize entity deduplication
        if self.enable_deduplication:
            self._initialize_entity_resolver(dedup_db_config, dedup_threshold)

    def _initialize_pipeline(self, config_path: Optional[str] = None):
        """Initialize the post-processing pipeline."""
        try:
            if config_path:
                # Load from specified config
                self.pipeline = create_pipeline_from_config(config_path)
            else:
                # Try default config location
                default_config = Path(__file__).parent / 'config' / 'pipeline_config.json'
                if default_config.exists():
                    self.pipeline = create_pipeline_from_config(str(default_config))
                else:
                    # Create default pipeline programmatically
                    self.pipeline = PipelineOrchestrator([
                        ConfidenceFilterModule({'entity_threshold': 0.70, 'relationship_threshold': 0.80}),
                        DocumentLevelDeduplicator(),
                        CanonicalResolverModule(),
                        EntityQualityFilterModule(),
                        ListSplitterModule(),
                        OntologyNormalizerModule()
                    ])

            self.logger.info(f"Pipeline initialized with {len(self.pipeline)} modules")
        except Exception as e:
            self.logger.warning(f"Failed to initialize pipeline: {e}. Falling back to legacy controls.")
            self.use_pipeline = False
            self._initialize_legacy_controls()

    def _initialize_legacy_controls(self):
        """Initialize legacy quality control filters."""
        if not HAS_QUALITY_CONTROLS:
            self.logger.warning("Quality control modules not available")
            self.enable_quality_controls = False
            return

        try:
            self.entity_filter = EntityQualityFilter(FilterConfig())
            self.canonical_resolver = CanonicalResolver()

            # Initialize confidence filter from config
            config_path = Path(__file__).parent / 'config' / 'quality_config.json'
            if config_path.exists():
                with open(config_path) as f:
                    config = json.load(f)
                conf_thresholds = config.get('confidence_thresholds', {})
                self.confidence_filter = ConfidenceFilter(
                    entity_threshold=conf_thresholds.get('entity_min_confidence', 0.70),
                    relationship_threshold=conf_thresholds.get('relationship_min_confidence', 0.80),
                    allow_null=conf_thresholds.get('allow_null_confidence', True),
                    strict_mode=conf_thresholds.get('strict_mode', False)
                )
                self.logger.info("Legacy quality controls initialized: EntityQualityFilter + CanonicalResolver + ConfidenceFilter")
            else:
                # Use default confidence filter settings
                self.confidence_filter = ConfidenceFilter()
                self.logger.info("Legacy quality controls initialized with defaults")
        except Exception as e:
            self.logger.warning(f"Failed to initialize legacy quality controls: {e}")
            self.enable_quality_controls = False

    def _initialize_entity_resolver(
        self,
        db_config: Dict[str, Any] = None,
        threshold: float = 0.95
    ):
        """
        Initialize entity resolver for pgvector-based deduplication.

        Args:
            db_config: Database connection config (defaults to env vars)
            threshold: Similarity threshold for semantic matching
        """
        if not HAS_ENTITY_RESOLVER:
            self.logger.warning("EntityResolver not available. Deduplication disabled.")
            self.enable_deduplication = False
            return

        try:
            # Build database config from env vars if not provided
            if db_config is None:
                db_config = {
                    "host": os.getenv("POSTGRES_HOST", "localhost"),
                    "port": int(os.getenv("POSTGRES_PORT", 5433)),
                    "database": os.getenv("POSTGRES_DB", "eliza"),
                    "user": os.getenv("POSTGRES_USER", "postgres"),
                    "password": os.getenv("POSTGRES_PASSWORD", "postgres")
                }

            self.entity_resolver = EntityResolver(
                db_config=db_config,
                fuzzy_threshold=threshold
            )
            self.logger.info(
                f"EntityResolver initialized (threshold: {threshold}, "
                f"db: {db_config.get('host')}:{db_config.get('port')}/{db_config.get('database')})"
            )
        except Exception as e:
            self.logger.warning(f"Failed to initialize EntityResolver: {e}")
            self.enable_deduplication = False
            self.entity_resolver = None

    def _initialize_graph(self) -> Graph:
        """Initialize RDF graph with appropriate store"""

        if self.store_type == "memory":
            # Simple in-memory graph
            return Graph()

        elif self.store_type == "postgresql":
            # PostgreSQL-backed graph (requires rdflib-postgresql)
            try:
                from rdflib_postgresql import PostgreSQLStore
                store = PostgreSQLStore(
                    host=self.store_config.get("host", "localhost"),
                    port=self.store_config.get("port", 5432),
                    database=self.store_config.get("database", "eliza"),
                    user=self.store_config.get("user", "postgres"),
                    password=self.store_config.get("password", "postgres")
                )
                g = Graph(store=store)
                g.open(create=True)
                return g
            except ImportError:
                self.logger.warning("rdflib-postgresql not installed, falling back to memory store")
                return Graph()

        elif self.store_type == "sparql":
            # SPARQL endpoint (e.g., Blazegraph, Fuseki)
            store = sparqlstore.SPARQLUpdateStore()
            store.open((
                self.store_config.get("query_endpoint", "http://localhost:9999/blazegraph/sparql"),
                self.store_config.get("update_endpoint", "http://localhost:9999/blazegraph/sparql")
            ))
            return Graph(store=store)

        else:
            return Graph()

    def _bind_namespaces(self):
        """Bind namespaces to prefixes for cleaner serialization"""
        self.graph.bind("regen", self.REGEN)
        self.graph.bind("koi", self.KOI)
        self.graph.bind("prov", self.PROV)
        self.graph.bind("schema", self.SCHEMA)
        self.graph.bind("dc", self.DC)
        self.graph.bind("discourse", self.DISCOURSE)
        self.graph.bind("twitter", self.TWITTER)
        self.graph.bind("medium", self.MEDIUM)
        self.graph.bind("github", self.GITHUB)

    def integrate_document(
        self,
        document: Dict[str, Any],
        extraction_metadata: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Integrate a document with extracted metadata into knowledge graph

        Args:
            document: Document with content and metadata
            extraction_metadata: LLM extraction results

        Returns:
            Integration report with created entities and relationships
        """

        report = {
            "document_uri": None,
            "entities_created": [],
            "entities_blocked": 0,
            "entities_canonicalized": 0,
            "relationships_created": [],
            "triples_added": 0
        }

        try:
            # Create document URI
            doc_rid = document.get("rid") or self._generate_rid(document)
            doc_uri = self.KOI[doc_rid]
            report["document_uri"] = str(doc_uri)

            # Add document as discourse element
            source_type = document.get("source_type", "unknown")
            self._add_document_triples(doc_uri, document, source_type)

            # Process extracted entities
            if extraction_metadata:
                initial_blocked = self.quality_stats['blocked_by_filter']
                initial_resolved = self.quality_stats['resolved_to_canonical']

                entities = extraction_metadata.get("extracted_entities", [])
                for entity in entities:
                    entity_uri = self._add_entity(entity, doc_uri)
                    if entity_uri:  # Only add if not blocked by quality controls
                        report["entities_created"].append(str(entity_uri))

                # Track quality control actions for this document
                report["entities_blocked"] = self.quality_stats['blocked_by_filter'] - initial_blocked
                report["entities_canonicalized"] = self.quality_stats['resolved_to_canonical'] - initial_resolved

                # Process extracted relationships
                relationships = extraction_metadata.get("extracted_relationships", [])
                for rel in relationships:
                    rel_triple = self._add_relationship(rel, doc_uri)
                    if rel_triple:
                        report["relationships_created"].append(rel_triple)

                # Process claims and evidence
                self._process_discourse_elements(doc_uri, extraction_metadata)

            # Count triples added
            report["triples_added"] = len(self.graph)

            self.logger.info(f"Integrated document {doc_rid}: {report['triples_added']} triples")

        except Exception as e:
            self.logger.error(f"Integration failed: {e}")

        return report

    def _generate_rid(self, document: Dict[str, Any]) -> str:
        """Generate RID for document if not provided"""
        content = document.get("content", "")
        url = document.get("url", "")
        hash_input = f"{url}:{content[:100]}"
        return f"koi:doc:{hashlib.sha256(hash_input.encode()).hexdigest()[:16]}"

    def _add_document_triples(self, doc_uri: URIRef, document: Dict[str, Any], source_type: str):
        """Add RDF triples for document"""

        # Type assertion based on source
        if source_type == "discourse":
            self.graph.add((doc_uri, RDF.type, self.DISCOURSE.Post))
        elif source_type == "twitter":
            self.graph.add((doc_uri, RDF.type, self.TWITTER.Tweet))
        elif source_type == "medium":
            self.graph.add((doc_uri, RDF.type, self.MEDIUM.Article))
        elif source_type == "github":
            self.graph.add((doc_uri, RDF.type, self.GITHUB.Issue))
        else:
            self.graph.add((doc_uri, RDF.type, self.REGEN.DiscourseElement))

        # Add basic properties
        if document.get("title"):
            self.graph.add((doc_uri, self.DC.title, Literal(document["title"])))

        if document.get("url"):
            self.graph.add((doc_uri, self.SCHEMA.url, Literal(document["url"])))

        if document.get("content"):
            # Store truncated content
            content = document["content"][:1000] + "..." if len(document["content"]) > 1000 else document["content"]
            self.graph.add((doc_uri, self.SCHEMA.text, Literal(content)))

        # Add metadata
        metadata = document.get("metadata", {})
        if metadata.get("author"):
            author_uri = self._get_or_create_author(metadata["author"])
            self.graph.add((doc_uri, self.REGEN.wasAttestedBy, author_uri))

        if metadata.get("published_at"):
            self.graph.add((doc_uri, self.SCHEMA.datePublished, Literal(metadata["published_at"], datatype=XSD.dateTime)))

        if metadata.get("tags"):
            for tag in metadata["tags"]:
                self.graph.add((doc_uri, self.SCHEMA.keywords, Literal(tag)))

    def _get_or_create_author(self, author_name: str) -> URIRef:
        """Get or create author entity"""

        # Check cache
        cache_key = f"author:{author_name}"
        if cache_key in self.entity_cache:
            return self.entity_cache[cache_key]

        # Create author URI
        author_id = hashlib.sha256(author_name.encode()).hexdigest()[:16]
        author_uri = self.KOI[f"agent:{author_id}"]

        # Add author triples
        self.graph.add((author_uri, RDF.type, self.REGEN.HumanActor))
        self.graph.add((author_uri, self.SCHEMA.name, Literal(author_name)))

        # Cache and return
        self.entity_cache[cache_key] = author_uri
        return author_uri

    def process_entity(self, entity_name: str, entity_type: str, confidence: Optional[float] = None, **kwargs) -> Optional[Tuple[str, str]]:
        """
        Process entity with quality controls before graph insertion.

        Uses pipeline if available, otherwise falls back to legacy filters.

        Pipeline mode applies modules in sequence:
        1. ConfidenceFilter - blocks low-confidence entities
        2. CanonicalResolver - normalizes known aliases
        3. EntityQualityFilter - blocks low-quality patterns
        4. ListSplitter - splits list-like entities (returns first item)
        5. OntologyNormalizer - normalizes entity types

        Legacy mode applies (in order):
        1. Confidence filter (early exit for low-confidence entities)
        2. Canonical resolution (normalizes known aliases - bypasses pattern filter)
        3. Quality filter (blocks low-quality entities if not known)

        Args:
            entity_name: The entity name to process
            entity_type: The entity type
            confidence: Optional confidence score (0.0-1.0) from extraction
            **kwargs: Additional entity properties

        Returns:
            Tuple of (processed_name, processed_type) if valid, None if blocked
        """
        self.quality_stats['total_extracted'] += 1

        # Use pipeline if available
        if self.use_pipeline and self.pipeline:
            return self._process_entity_with_pipeline(entity_name, entity_type, confidence)

        # Legacy mode
        return self._process_entity_legacy(entity_name, entity_type, confidence)

    def _process_entity_with_pipeline(self, entity_name: str, entity_type: str, confidence: Optional[float]) -> Optional[Tuple[str, str]]:
        """Process entity using the pipeline framework."""
        # Create pipeline entity
        entity = PipelineEntity(
            name=entity_name,
            type=entity_type,
            confidence=confidence
        )

        # Create context with single entity
        context = ProcessingContext(entities=[entity])

        # Run pipeline
        result = self.pipeline.process(context)
        self.quality_stats['pipeline_processed'] += 1

        # Check if entity was blocked
        if len(result.entities) == 0:
            self.quality_stats['blocked_by_filter'] += 1
            self.logger.debug(f"Pipeline blocked entity '{entity_name}' ({entity_type})")
            return None

        # Get processed entity (may have been split or modified)
        processed = result.entities[0]

        # Track modifications
        if processed.name != entity_name:
            self.quality_stats['resolved_to_canonical'] += 1
            self.logger.debug(f"Pipeline modified '{entity_name}' -> '{processed.name}'")

        return processed.name, processed.type

    def _process_entity_legacy(self, entity_name: str, entity_type: str, confidence: Optional[float]) -> Optional[Tuple[str, str]]:
        """Process entity using legacy filter classes."""
        # Step 1: Confidence filter (early exit for performance)
        if self.enable_quality_controls and self.confidence_filter:
            is_valid, reason = self.confidence_filter.filter_entity(entity_name, entity_type, confidence)
            if not is_valid:
                self.quality_stats['blocked_by_confidence'] += 1
                self.logger.debug(f"Blocked low-confidence entity '{entity_name}' ({entity_type}): {reason}")
                return None

        # Step 2: Check canonical resolver (known entities bypass pattern filter)
        processed_name = entity_name
        if self.enable_quality_controls and self.canonical_resolver:
            if self.canonical_resolver.is_known_entity(entity_name):
                canonical_name, was_resolved = self.canonical_resolver.resolve(entity_name, entity_type)
                if was_resolved:
                    self.quality_stats['resolved_to_canonical'] += 1
                    self.logger.debug(f"Resolved '{entity_name}' -> '{canonical_name}' (known entity)")
                    return canonical_name, entity_type
                else:
                    # Known entity but not resolved to different name - still trusted
                    return entity_name, entity_type

        # Step 3: Pattern-based quality filter (only for unknown entities)
        if self.enable_quality_controls and self.entity_filter:
            is_valid, reasons = self.entity_filter.filter_with_reasons(entity_name, entity_type)
            if not is_valid:
                self.quality_stats['blocked_by_filter'] += 1
                self.logger.debug(f"Blocked entity '{entity_name}': {', '.join(reasons)}")
                return None

        return processed_name, entity_type

    def process_entities_batch(self, entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Process a batch of entities through the pipeline.

        This method is more efficient for batch processing as it runs
        all entities through the pipeline at once.

        Args:
            entities: List of entity dictionaries with 'name', 'type', 'confidence'

        Returns:
            List of valid entities after pipeline processing
        """
        if not self.use_pipeline or not self.pipeline:
            # Fall back to individual processing
            results = []
            for e in entities:
                result = self.process_entity(e.get('name', ''), e.get('type', ''), e.get('confidence'))
                if result:
                    name, etype = result
                    results.append({'name': name, 'type': etype, 'confidence': e.get('confidence')})
            return results

        # Convert to pipeline entities
        pipeline_entities = [
            PipelineEntity(
                name=e.get('name', ''),
                type=e.get('type', ''),
                confidence=e.get('confidence'),
                metadata=e.get('metadata', {})
            )
            for e in entities
        ]

        # Create context and process
        context = ProcessingContext(entities=pipeline_entities)
        result = self.pipeline.process(context)

        # Update stats
        self.quality_stats['total_extracted'] += len(entities)
        self.quality_stats['pipeline_processed'] += len(entities)
        self.quality_stats['blocked_by_filter'] += len(result.blocked_entities)

        # Convert back to dictionaries
        return [
            {
                'name': e.name,
                'type': e.type,
                'confidence': e.confidence,
                'metadata': e.metadata
            }
            for e in result.entities
        ]

    def _add_entity(self, entity: Dict[str, Any], doc_uri: URIRef) -> Optional[URIRef]:
        """Add entity to graph with quality controls"""

        entity_name = entity.get("name", "unknown")
        entity_type = entity.get("type", "regen:Entity")
        confidence = entity.get("confidence")  # May be None

        # Apply quality controls (including confidence filtering)
        result = self.process_entity(entity_name, entity_type, confidence=confidence)
        if result is None:
            return None  # Entity was blocked

        processed_name, processed_type = result

        # Generate entity URI using processed name
        entity_id = hashlib.sha256(f"{processed_type}:{processed_name}".encode()).hexdigest()[:16]
        entity_uri = self.KOI[f"entity:{entity_id}"]

        # Add type assertion
        type_uri = self._parse_type_uri(processed_type)
        self.graph.add((entity_uri, RDF.type, type_uri))

        # Add name (use processed/canonical name)
        self.graph.add((entity_uri, self.SCHEMA.name, Literal(processed_name)))

        # Add properties
        for key, value in entity.get("properties", {}).items():
            if isinstance(value, str):
                self.graph.add((entity_uri, self.SCHEMA[key], Literal(value)))

        # Link to source document
        self.graph.add((entity_uri, self.PROV.wasDerivedFrom, doc_uri))

        self.quality_stats['inserted_to_graph'] += 1
        return entity_uri

    def _add_relationship(self, rel: Dict[str, Any], doc_uri: URIRef) -> Optional[Tuple]:
        """Add relationship to graph, respecting quality controls"""

        try:
            subject_name = rel.get("subject")
            predicate = rel.get("predicate")
            object_name = rel.get("object")
            confidence = rel.get("confidence")  # May be None

            if not all([subject_name, predicate, object_name]):
                return None

            # Check relationship confidence first (early exit)
            if self.enable_quality_controls and self.confidence_filter:
                is_valid, reason = self.confidence_filter.filter_relationship(
                    subject_name, predicate, object_name, confidence
                )
                if not is_valid:
                    self.logger.debug(f"Blocked low-confidence relationship: ({subject_name})-[{predicate}]->({object_name}): {reason}")
                    return None

            # Get or create subject and object URIs (may be blocked by quality controls)
            subject_uri = self._get_or_create_entity_by_name(subject_name)
            object_uri = self._get_or_create_entity_by_name(object_name)

            # Skip relationship if either entity was blocked
            if subject_uri is None or object_uri is None:
                self.logger.debug(f"Skipping relationship: entity blocked by quality controls")
                return None

            # Parse predicate
            pred_uri = self._parse_type_uri(predicate)

            # Add triple
            self.graph.add((subject_uri, pred_uri, object_uri))

            # Add provenance
            self.graph.add((subject_uri, self.PROV.wasDerivedFrom, doc_uri))

            return (str(subject_uri), str(pred_uri), str(object_uri))

        except Exception as e:
            self.logger.warning(f"Failed to add relationship: {e}")
            return None

    def _get_or_create_entity_by_name(self, name: str, entity_type: str = "ENTITY") -> Optional[URIRef]:
        """
        Get or create entity by name, applying quality controls and deduplication.

        Uses three-tier waterfall if deduplication is enabled:
        1. Tier 1: Exact match (B-Tree, microseconds)
        2. Tier 2: Semantic match (pgvector HNSW, milliseconds)
        3. Tier 3: Create new (deterministic URI)

        Args:
            name: Entity name
            entity_type: Entity type (defaults to "ENTITY")

        Returns:
            URIRef for the entity, or None if blocked by quality controls
        """
        # Apply quality controls to get processed name
        result = self.process_entity(name, entity_type)
        if result is None:
            return None  # Entity was blocked

        processed_name, processed_type = result

        # Use EntityResolver for deduplication if available
        if self.enable_deduplication and self.entity_resolver:
            try:
                dedup_result = self.entity_resolver.get_or_create_entity(
                    processed_name,
                    processed_type
                )

                # Track deduplication stats
                if dedup_result["match_method"] == "tier1_exact":
                    self.quality_stats['dedup_exact_hits'] += 1
                elif dedup_result["match_method"] == "tier2_semantic":
                    self.quality_stats['dedup_semantic_hits'] += 1
                elif dedup_result["match_method"] == "tier3_new":
                    self.quality_stats['dedup_new_entities'] += 1

                # Log deduplication info
                if dedup_result["matched"]:
                    self.logger.debug(
                        f"Dedup: '{name}' -> '{dedup_result['entity_text']}' "
                        f"via {dedup_result['match_method']} "
                        f"(score: {dedup_result['match_score']:.3f})"
                    )

                # Use the URI from entity_resolver
                entity_uri = URIRef(dedup_result["uri"])

                # Sync to local graph (self-healing)
                if (entity_uri, None, None) not in self.graph:
                    self._sync_entity_to_graph(
                        entity_uri,
                        dedup_result["entity_text"],
                        processed_type
                    )

                # Cache for future in-memory lookups
                cache_key = f"entity:{processed_name}"
                self.entity_cache[cache_key] = entity_uri

                return entity_uri

            except Exception as e:
                self.logger.warning(f"EntityResolver failed, falling back: {e}")
                # Fall through to legacy method

        # Legacy method (no deduplication)
        cache_key = f"entity:{processed_name}"
        if cache_key in self.entity_cache:
            return self.entity_cache[cache_key]

        entity_id = hashlib.sha256(processed_name.encode()).hexdigest()[:16]
        entity_uri = self.KOI[f"entity:{entity_id}"]

        # Add basic triple if entity doesn't exist
        if (entity_uri, None, None) not in self.graph:
            self.graph.add((entity_uri, RDF.type, self.REGEN.Entity))
            self.graph.add((entity_uri, self.SCHEMA.name, Literal(processed_name)))
            self.quality_stats['inserted_to_graph'] += 1

        self.entity_cache[cache_key] = entity_uri
        return entity_uri

    def _sync_entity_to_graph(self, uri: URIRef, name: str, entity_type: str):
        """
        Sync entity to local RDF graph.

        This is a self-healing mechanism - if entity exists in registry
        but not in local graph, add it.

        Args:
            uri: Entity URI
            name: Entity name
            entity_type: Entity type
        """
        # Add type assertion
        type_uri = self._parse_type_uri(f"regen:{entity_type}")
        self.graph.add((uri, RDF.type, type_uri))

        # Add name
        self.graph.add((uri, self.SCHEMA.name, Literal(name)))

        self.quality_stats['inserted_to_graph'] += 1

    def _process_discourse_elements(self, doc_uri: URIRef, metadata: Dict[str, Any]):
        """Process claims, evidence, and questions"""

        # Process claims
        for claim in metadata.get("claims", []):
            claim_id = hashlib.sha256(claim.encode()).hexdigest()[:16]
            claim_uri = self.KOI[f"claim:{claim_id}"]

            self.graph.add((claim_uri, RDF.type, self.REGEN.Claim))
            self.graph.add((claim_uri, self.SCHEMA.text, Literal(claim)))
            self.graph.add((claim_uri, self.PROV.wasDerivedFrom, doc_uri))

        # Process evidence
        for evidence in metadata.get("evidence", []):
            evidence_id = hashlib.sha256(evidence.encode()).hexdigest()[:16]
            evidence_uri = self.KOI[f"evidence:{evidence_id}"]

            self.graph.add((evidence_uri, RDF.type, self.REGEN.Evidence))
            self.graph.add((evidence_uri, self.SCHEMA.text, Literal(evidence)))
            self.graph.add((evidence_uri, self.PROV.wasDerivedFrom, doc_uri))

        # Add essence alignment
        for essence in metadata.get("essence_alignment", []):
            self.graph.add((doc_uri, self.REGEN.alignsWith, Literal(essence)))

    def _parse_type_uri(self, type_str: str) -> URIRef:
        """Parse type string to URIRef"""

        if ":" in type_str:
            prefix, local = type_str.split(":", 1)
            if prefix == "regen":
                return self.REGEN[local]
            elif prefix == "discourse":
                return self.DISCOURSE[local]
            elif prefix == "twitter":
                return self.TWITTER[local]
            elif prefix == "medium":
                return self.MEDIUM[local]
            elif prefix == "github":
                return self.GITHUB[local]

        # Default to REGEN namespace
        return self.REGEN[type_str]

    def query(self, sparql_query: str) -> List[Dict[str, Any]]:
        """Execute SPARQL query on knowledge graph"""

        results = []
        try:
            qres = self.graph.query(sparql_query)
            for row in qres:
                results.append({
                    str(var): str(val) for var, val in zip(qres.vars, row)
                })
        except Exception as e:
            self.logger.error(f"Query failed: {e}")

        return results

    def export_graph(self, format: str = "turtle", file_path: Optional[str] = None) -> str:
        """Export graph in specified format"""

        serialized = self.graph.serialize(format=format)

        if file_path:
            Path(file_path).write_text(serialized)
            self.logger.info(f"Graph exported to {file_path}")

        return serialized

    def get_statistics(self) -> Dict[str, int]:
        """Get statistics about the knowledge graph"""

        stats = {
            "total_triples": len(self.graph),
            "total_subjects": len(set(self.graph.subjects())),
            "total_predicates": len(set(self.graph.predicates())),
            "total_objects": len(set(self.graph.objects())),
        }

        # Count entities by type
        for entity_type in [self.REGEN.HumanActor, self.REGEN.Claim,
                           self.REGEN.Evidence, self.REGEN.Question]:
            count = len(list(self.graph.subjects(RDF.type, entity_type)))
            stats[f"count_{entity_type.split('#')[-1]}"] = count

        return stats

    def get_quality_stats(self) -> Dict[str, Any]:
        """Get quality control statistics"""
        stats = self.quality_stats.copy()

        # Calculate total blocked
        stats['blocked_total'] = stats['blocked_by_filter'] + stats['blocked_by_confidence']

        # Calculate rates
        if stats['total_extracted'] > 0:
            stats['block_rate'] = round(stats['blocked_total'] / stats['total_extracted'] * 100, 2)
            stats['confidence_block_rate'] = round(stats['blocked_by_confidence'] / stats['total_extracted'] * 100, 2)
            stats['pattern_block_rate'] = round(stats['blocked_by_filter'] / stats['total_extracted'] * 100, 2)
            stats['resolution_rate'] = round(stats['resolved_to_canonical'] / stats['total_extracted'] * 100, 2)
            stats['pass_rate'] = round(stats['inserted_to_graph'] / stats['total_extracted'] * 100, 2)
        else:
            stats['block_rate'] = 0
            stats['confidence_block_rate'] = 0
            stats['pattern_block_rate'] = 0
            stats['resolution_rate'] = 0
            stats['pass_rate'] = 0

        # Add mode indicator
        stats['mode'] = 'pipeline' if self.use_pipeline else 'legacy'

        # Include pipeline stats if using pipeline
        if self.use_pipeline and self.pipeline:
            stats['pipeline_statistics'] = self.pipeline.get_statistics()

        # Include filter stats if available (legacy mode)
        if self.entity_filter:
            stats['filter_breakdown'] = self.entity_filter.get_stats()

        # Include resolver stats if available (legacy mode)
        if self.canonical_resolver:
            stats['resolver_breakdown'] = self.canonical_resolver.get_stats()

        # Include confidence filter stats if available (legacy mode)
        if self.confidence_filter:
            stats['confidence_breakdown'] = self.confidence_filter.get_stats()

        # Include deduplication stats
        stats['deduplication_enabled'] = self.enable_deduplication
        if self.enable_deduplication and self.entity_resolver:
            dedup_total = (
                stats.get('dedup_exact_hits', 0) +
                stats.get('dedup_semantic_hits', 0) +
                stats.get('dedup_new_entities', 0)
            )
            if dedup_total > 0:
                stats['dedup_exact_rate'] = round(stats.get('dedup_exact_hits', 0) / dedup_total * 100, 2)
                stats['dedup_semantic_rate'] = round(stats.get('dedup_semantic_hits', 0) / dedup_total * 100, 2)
                stats['dedup_new_rate'] = round(stats.get('dedup_new_entities', 0) / dedup_total * 100, 2)
            stats['entity_resolver_stats'] = self.entity_resolver.get_stats()

        return stats

    def reset_quality_stats(self):
        """Reset quality control statistics"""
        self.quality_stats = {
            'total_extracted': 0,
            'blocked_by_filter': 0,
            'blocked_by_confidence': 0,
            'resolved_to_canonical': 0,
            'inserted_to_graph': 0,
            'pipeline_processed': 0,
            'dedup_exact_hits': 0,
            'dedup_semantic_hits': 0,
            'dedup_new_entities': 0
        }
        if self.pipeline:
            self.pipeline.reset()
        if self.entity_filter:
            self.entity_filter.reset_stats()
        if self.canonical_resolver:
            self.canonical_resolver.reset_stats()
        if self.confidence_filter:
            self.confidence_filter.reset_stats()
        if self.entity_resolver:
            self.entity_resolver.reset_stats()


# Example usage
async def main():
    """Example integration"""

    # Initialize knowledge graph
    kg = KnowledgeGraphIntegrator(store_type="memory")

    # Example document with extracted metadata
    document = {
        "rid": "orn:discourse:post:123",
        "title": "Carbon Credit Methodology Discussion",
        "url": "https://forum.regen.network/t/methodology/123",
        "content": "We should consider the Verra VM0042 standard...",
        "source_type": "discourse",
        "metadata": {
            "author": "Alice",
            "published_at": "2024-01-15T10:00:00Z",
            "tags": ["methodology", "carbon"],
        }
    }

    extraction = {
        "extracted_entities": [
            {"type": "regen:HumanActor", "name": "Alice", "properties": {"role": "researcher"}},
            {"type": "regen:Claim", "name": "Verra VM0042 is better", "properties": {}}
        ],
        "extracted_relationships": [
            {"subject": "Alice", "predicate": "regen:supports", "object": "Verra VM0042 is better"}
        ],
        "claims": ["Verra VM0042 captures ecosystem services better"],
        "evidence": ["30% increase in credit value after switching"],
        "essence_alignment": ["regenerative", "ecological"]
    }

    # Integrate into graph
    report = kg.integrate_document(document, extraction)
    print(json.dumps(report, indent=2))

    # Query the graph
    query = """
    SELECT ?subject ?predicate ?object
    WHERE {
        ?subject ?predicate ?object .
        FILTER(STRSTARTS(STR(?subject), "https://regen.network/koi"))
    }
    LIMIT 10
    """
    results = kg.query(query)
    print("\nQuery Results:")
    for r in results:
        print(r)

    # Get statistics
    stats = kg.get_statistics()
    print("\nGraph Statistics:")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
