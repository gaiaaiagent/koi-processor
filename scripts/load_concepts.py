#!/usr/bin/env python3
"""
Load concepts into the code graph and link them to entities.

1. Loads concepts from JSON
2. Creates Concept vertices
3. Creates EXPLAINS edges to related code entities
"""

import json
import psycopg2
from pathlib import Path
from typing import List, Dict
from loguru import logger


def connect_db(host='localhost', port=5433, password='postgres'):
    """Connect to PostgreSQL with Apache AGE"""
    conn = psycopg2.connect(
        host=host,
        port=port,
        database='eliza',
        user='postgres',
        password=password
    )
    conn.autocommit = False
    return conn


def load_age_extension(cur):
    """Load Apache AGE extension and set search path"""
    cur.execute("LOAD 'age';")
    cur.execute("SET search_path = ag_catalog, public;")


def create_concept_vertex(conn, concept: Dict, graph_name='regen_graph_v2') -> bool:
    """
    Create a Concept vertex in the graph.
    
    Args:
        conn: Database connection
        concept: Concept dictionary
        graph_name: Name of the graph
        
    Returns:
        True if created, False if failed
    """
    cur = conn.cursor()
    
    try:
        # Escape strings for Cypher
        concept_id = concept['id'].replace("'", "\\'")
        name = concept['name'].replace("'", "\\'")
        description = concept['description'][:500].replace("'", "\\'")
        source = concept['source'].replace("'", "\\'")
        module = concept['module'].replace("'", "\\'")
        domain = concept['domain'].replace("'", "\\'")
        keywords_json = json.dumps(concept['keywords'])
        
        # Create Concept vertex using Cypher
        query = f"""
        SELECT * FROM cypher('{graph_name}', 30597
            MERGE (c:Concept {{id: '{concept_id}'}})
            SET c.name = '{name}',
                c.description = '{description}',
                c.keywords = '{keywords_json}'::jsonb,
                c.source = '{source}',
                c.module = '{module}',
                c.domain = '{domain}'
            RETURN c.id
        30597) as (id agtype);
        """
        
        cur.execute(query)
        result = cur.fetchone()
        conn.commit()
        
        logger.debug(f"  Created concept: {name}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to create concept {concept.get('name', '?')}: {e}")
        conn.rollback()
        return False


def find_matching_entities(conn, concept: Dict, graph_name='regen_graph_v2', max_per_keyword=5) -> List[Dict]:
    """
    Find code entities that match concept keywords.
    
    Args:
        conn: Database connection
        concept: Concept dictionary
        graph_name: Name of the graph
        max_per_keyword: Maximum entities to find per keyword
        
    Returns:
        List of matching entities with scores
    """
    cur = conn.cursor()
    load_age_extension(cur)
    
    matching_entities = {}  # entity_id -> {name, type, score}
    
    keywords = concept['keywords'][:10]  # Use top 10 keywords
    
    for keyword in keywords:
        keyword_lower = keyword.lower()
        
        try:
            # Search for entities by name or file path
            query = f"""
            SELECT * FROM cypher('{graph_name}', 30597
                MATCH (e)
                WHERE toLower(e.name) CONTAINS '{keyword_lower}'
                   OR toLower(e.file_path) CONTAINS '{keyword_lower}'
                RETURN e.entity_id as entity_id, 
                       e.name as name, 
                       labels(e)[0] as type,
                       e.file_path as file_path
                LIMIT {max_per_keyword}
            30597) as (entity_id agtype, name agtype, type agtype, file_path agtype);
            """
            
            cur.execute(query)
            results = cur.fetchall()
            
            for entity_id, name, etype, file_path in results:
                # Clean up agtype values
                entity_id_clean = str(entity_id).strip('"')
                name_clean = str(name).strip('"')
                type_clean = str(etype).strip('"')
                
                if entity_id_clean not in matching_entities:
                    matching_entities[entity_id_clean] = {
                        'entity_id': entity_id_clean,
                        'name': name_clean,
                        'type': type_clean,
                        'score': 0
                    }
                
                # Increment score for each keyword match
                matching_entities[entity_id_clean]['score'] += 1
        
        except Exception as e:
            logger.debug(f"    Error searching for keyword '{keyword}': {e}")
            continue
    
    # Sort by score and return
    sorted_entities = sorted(matching_entities.values(), key=lambda x: -x['score'])
    
    return sorted_entities


