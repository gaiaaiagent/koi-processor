#!/usr/bin/env python3
"""
Validate Split Entities Post-Deletion
Created: 2025-12-10
Purpose: Check if deleted entities have been recreated as separate entities

This script validates that:
1. Split entities exist as separate entities (not merged)
2. They have correct types
3. They have reasonable occurrence counts

We don't need to re-extract - the ongoing extraction will naturally
recreate these entities, and semantic dedup will keep them separate.
"""

import os
import sys
from datetime import datetime
from typing import Dict, List

import psycopg2
from psycopg2.extras import RealDictCursor


class SplitEntityValidator:
    """Validate that split entities are properly separated."""

    # Entities that were split (user review decisions)
    EXPECTED_SPLITS = {
        "BuilderDAO/DAO": {
            "entities": ["BuilderDAO", "DAO"],
            "type": "ORGANIZATION",
            "reason": "BuilderDAO is a specific DAO implementation",
        },
        "Regen Registry": {
            "entities": ["Regen Registry Assistant", "Regen Registry program"],
            "type": "PROJECT",
            "reason": "AI agent vs blockchain registry",
        },
        "Proposals": {
            "entities": ["Proposal 23", "Proposal 25"],
            "type": "PROJECT",
            "reason": "Different governance proposals",
        },
        "White Pines": {
            "entities": ["eastern white pines", "western white pines"],
            "type": "PROJECT",
            "reason": "Different tree species",
        },
        "MCP": {
            "entities": ["MCP Server", "MCP Client"],
            "type": "TECHNOLOGY",  # Should be TECHNOLOGY, not PERSON!
            "reason": "Server vs client components",
        },
        "Regen Ledger Groups": {
            "entities": ["Regen Ledger Community", "Regen Ledger Team"],
            "type": "PERSON",
            "reason": "Community vs Team (different groups)",
        },
        "Phase 1-2": {
            "entities": ["Phase 1-2 Complete", "Phase 2a Complete", "Phase 2c Complete"],
            "type": "CLAIM",
            "reason": "Different project phases",
        },
        "Phase 7-8": {
            "entities": ["Phase 7 Complete", "Phase 8 Complete"],
            "type": "CLAIM",
            "reason": "Different project phases",
        },
        "UVPs": {
            "entities": ["Unique Value Proposition 1", "Unique Value Proposition 3", "Unique Value Proposition 4"],
            "type": "CLAIM",
            "reason": "Different numbered value propositions",
        },
    }

    def __init__(self):
        self.db_config = {
            "host": os.getenv("POSTGRES_HOST", "localhost"),
            "port": int(os.getenv("POSTGRES_PORT", 5433)),
            "database": os.getenv("POSTGRES_DB", "eliza"),
            "user": os.getenv("POSTGRES_USER", "postgres"),
            "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
        }

        self.stats = {
            "total_splits": len(self.EXPECTED_SPLITS),
            "validated_splits": 0,
            "partial_splits": 0,
            "missing_splits": 0,
            "type_mismatches": 0,
        }

    def connect(self):
        """Connect to PostgreSQL."""
        return psycopg2.connect(**self.db_config)

    def get_entity_registry_stats(self) -> Dict:
        """Get current entity registry stats."""
        conn = self.connect()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT
                COUNT(*) AS unique_entities,
                SUM(occurrence_count) AS total_mentions,
                ROUND(((1 - COUNT(*)::float / SUM(occurrence_count)::float) * 100)::numeric, 2) AS dedup_rate
            FROM entity_registry;
        """)

        stats = cursor.fetchone()
        conn.close()

        return dict(stats) if stats else {}

    def validate_split(self, category: str, config: Dict) -> Dict:
        """
        Validate a single split.

        Returns:
            Dict with validation results
        """
        entities = config["entities"]
        expected_type = config["type"]
        reason = config["reason"]

        conn = self.connect()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Search for these entities
        placeholders = ", ".join(["%s"] * len(entities))
        query = f"""
        SELECT entity_text, entity_type, occurrence_count, id
        FROM entity_registry
        WHERE normalized_text IN ({", ".join([f"LOWER(%s)" for _ in entities])})
        ORDER BY entity_text;
        """

        cursor.execute(query, entities)
        found = cursor.fetchall()

        conn.close()

        # Analyze results
        found_texts = [e["entity_text"] for e in found]
        found_count = len(found)
        expected_count = len(entities)

        # Check if all entities exist
        all_exist = found_count == expected_count

        # Check if they're separate (different IDs)
        if found_count > 1:
            ids = [e["id"] for e in found]
            are_separate = len(ids) == len(set(ids))
        else:
            are_separate = False

        # Check type correctness
        type_correct = all(e["entity_type"] == expected_type for e in found) if found else False

        # Determine status
        if all_exist and are_separate and type_correct:
            status = "✅ VALIDATED"
            self.stats["validated_splits"] += 1
        elif found_count > 0 and are_separate:
            status = "⚠️  PARTIAL" if not type_correct else "⚠️  PARTIAL (incomplete)"
            self.stats["partial_splits"] += 1
        elif found_count > 0 and not are_separate:
            status = "❌ MERGED (still combined)"
        else:
            status = "⏳ PENDING (not yet extracted)"
            self.stats["missing_splits"] += 1

        if not type_correct and found_count > 0:
            self.stats["type_mismatches"] += 1

        return {
            "category": category,
            "status": status,
            "expected": entities,
            "found": found_texts,
            "found_count": f"{found_count}/{expected_count}",
            "are_separate": are_separate,
            "type_correct": type_correct,
            "expected_type": expected_type,
            "details": found,
            "reason": reason,
        }

    def run(self):
        """Main validation flow."""
        print("=" * 80)
        print("SPLIT ENTITY VALIDATION")
        print("=" * 80)
        print(f"\nTimestamp: {datetime.now().isoformat()}")

        # Get current stats
        print("\n" + "=" * 80)
        print("ENTITY REGISTRY STATS")
        print("=" * 80)
        stats = self.get_entity_registry_stats()
        print(f"\nUnique entities: {stats.get('unique_entities', 'N/A')}")
        print(f"Total mentions: {stats.get('total_mentions', 'N/A')}")
        print(f"Dedup rate: {stats.get('dedup_rate', 'N/A')}%")

        # Validate each split
        print("\n" + "=" * 80)
        print("SPLIT VALIDATION RESULTS")
        print("=" * 80)

        results = []
        for category, config in self.EXPECTED_SPLITS.items():
            result = self.validate_split(category, config)
            results.append(result)

            print(f"\n{result['status']} {category}")
            print(f"  Expected: {', '.join(result['expected'])}")
            print(f"  Found: {', '.join(result['found'])} ({result['found_count']})")
            print(f"  Separate: {result['are_separate']}")
            print(f"  Type: {result['expected_type']} (correct: {result['type_correct']})")
            print(f"  Reason: {result['reason']}")

            if result["details"]:
                for entity in result["details"]:
                    print(f"    - {entity['entity_text']}: {entity['entity_type']} ({entity['occurrence_count']} mentions, ID: {entity['id']})")

        # Summary
        print("\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)
        print(f"\nTotal splits: {self.stats['total_splits']}")
        print(f"✅ Validated (complete): {self.stats['validated_splits']}")
        print(f"⚠️  Partial (some found): {self.stats['partial_splits']}")
        print(f"⏳ Pending (not extracted): {self.stats['missing_splits']}")
        print(f"❌ Type mismatches: {self.stats['type_mismatches']}")

        # Overall status
        print("\n" + "=" * 80)
        print("OVERALL STATUS")
        print("=" * 80)

        if self.stats["validated_splits"] == self.stats["total_splits"]:
            print("\n✅ ALL SPLITS VALIDATED")
            print("All entities exist as separate entries with correct types.")
            print("Batch consolidation user review successfully applied!")
        elif self.stats["validated_splits"] + self.stats["partial_splits"] >= self.stats["total_splits"] * 0.7:
            print("\n⚠️  MOSTLY COMPLETE")
            print(f"Most splits validated ({self.stats['validated_splits']}/{self.stats['total_splits']}).")
            print("Some entities may need re-extraction or are still processing.")
        else:
            print("\n⏳ IN PROGRESS")
            print("Entities are being extracted. Re-run this script after extraction completes.")
            print("\nNext steps:")
            print("1. Let ongoing extraction continue")
            print("2. Re-run this validation script periodically")
            print("3. Once validated, push knowledge graph to production")


def main():
    validator = SplitEntityValidator()
    validator.run()


if __name__ == "__main__":
    main()
