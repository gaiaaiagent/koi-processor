#!/usr/bin/env python3
"""
Archive old graph and deploy only refined graph
"""
import requests
from datetime import datetime
import gzip

ENDPOINT = "http://localhost:3030/koi"
AUTH = ("admin", "admin")

def archive_current():
    """Archive current graph to compressed file"""
    print("Archiving current graph...")

    # Export all triples
    response = requests.get(
        f"{ENDPOINT}/data",
        headers={"Accept": "text/turtle"},
        auth=AUTH
    )

    if response.status_code == 200:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_path = f"archive_full_graph_{timestamp}.ttl.gz"

        # Compress and save
        with gzip.open(archive_path, 'wt', encoding='utf-8') as f:
            f.write(response.text)

        size_mb = len(response.text) / (1024*1024)
        print(f"✅ Archived {size_mb:.2f}MB to {archive_path}")
        return True
    else:
        print(f"❌ Failed to export: {response.status_code}")
        return False

def clear_graph():
    """Clear entire graph"""
    print("Clearing graph...")

    response = requests.post(
        f"{ENDPOINT}/update",
        auth=AUTH,
        data={"update": "CLEAR ALL"}
    )

    if response.status_code == 200:
        print("✅ Graph cleared")
        return True
    else:
        print(f"❌ Failed to clear: {response.status_code}")
        return False

def load_refined():
    """Load refined graph"""
    print("Loading refined graph...")

    # Load the refined graph
    refined_path = "refined_graph_20251017_001349.ttl"

    with open(refined_path, 'r') as f:
        data = f.read()

    response = requests.post(
        f"{ENDPOINT}/data",
        data=data,
        headers={"Content-Type": "text/turtle"},
        auth=AUTH
    )

    if response.status_code in [200, 201, 204]:
        print("✅ Refined graph loaded")

        # Verify
        count_response = requests.post(
            f"{ENDPOINT}/sparql",
            auth=AUTH,
            data={"query": "SELECT (COUNT(*) as ?count) WHERE { ?s ?p ?o }"}
        )

        if count_response.status_code == 200:
            import json
            count = json.loads(count_response.text)['results']['bindings'][0]['count']['value']
            print(f"✅ New triple count: {count}")

        return True
    else:
        print(f"❌ Failed to load: {response.status_code}")
        print(response.text[:500])
        return False

if __name__ == "__main__":
    if archive_current():
        if clear_graph():
            load_refined()