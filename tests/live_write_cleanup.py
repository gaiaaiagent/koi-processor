#!/usr/bin/env python3
"""Exact, fail-closed cleanup for out-of-process integration tests.

The manifest records identifiers returned by the API before assertions can
abort the run. Cleanup deletes only those identifiers plus rows carrying the
run's UUID marker. A missing DSN, wrong database, failed delete, or surviving
identifier makes the test red.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse


VALID_KINDS = {
    "claim_rid",
    "commitment_rid",
    "entity_uri",
    "pool_rid",
    "source_document",
}


def validate_dsn(dsn: str) -> str:
    if not dsn:
        raise RuntimeError("KOI_LIVE_POSTGRES_URL is required")
    parsed = urlparse(dsn)
    database = parsed.path.lstrip("/")
    if not database:
        raise RuntimeError("database name missing from KOI_LIVE_POSTGRES_URL")
    return database


def append_record(manifest: Path, kind: str, value: str) -> None:
    if kind not in VALID_KINDS:
        raise ValueError(f"unsupported cleanup kind: {kind}")
    if not value:
        return
    with manifest.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": kind, "value": value}) + "\n")


def load_manifest(manifest: Path) -> dict[str, set[str]]:
    records = {kind: set() for kind in VALID_KINDS}
    if not manifest.exists():
        return records
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if item["kind"] not in VALID_KINDS:
            raise ValueError(f"unsupported cleanup kind: {item['kind']}")
        records[item["kind"]].add(item["value"])
    return records


def _table_exists(cur, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
    return cur.fetchone()[0] is not None


def _delete(cur, table: str, where: str, params: tuple) -> int:
    if not _table_exists(cur, table):
        return 0
    cur.execute(f'DELETE FROM "{table}" WHERE {where}', params)
    return cur.rowcount


def cleanup(dsn: str, manifest: Path, run_id: str) -> dict:
    import psycopg2

    expected_database = validate_dsn(dsn)
    records = load_manifest(manifest)
    deleted: dict[str, int] = {}
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        actual_database = cur.fetchone()[0]
        if actual_database != expected_database:
            raise RuntimeError(
                f"cleanup DSN named {expected_database!r} but connected to "
                f"{actual_database!r}"
            )

        marker = f"%{run_id}%"

        # Recover identifiers from the UUID marker when an HTTP response was
        # lost after the server committed but before the test could record it.
        for table, column, kind in (
            ("claims", "claim_rid", "claim_rid"),
            ("commitments", "commitment_rid", "commitment_rid"),
            ("commitment_pools", "pool_rid", "pool_rid"),
            ("entity_registry", "fuseki_uri", "entity_uri"),
        ):
            if not _table_exists(cur, table):
                continue
            cur.execute(
                f'SELECT "{column}" FROM "{table}" t '
                "WHERE row_to_json(t)::text LIKE %s",
                (marker,),
            )
            records[kind].update(row[0] for row in cur.fetchall() if row[0])

        claim_rids = sorted(records["claim_rid"])
        if claim_rids:
            deleted["claim_attestations"] = _delete(
                cur, "claim_attestations", "claim_rid = ANY(%s)", (claim_rids,)
            )
            deleted["claim_state_log"] = _delete(
                cur, "claim_state_log", "claim_rid = ANY(%s)", (claim_rids,)
            )
            deleted["claims"] = _delete(
                cur, "claims", "claim_rid = ANY(%s)", (claim_rids,)
            )

        commitment_rids = sorted(records["commitment_rid"])
        if commitment_rids:
            deleted["commitment_state_log"] = _delete(
                cur,
                "commitment_state_log",
                "commitment_rid = ANY(%s)",
                (commitment_rids,),
            )
            deleted["commitments"] = _delete(
                cur, "commitments", "commitment_rid = ANY(%s)", (commitment_rids,)
            )

        pool_rids = sorted(records["pool_rid"])
        if pool_rids:
            deleted["commitment_pool_events"] = _delete(
                cur,
                "commitment_pool_events",
                "pool_rid = ANY(%s)",
                (pool_rids,),
            )
            deleted["commitment_pools"] = _delete(
                cur, "commitment_pools", "pool_rid = ANY(%s)", (pool_rids,)
            )

        entity_uris = sorted(records["entity_uri"])
        if entity_uris:
            for table, where in (
                ("claim_attestations", "evidence_uris && %s::text[]"),
                ("entity_relationships", "subject_uri = ANY(%s) OR object_uri = ANY(%s)"),
                ("pending_relationships", "subject_uri = ANY(%s) OR object_uri = ANY(%s)"),
                ("document_entity_links", "entity_uri = ANY(%s)"),
                ("entity_closure_log", "entity_uri = ANY(%s)"),
                ("entity_rid_mappings", "canonical_uri = ANY(%s)"),
            ):
                params = (entity_uris, entity_uris) if where.count("%s") == 2 else (entity_uris,)
                deleted[table] = deleted.get(table, 0) + _delete(
                    cur, table, where, params
                )
            deleted["entity_registry"] = _delete(
                cur, "entity_registry", "fuseki_uri = ANY(%s)", (entity_uris,)
            )

        source_documents = sorted(records["source_document"])
        if source_documents:
            deleted["knowledge_episodes"] = _delete(
                cur,
                "knowledge_episodes",
                "source_document = ANY(%s)",
                (source_documents,),
            )

        # Federation endpoints do not consistently return row identifiers. All
        # test payloads carry the UUID run marker, so remove only rows containing
        # that exact marker from their JSON/text representation.
        for table in (
            "commitment_pool_events",
            "federation_applied_events",
            "koi_provenance_links",
            "koi_transformation_receipts",
            "web_crawl_jobs",
            "web_submissions",
            "koi_net_cross_refs",
            "koi_net_events",
            "koi_outbound_shares",
            "koi_shared_documents",
            "vault_sync_applied_events",
        ):
            if _table_exists(cur, table):
                cur.execute(
                    f'DELETE FROM "{table}" t WHERE row_to_json(t)::text LIKE %s',
                    (marker,),
                )
                deleted[table] = deleted.get(table, 0) + cur.rowcount

        # Fail closed if any explicitly recorded base object survived.
        survivors: list[str] = []
        checks = (
            ("claims", "claim_rid", claim_rids),
            ("commitments", "commitment_rid", commitment_rids),
            ("commitment_pools", "pool_rid", pool_rids),
            ("entity_registry", "fuseki_uri", entity_uris),
            ("knowledge_episodes", "source_document", source_documents),
        )
        for table, column, values in checks:
            if not values or not _table_exists(cur, table):
                continue
            cur.execute(
                f'SELECT count(*) FROM "{table}" WHERE "{column}" = ANY(%s)',
                (values,),
            )
            remaining = cur.fetchone()[0]
            if remaining:
                survivors.append(f"{table}.{column}={remaining}")
        if survivors:
            raise RuntimeError("cleanup survivors: " + ", ".join(survivors))

    return {"database": expected_database, "deleted": deleted, "run_id": run_id}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--dsn", required=True)
    record = sub.add_parser("record")
    record.add_argument("--manifest", type=Path, required=True)
    record.add_argument("--kind", choices=sorted(VALID_KINDS), required=True)
    record.add_argument("--value", required=True)
    clean = sub.add_parser("cleanup")
    clean.add_argument("--dsn", required=True)
    clean.add_argument("--manifest", type=Path, required=True)
    clean.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if args.command == "validate":
        print(validate_dsn(args.dsn))
    elif args.command == "record":
        append_record(args.manifest, args.kind, args.value)
    else:
        print(json.dumps(cleanup(args.dsn, args.manifest, args.run_id), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"live-write cleanup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
