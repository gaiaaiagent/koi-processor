#!/usr/bin/env python3
"""Simplest possible chunk re-embed: serial urllib calls, no threading."""
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
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())["embedding"]


def main():
    m = re.match(r"postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)", POSTGRES_URL)
    conn = psycopg2.connect(
        host=m.group(3), port=int(m.group(4)),
        dbname=m.group(5), user=m.group(1), password=m.group(2)
    )
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT chunk_rid, content->>'text' AS text, content->>'title' AS title
        FROM koi_memory_chunks WHERE embedding IS NULL
        ORDER BY chunk_rid
    """)
    rows = cur.fetchall()
    total = len(rows)
    print(f"Chunks to embed: {total}", flush=True)
    if total == 0:
        return

    embedded = 0
    failures = 0
    start = time.time()

    for i, row in enumerate(rows):
        title = row["title"] or ""
        text = row["text"] or ""
        embed_text = f"Page: {title}\n\n{text}" if title else text
        embed_text = embed_text[:4000]

        try:
            emb = embed_one(embed_text)
            emb_str = "[" + ",".join(str(x) for x in emb) + "]"
            cur.execute(
                "UPDATE koi_memory_chunks SET embedding = %s::vector WHERE chunk_rid = %s",
                (emb_str, row["chunk_rid"])
            )
            embedded += 1
        except Exception as e:
            failures += 1
            if failures <= 5:
                print(f"  FAIL {row['chunk_rid'][:50]}: {e}", flush=True)

        if (i + 1) % 50 == 0:
            conn.commit()
            elapsed = time.time() - start
            rate = embedded / elapsed * 60 if elapsed > 0 else 0
            remaining = total - embedded - failures
            eta = remaining / (rate / 60) if rate > 0 else 0
            print(f"  {embedded}/{total} ({rate:.0f}/min) fail={failures} ETA={eta/60:.0f}min", flush=True)

    conn.commit()
    elapsed = time.time() - start
    print(f"\nDone: {embedded}/{total} in {elapsed/60:.1f}min, failures={failures}", flush=True)
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
