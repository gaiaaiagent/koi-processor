#!/usr/bin/env python3
"""
Document deep-extraction, chunk-wise + cross-window merge.
  standard tier (Phase 1): entities + facts (v1 prompt/schema).
  thorough tier (Phase 3): + discourse moves (v2 prompt/schema) written to the
    generalized session_discourse_moves table (source_type='document', migration 103).

Part of the unified thorough content-ingestion plan
(~/.claude/plans/plan-a-unified-thorough-wiggly-plum.md).

Reads a document's RAG chunks (koi_memory_chunks, written by ingest_document.py),
packs them into windows (well under the claude -p char cap), runs the document
extractor prompt per window (transport + model are BOTH env-tunable — see
DOC_EXTRACTOR_TRANSPORT / DOC_EXTRACTOR_MODEL below; the old "Sonnet, forced for
extraction quality" wording was an unexamined default, and Haiku matched
fact-density at ~3.6x the speed in measurement — issue #37), then MERGES entities
(type-priority coercion) + facts (dedup) across
windows deterministically. Facts are written through POST /knowledge/episodes —
NOT the session raw-INSERT — so they land in fact_embedding_3072 and get 3-tier
resolution + cosine>0.95 dedup + type-mismatch detection. Type-mismatches are
recorded in document_extraction_item_errors and filed as /tasks/ingest cleanups
(report-and-task, never auto-merge across the resolution guard).

Idempotent + resumable via document_ingestion_log + document_window_extractions
(migration 102). Re-running skips already-extracted windows (cached raw_json).

Usage:
    source config/personal.env
    python scripts/extract_deep_documents.py --slug biohubs-whitepaper --tier standard
    python scripts/extract_deep_documents.py --document-rid document:<sha> --group-id <field>
"""

import argparse
import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import httpx
from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).parent.parent))
from api.provider_http import provider_async_client  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent

# ── Config ───────────────────────────────────────────────────────────────────────
POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")
# Pin deterministically to the CO-LOCATED KOI backend (:8351 on whatever host the
# pipeline runs — laptop or NUC). Use a dedicated var so it does NOT inherit
# personal.env's general KOI_BASE_URL, which points at a WireGuard peer
# (10.100.0.2) unreachable from the host running the ingest. (Plan Phase-2 note.)
KOI_BASE_URL = os.getenv("DOC_INGEST_KOI_URL", "http://localhost:8351")
CLAUDE_P_MODEL = os.getenv("CLAUDE_P_MODEL", "claude-sonnet-4-6")
# /knowledge/episodes authenticates with the CLAIMS service token (server-side
# require_service_auth checks KOI_CLAIMS_SERVICE_TOKEN). Prefer it; fall back to the
# legacy INGEST var only for envs that haven't split the two tokens. (:8351 episodes-
# gate readiness — sending the token is a harmless no-op until the gate lands.)
KOI_EPISODES_SERVICE_TOKEN = os.getenv("KOI_CLAIMS_SERVICE_TOKEN") or os.getenv("KOI_INGEST_SERVICE_TOKEN")
if not os.getenv("KOI_CLAIMS_SERVICE_TOKEN") and os.getenv("KOI_INGEST_SERVICE_TOKEN"):
    logger.warning(
        "KOI_CLAIMS_SERVICE_TOKEN unset — /knowledge/episodes will be sent with the legacy "
        "INGEST token, which the :8351 auth gate REJECTS (401). Set KOI_CLAIMS_SERVICE_TOKEN."
    )

# Tier selects the extractor contract: standard = entities+facts (v1); thorough =
# entities+facts+discourse (v2). Env overrides win if set.
PROMPT_V1 = Path(os.getenv(
    "DOC_EXTRACTOR_PROMPT_FILE", str(REPO_ROOT / "scripts/prompts/deep_extraction_doc_v1.md")))
SCHEMA_V1 = Path(os.getenv(
    "DOC_EXTRACTOR_SCHEMA_FILE", str(REPO_ROOT / "scripts/schemas/deep_extraction_doc_v1.schema.json")))
PROMPT_V2 = Path(os.getenv(
    "DOC_EXTRACTOR_PROMPT_V2_FILE", str(REPO_ROOT / "scripts/prompts/deep_extraction_doc_v2.md")))
SCHEMA_V2 = Path(os.getenv(
    "DOC_EXTRACTOR_SCHEMA_V2_FILE", str(REPO_ROOT / "scripts/schemas/deep_extraction_doc_v2.schema.json")))

# Deterministic namespace for document discourse-move ids (uuid5 → idempotent upsert).
DISCOURSE_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "discourse.document.koi")


def prompt_schema_for_tier(tier: str) -> Tuple[Path, Path]:
    """thorough → v2 (adds discourse); standard → v1 (entities+facts)."""
    return (PROMPT_V2, SCHEMA_V2) if tier == "thorough" else (PROMPT_V1, SCHEMA_V1)

WINDOW_CHARS = int(os.getenv("DOC_WINDOW_CHARS", "45000"))   # proper window size — the Anthropic API is fast,
                                                             # so no need to shrink windows to beat a CLI timeout
WINDOW_OVERLAP_CHUNKS = int(os.getenv("DOC_WINDOW_OVERLAP_CHUNKS", "2"))
MAX_WINDOWS = int(os.getenv("DOC_MAX_WINDOWS", "12"))        # per-invocation budget cap (plan §Q3)
# Was the cap CHOSEN by the caller, or is it just the default? A default 12 x 45k = 540k
# chars, which SILENTLY truncated every book-length source (2026-07-31: the 227-post blog
# lost 8 of 86 windows and still passed every gate floor). An explicit cap is a real cost
# guard and is honoured; an unset one now auto-raises instead of quietly dropping the tail.
MAX_WINDOWS_EXPLICIT = "DOC_MAX_WINDOWS" in os.environ
# Semantic dedup threshold: the exact-triple sweep misses PARAPHRASES (a re-extraction's
# fresh phrasings resolve to distinct triples). After it, retract the later of any
# same-subject + same-predicate fact pair whose fact_text embeddings exceed this cosine
# — conservative (predicate match + high bar) so re-extractions self-converge without
# false-retracting genuinely-distinct facts.
SEMANTIC_DEDUP_THRESHOLD = float(os.getenv("DOC_SEMANTIC_DEDUP_THRESHOLD", "0.95"))

# Per-window extraction transport — selected by DOC_EXTRACTOR_TRANSPORT:
#   'claude_p' (DEFAULT): run each window through the `claude -p` CLI on the Claude Code
#       SUBSCRIPTION — zero pay-per-token cost. Slower (harness startup, no server-side
#       batching) but $0 marginal. We strip ANTHROPIC_API_KEY from the child env so the
#       CLI uses OAuth, and pass --strict-mcp-config + --setting-sources '' to shed the
#       MCP/CLAUDE.md context (~7x fewer overhead tokens per call in measurement).
#   'api': the direct Anthropic Messages API — ~4-5x faster, but BILLS per-token against
#       ANTHROPIC_API_KEY.
#   'openai': ANY OpenAI-compatible /v1/chat/completions endpoint — bring your own model
#       (public OpenAI, a self-hosted vLLM/Ollama, a provider-hosted open model, etc.).
#       Configure it entirely via DOC_EXTRACTOR_OPENAI_* env vars (below); no endpoint or
#       key is baked in, so forks point it at their own infra.
# The default is 'claude_p' and should stay that way — 'openai'/'api' are opt-in per run.
DOC_EXTRACTOR_TRANSPORT = os.getenv("DOC_EXTRACTOR_TRANSPORT", "claude_p").strip().lower()
# Resolve the claude binary — the launchd job runs with a minimal PATH that may lack
# ~/.local/bin, so shutil.which() can miss it; fall back to the known install location.
CLAUDE_BIN = (os.getenv("CLAUDE_BIN") or shutil.which("claude")
              or os.path.expanduser("~/.local/bin/claude"))
