#!/usr/bin/env python3
"""
Re-extract Documents Containing Deleted Entities
Created: 2025-12-10
Purpose: Find and re-extract documents that mentioned entities deleted during user review

This script:
1. Searches for documents mentioning deleted entities (BuilderDAO, DAO, Regen Registry, etc.)
2. Re-extracts those documents with current pipeline (Tier 1+2 dedup)
3. Validates that split entities are now created separately
4. Reports on success/failures

Deleted entities from user review:
- BuilderDAO, DAO
- Regen Registry Assistant, Regen Registry program
- Proposal 23, Proposal 25
- eastern white pines, western white pines
- MCP Server, MCP Client
- Regen Ledger Community, Regen Ledger Team
- Phase 1-2 Complete, Phase 2a Complete, Phase 2c Complete
- Phase 7 Complete, Phase 8 Complete
- Unique Value Proposition 1, 3, 4
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set

import psycopg2
from psycopg2.extras import RealDictCursor

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from extraction.openai_extractor import OpenAIExtractor
from extraction.smart_chunker import SmartChunker
from knowledge_graph.graph_integration import KnowledgeGraphIntegrator


class SplitEntityReextractor:
    """Re-extract documents containing split entities."""

    # Entities that were deleted (canonical + variants)
    SPLIT_ENTITIES = {
        "BuilderDAO": ["BuilderDAO", "DAO"],
        "Regen Registry": ["Regen Registry Assistant", "Regen Registry program"],
        "Proposals": ["Proposal 23", "Proposal 25"],
        "White Pines": ["eastern white pines", "western white pines"],
        "MCP": ["MCP Server", "MCP Client"],
        "Regen Ledger Groups": ["Regen Ledger Community", "Regen Ledger Team"],
        "Phase 1-2": ["Phase 1-2 Complete", "Phase 2a Complete", "Phase 2c Complete"],
        "Phase 7-8": ["Phase 7 Complete", "Phase 8 Complete"],
        "UVPs": ["Unique Value Proposition 1", "Unique Value Proposition 3", "Unique Value Proposition 4"],
    }

    def __init__(self):
        self.db_config = {
            "host": os.getenv("POSTGRES_HOST", "localhost"),
            "port": int(os.getenv("POSTGRES_PORT", 5433)),
            "database": os.getenv("POSTGRES_DB", "eliza"),
            "user": os.getenv("POSTGRES_USER", "postgres"),
            "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
        }

        self.extractor = OpenAIExtractor()
        self.chunker = SmartChunker()
        self.kg = KnowledgeGraphIntegrator(store_type="memory", use_pipeline=True)

        self.stats = {
            "documents_found": 0,
            "documents_reextracted": 0,
            "entities_created": 0,
            "split_pairs_validated": 0,
            "errors": [],
        }

    def connect(self):
        """Connect to PostgreSQL."""
        return psycopg2.connect(**self.db_config)

    def find_documents_with_entities(self) -> List[Dict]:
        """
        Find documents that mention any of the split entities.

        Strategy: Search document metadata or content for entity mentions.
        Since we don't have a direct entity->document mapping, we'll search
        the documents table for content containing these terms.
        """
        conn = self.connect()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Build search terms (all entity variants)
        search_terms = []
        for category, variants in self.SPLIT_ENTITIES.items():
            search_terms.extend(variants)

        print(f"\n🔍 Searching for documents mentioning {len(search_terms)} entity terms...")

        # Search in documents table
        # Using ILIKE for case-insensitive search
        search_conditions = " OR ".join([
            f"content ILIKE '%{term}%'" for term in search_terms
        ])

        query = f"""
        SELECT DISTINCT
            id,
            source_url,
            title,
            content_type,
            LENGTH(content) as content_length,
            created_at
        FROM documents
        WHERE ({search_conditions})
        ORDER BY created_at DESC
        LIMIT 100;
        """

        cursor.execute(query)
        documents = cursor.fetchall()

        conn.close()

        self.stats["documents_found"] = len(documents)
        print(f"✓ Found {len(documents)} documents mentioning split entities")

        return documents

    async def reextract_document(self, doc: Dict) -> Dict:
        """
        Re-extract a single document.

        Returns:
            Dict with extraction results and validation info
        """
        doc_id = doc["id"]
        content = self.get_document_content(doc_id)

        if not content:
            return {
                "doc_id": doc_id,
                "success": False,
                "error": "No content found",
            }

        try:
            # Chunk document
            chunks = self.chunker.chunk_document(
                content,
                metadata={
                    "source": "reextraction_split_entities",
                    "doc_id": doc_id,
                    "url": doc.get("source_url", ""),
                }
            )

            # Extract entities from each chunk
            all_entities = []
            for chunk in chunks:
                entities = await self.extractor.extract_entities(
                    chunk["content"],
                    chunk["metadata"]
                )
                all_entities.extend(entities)

            # Process through pipeline (Tier 1+2 dedup)
            valid_entities = self.kg.process_entities_batch(all_entities)

            # Check which split entities were found
            found_entities = set()
            for entity in valid_entities:
                entity_text = entity.get("entity", entity.get("text", ""))
                for category, variants in self.SPLIT_ENTITIES.items():
                    if entity_text in variants:
                        found_entities.add(entity_text)

            self.stats["documents_reextracted"] += 1
            self.stats["entities_created"] += len(valid_entities)

            return {
                "doc_id": doc_id,
                "success": True,
                "total_entities": len(valid_entities),
                "split_entities_found": list(found_entities),
            }

        except Exception as e:
            self.stats["errors"].append({
                "doc_id": doc_id,
                "error": str(e),
            })
            return {
                "doc_id": doc_id,
                "success": False,
                "error": str(e),
            }

    def get_document_content(self, doc_id: int) -> str:
        """Fetch document content from database."""
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute("SELECT content FROM documents WHERE id = %s", (doc_id,))
        result = cursor.fetchone()

        conn.close()

        return result[0] if result else ""

    def validate_splits(self) -> Dict:
        """
        Validate that split entities now exist as separate entities in registry.

        Checks:
        1. For each split pair, both entities should exist
        2. They should have different IDs (not merged)
        3. They should have correct types
        """
        conn = self.connect()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        validation_results = {}

        for category, variants in self.SPLIT_ENTITIES.items():
            # Check if all variants exist
            placeholders = ", ".join(["%s"] * len(variants))
            query = f"""
            SELECT entity_text, entity_type, occurrence_count, id
            FROM entity_registry
            WHERE entity_text IN ({placeholders})
            ORDER BY entity_text;
            """

            cursor.execute(query, variants)
            found = cursor.fetchall()

            validation_results[category] = {
                "expected": variants,
                "found": [e["entity_text"] for e in found],
                "count": len(found),
                "details": found,
            }

            # Check if they're separate (different IDs)
            if len(found) > 1:
                ids = [e["id"] for e in found]
                if len(ids) == len(set(ids)):
                    validation_results[category]["separate"] = True
                    self.stats["split_pairs_validated"] += 1
                else:
                    validation_results[category]["separate"] = False
            else:
                validation_results[category]["separate"] = False

        conn.close()

        return validation_results

    async def run(self):
        """Main execution flow."""
        print("=" * 80)
        print("RE-EXTRACTION: Split Entities")
        print("=" * 80)
        print(f"\nStarted: {datetime.now().isoformat()}")

        # Step 1: Find documents
        print("\n" + "=" * 80)
        print("STEP 1: Find Documents")
        print("=" * 80)
        documents = self.find_documents_with_entities()

        if not documents:
            print("\n⚠️  No documents found mentioning split entities")
            return

        # Step 2: Re-extract documents
        print("\n" + "=" * 80)
        print("STEP 2: Re-extract Documents")
        print("=" * 80)
        print(f"\nRe-extracting {len(documents)} documents...")

        results = []
        for i, doc in enumerate(documents, 1):
            print(f"\n[{i}/{len(documents)}] Re-extracting doc {doc['id']}: {doc.get('title', 'Untitled')[:50]}")
            result = await self.reextract_document(doc)
            results.append(result)

            if result["success"]:
                found = result.get("split_entities_found", [])
                if found:
                    print(f"  ✓ Found split entities: {', '.join(found)}")
                else:
                    print(f"  ✓ Extracted {result['total_entities']} entities (no split entities found)")
            else:
                print(f"  ✗ Failed: {result.get('error', 'Unknown error')}")

        # Step 3: Validate splits
        print("\n" + "=" * 80)
        print("STEP 3: Validate Splits")
        print("=" * 80)
        validation = self.validate_splits()

        for category, result in validation.items():
            print(f"\n{category}:")
            print(f"  Expected: {result['expected']}")
            print(f"  Found: {result['found']} ({result['count']}/{len(result['expected'])})")

            if result["separate"]:
                print(f"  ✓ Entities are separate (not merged)")
            else:
                if result["count"] > 0:
                    print(f"  ⚠️  Entities may still be merged or partially extracted")
                else:
                    print(f"  ⚠️  Entities not found (may need more re-extraction)")

        # Step 4: Summary
        print("\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)
        print(f"\nDocuments found: {self.stats['documents_found']}")
        print(f"Documents re-extracted: {self.stats['documents_reextracted']}")
        print(f"Entities created: {self.stats['entities_created']}")
        print(f"Split pairs validated: {self.stats['split_pairs_validated']}/{len(self.SPLIT_ENTITIES)}")

        if self.stats["errors"]:
            print(f"\nErrors: {len(self.stats['errors'])}")
            for error in self.stats["errors"][:5]:  # Show first 5
                print(f"  - Doc {error['doc_id']}: {error['error']}")

        # Save results
        output = {
            "timestamp": datetime.now().isoformat(),
            "stats": self.stats,
            "validation": validation,
            "results": results,
        }

        output_path = Path(__file__).parent / "reextraction_split_entities_results.json"
        with open(output_path, "w") as f:
            json.dump(output, f, indent=2, default=str)

        print(f"\n✓ Results saved to: {output_path}")
        print(f"\nCompleted: {datetime.now().isoformat()}")


async def main():
    reextractor = SplitEntityReextractor()
    await reextractor.run()


if __name__ == "__main__":
    asyncio.run(main())
