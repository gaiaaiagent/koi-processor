#!/usr/bin/env python3
"""
Arbitrary-document ingestion into KOI — unified depth-dial orchestrator.

The unified "thorough content ingestion" capability
(`~/.claude/plans/plan-a-unified-thorough-wiggly-plum.md`).

Unlike `doc_scanner.py` (which is scoped to governed-repo markdown — requires
YAML frontmatter + a `doc_id` + a repo path), this module ingests ANY markdown
text under a content-addressed RID, with no frontmatter/repo requirement. It
reuses `TextChunker` + `OpenAIEmbeddingProvider` verbatim and writes the same
`koi_memories` + `koi_memory_chunks` (embedding_3072) tables, namespaced by
`source_sensor='document-ingest'` and `rid='document:<sha256(markdown)>'`.

`doc_scanner.py` and its `is_governed()` gate are intentionally left untouched.

Tiers (depth dial): `rag` = chunk + embed → RAG-searchable full text; `standard`
= rag + facts (deep-extract) + impact claims; `thorough` = standard + discourse
moves (Phase 3, written to session_discourse_moves source_type='document'). Every
stage is idempotent and content-addressed by `document_rid`. The run emits a flat
gate-evidence `response_summary` (`--gate-evidence-out`) the document-ingest
completion gate asserts on.

Usage:
    cd /path/to/koi-processor
    source config/personal.env
    python scripts/ingest_document.py --source-path ~/Documents/sources/biohubs/biohubs-whitepaper.md \
        --tier rag --source-url https://example.org/paper --slug biohubs-whitepaper

Idempotency: `document_rid = document:<sha256(converted-markdown bytes)>`. Re-running
on identical bytes replaces the same chunk set (net Δ = 0 chunks).
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import httpx

# Add repo root to path (mirror doc_scanner.py) so `api.*` imports resolve.
sys.path.insert(0, str(Path(__file__).parent.parent))
from api.embedding_provider import OpenAIEmbeddingProvider  # noqa: E402
from api.chunker import TextChunker  # noqa: E402
from api.provider_http import provider_async_client  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Config (mirror doc_scanner.py) ──────────────────────────────────────────────

POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "3072"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-large")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# Co-located KOI backend, pinned (see extract_deep_documents.py). A dedicated var so
# it does NOT inherit personal.env's general KOI_BASE_URL (a WireGuard peer).
KOI_BASE_URL = os.getenv("DOC_INGEST_KOI_URL", "http://localhost:8351")

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
SOURCE_SENSOR = "document-ingest"

# Path-safety allowlist (plan §16). When INGEST_SOURCE_ROOT is set the backend
# endpoint enforces it strictly; the operator CLI defaults to these roots and
# falls back to "any readable file" only when neither root exists.
DEFAULT_ALLOWED_ROOTS = [
    os.path.expanduser("~/Documents/sources"),
    str(Path.cwd() / "tmp" / "incoming"),
]
VALID_TIERS = ("rag", "standard", "thorough")


# ── Identity / provenance ───────────────────────────────────────────────────────

def compute_document_rid(markdown: str) -> tuple[str, str]:
    """Content-address the CONVERTED MARKDOWN (plan §8/§26), not the source PDF/HTML.

    Returns (content_hash, document_rid). Full 64-hex sha256 — re-ingesting
    identical converted bytes yields the same RID and is a no-op upsert.
    """
    chash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    return chash, f"document:{chash}"


def resolve_allowed_source_path(raw_path: str) -> Path:
    """Canonicalize + allowlist-check source_path (plan §16).

    Rejects non-existent / non-file paths and (when a root is configured/exists)
    paths that escape the allowlisted root via `..` or symlink.
    """
    p = Path(raw_path).expanduser().resolve()
    if not p.is_file():
        raise ValueError(f"source_path is not a readable file: {p}")

    env_root = os.getenv("INGEST_SOURCE_ROOT")
    roots = [Path(env_root).expanduser().resolve()] if env_root else [
        Path(r).resolve() for r in DEFAULT_ALLOWED_ROOTS if Path(r).exists()
    ]
    if not roots:
        # No configured/extant root → operator-CLI trust (endpoint sets a root).
        return p
    for root in roots:
        try:
            p.relative_to(root)
            return p
        except ValueError:
            continue
    raise ValueError(
        f"source_path {p} is outside the allowlisted root(s): "
        f"{', '.join(str(r) for r in roots)}"
    )


# ── DB writes (de-gated; mirror doc_scanner's koi_memories/koi_memory_chunks) ────

async def upsert_document_memory(
    conn: asyncpg.Connection,
    document_rid: str,
    markdown: str,
    source_meta: Dict[str, Any],
) -> str:
    """Upsert the document row into koi_memories (source_sensor='document-ingest').

    Optional `source_meta` keys, all defaulting to today's behaviour when absent
    (this is a shared path — `doc_scanner`, the documents router and the
    federation handler all reach it):

    - ``author``        → written to `metadata.author`. Omitted when absent, so
      an existing caller's metadata blob is byte-identical.
    - ``is_private``    → the `koi_memories.is_private` COLUMN, which this
      function previously never wrote. The column defaults to ``false``, so a
      federated private document would otherwise land flagged PUBLIC. ``None``
      means "caller did not say": on INSERT that becomes ``false`` (the schema
      default, i.e. unchanged), and on UPDATE it PRESERVES whatever the row
      already had rather than coercing it back to ``false``.
    - ``access_source`` → the `koi_memories.access_source` COLUMN, same
      unspecified-preserves semantics.
    - ``source_sensor`` → provenance stamp, default ``document-ingest``.
    - ``staged``        → the `koi_memories.superseded_at` COLUMN on the INSERT
      arm only: a document-history successor is born invisible and
      `POST /history/apply` reveals it. On conflict the row's pre-state decides:
      a staged re-ingest of an already-hidden row keeps its stamp, and every
      other re-ingest reveals the row. A re-ingest can never hide a live row.
      Whenever the row is live after the write, its discourse moves are
      un-stamped too (see `_unstamp_discourse_moves`).
    """
    doc_content = {
        "title": source_meta.get("name") or source_meta.get("slug") or document_rid,
        "text": markdown,
        "file_path": source_meta.get("source_path"),
    }
    doc_metadata: Dict[str, Any] = {
        "slug": source_meta.get("slug"),
        "source_url": source_meta.get("source_url"),
        "author": source_meta.get("author"),
        "retrieval_method": source_meta.get("retrieval_method"),
        "retrieved_at": source_meta.get("retrieved_at"),
        "content_hash": source_meta.get("content_hash"),
        "group_id": source_meta.get("group_id", "personal"),
        "char_count": len(markdown),
        "tier": source_meta.get("tier", "rag"),
        "redactions_applied": source_meta.get("redactions_applied"),
    }
    # Drop None-valued provenance keys for a clean metadata blob.
    doc_metadata = {k: v for k, v in doc_metadata.items() if v is not None}

    # None = "caller did not specify" for both privacy columns. Distinguishing
    # that from an explicit False is the whole point: COALESCE on UPDATE keeps
    # an existing True from being silently downgraded by a caller that simply
    # does not model privacy.
    is_private = source_meta.get("is_private")
    if is_private is not None:
        is_private = bool(is_private)
    access_source = source_meta.get("access_source")
    source_sensor = source_meta.get("source_sensor") or SOURCE_SENSOR
    # FLAT key — the same one _write_document reads. A nested
    # source_meta["history"]["staged"] would be invisible here, the successor would
    # never be born staged, and every apply would refuse SUCCESSOR_NOT_STAGED.
    staged = bool(source_meta.get("staged"))

    existing = await conn.fetchrow("SELECT id FROM koi_memories WHERE rid = $1", document_rid)
    event_type = "NEW" if existing is None else "UPDATE"

    # superseded_at on conflict: `THEN koi_memories.superseded_at`, not now(), because
    # the stamp records when this version stopped being live and a retried staged
    # ingest must be idempotent. Keeping the existing value is also what stops a
    # staged re-ingest from hiding a live row (its NULL stays NULL). The
    # `IS NOT NULL` conjunct is redundant with that THEN today (mutation-tested: an
    # equivalent mutant); it is kept so the guarantee survives if THEN ever stamps.
    row = await conn.fetchrow(
        """
        INSERT INTO koi_memories
            (id, rid, event_type, source_sensor, content, metadata, is_private,
             access_source, superseded_at)
        VALUES (gen_random_uuid(), $1, $2, $3, $4::jsonb, $5::jsonb,
                COALESCE($6::boolean, false), $7,
                CASE WHEN $8::boolean THEN now() ELSE NULL END)
        ON CONFLICT (rid) DO UPDATE SET
            event_type = EXCLUDED.event_type,
            content = EXCLUDED.content,
            metadata = EXCLUDED.metadata,
            is_private = COALESCE($6::boolean, koi_memories.is_private),
            access_source = COALESCE($7, koi_memories.access_source),
            superseded_at = CASE
                WHEN $8::boolean AND koi_memories.superseded_at IS NOT NULL
                    THEN koi_memories.superseded_at
                    ELSE NULL
            END,
            updated_at = NOW()
        RETURNING id, superseded_at
        """,
        document_rid, event_type, source_sensor,
        json.dumps(doc_content), json.dumps(doc_metadata),
        is_private, access_source, staged,
    )
    if row["superseded_at"] is None:
        await _unstamp_discourse_moves(conn, document_rid)
    return str(row["id"])


async def _unstamp_discourse_moves(conn: asyncpg.Connection, document_rid: str) -> None:
    """The reveal and the un-stamp travel together (apply_transaction.md step 12b).

    A live document must not carry stamped discourse moves (invariant I11). Keyed on
    the row's state after the upsert, so it also heals drift on an already-live row.
    The column exists only where migration 123 is applied; elsewhere (the NUC, the
    test database outside a fixture) a bare UPDATE would raise 42703 and abort the
    caller's transaction, so the catalog is asked first, per call — a process-level
    cache would be wrong across databases and rolled-back fixtures.
    """
    has_column = await conn.fetchval(
        """SELECT EXISTS (SELECT 1 FROM pg_attribute
                           WHERE attrelid = to_regclass('session_discourse_moves')
                             AND attname = 'superseded_at' AND NOT attisdropped)""")
    if has_column:
        await conn.execute(
            """UPDATE session_discourse_moves SET superseded_at = NULL
                WHERE source_type = 'document' AND source_rid = $1
                  AND superseded_at IS NOT NULL""", document_rid)


async def upsert_document_chunks(
    conn: asyncpg.Connection,
    document_rid: str,
    chunks: List[Dict[str, Any]],
    embeddings: List[Optional[List[float]]],
    source_meta: Dict[str, Any],
) -> int:
    """Replace this document's chunks with the freshly embedded set.

    Writes embedding_3072 (the 3072-dim primary), never the legacy 1024 column.
    DELETE-then-INSERT keyed on document_rid keeps the chunk set stable across
    re-runs (idempotent: identical bytes → identical chunk count).
    """
    await conn.execute("DELETE FROM koi_memory_chunks WHERE document_rid = $1", document_rid)

    context = source_meta.get("name") or source_meta.get("slug") or document_rid
    # `title`/`author`/`source_url` are document-level, but they are duplicated
    # onto every chunk deliberately: the `memories` surface of
    # /knowledge/unified-search reads koi_memory_chunks WITHOUT joining
    # koi_memories (knowledge_router.py ~:2101), so anything not on the chunk row
    # is unreachable from a search result. This mirrors how the `docs` surface
    # already surfaces title/source_url out of `mc.metadata`.
    base_meta = {
        "slug": source_meta.get("slug"),
        "group_id": source_meta.get("group_id", "personal"),
        "source_sensor": source_meta.get("source_sensor") or SOURCE_SENSOR,
        "title": context,
        "author": source_meta.get("author"),
        "source_url": source_meta.get("source_url"),
    }
    base_meta = {k: v for k, v in base_meta.items() if v is not None}

    written = 0
    total = len(chunks)
    for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
        chunk_rid = f"{document_rid}:chunk:{i}"
        chunk_content = {"text": chunk["text"], "context": context}
        chunk_meta = dict(base_meta)
        if emb is None:
            chunk_meta["embedding_failed"] = True
        emb_str = json.dumps(emb) if emb is not None else None
        await conn.execute(
            """
            INSERT INTO koi_memory_chunks
                (chunk_rid, document_rid, chunk_index, total_chunks, content, embedding_3072, metadata)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::vector(3072), $7::jsonb)
            ON CONFLICT (chunk_rid) DO UPDATE SET
                chunk_index = EXCLUDED.chunk_index,
                total_chunks = EXCLUDED.total_chunks,
                content = EXCLUDED.content,
                embedding_3072 = EXCLUDED.embedding_3072,
                metadata = EXCLUDED.metadata
            """,
            chunk_rid, document_rid, i, total,
            json.dumps(chunk_content), emb_str, json.dumps(chunk_meta),
        )
        written += 1
    return written


def effective_field_membership(group_id: str, fields: Optional[List[str]]) -> List[str]:
    """Compute the dedup(union([group_id] + fields)) membership set (Piece B / G2).

    Contract: `group_id` is ALWAYS the primary membership row (first, never
    dropped); `--fields` only ADDS rows. Order-preserving dedup keeps group_id
    first. Empty/whitespace tokens are filtered. When `fields` is None/empty the
    result is exactly `[group_id]` — byte-identical to the pre-`--fields` behavior.
    Unknown field IDs are valid by design (fields are created-by-use; no registry).
    """
    primary = (group_id or "personal").strip() or "personal"
    effective: List[str] = [primary]
    for raw in (fields or []):
        fid = (raw or "").strip()
        if fid and fid not in effective:
            effective.append(fid)
    return effective


async def upsert_field_membership(
    conn: asyncpg.Connection,
    document_rid: str,
    source_meta: Dict[str, Any],
) -> str:
    """Record this document's membership in the run's learning field(s).

    Option B multi-field membership: chunk rows are content-addressed and
    field-agnostic (the chunk DELETE+reinsert-by-rid is harmless), so field
    membership lives in this separate, ADDITIVE, authoritative layer. Re-ingesting
    a doc into field B must NOT drop its field-A membership — hence INSERT ...
    ON CONFLICT DO NOTHING and NEVER a delete-by-rid here.

    Piece B (`--fields`): the effective set = dedup(union([group_id] + fields)).
    With no `--fields` this collapses to exactly `{group_id}` (unchanged). Each
    field is inserted additively; duplicates collapse via the dedup + ON CONFLICT.
    """
    effective = effective_field_membership(
        source_meta.get("group_id", "personal"), source_meta.get("fields"))
    for field_id in effective:
        await conn.execute(
            """
            INSERT INTO document_field_membership (document_rid, field_id, added_by)
            VALUES ($1, $2, 'ingest')
            ON CONFLICT DO NOTHING
            """,
            document_rid, field_id,
        )
    # Return the PRIMARY field (group_id) — stable, backward-compatible str
    # contract. The full effective set is `effective` (all rows were inserted).
    return effective[0]


# ── RAG core ─────────────────────────────────────────────────────────────────────

async def _chunk_and_embed(
    document_rid: str,
    markdown: str,
    embedder: OpenAIEmbeddingProvider,
    chunker: TextChunker,
) -> tuple[List[Dict[str, Any]], List[Optional[List[float]]], int]:
    """Chunk then embed, with no database involvement.

    Split out so the embedding round-trips happen OUTSIDE any held connection or
    open transaction — a long document is minutes of network I/O and holding a
    pooled connection across it is how a pool gets exhausted.
    """
    chunks = chunker.chunk_text(markdown)
    if not chunks:
        logger.warning("No chunks produced for %s", document_rid)
        return [], [], 0

    # Embed one at a time (batch calls can time out on long docs) — mirror doc_scanner.
    embeddings: List[Optional[List[float]]] = []
    null_embeds = 0
    for idx, chunk in enumerate(chunks):
        try:
            emb = await embedder.embed(chunk["text"])
            embeddings.append(emb)
        except Exception as e:  # noqa: BLE001 — match doc_scanner's null-on-fail policy
            logger.warning("Embedding failed for %s chunk %d: %s", document_rid, idx, e)
            embeddings.append(None)
            null_embeds += 1
    return chunks, embeddings, null_embeds


# ── Document history: the ingest-side pair (migration 123) ───────────────────────
#
# Spec: ~/.claude/plans/history-preserving-website-sensor-artifacts/q1_fixed_pair.md
# (CANONICAL) with CLAIM_CUTOVER_SQL from ingest_side_pair.md. History keys in
# source_meta are FLAT: `staged`, `history_journal_id`,
# `history_source_kind|_id|_url`, `history_policy`.

class StagedWriteViolation(RuntimeError):
    """A --staged ingest reached the claim cutover. /history/apply is the sole writer."""


class EmptyContentClaim(RuntimeError):
    """document_rid is content-addressed, so every empty extraction collapses onto ONE rid."""


class JournalSuccessorConflict(RuntimeError):
    """The journal row already names a DIFFERENT successor, or is no longer open."""


class StagedJournalMisuse(RuntimeError):
    """--history-journal-id on a non-staged ingest. The two travel together or not at all."""


class HistoryClaimRequired(RuntimeError):
    """--history-journal-id without the claim triple: the PATCH's 4th conjunct has no operands."""


