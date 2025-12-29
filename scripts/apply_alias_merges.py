#!/usr/bin/env python3
"""
Apply Alias Merges Script
Created: 2025-12-29

Merges alias entities into their canonical counterparts with full backup.

Process:
1. Create backup tables for affected IDs
2. Merge relationships (combine counts, update last_seen)
3. Update chunk links to point to canonical
4. Delete alias entity rows
"""

import csv
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor

# ============================================================================
# CONFIGURATION
# ============================================================================
AUDIT_REPORT_PATH = Path(__file__).parent.parent / "data" / "alias_audit_report.csv"
BACKUP_PREFIX = "alias_merge_backup_20251229"

# Define which actions are safe to merge automatically
SAFE_ACTIONS = {'MERGE_SAFE', 'MERGE_RETYPE', 'REVIEW_TYPE', 'REVIEW'}

# Entities to explicitly skip (by alias_entity_id)
SKIP_IDS = {24503}  # 'registry' - too generic


def get_db_connection():
    """Get PostgreSQL connection to production server."""
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5433)),
        database=os.getenv("POSTGRES_DB", "eliza"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )


def load_merge_candidates() -> List[Dict]:
    """Load merge candidates from audit report."""
    print(f"Loading merge candidates from: {AUDIT_REPORT_PATH}")

    candidates = []
    with open(AUDIT_REPORT_PATH, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            alias_id = int(row['alias_entity_id'])
            action = row['action']

            # Skip deferred or explicitly excluded
            if action.startswith('DEFER') or action.startswith('SKIP'):
                print(f"  Skipping (deferred): {row['alias_text']} - {row['action']}")
                continue

            if alias_id in SKIP_IDS:
                print(f"  Skipping (explicit): {row['alias_text']}")
                continue

            # Only include safe actions
            if action in SAFE_ACTIONS:
                candidates.append({
                    'alias_id': alias_id,
                    'alias_text': row['alias_text'],
                    'alias_type': row['alias_type'],
                    'alias_uri': row['fuseki_uri'],
                    'canonical_id': int(row['canonical_entity_id']),
                    'canonical_text': row['canonical_name'],
                    'canonical_type': row['canonical_actual_type'],
                    'canonical_uri': row['canonical_fuseki_uri'],
                    'action': action,
                    'reason': row['action_reason']
                })

    return candidates


def create_backups(conn, candidates: List[Dict]) -> Dict[str, str]:
    """Create backup tables for affected entities."""
    cursor = conn.cursor()

    alias_ids = [c['alias_id'] for c in candidates]
    canonical_ids = list(set(c['canonical_id'] for c in candidates))
    all_entity_ids = list(set(alias_ids + canonical_ids))

    print(f"\nCreating backups for {len(all_entity_ids)} entity IDs...")

    backup_tables = {}

    # Backup entity_registry
    table_name = f"{BACKUP_PREFIX}_entity_registry"
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {table_name} AS
        SELECT * FROM entity_registry WHERE id = ANY(%s)
    """, (all_entity_ids,))
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    count = cursor.fetchone()[0]
    backup_tables['entity_registry'] = table_name
    print(f"  Created {table_name} ({count} rows)")

    # Backup koi_relationships (by subject_entity_id or object_entity_id)
    table_name = f"{BACKUP_PREFIX}_koi_relationships"
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {table_name} AS
        SELECT * FROM koi_relationships
        WHERE subject_entity_id = ANY(%s) OR object_entity_id = ANY(%s)
    """, (all_entity_ids, all_entity_ids))
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    count = cursor.fetchone()[0]
    backup_tables['koi_relationships'] = table_name
    print(f"  Created {table_name} ({count} rows)")

    # Get fuseki_uris for chunk link backup
    alias_uris = [c['alias_uri'] for c in candidates]

    # Backup koi_entity_chunk_links
    table_name = f"{BACKUP_PREFIX}_koi_entity_chunk_links"
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {table_name} AS
        SELECT * FROM koi_entity_chunk_links
        WHERE entity_uri = ANY(%s)
    """, (alias_uris,))
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    count = cursor.fetchone()[0]
    backup_tables['koi_entity_chunk_links'] = table_name
    print(f"  Created {table_name} ({count} rows)")

    conn.commit()
    return backup_tables


def merge_relationships(conn, alias_id: int, canonical_id: int) -> Tuple[int, int]:
    """
    Merge relationships from alias to canonical.

    For relationships where alias is subject/object:
    - If equivalent relationship exists for canonical, merge counts
    - Otherwise, update entity_id to point to canonical
    """
    cursor = conn.cursor()
    updated = 0
    merged = 0

    # Handle relationships where alias is subject
    cursor.execute("""
        SELECT id, predicate, object_entity_id, occurrence_count, last_seen_at
        FROM koi_relationships WHERE subject_entity_id = %s
    """, (alias_id,))
    alias_as_subject = cursor.fetchall()

    for rel in alias_as_subject:
        rel_id, pred, obj_id, occ, last_seen = rel

        # Check if canonical already has this relationship
        cursor.execute("""
            SELECT id, occurrence_count, last_seen_at FROM koi_relationships
            WHERE subject_entity_id = %s AND predicate = %s AND object_entity_id = %s
        """, (canonical_id, pred, obj_id))
        existing = cursor.fetchone()

        if existing:
            # Merge: add counts, update last_seen
            exist_id, exist_occ, exist_last_seen = existing
            new_occ = (occ or 0) + (exist_occ or 0)
            new_last_seen = max(last_seen, exist_last_seen) if (exist_last_seen and last_seen) else (last_seen or exist_last_seen)

            cursor.execute("""
                UPDATE koi_relationships
                SET occurrence_count = %s, last_seen_at = %s
                WHERE id = %s
            """, (new_occ, new_last_seen, exist_id))

            # Delete alias relationship
            cursor.execute("DELETE FROM koi_relationships WHERE id = %s", (rel_id,))
            merged += 1
        else:
            # Update to point to canonical
            cursor.execute("""
                UPDATE koi_relationships
                SET subject_entity_id = %s
                WHERE id = %s
            """, (canonical_id, rel_id))
            updated += 1

    # Handle relationships where alias is object
    cursor.execute("""
        SELECT id, predicate, subject_entity_id, occurrence_count, last_seen_at
        FROM koi_relationships WHERE object_entity_id = %s
    """, (alias_id,))
    alias_as_object = cursor.fetchall()

    for rel in alias_as_object:
        rel_id, pred, subj_id, occ, last_seen = rel

        # Check if canonical already has this relationship
        cursor.execute("""
            SELECT id, occurrence_count, last_seen_at FROM koi_relationships
            WHERE subject_entity_id = %s AND predicate = %s AND object_entity_id = %s
        """, (subj_id, pred, canonical_id))
        existing = cursor.fetchone()

        if existing:
            # Merge
            exist_id, exist_occ, exist_last_seen = existing
            new_occ = (occ or 0) + (exist_occ or 0)
            new_last_seen = max(last_seen, exist_last_seen) if (exist_last_seen and last_seen) else (last_seen or exist_last_seen)

            cursor.execute("""
                UPDATE koi_relationships
                SET occurrence_count = %s, last_seen_at = %s
                WHERE id = %s
            """, (new_occ, new_last_seen, exist_id))

            cursor.execute("DELETE FROM koi_relationships WHERE id = %s", (rel_id,))
            merged += 1
        else:
            # Update
            cursor.execute("""
                UPDATE koi_relationships
                SET object_entity_id = %s
                WHERE id = %s
            """, (canonical_id, rel_id))
            updated += 1

    return updated, merged


def update_chunk_links(conn, alias_uri: str, canonical_uri: str, canonical_type: str) -> int:
    """Update chunk links to point to canonical entity."""
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE koi_entity_chunk_links
        SET entity_uri = %s, entity_type = %s
        WHERE entity_uri = %s
    """, (canonical_uri, canonical_type, alias_uri))

    return cursor.rowcount


