#!/usr/bin/env python3
"""Ingest the local Indy Johar Substack corpus into personal-koi.

Reads /Users/darrenzal/projects/IndyJoharContent/IndyJoharPosts/indy_johar_FULL_content.json
(scraped via scrape_full_content.py / scrape_incremental.py), deduplicates against
existing email-sensor rows by canonical slug, and inserts the remaining posts
into koi_memories + koi_memory_chunks with embedding_3072 populated inline.

Tail-item resolutions baked in:
- Dedup via canonical_slug() function (handles email redirect tokens, edited URLs).
- ON CONFLICT (rid) DO NOTHING for idempotent re-runs.
- canonical_slug() returning None → skip + log to /tmp/indy-ingest-skipped-<ts>.log.
- chunk metadata.repo set so unified_search docs surface picks them up.

Usage:
    python3 scripts/ingest_indy_johar_corpus.py --dry-run
    python3 scripts/ingest_indy_johar_corpus.py
"""

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import asyncpg
import tiktoken

POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")
CORPUS_PATH = "/Users/darrenzal/projects/IndyJoharContent/IndyJoharPosts/indy_johar_FULL_content.json"

OPENAI_MODEL = "text-embedding-3-large"
OPENAI_DIMENSIONS = 3072
MAX_TOKENS_PER_CHUNK = 8000
MAX_CHUNKS_PER_POST = 32
CHUNK_TARGET_TOKENS = 1500  # ~6KB of text per chunk

SOURCE_SENSOR = "substack-corpus-backfill"
REPO_NAME = "substack-backfill"
FEED_SLUG = "indyjohar"
DOMAIN = "commons"
AUTHOR = "Indy Johar"
TAGS = ["substack", "indy-johar", "dark-matter-labs", "civic-design", "institutional-form"]

RATE_PER_MILLION = 0.13
COST_ABORT_USD = 5.0
BATCH_SIZE = 50

_TOKENIZER = tiktoken.encoding_for_model(OPENAI_MODEL)


def canonical_slug(url_or_text: str) -> Optional[str]:
    """Extract Substack canonical post slug from URL, redirect token, or body text.

    Strategy: prefer a direct `indyjohar.substack.com/p/<slug>` hit (always
    present in subscription emails near the top), only fall back to decoding
    substack.com/redirect/2/<token> if the direct pattern isn't found. This
    avoids accidentally decoding an unrelated 'Unsubscribe' redirect link from
    the email footer, which would resolve to /action/disable_email rather than
    the post URL.
    """
    if not url_or_text:
        return None
    m = re.search(r'indyjohar\.substack\.com/p/([a-z0-9\-]+)', url_or_text)
    if m:
        return m.group(1).rstrip('-')
    # Fallback: decode the first redirect token and re-test
    m = re.search(r'substack\.com/redirect/2/([A-Za-z0-9_-]+)', url_or_text)
    if m:
        try:
            token = m.group(1)
            decoded = json.loads(base64.urlsafe_b64decode(token + '==').decode())
            target = decoded.get('e', '')
            m2 = re.search(r'indyjohar\.substack\.com/p/([a-z0-9\-]+)', target)
            if m2:
                return m2.group(1).rstrip('-')
        except Exception:
            pass
    return None


def chunk_text(text: str) -> List[Dict[str, str]]:
    """Token-aware chunking targeting CHUNK_TARGET_TOKENS each."""
    tokens = _TOKENIZER.encode(text)
    if not tokens:
        return []
    chunks = []
    for i, start in enumerate(range(0, len(tokens), CHUNK_TARGET_TOKENS)):
        if i >= MAX_CHUNKS_PER_POST:
            break
        end = min(start + CHUNK_TARGET_TOKENS, len(tokens))
        chunk_tokens = tokens[start:end]
        # Final truncation safety (CHUNK_TARGET_TOKENS < MAX_TOKENS so this is a no-op,
        # but defensive against future tuning)
        chunk_tokens = chunk_tokens[:MAX_TOKENS_PER_CHUNK]
        chunks.append({
            "index": i,
            "text": _TOKENIZER.decode(chunk_tokens),
        })
    return chunks


