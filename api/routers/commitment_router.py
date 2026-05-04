"""Commitment pooling endpoints: pledge lifecycle, pool management, evidence linking, routing.

Implements the C0 commitment registry API:
  POST /commitments/create       — propose a new commitment pledge
  GET  /commitments/{rid}        — fetch commitment by RID
  PATCH /commitments/{rid}/state — transition state (steward action)
  POST /commitments/{rid}/link-evidence — attach Evidence entity
  GET  /commitments/             — list commitments (filterable)
  POST /commitments/routing-suggestions — score draft against pools
  POST /pools/create             — create a new commitment pool
  GET  /pools/{rid}              — fetch pool by RID
  POST /pools/{rid}/pledge       — add an existing commitment to a pool
  GET  /pools/{rid}/status       — pool status + pledge summary

All state transitions are recorded in commitment_state_log (insert-only).
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class CommitmentCreateRequest(BaseModel):
    pledger_uri: str = Field(..., description="entity_registry.fuseki_uri of the pledger")
    title: str = Field(..., min_length=3, max_length=200)
    description: Optional[str] = None
    offer_type: str = Field("labor", description="labor | goods | service | knowledge | stewardship")
    quantity: Optional[float] = None
    unit: Optional[str] = None
    validity_start: Optional[datetime] = None
    validity_end: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_by: Optional[str] = None


class CommitmentResponse(BaseModel):
    commitment_rid: str
    pledger_uri: str
    pool_rid: Optional[str]
    title: str
    description: Optional[str]
    offer_type: str
    quantity: Optional[float]
    unit: Optional[str]
    validity_start: Optional[datetime]
    validity_end: Optional[datetime]
    state: str
    scope: Optional[str] = None
    evidence_uri: Optional[str]
    metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime


class StateTransitionRequest(BaseModel):
    new_state: str = Field(..., description="Target state: VERIFIED | ACTIVE | REJECTED | WITHDRAWN | EVIDENCE_LINKED | REDEEMED | DISPUTED | RESOLVED")
    actor: Optional[str] = None
    reason: Optional[str] = None


class EvidenceLinkRequest(BaseModel):
    evidence_uri: str = Field(..., description="entity_registry.fuseki_uri of the Evidence entity")
    actor: Optional[str] = None


class PoolCreateRequest(BaseModel):
    name: str = Field(..., min_length=3, max_length=200)
    description: Optional[str] = None
    steward_uri: Optional[str] = None
    bioregion_uri: Optional[str] = None
    activation_threshold_pct: float = Field(80.0, ge=0.0, le=100.0)
    activation_threshold_count: Optional[int] = None
    demurrage_rate_monthly: float = Field(0.0, ge=0.0)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_by: Optional[str] = None


class PoolResponse(BaseModel):
    pool_rid: str
    name: str
    description: Optional[str]
    steward_uri: Optional[str]
    bioregion_uri: Optional[str]
    activation_threshold_pct: float
    activation_threshold_count: Optional[int]
    demurrage_rate_monthly: float
    state: str
    scope: Optional[str] = None
    metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime


class PledgeToPoolRequest(BaseModel):
    commitment_rid: str
    actor: Optional[str] = None


class TranscriptExtractRequest(BaseModel):
    """Request to extract commitments from transcript text."""
    document_text: str = Field(..., min_length=50)
    source_document: str = Field(..., description="Interview ID or document reference")
    bioregion: Optional[str] = None
    confidence_threshold: float = Field(0.6, ge=0.0, le=1.0)
    auto_create: bool = Field(False, description="If True, resolve entities and create commitments")


class CommitmentCandidate(BaseModel):
    pledger_name: str
    pledger_organization: Optional[str] = None
    title: str
    description: str = ""
    declaration_type: str = "commitment"
    offer_type: str = "labor"
    quantity: Optional[float] = None
    unit: Optional[str] = None
    need_category: Optional[str] = None
    fiat_only: Optional[bool] = None
    monthly_amount_usd: Optional[float] = None
    validity_start: Optional[str] = None
    validity_end: Optional[str] = None
    estimated_value_usd: Optional[float] = None
    routing_tags: List[str] = []
    wants: List[str] = []
    limits: List[str] = []
    confidence: float = 0.5
    source_snippet: str = ""
    source_document: Optional[str] = None


class TranscriptExtractResponse(BaseModel):
    candidates: List[CommitmentCandidate]
    summary: str
    auto_created: Optional[List[Dict[str, Any]]] = None


class CreateClaimRequest(BaseModel):
    actor: Optional[str] = None


class RoutingSuggestionRequest(BaseModel):
    """Draft commitment payload for routing scoring. Same shape as CommitmentCreateRequest."""
    pledger_uri: Optional[str] = None
    title: Optional[str] = None
    offer_type: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    validity_start: Optional[datetime] = None
    validity_end: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ScoreBreakdown(BaseModel):
    same_bioregion: int = 0
    offer_need_overlap: int = 0
    timeframe_overlap: int = 0
    capacity_fit: int = 0
    governance_compat: int = 0


class PoolSuggestion(BaseModel):
    pool_rid: str
    pool_name: str
    total_score: int
    score_breakdown: ScoreBreakdown
    hard_excludes: List[str]
    recommended: bool
    explanation: str


class RoutingSuggestionResponse(BaseModel):
    suggestions: List[PoolSuggestion]


# ---------------------------------------------------------------------------
# Valid state transitions
# ---------------------------------------------------------------------------

_VALID_TRANSITIONS = {
    "PROPOSED":        {"VERIFIED", "REJECTED", "WITHDRAWN"},
    "VERIFIED":        {"ACTIVE", "WITHDRAWN"},
    "ACTIVE":          {"EVIDENCE_LINKED", "WITHDRAWN", "DISPUTED"},
    "EVIDENCE_LINKED": {"REDEEMED", "DISPUTED"},
    "REDEEMED":        set(),
    "REJECTED":        set(),
    "WITHDRAWN":       set(),
    "DISPUTED":        {"RESOLVED"},
    "RESOLVED":        set(),
}


def _commitment_rid(pledger_uri: str, title: str) -> str:
    """Deterministic RID for a commitment pledge."""
    h = hashlib.sha256(f"commitment:{pledger_uri}:{title}".encode()).hexdigest()[:32]
    return f"orn:koi-net.commitment:{h}"


def _pool_rid(name: str, steward_uri: str = "") -> str:
    """Deterministic RID for a commitment pool."""
    h = hashlib.sha256(f"pool:{name}:{steward_uri}".encode()).hexdigest()[:32]
    return f"orn:koi-net.commitment-pool:{h}"


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_router(pool, caps=None):
    """Return an APIRouter for commitment pooling endpoints."""
    router = APIRouter(prefix="/commitments", tags=["commitments"])

    from api.federation_events import emit_domain_event

    # ------------------------------------------------------------------ #
    # Commitment CRUD                                                       #
    # ------------------------------------------------------------------ #

    @router.post("/create", response_model=CommitmentResponse, status_code=201)
    async def create_commitment(body: CommitmentCreateRequest):
        """Propose a new commitment pledge. Initial state: PROPOSED."""
        rid = _commitment_rid(body.pledger_uri, body.title)
        async with pool.acquire() as conn:
            # Verify pledger exists
            pledger = await conn.fetchrow(
                "SELECT fuseki_uri FROM entity_registry WHERE fuseki_uri = $1",
                body.pledger_uri,
            )
            if not pledger:
                raise HTTPException(status_code=404, detail=f"Pledger entity not found: {body.pledger_uri}")

            # Upsert commitment (idempotent by RID)
            row = await conn.fetchrow("""
                INSERT INTO commitments
                    (commitment_rid, pledger_uri, title, description, offer_type,
                     quantity, unit, validity_start, validity_end, state, metadata, created_by)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'PROPOSED',$10::jsonb,$11)
                ON CONFLICT (commitment_rid) DO UPDATE SET
                    updated_at = NOW()
                RETURNING *
            """,
                rid,
                body.pledger_uri,
                body.title,
                body.description,
                body.offer_type,
                body.quantity,
                body.unit,
                body.validity_start,
                body.validity_end,
                _json_dumps(body.metadata),
                body.created_by,
            )

            # Log initial state
            await conn.execute("""
                INSERT INTO commitment_state_log
                    (commitment_rid, from_state, to_state, actor, reason)
                VALUES ($1, NULL, 'PROPOSED', $2, 'created')
                ON CONFLICT DO NOTHING
            """, rid, body.created_by)

            # Write pledges_commitment relationship
            try:
                await conn.execute("""
                    INSERT INTO entity_relationships (subject_uri, predicate, object_uri, source)
                    VALUES ($1, 'pledges_commitment', $2, 'commitment_registry')
                    ON CONFLICT DO NOTHING
                """, body.pledger_uri, rid)
            except Exception as e:
                logger.warning(f"Failed to create pledges_commitment relationship: {e}")

        logger.info(f"commitment.create rid={rid} pledger={body.pledger_uri}")
        await emit_domain_event("commitment", "NEW", rid, {
            "commitment_rid": rid, "pledger_uri": body.pledger_uri,
            "title": body.title, "description": body.description,
            "offer_type": body.offer_type, "quantity": float(body.quantity) if body.quantity else None,
            "unit": body.unit,
            "validity_start": body.validity_start.isoformat() if body.validity_start else None,
            "validity_end": body.validity_end.isoformat() if body.validity_end else None,
            "state": "PROPOSED", "metadata": body.metadata or {},
            "created_by": body.created_by,
            "state_transition": {"from_state": None, "to_state": "PROPOSED",
                                 "actor": body.created_by, "reason": "created",
                                 "created_at": datetime.now(timezone.utc).isoformat()},
        })
        return _row_to_commitment(row)

    @router.get("/", response_model=List[CommitmentResponse])
    async def list_commitments(
        state: Optional[str] = Query(None, description="Filter by state"),
        pledger_uri: Optional[str] = Query(None),
        pool_rid: Optional[str] = Query(None),
        offer_type: Optional[str] = Query(None, description="Filter by offer type (labor, goods, service, knowledge, stewardship)"),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        """List commitments with optional filters."""
        async with pool.acquire() as conn:
            conditions = []
            params = []
            i = 1
            if state:
                conditions.append(f"c.state = ${i}::commitment_state")
                params.append(state.upper())
                i += 1
            if pledger_uri:
                conditions.append(f"pledger_uri = ${i}")
                params.append(pledger_uri)
                i += 1
            if pool_rid:
                conditions.append(f"pool_id = (SELECT id FROM commitment_pools WHERE pool_rid = ${i})")
                params.append(pool_rid)
                i += 1
            if offer_type:
                conditions.append(f"c.offer_type = ${i}")
                params.append(offer_type)
                i += 1

            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            params.extend([limit, offset])
            rows = await conn.fetch(f"""
                SELECT c.*, cp.pool_rid AS pool_rid_text
                FROM commitments c
                LEFT JOIN commitment_pools cp ON cp.id = c.pool_id
                {where}
                ORDER BY c.created_at DESC
                LIMIT ${i} OFFSET ${i+1}
            """, *params)

        return [_row_to_commitment(r) for r in rows]

    @router.post("/routing-suggestions", response_model=RoutingSuggestionResponse)
    async def routing_suggestions(body: RoutingSuggestionRequest):
        """Score a draft commitment against all available pools.

        Accepts an unpersisted draft (same shape as CommitmentCreateRequest).
        Returns pools ranked by routing score with breakdown and explanations.
        """
        suggestions = await _score_pools(pool, body)
        return RoutingSuggestionResponse(suggestions=suggestions)

    @router.post("/extract-from-transcript", response_model=TranscriptExtractResponse)
    async def extract_commitments_from_transcript(body: TranscriptExtractRequest):
        """Extract commitment candidates from transcript text via LLM.

        Returns candidates for human review. If auto_create=True, also resolves
        pledger entities and creates commitments in PROPOSED state.
        """
        from api.commitment_extractor import extract_commitments_from_text

        result = await extract_commitments_from_text(
            document_text=body.document_text,
            source_document=body.source_document,
            bioregion=body.bioregion,
            confidence_threshold=body.confidence_threshold,
        )

        candidates = [CommitmentCandidate(**c) for c in result["candidates"]]
        auto_created = None

        if body.auto_create and candidates:
            auto_created = []
            async with pool.acquire() as conn:
                for candidate in candidates:
                    # Resolve pledger entity
                    pledger_name = candidate.pledger_organization or candidate.pledger_name
                    pledger_row = await conn.fetchrow(
                        "SELECT fuseki_uri FROM entity_registry WHERE LOWER(entity_text) = LOWER($1) LIMIT 1",
                        pledger_name,
                    )
                    if not pledger_row:
                        auto_created.append({
                            "title": candidate.title,
                            "status": "skipped",
                            "reason": f"Pledger '{pledger_name}' not found in entity registry",
                        })
                        continue

                    pledger_uri = pledger_row["fuseki_uri"]

                    # Resolve bioregion URI
                    bioregion_uri = ""
                    if body.bioregion:
                        bio_row = await conn.fetchrow(
                            "SELECT fuseki_uri FROM entity_registry WHERE LOWER(entity_text) = LOWER($1) AND entity_type = 'Bioregion' LIMIT 1",
                            body.bioregion,
                        )
                        if bio_row:
                            bioregion_uri = bio_row["fuseki_uri"]

                    metadata = {
                        "wants": candidate.wants,
                        "limits": candidate.limits,
                        "routing_tags": candidate.routing_tags,
                        "estimated_value_usd": candidate.estimated_value_usd,
                        "bioregion_uri": bioregion_uri,
                        "source_interview_id": body.source_document,
                        "ai_confidence": candidate.confidence,
                    }
                    # Carry needs-specific fields from extraction
                    extra = candidate.model_dump(exclude_none=True)
                    for k in ("declaration_type", "need_category", "fiat_only", "monthly_amount_usd"):
                        if k in extra and extra[k] is not None:
                            metadata[k] = extra[k]

                    rid = _commitment_rid(pledger_uri, candidate.title)
                    try:
                        row = await conn.fetchrow("""
                            INSERT INTO commitments
                                (commitment_rid, pledger_uri, title, description, offer_type,
                                 quantity, unit, validity_start, validity_end, state, metadata, created_by)
                            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'PROPOSED',$10::jsonb,$11)
                            ON CONFLICT (commitment_rid) DO UPDATE SET updated_at = NOW()
                            RETURNING commitment_rid
                        """,
                            rid, pledger_uri, candidate.title, candidate.description,
                            candidate.offer_type, candidate.quantity, candidate.unit,
                            None, None, _json_dumps(metadata),
                            f"commitment-extractor:{body.source_document}",
                        )

                        await conn.execute("""
                            INSERT INTO commitment_state_log
                                (commitment_rid, from_state, to_state, actor, reason)
                            VALUES ($1, NULL, 'PROPOSED', $2, 'auto-created from transcript extraction')
                            ON CONFLICT DO NOTHING
                        """, rid, f"commitment-extractor:{body.source_document}")

                        try:
                            await conn.execute("""
                                INSERT INTO entity_relationships (subject_uri, predicate, object_uri, source)
                                VALUES ($1, 'pledges_commitment', $2, 'commitment_extractor')
                                ON CONFLICT DO NOTHING
                            """, pledger_uri, rid)
                        except Exception:
                            pass

                        auto_created.append({
                            "title": candidate.title,
                            "commitment_rid": rid,
                            "pledger_uri": pledger_uri,
                            "status": "created",
                        })
                    except Exception as e:
                        auto_created.append({
                            "title": candidate.title,
                            "status": "error",
                            "reason": str(e),
                        })

        return TranscriptExtractResponse(
            candidates=candidates,
            summary=result["summary"],
            auto_created=auto_created,
        )

    @router.get("/{rid}", response_model=CommitmentResponse)
    async def get_commitment(rid: str):
        """Fetch a commitment by RID."""
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT c.*, cp.pool_rid AS pool_rid_text
                FROM commitments c
                LEFT JOIN commitment_pools cp ON cp.id = c.pool_id
                WHERE c.commitment_rid = $1
            """, rid)
        if not row:
            raise HTTPException(status_code=404, detail=f"Commitment not found: {rid}")
        return _row_to_commitment(row)

    @router.patch("/{rid}/state", response_model=CommitmentResponse)
    async def transition_state(rid: str, body: StateTransitionRequest):
        """Steward-controlled state transition. Validates against the state machine."""
        new_state = body.new_state.upper()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM commitments WHERE commitment_rid = $1", rid
            )
            if not row:
                raise HTTPException(status_code=404, detail=f"Commitment not found: {rid}")

            current_state = row["state"]
            allowed = _VALID_TRANSITIONS.get(current_state, set())
            if new_state not in allowed:
                raise HTTPException(
                    status_code=409,
                    detail=f"Invalid transition {current_state} → {new_state}. Allowed: {sorted(allowed) or 'none (terminal state)'}",
                )

            updated = await conn.fetchrow("""
                UPDATE commitments
                SET state = $2::commitment_state, updated_at = NOW()
                WHERE commitment_rid = $1
                RETURNING *
            """, rid, new_state)

            await conn.execute("""
                INSERT INTO commitment_state_log
                    (commitment_rid, from_state, to_state, actor, reason)
                VALUES ($1, $2::commitment_state, $3::commitment_state, $4, $5)
            """, rid, current_state, new_state, body.actor, body.reason)

        logger.info(f"commitment.state_transition rid={rid} {current_state}→{new_state} actor={body.actor}")
        await emit_domain_event("commitment", "UPDATE", rid, {
            "commitment_rid": rid, "state": new_state,
            "state_transition": {"from_state": current_state, "to_state": new_state,
                                 "actor": body.actor, "reason": body.reason,
                                 "created_at": datetime.now(timezone.utc).isoformat()},
        })
        return _row_to_commitment(updated)

    @router.post("/{rid}/link-evidence", response_model=CommitmentResponse)
    async def link_evidence(rid: str, body: EvidenceLinkRequest):
        """Attach an Evidence entity to a commitment and transition to EVIDENCE_LINKED."""
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM commitments WHERE commitment_rid = $1", rid
            )
            if not row:
                raise HTTPException(status_code=404, detail=f"Commitment not found: {rid}")

            if row["state"] not in ("ACTIVE", "EVIDENCE_LINKED"):
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot link evidence in state {row['state']}. Commitment must be ACTIVE or EVIDENCE_LINKED.",
                )

            # Verify evidence entity exists
            evidence = await conn.fetchrow(
                "SELECT fuseki_uri FROM entity_registry WHERE fuseki_uri = $1",
                body.evidence_uri,
            )
            if not evidence:
                raise HTTPException(status_code=404, detail=f"Evidence entity not found: {body.evidence_uri}")

            updated = await conn.fetchrow("""
                UPDATE commitments
                SET evidence_uri = $2, state = 'EVIDENCE_LINKED'::commitment_state, updated_at = NOW()
                WHERE commitment_rid = $1
                RETURNING *
            """, rid, body.evidence_uri)

            await conn.execute("""
                INSERT INTO commitment_state_log
                    (commitment_rid, from_state, to_state, actor, reason)
                VALUES ($1, $2::commitment_state, 'EVIDENCE_LINKED', $3, 'evidence linked')
            """, rid, row["state"], body.actor)

            # Insert proves_commitment relationship into entity_relationships
            try:
                await conn.execute("""
                    INSERT INTO entity_relationships (subject_uri, predicate, object_uri, source)
                    VALUES ($1, 'proves_commitment', $2, 'commitment_registry')
                    ON CONFLICT DO NOTHING
                """, body.evidence_uri, rid)
            except Exception as e:
                logger.warning(f"Failed to create proves_commitment relationship: {e}")

        logger.info(f"commitment.link_evidence rid={rid} evidence={body.evidence_uri}")
        await emit_domain_event("commitment", "UPDATE", rid, {
            "commitment_rid": rid, "state": "EVIDENCE_LINKED",
            "evidence_uri": body.evidence_uri,
            "state_transition": {"from_state": row["state"], "to_state": "EVIDENCE_LINKED",
                                 "actor": body.actor, "reason": "evidence linked",
                                 "created_at": datetime.now(timezone.utc).isoformat()},
        })
        return _row_to_commitment(updated)

    @router.post("/{rid}/create-claim")
    async def create_claim_from_commitment(rid: str, body: CreateClaimRequest = CreateClaimRequest()):
        """Create a claim entity from a VERIFIED commitment for EAS attestation.

        Reads the commitment, creates a claim with type 'governance',
        embeds source_commitment_rid in claim metadata.
        Returns claim_rid for the existing EAS attest.ts pipeline.
        """
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT c.*, cp.pool_rid AS pool_rid_text
                FROM commitments c
                LEFT JOIN commitment_pools cp ON cp.id = c.pool_id
                WHERE c.commitment_rid = $1
            """, rid)
            if not row:
                raise HTTPException(status_code=404, detail=f"Commitment not found: {rid}")

            if row["state"] not in ("VERIFIED", "ACTIVE", "EVIDENCE_LINKED"):
                raise HTTPException(
                    status_code=409,
                    detail=f"Commitment must be VERIFIED, ACTIVE, or EVIDENCE_LINKED to create a claim. Current state: {row['state']}",
                )

            import json as _json
            meta = row["metadata"]
            if isinstance(meta, str):
                meta = _json.loads(meta)

            # Build claim statement from commitment
            statement = f"Commitment: {row['title']}"
            if row.get("description"):
                statement += f" — {row['description']}"

            # Derive claim RID
            claim_h = hashlib.sha256(f"claim:commitment:{rid}".encode()).hexdigest()[:32]
            claim_rid = f"orn:koi-net.claim:{claim_h}"

            # Create the claim
            claim_meta = {
                "source_commitment_rid": rid,
                "pledger_uri": row["pledger_uri"],
                "offer_type": row["offer_type"],
                "commitment_state": row["state"],
                **({"pool_rid": row.get("pool_rid_text")} if row.get("pool_rid_text") else {}),
            }

            # operator_uri has FK to entity_registry — use pledger_uri (guaranteed to exist)
            try:
                await conn.execute("""
                    INSERT INTO claims
                        (claim_rid, statement, claimant_uri, claim_type, verification, operator_uri, metadata, created_by)
                    VALUES ($1, $2, $3, 'governance', 'self_reported', $3, $4::jsonb, $5)
                    ON CONFLICT (claim_rid) DO UPDATE SET updated_at = NOW()
                """,
                    claim_rid,
                    statement,
                    row["pledger_uri"],
                    _json_dumps(claim_meta),
                    body.actor or f"commitment-bridge:{rid}",
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to create claim: {e}")

            # Link claim to commitment subject via 'about' relationship
            try:
                await conn.execute("""
                    INSERT INTO entity_relationships (subject_uri, predicate, object_uri, source)
                    VALUES ($1, 'about', $2, 'commitment_claim_bridge')
                    ON CONFLICT DO NOTHING
                """, claim_rid, rid)
            except Exception:
                pass

        logger.info(f"commitment.create_claim commitment_rid={rid} claim_rid={claim_rid}")
        return {
            "claim_rid": claim_rid,
            "commitment_rid": rid,
            "statement": statement,
            "claim_type": "governance",
            "verification": "self_reported",
            "metadata": claim_meta,
        }

    # ------------------------------------------------------------------ #
    # Metadata merge (for recording mint tx, token addresses, etc.)      #
    # ------------------------------------------------------------------ #

    class CommitmentMetadataUpdate(BaseModel):
        metadata: Dict[str, Any] = Field(..., description="Partial metadata to merge into existing")

    @router.patch("/{rid}/metadata")
    async def update_commitment_metadata(rid: str, body: CommitmentMetadataUpdate):
        """Merge partial metadata into an existing commitment's metadata field."""
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT metadata FROM commitments WHERE commitment_rid = $1", rid
            )
            if not row:
                raise HTTPException(status_code=404, detail=f"Commitment not found: {rid}")

            import json
            existing = json.loads(row["metadata"]) if row["metadata"] else {}
            existing.update(body.metadata)

            updated = await conn.fetchrow("""
                UPDATE commitments
                SET metadata = $2::jsonb, updated_at = NOW()
                WHERE commitment_rid = $1
                RETURNING *
            """, rid, _json_dumps(existing))

        logger.info(f"commitment.metadata_update rid={rid} keys={list(body.metadata.keys())}")
        return _row_to_commitment(updated)

    return router


