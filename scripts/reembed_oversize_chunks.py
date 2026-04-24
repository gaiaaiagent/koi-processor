"""Fix oversize chunks (>8K tokens) by split+mean embedding.

For each chunk where embedding_3072 IS NULL and text exceeds OpenAI's 8192
token input limit:
  1. Split text into sub-chunks of <=7500 tokens on paragraph/sentence
     boundaries (preserving word boundaries).
  2. Embed each sub-chunk via OpenAI text-embedding-3-large @ 3072-dim.
  3. Compute the mean vector across all sub-chunks.
  4. UPDATE koi_memory_chunks.embedding_3072 = mean for the parent chunk id.

Standard practice for handling oversize inputs in production retrieval
(OpenAI cookbook pattern). Preserves single-vector-per-chunk-rid invariant,
no schema change, no chunk_rid churn.
"""
import asyncio
import os
import re
import sys
import time

import asyncpg
import tiktoken
from openai import OpenAI

MODEL = "text-embedding-3-large"
DIM = 3072
SUB_CHUNK_TOK = 7500
OVERLAP_TOK = 200
API_KEY = os.environ["OPENAI_API_KEY"]
DB_URL = os.environ.get("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")

enc = tiktoken.get_encoding("cl100k_base")
client = OpenAI(api_key=API_KEY)


def split_by_tokens(text: str, max_tok: int = SUB_CHUNK_TOK, overlap: int = OVERLAP_TOK):
    """Split on whitespace-preserving token boundaries with small overlap."""
    tokens = enc.encode(text, disallowed_special=())
    if len(tokens) <= max_tok:
        return [text]
    out = []
    i = 0
    while i < len(tokens):
        window = tokens[i : i + max_tok]
        sub = enc.decode(window)
        out.append(sub)
        if i + max_tok >= len(tokens):
            break
        i += max_tok - overlap
    return out


def mean_vectors(vecs):
    """Element-wise mean. vecs: list[list[float]]."""
    n = len(vecs)
    if n == 0:
        return None
    dim = len(vecs[0])
    acc = [0.0] * dim
    for v in vecs:
        for k in range(dim):
            acc[k] += v[k]
    return [x / n for x in acc]


def embed_batch(texts):
    resp = client.embeddings.create(model=MODEL, input=texts, dimensions=DIM)
    return [d.embedding for d in resp.data]


async def main():
    conn = await asyncpg.connect(DB_URL)
    # Target: all chunks still NULL with text present.
    rows = await conn.fetch(
        """
        SELECT c.id, c.content->>'text' AS text
        FROM koi_memory_chunks c JOIN koi_memories m ON m.rid=c.document_rid
        WHERE m.source_sensor IN ('mediawiki-sensor','email-sensor','doc-scanner','ics-event')
          AND c.embedding_3072 IS NULL
          AND char_length(c.content->>'text') > 0
        """
    )
    print(f"Candidates: {len(rows)}")

    # Filter to only those truly > 8000 tokens (the only ones OpenAI rejects).
    oversize = []
    for r in rows:
        tok = len(enc.encode(r["text"] or "", disallowed_special=()))
        if tok > 8000:
            oversize.append((r["id"], r["text"], tok))
    print(f"Oversize (>8000 tokens): {len(oversize)}")

    t0 = time.time()
    total_subs = 0
    total_tokens = 0
    total_cost = 0.0
    processed = 0
    failed = []
    for cid, text, tok in oversize:
        subs = split_by_tokens(text)
        # Embed sub-chunks (batched).
        try:
            vecs = embed_batch(subs)
        except Exception as e:
            failed.append((cid, str(e)))
            continue
        mean = mean_vectors(vecs)
        # Write back.
        await conn.execute(
            "UPDATE koi_memory_chunks SET embedding_3072 = $1::vector WHERE id = $2",
            str(mean), cid,
        )
        sub_tok = sum(len(enc.encode(s, disallowed_special=())) for s in subs)
        total_subs += len(subs)
        total_tokens += sub_tok
        total_cost += sub_tok / 1_000_000 * 0.13
        processed += 1
        if processed % 25 == 0:
            print(
                f"  {processed}/{len(oversize)}: id={cid} subs={len(subs)} "
                f"orig={tok}tok total_cost=${total_cost:.4f} "
                f"elapsed={time.time()-t0:.0f}s",
                flush=True,
            )

    print(
        f"\nDone: processed={processed} sub_chunks={total_subs} "
        f"tokens={total_tokens} cost=${total_cost:.4f} "
        f"elapsed={time.time()-t0:.0f}s"
    )
    if failed:
        print(f"Failed: {len(failed)}")
        for cid, err in failed[:5]:
            print(f"  id={cid}: {err[:120]}")

    # Verify coverage.
    final = await conn.fetch(
        """
        SELECT m.source_sensor, COUNT(c.id) total, COUNT(c.embedding_3072) n
        FROM koi_memory_chunks c JOIN koi_memories m ON m.rid=c.document_rid
        WHERE m.source_sensor IN ('mediawiki-sensor','email-sensor','doc-scanner','ics-event')
        GROUP BY m.source_sensor ORDER BY total DESC
        """
    )
    print("\nFinal coverage:")
    for r in final:
        pct = r["n"] / r["total"] * 100 if r["total"] else 0.0
        print(f"  {r['source_sensor']:>20}: {r['n']}/{r['total']} ({pct:.3f}%)")
    await conn.close()


asyncio.run(main())
