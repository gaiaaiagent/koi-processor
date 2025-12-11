#!/usr/bin/env python3
"""
Identify and consolidate entity_registry type mismatches.

Phases
------
1) Reporting: find entity_text values that exist with multiple entity_type values.
2) Consolidation: pick a canonical type, update the keeper row, merge counts, delete variants.

Safeguards
----------
- Creates a pg_dump backup of entity_registry before any mutation (unless --no-backup).
- Dry-run mode available to preview plans without touching the database.
"""

import argparse
import logging
import os
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import psycopg2
from psycopg2.extras import RealDictCursor

from knowledge_graph.improvements.canonical_resolver import CanonicalResolver

# Type normalization mapping borrowed from the YonEarth cleanup scripts
TYPE_NORMALIZATION: Dict[str, str] = {
    # Organizations
    "organization": "ORGANIZATION",
    "org": "ORGANIZATION",
    "company": "ORGANIZATION",
    "foundation": "ORGANIZATION",
    "dao": "ORGANIZATION",
    "network": "ORGANIZATION",
    "organization_type": "ORGANIZATION",

    # People
    "person": "PERSON",
    "individual": "PERSON",
    "human": "PERSON",

    # Projects / products / technology
    "project": "PROJECT",
    "product": "PROJECT",
    "software": "PROJECT",
    "module": "PROJECT",
    "technology": "TECHNOLOGY",
    "website": "TECHNOLOGY",
    "tool": "TECHNOLOGY",

    # Concepts
    "concept": "CONCEPT",
    "topic": "CONCEPT",

    # Events
    "event": "EVENT",

    # Locations
    "location": "LOCATION",
    "place": "LOCATION",
}


def normalize_type(entity_type: str) -> str:
    """Normalize entity type using mapping, falling back to uppercase."""
    if not entity_type:
        return "UNKNOWN"
    return TYPE_NORMALIZATION.get(entity_type, entity_type.upper())


