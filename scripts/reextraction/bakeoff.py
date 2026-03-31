#!/usr/bin/env python3
"""
Multi-provider model bakeoff for entity extraction.

This compares extraction quality and cost across providers/models on the same
document sample, using the shared prompt builder for fair comparison.

Default models (from .env BAKEOFF_* vars):
  - gpt-4o-mini             (OpenAI baseline, Batch API by default)
  - gpt-5-nano              (OpenAI cheap tier, Batch API by default)
  - gemini-2.5-flash-lite   (Google, Batch API by default)
  - deepseek-chat           (DeepSeek, sync API with provider-side prefix cache)

Cost-saving techniques:
  1. OpenAI Batch API for offline runs where supported.
  2. Gemini Batch API for offline runs where supported.
  3. DeepSeek/OpenAI cached-token accounting when usage reports it.
  4. Per-model disk cache so re-runs only analyze, not re-call.
  5. Shared prompt/system instructions for fair prompts and repeated-prefix reuse.
  6. Stratified sampling across source types so the bakeoff is more decision-useful.

Usage:
  cd /Users/darrenzal/projects/RegenAI/koi-processor
  python scripts/reextraction/bakeoff.py --sample-size 80 --budget 25
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.entity_types import is_llm_allowed_type, normalize_type
from extraction.prompt_builder import build_extraction_prompt, get_system_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bakeoff")

# ---------------------------------------------------------------------------
# Pricing ($ per 1M tokens)
# Values should stay conservative when provider pricing rules are ambiguous.
# ---------------------------------------------------------------------------
PRICING: Dict[str, Dict[str, float]] = {
    "gpt-4o-mini": {
        "input": 0.15,
        "cached_input": 0.075,
        "output": 0.60,
        "batch_discount": 0.50,
    },
    "gpt-5-nano": {
        "input": 0.05,
        "cached_input": 0.005,
        "output": 0.40,
        "batch_discount": 0.50,
    },
    "gemini-2.5-flash-lite": {
        "input": 0.075,
        "output": 0.30,
        "batch_discount": 0.50,
    },
    "gemini-3.1-flash-lite-preview": {
        "input": 0.25,
        "cached_input": 0.025,
        "output": 1.50,
        "batch_discount": 0.50,
    },
    "deepseek-chat": {
        "input": 0.28,
        "cached_input": 0.028,
        "output": 0.42,
    },
}


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    batch: bool = False,
    cached_input_tokens: int = 0,
    uncached_input_tokens: Optional[int] = None,
) -> float:
    rates = PRICING.get(model, {"input": 0.50, "output": 1.00})
    batch_discount = rates.get("batch_discount", 1.0) if batch else 1.0

    if uncached_input_tokens is None:
        uncached_input_tokens = max(input_tokens - cached_input_tokens, 0)

    # For batch pricing we conservatively apply the documented 50% discount to
    # standard rates. We do not try to stack cached-input discounts on top.
    if batch:
        cached_input_cost = (cached_input_tokens / 1_000_000) * rates["input"] * batch_discount
        uncached_input_cost = (uncached_input_tokens / 1_000_000) * rates["input"] * batch_discount
    else:
        cached_input_rate = rates.get("cached_input", rates["input"])
        cached_input_cost = (cached_input_tokens / 1_000_000) * cached_input_rate
        uncached_input_cost = (uncached_input_tokens / 1_000_000) * rates["input"]

    output_cost = (output_tokens / 1_000_000) * rates["output"] * batch_discount
    return cached_input_cost + uncached_input_cost + output_cost


# ---------------------------------------------------------------------------
# Provider adapters — all return (extraction_dict, usage_dict)
# ---------------------------------------------------------------------------

async def call_openai_compat(
    prompt: str,
    system_msg: str,
    *,
    api_key: str,
    model: str,
    base_url: str = "https://api.openai.com/v1",
    timeout: float = 60.0,
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """Call any OpenAI-compatible chat/completions endpoint."""
    import httpx

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt},
        ],
        "max_completion_tokens": 4096,
        "response_format": {"type": "json_object"},
    }
    if not model.startswith("gpt-5"):
        body["temperature"] = 0.3
    else:
        body["reasoning_effort"] = "minimal"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{base_url}/chat/completions", headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {}) or {}
    prompt_details = usage.get("prompt_tokens_details", {}) or {}
    extraction = json.loads(content)
    return extraction, {
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "cached_tokens": prompt_details.get("cached_tokens", 0),
        "prompt_cache_hit_tokens": usage.get("prompt_cache_hit_tokens", 0),
        "prompt_cache_miss_tokens": usage.get("prompt_cache_miss_tokens", 0),
    }


async def call_gemini(
    prompt: str,
    system_msg: str,
    *,
    api_key: str,
    model: str,
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """Call Google Gemini via the genai SDK."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    config_kwargs: Dict[str, Any] = {
        "max_output_tokens": 4096,
        "response_mime_type": "application/json",
        "temperature": 1.0,
        "system_instruction": system_msg,
        "safety_settings": [
            types.SafetySetting(category=category, threshold="BLOCK_NONE")
            for category in GEMINI_SAFETY_CATEGORIES
        ],
    }
    if "flash-lite" not in model:
        config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level="low")

    config = types.GenerateContentConfig(
        **config_kwargs,
    )
    response = await asyncio.to_thread(
        client.models.generate_content,
        model=model,
        contents=prompt,
        config=config,
    )
    text = response.text or ""
    extraction = _extract_json(text)

    usage_meta = getattr(response, "usage_metadata", None)
    usage = {}
    if usage_meta:
        usage["prompt_tokens"] = getattr(usage_meta, "prompt_token_count", 0)
        usage["completion_tokens"] = getattr(usage_meta, "candidates_token_count", 0)
    return extraction, usage


def _extract_json(text: str) -> Dict[str, Any]:
    """Robustly extract JSON from LLM response text."""
    import re

    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return {}


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------

@dataclass
class ModelSpec:
    name: str
    provider: str  # "openai", "deepseek", "gemini"
    model_id: str
    api_key_env: str
    base_url: str = "https://api.openai.com/v1"


