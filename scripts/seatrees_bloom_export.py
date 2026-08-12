#!/usr/bin/env python3
"""SeaTrees Bloom Retirement Export.

Queries Regen Ledger REST API for retirement events, joins with
batch/project metadata, and outputs Bloom-compatible CSV/XLSX.

Usage:
    python -m scripts.seatrees_bloom_export --start 2026-02-01 --end 2026-02-28
    python -m scripts.seatrees_bloom_export --start 2024-10-01 --end 2024-10-31 --output seatrees_oct_2024.csv
    python -m scripts.seatrees_bloom_export --start 2024-10-01 --end 2024-10-31 --xlsx --output seatrees_oct_2024.xlsx
"""

import argparse
import csv
import json
import logging
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

log = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────

DEFAULT_API = "https://rest-regen.ecostake.com"
# Fallback REST providers ordered by tx-search depth.
# EcoStake and Cosmos Directory both serve archive history (verified 2026-05-04
# via probe of block 25,500,000 from 2026-02-09). PublicNode and Polkachu are
# pruned (lowest available height ~25.7M and ~26.1M respectively) so they
# can't reliably answer queries that need to walk older blocks.
FALLBACK_APIS = [
    "https://rest-regen.ecostake.com",
    "https://rest.cosmos.directory/regen",
    "https://regen-rest.publicnode.com",
]
# MBS01 batch denom prefixes (SeaTrees / Seatrees+ Biodiversity Blocks)
# Only known batch: MBS01-001-20240601-20340531-001
MBS01_PREFIXES = ("MBS01-",)

# Commercial facts the chain cannot supply.
#
# A retirement carries no price: price is a sales fact known only to SeaTrees.
# The same is true of the scheme label and the credit geometry. These values are
# therefore per-project, NOT global — a second product priced differently is the
# normal case, not an exception.
#
# Everything in COMMERCIAL_FIELDS must be present for a project to be exportable.
COMMERCIAL_FIELDS = (
    "credit_price",
    "transaction_description",
    "credit_scheme",
    "activity_type",
    "avg_price_per_hectare_per_year",
    "credit_size",
    "credit_length",
)

# ISO 3166-1 alpha-2 → country name (subset relevant to MBS01 projects)
COUNTRY_CODES = {
    "KE": "Kenya",
    "US": "United States",
    "ID": "Indonesia",
    "PH": "Philippines",
    "MX": "Mexico",
    "CO": "Colombia",
    "BR": "Brazil",
    "MG": "Madagascar",
    "TZ": "Tanzania",
    "IN": "India",
    "AU": "Australia",
    # Added 2026-08-12: MBS01-002 is CR-P (Puntarenas) and MBS01-003 is ES-PM
    # (Balearic Islands). Both were unmapped, so project_country would have
    # emitted the raw ISO code and project_region would have been blank.
    "CR": "Costa Rica",
    "ES": "Spain",
}

# Jurisdiction country → region mapping
REGION_MAP = {
    "Kenya": "East Africa",
    "Tanzania": "East Africa",
    "Madagascar": "East Africa",
    "Indonesia": "Southeast Asia",
    "Philippines": "Southeast Asia",
    "Mexico": "Latin America",
    "Colombia": "Latin America",
    "Brazil": "Latin America",
    "India": "South Asia",
    "Australia": "Oceania",
    "United States": "North America",
    "Costa Rica": "Latin America",
    "Spain": "Europe",
}

# Per-project registry.
#
# Keyed by on-chain project id. Carries the commercial facts above plus the
# project name and developer, which SHOULD come from the chain but cannot: the
# regen: IRI content-addressable metadata needs a data server that is not
# publicly reachable. Until that is fixed, name/developer are hand-maintained
# here too. Resolving that dependency deletes those two keys, not the table.
#
# A retirement for a project absent from this table CANNOT be exported. Every
# commercial field would otherwise fall back to another product's value, which
# is silently wrong in a money column. See export_rows().
PROJECTS = {
    "MBS01-001": {
        "name": "Mangrove Forest: Marereni",
        "developer": "",
        "credit_price": 3,
        "transaction_description": "Purchase of Seatrees+ Biodiversity Blocks",
        "credit_scheme": "Seatrees+ Biodiversity Blocks",
        "activity_type": "Uplift, Stewardship",
        "avg_price_per_hectare_per_year": 3000,
        "credit_size": 0.0001,
        "credit_length": 10,
    },
}


