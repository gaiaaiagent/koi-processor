#!/usr/bin/env python3
"""Phase 3: land the full Nate B. Jones corpus from the Phase 1 snapshot.

Drives `_apply_document` — the SHIPPED federation handler — rather than calling
the sink directly, so the backfill exercises exactly the path a polled event
takes: containment allowlist, slug->author derivation, URL sanitising,
subscriber-PII redaction, and the transactional rollback that refuses to leave a
document behind with null embeddings.

Reads ONLY from ~/.local/share/personal-koi/nate-jones-bundles/. It never
re-fetches: the coordinator's historical bundles survive solely because its
koi-cache-prune.service is broken, and re-fetching would make this run depend on
that bug still being present.

Resumable. A document already present with a complete, fully-embedded chunk set
is skipped; anything partial is re-ingested (the sink is idempotent —
DELETE-then-INSERT keyed on document_rid).

    KOI_FEDERATE_DOCUMENTS=true python scripts/federation/backfill_nate_corpus.py
    ... --limit 5 --dry-run
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from api import document_federation  # noqa: E402
from api.domain_event_handlers import _apply_document, FederationDeferred  # noqa: E402

SNAPSHOT = Path.home() / ".local/share/personal-koi/nate-jones-bundles"
PG = os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")

# text-embedding-3-large, $0.13 per 1M tokens. Chars/4 is the usual rough token
# estimate. The guard exists because an unbounded loop over a corpus is exactly
# where a pricing surprise lands; the plan's precedent is a $5 abort.
USD_PER_1M_TOKENS = 0.13
COST_ABORT_USD = float(os.getenv("COST_ABORT_USD", "5.0"))


def estimate_cost(chars: int) -> float:
    return (chars / 4.0) / 1_000_000.0 * USD_PER_1M_TOKENS


async def already_complete(conn, rid: str) -> bool:
    """True when this document is present AND every chunk carries an embedding.

    `count(*) = count(embedding_3072)` and non-zero, never count(*) alone: an
    unembedded chunk is invisible to retrieval while looking perfectly healthy
    by row count.
    """
    row = await conn.fetchrow(
        """SELECT (SELECT count(*) FROM koi_memories WHERE rid = $1) AS doc,
                  count(*) AS chunks, count(embedding_3072) AS embedded
           FROM koi_memory_chunks WHERE document_rid = $1""", rid)
    return bool(row["doc"]) and row["chunks"] > 0 and row["chunks"] == row["embedded"]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not document_federation.document_federation_enabled():
        print("KOI_FEDERATE_DOCUMENTS is not enabled — refusing to run.", file=sys.stderr)
        return 2
    if not document_federation.redaction_addresses():
        print("KOI_FEDERATE_DOCUMENTS_REDACT_EMAILS is unset — refusing to run a "
              "bulk ingest that would index subscriber addresses verbatim.",
              file=sys.stderr)
        return 2

    files = sorted(SNAPSHOT.glob("*.json"))
    if not files:
        print(f"no bundles at {SNAPSHOT}", file=sys.stderr)
        return 2
    if args.limit:
        files = files[:args.limit]

    bundles = [json.loads(f.read_text()) for f in files]
    total_chars = sum(len(b["contents"]["document"].get("content", "")) for b in bundles)
    est = estimate_cost(total_chars)
    print(f"snapshot: {len(bundles)} bundles, {total_chars:,} chars")
    print(f"estimated embedding cost: ${est:.2f}  (abort guard ${COST_ABORT_USD:.2f})")
    if est > COST_ABORT_USD:
        print("ABORT: estimate exceeds the cost guard.", file=sys.stderr)
        return 3
    if args.dry_run:
        print("dry-run: no writes.")
        return 0

    pool = await asyncpg.create_pool(PG, min_size=2, max_size=max(4, args.concurrency + 1))
    done = skipped = failed = 0
    deferred: list[tuple[str, str]] = []
    t0 = time.time()
    lock = asyncio.Lock()
    sem = asyncio.Semaphore(args.concurrency)

    async def one(i: int, bundle: dict):
        nonlocal done, skipped, failed
        rid = bundle["rid"]
        async with sem:
            async with pool.acquire() as conn:
                if await already_complete(conn, rid):
                    async with lock:
                        skipped += 1
                    return
                try:
                    await _apply_document(conn, rid, "NEW", bundle["contents"], "backfill")
                    async with lock:
                        done += 1
                except FederationDeferred as e:
                    async with lock:
                        failed += 1
                        deferred.append((rid, str(e)[:120]))
                except Exception as e:  # noqa: BLE001 — one bad document must not end the run
                    async with lock:
                        failed += 1
                        deferred.append((rid, f"{type(e).__name__}: {e}"[:120]))
        async with lock:
            n = done + skipped + failed
            if n % 25 == 0 or n == len(bundles):
                el = time.time() - t0
                rate = n / el if el else 0
                eta = (len(bundles) - n) / rate if rate else 0
                print(f"  {n:>3}/{len(bundles)}  landed={done} skipped={skipped} "
                      f"failed={failed}  {el/60:.1f}m elapsed, ~{eta/60:.1f}m left")

    try:
        await asyncio.gather(*(one(i, b) for i, b in enumerate(bundles)))
    finally:
        await pool.close()

    print(f"\nlanded={done}  skipped(already complete)={skipped}  failed={failed}  "
          f"in {(time.time()-t0)/60:.1f} min")
    if deferred:
        print("\nfailures (NOT confirmed, safe to re-run — the sink is idempotent):")
        for rid, err in deferred[:15]:
            print(f"  {rid[-28:]}  {err}")
        if len(deferred) > 15:
            print(f"  ... and {len(deferred)-15} more")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
