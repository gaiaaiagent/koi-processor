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

DEFAULT_API = "https://regen-api.polkachu.com"
# MBS01 batch denom prefixes (SeaTrees / Seatrees+ Biodiversity Blocks)
# Only known batch: MBS01-001-20240601-20340531-001
MBS01_PREFIXES = ("MBS01-",)

# Static column values per Bloom template
STATIC = {
    "credit_price": 3,
    "transaction_description": "Purchase of Seatrees+ Biodiversity Blocks",
    "credit_scheme": "Seatrees+ Biodiversity Blocks",
    "activity_type": "Uplift, Stewardship",
    "avg_price_per_hectare_per_year": 3000,
    "credit_size": 0.0001,
    "credit_length": 10,
}

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
}

# Known MBS01 project metadata (fallback when on-chain metadata IRI can't be resolved)
# The regen: IRI content-addressable metadata requires a data server that isn't publicly accessible.
KNOWN_PROJECTS = {
    "MBS01-001": {
        "name": "Mikoko Pamoja and Vanga Blue Forest",
        "developer": "COBEC / KMFRI",
    },
}

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

def _get(url: str, params: dict = None) -> dict:
    """GET request returning parsed JSON."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "seatrees-bloom-export/1.0")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:500]
        raise RuntimeError(f"HTTP {e.code} from {url}: {body}") from e


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
        """Resolve batch → project → metadata. Returns dict with name, developer, country, region."""
        batch = self.get_batch_info(batch_denom)
        project_id = batch.get("project_id", "")
        if not project_id:
            return {"name": "", "developer": "", "country": "", "region": ""}

        project = self.get_project_info(project_id)
        jurisdiction = project.get("jurisdiction", "")

        # Parse jurisdiction for country
        country_code = jurisdiction.split("-")[0] if jurisdiction else ""
        country = COUNTRY_CODES.get(country_code, country_code)
        region = REGION_MAP.get(country, "")

        # Try known project metadata first (regen: IRIs aren't publicly resolvable)
        known = KNOWN_PROJECTS.get(project_id, {})
        name = known.get("name", "")
        developer = known.get("developer", "")

        # If not in known list, try to resolve on-chain metadata IRI
        if not name:
            metadata_uri = project.get("metadata", "")
            if metadata_uri and not metadata_uri.startswith("regen:"):
                try:
                    meta = _get(metadata_uri)
                    name = (
                        meta.get("schema:name", "")
                        or meta.get("name", "")
                        or meta.get("regen:projectName", "")
                    )
                    developer = (
                        meta.get("regen:projectDeveloper", {}).get("schema:name", "")
                        if isinstance(meta.get("regen:projectDeveloper"), dict)
                        else meta.get("regen:projectDeveloper", "")
                    ) or developer
                except Exception as e:
                    log.warning("Failed to fetch metadata for project %s: %s", project_id, e)

        # Fallback for developer — use admin address
        if not developer:
            admin = project.get("admin", "")
            if admin:
                log.warning("Using admin address as developer for project %s: %s", project_id, admin)
                developer = admin

        return {"name": name, "developer": developer, "country": country, "region": region}


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

    # Deduplicate across action types (same tx could appear in multiple searches)
    seen = set()
    unique = []
    for r in retirements:
        key = (r["timestamp"], r["batch_denom"], r["amount"], r["owner"])
        if key not in seen:
            seen.add(key)
            unique.append(r)

    return unique


# ── Row builder ──────────────────────────────────────────────────────

def build_bloom_row(retirement: dict, metadata: MetadataCache) -> dict:
    """Build a single Bloom spreadsheet row from a retirement record."""
    amount = float(retirement["amount"])
    project = metadata.resolve_project_metadata(retirement["batch_denom"])

    return {
        "date (RETIREMENT)": retirement["timestamp"][:10],  # YYYY-MM-DD
        "purchase_type": "",  # SeaTrees fills (B2B/B2C)
        "purchase_amount": round(amount * STATIC["credit_price"], 2),
        "number_of_credits": amount,
        "credit_price": STATIC["credit_price"],
        "transaction_description": STATIC["transaction_description"],
        "project_name": project["name"],
        "project_developer": project["developer"],
        "project_country": project["country"],
        "project_region": project["region"],
        "credit_scheme": STATIC["credit_scheme"],
        "activity_type": STATIC["activity_type"],
        "avg_price_per_hectare_per_year": STATIC["avg_price_per_hectare_per_year"],
        "credit_size": STATIC["credit_size"],
        "credit_length": STATIC["credit_length"],
        "land_size": round(amount * STATIC["credit_size"], 6),
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

    # Query retirements
    print("\nQuerying retirement transactions...")
    retirements = query_retirements(args.api, args.start, args.end, tuple(args.batch_prefixes),
                                    max_pages=args.max_pages)
    print(f"Found {len(retirements)} retirements in date range")

    if not retirements:
        print("No retirements found. Check date range and batch prefixes.")
        sys.exit(0)

    # Build rows with metadata enrichment
    print("\nResolving project metadata...")
    cache = MetadataCache(args.api)
    rows = []
    for i, ret in enumerate(retirements, 1):
        row = build_bloom_row(ret, cache)
        rows.append(row)
        if i % 10 == 0:
            print(f"  Processed {i}/{len(retirements)} retirements...")

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