def create_explains_edge(conn, concept_id: str, entity_id: str, graph_name='regen_graph_v2') -> bool:
    """
    Create an EXPLAINS edge from concept to entity.
    
    Args:
        conn: Database connection
        concept_id: Concept ID
        entity_id: Entity ID
        graph_name: Name of the graph
        
    Returns:
        True if created, False if failed
    """
    cur = conn.cursor()
    load_age_extension(cur)
    
    try:
        concept_id_escaped = concept_id.replace("'", "\\'")
        entity_id_escaped = entity_id.replace("'", "\\'")
        
        query = f"""
        SELECT * FROM cypher('{graph_name}', 30597
            MATCH (c:Concept {{id: '{concept_id_escaped}'}})
            MATCH (e {{entity_id: '{entity_id_escaped}'}})
            MERGE (c)-[r:EXPLAINS]->(e)
            RETURN id(r)
        30597) as (edge_id agtype);
        """
        
        cur.execute(query)
        result = cur.fetchone()
        conn.commit()
        return True
        
    except Exception as e:
        logger.debug(f"    Failed to create edge: {e}")
        conn.rollback()
        return False


def link_concept_to_entities(conn, concept: Dict, graph_name='regen_graph_v2', max_links=10) -> int:
    """
    Find and link code entities related to a concept.
    
    Args:
        conn: Database connection
        concept: Concept dictionary
        graph_name: Name of the graph
        max_links: Maximum links to create per concept
        
    Returns:
        Number of edges created
    """
    logger.info(f"  Linking concept: {concept['name']}")
    
    # Find matching entities
    matching_entities = find_matching_entities(conn, concept, graph_name)
    
    if not matching_entities:
        logger.info(f"    No matching entities found")
        return 0
    
    logger.info(f"    Found {len(matching_entities)} matching entities")
    
    # Create EXPLAINS edges for top matches
    edges_created = 0
    for entity in matching_entities[:max_links]:
        if create_explains_edge(conn, concept['id'], entity['entity_id'], graph_name):
            logger.debug(f"      Linked to {entity['type']}: {entity['name']} (score: {entity['score']})")
            edges_created += 1
    
    logger.info(f"    Created {edges_created} EXPLAINS edges")
    return edges_created


def load_concepts(concepts_file: Path, db_host='localhost', db_port=5433, db_password='postgres', 
                  graph_name='regen_graph_v2'):
    """
    Load concepts from JSON file and insert into graph.
    
    Args:
        concepts_file: Path to JSON file with concepts
        db_host: Database host
        db_port: Database port
        db_password: Database password
        graph_name: Name of the graph
    """
    logger.info("="*50)
    logger.info("CONCEPT LOADER")
    logger.info("="*50)
    
    # Load concepts from JSON
    logger.info(f"Loading concepts from {concepts_file}...")
    concepts = json.loads(concepts_file.read_text())
    logger.info(f"Loaded {len(concepts)} concepts")
    
    # Connect to database
    logger.info(f"Connecting to {db_host}:{db_port}...")
    conn = connect_db(host=db_host, port=db_port, password=db_password)
    cur = conn.cursor()
    load_age_extension(cur)
    
    # Create concept vertices
    logger.info("Creating concept vertices...")
    created = 0
    for concept in concepts:
        if create_concept_vertex(conn, concept, graph_name):
            created += 1
    logger.info(f"Created {created}/{len(concepts)} concepts")
    
    # Link concepts to entities
    logger.info("\nLinking concepts to code entities...")
    total_edges = 0
    for concept in concepts:
        edges = link_concept_to_entities(conn, concept, graph_name)
        total_edges += edges
    
    logger.info("\n" + "="*50)
    logger.info("LOAD COMPLETE")
    logger.info("="*50)
    logger.info(f"  Concepts: {created}")
    logger.info(f"  EXPLAINS edges: {total_edges}")
    logger.info("="*50)
    
    conn.close()


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python load_concepts.py <concepts_json> [db_host] [db_port] [db_password]")
        sys.exit(1)
    
    concepts_file = Path(sys.argv[1])
    db_host = sys.argv[2] if len(sys.argv) > 2 else 'localhost'
    db_port = int(sys.argv[3]) if len(sys.argv) > 3 else 5433
    db_password = sys.argv[4] if len(sys.argv) > 4 else 'postgres'
    
    load_concepts(concepts_file, db_host, db_port, db_password)
