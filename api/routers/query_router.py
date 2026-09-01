"""Dynamic read-only query endpoint for the personal knowledge graph.

Allows Claude (via MCP) to execute arbitrary SELECT queries against a
whitelisted subset of tables.  Defence-in-depth: client-side pre-validation
in the MCP handler, plus server-side guardrails here.

Routes are prefix-relative — prefix "/sql" is applied at mount time.
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Table whitelist — only these tables can appear after FROM/JOIN/INTO
# ---------------------------------------------------------------------------
ALLOWED_TABLES = frozenset({
    # Core data
    "entity_registry",
    "entity_relationships",
    "task_registry",
    # Derived knowledge graph. Omitted until 2026-08-26, which meant the /sql
    # escape hatch could find an entity but never its facts — 59,343 rows that
    # every other surface (search_facts, /knowledge/facts/search) already
    # served. Read-only exposure here is the same data, joinable.
    "knowledge_facts",
    "knowledge_episodes",
    "document_entity_links",
    "claims",
    "claim_attestations",
    # Content
    "koi_memories",
    "koi_memory_chunks",
    "commitments",
    "commitment_pools",
    # External
    "email_metadata",
    # MediaWiki (p2pfoundation wiki live-sync + any future wikis)
    "mediawiki_wikis",
    "mediawiki_page_state",
    "mediawiki_page_links",
    "mediawiki_import_runs",
    # MediaWiki views
    "v_mediawiki_page_resolved",
})

# Dangerous SQL keywords (word-boundary matched, case-insensitive)
_DANGEROUS_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|COPY)\b",
    re.IGNORECASE,
)

# Catalog / information_schema access, and server-side file/large-object
# functions.
#
# The trailing \b was a bug (found 2026-08-26): \bpg_\b requires a non-word
# character after the underscore, so it matched only a bare `pg_` token and
# NOT `pg_class`, `pg_read_file`, or `pg_ls_dir`. Table position was still
# covered by the allowlist guard, but a bare function call has no FROM clause
# to catch — `SELECT pg_read_file($1)` passed every guard. Matching `pg_` as a
# PREFIX closes that. Verified no allowlisted table has a pg_-prefixed column,
# so this costs no legitimate query.
#
# This is a blocklist and blocklists leak. The durable fix is to stop
# connecting as a superuser; see the read-only-role task.
_CATALOG_ACCESS = re.compile(
    r"\b(pg_|information_schema\b|lo_import\b|lo_export\b|dblink\b)",
    re.IGNORECASE,
)

# Extract table identifiers after FROM / JOIN / INTO
_TABLE_REF = re.compile(
    r"\b(?:FROM|JOIN|INTO)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)

# Single-quoted string literals (not dollar-quotes)
_INLINE_LITERAL = re.compile(r"'[^']*'")

# Strip leading SQL line comments
_LEADING_COMMENTS = re.compile(r"^(\s*--[^\n]*\n)*", re.MULTILINE)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    sql: str = Field(..., description="SELECT or WITH query with $1,$2 param placeholders")
    params: List[Any] = Field(default_factory=list, description="Parameter values")
    limit: int = Field(default=200, ge=1, le=1000, description="Max rows returned")


class QueryResponse(BaseModel):
    columns: List[str]
    rows: List[List[Any]]
    row_count: int
    truncated: bool


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_router(pool, caps=None) -> APIRouter:
    """Return an APIRouter for the dynamic query endpoint.

    Parameters
    ----------
    pool : asyncpg.Pool
        Database connection pool.
    caps : Capabilities (optional)
        Runtime capabilities object.
    """
    router = APIRouter(tags=["query"])

    @router.post("", response_model=QueryResponse)
    async def execute_query(req: QueryRequest):
        sql = req.sql

        # --- Guard 1: Single-statement enforcement ---
        sql = sql.rstrip().rstrip(";").rstrip()
        if ";" in sql:
            raise HTTPException(400, "Only single statements allowed")

        # --- Guard 2: Must start with SELECT or WITH ---
        cleaned = _LEADING_COMMENTS.sub("", sql).strip()
        if not re.match(r"^(SELECT|WITH)\s", cleaned, re.IGNORECASE):
            raise HTTPException(400, "Only SELECT or WITH queries allowed")

        # --- Guard 3: Table whitelist ---
        referenced_tables = {m.group(1).lower() for m in _TABLE_REF.finditer(sql)}
        # Also allow subquery aliases (single-letter or _q patterns) — they aren't real tables
        disallowed = referenced_tables - ALLOWED_TABLES - {"_q"}
        if disallowed:
            raise HTTPException(
                400,
                f"Table(s) not allowed: {', '.join(sorted(disallowed))}. "
                f"Allowed: {', '.join(sorted(ALLOWED_TABLES))}",
            )

        # --- Guard 4: Reject dangerous keywords ---
        if _DANGEROUS_KEYWORDS.search(sql):
            raise HTTPException(400, "Query contains disallowed keyword")

        # --- Guard 5: Reject catalog access ---
        if _CATALOG_ACCESS.search(sql):
            raise HTTPException(400, "Catalog/system table access not allowed")

        # --- Guard 6: Parameterization enforcement ---
        if _INLINE_LITERAL.search(sql):
            raise HTTPException(
                400,
                "Use $1, $2 parameters instead of inline string literals",
            )

        # --- Execute with safety constraints ---
        limit = min(req.limit, 1000)
        wrapped_sql = f"SELECT * FROM ({sql}) AS _q LIMIT {limit}"

        start_ms = time.monotonic()
        try:
            async with pool.acquire() as conn:
                async with conn.transaction(readonly=True):
                    await conn.execute("SET LOCAL statement_timeout = '5s'")
                    stmt = await conn.prepare(wrapped_sql)
                    records = await stmt.fetch(*req.params)
                    columns = [attr.name for attr in stmt.get_attributes()]
        except Exception as e:
            error_msg = str(e)
            logger.warning("Query execution failed: %s | SQL: %s", error_msg, sql[:200])
            raise HTTPException(400, f"Query execution error: {error_msg}")

        elapsed_ms = (time.monotonic() - start_ms) * 1000

        # Convert records to lists
        rows = [list(r.values()) for r in records]
        row_count = len(rows)

        # --- Guard 9: Max result size (100KB) ---
        truncated = False
        result = QueryResponse(
            columns=columns,
            rows=rows,
            row_count=row_count,
            truncated=False,
        )
        serialized = result.model_dump_json()
        if len(serialized) > 100_000:
            # Trim rows until under 100KB
            while rows and len(result.model_dump_json()) > 100_000:
                rows.pop()
            truncated = True
            result = QueryResponse(
                columns=columns,
                rows=rows,
                row_count=len(rows),
                truncated=True,
            )

        # --- Query logging (zero PII — only SQL template, not params) ---
        extracted_tables = sorted(referenced_tables & ALLOWED_TABLES)
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO koi_query_log
                       (query_text, agent_id, confidence_score,
                        total_results, execution_time_ms, metadata)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    sql,
                    "dobby",
                    1.0,
                    row_count,
                    elapsed_ms,
                    json.dumps({
                        "source_type": "mcp",
                        "params_count": len(req.params),
                        "tables_accessed": extracted_tables,
                    }),
                )
        except Exception as e:
            logger.warning("Query logging failed (non-fatal): %s", e)

        return result

    return router
