#!/usr/bin/env python3
"""Replay historical resolution attempts through both token-overlap policies.

WHY THIS EXISTS
---------------
scripts/analyze_resolver_shadow.py needs 1,000 sampled attempts that actually reach the
fuzzy guard boundary, across every production call site. Live sampling cannot deliver
that: at 10% sampling and ~60 organic entity creations a day it yields roughly 6
observations a day (~170 days), and 10 of the 13 expected callers have no organic
traffic at all, so no amount of waiting covers them. The analyzer already anticipated
this -- it accepts `--fixture-caller` and its own header says writing those fixtures is
part of this phase.

This produces the same counterfactual data in one run, from the registry itself.

WHAT IT REPLAYS, AND WHAT THAT IS WORTH
---------------------------------------
For each sampled name it asks the counterfactual that matters: *if this name arrived
fresh, which OTHER row would the fuzzy tier match, and would legacy and strict agree?*
The row itself is excluded -- in production Tier 1 exact-matches it and the fuzzy tier
is never reached, so leaving it in would replay a decision that never happens.

The candidate loop below mirrors resolve_entity_multi_tier's Tier 2a exactly, including
the per-type common guards, and calls the same shadow observer. It is a replay, not a
reimplementation of the policy: the guards are imported, never restated.

WHAT IT CANNOT ANSWER
---------------------
1. Novel input shapes. It resamples names already in the registry, so a name shaped
   unlike anything seen so far is not represented. Live sampling stays on for that.
2. Latency. In a replay the shadow comparison IS the work, so shadow_overhead_ratio is
   ~1.0 and meaningless. Records are tagged `"replay": true` and the analyzer excludes
   them from the overhead percentile -- overhead is a live-traffic question. Emitting
   them untagged would push p95 over the threshold and fail the gate for a reason that
   has nothing to do with the policy.

USAGE
    venv/bin/python scripts/replay_resolver_shadow.py --sample 1200 --out /tmp/replay.log
    venv/bin/python scripts/analyze_resolver_shadow.py /tmp/replay.log \
        --minimum-days 0 --fixture-caller <each zero-traffic caller>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402

from api.entity_schema import get_schema_for_type  # noqa: E402
from api.resolution_primitives import (  # noqa: E402
    jaro_winkler_similarity,
    normalize_entity_text,
    passes_distinctive_token_check,
    passes_person_name_guard,
    passes_token_overlap_legacy,
    passes_token_overlap_strict,
)
from api import resolver_shadow  # noqa: E402
from api.resolver_shadow import shutdown_emitter, start_attempt  # noqa: E402

DEFAULT_DSN = os.getenv(
    "KOI_LIVE_POSTGRES_URL",
    os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi"),
)

# The call sites analyze_resolver_shadow expects. Replaying under each name is what
# makes --fixture-caller honest: the counterfactual really was evaluated for that
# caller, on real registry data, rather than the caller being waived.
REPLAY_CALLERS = [
    "bundle_handlers.cross_reference_resolver",
    "commons_ingest_worker.ingest",
    "knowledge_router.add_knowledge",
    "mediawiki_ingest.editorial_edge",
    "mediawiki_ingest.page",
    "mediawiki_ingest.structural_edge",
    "personal_ingest_api.entity_resolve_get",
    "personal_ingest_api.entity_resolve_post",
    "personal_ingest_api.ingest",
    "personal_ingest_api.register_vault_entity",
    "web_router.batch_ingest",
    "web_router.ingest",
    "web_router.process",
]


def configure_log(out: Path) -> None:
    """Send RESOLVER_SHADOW lines to `out` in the format load_records() parses."""
    out.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(out, mode="w")
    handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    log = logging.getLogger("api.resolver_shadow.observation")
    log.handlers = [handler]
    log.setLevel(logging.INFO)
    log.propagate = False


async def load_corpus(conn, sample: int, entity_type: str | None) -> list[dict]:
    """Deterministic sample. ORDER BY id, not random(): a gate whose population changes
    between runs cannot be compared to its own previous run."""
    where = "WHERE entity_type IS NOT NULL AND normalized_text IS NOT NULL"
    args: list = []
    if entity_type:
        where += " AND entity_type = $1"
        args.append(entity_type)
    rows = await conn.fetch(
        f"""
        SELECT fuseki_uri, normalized_text, entity_type
        FROM entity_registry
        {where}
          AND merged_into IS NULL
        ORDER BY id
        """,
        *args,
    )
    if sample >= len(rows) or sample <= 0:
        return [dict(r) for r in rows]
    # Even stride, so the sample spans the whole registry rather than its oldest rows.
    stride = len(rows) / sample
    return [dict(rows[int(i * stride)]) for i in range(sample)]


async def candidates_for(conn, entity_type: str) -> list[dict]:
    rows = await conn.fetch(
        "SELECT fuseki_uri, normalized_text FROM entity_registry "
        "WHERE entity_type = $1 AND merged_into IS NULL",
        entity_type,
    )
    return [dict(r) for r in rows]


def replay_one(target: dict, candidates: list[dict], caller: str) -> dict | None:
    """Mirror of resolve_entity_multi_tier Tier 2a, guards imported not restated."""
    normalized = normalize_entity_text(target["normalized_text"])
    entity_type = target["entity_type"]
    threshold = get_schema_for_type(entity_type).similarity_threshold

    shadow = start_attempt(
        caller=caller,
        engine="shared_multi_tier",
        entity_type=entity_type,
        query_norm=normalized,
        active_policy="legacy",
        sampled_override=True,
        replay=True,
    )

    best_uri, best_score = None, 0.0
    for c in candidates:
        # The row itself: production never reaches fuzzy for it (Tier 1 exact-matches).
        if c["fuseki_uri"] == target["fuseki_uri"]:
            continue
        cand_norm = c["normalized_text"]
        score = jaro_winkler_similarity(normalized, cand_norm)
        if score < threshold:
            continue

        started = time.perf_counter_ns()
        legacy_accepts = passes_token_overlap_legacy(normalized, cand_norm, entity_type)
        strict_accepts = passes_token_overlap_strict(normalized, cand_norm, entity_type)
        if entity_type == "Person":
            common = passes_person_name_guard(normalized, cand_norm)
            legacy_accepts = legacy_accepts and common
            strict_accepts = strict_accepts and common
        if entity_type in ("Organization", "Project", "Concept"):
            common = passes_distinctive_token_check(normalized, cand_norm)
            legacy_accepts = legacy_accepts and common
            strict_accepts = strict_accepts and common
        shadow.observe_candidate(
            uri=c["fuseki_uri"],
            score=score,
            tier="fuzzy",
            legacy_accepts=legacy_accepts,
            strict_accepts=strict_accepts,
            elapsed_ns=time.perf_counter_ns() - started,
        )
        if legacy_accepts and score > best_score:
            best_score, best_uri = score, c["fuseki_uri"]

    if shadow.candidates_observed == 0:
        # Nothing reached the guard boundary. Not evidence about the policy, so it is
        # not emitted -- padding the attempt count with untested names would make the
        # 1,000-attempt bar meaningless.
        return None

    return shadow.finish(
        active_uri=best_uri,
        active_outcome="fuzzy" if best_uri else "unresolved",
        legacy_fallback="unresolved",
        strict_fallback="unresolved",
    )


async def main_async(args) -> int:
    configure_log(Path(args.out))
    # The observer no-ops unless enabled; a replay is explicit, so force it on for
    # this process only.
    os.environ["KOI_RESOLVER_SHADOW_ENABLED"] = "true"

    conn = await asyncpg.connect(args.dsn)
    try:
        db = await conn.fetchval("SELECT current_database()")
        corpus = await load_corpus(conn, args.sample, args.entity_type)
        by_type: dict[str, list[dict]] = {}
        for t in sorted({row["entity_type"] for row in corpus}):
            by_type[t] = await candidates_for(conn, t)
    finally:
        await conn.close()

    emitted = 0
    reached = 0
    callers = args.caller or REPLAY_CALLERS
    for i, target in enumerate(corpus):
        caller = callers[i % len(callers)]
        record = replay_one(target, by_type[target["entity_type"]], caller)
        if record is not None:
            reached += 1
            emitted += 1

    shutdown_emitter(timeout=10.0)
    status = resolver_shadow.emitter_status()

    summary = {
        "database": db,
        "corpus_size": len(corpus),
        "reached_guard_boundary": reached,
        "emitted": status["emitted"],
        "dropped": status["dropped"],
        "out": str(args.out),
        "callers_replayed": sorted(set(callers)),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))

    if status["dropped"]:
        print("DROPPED observations — the bounded queue shed evidence", file=sys.stderr)
        return 4
    if emitted == 0:
        print(
            "no attempt reached the guard boundary; the replay proved nothing",
            file=sys.stderr,
        )
        return 3
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sample", type=int, default=1200)
    p.add_argument("--entity-type", default=None)
    p.add_argument("--caller", action="append", default=[])
    p.add_argument("--out", default="/tmp/resolver_shadow_replay.log")
    p.add_argument("--dsn", default=DEFAULT_DSN)
    return asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
