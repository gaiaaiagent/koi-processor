#!/usr/bin/env python3
"""
Knowledge Graph Audit Report Generator

Generates a comprehensive markdown audit report from the production database.
Used for cycle baselines and periodic quality checks.

Usage:
    cd /opt/projects/koi-processor && set -a; source .env; set +a
    python scripts/kg_audit_report.py --out docs/archive/reports/kg_audit_2026_01_day1.md

Author: Claude Code
Date: 2025-12-23
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


def run_query(conn, query: str, fetch_all: bool = True) -> Any:
    """Execute a query and return results."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute(query)
    if fetch_all:
        return cursor.fetchall()
    return cursor.fetchone()


def get_summary_metrics(conn) -> Dict[str, int]:
    """Get high-level summary metrics."""
    metrics = {}

    # Entity count
    result = run_query(conn, "SELECT COUNT(*) as cnt FROM entity_registry", fetch_all=False)
    metrics["entities"] = result["cnt"]

    # Relationship count
    result = run_query(conn, "SELECT COUNT(*) as cnt FROM koi_relationships", fetch_all=False)
    metrics["relationships"] = result["cnt"]

    # Distinct predicates
    result = run_query(conn, "SELECT COUNT(DISTINCT predicate) as cnt FROM koi_relationships", fetch_all=False)
    metrics["predicates"] = result["cnt"]

    return metrics


def get_quality_gates(conn) -> List[Dict]:
    """Check quality gates."""
    gates = []

    # Gate A: No HTTP URIs
    result = run_query(conn, "SELECT COUNT(*) as cnt FROM entity_registry WHERE fuseki_uri LIKE 'http://%'", fetch_all=False)
    gates.append({"gate": "A", "check": "No http:// URIs", "count": result["cnt"], "status": "PASS" if result["cnt"] == 0 else "FAIL"})

    # Gate: No ENTITY type
    result = run_query(conn, "SELECT COUNT(*) as cnt FROM entity_registry WHERE entity_type = 'ENTITY'", fetch_all=False)
    gates.append({"gate": "B", "check": "No generic ENTITY type", "count": result["cnt"], "status": "PASS" if result["cnt"] == 0 else "FAIL"})

    # Gate: No self-referential relationships
    result = run_query(conn, "SELECT COUNT(*) as cnt FROM koi_relationships WHERE subject_entity_id = object_entity_id", fetch_all=False)
    gates.append({"gate": "C", "check": "No self-referential", "count": result["cnt"], "status": "PASS" if result["cnt"] == 0 else "FAIL"})

    # Gate: No HumanActor type
    result = run_query(conn, "SELECT COUNT(*) as cnt FROM entity_registry WHERE entity_type IN ('HumanActor', 'HUMANACTOR')", fetch_all=False)
    gates.append({"gate": "D", "check": "No HumanActor type", "count": result["cnt"], "status": "PASS" if result["cnt"] == 0 else "FAIL"})

    return gates


def get_type_distribution(conn) -> List[Dict]:
    """Get entity type distribution."""
    return run_query(conn, """
        SELECT entity_type, COUNT(*) as cnt
        FROM entity_registry
        GROUP BY entity_type
        ORDER BY cnt DESC
    """)


def get_top_entities(conn, limit: int = 25) -> List[Dict]:
    """Get top entities by occurrence count."""
    return run_query(conn, f"""
        SELECT entity_text, entity_type, occurrence_count
        FROM entity_registry
        ORDER BY occurrence_count DESC
        LIMIT {limit}
    """)


def get_top_predicates(conn, limit: int = 25) -> List[Dict]:
    """Get top predicates by frequency."""
    return run_query(conn, f"""
        SELECT predicate, COUNT(*) as cnt
        FROM koi_relationships
        GROUP BY predicate
        ORDER BY cnt DESC
        LIMIT {limit}
    """)


def get_type_conflicts(conn, limit: int = 25) -> List[Dict]:
    """Get top cross-type conflicts."""
    return run_query(conn, f"""
        WITH normalized AS (
            SELECT
                LOWER(TRIM(entity_text)) as norm_name,
                entity_type,
                SUM(occurrence_count) as total_occ
            FROM entity_registry
            GROUP BY LOWER(TRIM(entity_text)), entity_type
        ),
        conflicts AS (
            SELECT
                norm_name,
                COUNT(DISTINCT entity_type) as type_count,
                SUM(total_occ) as total_occurrences,
                STRING_AGG(entity_type || '(' || total_occ || ')', ', ' ORDER BY total_occ DESC) as types
            FROM normalized
            GROUP BY norm_name
            HAVING COUNT(DISTINCT entity_type) > 1
        )
        SELECT norm_name, type_count, total_occurrences, types
        FROM conflicts
        ORDER BY total_occurrences DESC
        LIMIT {limit}
    """)


def get_duplicate_clusters(conn, limit: int = 20) -> List[Dict]:
    """Get remaining same-type duplicate clusters."""
    return run_query(conn, f"""
        SELECT
            LOWER(TRIM(entity_text)) as norm_name,
            entity_type,
            COUNT(*) as cnt,
            SUM(occurrence_count) as total_occ
        FROM entity_registry
        GROUP BY LOWER(TRIM(entity_text)), entity_type
        HAVING COUNT(*) > 1
        ORDER BY total_occ DESC
        LIMIT {limit}
    """)


