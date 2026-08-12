"""SeaTrees Bloom Retirement Export API endpoint.

Exposes the Bloom-compatible CSV/JSON export as an HTTP endpoint
so SeaTrees/Casson can download retirement data directly.
"""

import asyncio
import csv
import io
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

from scripts.seatrees_bloom_export import (
    BLOOM_COLUMNS,
    DEFAULT_API,
    MBS01_PREFIXES,
    MetadataCache,
    ExportRefusedError,
    build_bloom_row,
    query_retirements_with_fallback,
)

log = logging.getLogger(__name__)


def _build_export(start: str, end: str, max_pages: int, api_url: str) -> list[dict]:
    """Run the full blocking export pipeline in a worker thread."""
    retirements = query_retirements_with_fallback(api_url, start, end, tuple(MBS01_PREFIXES),
                                                   max_pages=max_pages)
    if not retirements:
        return []

    cache = MetadataCache(api_url)
    return [build_bloom_row(r, cache) for r in retirements]


def _rows_to_csv(rows: list[dict]) -> str:
    """Serialize rows to CSV string."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=BLOOM_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def create_router(pool, caps=None):
    """Return an APIRouter for SeaTrees export endpoints."""
    router = APIRouter(prefix="/seatrees", tags=["seatrees"])

    @router.get("/bloom-export")
    async def bloom_export(
        start: str = Query(..., description="Start date (YYYY-MM-DD)"),
        end: str = Query(..., description="End date (YYYY-MM-DD)"),
        format: str = Query("csv", description="Output format: csv or json"),
        max_pages: int = Query(50, description="Max pages to scan per message type"),
        api_url: str = Query(DEFAULT_API, description="Regen Ledger REST API URL"),
    ):
        """Export SeaTrees MBS01 retirements in Bloom-compatible format."""
        # Validate dates
        for label, val in [("start", start), ("end", end)]:
            try:
                datetime.strptime(val, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(400, f"Invalid {label} date: {val}. Expected YYYY-MM-DD.")

        if format not in ("csv", "json"):
            raise HTTPException(400, f"Invalid format: {format}. Expected csv or json.")

        # Offload entire blocking pipeline to thread pool
        log.info("SeaTrees export: %s to %s (max_pages=%d)", start, end, max_pages)
        try:
            rows = await asyncio.to_thread(_build_export, start, end, max_pages, api_url)
        except ExportRefusedError as e:
            # Refuse the whole export rather than serve a CSV in which some rows
            # silently carry another product's price. 409: the request is valid,
            # the server state cannot satisfy it yet.
            log.error("SeaTrees export refused: %s", e.message())
            raise HTTPException(409, detail={
                "error": "export_refused",
                "reason": e.message(),
                "project_id": e.project_id,
                "batch_denom": e.batch_denom,
                "missing_fields": list(e.missing),
                "remedy": e.remedy(),
                "note": (
                    "No partial CSV is emitted. Every missing field is "
                    "product-specific, so a default would be another product's value."
                ),
            })
        log.info("SeaTrees export: %d rows generated", len(rows))

        if format == "json":
            return JSONResponse({
                "start": start,
                "end": end,
                "count": len(rows),
                "columns": BLOOM_COLUMNS,
                "rows": rows,
            })

        # CSV response as file download
        csv_content = _rows_to_csv(rows)
        filename = f"seatrees_{start}_to_{end}.csv"
        return StreamingResponse(
            iter([csv_content]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    return router
