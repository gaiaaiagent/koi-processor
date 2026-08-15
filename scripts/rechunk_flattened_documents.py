#!/usr/bin/env python3
"""Re-chunk documents whose chunks were flattened by the pre-2026-08-14 chunker.

Background. `api/chunker.py` rebuilt every chunk as `' '.join(text.split())`,
which silently destroyed newlines, indentation and column alignment at ingest.
Fixed in koi-processor-runtime 65b8b57 and koi-processor b5d5aac7. The fix only
affects NEW ingests: re-running an ingest is idempotent on the content hash of
the source bytes, which have not changed, so damaged documents never self-heal.

The migration is cheap and entirely local because the damage was only ever at the
chunk layer. `koi_memories.content->>'text'` still holds the full original
document with its whitespace intact -- verified on The Working with Stories
Sourcebook, whose stored text carries 30 paired-pole lines and 251 column runs
while all 196 of its chunks carry zero of each and no newline at all. Nothing has
to be re-fetched; the source of truth is already in the database.

Damage signature, and why it is conservative:

    the stored full text contains a newline
    AND no chunk of that document contains one

A document with no newlines in its source is not damaged, it is simply flat, so
requiring the source to have them avoids rewriting documents that were always
fine. Documents are re-embedded, which costs money, so the selection is
deliberately narrow rather than "re-chunk everything".

Safety. Embedding happens for the whole document BEFORE anything is deleted, and
the swap runs inside a transaction, so a failure mid-document leaves the existing
chunks untouched rather than leaving it chunkless. The pre-existing
`upsert_document_chunks` is reused so chunk rids, metadata and the 3072-dim
column match exactly what a normal ingest writes.

    python3 scripts/rechunk_flattened_documents.py --dry-run
    python3 scripts/rechunk_flattened_documents.py --rid document:f4d6bdee...
    python3 scripts/rechunk_flattened_documents.py --limit 5
    python3 scripts/rechunk_flattened_documents.py --all
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402

from api.chunker import TextChunker  # noqa: E402
from scripts.ingest_document import (  # noqa: E402
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    OPENAI_API_KEY,
    OpenAIEmbeddingProvider,
    upsert_document_chunks,
)

DB = os.getenv("DATABASE_URL", "postgresql://localhost/personal_koi")

CANDIDATES = """
SELECT m.rid,
       coalesce(m.content->>'title', m.content->>'file_path', m.rid) AS title,
       length(m.content->>'text')                                    AS src_len,
       (SELECT count(*) FROM koi_memory_chunks c WHERE c.document_rid = m.rid) AS n_chunks
FROM koi_memories m
WHERE m.rid LIKE 'document:%'
  AND m.content->>'text' LIKE '%' || chr(10) || '%'
  AND EXISTS (SELECT 1 FROM koi_memory_chunks c WHERE c.document_rid = m.rid)
  AND NOT EXISTS (
        SELECT 1 FROM koi_memory_chunks c
        WHERE c.document_rid = m.rid
          AND c.content->>'text' LIKE '%' || chr(10) || '%')
ORDER BY length(m.content->>'text') DESC
"""


async def structure_counts(conn, rid: str) -> dict:
    row = await conn.fetchrow("""
        SELECT count(*) AS n,
               count(*) FILTER (WHERE content->>'text' LIKE '%' || chr(10) || '%') AS nl,
               count(*) FILTER (WHERE content->>'text' ~ '[A-Za-z]{3,}[ ]{4,}[A-Za-z]{3,}') AS cols
        FROM koi_memory_chunks WHERE document_rid = $1""", rid)
    return dict(row) if row else {"n": 0, "nl": 0, "cols": 0}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="list candidates, change nothing")
    ap.add_argument("--rid", help="re-chunk exactly this document rid")
    ap.add_argument("--limit", type=int, help="process at most N documents")
    ap.add_argument("--all", action="store_true", help="process every candidate")
    a = ap.parse_args()

    if not (a.dry_run or a.rid or a.limit or a.all):
        ap.error("pick one of --dry-run / --rid / --limit / --all; "
                 "this rewrites chunks and costs embedding spend")

    conn = await asyncpg.connect(DB)
    try:
        rows = await conn.fetch(CANDIDATES)
        if a.rid:
            rows = [r for r in rows if r["rid"] == a.rid]
            if not rows:
                print(f"{a.rid} is not a candidate: either it has no chunks, its stored "
                      f"text has no newlines, or its chunks already retain them.")
                return

        print(f"{len(rows)} damaged document(s); "
              f"{sum(r['n_chunks'] for r in rows)} chunks would be re-embedded\n")
        for r in rows[:20]:
            print(f"   {r['n_chunks']:>5} chunks  {r['src_len']:>9,} chars  {r['title'][:62]}")
        if len(rows) > 20:
            print(f"   ... and {len(rows) - 20} more")

        if a.dry_run:
            print("\ndry run; nothing changed")
            return

        todo = rows if a.all else rows[:(a.limit or len(rows))]
        embedder = OpenAIEmbeddingProvider(api_key=OPENAI_API_KEY, model=EMBEDDING_MODEL,
                                           dimension=EMBEDDING_DIMENSION)
        chunker = TextChunker(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)

        print(f"\nre-chunking {len(todo)} document(s)\n")
        for r in todo:
            rid = r["rid"]
            before = await structure_counts(conn, rid)
            text = await conn.fetchval(
                "SELECT content->>'text' FROM koi_memories WHERE rid = $1", rid)
            if not text:
                print(f"   SKIP {rid[:40]} — no stored text")
                continue

            chunks = chunker.chunk_text(text)
            # Embed everything BEFORE touching the existing rows, so a failure here
            # leaves the document's current chunks in place rather than deleting them.
            embeddings = []
            for c in chunks:
                try:
                    embeddings.append(await embedder.embed(c["text"]))
                except Exception as e:                      # noqa: BLE001
                    print(f"   embed failed on {rid[:36]} chunk {c['index']}: {e}")
                    embeddings.append(None)
            if sum(1 for e in embeddings if e is None) > len(embeddings) // 2:
                print(f"   ABORT {rid[:40]} — over half of embeddings failed, "
                      f"leaving existing chunks untouched")
                continue

            async with conn.transaction():
                written = await upsert_document_chunks(
                    conn, rid, chunks, embeddings,
                    {"name": r["title"], "group_id": "personal"})

            after = await structure_counts(conn, rid)
            print(f"   {r['title'][:52]:<52} {before['n']:>4} -> {written:>4} chunks  "
                  f"newlines {before['nl']}->{after['nl']}  columns {before['cols']}->{after['cols']}")
            if after["nl"] == 0:
                print(f"      ⚠ still no newlines after re-chunk — investigate {rid}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