def build_model_specs() -> List[ModelSpec]:
    """Build model specs from environment variables."""
    specs: List[ModelSpec] = []

    openai_key = os.getenv("OPENAI_API_KEY", "")
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    baseline = os.getenv("BAKEOFF_OPENAI_BASELINE_MODEL", "gpt-4o-mini")
    cheap = os.getenv("BAKEOFF_OPENAI_CHEAP_MODEL", "gpt-5-nano")
    gemini_model = os.getenv("BAKEOFF_GEMINI_MODEL", "gemini-2.5-flash-lite")
    deepseek_model = os.getenv("BAKEOFF_DEEPSEEK_MODEL", "deepseek-chat")
    extra_models_raw = os.getenv("BAKEOFF_EXTRA_MODELS", "")
    extra_models = [model.strip() for model in extra_models_raw.split(",") if model.strip()]

    if openai_key:
        specs.append(ModelSpec(baseline, "openai", baseline, "OPENAI_API_KEY"))
        if cheap != baseline:
            specs.append(ModelSpec(cheap, "openai", cheap, "OPENAI_API_KEY"))
    if gemini_key:
        specs.append(ModelSpec(gemini_model, "gemini", gemini_model, "GEMINI_API_KEY"))
    if deepseek_key:
        specs.append(
            ModelSpec(
                deepseek_model,
                "deepseek",
                deepseek_model,
                "DEEPSEEK_API_KEY",
                base_url=deepseek_url,
            )
        )

    for model in extra_models:
        if any(spec.name == model for spec in specs):
            continue

        provider = ""
        key_env = ""
        base_url = "https://api.openai.com/v1"
        if model.startswith("gpt-"):
            provider = "openai"
            key_env = "OPENAI_API_KEY"
        elif model.startswith("gemini-"):
            provider = "gemini"
            key_env = "GEMINI_API_KEY"
        elif model.startswith("deepseek-"):
            provider = "deepseek"
            key_env = "DEEPSEEK_API_KEY"
            base_url = deepseek_url

        if not provider or not os.getenv(key_env):
            log.warning(f"Skipping BAKEOFF_EXTRA_MODELS entry {model}: unsupported or missing API key")
            continue

        specs.append(ModelSpec(model, provider, model, key_env, base_url=base_url))

    return specs


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

@dataclass
class DocResult:
    rid: str
    source: str
    length: int
    entities_raw: int = 0
    entities_passed: int = 0
    entities_blocked: int = 0
    relationships: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    batch_mode: str = "sync"
    error: str = ""

    @property
    def pass_rate(self) -> float:
        return (self.entities_passed / self.entities_raw * 100) if self.entities_raw else 0.0


@dataclass
class ModelResult:
    name: str
    docs: List[DocResult] = field(default_factory=list)
    total_cost: float = 0.0
    errors: int = 0
    execution_mode: str = "sync"
    batch_job_name: str = ""

    @property
    def total_entities(self) -> int:
        return sum(d.entities_raw for d in self.docs)

    @property
    def total_passed(self) -> int:
        return sum(d.entities_passed for d in self.docs)

    @property
    def total_blocked(self) -> int:
        return sum(d.entities_blocked for d in self.docs)

    @property
    def total_relationships(self) -> int:
        return sum(d.relationships for d in self.docs)

    @property
    def total_cached_input_tokens(self) -> int:
        return sum(d.cached_input_tokens for d in self.docs)

    @property
    def mean_pass_rate(self) -> float:
        rates = [d.pass_rate for d in self.docs if d.entities_raw > 0]
        return sum(rates) / len(rates) if rates else 0.0

    @property
    def mean_latency(self) -> float:
        lats = [d.latency_s for d in self.docs if d.latency_s > 0]
        return sum(lats) / len(lats) if lats else 0.0

    @property
    def p95_latency(self) -> float:
        lats = sorted(d.latency_s for d in self.docs if d.latency_s > 0)
        if not lats:
            return 0.0
        idx = int(len(lats) * 0.95)
        return lats[min(idx, len(lats) - 1)]


# ---------------------------------------------------------------------------
# Pipeline evaluation (reuses existing quality gates)
# ---------------------------------------------------------------------------

_PIPELINE_INTEGRATOR = None


def evaluate_extraction(extraction: Dict[str, Any]) -> Tuple[int, int, int, int]:
    """Run extraction through quality pipeline. Returns (raw, passed, blocked, rels)."""
    from knowledge_graph.graph_integration import KnowledgeGraphIntegrator
    global _PIPELINE_INTEGRATOR

    entities = extraction.get("entities", [])
    relationships = extraction.get("relationships", [])

    if not entities:
        return 0, 0, 0, len(relationships)

    normalized = []
    for entity in entities:
        raw_type = entity.get("type", "")
        norm_type = normalize_type(raw_type)
        if not is_llm_allowed_type(norm_type):
            continue
        normalized.append(
            {
                "name": entity.get("name", ""),
                "type": norm_type,
                "confidence": entity.get("confidence", 0.5),
            }
        )

    if not normalized:
        return len(entities), 0, len(entities), len(relationships)

    if _PIPELINE_INTEGRATOR is None:
        _PIPELINE_INTEGRATOR = KnowledgeGraphIntegrator(store_type="memory", use_pipeline=True)
    processed = _PIPELINE_INTEGRATOR.process_entities_batch(normalized)

    passed = sum(1 for entity in processed if not entity.get("blocked"))
    blocked = sum(1 for entity in processed if entity.get("blocked"))

    return len(entities), passed, blocked, len(relationships)


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------

CACHE_DIR = REPO_ROOT / "scripts" / "reextraction" / "bakeoff_cache"
GEMINI_BATCH_STATE_DIR = CACHE_DIR / "gemini_batch_jobs"
GEMINI_SAFETY_CATEGORIES = (
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
)
GEMINI_TERMINAL_BATCH_STATES = {
    "JOB_STATE_SUCCEEDED",
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
}