ROUTE_USED = {"api": "anthropic_api", "openai": "openai_compat"}.get(
    DOC_EXTRACTOR_TRANSPORT, "claude_p_cli")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("DOC_EXTRACTOR_MODEL", CLAUDE_P_MODEL)
ANTHROPIC_MAX_TOKENS = int(os.getenv("DOC_EXTRACTOR_MAX_TOKENS", "24000"))
ANTHROPIC_TIMEOUT = int(os.getenv("DOC_EXTRACTOR_TIMEOUT", "300"))
ANTHROPIC_RETRYABLE = {429, 500, 502, 503, 529}

# 'openai' transport config — bring-your-own OpenAI-compatible model. Defaults are
# GENERIC (public OpenAI) so a fork works with just an OPENAI_API_KEY; point BASE_URL
# at any compatible server (vLLM/Ollama/provider) to use a different model. Nothing
# operator-specific is committed here — set these in your (gitignored) config/env.
#   DOC_EXTRACTOR_OPENAI_NO_THINK: for reasoning models (Qwen3, etc.) served by vLLM,
#     set =1 to send chat_template_kwargs.enable_thinking=false — otherwise the model
#     spends its budget reasoning and returns content=null. Default 0 because the param
#     is a vLLM extension that the public OpenAI API rejects. Enable it for reasoning
#     models on a compatible server.
OPENAI_BASE_URL = os.getenv("DOC_EXTRACTOR_OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("DOC_EXTRACTOR_OPENAI_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.getenv("DOC_EXTRACTOR_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY", "")
OPENAI_MAX_TOKENS = int(os.getenv("DOC_EXTRACTOR_OPENAI_MAX_TOKENS", "12000"))
OPENAI_NO_THINK = os.getenv("DOC_EXTRACTOR_OPENAI_NO_THINK", "0").strip().lower() in ("1", "true", "yes")

# Type-priority coercion (plan §23): highest wins on cross-window conflict.
TYPE_PRIORITY = {"Person": 7, "Organization": 6, "Project": 5, "Location": 4,
                 "Protocol": 3, "CaseStudy": 2, "Concept": 1}


class ExtractionError(RuntimeError):
    def __init__(self, reason: str, detail: str, terminal: bool = False):
        super().__init__(f"{reason}: {detail}")
        self.reason, self.detail, self.terminal = reason, detail, terminal


def _norm(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def compute_next_retry(attempts: int) -> Optional[datetime]:
    """Exponential backoff with a terminal cap at 3 attempts (mirrors the session path)."""
    if attempts >= 3:
        return None
    return datetime.now(timezone.utc) + timedelta(minutes=15 * (2 ** attempts))


# ── claude -p + validation (cloned from extract_deep_sessions.py) ─────────────────

async def _call_anthropic(prompt: str, http: httpx.AsyncClient, *, model: str,
                          max_tokens: int = ANTHROPIC_MAX_TOKENS, timeout: int = ANTHROPIC_TIMEOUT,
                          max_retries: int = 3) -> str:
    """Direct Anthropic Messages API call — the document-extraction transport.

    Same model/prompt/schema as the session path's claude -p; just a faster, headless
    transport. Retries transient errors (429/5xx/connection) with backoff so a blip
    never silently drops a window — on exhaustion (or max_tokens truncation) it RAISES,
    marking the window failed/resumable rather than producing a partial merge.
    """
    if not ANTHROPIC_API_KEY:
        raise ExtractionError("no_api_key", "ANTHROPIC_API_KEY not set (document extraction transport)",
                              terminal=True)
    headers = {"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    body = {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    last = None
    for attempt in range(max_retries):
        try:
            r = await http.post("https://api.anthropic.com/v1/messages",
                                headers=headers, json=body, timeout=timeout)
            if r.status_code in ANTHROPIC_RETRYABLE:
                last = f"http {r.status_code}: {r.text[:400]}"
                await asyncio.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            data = r.json()
            if data.get("stop_reason") == "max_tokens":
                raise ExtractionError("extract_truncated",
                                      f"hit max_tokens={max_tokens}; raise DOC_EXTRACTOR_MAX_TOKENS "
                                      f"or lower DOC_WINDOW_CHARS")
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            if not text.strip():
                raise ExtractionError("empty_completion", f"no text content: {str(data)[:400]}")
            return text
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.WriteError) as e:
            last = f"{type(e).__name__}: {e}"
            await asyncio.sleep(2 ** attempt)
    raise ExtractionError("extract_http_error", f"anthropic api failed after {max_retries} attempts: {last}")


async def _call_claude_p(prompt: str, *, model: str, timeout: int = ANTHROPIC_TIMEOUT,
                         max_retries: int = 3) -> str:
    """Subscription transport: run the extractor prompt through the `claude -p` CLI.

    Bills the Claude Code SUBSCRIPTION, not ANTHROPIC_API_KEY. Same model/prompt/schema
    as the API path — just a headless CLI transport with $0 marginal per-token cost.

    Two things make this correct-and-cheap: (1) we strip ANTHROPIC_API_KEY /
    ANTHROPIC_AUTH_TOKEN from the child env so `claude` authenticates via the OAuth
    subscription instead of falling back to pay-per-token API billing (the shell wrapper
    exports the key via `source personal.env`, so a naive child would inherit it); and
    (2) --strict-mcp-config (no MCP servers) + --setting-sources '' + a neutral cwd shed
    the MCP tool schemas and CLAUDE.md, cutting per-call overhead tokens ~7x. Retries
    transient CLI/API-status errors with backoff; raises (resumable) on exhaustion.
    """
    if not CLAUDE_BIN or not os.path.exists(CLAUDE_BIN):
        raise ExtractionError("no_claude_bin",
                              f"claude CLI not found (CLAUDE_BIN={CLAUDE_BIN!r}); set CLAUDE_BIN "
                              f"or add ~/.local/bin to PATH", terminal=True)
    # Force subscription auth — a set ANTHROPIC_API_KEY would make `claude` bill the API.
    child_env = {k: v for k, v in os.environ.items()
                 if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    cmd = [CLAUDE_BIN, "-p", "--output-format", "json", "--model", model,
           "--strict-mcp-config", "--setting-sources", ""]
    last = None
    for attempt in range(max_retries):
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=child_env, cwd="/tmp")
        except OSError as e:
            raise ExtractionError("no_claude_bin", f"failed to spawn {CLAUDE_BIN}: {e}", terminal=True)
        try:
            out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout=timeout)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            last = f"timeout after {timeout}s"
            await asyncio.sleep(2 ** attempt)
            continue
        if proc.returncode != 0:
            last = f"exit {proc.returncode}: {err.decode(errors='replace')[:400]}"
            await asyncio.sleep(2 ** attempt)
            continue
        try:
            env = json.loads(out.decode())
        except json.JSONDecodeError:
            last = f"non-JSON stdout: {out.decode(errors='replace')[:400]}"
            await asyncio.sleep(2 ** attempt)
            continue
        if env.get("is_error") or env.get("subtype") != "success":
            last = f"claude -p reported error: {str(env)[:400]}"
            await asyncio.sleep(2 ** attempt)
            continue
        text = env.get("result") or ""
        if not text.strip():
            raise ExtractionError("empty_completion", f"claude -p empty result: {str(env)[:400]}")
        return text
    raise ExtractionError("claude_p_error", f"claude -p failed after {max_retries} attempts: {last}")


async def _call_openai(prompt: str, http: httpx.AsyncClient, *,
                       timeout: int = ANTHROPIC_TIMEOUT, max_retries: int = 3,
                       temperature: float = 0.0) -> str:
    """OpenAI-compatible /v1/chat/completions transport (bring-your-own model).

    Same prompt/schema as the other transports. For reasoning models we disable the
    think phase (OPENAI_NO_THINK) — otherwise the model spends its budget in a
    `reasoning`/`<think>` channel and returns `content: null`. parse_and_validate
    downstream strips any residual <think> block and fences. Retries transient
    errors with backoff; raises (resumable) on exhaustion. `temperature` is bumped
    by the repair loop so a re-ask is not deterministically identical.
    """
    headers = {"content-type": "application/json"}
    if OPENAI_API_KEY:
        headers["authorization"] = f"Bearer {OPENAI_API_KEY}"
    body: Dict[str, Any] = {
        "model": OPENAI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": OPENAI_MAX_TOKENS,
        "temperature": temperature,
        "stream": False,
    }
    if OPENAI_NO_THINK:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    url = OPENAI_BASE_URL.rstrip("/") + "/chat/completions"
    last = None
    for attempt in range(max_retries):
        try:
            r = await http.post(url, headers=headers, json=body, timeout=timeout)
            if r.status_code in ANTHROPIC_RETRYABLE:
                last = f"http {r.status_code}: {r.text[:400]}"
                await asyncio.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            data = r.json()
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            text = msg.get("content") or msg.get("reasoning") or ""
            if choice.get("finish_reason") == "length" and not text.strip():
                raise ExtractionError("extract_truncated",
                                      f"hit max_tokens={OPENAI_MAX_TOKENS}; raise "
                                      f"DOC_EXTRACTOR_OPENAI_MAX_TOKENS or lower DOC_WINDOW_CHARS")
            if not text.strip():
                raise ExtractionError("empty_completion", f"no content: {str(data)[:400]}")
            return text
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.WriteError) as e:
            last = f"{type(e).__name__}: {e}"
            await asyncio.sleep(2 ** attempt)
    raise ExtractionError("extract_http_error", f"openai api failed after {max_retries} attempts: {last}")


