#!/usr/bin/env python3
"""
Entity Deduplication Script for Regen KOI Knowledge Graph

This script normalizes duplicate entities by:
1. Case-insensitive deduplication
2. Cross-type resolution
3. Generic noun removal

Usage:
    python deduplicate_entities.py --dry-run  # Preview changes
    python deduplicate_entities.py --apply    # Apply changes (CAUTION)

Requirements:
    pip install psycopg2-binary

Author: Claude Code Audit
Date: December 8, 2025
"""

import json
import argparse
from collections import defaultdict
import psycopg2

# Database connection config
DB_CONFIG = {
    "host": "localhost",
    "port": 5433,
    "database": "eliza",
    "user": "postgres",
    "password": "postgres"
}

# Canonical entity mappings (canonical_name: [variants])
CANONICAL_ENTITIES = {
    "Organization": {
        "Regen Network": ["regen network", "Regen network", "REGEN Network"],
        "Regen": ["regen", "REGEN"],
        "Regen Registry": ["regen registry", "REGEN REGISTRY"],
        "Regen Foundation": ["regen foundation"],
        "Regen Network Development": ["regen network development", "RND", "rnd"],
        "DeSci Labs": ["desci labs", "DeSci Labs AG"],
        "Hylo": ["hylo"],
        "Axelar": ["axelar"],
        "Osmosis": ["osmosis"],
        "Verra": ["verra", "VERRA"],
        "Commonwealth": ["commonwealth"],
        "Ecometric": ["ecometric", "EcoMetric"],
    },
    "Project": {
        "Regen Ledger": ["regen ledger", "REGEN LEDGER"],
        "$REGEN": ["$regen", "$Regen", "REGEN", "Regen Coin", "REGEN Coin", "regen coin", "Regen Token", "REGEN Token", "regen token", "REGEN TOKEN"],
        "Regen Marketplace": ["regen marketplace"],
        "DeSci Publish": ["desci publish"],
        "Cosmos SDK": ["cosmos sdk"],
        "CosmWasm": ["cosmwasm"],
        "Climate Wiki": ["climate wiki"],
    },
    "Person": {
        "Gregory Landua": ["Gregory", "gregory landua"],
        "Sarah Bax": ["sarah bax"],
    }
}

# Entities to remove (generic nouns)
ENTITIES_TO_REMOVE = [
    "User", "user", "farmers", "farmer", "company", "network", "protocol",
    "community", "Community", "scientists", "validators", "validator",
    "delegator", "developers"
]

# Cross-type resolution (entity_name: correct_type)
CROSS_TYPE_FIXES = {
    "Regen Network": "Organization",
    "Regen Registry": "Organization",  # Primary is Org, Project refs are about the product
    "Regen Ledger": "Project",  # Primary is Project
    "Regen Marketplace": "Project",
    "Cosmos SDK": "Project",
    "Osmosis": "Organization",
    "Axelar": "Organization",
    "Hylo": "Organization",
    "Commonwealth": "Organization",
}


def get_connection():
    """Create database connection."""
    return psycopg2.connect(**DB_CONFIG)


def find_duplicates(cursor):
    """Find all case-insensitive duplicates."""
    query = """
    WITH entity_names AS (
        SELECT
            entity->>'name' as name,
            entity->>'type' as type,
            entity->>'rid' as rid,
            extraction_rid
        FROM koi_kg_extractions,
             jsonb_array_elements(entities) as entity
    )
    SELECT
        lower(name) as normalized_name,
        type,
        array_agg(DISTINCT name) as variants,
        COUNT(*) as occurrences
    FROM entity_names
    GROUP BY lower(name), type
    HAVING COUNT(DISTINCT name) > 1
    ORDER BY occurrences DESC;
    """
    cursor.execute(query)
    return cursor.fetchall()


def find_generic_entities(cursor):
    """Find generic noun entities to remove."""
    placeholders = ','.join(['%s'] * len(ENTITIES_TO_REMOVE))
    query = f"""
    SELECT
        entity->>'name' as name,
        entity->>'type' as type,
        entity->>'rid' as rid,
        extraction_rid,
        id as extraction_id
    FROM koi_kg_extractions,
         jsonb_array_elements(entities) as entity
    WHERE entity->>'name' IN ({placeholders});
    """
    cursor.execute(query, ENTITIES_TO_REMOVE)
    return cursor.fetchall()


def generate_normalization_sql(entity_type, canonical, variants):
    """Generate SQL to normalize entity names."""
    variant_list = "', '".join(variants)
    return f"""
-- Normalize {entity_type}: {variants} -> {canonical}
UPDATE koi_kg_extractions
SET entities = (
    SELECT jsonb_agg(
        CASE
            WHEN lower(e->>'name') IN ('{variant_list.lower()}')
            THEN jsonb_set(e, '{{name}}', '"{canonical}"'::jsonb)
            ELSE e
        END
    )
    FROM jsonb_array_elements(entities) as e
)
WHERE EXISTS (
    SELECT 1 FROM jsonb_array_elements(entities) as e
    WHERE lower(e->>'name') IN ('{variant_list.lower()}')
);
"""


def generate_removal_sql(entity_names):
    """Generate SQL to remove generic entities."""
    name_list = "', '".join(entity_names)
    return f"""
-- Remove generic noun entities
UPDATE koi_kg_extractions
SET entities = (
    SELECT COALESCE(jsonb_agg(e), '[]'::jsonb)
    FROM jsonb_array_elements(entities) as e
    WHERE e->>'name' NOT IN ('{name_list}')
)
WHERE EXISTS (
    SELECT 1 FROM jsonb_array_elements(entities) as e
    WHERE e->>'name' IN ('{name_list}')
);
"""


def main():
    parser = argparse.ArgumentParser(description="Deduplicate KG entities")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    parser.add_argument("--apply", action="store_true", help="Apply changes to database")
    parser.add_argument("--output", default="dedup_changes.sql", help="Output SQL file")
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        print("Please specify --dry-run or --apply")
        return

    sql_statements = []

    # Add normalization SQL for each canonical entity
    for entity_type, mappings in CANONICAL_ENTITIES.items():
        for canonical, variants in mappings.items():
            sql = generate_normalization_sql(entity_type, canonical, variants)
            sql_statements.append(sql)

    # Add removal SQL for generic entities
    sql_statements.append(generate_removal_sql(ENTITIES_TO_REMOVE))

    # Write SQL file
    full_sql = "\n".join(sql_statements)
    with open(args.output, 'w') as f:
        f.write("-- Entity Deduplication Script\n")
        f.write("-- Generated: December 8, 2025\n")
        f.write("-- WARNING: Review carefully before executing!\n\n")
        f.write("BEGIN;\n\n")
        f.write(full_sql)
        f.write("\n-- Uncomment to commit changes:\n")
        f.write("-- COMMIT;\n")
        f.write("ROLLBACK;  -- Safe default: rollback\n")

    print(f"Generated SQL written to: {args.output}")

    if args.dry_run:
        print("\nDry run mode - no changes applied.")
        print(f"\nSummary:")
        print(f"  - Normalization rules: {sum(len(m) for m in CANONICAL_ENTITIES.values())}")
        print(f"  - Entities to remove: {len(ENTITIES_TO_REMOVE)}")

    elif args.apply:
        print("\n*** WARNING: Apply mode not implemented for safety ***")
        print("Please review the generated SQL file and execute manually:")
        print(f"  psql -h localhost -p 5433 -U postgres -d eliza -f {args.output}")


if __name__ == "__main__":
    main()
