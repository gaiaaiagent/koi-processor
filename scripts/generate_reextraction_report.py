#!/usr/bin/env python3
"""
Generate before/after comparison for the PROMPT_24 re-extraction.

The script queries live database metrics and writes a markdown report that
summarizes reductions for known bad entities and overall entity quality.
"""

import argparse
import os
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

import psycopg2


# Baseline metrics captured before re-extraction (from PROMPT_25 validation)
BEFORE = {
    "knowledge_network_expands": 4985,
    "dry_principles": 917,
    "ecological_credits": 3651,
    "total_entities": 6858,
    "total_mentions": 34194,
}


def connect_db():
    """Create a Postgres connection using environment variables or defaults."""
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5433)),
        database=os.getenv("POSTGRES_DB", "eliza"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )


def scalar_query(conn, sql: str, params=None) -> int:
    """Execute a query expected to return a single numeric value."""
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0


def query_occurrence(conn, pattern: str) -> int:
    """Count total mentions for an entity_text ILIKE pattern."""
    return scalar_query(
        conn,
        "SELECT COALESCE(SUM(occurrence_count), 0) "
        "FROM entity_registry WHERE entity_text ILIKE %s",
        (f"%{pattern}%",),
    )


def query_unique_entities(conn) -> int:
    """Count unique entities in the registry."""
    return scalar_query(conn, "SELECT COUNT(*) FROM entity_registry")


def query_total_mentions(conn) -> int:
    """Sum total mentions across the registry."""
    return scalar_query(conn, "SELECT COALESCE(SUM(occurrence_count), 0) FROM entity_registry")


def fetch_top_entities(conn, limit: int = 20) -> List[Tuple[str, str, int]]:
    """Return top entities by occurrence_count."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT entity_text, entity_type, occurrence_count
            FROM entity_registry
            ORDER BY occurrence_count DESC
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()


def percent_reduction(before: int, after: int) -> float:
    """Compute percentage reduction."""
    if before <= 0:
        return 0.0
    return round((before - after) / before * 100, 2)


def build_report(after: dict, top_entities: List[Tuple[str, str, int]]) -> str:
    """Construct markdown report content."""
    lines = []
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ")

    lines.append("# Re-extraction Quality Report")
    lines.append("")
    lines.append(f"Generated: {timestamp}")
    lines.append("")
    lines.append("## Key Metrics")
    lines.append("")
    lines.append("| Metric | Before | After | Reduction |")
    lines.append("|--------|--------|-------|-----------|")
    lines.append(
        f"| Knowledge network expands... | {BEFORE['knowledge_network_expands']} | "
        f"{after['knowledge_network_expands']} | "
        f"{percent_reduction(BEFORE['knowledge_network_expands'], after['knowledge_network_expands'])}% |"
    )
    lines.append(
        f"| DRY Principles | {BEFORE['dry_principles']} | "
        f"{after['dry_principles']} | "
        f"{percent_reduction(BEFORE['dry_principles'], after['dry_principles'])}% |"
    )
    lines.append(
        f"| Ecological Credits Application | {BEFORE['ecological_credits']} | "
        f"{after['ecological_credits']} | "
        f"{percent_reduction(BEFORE['ecological_credits'], after['ecological_credits'])}% |"
    )
    lines.append(
        f"| Total unique entities | {BEFORE['total_entities']} | "
        f"{after['total_entities']} | "
        f"{percent_reduction(BEFORE['total_entities'], after['total_entities'])}% |"
    )
    lines.append(
        f"| Total mentions | {BEFORE['total_mentions']} | "
        f"{after['total_mentions']} | "
        f"{percent_reduction(BEFORE['total_mentions'], after['total_mentions'])}% |"
    )
    lines.append("")

    lines.append("## Top Entities After Cleanup")
    lines.append("")
    lines.append("| entity_text | entity_type | occurrence_count |")
    lines.append("|-------------|-------------|------------------|")
    for text, etype, count in top_entities:
        safe_text = text.replace("|", "\\|")
        lines.append(f"| {safe_text} | {etype} | {count} |")

    lines.append("")
    lines.append("## Notes")
    lines.append("- Metrics are pulled live from `entity_registry` after re-extraction.")
    lines.append("- Before values are taken from PROMPT_25 validation results.")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate re-extraction quality report.")
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=f"reextraction_report_{datetime.utcnow().strftime('%Y%m%d')}.md",
        help="Output markdown path",
    )
    args = parser.parse_args()

    conn = connect_db()
    try:
        after = {
            "knowledge_network_expands": query_occurrence(conn, "Knowledge network expands"),
            "dry_principles": query_occurrence(conn, "DRY Principles"),
            "ecological_credits": query_occurrence(conn, "Ecological Credits Application"),
            "total_entities": query_unique_entities(conn),
            "total_mentions": query_total_mentions(conn),
        }

        top_entities = fetch_top_entities(conn, limit=20)
        report = build_report(after, top_entities)

        output_path = Path(args.output)
        output_path.write_text(report)
        print(f"Report written to {output_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
