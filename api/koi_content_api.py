#!/usr/bin/env python3
"""
KOI Content API - Serves real sensed content data from PostgreSQL
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from psycopg2.extras import RealDictCursor
import json
from typing import List, Dict, Any
import uvicorn
import os

app = FastAPI(title="KOI Content API", version="1.0.0")

# Enable CORS for browser access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database connection
DB_CONFIG = {
    "host": "localhost",
    "port": 5433,
    "database": "eliza",
    "user": "postgres",
    "password": "postgres"
}

def get_db_connection():
    """Create database connection"""
    return psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy", "service": "KOI Content API"}

@app.get("/api/koi/content/domains")
async def get_domains():
    """Get all domains with content counts"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT
                metadata->>'domain' as domain,
                COUNT(*) as page_count
            FROM koi_memories
            WHERE metadata->>'domain' IS NOT NULL
            GROUP BY domain
            ORDER BY page_count DESC
        """)

        results = cur.fetchall()
        cur.close()
        conn.close()

        return {"domains": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/koi/content/pages/{domain}")
async def get_pages_by_domain(domain: str):
    """Get all pages sensed from a specific domain (website or discourse forum)"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Try both domain field and forum field for discourse compatibility
        cur.execute("""
            SELECT
                metadata->>'title' as title,
                metadata->>'url' as url,
                metadata->>'source_file' as source_file,
                created_at
            FROM koi_memories
            WHERE metadata->>'domain' = %s
               OR metadata->>'forum' = %s
            ORDER BY created_at DESC
        """, (domain, domain))

        results = cur.fetchall()
        cur.close()
        conn.close()

        return {"domain": domain, "pages": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/koi/content/notion")
async def get_notion_pages():
    """Get all Notion pages that have been sensed"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT DISTINCT
                metadata->>'title' as title,
                metadata->>'url' as url
            FROM koi_memories
            WHERE metadata->>'source' = 'notion'
            OR metadata->>'url' LIKE '%notion.so%'
            ORDER BY title
        """)

        results = cur.fetchall()
        cur.close()
        conn.close()

        return {"pages": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/koi/content/sources")
async def get_all_sources():
    """Get all sources grouped by sensor type"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Get website sources
        cur.execute("""
            SELECT DISTINCT
                'website' as sensor_type,
                metadata->>'domain' as source
            FROM koi_memories
            WHERE metadata->>'domain' IS NOT NULL
        """)
        website_sources = cur.fetchall()

        # Get GitHub sources
        cur.execute("""
            SELECT DISTINCT
                'github' as sensor_type,
                metadata->>'repository' as source
            FROM koi_memories
            WHERE metadata->>'repository' IS NOT NULL
        """)
        github_sources = cur.fetchall()

        # Get Twitter sources
        cur.execute("""
            SELECT DISTINCT
                'twitter' as sensor_type,
                metadata->>'author' as source
            FROM koi_memories
            WHERE metadata->>'source' = 'twitter'
            AND metadata->>'author' IS NOT NULL
        """)
        twitter_sources = cur.fetchall()

        cur.close()
        conn.close()

        # Group by sensor type
        sources_by_type = {}
        for item in website_sources + github_sources + twitter_sources:
            sensor_type = item['sensor_type']
            source = item['source']
            if sensor_type not in sources_by_type:
                sources_by_type[sensor_type] = []
            if source:
                sources_by_type[sensor_type].append(source)

        return {"sources": sources_by_type}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/koi/content/statistics")
async def get_statistics():
    """Get overall content statistics"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Total memories
        cur.execute("SELECT COUNT(*) as total FROM koi_memories")
        total = cur.fetchone()['total']

        # By source type
        cur.execute("""
            SELECT
                metadata->>'source' as source,
                COUNT(*) as count
            FROM koi_memories
            WHERE metadata->>'source' IS NOT NULL
            GROUP BY source
        """)
        by_source = cur.fetchall()

        # Recent activity (last 24 hours)
        cur.execute("""
            SELECT COUNT(*) as recent
            FROM koi_memories
            WHERE created_at > NOW() - INTERVAL '24 hours'
        """)
        recent = cur.fetchone()['recent']

        cur.close()
        conn.close()

        return {
            "total_memories": total,
            "by_source": by_source,
            "recent_24h": recent
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    # Run on port 8007 to avoid conflicts
    uvicorn.run(app, host="0.0.0.0", port=8007)