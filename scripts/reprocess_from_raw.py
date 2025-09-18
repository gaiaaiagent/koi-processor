#!/usr/bin/env python3
"""
Reprocess content from raw artifacts stored in koi_content table
Demonstrates the improved three-layer storage architecture
"""

import psycopg2
from psycopg2.extras import RealDictCursor
import json
import hashlib
from datetime import datetime
from typing import Dict, List, Optional
import re
from bs4 import BeautifulSoup

# Database connection
DB_CONFIG = {
    "host": "localhost",
    "port": 5433,
    "database": "eliza",
    "user": "postgres",
    "password": "postgres"
}

def extract_metadata_from_raw(raw_content: str, content_type: str) -> Dict:
    """Extract metadata from raw HTML/JSON content"""
    metadata = {}

    if content_type == 'html' and raw_content:
        soup = BeautifulSoup(raw_content, 'html.parser')

        # Extract title
        title_elem = soup.find('title')
        if title_elem:
            metadata['title'] = title_elem.get_text(strip=True)

        # Extract meta description
        desc_elem = soup.find('meta', {'name': 'description'})
        if desc_elem:
            metadata['description'] = desc_elem.get('content', '')

        # Extract published date from meta tags
        date_elem = soup.find('meta', {'property': 'article:published_time'})
        if not date_elem:
            date_elem = soup.find('time', {'datetime': True})
        if date_elem:
            date_str = date_elem.get('content') or date_elem.get('datetime')
            if date_str:
                try:
                    # Parse ISO date
                    metadata['published_at'] = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                except:
                    pass

        # Extract main content
        # Try to find main content area
        content_elem = soup.find('article') or soup.find('main') or soup.find('div', class_='content')
        if content_elem:
            metadata['extracted_text'] = content_elem.get_text(separator=' ', strip=True)[:5000]
        else:
            metadata['extracted_text'] = soup.get_text(separator=' ', strip=True)[:5000]

    elif content_type == 'json' and raw_content:
        try:
            data = json.loads(raw_content)
            metadata['title'] = data.get('title', '')
            metadata['description'] = data.get('description', '')
            metadata['extracted_text'] = json.dumps(data, indent=2)[:5000]
            if 'published_at' in data:
                metadata['published_at'] = data['published_at']
        except:
            metadata['extracted_text'] = raw_content[:5000]

    return metadata

def chunk_text(text: str, chunk_size: int = 1000) -> List[str]:
    """Split text into chunks for processing"""
    if not text:
        return []

    # Simple chunking by sentences
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current_chunk = ""

    for sentence in sentences:
        if len(current_chunk) + len(sentence) < chunk_size:
            current_chunk += sentence + " "
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = sentence + " "

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks

