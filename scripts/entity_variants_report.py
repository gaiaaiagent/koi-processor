#!/usr/bin/env python3
"""
Entity Variants Report Generator

For ambiguous labels, shows all type variants with:
- URI, type, occurrence count
- Top predicates per variant
- Top connected entities

Usage:
    cd /opt/projects/koi-processor && set -a; source .env; set +a
    python scripts/entity_variants_report.py --label "koi" --out docs/archive/reports/entity_variants_koi.md
    python scripts/entity_variants_report.py --labels "notion,koi,governance" --out docs/archive/reports/entity_variants_combined.md

Author: Claude Code
Date: 2025-12-24
"""

import argparse
import os
import sys
from datetime import datetime
from typing import Dict, List, Any

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("Error: psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)


def get_db_config() -> Dict[str, Any]:
    """Get database configuration from environment."""
    return {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": int(os.getenv("POSTGRES_PORT", 5433)),
        "database": os.getenv("POSTGRES_DB", "eliza"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
    }


def run_query(conn, query: str, params: tuple = None, fetch_all: bool = True) -> Any:
    """Execute a query and return results."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute(query, params)
    if fetch_all:
        return cursor.fetchall()
    return cursor.fetchone()


def get_entity_variants(conn, label: str) -> List[Dict]:
    """Get all type variants for a given label."""
    return run_query(conn, """
        SELECT
            id,
            entity_text,
            entity_type,
            occurrence_count,
            fuseki_uri
        FROM entity_registry
        WHERE LOWER(TRIM(entity_text)) = LOWER(TRIM(%s))
        ORDER BY occurrence_count DESC
    """, (label,))


def get_top_predicates_as_subject(conn, entity_id: int, limit: int = 5) -> List[Dict]:
    """Get top predicates where this entity is the subject."""
    return run_query(conn, """
        SELECT
            predicate,
            COUNT(*) as cnt
        FROM koi_relationships
        WHERE subject_entity_id = %s
        GROUP BY predicate
        ORDER BY cnt DESC
        LIMIT %s
    """, (entity_id, limit))


def get_top_predicates_as_object(conn, entity_id: int, limit: int = 5) -> List[Dict]:
    """Get top predicates where this entity is the object."""
    return run_query(conn, """
        SELECT
            predicate,
            COUNT(*) as cnt
        FROM koi_relationships
        WHERE object_entity_id = %s
        GROUP BY predicate
        ORDER BY cnt DESC
        LIMIT %s
    """, (entity_id, limit))


def get_top_connected_entities(conn, entity_id: int, limit: int = 5) -> List[Dict]:
    """Get top connected entities (either direction)."""
    return run_query(conn, """
        WITH connected AS (
            SELECT object_entity_id as connected_id, predicate
            FROM koi_relationships
            WHERE subject_entity_id = %s
            UNION ALL
            SELECT subject_entity_id as connected_id, predicate
            FROM koi_relationships
            WHERE object_entity_id = %s
        )
        SELECT
            e.entity_text,
            e.entity_type,
            COUNT(*) as connection_count
        FROM connected c
        JOIN entity_registry e ON e.id = c.connected_id
        GROUP BY e.entity_text, e.entity_type
        ORDER BY connection_count DESC
        LIMIT %s
    """, (entity_id, entity_id, limit))


def get_relationship_count(conn, entity_id: int) -> Dict:
    """Get relationship counts for an entity."""
    result = run_query(conn, """
        SELECT
            (SELECT COUNT(*) FROM koi_relationships WHERE subject_entity_id = %s) as as_subject,
            (SELECT COUNT(*) FROM koi_relationships WHERE object_entity_id = %s) as as_object
    """, (entity_id, entity_id), fetch_all=False)
    return result


def format_table(headers: List[str], rows: List[List[Any]]) -> str:
    """Format data as markdown table."""
    if not rows:
        return "*None*"
    lines = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def generate_variant_section(conn, label: str) -> str:
    """Generate report section for a single label."""
    lines = []
    variants = get_entity_variants(conn, label)

    if not variants:
        lines.append(f"### {label}")
        lines.append("")
        lines.append("*No entities found with this label.*")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"### {label}")
    lines.append("")
    lines.append(f"**Variants found:** {len(variants)}")
    lines.append("")

    # Summary table
    summary_rows = []
    total_occ = sum(v["occurrence_count"] for v in variants)
    for v in variants:
        pct = 100 * v["occurrence_count"] / total_occ if total_occ > 0 else 0
        summary_rows.append([
            v["entity_type"],
            f"{v['occurrence_count']:,}",
            f"{pct:.1f}%",
            v["fuseki_uri"][:60] + "..." if len(v["fuseki_uri"]) > 60 else v["fuseki_uri"]
        ])
    lines.append(format_table(["Type", "Occurrences", "%", "URI"], summary_rows))
    lines.append("")

    # Detailed breakdown per variant
    for v in variants:
        entity_id = v["id"]
        lines.append(f"#### {v['entity_type']} (ID: {entity_id})")
        lines.append("")

        # Relationship counts
        rel_counts = get_relationship_count(conn, entity_id)
        total_rels = rel_counts["as_subject"] + rel_counts["as_object"]
        lines.append(f"**Relationships:** {total_rels} total ({rel_counts['as_subject']} as subject, {rel_counts['as_object']} as object)")
        lines.append("")

        # Top predicates as subject
        subj_preds = get_top_predicates_as_subject(conn, entity_id)
        if subj_preds:
            lines.append("**As subject (→):**")
            for p in subj_preds:
                lines.append(f"- `{p['predicate']}` ({p['cnt']})")
        lines.append("")

        # Top predicates as object
        obj_preds = get_top_predicates_as_object(conn, entity_id)
        if obj_preds:
            lines.append("**As object (←):**")
            for p in obj_preds:
                lines.append(f"- `{p['predicate']}` ({p['cnt']})")
        lines.append("")

        # Top connected entities
        connected = get_top_connected_entities(conn, entity_id)
        if connected:
            lines.append("**Top connected entities:**")
            conn_rows = [[c["entity_text"], c["entity_type"], c["connection_count"]] for c in connected]
            lines.append(format_table(["Entity", "Type", "Connections"], conn_rows))
        lines.append("")

    # Classification
    lines.append("**Classification:**")
    if len(variants) == 1:
        lines.append("- Single type, no conflict")
    elif len(variants) == 2:
        types = [v["entity_type"] for v in variants]
        occs = [v["occurrence_count"] for v in variants]
        if min(occs) <= 3 and max(occs) > 50:
            lines.append(f"- **Likely wrong-type noise**: {types[1]} ({occs[1]} occ) appears to be extraction error")
        else:
            lines.append(f"- **Polysemy candidate**: Both {types[0]} and {types[1]} have significant occurrences")
    else:
        types = [v["entity_type"] for v in variants]
        occs = [v["occurrence_count"] for v in variants]
        low_occ_types = [t for t, o in zip(types, occs) if o <= 3]
        if low_occ_types:
            lines.append(f"- **Mixed**: Likely polysemy with some wrong-type noise in: {', '.join(low_occ_types)}")
        else:
            lines.append(f"- **Multi-polysemy**: Entity legitimately appears as multiple types")
    lines.append("")

    return "\n".join(lines)


def generate_report(conn, labels: List[str], cycle_name: str = "2026-01") -> str:
    """Generate the full variants report."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    report = []
    report.append(f"# Entity Variants Report - Cycle {cycle_name}")
    report.append("")
    report.append(f"**Generated:** {now}")
    report.append(f"**Database:** {os.getenv('POSTGRES_DB', 'eliza')}")
    report.append(f"**Labels analyzed:** {len(labels)}")
    report.append("")
    report.append("---")
    report.append("")

    report.append("## Overview")
    report.append("")
    report.append("This report analyzes entity variants (same label, different types) to determine:")
    report.append("- Which are **true polysemy** (legitimate multi-type entities)")
    report.append("- Which are **typing drift** (extraction errors to fix)")
    report.append("")
    report.append("**Labels analyzed:**")
    for label in labels:
        report.append(f"- {label}")
    report.append("")
    report.append("---")
    report.append("")

    report.append("## Variant Analysis")
    report.append("")

    for label in labels:
        report.append(generate_variant_section(conn, label))
        report.append("---")
        report.append("")

    # Summary
    report.append("## Summary & Recommendations")
    report.append("")
    report.append("| Label | Classification | Recommended Action |")
    report.append("| --- | --- | --- |")

    for label in labels:
        variants = get_entity_variants(conn, label)
        if not variants:
            report.append(f"| {label} | Not found | - |")
            continue

        if len(variants) == 1:
            report.append(f"| {label} | Single type | None needed |")
        else:
            types = [v["entity_type"] for v in variants]
            occs = [v["occurrence_count"] for v in variants]
            low_occ = [t for t, o in zip(types, occs) if o <= 3]
            if low_occ and max(occs) > 50:
                report.append(f"| {label} | Wrong-type noise | Remove low-occ types: {', '.join(low_occ)} |")
            elif len(set(types)) > 3:
                report.append(f"| {label} | Mixed polysemy + noise | Review types with <5 occ |")
            else:
                report.append(f"| {label} | True polysemy | Keep all types |")

    report.append("")
    report.append("---")
    report.append("")
    report.append(f"*Report generated by `scripts/entity_variants_report.py`*")

    return "\n".join(report)


def main():
    parser = argparse.ArgumentParser(description="Generate Entity Variants Report")
    parser.add_argument("--label", help="Single label to analyze")
    parser.add_argument("--labels", help="Comma-separated list of labels to analyze")
    parser.add_argument("--out", required=True, help="Output markdown file path")
    parser.add_argument("--cycle", default="2026-01", help="Cycle name (default: 2026-01)")
    args = parser.parse_args()

    if not args.label and not args.labels:
        print("Error: Must specify --label or --labels")
        sys.exit(1)

    labels = []
    if args.label:
        labels.append(args.label)
    if args.labels:
        labels.extend([l.strip() for l in args.labels.split(",")])

    print("Entity Variants Report Generator")
    print("=" * 60)
    print(f"Labels to analyze: {labels}")

    # Connect to database
    db_config = get_db_config()
    print(f"Connecting to {db_config['host']}:{db_config['port']}/{db_config['database']}...")

    try:
        conn = psycopg2.connect(**db_config)
    except Exception as e:
        print(f"Error: Failed to connect to database: {e}")
        sys.exit(1)

    # Generate report
    print("Generating variants report...")
    report = generate_report(conn, labels, args.cycle)

    # Write output
    with open(args.out, "w") as f:
        f.write(report)

    print(f"\nReport written to: {args.out}")
    conn.close()


if __name__ == "__main__":
    main()
