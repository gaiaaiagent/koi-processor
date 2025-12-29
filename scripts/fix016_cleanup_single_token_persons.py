#!/usr/bin/env python3
"""
FIX-016 follow-up: cleanup existing single-token PERSON entities.

Dry-run by default. Use --apply to delete selected entities and their
relationships/chunk links after creating backup tables.
"""

from __future__ import annotations

import argparse
import csv
import os
from datetime import datetime
from typing import Dict, List, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor

try:
    from knowledge_graph.improvements.entity_quality_filter import EntityQualityFilter
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "ERROR: Unable to import EntityQualityFilter. "
        "Run with PYTHONPATH=src and ensure dependencies are installed."
    ) from exc


def get_db_config() -> Dict[str, str | int]:
    return {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": int(os.getenv("POSTGRES_PORT", "5433")),
        "database": os.getenv("POSTGRES_DB", "eliza"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
    }


def fetch_person_entities(conn) -> List[Dict]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, entity_text, fuseki_uri, occurrence_count
            FROM entity_registry
            WHERE entity_type = 'PERSON'
            """
        )
        return cur.fetchall()


def fetch_relationship_counts(conn, entity_ids: List[int]) -> Dict[int, int]:
    if not entity_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT entity_id, SUM(cnt) as rel_count
            FROM (
                SELECT subject_entity_id AS entity_id, COUNT(*) AS cnt
                FROM koi_relationships
                WHERE subject_entity_id = ANY(%s)
                GROUP BY subject_entity_id
                UNION ALL
                SELECT object_entity_id AS entity_id, COUNT(*) AS cnt
                FROM koi_relationships
                WHERE object_entity_id = ANY(%s)
                GROUP BY object_entity_id
            ) rels
            GROUP BY entity_id
            """,
            (entity_ids, entity_ids),
        )
        return {row[0]: row[1] for row in cur.fetchall()}


def fetch_chunk_link_counts(conn, entity_uris: List[str]) -> Dict[str, int]:
    if not entity_uris:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT entity_uri, COUNT(*) AS cnt
            FROM koi_entity_chunk_links
            WHERE entity_uri = ANY(%s)
            GROUP BY entity_uri
            """,
            (entity_uris,),
        )
        return {row[0]: row[1] for row in cur.fetchall()}


def write_csv(rows: List[Dict], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(rows)


def build_backup_tables(conn, ids: List[int], uris: List[str], suffix: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS entity_registry_backup_fix016_{suffix} AS
            SELECT * FROM entity_registry WHERE id = ANY(%s)
            """,
            (ids,),
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS koi_relationships_backup_fix016_{suffix} AS
            SELECT * FROM koi_relationships
            WHERE subject_entity_id = ANY(%s) OR object_entity_id = ANY(%s)
            """,
            (ids, ids),
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS koi_entity_chunk_links_backup_fix016_{suffix} AS
            SELECT * FROM koi_entity_chunk_links
            WHERE entity_uri = ANY(%s)
            """,
            (uris,),
        )


def apply_deletions(conn, ids: List[int], uris: List[str]) -> Tuple[int, int, int]:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM koi_relationships WHERE subject_entity_id = ANY(%s) OR object_entity_id = ANY(%s)",
            (ids, ids),
        )
        rel_deleted = cur.rowcount
        cur.execute(
            "DELETE FROM koi_entity_chunk_links WHERE entity_uri = ANY(%s)",
            (uris,),
        )
        links_deleted = cur.rowcount
        cur.execute("DELETE FROM entity_registry WHERE id = ANY(%s)", (ids,))
        entities_deleted = cur.rowcount
    return entities_deleted, rel_deleted, links_deleted


def main() -> None:
    parser = argparse.ArgumentParser(description="Cleanup single-token PERSON entities (FIX-016 follow-up).")
    parser.add_argument("--apply", action="store_true", help="Apply deletions (default is dry-run).")
    parser.add_argument(
        "--max-rel-count",
        type=int,
        default=0,
        help="Only delete entities with relationship_count <= this value (default: 0).",
    )
    parser.add_argument(
        "--max-occurrence",
        type=int,
        default=None,
        help="Optional upper bound on occurrence_count for deletion.",
    )
    parser.add_argument(
        "--output",
        default="data/fix016_single_token_persons.csv",
        help="CSV output path for full candidate report.",
    )
    args = parser.parse_args()

    db_config = get_db_config()
    print(f"Connecting to {db_config['host']}:{db_config['port']}/{db_config['database']}...")
    conn = psycopg2.connect(**db_config)
    conn.autocommit = False

    filter_guard = EntityQualityFilter()

    try:
        people = fetch_person_entities(conn)
        candidates = [
            p for p in people if filter_guard.is_single_token_person(p["entity_text"], "PERSON")
        ]

        if not candidates:
            print("No single-token PERSON candidates found.")
            return

        candidate_ids = [p["id"] for p in candidates]
        candidate_uris = [p["fuseki_uri"] for p in candidates]

        rel_counts = fetch_relationship_counts(conn, candidate_ids)
        link_counts = fetch_chunk_link_counts(conn, candidate_uris)

        rows = []
        for p in candidates:
            rows.append(
                {
                    "id": p["id"],
                    "entity_text": p["entity_text"],
                    "occurrence_count": p["occurrence_count"],
                    "relationship_count": rel_counts.get(p["id"], 0),
                    "chunk_link_count": link_counts.get(p["fuseki_uri"], 0),
                    "fuseki_uri": p["fuseki_uri"],
                }
            )

        rows.sort(key=lambda r: (-int(r["occurrence_count"]), r["entity_text"]))
        write_csv(rows, args.output)

        total_rel = sum(r["relationship_count"] for r in rows)
        total_links = sum(r["chunk_link_count"] for r in rows)
        print(f"Candidates: {len(rows)} single-token PERSON entities")
        print(f"Total relationships attached: {total_rel}")
        print(f"Total chunk links attached: {total_links}")
        print(f"Report written to: {args.output}")

        deletable = []
        for r in rows:
            if r["relationship_count"] > args.max_rel_count:
                continue
            if args.max_occurrence is not None and int(r["occurrence_count"]) > args.max_occurrence:
                continue
            deletable.append(r)

        print(f"Deletion candidates (max_rel_count={args.max_rel_count}, max_occurrence={args.max_occurrence}): {len(deletable)}")

        if not args.apply:
            conn.rollback()
            print("Dry-run only. Re-run with --apply to delete.")
            return

        if not deletable:
            conn.rollback()
            print("No deletions to apply.")
            return

        delete_ids = [r["id"] for r in deletable]
        delete_uris = [r["fuseki_uri"] for r in deletable]
        suffix = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

        build_backup_tables(conn, delete_ids, delete_uris, suffix)
        entities_deleted, rel_deleted, links_deleted = apply_deletions(conn, delete_ids, delete_uris)
        conn.commit()

        print("=== APPLY COMPLETE ===")
        print(f"Entities deleted: {entities_deleted}")
        print(f"Relationships deleted: {rel_deleted}")
        print(f"Chunk links deleted: {links_deleted}")
        print(f"Backup tables: entity_registry_backup_fix016_{suffix}, "
              f"koi_relationships_backup_fix016_{suffix}, "
              f"koi_entity_chunk_links_backup_fix016_{suffix}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
