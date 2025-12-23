#!/usr/bin/env python3
"""
Apply Safe Entity Merges (FIX-006)

Merges entities identified by dedup_dry_run.py using only the safest tiers:
- tier1_normalized: Exact matches after normalization
- tier1_5_canonical: Canonical alias matches from verified registry

Usage:
    cd /opt/projects/koi-processor && set -a; source .env; set +a
    python scripts/apply_safe_merges.py --input merges_post_deploy.csv --dry-run
    python scripts/apply_safe_merges.py --input merges_post_deploy.csv --apply

Author: Claude Code
Date: 2025-12-23
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("Error: psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)


# Only these methods are considered safe for auto-merge
SAFE_METHODS = {"tier1_normalized", "tier1_5_canonical"}


def get_db_config() -> Dict:
    """Get database configuration from environment."""
    return {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": int(os.getenv("POSTGRES_PORT", 5433)),
        "database": os.getenv("POSTGRES_DB", "eliza"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
    }


def load_merge_proposals(csv_path: str) -> List[Dict]:
    """Load and filter merge proposals from CSV."""
    proposals = []
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["method"] in SAFE_METHODS:
                proposals.append(row)
    return proposals


def get_entity_id(cursor, fuseki_uri: str) -> int:
    """Get entity_registry.id from fuseki_uri."""
    cursor.execute(
        "SELECT id FROM entity_registry WHERE fuseki_uri = %s",
        (fuseki_uri,)
    )
    result = cursor.fetchone()
    return result[0] if result else None


def merge_entity(conn, winner_uri: str, loser_uri: str, dry_run: bool = True) -> Dict:
    """
    Merge loser entity into winner entity.

    Steps:
    1. Get entity IDs
    2. Update koi_relationships (subject_entity_id, object_entity_id)
    3. Update koi_entity_chunk_links (entity_uri)
    4. Add loser's occurrence_count to winner
    5. Delete loser from entity_registry

    Returns dict with merge stats.
    """
    cursor = conn.cursor()
    stats = {
        "winner_uri": winner_uri,
        "loser_uri": loser_uri,
        "relationships_updated": 0,
        "chunk_links_updated": 0,
        "occurrence_count_added": 0,
        "success": False,
        "error": None,
    }

    try:
        # Use a savepoint so one failing merge doesn't rollback prior successful merges.
        # (Previous versions used conn.rollback(), which rolled back the entire transaction.)
        if not dry_run:
            cursor.execute("SAVEPOINT fix006_merge")

        # Get entity IDs
        winner_id = get_entity_id(cursor, winner_uri)
        loser_id = get_entity_id(cursor, loser_uri)

        if not winner_id:
            stats["error"] = f"Winner entity not found: {winner_uri}"
            return stats

        if not loser_id:
            stats["error"] = f"Loser entity not found: {loser_uri}"
            return stats

        # Get loser's occurrence_count
        cursor.execute(
            "SELECT occurrence_count FROM entity_registry WHERE id = %s",
            (loser_id,)
        )
        loser_count = cursor.fetchone()[0] or 0
        stats["occurrence_count_added"] = loser_count

        if dry_run:
            # Count what would be updated
            cursor.execute(
                """SELECT COUNT(*) FROM koi_relationships
                   WHERE subject_entity_id = %s OR object_entity_id = %s""",
                (loser_id, loser_id)
            )
            stats["relationships_updated"] = cursor.fetchone()[0]

            cursor.execute(
                "SELECT COUNT(*) FROM koi_entity_chunk_links WHERE entity_uri = %s",
                (loser_uri,)
            )
            stats["chunk_links_updated"] = cursor.fetchone()[0]

            stats["success"] = True
            return stats

        # === APPLY MODE ===

        # Update koi_relationships - subject
        cursor.execute(
            """UPDATE koi_relationships
               SET subject_entity_id = %s
               WHERE subject_entity_id = %s""",
            (winner_id, loser_id)
        )
        subject_updated = cursor.rowcount

        # Update koi_relationships - object
        cursor.execute(
            """UPDATE koi_relationships
               SET object_entity_id = %s
               WHERE object_entity_id = %s""",
            (winner_id, loser_id)
        )
        object_updated = cursor.rowcount
        stats["relationships_updated"] = subject_updated + object_updated

        # Update koi_entity_chunk_links
        cursor.execute(
            """UPDATE koi_entity_chunk_links
               SET entity_uri = %s
               WHERE entity_uri = %s""",
            (winner_uri, loser_uri)
        )
        stats["chunk_links_updated"] = cursor.rowcount

        # Update winner's occurrence_count
        cursor.execute(
            """UPDATE entity_registry
               SET occurrence_count = occurrence_count + %s,
                   last_seen_at = NOW()
               WHERE id = %s""",
            (loser_count, winner_id)
        )

        # Delete loser
        cursor.execute(
            "DELETE FROM entity_registry WHERE id = %s",
            (loser_id,)
        )

        stats["success"] = True

    except Exception as e:
        stats["error"] = str(e)
        if not dry_run:
            try:
                cursor.execute("ROLLBACK TO SAVEPOINT fix006_merge")
                cursor.execute("RELEASE SAVEPOINT fix006_merge")
            except Exception:
                # If savepoint rollback fails, fall back to full rollback.
                conn.rollback()
        return stats

    if not dry_run:
        cursor.execute("RELEASE SAVEPOINT fix006_merge")
    return stats


def main():
    parser = argparse.ArgumentParser(description="Apply Safe Entity Merges")
    parser.add_argument("--input", required=True, help="Input CSV from dedup_dry_run.py")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    parser.add_argument("--apply", action="store_true", help="Apply the merges")
    parser.add_argument("--log", help="Output log file (JSON)")
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        print("Error: Must specify either --dry-run or --apply")
        sys.exit(1)

    if args.dry_run and args.apply:
        print("Error: Cannot specify both --dry-run and --apply")
        sys.exit(1)

    dry_run = args.dry_run

    print("=" * 60)
    print("FIX-006 Safe Entity Merge")
    print("=" * 60)
    print(f"Mode: {'DRY-RUN (preview only)' if dry_run else 'APPLY (making changes)'}")
    print(f"Input: {args.input}")
    print(f"Safe methods: {', '.join(SAFE_METHODS)}")
    print()

    # Load proposals
    proposals = load_merge_proposals(args.input)
    print(f"Loaded {len(proposals)} safe merge proposals")

    # Group by method
    by_method = {}
    for p in proposals:
        method = p["method"]
        by_method[method] = by_method.get(method, 0) + 1
    for method, count in sorted(by_method.items()):
        print(f"  {method}: {count}")
    print()

    # Connect to database
    db_config = get_db_config()
    print(f"Connecting to {db_config['host']}:{db_config['port']}/{db_config['database']}...")
    conn = psycopg2.connect(**db_config)

    # Process merges
    results = []
    success_count = 0
    error_count = 0
    total_relationships = 0
    total_chunk_links = 0
    total_occurrences = 0

    print(f"\nProcessing {len(proposals)} merges...")

    for i, proposal in enumerate(proposals):
        winner_uri = proposal["winner_uri"]
        loser_uri = proposal["loser_uri"]

        stats = merge_entity(conn, winner_uri, loser_uri, dry_run=dry_run)
        stats["winner_name"] = proposal["winner_name"]
        stats["loser_name"] = proposal["loser_name"]
        stats["method"] = proposal["method"]

        if stats["success"]:
            success_count += 1
            total_relationships += stats["relationships_updated"]
            total_chunk_links += stats["chunk_links_updated"]
            total_occurrences += stats["occurrence_count_added"]
        else:
            error_count += 1
            print(f"  Error: {stats['error']}")

        results.append(stats)

        # Progress update every 50
        if (i + 1) % 50 == 0:
            print(f"  Processed {i + 1}/{len(proposals)}...")

    # Commit if applying
    if not dry_run:
        conn.commit()
        print("\nChanges committed to database.")

    conn.close()

    # Summary
    print()
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"Total proposals: {len(proposals)}")
    print(f"Successful: {success_count}")
    print(f"Errors: {error_count}")
    print()
    print(f"Relationships {'would be ' if dry_run else ''}updated: {total_relationships}")
    print(f"Chunk links {'would be ' if dry_run else ''}updated: {total_chunk_links}")
    print(f"Occurrence counts {'would be ' if dry_run else ''}added: {total_occurrences}")

    if dry_run:
        print()
        print("This was a DRY-RUN. No changes were made.")
        print("Run with --apply to execute the merges.")

    # Write log
    if args.log:
        log_data = {
            "timestamp": datetime.now().isoformat(),
            "mode": "dry-run" if dry_run else "apply",
            "input_file": args.input,
            "total_proposals": len(proposals),
            "success_count": success_count,
            "error_count": error_count,
            "total_relationships_updated": total_relationships,
            "total_chunk_links_updated": total_chunk_links,
            "total_occurrences_added": total_occurrences,
            "results": results,
        }
        with open(args.log, "w") as f:
            json.dump(log_data, f, indent=2)
        print(f"\nLog written to: {args.log}")


if __name__ == "__main__":
    main()
