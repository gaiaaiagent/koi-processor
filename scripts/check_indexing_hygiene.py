#!/usr/bin/env python3
"""
KOI Indexing Hygiene Check

Validates that no forbidden content patterns have been indexed.
Use in CI or as a periodic health check.

Exit codes:
  0 - All checks passed
  1 - Violations found
  2 - Error during check
"""

import os
import sys
from datetime import datetime

import psycopg2

# Forbidden patterns - content that should never be indexed
FORBIDDEN_PATTERNS = [
    # Crawl dump files from GitHub
    ('%_indexing_discourse_storage_%', 'Discourse crawl dumps indexed via GitHub'),
    ('%_indexing_twitter_storage_%', 'Twitter crawl dumps indexed via GitHub'),
    ('%_indexing_podcast_storage_%', 'Podcast crawl dumps indexed via GitHub'),
    # Add more patterns as needed
]


def get_db_connection():
    """Get database connection from environment."""
    db_url = os.environ.get("POSTGRES_URL", "postgresql://postgres:postgres@localhost:5433/eliza")
    return psycopg2.connect(db_url)


def check_forbidden_patterns(conn):
    """Check for forbidden patterns in koi_memories."""
    violations = []

    with conn.cursor() as cur:
        for pattern, description in FORBIDDEN_PATTERNS:
            # Check RID pattern
            cur.execute(
                "SELECT COUNT(*) FROM koi_memories WHERE rid LIKE %s AND superseded_at IS NULL",
                (pattern,),
            )
            count = cur.fetchone()[0]

            if count > 0:
                # Get sample RIDs
                cur.execute(
                    "SELECT rid FROM koi_memories WHERE rid LIKE %s AND superseded_at IS NULL LIMIT 3",
                    (pattern,),
                )
                samples = [row[0] for row in cur.fetchall()]
                violations.append(
                    {
                        "pattern": pattern,
                        "description": description,
                        "count": count,
                        "samples": samples,
                    }
                )

    return violations


def main():
    print(f"KOI Indexing Hygiene Check - {datetime.now().isoformat()}")
    print("=" * 60)

    try:
        conn = get_db_connection()
    except Exception as e:
        print(f"ERROR: Failed to connect to database: {e}")
        sys.exit(2)

    try:
        violations = check_forbidden_patterns(conn)

        if violations:
            print(f"\nFOUND {len(violations)} VIOLATION(S):\n")
            for v in violations:
                print(f"  Pattern: {v['pattern']}")
                print(f"  Issue: {v['description']}")
                print(f"  Count: {v['count']} records")
                print(f"  Samples: {v['samples'][:3]}")
                print()

            print("ACTION REQUIRED: Delete these records using:")
            print("  DELETE FROM koi_memories WHERE rid LIKE '<pattern>' AND superseded_at IS NULL;")
            print()
            sys.exit(1)

        print("\nAll checks passed. No forbidden patterns found.")
        sys.exit(0)

    except Exception as e:
        print(f"ERROR during check: {e}")
        sys.exit(2)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
