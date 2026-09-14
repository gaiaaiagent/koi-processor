#!/usr/bin/env python3
"""Run both document-ingest integrity gates against a document, read-only.

Issues #62 (identity) and #64 (semantic quality). This exists so the gates are
something an operator can RUN before ingesting a paper, not something that was
demonstrated once in a session transcript. A check with no entry point is a check
nothing reaches.

Nothing here writes. Every statement is a SELECT or a pure computation.

    GATE 1 — identity
        Collect the payload's complete distinct typed endpoint set, classify each
        against the live registry, and attempt the freeze. Reports blockers, the
        bijection, and which endpoints were only found by the #61 compatibility
        read (their stored normalized_text is stale).

    GATE 2 — semantic quality
        Deterministic, no model call. Chunk coverage, citation range and support,
        predicate concentration, discourse variety.

Usage:

    # against a live document's stored extraction
    python scripts/check_document_integrity.py --document-rid document:<sha256>

    # against a curated payload file, checked against the live graph
    python scripts/check_document_integrity.py \
        --document-rid document:<sha256> \
        --payload ~/Documents/sources/<slug>/ingest/curated-deep-extraction.json

    # machine-readable
    python scripts/check_document_integrity.py --document-rid ... --json

Exit codes
    0  both gates pass
    1  a gate failed (identity blocked, or semantic verdict is `fail`)
    2  semantic verdict is `review` and --strict-review was given, OR identity is
       `pass_after_preregistration` and --strict-preregistration was given
    3  misconfiguration (document not found, payload unreadable, no chunks)

`review` exits 0 by default. It means "a human should look", not "this is broken",
and making it fail by default would push operators toward running with the gate
off — which is worse than a verdict nobody blocks on.

`pass_after_preregistration` ALSO exits 0, and that is a correction (2026-09-14).
The status was introduced precisely to say "the only unbound endpoints are ones
that do not exist yet, which is every first ingest" — and then the exit code
lumped it in with `blocked` anyway, so the two michaelgarfield documents whose
sole finding was a not-yet-existing essay reported exit 1. An operator reading the
number rather than the word would conclude the gate had refused them. Use
--strict-preregistration when you genuinely mean "this payload must create
nothing".
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).parent.parent))
from api import document_extraction_contract as contract  # noqa: E402
from api import ingest_identity as ident  # noqa: E402
from api.extraction_quality import assess_extraction  # noqa: E402
from api.resolution_primitives import (  # noqa: E402
    normalize_alias,
    normalize_entity_text,
)

POSTGRES_URL = os.getenv(
    "POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")


class _Window:
    """Minimal stand-in for the extractor's Window, for the coverage denominator."""

    def __init__(self, index, chunk_indices):
        self.index = index
        self.chunk_indices = chunk_indices
        self.chunk_index_base = min(chunk_indices) if chunk_indices else 0


async def _load(conn, document_rid: str):
    chunks = {
        r["chunk_index"]: (r["text"] or "")
        for r in await conn.fetch(
            "SELECT chunk_index, content->>'text' AS text FROM koi_memory_chunks "
            "WHERE document_rid = $1 ORDER BY chunk_index", document_rid)
    }
    wins = await conn.fetch(
        "SELECT window_index, char_start, char_end, chunk_index_base, status, "
        "       raw_json, route_used, provider, model, transport "
        "  FROM document_window_extractions WHERE document_rid = $1 "
        " ORDER BY window_index", document_rid)
    return chunks, list(wins)


def _payload_from_windows(wins) -> dict:
    facts, entities, moves = [], [], []
    for w in wins:
        if not w["raw_json"]:
            continue
        d = json.loads(w["raw_json"])
        facts += d.get("facts") or []
        entities += d.get("entities") or []
        moves += d.get("discourse") or []
    # De-dupe entities by (name, type) the way the merge would.
    seen, ents = set(), []
    for e in entities:
        k = (normalize_entity_text(e.get("name") or ""), e.get("type"))
        if k not in seen:
            seen.add(k)
            ents.append(e)
    return {"entities": ents, "facts": facts, "discourse": moves, "type_map": {}}