async def fetch_existing_slugs(conn) -> Set[str]:
    """Build set of canonical slugs already present in email-sensor or prior backfill."""
    rows = await conn.fetch(
        """
        SELECT content->>'text' AS body, rid, source_sensor
        FROM koi_memories
        WHERE (source_sensor = 'email-sensor' AND content::text ILIKE '%indyjohar%')
           OR source_sensor = $1
        """,
        SOURCE_SENSOR,
    )
    slugs: Set[str] = set()
    for r in rows:
        slug = canonical_slug(r["body"] or r["rid"] or "")
        if slug:
            slugs.add(slug)
    return slugs


async def embed_batch(client, texts: List[str]) -> List[List[float]]:
    """OpenAI embeddings call; truncates each text to token limit defensively."""
    safe = []
    for t in texts:
        toks = _TOKENIZER.encode(t)
        if len(toks) > MAX_TOKENS_PER_CHUNK:
            t = _TOKENIZER.decode(toks[:MAX_TOKENS_PER_CHUNK])
        safe.append(t)
    resp = await asyncio.to_thread(
        client.embeddings.create,
        model=OPENAI_MODEL,
        input=safe,
        dimensions=OPENAI_DIMENSIONS,
    )
    return [d.embedding for d in resp.data]


def parse_corpus(path: str) -> List[Dict]:
    with open(path) as f:
        d = json.load(f)
    posts = d.get("posts", [])
    return [p for p in posts if p.get("url") and p.get("full_content")]


