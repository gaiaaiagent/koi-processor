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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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
    ready_to_anchor: bool
    reason: Optional[str] = None


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


def _canonical_json(claimant_uri: str, statement: str, claim_type: str, metadata: dict) -> str:
    """Deterministic JSON serialization of claim content fields."""
    obj = {
        "claimant_uri": claimant_uri,
        "statement": statement,
        "claim_type": claim_type,
        "metadata": metadata,
    }
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(',', ':'))


def _claim_rid(claimant_uri: str, statement: str, claim_type: str, metadata: dict) -> str:
    """Content-addressable RID: hash of all content fields."""
    canonical = _canonical_json(claimant_uri, statement, claim_type, metadata)
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
            # 1. Verify claimant exists
            claimant = await conn.fetchrow(
                "SELECT fuseki_uri FROM entity_registry WHERE fuseki_uri = $1",
                body.claimant_uri,
            )
            if not claimant:
                raise HTTPException(status_code=404, detail=f"Claimant entity not found: {body.claimant_uri}")

            # 2. Generate content-addressable RID
            claim_rid = _claim_rid(body.claimant_uri, body.statement, body.claim_type, body.metadata)

            # 3. Idempotency check
            existing = await conn.fetchrow(
                "SELECT * FROM claims WHERE claim_rid = $1", claim_rid
            )
            if existing:
                logger.info(f"claim.idempotent_hit rid={claim_rid}")
                return _row_to_claim(existing)

            # 4. Register claim as entity (URI derived from RID for version isolation)
            entity_uri = generate_entity_uri(claim_rid, 'Claim')
            await conn.execute("""
                INSERT INTO entity_registry (fuseki_uri, entity_text, entity_type, normalized_text)
                VALUES ($1, $2, 'Claim', $3)
                ON CONFLICT (fuseki_uri) DO NOTHING
            """, entity_uri, body.statement[:200], normalize_entity_text(body.statement[:200]))

            # 5. Write makes_claim relationship edge
            try:
                await conn.execute("""
                    INSERT INTO entity_relationships (subject_uri, predicate, object_uri, confidence, source)
                    VALUES ($1, 'makes_claim', $2, 1.0, 'claims_engine')
                    ON CONFLICT DO NOTHING
                """, body.claimant_uri, entity_uri)
            except Exception as e:
                logger.warning(f"Failed to create makes_claim relationship: {e}")

            # 6. Optional: link claim to subject entity via 'about' predicate
            if body.about_uri:
                target = await conn.fetchrow(
                    "SELECT fuseki_uri FROM entity_registry WHERE fuseki_uri = $1",
                    body.about_uri,
                )
                if target:
                    try:
                        await conn.execute("""
                            INSERT INTO entity_relationships (subject_uri, predicate, object_uri, confidence, source)
                            VALUES ($1, 'about', $2, 1.0, 'claims_engine')
                            ON CONFLICT DO NOTHING
                        """, entity_uri, body.about_uri)
                    except Exception as e:
                        logger.warning(f"Failed to create about relationship: {e}")

            # 7. Handle versioning — link to superseded claim
            if body.supersedes_rid:
                old = await conn.fetchrow(
                    "SELECT entity_uri FROM claims WHERE claim_rid = $1", body.supersedes_rid
                )
                if old and old['entity_uri']:
                    try:
                        await conn.execute("""
                            INSERT INTO entity_relationships (subject_uri, predicate, object_uri, confidence, source)
                            VALUES ($1, 'supersedes_claim', $2, 1.0, 'claims_engine')
                            ON CONFLICT DO NOTHING
                        """, entity_uri, old['entity_uri'])
                    except Exception as e:
                        logger.warning(f"Failed to create supersedes_claim relationship: {e}")

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
            row = await conn.fetchrow(
                "SELECT * FROM claims WHERE claim_rid = $1", rid
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

            # Verify evidence entity exists
            evidence = await conn.fetchrow(
                "SELECT fuseki_uri FROM entity_registry WHERE fuseki_uri = $1",
                body.evidence_uri,
            )
            if not evidence:
                raise HTTPException(status_code=404, detail=f"Evidence entity not found: {body.evidence_uri}")

            # Write evidences_claim relationship
            try:
                await conn.execute("""
                    INSERT INTO entity_relationships (subject_uri, predicate, object_uri, confidence, source)
                    VALUES ($1, 'evidences_claim', $2, 1.0, 'claims_engine')
                    ON CONFLICT DO NOTHING
                """, body.evidence_uri, row["entity_uri"])
            except Exception as e:
                logger.warning(f"Failed to create evidences_claim relationship: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to link evidence: {e}")

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
                    # Resolve claimant to entity URI
                    async with pool.acquire() as conn:
                        claimant = await conn.fetchrow("""
                            SELECT fuseki_uri FROM entity_registry
                            WHERE normalized_text = $1 OR entity_text ILIKE $2
                            LIMIT 1
                        """, candidate.get("claimant_name", "").lower().strip(),
                            f"%{candidate.get('claimant_name', '')}%")

                        if claimant:
                            create_body = ClaimCreateRequest(
                                claimant_uri=claimant["fuseki_uri"],
                                statement=candidate["statement"],
                                claim_type=candidate.get("claim_type", "ecological"),
                                source_document=body.source_document,
                                ai_confidence=candidate.get("confidence"),
                                metadata=candidate.get("metadata", {}),
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
        """Compute content hash for ledger anchoring. Broadcast is stubbed for V1."""
        from api.ledger_anchor import compute_content_hash

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

        logger.info(f"claim.prepare_anchor rid={rid} hash={content_hash[:16]}...")
        return AnchorPrepareResponse(
            claim_rid=rid,
            content_hash=content_hash,
            ready_to_anchor=False,
            reason="Service account not configured. Ledger anchoring will be enabled when funded.",
        )

    return router
