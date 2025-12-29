#!/usr/bin/env python3
"""
Canonical Alias Audit Script
Created: 2025-12-29

Identifies entity_registry rows that match canonical aliases but are not
the canonical entity itself. Generates audit report for merge classification.

Output: data/alias_audit_report.csv
"""

import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor

# ============================================================================
# CONFIGURATION
# ============================================================================
CANONICAL_ENTITIES_PATH = Path(__file__).parent.parent / "data" / "canonical_entities.json"
OUTPUT_PATH = Path(__file__).parent.parent / "data" / "alias_audit_report.csv"


def normalize_text(text: str) -> str:
    """Normalize text for comparison (matching entity_registry.normalized_text logic)."""
    # Lowercase, strip whitespace, remove special chars except alphanumeric and space
    normalized = text.lower().strip()
    # Remove special prefixes like $ for matching
    normalized = re.sub(r'^[\$@#]+', '', normalized)
    # Keep alphanumeric and spaces only
    normalized = re.sub(r'[^a-z0-9\s]', '', normalized)
    # Collapse multiple spaces
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized


def load_canonical_entities() -> Dict[str, Dict]:
    """
    Load canonical entities and build alias → canonical mapping.

    Returns:
        Dict mapping normalized_alias -> {canonical_name, canonical_type, entity_key}
    """
    print(f"Loading canonical entities from: {CANONICAL_ENTITIES_PATH}")

    with open(CANONICAL_ENTITIES_PATH, 'r') as f:
        data = json.load(f)

    alias_map = {}
    categories = ['organizations', 'projects', 'people', 'concepts']

    for category in categories:
        if category not in data.get('entities', {}):
            continue

        for entity_key, entity_info in data['entities'][category].items():
            canonical_name = entity_info.get('canonical_name', '')
            canonical_type = entity_info.get('entity_type', 'UNKNOWN')
            aliases = entity_info.get('aliases', [])

            # Add each alias to the map
            for alias in aliases:
                normalized_alias = normalize_text(alias)
                if normalized_alias:  # Skip empty
                    alias_map[normalized_alias] = {
                        'canonical_name': canonical_name,
                        'canonical_type': canonical_type,
                        'entity_key': entity_key,
                        'original_alias': alias,
                        'category': category
                    }

            # Also add the canonical name itself (normalized) to track which entities exist
            normalized_canonical = normalize_text(canonical_name)
            if normalized_canonical and normalized_canonical not in alias_map:
                alias_map[normalized_canonical] = {
                    'canonical_name': canonical_name,
                    'canonical_type': canonical_type,
                    'entity_key': entity_key,
                    'original_alias': canonical_name,
                    'category': category,
                    'is_canonical': True
                }

    return alias_map


def get_db_connection():
    """Get PostgreSQL connection to production server."""
    # Production server connection via SSH tunnel or direct
    # Default to production settings from CLAUDE.md
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5433)),  # Production uses 5433
        database=os.getenv("POSTGRES_DB", "eliza"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )


