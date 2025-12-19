#!/usr/bin/env python3
"""
Merge entity variants in entity_registry using canonical_entities.json aliases.

Workflow:
1. Load canonical registry.
2. For each canonical entry, find matching rows by entity_text (canonical + aliases) and entity_type.
3. Sum occurrence_count across matches.
4. Upsert canonical row with total count.
5. Delete alias rows (canonical row retained).
"""

import json
import psycopg2
from typing import Dict, List, Tuple


def load_canonical_mappings(filepath: str) -> Dict:
    """Load canonical entity mappings from JSON file."""
    with open(filepath, "r") as f:
        return json.load(f)


def consolidate_aliases(conn, canonical_name: str, entity_type: str, aliases: List[str]) -> Tuple[int, int]:
    """
    Consolidate all aliases into canonical entity.

    Args:
        conn: psycopg2 connection
        canonical_name: canonical entity_text
        entity_type: canonical entity_type
        aliases: list of alias strings

    Returns:
        (aliases_merged, total_mentions_consolidated)
    """
    cursor = conn.cursor()

    all_variants = [canonical_name] + aliases
    placeholders = ",".join(["%s"] * len(all_variants))

    cursor.execute(
        f"""
        SELECT id, entity_text, occurrence_count, normalized_text
        FROM entity_registry
        WHERE entity_text IN ({placeholders})
          AND entity_type = %s
        """,
        all_variants + [entity_type],
    )
    matches = cursor.fetchall()

    if not matches:
        print(f"  ⚠️  No matches found for {canonical_name}")
        return 0, 0

    total_count = sum(row[2] for row in matches)

    # Identify canonical row (prefer exact name match)
    canonical_row = next((row for row in matches if row[1] == canonical_name), None)
    if canonical_row is None:
        # Promote the highest-occurrence variant to canonical
        canonical_row = sorted(matches, key=lambda r: r[2], reverse=True)[0]
        cursor.execute(
            """
            UPDATE entity_registry
            SET entity_text = %s
            WHERE id = %s
            """,
            (canonical_name, canonical_row[0]),
        )

    canonical_id = canonical_row[0]

    # Update canonical occurrence_count to total
    cursor.execute(
        """
        UPDATE entity_registry
        SET occurrence_count = %s
        WHERE id = %s
        """,
        (total_count, canonical_id),
    )

    # Delete other variants
    variants_to_delete = [row[0] for row in matches if row[0] != canonical_id]
    if variants_to_delete:
        placeholders = ",".join(["%s"] * len(variants_to_delete))
        cursor.execute(
            f"DELETE FROM entity_registry WHERE id IN ({placeholders})",
            variants_to_delete,
        )

    merged_count = len(variants_to_delete)
    print(f"  ✓ {canonical_name}: merged {merged_count} variants ({total_count} total mentions)")
    return merged_count, total_count


def main():
    print("Canonical Alias Consolidation")
    print("=" * 60)

    mappings = load_canonical_mappings("data/canonical_entities.json")

    conn = psycopg2.connect(
        host="localhost",
        port=5433,
        database="eliza",
        user="postgres",
        password="postgres",
    )

    total_merged = 0
    total_mentions = 0

    for category, entities in mappings.get("entities", {}).items():
        print(f"\n{category.upper()}:")
        for _, entity_data in entities.items():
            canonical_name = entity_data.get("canonical_name")
            entity_type = entity_data.get("entity_type")
            aliases = entity_data.get("aliases", [])

            if not canonical_name or not entity_type:
                continue

            merged, mentions = consolidate_aliases(conn, canonical_name, entity_type, aliases)
            total_merged += merged
            total_mentions += mentions

    conn.commit()
    conn.close()

    print(f"\n{'=' * 60}")
    print("CONSOLIDATION COMPLETE")
    print(f"  Variants merged: {total_merged}")
    print(f"  Total mentions consolidated: {total_mentions}")
    print("=" * 60)


if __name__ == "__main__":
    main()