async def _extract_window(prompt: str, http: httpx.AsyncClient, *, model: str,
                          temperature: float = 0.0) -> str:
    """Dispatch a window extraction to the configured transport (openai / api / subscription CLI)."""
    if DOC_EXTRACTOR_TRANSPORT == "openai":
        return await _call_openai(prompt, http, temperature=temperature)
    if DOC_EXTRACTOR_TRANSPORT == "api":
        return await _call_anthropic(prompt, http, model=model)
    return await _call_claude_p(prompt, model=model)


# How many repair passes to attempt when the model returns malformed / schema-invalid
# JSON. Smaller / open models occasionally slip on strict JSON — a
# missing comma, or a bare string where the schema wants an object. Re-asking with the
# exact error + a bumped temperature recovers most of these (a plain retry at temp 0
# would reproduce the same broken output deterministically). 0 disables the loop.
DOC_EXTRACTOR_REPAIR_PASSES = int(os.getenv("DOC_EXTRACTOR_REPAIR_PASSES", "2"))


async def extract_window_validated(prompt: str, http: httpx.AsyncClient, schema: dict,
                                   *, model: str) -> dict:
    """Extract one window and parse+validate it, with a repair loop on EVERY transport.

    On a parse/schema failure, re-ask the model with the broken output and the exact
    validator error, nudging temperature up each pass so the retry isn't identical.
    Set DOC_EXTRACTOR_REPAIR_PASSES=0 for single-shot behaviour.

    If repair is still exhausted, the caller dead-letters this WINDOW and continues over
    the rest of the document (#40) — a bad window no longer costs the whole book."""
    raw = await _extract_window(prompt, http, model=model)
    try:
        return parse_and_validate(raw, schema)
    except ExtractionError as first_err:
        repairable = first_err.reason in ("extract_parse_error", "empty_completion")
        # #40: the repair loop used to be gated to the 'openai' transport, on the reasoning
        # that api/claude_p "rarely emit invalid JSON" and re-prompting claude -p is slow.
        # Measured otherwise: on the Kurtz corpus the DEFAULT claude_p transport hit four
        # separate schema failures, each of which discarded a whole document (30-50 min of
        # extraction) because it had no repair path at all. One extra ~2 min repair call is
        # obviously cheaper than that. Repair now runs on every transport; set
        # DOC_EXTRACTOR_REPAIR_PASSES=0 to restore single-shot behaviour.
        if not repairable or DOC_EXTRACTOR_REPAIR_PASSES <= 0:
            raise
        last_err = first_err
        for i in range(DOC_EXTRACTOR_REPAIR_PASSES):
            repair_prompt = (
                f"{prompt}\n\n---\nYour previous response was NOT accepted. Error:\n"
                f"{last_err.detail}\n\nPrevious response (fix it):\n{raw[:6000]}\n\n"
                "Return the CORRECTED result as ONE valid JSON object matching the schema "
                "exactly — no prose, no markdown fences, every required field present, and "
                "every entities[] item an object with name+type (never a bare string). "
                "Start with { and end with }."
            )
            temp = 0.2 + 0.3 * i
            logger.info("  repair pass %d/%d (temp=%.1f): %s",
                        i + 1, DOC_EXTRACTOR_REPAIR_PASSES, temp, last_err.detail[:80])
            try:
                raw = await _call_openai(repair_prompt, http, temperature=temp)
                return parse_and_validate(raw, schema)
            except ExtractionError as e:
                last_err = e
                if e.reason not in ("extract_parse_error", "empty_completion"):
                    raise
        raise last_err