class ExportRefusedError(RuntimeError):
    """The export cannot proceed without emitting a value that would be wrong.

    Every subclass carries enough detail to fix the cause, so a refusal is a
    task rather than a wall. Never downgrade one of these to a warning: the
    whole point is that the alternative is a plausible-looking wrong number in
    a partner's spreadsheet.
    """

    def message(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def remedy(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError


class UnmappedJurisdictionError(ExportRefusedError):
    """The project's on-chain jurisdiction has no country mapping.

    Without it, project_country emits a bare ISO code and project_region is
    blank. Both are silent degradations in a partner-facing column.
    """

    def __init__(self, project_id: str, batch_denom: str, jurisdiction: str, country_code: str):
        self.project_id = project_id
        self.batch_denom = batch_denom
        self.jurisdiction = jurisdiction
        self.country_code = country_code
        self.missing = (f"country for ISO code {country_code!r}",)
        super().__init__(self.message())

    def message(self) -> str:
        return (
            f"project {self.project_id} (batch {self.batch_denom}) has jurisdiction "
            f"{self.jurisdiction!r}, whose country code {self.country_code!r} is not mapped"
        )

    def remedy(self) -> str:
        return (
            f'Add to COUNTRY_CODES in {Path(__file__).name}:\n\n'
            f'    "{self.country_code}": "<country name>",\n\n'
            f"then add that country to REGION_MAP so project_region resolves."
        )


class UnregisteredProjectError(ExportRefusedError):
    """A retirement references a project with no registry entry.

    Emitting the row instead means shipping another product's price in a
    partner's accounting column.
    """

    def __init__(self, project_id: str, batch_denom: str, missing: tuple[str, ...] = ()):
        self.project_id = project_id
        self.batch_denom = batch_denom
        self.missing = missing or ("name",) + COMMERCIAL_FIELDS
        super().__init__(self.message())

    def message(self) -> str:
        return (
            f"project {self.project_id} (batch {self.batch_denom}) is not registered "
            f"for Bloom export; missing: {', '.join(self.missing)}"
        )

    def remedy(self) -> str:
        """A paste-ready registry entry, so the fix is mechanical."""
        lines = [f'    "{self.project_id}": {{']
        lines.append('        "name": "",            # project name as SeaTrees refers to it')
        lines.append('        "developer": "",       # blank falls back to the on-chain admin address')
        for field in COMMERCIAL_FIELDS:
            lines.append(f'        "{field}": ...,')
        lines.append("    },")
        return (
            f"Add to PROJECTS in {Path(__file__).name}:\n\n" + "\n".join(lines) +
            "\n\nThe commercial values must come from SeaTrees. Do not guess them."
        )

# Bloom spreadsheet columns (23 total)
BLOOM_COLUMNS = [
    "date (RETIREMENT)",
    "purchase_type",
    "purchase_amount",
    "number_of_credits",
    "credit_price",
    "transaction_description",
    "project_name",
    "project_developer",
    "project_country",
    "project_region",
    "credit_scheme",
    "activity_type",
    "avg_price_per_hectare_per_year",
    "credit_size",
    "credit_length",
    "land_size",
    "buyer_name",
    "buyer_email",
    "buyer_company",
    "buyer_country",
    "buyer_type",
    "buyer_channel",
    "buyer_notes",
]


# ── HTTP helpers ─────────────────────────────────────────────────────

class PrunedHistoryError(RuntimeError):
    """Raised when a Cosmos REST provider can't serve the requested block height
    because it has pruned that history. Lets the fallback chain skip the dead
    provider instead of running a 50-page scan that returns silently empty."""


def _get(url: str, params: dict = None) -> dict:
    """GET request returning parsed JSON."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "seatrees-bloom-export/1.0")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:500]
        if "lowest height is" in body or "is not available" in body:
            raise PrunedHistoryError(body) from e
        raise RuntimeError(f"HTTP {e.code} from {url}: {body}") from e
    # Some providers return a 200 with a Cosmos error envelope in the body.
    if isinstance(data, dict) and "lowest height is" in str(data.get("message") or ""):
        raise PrunedHistoryError(data["message"])
    return data


# ── Metadata cache ───────────────────────────────────────────────────

class MetadataCache:
    """Caches batch→project and project→metadata lookups."""

    def __init__(self, api_url: str):
        self.api = api_url.rstrip("/")
        self._batch_cache: dict[str, dict] = {}
        self._project_cache: dict[str, dict] = {}

    def get_batch_info(self, batch_denom: str) -> dict:
        if batch_denom not in self._batch_cache:
            url = f"{self.api}/regen/ecocredit/v1/batches/{batch_denom}"
            try:
                data = _get(url)
                self._batch_cache[batch_denom] = data.get("batch", {})
            except Exception as e:
                log.warning("Failed to fetch batch %s: %s", batch_denom, e)
                self._batch_cache[batch_denom] = {}
        return self._batch_cache[batch_denom]

    def get_project_info(self, project_id: str) -> dict:
        if project_id not in self._project_cache:
            url = f"{self.api}/regen/ecocredit/v1/projects/{project_id}"
            try:
                data = _get(url)
                self._project_cache[project_id] = data.get("project", {})
            except Exception as e:
                log.warning("Failed to fetch project %s: %s", project_id, e)
                self._project_cache[project_id] = {}
        return self._project_cache[project_id]

    def resolve_project_metadata(self, batch_denom: str) -> dict:
        """Resolve batch → project → full export record.

        Raises UnregisteredProjectError if the project has no registry entry, or
        if its entry is missing any commercial field. The caller must not
        substitute defaults: every commercial value is product-specific, so a
        default is another product's number.
        """
        batch = self.get_batch_info(batch_denom)
        project_id = batch.get("project_id", "")
        if not project_id:
            raise UnregisteredProjectError("<unresolved>", batch_denom)

        entry = PROJECTS.get(project_id)
        if entry is None:
            raise UnregisteredProjectError(project_id, batch_denom)

        missing = tuple(f for f in COMMERCIAL_FIELDS if entry.get(f) is None)
        if not entry.get("name"):
            missing = ("name",) + missing
        if missing:
            raise UnregisteredProjectError(project_id, batch_denom, missing)

        project = self.get_project_info(project_id)
        jurisdiction = project.get("jurisdiction", "")

        # Country and region genuinely do come from the chain, but only if the
        # ISO code is mapped. An unmapped code used to fall through as the bare
        # code with a blank region, which is a silent degradation.
        country_code = jurisdiction.split("-")[0] if jurisdiction else ""
        if country_code not in COUNTRY_CODES:
            raise UnmappedJurisdictionError(project_id, batch_denom, jurisdiction, country_code)
        country = COUNTRY_CODES[country_code]
        region = REGION_MAP.get(country, "")

        # A blank registered developer falls back to the on-chain admin address,
        # matching long-standing behaviour for MBS01-001.
        developer = entry.get("developer", "")
        if not developer:
            admin = project.get("admin", "")
            if admin:
                log.warning("Using admin address as developer for project %s: %s", project_id, admin)
                developer = admin

        record = dict(entry)
        record.update({
            "project_id": project_id,
            "developer": developer,
            "country": country,
            "region": region,
        })
        return record


# ── Retirement query ─────────────────────────────────────────────────

def _extract_retirements_from_tx(tx_resp: dict, batch_prefixes: tuple) -> list[dict]:
    """Extract matching retirement records from a single tx_response."""
    timestamp = tx_resp.get("timestamp", "")
    if not timestamp:
        return []

    results = []
    seen = set()

    for event in tx_resp.get("events", []):
        if event.get("type") != "regen.ecocredit.v1.EventRetire":
            continue
        attrs = {a["key"]: a.get("value", "").strip('"') for a in event.get("attributes", [])}
        batch_denom = attrs.get("batch_denom", "")
        if not any(batch_denom.startswith(p) for p in batch_prefixes):
            continue

        dedup_key = (timestamp, batch_denom, attrs.get("amount", "0"), attrs.get("owner", ""))
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        results.append({
            "timestamp": timestamp,
            "owner": attrs.get("owner", ""),
            "batch_denom": batch_denom,
            "amount": attrs.get("amount", "0"),
            "jurisdiction": attrs.get("jurisdiction", ""),
        })
    return results


def _scan_txs(api: str, action: str, start_dt, end_dt, batch_prefixes: tuple,
              per_page: int = 100, max_pages: int = 0) -> list[dict]:
    """Scan txs for a given message action, filtering by date and batch prefix."""
    retirements = []
    page = 1
    while True:
        query = urllib.parse.urlencode({
            "query": f"message.action='{action}'",
            "page": str(page),
            "per_page": str(per_page),
            "order_by": "ORDER_BY_DESC",
        })
        url = f"{api}/cosmos/tx/v1beta1/txs?{query}"
        log.info("  [%s] page %d...", action.split(".")[-1], page)

        try:
            data = _get(url)
        except PrunedHistoryError:
            raise
        except Exception as e:
            log.error("Failed to query page %d: %s", page, e)
            break

        tx_responses = data.get("tx_responses", [])
        if not tx_responses:
            break

        past_range = False
        for tx_resp in tx_responses:
            timestamp = tx_resp.get("timestamp", "")
            if not timestamp:
                continue
            try:
                tx_dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue

            if tx_dt < start_dt:
                past_range = True
                break
            if tx_dt > end_dt:
                continue

            retirements.extend(_extract_retirements_from_tx(tx_resp, batch_prefixes))

        if past_range:
            break

        total = int(data.get("total", "0") or "0")
        if total == 0 or page * per_page >= total or len(tx_responses) < per_page:
            break
        if max_pages and page >= max_pages:
            log.info("  Reached max pages (%d), stopping scan", max_pages)
            break
        page += 1

    return retirements


def query_retirements(api_url: str, start_date: str, end_date: str, batch_prefixes: tuple,
                      max_pages: int = 50) -> list[dict]:
    """Query EventRetire transactions from Cosmos tx search.

    Searches multiple message action types since retirements can happen via:
    - MsgRetire (direct retirement)
    - MsgExec (authz — used by group policy accounts like SeaTrees/MBS01)
    - MsgBuyDirect (marketplace auto-retire)

    Uses page-based pagination (Tendermint-style) since offset pagination
    doesn't work reliably on some Cosmos SDK nodes.

    Returns list of retirement records with: timestamp, owner, batch_denom, amount, jurisdiction.
    """
    api = api_url.rstrip("/")
    start_dt = datetime.fromisoformat(f"{start_date}T00:00:00+00:00")
    end_dt = datetime.fromisoformat(f"{end_date}T23:59:59+00:00")

    retirements = []

    # Search across message types that can produce EventRetire events
    actions = [
        "/cosmos.authz.v1beta1.MsgExec",       # Group policy / authz (MBS01 uses this)
        "/regen.ecocredit.v1.MsgRetire",        # Direct retirement
        "/regen.ecocredit.marketplace.v1.MsgBuyDirect",  # Marketplace auto-retire
    ]

    for action in actions:
        log.info("Scanning %s...", action)
        found = _scan_txs(api, action, start_dt, end_dt, batch_prefixes, max_pages=max_pages)
        if found:
            log.info("  Found %d retirements from %s", len(found), action.split(".")[-1])
        retirements.extend(found)
        # SeaTrees MBS01 retirements are *exclusively* executed via authz/MsgExec
        # (group-policy account). After scanning MsgExec, MsgRetire/MsgBuyDirect
        # cannot contain MBS01 retirements — they'd just churn through thousands
        # of unrelated txs (24K+ daily) and push the export past proxy timeouts.
        # Always break, regardless of whether we found anything: an empty result
        # is a real "no retirements in window" answer that should return fast.
        if action == "/cosmos.authz.v1beta1.MsgExec":
            log.info("  Done with MsgExec scan (%d match%s); skipping unrelated MsgRetire/MsgBuyDirect actions",
                     len(found), "" if len(found) == 1 else "es")
            break

    # Deduplicate across action types (same tx could appear in multiple searches)
    seen = set()
    unique = []
    for r in retirements:
        key = (r["timestamp"], r["batch_denom"], r["amount"], r["owner"])
        if key not in seen:
            seen.add(key)
            unique.append(r)

    return unique


def query_retirements_with_fallback(api_url: str, start_date: str, end_date: str,
                                     batch_prefixes: tuple, max_pages: int = 50) -> list[dict]:
    """Try the requested API first, then fall back through FALLBACK_APIS if it returns empty."""
    # Build ordered list: caller's choice first, then fallbacks (skip duplicates)
    apis = [api_url]
    for fb in FALLBACK_APIS:
        if fb.rstrip("/") != api_url.rstrip("/"):
            apis.append(fb)

    last_provider_was_authoritative = False
    for api in apis:
        log.info("Trying %s ...", api)
        try:
            results = query_retirements(api, start_date, end_date, batch_prefixes, max_pages=max_pages)
        except PrunedHistoryError as e:
            log.warning("Provider %s is pruned (%s); trying next provider...", api, e)
            continue
        if results:
            log.info("Got %d retirements from %s", len(results), api)
            return results
        # Empty result from a provider that didn't error is authoritative —
        # no need to keep trying the rest of the chain just to confirm zero.
        log.info("0 retirements from %s in date range (provider responded successfully)", api)
        last_provider_was_authoritative = True
        break

    if not last_provider_was_authoritative:
        log.warning("All providers failed (pruned/errored); cannot determine retirement count")
    return []


# ── Row builder ──────────────────────────────────────────────────────

def build_bloom_row(retirement: dict, metadata: MetadataCache) -> dict:
    """Build a single Bloom spreadsheet row from a retirement record."""
    amount = float(retirement["amount"])
    project = metadata.resolve_project_metadata(retirement["batch_denom"])

    return {
        "date (RETIREMENT)": retirement["timestamp"][:10],  # YYYY-MM-DD
        "purchase_type": "",  # SeaTrees fills (B2B/B2C)
        "purchase_amount": round(amount * project["credit_price"], 2),
        "number_of_credits": amount,
        "credit_price": project["credit_price"],
        "transaction_description": project["transaction_description"],
        "project_name": project["name"],
        "project_developer": project["developer"],
        "project_country": project["country"],
        "project_region": project["region"],
        "credit_scheme": project["credit_scheme"],
        "activity_type": project["activity_type"],
        "avg_price_per_hectare_per_year": project["avg_price_per_hectare_per_year"],
        "credit_size": project["credit_size"],
        "credit_length": project["credit_length"],
        "land_size": round(amount * project["credit_size"], 6),
        # Buyer columns — SeaTrees fills from CRM
        "buyer_name": "",
        "buyer_email": "",
        "buyer_company": "",
        "buyer_country": "",
        "buyer_type": "",
        "buyer_channel": "",
        "buyer_notes": "",
    }


# ── Output writers ───────────────────────────────────────────────────

def write_csv(rows: list[dict], output_path: str):
    """Write rows as CSV."""
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=BLOOM_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"CSV written: {output_path} ({len(rows)} rows)")


def write_xlsx(rows: list[dict], output_path: str):
    """Write rows as XLSX (requires openpyxl)."""
    try:
        from openpyxl import Workbook
    except ImportError:
        print("ERROR: openpyxl not installed. Install with: pip install openpyxl")
        print("Falling back to CSV output.")
        csv_path = output_path.replace(".xlsx", ".csv")
        write_csv(rows, csv_path)
        return

    wb = Workbook()
    ws = wb.active
    ws.title = "Bloom Export"
    ws.append(BLOOM_COLUMNS)
    for row in rows:
        ws.append([row.get(col, "") for col in BLOOM_COLUMNS])
    wb.save(output_path)
    print(f"XLSX written: {output_path} ({len(rows)} rows)")


# ── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SeaTrees Bloom Retirement Export")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--output", default=None, help="Output file path (default: seatrees_{start}_to_{end}.csv)")
    parser.add_argument("--xlsx", action="store_true", help="Output as XLSX instead of CSV")
    parser.add_argument("--api", default=DEFAULT_API, help=f"Regen Ledger REST API URL (default: {DEFAULT_API})")
    parser.add_argument("--batch-prefixes", nargs="+", default=list(MBS01_PREFIXES),
                        help="Batch denom prefixes to filter (default: C04-005- C04-007-)")
    parser.add_argument("--max-pages", type=int, default=50,
                        help="Max pages to scan per message type (default: 50, 0=unlimited)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    # Validate dates
    try:
        datetime.strptime(args.start, "%Y-%m-%d")
        datetime.strptime(args.end, "%Y-%m-%d")
    except ValueError:
        print("ERROR: Dates must be in YYYY-MM-DD format")
        sys.exit(1)

    ext = ".xlsx" if args.xlsx else ".csv"
    output = args.output or f"seatrees_{args.start}_to_{args.end}{ext}"

    print(f"SeaTrees Bloom Retirement Export")
    print(f"Date range: {args.start} to {args.end}")
    print(f"API: {args.api}")
    print(f"Batch prefixes: {args.batch_prefixes}")
    print(f"Output: {output}")
    print("=" * 60)

    # Query retirements (with automatic fallback to other providers)
    print("\nQuerying retirement transactions...")
    retirements = query_retirements_with_fallback(args.api, args.start, args.end,
                                                   tuple(args.batch_prefixes),
                                                   max_pages=args.max_pages)
    print(f"Found {len(retirements)} retirements in date range")

    if not retirements:
        print("No retirements found. Check date range and batch prefixes.")
        sys.exit(0)

    # Build rows with metadata enrichment
    print("\nResolving project metadata...")
    cache = MetadataCache(args.api)
    rows = []
    try:
        for i, ret in enumerate(retirements, 1):
            row = build_bloom_row(ret, cache)
            rows.append(row)
            if i % 10 == 0:
                print(f"  Processed {i}/{len(retirements)} retirements...")
    except ExportRefusedError as e:
        # Refuse the whole export. A partial CSV is indistinguishable from a
        # complete one once it reaches a spreadsheet.
        print(f"\nEXPORT REFUSED: {e.message()}", file=sys.stderr)
        print(f"\n{e.remedy()}", file=sys.stderr)
        print("\nNo file was written.", file=sys.stderr)
        sys.exit(2)

    # Sort by date
    rows.sort(key=lambda r: r["date (RETIREMENT)"])

    # Summary
    total_credits = sum(float(r["number_of_credits"]) for r in rows)
    total_amount = sum(float(r["purchase_amount"]) for r in rows)
    unique_projects = len(set(r["project_name"] for r in rows if r["project_name"]))
    print(f"\nSummary:")
    print(f"  Retirements: {len(rows)}")
    print(f"  Total credits: {total_credits:,.2f}")
    print(f"  Total amount: ${total_amount:,.2f}")
    print(f"  Unique projects: {unique_projects}")

    # Write output
    print("")
    if args.xlsx:
        write_xlsx(rows, output)
    else:
        write_csv(rows, output)


if __name__ == "__main__":
    main()
