#!/usr/bin/env python3
"""
Knowledge Graph Polysemy Split Report Generator

Separates type conflicts into:
- Expected polysemy pairs (allowlist)
- Unexpected/actionable conflicts

Usage:
    cd /opt/projects/koi-processor && set -a; source .env; set +a
    python scripts/kg_audit_polysemy_report.py --out docs/archive/reports/kg_audit_2026_01_polysemy_split.md

Author: Claude Code
Date: 2025-12-24
"""

import argparse
import os
import sys
from datetime import datetime
from typing import Dict, List, Any, Set, Tuple
from collections import defaultdict

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("Error: psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)


# Expected polysemy pairs - these are legitimate multi-type entities
# Updated Week 5: Added ORGANIZATION↔TECHNOLOGY, CONCEPT↔STANDARD, STANDARD↔TECHNOLOGY
EXPECTED_POLYSEMY_PAIRS: Set[frozenset] = {
    frozenset({"CONCEPT", "TECHNOLOGY"}),
    frozenset({"CONCEPT", "PROCESS"}),
    frozenset({"PROJECT", "TECHNOLOGY"}),
    frozenset({"CONCEPT", "PROJECT"}),
    frozenset({"ORGANIZATION", "PROJECT"}),
    frozenset({"ORGANIZATION", "TECHNOLOGY"}),  # Platform companies (Notion, Discord, etc.)
    frozenset({"CONCEPT", "STANDARD"}),         # Standards are conceptual (RDF, SPARQL, etc.)
    frozenset({"STANDARD", "TECHNOLOGY"}),      # Tech standards (OAuth, JSON-LD, etc.)
}


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


def get_all_type_conflicts(conn) -> List[Dict]:
    """Get all cross-type conflicts with full type breakdown."""
    return run_query(conn, """
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
                ARRAY_AGG(entity_type ORDER BY total_occ DESC) as type_array,
                ARRAY_AGG(total_occ ORDER BY total_occ DESC) as occ_array
            FROM normalized
            GROUP BY norm_name
            HAVING COUNT(DISTINCT entity_type) > 1
        )
        SELECT norm_name, type_count, total_occurrences, type_array, occ_array
        FROM conflicts
        ORDER BY total_occurrences DESC
    """)


def classify_conflict(types: List[str]) -> Tuple[bool, Set[frozenset]]:
    """
    Classify a conflict as expected polysemy or unexpected.

    Returns (is_expected, matching_pairs)
    """
    type_set = set(types)
    matching_pairs = set()

    # Check all pairs of types in this conflict
    for i, t1 in enumerate(types):
        for t2 in types[i+1:]:
            pair = frozenset({t1, t2})
            if pair in EXPECTED_POLYSEMY_PAIRS:
                matching_pairs.add(pair)

    # It's only "expected" if ALL type pairs are in the allowlist
    all_pairs_expected = True
    for i, t1 in enumerate(types):
        for t2 in types[i+1:]:
            pair = frozenset({t1, t2})
            if pair not in EXPECTED_POLYSEMY_PAIRS:
                all_pairs_expected = False
                break
        if not all_pairs_expected:
            break

    return all_pairs_expected, matching_pairs


def get_unexpected_pairs(types: List[str]) -> List[frozenset]:
    """Get all unexpected type pairs from a conflict."""
    unexpected = []
    for i, t1 in enumerate(types):
        for t2 in types[i+1:]:
            pair = frozenset({t1, t2})
            if pair not in EXPECTED_POLYSEMY_PAIRS:
                unexpected.append(pair)
    return unexpected


def format_table(headers: List[str], rows: List[List[Any]]) -> str:
    """Format data as markdown table."""
    lines = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def format_type_breakdown(types: List[str], occs: List[int]) -> str:
    """Format types with occurrence counts."""
    parts = [f"{t}({o})" for t, o in zip(types, occs)]
    return ", ".join(parts)


def generate_report(conn, cycle_name: str = "2026-01") -> str:
    """Generate the polysemy split report."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Get all conflicts
    conflicts = get_all_type_conflicts(conn)

    # Classify each conflict
    expected_polysemy = []
    unexpected_conflicts = []
    unexpected_pair_counts = defaultdict(int)
    unexpected_pair_occs = defaultdict(int)

    for c in conflicts:
        types = c["type_array"]
        occs = c["occ_array"]
        is_expected, _ = classify_conflict(types)

        if is_expected:
            expected_polysemy.append(c)
        else:
            unexpected_conflicts.append(c)
            # Track which pairs are unexpected
            for pair in get_unexpected_pairs(types):
                pair_str = "↔".join(sorted(pair))
                unexpected_pair_counts[pair_str] += 1
                unexpected_pair_occs[pair_str] += c["total_occurrences"]

    # Build report
    report = []
    report.append(f"# Knowledge Graph Polysemy Split Report - Cycle {cycle_name}")
    report.append("")
    report.append(f"**Generated:** {now}")
    report.append(f"**Database:** {os.getenv('POSTGRES_DB', 'eliza')}")
    report.append("")
    report.append("---")
    report.append("")

    # Summary
    report.append("## Summary")
    report.append("")
    total = len(conflicts)
    expected_count = len(expected_polysemy)
    unexpected_count = len(unexpected_conflicts)
    report.append(format_table(
        ["Category", "Labels", "Percentage"],
        [
            ["Total type conflicts", f"{total:,}", "100%"],
            ["Expected polysemy (allowlist)", f"{expected_count:,}", f"{100*expected_count/total:.1f}%"],
            ["**Unexpected conflicts (actionable)**", f"**{unexpected_count:,}**", f"**{100*unexpected_count/total:.1f}%**"],
        ]
    ))
    report.append("")

    # Allowlist definition
    report.append("## Expected Polysemy Pairs (Allowlist)")
    report.append("")
    report.append("These type pairs represent legitimate multi-type entities:")
    report.append("")
    for pair in sorted(EXPECTED_POLYSEMY_PAIRS, key=lambda x: "↔".join(sorted(x))):
        pair_str = "↔".join(sorted(pair))
        report.append(f"- **{pair_str}**")
    report.append("")
    report.append("Conflicts where ALL type pairs are in this allowlist are classified as expected polysemy.")
    report.append("")

    # Unexpected pair distribution
    report.append("## Unexpected Pair Distribution")
    report.append("")
    report.append("Type pairs NOT in the allowlist (sorted by label count):")
    report.append("")
    sorted_pairs = sorted(unexpected_pair_counts.items(), key=lambda x: -x[1])[:20]
    pair_rows = [[pair, count, f"{unexpected_pair_occs[pair]:,}"] for pair, count in sorted_pairs]
    report.append(format_table(["Type Pair", "Labels", "Total Occurrences"], pair_rows))
    report.append("")

    # Top 20 unexpected conflicts
    report.append("## Top 20 Unexpected Conflicts (Actionable)")
    report.append("")
    report.append("These are the highest-occurrence conflicts with at least one unexpected type pair:")
    report.append("")
    top_unexpected = unexpected_conflicts[:20]
    unexpected_rows = []
    for c in top_unexpected:
        types = c["type_array"]
        occs = c["occ_array"]
        unexpected_pairs = get_unexpected_pairs(types)
        pairs_str = ", ".join("↔".join(sorted(p)) for p in unexpected_pairs)
        type_breakdown = format_type_breakdown(types, occs)
        unexpected_rows.append([
            c["norm_name"],
            f"{c['total_occurrences']:,}",
            type_breakdown,
            pairs_str
        ])
    report.append(format_table(
        ["Label", "Total Occ", "Types", "Unexpected Pairs"],
        unexpected_rows
    ))
    report.append("")

    # Per-type occurrence breakdown for unexpected bucket
    report.append("## Type Distribution in Unexpected Conflicts")
    report.append("")
    type_in_unexpected = defaultdict(int)
    type_occ_in_unexpected = defaultdict(int)
    for c in unexpected_conflicts:
        for t, o in zip(c["type_array"], c["occ_array"]):
            type_in_unexpected[t] += 1
            type_occ_in_unexpected[t] += o

    sorted_types = sorted(type_in_unexpected.items(), key=lambda x: -x[1])
    type_rows = [[t, count, f"{type_occ_in_unexpected[t]:,}"] for t, count in sorted_types]
    report.append(format_table(["Type", "Labels", "Total Occurrences"], type_rows))
    report.append("")

    # Expected polysemy sample
    report.append("## Sample Expected Polysemy (Top 10)")
    report.append("")
    report.append("For reference, these high-occurrence conflicts are classified as expected polysemy:")
    report.append("")
    top_expected = expected_polysemy[:10]
    expected_rows = []
    for c in top_expected:
        types = c["type_array"]
        occs = c["occ_array"]
        type_breakdown = format_type_breakdown(types, occs)
        expected_rows.append([
            c["norm_name"],
            f"{c['total_occurrences']:,}",
            type_breakdown
        ])
    report.append(format_table(
        ["Label", "Total Occ", "Types"],
        expected_rows
    ))
    report.append("")

    # Recommendations
    report.append("## Recommendations")
    report.append("")
    report.append("### Priority Actions")
    report.append("")
    report.append("1. **Review top unexpected conflicts** - Determine if they are:")
    report.append("   - True extraction errors (fix/remove wrong type)")
    report.append("   - Missing from allowlist (add pair if legitimate)")
    report.append("")
    report.append("2. **Expand allowlist if needed** - Consider adding:")
    if "ORGANIZATION↔TECHNOLOGY" in unexpected_pair_counts:
        report.append("   - `ORGANIZATION↔TECHNOLOGY` (platforms that are also companies)")
    if "CONCEPT↔STANDARD" in unexpected_pair_counts:
        report.append("   - `CONCEPT↔STANDARD` (standards that are also concepts)")
    if "STANDARD↔TECHNOLOGY" in unexpected_pair_counts:
        report.append("   - `STANDARD↔TECHNOLOGY` (tech standards)")
    report.append("")
    report.append("3. **Target remaining wrong-type noise** - Low-occurrence unexpected types")
    report.append("")

    # Footer
    report.append("---")
    report.append("")
    report.append(f"*Report generated by `scripts/kg_audit_polysemy_report.py`*")

    return "\n".join(report)


def main():
    parser = argparse.ArgumentParser(description="Generate KG Polysemy Split Report")
    parser.add_argument("--out", required=True, help="Output markdown file path")
    parser.add_argument("--cycle", default="2026-01", help="Cycle name (default: 2026-01)")
    args = parser.parse_args()

    print("Knowledge Graph Polysemy Split Report Generator")
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
    print("Generating polysemy split report...")
    print(f"Allowlist pairs: {len(EXPECTED_POLYSEMY_PAIRS)}")
    for pair in sorted(EXPECTED_POLYSEMY_PAIRS, key=lambda x: "↔".join(sorted(x))):
        print(f"  - {'↔'.join(sorted(pair))}")

    report = generate_report(conn, args.cycle)

    # Write output
    with open(args.out, "w") as f:
        f.write(report)

    print(f"\nReport written to: {args.out}")
    conn.close()


if __name__ == "__main__":
    main()
