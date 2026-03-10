"""Ledger anchoring — content hash computation + CLI-based broadcast to Regen testnet.

Computes BLAKE2b-256 hash of canonical claim serialization, derives IRI via
regen CLI, and broadcasts MsgAnchor to the regen-upgrade testnet.
"""

import base64
import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time

logger = logging.getLogger(__name__)

# Config from environment (set in personal.env, no mnemonic)
REGEN_CHAIN_ID = os.getenv("REGEN_CHAIN_ID", "regen-upgrade")
REGEN_RPC_URL = os.getenv("REGEN_RPC_URL", "https://rpc-regen-upgrade.vitwit.com/")
REGEN_REST_URL = os.getenv("REGEN_REST_URL", "https://api-regen-upgrade.vitwit.com/")
REGEN_KEY_NAME = os.getenv("REGEN_KEY_NAME", "claims-service")


def _canonical_claim_json(row) -> str:
    """Canonical JSON serialization of a claim for hashing.

    Includes all content fields that define the claim's identity.
    Sorted keys + deterministic serialization ensures same content → same hash.
    """
    meta = row["metadata"]
    if isinstance(meta, str):
        meta = json.loads(meta)

    obj = {
        "claim_rid": row["claim_rid"],
        "entity_uri": row.get("entity_uri") or "",
        "claimant_uri": row["claimant_uri"],
        "statement": row["statement"],
        "claim_type": row["claim_type"],
        "verification": row["verification"],
        "metadata": meta or {},
    }

    # Include versioning if present
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


def _check_regen_cli() -> str:
    """Return path to regen binary, or raise if not found."""
    regen_path = shutil.which("regen")
    if not regen_path:
        raise RuntimeError(
            "regen CLI binary not found in PATH. "
            "Install from https://github.com/regen-network/regen-ledger/releases"
        )
    return regen_path


def derive_ledger_iri(content_hash: str) -> str:
    """Derive ledger IRI from content hash via regen CLI.

    Uses per-request temp file for concurrency safety.
    The hash must be base64-encoded (not hex) for the CLI.
    """
    regen_bin = _check_regen_cli()

    # Convert hex hash to base64 (CLI expects base64)
    hash_bytes = bytes.fromhex(content_hash)
    hash_b64 = base64.b64encode(hash_bytes).decode()

    hash_json = {
        "raw": {
            "hash": hash_b64,
            "digest_algorithm": 1,  # DIGEST_ALGORITHM_BLAKE2B_256
            "file_extension": "json",  # no leading dot
        }
    }

    # Per-request temp file with guaranteed cleanup
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(hash_json, f)
            tmp_path = f.name

        result = subprocess.run(
            [regen_bin, "q", "data", "convert-hash-to-iri", tmp_path,
             "--node", REGEN_RPC_URL, "--output", "json"],
            capture_output=True, text=True, timeout=30,
        )

        if result.returncode != 0:
            raise RuntimeError(f"IRI derivation failed: {result.stderr.strip()}")

        parsed = json.loads(result.stdout)
        iri = parsed.get("iri")
        if not iri:
            raise RuntimeError(f"No IRI in CLI response: {result.stdout}")

        return iri
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


async def broadcast_anchor(claim_rid: str, content_hash: str) -> dict:
    """Broadcast content hash to Regen Ledger via MsgAnchor using CLI.

    1. Derives IRI from content hash
    2. Broadcasts MsgAnchor transaction via regen CLI
    3. Polls for tx confirmation
    4. Returns anchoring result with IRI and timestamp
    """
    regen_bin = _check_regen_cli()

    # 1. Derive IRI
    iri = derive_ledger_iri(content_hash)
    logger.info(f"ledger_anchor.broadcast rid={claim_rid} iri={iri}")

    # 2. Broadcast MsgAnchor
    tx_result = subprocess.run(
        [regen_bin, "tx", "data", "anchor", iri,
         "--from", REGEN_KEY_NAME,
         "--chain-id", REGEN_CHAIN_ID,
         "--node", REGEN_RPC_URL,
         "--keyring-backend", "test",
         "--fees", "5000uregen",
         "--output", "json",
         "--yes"],
        capture_output=True, text=True, timeout=30,
    )

    if tx_result.returncode != 0:
        stderr = tx_result.stderr.strip()
        if "key not found" in stderr.lower():
            return {
                "claim_rid": claim_rid,
                "content_hash": content_hash,
                "ready_to_anchor": False,
                "reason": f"Key '{REGEN_KEY_NAME}' not found in keyring. "
                          f"Run: regen keys add {REGEN_KEY_NAME} --keyring-backend test",
                "ledger_iri": iri,
                "ledger_timestamp": None,
            }
        if "insufficient funds" in stderr.lower() or "insufficient fee" in stderr.lower():
            return {
                "claim_rid": claim_rid,
                "content_hash": content_hash,
                "ready_to_anchor": False,
                "reason": f"Insufficient funds for '{REGEN_KEY_NAME}'. Fund from testnet faucet.",
                "ledger_iri": iri,
                "ledger_timestamp": None,
            }
        raise RuntimeError(f"Anchor broadcast failed: {stderr or tx_result.stdout}")

    tx_data = json.loads(tx_result.stdout)
    tx_hash = tx_data.get("txhash")
    if not tx_hash:
        raise RuntimeError(f"No txhash in broadcast response: {tx_result.stdout}")

    logger.info(f"ledger_anchor.broadcast_sent rid={claim_rid} txhash={tx_hash}")

    # 3. Poll for tx confirmation (up to 30s)
    ledger_timestamp = None
    for attempt in range(6):
        time.sleep(5)
        query_result = subprocess.run(
            [regen_bin, "query", "tx", tx_hash,
             "--node", REGEN_RPC_URL,
             "--output", "json"],
            capture_output=True, text=True, timeout=15,
        )
        if query_result.returncode == 0:
            query_data = json.loads(query_result.stdout)
            code = query_data.get("code", -1)
            if code == 0:
                ledger_timestamp = query_data.get("timestamp") or query_data.get("height")
                logger.info(f"ledger_anchor.confirmed rid={claim_rid} txhash={tx_hash}")
                break
            else:
                raise RuntimeError(
                    f"Tx failed on-chain: code={code} log={query_data.get('raw_log', '')}"
                )

    if ledger_timestamp is None:
        logger.warning(f"ledger_anchor.timeout rid={claim_rid} txhash={tx_hash} — tx may still confirm")

    return {
        "claim_rid": claim_rid,
        "content_hash": content_hash,
        "ready_to_anchor": True,
        "ledger_iri": iri,
        "ledger_timestamp": str(ledger_timestamp) if ledger_timestamp else None,
        "tx_hash": tx_hash,
    }