def cache_key(model: str, rid: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe_model = model.replace("/", "_").replace(":", "_")
    safe_rid = "".join(ch if ch.isalnum() else "_" for ch in rid[:24]).strip("_") or "rid"
    rid_hash = hashlib.sha1(rid.encode("utf-8")).hexdigest()[:12]
    return CACHE_DIR / f"{safe_model}__{safe_rid}__{rid_hash}.json"


def cache_get(model: str, rid: str) -> Optional[Dict[str, Any]]:
    path = cache_key(model, rid)
    if path.exists():
        return json.loads(path.read_text())
    return None


def cache_put(model: str, rid: str, data: Dict[str, Any]) -> None:
    path = cache_key(model, rid)
    path.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Shared prompt / result helpers
# ---------------------------------------------------------------------------

def _safe_name(value: str) -> str:
    return value.replace("/", "_").replace(":", "_")


def _gemini_batch_signature(spec: ModelSpec, docs: List[Dict[str, Any]]) -> str:
    joined = "\n".join(doc["rid"] for doc in docs)
    return hashlib.sha1(f"{spec.name}\n{joined}".encode("utf-8")).hexdigest()[:16]


def _gemini_batch_state_path(spec: ModelSpec, docs: List[Dict[str, Any]]) -> Path:
    GEMINI_BATCH_STATE_DIR.mkdir(parents=True, exist_ok=True)
    return GEMINI_BATCH_STATE_DIR / f"{_safe_name(spec.name)}__{_gemini_batch_signature(spec, docs)}.json"


def _gemini_generation_config_json() -> Dict[str, Any]:
    return {
        "response_mime_type": "application/json",
        "temperature": 1.0,
    }


def _gemini_safety_settings_json() -> List[Dict[str, str]]:
    return [{"category": category, "threshold": "BLOCK_NONE"} for category in GEMINI_SAFETY_CATEGORIES]


def _write_gemini_batch_input_file(
    spec: ModelSpec, docs: List[Dict[str, Any]], system_msg: str
) -> Tuple[Path, Dict[str, str]]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    prompt_by_rid: Dict[str, str] = {}
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", prefix=f"{_safe_name(spec.name)}_", dir=CACHE_DIR, delete=False
    ) as handle:
        for doc in docs:
            prompt = build_prompt_for_doc(doc)
            prompt_by_rid[doc["rid"]] = prompt
            request = {
                "key": doc["rid"],
                "request": {
                    "contents": [{"parts": [{"text": prompt}], "role": "user"}],
                    "system_instruction": {"parts": [{"text": system_msg}]},
                    "generation_config": _gemini_generation_config_json(),
                    "safety_settings": _gemini_safety_settings_json(),
                },
            }
            handle.write(json.dumps(request) + "\n")

    return Path(handle.name), prompt_by_rid


def _extract_gemini_response_text(response_obj: Any) -> str:
    if response_obj is None:
        return ""

    text = getattr(response_obj, "text", None)
    if text:
        return text

    if isinstance(response_obj, dict):
        candidates = response_obj.get("candidates") or []
        for candidate in candidates:
            content = candidate.get("content") or {}
            parts = content.get("parts") or []
            text_parts = [part.get("text", "") for part in parts if part.get("text")]
            if text_parts:
                return "\n".join(text_parts).strip()

    return ""


def _extract_gemini_usage(response_obj: Any) -> Dict[str, int]:
    usage_meta = getattr(response_obj, "usage_metadata", None)
    if usage_meta:
        return {
            "prompt_tokens": getattr(usage_meta, "prompt_token_count", 0) or 0,
            "completion_tokens": getattr(usage_meta, "candidates_token_count", 0) or 0,
            "cached_tokens": getattr(usage_meta, "cached_content_token_count", 0) or 0,
        }

    if isinstance(response_obj, dict):
        usage_meta = response_obj.get("usageMetadata") or response_obj.get("usage_metadata") or {}
        return {
            "prompt_tokens": usage_meta.get("promptTokenCount")
            or usage_meta.get("prompt_token_count")
            or 0,
            "completion_tokens": usage_meta.get("candidatesTokenCount")
            or usage_meta.get("candidates_token_count")
            or 0,
            "cached_tokens": usage_meta.get("cachedContentTokenCount")
            or usage_meta.get("cached_content_token_count")
            or 0,
        }

    return {}


