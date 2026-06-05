"""SHACL validation for claims (OFF by default).

Validates a claim's RDF representation against the FWG LinkML-derived SHACL
shape (``schema/shacl/claim.shacl.ttl``, generated from regen-data-standards
``Claim.yaml``).

Two hard design constraints, both learned the hard way:

1. **Never block the event loop.** The personal koi-processor runs as a
   single-worker service; a synchronous ``pyshacl.validate()`` on the hot
   ``POST /claims/`` path can starve the asyncpg pool and take the whole
   service down. All validation runs in ``asyncio.to_thread()``.

2. **Fail closed, not silent.** If validation is enabled but the shape file is
   missing, raise — do not silently accept unvalidated claims (a silent skip
   gives false confidence).

STATUS (2026-06-05): this module is **not yet wired into create_claim**. The
koi-processor claim JSON-LD (flat keys: ``claimant_uri``/``claim_type``/
``statement``) does not match the LinkML ``rfs:Claim`` slot vocabulary
(``hasClaimant``/``hasSubject``/``hasPrimaryImpact``/...). Enabling validation
requires aligning the claim representation first. See
``docs/claims/shacl-alignment-gap.md``.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

# schema/shacl/claim.shacl.ttl relative to repo root (this file is api/shacl_validation.py)
_DEFAULT_SHAPE = Path(__file__).resolve().parent.parent / "schema" / "shacl" / "claim.shacl.ttl"


def shacl_enabled() -> bool:
    """True only when VALIDATE_CLAIMS_SHACL is explicitly 'true'. Default off."""
    return os.getenv("VALIDATE_CLAIMS_SHACL", "false").strip().lower() == "true"


def _validate_sync(data_ttl: str, shape_path: str) -> tuple[bool, str]:
    """Blocking validation — only ever called via asyncio.to_thread()."""
    import rdflib  # imported lazily so the module loads even if deps are absent
    from pyshacl import validate

    data_graph = rdflib.Graph().parse(data=data_ttl, format="turtle")
    shape_graph = rdflib.Graph().parse(shape_path, format="turtle")
    conforms, _results_graph, results_text = validate(
        data_graph,
        shacl_graph=shape_graph,
        inference="rdfs",
        advanced=True,
        meta_shacl=False,
    )
    return bool(conforms), results_text


async def validate_claim_ttl(data_ttl: str, shape_path: str | os.PathLike | None = None) -> tuple[bool, str]:
    """Validate a claim RDF graph (Turtle) against the claim SHACL shape.

    Returns (conforms, human_readable_report). Runs pyshacl off the event loop.
    Raises RuntimeError if the shape file is missing (fail-closed).
    """
    shape = Path(shape_path) if shape_path else _DEFAULT_SHAPE
    if not shape.exists():
        raise RuntimeError(
            f"SHACL shape file not found: {shape} — refusing to skip validation (fail-closed). "
            "Check the schema/shacl/ directory is present in the deployed checkout."
        )
    return await asyncio.to_thread(_validate_sync, data_ttl, str(shape))
