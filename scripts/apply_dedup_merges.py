#!/usr/bin/env python3
"""
Apply FIX-006 merge proposals to PostgreSQL (transactional).

Designed to apply the "safe subset" of merges produced by `scripts/dedup_dry_run.py`:
  - tier1_normalized
  - tier1_5_canonical

This script updates:
  - entity_registry (merge occurrence_count + timestamps, then delete loser row)
  - koi_relationships (rewrites subject/object entity IDs; merges duplicates)
  - koi_entity_chunk_links (rewrites entity_uri)

It also records applied merges in `dedup_merge_plan` for audit/rollback planning.

Usage:
  cd koi-processor
  set -a; source .env; set +a

  # Dry run (default)
  python scripts/apply_dedup_merges.py --in merges_post_deploy.csv

  # Apply only safe methods
  python scripts/apply_dedup_merges.py --in merges_post_deploy.csv --apply

  # Explicit methods (comma-separated)
  python scripts/apply_dedup_merges.py --in merges_post_deploy.csv --apply --methods tier1_normalized,tier1_5_canonical

Safeguards:
  - By default does not mutate the DB (dry-run).
  - Creates a pg_dump backup of affected tables unless --no-backup is set.
  - Enforces same-type merges and skips type_conflict/tier1x_fuzzy by default.
"""

import argparse
import csv
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import psycopg2
from psycopg2.extras import RealDictCursor


LOGGER = logging.getLogger("apply_dedup_merges")


DEFAULT_ALLOWED_METHODS = ("tier1_normalized", "tier1_5_canonical")


@dataclass(frozen=True)
class MergeRow:
    winner_uri: str
    winner_type: str
    loser_uri: str
    loser_type: str
    score: float
    method: str
    reason: str