def _parse_gemini_batch_file_results(file_bytes: bytes) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    responses_by_key: Dict[str, Dict[str, Any]] = {}
    errors_by_key: Dict[str, str] = {}

    for raw_line in file_bytes.decode("utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        record = json.loads(line)
        key = record.get("key") or record.get("metadata", {}).get("key")
        if not key:
            continue
        if record.get("response"):
            responses_by_key[key] = record["response"]
        else:
            error_obj = record.get("error") or record.get("status") or "Unknown Gemini batch error"
            errors_by_key[key] = json.dumps(error_obj) if isinstance(error_obj, dict) else str(error_obj)

    return responses_by_key, errors_by_key

def build_prompt_for_doc(doc: Dict[str, Any]) -> str:
    return build_extraction_prompt(
        content=doc["text"],
        source_type=doc["source"],
        metadata={"rid": doc["rid"]},
        max_content_length=3000,
    )


def hydrate_doc_result(
    spec: ModelSpec,
    doc: Dict[str, Any],
    *,
    extraction: Dict[str, Any],
    usage: Dict[str, Any],
    prompt: str,
    latency_s: float,
    batch: bool,
    batch_mode: str,
) -> DocResult:
    dr = DocResult(
        rid=doc["rid"],
        source=doc["source"],
        length=doc["length"],
        latency_s=latency_s,
        batch_mode=batch_mode,
    )

    dr.input_tokens = int(usage.get("prompt_tokens", 0) or 0)
    dr.output_tokens = int(usage.get("completion_tokens", 0) or 0)

    prompt_cache_hit = int(usage.get("prompt_cache_hit_tokens", 0) or 0)
    cached_tokens = int(usage.get("cached_tokens", 0) or 0)
    dr.cached_input_tokens = prompt_cache_hit or cached_tokens

    if dr.input_tokens == 0:
        dr.input_tokens = len(prompt) // 4
    if dr.output_tokens == 0:
        dr.output_tokens = len(json.dumps(extraction)) // 4

    prompt_cache_miss = usage.get("prompt_cache_miss_tokens")
    uncached_input_tokens = int(prompt_cache_miss) if prompt_cache_miss is not None else None
    dr.cost_usd = estimate_cost(
        spec.name,
        dr.input_tokens,
        dr.output_tokens,
        batch=batch,
        cached_input_tokens=dr.cached_input_tokens,
        uncached_input_tokens=uncached_input_tokens,
    )

    raw, passed, blocked, rels = evaluate_extraction(extraction)
    dr.entities_raw = raw
    dr.entities_passed = passed
    dr.entities_blocked = blocked
    dr.relationships = rels
    return dr


def hydrate_doc_result_from_cache(spec: ModelSpec, doc: Dict[str, Any], cached: Dict[str, Any]) -> DocResult:
    extraction = cached.get("extraction", {}) or {}
    usage = cached.get("usage")
    if usage is None:
        usage = {
            "prompt_tokens": cached.get("input_tokens", 0),
            "completion_tokens": cached.get("output_tokens", 0),
            "cached_tokens": cached.get("cached_input_tokens", 0),
        }

    prompt = build_prompt_for_doc(doc)
    return hydrate_doc_result(
        spec,
        doc,
        extraction=extraction,
        usage=usage,
        prompt=prompt,
        latency_s=float(cached.get("latency_s", 0.0) or 0.0),
        batch=bool(cached.get("batch_mode", "") == "batch"),
        batch_mode=str(cached.get("batch_mode", "cache")),
    )


def cache_doc_result(model: str, doc_result: DocResult, extraction: Dict[str, Any], usage: Dict[str, Any]) -> None:
    cache_put(
        model,
        doc_result.rid,
        {
            "extraction": extraction,
            "usage": usage,
            "latency_s": doc_result.latency_s,
            "batch_mode": doc_result.batch_mode,
        },
    )


# ---------------------------------------------------------------------------
# Sync runner — used for DeepSeek and fallback paths
# ---------------------------------------------------------------------------

async def extract_one(
    spec: ModelSpec,
    doc: Dict[str, Any],
    system_msg: str,
    *,
    use_cache: bool = True,
) -> DocResult:
    """Extract entities from a single document with a single model."""
    cached = cache_get(spec.name, doc["rid"]) if use_cache else None
    if cached:
        return hydrate_doc_result_from_cache(spec, doc, cached)

    prompt = build_prompt_for_doc(doc)
    api_key = os.getenv(spec.api_key_env, "")
    started = time.time()

    try:
        if spec.provider in ("openai", "deepseek"):
            extraction, usage = await call_openai_compat(
                prompt,
                system_msg,
                api_key=api_key,
                model=spec.model_id,
                base_url=spec.base_url,
            )
        elif spec.provider == "gemini":
            extraction, usage = await call_gemini(
                prompt,
                system_msg,
                api_key=api_key,
                model=spec.model_id,
            )
        else:
            raise ValueError(f"Unknown provider: {spec.provider}")

        dr = hydrate_doc_result(
            spec,
            doc,
            extraction=extraction,
            usage=usage,
            prompt=prompt,
            latency_s=time.time() - started,
            batch=False,
            batch_mode="sync",
        )
        if use_cache:
            cache_doc_result(spec.name, dr, extraction, usage)
        return dr

    except Exception as exc:
        return DocResult(
            rid=doc["rid"],
            source=doc["source"],
            length=doc["length"],
            latency_s=time.time() - started,
            batch_mode="sync",
            error=f"{type(exc).__name__}: {exc}",
        )


async def run_model_sync(
    spec: ModelSpec,
    documents: List[Dict[str, Any]],
    *,
    concurrency: int,
    budget_cap_usd: float,
    use_cache: bool,
) -> ModelResult:
    """Run extraction for all documents with a single model using sync calls."""
    result = ModelResult(name=spec.name, execution_mode="sync")
    system_msg = get_system_message()
    sem = asyncio.Semaphore(concurrency)

    log.info(f"\n{'=' * 60}")
    log.info(f"Model: {spec.name} ({spec.provider}, sync)")
    log.info(f"Budget cap: ${budget_cap_usd:.2f}")
    log.info(f"{'=' * 60}")

    async def bounded(doc: Dict[str, Any]) -> DocResult:
        async with sem:
            return await extract_one(spec, doc, system_msg, use_cache=use_cache)

    tasks = [bounded(doc) for doc in documents]
    completed = 0
    for coro in asyncio.as_completed(tasks):
        dr = await coro
        result.docs.append(dr)
        result.total_cost += dr.cost_usd
        if dr.error:
            result.errors += 1

        completed += 1
        status = "ERR" if dr.error else f"{dr.entities_passed}/{dr.entities_raw}"
        log.info(
            f"  [{completed}/{len(documents)}] {dr.rid[:8]} "
            f"{status} ${dr.cost_usd:.4f} {dr.latency_s:.1f}s"
        )

        if result.total_cost >= budget_cap_usd:
            remaining = len(documents) - completed
            if remaining > 0:
                log.warning(
                    f"  Budget cap reached (${result.total_cost:.2f}). "
                    f"Skipping {remaining} remaining docs."
                )
                break

    rid_order = {doc["rid"]: i for i, doc in enumerate(documents)}
    result.docs.sort(key=lambda d: rid_order.get(d.rid, 999))
    return result


# ---------------------------------------------------------------------------
# Batch runners — OpenAI and Gemini
# ---------------------------------------------------------------------------

def _build_openai_batch_file(spec: ModelSpec, docs: List[Dict[str, Any]], system_msg: str) -> Tuple[str, Dict[str, str]]:
    """Create a JSONL file for OpenAI Batch API and return its path plus prompt map."""
    prompt_by_rid: Dict[str, str] = {}
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".jsonl") as handle:
        for doc in docs:
            prompt = build_prompt_for_doc(doc)
            prompt_by_rid[doc["rid"]] = prompt
            request = {
                "custom_id": doc["rid"],
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": spec.model_id,
                    "messages": [
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": prompt},
                    ],
                    "max_completion_tokens": 4096,
                    "response_format": {"type": "json_object"},
                },
            }
            if not spec.model_id.startswith("gpt-5"):
                request["body"]["temperature"] = 0.3
            else:
                request["body"]["reasoning_effort"] = "minimal"
            handle.write(json.dumps(request) + "\n")
        return handle.name, prompt_by_rid


def _run_openai_batch_sync(
    spec: ModelSpec,
    docs: List[Dict[str, Any]],
    system_msg: str,
    *,
    api_key: str,
    poll_interval_s: int,
    timeout_s: int,
) -> Dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=spec.base_url)
    input_path, prompt_by_rid = _build_openai_batch_file(spec, docs, system_msg)

    try:
        with open(input_path, "rb") as batch_file:
            uploaded = client.files.create(file=batch_file, purpose="batch")

        batch = client.batches.create(
            input_file_id=uploaded.id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
            metadata={"runner": "bakeoff", "model": spec.model_id},
        )

        terminal = {"completed", "failed", "cancelled", "expired"}
        deadline = time.time() + timeout_s
        while batch.status not in terminal:
            if time.time() > deadline:
                raise TimeoutError(f"Timed out waiting for OpenAI batch {batch.id} ({batch.status})")
            time.sleep(poll_interval_s)
            batch = client.batches.retrieve(batch.id)

        if batch.status != "completed":
            raise RuntimeError(f"OpenAI batch {batch.id} ended with status={batch.status}")

        output_records: List[Dict[str, Any]] = []
        if batch.output_file_id:
            content = client.files.content(batch.output_file_id).text
            output_records = [json.loads(line) for line in content.splitlines() if line.strip()]

        error_records: List[Dict[str, Any]] = []
        if batch.error_file_id:
            content = client.files.content(batch.error_file_id).text
            error_records = [json.loads(line) for line in content.splitlines() if line.strip()]

        return {
            "batch_id": batch.id,
            "status": batch.status,
            "output_records": output_records,
            "error_records": error_records,
            "prompt_by_rid": prompt_by_rid,
        }
    finally:
        with suppress(FileNotFoundError):
            os.unlink(input_path)


