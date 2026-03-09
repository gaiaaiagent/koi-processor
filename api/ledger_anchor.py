"""Ledger anchoring stub — content hash computation + broadcast placeholder.

V1: Computes BLAKE2b-256 hash of canonical claim serialization.
    Broadcast is stubbed — returns ready_to_anchor=False.
V2: Wire up to Regen Ledger REST endpoint when service account is funded.
"""

import hashlib
import json
import logging

logger = logging.getLogger(__name__)


def _canonical_claim_json(row) -> str:
    """Canonical JSON serialization of a claim for hashing.

    Includes all content fields that define the claim's identity.
    Sorted keys + deterministic serialization ensures same content → same hash.
    """
    meta = row["metadata"]
    if isinstance(meta, str):
        meta = json.loads(meta)

    obj = {
        "claimant_uri": row["claimant_uri"],
        "statement": row["statement"],
        "claim_type": row["claim_type"],
        "verification": row["verification"],
        "metadata": meta or {},
    }

    # Include evidence and versioning if present
    if row.get("supersedes_rid"):
        obj["supersedes_rid"] = row["supersedes_rid"]

    return json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(',', ':'))


def compute_content_hash(row) -> str:
    """Compute BLAKE2b-256 hash of canonical claim serialization.

    Returns hex-encoded hash string.
    """
    canonical = _canonical_claim_json(row)
    h = hashlib.blake2b(canonical.encode(), digest_size=32)
    return h.hexdigest()


async def broadcast_anchor(claim_rid: str, content_hash: str) -> dict:
    """STUB: Broadcast content hash to Regen Ledger via MsgAnchor.

    V1: Logs intent and returns not-ready status.
    V2: HTTP POST to Regen Ledger REST endpoint with service account credentials.

    Returns:
        dict with anchoring result status
    """
    logger.info(f"ledger_anchor.broadcast_stub rid={claim_rid} hash={content_hash[:16]}...")

    # TODO: When service account is configured:
    # 1. POST to Regen Ledger REST API with MsgAnchor
    # 2. Store ledger_iri and ledger_timestamp on claim
    # 3. Transition verification to 'ledger_anchored'

    return {
        "claim_rid": claim_rid,
        "content_hash": content_hash,
        "ready_to_anchor": False,
        "reason": "Service account not configured. Ledger anchoring will be enabled when funded.",
        "ledger_iri": None,
        "ledger_timestamp": None,
    }
