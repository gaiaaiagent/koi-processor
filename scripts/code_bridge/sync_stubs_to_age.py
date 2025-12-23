#!/usr/bin/env python3
"""
Sync stub nodes and edges into Apache AGE (mark/sweep).

Stub nodes provide semantic anchors inside the AGE graph for single-query access:
  - Stub:Person, Stub:Organization, Stub:Proposal, Stub:Concept, Stub:Module
  - Stub:Doc (for doc→code links)
  - Stub:CodeArtifact (bridge node for code_uri)

Edges:
  - (:Stub:Doc)-[:MENTIONS]->(:Stub:CodeArtifact)
  - (:Stub:*)-[:CODE_REF]->(:Stub:CodeArtifact)  (entity-level code links)

All stub nodes/edges are tagged with sync_run_id to allow mark/sweep cleanup.

Usage:
  cd /opt/projects/koi-processor
  PYTHONPATH=src ./.venv/bin/python scripts/code_bridge/sync_stubs_to_age.py --dry-run
  PYTHONPATH=src ./.venv/bin/python scripts/code_bridge/sync_stubs_to_age.py

Environment:
  POSTGRES_HOST (default: localhost)
  POSTGRES_PORT (default: 5433)
  POSTGRES_DB   (default: eliza)
  POSTGRES_USER (default: postgres)
  POSTGRES_PASSWORD (default: postgres)
  AGE_GRAPH     (default: regen_graph_v2)
"""

from __future__ import annotations

import argparse
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List

import psycopg2
from psycopg2.extras import RealDictCursor


def escape_cypher(text: str) -> str:
    if text is None:
        return ""
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def cypher_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return f"'{escape_cypher(value)}'"


