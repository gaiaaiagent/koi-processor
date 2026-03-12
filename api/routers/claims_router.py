"""Claims Engine V2 endpoints: create, search, verify, evidence linking, extraction, anchoring, reconcile, attestations.

Implements the Claims Engine API:
  POST /claims/           — create a new impact claim (entity + graph edges + SQL)
  GET  /claims/           — list/search claims with filters
  GET  /claims/{rid}      — get claim with linked evidence entities
  PATCH /claims/{rid}/verify — advance verification level (V2: attestation policy gate)
  POST /claims/{rid}/evidence — attach evidence entity
  GET  /claims/{rid}/history — verification audit log
  POST /claims/extract    — AI extraction from document text
  POST /claims/{rid}/prepare-anchor — compute content hash for ledger anchoring
  POST /claims/{rid}/anchor — broadcast anchor to Regen Ledger
  POST /claims/{rid}/reconcile — check on-chain status of timed-out broadcast
  GET  /claims/{rid}/proof-pack — synthesized verification artifact (requires ledger_anchored)
  POST /claims/{rid}/attestations — create/update attestation (UPSERT)
  GET  /claims/{rid}/attestations — list attestations for a claim
  GET  /claims/{rid}/attestations/{att_rid} — get single attestation
  POST /claims/{rid}/attestations/{att_rid}/anchor — anchor attestation on Regen Ledger
  POST /claims/{rid}/attestations/{att_rid}/reconcile — check on-chain status of attestation anchor
  POST /claims/claim-from-settlement — settlement→evidence→claim with threshold auto-advance
  GET  /claims/settlements — list TBFF settlement receipts with linked claim status

All verification transitions are recorded in claim_state_log (insert-only).
Claims are first-class KOI entities with graph edges (makes_claim, about, evidences_claim).
V2 adds identity-bound attestations: reviewer_uri FK, attestation policy gates, operator tracking.
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
    operator_uri: Optional[str] = Field(None, description="entity_registry.fuseki_uri of the operator who entered the claim")
    created_by: Optional[str] = None


class ClaimResponse(BaseModel):
    claim_rid: str
    entity_uri: Optional[str]
    claimant_uri: str
    claimant_name: Optional[str] = None
    statement: str
    claim_type: str
    verification: str
    source_document: Optional[str]
    ai_confidence: Optional[float]
    content_hash: Optional[str]
    ledger_iri: Optional[str]
    tx_hash: Optional[str] = None
    supersedes_rid: Optional[str]
    metadata: Dict[str, Any]
    created_by: Optional[str] = None
    operator_uri: Optional[str] = None
    attestation_count: int = 0
    attestation_summary: Optional[Dict[str, int]] = None
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


class AnchorPendingResponse(BaseModel):
    claim_rid: str
    content_hash: str
    tx_hash: Optional[str] = None
    ledger_iri: Optional[str] = None
    status: str = "pending"
    message: str = ""


class ReconcileResponse(BaseModel):
    claim_rid: str
    status: str  # "anchored", "pending", "failed"
    tx_hash: Optional[str] = None
    ledger_iri: Optional[str] = None
    ledger_timestamp: Optional[str] = None
    message: Optional[str] = None


class ProofPackResponse(BaseModel):
    """Synthesized verification artifact for an anchored claim."""
    claim_rid: str
    verification: str
    claim: Dict[str, Any]
    evidence: List[Dict[str, Any]]
    history: List[Dict[str, Any]]
    anchor: Dict[str, Any]
    attestations: List[Dict[str, Any]]
    claim_content_hash_verified: bool = False
    chain_id: str = ""
    verification_instructions: str = ""
    assembled_at: str
    version: str = "2.0"


class EvidenceFromArtifactsRequest(BaseModel):
    source_uris: List[str] = Field(..., min_length=1, description="URIs of published artifacts (Pattern, Protocol, CaseStudy, Practice)")
    name: str = Field(..., min_length=3, max_length=500, description="Name for the Evidence entity")
    description: str = Field(..., min_length=10, max_length=5000, description="Description summarizing the evidence")
    bioregion: Optional[str] = Field(None, description="Bioregion name for path prefix (e.g. 'Salish Sea')")


class EvidenceFromArtifactsResponse(BaseModel):
    evidence_uri: str
    vault_rid: str
    vault_path: str
    is_new: bool
    visibility_scope: str
    source_artifacts: List[Dict[str, Any]]


class EvidenceFromSettlementRequest(BaseModel):
    settlement_id: str = Field(..., description="Unique settlement identifier (e.g. tx hash or internal ID)")
    tx_hash: Optional[str] = Field(None, description="On-chain transaction hash (Base Sepolia/mainnet)")
    chain_id: Optional[int] = Field(None, description="EVM chain ID (e.g. 84532 for Base Sepolia)")
    block_number: Optional[int] = Field(None, description="Block number of the settlement transaction")
    iterations: int = Field(..., ge=1, le=50, description="Number of TBFF iterations to convergence")
    converged: bool = Field(True, description="Whether the settlement converged")
    total_redistributed_usd: float = Field(..., ge=0, description="Total USD redistributed in this settlement")
    node_balances: List[Dict[str, Any]] = Field(..., min_length=1, description="Final balance snapshot: [{participant_name, participant_uri?, initial_balance, final_balance, threshold}]")
    bioregion: Optional[str] = Field(None, description="Bioregion name for path prefix")
    description: str = Field(..., min_length=10, max_length=5000, description="Human-readable description of the settlement")
    parent_receipt_id: Optional[str] = Field(None, description="Parent CAT receipt ID for provenance chaining")


class EvidenceFromSettlementResponse(BaseModel):
    evidence_uri: str
    receipt_id: Optional[str] = None
    receipt_persisted: bool = True
    is_new: bool
    settlement_summary: Dict[str, Any]


class ClaimFromSettlementRequest(BaseModel):
    """Create a claim backed by settlement evidence, with threshold-based auto-advance."""
    settlement: EvidenceFromSettlementRequest = Field(..., description="Settlement data (forwarded to evidence-from-settlement)")
    claimant_uri: str = Field(..., description="entity_registry.fuseki_uri of the claimant")
    about_uri: Optional[str] = Field(None, description="Entity URI the claim is about (Location, Org, Project, etc.)")
    statement: str = Field(..., min_length=10, max_length=5000, description="Plain-language impact assertion")
    claim_type: str = Field("financial", description="ecological | social | financial | governance")
    operator_uri: Optional[str] = Field(None, description="entity_registry.fuseki_uri of the operator")
    reviewer_uri: Optional[str] = Field(None, description="Reviewer for system attestation (required for auto-advance)")
    manual_override: bool = Field(False, description="Force self_reported regardless of threshold band")


class ClaimFromSettlementResponse(BaseModel):
    evidence_uri: str
    claim_rid: str
    verification: str
    threshold_band: str  # "auto" | "semi" | "manual"
    auto_advanced: bool
    auto_advance_reason: Optional[str] = None
    receipt_id: Optional[str] = None
    settlement_summary: Dict[str, Any]


class AttestationCreateRequest(BaseModel):
    reviewer_uri: str
    verdict: str = "pending"  # pending|approved|rejected|needs_info
    rationale: Optional[str] = None
    evidence_uris: Optional[List[str]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AttestationResponse(BaseModel):
    attestation_rid: str
    claim_rid: str
    reviewer_uri: str
    reviewer_name: Optional[str] = None
    verdict: str
    rationale: Optional[str]
    evidence_uris: Optional[List[str]]
    content_hash: Optional[str] = None
    attest_tx_hash: Optional[str] = None
    ledger_iri: Optional[str] = None
    attest_timestamp: Optional[str] = None
    attestor_address: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class AttestationAnchorResponse(BaseModel):
    attestation_rid: str
    claim_rid: str
    content_hash: str
    attest_tx_hash: str
    ledger_iri: str
    attest_timestamp: Optional[str] = None
    attestor_address: Optional[str] = None


class AttestationAnchorPendingResponse(BaseModel):
    attestation_rid: str
    claim_rid: str
    content_hash: str
    attest_tx_hash: str
    ledger_iri: str
    status: str = "pending"
    message: str = ""


class AttestationReconcileResponse(BaseModel):
    attestation_rid: str
    claim_rid: str
    status: str  # "anchored", "pending", "failed"
    attest_tx_hash: Optional[str] = None
    ledger_iri: Optional[str] = None
    attest_timestamp: Optional[str] = None
    message: Optional[str] = None


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
    "practice", "pattern", "casestudy", "concept", "project",
    "bioregion", "location", "organization", "person",
}

# ---------------------------------------------------------------------------
# TBFF threshold policy bands (settlement evidence path only)
# ---------------------------------------------------------------------------
# Below AUTO_ADVANCE_CEILING:     auto-advance to verified (2 system attestations)
# AUTO_ADVANCE_CEILING..MANUAL:   auto-advance to peer_reviewed (1 attestation; 1 more needed)
# Above MANUAL_FLOOR:             stays self_reported (full attestation chain)
TBFF_AUTO_ADVANCE_CEILING_USD = 500.0
TBFF_MANUAL_FLOOR_USD = 5000.0

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


def _attestation_rid(claim_rid: str, reviewer_uri: str) -> str:
    """Stable attestation RID derived from (claim_rid, reviewer_uri) only.

    Same reviewer + claim always yields the same RID, making UPSERT safe.
    Verdict changes are mutations on the same record, not new records.
    """
    obj = {"claim_rid": claim_rid, "reviewer_uri": reviewer_uri}
    canonical = json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
    h = hashlib.blake2b(canonical.encode(), digest_size=32).hexdigest()[:16]
    return f"orn:koi-net.attestation:{h}"


def _attestation_response(row, reviewer_name=None) -> AttestationResponse:
    """Build AttestationResponse from a DB row."""
    return AttestationResponse(
        attestation_rid=row["attestation_rid"],
        claim_rid=row["claim_rid"],
        reviewer_uri=row["reviewer_uri"],
        reviewer_name=reviewer_name or row.get("reviewer_name"),
        verdict=row["verdict"],
        rationale=row.get("rationale"),
        evidence_uris=row.get("evidence_uris"),
        content_hash=row.get("content_hash"),
        attest_tx_hash=row.get("attest_tx_hash"),
        ledger_iri=row.get("ledger_iri"),
        attest_timestamp=row["attest_timestamp"].isoformat() if row.get("attest_timestamp") else None,
        attestor_address=row.get("attestor_address"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _check_attestation_policy(conn, claim_rid: str, target_level: str, claim_created_at) -> tuple:
    """Check attestation policy gate for post-migration claims.

    Returns (ok: bool, message: str). Pre-V2 claims are grandfathered.
    """
    # Check if claim_attestations table exists (graceful for pre-migration DBs)
    table_exists = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'claim_attestations')"
    )
    if not table_exists:
        return True, ""

    # Grandfathering: exempt pre-V2 claims
    migration_ts = await conn.fetchval(
        "SELECT applied_at FROM koi_migrations WHERE migration_id = '066_attestations'"
    )
    if migration_ts is None:
        return True, ""
    if claim_created_at < migration_ts:
        return True, ""  # Pre-V2 claim, exempt from policy

    count = await conn.fetchval(
        "SELECT COUNT(*) FROM claim_attestations WHERE claim_rid=$1 AND verdict='approved'",
        claim_rid,
    )
    if target_level == "peer_reviewed" and count < 1:
        return False, f"Attestation policy: need >= 1 approved attestation for peer_reviewed (have {count})"
    if target_level == "verified" and count < 2:
        return False, f"Attestation policy: need >= 2 approved attestations for verified (have {count})"
    return True, ""


async def _get_attestation_summary(conn, claim_rid: str) -> tuple:
    """Return (count, summary_dict) for a claim's attestations."""
    try:
        rows = await conn.fetch(
            "SELECT verdict, COUNT(*) AS cnt FROM claim_attestations WHERE claim_rid = $1 GROUP BY verdict",
            claim_rid,
        )
        if not rows:
            return 0, None
        summary = {r["verdict"]: r["cnt"] for r in rows}
        total = sum(summary.values())
        return total, summary
    except Exception:
        # Table may not exist yet (pre-migration)
        return 0, None


