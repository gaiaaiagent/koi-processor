"""
Knowledge Graph Integration Module for KOI
Connects extracted entities and relationships into unified RDF knowledge graph
"""

import json
import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
from pathlib import Path
import hashlib

try:
    from rdflib import Graph, Namespace, URIRef, Literal, RDF, RDFS, OWL, XSD
    from rdflib.plugins.stores import sparqlstore
    HAS_RDFLIB = True
except ImportError:
    print("Warning: rdflib not installed. Install with: pip install rdflib SPARQLWrapper")
    HAS_RDFLIB = False


class KnowledgeGraphIntegrator:
    """
    Integrates extracted entities and relationships into RDF knowledge graph
    """

    def __init__(
        self,
        store_type: str = "memory",  # memory, postgresql, or sparql
        store_config: Dict[str, Any] = None
    ):
        self.logger = logging.getLogger(__name__)
        self.store_type = store_type
        self.store_config = store_config or {}

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
                entities = extraction_metadata.get("extracted_entities", [])
                for entity in entities:
                    entity_uri = self._add_entity(entity, doc_uri)
                    report["entities_created"].append(str(entity_uri))

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

    def _add_entity(self, entity: Dict[str, Any], doc_uri: URIRef) -> URIRef:
        """Add entity to graph"""

        # Generate entity URI
        entity_name = entity.get("name", "unknown")
        entity_type = entity.get("type", "regen:Entity")
        entity_id = hashlib.sha256(f"{entity_type}:{entity_name}".encode()).hexdigest()[:16]
        entity_uri = self.KOI[f"entity:{entity_id}"]

        # Add type assertion
        type_uri = self._parse_type_uri(entity_type)
        self.graph.add((entity_uri, RDF.type, type_uri))

        # Add name
        self.graph.add((entity_uri, self.SCHEMA.name, Literal(entity_name)))

        # Add properties
        for key, value in entity.get("properties", {}).items():
            if isinstance(value, str):
                self.graph.add((entity_uri, self.SCHEMA[key], Literal(value)))

        # Link to source document
        self.graph.add((entity_uri, self.PROV.wasDerivedFrom, doc_uri))

        return entity_uri

    def _add_relationship(self, rel: Dict[str, Any], doc_uri: URIRef) -> Optional[Tuple]:
        """Add relationship to graph"""

        try:
            subject_name = rel.get("subject")
            predicate = rel.get("predicate")
            object_name = rel.get("object")

            if not all([subject_name, predicate, object_name]):
                return None

            # Get or create subject and object URIs
            subject_uri = self._get_or_create_entity_by_name(subject_name)
            object_uri = self._get_or_create_entity_by_name(object_name)

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

    def _get_or_create_entity_by_name(self, name: str) -> URIRef:
        """Get or create entity by name"""

        cache_key = f"entity:{name}"
        if cache_key in self.entity_cache:
            return self.entity_cache[cache_key]

        entity_id = hashlib.sha256(name.encode()).hexdigest()[:16]
        entity_uri = self.KOI[f"entity:{entity_id}"]

        # Add basic triple if entity doesn't exist
        if (entity_uri, None, None) not in self.graph:
            self.graph.add((entity_uri, RDF.type, self.REGEN.Entity))
            self.graph.add((entity_uri, self.SCHEMA.name, Literal(name)))

        self.entity_cache[cache_key] = entity_uri
        return entity_uri

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