def load_db_config() -> Dict[str, str]:
    return {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": int(os.getenv("POSTGRES_PORT", 5433)),
        "database": os.getenv("POSTGRES_DB", "eliza"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
    }


def connect(db_config: Dict[str, str]):
    return psycopg2.connect(**db_config)


def parse_merges_csv(path: Path) -> List[MergeRow]:
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        required = {
            "winner_uri",
            "winner_type",
            "loser_uri",
            "loser_type",
            "score",
            "method",
            "reason",
        }
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV missing required columns: {sorted(missing)}")

        rows: List[MergeRow] = []
        for r in reader:
            rows.append(
                MergeRow(
                    winner_uri=(r["winner_uri"] or "").strip(),
                    winner_type=(r["winner_type"] or "").strip().upper(),
                    loser_uri=(r["loser_uri"] or "").strip(),
                    loser_type=(r["loser_type"] or "").strip().upper(),
                    score=float(r["score"] or 0.0),
                    method=(r["method"] or "").strip(),
                    reason=(r["reason"] or "").strip(),
                )
            )
    return rows


def build_final_mapping(rows: Iterable[MergeRow]) -> Dict[str, MergeRow]:
    """
    Build a loser_uri -> MergeRow mapping, resolving:
      - multiple proposals for same loser (keeps highest score)
      - transitive chains (A<-B, B<-C) so loser maps to final winner
    """
    by_loser: Dict[str, MergeRow] = {}
    for row in rows:
        if not row.loser_uri or not row.winner_uri:
            continue
        existing = by_loser.get(row.loser_uri)
        if existing is None or row.score > existing.score:
            by_loser[row.loser_uri] = row

    def resolve_winner_uri(uri: str, seen: Optional[Set[str]] = None) -> str:
        seen = seen or set()
        if uri in seen:
            raise ValueError(f"Cycle detected in merge mapping at {uri}")
        seen.add(uri)
        nxt = by_loser.get(uri)
        if not nxt:
            return uri
        return resolve_winner_uri(nxt.winner_uri, seen)

    resolved: Dict[str, MergeRow] = {}
    for loser_uri, row in by_loser.items():
        final_winner = resolve_winner_uri(row.winner_uri)
        if final_winner == loser_uri:
            raise ValueError(f"Self-merge detected for {loser_uri}")
        resolved[loser_uri] = MergeRow(
            winner_uri=final_winner,
            winner_type=row.winner_type,
            loser_uri=row.loser_uri,
            loser_type=row.loser_type,
            score=row.score,
            method=row.method,
            reason=row.reason,
        )
    return resolved


def backup_tables(db_config: Dict[str, str], output_dir: Path, tables: List[str]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = output_dir / f"fix006_merge_backup_{timestamp}.sql"

    env = os.environ.copy()
    env["PGPASSWORD"] = db_config["password"]

    cmd = [
        "pg_dump",
        "-h",
        db_config["host"],
        "-p",
        str(db_config["port"]),
        "-U",
        db_config["user"],
        "-d",
        db_config["database"],
    ]
    for t in tables:
        cmd.extend(["-t", t])
    cmd.extend(["-f", str(backup_path)])

    LOGGER.info("Creating backup: %s", backup_path)
    subprocess.run(cmd, check=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return backup_path


def ensure_merge_plan_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS dedup_merge_plan (
          id SERIAL PRIMARY KEY,
          winner_uri TEXT NOT NULL,
          loser_uri TEXT NOT NULL,
          winner_type TEXT,
          loser_type TEXT,
          method TEXT,
          reason TEXT,
          score FLOAT,
          applied BOOLEAN DEFAULT FALSE,
          applied_at TIMESTAMP,
          notes TEXT
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS dedup_merge_plan_applied_idx ON dedup_merge_plan (applied, applied_at);")
    cur.execute("CREATE INDEX IF NOT EXISTS dedup_merge_plan_loser_idx ON dedup_merge_plan (loser_uri);")
    cur.execute("CREATE INDEX IF NOT EXISTS dedup_merge_plan_winner_idx ON dedup_merge_plan (winner_uri);")


def table_exists(cur, table_name: str) -> bool:
    cur.execute(
        "SELECT to_regclass(%s) IS NOT NULL AS exists",
        (f"public.{table_name}",),
    )
    row = cur.fetchone()
    if row is None:
        return False
    if isinstance(row, dict):
        return bool(row.get("exists"))
    return bool(row[0])


def fetch_entity_row(cur, uri: str) -> Optional[Dict]:
    cur.execute(
        """
        SELECT id, fuseki_uri, entity_text, entity_type, normalized_text,
               occurrence_count, first_seen_at, last_seen_at
        FROM entity_registry
        WHERE fuseki_uri = %s
        """,
        (uri,),
    )
    return cur.fetchone()


def merge_koi_relationships(cur, winner_id: int, loser_id: int):
    """
    Rewrite koi_relationships to replace loser_id with winner_id while preserving counts.

    Handles unique conflicts on (subject_entity_id, predicate, object_entity_id) by
    merging occurrence_count/confidence/last_seen_at when those columns exist.
    """
    if not table_exists(cur, "koi_relationships"):
        LOGGER.warning("Table koi_relationships not found; skipping relationship rewrites.")
        return

    # Discover optional columns
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name='koi_relationships'
        """
    )
    fetched = cur.fetchall()
    cols = {(r["column_name"] if isinstance(r, dict) else r[0]) for r in fetched}
    has_occ = "occurrence_count" in cols
    has_conf = "confidence" in cols
    has_last = "last_seen_at" in cols

    def merge_fields_sql(prefix_existing: str, prefix_incoming: str) -> str:
        parts = []
        if has_occ:
            parts.append(f"occurrence_count = {prefix_existing}.occurrence_count + {prefix_incoming}.occurrence_count")
        if has_conf:
            parts.append(f"confidence = GREATEST({prefix_existing}.confidence, {prefix_incoming}.confidence)")
        if has_last:
            parts.append(f"last_seen_at = GREATEST({prefix_existing}.last_seen_at, {prefix_incoming}.last_seen_at)")
        return ", ".join(parts) if parts else ""

    # 0) Delete relationships that would become self-referential after merge
    # These are relationships where one end is loser and other end is winner
    cur.execute(
        """
        DELETE FROM koi_relationships
        WHERE (subject_entity_id = %s AND object_entity_id = %s)
           OR (subject_entity_id = %s AND object_entity_id = %s)
        """,
        (loser_id, winner_id, winner_id, loser_id),
    )

    # 1) Merge subject-side conflicts then update remaining
    subject_merge_set = merge_fields_sql("w", "c")
    if subject_merge_set:
        cur.execute(
            f"""
            WITH conflicts AS (
              SELECT l.id AS loser_rel_id, w.id AS winner_rel_id,
                     l.occurrence_count AS occurrence_count,
                     l.confidence AS confidence,
                     l.last_seen_at AS last_seen_at
              FROM koi_relationships l
              JOIN koi_relationships w
                ON w.subject_entity_id = %s
               AND l.subject_entity_id = %s
               AND w.predicate = l.predicate
               AND w.object_entity_id = l.object_entity_id
            )
            UPDATE koi_relationships w
            SET {subject_merge_set}
            FROM conflicts c
            WHERE w.id = c.winner_rel_id
            """,
            (winner_id, loser_id),
        )
    cur.execute(
        """
        DELETE FROM koi_relationships l
        USING koi_relationships w
        WHERE l.subject_entity_id = %s
          AND w.subject_entity_id = %s
          AND w.predicate = l.predicate
          AND w.object_entity_id = l.object_entity_id
        """,
        (loser_id, winner_id),
    )
    cur.execute(
        """
        UPDATE koi_relationships
        SET subject_entity_id = %s
        WHERE subject_entity_id = %s
        """,
        (winner_id, loser_id),
    )

    # 2) Merge object-side conflicts then update remaining
    object_merge_set = merge_fields_sql("w", "c")
    if object_merge_set:
        cur.execute(
            f"""
            WITH conflicts AS (
              SELECT l.id AS loser_rel_id, w.id AS winner_rel_id,
                     l.occurrence_count AS occurrence_count,
                     l.confidence AS confidence,
                     l.last_seen_at AS last_seen_at
              FROM koi_relationships l
              JOIN koi_relationships w
                ON w.object_entity_id = %s
               AND l.object_entity_id = %s
               AND w.predicate = l.predicate
               AND w.subject_entity_id = l.subject_entity_id
            )
            UPDATE koi_relationships w
            SET {object_merge_set}
            FROM conflicts c
            WHERE w.id = c.winner_rel_id
            """,
            (winner_id, loser_id),
        )
    cur.execute(
        """
        DELETE FROM koi_relationships l
        USING koi_relationships w
        WHERE l.object_entity_id = %s
          AND w.object_entity_id = %s
          AND w.predicate = l.predicate
          AND w.subject_entity_id = l.subject_entity_id
        """,
        (loser_id, winner_id),
    )
    cur.execute(
        """
        UPDATE koi_relationships
        SET object_entity_id = %s
        WHERE object_entity_id = %s
        """,
        (winner_id, loser_id),
    )


def update_chunk_links(cur, winner_uri: str, loser_uri: str):
    if not table_exists(cur, "koi_entity_chunk_links"):
        return
    cur.execute(
        """
        UPDATE koi_entity_chunk_links
        SET entity_uri = %s
        WHERE entity_uri = %s
        """,
        (winner_uri, loser_uri),
    )


def apply_one_merge(cur, row: MergeRow, notes: str = "") -> Tuple[int, int]:
    """
    Apply a single merge.

    Returns:
      (winner_id, loser_id)
    """
    winner = fetch_entity_row(cur, row.winner_uri)
    loser = fetch_entity_row(cur, row.loser_uri)
    if not winner or not loser:
        raise RuntimeError(f"Missing entity row(s): winner={bool(winner)} loser={bool(loser)}")

    if (winner["entity_type"] or "").upper() != (loser["entity_type"] or "").upper():
        raise RuntimeError(
            f"Type mismatch in DB for merge: {row.loser_uri} ({loser['entity_type']}) -> "
            f"{row.winner_uri} ({winner['entity_type']})"
        )

    winner_id = int(winner["id"])
    loser_id = int(loser["id"])

    # Merge entity counts + timestamps
    cur.execute(
        """
        UPDATE entity_registry
        SET occurrence_count = occurrence_count + %s,
            first_seen_at = LEAST(first_seen_at, %s),
            last_seen_at = GREATEST(last_seen_at, %s)
        WHERE id = %s
        """,
        (int(loser["occurrence_count"]), loser["first_seen_at"], loser["last_seen_at"], winner_id),
    )

    # Rewrite relationships and chunk links before deleting loser
    merge_koi_relationships(cur, winner_id=winner_id, loser_id=loser_id)
    update_chunk_links(cur, winner_uri=row.winner_uri, loser_uri=row.loser_uri)

    # Delete loser entity row
    cur.execute("DELETE FROM entity_registry WHERE id = %s", (loser_id,))

    # Record in merge plan
    cur.execute(
        """
        INSERT INTO dedup_merge_plan
          (winner_uri, loser_uri, winner_type, loser_type, method, reason, score, applied, applied_at, notes)
        VALUES
          (%s, %s, %s, %s, %s, %s, %s, TRUE, NOW(), %s)
        """,
        (
            row.winner_uri,
            row.loser_uri,
            row.winner_type,
            row.loser_type,
            row.method,
            row.reason,
            row.score,
            notes,
        ),
    )

    return winner_id, loser_id


def main():
    parser = argparse.ArgumentParser(description="Apply FIX-006 merge proposals to PostgreSQL.")
    parser.add_argument("--in", dest="input_path", required=True, help="Input CSV from dedup_dry_run.py")
    parser.add_argument(
        "--methods",
        default=",".join(DEFAULT_ALLOWED_METHODS),
        help=f"Comma-separated methods to apply (default: {','.join(DEFAULT_ALLOWED_METHODS)})",
    )
    parser.add_argument("--apply", action="store_true", help="Apply changes (otherwise dry-run)")
    parser.add_argument("--no-backup", action="store_true", help="Skip pg_dump backup (NOT recommended)")
    parser.add_argument("--backup-dir", default="backups", help="Backup output directory (default: ./backups)")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of merges applied (safety)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    load_dotenv()
    db_config = load_db_config()
    input_path = Path(args.input_path)
    if not input_path.exists():
        raise FileNotFoundError(str(input_path))

    allowed_methods = {m.strip() for m in args.methods.split(",") if m.strip()}
    rows = parse_merges_csv(input_path)

    # Filter to allowed + same-type
    filtered = [
        r
        for r in rows
        if r.method in allowed_methods and r.winner_type == r.loser_type and r.loser_uri and r.winner_uri
    ]
    mapping = build_final_mapping(filtered)
    final_merges = list(mapping.values())

    # Deterministic order: apply highest-score first (generally keeps strongest merges up front)
    final_merges.sort(key=lambda r: (r.method, -r.score, r.loser_uri))
    if args.limit is not None:
        final_merges = final_merges[: args.limit]

    LOGGER.info("Input rows: %d", len(rows))
    LOGGER.info("Filtered rows: %d (methods=%s)", len(filtered), ",".join(sorted(allowed_methods)))
    LOGGER.info("Final merges (after chain resolution): %d", len(final_merges))

    if not args.apply:
        print("\nDRY RUN (no DB writes). To apply, re-run with --apply.\n")
        for r in final_merges[:25]:
            print(f"- {r.method}: {r.loser_uri} -> {r.winner_uri} (score={r.score}, reason={r.reason})")
        if len(final_merges) > 25:
            print(f"... and {len(final_merges) - 25} more")
        return

    if not args.no_backup:
        backup_tables(
            db_config,
            output_dir=Path(args.backup_dir),
            tables=["entity_registry", "koi_relationships", "koi_entity_chunk_links"],
        )

    with connect(db_config) as conn:
        conn.autocommit = False
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            ensure_merge_plan_table(cur)

            applied = 0
            for row in final_merges:
                try:
                    apply_one_merge(cur, row, notes="FIX-006 safe subset apply_dedup_merges.py")
                    applied += 1
                    if applied % 50 == 0:
                        LOGGER.info("Applied %d/%d merges...", applied, len(final_merges))
                except Exception as e:
                    LOGGER.exception("Failed applying merge %s -> %s: %s", row.loser_uri, row.winner_uri, e)
                    raise

            conn.commit()
            LOGGER.info("✓ Applied merges: %d", applied)


if __name__ == "__main__":
    main()