def parse_and_validate(raw: str, schema: dict) -> dict:
    first, last = raw.find("{"), raw.rfind("}")
    if first < 0 or last < 0 or last < first:
        raise ExtractionError("extract_parse_error", "no JSON object found in output")
    try:
        data = json.loads(raw[first:last + 1])
    except json.JSONDecodeError as e:
        raise ExtractionError("extract_parse_error", f"json decode: {e}") from e
    # Robustness: the extractor occasionally emits a fact object missing the required
    # fact_text. Rather than fail (and discard) the whole window, synthesize fact_text
    # from the triple (subject / predicate / object|object_literal) so no fact is lost
    # and schema validation passes. Only fires when fact_text is absent/blank — the
    # normal path (model-written fact_text) is untouched.
    if isinstance(data.get("facts"), list):
        for f in data["facts"]:
            if isinstance(f, dict) and not str(f.get("fact_text") or "").strip():
                obj = f.get("object") or f.get("object_literal") or ""
                synth = " ".join(str(x).strip() for x in (f.get("subject"), f.get("predicate"), obj)
                                 if x and str(x).strip())
                if synth:
                    f["fact_text"] = synth
    # Same spirit as the fact_text repair above. `chunk_range` must be EXACTLY two ints —
    # the cross-window merge indexes cr[0] and cr[1] — but when a fact's evidence sits in a
    # single chunk the extractor reasonably answers `[990]`, and that one slip discarded a
    # whole document (2026-07-31: the 227-post blog died at window 78 of 86 on
    # `['facts', 2, 'chunk_range']: [990] is too short`). Normalise BEFORE validating so the
    # len==2 invariant the merge relies on stays enforced, rather than relaxing minItems
    # (which would turn a clean validation error into an IndexError downstream).
    for _key in ("facts", "discourse"):
        if isinstance(data.get(_key), list):
            for _item in data[_key]:
                if not isinstance(_item, dict):
                    continue
                _cr = _item.get("chunk_range")
                if isinstance(_cr, list) and all(isinstance(n, int) for n in _cr):
                    if len(_cr) == 1:
                        _item["chunk_range"] = [_cr[0], _cr[0]]
                    elif len(_cr) > 2:
                        _item["chunk_range"] = [min(_cr), max(_cr)]
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path))
    if errors:
        msgs = [f"{list(e.path)}: {e.message}" for e in errors[:5]]
        raise ExtractionError("extract_parse_error", f"schema: {' | '.join(msgs)}")
    return data


# ── Windowing over the persisted RAG chunks ───────────────────────────────────────

class Window:
    __slots__ = ("index", "char_start", "char_end", "chunk_index_base", "chunk_indices", "text")

    def __init__(self, index, char_start, char_end, chunk_index_base, chunk_indices, text):
        self.index = index
        self.char_start = char_start
        self.char_end = char_end
        self.chunk_index_base = chunk_index_base
        self.chunk_indices = chunk_indices
        self.text = text


async def fetch_rag_chunks(conn: asyncpg.Connection, document_rid: str) -> List[Tuple[int, str]]:
    rows = await conn.fetch(
        "SELECT chunk_index, content->>'text' AS text FROM koi_memory_chunks "
        "WHERE document_rid = $1 ORDER BY chunk_index ASC", document_rid)
    return [(r["chunk_index"], r["text"] or "") for r in rows]


def build_windows(chunks: List[Tuple[int, str]], target_chars: int, overlap_chunks: int) -> List[Window]:
    """Pack consecutive global RAG chunks into windows; label each chunk with its
    GLOBAL index [N] (no offset — the model emits global coordinates directly)."""
    if not chunks:
        return []
    # Running char offsets per chunk for char_start/char_end bookkeeping.
    offsets, run = [], 0
    for _, txt in chunks:
        offsets.append(run)
        run += len(txt)

    windows: List[Window] = []
    i, n, w = 0, len(chunks), 0
    while i < n:
        j, size = i, 0
        while j < n and (size == 0 or size + len(chunks[j][1]) <= target_chars):
            size += len(chunks[j][1])
            j += 1
        sel = chunks[i:j]
        text = "\n\n".join(f"[{ci}] {txt}" for ci, txt in sel)
        windows.append(Window(
            index=w, char_start=offsets[i], char_end=offsets[j - 1] + len(chunks[j - 1][1]),
            chunk_index_base=sel[0][0], chunk_indices=[ci for ci, _ in sel], text=text))
        w += 1
        if j >= n:
            break
        i = max(i + 1, j - overlap_chunks)   # advance with overlap
    return windows


def build_prompt(template: str, window: Window, window_count: int) -> str:
    placeholder = "<!-- The pipeline appends the concatenated window chunks here at call time -->"
    if placeholder not in template:
        raise RuntimeError("doc prompt template missing window placeholder")
    return (template
            .replace("{WINDOW_INDEX}", str(window.index + 1))
            .replace("{WINDOW_COUNT}", str(window_count))
            .replace(placeholder, window.text))


# ── Cross-window merge (deterministic) ────────────────────────────────────────────

def merge_extractions(per_window: List[dict], windows: List[Window]) -> Dict[str, Any]:
    """Union entities (type-priority coercion) + dedup facts across windows."""
    ent: Dict[str, Dict[str, Any]] = {}
    for ex in per_window:
        for e in ex.get("entities", []):
            k = _norm(e["name"])
            if not k:
                continue
            cur = ent.get(k)
            etype = e["type"]
            if cur is None:
                ent[k] = {"name": e["name"], "type": etype,
                          "first_seen_chunk": e["first_seen_chunk"], "mention_count": e["mention_count"]}
            else:
                if TYPE_PRIORITY.get(etype, 0) > TYPE_PRIORITY.get(cur["type"], 0):
                    cur["type"] = etype
                cur["first_seen_chunk"] = min(cur["first_seen_chunk"], e["first_seen_chunk"])
                cur["mention_count"] += e["mention_count"]
                if len(e["name"]) > len(cur["name"]):      # prefer most-specific surface form
                    cur["name"] = e["name"]

    facts: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for ex in per_window:
        for f in ex.get("facts", []):
            obj_key = _norm(f.get("object")) if f.get("object") else f"lit:{_norm(f.get('object_literal'))}"
            key = (_norm(f["subject"]), f["predicate"], obj_key)
            cr = f.get("chunk_range") or [0, 0]
            if key not in facts:
                facts[key] = {**f, "chunk_range": list(cr)}
            else:
                prev = facts[key]
                prev["chunk_range"] = [min(prev["chunk_range"][0], cr[0]), max(prev["chunk_range"][1], cr[1])]
                if f.get("confidence") == "high":
                    prev["confidence"] = "high"

    type_map = {k: v["type"] for k, v in ent.items()}
    return {"entities": list(ent.values()), "facts": list(facts.values()), "type_map": type_map}


# ── Discourse merge + write (thorough tier only) ───────────────────────────────────

# Document ARGUMENT taxonomy (plan §Q2; enforced by migration 104's source-aware CHECK).
# Session discourse keeps its own enums (session_discourse_moves rows with
# source_type='session'); document moves use these.
VALID_MOVE_TYPES = {"thesis", "claim", "evidence", "premise",
                    "counterpoint", "open_question", "definition", "implication"}
VALID_MOVE_STATUS = {"asserted", "supported", "contested", "speculative", "open", "deferred"}


def merge_discourse(per_window: List[dict]) -> List[Dict[str, Any]]:
    """Dedup discourse moves across windows by (move_type, normalized title).
    Widen chunk_range; backfill a missing detail/status/supports from a later
    window. Deterministic order (insertion) so uuid5 ids are stable across re-runs."""
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for ex in per_window:
        for m in ex.get("discourse", []) or []:
            title = (m.get("title") or "").strip()
            if not title:
                continue
            key = (m.get("move_type"), _norm(title))
            cr = m.get("chunk_range") or [0, 0]
            if key not in out:
                out[key] = {**m, "title": title[:400], "chunk_range": list(cr)}
            else:
                prev = out[key]
                prev["chunk_range"] = [min(prev["chunk_range"][0], cr[0]),
                                       max(prev["chunk_range"][1], cr[1])]
                if not prev.get("detail") and m.get("detail"):
                    prev["detail"] = m["detail"]
                if not prev.get("status") and m.get("status"):
                    prev["status"] = m["status"]
                if not prev.get("supports") and m.get("supports"):
                    prev["supports"] = m["supports"]
    return list(out.values())


