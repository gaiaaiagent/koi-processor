#!/usr/bin/env python3
"""
Interactive Merge Review Script
Created: 2025-12-10
Purpose: Review questionable merges from batch consolidation interactively

Usage:
    python3 scripts/interactive_merge_review.py [--limit 30] [--output review_decisions.json]

For each questionable merge:
- Shows canonical entity + variants merged
- Shows occurrence counts
- Asks: Keep merged or Split?
- If split, asks for reason
- Records all decisions in JSON file
- Generates SQL script to apply splits
"""

import argparse
import json
import os
import sys
from typing import Dict, List, Tuple

import psycopg2


class MergeReviewer:
    def __init__(self, db_config: Dict[str, str], output_path: str = "review_decisions.json"):
        self.db_config = db_config
        self.output_path = output_path
        self.decisions = []
        self.split_count = 0
        self.keep_count = 0

    def connect(self):
        """Connect to PostgreSQL database."""
        return psycopg2.connect(**self.db_config)

    def get_questionable_merges(self, limit: int = 30) -> List[Dict]:
        """
        Identify questionable merges from the execution log.

        Heuristics for "questionable":
        1. Merges with < 0.90 similarity (high threshold, but semantic matched)
        2. Merges involving proper nouns (capitalized terms)
        3. Merges with significant count disparity (e.g., 2 + 355)
        4. Merges involving specific vs generic terms
        """
        conn = self.connect()
        cursor = conn.cursor()

        # Parse the execution log to find all merges
        # For now, we'll use a simpler approach: Find all entities that were
        # likely merged by looking for ones with high counts

        questionable = []

        # Strategy: Look for entities where variants might be semantically similar
        # but contextually different (e.g., BuilderDAO vs DAO)

        # Pattern 1: Generic vs Specific (e.g., "DAO" vs "BuilderDAO")
        cursor.execute("""
            WITH generic_terms AS (
                SELECT entity_text, entity_type, occurrence_count
                FROM entity_registry
                WHERE length(entity_text) <= 10  -- Short, potentially generic
                  AND occurrence_count > 20      -- High usage
                  AND entity_text ~ '^[A-Z]+$'   -- All caps (acronyms)
            ),
            specific_terms AS (
                SELECT entity_text, entity_type, occurrence_count
                FROM entity_registry
                WHERE entity_text LIKE '%' || generic_terms.entity_text || '%'
                  AND entity_text != generic_terms.entity_text
                  AND entity_type = generic_terms.entity_type
                FROM generic_terms
            )
            SELECT g.entity_text as canonical, g.entity_type, g.occurrence_count,
                   NULL as variant, NULL as variant_count
            FROM generic_terms g
            LIMIT %s;
        """, (limit // 4,))  # Reserve some quota

        # Simpler approach: Read from execution log if available
        # For this script, we'll manually specify known questionable merges
        # based on the execution log review

        # Known questionable merges from execution log:
        known_questionable = [
            # Format: (canonical_text, canonical_type, canonical_count, variant_text, variant_count, cluster_id, reason)
            ("BuilderDAO", "ORGANIZATION", 33, "DAO", 31, 107, "Generic vs specific: DAO (generic) vs BuilderDAO (specific DAO)"),
            ("Regen Registry Assistant", "PROJECT", 359, "Regen Registry program", 355, 1275, "AI agent vs blockchain registry: Different components"),
            ("Proposal 23", "PROJECT", 4, "Proposal 25", 2, 1800, "Different proposal numbers"),
            ("eastern white pines", "PROJECT", 4, "western white pines", 2, 1998, "Different tree species"),
            ("MCP Server", "PERSON", 11, "MCP Client", 4, 3291, "Server vs client + wrong type (should be TECHNOLOGY)"),
            ("Cosmos SDK", "PROJECT", 98, "Cosmos SDK 0.53", 2, 1246, "Version-specific vs generic"),
            ("CosmWasm integration", "PROJECT", 71, "CosmWasm", 69, 1213, "Specific integration vs general library"),
            ("Regen Ledger Community", "PERSON", 29, "Regen Ledger Team", 28, 3184, "Community vs Team: Different groups?"),
            ("Phase 1-2 Complete", "CLAIM", 9, "Phase 2a Complete", 4, 4486, "Different phases"),
            ("Phase 7 Complete", "CLAIM", 4, "Phase 8 Complete", 1, 4488, "Different phases"),
            ("Unique Value Proposition 1", "CLAIM", 4, "Unique Value Proposition 3", 1, 4922, "Different UVPs (numbered)"),
        ]

        for item in known_questionable[:limit]:
            canonical_text, canonical_type, canonical_count, variant_text, variant_count, cluster_id, reason = item
            questionable.append({
                "canonical": canonical_text,
                "type": canonical_type,
                "canonical_count": canonical_count,
                "variant": variant_text,
                "variant_count": variant_count,
                "cluster_id": cluster_id,
                "reason": reason,
            })

        conn.close()
        return questionable

    def review_merge(self, merge: Dict) -> Dict:
        """
        Interactively review a single merge with the user.

        Returns decision dict with:
        - action: "keep" or "split"
        - reason: user-provided reason for split
        """
        print("\n" + "="*80)
        print(f"Cluster {merge['cluster_id']}: {merge['canonical']} ({merge['type']})")
        print("="*80)
        print(f"\nCanonical: {merge['canonical']} ({merge['canonical_count']} mentions)")
        print(f"Variant:   {merge['variant']} ({merge['variant_count']} mentions)")
        print(f"\nContext: {merge['reason']}")
        print("-"*80)

        while True:
            response = input(
                "\nAction? [ENTER=keep merged, 's'=split, 'q'=quit]: "
            ).strip().lower()

            if response == "" or response == "k":
                print("✓ Keeping merged")
                return {"action": "keep", "reason": None}

            elif response == "s":
                reason = input("Reason for split (brief): ").strip()
                if not reason:
                    reason = merge['reason']  # Use default reason
                print(f"✓ Will split: {reason}")
                return {"action": "split", "reason": reason}

            elif response == "q":
                print("\nQuitting review...")
                return {"action": "quit", "reason": None}

            else:
                print("Invalid input. Use ENTER (keep), 's' (split), or 'q' (quit)")

    def run_review(self, limit: int = 30):
        """Run the interactive review process."""
        print("="*80)
        print("INTERACTIVE MERGE REVIEW")
        print("="*80)
        print(f"\nFetching up to {limit} questionable merges...")

        questionable_merges = self.get_questionable_merges(limit)
        total = len(questionable_merges)

        print(f"Found {total} questionable merges to review\n")
        print("For each merge:")
        print("  - Press ENTER to keep merged")
        print("  - Type 's' to split (you'll be asked for a reason)")
        print("  - Type 'q' to quit review\n")

        input("Press ENTER to start review...")

        for i, merge in enumerate(questionable_merges, 1):
            print(f"\n[{i}/{total}]")

            decision = self.review_merge(merge)

            if decision["action"] == "quit":
                print(f"\nReview stopped at {i-1}/{total}")
                break

            # Record decision
            self.decisions.append({
                "merge": merge,
                "decision": decision["action"],
                "reason": decision["reason"],
            })

            if decision["action"] == "split":
                self.split_count += 1
            else:
                self.keep_count += 1

        # Save decisions
        self.save_decisions()

        # Generate SQL script
        self.generate_sql_script()

        # Print summary
        self.print_summary()

    def save_decisions(self):
        """Save review decisions to JSON file."""
        with open(self.output_path, "w") as f:
            json.dump({
                "total_reviewed": len(self.decisions),
                "splits": self.split_count,
                "keeps": self.keep_count,
                "decisions": self.decisions,
            }, f, indent=2)

        print(f"\n✓ Decisions saved to: {self.output_path}")

    def generate_sql_script(self):
        """Generate SQL script to apply split decisions."""
        sql_path = self.output_path.replace(".json", "_splits.sql")

        splits = [d for d in self.decisions if d["decision"] == "split"]

        if not splits:
            print("\n✓ No splits needed - all merges kept as-is")
            return

        with open(sql_path, "w") as f:
            f.write("-- Split Script Generated from Interactive Review\n")
            f.write(f"-- Created: {self.output_path}\n")
            f.write(f"-- Splits: {len(splits)}\n\n")
            f.write("-- BEFORE RUNNING: pg_dump backup!\n\n")
            f.write("BEGIN;\n\n")

            for i, decision in enumerate(splits, 1):
                merge = decision["merge"]
                reason = decision["reason"]

                f.write(f"-- Split {i}: {merge['canonical']} ↔ {merge['variant']}\n")
                f.write(f"-- Reason: {reason}\n")
                f.write(f"-- Cluster: {merge['cluster_id']}\n\n")

                f.write(f"DO $$\n")
                f.write(f"BEGIN\n")

                # Re-create variant
                f.write(f"    INSERT INTO entity_registry (entity_text, entity_type, occurrence_count)\n")
                f.write(f"    VALUES ('{merge['variant']}', '{merge['type']}', {merge['variant_count']})\n")
                f.write(f"    ON CONFLICT (entity_text, entity_type) DO NOTHING;\n\n")

                # Restore canonical to original count
                f.write(f"    UPDATE entity_registry\n")
                f.write(f"    SET occurrence_count = {merge['canonical_count'] - merge['variant_count']}\n")
                f.write(f"    WHERE entity_text = '{merge['canonical']}' AND entity_type = '{merge['type']}';\n\n")

                f.write(f"    RAISE NOTICE 'Split {i}: {merge['canonical']} (%) ↔ {merge['variant']} (%)', ")
                f.write(f"{merge['canonical_count'] - merge['variant_count']}, {merge['variant_count']};\n")
                f.write(f"END $$;\n\n")

            f.write("COMMIT;\n\n")

            # Validation queries
            f.write("-- Validation\n")
            f.write("SELECT COUNT(*) AS unique_entities, SUM(occurrence_count) AS total_mentions,\n")
            f.write("       ROUND(((1 - COUNT(*)::float / SUM(occurrence_count)::float) * 100)::numeric, 2) AS dedup_rate\n")
            f.write("FROM entity_registry;\n")

        print(f"✓ SQL script generated: {sql_path}")
        print(f"  Run: psql -f {sql_path}")

    def print_summary(self):
        """Print review summary."""
        print("\n" + "="*80)
        print("REVIEW SUMMARY")
        print("="*80)
        print(f"Total reviewed: {len(self.decisions)}")
        print(f"Keep merged:    {self.keep_count}")
        print(f"Split:          {self.split_count}")

        if self.split_count > 0:
            print(f"\nSplits will:")
            print(f"  - Add {self.split_count} entities")
            print(f"  - Reduce dedup rate slightly (~{self.split_count * 0.01:.2f}%)")
            print(f"  - Improve accuracy (correct semantic distinctions)")


def main():
    parser = argparse.ArgumentParser(description="Interactive merge review")
    parser.add_argument("--limit", type=int, default=30, help="Max merges to review")
    parser.add_argument("--output", default="review_decisions.json", help="Output file for decisions")
    args = parser.parse_args()

    # Database config from environment
    db_config = {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": int(os.getenv("POSTGRES_PORT", 5433)),
        "database": os.getenv("POSTGRES_DB", "eliza"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
    }

    reviewer = MergeReviewer(db_config, args.output)
    reviewer.run_review(args.limit)


if __name__ == "__main__":
    main()