def _windows_for_coverage(wins, chunks) -> list:
    """Reconstruct each window's chunk band.

    `document_window_extractions` stores `chunk_index_base` but not the band, so
    the band is the half-open interval up to the NEXT window's base. Derived from
    the stored plan rather than recomputed from WINDOW_CHARS, because the plan is
    what the extraction actually used and the env var may have changed since.
    """
    if not wins or not chunks:
        return []
    hi = max(chunks)
    bases = [w["chunk_index_base"] for w in wins]
    out = []
    for i, w in enumerate(wins):
        start = bases[i]
        end = bases[i + 1] - 1 if i + 1 < len(bases) else hi
        out.append(_Window(w["window_index"],
                           [c for c in range(start, end + 1) if c in chunks]))
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--document-rid", required=True)
    ap.add_argument("--payload", help="curated extraction JSON; defaults to the "
                                      "stored window extractions")
    ap.add_argument("--tier", default="thorough", choices=("rag", "standard", "thorough"))
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--strict-review", action="store_true",
                    help="exit 2 when the semantic verdict is `review`")
    ap.add_argument("--strict-preregistration", action="store_true",
                    help="exit 2 when identity is `pass_after_preregistration`, i.e. "
                         "the payload would create entities that do not exist yet")
    ap.add_argument("--alias-decisions", help="JSON file of audited {label: uri} — "
                                              "WHICH identity a label means")
    ap.add_argument("--type-decisions", help="JSON file of audited {label: entity_type} — "
                                            "WHAT a label is; the only thing that clears "
                                            "a cross_type_conflict")
    args = ap.parse_args()

    alias_decisions = {}
    if args.alias_decisions:
        alias_decisions = json.loads(Path(args.alias_decisions).expanduser().read_text())

    # Issue #68 requirement 7. `preflight_endpoints` has accepted type decisions
    # since #66, but nothing passed them: the parameter existed and no caller
    # supplied it, so the blocker's own remedy ("Supply an audited type decision")
    # was unreachable from a command line. A decision naming a type the extractor
    # cannot emit is refused here rather than silently binding nothing.
    type_decisions = {}
    if args.type_decisions:
        type_decisions = json.loads(Path(args.type_decisions).expanduser().read_text())
        bad = {k: v for k, v in type_decisions.items()
               if not contract.is_extractable(v)}
        if bad:
            print(f"MISCONFIGURED: --type-decisions names type(s) outside "
                  f"{contract.DOCUMENT_TYPE_CONTRACT_VERSION}: "
                  f"{json.dumps(bad, sort_keys=True)}. Admitted: "
                  f"{', '.join(contract.DOCUMENT_ENTITY_TYPE_NAMES)}", file=sys.stderr)
            return 3

    conn = await asyncpg.connect(POSTGRES_URL)
    try:
        chunks, wins = await _load(conn, args.document_rid)
        if not chunks:
            print(f"MISCONFIGURED: no chunks for {args.document_rid}", file=sys.stderr)
            return 3

        if args.payload:
            try:
                payload = json.loads(Path(args.payload).expanduser().read_text())
            except Exception as e:  # noqa: BLE001
                print(f"MISCONFIGURED: cannot read payload: {e}", file=sys.stderr)
                return 3
        else:
            payload = _payload_from_windows(wins)

        report: dict = {"document_rid": args.document_rid, "tier": args.tier}

        # ── GATE 1 — identity ────────────────────────────────────────────────
        identity_ok = False
        identity_evidence: dict = {"mode": "check-only"}
        try:
            eps = ident.collect_endpoints(payload, normalize=normalize_entity_text)
            pre = await ident.preflight_endpoints(
                conn, eps, normalize=normalize_entity_text,
                normalize_alias_fn=normalize_alias, alias_decisions=alias_decisions,
                type_decisions=type_decisions)
            identity_evidence["preflight"] = pre.as_evidence()
            ident.require_no_blockers(pre)
            # An endpoint the preflight classified MISSING is not unbindable — a real
            # ingest pre-registers it before freezing. Reporting it as a BLOCK made
            # this read-only audit say "blocked" for documents whose only issue was
            # entities that do not exist yet, which is every first ingest.
            would_create = sorted(f.endpoint.name for f in pre.missing)
            report.setdefault("identity", {})
            frozen = await ident.freeze_endpoint_map(
                conn, eps, normalize=normalize_entity_text,
                alias_decisions=alias_decisions)
            bijective = len(frozen.by_name) == frozen.distinct_uris
            identity_evidence["frozen_map"] = frozen.as_evidence()
            identity_evidence["verification"] = {"binding_verified": bijective,
                                                 "checked_facts": len(payload.get("facts") or []),
                                                 "offending_facts": 0}
            identity_ok = bijective
            report["identity"] = {
                "status": "pass" if bijective else "fail",
                "endpoints": len(frozen.by_name),
                "distinct_uris": frozen.distinct_uris,
                "bijective": bijective,
                "state_counts": pre.counts(),
                "drift_recovered": pre.as_evidence()["drift_recovered"],
                "tombstones_recorded": [f.endpoint.name for f in pre.findings
                                        if f.tombstoned_uris],
            }
        except ident.IdentityError as e:
            blockers = e.blockers[:10]
            # Separate "does not exist yet" from "cannot be bound". Only the latter
            # is a gate failure; the former is what preregistration is for.
            pending = [b for b in blockers if b.get("state") == ident.STATE_MISSING]
            real = [b for b in blockers if b.get("state") != ident.STATE_MISSING]
            report["identity"] = {
                "status": "blocked" if real else "pass_after_preregistration",
                "reason": str(e)[:800],
                "blockers": real,
                "would_preregister": [b.get("name") for b in pending],
            }

        # ── GATE 2 — semantic quality ───────────────────────────────────────
        q = assess_extraction(
            merged=payload, chunks_by_index=chunks,
            windows=_windows_for_coverage(wins, chunks), tier=args.tier,
            discourse_moves=payload.get("discourse"),
            identity_evidence=identity_evidence)
        report["quality"] = q.as_dict()

        # ── Type contract, reported but never gated ─────────────────────────
        # Read off the payload as it stands rather than re-running the merge: this
        # is an audit of what a stored extraction contains, and recomputing would
        # report what a merge WOULD do today, which is a different claim.
        seen_types: dict = {}
        for e in payload.get("entities") or []:
            seen_types[e.get("type")] = seen_types.get(e.get("type"), 0) + 1
        report["type_contract"] = {
            "version": contract.DOCUMENT_TYPE_CONTRACT_VERSION,
            "admitted": len(contract.DOCUMENT_ENTITY_TYPE_NAMES),
            "types_present": dict(sorted((str(k), v) for k, v in seen_types.items())),
            "off_contract_types": sorted(
                str(k) for k in seen_types if not contract.is_extractable(k)),
            "conflicts": payload.get("type_conflicts") or [],
            "audited_type_decisions": type_decisions,
        }

        # ── Provenance, reported but never gated ────────────────────────────
        report["provenance"] = {
            "windows": len(wins),
            "providers": sorted({w["provider"] for w in wins if w["provider"]}),
            "models": sorted({w["model"] for w in wins if w["model"]}),
            "routes": sorted({w["route_used"] for w in wins if w["route_used"]}),
            "windows_without_producer": sum(1 for w in wins if not w["provider"]),
        }

        if args.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            i = report["identity"]
            print(f"document: {args.document_rid}")
            print(f"\nGATE 1 — identity: {i['status'].upper()}")
            if i["status"] in ("blocked", "pass_after_preregistration"):
                print(f"  {i['reason'][:400]}")
                for b in i.get("blockers") or []:
                    print(f"  BLOCKER {b.get('state')}: {b.get('name')!r} "
                          f"({b.get('type')}) existing={b.get('existing') or b.get('live_uris')}")
                pend = i.get("would_preregister") or []
                if pend:
                    print(f"  {len(pend)} endpoint(s) a real ingest would pre-register: "
                          f"{', '.join(map(repr, pend[:8]))}"
                          + (" …" if len(pend) > 8 else ""))
            else:
                print(f"  {i['endpoints']} endpoints -> {i['distinct_uris']} distinct URIs "
                      f"(bijective={i['bijective']})")
                print(f"  states: {i['state_counts']}")
                if i["drift_recovered"]:
                    print(f"  {i['drift_recovered']} endpoint(s) found ONLY via the #61 "
                          f"compatibility read — their stored normalized_text is stale")
                if i["tombstones_recorded"]:
                    print(f"  tombstones alongside a live row (evidence, not blocks): "
                          f"{i['tombstones_recorded']}")
            print(f"\nGATE 2 — semantic quality: {q.status.upper()}")
            for d in q.dimensions:
                print(f"  {d.name:26} {d.status:7} {str(d.value):9} {d.detail[:66]}")
            tc = report["type_contract"]
            print(f"\ntype contract: {tc['version']} | {tc['admitted']} admitted types"
                  f" | {len(tc['conflicts'])} cross-window coercion(s)"
                  f" | {len(tc['off_contract_types'])} off-contract type(s)")
            for c in tc["conflicts"][:6]:
                print(f"  coerced {c['name']!r} -> {c['kept']} over "
                      f"{'/'.join(c['dropped'])}")
            if tc["off_contract_types"]:
                print(f"  off-contract types present: {tc['off_contract_types']} — these "
                      f"rank below every admitted type in a cross-window merge")
            p = report["provenance"]
            print(f"\nprovenance: {p['windows']} windows | providers={p['providers'] or '-'} "
                  f"| models={p['models'] or '-'} | routes={p['routes']}")
            if p["windows_without_producer"]:
                print(f"  {p['windows_without_producer']} window(s) predate migration 125 "
                      f"and carry no producer — route_used alone is unreliable (see #64)")

        identity_status = report["identity"]["status"]
        if identity_status not in ("pass", "pass_after_preregistration") or q.status == "fail":
            return 1
        if q.status == "review" and args.strict_review:
            return 2
        if identity_status == "pass_after_preregistration" and args.strict_preregistration:
            return 2
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