async def write_discourse_moves(conn, *, document_rid: str, episode_id,
                                moves: List[Dict[str, Any]]) -> int:
    """Write document discourse moves into the generalized session_discourse_moves
    table (source_type='document', migration 103). Deterministic uuid5 ids →
    idempotent upsert. Two passes: insert all moves, then link argument edges
    (supports → the claim/thesis's id, via resolves_move_id). Document moves carry no
    embedding in v1 (the column is 1024-dim/session-scoped); the chunk range lands in
    the turn_range_* columns per migration 103's documented dual semantics. Invalid
    moves are dead-lettered, never silently dropped."""
    if not moves:
        return 0

    def move_id(mt: str, title: str, chunk_start: int):
        # chunk_start (global RAG-chunk index) keeps two same-type/same-title moves at
        # different locations distinct (plan §163).
        return uuid.uuid5(DISCOURSE_NAMESPACE, f"{document_rid}:{mt}:{_norm(title)}:{chunk_start}")

    title_to_id: Dict[str, Any] = {}
    inserted = 0
    for m in moves:
        mt = m.get("move_type")
        title = (m.get("title") or "").strip()[:400]
        if mt not in VALID_MOVE_TYPES or not title:
            with contextlib.suppress(Exception):
                await conn.execute(
                    "INSERT INTO document_extraction_item_errors (document_rid, item_type, payload, error) "
                    "VALUES ($1,'discourse_move',$2::jsonb,$3)",
                    document_rid, json.dumps(m), f"invalid move_type/title: move_type={mt!r}")
            continue
        status = m.get("status") if m.get("status") in VALID_MOVE_STATUS else None
        cr = m.get("chunk_range") or [None, None]
        a = cr[0] if isinstance(cr[0], int) else None
        b = cr[1] if isinstance(cr[1], int) else None
        mid = move_id(mt, title, a if a is not None else 0)
        await conn.execute(
            """INSERT INTO session_discourse_moves
                 (id, episode_id, source_type, source_rid, session_id, move_type, title,
                  detail, status, turn_range_start, turn_range_end, embedding)
               VALUES ($1,$2,'document',$3,NULL,$4,$5,$6,$7,$8,$9,NULL)
               ON CONFLICT (id) DO UPDATE SET
                 detail=EXCLUDED.detail, status=EXCLUDED.status,
                 turn_range_start=EXCLUDED.turn_range_start,
                 turn_range_end=EXCLUDED.turn_range_end,
                 episode_id=EXCLUDED.episode_id""",
            mid, episode_id, document_rid, mt, title, m.get("detail"), status, a, b)
        title_to_id[_norm(title)] = mid
        inserted += 1

    # Second pass: link argument edges — a premise/evidence/counterpoint `supports`
    # the claim/thesis whose title it names → resolves_move_id self-FK.
    for m in moves:
        sup = m.get("supports")
        title = (m.get("title") or "").strip()[:400]
        mt = m.get("move_type")
        if not sup or mt not in VALID_MOVE_TYPES or not title:
            continue
        target = title_to_id.get(_norm(sup))
        if not target:
            continue
        cr = m.get("chunk_range") or [None, None]
        cs = cr[0] if isinstance(cr[0], int) else 0
        with contextlib.suppress(Exception):
            await conn.execute(
                "UPDATE session_discourse_moves SET resolves_move_id=$2 "
                "WHERE id=$1 AND source_rid=$3",
                move_id(mt, title, cs), target, document_rid)
    return inserted


def facts_to_episode_payload(merged: dict, *, name: str, summary: str, source_document: str,
                             group_id: str) -> dict:
    type_map = merged["type_map"]
    fact_inputs = []
    for f in merged["facts"]:
        subj_t = type_map.get(_norm(f["subject"]))
        obj_t = type_map.get(_norm(f["object"])) if f.get("object") else None
        fact_inputs.append({
            "subject": f["subject"], "subject_type": subj_t,
            "predicate": f["predicate"],
            "object": f.get("object"), "object_type": obj_t,
            "object_literal": f.get("object_literal"),
            "fact_text": f["fact_text"],
        })
    return {
        "name": name, "content": summary[:2000] if summary else None,
        "source_description": "document", "source_document": source_document,
        "group_id": group_id, "create_entities": True, "facts": fact_inputs,
    }


# ── HTTP: /knowledge/episodes + /tasks/ingest ──────────────────────────────────────

async def post_episode(http: httpx.AsyncClient, payload: dict) -> dict:
    headers = {}
    if KOI_EPISODES_SERVICE_TOKEN:
        headers["Authorization"] = f"Bearer {KOI_EPISODES_SERVICE_TOKEN}"
    # 180s was hardcoded and is a hard SCALE CEILING: a book-length document merges into a
    # single episode (WWS 4th edition = 296 entities + 299 facts), and 3-tier entity
    # resolution with embeddings for that many entities exceeds 180s. The client then
    # raises ReadTimeout and the whole ingest aborts AFTER all windows extracted — while
    # uvicorn keeps working server-side, leaving a partially-written episode. Env-tunable,
    # default unchanged at 180 so nothing else shifts behaviour.
    # (Added 2026-07-31 during the Kurtz corpus drain — separate from the uncommitted
    # transport work already in this file.)
    _ep_timeout = float(os.getenv("DOC_EPISODE_TIMEOUT", "180"))
    r = await http.post(f"{KOI_BASE_URL}/knowledge/episodes", json=payload, headers=headers,
                        timeout=_ep_timeout)
    r.raise_for_status()
    return r.json()


# Episode request-size cap (#41). `EpisodeCreateRequest.facts` has no max_items and the
# write path has no chunking anywhere, so request duration scaled with DOCUMENT size,
# unbounded: a book merges into ONE call (WWS 4th edition 296 entities/299 facts; the
# blog 1,502/1,323). Two coupled failures followed. (a) The request outran the client
# timeout. (b) Worse, a client disconnect does NOT cancel the server request —
# create_episode runs to completion and COMMITS — so the client's retry then lock-waited
# on the ORIGINAL request's own uncommitted tuples, blew asyncpg's 60s per-statement
# command_timeout, and surfaced as a bare TimeoutError + HTTP 500. A request colliding
# with itself.
#
# Batching converts request duration from a function of document size (unbounded) into a
# function of batch size (bounded, tunable). Batches share (source_document, group_id), and
# the server's episode-reuse path keys on exactly that pair (knowledge_router ~line 813),
# so they collapse into ONE episode — same episode_id, so the post-episode dedup sweeps
# (which key on episode_id) still see the whole document and now also catch CROSS-BATCH
# duplicates that a single mega-request would have merged internally.
#
# ATOMICITY TRADE, stated plainly: P4's "every write commits together or not at all"
# narrows from per-DOCUMENT to per-BATCH. Acceptable because it still holds for every
# individual request; resume state lives in document_window_extractions, so a failure
# costs a re-POST and never a re-extraction; episode reuse plus the dedup sweeps make a
# re-POST convergent; and deep_extracted_at is only set after the LAST batch, so a
# partially-written document is retried rather than marked done. The status quo was
# strictly worse — the retry collision above already produced 489 facts from a ~299-fact
# payload.
DOC_EPISODE_BATCH_SIZE = int(os.getenv("DOC_EPISODE_BATCH_SIZE", "100"))


