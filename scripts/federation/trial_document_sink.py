#!/usr/bin/env python3
"""Phase 2 trial: prove the federated-document landing path on N documents.

Drives `KOIPoller._process_event` — the REAL dispatch entry point — rather than
calling `_apply_document` directly, because the defect this phase exists to
prevent is precisely a handler that is registered but never reached. A test that
calls the handler directly passes while dispatch is broken.

Reads bundles from the Phase 1 on-disk snapshot (never re-fetches: the
coordinator's cache survives only because its prune service is broken).

Asserts, per the plan's acceptance criteria:
  AC3  — parent rows == N, and chunks == embedded, both non-zero.
  AC4  — recomputed JCS manifest hash matches the bundle's stored sha256_hash.
  AC10 — a non-allowlisted coordinator RID through the IDENTICAL path is
         rejected and lands zero rows, while the allowed RIDs do land.
  plus — author / is_private / access_source / canonical URL are non-null on
         the landed rows (the four fields the unmodified sink dropped).

Usage:
    set -a; source config/personal.env; set +a
    KOI_FEDERATE_DOCUMENTS=true \
      python scripts/federation/trial_document_sink.py --limit 3
"""

import argparse
import asyncio
import base64
import json
import os
import re
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from api.koi_poller import KOIPoller  # noqa: E402
from api import koi_net_router  # noqa: E402
from api import document_federation  # noqa: E402

SNAPSHOT = Path.home() / ".local/share/personal-koi/nate-jones-bundles"
POSTGRES_URL = os.getenv(
    "POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")

# A real coordinator RID that is NOT a Nate Jones newsletter. Used as the AC10
# containment control: same entry point, same shape, must land nothing.
CONTROL_RID = "regen.newsletter:newsletter_nate-hagens-substack_deadbeefdeadbeef"


def _url_leaks_pii(url) -> bool:
    """True if a URL embeds a base64 token decoding to the subscriber address.

    Substack tracking-pixel URLs carry a JWT whose payload includes the
    recipient email. Storing one in `source_url` would surface it in search
    results, so this is asserted, not assumed.
    """
    if not url:
        return False
    for tok in re.findall(r"eyJ[A-Za-z0-9_-]{20,}", url):
        try:
            payload = base64.urlsafe_b64decode(tok + "=" * (-len(tok) % 4))
        except Exception:
            continue
        if b"@" in payload and b"zaldarren" in payload:
            return True
    return False


def load_bundles(limit: int) -> list[dict]:
    files = sorted(SNAPSHOT.glob("*.json"))
    if not files:
        raise SystemExit(f"no bundles in {SNAPSHOT} — run Phase 1 first")
    return [json.loads(f.read_text()) for f in files[:limit]]


def control_bundle(template: dict) -> dict:
    """A well-formed document bundle on a disallowed RID."""
    b = json.loads(json.dumps(template))
    b["rid"] = CONTROL_RID
    b["contents"]["document"]["id"] = "newsletter_nate-hagens-substack_deadbeefdeadbeef"
    b["contents"]["document"]["source"] = "newsletters:nate-hagens-substack"
    b["contents"]["metadata"]["newsletter_slug"] = "nate-hagens-substack"
    return b


