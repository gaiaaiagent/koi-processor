#!/usr/bin/env python3
"""Fast entity re-embed: sequential calls via urllib, minimal overhead."""
import json, os, sys, time, re, urllib.request
import psycopg2
from psycopg2.extras import RealDictCursor

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://10.100.0.1:11435")
POSTGRES_URL = os.getenv("POSTGRES_URL")


def embed_one(text):
    data = json.dumps({"model": "bge-large", "prompt": text}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/embeddings",
        data=data,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())["embedding"]


def main():
    m = re.match(r"postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)", POSTGRES_URL)
    if not m:
        print("Cannot parse POSTGRES_URL")
        sys.exit(1)

    conn = psycopg2.connect(
        host=m.group(3), port=int(m.group(4)),
        dbname=m.group(5), user=m.group(1), password=m.group(2)
    )
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT fuseki_uri, entity_text, description, metadata->>'context' AS context
        FROM entity_registry WHERE embedding IS NULL
        ORDER BY entity_type, entity_text
    """)
    rows = cur.fetchall()
    total = len(rows)
    print(f"Entities to embed: {total}", flush=True)

    embedded = 0
    failures = 0
    start = time.time()

    for i, row in enumerate(rows):
        name = row["entity_text"] or ""
        ctx = (row["context"] or "").strip()
        desc = (row["description"] or "").strip()
        combined = ". ".join(filter(None, [ctx, desc]))
        text = f"{name}: {combined}" if combined else name
        text = text[:8000]

        try:
            emb = embed_one(text)
            emb_str = "[" + ",".join(str(x) for x in emb) + "]"
            cur.execute(
                "UPDATE entity_registry SET embedding = %s::vector WHERE fuseki_uri = %s",
                (emb_str, row["fuseki_uri"])
            )
            embedded += 1
        except Exception as e:
            failures += 1
            print(f"  FAIL {row['fuseki_uri'][:40]}: {e}", flush=True)

        if (i + 1) % 50 == 0:
            conn.commit()
            elapsed = time.time() - start
            rate = embedded / elapsed * 60
            print(f"  {embedded}/{total} ({rate:.0f}/min) failures={failures}", flush=True)

    conn.commit()
    elapsed = time.time() - start
    print(f"\nDone: {embedded}/{total} in {elapsed:.0f}s ({elapsed/60:.1f}min), failures={failures}", flush=True)

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
