#!/usr/bin/env python3
"""Deep session extraction v2 orchestrator.

See ~/.claude/plans/deep-session-extraction.md for full Decision log (1-143).
Implements Phase C of the plan. Assumes migration 086 applied.

Invocation modes (Decision 104):
  --session-id <uuid> [...]   — process explicit sessions (sensor path)
  --auto-select --limit N     — eligibility-query mode (backfill path)

Usage:
  source ~/projects/regenai/koi-processor/config/deep_extract.env
  python scripts/extract_deep_sessions.py --auto-select --limit 10 --preflight
  python scripts/extract_deep_sessions.py --session-id <uuid1> --session-id <uuid2>
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import asyncpg
import httpx
from jsonschema import Draft202012Validator

# Orchestrator lives under ~/projects/regenai/koi-processor/scripts/.
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SCHEMA_PATH = SCRIPT_DIR / "schemas" / "deep_extraction_v2.schema.json"
TMP_ROOT = Path("/tmp/deep_extract")
LOCK_PATH = TMP_ROOT / "orchestrator.lock"

# Import sibling TELUS client (live in same scripts/ dir)
sys.path.insert(0, str(SCRIPT_DIR))
import extract_via_telus  # noqa: E402

TELUS_CHAR_CAP = 40_000      # Decision 4 + 56
CLAUDE_P_CHAR_CAP = 200_000  # Decision 7 + 56
CLAUDE_FAILURE_MARKERS = [
    "reached max turns",
    "tool_unavailable",
    "permission denied",
    "denied by the user",
]
NAMESPACE = uuid.UUID("00000000-0000-0000-0000-000000000001")

# Decision 29: predicate classification
ENTITY_PREDICATES = {
    "USES", "WORKS_ON", "DEPENDS_ON", "BUILT_BY", "OWNED_BY",
    "MEMBER_OF", "LOCATED_IN", "REPLACES", "MANAGES", "COLLABORATES_WITH",
    "RELATES_TO", "PART_OF", "INSTANCE_OF", "AUTHORED_BY",
}
LITERAL_PREDICATES = {
    "HAS_VERSION", "HAS_DATE", "HAS_COUNT", "HAS_STATUS", "HAS_PRIORITY",
    "HAS_DESCRIPTION", "HAS_URL", "HAS_NAME", "HAS_PATH",
}


# ======================================================================
# Configuration + guards
# ======================================================================

@dataclass
class Config:
    database_url: str
    koi_base_url: str
    poly_embedding_url: str
    expected_pg_data_dir: str
    claude_p_model: str
    prompt_path: Path
    mode: str  # 'layers_only' | 'full'
    run_id: str

    @classmethod
    def from_env(cls, run_id: Optional[str] = None) -> "Config":
        if os.environ.get("DEEP_EXTRACT_CONFIRM") != "yes":
            raise SystemExit(
                "Refusing to run without DEEP_EXTRACT_CONFIRM=yes; "
                "confirm you're targeting local personal_koi. Exit 12."
            )
        mode = os.environ.get("DEEP_EXTRACTION_MODE", "layers_only")
        if mode not in ("layers_only", "full"):
            raise SystemExit(f"Invalid DEEP_EXTRACTION_MODE={mode!r}; use layers_only or full.")
        prompt_path = Path(os.environ.get(
            "EXTRACTOR_PROMPT_FILE",
            str(Path.home() / "projects/darren-workflow/skills/extract-session-entities/extractor-prompt-v2.md"),
        ))
        if not prompt_path.exists():
            raise SystemExit(f"EXTRACTOR_PROMPT_FILE not found: {prompt_path}")
        return cls(
            database_url=os.environ.get(
                "DATABASE_URL", "postgresql://darrenzal@localhost/personal_koi"
            ),
            koi_base_url=os.environ.get("KOI_BASE_URL", "http://localhost:8351"),
            poly_embedding_url=os.environ.get("POLY_EMBEDDING_URL", "http://10.100.0.1:11435"),
            expected_pg_data_dir=os.environ.get(
                "EXPECTED_PG_DATA_DIR", "/opt/homebrew/var/postgresql@14"
            ),
            claude_p_model=os.environ.get("CLAUDE_P_MODEL", "claude-sonnet-4-6"),
            prompt_path=prompt_path,
            mode=mode,
            run_id=run_id or _default_run_id(),
        )


def _default_run_id() -> str:
    env_val = os.environ.get("EXTRACTION_RUN_ID", "").strip()
    if env_val:
        return env_val
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


async def assert_db_target(conn: asyncpg.Connection, expected_pg_data_dir: str) -> None:
    """Decisions 79, 85, 96, 106: block wrong DB / remote / data_directory mismatch."""
    row = await conn.fetchrow(
        "SELECT current_database() AS db, inet_server_addr() AS host;"
    )
    test_mode = os.environ.get("DEEP_EXTRACT_TEST_MODE") == "1"
    expected_dbs = {"personal_koi"}
    if test_mode:
        expected_dbs.add("personal_koi_test")
    if row["db"] not in expected_dbs:
        raise SystemExit(f"Refusing to run on DB {row['db']!r}; expected one of {expected_dbs}. Exit 10.")
    host = row["host"]
    if host is not None and str(host) not in ("127.0.0.1", "::1", "localhost"):
        raise SystemExit(f"Refusing to run against non-local host {host!r}. Exit 10.")
    data_dir = await conn.fetchval("SELECT setting FROM pg_settings WHERE name='data_directory';")
    if data_dir != expected_pg_data_dir:
        raise SystemExit(
            f"data_directory mismatch: got {data_dir!r}, expected {expected_pg_data_dir!r}. "
            "Update EXPECTED_PG_DATA_DIR in deep_extract.env if this is intentional. Exit 14."
        )


# ======================================================================
# Preflight
# ======================================================================

async def preflight(config: Config) -> dict:
    """Decision 37 + 83 + 118: check KOI resolver + claude-p + (optionally) TELUS."""
    report: dict[str, Any] = {"koi": None, "claude_p": None, "telus": None}

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.get(f"{config.koi_base_url}/health")
            r.raise_for_status()
            data = r.json()
            tiers = data.get("resolution_tiers", {})
            if not (
                data.get("status") == "healthy"
                and data.get("semantic_matching") is True
                and tiers.get("tier1_exact") is True
                and tiers.get("tier2_semantic") is True
                and tiers.get("tier3_create") is True
            ):
                raise RuntimeError(f"KOI /health not all tiers healthy: {data}")
            report["koi"] = {"status": "healthy"}
        except Exception as e:
            report["koi"] = {"status": "unhealthy", "error": str(e)}

    # Decision 118: pinned-model probe
    try:
        result = subprocess.run(
            ["claude", "-p", "--model", config.claude_p_model, "--print"],
            input="say ok",
            capture_output=True,
            text=True,
            timeout=60,
        )
        stdout = (result.stdout or "").lower()
        silent = any(m in stdout for m in CLAUDE_FAILURE_MARKERS)
        if result.returncode == 0 and "ok" in stdout and not silent:
            report["claude_p"] = {"status": "healthy", "model": config.claude_p_model}
        else:
            report["claude_p"] = {
                "status": "unhealthy", "rc": result.returncode,
                "stdout": stdout[:200], "silent_marker": silent,
            }
    except subprocess.TimeoutExpired:
        report["claude_p"] = {"status": "timeout"}

    try:
        await extract_via_telus.healthcheck()
        report["telus"] = {"status": "healthy"}
    except extract_via_telus.TelusUnavailable as e:
        report["telus"] = {"status": "unavailable", "error": str(e)}

    return report


# ======================================================================
# Eligibility + selection
# ======================================================================

async def eligible_sessions(conn: asyncpg.Connection, limit: int, min_chunks: int = 1) -> list[str]:
    """Decision 8 + 35 + 111: eligibility with sentinel exclusion + retry gate.
    --min-chunks filters out thin meta-sessions (e.g., v1-extractor subprocess
    invocations that became their own single-chunk sessions) for pilot quality.
    """
    rows = await conn.fetch(
        """
        SELECT session_id FROM session_ingestion_log
         WHERE (deep_extracted_at IS NULL OR deep_extracted_at < last_ingested_at)
           AND (deep_extracted_at IS NULL OR deep_extracted_at != '1970-01-01'::timestamp)
           AND (deep_extraction_next_retry_at IS NULL OR deep_extraction_next_retry_at <= NOW())
           AND chunk_count >= $2
         ORDER BY last_ingested_at DESC
         LIMIT $1;
        """,
        limit, min_chunks,
    )
    return [r["session_id"] for r in rows]


# ======================================================================
# Transcript building + LLM routing
# ======================================================================

async def fetch_chunks(conn: asyncpg.Connection, session_id: str) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT chunk_index, chunk_text FROM session_chunks WHERE session_id = $1 ORDER BY chunk_index ASC",
        session_id,
    )


def build_transcript(chunks: list[asyncpg.Record]) -> str:
    """Decision 56 + Decision 14: preserve full chunk content.
    The transcript_len check in route_and_extract enforces 40k/200k caps.
    Do NOT truncate individual chunks here — lost content causes the model
    to hallucinate 'transcript truncated' discourse moves.
    """
    parts = []
    for c in chunks:
        txt = c["chunk_text"] or ""
        parts.append(f"[{c['chunk_index']}] {txt}")
    return "\n\n".join(parts)


def build_prompt(prompt_template: str, transcript: str) -> str:
    placeholder = "<!-- The pipeline appends the concatenated session chunks here at call time -->"
    if placeholder not in prompt_template:
        raise RuntimeError("Prompt template missing SESSION TEXT placeholder")
    return prompt_template.replace(placeholder, transcript)


async def route_and_extract(
    prompt: str, transcript_len: int, config: Config, telus_healthy: bool,
) -> tuple[str, str]:
    """Decision 56 + 57 + 70 + 115: pick TELUS (<=40k) vs claude -p.
    Returns (raw_completion, route_used)."""
    if transcript_len > CLAUDE_P_CHAR_CAP:
        raise ExtractionError("session_too_long", f"transcript={transcript_len} > {CLAUDE_P_CHAR_CAP}", terminal=True)

    if transcript_len <= TELUS_CHAR_CAP and telus_healthy:
        try:
            raw = await extract_via_telus.extract(prompt)
            return raw, "telus_gemma"
        except extract_via_telus.TelusTimeout as e:
            raise ExtractionError("extract_timeout", str(e)) from e
        except extract_via_telus.TelusUnavailable as e:
            # Fall through to claude -p if TELUS fails mid-run (Decision 32 is about claude-p)
            raise ExtractionError("extract_http_error", f"telus: {e}") from e

    # claude -p path (Decision 70 + 115)
    raw = await _call_claude_p(prompt, model=config.claude_p_model)
    return raw, "claude_p"


async def _call_claude_p(prompt: str, model: str, timeout: int = 180) -> str:
    """Invoke claude -p via stdin (Decision 70 + 115).

    Uses native asyncio.subprocess + asyncio.wait_for because on macOS
    synchronous subprocess.run(timeout=...) wrapped in asyncio.to_thread did
    NOT reliably kill hanging children — observed claude-p subprocesses
    surviving past their 180s timeout, blocking the orchestrator.
    """
    process = await asyncio.create_subprocess_exec(
        "claude", "-p", "--model", model, "--print",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(prompt.encode()),
            timeout=timeout,
        )
    except asyncio.TimeoutError as e:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        with contextlib.suppress(Exception):
            await process.wait()
        raise ExtractionError(
            "extract_timeout",
            f"claude_p hung past {timeout}s (killed)",
        ) from e

    stdout = (stdout_bytes or b"").decode("utf-8", errors="replace")
    stderr = (stderr_bytes or b"").decode("utf-8", errors="replace")
    lower = stdout.lower()
    silent = next((m for m in CLAUDE_FAILURE_MARKERS if m in lower), None)
    if process.returncode != 0 or silent:
        raise ExtractionError(
            "extract_http_error",
            f"claude_p_unavailable: rc={process.returncode} silent={silent} stderr={stderr[:200]}",
        )
    return stdout


# ======================================================================
# Parse + validate
# ======================================================================

class ExtractionError(RuntimeError):
    def __init__(self, reason: str, detail: str, terminal: bool = False):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.terminal = terminal


def parse_and_validate(raw: str, schema: dict) -> dict:
    first, last = raw.find("{"), raw.rfind("}")
    if first < 0 or last < 0 or last < first:
        raise ExtractionError("extract_parse_error", "no JSON object found in output")
    try:
        data = json.loads(raw[first : last + 1])
    except json.JSONDecodeError as e:
        raise ExtractionError("extract_parse_error", f"json decode: {e}") from e
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: e.path)
    if errors:
        msgs = [f"{list(e.path)}: {e.message}" for e in errors[:5]]
        raise ExtractionError("extract_parse_error", f"schema: {' | '.join(msgs)}")
    return data


# ======================================================================
# Resolver + embedding helpers
# ======================================================================

class Resolver:
    """Decision 12 + C.3a: tiered entity resolution via POST /entity/resolve."""

    def __init__(self, base_url: str, client: httpx.AsyncClient):
        self.base_url = base_url
        self.client = client

    async def resolve(self, text: str, type_hint: Optional[str] = None) -> Optional[dict]:
        payload = {"text": text}
        if type_hint:
            payload["type_hint"] = type_hint
        try:
            r = await self.client.post(f"{self.base_url}/entity/resolve", json=payload, timeout=20.0)
            r.raise_for_status()
        except httpx.HTTPError:
            return None
        return r.json()


async def embed_text(client: httpx.AsyncClient, poly_url: str, text: str) -> Optional[list[float]]:
    """Decision 34: poly embed with graceful null fallback."""
    if not text.strip():
        return None
    try:
        # Poly endpoint uses /embed per reference_poly_embedding_server memory
        r = await client.post(
            f"{poly_url}/embed", json={"input": text[:2000]}, timeout=15.0,
        )
        r.raise_for_status()
        data = r.json()
        vec = data.get("embedding") or (data.get("embeddings") or [None])[0]
        if vec and len(vec) == 1024:
            return list(vec)
    except Exception:
        pass
    return None


def vector_literal(vec: Optional[list[float]]) -> Optional[str]:
    """Format Python list as pgvector text literal (asyncpg doesn't codec pgvector by default)."""
    if vec is None:
        return None
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


# ======================================================================
# Session ID resolution (8-char prefix → full UUID)
# ======================================================================

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
PREFIX_RE = re.compile(r"^[0-9a-fA-F]{8}$")


async def resolve_session_ref(conn: asyncpg.Connection, ref: str) -> tuple[Optional[str], Optional[str]]:
    """Decision 27 + 28 + 36: resolve full UUID or 8-char prefix.
    Returns (resolved_uuid, error_reason). error_reason is None if resolved.
    """
    ref = ref.strip()
    if UUID_RE.match(ref):
        row = await conn.fetchval("SELECT session_id FROM session_ingestion_log WHERE session_id = $1;", ref)
        if row:
            return row, None
        return None, "continuity_unknown_target"
    if PREFIX_RE.match(ref):
        rows = await conn.fetch(
            "SELECT session_id FROM session_ingestion_log WHERE session_id LIKE $1 LIMIT 2;",
            ref.lower() + "%",
        )
        if len(rows) == 1:
            return rows[0]["session_id"], None
        if len(rows) == 0:
            return None, "continuity_prefix_no_match"
        return None, "continuity_prefix_ambiguous"
    return None, "continuity_prefix_no_match"


# ======================================================================
# Run summary
# ======================================================================

@dataclass
class RunSummary:
    run_id: str
    mode: str
    sessions_attempted: int = 0
    sessions_succeeded: int = 0
    sessions_first_attempt_failed: int = 0
    sessions_retry_exhausted: int = 0
    sessions_skipped_locked: int = 0
    sessions_skipped_too_long: int = 0
    items_written: dict[str, int] = field(default_factory=lambda: {
        "facts": 0, "discourse_moves": 0, "continuity_links": 0,
        "document_entity_links": 0, "episodes": 0,
    })
    item_errors: int = 0
    telus_calls: int = 0
    claude_p_calls: int = 0
    provider_errors: int = 0
    session_results: list[dict] = field(default_factory=list)

    def write(self) -> Path:
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        out = TMP_ROOT / f"{self.run_id}.summary.json"
        out.write_text(json.dumps({
            "run_id": self.run_id,
            "mode": self.mode,
            "sessions_attempted": self.sessions_attempted,
            "sessions_succeeded": self.sessions_succeeded,
            "sessions_first_attempt_failed": self.sessions_first_attempt_failed,
            "sessions_retry_exhausted": self.sessions_retry_exhausted,
            "sessions_skipped_locked": self.sessions_skipped_locked,
            "sessions_skipped_too_long": self.sessions_skipped_too_long,
            "items_written": self.items_written,
            "item_errors": self.item_errors,
            "telus_calls": self.telus_calls,
            "claude_p_calls": self.claude_p_calls,
            "provider_errors": self.provider_errors,
            "session_results": self.session_results,
        }, indent=2))
        return out


# ======================================================================
# Retry schedule (Decision 108)
# ======================================================================

def compute_next_retry(attempts: int) -> Optional[datetime]:
    # DB column is TIMESTAMP (without timezone); strip tzinfo to avoid asyncpg
    # "can't subtract offset-naive and offset-aware" at bind time.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if attempts == 1:
        return now + timedelta(hours=6)
    if attempts == 2:
        return now + timedelta(hours=24)
    return None


# ======================================================================
# Per-session processor
# ======================================================================

async def cleanup_tmp_stale() -> None:
    """Decision 87 Layer 2: self-clean at orchestrator startup."""
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    TMP_ROOT.chmod(0o700)
    now = time.time()
    for p in TMP_ROOT.glob("*.prompt.txt"):
        with contextlib.suppress(Exception):
            if now - p.stat().st_mtime > 86_400:
                p.unlink()
    for p in TMP_ROOT.glob("*.summary.json"):
        with contextlib.suppress(Exception):
            if now - p.stat().st_mtime > 7 * 86_400:
                p.unlink()


async def process_session(
    conn: asyncpg.Connection,
    http: httpx.AsyncClient,
    resolver: Resolver,
    session_id: str,
    config: Config,
    schema: dict,
    prompt_template: str,
    telus_healthy: bool,
    summary: RunSummary,
) -> dict:
    """Process a single session end-to-end."""
    result: dict[str, Any] = {"session_id": session_id, "status": None, "mode": None}

    # Decision 82: per-session advisory lock (transaction-scoped)
    # We hold it in a dedicated short transaction for the pre-flight + write.
    lock_ok = await conn.fetchval(
        "SELECT pg_try_advisory_lock(hashtext('deep-extract:' || $1));", session_id
    )
    if not lock_ok:
        summary.sessions_skipped_locked += 1
        result["status"] = "skipped_locked"
        return result

    try:
        # Determine mode for THIS session (Decision 25 + 33 + 74)
        session_row = await conn.fetchrow(
            "SELECT entities_extracted_at, deep_extracted_at, deep_extraction_attempts, last_ingested_at "
            "FROM session_ingestion_log WHERE session_id = $1",
            session_id,
        )
        if not session_row:
            result["status"] = "missing_session"
            return result

        env_mode = config.mode  # layers_only default
        has_v1 = session_row["entities_extracted_at"] is not None
        is_new_session = not has_v1 and session_row["deep_extracted_at"] is None
        if is_new_session:
            session_mode = "full"
        elif env_mode == "full":
            session_mode = "full"
        else:
            session_mode = "layers_only"
        result["mode"] = session_mode

        # Decision 103: write run metadata BEFORE any extraction work
        new_attempts = (session_row["deep_extraction_attempts"] or 0) + 1
        await conn.execute(
            """
            UPDATE session_ingestion_log
               SET deep_extraction_mode = $1,
                   deep_extraction_last_run_id = $2,
                   deep_extraction_attempts = $3
             WHERE session_id = $4
            """,
            session_mode, config.run_id, new_attempts, session_id,
        )

        # Decision 141: set application_name for the trigger guard (layers_only only)
        if session_mode == "layers_only":
            await conn.execute(
                f"SET application_name = 'deep-extract:layers_only:{config.run_id}';"
            )
        else:
            await conn.execute(
                f"SET application_name = 'deep-extract:full:{config.run_id}';"
            )

        # Fetch chunks
        chunks = await fetch_chunks(conn, session_id)
        chunk_count = len(chunks)
        if chunk_count == 0:
            await _record_failure(
                conn, session_id, new_attempts,
                "prompt_build_error", "no chunks for session",
            )
            summary.sessions_first_attempt_failed += 1
            result["status"] = "no_chunks"
            return result

        transcript = build_transcript(chunks)
        prompt = build_prompt(prompt_template, transcript)
        transcript_len = len(transcript)

        if transcript_len > CLAUDE_P_CHAR_CAP:
            await _mark_retry_exhausted(
                conn, session_id,
                "session_too_long", f"transcript={transcript_len} > {CLAUDE_P_CHAR_CAP}",
            )
            summary.sessions_skipped_too_long += 1
            result["status"] = "too_long"
            return result

        # Persist prompt for debugging (deleted on success per Decision 80)
        prompt_tmp = TMP_ROOT / f"{session_id}.prompt.txt"
        prompt_tmp.write_text(prompt)

        # Route + extract
        try:
            raw, route = await route_and_extract(prompt, transcript_len, config, telus_healthy)
        except ExtractionError as e:
            summary.provider_errors += 1
            if e.terminal:
                await _mark_retry_exhausted(conn, session_id, e.reason, e.detail)
                result["status"] = "terminal_failure"
                return result
            await _record_failure(conn, session_id, new_attempts, e.reason, e.detail)
            summary.sessions_first_attempt_failed += 1
            if new_attempts >= 3:
                await _mark_retry_exhausted(conn, session_id, e.reason, e.detail)
                summary.sessions_retry_exhausted += 1
            result["status"] = "provider_error"
            result["reason"] = e.reason
            return result

        if route == "telus_gemma":
            summary.telus_calls += 1
        else:
            summary.claude_p_calls += 1

        # Parse + validate
        try:
            data = parse_and_validate(raw, schema)
        except ExtractionError as e:
            await _record_failure(conn, session_id, new_attempts, e.reason, e.detail)
            summary.sessions_first_attempt_failed += 1
            if new_attempts >= 3:
                await _mark_retry_exhausted(conn, session_id, e.reason, e.detail)
                summary.sessions_retry_exhausted += 1
            result["status"] = "parse_failed"
            result["reason"] = e.reason
            return result

        # Import in ONE transaction (Decision 46 + 88 + 93)
        writes = await _import_session(
            conn, http, resolver, session_id, data, chunk_count,
            session_mode, config, summary,
        )
        result.update(writes)

        # Success path (Decision 103 + 123: reset attempts)
        await conn.execute(
            """
            UPDATE session_ingestion_log
               SET deep_extracted_at = NOW(),
                   deep_extraction_attempts = 0,
                   deep_extraction_last_error = NULL,
                   deep_extraction_next_retry_at = NULL
             WHERE session_id = $1
            """,
            session_id,
        )
        summary.sessions_succeeded += 1
        result["status"] = "ok"
        result["route"] = route

        # Decision 80: delete prompt on success
        with contextlib.suppress(FileNotFoundError):
            prompt_tmp.unlink()

        return result
    finally:
        await conn.execute(
            "SELECT pg_advisory_unlock(hashtext('deep-extract:' || $1));", session_id
        )
        # Reset application_name so the trigger guard doesn't affect other ops
        await conn.execute("RESET application_name;")


async def _record_failure(conn, session_id: str, attempts: int, reason: str, detail: str) -> None:
    next_retry = compute_next_retry(attempts)
    err_text = f"{reason}: {detail}"[:500]
    await conn.execute(
        """
        UPDATE session_ingestion_log
           SET deep_extraction_last_error = $1,
               deep_extraction_next_retry_at = $2
         WHERE session_id = $3
        """,
        err_text, next_retry, session_id,
    )


async def _mark_retry_exhausted(conn, session_id: str, reason: str, detail: str) -> None:
    err_text = f"{reason}: {detail}"[:500]
    await conn.execute(
        """
        UPDATE session_ingestion_log
           SET deep_extracted_at = '1970-01-01'::timestamp,
               deep_extraction_last_error = $1,
               deep_extraction_next_retry_at = NULL
         WHERE session_id = $2
        """,
        err_text, session_id,
    )


# ======================================================================
# Import layer (DB writes)
# ======================================================================

async def _import_session(
    conn: asyncpg.Connection,
    http: httpx.AsyncClient,
    resolver: Resolver,
    session_id: str,
    data: dict,
    chunk_count: int,
    mode: str,
    config: Config,
    summary: RunSummary,
) -> dict:
    """All DB writes happen in an explicit transaction."""
    written = {"facts": 0, "discourse_moves": 0, "continuity_links": 0,
               "document_entity_links": 0, "episodes": 0}
    async with conn.transaction():
        # Re-apply application_name inside transaction (Decision 141)
        if mode == "layers_only":
            await conn.execute(
                f"SET LOCAL application_name = 'deep-extract:layers_only:{config.run_id}';"
            )
        else:
            await conn.execute(
                f"SET LOCAL application_name = 'deep-extract:full:{config.run_id}';"
            )

        # ---------- Episode ----------
        ep = data["episode"]
        existing = await conn.fetchrow(
            "SELECT id FROM knowledge_episodes "
            "WHERE source_document = $1 AND source_description = 'claude_session' "
            "ORDER BY created_at DESC LIMIT 1",
            session_id,
        )
        ep_metadata = {
            "session_arc": ep["session_arc"],
            "summary": ep["summary"],
            "duration_turns": ep["duration_turns"],
            "v2_extraction_run_id": config.run_id,
            "v2_extraction_mode": mode,
        }
        if mode == "layers_only" and existing is None:
            # Decision 68: narrow exception — create minimal episode so discourse moves can link
            ep_metadata["minimal_episode"] = True
            ep_metadata["reason"] = "missing_episode_layers_only"
            episode_id = await conn.fetchval(
                """
                INSERT INTO knowledge_episodes (name, content, source_description, source_document, metadata)
                VALUES ($1, '', 'claude_session', $2, $3::jsonb)
                RETURNING id
                """,
                ep["name"][:200], session_id, json.dumps(ep_metadata),
            )
            await conn.execute(
                """
                INSERT INTO deep_extraction_item_errors (session_id, item_type, reason, payload, extraction_run_id)
                VALUES ($1, 'discourse_move', 'missing_episode_auto_created', $2::jsonb, $3)
                """,
                session_id, json.dumps({"created_episode_id": str(episode_id)}), config.run_id,
            )
            summary.item_errors += 1
            written["episodes"] += 1
        elif mode == "full":
            if existing is None:
                episode_id = await conn.fetchval(
                    """
                    INSERT INTO knowledge_episodes (name, content, source_description, source_document, metadata)
                    VALUES ($1, $2, 'claude_session', $3, $4::jsonb)
                    RETURNING id
                    """,
                    ep["name"][:200], ep["summary"], session_id, json.dumps(ep_metadata),
                )
            else:
                episode_id = existing["id"]
                # Decision 89: MERGE metadata
                await conn.execute(
                    """
                    UPDATE knowledge_episodes
                       SET name = $1, content = $2, metadata = metadata || $3::jsonb
                     WHERE id = $4
                    """,
                    ep["name"][:200], ep["summary"], json.dumps(ep_metadata), episode_id,
                )
            written["episodes"] += 1
        else:
            # layers_only with existing episode: no write
            episode_id = existing["id"]

        # ---------- Discourse moves (always written) ----------
        moves_written = await _write_discourse_moves(
            conn, http, config, session_id, episode_id, data["discourse"], chunk_count, summary,
        )
        written["discourse_moves"] += moves_written

        # ---------- Continuity links (always written) ----------
        cl_written = await _write_continuity_links(
            conn, session_id, data.get("continuity", {}), data["episode"].get("is_continuation_of"), summary,
        )
        written["continuity_links"] += cl_written

        # ---------- Entities, facts, entity_links (only on full mode) ----------
        if mode == "full":
            entity_uris = await _write_entities(
                conn, resolver, session_id, data.get("entities", []), summary,
            )
            written["document_entity_links"] += len(entity_uris)
            if data.get("entities") or data.get("facts"):
                # Facts (Decision 93)
                fact_n = await _write_facts(
                    conn, http, config, resolver, session_id, episode_id,
                    data.get("facts", []), chunk_count, summary,
                )
                written["facts"] += fact_n

    for k, v in written.items():
        summary.items_written[k] += v
    return {"writes": written}


async def _write_discourse_moves(
    conn, http, config, session_id, episode_id, discourse, chunk_count, summary,
) -> int:
    total = 0
    # Decision 21: observations not emitted in v2.0
    for move_type, items in (
        ("decision", discourse.get("decisions", [])),
        ("problem", discourse.get("problems", [])),
        ("question", discourse.get("questions", [])),
        ("next_step", discourse.get("next_steps", [])),
        ("learning", discourse.get("learnings", [])),
    ):
        for item in items:
            tr = item.get("turn_range") or [None, None]
            a, b = tr[0], tr[1]
            # Bounds (Decision 14 + C.3b): null out-of-range rather than reject
            a_valid = isinstance(a, int) and 0 <= a < chunk_count
            b_valid = isinstance(b, int) and 0 <= b < chunk_count
            if a_valid and b_valid and a > b:
                a, b = b, a
            if not (a_valid and b_valid):
                await conn.execute(
                    """
                    INSERT INTO deep_extraction_item_errors
                        (session_id, item_type, reason, payload, extraction_run_id)
                    VALUES ($1, 'discourse_move', 'turn_range_out_of_range', $2::jsonb, $3)
                    """,
                    session_id, json.dumps({"move_type": move_type, "turn_range": tr, "title": item.get("title")}),
                    config.run_id,
                )
                summary.item_errors += 1
                a, b = None, None

            title = (item.get("title") or "")[:500]
            if not title:
                continue
            detail = item.get("detail")

            # Status mapping + validation (Decisions 10 + 22 + 59)
            raw_status = item.get("status")
            if move_type == "next_step" and raw_status is None:
                raw_status = "pending"
            status = raw_status

            # Decision 59: problem with status='resolved' but missing resolution_turn_range
            # → downgrade to 'open', log item error
            resolution_tr = item.get("resolution_turn_range")
            if move_type == "problem" and status == "resolved":
                if not isinstance(resolution_tr, list) or len(resolution_tr) != 2:
                    await conn.execute(
                        """
                        INSERT INTO deep_extraction_item_errors
                            (session_id, item_type, reason, payload, extraction_run_id)
                        VALUES ($1, 'discourse_move', 'resolution_range_missing', $2::jsonb, $3)
                        """,
                        session_id, json.dumps({"title": title, "turn_range": tr}),
                        config.run_id,
                    )
                    summary.item_errors += 1
                    status = "open"
                    resolution_tr = None

            # Deterministic ID (Decision 31)
            move_id = uuid.uuid5(
                NAMESPACE,
                f"move:{session_id}:{move_type}:{hashlib.sha256(title.lower().encode()).hexdigest()[:16]}:{a or 0}",
            )

            # Embedding (Decision 34)
            embed_text_val = title + ("\n" + detail if detail else "")
            embedding = await embed_text(http, config.poly_embedding_url, embed_text_val)
            embedding_literal = vector_literal(embedding)

            target_date = None
            if move_type == "next_step":
                td = item.get("target_date")
                if td:
                    try:
                        target_date = datetime.strptime(td, "%Y-%m-%d").date()
                    except (TypeError, ValueError):
                        target_date = None  # bad date format: null it out, log item-error
                        await conn.execute(
                            """
                            INSERT INTO deep_extraction_item_errors
                                (session_id, item_type, reason, payload, extraction_run_id)
                            VALUES ($1, 'discourse_move', 'target_date_invalid', $2::jsonb, $3)
                            """,
                            session_id, json.dumps({"title": title, "target_date": td}), config.run_id,
                        )
                        summary.item_errors += 1

            await conn.execute(
                """
                INSERT INTO session_discourse_moves
                    (id, episode_id, session_id, move_type, title, detail, status,
                     turn_range_start, turn_range_end, embedding, target_date)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::vector, $11)
                ON CONFLICT (id) DO UPDATE SET
                    detail = EXCLUDED.detail,
                    status = EXCLUDED.status,
                    turn_range_end = EXCLUDED.turn_range_end,
                    embedding = EXCLUDED.embedding,
                    target_date = EXCLUDED.target_date
                """,
                move_id, episode_id, session_id, move_type, title[:200], detail,
                status, a, b, embedding_literal, target_date,
            )
            total += 1

            # Decision 11: write resolution as SEPARATE row
            if move_type == "problem" and resolution_tr and len(resolution_tr) == 2:
                rta, rtb = resolution_tr
                if isinstance(rta, int) and isinstance(rtb, int) and 0 <= rta <= rtb < chunk_count:
                    res_id = uuid.uuid5(
                        NAMESPACE,
                        f"move:{session_id}:resolution:{hashlib.sha256(title.lower().encode()).hexdigest()[:16]}:{rta}",
                    )
                    await conn.execute(
                        """
                        INSERT INTO session_discourse_moves
                            (id, episode_id, session_id, move_type, title, detail, status,
                             resolves_move_id, turn_range_start, turn_range_end, embedding)
                        VALUES ($1, $2, $3, 'resolution', $4, NULL, NULL, $5, $6, $7, $8::vector)
                        ON CONFLICT (id) DO UPDATE SET
                            turn_range_end = EXCLUDED.turn_range_end,
                            embedding = EXCLUDED.embedding
                        """,
                        res_id, episode_id, session_id, title[:200], move_id,
                        rta, rtb, embedding_literal,
                    )
                    total += 1
    return total


async def _write_continuity_links(conn, session_id, continuity, is_continuation_of, summary) -> int:
    total = 0
    links = list(continuity.get("continues_from", [])) + list(continuity.get("referenced_sessions", []))
    # Decision 30: if episode.is_continuation_of is set, map to a continues link
    if is_continuation_of:
        links.append({"session_id": is_continuation_of, "link_type": "continues", "detail": None})

    seen = set()
    for link in links:
        ref = link.get("session_id")
        link_type = link.get("link_type", "references")
        if not ref or link_type not in ("continues", "fixes", "supersedes", "references", "expands"):
            continue
        resolved, reason = await resolve_session_ref(conn, ref)
        if not resolved:
            await conn.execute(
                """
                INSERT INTO deep_extraction_item_errors
                    (session_id, item_type, reason, payload, extraction_run_id)
                VALUES ($1, 'continuity_link', $2, $3::jsonb,
                        (SELECT deep_extraction_last_run_id FROM session_ingestion_log WHERE session_id = $1))
                """,
                session_id, reason or "continuity_prefix_no_match",
                json.dumps({"ref": ref, "link_type": link_type}),
            )
            summary.item_errors += 1
            continue
        key = (session_id, resolved, link_type)
        if key in seen:
            continue
        seen.add(key)
        await conn.execute(
            """
            INSERT INTO session_continuity_links
                (from_session_id, to_session_id, link_type, detail)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (from_session_id, to_session_id, link_type)
            DO UPDATE SET detail = EXCLUDED.detail
            """,
            session_id, resolved, link_type, link.get("detail"),
        )
        total += 1
    return total


async def _write_entities(conn, resolver: Resolver, session_id: str, entities: list[dict], summary) -> set[str]:
    """Full mode only. Resolve via /entity/resolve + upsert document_entity_links (Decision 46)."""
    resolved_uris: set[str] = set()
    new_counts: dict[str, int] = {}
    for ent in entities:
        name = (ent.get("name") or "").strip()
        mention_count = max(1, int(ent.get("mention_count") or 1))
        if not name:
            continue
        res = await resolver.resolve(name, type_hint=ent.get("type"))
        if not res:
            continue
        uri = res.get("fuseki_uri")
        if not uri:
            continue
        confidence = res.get("confidence", 1.0)
        if confidence < 0.85:
            continue
        resolved_uris.add(uri)
        # Decision 4 (entity_links mention_count overwrite, Decision 4 was wrong number; this is resolution flow)
        new_counts[uri] = mention_count

    document_rid = f"claude-session:{session_id}"

    # Decision 46: DELETE stale set-diff (full mode only)
    if resolved_uris:
        await conn.execute(
            """
            DELETE FROM document_entity_links
             WHERE document_rid = $1
               AND NOT (entity_uri = ANY($2::text[]))
            """,
            document_rid, list(resolved_uris),
        )
    else:
        await conn.execute(
            "DELETE FROM document_entity_links WHERE document_rid = $1", document_rid
        )

    for uri, count in new_counts.items():
        await conn.execute(
            """
            INSERT INTO document_entity_links (document_rid, entity_uri, mention_count)
            VALUES ($1, $2, $3)
            ON CONFLICT (document_rid, entity_uri)
            DO UPDATE SET mention_count = EXCLUDED.mention_count
            """,
            document_rid, uri, count,
        )
    return resolved_uris


async def _write_facts(
    conn, http: httpx.AsyncClient, config: Config, resolver: Resolver,
    session_id: str, episode_id, facts: list[dict], chunk_count: int, summary,
) -> int:
    """Full mode only. Decision 12 + 38 + 93 + 123."""
    # Decision 38: DELETE-before-INSERT scoped to session's lineage (safe in full mode)
    await conn.execute(
        "DELETE FROM knowledge_facts WHERE source_node_rid = $1",
        f"claude-session:{session_id}",
    )
    total = 0
    for f in facts:
        subj_text = (f.get("subject") or "").strip()
        pred = (f.get("predicate") or "").strip()
        obj_text = f.get("object")
        obj_literal = f.get("object_literal")
        fact_text = (f.get("fact_text") or "").strip()
        tr = f.get("turn_range") or [None, None]
        if not subj_text or not pred or not fact_text:
            continue
        a, b = tr[0], tr[1]
        a_valid = isinstance(a, int) and 0 <= a < chunk_count
        b_valid = isinstance(b, int) and 0 <= b < chunk_count
        if a_valid and b_valid and a > b:
            a, b = b, a
        if not (a_valid and b_valid):
            a, b = None, None

        # Subject resolution (required)
        subj_res = await resolver.resolve(subj_text)
        subj_uri = (subj_res or {}).get("fuseki_uri") if subj_res and (subj_res.get("confidence", 0) >= 0.85) else None
        if not subj_uri:
            await conn.execute(
                """
                INSERT INTO deep_extraction_item_errors
                    (session_id, item_type, reason, payload, extraction_run_id)
                VALUES ($1, 'fact', 'subject_unresolvable', $2::jsonb, $3)
                """,
                session_id, json.dumps(f), config.run_id,
            )
            summary.item_errors += 1
            continue

        # Object resolution
        obj_uri = None
        final_literal = obj_literal
        if obj_text and pred in ENTITY_PREDICATES:
            obj_res = await resolver.resolve(obj_text)
            if obj_res and obj_res.get("confidence", 0) >= 0.85:
                obj_uri = obj_res.get("fuseki_uri")
            if obj_uri is None:
                # entity-predicate with unresolvable object → dead-letter
                await conn.execute(
                    """
                    INSERT INTO deep_extraction_item_errors
                        (session_id, item_type, reason, payload, extraction_run_id)
                    VALUES ($1, 'fact', 'object_unresolvable_entity_predicate', $2::jsonb, $3)
                    """,
                    session_id, json.dumps(f), config.run_id,
                )
                summary.item_errors += 1
                continue
        elif pred in LITERAL_PREDICATES:
            if final_literal is None and obj_text:
                final_literal = obj_text
        else:
            # Unknown predicate — default to entity-required, else literal fallback
            if obj_text:
                obj_res = await resolver.resolve(obj_text)
                if obj_res and obj_res.get("confidence", 0) >= 0.85:
                    obj_uri = obj_res.get("fuseki_uri")
                else:
                    final_literal = final_literal or obj_text

        fact_id = uuid.uuid5(
            NAMESPACE,
            f"fact:{session_id}:{subj_uri}:{pred}:{obj_uri or final_literal or ''}:{a or 0}",
        )
        embedding = await embed_text(http, config.poly_embedding_url, fact_text)
        embedding_literal = vector_literal(embedding)

        await conn.execute(
            """
            INSERT INTO knowledge_facts
                (id, episode_id, subject_uri, predicate, object_uri, object_literal,
                 fact_text, fact_embedding, source_node_rid,
                 turn_range_start, turn_range_end)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::vector,
                    'claude-session:' || $9, $10, $11)
            ON CONFLICT (id) DO UPDATE SET
                fact_text = EXCLUDED.fact_text,
                turn_range_start = EXCLUDED.turn_range_start,
                turn_range_end = EXCLUDED.turn_range_end,
                fact_embedding = EXCLUDED.fact_embedding
            """,
            fact_id, episode_id, subj_uri, pred, obj_uri, final_literal,
            fact_text, embedding_literal, session_id, a, b,
        )
        total += 1
    return total


# ======================================================================
# Main orchestrator
# ======================================================================

@contextlib.contextmanager
def pid_file_lock() -> None:
    """Decision 82 Layer 2: flock on orchestrator-wide PID file."""
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    fd = None
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another orchestrator is running (flock held). Exit 11.")
        os.write(fd, f"{os.getpid()}\n".encode())
        yield
    finally:
        if fd is not None:
            with contextlib.suppress(Exception):
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)