class StagedTierMisuse(RuntimeError):
    """--staged with a tier deeper than rag: Stage 2 would run on a hidden successor."""


class HistoryPolicyMisuse(RuntimeError):
    """--history-policy on a run where nothing would compare it: staged, or no claim triple."""


JOURNAL_SUCCESSOR_PATCH_SQL = """
UPDATE document_supersession
   SET successor_rid = $2
 WHERE id = $1
   AND state IN ('pending','failed')
   AND (successor_rid IS NULL OR successor_rid = $2)
   AND (source_kind, source_id, source_url) = ($3, $4, $5)
RETURNING id, state, successor_rid
"""

# history_policy is absent from DO UPDATE SET: that absence IS the stickiness, and
# the RETURNING value is the enforcement point. claim_state, retired_at and
# released_at move together as literals (chk_dsc_state is a three-arm disjunction
# over exactly those columns, so any two-of-three write raises 23514).
CLAIM_CUTOVER_SQL = """
WITH prior AS (
    SELECT c.claim_state, c.retired_at, c.released_at, c.document_rid
      FROM document_source_claim c
     WHERE (c.source_kind, c.source_id, c.source_url) = ($1, $2, $3)
)
INSERT INTO document_source_claim
    (source_kind, source_id, source_url, document_rid, content_hash,
     history_policy, claim_state, retired_at, released_at, last_seen_at)
VALUES ($1, $2, $3, $4, $5, COALESCE($6,'keep'), 'live', NULL, NULL, now())
ON CONFLICT (source_kind, source_id, source_url) DO UPDATE
   SET document_rid = EXCLUDED.document_rid,
       content_hash = EXCLUDED.content_hash,
       last_seen_at = now(),
       claim_state  = 'live',
       retired_at   = NULL,
       released_at  = NULL
RETURNING history_policy, document_rid, claim_state, retired_at, released_at,
          (xmax = 0) AS created,
          (SELECT p.claim_state  FROM prior p) AS prior_claim_state,
          (SELECT p.document_rid FROM prior p) AS prior_rid
"""


