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
extractor prompt per window via `claude -p` (Sonnet — forced for extraction
quality), then MERGES entities (type-priority coercion) + facts (dedup) across
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
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import httpx
from jsonschema import Draft202012Validator

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
# Semantic dedup threshold: the exact-triple sweep misses PARAPHRASES (a re-extraction's
# fresh phrasings resolve to distinct triples). After it, retract the later of any
# same-subject + same-predicate fact pair whose fact_text embeddings exceed this cosine
# — conservative (predicate match + high bar) so re-extractions self-converge without
# false-retracting genuinely-distinct facts.
SEMANTIC_DEDUP_THRESHOLD = float(os.getenv("DOC_SEMANTIC_DEDUP_THRESHOLD", "0.95"))

# Per-window extraction transport: the direct Anthropic Messages API (DOCUMENT path
# only — the session pipeline keeps its own `claude -p` on the subscription). The API
# is ~4-5x faster than the claude -p CLI (which adds harness/MCP overhead) and is the
# correct headless backend transport. Bills per-token against ANTHROPIC_API_KEY.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("DOC_EXTRACTOR_MODEL", CLAUDE_P_MODEL)
ANTHROPIC_MAX_TOKENS = int(os.getenv("DOC_EXTRACTOR_MAX_TOKENS", "24000"))
ANTHROPIC_TIMEOUT = int(os.getenv("DOC_EXTRACTOR_TIMEOUT", "300"))
ANTHROPIC_RETRYABLE = {429, 500, 502, 503, 529}

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


def parse_and_validate(raw: str, schema: dict) -> dict:
    first, last = raw.find("{"), raw.rfind("}")
    if first < 0 or last < 0 or last < first:
        raise ExtractionError("extract_parse_error", "no JSON object found in output")
    try:
        data = json.loads(raw[first:last + 1])
    except json.JSONDecodeError as e:
        raise ExtractionError("extract_parse_error", f"json decode: {e}") from e
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
    r = await http.post(f"{KOI_BASE_URL}/knowledge/episodes", json=payload, headers=headers, timeout=180.0)
    r.raise_for_status()
    return r.json()


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
                                run_id: str, force: bool) -> Dict[str, Any]:
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
                "SELECT content->>'title' AS title, metadata->>'source_url' AS url, "
                "metadata->>'slug' AS slug, metadata->>'group_id' AS group_id "
                "FROM koi_memories WHERE rid = $1 AND source_sensor = 'document-ingest'", document_rid)
            if mem is None:
                raise ExtractionError("no_rag", f"no document-ingest koi_memories row for {document_rid} "
                                                f"(run the RAG step first)", terminal=True)
            group_id = group_id or mem["group_id"] or "personal"
            source_document = mem["url"] or document_rid
            doc_title = mem["title"] or mem["slug"] or document_rid

            chunks = await fetch_rag_chunks(conn, document_rid)
            windows = build_windows(chunks, WINDOW_CHARS, WINDOW_OVERLAP_CHUNKS)
            budget_exhausted = len(windows) > MAX_WINDOWS
            if budget_exhausted:
                logger.warning("window budget: %d windows > MAX_WINDOWS=%d → truncating",
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

            # Extract any window not already cached (FORCE Sonnet for quality).
            for w in windows:
                if per_window[w.index] is not None:
                    logger.info("window %d/%d cached — skip", w.index + 1, len(windows))
                    continue
                logger.info("window %d/%d: extracting (%d chunks, %d chars) via %s",
                            w.index + 1, len(windows), len(w.chunk_indices), len(w.text), ANTHROPIC_MODEL)
                prompt = build_prompt(template, w, len(windows))
                raw = await _call_anthropic(prompt, http, model=ANTHROPIC_MODEL)
                data = parse_and_validate(raw, schema)
                per_window[w.index] = data
                await conn.execute(
                    """UPDATE document_window_extractions
                       SET status='extracted', route_used='anthropic_api', raw_json=$3::jsonb, updated_at=NOW()
                       WHERE document_rid=$1 AND window_index=$2""",
                    document_rid, w.index, json.dumps(data))

            # Merge + write facts through /episodes.
            merged = merge_extractions([d for d in per_window if d], windows)
            summary = next((d["document"].get("summary") for d in per_window if d and d.get("document")), "")
            payload = facts_to_episode_payload(merged, name=doc_title, summary=summary,
                                               source_document=source_document, group_id=group_id)
            logger.info("merged: %d entities, %d facts → POST /knowledge/episodes (group=%s)",
                        len(merged["entities"]), len(merged["facts"]), group_id)
            ep = await post_episode(http, payload)

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

            for w in windows:
                await conn.execute(
                    "UPDATE document_window_extractions SET status='imported', updated_at=NOW() "
                    "WHERE document_rid=$1 AND window_index=$2", document_rid, w.index)
            await conn.execute(
                """UPDATE document_ingestion_log
                   SET deep_extracted_at = CASE WHEN $2 THEN NULL ELSE NOW() END,
                       deep_extraction_attempts = 0, deep_extraction_last_error = $3
                   WHERE document_rid = $1""",
                document_rid, budget_exhausted,
                (f"budget_truncated:{MAX_WINDOWS}/{len(chunks)}" if budget_exhausted else None))

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
                    "SELECT rid FROM koi_memories WHERE source_sensor='document-ingest' "
                    "AND metadata->>'slug' = $1 ORDER BY updated_at DESC LIMIT 1", args.slug)
        if not document_rid:
            print("Error: provide --document-rid or a --slug that resolves to one", file=sys.stderr)
            return 1

        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        async with httpx.AsyncClient() as http:
            result = await extract_deep_document(
                pool, http, document_rid=document_rid, tier=args.tier,
                group_id=args.group_id, run_id=run_id, force=args.force)
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
    args = parser.parse_args()
    try:
        return asyncio.run(amain(args))
    except ExtractionError as e:
        print(f"ExtractionError: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