def find_alias_duplicates(alias_map: Dict[str, Dict]) -> List[Dict]:
    """
    Query entity_registry for rows that match aliases but aren't canonical.

    Args:
        alias_map: Mapping from normalized alias to canonical info

    Returns:
        List of audit records
    """
    print("\nConnecting to PostgreSQL...")
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # First, get all entities from entity_registry
    print("Fetching entity registry...")
    cursor.execute("""
        SELECT
            id,
            entity_text,
            entity_type,
            normalized_text,
            occurrence_count,
            fuseki_uri,
            first_seen_at,
            last_seen_at
        FROM entity_registry
        ORDER BY occurrence_count DESC
    """)

    entities = cursor.fetchall()
    print(f"Found {len(entities)} entities in registry")

    # Find duplicates
    duplicates = []
    canonical_found = set()  # Track which canonical entities exist

    for entity in entities:
        # Normalize the entity text same way as aliases
        normalized_entity = normalize_text(entity['entity_text'] or '')

        # Check if this matches an alias
        if normalized_entity in alias_map:
            alias_info = alias_map[normalized_entity]
            canonical_name = alias_info['canonical_name']
            canonical_type = alias_info['canonical_type']

            # Is this the canonical entity itself?
            normalized_canonical = normalize_text(canonical_name)
            is_canonical_entry = (normalized_entity == normalized_canonical)

            if is_canonical_entry:
                # This is the canonical entity - track it
                canonical_found.add(alias_info['entity_key'])
            else:
                # This is an alias that exists as a separate entity
                duplicates.append({
                    'alias_entity_id': entity['id'],
                    'alias_text': entity['entity_text'],
                    'alias_type': entity['entity_type'],
                    'alias_normalized': normalized_entity,
                    'occurrences': entity['occurrence_count'],
                    'fuseki_uri': entity['fuseki_uri'],
                    'canonical_name': canonical_name,
                    'canonical_type': canonical_type,
                    'canonical_key': alias_info['entity_key'],
                    'category': alias_info['category'],
                    'original_alias': alias_info['original_alias'],
                    'action': 'PENDING'
                })

    # Now check if canonical entries exist for each duplicate
    print(f"\nChecking canonical entries for {len(duplicates)} alias duplicates...")

    # Build lookup by exact entity_text (case-insensitive) - keep highest occurrence count
    canonical_lookup = {}
    for entity in entities:
        key = (entity['entity_text'] or '').lower()
        if key not in canonical_lookup or entity['occurrence_count'] > canonical_lookup[key]['occurrence_count']:
            canonical_lookup[key] = entity

    for dup in duplicates:
        # Look up by exact canonical name (case-insensitive)
        canonical_key = dup['canonical_name'].lower()
        if canonical_key in canonical_lookup:
            canonical_entity = canonical_lookup[canonical_key]
            dup['canonical_exists'] = True
            dup['canonical_entity_id'] = canonical_entity['id']
            dup['canonical_occurrences'] = canonical_entity['occurrence_count']
            dup['canonical_fuseki_uri'] = canonical_entity['fuseki_uri']
            dup['canonical_actual_type'] = canonical_entity['entity_type']
        else:
            dup['canonical_exists'] = False
            dup['canonical_entity_id'] = None
            dup['canonical_occurrences'] = 0
            dup['canonical_fuseki_uri'] = None
            dup['canonical_actual_type'] = None

    conn.close()
    return duplicates


def classify_merges(duplicates: List[Dict]) -> List[Dict]:
    """
    Classify each duplicate for merge action.

    Rules:
    - MERGE: Alias is clearly a synonym (case variants, $ prefix, plural) and canonical has more occurrences
    - DEFER: Ambiguous or likely different concept
    - SKIP: Canonical entry doesn't exist (need to create first)
    """
    print("\nClassifying merge actions...")

    for dup in duplicates:
        if not dup['canonical_exists']:
            dup['action'] = 'SKIP_NO_CANONICAL'
            dup['action_reason'] = f"Canonical '{dup['canonical_name']}' not in entity_registry"
            continue

        alias_text = dup['alias_text'].lower()
        canonical_text = dup['canonical_name'].lower()
        alias_type = dup['alias_type']
        canonical_actual_type = dup.get('canonical_actual_type', dup['canonical_type'])

        # Check for clear synonyms
        is_case_variant = alias_text == canonical_text
        is_prefix_variant = alias_text.lstrip('$@#') == canonical_text.lstrip('$@#')
        is_underscore_variant = alias_text.replace('_', ' ') == canonical_text.replace('_', ' ')
        is_hyphen_variant = alias_text.replace('-', ' ') == canonical_text.replace('-', ' ')
        is_clear_variant = is_case_variant or is_prefix_variant or is_underscore_variant or is_hyphen_variant

        # Type comparison - use actual type in DB
        type_mismatch = alias_type != canonical_actual_type

        # Occurrence ratio - if alias has way more occurrences, something is off
        alias_occ = dup['occurrences']
        canonical_occ = dup['canonical_occurrences']
        canonical_dominant = canonical_occ > alias_occ * 2  # Canonical has 2x+ more

        # Generic alias check (single common words)
        is_generic = alias_text in ['registry', 'foundation', 'network', 'ledger', 'sdk']

        # Classification logic
        if is_generic and type_mismatch:
            dup['action'] = 'DEFER_AMBIGUOUS'
            dup['action_reason'] = f"Generic term '{alias_text}' with type mismatch"
        elif is_clear_variant and not type_mismatch and canonical_dominant:
            dup['action'] = 'MERGE_SAFE'
            dup['action_reason'] = f"Clear variant, canonical has {canonical_occ} vs {alias_occ} occurrences"
        elif is_clear_variant and canonical_dominant:
            dup['action'] = 'MERGE_RETYPE'
            dup['action_reason'] = f"Clear variant with type change: {alias_type} → {canonical_actual_type}"
        elif type_mismatch and canonical_dominant:
            dup['action'] = 'REVIEW_TYPE'
            dup['action_reason'] = f"Type mismatch ({alias_type} vs {canonical_actual_type}), but canonical dominant"
        elif type_mismatch:
            dup['action'] = 'REVIEW_TYPE'
            dup['action_reason'] = f"Type mismatch: {alias_type} vs {canonical_actual_type}"
        elif is_clear_variant:
            dup['action'] = 'MERGE_SAFE'
            dup['action_reason'] = 'Clear synonym (case/prefix/separator variant)'
        elif alias_text in canonical_text or canonical_text in alias_text:
            if canonical_dominant:
                dup['action'] = 'MERGE_SAFE'
                dup['action_reason'] = 'Substring match, canonical dominant'
            else:
                dup['action'] = 'REVIEW'
                dup['action_reason'] = 'Substring match but alias has more occurrences'
        else:
            dup['action'] = 'REVIEW'
            dup['action_reason'] = 'Manual review needed'

    return duplicates