async def _patch_journal_successor(conn, *, journal_id: int, document_rid: str,
                                   source_kind: str, source_id: str, source_url: str) -> str:
    """Fill in the successor the intent row was born without.

    The intent is written BEFORE this ingest spawns and `POST /history/intent`
    refuses a caller-supplied successor_rid, so this statement is the ONLY writer of
    successor_rid on a non-legacy edge.

    FOUR conjuncts, each required:
      id = $1                              -- the edge this run was told to fill
      state IN ('pending','failed')        -- an applied or refused edge is history
      successor_rid IS NULL OR = $2        -- idempotent on replay, a hard stop when
                                              the journal already names someone else
      (source_kind, source_id, source_url)
          = ($3, $4, $5)                   -- the edge must belong to THE PAGE THIS
                                              INGEST IS. `id` alone is a global key,
                                              and a wrong --history-journal-id
                                              otherwise patches another page's edge
                                              (measured: UPDATE 1).
    0 rows is never silence: a terminal edge, a conflicting successor and a foreign
    edge all need a human. A successor equal to the predecessor (the bytes did not
    change) trips chk_dsup_selfedge and is reported the same way.
    """
    try:
        row = await conn.fetchrow(JOURNAL_SUCCESSOR_PATCH_SQL, journal_id, document_rid,
                                  source_kind, source_id, source_url)
    except asyncpg.exceptions.CheckViolationError as e:
        if e.constraint_name != "chk_dsup_selfedge":
            raise
        raise JournalSuccessorConflict(
            f"SELF_SUPERSESSION: journal row {journal_id}'s predecessor is {document_rid}, "
            f"the rid this ingest produced, so the bytes did not change. Resolve the intent "
            f"with POST /history/resolve, never by re-running this ingest.") from e
    if row is None:
        raise JournalSuccessorConflict(
            f"journal row {journal_id} did not accept successor_rid={document_rid} for "
            f"({source_kind}, {source_id}, {source_url}): it is terminal (not "
            f"pending/failed), already names a different successor, or belongs to a "
            f"DIFFERENT page. Resolve with POST /history/resolve, never by re-running "
            f"this ingest.")
    return row["successor_rid"]


