#!/usr/bin/env python3
"""
Reprocess website content to extract dates with improved date extraction
"""

import psycopg2
from psycopg2.extras import RealDictCursor
import sys
from datetime import datetime

def main():
    # Connect to database
    conn = psycopg2.connect(
        host="localhost",
        port=5433,
        database="eliza",
        user="postgres",
        password="postgres"
    )

    print("=== REPROCESSING CONTENT FOR DATE EXTRACTION ===\n")

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # First, check what we have
        cur.execute("""
            SELECT COUNT(*) as total,
                   COUNT(published_at) as with_dates,
                   COUNT(*) FILTER (WHERE source_sensor LIKE 'website%') as website_content
            FROM koi_memories
        """)
        stats = cur.fetchone()
        print(f"Current content stats:")
        print(f"  Total memories: {stats['total']}")
        print(f"  With dates: {stats['with_dates']}")
        print(f"  Website content: {stats['website_content']}")

        # Delete existing website content to force reprocessing
        print("\n⚠️  Deleting existing website content to force reprocessing...")
        cur.execute("""
            DELETE FROM koi_memories
            WHERE source_sensor LIKE 'website%'
            RETURNING rid
        """)
        deleted = cur.fetchall()
        print(f"Deleted {len(deleted)} website content items")

        # Also clear the processed URLs cache (if it exists)
        cur.execute("""
            DELETE FROM koi_processed_urls WHERE 1=1
        """)
        print("Cleared processed URLs cache")

        conn.commit()
        print("\n✅ Database cleaned. Website sensor will re-scrape and process content with improved date extraction.")
        print("\n📝 Next steps:")
        print("1. The website sensor is already running and will rescan at the next interval")
        print("2. The improved date extraction code is in place")
        print("3. The semantic event bridge will preserve metadata including dates")
        print("4. Monitor the database for new content with dates")

    conn.close()

if __name__ == "__main__":
    main()