def reprocess_content(content_rid: str):
    """Reprocess a specific content item from raw artifact"""

    conn = psycopg2.connect(**DB_CONFIG)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 1. Get raw content from koi_content
        cur.execute("""
            SELECT rid, raw_content, content_type, url, scraped_at
            FROM koi_content
            WHERE rid = %s
        """, (content_rid,))

        content = cur.fetchone()
        if not content:
            print(f"Content {content_rid} not found")
            return

        print(f"\n=== REPROCESSING {content_rid} ===")
        print(f"Type: {content['content_type']}")
        print(f"URL: {content['url']}")
        print(f"Scraped: {content['scraped_at']}")

        # 2. Extract metadata from raw content
        metadata = extract_metadata_from_raw(
            content['raw_content'],
            content['content_type']
        )

        print(f"\nExtracted metadata:")
        print(f"  Title: {metadata.get('title', 'N/A')}")
        print(f"  Published: {metadata.get('published_at', 'N/A')}")
        print(f"  Text length: {len(metadata.get('extracted_text', ''))}")

        # 3. Update or create document in koi_memories
        cur.execute("""
            INSERT INTO koi_memories (
                rid, source_sensor, event_type, content, metadata, published_at
            ) VALUES (
                %s, 'reprocessor', 'UPDATE', %s, %s, %s
            )
            ON CONFLICT (rid, version) DO UPDATE SET
                content = EXCLUDED.content,
                metadata = EXCLUDED.metadata,
                published_at = EXCLUDED.published_at,
                updated_at = NOW()
            RETURNING id
        """, (
            content_rid,
            json.dumps({
                'title': metadata.get('title', ''),
                'text': metadata.get('extracted_text', '')
            }),
            json.dumps({
                'url': content['url'],
                'description': metadata.get('description', ''),
                'reprocessed_at': datetime.now().isoformat()
            }),
            metadata.get('published_at')
        ))

        document_id = cur.fetchone()['id']
        print(f"\nUpdated document: {document_id}")

        # 4. Create chunks in koi_memory_chunks
        text_to_chunk = metadata.get('extracted_text', '')
        if text_to_chunk:
            chunks = chunk_text(text_to_chunk)
            print(f"\nCreated {len(chunks)} chunks")

            for idx, chunk_text in enumerate(chunks):
                chunk_rid = f"{content_rid}:chunk_{idx}"

                cur.execute("""
                    INSERT INTO koi_memory_chunks (
                        chunk_rid, document_rid, source_content_rid,
                        chunk_index, total_chunks, content, metadata
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (chunk_rid) DO UPDATE SET
                        content = EXCLUDED.content,
                        metadata = EXCLUDED.metadata
                """, (
                    chunk_rid,
                    content_rid,
                    content_rid,
                    idx,
                    len(chunks),
                    json.dumps({'text': chunk_text}),
                    json.dumps({
                        'url': content['url'],
                        'published_at': metadata.get('published_at', '').isoformat() if metadata.get('published_at') else None
                    })
                ))

        # 5. Update processing status
        cur.execute("""
            INSERT INTO koi_processing_status (
                content_rid, stage, status, completed_at
            ) VALUES (
                %s, 'reprocessed', 'completed', NOW()
            )
            ON CONFLICT (content_rid, stage) DO UPDATE SET
                status = 'completed',
                completed_at = NOW()
        """, (content_rid,))

        conn.commit()
        print(f"\n✅ Successfully reprocessed {content_rid}")

def list_raw_content():
    """List all raw content available for reprocessing"""

    conn = psycopg2.connect(**DB_CONFIG)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT
                c.rid,
                c.url,
                c.content_type,
                c.scraped_at,
                LENGTH(c.raw_content) as raw_size,
                m.published_at,
                COUNT(ch.id) as chunk_count
            FROM koi_content c
            LEFT JOIN koi_memories m ON m.rid = c.rid
            LEFT JOIN koi_memory_chunks ch ON ch.source_content_rid = c.rid
            WHERE c.raw_content IS NOT NULL
            GROUP BY c.rid, c.url, c.content_type, c.scraped_at, c.raw_content, m.published_at
            ORDER BY c.scraped_at DESC
            LIMIT 20
        """)

        results = cur.fetchall()

        print("\n=== RAW CONTENT AVAILABLE FOR REPROCESSING ===\n")
        print(f"Found {len(results)} items with raw content\n")

        for item in results:
            print(f"RID: {item['rid']}")
            print(f"  URL: {item['url']}")
            print(f"  Type: {item['content_type']}")
            print(f"  Raw size: {item['raw_size']} bytes")
            print(f"  Scraped: {item['scraped_at']}")
            print(f"  Published: {item['published_at'] or 'Not extracted'}")
            print(f"  Chunks: {item['chunk_count']}")
            print()

    return [r['rid'] for r in results]

def main():
    """Main function"""

    print("=== KOI Content Reprocessor ===")
    print("Demonstrating three-layer storage architecture")
    print()

    # List available content
    content_rids = list_raw_content()

    if not content_rids:
        print("No content with raw artifacts found!")
        print("\nTo store raw content, the website sensor needs to be updated")
        print("to save raw HTML in the koi_content.raw_content field")
        return

    # Reprocess first item as demo
    if content_rids:
        print("\n" + "="*50)
        print("DEMO: Reprocessing first item...")
        print("="*50)

        reprocess_content(content_rids[0])

        print("\n" + "="*50)
        print("\n📝 Summary:")
        print("1. Raw artifact stored in koi_content.raw_content")
        print("2. Document extracted and stored in koi_memories")
        print("3. Chunks created in koi_memory_chunks")
        print("4. All linked via RIDs for full provenance")
        print("\nThis architecture allows reprocessing without re-scraping!")

if __name__ == "__main__":
    main()