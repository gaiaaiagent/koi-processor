#!/usr/bin/env python3
"""Fast chunk re-embed: 2-worker concurrent, 120s timeout, keep-alive connections."""
import json, os, sys, time, re
import psycopg2
from psycopg2.extras import RealDictCursor
from concurrent.futures import ThreadPoolExecutor, as_completed
import http.client
import urllib.request
import threading

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://10.100.0.1:11435")
POSTGRES_URL = os.getenv("POSTGRES_URL")
WORKERS = int(os.getenv("EMBED_WORKERS", "2"))

_local = threading.local()


def get_ollama_conn():
    if not hasattr(_local, 'conn'):
        parsed = urllib.request.urlparse(OLLAMA_URL)
        _local.conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=120)
    return _local.conn


def embed_one(text):
    conn = get_ollama_conn()
    body = json.dumps({"model": "bge-large", "prompt": text}).encode()
    try:
        conn.request("POST", "/api/embeddings", body=body,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read())
        return data["embedding"]
    except Exception:
        parsed = urllib.request.urlparse(OLLAMA_URL)
        _local.conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=120)
        conn = _local.conn
        conn.request("POST", "/api/embeddings", body=body,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read())
        return data["embedding"]


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
    print(f"Chunks to embed: {total} (workers={WORKERS})", flush=True)
    if total == 0:
        return

    embedded = 0
    failures = 0
    start = time.time()

    for batch_start in range(0, total, 50):
        batch = rows[batch_start:batch_start + 50]
        results = {}

        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {}
            for row in batch:
                title = row["title"] or ""
                text = row["text"] or ""
                embed_text = f"Page: {title}\n\n{text}" if title else text
                embed_text = embed_text[:4000]  # shorter truncation for faster embedding
                f = executor.submit(embed_one, embed_text)
                futures[f] = row["chunk_rid"]

            for future in as_completed(futures):
                rid = futures[future]
                try:
                    emb = future.result()
                    results[rid] = emb
                except Exception as e:
                    failures += 1
                    print(f"  FAIL {rid[:50]}: {e}", flush=True)

        for rid, emb in results.items():
            emb_str = "[" + ",".join(str(x) for x in emb) + "]"
            cur.execute(
                "UPDATE koi_memory_chunks SET embedding = %s::vector WHERE chunk_rid = %s",
                (emb_str, rid)
            )
            embedded += 1

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
