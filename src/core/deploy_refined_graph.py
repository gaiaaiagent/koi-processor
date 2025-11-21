#!/usr/bin/env python3
"""
Deploy refined graph to Apache Jena
Creates a new named graph to preserve the original
"""

import requests
import sys
from datetime import datetime

JENA_ENDPOINT = "http://localhost:3030/koi"
REFINED_GRAPH_FILE = "refined_graph_20251017_001349.ttl"
NAMED_GRAPH = f"https://regen.network/graphs/refined-{datetime.now().strftime('%Y%m%d')}"

def load_refined_graph():
    """Load refined graph to Jena as a named graph"""

    print(f"Loading refined graph to: {NAMED_GRAPH}")

    # Read the refined graph
    print(f"Reading {REFINED_GRAPH_FILE}...")
    with open(REFINED_GRAPH_FILE, 'r') as f:
        ttl_data = f.read()

    print(f"Graph size: {len(ttl_data)} bytes")

    # First, clear the named graph if it exists
    clear_query = f"DROP GRAPH <{NAMED_GRAPH}>"
    print(f"Clearing existing refined graph...")

    try:
        response = requests.post(
            f"{JENA_ENDPOINT}/update",
            data={"update": clear_query},
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        print(f"Clear response: {response.status_code}")
    except Exception as e:
        print(f"Warning: Could not clear graph: {e}")

    # Load the refined graph
    print(f"Loading refined graph...")

    # Use the data endpoint for bulk loading
    try:
        response = requests.put(
            f"{JENA_ENDPOINT}/data?graph={NAMED_GRAPH}",
            data=ttl_data.encode('utf-8'),
            headers={"Content-Type": "text/turtle"}
        )

        if response.status_code in [200, 201, 204]:
            print(f"✅ Successfully loaded refined graph!")
        else:
            print(f"❌ Failed to load: {response.status_code}")
            print(response.text[:500])
            return False

    except Exception as e:
        print(f"Error loading graph: {e}")
        return False

    # Verify the load
    count_query = f"""
    SELECT (COUNT(*) as ?count)
    WHERE {{
        GRAPH <{NAMED_GRAPH}> {{
            ?s ?p ?o
        }}
    }}
    """

    try:
        response = requests.post(
            f"{JENA_ENDPOINT}/sparql",
            data={"query": count_query},
            headers={"Accept": "application/sparql-results+json"}
        )

        if response.status_code == 200:
            result = response.json()
            count = result['results']['bindings'][0]['count']['value']
            print(f"\n✅ Verification: {count} triples loaded to named graph")
            print(f"   Graph URI: {NAMED_GRAPH}")
        else:
            print(f"Could not verify: {response.status_code}")

    except Exception as e:
        print(f"Verification error: {e}")

    # Show how to query both graphs
    print("\n" + "="*60)
    print("DEPLOYMENT COMPLETE")
    print("="*60)
    print(f"\nTo query ONLY the refined graph:")
    print(f"  SELECT * WHERE {{ GRAPH <{NAMED_GRAPH}> {{ ?s ?p ?o }} }}")
    print(f"\nTo query the original graph:")
    print(f"  SELECT * WHERE {{ ?s ?p ?o }}")
    print(f"\nTo query BOTH graphs:")
    print(f"  SELECT * WHERE {{ {{ ?s ?p ?o }} UNION {{ GRAPH <{NAMED_GRAPH}> {{ ?s ?p ?o }} }} }}")

    return True

def test_refined_queries():
    """Test some queries against the refined graph"""

    print("\n" + "="*60)
    print("TESTING REFINED GRAPH QUERIES")
    print("="*60)

    # Test query 1: Count unique predicates in refined graph
    query1 = f"""
    PREFIX regx: <https://regen.network/ontology/experimental#>
    SELECT (COUNT(DISTINCT ?pred) as ?count)
    WHERE {{
        GRAPH <{NAMED_GRAPH}> {{
            ?stmt regx:predicate ?pred
        }}
    }}
    """

    response = requests.post(
        f"{JENA_ENDPOINT}/sparql",
        data={"query": query1},
        headers={"Accept": "application/sparql-results+json"}
    )

    if response.status_code == 200:
        result = response.json()
        count = result['results']['bindings'][0]['count']['value']
        print(f"\nUnique predicates in refined graph: {count}")

    # Test query 2: Sample consolidated predicates
    query2 = f"""
    PREFIX regx: <https://regen.network/ontology/experimental#>
    SELECT DISTINCT ?pred
    WHERE {{
        GRAPH <{NAMED_GRAPH}> {{
            ?stmt regx:predicate ?pred
        }}
    }}
    LIMIT 20
    """

    response = requests.post(
        f"{JENA_ENDPOINT}/sparql",
        data={"query": query2},
        headers={"Accept": "application/sparql-results+json"}
    )

    if response.status_code == 200:
        result = response.json()
        print(f"\nSample predicates from refined graph:")
        for binding in result['results']['bindings'][:10]:
            pred = binding['pred']['value']
            print(f"  - {pred}")

if __name__ == "__main__":
    if load_refined_graph():
        test_refined_queries()
    else:
        print("Failed to load refined graph")
        sys.exit(1)