def _parse_openai_batch_record(record: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any], Optional[str]]:
    response = record.get("response") or {}
    status_code = int(response.get("status_code", 0) or 0)
    body = response.get("body") or {}

    if status_code >= 400:
        error_obj = body.get("error") or record.get("error") or {}
        message = error_obj.get("message") if isinstance(error_obj, dict) else str(error_obj)
        return None, {}, message or f"HTTP {status_code}"

    try:
        content = body["choices"][0]["message"]["content"]
        extraction = json.loads(content)
    except Exception as exc:
        return None, {}, f"ParseError: {exc}"

    usage = body.get("usage", {}) or {}
    prompt_details = usage.get("prompt_tokens_details", {}) or {}
    return (
        extraction,
        {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "cached_tokens": prompt_details.get("cached_tokens", 0),
        },
        None,
    )


async def run_openai_batch_model(
    spec: ModelSpec,
    documents: List[Dict[str, Any]],
    *,
    budget_cap_usd: float,
    use_cache: bool,
    poll_interval_s: int,
    timeout_s: int,
) -> ModelResult:
    result = ModelResult(name=spec.name, execution_mode="batch")
    system_msg = get_system_message()

    cached_docs: List[DocResult] = []
    fresh_docs: List[Dict[str, Any]] = []
    for doc in documents:
        cached = cache_get(spec.name, doc["rid"]) if use_cache else None
        if cached:
            cached_docs.append(hydrate_doc_result_from_cache(spec, doc, cached))
        else:
            fresh_docs.append(doc)

    for dr in cached_docs:
        result.docs.append(dr)
        result.total_cost += dr.cost_usd

    log.info(f"\n{'=' * 60}")
    log.info(f"Model: {spec.name} ({spec.provider}, batch)")
    log.info(f"Budget cap: ${budget_cap_usd:.2f}")
    log.info(f"Cached docs reused: {len(cached_docs)}")
    log.info(f"Fresh docs to batch: {len(fresh_docs)}")
    log.info(f"{'=' * 60}")

    if result.total_cost >= budget_cap_usd:
        log.warning(f"Cached results already exceed budget cap for {spec.name}.")
        result.docs.sort(key=lambda d: next(i for i, doc in enumerate(documents) if doc['rid'] == d.rid))
        return result

    if not fresh_docs:
        rid_order = {doc["rid"]: i for i, doc in enumerate(documents)}
        result.docs.sort(key=lambda d: rid_order.get(d.rid, 999))
        return result

    started = time.time()
    batch_payload = await asyncio.to_thread(
        _run_openai_batch_sync,
        spec,
        fresh_docs,
        system_msg,
        api_key=os.getenv(spec.api_key_env, ""),
        poll_interval_s=poll_interval_s,
        timeout_s=timeout_s,
    )
    elapsed = time.time() - started
    result.batch_job_name = batch_payload["batch_id"]

    by_rid = {doc["rid"]: doc for doc in fresh_docs}
    prompt_by_rid = batch_payload["prompt_by_rid"]
    success_by_rid: Dict[str, DocResult] = {}
    errors_by_rid: Dict[str, str] = {}
    per_doc_latency = elapsed / max(len(fresh_docs), 1)

    for record in batch_payload["output_records"]:
        rid = record.get("custom_id", "")
        if rid not in by_rid:
            continue
        extraction, usage, error = _parse_openai_batch_record(record)
        if error:
            errors_by_rid[rid] = error
            continue
        dr = hydrate_doc_result(
            spec,
            by_rid[rid],
            extraction=extraction or {},
            usage=usage,
            prompt=prompt_by_rid[rid],
            latency_s=per_doc_latency,
            batch=True,
            batch_mode="batch",
        )
        success_by_rid[rid] = dr
        if use_cache:
            cache_doc_result(spec.name, dr, extraction or {}, usage)

    for record in batch_payload["error_records"]:
        rid = record.get("custom_id", "")
        if rid and rid not in errors_by_rid:
            _, _, error = _parse_openai_batch_record(record)
            errors_by_rid[rid] = error or "Unknown batch error"

    for doc in fresh_docs:
        dr = success_by_rid.get(doc["rid"])
        if dr is None:
            dr = DocResult(
                rid=doc["rid"],
                source=doc["source"],
                length=doc["length"],
                latency_s=per_doc_latency,
                batch_mode="batch",
                error=errors_by_rid.get(doc["rid"], "Missing result in OpenAI batch output"),
            )
        result.docs.append(dr)
        result.total_cost += dr.cost_usd
        if dr.error:
            result.errors += 1

    rid_order = {doc["rid"]: i for i, doc in enumerate(documents)}
    result.docs.sort(key=lambda d: rid_order.get(d.rid, 999))
    return result


