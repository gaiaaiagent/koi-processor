#!/usr/bin/env python3
"""
Direct bakeoff for B8-style contextual retrieval snippets.

This script samples moderate-size parent documents from chunked koi_memories rows,
reconstructs the parent document text, and compares candidate models on the real
"document + chunk -> 1-2 sentence situating context" task described in the B8 plan.

Outputs:
  - Markdown report in scripts/reextraction/
  - JSON raw results alongside the report

Example:
  cd /Users/darrenzal/projects/regenai/koi-processor
  python scripts/reextraction/contextual_bakeoff.py --sample-size 24
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = REPO_ROOT / "scripts" / "reextraction"

CONTEXT_SYSTEM = (
    "You write short contextual snippets for retrieval indexing. "
    "Given a full document and one chunk from it, produce only a concise "
    "1-2 sentence snippet that situates the chunk within the document."
)

JUDGE_SYSTEM = (
    "You are evaluating contextual retrieval snippets for a RAG indexing task. "
    "Choose the better snippet based on factual grounding in the document, "
    "usefulness for retrieval, and concise 1-2 sentence execution. "
    "Return strict JSON only."
)

PRICING: Dict[str, Dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.15, "cached_input": 0.075, "output": 0.60},
    "gemini-2.5-flash-lite": {"input": 0.075, "output": 0.30},
    "gemini-3.1-flash-lite-preview": {"input": 0.25, "cached_input": 0.025, "output": 1.50},
    "deepseek-chat": {"input": 0.28, "cached_input": 0.028, "output": 0.42},
}

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "their", "there",
    "about", "which", "when", "where", "while", "than", "then", "only", "also", "such",
    "have", "has", "had", "been", "being", "were", "will", "would", "could", "should",
    "a", "an", "to", "of", "in", "on", "at", "by", "is", "it", "its", "as", "or",
    "be", "are", "was", "if", "but", "not", "can", "may", "do", "does", "did",
    "these", "those", "here", "than", "them", "they", "you", "your", "our", "we",
    "chunk", "document", "file", "code", "function", "module", "section",
}

GEMINI_SAFETY_CATEGORIES = [
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
]


@dataclass
class ParentCandidate:
    parent_rid: str
    kind: str
    total_chars: int
    chunks: int
    bucket: str


@dataclass
class Sample:
    parent_rid: str
    document_title: str
    document_text: str
    chunk_rid: str
    chunk_index: int
    total_chunks: int
    chunk_text: str
    kind: str
    total_chars: int


def infer_provider(model: str) -> str:
    if model.startswith("gpt-"):
        return "openai"
    if model.startswith("gemini-"):
        return "gemini"
    if model.startswith("deepseek"):
        return "deepseek"
    raise ValueError(f"Unsupported model/provider inference for {model}")


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cached_input_tokens: int = 0,
) -> float:
    rates = PRICING.get(model, {"input": 0.50, "output": 1.00})
    uncached = max(input_tokens - cached_input_tokens, 0)
    cached_rate = rates.get("cached_input", rates["input"])
    return (
        (uncached / 1_000_000) * rates["input"]
        + (cached_input_tokens / 1_000_000) * cached_rate
        + (output_tokens / 1_000_000) * rates["output"]
    )


def kind_for_parent(parent_rid: str) -> str:
    if parent_rid.endswith(".md"):
        return "md"
    if parent_rid.endswith(".proto"):
        return "proto"
    if parent_rid.endswith("pb.gw.go"):
        return "pb.gw.go"
    if parent_rid.endswith("pb.go"):
        return "pb.go"
    if parent_rid.endswith("pulsar.go"):
        return "pulsar.go"
    if parent_rid.endswith(".go"):
        return "go"
    return "other"


def size_bucket(total_chars: int) -> str:
    if total_chars < 15_000:
        return "5k-15k"
    if total_chars < 30_000:
        return "15k-30k"
    return "30k-60k"


def derive_title(parent_rid: str) -> str:
    if ":" in parent_rid:
        parent_rid = parent_rid.split(":", 1)[1]
    slug = parent_rid.replace("github_", "").replace("_", "/")
    return slug


def parse_chunk_index(rid: str, meta_index: Optional[int]) -> int:
    if meta_index is not None:
        return meta_index
    match = re.search(r"#chunk(\d+)$", rid)
    return int(match.group(1)) if match else 0


def stitch_chunks(chunks: Sequence[str], *, max_overlap: int = 250) -> str:
    if not chunks:
        return ""
    stitched = chunks[0]
    for nxt in chunks[1:]:
        stitched = merge_with_overlap(stitched, nxt, max_overlap=max_overlap)
    return stitched


def merge_with_overlap(prev: str, nxt: str, *, max_overlap: int = 250) -> str:
    max_len = min(len(prev), len(nxt), max_overlap)
    for overlap in range(max_len, 39, -1):
        if prev[-overlap:] == nxt[:overlap]:
            return prev + nxt[overlap:]
    return prev + "\n" + nxt


def tokenize(text: str) -> set[str]:
    tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_./:-]{2,}", text.lower()))
    return {t for t in tokens if t not in STOPWORDS}


def contextual_prompt(document_title: str, document_text: str, chunk_text: str) -> str:
    return (
        f"<document>\n{document_title}\n\n{document_text}\n</document>\n\n"
        f"Here is a chunk from this document:\n\n"
        f"<chunk>\n{chunk_text}\n</chunk>\n\n"
        "Give a short (1-2 sentence) context to situate this chunk within the overall "
        "document, for improving search retrieval. Answer only with the context."
    )


def judge_prompt(
    sample: Sample,
    snippet_a: str,
    snippet_b: str,
) -> str:
    return (
        f"Document title:\n{sample.document_title}\n\n"
        f"Full document:\n<document>\n{sample.document_text}\n</document>\n\n"
        f"Chunk:\n<chunk>\n{sample.chunk_text}\n</chunk>\n\n"
        "Task: Decide which candidate snippet is better for contextual retrieval. "
        "The better snippet should accurately situate the chunk within the document, "
        "add useful document-level context that helps retrieval, stay concise (1-2 sentences), "
        "and avoid unsupported claims.\n\n"
        f"Candidate A:\n{snippet_a}\n\n"
        f"Candidate B:\n{snippet_b}\n\n"
        'Return JSON only: {"winner":"A"|"B"|"tie","rationale":"one short sentence"}'
    )


async def call_openai_compat_text(
    prompt: str,
    system_msg: str,
    *,
    api_key: str,
    model: str,
    base_url: str = "https://api.openai.com/v1",
    timeout: float = 120.0,
    response_format: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Dict[str, int]]:
    import httpx

    body: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt},
        ],
        "max_completion_tokens": 512,
    }
    if response_format is not None:
        body["response_format"] = response_format
    if not model.startswith("gpt-5"):
        body["temperature"] = 0.2
    else:
        body["reasoning_effort"] = "minimal"

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{base_url}/chat/completions", headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    usage = data.get("usage", {}) or {}
    prompt_details = usage.get("prompt_tokens_details", {}) or {}
    text = data["choices"][0]["message"]["content"].strip()
    return text, {
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "cached_tokens": prompt_details.get("cached_tokens", 0),
        "prompt_cache_hit_tokens": usage.get("prompt_cache_hit_tokens", 0),
    }


async def call_gemini_text(
    prompt: str,
    system_msg: str,
    *,
    api_key: str,
    model: str,
    json_mode: bool = False,
) -> Tuple[str, Dict[str, int]]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    config_kwargs: Dict[str, Any] = {
        "max_output_tokens": 512,
        "temperature": 0.2,
        "system_instruction": system_msg,
        "safety_settings": [
            types.SafetySetting(category=category, threshold="BLOCK_NONE")
            for category in GEMINI_SAFETY_CATEGORIES
        ],
    }
    if json_mode:
        config_kwargs["response_mime_type"] = "application/json"
    if "flash-lite" not in model:
        config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level="low")

    config = types.GenerateContentConfig(**config_kwargs)
    response = await asyncio.to_thread(
        client.models.generate_content,
        model=model,
        contents=prompt,
        config=config,
    )
    text = (response.text or "").strip()
    usage_meta = getattr(response, "usage_metadata", None)
    usage: Dict[str, int] = {}
    if usage_meta:
        usage["prompt_tokens"] = getattr(usage_meta, "prompt_token_count", 0)
        usage["completion_tokens"] = getattr(usage_meta, "candidates_token_count", 0)
        usage["cached_tokens"] = getattr(usage_meta, "cached_content_token_count", 0)
    return text, usage


async def call_text_model(
    model: str,
    prompt: str,
    system_msg: str,
    *,
    json_mode: bool = False,
) -> Tuple[str, Dict[str, int]]:
    provider = infer_provider(model)
    if provider == "openai":
        api_key = os.environ["OPENAI_API_KEY"]
        response_format = {"type": "json_object"} if json_mode else None
        return await call_openai_compat_text(
            prompt,
            system_msg,
            api_key=api_key,
            model=model,
            response_format=response_format,
        )
    if provider == "deepseek":
        api_key = os.environ["DEEPSEEK_API_KEY"]
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        response_format = {"type": "json_object"} if json_mode else None
        return await call_openai_compat_text(
            prompt,
            system_msg,
            api_key=api_key,
            model=model,
            base_url=base_url,
            response_format=response_format,
        )
    if provider == "gemini":
        api_key = os.environ["GEMINI_API_KEY"]
        return await call_gemini_text(prompt, system_msg, api_key=api_key, model=model, json_mode=json_mode)
    raise ValueError(f"Unsupported provider for {model}")


def load_candidates(conn, *, min_chars: int, max_chars: int) -> List[ParentCandidate]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            WITH parents AS (
              SELECT
                metadata->>'parent_rid' AS parent_rid,
                COUNT(*) AS chunks,
                SUM(LENGTH(COALESCE(content->>'text', ''))) AS total_chars
              FROM koi_memories
              WHERE metadata->>'parent_rid' IS NOT NULL
                AND COALESCE(content->>'text', '') <> ''
              GROUP BY 1
            )
            SELECT parent_rid, chunks, total_chars
            FROM parents
            WHERE total_chars BETWEEN %s AND %s
              AND parent_rid !~ 'pb\\.go$'
              AND parent_rid !~ 'pb\\.gw\\.go$'
              AND parent_rid !~ 'pulsar\\.go$'
            ORDER BY parent_rid
            """,
            (min_chars, max_chars),
        )
        rows = cur.fetchall()
    candidates = []
    for row in rows:
        parent_rid = row["parent_rid"]
        total_chars = int(row["total_chars"])
        candidates.append(
            ParentCandidate(
                parent_rid=parent_rid,
                kind=kind_for_parent(parent_rid),
                total_chars=total_chars,
                chunks=int(row["chunks"]),
                bucket=size_bucket(total_chars),
            )
        )
    return candidates