def update_canonical_counts(conn, canonical_id: int, alias_occurrences: int):
    """Add alias occurrence count to canonical."""
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE entity_registry
        SET occurrence_count = occurrence_count + %s,
            last_seen_at = GREATEST(last_seen_at, NOW())
        WHERE id = %s
    """, (alias_occurrences, canonical_id))


def delete_alias_entity(conn, alias_id: int) -> bool:
    """Delete alias entity from registry."""
    cursor = conn.cursor()

    cursor.execute("DELETE FROM entity_registry WHERE id = %s", (alias_id,))
    return cursor.rowcount > 0


def apply_merges(candidates: List[Dict], dry_run: bool = False):
    """Apply all merges."""
    print("\n" + "=" * 80)
    print("APPLYING ALIAS MERGES")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print("=" * 80)

    if not candidates:
        print("No candidates to merge!")
        return

    conn = get_db_connection()

    # Create backups first
    if not dry_run:
        backup_tables = create_backups(conn, candidates)
    else:
        print("\n[DRY RUN] Would create backup tables")
        backup_tables = {}

    # Apply each merge
    stats = {
        'entities_merged': 0,
        'relationships_updated': 0,
        'relationships_merged': 0,
        'chunk_links_updated': 0,
        'errors': []
    }

    print(f"\nProcessing {len(candidates)} merge candidates...\n")

    for i, candidate in enumerate(candidates, 1):
        print(f"[{i}/{len(candidates)}] {candidate['alias_text']!r} → {candidate['canonical_text']!r}")
        print(f"    Action: {candidate['action']}")
        print(f"    Alias ID: {candidate['alias_id']}, Canonical ID: {candidate['canonical_id']}")

        try:
            if dry_run:
                print("    [DRY RUN] Would merge relationships and chunk links")
                print("    [DRY RUN] Would delete alias entity")
            else:
                # 1. Merge relationships
                rel_updated, rel_merged = merge_relationships(
                    conn,
                    candidate['alias_id'],
                    candidate['canonical_id']
                )
                print(f"    Relationships: {rel_updated} updated, {rel_merged} merged")
                stats['relationships_updated'] += rel_updated
                stats['relationships_merged'] += rel_merged

                # 2. Update chunk links
                chunks_updated = update_chunk_links(
                    conn,
                    candidate['alias_uri'],
                    candidate['canonical_uri'],
                    candidate['canonical_type']
                )
                print(f"    Chunk links: {chunks_updated} updated")
                stats['chunk_links_updated'] += chunks_updated

                # 3. Note: We don't add occurrence counts since they may be duplicated
                # The canonical already has its own counts

                # 4. Delete alias entity
                deleted = delete_alias_entity(conn, candidate['alias_id'])
                if deleted:
                    print(f"    Entity deleted: {candidate['alias_id']}")
                    stats['entities_merged'] += 1
                else:
                    print(f"    WARNING: Entity {candidate['alias_id']} not found for deletion")

                conn.commit()

        except Exception as e:
            print(f"    ERROR: {e}")
            stats['errors'].append((candidate['alias_id'], str(e)))
            if not dry_run:
                conn.rollback()

    conn.close()

    # Print summary
    print("\n" + "=" * 80)
    print("MERGE SUMMARY")
    print("=" * 80)

    print(f"\nEntities merged: {stats['entities_merged']}")
    print(f"Relationships updated: {stats['relationships_updated']}")
    print(f"Relationships merged (deduplicated): {stats['relationships_merged']}")
    print(f"Chunk links updated: {stats['chunk_links_updated']}")

    if stats['errors']:
        print(f"\nErrors: {len(stats['errors'])}")
        for alias_id, error in stats['errors']:
            print(f"  - Entity {alias_id}: {error}")

    if backup_tables:
        print("\nBackup tables created:")
        for table_type, table_name in backup_tables.items():
            print(f"  - {table_name}")

    return stats


def main():
    print("=" * 80)
    print("ALIAS MERGE PROCESSOR")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 80)

    # Check for --dry-run flag
    dry_run = '--dry-run' in sys.argv

    # Load candidates
    candidates = load_merge_candidates()
    print(f"\nLoaded {len(candidates)} merge candidates")

    if not candidates:
        print("No candidates to process.")
        return

    # Show summary before proceeding
    print("\nMerge candidates:")
    for c in candidates:
        print(f"  - {c['alias_text']!r} ({c['alias_type']}) → {c['canonical_text']!r} ({c['canonical_type']})")

    if not dry_run:
        print("\n" + "!" * 80)
        print("WARNING: This will modify production data!")
        print("!" * 80)
        response = input("\nProceed with merge? (type 'yes' to confirm): ")
        if response.lower() != 'yes':
            print("Aborted.")
            return

    # Apply merges
    stats = apply_merges(candidates, dry_run=dry_run)

    print("\n" + "=" * 80)
    print(f"Completed: {datetime.now().isoformat()}")
    print("=" * 80)


if __name__ == "__main__":
    main()