def load_db_config() -> Dict[str, str]:
    """Database configuration from environment with sensible defaults."""
    return {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": int(os.getenv("POSTGRES_PORT", 5433)),
        "database": os.getenv("POSTGRES_DB", "eliza"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
    }


@dataclass
class Variant:
    """Represents one row in entity_registry for a given entity_text/type pair."""

    id: int
    entity_text: str
    entity_type: str
    normalized_text: str
    occurrence_count: int
    fuseki_uri: str


@dataclass
class ResolutionPlan:
    """Planned actions to consolidate one entity_text."""

    entity_text: str
    canonical_type: str
    keeper: Variant
    type_changes: List[Tuple[int, str, str]]  # (id, from_type, to_type)
    merges: List[Tuple[int, int, int, str]]  # (source_id, target_id, count, source_type)


class TypeFixer:
    """Encapsulates reporting and consolidation logic."""

    def __init__(self, db_config: Dict[str, str], dry_run: bool = False, limit: Optional[int] = None):
        self.db_config = db_config
        self.dry_run = dry_run
        self.limit = limit
        self.resolver = CanonicalResolver()

    def _connect(self):
        return psycopg2.connect(**self.db_config)

    # ------------------------------------------------------------------ #
    # Phase 1: Discovery
    # ------------------------------------------------------------------ #
    def fetch_type_collisions(self) -> Dict[str, List[Variant]]:
        """Fetch entities that appear with multiple types."""
        query = """
            SELECT id, entity_text, entity_type, normalized_text, occurrence_count, fuseki_uri
            FROM entity_registry
            WHERE entity_text IN (
                SELECT entity_text
                FROM entity_registry
                GROUP BY entity_text
                HAVING COUNT(DISTINCT entity_type) > 1
            )
            ORDER BY entity_text, occurrence_count DESC
        """

        collisions: Dict[str, List[Variant]] = defaultdict(list)

        with self._connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            for row in cur.fetchall():
                variant = Variant(
                    id=row["id"],
                    entity_text=row["entity_text"],
                    entity_type=row["entity_type"],
                    normalized_text=row["normalized_text"],
                    occurrence_count=row["occurrence_count"],
                    fuseki_uri=row["fuseki_uri"],
                )
                collisions[variant.entity_text].append(variant)

        if self.limit:
            # Trim to first N entities for safety if limit is set
            limited = dict(list(collisions.items())[: self.limit])
            return limited
        return collisions

    @staticmethod
    def render_report(collisions: Dict[str, List[Variant]]) -> str:
        """Create a human-readable report of collision patterns."""
        lines = [
            "# Entity Type Mismatch Report",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Total entities with collisions: {len(collisions)}",
            "",
        ]

        if not collisions:
            lines.append("No type collisions detected.")
            return "\n".join(lines)

        lines.append("Top collisions by total occurrences:")
        summary_rows = []
        for name, variants in collisions.items():
            total = sum(v.occurrence_count for v in variants)
            types = ", ".join(f"{v.entity_type} ({v.occurrence_count})" for v in variants)
            summary_rows.append((total, name, types))

        for total, name, types in sorted(summary_rows, key=lambda x: x[0], reverse=True)[:50]:
            lines.append(f"- {name}: {types} | total={total}")

        lines.append("")
        lines.append("Sample collisions (first 20):")
        for name in list(collisions.keys())[:20]:
            variants = collisions[name]
            variant_str = "; ".join(f"{v.entity_type} x{v.occurrence_count}" for v in variants)
            lines.append(f"- {name}: {variant_str}")

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Planning helpers
    # ------------------------------------------------------------------ #
    def resolve_type(self, entity_text: str, entity_type: str) -> str:
        """Use normalization + CanonicalResolver to pick the best type."""
        normalized_type = normalize_type(entity_type)

        canonical_name, _ = self.resolver.resolve(
            entity_text,
            normalized_type,
            allow_type_mismatch=True,
        )
        canonical_type = self.resolver.get_canonical_type(canonical_name)
        if canonical_type:
            return canonical_type

        return normalized_type

    def build_plan(self, entity_text: str, variants: List[Variant]) -> ResolutionPlan:
        """Produce a deterministic consolidation plan for one entity_text."""
        # Prefer the most common variant type as seed
        sorted_variants = sorted(variants, key=lambda v: v.occurrence_count, reverse=True)
        seed_type = sorted_variants[0].entity_type if sorted_variants else "UNKNOWN"
        canonical_type = self.resolve_type(entity_text, seed_type)

        # Choose keeper: prefer an existing row already with the canonical type
        keeper: Optional[Variant] = None
        canonical_variants = [v for v in variants if normalize_type(v.entity_type) == canonical_type]
        if canonical_variants:
            keeper = sorted(canonical_variants, key=lambda v: v.occurrence_count, reverse=True)[0]
        else:
            keeper = sorted_variants[0]

        type_changes: List[Tuple[int, str, str]] = []
        merges: List[Tuple[int, int, int, str]] = []

        # If keeper is not canonical type, plan to update it (safe because no canonical variant exists)
        if normalize_type(keeper.entity_type) != canonical_type:
            type_changes.append((keeper.id, keeper.entity_type, canonical_type))

        for variant in variants:
            if variant.id == keeper.id:
                continue

            # Merge everything else into keeper and drop the source row
            merges.append((variant.id, keeper.id, variant.occurrence_count, variant.entity_type))

        return ResolutionPlan(
            entity_text=entity_text,
            canonical_type=canonical_type,
            keeper=keeper,
            type_changes=type_changes,
            merges=merges,
        )

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #
    def apply_plan(self, plan: ResolutionPlan) -> Dict[str, int]:
        """Execute the consolidation for one plan."""
        stats = {
            "type_updates": 0,
            "merged_variants": 0,
            "occurrences_transferred": 0,
        }

        with self._connect() as conn, conn.cursor() as cur:
            # Allow caller to wrap this in dry-run
            if not self.dry_run:
                conn.autocommit = False

            keeper_id = plan.keeper.id

            # If a canonical row already exists for the normalized_text + canonical_type,
            # merge everything into that row instead of updating and risking a uniqueness clash.
            if plan.type_changes:
                cur.execute(
                    """
                    SELECT id FROM entity_registry
                    WHERE normalized_text = %s AND entity_type = %s
                    LIMIT 1
                    """,
                    (plan.keeper.normalized_text, plan.canonical_type),
                )
                existing = cur.fetchone()

                if existing and existing[0] != keeper_id:
                    target_keeper_id = existing[0]
                    msg = (
                        f"Found existing canonical row id={target_keeper_id} for "
                        f"{plan.entity_text}; redirecting merges."
                    )
                    logging.info(msg)

                    # Redirect all merges to the existing canonical row
                    plan.merges = [
                        (source_id, target_keeper_id, count, source_type)
                        for source_id, _, count, source_type in plan.merges
                    ]
                    # Merge the original keeper into the canonical row as well
                    plan.merges.append(
                        (keeper_id, target_keeper_id, plan.keeper.occurrence_count, plan.keeper.entity_type)
                    )
                    plan.type_changes = []
                    keeper_id = target_keeper_id

                # No conflicting canonical row found; safe to update keeper type
                for entity_id, from_type, to_type in plan.type_changes:
                    if self.dry_run:
                        logging.info(f"[DRY-RUN] Would update id={entity_id} type {from_type} -> {to_type}")
                    else:
                        cur.execute(
                            "UPDATE entity_registry SET entity_type = %s WHERE id = %s",
                            (to_type, entity_id),
                        )
                    stats["type_updates"] += 1

            # Merge other variants into keeper
            for source_id, target_id, count, source_type in plan.merges:
                if self.dry_run:
                    logging.info(
                        f"[DRY-RUN] Would merge id={source_id} ({source_type}, +{count}) into id={target_id}"
                    )
                else:
                    # Add occurrence_count to keeper
                    cur.execute(
                        "UPDATE entity_registry SET occurrence_count = occurrence_count + %s WHERE id = %s",
                        (count, target_id),
                    )
                    # Remove the source row
                    cur.execute("DELETE FROM entity_registry WHERE id = %s", (source_id,))

                stats["merged_variants"] += 1
                stats["occurrences_transferred"] += count

            if not self.dry_run:
                conn.commit()

        return stats


def backup_entity_registry(db_config: Dict[str, str], output_dir: Path) -> Path:
    """Create a pg_dump of entity_registry."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = output_dir / f"entity_registry_backup_{timestamp}.sql"

    env = os.environ.copy()
    env["PGPASSWORD"] = db_config["password"]

    command = [
        "pg_dump",
        "-h",
        db_config["host"],
        "-p",
        str(db_config["port"]),
        "-U",
        db_config["user"],
        "-d",
        db_config["database"],
        "-t",
        "entity_registry",
        "-f",
        str(backup_path),
    ]

    logging.info(f"Creating backup: {backup_path}")
    subprocess.run(command, check=True, env=env)
    return backup_path


def write_report(content: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    logging.info(f"Report written to {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fix type mismatches in entity_registry.")
    parser.add_argument("--dry-run", action="store_true", help="Plan and log changes without mutating the DB.")
    parser.add_argument("--limit", type=int, help="Limit number of entity_text groups to process.")
    parser.add_argument(
        "--report-path",
        type=Path,
        default=Path("entity_type_mismatch_report.txt"),
        help="Where to write the mismatch report.",
    )
    parser.add_argument(
        "--execution-report",
        type=Path,
        default=Path("type_fix_execution_report.txt"),
        help="Where to write the consolidation summary.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip automatic pg_dump backup (not recommended).",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env")

    db_config = load_db_config()
    fixer = TypeFixer(db_config=db_config, dry_run=args.dry_run, limit=args.limit)

    # Phase 1: discovery
    collisions = fixer.fetch_type_collisions()
    report = fixer.render_report(collisions)
    write_report(report, args.report_path)

    if not collisions:
        logging.info("No collisions found. Exiting.")
        return

    if args.dry_run:
        logging.info("Dry-run enabled; skipping backup and consolidation.")
        return

    # Backup before mutation
    if not args.no_backup:
        backup_dir = project_root / ".local-backup"
        backup_entity_registry(db_config, backup_dir)
    else:
        logging.warning("Skipping backup per --no-backup flag.")

    # Phase 2: consolidation
    summary_lines = [
        "# Type Fix Execution Report",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Entities with collisions: {len(collisions)}",
        "",
        "## Stats",
    ]

    aggregate = {
        "type_updates": 0,
        "merged_variants": 0,
        "occurrences_transferred": 0,
    }

    for idx, (entity_text, variants) in enumerate(collisions.items(), start=1):
        plan = fixer.build_plan(entity_text, variants)
        stats = fixer.apply_plan(plan)

        aggregate["type_updates"] += stats["type_updates"]
        aggregate["merged_variants"] += stats["merged_variants"]
        aggregate["occurrences_transferred"] += stats["occurrences_transferred"]

        logging.info(
            f"[{idx}/{len(collisions)}] {entity_text}: "
            f"type_updates={stats['type_updates']}, merged={stats['merged_variants']}"
        )

    summary_lines.append(f"- Type updates: {aggregate['type_updates']}")
    summary_lines.append(f"- Variants merged: {aggregate['merged_variants']}")
    summary_lines.append(f"- Occurrences transferred: {aggregate['occurrences_transferred']}")

    write_report("\n".join(summary_lines), args.execution_report)


if __name__ == "__main__":
    main()
