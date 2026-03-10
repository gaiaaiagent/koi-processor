"""Claims Engine V1 endpoints: create, search, verify, evidence linking, extraction, anchoring.

Implements the Claims Engine API:
  POST /claims/           — create a new impact claim (entity + graph edges + SQL)
  GET  /claims/           — list/search claims with filters
  GET  /claims/{rid}      — get claim with linked evidence entities
  PATCH /claims/{rid}/verify — advance verification level
  POST /claims/{rid}/evidence — attach evidence entity
  GET  /claims/{rid}/history — verification audit log
  POST /claims/extract    — AI extraction from document text
  POST /claims/{rid}/prepare-anchor — compute content hash for ledger anchoring

All verification transitions are recorded in claim_state_log (insert-only).
Claims are first-class KOI entities with graph edges (makes_claim, about, evidences_claim).
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ClaimCreateRequest(BaseModel):
    claimant_uri: str = Field(..., description="entity_registry.fuseki_uri of the claimant (must exist)")
    statement: str = Field(..., min_length=10, max_length=5000, description="Plain-language impact assertion")
    claim_type: str = Field("ecological", description="ecological | social | financial | governance")
    about_uri: Optional[str] = Field(None, description="Entity URI this claim is about (Location, Org, Project, etc.)")
    source_document: Optional[str] = Field(None, description="Document RID or path the claim was extracted from")
    ai_confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="AI extraction confidence (NULL if manual)")
    supersedes_rid: Optional[str] = Field(None, description="Previous version claim_rid (for versioning)")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Extensible fields: quantity, unit, dates, SDGs, methodology, etc.")
    created_by: Optional[str] = None


class ClaimResponse(BaseModel):
    claim_rid: str
    entity_uri: Optional[str]
    claimant_uri: str
    statement: str
    claim_type: str
    verification: str
    source_document: Optional[str]
    ai_confidence: Optional[float]
    content_hash: Optional[str]
    ledger_iri: Optional[str]
    supersedes_rid: Optional[str]
    metadata: Dict[str, Any]
    evidence: Optional[List[Dict[str, Any]]] = None
    created_at: datetime
    updated_at: datetime


class VerifyRequest(BaseModel):
    new_level: str = Field(..., description="Target: peer_reviewed | verified | ledger_anchored | withdrawn")
    actor: Optional[str] = None
    reason: Optional[str] = None


class EvidenceLinkRequest(BaseModel):
    evidence_uri: str = Field(..., description="entity_registry.fuseki_uri of the Evidence entity")
    actor: Optional[str] = None


class ClaimExtractRequest(BaseModel):
    document_text: str = Field(..., min_length=50, description="Document text to extract claims from")
    source_document: str = Field(..., description="Document RID or path (required for provenance)")
    auto_create: bool = Field(False, description="If true, automatically create extracted claims")
    confidence_threshold: float = Field(0.7, ge=0.0, le=1.0, description="Minimum confidence for extraction")


class AnchorPrepareResponse(BaseModel):
    claim_rid: str
    content_hash: str
    predicted_ledger_iri: Optional[str] = None
    ready_to_anchor: bool
    reason: Optional[str] = None


class AnchorResponse(BaseModel):
    claim_rid: str
    content_hash: str
    ledger_iri: str
    ledger_timestamp: Optional[str] = None
    tx_hash: Optional[str] = None


# ---------------------------------------------------------------------------
# Valid verification transitions (progressive, per Smith/Bennetts)
# ---------------------------------------------------------------------------

_VALID_TRANSITIONS = {
    "self_reported":   {"peer_reviewed", "withdrawn"},
    "peer_reviewed":   {"verified", "withdrawn"},
    "verified":        {"ledger_anchored"},
    "ledger_anchored": set(),              # terminal — on-chain is permanent
    "withdrawn":       set(),              # terminal
}

# Transitions that require preconditions beyond just the state machine
# Allowed entity types for the 'about' predicate on claims
_ABOUT_ALLOWED_TYPES = {
    "Practice", "Pattern", "CaseStudy", "Concept", "Project",
    "Bioregion", "Location", "Organization", "Person",
}

_TRANSITION_PRECONDITIONS = {
    "ledger_anchored": lambda row: (
        bool(row.get("content_hash") and row.get("ledger_iri")),
        "Cannot transition to ledger_anchored: requires content_hash and ledger_iri "
        "(call prepare-anchor first, then actual anchoring when service account is funded)"
    ),
}


def _canonical_json(claimant_uri: str, statement: str, claim_type: str,
                    about_uri: str | None, metadata: dict) -> str:
    """Deterministic JSON serialization of claim content fields.

    Includes about_uri so that identical statements about different entities
    produce distinct RIDs instead of collapsing into the same claim.
    """
    obj = {
        "about_uri": about_uri or "",
        "claimant_uri": claimant_uri,
        "claim_type": claim_type,
        "metadata": metadata,
        "statement": statement,
    }
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(',', ':'))


def _claim_rid(claimant_uri: str, statement: str, claim_type: str,
               about_uri: str | None, metadata: dict) -> str:
    """Content-addressable RID: hash of all content fields including about_uri."""
    canonical = _canonical_json(claimant_uri, statement, claim_type, about_uri, metadata)
    h = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    return f"orn:koi-net.claim:{h}"


def _json_dumps(obj) -> str:
    return json.dumps(obj)


def _row_to_claim(row, evidence=None) -> ClaimResponse:
    meta = row["metadata"]
    if isinstance(meta, str):
        meta = json.loads(meta)
    return ClaimResponse(
        claim_rid=row["claim_rid"],
        entity_uri=row.get("entity_uri"),
        claimant_uri=row["claimant_uri"],
        statement=row["statement"],
        claim_type=row["claim_type"],
        verification=row["verification"],
        source_document=row.get("source_document"),
        ai_confidence=float(row["ai_confidence"]) if row.get("ai_confidence") is not None else None,
        content_hash=row.get("content_hash"),
        ledger_iri=row.get("ledger_iri"),
        supersedes_rid=row.get("supersedes_rid"),
        metadata=meta or {},
        evidence=evidence,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_router(pool, caps=None):
    """Return an APIRouter for claims engine endpoints."""
    router = APIRouter(prefix="/claims", tags=["claims"])

    # Lazy import to avoid circular deps at module level
    _entity_helpers = {}

    def _get_entity_helpers():
        if not _entity_helpers:
            from api.personal_ingest_api import generate_entity_uri, normalize_entity_text
            _entity_helpers['generate_entity_uri'] = generate_entity_uri
            _entity_helpers['normalize_entity_text'] = normalize_entity_text
        return _entity_helpers

    # ------------------------------------------------------------------ #
    # Create claim                                                         #
    # ------------------------------------------------------------------ #

    @router.post("/", response_model=ClaimResponse, status_code=201)
    async def create_claim(body: ClaimCreateRequest):
        """Create a new impact claim. Registers as entity, writes graph edges."""
        helpers = _get_entity_helpers()
        generate_entity_uri = helpers['generate_entity_uri']
        normalize_entity_text = helpers['normalize_entity_text']

        async with pool.acquire() as conn:
            # 1. Verify claimant exists (before transaction — read-only)
            claimant = await conn.fetchrow(
                "SELECT fuseki_uri FROM entity_registry WHERE fuseki_uri = $1",
                body.claimant_uri,
            )
            if not claimant:
                raise HTTPException(status_code=404, detail=f"Claimant entity not found: {body.claimant_uri}")

            # 2. Validate about_uri exists and has an allowed type before it enters the RID hash
            if body.about_uri:
                about_entity = await conn.fetchrow(
                    "SELECT fuseki_uri, entity_type FROM entity_registry WHERE fuseki_uri = $1",
                    body.about_uri,
                )
                if not about_entity:
                    raise HTTPException(status_code=404, detail=f"About entity not found: {body.about_uri}")
                if about_entity["entity_type"] not in _ABOUT_ALLOWED_TYPES:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Entity {body.about_uri} is type '{about_entity['entity_type']}', "
                               f"not valid for 'about' predicate. Allowed: {sorted(_ABOUT_ALLOWED_TYPES)}",
                    )

            # 3. Generate content-addressable RID (includes about_uri in identity)
            claim_rid = _claim_rid(body.claimant_uri, body.statement, body.claim_type,
                                   body.about_uri, body.metadata)

            # 3. Idempotency check (before transaction — read-only)
            existing = await conn.fetchrow(
                "SELECT * FROM claims WHERE claim_rid = $1", claim_rid
            )
            if existing:
                logger.info(f"claim.idempotent_hit rid={claim_rid}")
                return _row_to_claim(existing)

            # All writes in a single transaction — no partial state on failure.
            # Catch UniqueViolationError for concurrent-request idempotency:
            # two identical POSTs can both pass the preflight check; the loser
            # hits the unique constraint and falls through to return the winner's row.
            try:
                async with conn.transaction():
                    # 4. Register claim as entity (URI derived from RID for version isolation)
                    entity_uri = generate_entity_uri(claim_rid, 'Claim')
                    await conn.execute("""
                        INSERT INTO entity_registry (fuseki_uri, entity_text, entity_type, normalized_text)
                        VALUES ($1, $2, 'Claim', $3)
                        ON CONFLICT (fuseki_uri) DO NOTHING
                    """, entity_uri, body.statement[:200], normalize_entity_text(body.statement[:200]))

                    # 5. Write makes_claim relationship edge
                    await conn.execute("""
                        INSERT INTO entity_relationships (subject_uri, predicate, object_uri, confidence, source)
                        VALUES ($1, 'makes_claim', $2, 1.0, 'claims_engine')
                        ON CONFLICT DO NOTHING
                    """, body.claimant_uri, entity_uri)

                    # 6. Link claim to subject entity via 'about' predicate (already validated above)
                    if body.about_uri:
                        await conn.execute("""
                            INSERT INTO entity_relationships (subject_uri, predicate, object_uri, confidence, source)
                            VALUES ($1, 'about', $2, 1.0, 'claims_engine')
                            ON CONFLICT DO NOTHING
                        """, entity_uri, body.about_uri)

                    # 7. Handle versioning — link to superseded claim
                    if body.supersedes_rid:
                        old = await conn.fetchrow(
                            "SELECT entity_uri FROM claims WHERE claim_rid = $1", body.supersedes_rid
                        )
                        if old and old['entity_uri']:
                            await conn.execute("""
                                INSERT INTO entity_relationships (subject_uri, predicate, object_uri, confidence, source)
                                VALUES ($1, 'supersedes_claim', $2, 1.0, 'claims_engine')
                                ON CONFLICT DO NOTHING
                            """, entity_uri, old['entity_uri'])

                    # 8. Insert claim row
                    row = await conn.fetchrow("""
                        INSERT INTO claims (claim_rid, entity_uri, claimant_uri, statement,
                                            claim_type, source_document, ai_confidence,
                                            supersedes_rid, metadata, created_by)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)
                        RETURNING *
                    """,
                        claim_rid, entity_uri, body.claimant_uri, body.statement,
                        body.claim_type, body.source_document, body.ai_confidence,
                        body.supersedes_rid, _json_dumps(body.metadata), body.created_by,
                    )

                    # 9. Log initial state
                    await conn.execute("""
                        INSERT INTO claim_state_log (claim_rid, from_state, to_state, actor, reason)
                        VALUES ($1, NULL, 'self_reported', $2, 'created')
                    """, claim_rid, body.created_by)
            except asyncpg.UniqueViolationError:
                # Concurrent insert won the race — return the winner's row
                logger.info(f"claim.concurrent_idempotent rid={claim_rid}")
                existing = await conn.fetchrow(
                    "SELECT * FROM claims WHERE claim_rid = $1", claim_rid
                )
                if existing:
                    return _row_to_claim(existing)
                raise  # should not happen — unique violation implies the row exists

        logger.info(f"claim.create rid={claim_rid} claimant={body.claimant_uri} type={body.claim_type}")
        return _row_to_claim(row)

    # ------------------------------------------------------------------ #
    # List / search claims                                                 #
    # ------------------------------------------------------------------ #

    @router.get("/", response_model=List[ClaimResponse])
    async def list_claims(
        verification: Optional[str] = Query(None, description="Filter by verification level"),
        claim_type: Optional[str] = Query(None, description="Filter by claim type"),
        claimant_uri: Optional[str] = Query(None, description="Filter by claimant"),
        about_uri: Optional[str] = Query(None, description="Filter by about entity (via graph edge)"),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        """List claims with optional filters."""
        async with pool.acquire() as conn:
            conditions = []
            params: list = []
            i = 1

            if verification:
                conditions.append(f"c.verification = ${i}")
                params.append(verification)
                i += 1
            if claim_type:
                conditions.append(f"c.claim_type = ${i}")
                params.append(claim_type)
                i += 1
            if claimant_uri:
                conditions.append(f"c.claimant_uri = ${i}")
                params.append(claimant_uri)
                i += 1
            if about_uri:
                conditions.append(f"""c.entity_uri IN (
                    SELECT subject_uri FROM entity_relationships
                    WHERE predicate = 'about' AND object_uri = ${i}
                )""")
                params.append(about_uri)
                i += 1

            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            params.extend([limit, offset])

            rows = await conn.fetch(f"""
                SELECT c.* FROM claims c
                {where}
                ORDER BY c.created_at DESC
                LIMIT ${i} OFFSET ${i+1}
            """, *params)

        return [_row_to_claim(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Get claim by RID (with evidence)                                     #
    # ------------------------------------------------------------------ #

    @router.get("/{rid}", response_model=ClaimResponse)
    async def get_claim(rid: str):
        """Fetch a claim by RID, including linked evidence entities."""
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM claims WHERE claim_rid = $1", rid
            )
            if not row:
                raise HTTPException(status_code=404, detail=f"Claim not found: {rid}")

            # Fetch evidence entities linked via evidences_claim predicate
            evidence = []
            if row.get("entity_uri"):
                ev_rows = await conn.fetch("""
                    SELECT er.fuseki_uri, er.entity_text, er.entity_type,
                           rel.confidence, rel.source
                    FROM entity_relationships rel
                    JOIN entity_registry er ON er.fuseki_uri = rel.subject_uri
                    WHERE rel.predicate = 'evidences_claim'
                      AND rel.object_uri = $1
                """, row["entity_uri"])
                evidence = [
                    {
                        "uri": ev["fuseki_uri"],
                        "text": ev["entity_text"],
                        "type": ev["entity_type"],
                        "confidence": float(ev["confidence"]) if ev.get("confidence") is not None else None,
                        "source": ev.get("source"),
                    }
                    for ev in ev_rows
                ]

        return _row_to_claim(row, evidence=evidence or None)

    # ------------------------------------------------------------------ #
    # Verify (advance verification level)                                  #
    # ------------------------------------------------------------------ #

    @router.patch("/{rid}/verify", response_model=ClaimResponse)
    async def verify_claim(rid: str, body: VerifyRequest):
        """Advance claim verification level. Validates state machine transitions."""
        new_level = body.new_level.lower()
        async with pool.acquire() as conn:
            # All reads and writes in one transaction with row lock
            async with conn.transaction():
                # FOR UPDATE locks the row — concurrent verify calls serialize here
                row = await conn.fetchrow(
                    "SELECT * FROM claims WHERE claim_rid = $1 FOR UPDATE", rid
                )
                if not row:
                    raise HTTPException(status_code=404, detail=f"Claim not found: {rid}")

                current = row["verification"]
                allowed = _VALID_TRANSITIONS.get(current, set())
                if new_level not in allowed:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Invalid transition {current} → {new_level}. Allowed: {sorted(allowed) or 'none (terminal state)'}",
                    )

                # Check preconditions (e.g. ledger_anchored requires content_hash + ledger_iri)
                if new_level in _TRANSITION_PRECONDITIONS:
                    ok, msg = _TRANSITION_PRECONDITIONS[new_level](row)
                    if not ok:
                        raise HTTPException(status_code=409, detail=msg)

                updated = await conn.fetchrow("""
                    UPDATE claims
                    SET verification = $2, updated_at = NOW()
                    WHERE claim_rid = $1
                    RETURNING *
                """, rid, new_level)

                await conn.execute("""
                    INSERT INTO claim_state_log (claim_rid, from_state, to_state, actor, reason)
                    VALUES ($1, $2, $3, $4, $5)
                """, rid, current, new_level, body.actor, body.reason)

        logger.info(f"claim.verify rid={rid} {current}→{new_level} actor={body.actor}")
        return _row_to_claim(updated)

    # ------------------------------------------------------------------ #
    # Link evidence                                                        #
    # ------------------------------------------------------------------ #

    @router.post("/{rid}/evidence", response_model=ClaimResponse)
    async def link_evidence(rid: str, body: EvidenceLinkRequest):
        """Attach an evidence entity to a claim via evidences_claim edge."""
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM claims WHERE claim_rid = $1", rid
            )
            if not row:
                raise HTTPException(status_code=404, detail=f"Claim not found: {rid}")

            # Verify evidence entity exists AND is type Evidence (Finding 5)
            evidence = await conn.fetchrow(
                "SELECT fuseki_uri, entity_type FROM entity_registry WHERE fuseki_uri = $1",
                body.evidence_uri,
            )
            if not evidence:
                raise HTTPException(status_code=404, detail=f"Evidence entity not found: {body.evidence_uri}")
            if evidence["entity_type"] != "Evidence":
                raise HTTPException(
                    status_code=422,
                    detail=f"Entity {body.evidence_uri} is type '{evidence['entity_type']}', not 'Evidence'. "
                           f"Only Evidence entities can be linked via evidences_claim.",
                )

            async with conn.transaction():
                # Write evidences_claim relationship
                await conn.execute("""
                    INSERT INTO entity_relationships (subject_uri, predicate, object_uri, confidence, source)
                    VALUES ($1, 'evidences_claim', $2, 1.0, 'claims_engine')
                    ON CONFLICT DO NOTHING
                """, body.evidence_uri, row["entity_uri"])

                # Log the action
                await conn.execute("""
                    INSERT INTO claim_state_log (claim_rid, from_state, to_state, actor, reason, metadata)
                    VALUES ($1, $2, $2, $3, 'evidence linked', $4::jsonb)
                """, rid, row["verification"], body.actor,
                    _json_dumps({"evidence_uri": body.evidence_uri}))

                # Update timestamp
                updated = await conn.fetchrow("""
                    UPDATE claims SET updated_at = NOW() WHERE claim_rid = $1 RETURNING *
                """, rid)

        logger.info(f"claim.link_evidence rid={rid} evidence={body.evidence_uri}")
        return _row_to_claim(updated)

    # ------------------------------------------------------------------ #
    # History (audit log)                                                  #
    # ------------------------------------------------------------------ #

    @router.get("/{rid}/history")
    async def claim_history(rid: str):
        """Get the verification transition history for a claim."""
        async with pool.acquire() as conn:
            # Verify claim exists
            claim = await conn.fetchrow(
                "SELECT claim_rid FROM claims WHERE claim_rid = $1", rid
            )
            if not claim:
                raise HTTPException(status_code=404, detail=f"Claim not found: {rid}")

            rows = await conn.fetch("""
                SELECT * FROM claim_state_log
                WHERE claim_rid = $1
                ORDER BY created_at ASC
            """, rid)

        return {
            "claim_rid": rid,
            "transitions": [
                {
                    "from_state": r.get("from_state"),
                    "to_state": r["to_state"],
                    "actor": r.get("actor"),
                    "reason": r.get("reason"),
                    "metadata": json.loads(r["metadata"]) if r.get("metadata") and isinstance(r["metadata"], str) else r.get("metadata"),
                    "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                }
                for r in rows
            ],
        }

    # ------------------------------------------------------------------ #
    # Extract claims from document (Phase 2)                               #
    # ------------------------------------------------------------------ #

    @router.post("/extract")
    async def extract_claims(body: ClaimExtractRequest):
        """Extract structured claims from document text using AI."""
        from api.claim_extractor import extract_claims_from_text

        candidates = await extract_claims_from_text(
            document_text=body.document_text,
            source_document=body.source_document,
            confidence_threshold=body.confidence_threshold,
        )

        created_claims = []
        if body.auto_create and candidates:
            for candidate in candidates:
                try:
                    async with pool.acquire() as conn:
                        # Resolve claimant to entity URI
                        claimant_name = candidate.get("claimant_name", "").strip()
                        claimant = await conn.fetchrow("""
                            SELECT fuseki_uri FROM entity_registry
                            WHERE normalized_text = $1 OR entity_text ILIKE $2
                            LIMIT 1
                        """, claimant_name.lower(),
                            f"%{claimant_name}%") if claimant_name else None

                        if not claimant:
                            continue

                        # Resolve about_uri from extracted metadata (Finding 4)
                        about_uri = None
                        meta = candidate.get("metadata", {})
                        subject_location = meta.get("subject_location", "")
                        if subject_location:
                            loc = await conn.fetchrow("""
                                SELECT fuseki_uri FROM entity_registry
                                WHERE entity_type IN ('Location', 'Bioregion')
                                  AND (normalized_text = $1 OR entity_text ILIKE $2)
                                LIMIT 1
                            """, subject_location.lower().strip(),
                                f"%{subject_location}%")
                            if loc:
                                about_uri = loc["fuseki_uri"]

                        create_body = ClaimCreateRequest(
                            claimant_uri=claimant["fuseki_uri"],
                            statement=candidate["statement"],
                            claim_type=candidate.get("claim_type", "ecological"),
                            about_uri=about_uri,
                            source_document=body.source_document,
                            ai_confidence=candidate.get("confidence"),
                            metadata=meta,
                        )
                        # Reuse create endpoint logic
                        result = await create_claim(create_body)
                        created_claims.append(result.claim_rid)
                except Exception as e:
                    logger.warning(f"Failed to auto-create extracted claim: {e}")

        return {
            "candidates": candidates,
            "candidate_count": len(candidates),
            "source_document": body.source_document,
            "auto_created": created_claims if body.auto_create else None,
        }

    # ------------------------------------------------------------------ #
    # Prepare anchor (Phase 4)                                             #
    # ------------------------------------------------------------------ #

    @router.post("/{rid}/prepare-anchor", response_model=AnchorPrepareResponse)
    async def prepare_anchor(rid: str):
        """Non-broadcasting preflight: compute content hash and derive predicted IRI.

        Persists content_hash to the claim but does NOT broadcast to the blockchain.
        """
        from api.ledger_anchor import compute_content_hash, derive_ledger_iri

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM claims WHERE claim_rid = $1", rid
            )
            if not row:
                raise HTTPException(status_code=404, detail=f"Claim not found: {rid}")

            # Compute and store content hash
            content_hash = compute_content_hash(row)

            await conn.execute("""
                UPDATE claims SET content_hash = $2, updated_at = NOW()
                WHERE claim_rid = $1
            """, rid, content_hash)

        # Derive predicted IRI via CLI (non-fatal if regen binary not available)
        predicted_iri = None
        reason = None
        try:
            predicted_iri = derive_ledger_iri(content_hash)
        except RuntimeError as e:
            reason = str(e)
        except Exception as e:
            reason = f"IRI derivation failed: {e}"

        logger.info(f"claim.prepare_anchor rid={rid} hash={content_hash[:16]}... iri={predicted_iri}")
        return AnchorPrepareResponse(
            claim_rid=rid,
            content_hash=content_hash,
            predicted_ledger_iri=predicted_iri,
            ready_to_anchor=predicted_iri is not None,
            reason=reason,
        )

    # ------------------------------------------------------------------ #
    # Anchor (Phase 4 — live broadcast)                                    #
    # ------------------------------------------------------------------ #

    @router.post("/{rid}/anchor", response_model=AnchorResponse)
    async def anchor_claim(rid: str):
        """Anchor a verified claim on the Regen Ledger testnet.

        Precondition: claim must be at 'verified' state.
        Requires content_hash (call prepare-anchor first if missing).
        """
        from api.ledger_anchor import broadcast_anchor, compute_content_hash

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM claims WHERE claim_rid = $1", rid
            )
            if not row:
                raise HTTPException(status_code=404, detail=f"Claim not found: {rid}")

            # Precondition: must be at 'verified' state
            if row["verification"] != "verified":
                raise HTTPException(
                    status_code=409,
                    detail=f"Claim must be at 'verified' state to anchor "
                           f"(current: {row['verification']}). "
                           f"State path: self_reported → peer_reviewed → verified → ledger_anchored",
                )

            # Ensure content_hash exists
            content_hash = row.get("content_hash")
            if not content_hash:
                content_hash = compute_content_hash(row)
                await conn.execute("""
                    UPDATE claims SET content_hash = $2, updated_at = NOW()
                    WHERE claim_rid = $1
                """, rid, content_hash)

        # Broadcast anchor via CLI (blocking — may take up to 30s for confirmation)
        result = await broadcast_anchor(rid, content_hash)

        if not result.get("ready_to_anchor"):
            raise HTTPException(
                status_code=503,
                detail=result.get("reason", "Anchoring not available"),
            )

        # On success: update claim with ledger data and transition state
        # Parse ledger_timestamp string to datetime for TIMESTAMPTZ column
        from datetime import datetime as _dt
        ledger_ts = None
        ts_str = result.get("ledger_timestamp")
        if ts_str:
            try:
                ledger_ts = _dt.fromisoformat(ts_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                ledger_ts = datetime.now(timezone.utc)

        async with pool.acquire() as conn:
            async with conn.transaction():
                # FOR UPDATE lock for concurrent safety
                row = await conn.fetchrow(
                    "SELECT * FROM claims WHERE claim_rid = $1 FOR UPDATE", rid
                )

                await conn.execute("""
                    UPDATE claims
                    SET ledger_iri = $2, ledger_timestamp = $3, content_hash = $4,
                        verification = 'ledger_anchored', updated_at = NOW()
                    WHERE claim_rid = $1
                """, rid, result["ledger_iri"], ledger_ts, content_hash)

                await conn.execute("""
                    INSERT INTO claim_state_log (claim_rid, from_state, to_state, actor, reason, metadata)
                    VALUES ($1, 'verified', 'ledger_anchored', 'ledger_anchor_service',
                            'Anchored on Regen Ledger testnet', $2::jsonb)
                """, rid, json.dumps({
                    "tx_hash": result.get("tx_hash"),
                    "ledger_iri": result["ledger_iri"],
                    "chain_id": os.getenv("REGEN_CHAIN_ID", "regen-upgrade"),
                }))

        logger.info(f"claim.anchored rid={rid} iri={result['ledger_iri']} tx={result.get('tx_hash')}")
        return AnchorResponse(
            claim_rid=rid,
            content_hash=content_hash,
            ledger_iri=result["ledger_iri"],
            ledger_timestamp=result.get("ledger_timestamp"),
            tx_hash=result.get("tx_hash"),
        )

    return router