# ---------------------------------------------------------------------------
# Pool sub-router (separate prefix)
# ---------------------------------------------------------------------------

def create_pool_router(pool, caps=None):
    """Return an APIRouter for CommitmentPool endpoints."""
    router = APIRouter(prefix="/pools", tags=["commitment-pools"])

    from api.federation_events import emit_domain_event

    @router.get("/", response_model=List[PoolResponse])
    async def list_pools(
        state: Optional[str] = None,
        bioregion_uri: Optional[str] = None,
        limit: int = Query(default=50, le=200),
        offset: int = Query(default=0, ge=0),
    ):
        """List commitment pools with optional filters."""
        conditions: list[str] = []
        params: list = []
        i = 1
        if state:
            conditions.append(f"state = ${i}")
            params.append(state)
            i += 1
        if bioregion_uri:
            conditions.append(f"bioregion_uri = ${i}")
            params.append(bioregion_uri)
            i += 1
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.extend([limit, offset])
        async with pool.acquire() as conn:
            rows = await conn.fetch(f"""
                SELECT * FROM commitment_pools
                {where}
                ORDER BY created_at DESC
                LIMIT ${i} OFFSET ${i+1}
            """, *params)
        return [_row_to_pool(r) for r in rows]

    @router.post("/create", response_model=PoolResponse, status_code=201)
    async def create_pool(body: PoolCreateRequest):
        """Create a new commitment pool."""
        rid = _pool_rid(body.name, body.steward_uri or "")
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO commitment_pools
                    (pool_rid, name, description, steward_uri, bioregion_uri,
                     activation_threshold_pct, activation_threshold_count,
                     demurrage_rate_monthly, state, metadata, created_by)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'forming',$9::jsonb,$10)
                ON CONFLICT (pool_rid) DO UPDATE SET updated_at = NOW()
                RETURNING *
            """,
                rid, body.name, body.description, body.steward_uri, body.bioregion_uri,
                body.activation_threshold_pct, body.activation_threshold_count,
                body.demurrage_rate_monthly, _json_dumps(body.metadata), body.created_by,
            )

            await conn.execute("""
                INSERT INTO commitment_pool_events (pool_rid, event_type, actor)
                VALUES ($1, 'created', $2)
            """, rid, body.created_by)

            # Write governs_pool relationship
            if body.steward_uri:
                try:
                    await conn.execute("""
                        INSERT INTO entity_relationships (subject_uri, predicate, object_uri, source)
                        VALUES ($1, 'governs_pool', $2, 'commitment_registry')
                        ON CONFLICT DO NOTHING
                    """, body.steward_uri, rid)
                except Exception as e:
                    logger.warning(f"Failed to create governs_pool relationship: {e}")

        logger.info(f"pool.create rid={rid} name={body.name}")
        await emit_domain_event("commitment_pool", "NEW", rid, {
            "pool_rid": rid, "name": body.name, "description": body.description,
            "steward_uri": body.steward_uri, "bioregion_uri": body.bioregion_uri,
            "activation_threshold_pct": float(body.activation_threshold_pct) if body.activation_threshold_pct else None,
            "activation_threshold_count": body.activation_threshold_count,
            "demurrage_rate_monthly": float(body.demurrage_rate_monthly) if body.demurrage_rate_monthly else None,
            "state": "forming", "metadata": body.metadata or {},
            "created_by": body.created_by,
        })
        return _row_to_pool(row)

    @router.get("/{rid}", response_model=PoolResponse)
    async def get_pool(rid: str):
        """Fetch a pool by RID."""
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM commitment_pools WHERE pool_rid = $1", rid
            )
        if not row:
            raise HTTPException(status_code=404, detail=f"Pool not found: {rid}")
        return _row_to_pool(row)

    @router.post("/{rid}/pledge", response_model=Dict[str, Any])
    async def add_pledge_to_pool(rid: str, body: PledgeToPoolRequest):
        """Add an existing VERIFIED or PROPOSED commitment to this pool."""
        async with pool.acquire() as conn:
            pool_row = await conn.fetchrow(
                "SELECT id, state FROM commitment_pools WHERE pool_rid = $1", rid
            )
            if not pool_row:
                raise HTTPException(status_code=404, detail=f"Pool not found: {rid}")
            if pool_row["state"] not in ("forming", "active"):
                raise HTTPException(status_code=409, detail=f"Pool is {pool_row['state']}; cannot add pledges.")

            commitment = await conn.fetchrow(
                "SELECT id, state FROM commitments WHERE commitment_rid = $1",
                body.commitment_rid,
            )
            if not commitment:
                raise HTTPException(status_code=404, detail=f"Commitment not found: {body.commitment_rid}")
            if commitment["state"] not in ("PROPOSED", "VERIFIED"):
                raise HTTPException(
                    status_code=409,
                    detail=f"Commitment is {commitment['state']}; only PROPOSED or VERIFIED can be added to a pool.",
                )

            await conn.execute("""
                UPDATE commitments SET pool_id = $1, updated_at = NOW()
                WHERE commitment_rid = $2
            """, pool_row["id"], body.commitment_rid)

            await conn.execute("""
                INSERT INTO commitment_pool_events (pool_rid, event_type, actor, payload)
                VALUES ($1, 'pledge_added', $2, $3::jsonb)
            """, rid, body.actor, _json_dumps({"commitment_rid": body.commitment_rid}))

            # Write aggregates_commitments relationship
            try:
                await conn.execute("""
                    INSERT INTO entity_relationships (subject_uri, predicate, object_uri, source)
                    VALUES ($1, 'aggregates_commitments', $2, 'commitment_registry')
                    ON CONFLICT DO NOTHING
                """, rid, body.commitment_rid)
            except Exception as e:
                logger.warning(f"Failed to create aggregates_commitments relationship: {e}")

            # Check if threshold is now met and auto-activate
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM commitments WHERE pool_id = $1", pool_row["id"]
            )
            verified = await conn.fetchval(
                "SELECT COUNT(*) FROM commitments WHERE pool_id = $1 AND state IN ('VERIFIED','ACTIVE','EVIDENCE_LINKED','REDEEMED')",
                pool_row["id"],
            )
            pool_full = await conn.fetchrow(
                "SELECT * FROM commitment_pools WHERE pool_rid = $1", rid
            )
            threshold_met = _check_threshold(pool_full, int(total), int(verified))
            activated = False
            if threshold_met and pool_full["state"] == "forming":
                await conn.execute(
                    "UPDATE commitment_pools SET state = 'active', updated_at = NOW() WHERE pool_rid = $1",
                    rid,
                )
                await conn.execute("""
                    INSERT INTO commitment_pool_events (pool_rid, event_type, actor, payload)
                    VALUES ($1, 'activated', 'system', $2::jsonb)
                """, rid, _json_dumps({"total": total, "verified": verified}))
                activated = True
                logger.info(f"pool.activated rid={rid} total={total} verified={verified}")

        return {
            "pool_rid": rid,
            "commitment_rid": body.commitment_rid,
            "pool_activated": activated,
            "total_pledges": int(total),
            "verified_pledges": int(verified),
        }

    @router.get("/{rid}/status")
    async def pool_status(rid: str):
        """Pool summary: state, pledge counts by state, threshold progress."""
        async with pool.acquire() as conn:
            pool_row = await conn.fetchrow(
                "SELECT * FROM commitment_pools WHERE pool_rid = $1", rid
            )
            if not pool_row:
                raise HTTPException(status_code=404, detail=f"Pool not found: {rid}")

            counts = await conn.fetch("""
                SELECT state::text, COUNT(*) AS n
                FROM commitments WHERE pool_id = $1
                GROUP BY state
            """, pool_row["id"])

        state_counts = {r["state"]: int(r["n"]) for r in counts}
        total = sum(state_counts.values())
        verified_states = {"VERIFIED", "ACTIVE", "EVIDENCE_LINKED", "REDEEMED"}
        verified = sum(state_counts.get(s, 0) for s in verified_states)
        pct = round(verified / total * 100, 1) if total else 0.0

        return {
            "pool_rid": rid,
            "pool_state": pool_row["state"],
            "name": pool_row["name"],
            "total_pledges": total,
            "verified_pledges": verified,
            "threshold_pct_required": float(pool_row["activation_threshold_pct"]),
            "threshold_pct_current": pct,
            "threshold_met": pct >= float(pool_row["activation_threshold_pct"]),
            "pledge_counts_by_state": state_counts,
            "demurrage_rate_monthly": float(pool_row["demurrage_rate_monthly"]),
        }

    return router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_threshold(pool_row, total: int, verified: int) -> bool:
    if total == 0:
        return False
    if pool_row["activation_threshold_count"] is not None:
        return verified >= pool_row["activation_threshold_count"]
    return (verified / total * 100) >= float(pool_row["activation_threshold_pct"])


def _row_to_commitment(row) -> CommitmentResponse:
    import json as _json
    meta = row["metadata"]
    if isinstance(meta, str):
        meta = _json.loads(meta)
    return CommitmentResponse(
        commitment_rid=row["commitment_rid"],
        pledger_uri=row["pledger_uri"],
        pool_rid=row.get("pool_rid_text") or row.get("pool_rid"),
        title=row["title"],
        description=row.get("description"),
        offer_type=row["offer_type"],
        quantity=float(row["quantity"]) if row.get("quantity") is not None else None,
        unit=row.get("unit"),
        validity_start=row.get("validity_start"),
        validity_end=row.get("validity_end"),
        state=row["state"],
        scope=row.get("scope"),
        evidence_uri=row.get("evidence_uri"),
        metadata=meta or {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_pool(row) -> PoolResponse:
    import json as _json
    meta = row["metadata"]
    if isinstance(meta, str):
        meta = _json.loads(meta)
    return PoolResponse(
        pool_rid=row["pool_rid"],
        name=row["name"],
        description=row.get("description"),
        steward_uri=row.get("steward_uri"),
        bioregion_uri=row.get("bioregion_uri"),
        activation_threshold_pct=float(row["activation_threshold_pct"]),
        activation_threshold_count=row.get("activation_threshold_count"),
        demurrage_rate_monthly=float(row["demurrage_rate_monthly"]),
        state=row["state"],
        scope=row.get("scope"),
        metadata=meta or {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _json_dumps(obj) -> str:
    import json
    return json.dumps(obj)


# ---------------------------------------------------------------------------
# Routing scorer v0
# ---------------------------------------------------------------------------

async def _score_pools(db_pool, draft: "RoutingSuggestionRequest") -> List[PoolSuggestion]:
    """Score all forming/active pools against a draft commitment.

    Factors (v0):
      same_bioregion      +30  exact bioregion_uri match
      umbrella_bioregion   +15  parent via `broader` predicate (reduces same_bioregion to 0)
      offer_need_overlap   +25  routing_tags ∩ pool need_tags / max(len)
      timeframe_overlap    +15  date range intersection / commitment range
      capacity_fit         +20  value fits remaining capacity
      governance_compat    +10  same governance_membrane (inactive v0 — returns 0)

    Hard excludes: no remaining capacity, outside timeframe entirely.
    """
    import json as _json
    from datetime import date

    meta = draft.metadata or {}
    draft_bioregion = meta.get("bioregion_uri", "")
    draft_tags = set(meta.get("routing_tags", []))
    draft_value = meta.get("estimated_value_usd", 0) or 0
    draft_start = draft.validity_start
    draft_end = draft.validity_end
    draft_governance = meta.get("governance_membrane", "")

    async with db_pool.acquire() as conn:
        # Fetch all forming/active pools
        pool_rows = await conn.fetch(
            "SELECT * FROM commitment_pools WHERE state IN ('forming', 'active')"
        )

        # Pre-fetch broader relationships for bioregion matching
        broader_parents = {}
        if draft_bioregion:
            parents = await conn.fetch("""
                SELECT object_uri FROM entity_relationships
                WHERE subject_uri = $1 AND predicate = 'broader'
            """, draft_bioregion)
            broader_parents = {r["object_uri"] for r in parents}

            # Also check if pool bioregions are children of draft bioregion
            # (draft is in Salish Sea, pool is in Cascadia which is broader)
            children = await conn.fetch("""
                SELECT subject_uri FROM entity_relationships
                WHERE object_uri = $1 AND predicate = 'broader'
            """, draft_bioregion)
            broader_parents.update(r["subject_uri"] for r in children)

    suggestions = []
    for pr in pool_rows:
        pool_meta_raw = pr["metadata"]
        if isinstance(pool_meta_raw, str):
            pool_meta = _json.loads(pool_meta_raw)
        else:
            pool_meta = pool_meta_raw or {}

        pool_bioregion = pr["bioregion_uri"] or ""
        pool_need_tags = set(pool_meta.get("need_tags", []))
        pool_capacity = pool_meta.get("capacity_usd", 0) or 0
        pool_remaining = pool_meta.get("remaining_capacity_usd", pool_capacity) or pool_capacity
        pool_threshold = pool_meta.get("activation_threshold_usd", 0) or 0
        pool_governance = pool_meta.get("governance_membrane", "")
        pool_start_str = pool_meta.get("validity_start")
        pool_end_str = pool_meta.get("validity_end")

        hard_excludes = []
        breakdown = ScoreBreakdown()

        # --- Same bioregion (+30) or umbrella (+15) ---
        if draft_bioregion and pool_bioregion:
            if draft_bioregion == pool_bioregion:
                breakdown.same_bioregion = 30
            elif pool_bioregion in broader_parents or draft_bioregion in broader_parents:
                breakdown.same_bioregion = 15
            # Also check if pool's bioregion is a parent of draft's bioregion
            # by checking broader edges between pool_bioregion and draft_bioregion
            elif not breakdown.same_bioregion:
                async with db_pool.acquire() as conn:
                    link = await conn.fetchval("""
                        SELECT 1 FROM entity_relationships
                        WHERE (subject_uri = $1 AND object_uri = $2 AND predicate = 'broader')
                           OR (subject_uri = $2 AND object_uri = $1 AND predicate = 'broader')
                        LIMIT 1
                    """, draft_bioregion, pool_bioregion)
                    if link:
                        breakdown.same_bioregion = 15

        # --- Offer/need taxonomy overlap (+25) ---
        if draft_tags and pool_need_tags:
            overlap = len(draft_tags & pool_need_tags)
            max_len = max(len(draft_tags), len(pool_need_tags))
            if max_len > 0:
                breakdown.offer_need_overlap = round(25 * overlap / max_len)

        # --- Timeframe overlap (+15) ---
        if draft_start and draft_end:
            # Parse pool dates if present
            p_start = None
            p_end = None
            if pool_start_str:
                try:
                    p_start = datetime.fromisoformat(pool_start_str)
                except (ValueError, TypeError):
                    pass
            if pool_end_str:
                try:
                    p_end = datetime.fromisoformat(pool_end_str)
                except (ValueError, TypeError):
                    pass

            if p_start and p_end:
                # Both have date ranges — compute overlap
                overlap_start = max(draft_start, p_start)
                overlap_end = min(draft_end, p_end)
                if overlap_end > overlap_start:
                    overlap_days = (overlap_end - overlap_start).days
                    total_days = (draft_end - draft_start).days or 1
                    breakdown.timeframe_overlap = round(15 * overlap_days / total_days)
                else:
                    hard_excludes.append("outside_timeframe")
            else:
                # Pool has no date constraints — full overlap assumed
                breakdown.timeframe_overlap = 15

        # --- Capacity fit (+20) ---
        if draft_value > 0 and pool_remaining > 0:
            if draft_value > pool_remaining:
                hard_excludes.append("exceeds_capacity")
            else:
                # Score higher when commitment moves pool closer to threshold
                if pool_threshold > 0:
                    # How much does this pledge contribute toward threshold?
                    contribution = min(draft_value / pool_threshold, 1.0)
                    breakdown.capacity_fit = round(20 * min(contribution + 0.5, 1.0))
                else:
                    # No threshold — just check it fits
                    fit_ratio = 1.0 - (draft_value / pool_remaining)
                    breakdown.capacity_fit = round(20 * max(fit_ratio, 0.3))
        elif draft_value > 0 and pool_remaining <= 0:
            hard_excludes.append("no_capacity")

        # --- Governance compatibility (+10, inactive v0) ---
        # Returns 0 until governance_membrane is populated on both sides
        breakdown.governance_compat = 0

        # --- Total and recommendation ---
        total_score = (
            breakdown.same_bioregion
            + breakdown.offer_need_overlap
            + breakdown.timeframe_overlap
            + breakdown.capacity_fit
            + breakdown.governance_compat
        )

        # Build explanation
        parts = []
        if breakdown.same_bioregion == 30:
            parts.append("same bioregion")
        elif breakdown.same_bioregion == 15:
            parts.append("umbrella bioregion match")
        if breakdown.offer_need_overlap > 0:
            overlap_count = len(draft_tags & pool_need_tags) if draft_tags and pool_need_tags else 0
            parts.append(f"{overlap_count} tag overlap")
        if breakdown.timeframe_overlap > 0:
            parts.append("overlapping timeframe")
        if breakdown.capacity_fit > 0:
            parts.append("within capacity")
        if hard_excludes:
            parts.append(f"excludes: {', '.join(hard_excludes)}")

        explanation = "; ".join(parts) if parts else "no scoring factors matched"

        suggestions.append(PoolSuggestion(
            pool_rid=pr["pool_rid"],
            pool_name=pr["name"],
            total_score=total_score,
            score_breakdown=breakdown,
            hard_excludes=hard_excludes,
            recommended=total_score >= 60 and not hard_excludes,
            explanation=explanation,
        ))

    # Sort by total_score desc, then pool_rid asc for deterministic tie-break
    suggestions.sort(key=lambda s: (-s.total_score, s.pool_rid))
    # Filter out hard-excluded from recommendations but still return them
    return suggestions
