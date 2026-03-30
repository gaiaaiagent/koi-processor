"""Protocol layer endpoints: requirements, coverage, signals, gap computation.

Implements the Claims × Spore coordination protocol:
  POST /requirements/create          — declare a normative requirement
  GET  /requirements/{rid}           — fetch requirement by RID
  GET  /requirements/                — list requirements (filterable)
  POST /coverage/link                — create a coverage link
  GET  /coverage/                    — list coverage links (filterable)
  GET  /pools/{rid}/gaps             — compute unmet/stale requirements for a pool
  POST /signals/create               — record a signal
  GET  /signals/                     — list signals (filterable)

Additive layer on top of existing claims/commitments/intents.
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class RequirementCreateRequest(BaseModel):
    scope: str = Field(..., description="personal | team | pool | org | federation | on_chain_group")
    scope_ref: Optional[str] = Field(None, description="URI of the scoped entity (pool_rid, org_uri)")
    policy_source: str = Field(..., description="URI of the policy/constitution declaring this")
    requirement_type: str = Field(..., description="monitoring | reporting | stewardship | governance | contribution")
    statement: str = Field(..., min_length=5, max_length=1000)
    subject_uri: Optional[str] = None
    frequency: Optional[str] = Field(None, description="once | weekly | monthly | quarterly | annual")
    freshness_window_days: Optional[int] = Field(None, ge=1)
    severity: str = Field("medium", description="low | medium | high | critical")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RequirementResponse(BaseModel):
    requirement_rid: str
    scope: str
    scope_ref: Optional[str]
    policy_source: str
    requirement_type: str
    statement: str
    subject_uri: Optional[str]
    frequency: Optional[str]
    freshness_window_days: Optional[int]
    severity: str
    active: bool
    metadata: Dict[str, Any]
    created_at: datetime


class CoverageLinkRequest(BaseModel):
    coverage_type: str = Field(..., description="commitment_covers_requirement | claim_covers_condition | evidence_covers_commitment")
    source_rid: str = Field(..., description="RID of the covering artifact")
    target_rid: str = Field(..., description="RID of the covered artifact")
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    provenance: Optional[str] = Field(None, description="manual | ai_inferred | policy_rule")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CoverageLinkResponse(BaseModel):
    coverage_rid: str
    coverage_type: str
    source_rid: str
    target_rid: str
    valid_from: datetime
    valid_until: Optional[datetime]
    confidence: Optional[float]
    provenance: Optional[str]
    metadata: Dict[str, Any]
    created_at: datetime


class SignalCreateRequest(BaseModel):
    signal_type: str = Field(..., description="declaration | discourse | gap_computed | sensor | document_extract")
    source_kind: str = Field(..., description="What produced it")
    source_ref: Optional[str] = None
    statement: str = Field(..., min_length=5, max_length=2000)
    scope: str = Field(..., description="personal | team | pool | org | federation | on_chain_group")
    subject_uri: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    fresh_until: Optional[datetime] = None


class SignalResponse(BaseModel):
    signal_rid: str
    signal_type: str
    source_kind: str
    source_ref: Optional[str]
    statement: str
    scope: str
    subject_uri: Optional[str]
    metadata: Dict[str, Any]
    confidence: Optional[float]
    fresh_until: Optional[datetime]
    created_at: datetime


class GapSignalResponse(BaseModel):
    requirement_rid: str
    requirement_statement: str
    requirement_type: str
    severity: str
    frequency: Optional[str]
    freshness_window_days: Optional[int]
    gap_type: str  # unmet | stale
    coverage_count: int
    latest_coverage_until: Optional[datetime]
    signal_rid: Optional[str]  # RID of emitted signal (if created)
    next_move: str  # surface_only | request_offer | propose_commitment | escalate_to_council


class PoolGapsResponse(BaseModel):
    pool_rid: str
    total_requirements: int
    unmet_count: int
    stale_count: int
    covered_count: int
    gaps: List[GapSignalResponse]


# ---------------------------------------------------------------------------
# RID helpers
# ---------------------------------------------------------------------------

_VALID_SCOPES = {"personal", "team", "pool", "org", "federation", "on_chain_group"}
_VALID_REQ_TYPES = {"monitoring", "reporting", "stewardship", "governance", "contribution"}
_VALID_SEVERITIES = {"low", "medium", "high", "critical"}
_VALID_FREQUENCIES = {"once", "weekly", "monthly", "quarterly", "annual"}
_VALID_SIGNAL_TYPES = {"declaration", "discourse", "gap_computed", "sensor", "document_extract"}
_VALID_COVERAGE_TYPES = {"commitment_covers_requirement", "claim_covers_condition", "evidence_covers_commitment"}
_VALID_PROVENANCES = {"manual", "ai_inferred", "policy_rule"}


def _requirement_rid(scope: str, scope_ref: Optional[str], policy_source: str, statement: str) -> str:
    canonical = json.dumps({
        "policy_source": policy_source,
        "scope": scope,
        "scope_ref": scope_ref or "",
        "statement": statement,
    }, sort_keys=True, separators=(",", ":"))
    h = hashlib.blake2b(canonical.encode(), digest_size=32).hexdigest()[:32]
    return f"orn:koi-net.requirement:{h}"


def _coverage_rid(coverage_type: str, source_rid: str, target_rid: str) -> str:
    canonical = json.dumps({
        "coverage_type": coverage_type,
        "source_rid": source_rid,
        "target_rid": target_rid,
    }, sort_keys=True, separators=(",", ":"))
    h = hashlib.blake2b(canonical.encode(), digest_size=32).hexdigest()[:32]
    return f"orn:koi-net.coverage:{h}"


def _signal_rid(signal_type: str, source_kind: str, source_ref: Optional[str],
                statement: str, scope: str) -> str:
    canonical = json.dumps({
        "scope": scope,
        "signal_type": signal_type,
        "source_kind": source_kind,
        "source_ref": source_ref or "",
        "statement": statement,
    }, sort_keys=True, separators=(",", ":"))
    h = hashlib.blake2b(canonical.encode(), digest_size=32).hexdigest()[:32]
    return f"orn:koi-net.signal:{h}"


def _row_to_response(model_cls, row):
    """Convert asyncpg Record to Pydantic model, parsing JSONB strings."""
    d = dict(row)
    if "metadata" in d and isinstance(d["metadata"], str):
        d["metadata"] = json.loads(d["metadata"])
    return model_cls(**d)


def _next_move(severity: str, scope: str) -> str:
    """Compute suggested next move based on severity and scope."""
    if severity == "critical":
        return "escalate_to_council"
    if severity == "high":
        return "propose_commitment"
    if severity == "medium":
        return "request_offer"
    return "surface_only"


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_protocol_router(pool) -> APIRouter:
    router = APIRouter()

    # ------------------------------------------------------------------ #
    # Requirements CRUD                                                    #
    # ------------------------------------------------------------------ #

    @router.post("/requirements/create", response_model=RequirementResponse, status_code=201)
    async def create_requirement(body: RequirementCreateRequest):
        if body.scope not in _VALID_SCOPES:
            raise HTTPException(400, f"Invalid scope: {body.scope}")
        if body.requirement_type not in _VALID_REQ_TYPES:
            raise HTTPException(400, f"Invalid requirement_type: {body.requirement_type}")
        if body.severity not in _VALID_SEVERITIES:
            raise HTTPException(400, f"Invalid severity: {body.severity}")
        if body.frequency and body.frequency not in _VALID_FREQUENCIES:
            raise HTTPException(400, f"Invalid frequency: {body.frequency}")

        rid = _requirement_rid(body.scope, body.scope_ref, body.policy_source, body.statement)

        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO requirements (
                    requirement_rid, scope, scope_ref, policy_source, requirement_type,
                    statement, subject_uri, frequency, freshness_window_days,
                    severity, metadata
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (requirement_rid) DO UPDATE SET
                    frequency = EXCLUDED.frequency,
                    freshness_window_days = EXCLUDED.freshness_window_days,
                    severity = EXCLUDED.severity,
                    subject_uri = EXCLUDED.subject_uri,
                    metadata = EXCLUDED.metadata
                RETURNING *
            """, rid, body.scope, body.scope_ref, body.policy_source,
                body.requirement_type, body.statement, body.subject_uri,
                body.frequency, body.freshness_window_days, body.severity,
                json.dumps(body.metadata))
            return _row_to_response(RequirementResponse, row)

    @router.get("/requirements/{rid}", response_model=RequirementResponse)
    async def get_requirement(rid: str):
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM requirements WHERE requirement_rid = $1", rid)
            if not row:
                raise HTTPException(404, f"Requirement not found: {rid}")
            return _row_to_response(RequirementResponse, row)

    @router.get("/requirements/", response_model=List[RequirementResponse])
    async def list_requirements(
        scope: Optional[str] = Query(None),
        scope_ref: Optional[str] = Query(None),
        requirement_type: Optional[str] = Query(None),
        severity: Optional[str] = Query(None),
        active_only: bool = Query(True),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        async with pool.acquire() as conn:
            conditions = []
            params: list = []
            i = 1
            if scope:
                conditions.append(f"scope = ${i}")
                params.append(scope)
                i += 1
            if scope_ref:
                conditions.append(f"scope_ref = ${i}")
                params.append(scope_ref)
                i += 1
            if requirement_type:
                conditions.append(f"requirement_type = ${i}")
                params.append(requirement_type)
                i += 1
            if severity:
                conditions.append(f"severity = ${i}")
                params.append(severity)
                i += 1
            if active_only:
                conditions.append("active = TRUE")

            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            params.extend([limit, offset])
            rows = await conn.fetch(f"""
                SELECT * FROM requirements {where}
                ORDER BY created_at DESC
                LIMIT ${i} OFFSET ${i+1}
            """, *params)
            return [_row_to_response(RequirementResponse, r) for r in rows]

    # ------------------------------------------------------------------ #
    # Coverage CRUD                                                        #
    # ------------------------------------------------------------------ #

    @router.post("/coverage/link", response_model=CoverageLinkResponse, status_code=201)
    async def create_coverage_link(body: CoverageLinkRequest):
        if body.coverage_type not in _VALID_COVERAGE_TYPES:
            raise HTTPException(400, f"Invalid coverage_type: {body.coverage_type}")
        if body.provenance and body.provenance not in _VALID_PROVENANCES:
            raise HTTPException(400, f"Invalid provenance: {body.provenance}")

        rid = _coverage_rid(body.coverage_type, body.source_rid, body.target_rid)
        valid_from = body.valid_from or datetime.now(timezone.utc)

        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO coverage_links (
                    coverage_rid, coverage_type, source_rid, target_rid,
                    valid_from, valid_until, confidence, provenance, metadata
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (coverage_rid) DO UPDATE SET
                    valid_from = EXCLUDED.valid_from,
                    valid_until = EXCLUDED.valid_until,
                    confidence = EXCLUDED.confidence,
                    provenance = EXCLUDED.provenance,
                    metadata = EXCLUDED.metadata
                RETURNING *
            """, rid, body.coverage_type, body.source_rid, body.target_rid,
                valid_from, body.valid_until, body.confidence, body.provenance,
                json.dumps(body.metadata))
            return _row_to_response(CoverageLinkResponse, row)

    @router.get("/coverage/", response_model=List[CoverageLinkResponse])
    async def list_coverage(
        target_rid: Optional[str] = Query(None, description="Filter by covered artifact"),
        source_rid: Optional[str] = Query(None, description="Filter by covering artifact"),
        coverage_type: Optional[str] = Query(None),
        valid_only: bool = Query(True, description="Only return currently valid coverage"),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        async with pool.acquire() as conn:
            conditions = []
            params: list = []
            i = 1
            if target_rid:
                conditions.append(f"target_rid = ${i}")
                params.append(target_rid)
                i += 1
            if source_rid:
                conditions.append(f"source_rid = ${i}")
                params.append(source_rid)
                i += 1
            if coverage_type:
                conditions.append(f"coverage_type = ${i}")
                params.append(coverage_type)
                i += 1
            if valid_only:
                conditions.append("valid_from <= NOW()")
                conditions.append("(valid_until IS NULL OR valid_until > NOW())")

            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            params.extend([limit, offset])
            rows = await conn.fetch(f"""
                SELECT * FROM coverage_links {where}
                ORDER BY created_at DESC
                LIMIT ${i} OFFSET ${i+1}
            """, *params)
            return [_row_to_response(CoverageLinkResponse, r) for r in rows]

    # ------------------------------------------------------------------ #
    # Signals CRUD                                                         #
    # ------------------------------------------------------------------ #

    @router.post("/signals/create", response_model=SignalResponse, status_code=201)
    async def create_signal(body: SignalCreateRequest):
        if body.signal_type not in _VALID_SIGNAL_TYPES:
            raise HTTPException(400, f"Invalid signal_type: {body.signal_type}")
        if body.scope not in _VALID_SCOPES:
            raise HTTPException(400, f"Invalid scope: {body.scope}")

        rid = _signal_rid(body.signal_type, body.source_kind, body.source_ref,
                         body.statement, body.scope)

        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO signals (
                    signal_rid, signal_type, source_kind, source_ref,
                    statement, scope, subject_uri, metadata,
                    confidence, fresh_until
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (signal_rid) DO UPDATE SET
                    metadata = EXCLUDED.metadata,
                    confidence = EXCLUDED.confidence,
                    fresh_until = EXCLUDED.fresh_until
                RETURNING *
            """, rid, body.signal_type, body.source_kind, body.source_ref,
                body.statement, body.scope, body.subject_uri,
                json.dumps(body.metadata), body.confidence, body.fresh_until)
            return _row_to_response(SignalResponse, row)

    @router.get("/signals/", response_model=List[SignalResponse])
    async def list_signals(
        signal_type: Optional[str] = Query(None),
        scope: Optional[str] = Query(None),
        source_ref: Optional[str] = Query(None),
        fresh_only: bool = Query(False, description="Only return signals that are not stale"),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        async with pool.acquire() as conn:
            conditions = []
            params: list = []
            i = 1
            if signal_type:
                conditions.append(f"signal_type = ${i}")
                params.append(signal_type)
                i += 1
            if scope:
                conditions.append(f"scope = ${i}")
                params.append(scope)
                i += 1
            if source_ref:
                conditions.append(f"source_ref = ${i}")
                params.append(source_ref)
                i += 1
            if fresh_only:
                conditions.append("(fresh_until IS NULL OR fresh_until > NOW())")

            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            params.extend([limit, offset])
            rows = await conn.fetch(f"""
                SELECT * FROM signals {where}
                ORDER BY created_at DESC
                LIMIT ${i} OFFSET ${i+1}
            """, *params)
            return [_row_to_response(SignalResponse, r) for r in rows]

    # ------------------------------------------------------------------ #
    # Gap computation                                                      #
    # ------------------------------------------------------------------ #

    @router.get("/pools/{pool_rid}/gaps", response_model=PoolGapsResponse)
    async def compute_pool_gaps(pool_rid: str):
        """Compute unmet and stale requirements for a commitment pool.

        For each active requirement scoped to this pool, checks coverage_links
        for valid coverage. No valid coverage → gap. Stale coverage (valid_until
        expired or freshness_window exceeded) → stale gap.

        Emits gap_computed signals for each gap found.
        """
        async with pool.acquire() as conn:
            # Verify pool exists
            pool_row = await conn.fetchrow(
                "SELECT pool_rid FROM commitment_pools WHERE pool_rid = $1", pool_rid)
            if not pool_row:
                raise HTTPException(404, f"Pool not found: {pool_rid}")

            # Fetch active requirements for this pool
            requirements = await conn.fetch("""
                SELECT * FROM requirements
                WHERE active = TRUE
                  AND scope = 'pool'
                  AND scope_ref = $1
                ORDER BY severity DESC, created_at ASC
            """, pool_rid)

            gaps: list = []
            covered_count = 0
            now = datetime.now(timezone.utc)

            for req in requirements:
                req_rid = req["requirement_rid"]

                # Check valid coverage (only commitment_covers_requirement,
                # must have started and not expired)
                coverage = await conn.fetch("""
                    SELECT * FROM coverage_links
                    WHERE target_rid = $1
                      AND coverage_type = 'commitment_covers_requirement'
                      AND valid_from <= $2
                      AND (valid_until IS NULL OR valid_until > $2)
                    ORDER BY valid_from DESC
                """, req_rid, now)

                # Determine gap status
                gap_type = None
                latest_until = None

                if not coverage:
                    # No coverage at all — check if there was ever any (stale vs unmet)
                    # Only commitment_covers_requirement counts for pool gap history
                    expired = await conn.fetchrow("""
                        SELECT valid_until FROM coverage_links
                        WHERE target_rid = $1
                          AND coverage_type = 'commitment_covers_requirement'
                        ORDER BY valid_until DESC NULLS LAST
                        LIMIT 1
                    """, req_rid)
                    if expired and expired["valid_until"]:
                        gap_type = "stale"
                        latest_until = expired["valid_until"]
                    else:
                        gap_type = "unmet"
                else:
                    # Has valid coverage — check freshness window if recurrent
                    fw = req["freshness_window_days"]
                    if fw:
                        latest_from = max(c["valid_from"] for c in coverage)
                        if now - latest_from > timedelta(days=fw):
                            gap_type = "stale"
                            latest_until = latest_from + timedelta(days=fw)

                if gap_type:
                    next_move = _next_move(req["severity"], req["scope"])

                    # Emit a gap_computed signal
                    statement = (
                        f"{req['requirement_type'].title()} gap: {req['statement']} "
                        f"({gap_type}, severity={req['severity']})"
                    )
                    sig_rid = _signal_rid("gap_computed", "gap_computation", pool_rid,
                                          statement, "pool")
                    sig_meta = json.dumps({
                        "requirement_rid": req_rid,
                        "gap_type": gap_type,
                        "next_move": next_move,
                        "coverage_count": len(coverage),
                        "computed_at": now.isoformat(),
                    })
                    await conn.execute("""
                        INSERT INTO signals (
                            signal_rid, signal_type, source_kind, source_ref,
                            statement, scope, subject_uri, metadata, confidence
                        ) VALUES ($1, 'gap_computed', 'gap_computation', $2, $3, 'pool', $4, $5, 1.0)
                        ON CONFLICT (signal_rid) DO UPDATE SET
                            metadata = EXCLUDED.metadata,
                            confidence = EXCLUDED.confidence
                    """, sig_rid, pool_rid, statement, req["subject_uri"], sig_meta)

                    gaps.append(GapSignalResponse(
                        requirement_rid=req_rid,
                        requirement_statement=req["statement"],
                        requirement_type=req["requirement_type"],
                        severity=req["severity"],
                        frequency=req["frequency"],
                        freshness_window_days=req["freshness_window_days"],
                        gap_type=gap_type,
                        coverage_count=len(coverage),
                        latest_coverage_until=latest_until,
                        signal_rid=sig_rid,
                        next_move=next_move,
                    ))
                else:
                    covered_count += 1

            unmet = sum(1 for g in gaps if g.gap_type == "unmet")
            stale = sum(1 for g in gaps if g.gap_type == "stale")

            return PoolGapsResponse(
                pool_rid=pool_rid,
                total_requirements=len(requirements),
                unmet_count=unmet,
                stale_count=stale,
                covered_count=covered_count,
                gaps=gaps,
            )

    return router
