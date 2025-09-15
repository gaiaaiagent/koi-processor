#!/usr/bin/env python3
"""
Extract publication dates from KOI memories content and update database
"""

import asyncio
import asyncpg
import json
import re
from datetime import datetime
from typing import Optional, Tuple
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger

# Date patterns to search for in content
DATE_PATTERNS = [
    # ISO format: 2025-06-26T17:11:26.091Z
    r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)',
    # Discourse post format: Post by X (date)
    r'Post by \w+ \(([^)]+)\)',
    # GitHub format: created_at, updated_at
    r'"(?:created_at|updated_at|published_at|date)"\s*:\s*"([^"]+)"',
    # Date in URLs: /2025/06/26/ or /2025-06-26/
    r'/(\d{4}[/-]\d{2}[/-]\d{2})/',
    # Date in text: June 26, 2025
    r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4})',
    # Simple date: 2025-06-26
    r'(\d{4}-\d{2}-\d{2})',
    # Posted on format
    r'Posted on ([^,\n]+)',
    # Published: format
    r'Published:\s*([^\n]+)',
]

def extract_date_from_content(content: dict) -> Tuple[Optional[datetime], float]:
    """
    Extract publication date from content with confidence score

    Returns:
        Tuple of (datetime or None, confidence score 0-1)
    """
    try:
        content_text = ""

        # Extract text from content
        if isinstance(content, dict):
            # Try different keys
            content_text = str(content.get('text', ''))
            if not content_text:
                content_text = str(content.get('content', ''))
            if not content_text:
                content_text = json.dumps(content)
        elif isinstance(content, str):
            content_text = content

        # Search for date patterns
        for pattern in DATE_PATTERNS:
            matches = re.findall(pattern, content_text[:2000])  # Check first 2000 chars
            if matches:
                date_str = matches[0]

                # Try to parse the date
                try:
                    # ISO format
                    if 'T' in date_str and ':' in date_str:
                        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                        return dt, 0.9  # High confidence for ISO dates

                    # URL date format (2025/06/26 or 2025-06-26)
                    if '/' in date_str or '-' in date_str:
                        date_str = date_str.replace('/', '-')
                        try:
                            dt = datetime.strptime(date_str, '%Y-%m-%d')
                            return dt, 0.8  # Good confidence for URL dates
                        except:
                            pass

                    # Try other formats
                    for fmt in ['%B %d, %Y', '%b %d, %Y', '%Y-%m-%d', '%Y/%m/%d', '%d %B %Y', '%d %b %Y']:
                        try:
                            dt = datetime.strptime(date_str, fmt)
                            return dt, 0.7  # Medium confidence for parsed dates
                        except:
                            continue
                except Exception as e:
                    logger.debug(f"Failed to parse date {date_str}: {e}")
                    continue

        # Check metadata for dates
        if isinstance(content, dict):
            meta = content.get('metadata', {})
            if isinstance(meta, dict):
                for key in ['published_at', 'created_at', 'date', 'timestamp']:
                    if key in meta:
                        try:
                            dt = datetime.fromisoformat(str(meta[key]).replace('Z', '+00:00'))
                            return dt, 0.8
                        except:
                            pass

    except Exception as e:
        logger.error(f"Error extracting date: {e}")

    return None, 0.0

async def extract_dates_for_source(source_pattern: str, conn: asyncpg.Connection):
    """Extract and update dates for a specific source sensor"""

    # Get memories without dates
    query = """
        SELECT id, rid, content, metadata, source_sensor
        FROM koi_memories
        WHERE source_sensor LIKE $1
        AND published_at IS NULL
        LIMIT 500
    """

    rows = await conn.fetch(query, source_pattern)
    logger.info(f"Processing {len(rows)} memories from {source_pattern}")

    updates = []
    for row in rows:
        content = row['content']

        # Extract date
        pub_date, confidence = extract_date_from_content(content)

        if pub_date and confidence > 0.5:
            updates.append((row['id'], pub_date, confidence))

    # Batch update
    if updates:
        logger.info(f"Updating {len(updates)} memories with extracted dates")

        update_query = """
            UPDATE koi_memories
            SET published_at = $2,
                published_confidence = $3
            WHERE id = $1
        """

        for update in updates:
            await conn.execute(update_query, *update)

        logger.info(f"Updated {len(updates)} memories for {source_pattern}")

    return len(updates)

async def main():
    """Main extraction process"""

    # Connect to database
    db_url = os.getenv('POSTGRES_URL', 'postgresql://postgres:postgres@localhost:5433/eliza')
    conn = await asyncpg.connect(db_url)

    try:
        # Check current status
        total_count = await conn.fetchval("SELECT COUNT(*) FROM koi_memories")
        with_dates = await conn.fetchval("SELECT COUNT(*) FROM koi_memories WHERE published_at IS NOT NULL")

        logger.info(f"Total KOI memories: {total_count}")
        logger.info(f"With publication dates: {with_dates}")
        logger.info(f"Missing dates: {total_count - with_dates}")

        # Process each source type
        sources = [
            'discourse-sensor%',
            'github-sensor%',
            'gitlab-sensor%',
            'website-sensor%',
            'medium-sensor%',
            'notion-sensor%',
            'podcast-sensor%'
        ]

        total_updated = 0
        for source in sources:
            updated = await extract_dates_for_source(source, conn)
            total_updated += updated

        # Final status
        with_dates_after = await conn.fetchval("SELECT COUNT(*) FROM koi_memories WHERE published_at IS NOT NULL")

        logger.info(f"\n=== EXTRACTION COMPLETE ===")
        logger.info(f"Total memories updated: {total_updated}")
        logger.info(f"Memories with dates before: {with_dates}")
        logger.info(f"Memories with dates after: {with_dates_after}")
        logger.info(f"New dates extracted: {with_dates_after - with_dates}")

        # Show sample of updated content
        sample = await conn.fetch("""
            SELECT source_sensor, published_at, published_confidence
            FROM koi_memories
            WHERE published_at IS NOT NULL
            ORDER BY published_confidence DESC
            LIMIT 5
        """)

        logger.info("\nSample of extracted dates:")
        for row in sample:
            logger.info(f"  {row['source_sensor'][:30]}: {row['published_at']} (confidence: {row['published_confidence']:.2f})")

    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())