def _history_claim_triple(source_meta: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
    """This ingest's own claim triple, read FLAT. All three or none."""
    t = (source_meta.get("history_source_kind"),
         source_meta.get("history_source_id"),
         source_meta.get("history_source_url"))
    if all(t):
        return t
    if any(t):
        raise HistoryClaimRequired(
            f"--history-claim must supply all three of kind|id|url, got {t!r}")
    return None


def _assert_history_flags(*, staged: bool, journal_id: Optional[int],
                          claim: Optional[Tuple[str, str, str]],
                          policy: Optional[str] = None) -> None:
    """REFUSE, never imply.

    Making --history-journal-id imply --staged would silently change what the run
    writes for a caller that meant the non-staged path. Refusing costs one loud
    non-zero exit and writes nothing. The same rule covers the reverse pairing and a
    policy assertion that nothing would ever compare.
    """
    if policy is not None and (staged or claim is None):
        raise HistoryPolicyMisuse(
            f"--history-policy {policy!r} would never be checked: it is compared only by "
            f"the claim cutover, which runs on a non-staged ingest with --history-claim. "
            f"Drop --history-policy, or pass --history-claim without --staged.")
    if staged and journal_id is None:
        raise StagedJournalMisuse(
            "--staged was given without --history-journal-id. A staged row is born hidden "
            "and only POST /history/apply, through the journal edge this run fills, can "
            "reveal it; without one the row is hidden with no edge naming it. Pass "
            "--history-journal-id and --history-claim, or drop --staged.")
    if journal_id is None:
        return
    if not staged:
        raise StagedJournalMisuse(
            "--history-journal-id was given without --staged. A non-staged ingest that "
            "patches a journal edge ALSO advances the claim, so POST /history/apply then "
            "reads a claim that no longer names the predecessor and refuses STALE_INTENT "
            "— reveal-unsafe, so the successor stays hidden permanently and the "
            "predecessor is left live with no live claim (INV-1/I18). Pass --staged, or "
            "drop --history-journal-id.")
    if claim is None:
        raise HistoryClaimRequired(
            "--history-journal-id requires --history-claim: the PATCH is keyed on "
            "(id, source_kind, source_id, source_url) and without the triple a wrong "
            "journal id silently patches another page's edge.")


async def _claim_cutover(conn, *, staged: bool, markdown: str, source_kind: str,
                         source_id: str, source_url: str, document_rid: str,
                         content_hash: Optional[str], history_policy: Optional[str]):
    """Advance (or mint) this source's claim to the bytes this ingest just committed.

    `staged` is keyword-only and REQUIRED: any future call site must state which kind
    of ingest it is, and stating `True` is refused rather than obeyed.
    """
    if staged:
        raise StagedWriteViolation(
            f"--staged ingest tried to advance the claim for "
            f"({source_kind}, {source_id}, {source_url}) to {document_rid}. "
            f"POST /history/apply is the sole writer of a claim advance.")
    if not (markdown or "").strip():
        # Belt and braces, not the enforcement: both call sites return on
        # `if not chunks` before _write_document, and the chunker returns [] for
        # whitespace-only text, so markdown reaching here is non-empty.
        raise EmptyContentClaim(
            f"refusing to claim {document_rid}: converted markdown is empty, and the rid "
            f"is content-addressed, so every empty extraction in the corpus shares this "
            f"one rid")
    row = await conn.fetchrow(CLAIM_CUTOVER_SQL, source_kind, source_id, source_url,
                              document_rid, content_hash, history_policy)
    if history_policy and row["history_policy"] != history_policy:
        raise RuntimeError(
            f"POLICY_ASSERTION_MISMATCH: claim holds {row['history_policy']!r}, "
            f"this run asserted {history_policy!r}; history_policy is sticky and only "
            f"POST /history/policy may change it")
    if row["prior_claim_state"] in ("retired", "released"):
        logger.warning(
            "claim (%s,%s,%s) revived from %s by a fresh ingest: the source was observed "
            "serving content, so the claim is live again (prior rid %s)",
            source_kind, source_id, source_url, row["prior_claim_state"], row["prior_rid"])
    return row


async def _write_document(
    conn: asyncpg.Connection,
    document_rid: str,
    markdown: str,
    chunks: List[Dict[str, Any]],
    embeddings: List[Optional[List[float]]],
    source_meta: Dict[str, Any],
) -> int:
    """Transactionally upsert the parent row, the chunk set, field membership, and
    — when this ingest is under document-history management — the ingest-side pair.

    The pair is INSIDE the existing transaction on purpose: a claim naming bytes that
    were not committed, or a journal edge naming a successor that does not exist, is
    the invisible inconsistency the write-ahead journal exists to prevent. A
    JournalSuccessorConflict therefore discards this ingest's bytes, chunks and field
    membership as well.

    Not reached at all when both callers return early (`if not chunks`, `if dry_run`):
    a history-managed run that takes either exits 0 reporting `chunks_written: 0` and
    leaves the journal row `pending` with `successor_rid` NULL, which /history/apply
    refuses as SUCCESSOR_NOT_CHUNKED (retryable).
    """
    staged     = bool(source_meta.get("staged"))
    journal_id = source_meta.get("history_journal_id")
    claim      = _history_claim_triple(source_meta)

    # Restated here as well as at flag-parse time, because the koi-net federation
    # handler (via ingest_document_rag_conn) and any direct importer of
    # ingest_document_rag or _write_document build their own source_meta and never
    # pass through ingest_path's validation.
    _assert_history_flags(staged=staged, journal_id=journal_id, claim=claim,
                          policy=source_meta.get("history_policy"))

    async with conn.transaction():
        await upsert_document_memory(conn, document_rid, markdown, source_meta)
        written = await upsert_document_chunks(conn, document_rid, chunks, embeddings, source_meta)
        # Additive field membership (Option B): never deletes other fields' rows.
        await upsert_field_membership(conn, document_rid, source_meta)

        # (1) journal successor PATCH — only a --staged run may carry a journal id.
        if journal_id is not None:
            await _patch_journal_successor(
                conn, journal_id=int(journal_id), document_rid=document_rid,
                source_kind=claim[0], source_id=claim[1], source_url=claim[2])

        # (2) claim cutover — NEVER when staged.
        if staged:
            hidden = await conn.fetchval(
                "SELECT superseded_at IS NOT NULL FROM koi_memories WHERE rid = $1",
                document_rid)
            if hidden:
                logger.info(
                    "--staged %s: hidden, journal successor PATCH written; claim advance "
                    "is POST /history/apply's to make, not this ingest's", document_rid)
            else:
                # The rid already existed live (bytes shared with another claim), and a
                # re-ingest never hides a live row. Apply will refuse SUCCESSOR_NOT_STAGED.
                logger.warning(
                    "--staged %s: NOT hidden — this rid was already live (shared bytes), "
                    "so POST /history/apply will refuse SUCCESSOR_NOT_STAGED for journal "
                    "row %s", document_rid, journal_id)
        elif claim is not None:
            await _claim_cutover(
                conn, staged=staged, markdown=markdown, document_rid=document_rid,
                content_hash=source_meta.get("content_hash"),
                source_kind=claim[0], source_id=claim[1], source_url=claim[2],
                history_policy=source_meta.get("history_policy"))
    return written


def _rag_result(document_rid: str, written: int, total: int, null_embeds: int) -> Dict[str, Any]:
    return {
        "document_rid": document_rid,
        "chunks_written": written,
        "chunks_total": total,
        "null_embeds": null_embeds,
    }


async def ingest_document_rag_conn(
    conn: asyncpg.Connection,
    *,
    document_rid: str,
    markdown: str,
    source_meta: Dict[str, Any],
    embedder: OpenAIEmbeddingProvider,
    chunker: TextChunker,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """`ingest_document_rag` against an already-acquired connection.

    Same contract and same return shape; exists for callers that are handed a
    connection rather than a pool — notably the koi-net document federation
    handler, which runs inside the poller's `pool.acquire()` block and must not
    open a second connection to write.
    """
    chunks, embeddings, null_embeds = await _chunk_and_embed(
        document_rid, markdown, embedder, chunker)
    if not chunks:
        return _rag_result(document_rid, 0, 0, 0)
    if dry_run:
        logger.info("DRY-RUN: %s → %d chunks (no DB write)", document_rid, len(chunks))
        return _rag_result(document_rid, 0, len(chunks), 0)

    written = await _write_document(
        conn, document_rid, markdown, chunks, embeddings, source_meta)
    logger.info(
        "Ingested %s: %d chunks written (%d null embeds)",
        document_rid, written, null_embeds,
    )
    return _rag_result(document_rid, written, len(chunks), null_embeds)


async def ingest_document_rag(
    pool: asyncpg.Pool,
    *,
    document_rid: str,
    markdown: str,
    source_meta: Dict[str, Any],
    embedder: OpenAIEmbeddingProvider,
    chunker: TextChunker,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Chunk + embed `markdown` and upsert koi_memories + koi_memory_chunks.

    Returns {document_rid, chunks_written, chunks_total, null_embeds}.
    This is the `--tier rag` core; higher tiers compose on top of it.
    """
    chunks, embeddings, null_embeds = await _chunk_and_embed(
        document_rid, markdown, embedder, chunker)
    if not chunks:
        return _rag_result(document_rid, 0, 0, 0)
    if dry_run:
        logger.info("DRY-RUN: %s → %d chunks (no DB write)", document_rid, len(chunks))
        return _rag_result(document_rid, 0, len(chunks), 0)

    async with pool.acquire() as conn:
        written = await _write_document(
            conn, document_rid, markdown, chunks, embeddings, source_meta)

    logger.info(
        "Ingested %s: %d chunks written (%d null embeds)",
        document_rid, written, null_embeds,
    )
    return _rag_result(document_rid, written, len(chunks), null_embeds)


# ── Claims + extractor composition (Phase 2 orchestration) ───────────────────────

def _claims_service_token() -> Optional[str]:
    """Token for the auth-gated /claims/ write path — env first, then the koi-state file."""
    tok = os.getenv("KOI_CLAIMS_SERVICE_TOKEN")
    if tok:
        return tok.strip()
    p = Path.home() / ".config/personal-koi/koi-state/claims_service_token"
    if p.exists():
        return p.read_text().strip()
    return None


# /claims/extract caps its input at ~10K chars (api/claim_extractor.py), so a single
# call on a long document only sees the intro. Window the text so the WHOLE document
# is covered; server-side content_hash dedup collapses overlap. Windows run with
# bounded concurrency. Auth failures (401/403) are raised LOUDLY — a silent auth fail
# is the 0-claims failure mode that bit project_bridge_notes.py; a per-window blip is
# logged and counted 0 so one transient error never kills the claims stage.
CLAIMS_WINDOW_CHARS = int(os.getenv("DOC_CLAIMS_WINDOW_CHARS", "9000"))
CLAIMS_WINDOW_OVERLAP = int(os.getenv("DOC_CLAIMS_WINDOW_OVERLAP", "500"))
CLAIMS_MAX_CONCURRENCY = int(os.getenv("DOC_CLAIMS_MAX_CONCURRENCY", "4"))


async def extract_document_claims(
    http: httpx.AsyncClient, *, document_text: str, source_document: str,
    confidence_threshold: float = 0.7,
) -> Dict[str, Any]:
    """Extract impact claims over the FULL document via windowed POSTs to
    /claims/extract (auth-gated). The endpoint truncates to ~10K chars internally, so
    we window the text ourselves (overlap + server content_hash dedup) — otherwise only
    the first 10K (the intro) is ever seen. The completion gate asserts claims_created
    >= 1 for standard/thorough; the windowing is what makes that count reflect the
    whole document."""
    token = _claims_service_token()
    if not token:
        raise RuntimeError(
            "KOI_CLAIMS_SERVICE_TOKEN not found (env or "
            "~/.config/personal-koi/koi-state/claims_service_token) — /claims/extract is auth-gated.")
    headers = {"Authorization": f"Bearer {token}"}

    stride = max(1, CLAIMS_WINDOW_CHARS - CLAIMS_WINDOW_OVERLAP)
    windows = [document_text[i:i + CLAIMS_WINDOW_CHARS]
               for i in range(0, len(document_text), stride)] or [document_text]
    sem = asyncio.Semaphore(CLAIMS_MAX_CONCURRENCY)

    async def _one(chunk: str) -> int:
        async with sem:
            payload = {"document_text": chunk, "source_document": source_document,
                       "auto_create": True, "confidence_threshold": confidence_threshold}
            try:
                r = await http.post(f"{KOI_BASE_URL}/claims/extract", json=payload,
                                    headers=headers, timeout=240.0)
            except Exception as e:  # noqa: BLE001 — a transient blip on one window != stage failure
                logger.warning("claims window error (counted 0): %s", e)
                return 0
            if r.status_code in (401, 403):
                raise RuntimeError(
                    f"/claims/extract auth failed ({r.status_code}); token rejected. NOT swallowing — "
                    f"a silent {r.status_code} yields 0 claims with no error.")
            if r.status_code >= 400:
                logger.warning("claims window non-2xx %s (counted 0): %s", r.status_code, r.text[:120])
                return 0
            data = r.json()
            created = data.get("auto_created")
            if created is None:
                created = data.get("claims_created", [])
            return len(created) if isinstance(created, list) else int(created or 0)

    counts = await asyncio.gather(*(_one(w) for w in windows))
    return {"claims_created": sum(counts), "claims_windows": len(windows)}


def _load_extractor():
    """Import the Phase-1 document extractor module by path (sibling script)."""
    import importlib.util
    p = Path(__file__).parent / "extract_deep_documents.py"
    spec = importlib.util.spec_from_file_location("extract_deep_documents", str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_gate_evidence(result: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten the nested ingest result into the gate's response_summary — the flat
    set of counts the document-ingest completion gate asserts on. `embeds_ok`/`dups_ok`
    are pre-derived booleans (1=ok) so the gate can express the "must be zero" checks
    as plain floors (a gate evaluator with only >= is enough)."""
    rag = result.get("rag") or {}
    ext = result.get("extract") or {}
    cl = result.get("claims") or {}
    rag_null = int(rag.get("null_embeds") or 0)
    facts_null = int(ext.get("facts_null_embed") or 0)
    dups = int(ext.get("facts_dup_removed") or 0)
    ent_created = int(ext.get("entities_created") or 0)
    ent_resolved = int(ext.get("entities_resolved") or 0)
    return {
        "tier": result.get("tier"),
        "document_rid": result.get("document_rid"),
        "chunks_written": int(rag.get("chunks_written") or 0),
        "chunks_total": int(rag.get("chunks_total") or 0),
        "rag_null_embeds": rag_null,
        "facts_created": int(ext.get("facts_created") or 0),
        "facts_skipped": int(ext.get("facts_skipped") or 0),
        "facts_dup_removed": dups,
        "semantic_dups_retracted": int(ext.get("semantic_dups_retracted") or 0),
        "facts_null_embed": facts_null,
        "entities_created": ent_created,
        "entities_resolved": ent_resolved,
        "entities_total": ent_created + ent_resolved,
        "entity_links": int(ext.get("entity_links") or 0),
        "type_mismatches": int(ext.get("type_mismatches") or 0),
        "discourse_moves_created": int(ext.get("discourse_moves_created") or 0),
        "claims_created": int(cl.get("claims_created") or 0),
        # DERIVED keys the document-ingest gate actually asserts on. Added 2026-07-31:
        # phase_expectations.yaml floors on `facts_available` and `claims_available`, but
        # this function never emitted them, so the gate reported "key ABSENT from evidence"
        # and exited 2 for EVERY standard/thorough ingest — including runs that landed
        # hundreds of facts and discourse moves with zero null embeds. The gate was
        # effectively dead above `rag` tier. Derivations follow the catalog's own comments:
        #   facts_available  = "facts created or intentionally skipped as known duplicates"
        #   claims_available = "impact claims or document-argument claims" (discourse moves
        #                      ARE the document-argument taxonomy, per migrations 103/104)
        # Purely additive — no existing key changes — so nothing downstream can regress.
        # 1 = the whole document was windowed; 0 = an explicit DOC_MAX_WINDOWS cap cut the
        # tail off. Truncation previously passed every floor, so a half-ingested book could
        # be reported complete. Gate floors on this.
        "not_truncated": 0 if ext.get("budget_exhausted") else 1,
        # 1 = every window extracted; 0 = one or more were dead-lettered (#40). A
        # document that lost windows must not pass as complete.
        "all_windows_ok": 0 if int(ext.get("windows_failed") or 0) > 0 else 1,
        "windows_failed": int(ext.get("windows_failed") or 0),
        "facts_available": int(ext.get("facts_created") or 0) + int(ext.get("facts_skipped") or 0),
        "claims_available": int(cl.get("claims_created") or 0)
                            + int(ext.get("discourse_moves_created") or 0),
        "embeds_ok": 1 if (rag_null == 0 and facts_null == 0) else 0,
        "dups_ok": 1 if dups == 0 else 0,
        # End-state invariant (the right "0 residual dups" gate floor): 1 = no duplicate
        # triples remain after the sweeps; 0 only if a sweep was skipped/broken.
        "no_residual_dups": int(ext.get("no_residual_dups") if ext.get("no_residual_dups") is not None else 1),
        # ── Identity contract (#62) ──────────────────────────────────────────
        # The floor the old gate never had. A green structural result said every
        # stage ran; it said nothing about whether the entities those stages wrote
        # are the ones the payload meant. The audited Buehler import passed strict
        # with 11 fact endpoints bound to the wrong entity.
        #
        # 1 = a payload-endpoint map was frozen AND every persisted subject/object
        # was verified against it. 0 = not verified, which includes warn mode, off
        # mode, and rag tier (no facts to bind). The gate floors this at standard
        # and thorough only — see phase_expectations.yaml.
        "identity_verified": int(ext.get("identity_verified") or 0),
        "identity_mode": ext.get("identity_mode"),
        "identity_endpoints": int(ext.get("identity_endpoints") or 0),
        # DISTINCT-ENDPOINT CARDINALITY (#62 acceptance). Equal values mean the
        # bijection held: N distinct typed payload endpoints became N distinct
        # identities. The gate floors on the derived boolean because its evaluator
        # supports only `>=`, but both operands are emitted so a reader can see
        # WHICH side shrank rather than only that something did.
        "identity_distinct_uris": int(ext.get("identity_distinct_uris") or 0),
        "identity_bijective": 1 if (
            int(ext.get("identity_endpoints") or 0)
            == int(ext.get("identity_distinct_uris") or 0)
        ) else 0,
        "endpoints_pinned": int(ext.get("endpoints_pinned") or 0),
        # ── Producer provenance (#64) ────────────────────────────────────────
        # 1 = every window this run produced recorded which provider/model made it.
        # `providers`/`models` are lists, not one value: a run that fell back
        # through transports legitimately has more than one producer, and
        # flattening that to "the model" is the misattribution #64 is about.
        "producers_recorded": int(ext.get("producers_recorded") or 0),
        "providers": ext.get("providers") or [],
        "models": ext.get("models") or [],
        "transports": ext.get("transports") or [],
        # ── Semantic quality (#64), kept STRUCTURALLY SEPARATE ───────────────
        # Never folded into the structural keys above. 'not_evaluated' is its own
        # state and is not a pass: the original Buehler run was structurally
        # complete with 25 facts where a curated re-run of the same document found
        # 213, and a single blended status is how that stops being visible.
        #
        # Read from `ext`, like every sibling key: the extractor returns `quality`
        # inside the dict stored under result["extract"]. This read used to look at
        # result["quality"], which nothing writes, so the gate saw 'not_evaluated' /
        # 0 on every run regardless of the verdict (review finding M2).
        "semantic_status": (ext.get("quality") or {}).get("status", "not_evaluated"),
        "semantic_ok": 1 if (ext.get("quality") or {}).get("status") == "pass" else 0,
    }


def _history_source_meta(*, staged: bool, journal_id: Optional[int],
                         claim: Optional[str], policy: Optional[str]) -> Dict[str, Any]:
    """Flatten the four --history flags into source_meta and refuse the two misuses
    BEFORE any embedding spend or DB work.

    Absent every flag this returns {} and source_meta is byte-identical to today's,
    which keeps every ingest_path caller (the CLI, the documents router, the
    research-paper wrapper) unchanged. The federation handler never reaches here.
    """
    if not (staged or journal_id is not None or claim or policy):
        return {}
    meta: Dict[str, Any] = {"staged": bool(staged)}
    if journal_id is not None:
        meta["history_journal_id"] = int(journal_id)
    if policy:
        meta["history_policy"] = policy
    if claim:
        parts = claim.split("|")
        if len(parts) != 3 or not all(p.strip() for p in parts):
            raise ValueError(
                f"--history-claim must be 'source_kind|source_id|source_url', got {claim!r}")
        (meta["history_source_kind"], meta["history_source_id"],
         meta["history_source_url"]) = (p.strip() for p in parts)
    _assert_history_flags(staged=meta["staged"],
                          journal_id=meta.get("history_journal_id"),
                          claim=_history_claim_triple(meta),
                          policy=meta.get("history_policy"))
    return meta


async def ingest_path(
    *,
    source_path: str,
    tier: str,
    slug: Optional[str],
    name: Optional[str],
    source_url: Optional[str],
    retrieval_method: Optional[str],
    group_id: str,
    fields: Optional[List[str]] = None,
    claims: bool = True,
    force: bool = False,
    dry_run: bool = False,
    staged: bool = False,
    history_journal_id: Optional[int] = None,
    history_claim: Optional[str] = None,
    history_policy: Optional[str] = None,
) -> Dict[str, Any]:
    """Unified document-ingest orchestrator: rag -> (standard/thorough) extract -> claims.

    Depth dial: rag = chunk+embed only; standard = rag + facts + claims;
    thorough = standard + discourse (Phase 3). One content-addressed document_rid
    threads every stage; each stage is idempotent.
    """
    if tier not in VALID_TIERS:
        raise ValueError(f"--tier must be one of {VALID_TIERS}, got {tier!r}")
    if staged and tier != "rag":
        # A staged successor is hidden until POST /history/apply reveals it. A deeper
        # tier would run Stage 2 and Stage 3 on it before the swap, leaving facts and
        # claims live for a document a terminal refusal keeps hidden for good.
        raise StagedTierMisuse(
            f"--staged requires --tier rag (got {tier!r}). Stage with --tier rag, apply, "
            f"then re-ingest at the real tier.")

    path = resolve_allowed_source_path(source_path)
    markdown = path.read_text(encoding="utf-8", errors="replace")
    if not markdown.strip():
        raise ValueError(f"source file is empty: {path}")

    content_hash, document_rid = compute_document_rid(markdown)
    source_meta = {
        "slug": slug or path.stem, "name": name, "source_url": source_url,
        "retrieval_method": retrieval_method,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "content_hash": content_hash, "group_id": group_id,
        "fields": list(fields) if fields else [],
        "source_path": str(path), "tier": tier,
        # document-history keys, FLAT, following this file's own convention:
        **_history_source_meta(staged=staged, journal_id=history_journal_id,
                               claim=history_claim, policy=history_policy),
    }
    source_document = source_url or document_rid

    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY not set — required for document-ingest (OpenAI 3072-dim). "
            "Source config/personal.env or export the key.")
    embedder = OpenAIEmbeddingProvider(api_key=OPENAI_API_KEY, model=EMBEDDING_MODEL,
                                       dimension=EMBEDDING_DIMENSION)
    chunker = TextChunker(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)

    result: Dict[str, Any] = {"document_rid": document_rid, "slug": source_meta["slug"],
                              "content_hash": content_hash, "char_count": len(markdown), "tier": tier}
    pool = await asyncpg.create_pool(POSTGRES_URL, min_size=1, max_size=3)
    try:
        # Stage 1 — RAG chunk+embed (all tiers).
        result["rag"] = await ingest_document_rag(
            pool, document_rid=document_rid, markdown=markdown, source_meta=source_meta,
            embedder=embedder, chunker=chunker, dry_run=dry_run)
        if dry_run or tier == "rag":
            result["gate_evidence"] = build_gate_evidence(result)
            return result

        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        # #36: the pool settings that actually fix CLOSE_WAIT reuse (keepalive_expiry
        # + TCP keepalive) can only come from the CLIENT — a per-request `timeout=`
        # scalar does not reliably fire on a half-closed pooled socket (observed: a
        # 40-minute hang against timeout=300). Per-request overrides below still
        # apply on top of these per-phase ceilings.
        async with provider_async_client(read=900.0) as http:
            # Stage 2 — deep-extract entities + facts (standard/thorough).
            edd = _load_extractor()
            result["extract"] = await edd.extract_deep_document(
                pool, http, document_rid=document_rid, tier=tier,
                group_id=group_id, run_id=run_id, force=force)
            # Stage 3 — impact claims (auth-gated; surfaced loudly, never swallowed).
            if claims:
                result["claims"] = await extract_document_claims(
                    http, document_text=markdown, source_document=source_document)
    finally:
        await pool.close()
    result["gate_evidence"] = build_gate_evidence(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-path", required=True, help="Path to the converted markdown file")
    parser.add_argument("--tier", default="standard", choices=VALID_TIERS,
                        help="Depth dial: rag | standard (rag+facts+claims) | thorough (+discourse, Phase 3)")
    parser.add_argument("--slug", help="Stable slug (default: file stem)")
    parser.add_argument("--name", help="Human-readable document title")
    parser.add_argument("--source-url", help="Canonical source URL (provenance)")
    parser.add_argument("--retrieval-method", help="How it was acquired (download.php|playwright|operator-drop)")
    parser.add_argument("--group-id", default="personal", help="Learning field / group_id (always the primary membership)")
    parser.add_argument("--fields",
                        help="Comma-separated ADDITIONAL field IDs for multi-field membership "
                             "(Option B). Effective membership = dedup(union of --group-id + these). "
                             "--group-id is always primary; omit to keep single-membership behavior. "
                             "Unknown field IDs are valid (fields are created-by-use).")
    parser.add_argument("--no-claims", action="store_true", help="Skip the claims stage (standard/thorough)")
    parser.add_argument("--force", action="store_true", help="Re-extract all windows (ignore cache)")
    parser.add_argument("--dry-run", action="store_true", help="Chunk + report without writing/embedding")
    parser.add_argument("--gate-evidence-out",
                        help="Write the flat gate-evidence JSON (response_summary) to this path for the gate")
    parser.add_argument("--staged", action="store_true",
                        help="Document-history staging: the successor is born with "
                             "superseded_at set (invisible) and POST /history/apply reveals "
                             "it. Travels with --history-journal-id (each requires the "
                             "other) and requires --tier rag.")
    parser.add_argument("--history-journal-id", type=int,
                        help="document_supersession.id whose successor_rid this run fills "
                             "in. Implies nothing: it REQUIRES --staged and --history-claim.")
    parser.add_argument("--history-claim", metavar="KIND|ID|URL",
                        help="This ingest's own claim triple, 'source_kind|source_id|"
                             "source_url'. Keys BOTH the journal PATCH and the cutover.")
    parser.add_argument("--history-policy", choices=("keep", "drop"),
                        help="Asserted retention policy, non-staged runs with "
                             "--history-claim only. Stored once, when this ingest mints the "
                             "source's first claim (default keep); never updated by an ingest "
                             "after that. On an existing claim it is compared with the sticky "
                             "value and refused on mismatch.")
    args = parser.parse_args()

    claims = (args.tier != "rag") and not args.no_claims
    fields = [t.strip() for t in (args.fields or "").split(",") if t.strip()]
    try:
        result = asyncio.run(ingest_path(
            source_path=args.source_path, tier=args.tier, slug=args.slug, name=args.name,
            source_url=args.source_url, retrieval_method=args.retrieval_method,
            group_id=args.group_id, fields=fields, claims=claims, force=args.force, dry_run=args.dry_run,
            staged=args.staged, history_journal_id=args.history_journal_id,
            history_claim=args.history_claim, history_policy=args.history_policy,
        ))
    except (ValueError, NotImplementedError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    rag = result.get("rag", {})
    ext = result.get("extract") or {}
    cl = result.get("claims") or {}
    print("\nDocument ingest complete:")
    print(f"  document_rid:   {result['document_rid']}")
    print(f"  slug / tier:    {result.get('slug')} / {result.get('tier')}")
    print(f"  char_count:     {result.get('char_count')}")
    print(f"  rag chunks:     {rag.get('chunks_written')} written / {rag.get('null_embeds')} null-embed")
    if ext:
        print(f"  facts_created:  {ext.get('facts_created')} (skipped {ext.get('facts_skipped')}, "
              f"dup_removed {ext.get('facts_dup_removed')})")
        print(f"  entities:       {ext.get('entities_created')} created / {ext.get('entities_resolved')} resolved")
        print(f"  type_mismatch:  {ext.get('type_mismatches')}  | windows {ext.get('windows_processed')}/{ext.get('windows_total')}")
        if result.get("tier") == "thorough":
            print(f"  discourse_moves:{ext.get('discourse_moves_created')}")
    if cl:
        print(f"  claims_created: {cl.get('claims_created')}")
    if args.gate_evidence_out:
        ev = result.get("gate_evidence") or build_gate_evidence(result)
        Path(args.gate_evidence_out).write_text(json.dumps(ev, indent=2))
        print(f"  gate-evidence:  {args.gate_evidence_out}")


if __name__ == "__main__":
    main()
