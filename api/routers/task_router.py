"""Task registry endpoints (ingest, list, update, stats).

Provides dedicated task storage to replace the fragmented /register-entity approach.
All task writers (meeting-notes, task-agent, /tasks skill) converge here.

Routes are prefix-relative — prefix "/tasks" is applied at mount in personal_ingest_api.py.
"""

import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class TaskIngestRequest(BaseModel):
    taskKey: str = Field(..., description="Idempotency key (e.g. 'meeting-2026-02-27-slug')")
    uuid: Optional[str] = None
    title: str
    # Fix 2: status/priority default to None so partial payloads don't regress
    # existing values. INSERT uses COALESCE($, 'inbox') for new rows.
    status: Optional[str] = None
    priority: Optional[str] = None
    dueDate: Optional[str] = None       # ISO date string YYYY-MM-DD
    startDate: Optional[str] = None
    waitUntil: Optional[str] = None
    context: Optional[str] = None
    effort: Optional[str] = None
    ownerWikilink: Optional[str] = None        # "[[People/Name|alias]]" or "Name"
    projectWikilink: Optional[str] = None      # "[[Projects/Name]]" or plain text
    collaboratorWikilinks: Optional[List[str]] = []
    blockedBy: Optional[List[str]] = []
    sourceNote: Optional[str] = None
    sourceType: Optional[str] = None
    vaultPath: Optional[str] = None
    tags: Optional[List[str]] = []


class TaskIngestResponse(BaseModel):
    task_key: str
    action: str    # "created" or "updated"
    id: int


class TaskRecord(BaseModel):
    id: int
    task_key: str
    uuid: Optional[str] = None
    title: str
    status: str
    priority: Optional[str] = None
    due_date: Optional[str] = None
    start_date: Optional[str] = None
    wait_until: Optional[str] = None
    context: Optional[str] = None
    effort: Optional[str] = None
    owner_uri: Optional[str] = None
    project_uri: Optional[str] = None
    collaborator_uris: List[str] = []
    blocked_by: List[str] = []
    source_note: Optional[str] = None
    source_type: Optional[str] = None
    vault_path: Optional[str] = None
    tags: List[str] = []
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None
    started_at: Optional[str] = None
    triaged_at: Optional[str] = None


class TaskPatchRequest(BaseModel):
    # Fix 3: all fields default to None; model_fields_set distinguishes
    # "not provided" from "explicitly null" for date clearing.
    status: Optional[str] = None
    priority: Optional[str] = None
    dueDate: Optional[str] = None
    startDate: Optional[str] = None
    waitUntil: Optional[str] = None
    context: Optional[str] = None
    effort: Optional[str] = None
    ownerWikilink: Optional[str] = None
    projectWikilink: Optional[str] = None
    collaboratorWikilinks: Optional[List[str]] = None
    blockedBy: Optional[List[str]] = None
    tags: Optional[List[str]] = None