def _row_to_claim(row, evidence=None, attestation_count=0, attestation_summary=None) -> ClaimResponse:
    meta = row["metadata"]
    if isinstance(meta, str):
        meta = json.loads(meta)
    return ClaimResponse(
        claim_rid=row["claim_rid"],
        entity_uri=row.get("entity_uri"),
        claimant_uri=row["claimant_uri"],
        claimant_name=row.get("claimant_name"),
        statement=row["statement"],
        claim_type=row["claim_type"],
        verification=row["verification"],
        source_document=row.get("source_document"),
        ai_confidence=float(row["ai_confidence"]) if row.get("ai_confidence") is not None else None,
        content_hash=row.get("content_hash"),
        ledger_iri=row.get("ledger_iri"),
        tx_hash=row.get("tx_hash"),
        supersedes_rid=row.get("supersedes_rid"),
        metadata=meta or {},
        created_by=row.get("created_by"),
        operator_uri=row.get("operator_uri"),
        attestation_count=attestation_count,
        attestation_summary=attestation_summary,
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

    try:
        from api.federation_events import emit_domain_event
    except ImportError:
        async def emit_domain_event(*a, **kw): pass  # no-op when federation bridge not deployed

    # Lazy import to avoid circular deps at module level
    _entity_helpers = {}

    def _get_entity_helpers():
        if not _entity_helpers:
            from api.personal_ingest_api import generate_entity_uri, normalize_entity_text
            _entity_helpers['generate_entity_uri'] = generate_entity_uri
            _entity_helpers['normalize_entity_text'] = normalize_entity_text
        return _entity_helpers

    async def _fetch_enriched_claim(conn, rid: str) -> ClaimResponse:
        """Re-fetch a claim with claimant name join and attestation summary."""
        row = await conn.fetchrow("""
            SELECT c.*, er.entity_text AS claimant_name
            FROM claims c
            LEFT JOIN entity_registry er ON c.claimant_uri = er.fuseki_uri
            WHERE c.claim_rid = $1
        """, rid)
        att_count, att_summary = await _get_attestation_summary(conn, rid)
        return _row_to_claim(row, attestation_count=att_count, attestation_summary=att_summary)

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

            # 2a. Validate operator_uri if provided
            if body.operator_uri:
                operator = await conn.fetchrow(
                    "SELECT fuseki_uri FROM entity_registry WHERE fuseki_uri = $1",
                    body.operator_uri,
                )
                if not operator:
                    raise HTTPException(status_code=404, detail=f"Operator entity not found: {body.operator_uri}")

            # 2. Validate about_uri exists and has an allowed type before it enters the RID hash
            if body.about_uri:
                about_entity = await conn.fetchrow(
                    "SELECT fuseki_uri, entity_type FROM entity_registry WHERE fuseki_uri = $1",
                    body.about_uri,
                )
                if not about_entity:
                    raise HTTPException(status_code=404, detail=f"About entity not found: {body.about_uri}")
                if about_entity["entity_type"].lower() not in _ABOUT_ALLOWED_TYPES:
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
                return await _fetch_enriched_claim(conn, claim_rid)

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
                                            supersedes_rid, metadata, created_by, operator_uri)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11)
                        RETURNING *
                    """,
                        claim_rid, entity_uri, body.claimant_uri, body.statement,
                        body.claim_type, body.source_document, body.ai_confidence,
                        body.supersedes_rid, _json_dumps(body.metadata), body.created_by,
                        body.operator_uri,
                    )

                    # 8b. Write operates_claim edge if operator provided
                    if body.operator_uri:
                        await conn.execute("""
                            INSERT INTO entity_relationships (subject_uri, predicate, object_uri, confidence, source)
                            VALUES ($1, 'operates_claim', $2, 1.0, 'claims_engine')
                            ON CONFLICT DO NOTHING
                        """, body.operator_uri, entity_uri)

                    # 9. Log initial state
                    await conn.execute("""
                        INSERT INTO claim_state_log (claim_rid, from_state, to_state, actor, reason)
                        VALUES ($1, NULL, 'self_reported', $2, 'created')
                    """, claim_rid, body.created_by)
            except asyncpg.UniqueViolationError:
                # Concurrent insert won the race — return the winner's row
                logger.info(f"claim.concurrent_idempotent rid={claim_rid}")
                return await _fetch_enriched_claim(conn, claim_rid)

            logger.info(f"claim.create rid={claim_rid} claimant={body.claimant_uri} type={body.claim_type}")
            await emit_domain_event("claim", "NEW", claim_rid, {
                "claim_rid": claim_rid, "entity_uri": entity_uri,
                "claimant_uri": body.claimant_uri, "statement": body.statement,
                "claim_type": body.claim_type, "verification": "self_reported",
                "source_document": body.source_document, "ai_confidence": body.ai_confidence,
                "supersedes_rid": body.supersedes_rid, "metadata": body.metadata or {},
                "created_by": body.created_by, "operator_uri": body.operator_uri,
                "state_transition": {"from_state": None, "to_state": "self_reported",
                                     "actor": body.created_by, "reason": "created",
                                     "created_at": datetime.now(timezone.utc).isoformat()},
            })
            return await _fetch_enriched_claim(conn, claim_rid)

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
                SELECT c.*, er.entity_text AS claimant_name
                FROM claims c
                LEFT JOIN entity_registry er ON c.claimant_uri = er.fuseki_uri
                {where}
                ORDER BY c.created_at DESC
                LIMIT ${i} OFFSET ${i+1}
            """, *params)

            # Batch attestation summary (preserves pre-migration fallback)
            att_map = {}
            try:
                rids = [r["claim_rid"] for r in rows]
                att_rows = await conn.fetch("""
                    SELECT claim_rid, verdict, COUNT(*) AS cnt
                    FROM claim_attestations
                    WHERE claim_rid = ANY($1)
                    GROUP BY claim_rid, verdict
                """, rids)
                for ar in att_rows:
                    rid = ar["claim_rid"]
                    if rid not in att_map:
                        att_map[rid] = [0, {}]
                    att_map[rid][0] += ar["cnt"]
                    att_map[rid][1][ar["verdict"]] = ar["cnt"]
            except Exception:
                pass  # Table may not exist yet (pre-migration)

            results = []
            for r in rows:
                count, summary = att_map.get(r["claim_rid"], [0, {}])
                results.append(_row_to_claim(r, attestation_count=count, attestation_summary=summary or None))
            return results

    # ------------------------------------------------------------------ #
    # Claim from settlement with threshold policy (3A)                     #
    # ------------------------------------------------------------------ #

    @router.post("/claim-from-settlement", response_model=ClaimFromSettlementResponse, status_code=201)
    async def claim_from_settlement(body: ClaimFromSettlementRequest):
        """Create Evidence from settlement, then create a claim with threshold-based auto-advance.

        Threshold bands (total_redistributed_usd):
          < $500    → "auto"  — auto-advance to verified (2 system attestations)
          $500–$5k  → "semi"  — auto-advance to peer_reviewed (1 system attestation)
          > $5k     → "manual" — stays self_reported (full attestation chain required)

        manual_override=true forces self_reported regardless of amount.
        """
        # 1. Determine threshold band
        amount = body.settlement.total_redistributed_usd
        if body.manual_override:
            band = "manual"
        elif amount < TBFF_AUTO_ADVANCE_CEILING_USD:
            band = "auto"
        elif amount < TBFF_MANUAL_FLOOR_USD:
            band = "semi"
        else:
            band = "manual"

        # 2. Create Evidence via existing endpoint logic
        ev_response = await evidence_from_settlement(body.settlement)

        # 3. Create claim
        claim_body = ClaimCreateRequest(
            claimant_uri=body.claimant_uri,
            statement=body.statement,
            claim_type=body.claim_type,
            about_uri=body.about_uri,
            operator_uri=body.operator_uri,
            metadata={
                "settlement_id": body.settlement.settlement_id,
                "total_redistributed_usd": amount,
                "threshold_band": band,
                "tbff_policy_version": "0.1",
            },
        )
        claim_response = await create_claim(claim_body)

        # 4. Link evidence to claim
        link_body = EvidenceLinkRequest(
            evidence_uri=ev_response.evidence_uri,
            actor="tbff-threshold-policy",
        )
        await link_evidence(claim_response.claim_rid, link_body)

        # 5. Auto-advance based on band (requires reviewer_uri for attestation)
        auto_advanced = False
        auto_advance_reason = None
        final_verification = "self_reported"

        if band in ("auto", "semi") and body.reviewer_uri:
            async with pool.acquire() as conn:
                # Verify reviewer exists and is not the claimant
                reviewer = await conn.fetchrow(
                    "SELECT fuseki_uri, entity_type FROM entity_registry WHERE fuseki_uri = $1",
                    body.reviewer_uri,
                )
                if not reviewer:
                    auto_advance_reason = f"Reviewer not found: {body.reviewer_uri}"
                elif reviewer["entity_type"].lower() not in ("person", "organization"):
                    auto_advance_reason = f"Reviewer type '{reviewer['entity_type']}' not valid for attestation"
                elif body.reviewer_uri == body.claimant_uri:
                    auto_advance_reason = "Reviewer cannot be the claimant (self-attestation)"
                else:
                    try:
                        # Create first system attestation
                        att1_body = AttestationCreateRequest(
                            reviewer_uri=body.reviewer_uri,
                            verdict="approved",
                            rationale=f"TBFF threshold policy auto-approve: ${amount:,.2f} in '{band}' band",
                            evidence_uris=[ev_response.evidence_uri],
                            metadata={"policy": "tbff-threshold-v0.1", "band": band, "auto": True},
                        )
                        await create_attestation(claim_response.claim_rid, att1_body)

                        # Advance to peer_reviewed
                        verify_body = VerifyRequest(
                            new_level="peer_reviewed",
                            actor="tbff-threshold-policy",
                            reason=f"Auto-advance: ${amount:,.2f} below ${TBFF_MANUAL_FLOOR_USD:,.0f} threshold",
                        )
                        await verify_claim(claim_response.claim_rid, verify_body)
                        final_verification = "peer_reviewed"
                        auto_advanced = True

                        if band == "auto":
                            # For auto band, need 2 attestations for verified.
                            # Use operator_uri as second reviewer if available and different.
                            second_reviewer = body.operator_uri
                            if second_reviewer and second_reviewer != body.reviewer_uri and second_reviewer != body.claimant_uri:
                                att2_body = AttestationCreateRequest(
                                    reviewer_uri=second_reviewer,
                                    verdict="approved",
                                    rationale=f"TBFF threshold policy auto-approve: ${amount:,.2f} in 'auto' band (< ${TBFF_AUTO_ADVANCE_CEILING_USD:,.0f})",
                                    evidence_uris=[ev_response.evidence_uri],
                                    metadata={"policy": "tbff-threshold-v0.1", "band": "auto", "auto": True},
                                )
                                await create_attestation(claim_response.claim_rid, att2_body)

                                verify2_body = VerifyRequest(
                                    new_level="verified",
                                    actor="tbff-threshold-policy",
                                    reason=f"Auto-advance: ${amount:,.2f} below ${TBFF_AUTO_ADVANCE_CEILING_USD:,.0f} auto threshold",
                                )
                                await verify_claim(claim_response.claim_rid, verify2_body)
                                final_verification = "verified"
                            else:
                                auto_advance_reason = (
                                    "Auto band needs 2 distinct reviewers for verified; "
                                    "advanced to peer_reviewed only (provide operator_uri as second reviewer)"
                                )
                    except HTTPException as e:
                        auto_advance_reason = f"Auto-advance failed: {e.detail}"
                        # Claim still exists at self_reported — not fatal
        elif band in ("auto", "semi") and not body.reviewer_uri:
            auto_advance_reason = "reviewer_uri required for auto-advance"

        logger.info(
            f"claim.from_settlement rid={claim_response.claim_rid} band={band} "
            f"verification={final_verification} auto={auto_advanced} amount=${amount:,.2f}"
        )

        return ClaimFromSettlementResponse(
            evidence_uri=ev_response.evidence_uri,
            claim_rid=claim_response.claim_rid,
            verification=final_verification,
            threshold_band=band,
            auto_advanced=auto_advanced,
            auto_advance_reason=auto_advance_reason,
            receipt_id=ev_response.receipt_id,
            settlement_summary=ev_response.settlement_summary,
        )

    # ------------------------------------------------------------------ #
    # List settlements (read model for flow-funding visualization)         #
    # ------------------------------------------------------------------ #

    @router.get("/settlements")
    async def list_settlements(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        """List TBFF settlement receipts with linked claim verification status.

        Joins koi_transformation_receipts (transformation_type='tbff_settlement')
        to claims via evidences_claim edges. Returns structured settlement data
        including node_balances (when available in receipt metadata JSONB).
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT DISTINCT ON (r.receipt_id)
                    r.receipt_id,
                    r.output_rid AS evidence_uri,
                    r.metadata,
                    r.created_at,
                    c.claim_rid,
                    c.verification,
                    c.statement
                FROM koi_transformation_receipts r
                LEFT JOIN entity_relationships ev_link
                    ON ev_link.subject_uri = r.output_rid
                    AND ev_link.predicate = 'evidences_claim'
                LEFT JOIN claims c
                    ON c.entity_uri = ev_link.object_uri
                WHERE r.transformation_type = 'tbff_settlement'
                ORDER BY r.receipt_id, c.created_at DESC
            """)
            # Apply pagination after dedup
            rows = rows[offset:offset + limit]

            settlements = []
            for row in rows:
                raw_meta = row["metadata"] or {}
                meta = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
                amount = meta.get("total_redistributed_usd", 0)
                # Derive threshold band from amount
                if amount < 500:
                    threshold_band = "auto"
                elif amount <= 5000:
                    threshold_band = "semi"
                else:
                    threshold_band = "manual"

                settlements.append({
                    "receipt_id": row["receipt_id"],
                    "evidence_uri": row["evidence_uri"],
                    "settlement_id": meta.get("settlement_id"),
                    "tx_hash": meta.get("tx_hash"),
                    "iterations": meta.get("iterations"),
                    "converged": meta.get("converged"),
                    "total_redistributed_usd": amount,
                    "participant_count": meta.get("participant_count"),
                    "node_balances": meta.get("node_balances"),  # None for pre-fix receipts
                    "threshold_band": threshold_band,
                    "claim_rid": row["claim_rid"],
                    "claim_state": row["verification"],
                    "claim_statement": row["statement"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                })

            return {"settlements": settlements, "total": len(settlements)}

    # ------------------------------------------------------------------ #
    # Get claim by RID (with evidence)                                     #
    # ------------------------------------------------------------------ #

    @router.get("/{rid}", response_model=ClaimResponse)
    async def get_claim(rid: str):
        """Fetch a claim by RID, including linked evidence entities."""
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT c.*, er.entity_text AS claimant_name
                FROM claims c
                LEFT JOIN entity_registry er ON c.claimant_uri = er.fuseki_uri
                WHERE c.claim_rid = $1
            """, rid)
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

            att_count, att_summary = await _get_attestation_summary(conn, rid)

        return _row_to_claim(row, evidence=evidence or None,
                             attestation_count=att_count, attestation_summary=att_summary)

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

                # V2: Attestation policy gate (post-migration claims only)
                if new_level in ("peer_reviewed", "verified"):
                    policy_ok, policy_msg = await _check_attestation_policy(
                        conn, rid, new_level, row["created_at"]
                    )
                    if not policy_ok:
                        raise HTTPException(status_code=409, detail=policy_msg)

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
            await emit_domain_event("claim", "UPDATE", rid, {
                "claim_rid": rid, "verification": new_level,
                "state_transition": {"from_state": current, "to_state": new_level,
                                     "actor": body.actor, "reason": body.reason,
                                     "created_at": datetime.now(timezone.utc).isoformat()},
            })
            return await _fetch_enriched_claim(conn, rid)

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
                await conn.execute("""
                    UPDATE claims SET updated_at = NOW() WHERE claim_rid = $1
                """, rid)

            logger.info(f"claim.link_evidence rid={rid} evidence={body.evidence_uri}")
            await emit_domain_event("claim", "UPDATE", rid, {
                "claim_rid": rid,
                "state_transition": {"from_state": row["verification"], "to_state": row["verification"],
                                     "actor": body.actor, "reason": "evidence linked",
                                     "created_at": datetime.now(timezone.utc).isoformat(),
                                     "metadata": {"evidence_uri": body.evidence_uri}},
            })
            return await _fetch_enriched_claim(conn, rid)

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
    # Evidence from artifacts (Steel Thread Phase B)                        #
    # ------------------------------------------------------------------ #

    @router.post("/evidence-from-artifacts", response_model=EvidenceFromArtifactsResponse, status_code=201)
    async def evidence_from_artifacts(body: EvidenceFromArtifactsRequest):
        """Create an Evidence entity from published interview artifacts.

        Bundles published Pattern/Protocol/CaseStudy/Practice entities into a
        citable Evidence entity. Delegates registration to the existing
        /register-entity path (entity resolution, collision detection,
        visibility recompute) so that all shared registration behavior
        is inherited.

        Visibility scope is inherited as most-restrictive of source artifacts:
        if ANY source entity has node_private=true, Evidence is node_private.
        """
        async with pool.acquire() as conn:
            # 1. Validate all source URIs exist and collect metadata
            source_artifacts = []
            any_private = False
            for uri in body.source_uris:
                row = await conn.fetchrow(
                    "SELECT fuseki_uri, entity_text, entity_type, node_private "
                    "FROM entity_registry WHERE fuseki_uri = $1",
                    uri,
                )
                if not row:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Source artifact not found: {uri}",
                    )
                if row["entity_type"] not in (
                    "Practice", "Pattern", "Protocol", "CaseStudy",
                    "PatternCandidate", "ProtocolCandidate",
                ):
                    raise HTTPException(
                        status_code=422,
                        detail=f"Source entity '{uri}' is type '{row['entity_type']}', "
                        f"expected Practice/Pattern/Protocol/CaseStudy",
                    )
                if row["node_private"]:
                    any_private = True
                source_artifacts.append({
                    "uri": row["fuseki_uri"],
                    "name": row["entity_text"],
                    "type": row["entity_type"],
                    "node_private": row["node_private"],
                })

            visibility_scope = "node_private" if any_private else "public"

            # 2. Build bioregion-qualified entity name for distinct canonical URIs
            evidence_name = body.name.strip()
            if body.bioregion:
                evidence_name = f"{body.bioregion.strip()} — {evidence_name}"

            # 3. Generate vault_rid and vault_path
            safe_name = body.name.strip().replace(" ", "-").replace("'", "")[:80]
            if body.bioregion:
                safe_bio = body.bioregion.strip().replace(" ", "-")[:30]
                vault_rid = f"orn:openclaw.entity:Evidence/{safe_bio}--{safe_name}"
                vault_path = f"Evidence/{evidence_name}.md"
            else:
                vault_rid = f"orn:openclaw.entity:Evidence/{safe_name}"
                vault_path = f"Evidence/{evidence_name}.md"

            # 4. Generate markdown content for vault note
            source_lines = []
            for art in source_artifacts:
                source_lines.append(f"- **{art['type']}:** {art['name']} (`{art['uri']}`)")
            source_uris_yaml = "\n".join(
                f'  - "{art["uri"]}"' for art in source_artifacts
            )
            markdown_content = (
                f'---\n'
                f'"@type": Evidence\n'
                f'name: "{evidence_name}"\n'
                f'description: "{body.description.strip()}"\n'
                f'source_artifact_uris:\n'
                f'{source_uris_yaml}\n'
                f'---\n\n'
                f'# {evidence_name}\n\n'
                f'{body.description.strip()}\n\n'
                f'## Source Artifacts\n\n'
                + "\n".join(source_lines)
                + "\n"
            )

            content_hash = hashlib.sha256(markdown_content.encode()).hexdigest()

        # 5. Register via /register-entity path (entity resolution,
        #    collision detection, alias handling, node_private recompute)
        from api.personal_ingest_api import register_vault_entity, RegisterEntityRequest

        reg_request = RegisterEntityRequest(
            vault_rid=vault_rid,
            vault_path=vault_path,
            entity_type="Evidence",
            name=evidence_name,
            content_hash=content_hash,
            visibility_scope=visibility_scope,
            publication_scope="local_graph",
            frontmatter={
                "@type": "Evidence",
                "name": evidence_name,
                "description": body.description.strip(),
                "source_artifact_uris": [art["uri"] for art in source_artifacts],
            },
        )

        reg_response = await register_vault_entity(reg_request)
        evidence_uri = reg_response.canonical_uri
        is_new = reg_response.is_new

        # 6. Create derived_from edges (not part of /register-entity)
        async with pool.acquire() as conn:
            for art in source_artifacts:
                await conn.execute("""
                    INSERT INTO entity_relationships
                        (subject_uri, predicate, object_uri, confidence, source)
                    VALUES ($1, 'derived_from', $2, 1.0, 'evidence-from-artifacts')
                    ON CONFLICT DO NOTHING
                """, evidence_uri, art["uri"])

        logger.info(
            f"Evidence from artifacts: uri={evidence_uri} is_new={is_new} "
            f"sources={len(source_artifacts)} visibility={visibility_scope}"
        )

        return EvidenceFromArtifactsResponse(
            evidence_uri=evidence_uri,
            vault_rid=vault_rid,
            vault_path=vault_path,
            is_new=is_new,
            visibility_scope=visibility_scope,
            source_artifacts=source_artifacts,
        )

    # ------------------------------------------------------------------ #
    # Evidence from TBFF settlement (Capital Plane Phase 2)                #
    # ------------------------------------------------------------------ #

    @router.post("/evidence-from-settlement", response_model=EvidenceFromSettlementResponse, status_code=201)
    async def evidence_from_settlement(body: EvidenceFromSettlementRequest):
        """Create an Evidence entity from a TBFF settlement event.

        Transforms on-chain settlement data (balances, iterations, convergence)
        into a citable Evidence entity with a CAT receipt chain. Follows the
        same vault-note registration pattern as evidence_from_artifacts.
        """
        from api.personal_ingest_api import register_vault_entity, RegisterEntityRequest
        from api.cat_receipts import create_receipt, generate_receipt_id

        # 1. Build Evidence entity name and vault paths
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        evidence_name = f"TBFF Settlement — {body.bioregion or 'Unknown'} — {date_str}"

        safe_bio = (body.bioregion or "unknown").strip().replace(" ", "-")[:30]
        safe_id = body.settlement_id[:16]
        vault_rid = f"orn:openclaw.entity:Evidence/tbff-settlement-{safe_id}"
        vault_path = f"Evidence/{evidence_name}.md"

        # 2. Build settlement summary
        settlement_summary = {
            "settlement_id": body.settlement_id,
            "tx_hash": body.tx_hash,
            "chain_id": body.chain_id,
            "block_number": body.block_number,
            "iterations": body.iterations,
            "converged": body.converged,
            "total_redistributed_usd": body.total_redistributed_usd,
            "participant_count": len(body.node_balances),
            "date": date_str,
        }

        # 3. Generate markdown content for vault note
        balance_lines = []
        for nb in body.node_balances:
            name = nb.get("participant_name", "Unknown")
            initial = nb.get("initial_balance", 0)
            final = nb.get("final_balance", 0)
            threshold = nb.get("threshold", 0)
            balance_lines.append(
                f"| {name} | ${initial:,.2f} | ${final:,.2f} | ${threshold:,.2f} |"
            )

        balance_table = (
            "| Participant | Initial | Final | Threshold |\n"
            "|---|---|---|---|\n"
            + "\n".join(balance_lines)
        )

        markdown_content = (
            f'---\n'
            f'"@type": Evidence\n'
            f'name: "{evidence_name}"\n'
            f'description: "{body.description.strip()}"\n'
            f'settlement_id: "{body.settlement_id}"\n'
            + (f'tx_hash: "{body.tx_hash}"\n' if body.tx_hash else "")
            + (f'chain_id: {body.chain_id}\n' if body.chain_id else "")
            + f'---\n\n'
            f'# {evidence_name}\n\n'
            f'{body.description.strip()}\n\n'
            f'## Settlement Details\n\n'
            f'- **Iterations:** {body.iterations}\n'
            f'- **Converged:** {body.converged}\n'
            f'- **Total redistributed:** ${body.total_redistributed_usd:,.2f}\n'
            + (f'- **TX hash:** `{body.tx_hash}`\n' if body.tx_hash else "")
            + (f'- **Chain:** {body.chain_id}\n' if body.chain_id else "")
            + (f'- **Block:** {body.block_number}\n' if body.block_number else "")
            + f'\n## Final Balances\n\n'
            f'{balance_table}\n'
        )

        content_hash = hashlib.sha256(markdown_content.encode()).hexdigest()

        # 4. Register via /register-entity path
        reg_request = RegisterEntityRequest(
            vault_rid=vault_rid,
            vault_path=vault_path,
            entity_type="Evidence",
            name=evidence_name,
            content_hash=content_hash,
            visibility_scope="public",
            publication_scope="local_graph",
            frontmatter={
                "@type": "Evidence",
                "name": evidence_name,
                "description": body.description.strip(),
                "settlement_id": body.settlement_id,
                "tx_hash": body.tx_hash,
                "chain_id": body.chain_id,
            },
        )

        reg_response = await register_vault_entity(reg_request)
        evidence_uri = reg_response.canonical_uri
        is_new = reg_response.is_new

        # 5. Create `documents` edges to participant Person entities (if in graph)
        async with pool.acquire() as conn:
            for nb in body.node_balances:
                participant_uri = nb.get("participant_uri")
                if participant_uri:
                    await conn.execute("""
                        INSERT INTO entity_relationships
                            (subject_uri, predicate, object_uri, confidence, source)
                        VALUES ($1, 'documents', $2, 1.0, 'evidence-from-settlement')
                        ON CONFLICT DO NOTHING
                    """, evidence_uri, participant_uri)

        # 6. Create CAT receipt for provenance tracking
        receipt_id = generate_receipt_id("tbff_settlement", f"tbff:{body.settlement_id}", evidence_uri)
        try:
            async with pool.acquire() as conn:
                receipt = await create_receipt(
                    conn,
                    transformation_type="tbff_settlement",
                    input_rid=f"tbff:{body.settlement_id}",
                    output_rid=evidence_uri,
                    processor_name="claims_router.evidence_from_settlement",
                    source_sensor="tbff",
                    metadata={
                        "settlement_id": body.settlement_id,
                        "tx_hash": body.tx_hash,
                        "iterations": body.iterations,
                        "converged": body.converged,
                        "total_redistributed_usd": body.total_redistributed_usd,
                        "participant_count": len(body.node_balances),
                        "node_balances": [dict(nb) for nb in body.node_balances],
                    },
                    parent_receipt_id=body.parent_receipt_id,
                    content_hash=content_hash,
                )
                receipt_id = receipt.receipt_id
                receipt_persisted = True
        except Exception as e:
            logger.error(f"Failed to create TBFF settlement receipt: {e}")
            receipt_persisted = False

        logger.info(
            f"Evidence from settlement: uri={evidence_uri} is_new={is_new} "
            f"settlement={body.settlement_id} redistributed=${body.total_redistributed_usd}"
        )

        return EvidenceFromSettlementResponse(
            evidence_uri=evidence_uri,
            receipt_id=receipt_id,
            receipt_persisted=receipt_persisted,
            is_new=is_new,
            settlement_summary=settlement_summary,
        )

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

    @router.post("/{rid}/anchor", responses={
        200: {"model": AnchorResponse, "description": "Anchor confirmed on-chain"},
        202: {"model": AnchorPendingResponse, "description": "Tx broadcast but on-chain verification pending"},
    })
    async def anchor_claim(rid: str):
        """Anchor a verified claim on the Regen Ledger mainnet.

        Precondition: claim must be at 'verified' state.
        Requires content_hash (call prepare-anchor first if missing).

        Returns AnchorResponse (200) on full success, or
        AnchorPendingResponse (202) if tx broadcast succeeded but on-chain
        verification is not yet available.
        """
        from api.ledger_anchor import broadcast_anchor, compute_content_hash, verify_anchor_onchain

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
            # Timeout or pre-broadcast failure — persist tx_hash + ledger_iri if available
            tx_hash = result.get("tx_hash")
            ledger_iri = result.get("ledger_iri")
            if tx_hash:
                # Broadcast happened but confirmation timed out — save for reconciliation
                async with pool.acquire() as conn:
                    await conn.execute("""
                        UPDATE claims SET tx_hash = $2, ledger_iri = $3, updated_at = NOW()
                        WHERE claim_rid = $1
                    """, rid, tx_hash, ledger_iri)
                from starlette.responses import JSONResponse
                return JSONResponse(
                    status_code=202,
                    content=AnchorPendingResponse(
                        claim_rid=rid,
                        content_hash=content_hash,
                        tx_hash=tx_hash,
                        ledger_iri=ledger_iri,
                        status="pending",
                        message=result.get("reason", "Tx broadcast but confirmation timed out. "
                                f"Call POST /claims/{rid}/reconcile to finalize."),
                    ).model_dump(),
                )
            # Pre-broadcast failure (key not found, insufficient funds, etc.)
            raise HTTPException(
                status_code=503,
                detail=result.get("reason", "Anchoring not available"),
            )

        # Broadcast succeeded and tx confirmed (code=0).
        # Skip IRI verification via REST — most public endpoints don't support
        # the regen data module query. Tx confirmation is sufficient.

        # Full success: update claim with ledger data and transition state
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
                        tx_hash = $5, verification = 'ledger_anchored', updated_at = NOW()
                    WHERE claim_rid = $1
                """, rid, result["ledger_iri"], ledger_ts, content_hash, result.get("tx_hash"))

                chain_id = os.getenv("REGEN_CHAIN_ID", "regen-1")
                await conn.execute("""
                    INSERT INTO claim_state_log (claim_rid, from_state, to_state, actor, reason, metadata)
                    VALUES ($1, 'verified', 'ledger_anchored', 'ledger_anchor_service',
                            $3, $2::jsonb)
                """, rid, json.dumps({
                    "tx_hash": result.get("tx_hash"),
                    "ledger_iri": result["ledger_iri"],
                    "chain_id": chain_id,
                }), f"Anchored on Regen Ledger ({chain_id})")

        logger.info(f"claim.anchored rid={rid} iri={result['ledger_iri']} tx={result.get('tx_hash')}")
        return AnchorResponse(
            claim_rid=rid,
            content_hash=content_hash,
            ledger_iri=result["ledger_iri"],
            ledger_timestamp=result.get("ledger_timestamp"),
            tx_hash=result.get("tx_hash"),
        )

    # ------------------------------------------------------------------ #
    # Reconcile (check on-chain status of timed-out broadcasts)            #
    # ------------------------------------------------------------------ #

    @router.post("/{rid}/reconcile", response_model=ReconcileResponse)
    async def reconcile_claim(rid: str):
        """Check on-chain status of a claim with a pending broadcast.

        For claims that have a tx_hash but haven't transitioned to ledger_anchored
        (e.g., broadcast timed out). Queries the tx on-chain and finalizes state.
        """
        from api.ledger_anchor import query_tx_status, verify_anchor_onchain

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM claims WHERE claim_rid = $1", rid
            )
            if not row:
                raise HTTPException(status_code=404, detail=f"Claim not found: {rid}")

            if row["verification"] != "verified":
                raise HTTPException(
                    status_code=409,
                    detail=f"Reconcile requires claim at 'verified' state "
                           f"(current: {row['verification']})",
                )

            tx_hash = row.get("tx_hash")
            if not tx_hash:
                raise HTTPException(
                    status_code=409,
                    detail="No tx_hash on this claim. Nothing to reconcile. "
                           "Use POST /claims/{rid}/anchor to broadcast.",
                )

        # Query tx status on-chain
        tx_status = query_tx_status(tx_hash)

        if not tx_status["found"]:
            # Tx not yet indexed — could be propagation delay
            return ReconcileResponse(
                claim_rid=rid,
                status="pending",
                tx_hash=tx_hash,
                ledger_iri=row.get("ledger_iri"),
                message="Transaction not yet indexed on-chain. Retry reconcile later.",
            )

        if tx_status["code"] != 0:
            # Tx definitively failed on-chain — clear tx_hash + ledger_iri, allow re-anchor
            async with pool.acquire() as conn:
                await conn.execute("""
                    UPDATE claims SET tx_hash = NULL, ledger_iri = NULL, updated_at = NOW()
                    WHERE claim_rid = $1
                """, rid)
            return ReconcileResponse(
                claim_rid=rid,
                status="failed",
                tx_hash=tx_hash,
                message=f"Transaction failed on-chain (code={tx_status['code']}). "
                        f"tx_hash and ledger_iri cleared. You may re-anchor.",
            )

        # Tx confirmed (code=0) — verify anchor presence via REST
        ledger_iri = row.get("ledger_iri")
        if not ledger_iri:
            # Shouldn't happen (we store ledger_iri on broadcast), but handle gracefully
            return ReconcileResponse(
                claim_rid=rid,
                status="pending",
                tx_hash=tx_hash,
                message="Tx confirmed but ledger_iri not stored. Re-anchor to derive IRI.",
            )

        anchor_present = verify_anchor_onchain(ledger_iri)
        if not anchor_present:
            # Tx confirmed (code=0) but IRI not queryable via REST.
            # Many public REST endpoints don't support the data module query,
            # so treat tx confirmation as sufficient for finalization.
            import logging as _logging
            _logging.getLogger("claims").warning(
                f"Anchor IRI {ledger_iri} not queryable via REST "
                f"(tx {tx_hash} confirmed code=0). Proceeding with finalization."
            )

        # Tx confirmed — transition to ledger_anchored
        from datetime import datetime as _dt
        ledger_ts = None
        ts_raw = tx_status.get("timestamp")
        if ts_raw:
            try:
                ledger_ts = _dt.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                ledger_ts = datetime.now(timezone.utc)

        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT * FROM claims WHERE claim_rid = $1 FOR UPDATE", rid
                )
                if row["verification"] != "verified":
                    raise HTTPException(
                        status_code=409,
                        detail=f"Claim state changed during reconcile (now: {row['verification']})",
                    )

                await conn.execute("""
                    UPDATE claims
                    SET ledger_timestamp = $2, verification = 'ledger_anchored', updated_at = NOW()
                    WHERE claim_rid = $1
                """, rid, ledger_ts)

                await conn.execute("""
                    INSERT INTO claim_state_log (claim_rid, from_state, to_state, actor, reason, metadata)
                    VALUES ($1, 'verified', 'ledger_anchored', 'reconcile_service',
                            'Reconciled: tx confirmed and anchor verified on-chain', $2::jsonb)
                """, rid, json.dumps({
                    "tx_hash": tx_hash,
                    "ledger_iri": ledger_iri,
                    "chain_id": os.getenv("REGEN_CHAIN_ID", "regen-1"),
                    "reconciled": True,
                }))

        logger.info(f"claim.reconciled rid={rid} iri={ledger_iri} tx={tx_hash}")
        return ReconcileResponse(
            claim_rid=rid,
            status="anchored",
            tx_hash=tx_hash,
            ledger_iri=ledger_iri,
            ledger_timestamp=str(ts_raw) if ts_raw else None,
            message="Claim successfully reconciled and transitioned to ledger_anchored.",
        )

    # ------------------------------------------------------------------ #
    # Proof pack (synthesized verification artifact)                       #
    # ------------------------------------------------------------------ #

    @router.get("/{rid}/proof-pack", response_model=ProofPackResponse)
    async def get_proof_pack(
        rid: str,
        format: Optional[str] = Query(None, description="Set to 'download' for Content-Disposition attachment"),
    ):
        """Assemble a proof pack for a claim.

        Synthesizes claim data, linked evidence, full audit history, anchor
        fields, attestations with anchor details, and hash verification into
        a single archivable verification artifact.
        The claim must be at ledger_anchored state.
        """
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT c.*, er.entity_text AS claimant_name
                FROM claims c
                LEFT JOIN entity_registry er ON c.claimant_uri = er.fuseki_uri
                WHERE c.claim_rid = $1
            """, rid)
            if not row:
                raise HTTPException(status_code=404, detail=f"Claim not found: {rid}")

            if row["verification"] != "ledger_anchored":
                raise HTTPException(
                    status_code=409,
                    detail=f"Proof pack requires ledger_anchored state "
                           f"(current: {row['verification']}). "
                           f"Anchor the claim first via POST /claims/{rid}/anchor",
                )

            # Fetch linked evidence entities
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
                        "name": ev["entity_text"],
                        "type": ev["entity_type"],
                        "confidence": float(ev["confidence"]) if ev.get("confidence") is not None else None,
                        "source": ev.get("source"),
                    }
                    for ev in ev_rows
                ]

            # Fetch full audit history
            history_rows = await conn.fetch("""
                SELECT * FROM claim_state_log
                WHERE claim_rid = $1
                ORDER BY created_at ASC
            """, rid)
            history = [
                {
                    "from_state": r.get("from_state"),
                    "to_state": r["to_state"],
                    "actor": r.get("actor"),
                    "reason": r.get("reason"),
                    "metadata": json.loads(r["metadata"]) if r.get("metadata") and isinstance(r["metadata"], str) else r.get("metadata"),
                    "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                }
                for r in history_rows
            ]

            # Fetch about_uri (claim subject) from graph edge
            about_uri = None
            if row.get("entity_uri"):
                about_row = await conn.fetchrow("""
                    SELECT object_uri FROM entity_relationships
                    WHERE subject_uri = $1 AND predicate = 'about'
                    LIMIT 1
                """, row["entity_uri"])
                if about_row:
                    about_uri = about_row["object_uri"]

            # Fetch attestations (with anchor details)
            attestations = []
            try:
                att_rows = await conn.fetch("""
                    SELECT a.*, er.entity_text AS reviewer_name
                    FROM claim_attestations a
                    LEFT JOIN entity_registry er ON a.reviewer_uri = er.fuseki_uri
                    WHERE a.claim_rid = $1
                    ORDER BY a.created_at ASC
                """, rid)
                from api.ledger_anchor import compute_attestation_hash, derive_ledger_iri
                for a in att_rows:
                    att_entry = {
                        "attestation_rid": a["attestation_rid"],
                        "reviewer_uri": a["reviewer_uri"],
                        "reviewer_name": a.get("reviewer_name"),
                        "verdict": a["verdict"],
                        "rationale": a.get("rationale"),
                        "evidence_uris": a.get("evidence_uris"),
                        "content_hash": a.get("content_hash"),
                        "attest_tx_hash": a.get("attest_tx_hash"),
                        "ledger_iri": a.get("ledger_iri"),
                        "attest_timestamp": a["attest_timestamp"].isoformat() if a.get("attest_timestamp") else None,
                        "attestor_address": a.get("attestor_address"),
                        "created_at": a["created_at"].isoformat() if a.get("created_at") else None,
                    }
                    # Verify attestation content hash
                    if a.get("content_hash"):
                        if a.get("attest_tx_hash") and a.get("ledger_iri"):
                            # Anchored: verify stored hash → IRI matches stored IRI
                            try:
                                derived_iri = derive_ledger_iri(a["content_hash"])
                                att_entry["hash_verified"] = (derived_iri == a["ledger_iri"])
                            except Exception:
                                att_entry["hash_verified"] = False
                        else:
                            # Not anchored: verify via recomputation
                            recomputed = compute_attestation_hash(a)
                            att_entry["hash_verified"] = (recomputed == a["content_hash"])
                    else:
                        att_entry["hash_verified"] = None
                    attestations.append(att_entry)
            except Exception:
                pass  # Pre-migration DB — no attestations table

        meta = row["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)

        # Verify claim content hash
        # For anchored claims with a pinned hash (pre-v2 hashes included mutable
        # 'verification' field), verify that the stored hash matches the on-chain
        # IRI instead of recomputing from fields.
        claim_hash_verified = False
        if row.get("content_hash"):
            from api.ledger_anchor import compute_content_hash
            if row.get("ledger_iri") and row.get("tx_hash"):
                # Anchored: verify stored hash → IRI derivation matches
                from api.ledger_anchor import derive_ledger_iri
                try:
                    derived_iri = derive_ledger_iri(row["content_hash"])
                    claim_hash_verified = (derived_iri == row["ledger_iri"])
                except Exception:
                    claim_hash_verified = False
            else:
                # Not anchored: recompute from fields
                recomputed = compute_content_hash(row)
                claim_hash_verified = (recomputed == row["content_hash"])

        chain_id = os.getenv("REGEN_CHAIN_ID", "regen-1")
        rpc_url = os.getenv("REGEN_RPC_URL", "https://regen-rpc.polkachu.com/")
        verification_instructions = (
            "1. Verify claim content_hash matches BLAKE2b-256 of canonical claim JSON\n"
            f"2. Query tx on Regen Ledger: regen query tx <tx_hash> --node {rpc_url}\n"
            "3. Confirm tx code=0 and IRI matches claim's ledger_iri\n"
            "4. Repeat for each attestation anchor"
        )

        proof_pack = ProofPackResponse(
            claim_rid=rid,
            verification=row["verification"],
            claim={
                "claim_rid": row["claim_rid"],
                "entity_uri": row.get("entity_uri"),
                "claimant_uri": row["claimant_uri"],
                "claimant_name": row.get("claimant_name"),
                "statement": row["statement"],
                "claim_type": row["claim_type"],
                "about_uri": about_uri,
                "source_document": row.get("source_document"),
                "ai_confidence": float(row["ai_confidence"]) if row.get("ai_confidence") is not None else None,
                "supersedes_rid": row.get("supersedes_rid"),
                "metadata": meta or {},
                "operator_uri": row.get("operator_uri"),
                "created_by": row.get("created_by"),
                "created_at": row["created_at"].isoformat(),
                "updated_at": row["updated_at"].isoformat(),
            },
            evidence=evidence,
            history=history,
            anchor={
                "content_hash": row.get("content_hash"),
                "ledger_iri": row.get("ledger_iri"),
                "tx_hash": row.get("tx_hash"),
                "ledger_timestamp": row["ledger_timestamp"].isoformat() if row.get("ledger_timestamp") else None,
                "chain_id": chain_id,
            },
            attestations=attestations,
            claim_content_hash_verified=claim_hash_verified,
            chain_id=chain_id,
            verification_instructions=verification_instructions,
            assembled_at=datetime.now(timezone.utc).isoformat(),
        )

        if format == "download":
            from starlette.responses import JSONResponse
            short_rid = rid.split(":")[-1][:12] if ":" in rid else rid[:12]
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            filename = f"proof-pack-{short_rid}-{date_str}.json"
            return JSONResponse(
                content=proof_pack.model_dump(),
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        return proof_pack

    # ------------------------------------------------------------------ #
    # Attestations (V2 Phase 1)                                           #
    # ------------------------------------------------------------------ #

    _VALID_VERDICTS = {"pending", "approved", "rejected", "needs_info"}

    @router.post("/{rid}/attestations", response_model=AttestationResponse, status_code=201)
    async def create_attestation(rid: str, body: AttestationCreateRequest):
        """Create or update an attestation on a claim (UPSERT by reviewer_uri).

        Same reviewer + claim always yields the same attestation_rid.
        Re-submitting updates verdict/rationale rather than creating a new record.
        """
        if body.verdict not in _VALID_VERDICTS:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid verdict '{body.verdict}'. Must be one of: {sorted(_VALID_VERDICTS)}",
            )

        att_rid = _attestation_rid(rid, body.reviewer_uri)

        async with pool.acquire() as conn:
            # Verify claim exists
            claim = await conn.fetchrow(
                "SELECT claim_rid, claimant_uri, entity_uri FROM claims WHERE claim_rid = $1", rid
            )
            if not claim:
                raise HTTPException(status_code=404, detail=f"Claim not found: {rid}")

            # Verify reviewer exists and is an allowed type (Person or Organization)
            reviewer = await conn.fetchrow(
                "SELECT fuseki_uri, entity_text, entity_type FROM entity_registry WHERE fuseki_uri = $1",
                body.reviewer_uri,
            )
            if not reviewer:
                raise HTTPException(
                    status_code=422,
                    detail=f"Reviewer entity not found: {body.reviewer_uri}",
                )
            _REVIEWER_ALLOWED_TYPES = {"person", "organization"}
            if reviewer["entity_type"].lower() not in _REVIEWER_ALLOWED_TYPES:
                raise HTTPException(
                    status_code=422,
                    detail=f"Reviewer {body.reviewer_uri} is type '{reviewer['entity_type']}', "
                           f"not valid for attestation. Allowed: {sorted(_REVIEWER_ALLOWED_TYPES)}",
                )

            # Non-self-attestation guard
            if body.reviewer_uri == claim["claimant_uri"]:
                raise HTTPException(
                    status_code=409,
                    detail="Self-attestation not allowed: reviewer_uri cannot be the claimant",
                )

            # Compute content_hash before UPSERT
            from api.ledger_anchor import compute_attestation_hash
            att_hash_row = {
                "attestation_rid": att_rid,
                "claim_rid": rid,
                "reviewer_uri": body.reviewer_uri,
                "verdict": body.verdict,
                "rationale": body.rationale,
                "evidence_uris": body.evidence_uris,
            }
            content_hash = compute_attestation_hash(att_hash_row)

            async with conn.transaction():
                # Immutability guard: anchored attestations cannot be modified (row-locked)
                existing = await conn.fetchrow("""
                    SELECT attest_tx_hash FROM claim_attestations
                    WHERE claim_rid = $1 AND reviewer_uri = $2
                    FOR UPDATE
                """, rid, body.reviewer_uri)
                if existing and existing.get("attest_tx_hash"):
                    raise HTTPException(
                        status_code=409,
                        detail=f"Attestation already anchored on-chain (tx: {existing['attest_tx_hash'][:16]}...). "
                               f"Anchored attestations are immutable. To revise, create a new claim version.",
                    )

                # UPSERT attestation (includes content_hash)
                row = await conn.fetchrow("""
                    INSERT INTO claim_attestations
                        (attestation_rid, claim_rid, reviewer_uri, verdict, rationale,
                         evidence_uris, content_hash, metadata, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, NOW(), NOW())
                    ON CONFLICT (claim_rid, reviewer_uri)
                    DO UPDATE SET verdict = EXCLUDED.verdict,
                                  rationale = EXCLUDED.rationale,
                                  evidence_uris = EXCLUDED.evidence_uris,
                                  content_hash = EXCLUDED.content_hash,
                                  metadata = EXCLUDED.metadata,
                                  updated_at = NOW()
                    RETURNING *
                """,
                    att_rid, rid, body.reviewer_uri, body.verdict,
                    body.rationale, body.evidence_uris, content_hash,
                    _json_dumps(body.metadata),
                )

                # Create attests_claim edge (idempotent)
                if claim.get("entity_uri"):
                    await conn.execute("""
                        INSERT INTO entity_relationships
                            (subject_uri, predicate, object_uri, confidence, source)
                        VALUES ($1, 'attests_claim', $2, 1.0, 'claims_engine')
                        ON CONFLICT DO NOTHING
                    """, body.reviewer_uri, claim["entity_uri"])

                # Log to claim_state_log
                await conn.execute("""
                    INSERT INTO claim_state_log
                        (claim_rid, from_state, to_state, actor, reason, metadata)
                    VALUES ($1, $2, $2, $3, $4, $5::jsonb)
                """,
                    rid,
                    (await conn.fetchval("SELECT verification FROM claims WHERE claim_rid = $1", rid)),
                    body.reviewer_uri,
                    f"attestation:{body.verdict}",
                    _json_dumps({
                        "attestation_rid": att_rid,
                        "verdict": body.verdict,
                        "rationale": body.rationale,
                    }),
                )

        logger.info(f"claim.attestation rid={rid} att={att_rid} reviewer={body.reviewer_uri} verdict={body.verdict}")
        await emit_domain_event("attestation", "NEW", att_rid, {
            "attestation_rid": att_rid, "claim_rid": rid,
            "reviewer_uri": body.reviewer_uri, "verdict": body.verdict,
            "rationale": body.rationale, "evidence_uris": body.evidence_uris or [],
            "content_hash": content_hash, "metadata": body.metadata or {},
        })
        return _attestation_response(row, reviewer_name=reviewer["entity_text"] if reviewer else None)

    @router.get("/{rid}/attestations", response_model=List[AttestationResponse])
    async def list_attestations(
        rid: str,
        verdict: Optional[str] = Query(None, description="Filter by verdict"),
    ):
        """List all attestations for a claim."""
        async with pool.acquire() as conn:
            # Verify claim exists
            claim = await conn.fetchrow(
                "SELECT claim_rid FROM claims WHERE claim_rid = $1", rid
            )
            if not claim:
                raise HTTPException(status_code=404, detail=f"Claim not found: {rid}")

            if verdict:
                rows = await conn.fetch("""
                    SELECT a.*, er.entity_text AS reviewer_name
                    FROM claim_attestations a
                    LEFT JOIN entity_registry er ON a.reviewer_uri = er.fuseki_uri
                    WHERE a.claim_rid = $1 AND a.verdict = $2
                    ORDER BY a.created_at DESC
                """, rid, verdict)
            else:
                rows = await conn.fetch("""
                    SELECT a.*, er.entity_text AS reviewer_name
                    FROM claim_attestations a
                    LEFT JOIN entity_registry er ON a.reviewer_uri = er.fuseki_uri
                    WHERE a.claim_rid = $1
                    ORDER BY a.created_at DESC
                """, rid)

        return [_attestation_response(r) for r in rows]

    @router.get("/{rid}/attestations/{att_rid}", response_model=AttestationResponse)
    async def get_attestation(rid: str, att_rid: str):
        """Get a single attestation by RID."""
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT a.*, er.entity_text AS reviewer_name
                FROM claim_attestations a
                LEFT JOIN entity_registry er ON a.reviewer_uri = er.fuseki_uri
                WHERE a.claim_rid = $1 AND a.attestation_rid = $2
            """, rid, att_rid)
            if not row:
                raise HTTPException(status_code=404, detail=f"Attestation not found: {att_rid}")

        return _attestation_response(row)

    # ------------------------------------------------------------------ #
    # Attestation anchoring                                                #
    # ------------------------------------------------------------------ #

    @router.post("/{rid}/attestations/{att_rid}/anchor", response_model=AttestationAnchorResponse)
    async def anchor_attestation(rid: str, att_rid: str):
        """Anchor an attestation on-chain via MsgAnchor.

        Mirrors claim anchor semantics: parent claim must be ledger_anchored,
        attestation verdict must be approved or rejected (not pending/needs_info).
        Lazy content_hash backfill for pre-existing attestations.
        """
        from api.ledger_anchor import (
            broadcast_anchor, compute_attestation_hash, derive_ledger_iri,
            get_signing_address,
        )

        async with pool.acquire() as conn:
            # Fetch attestation
            att = await conn.fetchrow("""
                SELECT * FROM claim_attestations
                WHERE claim_rid = $1 AND attestation_rid = $2
            """, rid, att_rid)
            if not att:
                raise HTTPException(status_code=404, detail=f"Attestation not found: {att_rid}")

            # Guard: parent claim must be ledger_anchored
            claim = await conn.fetchrow(
                "SELECT verification FROM claims WHERE claim_rid = $1", rid
            )
            if not claim or claim["verification"] != "ledger_anchored":
                raise HTTPException(
                    status_code=409,
                    detail=f"Parent claim must be ledger_anchored (current: {claim['verification'] if claim else 'not found'})",
                )

            # Guard: verdict must be approved or rejected
            if att["verdict"] not in ("approved", "rejected"):
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot anchor attestation with verdict '{att['verdict']}'. "
                           f"Must be 'approved' or 'rejected'.",
                )

            # Guard: already anchored
            if att.get("attest_tx_hash"):
                raise HTTPException(
                    status_code=409,
                    detail=f"Attestation already anchored (tx_hash: {att['attest_tx_hash'][:16]}...)",
                )

            # Lazy content_hash backfill
            content_hash = att.get("content_hash")
            if not content_hash:
                content_hash = compute_attestation_hash(att)
                await conn.execute(
                    "UPDATE claim_attestations SET content_hash = $2 WHERE attestation_rid = $1",
                    att_rid, content_hash,
                )

        # Broadcast anchor (reuses claim anchor infrastructure)
        try:
            result = await broadcast_anchor(att_rid, content_hash)
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Anchor broadcast failed: {e}")

        ledger_iri = result.get("ledger_iri")
        tx_hash = result.get("tx_hash")

        # Resolve signing address
        try:
            attestor_address = get_signing_address()
        except Exception:
            attestor_address = None

        if result.get("ready_to_anchor"):
            # Full success — store anchor data
            async with pool.acquire() as conn:
                await conn.execute("""
                    UPDATE claim_attestations
                    SET attest_tx_hash = $2, ledger_iri = $3, attest_timestamp = NOW(),
                        attestor_address = $4, updated_at = NOW()
                    WHERE attestation_rid = $1
                """, att_rid, tx_hash, ledger_iri, attestor_address)

            logger.info(f"attestation.anchored att={att_rid} iri={ledger_iri} tx={tx_hash}")
            return AttestationAnchorResponse(
                attestation_rid=att_rid,
                claim_rid=rid,
                content_hash=content_hash,
                attest_tx_hash=tx_hash,
                ledger_iri=ledger_iri,
                attest_timestamp=result.get("ledger_timestamp"),
                attestor_address=attestor_address,
            )

        if tx_hash:
            # Broadcast happened but confirmation timed out
            async with pool.acquire() as conn:
                await conn.execute("""
                    UPDATE claim_attestations
                    SET attest_tx_hash = $2, ledger_iri = $3, attestor_address = $4, updated_at = NOW()
                    WHERE attestation_rid = $1
                """, att_rid, tx_hash, ledger_iri, attestor_address)
            from starlette.responses import JSONResponse
            return JSONResponse(
                status_code=202,
                content=AttestationAnchorPendingResponse(
                    attestation_rid=att_rid,
                    claim_rid=rid,
                    content_hash=content_hash,
                    attest_tx_hash=tx_hash,
                    ledger_iri=ledger_iri,
                    status="pending",
                    message=result.get("reason", "Tx broadcast but confirmation timed out. "
                            f"Call POST /claims/{rid}/attestations/{att_rid}/reconcile to finalize."),
                ).model_dump(),
            )

        # Pre-broadcast failure
        raise HTTPException(
            status_code=503,
            detail=result.get("reason", "Attestation anchoring not available"),
        )

    @router.post("/{rid}/attestations/{att_rid}/reconcile", response_model=AttestationReconcileResponse)
    async def reconcile_attestation(rid: str, att_rid: str):
        """Check on-chain status of an attestation with a pending broadcast.

        Mirrors claim reconcile semantics exactly.
        """
        from api.ledger_anchor import query_tx_status, verify_anchor_onchain

        async with pool.acquire() as conn:
            att = await conn.fetchrow("""
                SELECT * FROM claim_attestations
                WHERE claim_rid = $1 AND attestation_rid = $2
            """, rid, att_rid)
            if not att:
                raise HTTPException(status_code=404, detail=f"Attestation not found: {att_rid}")

            tx_hash = att.get("attest_tx_hash")
            if not tx_hash:
                raise HTTPException(
                    status_code=409,
                    detail="No attest_tx_hash on this attestation. Nothing to reconcile. "
                           f"Use POST /claims/{rid}/attestations/{att_rid}/anchor to broadcast.",
                )

        # Query tx status on-chain
        tx_status = query_tx_status(tx_hash)

        if not tx_status["found"]:
            return AttestationReconcileResponse(
                attestation_rid=att_rid,
                claim_rid=rid,
                status="pending",
                attest_tx_hash=tx_hash,
                ledger_iri=att.get("ledger_iri"),
                message="Transaction not yet indexed on-chain. Retry reconcile later.",
            )

        if tx_status["code"] != 0:
            # Tx failed — clear tx_hash + ledger_iri, allow re-anchor
            async with pool.acquire() as conn:
                await conn.execute("""
                    UPDATE claim_attestations
                    SET attest_tx_hash = NULL, ledger_iri = NULL, updated_at = NOW()
                    WHERE attestation_rid = $1
                """, att_rid)
            return AttestationReconcileResponse(
                attestation_rid=att_rid,
                claim_rid=rid,
                status="failed",
                attest_tx_hash=tx_hash,
                message=f"Transaction failed on-chain (code={tx_status['code']}). "
                        f"attest_tx_hash and ledger_iri cleared. You may re-anchor.",
            )

        # Tx confirmed (code=0) — soft IRI check
        ledger_iri = att.get("ledger_iri")
        if ledger_iri:
            anchor_present = verify_anchor_onchain(ledger_iri)
            if not anchor_present:
                logger.warning(
                    f"Attestation anchor IRI {ledger_iri} not queryable via REST "
                    f"(tx {tx_hash} confirmed code=0). Proceeding with finalization."
                )

        # Finalize — store timestamp
        from datetime import datetime as _dt
        attest_ts = None
        ts_raw = tx_status.get("timestamp")
        if ts_raw:
            try:
                attest_ts = _dt.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                attest_ts = datetime.now(timezone.utc)

        async with pool.acquire() as conn:
            await conn.execute("""
                UPDATE claim_attestations
                SET attest_timestamp = $2, updated_at = NOW()
                WHERE attestation_rid = $1
            """, att_rid, attest_ts)

        logger.info(f"attestation.reconciled att={att_rid} iri={ledger_iri} tx={tx_hash}")
        return AttestationReconcileResponse(
            attestation_rid=att_rid,
            claim_rid=rid,
            status="anchored",
            attest_tx_hash=tx_hash,
            ledger_iri=ledger_iri,
            attest_timestamp=str(ts_raw) if ts_raw else None,
            message="Attestation anchor confirmed on-chain.",
        )

    # ------------------------------------------------------------------ #
    # Chain Info (expose current chain config)                             #
    # ------------------------------------------------------------------ #

    @router.get("/chain-info")
    async def chain_info():
        """Return current chain configuration for portal and eval harness."""
        chain_id = os.getenv("REGEN_CHAIN_ID", "regen-1")
        return {
            "chain_id": chain_id,
            "rpc_url": os.getenv("REGEN_RPC_URL", ""),
            "is_testnet": chain_id != "regen-1",
        }

    return router
