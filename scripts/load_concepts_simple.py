#!/usr/bin/env python3
"""
Simplified concept loader that works with Apache AGE.
"""

import json
import psycopg2
from pathlib import Path
from loguru import logger


def load_concepts(concepts_json: Path):
    """Load concepts from JSON and insert into graph"""
    logger.info("Loading concepts...")
    
    # Load JSON
    concepts = json.loads(concepts_json.read_text())
    logger.info(f"Loaded {len(concepts)} concepts")
    
    # Connect to DB
    conn = psycopg2.connect(
        host='localhost',
        port=5433,
        database='eliza',
        user='postgres',
        password='postgres'
    )
    conn.autocommit = False
    cur = conn.cursor()
    
    # Setup AGE
    cur.execute("LOAD 'age';")
    cur.execute("SET search_path = ag_catalog, public;")
    
    created = 0
    for concept in concepts:
        try:
            # Escape strings
            cid = concept['id'].replace("'", "''" )
            name = concept['name'].replace("'", "''")
            desc = concept['description'][:500].replace("'", "''")
            source = concept['source'].replace("'", "''")
            module = concept['module'].replace("'", "''")
            keywords_str = ','.join(concept['keywords'][:10])
            
            # Use CREATE instead of MERGE
            query = f"""
            SELECT * FROM cypher('regen_graph_v2', 33224
                CREATE (c:Concept {{
                    id: '{cid}',
                    name: '{name}',
                    description: '{desc}',
                    source: '{source}',
                    module: '{module}',
                    keywords: '{keywords_str}'
                }})
                RETURN c.id
            33224) as (id agtype);
            """
            
            cur.execute(query)
            conn.commit()
            created += 1
            logger.info(f"  Created: {name}")
            
        except Exception as e:
            logger.error(f"Failed to create {concept['name']}: {e}")
            conn.rollback()
    
    logger.info(f"Created {created}/{len(concepts)} concepts")
    conn.close()


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python load_concepts_simple.py <concepts.json>")
        sys.exit(1)
    
    load_concepts(Path(sys.argv[1]))