async def post_episode_batched(http: httpx.AsyncClient, payload: dict) -> dict:
    """POST an episode, splitting oversized fact lists into sequential same-episode calls.

    Returns one aggregated response shaped like a single post_episode() result.
    A payload at or under the cap takes the single-POST path, byte-identical to before;
    set DOC_EPISODE_BATCH_SIZE=0 to force that path always.
    """
    facts = payload.get("facts") or []
    if DOC_EPISODE_BATCH_SIZE <= 0 or len(facts) <= DOC_EPISODE_BATCH_SIZE:
        return await post_episode(http, payload)

    n = DOC_EPISODE_BATCH_SIZE
    chunks = [facts[i:i + n] for i in range(0, len(facts), n)]
    logger.info("episode: %d facts > cap %d → %d sequential batches sharing one episode",
                len(facts), n, len(chunks))

    agg = {"facts_created": 0, "facts_skipped": 0, "facts_null_embed": 0,
           "entities_created": 0, "entities_resolved": 0}
    mismatches: List[dict] = []
    episode_id = None
    for i, batch in enumerate(chunks):
        sub = {**payload, "facts": batch}
        ep = await post_episode(http, sub)
        for k in agg:
            agg[k] += int(ep.get(k) or 0)
        mismatches.extend(ep.get("type_mismatches") or [])
        eid = ep.get("episode_id")
        if episode_id is None:
            episode_id = eid
        elif eid and eid != episode_id:
            # The whole design rests on episode reuse collapsing these. If it did not,
            # the document is now split across episodes and the dedup sweeps (which key on
            # a single episode_id) would silently only clean the first one. Fail loud.
            raise ExtractionError(
                "episode_split",
                f"batch {i + 1}/{len(chunks)} landed in episode {eid}, not {episode_id} — "
                f"episode reuse by (source_document, group_id) did not hold; refusing to "
                f"continue with a document split across episodes")
        logger.info("  episode batch %d/%d: %d facts (created=%s skipped=%s)",
                    i + 1, len(chunks), len(batch), ep.get("facts_created"), ep.get("facts_skipped"))
    return {**agg, "episode_id": episode_id, "type_mismatches": mismatches}


async def file_type_mismatch_task(http: httpx.AsyncClient, conn, document_rid: str, tm: dict) -> None:
    """Record the mismatch in the dead-letter table AND file a best-effort cleanup task."""
    with contextlib.suppress(Exception):
        await conn.execute(
            "INSERT INTO document_extraction_item_errors (document_rid, item_type, payload, error) "
            "VALUES ($1, 'type_mismatch', $2::jsonb, $3)",
            document_rid, json.dumps(tm), f"requested={tm.get('requested_type')} resolved={tm.get('resolved_type')}")
    name = tm.get("name", "?")
    task_key = "doc-ingest-typemismatch-" + hashlib.sha1(
        f"{document_rid}:{_norm(name)}".encode()).hexdigest()[:12]
    payload = {
        "taskKey": task_key,
        "title": f"Review entity type-mismatch: {name} ({tm.get('requested_type')} vs {tm.get('resolved_type')})",
        "status": "open", "priority": "low", "sourceType": "document-ingest",
        "context": f"Document {document_rid} extraction hinted type {tm.get('requested_type')!r} for "
                   f"{name!r} but it resolved to existing {tm.get('resolved_type')!r} ({tm.get('resolved_uri')}). "
                   f"Review/merge if the existing entity is mis-typed.",
        "tags": ["document-ingest", "type-mismatch"],
    }
    with contextlib.suppress(Exception):
        r = await http.post(f"{KOI_BASE_URL}/tasks/ingest", json=payload, timeout=30.0)
        if r.status_code >= 300:
            logger.warning("task_ingest non-2xx (%s) for %s", r.status_code, name)


# ── Orchestration ──────────────────────────────────────────────────────────────────