class TaskStatsResponse(BaseModel):
    total_open: int
    total_done: int
    by_status: Dict[str, int]
    overdue: int
    due_today: int
    due_this_week: int


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_router(pool, caps) -> APIRouter:
    """Return an APIRouter for task registry endpoints.

    Parameters
    ----------
    pool : asyncpg.Pool
        Database connection pool.
    caps : Capabilities
        Runtime capabilities object (unused for tasks, included for consistency).
    """
    router = APIRouter(tags=["tasks"])

    from api.federation_events import emit_domain_event

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _parse_wikilink_path(wikilink: str) -> Optional[str]:
        """Extract vault path from a wikilink like [[Folder/Name|alias]].

        Returns the path portion (e.g. "People/David Fortson"), or None if
        the input is not a wikilink format.
        """
        m = re.match(r'^\[\[([^\]|]+)(?:\|[^\]]+)?\]\]$', wikilink.strip())
        return m.group(1) if m else None

    async def _resolve_entity(conn, raw: str, entity_type: str) -> Optional[str]:
        """Resolve a wikilink or plain name to a canonical_uri.

        Attempts:
        1. Wikilink vault path lookup in entity_rid_mappings (exact, case-insensitive)
        2. Plain text name lookup in entity_rid_mappings (case-insensitive)
        3. Normalized text lookup in entity_registry (case-insensitive)

        entity_type hint prevents cross-type collisions (e.g. a Project named
        "IndigenomicsAI" won't resolve as a Person).
        """
        if not raw or not raw.strip():
            return None

        raw = raw.strip()
        vault_path = _parse_wikilink_path(raw)

        if vault_path:
            # Tier 1: wikilink vault path lookup
            row = await conn.fetchrow(
                """
                SELECT canonical_uri FROM entity_rid_mappings
                WHERE LOWER(vault_path) = LOWER($1)
                  AND (entity_type = $2 OR entity_type IS NULL)
                LIMIT 1
                """,
                vault_path, entity_type
            )
            if row:
                return row["canonical_uri"]

        # Tier 2: plain name lookup (works for both unresolved wikilinks
        # and plain text names like "David Fortson" or "IndigenomicsAI")
        name = vault_path.split("/")[-1] if vault_path else raw
        row = await conn.fetchrow(
            """
            SELECT canonical_uri FROM entity_rid_mappings
            WHERE LOWER(name) = LOWER($1)
              AND (entity_type = $2 OR entity_type IS NULL)
            LIMIT 1
            """,
            name, entity_type
        )
        if row:
            return row["canonical_uri"]

        # Tier 3: entity_registry normalized_text fallback
        row = await conn.fetchrow(
            """
            SELECT fuseki_uri FROM entity_registry
            WHERE LOWER(normalized_text) = LOWER($1)
              AND (entity_type = $2 OR entity_type IS NULL)
            LIMIT 1
            """,
            name.lower().strip(), entity_type
        )
        if row:
            return row["fuseki_uri"]

        return None

    def _row_to_dict(row) -> Dict[str, Any]:
        """Convert an asyncpg Record to a serialisable dict."""
        d = dict(row)
        for k, v in d.items():
            if isinstance(v, (date, datetime)):
                d[k] = v.isoformat()
        return d

    def _parse_date(s: Optional[str]) -> Optional[date]:
        """Parse an ISO date string; return None on failure or None input."""
        if not s:
            return None
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None

    # -----------------------------------------------------------------------
    # POST /ingest — upsert by taskKey
    # -----------------------------------------------------------------------

    @router.post("/ingest", response_model=TaskIngestResponse)
    async def ingest_task(req: TaskIngestRequest):
        """Upsert a task by taskKey. Used by meeting-notes, task-agent, and /tasks add.

        Partial payloads are safe: existing status/priority/dates are preserved
        via COALESCE on conflict. Only title is always overwritten.
        """
        async with pool.acquire() as conn:
            owner_uri = await _resolve_entity(conn, req.ownerWikilink, "Person") if req.ownerWikilink else None
            project_uri = await _resolve_entity(conn, req.projectWikilink, "Project") if req.projectWikilink else None

            collab_uris: List[str] = []
            for c in (req.collaboratorWikilinks or []):
                uri = await _resolve_entity(conn, c, "Person")
                if uri:
                    collab_uris.append(uri)

            due_date = _parse_date(req.dueDate)
            start_date = _parse_date(req.startDate)
            wait_until = _parse_date(req.waitUntil)

            row = await conn.fetchrow(
                """
                INSERT INTO task_registry (
                    task_key, uuid, title, status, priority,
                    due_date, start_date, wait_until, context, effort,
                    owner_uri, project_uri, collaborator_uris, blocked_by,
                    source_note, source_type, vault_path, tags,
                    created_at, updated_at
                ) VALUES (
                    $1, $2, $3,
                    COALESCE($4, 'inbox'), COALESCE($5, 'medium'),
                    $6, $7, $8, $9, $10,
                    $11, $12, $13, $14,
                    $15, COALESCE($16, 'meeting'), $17, $18,
                    NOW(), NOW()
                )
                ON CONFLICT (task_key) DO UPDATE SET
                    uuid            = COALESCE(EXCLUDED.uuid, task_registry.uuid),
                    title           = EXCLUDED.title,
                    -- Fix 2: reference $4/$5 directly (EXCLUDED.status is never NULL
                    -- because VALUES applied COALESCE, so we can't use COALESCE(EXCLUDED...))
                    status          = CASE WHEN $4 IS NULL THEN task_registry.status ELSE $4 END,
                    priority        = CASE WHEN $5 IS NULL THEN task_registry.priority ELSE $5 END,
                    due_date        = COALESCE(EXCLUDED.due_date, task_registry.due_date),
                    start_date      = COALESCE(EXCLUDED.start_date, task_registry.start_date),
                    wait_until      = COALESCE(EXCLUDED.wait_until, task_registry.wait_until),
                    context         = COALESCE(EXCLUDED.context, task_registry.context),
                    effort          = COALESCE(EXCLUDED.effort, task_registry.effort),
                    owner_uri       = COALESCE(EXCLUDED.owner_uri, task_registry.owner_uri),
                    project_uri     = COALESCE(EXCLUDED.project_uri, task_registry.project_uri),
                    collaborator_uris = CASE WHEN array_length(EXCLUDED.collaborator_uris, 1) > 0
                                            THEN EXCLUDED.collaborator_uris
                                            ELSE task_registry.collaborator_uris END,
                    blocked_by      = CASE WHEN array_length(EXCLUDED.blocked_by, 1) > 0
                                          THEN EXCLUDED.blocked_by
                                          ELSE task_registry.blocked_by END,
                    source_note     = COALESCE(EXCLUDED.source_note, task_registry.source_note),
                    source_type     = COALESCE(EXCLUDED.source_type, task_registry.source_type),
                    vault_path      = COALESCE(EXCLUDED.vault_path, task_registry.vault_path),
                    tags            = CASE WHEN array_length(EXCLUDED.tags, 1) > 0
                                          THEN EXCLUDED.tags
                                          ELSE task_registry.tags END,
                    updated_at      = NOW()
                RETURNING id, task_key,
                    (xmax = 0) AS was_inserted
                """,
                req.taskKey, req.uuid, req.title,
                req.status, req.priority,
                due_date, start_date, wait_until, req.context, req.effort,
                owner_uri, project_uri, collab_uris, req.blockedBy or [],
                req.sourceNote, req.sourceType, req.vaultPath,
                req.tags or [],
            )

        action = "created" if row["was_inserted"] else "updated"
        await emit_domain_event("task", "NEW" if action == "created" else "UPDATE", req.taskKey, {
            "task_key": req.taskKey, "uuid": req.uuid, "title": req.title,
            "status": req.status, "priority": req.priority,
            "due_date": req.dueDate, "start_date": req.startDate,
            "wait_until": req.waitUntil, "context": req.context, "effort": req.effort,
            "owner_uri": owner_uri, "project_uri": project_uri,
            "collaborator_uris": collab_uris, "blocked_by": req.blockedBy or [],
            "source_note": req.sourceNote, "source_type": req.sourceType,
            "vault_path": req.vaultPath, "tags": req.tags or [],
        })
        return TaskIngestResponse(task_key=row["task_key"], action=action, id=row["id"])

    # -----------------------------------------------------------------------
    # GET / — list tasks with filters
    # -----------------------------------------------------------------------

    @router.get("/", response_model=List[TaskRecord])
    async def list_tasks(
        status: Optional[str] = Query(None, description="Comma-separated statuses; omit to exclude done,cancelled"),
        owner: Optional[str] = Query(None, description="Name or URI substring — matches entity_rid_mappings name or owner_uri"),
        project: Optional[str] = Query(None, description="Case-insensitive project name lookup → project_uri; fallback substring on project_uri"),
        project_uri: Optional[str] = Query(None, description="Exact project_uri match (preferred over ?project)"),
        due_before: Optional[str] = Query(None, description="ISO date — tasks due before this date"),
        due_after: Optional[str] = Query(None, description="ISO date — tasks due after (inclusive) this date"),
        updated_before: Optional[str] = Query(None, description="ISO date — tasks last updated before this date (stale-no-activity filter)"),
        updated_after: Optional[str] = Query(None, description="ISO date — tasks last updated on or after this date"),
        source_note: Optional[str] = Query(None, description="Substring match on source_note"),
        source_type: Optional[str] = Query(None, description="Exact match on source_type"),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ):
        """List tasks with optional filters. Default excludes done and cancelled."""
        async with pool.acquire() as conn:
            conditions = []
            params: List[Any] = []

            def add(clause: str, val: Any):
                params.append(val)
                conditions.append(clause.replace("?", f"${len(params)}"))

            # Status filter
            if status:
                status_list = [s.strip() for s in status.split(",")]
                params.append(status_list)
                conditions.append(f"status = ANY(${len(params)})")
            else:
                conditions.append("status NOT IN ('done', 'cancelled')")

            # Project filter (URI exact > name lookup > substring)
            if project_uri:
                add("project_uri = ?", project_uri)
            elif project:
                proj_row = await conn.fetchrow(
                    """
                    SELECT canonical_uri FROM entity_rid_mappings
                    WHERE LOWER(name) = LOWER($1)
                      AND (entity_type = 'Project' OR entity_type IS NULL)
                    LIMIT 1
                    """,
                    project
                )
                if proj_row:
                    add("project_uri = ?", proj_row["canonical_uri"])
                else:
                    add("LOWER(project_uri) LIKE LOWER(?)", f"%{project}%")

            # Fix 4: owner filter checks entity name (via entity_rid_mappings) OR
            # falls back to URI substring so opaque URIs still work.
            if owner:
                params.append(f"%{owner}%")
                n = len(params)
                conditions.append(
                    f"""(
                        LOWER(owner_uri) LIKE LOWER(${n})
                        OR owner_uri IN (
                            SELECT canonical_uri FROM entity_rid_mappings
                            WHERE LOWER(name) LIKE LOWER(${n})
                              AND (entity_type = 'Person' OR entity_type IS NULL)
                        )
                    )"""
                )

            # Date filters
            if due_before:
                d = _parse_date(due_before)
                if d:
                    add("due_date < ?", d)
            if due_after:
                d = _parse_date(due_after)
                if d:
                    add("due_date >= ?", d)
            if updated_before:
                d = _parse_date(updated_before)
                if d:
                    add("updated_at < ?", d)
            if updated_after:
                d = _parse_date(updated_after)
                if d:
                    add("updated_at >= ?", d)

            # Source filters
            if source_note:
                add("LOWER(source_note) LIKE LOWER(?)", f"%{source_note}%")
            if source_type:
                add("source_type = ?", source_type)

            where = "WHERE " + " AND ".join(conditions) if conditions else ""

            params += [limit, offset]
            query = f"""
                SELECT * FROM task_registry
                {where}
                ORDER BY
                    CASE WHEN due_date IS NOT NULL AND due_date < CURRENT_DATE
                              AND status NOT IN ('done', 'cancelled')
                         THEN 0 ELSE 1 END,
                    due_date ASC NULLS LAST,
                    CASE priority
                        WHEN 'critical' THEN 1
                        WHEN 'high' THEN 2
                        WHEN 'medium' THEN 3
                        WHEN 'low' THEN 4
                        ELSE 5
                    END,
                    id ASC
                LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """
            rows = await conn.fetch(query, *params)

        return [TaskRecord(**_row_to_dict(r)) for r in rows]

    # -----------------------------------------------------------------------
    # PATCH /{task_key} — partial update
    # -----------------------------------------------------------------------

    @router.patch("/{task_key}")
    async def patch_task(task_key: str, req: TaskPatchRequest):
        """Partial update. Auto-sets timestamps based on status transitions.

        Fix 3: Date fields use model_fields_set to distinguish "not provided"
        from "explicitly null" (which clears the date).
        """
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT * FROM task_registry WHERE task_key = $1", task_key
            )
            if not existing:
                raise HTTPException(status_code=404, detail=f"Task not found: {task_key}")

            old_status = existing["status"]
            new_status = req.status if req.status is not None else old_status

            # Use naive UTC datetimes — asyncpg rejects tz-aware into TIMESTAMP columns
            updates: Dict[str, Any] = {"updated_at": datetime.now(timezone.utc).replace(tzinfo=None)}
            explicitly_set = req.model_fields_set

            if req.status is not None:
                updates["status"] = req.status
            if req.priority is not None:
                updates["priority"] = req.priority
            if req.context is not None:
                updates["context"] = req.context
            if req.effort is not None:
                updates["effort"] = req.effort
            if req.tags is not None:
                updates["tags"] = req.tags
            if req.blockedBy is not None:
                updates["blocked_by"] = req.blockedBy

            # Fix 3: date fields — explicitly-null clears; valid string sets; absent skips
            for field, col in (("dueDate", "due_date"), ("startDate", "start_date"), ("waitUntil", "wait_until")):
                if field in explicitly_set:
                    raw = getattr(req, field)
                    if raw is None:
                        updates[col] = None          # explicit clear
                    else:
                        parsed = _parse_date(raw)
                        if parsed is not None:
                            updates[col] = parsed    # valid date
                        # else: malformed string — leave unchanged (no update)

            # Resolve owner / project / collaborators
            if req.ownerWikilink is not None:
                updates["owner_uri"] = await _resolve_entity(conn, req.ownerWikilink, "Person")
            if req.projectWikilink is not None:
                updates["project_uri"] = await _resolve_entity(conn, req.projectWikilink, "Project")
            if req.collaboratorWikilinks is not None:
                uris = []
                for c in req.collaboratorWikilinks:
                    uri = await _resolve_entity(conn, c, "Person")
                    if uri:
                        uris.append(uri)
                updates["collaborator_uris"] = uris

            # Auto-timestamps based on status transition (naive UTC for TIMESTAMP columns)
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if new_status == "done" and old_status != "done":
                updates["completed_at"] = now
            if new_status == "in-progress" and old_status != "in-progress":
                updates["started_at"] = now
            if old_status == "inbox" and new_status != "inbox":
                updates["triaged_at"] = now

            # Build SET clause
            set_clauses = []
            vals: List[Any] = []
            for col, val in updates.items():
                vals.append(val)
                set_clauses.append(f"{col} = ${len(vals)}")

            vals.append(task_key)
            await conn.execute(
                f"UPDATE task_registry SET {', '.join(set_clauses)} WHERE task_key = ${len(vals)}",
                *vals
            )

        await emit_domain_event("task", "UPDATE", task_key, {
            "task_key": task_key, "title": existing["title"],
            "status": new_status, "priority": updates.get("priority", existing["priority"]),
        })
        return {"task_key": task_key, "action": "updated"}

    # -----------------------------------------------------------------------
    # GET /stats — aggregate counts
    # -----------------------------------------------------------------------

    @router.get("/stats", response_model=TaskStatsResponse)
    async def get_stats():
        """Return aggregate task counts."""
        async with pool.acquire() as conn:
            today = date.today()

            by_status_rows = await conn.fetch(
                """
                SELECT status, COUNT(*) AS cnt FROM task_registry
                WHERE source_type IS DISTINCT FROM 'test'
                GROUP BY status
                """
            )
            by_status = {r["status"]: r["cnt"] for r in by_status_rows}

            total_open = sum(
                by_status.get(s, 0)
                for s in ("inbox", "open", "in-progress", "waiting")
            )
            total_done = by_status.get("done", 0)

            overdue = await conn.fetchval(
                """
                SELECT COUNT(*) FROM task_registry
                WHERE due_date < $1
                  AND status NOT IN ('done', 'cancelled')
                  AND source_type IS DISTINCT FROM 'test'
                """,
                today
            )
            due_today = await conn.fetchval(
                """
                SELECT COUNT(*) FROM task_registry
                WHERE due_date = $1
                  AND status NOT IN ('done', 'cancelled')
                  AND source_type IS DISTINCT FROM 'test'
                """,
                today
            )
            due_this_week = await conn.fetchval(
                """
                SELECT COUNT(*) FROM task_registry
                WHERE due_date BETWEEN $1 AND $1 + INTERVAL '7 days'
                  AND status NOT IN ('done', 'cancelled')
                  AND source_type IS DISTINCT FROM 'test'
                """,
                today
            )

        return TaskStatsResponse(
            total_open=total_open,
            total_done=total_done,
            by_status=by_status,
            overdue=overdue,
            due_today=due_today,
            due_this_week=due_this_week,
        )

    return router
