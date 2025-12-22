"""
KOI SignedEnvelope utilities (minimal, protocol-aligned).
"""

from __future__ import annotations

import json
import os
from base64 import b64decode, b64encode
from typing import Any, Dict, Optional, Tuple

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import (
        decode_dss_signature,
        encode_dss_signature
    )
    _CRYPTO_AVAILABLE = True
except Exception:  # pragma: no cover - runtime dependency
    _CRYPTO_AVAILABLE = False
    InvalidSignature = Exception
    ec = None


class EnvelopeError(Exception):
    """Raised when envelope validation fails."""


def _normalize_pem(pem: str) -> str:
    return pem.replace("\\n", "\n")


def _unsigned_envelope_bytes(payload: Dict[str, Any], source_node: str, target_node: str) -> bytes:
    envelope = {
        "payload": payload,
        "source_node": source_node,
        "target_node": target_node
    }
    return json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode()


def _der_to_raw_signature(der_signature: bytes) -> bytes:
    r, s = decode_dss_signature(der_signature)
    byte_length = (ec.SECP256R1().key_size + 7) // 8
    r_bytes = r.to_bytes(byte_length, byteorder="big")
    s_bytes = s.to_bytes(byte_length, byteorder="big")
    return r_bytes + s_bytes


def _raw_to_der_signature(raw_signature: bytes) -> bytes:
    byte_length = (ec.SECP256R1().key_size + 7) // 8
    if len(raw_signature) != 2 * byte_length:
        raise EnvelopeError(f"Raw signature must be {2 * byte_length} bytes")
    r_bytes = raw_signature[:byte_length]
    s_bytes = raw_signature[byte_length:]
    r = int.from_bytes(r_bytes, byteorder="big")
    s = int.from_bytes(s_bytes, byteorder="big")
    return encode_dss_signature(r, s)


def load_private_key_from_env(prefix: str = "KOI"):
    if not _CRYPTO_AVAILABLE:
        return None

    pem = os.getenv(f"{prefix}_PRIVATE_KEY_PEM")
    pem_path = os.getenv(f"{prefix}_PRIVATE_KEY_PEM_PATH")
    password = os.getenv(f"{prefix}_PRIVATE_KEY_PASSWORD")

    if pem_path:
        with open(pem_path, "r") as f:
            pem = f.read()
    if not pem:
        return None

    pem = _normalize_pem(pem)
    password_bytes = password.encode() if password else None

    return serialization.load_pem_private_key(
        data=pem.encode(),
        password=password_bytes
    )


def sign_envelope(
    payload: Dict[str, Any],
    source_node: str,
    target_node: str,
    private_key
) -> Dict[str, Any]:
    if not _CRYPTO_AVAILABLE:
        raise EnvelopeError("cryptography is required for signing envelopes")
    message = _unsigned_envelope_bytes(payload, source_node, target_node)
    der_signature = private_key.sign(message, ec.ECDSA(hashes.SHA256()))
    raw_signature = _der_to_raw_signature(der_signature)
    signature = b64encode(raw_signature).decode()
    return {
        "payload": payload,
        "source_node": source_node,
        "target_node": target_node,
        "signature": signature
    }


def verify_envelope(
    envelope: Dict[str, Any],
    public_keys: Dict[str, Any],
    expected_target: Optional[str] = None,
    enforce_target: bool = False
) -> Tuple[Dict[str, Any], str]:
    if not _CRYPTO_AVAILABLE:
        raise EnvelopeError("cryptography is required for verifying envelopes")

    if not isinstance(envelope, dict) or "payload" not in envelope:
        raise EnvelopeError("Invalid envelope structure")

    source_node = envelope.get("source_node")
    target_node = envelope.get("target_node")
    signature = envelope.get("signature")

    if enforce_target and expected_target and target_node != expected_target:
        raise EnvelopeError(f"Envelope target {target_node!r} does not match {expected_target!r}")

    if not source_node or not signature:
        raise EnvelopeError("Envelope missing source_node or signature")

    public_key = public_keys.get(source_node)
    if not public_key:
        raise EnvelopeError(f"No public key registered for {source_node}")

    message = _unsigned_envelope_bytes(envelope["payload"], source_node, target_node)
    raw_signature = b64decode(signature)
    der_signature = _raw_to_der_signature(raw_signature)

    try:
        public_key.verify(der_signature, message, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise EnvelopeError("Invalid envelope signature") from exc

    return envelope["payload"], source_node