def batch_execute(cur, graph: str, statements: List[str], batch_size: int = 200):
    # Run each statement individually to avoid variable conflicts in AGE
    for stmt in statements:
        if not stmt:
            continue
        query = f"""
        SELECT * FROM cypher('{graph}', $$
            {stmt}
        $$) AS (result agtype);
        """
        cur.execute(query)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync stub nodes/edges into Apache AGE")
    parser.add_argument("--dry-run", action="store_true", help="Do not write to AGE")
    parser.add_argument("--no-sweep", action="store_true", help="Skip mark/sweep cleanup")
    parser.add_argument("--batch-size", type=int, default=200, help="Cypher statements per batch")
    parser.add_argument("--min-person-occ", type=int, default=3, help="Min occurrence_count for Person stubs")
    parser.add_argument("--min-org-occ", type=int, default=2, help="Min occurrence_count for Organization stubs")
    args = parser.parse_args()

    graph = os.getenv("AGE_GRAPH", "regen_graph_v2")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5433")),
        database=os.getenv("POSTGRES_DB", "eliza"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )
    conn.set_session(autocommit=True)

    cur = conn.cursor()
    cur.execute("LOAD 'age';")
    cur.execute("SET search_path = ag_catalog, '$user', public;")

    # -----------------------
    # Build CodeArtifact stubs
    # -----------------------
    with conn.cursor(cursor_factory=RealDictCursor) as ccur:
        ccur.execute(
            """
            SELECT DISTINCT code_uri, kind, repo_key, file_path, symbol, language, commit_sha
            FROM koi_code_artifacts
            WHERE code_uri IN (
                SELECT DISTINCT code_uri FROM koi_doc_code_links
                UNION
                SELECT DISTINCT metadata->>'code_uri' FROM entity_registry WHERE metadata ? 'code_uri'
            )
            """
        )
        code_artifacts = ccur.fetchall()

    code_statements = []
    for row in code_artifacts:
        props = {
            "code_uri": row["code_uri"],
            "kind": row["kind"],
            "repo_key": row["repo_key"],
            "file_path": row["file_path"],
            "symbol": row["symbol"] or "",
            "language": row["language"] or "",
            "commit_sha": row["commit_sha"] or "",
            "sync_run_id": run_id,
            "stub_node": True,
        }
        prop_str = ", ".join([f"{k}: {cypher_value(v)}" for k, v in props.items()])
        code_statements.append(
            f"MERGE (c:CodeArtifact {{code_uri: {cypher_value(row['code_uri'])}}}) "
            f"SET c += {{{prop_str}}}"
        )

    # -----------------------
    # Build entity stubs
    # -----------------------
    entity_statements = []
    with conn.cursor(cursor_factory=RealDictCursor) as ecur:
        ecur.execute(
            """
            SELECT id, fuseki_uri, entity_text, entity_type, occurrence_count, metadata
            FROM entity_registry
            WHERE entity_type = 'PERSON' AND occurrence_count >= %s
            """,
            (args.min_person_occ,),
        )
        for row in ecur.fetchall():
            props = {
                "entity_id": row["id"],
                "uri": row["fuseki_uri"],
                "name": row["entity_text"],
                "entity_type": row["entity_type"],
                "occurrence_count": row["occurrence_count"],
                "sync_run_id": run_id,
                "stub_node": True,
            }
            prop_str = ", ".join([f"{k}: {cypher_value(v)}" for k, v in props.items()])
            entity_statements.append(
                f"MERGE (p:Person {{entity_id: {row['id']}}}) SET p += {{{prop_str}}}"
            )

        ecur.execute(
            """
            SELECT id, fuseki_uri, entity_text, entity_type, occurrence_count, metadata
            FROM entity_registry
            WHERE entity_type = 'ORGANIZATION' AND occurrence_count >= %s
            """,
            (args.min_org_occ,),
        )
        for row in ecur.fetchall():
            props = {
                "entity_id": row["id"],
                "uri": row["fuseki_uri"],
                "name": row["entity_text"],
                "entity_type": row["entity_type"],
                "occurrence_count": row["occurrence_count"],
                "sync_run_id": run_id,
                "stub_node": True,
            }
            prop_str = ", ".join([f"{k}: {cypher_value(v)}" for k, v in props.items()])
            entity_statements.append(
                f"MERGE (o:Organization {{entity_id: {row['id']}}}) SET o += {{{prop_str}}}"
            )

        ecur.execute(
            """
            SELECT id, fuseki_uri, entity_text, entity_type, occurrence_count, metadata
            FROM entity_registry
            WHERE entity_type = 'GOVERNANCE_PROPOSAL'
            """
        )
        for row in ecur.fetchall():
            props = {
                "entity_id": row["id"],
                "uri": row["fuseki_uri"],
                "title": row["entity_text"],
                "entity_type": row["entity_type"],
                "occurrence_count": row["occurrence_count"],
                "sync_run_id": run_id,
                "stub_node": True,
            }
            prop_str = ", ".join([f"{k}: {cypher_value(v)}" for k, v in props.items()])
            entity_statements.append(
                f"MERGE (pr:Proposal {{entity_id: {row['id']}}}) SET pr += {{{prop_str}}}"
            )

        ecur.execute(
            """
            SELECT id, fuseki_uri, entity_text, entity_type, occurrence_count, metadata
            FROM entity_registry
            WHERE entity_type = 'CONCEPT' AND (metadata->>'code_uri') IS NOT NULL
            """
        )
        for row in ecur.fetchall():
            code_uri = row["metadata"].get("code_uri") if row.get("metadata") else None
            props = {
                "entity_id": row["id"],
                "uri": row["fuseki_uri"],
                "label": row["entity_text"],
                "entity_type": row["entity_type"],
                "occurrence_count": row["occurrence_count"],
                "code_uri": code_uri,
                "sync_run_id": run_id,
                "stub_node": True,
            }
            prop_str = ", ".join([f"{k}: {cypher_value(v)}" for k, v in props.items()])
            entity_statements.append(
                f"MERGE (cn:Concept {{entity_id: {row['id']}}}) SET cn += {{{prop_str}}}"
            )

        ecur.execute(
            """
            SELECT id, fuseki_uri, entity_text, entity_type, occurrence_count, metadata
            FROM entity_registry
            WHERE entity_type = 'MODULE' AND (metadata->>'code_uri') IS NOT NULL
            """
        )
        for row in ecur.fetchall():
            code_uri = row["metadata"].get("code_uri") if row.get("metadata") else None
            props = {
                "entity_id": row["id"],
                "uri": row["fuseki_uri"],
                "name": row["entity_text"],
                "entity_type": row["entity_type"],
                "occurrence_count": row["occurrence_count"],
                "code_uri": code_uri,
                "sync_run_id": run_id,
                "stub_node": True,
            }
            prop_str = ", ".join([f"{k}: {cypher_value(v)}" for k, v in props.items()])
            entity_statements.append(
                f"MERGE (m:Module {{entity_id: {row['id']}}}) SET m += {{{prop_str}}}"
            )

    # -----------------------
    # Build Doc stubs + MENTIONS edges
    # -----------------------
    doc_statements = []
    mention_statements = []
    with conn.cursor(cursor_factory=RealDictCursor) as dcur:
        dcur.execute(
            """
            SELECT DISTINCT ON (d.memory_rid)
                d.memory_rid,
                m.source_sensor,
                COALESCE(m.metadata->>'title', m.content->>'title', m.metadata->>'url', d.memory_rid) AS title
            FROM koi_doc_code_links d
            JOIN koi_memories m ON m.rid = d.memory_rid
            """
        )
        for row in dcur.fetchall():
            props = {
                "rid": row["memory_rid"],
                "title": row["title"],
                "source_sensor": row["source_sensor"],
                "sync_run_id": run_id,
                "stub_node": True,
            }
            prop_str = ", ".join([f"{k}: {cypher_value(v)}" for k, v in props.items()])
            doc_statements.append(
                f"MERGE (d:Doc {{rid: {cypher_value(row['memory_rid'])}}}) SET d += {{{prop_str}}}"
            )

        dcur.execute(
            """
            SELECT memory_rid, code_uri, confidence, metadata
            FROM koi_doc_code_links
            """
        )
        for row in dcur.fetchall():
            conf = row.get("confidence") or 0.0
            method = None
            if row.get("metadata") and isinstance(row["metadata"], dict):
                method = row["metadata"].get("match_type")
            props = {
                "sync_run_id": run_id,
                "stub_edge": True,
                "confidence": conf,
                "match_type": method or "",
            }
            prop_str = ", ".join([f"{k}: {cypher_value(v)}" for k, v in props.items()])
            mention_statements.append(
                f"MERGE (d:Doc {{rid: {cypher_value(row['memory_rid'])}}}) "
                f"MERGE (c:CodeArtifact {{code_uri: {cypher_value(row['code_uri'])}}}) "
                f"MERGE (d)-[r:MENTIONS]->(c) SET r += {{{prop_str}}}"
            )

    # -----------------------
    # Build CODE_REF edges (entity-level links)
    # -----------------------
    code_ref_statements = []
    with conn.cursor(cursor_factory=RealDictCursor) as rcur:
        rcur.execute(
            """
            SELECT id, entity_type, metadata
            FROM entity_registry
            WHERE (metadata->>'code_uri') IS NOT NULL
            """
        )
        for row in rcur.fetchall():
            meta = row.get("metadata") or {}
            code_uri = meta.get("code_uri")
            confidence = meta.get("link_confidence") or 0.0
            method = meta.get("link_method") or ""
            props = {
                "sync_run_id": run_id,
                "stub_edge": True,
                "confidence": confidence,
                "link_method": method,
            }
            prop_str = ", ".join([f"{k}: {cypher_value(v)}" for k, v in props.items()])
            code_ref_statements.append(
                f"MATCH (e {{entity_id: {row['id']}, stub_node: true}}) "
                f"MERGE (c:CodeArtifact {{code_uri: {cypher_value(code_uri)}}}) "
                f"MERGE (e)-[r:CODE_REF]->(c) SET r += {{{prop_str}}}"
            )

    if args.dry_run:
        print("[sync_stubs_to_age] dry_run=True; no AGE writes")
        print(f"[sync_stubs_to_age] code_artifacts={len(code_statements)} entities={len(entity_statements)} docs={len(doc_statements)} mentions={len(mention_statements)} code_refs={len(code_ref_statements)}")
        conn.close()
        return 0

    # Execute batch writes
    batch_execute(cur, graph, code_statements, args.batch_size)
    batch_execute(cur, graph, entity_statements, args.batch_size)
    batch_execute(cur, graph, doc_statements, args.batch_size)
    batch_execute(cur, graph, mention_statements, args.batch_size)
    batch_execute(cur, graph, code_ref_statements, args.batch_size)

    # Mark/sweep cleanup
    if not args.no_sweep:
        cur.execute(
            f"""
            SELECT * FROM cypher('{graph}', $$
                MATCH ()-[r]-()
                WHERE r.stub_edge = true AND r.sync_run_id <> '{run_id}'
                DELETE r
            $$) AS (result agtype);
            """
        )
        cur.execute(
            f"""
            SELECT * FROM cypher('{graph}', $$
                MATCH (n)
                WHERE n.stub_node = true AND n.sync_run_id <> '{run_id}'
                DETACH DELETE n
            $$) AS (result agtype);
            """
        )

    conn.close()
    print(f"[sync_stubs_to_age] completed run_id={run_id} graph={graph}")
    print(f"[sync_stubs_to_age] code_artifacts={len(code_statements)} entities={len(entity_statements)} docs={len(doc_statements)} mentions={len(mention_statements)} code_refs={len(code_ref_statements)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
