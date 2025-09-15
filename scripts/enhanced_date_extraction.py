#!/usr/bin/env python3
"""
Enhanced publication date extraction for existing KOI memories
Handles sensor-specific formats and patterns
"""

import asyncio
import asyncpg
import json
import re
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger

class SensorSpecificExtractor:
    """Extract dates based on sensor type"""

    @staticmethod
    def extract_discourse(content: Dict[str, Any], metadata: Dict[str, Any]) -> Tuple[Optional[datetime], float]:
        """Extract date from discourse sensor data"""
        try:
            # Discourse posts have dates in the content format
            if isinstance(content, dict):
                text = content.get('text', '')
            else:
                text = str(content)

            # Pattern: "Post by username (2025-09-10T20:31:15.119Z)"
            pattern = r'Post by \w+ \((\d{4}-\d{2}-\d{2}T[\d:\.]+Z?)\)'
            matches = re.findall(pattern, text)
            if matches:
                # Get the earliest date (first post)
                date_str = matches[0]
                dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                return dt, 0.95  # High confidence for discourse

            # Fallback: Look for any ISO date in first 500 chars
            iso_pattern = r'(\d{4}-\d{2}-\d{2}T[\d:\.]+Z?)'
            matches = re.findall(iso_pattern, text[:500])
            if matches:
                dt = datetime.fromisoformat(matches[0].replace('Z', '+00:00'))
                return dt, 0.8

        except Exception as e:
            logger.debug(f"Failed to extract discourse date: {e}")

        return None, 0.0

    @staticmethod
    def extract_github(content: Dict[str, Any], metadata: Dict[str, Any]) -> Tuple[Optional[datetime], float]:
        """Extract date from GitHub sensor data"""
        try:
            # GitHub content might have dates in markdown frontmatter
            if isinstance(content, dict):
                text = content.get('text', '') or content.get('content', '')
            else:
                text = str(content)

            # Look for date in frontmatter
            if text.startswith('---'):
                # YAML frontmatter
                frontmatter = text.split('---')[1] if '---' in text else ''
                date_pattern = r'date:\s*["\']*(\d{4}-\d{2}-\d{2})'
                match = re.search(date_pattern, frontmatter)
                if match:
                    dt = datetime.strptime(match.group(1), '%Y-%m-%d')
                    dt = dt.replace(tzinfo=timezone.utc)
                    return dt, 0.85

            # Look for commit/PR/issue references with dates
            # Pattern: created_at, updated_at in JSON
            json_date_pattern = r'"(?:created_at|updated_at)"\s*:\s*"([^"]+)"'
            match = re.search(json_date_pattern, text)
            if match:
                dt = datetime.fromisoformat(match.group(1).replace('Z', '+00:00'))
                return dt, 0.8

            # Look for dates in markdown content
            # Pattern: "Published: Month DD, YYYY" or "Date: YYYY-MM-DD"
            pub_pattern = r'(?:Published|Date|Created):\s*(\d{4}-\d{2}-\d{2}|\w+ \d{1,2},? \d{4})'
            match = re.search(pub_pattern, text, re.IGNORECASE)
            if match:
                date_str = match.group(1)
                try:
                    if '-' in date_str:
                        dt = datetime.strptime(date_str, '%Y-%m-%d')
                    else:
                        # Try common date formats
                        for fmt in ['%B %d, %Y', '%b %d, %Y', '%B %d %Y']:
                            try:
                                dt = datetime.strptime(date_str.replace(',', ''), fmt)
                                break
                            except:
                                continue
                    dt = dt.replace(tzinfo=timezone.utc)
                    return dt, 0.75
                except:
                    pass

        except Exception as e:
            logger.debug(f"Failed to extract GitHub date: {e}")

        return None, 0.0

    @staticmethod
    def extract_medium(content: Dict[str, Any], metadata: Dict[str, Any]) -> Tuple[Optional[datetime], float]:
        """Extract date from Medium sensor data"""
        try:
            # Medium metadata might have published_date
            if metadata and 'published_date' in metadata:
                date_str = metadata['published_date']
                if date_str:
                    dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    return dt, 0.9

            # Look in content
            if isinstance(content, dict):
                text = content.get('text', '')
            else:
                text = str(content)

            # Medium date patterns
            # Pattern: "Published on Dec 15, 2023"
            pattern = r'Published on (\w+ \d{1,2},? \d{4})'
            match = re.search(pattern, text)
            if match:
                date_str = match.group(1)
                for fmt in ['%B %d, %Y', '%b %d, %Y']:
                    try:
                        dt = datetime.strptime(date_str.replace(',', ''), fmt)
                        dt = dt.replace(tzinfo=timezone.utc)
                        return dt, 0.85
                    except:
                        continue

        except Exception as e:
            logger.debug(f"Failed to extract Medium date: {e}")

        return None, 0.0

    @staticmethod
    def extract_podcast(content: Dict[str, Any], metadata: Dict[str, Any]) -> Tuple[Optional[datetime], float]:
        """Extract date from podcast sensor data"""
        try:
            # Podcast episodes usually have publication dates
            if isinstance(content, dict):
                # Check for pubDate field
                if 'pubDate' in content:
                    dt = datetime.fromisoformat(content['pubDate'].replace('Z', '+00:00'))
                    return dt, 0.95

                text = content.get('text', '')
            else:
                text = str(content)

            # Look for episode date patterns
            # Pattern: "Episode aired on DATE" or "Released: DATE"
            pattern = r'(?:aired|released|published|recorded)(?:\s+on)?:\s*(\d{4}-\d{2}-\d{2}|\w+ \d{1,2},? \d{4})'
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                date_str = match.group(1)
                if '-' in date_str:
                    dt = datetime.strptime(date_str, '%Y-%m-%d')
                else:
                    for fmt in ['%B %d, %Y', '%b %d, %Y']:
                        try:
                            dt = datetime.strptime(date_str.replace(',', ''), fmt)
                            break
                        except:
                            continue
                dt = dt.replace(tzinfo=timezone.utc)
                return dt, 0.8

        except Exception as e:
            logger.debug(f"Failed to extract podcast date: {e}")

        return None, 0.0

    @staticmethod
    def extract_website(content: Dict[str, Any], metadata: Dict[str, Any]) -> Tuple[Optional[datetime], float]:
        """Extract date from website sensor data"""
        try:
            if isinstance(content, dict):
                text = content.get('text', '')
            else:
                text = str(content)

            # Website dates are less reliable - look for common patterns
            # ISO dates
            iso_pattern = r'(\d{4}-\d{2}-\d{2})'
            matches = re.findall(iso_pattern, text[:1000])  # Check first 1000 chars
            if matches:
                # Take the first date found
                dt = datetime.strptime(matches[0], '%Y-%m-%d')
                dt = dt.replace(tzinfo=timezone.utc)
                return dt, 0.6  # Lower confidence for websites

            # Written dates
            written_pattern = r'(\w+ \d{1,2},? \d{4})'
            matches = re.findall(written_pattern, text[:1000])
            if matches:
                for date_str in matches:
                    for fmt in ['%B %d %Y', '%b %d %Y', '%B %d, %Y']:
                        try:
                            dt = datetime.strptime(date_str.replace(',', ''), fmt)
                            dt = dt.replace(tzinfo=timezone.utc)
                            return dt, 0.55
                        except:
                            continue

        except Exception as e:
            logger.debug(f"Failed to extract website date: {e}")

        return None, 0.0


