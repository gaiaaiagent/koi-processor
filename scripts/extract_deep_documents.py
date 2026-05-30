#!/usr/bin/env python3
"""
Document deep-extraction (Phase 1: entities + facts), chunk-wise + cross-window merge.

Phase 1 of the unified thorough content-ingestion plan
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
KOI_BASE_URL = os.getenv("KOI_BASE_URL", "http://localhost:8351")
CLAUDE_P_MODEL = os.getenv("CLAUDE_P_MODEL", "claude-sonnet-4-6")
KOI_INGEST_SERVICE_TOKEN = os.getenv("KOI_INGEST_SERVICE_TOKEN") or os.getenv("KOI_CLAIMS_SERVICE_TOKEN")

PROMPT_PATH = Path(os.getenv(
    "DOC_EXTRACTOR_PROMPT_FILE", str(REPO_ROOT / "scripts/prompts/deep_extraction_doc_v1.md")))
SCHEMA_PATH = Path(os.getenv(
    "DOC_EXTRACTOR_SCHEMA_FILE", str(REPO_ROOT / "scripts/schemas/deep_extraction_doc_v1.schema.json")))

WINDOW_CHARS = int(os.getenv("DOC_WINDOW_CHARS", "32000"))   # well under CLAUDE_P_CHAR_CAP; sized so a
                                                             # dense window finishes within the claude -p timeout
WINDOW_OVERLAP_CHUNKS = int(os.getenv("DOC_WINDOW_OVERLAP_CHUNKS", "2"))
CLAUDE_P_TIMEOUT = int(os.getenv("DOC_CLAUDE_P_TIMEOUT", "280"))
MAX_WINDOWS = int(os.getenv("DOC_MAX_WINDOWS", "12"))        # per-invocation budget cap (plan §Q3)
CLAUDE_FAILURE_MARKERS = ("invalid api key", "usage limit", "overloaded", "rate limit")

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

async def _call_claude_p(prompt: str, model: str, timeout: int = 180) -> str:
    process = await asyncio.create_subprocess_exec(
        "claude", "-p", "--model", model, "--print",
        # Run lean: extraction is a pure text->JSON completion needing no tools.
        # --strict-mcp-config + an empty MCP config stops the (possibly nested)
        # CLI from loading the caller's MCP servers/hooks, which otherwise hangs
        # the invocation for minutes. Verified: cuts a window from >280s to ~seconds.
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(process.communicate(prompt.encode()), timeout=timeout)
    except asyncio.TimeoutError as e:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        with contextlib.suppress(Exception):
            await process.wait()
        raise ExtractionError("extract_timeout", f"claude_p hung past {timeout}s (killed)") from e
    stdout = (stdout_b or b"").decode("utf-8", errors="replace")
    stderr = (stderr_b or b"").decode("utf-8", errors="replace")
    silent = next((m for m in CLAUDE_FAILURE_MARKERS if m in stdout.lower()), None)
    if process.returncode != 0 or silent:
        raise ExtractionError("extract_http_error",
                              f"claude_p rc={process.returncode} silent={silent} stderr={stderr[:200]}")
    return stdout


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
    if KOI_INGEST_SERVICE_TOKEN:
        headers["Authorization"] = f"Bearer {KOI_INGEST_SERVICE_TOKEN}"
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
    template = PROMPT_PATH.read_text(encoding="utf-8")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

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
                            w.index + 1, len(windows), len(w.chunk_indices), len(w.text), CLAUDE_P_MODEL)
                prompt = build_prompt(template, w, len(windows))
                raw = await _call_claude_p(prompt, CLAUDE_P_MODEL, timeout=CLAUDE_P_TIMEOUT)
                data = parse_and_validate(raw, schema)
                per_window[w.index] = data
                await conn.execute(
                    """UPDATE document_window_extractions
                       SET status='extracted', route_used='claude_p', raw_json=$3::jsonb, updated_at=NOW()
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
                "entities_created": ep.get("entities_created"), "entities_resolved": ep.get("entities_resolved"),
                "type_mismatches": len(mismatches), "budget_exhausted": budget_exhausted,
                "episode_id": ep.get("episode_id"),
            }
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtext('deep-extract-doc:' || $1));", document_rid)


async def amain(args) -> int:
    if not PROMPT_PATH.exists() or not SCHEMA_PATH.exists():
        print(f"Error: prompt/schema missing ({PROMPT_PATH}, {SCHEMA_PATH})", file=sys.stderr)
        return 1
    if args.tier != "standard":
        print(f"Error: Phase 1 implements --tier standard (entities+facts) only; got {args.tier!r}. "
              f"thorough (discourse+claims) arrives in Phase 3.", file=sys.stderr)
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
                        help="Phase 1 implements 'standard' (entities+facts)")
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