async def extract_deep_document(pool: asyncpg.Pool, http: httpx.AsyncClient, *,
                                document_rid: str, tier: str, group_id: Optional[str],
                                run_id: str, force: bool,
                                source_sensor: str = "document-ingest") -> Dict[str, Any]:
    prompt_path, schema_path = prompt_schema_for_tier(tier)
    template = prompt_path.read_text(encoding="utf-8")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    want_discourse = (tier == "thorough")

    async with pool.acquire() as conn:
        locked = await conn.fetchval(
            "SELECT pg_try_advisory_lock(hashtext('deep-extract-doc:' || $1));", document_rid)
        if not locked:
            return {"status": "skipped_locked", "document_rid": document_rid}
        try:
            mem = await conn.fetchrow(
                "SELECT content->>'title' AS title, "
                "COALESCE(metadata->>'source_url', metadata->>'url') AS url, "
                "metadata->>'slug' AS slug, metadata->>'group_id' AS group_id "
                "FROM koi_memories WHERE rid = $1 AND source_sensor = $2", document_rid, source_sensor)
            if mem is None:
                raise ExtractionError("no_rag", f"no koi_memories row for {document_rid} "
                                                f"with source_sensor={source_sensor!r} (run the RAG step first)",
                                      terminal=True)
            group_id = group_id or mem["group_id"] or "personal"
            source_document = mem["url"] or document_rid
            doc_title = mem["title"] or mem["slug"] or document_rid

            chunks = await fetch_rag_chunks(conn, document_rid)
            windows = build_windows(chunks, WINDOW_CHARS, WINDOW_OVERLAP_CHUNKS)
            budget_exhausted = len(windows) > MAX_WINDOWS
            if budget_exhausted and not MAX_WINDOWS_EXPLICIT:
                # No caller-chosen budget → the default must not silently discard the
                # tail of a long document. Process all windows and do NOT flag truncation.
                logger.warning("window budget: %d windows > default MAX_WINDOWS=%d → "
                               "auto-raising (DOC_MAX_WINDOWS unset; set it to cap cost)",
                               len(windows), MAX_WINDOWS)
                budget_exhausted = False
            elif budget_exhausted:
                logger.warning("window budget: %d windows > MAX_WINDOWS=%d → truncating "
                               "(explicit cap; document tail NOT extracted)",
                               len(windows), MAX_WINDOWS)
                windows = windows[:MAX_WINDOWS]

            await conn.execute(
                """INSERT INTO document_ingestion_log
                   (document_rid, source_path, source_url, title, content_hash, chunk_count,
                    window_count, group_id, tier, rag_chunked_at, last_run_id, last_ingested_at)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9, NOW(), $10, NOW())
                   ON CONFLICT (document_rid) DO UPDATE SET
                     chunk_count=EXCLUDED.chunk_count, window_count=EXCLUDED.window_count,
                     tier=EXCLUDED.tier, group_id=EXCLUDED.group_id,
                     last_run_id=EXCLUDED.last_run_id, last_ingested_at=NOW()""",
                document_rid, None, source_document, doc_title,
                document_rid.split(":", 1)[-1], len(chunks), len(windows), group_id, tier, run_id)

            # Plan windows (idempotent); load cached extractions for resume.
            per_window: List[Optional[dict]] = [None] * len(windows)
            for w in windows:
                await conn.execute(
                    """INSERT INTO document_window_extractions
                       (document_rid, window_index, char_start, char_end, chunk_index_base, status, run_id)
                       VALUES ($1,$2,$3,$4,$5,'pending',$6)
                       ON CONFLICT (document_rid, window_index) DO NOTHING""",
                    document_rid, w.index, w.char_start, w.char_end, w.chunk_index_base, run_id)
                cached = await conn.fetchrow(
                    "SELECT status, raw_json FROM document_window_extractions "
                    "WHERE document_rid=$1 AND window_index=$2", document_rid, w.index)
                if cached and cached["status"] in ("extracted", "imported") and cached["raw_json"] and not force:
                    per_window[w.index] = json.loads(cached["raw_json"])

            # Extract any window not already cached.
            #
            # PER-WINDOW ISOLATION (#40): a window that cannot be extracted is DEAD-LETTERED
            # and the run continues over the windows that succeeded. Previously any single
            # window error propagated out and aborted the whole document, discarding every
            # window already extracted — during the Kurtz corpus drain that cost four
            # separate 30-50 minute passes, each losing 7-78 successfully extracted windows
            # to one stray key or a 1-element chunk_range. The schema already had
            # status='failed' + last_error for exactly this; nothing used them.
            #
            # Prefer "N-1 of N windows, loudly reported" over "0 of N, silently re-run
            # tomorrow". windows_failed is surfaced in the result and the gate evidence, and
            # deep_extracted_at is left NULL when any window failed, so a partial document
            # is retried rather than marked complete. Failed windows stay status='failed'
            # with their error, so a later run retries only those.
            windows_failed: List[int] = []
            for w in windows:
                if per_window[w.index] is not None:
                    logger.info("window %d/%d cached — skip", w.index + 1, len(windows))
                    continue
                logger.info("window %d/%d: extracting (%d chunks, %d chars) via %s",
                            w.index + 1, len(windows), len(w.chunk_indices), len(w.text), ANTHROPIC_MODEL)
                prompt = build_prompt(template, w, len(windows))
                try:
                    data = await extract_window_validated(prompt, http, schema, model=ANTHROPIC_MODEL)
                except ExtractionError as e:
                    windows_failed.append(w.index)
                    logger.error("window %d/%d FAILED (%s) — dead-lettering the WINDOW and "
                                 "continuing; the document is not lost: %s",
                                 w.index + 1, len(windows), e.reason, str(e)[:400])
                    await conn.execute(
                        """UPDATE document_window_extractions
                           SET status='failed', last_error=$3, route_used=$4, updated_at=NOW()
                           WHERE document_rid=$1 AND window_index=$2""",
                        document_rid, w.index, str(e)[:2000], ROUTE_USED)
                    continue
                per_window[w.index] = data
                await conn.execute(
                    """UPDATE document_window_extractions
                       SET status='extracted', route_used=$4, raw_json=$3::jsonb,
                           last_error=NULL, updated_at=NOW()
                       WHERE document_rid=$1 AND window_index=$2""",
                    document_rid, w.index, json.dumps(data), ROUTE_USED)

            # Only a TOTAL loss is fatal — there is nothing to merge, and silently writing an
            # empty episode would look like success.
            if windows_failed and not any(d for d in per_window):
                raise ExtractionError(
                    "all_windows_failed",
                    f"all {len(windows)} window(s) failed extraction; nothing to merge "
                    f"(see document_window_extractions.last_error for {document_rid})")
            if windows_failed:
                logger.warning("proceeding with %d/%d windows — %d dead-lettered: %s",
                               len(windows) - len(windows_failed), len(windows),
                               len(windows_failed), windows_failed)

            # Merge + write facts through /episodes.
            merged = merge_extractions([d for d in per_window if d], windows)
            summary = next((d["document"].get("summary") for d in per_window if d and d.get("document")), "")
            payload = facts_to_episode_payload(merged, name=doc_title, summary=summary,
                                               source_document=source_document, group_id=group_id)
            logger.info("merged: %d entities, %d facts → POST /knowledge/episodes (group=%s)",
                        len(merged["entities"]), len(merged["facts"]), group_id)
            ep = await post_episode_batched(http, payload)

            # Type-mismatches → dead-letter + cleanup task.
            mismatches = ep.get("type_mismatches") or []
            for tm in mismatches:
                await file_type_mismatch_task(http, conn, document_rid, tm)

            # Post-resolution fact dedup (merge-core correctness). The name-based
            # pre-merge AND /episodes' (predicate, object_uri) dedup both miss facts
            # that resolve to the same triple: distinct extracted names resolving to
            # one URI, and literal-object facts entirely (object_uri is NULL, so the
            # (predicate, object_uri) check never fires). Collapse by the RESOLVED key,
            # keeping the earliest row per (subject_uri, predicate, object|literal).
            episode_id = ep.get("episode_id")
            dedup_tag = await conn.execute(
                """
                WITH ranked AS (
                  SELECT id, row_number() OVER (
                    PARTITION BY subject_uri, predicate, COALESCE(object_uri, object_literal)
                    ORDER BY created_at ASC, id ASC) AS rn
                  FROM knowledge_facts WHERE episode_id = $1)
                DELETE FROM knowledge_facts f USING ranked r
                WHERE f.id = r.id AND r.rn > 1
                """, episode_id)
            dup_deleted = int(dedup_tag.split()[-1]) if dedup_tag.startswith("DELETE") else 0
            if dup_deleted:
                logger.info("post-resolution dedup: removed %d duplicate triple(s)", dup_deleted)

            # Semantic dedup (paraphrase tail) — the exact-triple sweep above misses
            # re-extraction paraphrases (fresh phrasing → distinct triple). Soft-retract
            # the LATER of a same-subject + same-predicate + same-OBJECT pair whose
            # fact_text embeddings exceed the threshold (keep earliest). OBJECT-AWARE so it
            # never merges genuinely-distinct facts (PR-#28 review): entity objects must
            # share object_uri (so "uses solar" vs "uses wind" never collapse), and literal
            # objects must be containment-related — one a more-detailed form of the other
            # ("152 initiatives" ⊂ "152 initiatives across 44 countries") — NOT merely
            # text-similar (so "$30 fee" vs "$40 fee" never collapse). Reversible (valid_to).
            sem_tag = await conn.execute(
                """
                WITH pairs AS (
                  SELECT a.id a_id, b.id b_id, a.created_at a_ct, b.created_at b_ct
                  FROM knowledge_facts a JOIN knowledge_facts b
                    ON a.subject_uri = b.subject_uri AND a.predicate = b.predicate
                   AND a.object_uri IS NOT DISTINCT FROM b.object_uri
                   AND (a.object_literal IS NULL OR b.object_literal IS NULL
                        OR position(b.object_literal IN a.object_literal) > 0
                        OR position(a.object_literal IN b.object_literal) > 0)
                   AND a.id < b.id
                   AND a.episode_id = $1 AND b.episode_id = $1
                   AND a.valid_to IS NULL AND b.valid_to IS NULL
                   AND a.fact_embedding_3072 IS NOT NULL AND b.fact_embedding_3072 IS NOT NULL
                   AND (1 - (a.fact_embedding_3072::halfvec(3072) <=> b.fact_embedding_3072::halfvec(3072))) > $2
                )
                UPDATE knowledge_facts SET valid_to = NOW()
                WHERE id IN (SELECT CASE WHEN a_ct <= b_ct THEN b_id ELSE a_id END FROM pairs)
                  AND valid_to IS NULL
                """, episode_id, SEMANTIC_DEDUP_THRESHOLD)
            sem_retracted = int(sem_tag.split()[-1]) if sem_tag.startswith("UPDATE") else 0
            if sem_retracted:
                logger.info("semantic dedup: retracted %d paraphrase-duplicate fact(s) (>%.2f)",
                            sem_retracted, SEMANTIC_DEDUP_THRESHOLD)

            # Flag 3 (plan §25): stamp source_node_rid = document_rid so a document purge
            # can key on it, not only episode_id.
            await conn.execute(
                "UPDATE knowledge_facts SET source_node_rid = $2 "
                "WHERE episode_id = $1 AND source_node_rid IS DISTINCT FROM $2",
                episode_id, document_rid)

            # Flag 2: populate the doc→entity bridge from the episode's resolved entity
            # URIs — /episodes writes facts but never document_entity_links, so
            # mentioned-in / get_entity_documents wouldn't surface this doc's entities.
            await conn.execute(
                """
                INSERT INTO document_entity_links (document_rid, entity_uri, mention_count, context)
                SELECT $2, uri, sum(n)::int, $3 FROM (
                  SELECT subject_uri AS uri, count(*) n FROM knowledge_facts
                    WHERE episode_id = $1 AND valid_to IS NULL GROUP BY subject_uri
                  UNION ALL
                  SELECT object_uri, count(*) FROM knowledge_facts
                    WHERE episode_id = $1 AND object_uri IS NOT NULL AND valid_to IS NULL GROUP BY object_uri
                ) u WHERE uri IS NOT NULL GROUP BY uri
                ON CONFLICT (document_rid, entity_uri) DO UPDATE SET mention_count = EXCLUDED.mention_count
                """, episode_id, document_rid, doc_title)
            entity_links = await conn.fetchval(
                "SELECT count(*) FROM document_entity_links WHERE document_rid = $1", document_rid)

            # no_residual_dups (gate regression guard): after both sweeps, assert 0 dup
            # triples REMAIN. Always 1 on a healthy run (the sweeps guarantee it); 0 only
            # if a sweep was skipped/broken — the gate floors this (the right "0 residual
            # dups" invariant, vs the old dups_ok which wrongly failed on healthy dedup).
            residual = await conn.fetchval(
                """SELECT count(*) FROM (
                     SELECT 1 FROM knowledge_facts WHERE episode_id = $1 AND valid_to IS NULL
                     GROUP BY subject_uri, predicate, COALESCE(object_uri, object_literal)
                     HAVING count(*) > 1) d""", episode_id)
            no_residual_dups = 1 if (residual or 0) == 0 else 0

            # Discourse (argument) layer — thorough tier only. Writes moves into the
            # generalized session_discourse_moves table (source_type='document').
            discourse_created = 0
            if want_discourse:
                merged_moves = merge_discourse([d for d in per_window if d])
                discourse_created = await write_discourse_moves(
                    conn, document_rid=document_rid, episode_id=episode_id, moves=merged_moves)
                logger.info("discourse: merged %d move(s) → wrote %d (source_type=document)",
                            len(merged_moves), discourse_created)

            # Only promote windows that actually extracted. A dead-lettered window must KEEP
            # status='failed' (with its last_error) so a later run retries just that window.
            for w in windows:
                if w.index in windows_failed:
                    continue
                await conn.execute(
                    "UPDATE document_window_extractions SET status='imported', updated_at=NOW() "
                    "WHERE document_rid=$1 AND window_index=$2", document_rid, w.index)
            # deep_extracted_at stays NULL when the document is INCOMPLETE for any reason —
            # a truncated tail OR a dead-lettered window — so a partial document is retried
            # rather than reported done.
            incomplete = bool(budget_exhausted) or bool(windows_failed)
            _err_parts = []
            if budget_exhausted:
                _err_parts.append(f"budget_truncated:{MAX_WINDOWS}/{len(chunks)}")
            if windows_failed:
                _err_parts.append(f"windows_failed:{len(windows_failed)}/{len(windows)}"
                                  f":{windows_failed[:20]}")
            await conn.execute(
                """UPDATE document_ingestion_log
                   SET deep_extracted_at = CASE WHEN $2 THEN NULL ELSE NOW() END,
                       deep_extraction_attempts = 0, deep_extraction_last_error = $3
                   WHERE document_rid = $1""",
                document_rid, incomplete, ("; ".join(_err_parts) if _err_parts else None))

            return {
                "status": "ok", "document_rid": document_rid, "group_id": group_id,
                "windows_total": len(windows), "windows_processed": sum(1 for d in per_window if d),
                "merged_entities": len(merged["entities"]), "merged_facts": len(merged["facts"]),
                "facts_created": ep.get("facts_created"), "facts_skipped": ep.get("facts_skipped"),
                "facts_dup_removed": dup_deleted, "facts_null_embed": ep.get("facts_null_embed"),
                "semantic_dups_retracted": sem_retracted, "no_residual_dups": no_residual_dups,
                "entity_links": entity_links,
                "entities_created": ep.get("entities_created"), "entities_resolved": ep.get("entities_resolved"),
                "discourse_moves_created": discourse_created,
                "type_mismatches": len(mismatches), "budget_exhausted": budget_exhausted,
                "windows_failed": len(windows_failed), "windows_failed_idx": windows_failed,
                "episode_id": ep.get("episode_id"),
            }
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtext('deep-extract-doc:' || $1));", document_rid)