async def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus-path", default=CORPUS_PATH)
    ap.add_argument("--db-url", default=POSTGRES_URL)
    ap.add_argument("--dry-run", action="store_true",
                    help="Count + cost estimate; no API calls or DB writes.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap posts processed (testing).")
    args = ap.parse_args()

    posts = parse_corpus(args.corpus_path)
    print(f"Corpus posts (with url+full_content): {len(posts)}")

    conn = await asyncpg.connect(args.db_url)
    skip_log_path = Path(f"/tmp/indy-ingest-skipped-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.log")
    try:
        existing_slugs = await fetch_existing_slugs(conn)
        print(f"Existing slugs in koi_memories (email-sensor + prior backfill): {len(existing_slugs)}")

        # Intra-corpus dedup: keep longest full_content per slug
        by_slug: Dict[str, Dict] = {}
        skipped_no_slug = []
        for p in posts:
            slug = canonical_slug(p["url"])
            if not slug:
                skipped_no_slug.append(p["url"])
                continue
            if slug in by_slug:
                if len(p.get("full_content", "")) > len(by_slug[slug].get("full_content", "")):
                    by_slug[slug] = p
            else:
                by_slug[slug] = p

        # Cross-dedup against existing
        to_ingest = [(slug, post) for slug, post in by_slug.items() if slug not in existing_slugs]
        print(f"Intra-corpus distinct slugs: {len(by_slug)} (skipped {len(skipped_no_slug)} with no slug)")
        print(f"After dedup against email-sensor: {len(to_ingest)} posts to ingest")

        if args.limit:
            to_ingest = to_ingest[: args.limit]
            print(f"--limit applied: {len(to_ingest)} posts")

        if not to_ingest:
            print("Nothing to ingest.")
            return 0

        # Plan chunks
        planned: List[Tuple[str, Dict, List[Dict]]] = []
        total_chunks = 0
        total_chars = 0
        for slug, post in to_ingest:
            body = post.get("full_content", "")
            chunks = chunk_text(body)
            if not chunks:
                continue
            planned.append((slug, post, chunks))
            total_chunks += len(chunks)
            total_chars += sum(len(c["text"]) for c in chunks)

        est_tokens = total_chars // 4
        est_cost = (est_tokens / 1_000_000) * RATE_PER_MILLION
        print(f"Planned: {len(planned)} posts → {total_chunks} chunks; ~{est_tokens:,} tokens, ~${est_cost:.4f}")

        if est_cost > COST_ABORT_USD:
            print(f"ABORT: estimated cost ${est_cost:.4f} > ${COST_ABORT_USD}")
            return 3
        if args.dry_run:
            print("Dry-run: skipping API + writes.")
            if skipped_no_slug:
                print(f"  ({len(skipped_no_slug)} posts had no extractable slug)")
            return 0

        # Write skipped log (real run)
        if skipped_no_slug:
            with skip_log_path.open("w") as f:
                for u in skipped_no_slug:
                    f.write(f"NO_SLUG\t{u}\n")
            print(f"Skipped-log: {skip_log_path}")

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
            return 2
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        inserted_posts = 0
        inserted_chunks = 0
        actual_tokens = 0
        t0 = time.time()

        for slug, post, chunks in planned:
            url = post["url"]
            title = post.get("title", "(untitled)")
            subtitle = post.get("subtitle", "")
            full_content = post.get("full_content", "")
            date = post.get("date", "") or None

            document_rid = f"substack-corpus:{FEED_SLUG}:{slug}"
            parent_content = {
                "text": full_content[:1000],  # preview; chunks hold full text
                "title": title,
                "subtitle": subtitle,
                "url": url,
            }
            parent_metadata = {
                "repo": REPO_NAME,
                "source_type": "substack_corpus",
                "feed_slug": FEED_SLUG,
                "canonical_slug": slug,
                "url": url,
                "title": title,
                "author": AUTHOR,
                "domain": DOMAIN,
                "tags": TAGS,
                "published_at": date,
            }

            # Embed chunks (batched by BATCH_SIZE across all chunks for this post,
            # though typical post < BATCH_SIZE so one call each)
            embeddings: List[List[float]] = []
            for i in range(0, len(chunks), BATCH_SIZE):
                batch_chunks = chunks[i : i + BATCH_SIZE]
                texts = [c["text"] for c in batch_chunks]
                actual_tokens += sum(len(_TOKENIZER.encode(t)) for t in texts)
                running_cost = (actual_tokens / 1_000_000) * RATE_PER_MILLION
                if running_cost > COST_ABORT_USD:
                    print(f"ABORT mid-run: cost ${running_cost:.4f} > ${COST_ABORT_USD}; stopped at {inserted_posts} posts")
                    return 3
                embeddings.extend(await embed_batch(client, texts))

            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO koi_memories (rid, event_type, source_sensor, content, metadata)
                    VALUES ($1::text, 'NEW', $2::text, $3::jsonb, $4::jsonb)
                    ON CONFLICT (rid) DO NOTHING
                    """,
                    document_rid,
                    SOURCE_SENSOR,
                    json.dumps(parent_content),
                    json.dumps(parent_metadata),
                )
                for chunk, emb in zip(chunks, embeddings):
                    chunk_rid = f"{document_rid}#chunk{chunk['index']}"
                    chunk_content = {
                        "text": chunk["text"],
                        "context": f"substack:{FEED_SLUG}:{slug}",
                    }
                    chunk_metadata = {
                        "repo": REPO_NAME,
                        "source_type": "substack_corpus",
                        "feed_slug": FEED_SLUG,
                        "canonical_slug": slug,
                        "url": url,
                        "title": title,
                        "author": AUTHOR,
                        "domain": DOMAIN,
                        "tags": TAGS,
                    }
                    await conn.execute(
                        """
                        INSERT INTO koi_memory_chunks
                            (chunk_rid, document_rid, chunk_index, total_chunks,
                             content, embedding_3072, metadata)
                        VALUES ($1::text, $2::text, $3::int, $4::int,
                                $5::jsonb, $6::vector, $7::jsonb)
                        ON CONFLICT (chunk_rid) DO NOTHING
                        """,
                        chunk_rid, document_rid, chunk["index"], len(chunks),
                        json.dumps(chunk_content), str(emb), json.dumps(chunk_metadata),
                    )
                    inserted_chunks += 1
            inserted_posts += 1
            if inserted_posts % 10 == 0:
                print(f"  progress: {inserted_posts}/{len(planned)} posts, {inserted_chunks} chunks")

        elapsed = time.time() - t0
        final_cost = (actual_tokens / 1_000_000) * RATE_PER_MILLION
        print(f"\nDone: {inserted_posts}/{len(planned)} posts, {inserted_chunks} chunks in {elapsed:.1f}s")
        print(f"Cost: ${final_cost:.4f} ({actual_tokens:,} tokens)")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
