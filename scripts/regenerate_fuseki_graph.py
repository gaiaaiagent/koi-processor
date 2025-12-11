#!/usr/bin/env python3
"""
Regenerate Fuseki Graph from Entity Registry
Created: 2025-12-11
Purpose: Export entity_registry (PostgreSQL) to RDF and reload into Fuseki

This script:
1. Backs up existing Fuseki graph
2. Exports entity_registry to RDF (Turtle format)
3. Clears Fuseki /koi dataset
4. Loads new RDF graph into Fuseki
5. Validates triple count

Requirements:
- rdflib
- SPARQLWrapper
- psycopg2
"""

import os
import sys
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict

import psycopg2
from psycopg2.extras import RealDictCursor
from rdflib import Graph, Namespace, URIRef, Literal, RDF, RDFS
from SPARQLWrapper import SPARQLWrapper, POST, DIGEST

# Namespaces
KOI = Namespace("http://regen.network/koi#")
ENTITY = Namespace("http://regen.network/koi/entity/")
REL = Namespace("http://regen.network/koi/relationship/")


class FusekiGraphRegenerator:
    """Regenerate Fuseki graph from PostgreSQL entity_registry."""

    def __init__(self):
        self.db_config = {
            "host": os.getenv("POSTGRES_HOST", "localhost"),
            "port": int(os.getenv("POSTGRES_PORT", 5433)),
            "database": os.getenv("POSTGRES_DB", "eliza"),
            "user": os.getenv("POSTGRES_USER", "postgres"),
            "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
        }

        self.fuseki_endpoint = "http://localhost:3030/koi"
        self.fuseki_update_endpoint = f"{self.fuseki_endpoint}/update"
        self.fuseki_data_endpoint = f"{self.fuseki_endpoint}/data"

        self.graph = Graph()
        self.graph.bind("koi", KOI)
        self.graph.bind("entity", ENTITY)
        self.graph.bind("rel", REL)

        self.stats = {
            "entities_exported": 0,
            "triples_created": 0,
            "fuseki_cleared": False,
            "fuseki_loaded": False,
        }

    def connect_db(self):
        """Connect to PostgreSQL."""
        return psycopg2.connect(**self.db_config)

    def export_entities_to_rdf(self) -> Graph:
        """
        Export entity_registry to RDF graph.

        Creates triples for:
        - Entity URI
        - Entity text (rdfs:label)
        - Entity type (rdf:type)
        - Occurrence count (koi:occurrenceCount)
        - First/last seen (koi:firstSeen, koi:lastSeen)
        """
        print("\n" + "=" * 80)
        print("STEP 1: Export Entity Registry to RDF")
        print("=" * 80)

        conn = self.connect_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT
                id,
                fuseki_uri,
                entity_text,
                entity_type,
                occurrence_count,
                first_seen_at,
                last_seen_at
            FROM entity_registry
            ORDER BY id;
        """)

        entities = cursor.fetchall()
        conn.close()

        print(f"\n📊 Exporting {len(entities)} entities from PostgreSQL...")

        for entity in entities:
            # Create entity URI
            if entity["fuseki_uri"]:
                entity_uri = URIRef(entity["fuseki_uri"])
            else:
                # Fallback: create URI from ID
                entity_uri = ENTITY[f"entity_{entity['id']}"]

            # Add triples
            # Label
            self.graph.add((entity_uri, RDFS.label, Literal(entity["entity_text"])))

            # Type
            entity_type = entity["entity_type"]
            type_uri = KOI[f"{entity_type}Entity"]
            self.graph.add((entity_uri, RDF.type, type_uri))

            # Occurrence count
            self.graph.add((entity_uri, KOI.occurrenceCount, Literal(entity["occurrence_count"])))

            # Timestamps
            if entity["first_seen_at"]:
                self.graph.add((entity_uri, KOI.firstSeen, Literal(entity["first_seen_at"])))
            if entity["last_seen_at"]:
                self.graph.add((entity_uri, KOI.lastSeen, Literal(entity["last_seen_at"])))

            self.stats["entities_exported"] += 1

        self.stats["triples_created"] = len(self.graph)

        print(f"✓ Exported {self.stats['entities_exported']} entities")
        print(f"✓ Created {self.stats['triples_created']} triples")

        return self.graph

    def save_rdf_to_file(self, output_path: Path):
        """Save RDF graph to Turtle file."""
        print(f"\n💾 Saving RDF graph to: {output_path}")

        self.graph.serialize(destination=str(output_path), format="turtle")

        file_size = output_path.stat().st_size / (1024 * 1024)  # MB
        print(f"✓ Saved {file_size:.2f} MB")

    def clear_fuseki_dataset(self):
        """Clear existing Fuseki /koi dataset."""
        print("\n" + "=" * 80)
        print("STEP 2: Clear Existing Fuseki Dataset")
        print("=" * 80)

        sparql = SPARQLWrapper(self.fuseki_update_endpoint)
        sparql.setMethod(POST)

        # SPARQL UPDATE to delete all triples
        delete_query = "DELETE WHERE { ?s ?p ?o }"

        try:
            sparql.setQuery(delete_query)
            sparql.query()

            self.stats["fuseki_cleared"] = True
            print("✓ Fuseki /koi dataset cleared")

        except Exception as e:
            print(f"✗ Failed to clear Fuseki: {e}")
            raise

    def load_rdf_to_fuseki(self, rdf_file: Path):
        """Load RDF file into Fuseki dataset."""
        print("\n" + "=" * 80)
        print("STEP 3: Load New RDF Graph to Fuseki")
        print("=" * 80)

        import requests

        try:
            # Read RDF file
            with open(rdf_file, "rb") as f:
                rdf_data = f.read()

            # POST to Fuseki data endpoint
            headers = {"Content-Type": "text/turtle"}
            response = requests.post(
                self.fuseki_data_endpoint,
                data=rdf_data,
                headers=headers
            )

            if response.status_code in [200, 201, 204]:
                self.stats["fuseki_loaded"] = True
                print(f"✓ Loaded RDF graph to Fuseki (HTTP {response.status_code})")
            else:
                print(f"✗ Failed to load RDF: HTTP {response.status_code}")
                print(f"Response: {response.text}")
                raise Exception(f"Fuseki load failed: {response.status_code}")

        except Exception as e:
            print(f"✗ Failed to load Fuseki: {e}")
            raise

    def validate_fuseki_graph(self) -> int:
        """Validate Fuseki graph by counting triples."""
        print("\n" + "=" * 80)
        print("STEP 4: Validate Fuseki Graph")
        print("=" * 80)

        sparql = SPARQLWrapper(f"{self.fuseki_endpoint}/sparql")

        count_query = "SELECT (COUNT(*) as ?count) WHERE { ?s ?p ?o }"

        try:
            sparql.setQuery(count_query)
            sparql.setReturnFormat("json")
            results = sparql.query().convert()

            triple_count = int(results["results"]["bindings"][0]["count"]["value"])

            print(f"\n📊 Fuseki triple count: {triple_count:,}")
            print(f"📊 Expected (from export): {self.stats['triples_created']:,}")

            if triple_count == self.stats["triples_created"]:
                print("✓ Triple counts match!")
            else:
                print(f"⚠️  Triple count mismatch (diff: {abs(triple_count - self.stats['triples_created'])})")

            return triple_count

        except Exception as e:
            print(f"✗ Failed to validate Fuseki: {e}")
            raise

    def run(self):
        """Main execution flow."""
        print("=" * 80)
        print("FUSEKI GRAPH REGENERATION")
        print("=" * 80)
        print(f"\nStarted: {datetime.now().isoformat()}")

        output_dir = Path("/opt/projects/koi-processor/exports/fuseki_regen_$(date +%Y%m%d)")
        output_dir = Path(f"/opt/projects/koi-processor/exports/fuseki_regen_{datetime.now().strftime('%Y%m%d')}")
        output_dir.mkdir(parents=True, exist_ok=True)

        rdf_file = output_dir / "entity_registry.ttl"

        try:
            # Step 1: Export to RDF
            self.export_entities_to_rdf()

            # Save to file
            self.save_rdf_to_file(rdf_file)

            # Step 2: Clear Fuseki
            self.clear_fuseki_dataset()

            # Step 3: Load new graph
            self.load_rdf_to_fuseki(rdf_file)

            # Step 4: Validate
            triple_count = self.validate_fuseki_graph()

            # Summary
            print("\n" + "=" * 80)
            print("SUMMARY")
            print("=" * 80)
            print(f"\n✓ Entities exported: {self.stats['entities_exported']}")
            print(f"✓ Triples created: {self.stats['triples_created']:,}")
            print(f"✓ Fuseki cleared: {self.stats['fuseki_cleared']}")
            print(f"✓ Fuseki loaded: {self.stats['fuseki_loaded']}")
            print(f"✓ Fuseki triple count: {triple_count:,}")

            print(f"\n✓ RDF export saved: {rdf_file}")
            print(f"\nCompleted: {datetime.now().isoformat()}")

            return True

        except Exception as e:
            print(f"\n✗ Regeneration failed: {e}")
            return False


def main():
    regenerator = FusekiGraphRegenerator()
    success = regenerator.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