def generate_report(duplicates: List[Dict]):
    """Generate CSV audit report."""
    print(f"\nGenerating report: {OUTPUT_PATH}")

    # Ensure output directory exists
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        'alias_entity_id',
        'alias_text',
        'alias_type',
        'occurrences',
        'canonical_name',
        'canonical_type',
        'canonical_actual_type',
        'canonical_exists',
        'canonical_entity_id',
        'canonical_occurrences',
        'action',
        'action_reason',
        'fuseki_uri',
        'canonical_fuseki_uri',
        'category'
    ]

    with open(OUTPUT_PATH, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(duplicates)

    # Print summary
    print("\n" + "=" * 80)
    print("ALIAS AUDIT SUMMARY")
    print("=" * 80)

    action_counts = {}
    for dup in duplicates:
        action = dup['action']
        action_counts[action] = action_counts.get(action, 0) + 1

    print(f"\nTotal alias duplicates found: {len(duplicates)}")
    print("\nBy action:")
    for action, count in sorted(action_counts.items()):
        print(f"  {action}: {count}")

    print(f"\nReport saved to: {OUTPUT_PATH}")

    # Show top 20 by occurrence count
    print("\n" + "-" * 80)
    print("Top 20 alias duplicates by occurrence count:")
    print("-" * 80)

    sorted_dups = sorted(duplicates, key=lambda x: x['occurrences'], reverse=True)[:20]
    for i, dup in enumerate(sorted_dups, 1):
        print(f"{i:2}. [{dup['action']:20}] {dup['alias_text']!r} ({dup['occurrences']} occurrences)")
        print(f"    → canonical: {dup['canonical_name']!r} ({dup['canonical_type']})")
        print(f"    → reason: {dup.get('action_reason', 'N/A')}")


def main():
    print("=" * 80)
    print("CANONICAL ALIAS AUDIT")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 80)

    # Step 1: Load canonical entities
    alias_map = load_canonical_entities()
    print(f"Loaded {len(alias_map)} alias mappings from canonical_entities.json")

    # Step 2: Find duplicates in entity_registry
    duplicates = find_alias_duplicates(alias_map)
    print(f"Found {len(duplicates)} alias entities that are not canonical entries")

    # Step 3: Classify merges
    duplicates = classify_merges(duplicates)

    # Step 4: Generate report
    generate_report(duplicates)

    print("\n" + "=" * 80)
    print(f"Completed: {datetime.now().isoformat()}")
    print("=" * 80)

    return duplicates


if __name__ == "__main__":
    main()