async def feed(poller: KOIPoller, bundle: dict, event_type: str = "NEW"):
    await poller._process_event(
        rid=bundle["rid"],
        event_type=event_type,
        contents=bundle["contents"],
        manifest=bundle.get("manifest"),
        source_node="orn:koi-net.node:koi-coordinator-main+trial",
        event_id=None,
    )


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--cleanup", action="store_true",
                    help="delete the trial documents afterwards")
    args = ap.parse_args()

    bundles = load_bundles(args.limit)
    rids = [b["rid"] for b in bundles]
    print(f"trial bundles ({len(rids)}):")
    for r in rids:
        print(f"  {r}")

    # ── AC4 — manifest hash, through the shipped function ────────────────────
    hash_ok = sum(
        1 for b in bundles
        if koi_net_router._jcs_sha256(b["contents"]) == b["manifest"]["sha256_hash"]
    )
    print(f"\nAC4 manifest hash: {hash_ok}/{len(bundles)} match stored sha256_hash")

    pool = await asyncpg.create_pool(POSTGRES_URL, min_size=1, max_size=3)
    try:
        # Clear any prior trial state so counts mean what they say.
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM koi_memory_chunks WHERE document_rid = ANY($1::text[])",
                rids + [CONTROL_RID])
            await conn.execute(
                "DELETE FROM koi_memories WHERE rid = ANY($1::text[])",
                rids + [CONTROL_RID])

        poller = KOIPoller(pool=pool, node_rid="orn:koi-net.node:trial+0")

        print("\nfeeding through KOIPoller._process_event ...")
        for b in bundles:
            await feed(poller, b)

        # ── AC10 containment control, IDENTICAL entry point ──────────────────
        ctl = control_bundle(bundles[0])
        print(f"\nAC10 control (must be rejected): {ctl['rid']}")
        await feed(poller, ctl)

        async with pool.acquire() as conn:
            docs = await conn.fetchval(
                "SELECT count(*) FROM koi_memories WHERE rid = ANY($1::text[])", rids)
            ctl_docs = await conn.fetchval(
                "SELECT count(*) FROM koi_memories WHERE rid = $1", CONTROL_RID)
            ctl_chunks = await conn.fetchval(
                "SELECT count(*) FROM koi_memory_chunks WHERE document_rid = $1",
                CONTROL_RID)
            chunks, embedded = await conn.fetchrow(
                "SELECT count(*), count(embedding_3072) FROM koi_memory_chunks "
                "WHERE document_rid = ANY($1::text[])", rids)
            rows = await conn.fetch(
                """SELECT rid, is_private, access_source, source_sensor,
                          metadata->>'author'     AS author,
                          metadata->>'source_url' AS source_url,
                          content->>'title'       AS title
                   FROM koi_memories WHERE rid = ANY($1::text[]) ORDER BY rid""", rids)
            chunk_meta = await conn.fetchrow(
                """SELECT count(*) FILTER (WHERE metadata ? 'author')     AS with_author,
                          count(*) FILTER (WHERE metadata ? 'source_url') AS with_url,
                          count(*) FILTER (WHERE metadata ? 'title')      AS with_title,
                          count(*) AS total
                   FROM koi_memory_chunks WHERE document_rid = ANY($1::text[])""", rids)

        async with pool.acquire() as conn:
            expected_url_chunks = await conn.fetchval(
                """SELECT count(*) FROM koi_memory_chunks c
                   JOIN koi_memories m ON m.rid = c.document_rid
                   WHERE c.document_rid = ANY($1::text[])
                     AND m.metadata->>'source_url' IS NOT NULL""", rids)
            legacy_1024 = await conn.fetchval(
                "SELECT count(embedding) FROM koi_memory_chunks "
                "WHERE document_rid = ANY($1::text[])", rids)
            # Subscriber PII must not survive into either table.
            addrs = list(document_federation.redaction_addresses())
            pii_docs = pii_chunks = total_redactions = 0
            if addrs:
                pats = [f"%{a}%" for a in addrs]
                pii_docs = await conn.fetchval(
                    "SELECT count(*) FROM koi_memories WHERE rid = ANY($1::text[]) "
                    "AND lower(content->>'text') LIKE ANY($2::text[])", rids, pats)
                total_redactions = await conn.fetchval(
                    "SELECT COALESCE(sum((metadata->>'redactions_applied')::int), 0) "
                    "FROM koi_memories WHERE rid = ANY($1::text[])", rids)
                pii_chunks = await conn.fetchval(
                    "SELECT count(*) FROM koi_memory_chunks "
                    "WHERE document_rid = ANY($1::text[]) "
                    "AND lower(content->>'text') LIKE ANY($2::text[])", rids, pats)

        print("\n── landed rows ──")
        for r in rows:
            print(f"  {r['rid'][-24:]}  private={r['is_private']}  "
                  f"access={r['access_source']}  author={r['author']!r}")
            print(f"      sensor={r['source_sensor']}  url={r['source_url']}")
            print(f"      title={(r['title'] or '')[:70]!r}")

        checks = [
            ("AC4  manifest hash matches", hash_ok == len(bundles),
             f"{hash_ok}/{len(bundles)}"),
            ("AC3  parent docs == N", docs == len(rids), f"{docs} == {len(rids)}"),
            ("AC3  chunks == embedded", chunks == embedded, f"{chunks} == {embedded}"),
            ("AC3  chunks non-zero", chunks > 0, f"chunks={chunks}"),
            ("AC10 control landed 0 docs", ctl_docs == 0, f"docs={ctl_docs}"),
            ("AC10 control landed 0 chunks", ctl_chunks == 0, f"chunks={ctl_chunks}"),
            # Trap: the legacy vector(1024) column is read by zero retrieval
            # code. Writing it would be invisible waste.
            ("     legacy 1024 column untouched", legacy_1024 == 0,
             f"non-null embedding={legacy_1024}"),
            # Two halves, deliberately. "No PII found" alone would pass if the
            # trial set simply had none — so the run must also show that
            # redaction actually FIRED on this set.
            ("     no subscriber PII in landed content",
             bool(addrs) and pii_docs == 0 and pii_chunks == 0,
             f"docs={pii_docs} chunks={pii_chunks} "
             f"(redacting {len(addrs)} address(es))"),
            ("     redaction demonstrably fired", total_redactions > 0,
             f"{total_redactions} redaction(s) across the trial set"),
            # `rows` is required non-empty in each: all([]) is True, and a
            # vacuous pass on an empty result set is exactly the shape that
            # reports a broken ingest as healthy.
            ("     author non-null on all",
             bool(rows) and all(r["author"] for r in rows),
             str([r["author"] for r in rows])),
            ("     is_private TRUE on all",
             bool(rows) and all(r["is_private"] is True for r in rows),
             str([r["is_private"] for r in rows])),
            ("     access_source non-null on all",
             bool(rows) and all(r["access_source"] for r in rows),
             str([r["access_source"] for r in rows])),
            # NOT "every document has a URL": 112/448 bundles carry only a
            # tracking-pixel URL, so demanding one would make the criterion
            # unsatisfiable by the data. The invariants that DO hold are that a
            # stored URL is always on the publication's host, and that at least
            # one document carries one (so a total failure to map URLs fails).
            ("     no stored URL is off-host",
             all(r["source_url"] is None
                 or r["source_url"].startswith("https://natesnewsletter.substack.com/")
                 for r in rows),
             str([r["source_url"] for r in rows])),
            ("     >=1 canonical URL stored",
             any(r["source_url"] for r in rows),
             f"{sum(1 for r in rows if r['source_url'])}/{len(rows)}"),
            ("     no stored URL leaks subscriber PII",
             not any(_url_leaks_pii(r["source_url"]) for r in rows),
             "checked token payloads"),
            ("     chunk metadata carries author",
             chunk_meta["total"] > 0 and chunk_meta["with_author"] == chunk_meta["total"],
             f"{chunk_meta['with_author']}/{chunk_meta['total']}"),
            # source_url is absent on chunks of documents whose bundle has no
            # canonical URL — expected, so this asserts consistency with the
            # parent rows rather than universal presence.
            ("     chunk source_url matches parents",
             chunk_meta["with_url"] == expected_url_chunks,
             f"{chunk_meta['with_url']}/{chunk_meta['total']} "
             f"(expected {expected_url_chunks})"),
            ("     chunk metadata carries title",
             chunk_meta["total"] > 0 and chunk_meta["with_title"] == chunk_meta["total"],
             f"{chunk_meta['with_title']}/{chunk_meta['total']}"),
        ]

        print("\n── assertions ──")
        failed = 0
        for name, ok, detail in checks:
            print(f"  {'PASS' if ok else 'FAIL'}  {name:38s}  {detail}")
            if not ok:
                failed += 1

        if args.cleanup:
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM koi_memory_chunks WHERE document_rid = ANY($1::text[])",
                    rids + [CONTROL_RID])
                await conn.execute(
                    "DELETE FROM koi_memories WHERE rid = ANY($1::text[])",
                    rids + [CONTROL_RID])
            print("\ncleaned up trial rows")

        print(f"\n{'ALL CHECKS PASSED' if failed == 0 else f'{failed} CHECK(S) FAILED'}")
        return 1 if failed else 0
    finally:
        await pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