async def amain(args) -> int:
    config = Config.from_env(run_id=args.run_id)
    print(f"[orch] run_id={config.run_id} mode={config.mode}")

    with pid_file_lock():
        await cleanup_tmp_stale()

        # Preflight
        preflight_report = None
        telus_healthy = False
        if args.preflight:
            preflight_report = await preflight(config)
            print(f"[orch] preflight: {json.dumps(preflight_report)}")
            if preflight_report["koi"]["status"] != "healthy":
                print("ABORT: KOI unhealthy", file=sys.stderr)
                return 1
            if preflight_report["claude_p"]["status"] != "healthy":
                print("ABORT: claude -p unhealthy", file=sys.stderr)
                return 3
            telus_healthy = preflight_report["telus"]["status"] == "healthy"
        else:
            # Still probe TELUS silently for routing decision
            try:
                await extract_via_telus.healthcheck()
                telus_healthy = True
            except extract_via_telus.TelusUnavailable:
                telus_healthy = False

        prompt_template = config.prompt_path.read_text()
        schema = json.loads(SCHEMA_PATH.read_text())
        summary = RunSummary(run_id=config.run_id, mode=config.mode)

        conn = await asyncpg.connect(config.database_url)
        try:
            await assert_db_target(conn, config.expected_pg_data_dir)

            # Collect session IDs
            if args.session_id:
                session_ids = list(args.session_id)
            elif args.auto_select:
                session_ids = await eligible_sessions(conn, args.limit, args.min_chunks)
                print(f"[orch] auto-selected {len(session_ids)} sessions (min_chunks={args.min_chunks})")
            else:
                print("Provide --session-id or --auto-select.", file=sys.stderr)
                return 2

            if not session_ids:
                print("[orch] no sessions to process")
                summary.write()
                return 0

            async with httpx.AsyncClient(timeout=30.0) as http:
                resolver = Resolver(config.koi_base_url, http)
                for sid in session_ids:
                    summary.sessions_attempted += 1
                    try:
                        result = await process_session(
                            conn, http, resolver, sid, config, schema, prompt_template,
                            telus_healthy, summary,
                        )
                        summary.session_results.append(result)
                        print(f"[orch] session={sid[:8]} status={result.get('status')} mode={result.get('mode')}")
                    except Exception as e:
                        summary.sessions_first_attempt_failed += 1
                        summary.session_results.append({
                            "session_id": sid, "status": "unhandled_exception",
                            "error": str(e), "trace": traceback.format_exc()[:500],
                        })
                        print(f"[orch] session={sid[:8]} UNHANDLED: {e}", file=sys.stderr)
        finally:
            await conn.close()

        out_path = summary.write()
        print(f"[orch] summary → {out_path}")
        return 0 if summary.sessions_succeeded == summary.sessions_attempted else 4


def main() -> int:
    parser = argparse.ArgumentParser(description="Deep session extraction v2 orchestrator")
    parser.add_argument("--session-id", action="append", help="Process this session_id (repeatable)")
    parser.add_argument("--auto-select", action="store_true", help="Use eligibility query")
    parser.add_argument("--limit", type=int, default=5, help="Max sessions for --auto-select")
    parser.add_argument("--min-chunks", type=int, default=1, help="Filter to sessions with chunk_count >= N (pilot: use 3)")
    parser.add_argument("--preflight", action="store_true", help="Run preflight checks")
    parser.add_argument("--run-id", help="Override run_id (else generated)")
    args = parser.parse_args()
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