def _run_gemini_batch_sync(
    spec: ModelSpec,
    docs: List[Dict[str, Any]],
    system_msg: str,
    *,
    api_key: str,
    poll_interval_s: int,
    timeout_s: int,
) -> Dict[str, Any]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    state_path = _gemini_batch_state_path(spec, docs)
    prompt_by_rid = {doc["rid"]: build_prompt_for_doc(doc) for doc in docs}
    job = None

    if state_path.exists():
        with suppress(Exception):
            state = json.loads(state_path.read_text())
            job_name = state.get("job_name")
            if job_name:
                job = client.batches.get(name=job_name)
                log.info(f"Resuming Gemini batch job {job.name} (state={job.state.name})")

    if job is None or job.state.name in GEMINI_TERMINAL_BATCH_STATES - {"JOB_STATE_SUCCEEDED"}:
        batch_input_path, prompt_by_rid = _write_gemini_batch_input_file(spec, docs, system_msg)
        try:
            uploaded_file = client.files.upload(
                file=str(batch_input_path),
                config=types.UploadFileConfig(
                    display_name=f"bakeoff-{spec.model_id}-{int(time.time())}",
                    mime_type="jsonl",
                ),
            )
            job = client.batches.create(
                model=spec.model_id,
                src=uploaded_file.name,
                config={"display_name": f"bakeoff-{spec.model_id}-{int(time.time())}"},
            )
            state_path.write_text(
                json.dumps(
                    {
                        "job_name": job.name,
                        "input_file_name": uploaded_file.name,
                        "state": job.state.name,
                        "doc_rids": [doc["rid"] for doc in docs],
                        "created_at": time.time(),
                    },
                    indent=2,
                )
            )
        finally:
            with suppress(FileNotFoundError):
                batch_input_path.unlink()

    deadline = time.time() + timeout_s
    while job.state.name not in GEMINI_TERMINAL_BATCH_STATES:
        state_path.write_text(
            json.dumps(
                {
                    "job_name": job.name,
                    "state": job.state.name,
                    "doc_rids": [doc["rid"] for doc in docs],
                    "updated_at": time.time(),
                },
                indent=2,
            )
        )
        if time.time() > deadline:
            raise TimeoutError(
                f"Timed out waiting for Gemini batch {job.name} ({job.state.name}). "
                f"Batch jobs target up to 24h turnaround; rerun later to resume from {state_path}."
            )
        time.sleep(poll_interval_s)
        job = client.batches.get(name=job.name)

    if job.state.name != "JOB_STATE_SUCCEEDED":
        raise RuntimeError(f"Gemini batch {job.name} ended with state={job.state.name}")

    state_payload = {
        "job_name": job.name,
        "state": job.state.name,
        "doc_rids": [doc["rid"] for doc in docs],
        "updated_at": time.time(),
    }
    if getattr(job, "dest", None) and getattr(job.dest, "file_name", None):
        state_payload["result_file_name"] = job.dest.file_name
    state_path.write_text(json.dumps(state_payload, indent=2))

    responses_by_rid: Dict[str, Any] = {}
    errors_by_rid: Dict[str, str] = {}
    if getattr(job, "dest", None) and getattr(job.dest, "file_name", None):
        result_file = client.files.download(file=job.dest.file_name)
        responses_by_rid, errors_by_rid = _parse_gemini_batch_file_results(result_file)
    else:
        for idx, inline_response in enumerate(list(getattr(job.dest, "inlined_responses", []) or [])):
            rid = docs[idx]["rid"] if idx < len(docs) else ""
            if not rid:
                continue
            response = getattr(inline_response, "response", None)
            error = getattr(inline_response, "error", None)
            if response:
                responses_by_rid[rid] = response
            elif error:
                errors_by_rid[rid] = str(error)

    return {
        "job_name": job.name,
        "prompt_by_rid": prompt_by_rid,
        "responses_by_rid": responses_by_rid,
        "errors_by_rid": errors_by_rid,
    }


async def run_gemini_batch_model(
    spec: ModelSpec,
    documents: List[Dict[str, Any]],
    *,
    budget_cap_usd: float,
    use_cache: bool,
    poll_interval_s: int,
    timeout_s: int,
) -> ModelResult:
    result = ModelResult(name=spec.name, execution_mode="batch")
    system_msg = get_system_message()

    cached_docs: List[DocResult] = []
    fresh_docs: List[Dict[str, Any]] = []
    for doc in documents:
        cached = cache_get(spec.name, doc["rid"]) if use_cache else None
        if cached:
            cached_docs.append(hydrate_doc_result_from_cache(spec, doc, cached))
        else:
            fresh_docs.append(doc)

    for dr in cached_docs:
        result.docs.append(dr)
        result.total_cost += dr.cost_usd

    log.info(f"\n{'=' * 60}")
    log.info(f"Model: {spec.name} ({spec.provider}, batch)")
    log.info(f"Budget cap: ${budget_cap_usd:.2f}")
    log.info(f"Cached docs reused: {len(cached_docs)}")
    log.info(f"Fresh docs to batch: {len(fresh_docs)}")
    log.info(f"{'=' * 60}")

    if result.total_cost >= budget_cap_usd:
        log.warning(f"Cached results already exceed budget cap for {spec.name}.")
        rid_order = {doc["rid"]: i for i, doc in enumerate(documents)}
        result.docs.sort(key=lambda d: rid_order.get(d.rid, 999))
        return result

    if not fresh_docs:
        rid_order = {doc["rid"]: i for i, doc in enumerate(documents)}
        result.docs.sort(key=lambda d: rid_order.get(d.rid, 999))
        return result

    started = time.time()
    batch_payload = await asyncio.to_thread(
        _run_gemini_batch_sync,
        spec,
        fresh_docs,
        system_msg,
        api_key=os.getenv(spec.api_key_env, ""),
        poll_interval_s=poll_interval_s,
        timeout_s=timeout_s,
    )
    elapsed = time.time() - started
    result.batch_job_name = batch_payload["job_name"]
    per_doc_latency = elapsed / max(len(fresh_docs), 1)

    responses_by_rid = batch_payload["responses_by_rid"]
    errors_by_rid = batch_payload["errors_by_rid"]
    prompt_by_rid = batch_payload["prompt_by_rid"]

    for doc in fresh_docs:
        response = responses_by_rid.get(doc["rid"])
        error = errors_by_rid.get(doc["rid"], "")
        if response is None:
            dr = DocResult(
                rid=doc["rid"],
                source=doc["source"],
                length=doc["length"],
                latency_s=per_doc_latency,
                batch_mode="batch",
                error=error or "Missing Gemini batch response",
            )
        else:
            extraction = _extract_json(_extract_gemini_response_text(response))
            usage = _extract_gemini_usage(response)
            dr = hydrate_doc_result(
                spec,
                doc,
                extraction=extraction,
                usage=usage,
                prompt=prompt_by_rid.get(doc["rid"], build_prompt_for_doc(doc)),
                latency_s=per_doc_latency,
                batch=True,
                batch_mode="batch",
            )
            if use_cache:
                cache_doc_result(spec.name, dr, extraction, usage)

        result.docs.append(dr)
        result.total_cost += dr.cost_usd
        if dr.error:
            result.errors += 1

    rid_order = {doc["rid"]: i for i, doc in enumerate(documents)}
    result.docs.sort(key=lambda d: rid_order.get(d.rid, 999))
    return result


# ---------------------------------------------------------------------------
# Document sampling
# ---------------------------------------------------------------------------

