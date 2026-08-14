#!/usr/bin/env python3
"""Alert when a SeaTrees retirement references a project the exporter cannot price.

The Bloom export refuses rather than guesses (see INC-20260812-001). Refusing is
correct but it is still a failure the partner discovers. This probe finds the
same condition on a schedule, so an unregistered product surfaces to us on the
day it is first retired instead of when SeaTrees queries a wrong number.

Exit codes:
    0  every retired project in the window is fully registered
    1  at least one is not (details on stdout)
    2  the ledger could not be queried, so coverage is UNKNOWN, not clean

Usage:
    python -m scripts.check_seatrees_export_coverage
    python -m scripts.check_seatrees_export_coverage --days 30
    python -m scripts.check_seatrees_export_coverage --json
"""

import argparse
import json
import logging
import sys
from datetime import date, timedelta

from scripts.seatrees_bloom_export import (
    DEFAULT_API,
    FALLBACK_APIS,
    MBS01_PREFIXES,
    ExportRefusedError,
    MetadataCache,
    _get,
    build_bloom_row,
    query_retirements_with_fallback,
)

log = logging.getLogger(__name__)


class LedgerUnreachableError(RuntimeError):
    """No REST provider answered, so coverage is unknown."""


def assert_ledger_reachable(api: str) -> str:
    """Return the first provider that answers, or raise.

    This preflight exists because query_retirements_with_fallback returns an
    empty list BOTH for a genuine zero and for total provider failure. Without
    it, an outage renders as "0 retirements, everything registered" — a clean
    bill of health the probe has no basis for. Reporting clean when nothing was
    checked is the exact failure class this probe was written to catch.
    """
    tried = []
    for candidate in [api, *(p for p in FALLBACK_APIS if p != api)]:
        try:
            _get(f"{candidate}/regen/ecocredit/v1/classes")
            return candidate
        except Exception as e:  # noqa: BLE001 - any failure means "cannot confirm"
            tried.append(f"{candidate}: {type(e).__name__}")
    raise LedgerUnreachableError("no REST provider answered; tried " + "; ".join(tried))


def check(api: str, start: str, end: str, prefixes: tuple, max_pages: int) -> dict:
    """Run every retirement in the window through the real row builder.

    Using build_bloom_row rather than re-implementing the checks means the probe
    cannot drift from what the export actually does: whatever the exporter would
    refuse, this refuses too.
    """
    api = assert_ledger_reachable(api)
    retirements = query_retirements_with_fallback(api, start, end, prefixes, max_pages=max_pages)

    cache = MetadataCache(api)
    problems: dict[str, dict] = {}
    ok_batches: set[str] = set()

    for retirement in retirements:
        batch = retirement.get("batch_denom", "")
        try:
            build_bloom_row(retirement, cache)
            ok_batches.add(batch)
        except ExportRefusedError as e:
            key = getattr(e, "project_id", batch)
            entry = problems.setdefault(key, {
                "project_id": key,
                "batch_denom": batch,
                "reason": e.message(),
                "missing": list(getattr(e, "missing", ())),
                "remedy": e.remedy(),
                "retirement_count": 0,
                "first_seen": retirement.get("timestamp", "")[:10],
                "credits": 0.0,
            })
            entry["retirement_count"] += 1
            entry["credits"] += float(retirement.get("amount", 0) or 0)
            stamp = retirement.get("timestamp", "")[:10]
            if stamp and stamp < entry["first_seen"]:
                entry["first_seen"] = stamp

    return {
        "window": {"start": start, "end": end},
        "retirements_scanned": len(retirements),
        "exportable_batches": sorted(ok_batches),
        "unexportable": sorted(problems.values(), key=lambda p: p["project_id"]),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=90, help="Trailing window in days (default: 90)")
    parser.add_argument("--start", help="Explicit start date (YYYY-MM-DD); overrides --days")
    parser.add_argument("--end", help="Explicit end date (YYYY-MM-DD); defaults to today")
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--batch-prefixes", nargs="+", default=list(MBS01_PREFIXES))
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    end = args.end or date.today().isoformat()
    start = args.start or (date.fromisoformat(end) - timedelta(days=args.days)).isoformat()

    try:
        report = check(args.api, start, end, tuple(args.batch_prefixes), args.max_pages)
    except Exception as e:
        # An unreachable ledger means coverage is unknown. Never report clean.
        payload = {"status": "unknown", "error": f"{type(e).__name__}: {e}"}
        print(json.dumps(payload, indent=2) if args.json else
              f"COVERAGE UNKNOWN: could not query the ledger: {type(e).__name__}: {e}")
        return 2

    report["status"] = "gap" if report["unexportable"] else "ok"

    if args.json:
        print(json.dumps(report, indent=2))
    elif report["status"] == "ok":
        print(f"OK: {report['retirements_scanned']} retirement(s) in {start}..{end}; "
              f"every referenced project is fully registered.")
    else:
        print(f"COVERAGE GAP: {len(report['unexportable'])} project(s) retired in {start}..{end} "
              f"cannot be exported. The Bloom export will refuse until each is registered.\n")
        for p in report["unexportable"]:
            print(f"  {p['project_id']}  ({p['retirement_count']} retirement(s), "
                  f"{p['credits']:g} credits, first {p['first_seen']})")
            print(f"    {p['reason']}")
            print(f"    missing: {', '.join(p['missing'])}\n")
        print(report["unexportable"][0]["remedy"])

    return 1 if report["status"] == "gap" else 0


if __name__ == "__main__":
    raise SystemExit(main())
