#!/usr/bin/env python3
"""
B8 Contextual Retrieval — LLM context generation for document chunks.

For each chunk, generates a 1-2 sentence context snippet explaining its
position within the source document. This context is prepended to the chunk
before embedding and BM25 indexing, making retrieval more precise.

Reference: Anthropic, "Introducing Contextual Retrieval"
https://www.anthropic.com/engineering/contextual-retrieval

Used by:
  - scripts/backfill_contextual_retrieval.py (batch backfill)
  - api/mediawiki_sensor.py (ingest-time generation)
  - api/github_sensor.py (ingest-time generation, non-code files only)
"""

import asyncio
import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
CONTEXT_MODEL = os.getenv("B8_CONTEXT_MODEL", "gpt-4o-mini")

# Safety truncation: 100k tokens ≈ 400k chars.
# GPT-4o-mini context window is 128k tokens.
# Largest doc in Octo corpus is ~51k tokens — well within limits.
MAX_DOC_CHARS = 400_000

_openai_client = None


def _get_openai_client():
    """Lazy-initialize the AsyncOpenAI client for context generation."""
    global _openai_client
    if _openai_client is None:
        if not OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY not set — context generation disabled")
            return None
        try:
            from openai import AsyncOpenAI
            _openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
            logger.info(f"Initialized B8 context generation client (model: {CONTEXT_MODEL})")
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI client for B8: {e}")
            return None
    return _openai_client


CONTEXT_PROMPT_TEMPLATE = """\
<document>
{document_title}

{document_text}
</document>

Here is a chunk from this document:

<chunk>
{chunk_text}
</chunk>

Give a short (1-2 sentence) context to situate this chunk within the overall document, for improving search retrieval. Answer only with the context."""


async def generate_chunk_context(
    document_text: str,
    chunk_text: str,
    document_title: str = "",
    openai_client=None,
) -> str:
    """Generate a 1-2 sentence context snippet for a chunk.

    Args:
        document_text: Full text of the source document.
        chunk_text: The chunk to contextualize.
        document_title: Title of the source document.
        openai_client: Optional pre-initialized AsyncOpenAI client.

    Returns:
        Context string, or empty string on failure.
    """
    client = openai_client or _get_openai_client()
    if client is None:
        return ""

    # Safety truncation
    doc_text = document_text[:MAX_DOC_CHARS] if document_text else ""
    if not doc_text and not chunk_text:
        return ""

    prompt = CONTEXT_PROMPT_TEMPLATE.format(
        document_title=document_title or "(untitled)",
        document_text=doc_text,
        chunk_text=chunk_text,
    )

    try:
        response = await client.chat.completions.create(
            model=CONTEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0.0,
        )
        context = response.choices[0].message.content.strip()
        return context
    except Exception as e:
        logger.warning(f"B8 context generation failed: {e}")
        return ""


async def generate_contexts_for_document(
    document_text: str,
    chunks: List[dict],
    document_title: str = "",
    concurrency: int = 5,
    openai_client=None,
) -> List[str]:
    """Generate context snippets for all chunks from a single document.

    Args:
        document_text: Full text of the source document.
        chunks: List of chunk dicts, each with at least a "text" key.
        document_title: Title of the source document.
        concurrency: Max concurrent LLM calls.
        openai_client: Optional pre-initialized AsyncOpenAI client.

    Returns:
        List of context strings, one per chunk (empty string on failure).
    """
    client = openai_client or _get_openai_client()
    if client is None:
        return [""] * len(chunks)

    sem = asyncio.Semaphore(concurrency)

    async def _generate_one(chunk: dict) -> str:
        async with sem:
            return await generate_chunk_context(
                document_text=document_text,
                chunk_text=chunk.get("text", ""),
                document_title=document_title,
                openai_client=client,
            )

    results = await asyncio.gather(*[_generate_one(c) for c in chunks])
    return list(results)