def diverse_sample(candidates: Sequence[ParentCandidate], sample_size: int, seed: int) -> List[ParentCandidate]:
    rng = random.Random(seed)
    groups: Dict[Tuple[str, str], List[ParentCandidate]] = defaultdict(list)
    for item in candidates:
        groups[(item.kind, item.bucket)].append(item)
    for items in groups.values():
        rng.shuffle(items)

    keys = sorted(groups.keys(), key=lambda k: (k[0], k[1]))
    chosen: List[ParentCandidate] = []
    while len(chosen) < sample_size:
        progressed = False
        for key in keys:
            bucket = groups[key]
            if bucket and len(chosen) < sample_size:
                chosen.append(bucket.pop())
                progressed = True
        if not progressed:
            break
    rng.shuffle(chosen)
    return chosen


def fetch_sample(conn, candidate: ParentCandidate, seed: int) -> Optional[Sample]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
              rid,
              metadata->>'parent_rid' AS parent_rid,
              CASE
                WHEN metadata->>'chunk_index' IS NOT NULL THEN (metadata->>'chunk_index')::int
                WHEN rid ~ '#chunk[0-9]+$' THEN substring(rid from '#chunk([0-9]+)$')::int
                ELSE 0
              END AS chunk_index,
              COALESCE(content->>'text', '') AS chunk_text
            FROM koi_memories
            WHERE metadata->>'parent_rid' = %s
              AND COALESCE(content->>'text', '') <> ''
            ORDER BY chunk_index
            """,
            (candidate.parent_rid,),
        )
        rows = cur.fetchall()

    if len(rows) < 2:
        return None

    chunk_texts = [r["chunk_text"] for r in rows]
    document_text = stitch_chunks(chunk_texts)
    rng = random.Random(f"{seed}:{candidate.parent_rid}")
    valid_indices = list(range(len(rows)))
    if len(rows) > 4:
        valid_indices = list(range(1, len(rows) - 1))
    chosen_idx = rng.choice(valid_indices)
    chosen = rows[chosen_idx]

    return Sample(
        parent_rid=candidate.parent_rid,
        document_title=derive_title(candidate.parent_rid),
        document_text=document_text,
        chunk_rid=chosen["rid"],
        chunk_index=int(chosen["chunk_index"]),
        total_chunks=len(rows),
        chunk_text=chosen["chunk_text"],
        kind=candidate.kind,
        total_chars=len(document_text),
    )


async def generate_context(
    model: str,
    sample: Sample,
    *,
    semaphore: asyncio.Semaphore,
) -> Dict[str, Any]:
    async with semaphore:
        prompt = contextual_prompt(sample.document_title, sample.document_text, sample.chunk_text)
        started = time.perf_counter()
        text, usage = await call_text_model(model, prompt, CONTEXT_SYSTEM)
        latency = time.perf_counter() - started

    text = re.sub(r"\s+", " ", text).strip()
    return {
        "model": model,
        "text": text,
        "usage": usage,
        "latency_s": latency,
        "cost_usd": estimate_cost(
            model,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            cached_input_tokens=(usage.get("cached_tokens") or 0) + (usage.get("prompt_cache_hit_tokens") or 0),
        ),
    }


def parse_judge_json(text: str) -> Dict[str, str]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    blob = match.group(0) if match else text
    data = json.loads(blob)
    winner = data.get("winner", "tie")
    if winner not in {"A", "B", "tie"}:
        winner = "tie"
    rationale = str(data.get("rationale", "")).strip()
    return {"winner": winner, "rationale": rationale}


async def judge_pair(
    judge_model: str,
    sample: Sample,
    left_model: str,
    left_text: str,
    right_model: str,
    right_text: str,
    *,
    semaphore: asyncio.Semaphore,
    seed: int,
) -> Dict[str, Any]:
    rng = random.Random(f"judge:{seed}:{sample.chunk_rid}")
    if rng.random() < 0.5:
        order = [("A", left_model, left_text), ("B", right_model, right_text)]
    else:
        order = [("A", right_model, right_text), ("B", left_model, left_text)]

    prompt = judge_prompt(sample, order[0][2], order[1][2])
    async with semaphore:
        started = time.perf_counter()
        raw_text, usage = await call_text_model(judge_model, prompt, JUDGE_SYSTEM, json_mode=True)
        latency = time.perf_counter() - started

    parsed = parse_judge_json(raw_text)
    label_to_model = {label: model for label, model, _ in order}
    winner_model = label_to_model.get(parsed["winner"]) if parsed["winner"] in {"A", "B"} else "tie"

    return {
        "judge_model": judge_model,
        "winner": winner_model,
        "raw_winner": parsed["winner"],
        "rationale": parsed["rationale"],
        "usage": usage,
        "latency_s": latency,
        "cost_usd": estimate_cost(
            judge_model,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            cached_input_tokens=usage.get("cached_tokens") or 0,
        ),
        "order": [
            {"label": label, "model": model}
            for label, model, _ in order
        ],
    }


def snippet_metrics(sample: Sample, text: str) -> Dict[str, Any]:
    doc_tokens = tokenize(sample.document_text)
    chunk_tokens = tokenize(sample.chunk_text)
    snippet_tokens = tokenize(text)
    new_terms = (snippet_tokens - chunk_tokens) & doc_tokens
    unsupported = snippet_tokens - doc_tokens
    return {
        "chars": len(text),
        "sentences": max(1, len([s for s in re.split(r"[.!?]+", text) if s.strip()])),
        "new_doc_terms": len(new_terms),
        "unsupported_terms": len(unsupported),
    }


def mean(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def median(values: Iterable[float]) -> float:
    values = sorted(values)
    if not values:
        return 0.0
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


def build_report(
    *,
    report_path: Path,
    json_path: Path,
    samples: Sequence[Sample],
    generations: Dict[str, List[Dict[str, Any]]],
    judge_results: List[Dict[str, Any]],
    candidate_models: Sequence[str],
    judge_model: str,
) -> str:
    wins = {model: 0 for model in candidate_models}
    ties = 0
    for jr in judge_results:
        if jr["winner"] == "tie":
            ties += 1
        else:
            wins[jr["winner"]] += 1

    lines = [
        f"# Contextual Retrieval Bakeoff",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        f"- Samples: {len(samples)} reconstructed parent documents",
        f"- Candidates: {', '.join(candidate_models)}",
        f"- Judge: {judge_model}",
        f"- Raw results: `{json_path.name}`",
        "",
        "## Verdict",
        "",
    ]

    ordered = sorted(candidate_models, key=lambda m: (-wins[m], m))
    leader = ordered[0]
    runner_up = ordered[1] if len(ordered) > 1 else None
    lines.append(
        f"- Judge wins: `{leader}` {wins[leader]}/{len(samples)}"
        + (f", `{runner_up}` {wins[runner_up]}/{len(samples)}" if runner_up else "")
        + f", `ties` {ties}/{len(samples)}"
    )
    lines.append("")
    lines.append("## Metrics")
    lines.append("")
    lines.append("| Model | Judge Wins | Mean Cost | Mean Latency | Mean Chars | Mean New Doc Terms | Mean Unsupported Terms |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for model in candidate_models:
        rows = generations[model]
        lines.append(
            f"| `{model}` | {wins[model]} | "
            f"${mean(r['cost_usd'] for r in rows):.4f} | "
            f"{mean(r['latency_s'] for r in rows):.2f}s | "
            f"{mean(r['metrics']['chars'] for r in rows):.1f} | "
            f"{mean(r['metrics']['new_doc_terms'] for r in rows):.2f} | "
            f"{mean(r['metrics']['unsupported_terms'] for r in rows):.2f} |"
        )
    lines.append("")
    lines.append("## Sample Breakdown")
    lines.append("")
    kind_counts: Dict[str, int] = defaultdict(int)
    bucket_counts: Dict[str, int] = defaultdict(int)
    for sample in samples:
        kind_counts[sample.kind] += 1
        bucket_counts[size_bucket(sample.total_chars)] += 1
    lines.append(f"- Kinds: {', '.join(f'{k}={v}' for k, v in sorted(kind_counts.items()))}")
    lines.append(f"- Size buckets: {', '.join(f'{k}={v}' for k, v in sorted(bucket_counts.items()))}")
    lines.append("")
    lines.append("## Notable Samples")
    lines.append("")
    for sample, jr in list(zip(samples, judge_results))[:8]:
        lines.append(f"### `{sample.chunk_rid}`")
        lines.append("")
        for model in candidate_models:
            row = next(r for r in generations[model] if r["chunk_rid"] == sample.chunk_rid)
            lines.append(f"- `{model}`: {row['text']}")
        lines.append(
            f"- Judge: `{jr['winner']}`"
            + (f" ({jr['rationale']})" if jr['rationale'] else "")
        )
        lines.append("")

    report = "\n".join(lines).strip() + "\n"
    report_path.write_text(report, encoding="utf-8")
    return report


async def main() -> None:
    parser = argparse.ArgumentParser(description="Direct B8 contextual retrieval bakeoff")
    parser.add_argument("--sample-size", type=int, default=24)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-chars", type=int, default=5000)
    parser.add_argument("--max-chars", type=int, default=60000)
    parser.add_argument(
        "--models",
        default="gpt-4o-mini,gemini-3.1-flash-lite-preview",
        help="Comma-separated candidate models",
    )
    parser.add_argument("--judge-model", default="gemini-2.5-flash-lite")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    db_url = os.environ["POSTGRES_URL"]
    candidate_models = [m.strip() for m in args.models.split(",") if m.strip()]

    conn = psycopg2.connect(db_url)
    try:
        candidates = load_candidates(conn, min_chars=args.min_chars, max_chars=args.max_chars)
        chosen = diverse_sample(candidates, args.sample_size, args.seed)
        samples = [s for c in chosen if (s := fetch_sample(conn, c, args.seed))]
    finally:
        conn.close()

    if not samples:
        raise SystemExit("No samples available for contextual bakeoff")

    gen_semaphore = asyncio.Semaphore(args.concurrency)
    judge_semaphore = asyncio.Semaphore(min(args.concurrency, 3))

    generations: Dict[str, List[Dict[str, Any]]] = {model: [] for model in candidate_models}
    for sample in samples:
        results = await asyncio.gather(
            *[
                generate_context(model, sample, semaphore=gen_semaphore)
                for model in candidate_models
            ]
        )
        for model, result in zip(candidate_models, results):
            result["chunk_rid"] = sample.chunk_rid
            result["parent_rid"] = sample.parent_rid
            result["metrics"] = snippet_metrics(sample, result["text"])
            generations[model].append(result)

    judge_tasks = []
    for sample in samples:
        left = next(r for r in generations[candidate_models[0]] if r["chunk_rid"] == sample.chunk_rid)
        right = next(r for r in generations[candidate_models[1]] if r["chunk_rid"] == sample.chunk_rid)
        judge_tasks.append(
            judge_pair(
                args.judge_model,
                sample,
                candidate_models[0],
                left["text"],
                candidate_models[1],
                right["text"],
                semaphore=judge_semaphore,
                seed=args.seed,
            )
        )
    judge_results = await asyncio.gather(*judge_tasks)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = OUTPUT_DIR / f"CONTEXTUAL_BAKEOFF_{timestamp}.md"
    json_path = OUTPUT_DIR / f"CONTEXTUAL_BAKEOFF_{timestamp}.json"

    report = build_report(
        report_path=report_path,
        json_path=json_path,
        samples=samples,
        generations=generations,
        judge_results=judge_results,
        candidate_models=candidate_models,
        judge_model=args.judge_model,
    )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample_size": len(samples),
        "candidate_models": candidate_models,
        "judge_model": args.judge_model,
        "samples": [
            {
                "parent_rid": s.parent_rid,
                "document_title": s.document_title,
                "chunk_rid": s.chunk_rid,
                "chunk_index": s.chunk_index,
                "total_chunks": s.total_chunks,
                "kind": s.kind,
                "total_chars": s.total_chars,
            }
            for s in samples
        ],
        "generations": generations,
        "judge_results": judge_results,
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(report)
    print(f"\nSaved report: {report_path}")
    print(f"Saved raw results: {json_path}")


if __name__ == "__main__":
    asyncio.run(main())