def sample_documents(sample_size: int = 80) -> List[Dict[str, Any]]:
    """Sample documents from the database, stratified across top sources."""
    pg_url = os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")
    log.info(f"Sampling {sample_size} documents from {pg_url.split('@')[-1]}...")

    conn = psycopg2.connect(pg_url)
    cur = conn.cursor()

    source_query = """
    SELECT source, doc_count
    FROM (
        SELECT
            COALESCE(m.metadata->>'source', 'unknown') AS source,
            COUNT(*) AS doc_count
        FROM koi_memories m
        WHERE m.content->>'text' IS NOT NULL
          AND char_length(m.content->>'text') > 200
          AND char_length(m.content->>'text') < 8000
        GROUP BY 1
    ) s
    ORDER BY doc_count DESC, source
    LIMIT 5
    """
    cur.execute(source_query)
    sources = [row[0] for row in cur.fetchall()]
    if not sources:
        cur.close()
        conn.close()
        return []

    selected: List[Dict[str, Any]] = []
    seen_rids = set()

    base_per_source = sample_size // len(sources)
    remainder = sample_size % len(sources)

    for idx, source in enumerate(sources):
        source_limit = base_per_source + (1 if idx < remainder else 0)
        if source_limit <= 0:
            continue
        cur.execute(
            """
            SELECT
                m.rid,
                m.content->>'text' AS text,
                COALESCE(m.metadata->>'source', 'unknown') AS source,
                char_length(m.content->>'text') AS text_length
            FROM koi_memories m
            WHERE m.content->>'text' IS NOT NULL
              AND char_length(m.content->>'text') > 200
              AND char_length(m.content->>'text') < 8000
              AND COALESCE(m.metadata->>'source', 'unknown') = %s
            ORDER BY RANDOM()
            LIMIT %s
            """,
            (source, source_limit),
        )
        for row in cur.fetchall():
            if row[0] in seen_rids:
                continue
            seen_rids.add(row[0])
            selected.append({"rid": row[0], "text": row[1], "source": row[2], "length": row[3]})

    if len(selected) < sample_size:
        remaining = sample_size - len(selected)
        cur.execute(
            """
            SELECT
                m.rid,
                m.content->>'text' AS text,
                COALESCE(m.metadata->>'source', 'unknown') AS source,
                char_length(m.content->>'text') AS text_length
            FROM koi_memories m
            WHERE m.content->>'text' IS NOT NULL
              AND char_length(m.content->>'text') > 200
              AND char_length(m.content->>'text') < 8000
              AND NOT (m.rid = ANY(%s))
            ORDER BY RANDOM()
            LIMIT %s
            """,
            (list(seen_rids) or [""], remaining),
        )
        for row in cur.fetchall():
            if row[0] in seen_rids:
                continue
            selected.append({"rid": row[0], "text": row[1], "source": row[2], "length": row[3]})
            seen_rids.add(row[0])

    cur.close()
    conn.close()

    log.info(f"Sampled {len(selected)} documents across sources: {', '.join(sources)}")
    return selected[:sample_size]


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(results: Dict[str, ModelResult], sample_size: int, output_path: Path) -> None:
    """Generate markdown comparison report."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    models = list(results.values())

    lines = [
        "# Model Bakeoff Report",
        "",
        f"**Date**: {ts}",
        f"**Sample Size**: {sample_size} documents",
        f"**Models**: {', '.join(r.name for r in models)}",
        "",
        "---",
        "",
        "## Summary",
        "",
        f"| Metric | " + " | ".join(r.name for r in models) + " |",
        f"|--------| " + " | ".join("---" for _ in models) + " |",
    ]

    def row(label: str, values: List[str]) -> str:
        return f"| {label} | " + " | ".join(values) + " |"

    lines.append(row("Mode", [r.execution_mode for r in models]))
    lines.append(row("Docs processed", [str(len(r.docs)) for r in models]))
    lines.append(row("Total entities", [str(r.total_entities) for r in models]))
    lines.append(row("Passed pipeline", [str(r.total_passed) for r in models]))
    lines.append(row("Blocked", [str(r.total_blocked) for r in models]))
    lines.append(row("Relationships", [str(r.total_relationships) for r in models]))
    lines.append(row("Mean pass rate", [f"{r.mean_pass_rate:.1f}%" for r in models]))
    lines.append(row("Errors", [str(r.errors) for r in models]))
    lines.append(row("Cached input tokens", [str(r.total_cached_input_tokens) for r in models]))
    lines.append(row("**Total cost**", [f"**${r.total_cost:.4f}**" for r in models]))
    lines.append(row("Mean latency", [f"{r.mean_latency:.2f}s" for r in models]))
    lines.append(row("p95 latency", [f"{r.p95_latency:.2f}s" for r in models]))

    lines += [
        "",
        "---",
        "",
        "## Cost Extrapolation",
        "",
        "| Model | Sample cost | Per-doc avg | Est. 1,065 docs |",
        "|-------|-------------|-------------|------------------|",
    ]
    for model_result in models:
        count = max(1, len(model_result.docs))
        per_doc = model_result.total_cost / count
        full = per_doc * 1065
        lines.append(
            f"| {model_result.name} | ${model_result.total_cost:.4f} | "
            f"${per_doc:.5f} | ${full:.2f} |"
        )

    lines += ["", "---", "", "## Quality Comparison", ""]
    if models:
        best_pass = max(models, key=lambda r: r.mean_pass_rate)
        cheapest = min(models, key=lambda r: r.total_cost)
        fastest = min(models, key=lambda r: r.mean_latency if r.mean_latency > 0 else 999999)
        most_entities = max(models, key=lambda r: r.total_passed)

        lines += [
            f"- **Highest pass rate**: {best_pass.name} ({best_pass.mean_pass_rate:.1f}%)",
            f"- **Most entities passed**: {most_entities.name} ({most_entities.total_passed})",
            f"- **Cheapest**: {cheapest.name} (${cheapest.total_cost:.4f})",
            f"- **Fastest**: {fastest.name} ({fastest.mean_latency:.2f}s avg)",
        ]

    lines += ["", "---", "", "## Recommendation", ""]
    if len(models) >= 2:
        baseline = models[0]
        for challenger in models[1:]:
            if baseline.total_cost <= 0 or challenger.total_cost <= 0:
                lines.append(f"**{challenger.name} vs {baseline.name}**: insufficient cost data.")
                continue
            quality_diff = challenger.mean_pass_rate - baseline.mean_pass_rate
            cost_ratio = challenger.total_cost / baseline.total_cost
            cost_pct = (cost_ratio - 1) * 100
            if quality_diff > 2.0 and cost_pct < 50:
                verdict = "UPGRADE"
            elif abs(quality_diff) <= 2.0 and cost_pct < -10:
                verdict = "CHEAPER, SAME QUALITY"
            elif quality_diff < -2.0:
                verdict = "WORSE QUALITY"
            else:
                verdict = "MIXED"

            lines.append(
                f"**{challenger.name} vs {baseline.name}**: "
                f"{quality_diff:+.1f}% pass rate, {cost_pct:+.0f}% cost "
                f"→ **{verdict}**"
            )
    else:
        lines.append("Only one model tested — no comparison possible.")

    lines += [
        "",
        "---",
        "",
        "## Per-Document Detail (first 15)",
        "",
        "| # | RID | Length | " + " | ".join(f"{r.name} pass%" for r in models) + " |",
        "|---|-----|--------| " + " | ".join("---" for _ in models) + " |",
    ]

    common_rids = set.intersection(*(set(d.rid for d in r.docs) for r in models)) if models else set()
    common_rids = sorted(common_rids)[:15]
    for idx, rid in enumerate(common_rids, 1):
        doc_results = []
        length = 0
        for model_result in models:
            doc_result = next((d for d in model_result.docs if d.rid == rid), None)
            if doc_result:
                length = doc_result.length
                doc_results.append(f"{doc_result.pass_rate:.0f}%")
            else:
                doc_results.append("-")
        lines.append(f"| {idx} | {rid[:12]}… | {length} | " + " | ".join(doc_results) + " |")

    lines += ["", "---", "", "## Raw Metrics (JSON)", "", "```json"]
    raw: Dict[str, Any] = {}
    for model_result in models:
        raw[model_result.name] = {
            "execution_mode": model_result.execution_mode,
            "batch_job_name": model_result.batch_job_name,
            "docs_processed": len(model_result.docs),
            "total_entities": model_result.total_entities,
            "total_passed": model_result.total_passed,
            "total_blocked": model_result.total_blocked,
            "total_relationships": model_result.total_relationships,
            "mean_pass_rate": round(model_result.mean_pass_rate, 2),
            "errors": model_result.errors,
            "total_cost_usd": round(model_result.total_cost, 6),
            "total_cached_input_tokens": model_result.total_cached_input_tokens,
            "mean_latency_s": round(model_result.mean_latency, 3),
            "p95_latency_s": round(model_result.p95_latency, 3),
        }
    lines.append(json.dumps(raw, indent=2))
    lines += ["```", ""]

    output_path.write_text("\n".join(lines))
    log.info(f"\nReport written to: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-provider entity extraction bakeoff")
    parser.add_argument(
        "--sample-size",
        type=int,
        default=None,
        help="Number of docs to sample (default: BAKEOFF_SAMPLE_SIZE or 80)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Max concurrent sync requests per model (default: 5)",
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=None,
        help="Total budget in USD (default: BAKEOFF_BUDGET_USD or 25)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore cached results, re-call all APIs",
    )
    parser.add_argument(
        "--models",
        type=str,
        default=None,
        help="Comma-separated model names to test (default: all configured)",
    )
    parser.add_argument(
        "--no-openai-batch",
        action="store_true",
        help="Disable OpenAI Batch API and use sync calls instead",
    )
    parser.add_argument(
        "--no-gemini-batch",
        action="store_true",
        help="Disable Gemini Batch API and use sync calls instead",
    )
    parser.add_argument(
        "--batch-poll-seconds",
        type=int,
        default=20,
        help="Polling interval for provider batch jobs (default: 20)",
    )
    parser.add_argument(
        "--batch-timeout-minutes",
        type=int,
        default=30,
        help="Timeout for a single provider batch job (default: 30)",
    )
    args = parser.parse_args()

    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    personal_env = REPO_ROOT / "config" / "personal.env"
    if personal_env.exists():
        load_dotenv(personal_env, override=False)

    sample_size = args.sample_size or int(os.getenv("BAKEOFF_SAMPLE_SIZE", "80"))
    budget = args.budget or float(os.getenv("BAKEOFF_BUDGET_USD", "25"))
    use_cache = not args.no_cache

    specs = build_model_specs()
    if args.models:
        wanted = {model.strip() for model in args.models.split(",")}
        specs = [spec for spec in specs if spec.name in wanted]

    if not specs:
        log.error("No models configured. Check API keys in .env")
        sys.exit(1)

    log.info(f"Bakeoff: {len(specs)} models, {sample_size} docs, ${budget} budget")
    for spec in specs:
        log.info(f"  - {spec.name} ({spec.provider}) via {spec.base_url}")

    documents = sample_documents(sample_size)
    if not documents:
        log.error("No documents found in database")
        sys.exit(1)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / "sampled_docs.json").write_text(
        json.dumps(
            [{"rid": doc["rid"], "source": doc["source"], "length": doc["length"]} for doc in documents],
            indent=2,
        )
    )

    budget_per_model = budget / max(len(specs), 1)
    timeout_s = args.batch_timeout_minutes * 60

    results: Dict[str, ModelResult] = {}
    total_spent = 0.0
    for spec in specs:
        if total_spent >= budget:
            log.warning(f"Total budget exhausted (${total_spent:.2f}). Skipping {spec.name}.")
            continue

        try:
            if spec.provider == "openai" and not args.no_openai_batch:
                model_result = await run_openai_batch_model(
                    spec,
                    documents,
                    budget_cap_usd=budget_per_model,
                    use_cache=use_cache,
                    poll_interval_s=args.batch_poll_seconds,
                    timeout_s=timeout_s,
                )
            elif spec.provider == "gemini" and not args.no_gemini_batch:
                model_result = await run_gemini_batch_model(
                    spec,
                    documents,
                    budget_cap_usd=budget_per_model,
                    use_cache=use_cache,
                    poll_interval_s=args.batch_poll_seconds,
                    timeout_s=timeout_s,
                )
            else:
                model_result = await run_model_sync(
                    spec,
                    documents,
                    concurrency=args.concurrency,
                    budget_cap_usd=budget_per_model,
                    use_cache=use_cache,
                )
        except Exception as exc:
            log.warning(
                f"{spec.name}: preferred execution path failed ({type(exc).__name__}: {exc}). "
                f"Falling back to sync requests."
            )
            model_result = await run_model_sync(
                spec,
                documents,
                concurrency=args.concurrency,
                budget_cap_usd=budget_per_model,
                use_cache=use_cache,
            )

        results[spec.name] = model_result
        total_spent += model_result.total_cost
        log.info(
            f"\n  {spec.name} summary: {model_result.total_passed}/{model_result.total_entities} entities, "
            f"{model_result.mean_pass_rate:.1f}% pass rate, ${model_result.total_cost:.4f}, "
            f"{model_result.errors} errors, mode={model_result.execution_mode}"
        )

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = REPO_ROOT / "scripts" / "reextraction" / f"BAKEOFF_REPORT_{ts}.md"
    generate_report(results, sample_size, report_path)

    log.info(f"\nTotal spend: ${total_spent:.4f}")
    log.info("Done!")


if __name__ == "__main__":
    asyncio.run(main())