def get_single_token_persons(conn, limit: int = 20) -> List[Dict]:
    """Get single-token PERSON entities (ambiguity candidates)."""
    return run_query(conn, f"""
        SELECT entity_text, occurrence_count
        FROM entity_registry
        WHERE entity_type = 'PERSON'
          AND entity_text NOT LIKE '%% %%'
        ORDER BY occurrence_count DESC
        LIMIT {limit}
    """)


def format_table(headers: List[str], rows: List[List[Any]]) -> str:
    """Format data as markdown table."""
    lines = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def generate_report(conn, cycle_name: str = "2026-01") -> str:
    """Generate the full audit report."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Gather all data
    metrics = get_summary_metrics(conn)
    gates = get_quality_gates(conn)
    type_dist = get_type_distribution(conn)
    top_entities = get_top_entities(conn, 25)
    top_predicates = get_top_predicates(conn, 25)
    type_conflicts = get_type_conflicts(conn, 25)
    duplicate_clusters = get_duplicate_clusters(conn, 20)
    single_token_persons = get_single_token_persons(conn, 20)

    # Build report
    report = []
    report.append(f"# Knowledge Graph Audit Report - Cycle {cycle_name}")
    report.append("")
    report.append(f"**Generated:** {now}")
    report.append(f"**Database:** {os.getenv('POSTGRES_DB', 'eliza')}")
    report.append("")
    report.append("---")
    report.append("")

    # Summary Metrics
    report.append("## Summary Metrics")
    report.append("")
    report.append(format_table(
        ["Metric", "Value"],
        [
            ["Entities (entity_registry)", f"{metrics['entities']:,}"],
            ["Relationships (koi_relationships)", f"{metrics['relationships']:,}"],
            ["Distinct Predicates", f"{metrics['predicates']:,}"],
        ]
    ))
    report.append("")

    # Quality Gates
    report.append("## Quality Gates")
    report.append("")
    gate_rows = [[g["gate"], g["check"], g["count"], f"**{g['status']}**"] for g in gates]
    report.append(format_table(["Gate", "Check", "Count", "Status"], gate_rows))
    report.append("")

    # Type Distribution
    report.append("## Entity Type Distribution")
    report.append("")
    type_rows = [[t["entity_type"], f"{t['cnt']:,}"] for t in type_dist]
    report.append(format_table(["Type", "Count"], type_rows))
    report.append("")

    # Top Entities
    report.append("## Top 25 Entities by Occurrence")
    report.append("")
    entity_rows = [[e["entity_text"], e["entity_type"], f"{e['occurrence_count']:,}"] for e in top_entities]
    report.append(format_table(["Entity", "Type", "Occurrences"], entity_rows))
    report.append("")

    # Top Predicates
    report.append("## Top 25 Predicates by Frequency")
    report.append("")
    pred_rows = [[p["predicate"], f"{p['cnt']:,}"] for p in top_predicates]
    report.append(format_table(["Predicate", "Count"], pred_rows))
    report.append("")

    # Type Conflicts
    report.append("## Top 25 Type Conflicts (Cross-Type Collisions)")
    report.append("")
    report.append("Entities with the same normalized name but different types:")
    report.append("")
    conflict_rows = [[c["norm_name"], c["type_count"], f"{c['total_occurrences']:,}", c["types"]] for c in type_conflicts]
    report.append(format_table(["Name", "Type Count", "Total Occurrences", "Types"], conflict_rows))
    report.append("")

    # Duplicate Clusters
    report.append("## Remaining Duplicate Clusters (Same Type)")
    report.append("")
    if duplicate_clusters:
        dup_rows = [[d["norm_name"], d["entity_type"], d["cnt"], f"{d['total_occ']:,}"] for d in duplicate_clusters]
        report.append(format_table(["Name", "Type", "Count", "Total Occurrences"], dup_rows))
    else:
        report.append("**No same-type duplicate clusters remaining.**")
    report.append("")

    # Single-Token Persons
    report.append("## Single-Token PERSON Entities (Ambiguity Tracking)")
    report.append("")
    report.append("First names that may refer to multiple people:")
    report.append("")
    person_rows = [[p["entity_text"], f"{p['occurrence_count']:,}"] for p in single_token_persons]
    report.append(format_table(["Name", "Occurrences"], person_rows))
    report.append("")

    # Footer
    report.append("---")
    report.append("")
    report.append(f"*Report generated by `scripts/kg_audit_report.py`*")

    return "\n".join(report)


def main():
    parser = argparse.ArgumentParser(description="Generate KG Audit Report")
    parser.add_argument("--out", required=True, help="Output markdown file path")
    parser.add_argument("--cycle", default="2026-01", help="Cycle name (default: 2026-01)")
    args = parser.parse_args()

    print("Knowledge Graph Audit Report Generator")
    print("=" * 60)

    # Connect to database
    db_config = get_db_config()
    print(f"Connecting to {db_config['host']}:{db_config['port']}/{db_config['database']}...")

    try:
        conn = psycopg2.connect(**db_config)
    except Exception as e:
        print(f"Error: Failed to connect to database: {e}")
        sys.exit(1)

    # Generate report
    print("Generating report...")
    report = generate_report(conn, args.cycle)

    # Write output
    with open(args.out, "w") as f:
        f.write(report)

    print(f"\nReport written to: {args.out}")
    conn.close()


if __name__ == "__main__":
    main()