async def amain(args) -> int:
    pp, sp = prompt_schema_for_tier(args.tier)
    if not pp.exists() or not sp.exists():
        print(f"Error: prompt/schema missing ({pp}, {sp})", file=sys.stderr)
        return 1

    pool = await asyncpg.create_pool(POSTGRES_URL, min_size=1, max_size=3)
    try:
        document_rid = args.document_rid
        if not document_rid and args.slug:
            async with pool.acquire() as conn:
                document_rid = await conn.fetchval(
                    "SELECT rid FROM koi_memories WHERE source_sensor=$1 "
                    "AND metadata->>'slug' = $2 ORDER BY updated_at DESC LIMIT 1",
                    args.source_sensor, args.slug)
        if not document_rid:
            print("Error: provide --document-rid or a --slug that resolves to one", file=sys.stderr)
            return 1

        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        # #36: the pool settings that actually fix CLOSE_WAIT reuse (keepalive_expiry
        # + TCP keepalive) can only come from the CLIENT — a per-request `timeout=`
        # scalar does not reliably fire on a half-closed pooled socket (observed: a
        # 40-minute hang against timeout=300). Per-request overrides below still
        # apply on top of these per-phase ceilings.
        async with provider_async_client(read=ANTHROPIC_TIMEOUT) as http:
            result = await extract_deep_document(
                pool, http, document_rid=document_rid, tier=args.tier,
                group_id=args.group_id, run_id=run_id, force=args.force,
                source_sensor=args.source_sensor)
    finally:
        await pool.close()

    print("\nDocument deep-extraction result:")
    for k, v in result.items():
        print(f"  {k}: {v}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--document-rid", help="document:<sha256> RID")
    g.add_argument("--slug", help="resolve the RID from koi_memories metadata slug")
    parser.add_argument("--tier", default="standard", choices=["standard", "thorough"],
                        help="standard = entities+facts (v1 prompt); thorough = +discourse (v2 prompt)")
    parser.add_argument("--group-id", help="override learning field (default: the document's group_id)")
    parser.add_argument("--force", action="store_true", help="re-extract all windows (ignore cache)")
    parser.add_argument("--source-sensor", default="document-ingest",
                        help="koi_memories.source_sensor to read RAG rows from "
                             "(default: document-ingest; e.g. substack-corpus-backfill for Substack posts)")
    args = parser.parse_args()
    try:
        return asyncio.run(amain(args))
    except ExtractionError as e:
        print(f"ExtractionError: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