async def extract_dates_for_sensor(sensor_pattern: str, conn: asyncpg.Connection):
    """Extract and update dates for a specific sensor"""

    # Determine extraction method based on sensor type
    extractor = None
    if 'discourse' in sensor_pattern.lower():
        extractor = SensorSpecificExtractor.extract_discourse
    elif 'github' in sensor_pattern.lower():
        extractor = SensorSpecificExtractor.extract_github
    elif 'medium' in sensor_pattern.lower():
        extractor = SensorSpecificExtractor.extract_medium
    elif 'podcast' in sensor_pattern.lower():
        extractor = SensorSpecificExtractor.extract_podcast
    elif 'website' in sensor_pattern.lower():
        extractor = SensorSpecificExtractor.extract_website
    else:
        logger.warning(f"No specific extractor for {sensor_pattern}")
        return 0

    # Get memories without dates
    query = """
        SELECT id, rid, content, metadata, source_sensor
        FROM koi_memories
        WHERE source_sensor LIKE $1
        AND published_at IS NULL
        AND content::text NOT LIKE '%sensor_heartbeat%'
        LIMIT 1000
    """

    rows = await conn.fetch(query, sensor_pattern)
    logger.info(f"Processing {len(rows)} memories from {sensor_pattern}")

    updates = []
    for row in rows:
        content = row['content']
        metadata = row['metadata']

        # Extract date using sensor-specific method
        pub_date, confidence = extractor(content, metadata)

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

        logger.info(f"Updated {len(updates)} memories for {sensor_pattern}")

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

        # Process each sensor type
        sensors = [
            'discourse-sensor%',
            'github-sensor%',
            'gitlab-sensor%',
            'website-sensor%',
            'medium-sensor%',
            'podcast-sensor%',
            'notion-sensor%'
        ]

        total_updated = 0
        for sensor in sensors:
            logger.info(f"\nProcessing {sensor}...")
            updated = await extract_dates_for_sensor(sensor, conn)
            total_updated += updated

        # Final status
        with_dates_after = await conn.fetchval("SELECT COUNT(*) FROM koi_memories WHERE published_at IS NOT NULL")

        logger.info(f"\n=== EXTRACTION COMPLETE ===")
        logger.info(f"Total memories updated: {total_updated}")
        logger.info(f"Memories with dates before: {with_dates}")
        logger.info(f"Memories with dates after: {with_dates_after}")
        logger.info(f"New dates extracted: {with_dates_after - with_dates}")

        # Show sample by sensor
        sample = await conn.fetch("""
            SELECT source_sensor,
                   COUNT(*) as total,
                   COUNT(published_at) as with_dates,
                   ROUND(100.0 * COUNT(published_at) / COUNT(*), 1) as percent
            FROM koi_memories
            GROUP BY source_sensor
            ORDER BY total DESC
            LIMIT 10
        """)

        logger.info("\nDate coverage by sensor:")
        for row in sample:
            logger.info(f"  {row['source_sensor'][:30]:30} : {row['with_dates']:5}/{row['total']:5} ({row['percent']:5.1f}%)")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())