#!/usr/bin/env python3
"""
Knowledge Graph Unexpected Type-Pairs Report Generator

Generates detailed report of type conflicts OUTSIDE the polysemy allowlist.
For each unexpected pair, shows:
- Label count
- Total occurrences
- Top 20 labels with per-type breakdown

Usage:
    cd /opt/projects/koi-processor && set -a; source .env; set +a
    python scripts/kg_audit_unexpected_pairs_report.py --out docs/archive/reports/type_conflict_unexpected_pairs_week6.md

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
# Updated Week 5: 8 pairs in allowlist
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


def get_unexpected_pairs(types: List[str]) -> List[frozenset]:
    """Get all unexpected type pairs from a conflict."""
    unexpected = []
    for i, t1 in enumerate(types):
        for t2 in types[i+1:]:
            pair = frozenset({t1, t2})
            if pair not in EXPECTED_POLYSEMY_PAIRS:
                unexpected.append(pair)
    return unexpected


def is_conflict_unexpected(types: List[str]) -> bool:
    """Check if any type pair in this conflict is unexpected."""
    for i, t1 in enumerate(types):
        for t2 in types[i+1:]:
            pair = frozenset({t1, t2})
            if pair not in EXPECTED_POLYSEMY_PAIRS:
                return True
    return False


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


def pair_to_str(pair: frozenset) -> str:
    """Convert frozenset pair to sorted string."""
    return "↔".join(sorted(pair))


def generate_report(conn, cycle_name: str = "2026-01") -> str:
    """Generate the unexpected pairs report."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Get all conflicts
    conflicts = get_all_type_conflicts(conn)

    # Group conflicts by unexpected pairs
    # Each conflict may contribute to multiple pairs
    pair_data = defaultdict(lambda: {"labels": [], "total_occ": 0})
    unexpected_conflicts = []

    for c in conflicts:
        types = c["type_array"]
        occs = c["occ_array"]

        if not is_conflict_unexpected(types):
            continue

        unexpected_conflicts.append(c)

        # Track this label under each unexpected pair it has
        for pair in get_unexpected_pairs(types):
            pair_str = pair_to_str(pair)
            # Build type breakdown dict for this label
            type_dict = dict(zip(types, occs))
            pair_data[pair_str]["labels"].append({
                "name": c["norm_name"],
                "total_occ": c["total_occurrences"],
                "type_breakdown": type_dict,
                "types": types,
                "occs": occs
            })
            pair_data[pair_str]["total_occ"] += c["total_occurrences"]

    # Build report
    report = []
    report.append(f"# Type Conflict Unexpected Pairs Report - Week 6")
    report.append("")
    report.append(f"**Generated:** {now}")
    report.append(f"**Database:** {os.getenv('POSTGRES_DB', 'eliza')}")
    report.append(f"**Purpose:** Detailed breakdown of conflicts OUTSIDE the polysemy allowlist")
    report.append("")
    report.append("---")
    report.append("")

    # Summary
    report.append("## Summary")
    report.append("")
    total_conflicts = len(conflicts)
    unexpected_count = len(unexpected_conflicts)
    expected_count = total_conflicts - unexpected_count
    report.append(format_table(
        ["Category", "Labels", "Percentage"],
        [
            ["Total type conflicts", f"{total_conflicts:,}", "100%"],
            ["Expected polysemy (allowlist)", f"{expected_count:,}", f"{100*expected_count/total_conflicts:.1f}%"],
            ["**Unexpected conflicts**", f"**{unexpected_count:,}**", f"**{100*unexpected_count/total_conflicts:.1f}%**"],
        ]
    ))
    report.append("")

    # Allowlist reminder
    report.append("## Current Allowlist (8 pairs)")
    report.append("")
    for pair in sorted(EXPECTED_POLYSEMY_PAIRS, key=lambda x: pair_to_str(x)):
        report.append(f"- {pair_to_str(pair)}")
    report.append("")

    # Unexpected pairs ranking
    report.append("## Unexpected Pairs Summary")
    report.append("")
    report.append("Ranked by label count (conflicts contributing to this pair):")
    report.append("")

    sorted_pairs = sorted(
        pair_data.items(),
        key=lambda x: (-len(x[1]["labels"]), -x[1]["total_occ"])
    )

    summary_rows = []
    for pair_str, data in sorted_pairs:
        label_count = len(data["labels"])
        total_occ = data["total_occ"]
        summary_rows.append([pair_str, label_count, f"{total_occ:,}"])

    report.append(format_table(
        ["Type Pair", "Label Count", "Total Occurrences"],
        summary_rows
    ))
    report.append("")

    # Top 3 actionable pairs identification
    report.append("## Top 3 Actionable Pairs")
    report.append("")
    top3 = sorted_pairs[:3]
    total_top3_labels = sum(len(p[1]["labels"]) for p in top3)
    report.append(f"These 3 pairs account for **{total_top3_labels}** labels ({100*total_top3_labels/unexpected_count:.1f}% of unexpected conflicts):")
    report.append("")
    for i, (pair_str, data) in enumerate(top3, 1):
        report.append(f"{i}. **{pair_str}**: {len(data['labels'])} labels, {data['total_occ']:,} occurrences")
    report.append("")

    # Detailed breakdown per pair (top 20 labels each)
    report.append("---")
    report.append("")
    report.append("## Detailed Pair Breakdowns")
    report.append("")
    report.append("For each unexpected pair, showing top 20 labels with per-type occurrence breakdown.")
    report.append("")

    for pair_str, data in sorted_pairs:
        report.append(f"### {pair_str}")
        report.append("")
        report.append(f"**Labels:** {len(data['labels'])} | **Total Occurrences:** {data['total_occ']:,}")
        report.append("")

        # Sort labels by total occurrence
        sorted_labels = sorted(data["labels"], key=lambda x: -x["total_occ"])[:20]

        # Get the two types in this pair
        pair_types = pair_str.split("↔")
        t1, t2 = pair_types[0], pair_types[1]

        label_rows = []
        for label in sorted_labels:
            td = label["type_breakdown"]
            t1_occ = td.get(t1, 0)
            t2_occ = td.get(t2, 0)
            other_types = [f"{t}({o})" for t, o in td.items() if t not in [t1, t2]]
            other_str = ", ".join(other_types) if other_types else "-"

            # Identify majority and minority for analysis
            majority = t1 if t1_occ >= t2_occ else t2
            minority = t2 if t1_occ >= t2_occ else t1
            minority_occ = min(t1_occ, t2_occ)
            majority_occ = max(t1_occ, t2_occ)

            label_rows.append([
                label["name"],
                f"{label['total_occ']:,}",
                f"{t1_occ}",
                f"{t2_occ}",
                other_str
            ])

        report.append(format_table(
            ["Label", "Total", t1, t2, "Other Types"],
            label_rows
        ))
        report.append("")

    # Analysis section for actionable patterns
    report.append("---")
    report.append("")
    report.append("## Analysis: Actionable Wrong-Type Patterns")
    report.append("")
    report.append("Based on the pair breakdowns above, identify patterns where:")
    report.append("- One type is clearly dominant (>90% of occurrences)")
    report.append("- The minority type has very low counts (1-5 occurrences)")
    report.append("- The minority type appears to be extraction noise")
    report.append("")

    # Auto-detect potential wrong-type patterns
    report.append("### Auto-Detected Candidates")
    report.append("")
    report.append("Labels where one type has <3 occurrences and <5% of total (potential noise):")
    report.append("")

    candidates = []
    for pair_str, data in sorted_pairs[:10]:  # Top 10 pairs only
        pair_types = pair_str.split("↔")
        t1, t2 = pair_types[0], pair_types[1]

        for label in data["labels"]:
            td = label["type_breakdown"]
            t1_occ = td.get(t1, 0)
            t2_occ = td.get(t2, 0)
            total = label["total_occ"]

            # Check if one type is noise (< 3 occ AND < 5% of total)
            if t1_occ > 0 and t1_occ <= 3 and t1_occ / total < 0.05:
                candidates.append({
                    "label": label["name"],
                    "wrong_type": t1,
                    "wrong_occ": t1_occ,
                    "correct_type": t2,
                    "correct_occ": t2_occ,
                    "pair": pair_str
                })
            if t2_occ > 0 and t2_occ <= 3 and t2_occ / total < 0.05:
                candidates.append({
                    "label": label["name"],
                    "wrong_type": t2,
                    "wrong_occ": t2_occ,
                    "correct_type": t1,
                    "correct_occ": t1_occ,
                    "pair": pair_str
                })

    if candidates:
        # Dedupe by label+wrong_type
        seen = set()
        unique_candidates = []
        for c in candidates:
            key = (c["label"], c["wrong_type"])
            if key not in seen:
                seen.add(key)
                unique_candidates.append(c)

        # Sort by wrong_type to group similar patterns
        unique_candidates.sort(key=lambda x: (x["wrong_type"], -x["correct_occ"]))

        candidate_rows = [[
            c["label"],
            c["wrong_type"],
            c["wrong_occ"],
            c["correct_type"],
            c["correct_occ"]
        ] for c in unique_candidates[:30]]

        report.append(format_table(
            ["Label", "Wrong Type", "Wrong Occ", "Dominant Type", "Dominant Occ"],
            candidate_rows
        ))
    else:
        report.append("No obvious noise candidates auto-detected.")
    report.append("")

    # Footer
    report.append("---")
    report.append("")
    report.append(f"*Report generated by `scripts/kg_audit_unexpected_pairs_report.py`*")

    return "\n".join(report)


def main():
    parser = argparse.ArgumentParser(description="Generate KG Unexpected Pairs Report")
    parser.add_argument("--out", required=True, help="Output markdown file path")
    parser.add_argument("--cycle", default="2026-01", help="Cycle name (default: 2026-01)")
    args = parser.parse_args()

    print("Knowledge Graph Unexpected Pairs Report Generator")
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
    print("Generating unexpected pairs report...")
    print(f"Allowlist has {len(EXPECTED_POLYSEMY_PAIRS)} pairs")

    report = generate_report(conn, args.cycle)

    # Write output
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        f.write(report)

    print(f"\nReport written to: {args.out}")
    conn.close()


if __name__ == "__main__":
    main()
