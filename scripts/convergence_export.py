#!/usr/bin/env python3
"""Export learning field convergence data as structured JSON.

Machine-readable interface over the convergence SQL queries.
The human-oriented SQL (learning_field_convergence.sql) stays as-is.

Usage:
  python scripts/convergence_export.py --families          # field family overview
  python scripts/convergence_export.py --ready             # synthesis-ready only
  python scripts/convergence_export.py --cluster <key>     # single cluster detail
  python scripts/convergence_export.py --all               # everything
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    sys.exit("psycopg2 required: pip install psycopg2-binary")


def get_connection():
    """Connect to personal_koi database."""
    env_file = Path(__file__).resolve().parents[1] / "config" / "personal.env"
    db_params = {
        "dbname": "personal_koi",
        "host": "localhost",
        "port": 5432,
    }
    # Try to read from env file for user/password if available
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("POSTGRES_USER="):
                db_params["user"] = line.split("=", 1)[1].strip()
            elif line.startswith("POSTGRES_PASSWORD="):
                db_params["password"] = line.split("=", 1)[1].strip()
    # Environment overrides
    if os.environ.get("PGUSER"):
        db_params["user"] = os.environ["PGUSER"]
    if os.environ.get("PGPASSWORD"):
        db_params["password"] = os.environ["PGPASSWORD"]
    return psycopg2.connect(**db_params)


FIELD_FAMILIES_SQL = """
WITH cluster_stats AS (
  SELECT
    split_part(rc.metadata->>'governance_cluster_key', ':', 2) AS concept_slug,
    rc.metadata->>'governance_cluster_key' AS cluster_key,
    split_part(rc.metadata->>'governance_cluster_key', ':', 1) AS target_doc,
    er.predicate AS stance,
    sc.source_document
  FROM claims rc
  JOIN entity_relationships er ON er.object_uri = rc.entity_uri
    AND er.predicate IN ('supports', 'opposes')
    AND er.source = 'learning_field'
  JOIN claims sc ON sc.entity_uri = er.subject_uri
    AND sc.metadata->>'claim_layer' = 'source'
    AND sc.metadata->>'source' = 'learning_field'
  WHERE rc.metadata->>'source' = 'learning_field'
    AND rc.metadata->>'claim_layer' = 'review'
)
SELECT
  concept_slug,
  count(DISTINCT cluster_key) AS governance_clusters,
  count(DISTINCT target_doc) AS target_docs,
  count(*) FILTER (WHERE stance = 'supports') AS total_supports,
  count(*) FILTER (WHERE stance = 'opposes') AS total_opposes,
  count(DISTINCT source_document) AS distinct_sources,
  CASE WHEN count(DISTINCT source_document) FILTER (WHERE source_document LIKE 'spore.%%') > 0
        AND count(DISTINCT source_document) FILTER (WHERE source_document LIKE 'ic.%%') > 0
       THEN true ELSE false END AS cross_project,
  CASE
    WHEN count(DISTINCT source_document) >= 2 AND count(*) FILTER (WHERE stance = 'opposes') > 0
      THEN 'ready_with_tension'
    WHEN count(DISTINCT source_document) >= 2 THEN 'ready_convergent'
    WHEN count(DISTINCT source_document) = 1 THEN 'needs_more_sources'
    ELSE 'insufficient'
  END AS synthesis_readiness,
  string_agg(DISTINCT target_doc, ', ' ORDER BY target_doc) AS target_doc_list
FROM cluster_stats
GROUP BY concept_slug
ORDER BY
  cross_project DESC,
  count(DISTINCT cluster_key) DESC,
  count(DISTINCT source_document) DESC,
  concept_slug
"""

GOVERNANCE_CLUSTERS_SQL = """
WITH cluster_members AS (
  SELECT
    rc.metadata->>'governance_cluster_key' AS cluster_key,
    split_part(rc.metadata->>'governance_cluster_key', ':', 1) AS target_doc,
    split_part(rc.metadata->>'governance_cluster_key', ':', 2) AS concept_slug,
    rc.claim_rid AS review_rid,
    rc.statement AS review_statement,
    rc.metadata->>'target_section' AS target_section,
    rc.metadata->>'change_slug' AS change_slug,
    er.predicate AS stance,
    sc.claim_rid AS source_rid,
    sc.statement AS source_statement,
    sc.source_document,
    sc.metadata->>'project_uri' AS source_project_uri
  FROM claims rc
  JOIN entity_relationships er ON er.object_uri = rc.entity_uri
    AND er.predicate IN ('supports', 'opposes')
    AND er.source = 'learning_field'
  JOIN claims sc ON sc.entity_uri = er.subject_uri
    AND sc.metadata->>'claim_layer' = 'source'
    AND sc.metadata->>'source' = 'learning_field'
  WHERE rc.metadata->>'source' = 'learning_field'
    AND rc.metadata->>'claim_layer' = 'review'
)
SELECT
  cluster_key,
  target_doc,
  concept_slug,
  review_rid,
  review_statement,
  target_section,
  change_slug,
  count(*) FILTER (WHERE stance = 'supports') AS support_count,
  count(*) FILTER (WHERE stance = 'opposes') AS oppose_count,
  count(DISTINCT source_document) AS distinct_sources,
  CASE WHEN count(DISTINCT source_document) FILTER (WHERE source_document LIKE 'spore.%%') > 0
        AND count(DISTINCT source_document) FILTER (WHERE source_document LIKE 'ic.%%') > 0
       THEN true ELSE false END AS cross_project,
  string_agg(DISTINCT source_document, ', ' ORDER BY source_document) AS source_notes
FROM cluster_members
GROUP BY cluster_key, target_doc, concept_slug, review_rid, review_statement, target_section, change_slug
ORDER BY
  CASE WHEN count(DISTINCT source_document) FILTER (WHERE source_document LIKE 'spore.%%') > 0
        AND count(DISTINCT source_document) FILTER (WHERE source_document LIKE 'ic.%%') > 0
       THEN 0 ELSE 1 END,
  count(DISTINCT source_document) DESC,
  count(*) FILTER (WHERE stance = 'opposes') ASC,
  cluster_key
"""

SOURCE_CLAIMS_SQL = """
SELECT
  rc.metadata->>'governance_cluster_key' AS cluster_key,
  er.predicate AS stance,
  sc.claim_rid AS source_rid,
  sc.statement AS source_statement,
  sc.source_document,
  sc.metadata->>'project_uri' AS source_project_uri
FROM claims rc
JOIN entity_relationships er ON er.object_uri = rc.entity_uri
  AND er.predicate IN ('supports', 'opposes')
  AND er.source = 'learning_field'
JOIN claims sc ON sc.entity_uri = er.subject_uri
  AND sc.metadata->>'claim_layer' = 'source'
  AND sc.metadata->>'source' = 'learning_field'
WHERE rc.metadata->>'source' = 'learning_field'
  AND rc.metadata->>'claim_layer' = 'review'
ORDER BY cluster_key, stance, sc.source_document
"""

QUESTIONS_SQL = """
SELECT
  split_part(rc.metadata->>'governance_cluster_key', ':', 2) AS concept_slug,
  q.entity_text AS question
FROM claims rc
JOIN entity_relationships er ON er.object_uri = rc.entity_uri
  AND er.predicate IN ('supports', 'opposes')
  AND er.source = 'learning_field'
JOIN claims sc ON sc.entity_uri = er.subject_uri
  AND sc.metadata->>'claim_layer' = 'source'
JOIN entity_relationships ab ON ab.subject_uri = sc.entity_uri
  AND ab.predicate = 'about' AND ab.source = 'claims_engine'
JOIN entity_registry concept ON concept.fuseki_uri = ab.object_uri AND concept.entity_type = 'Concept'
JOIN entity_relationships qab ON qab.object_uri = concept.fuseki_uri
  AND qab.predicate = 'about' AND qab.source = 'learning_field'
JOIN entity_registry q ON q.fuseki_uri = qab.subject_uri AND q.entity_type = 'Question'
WHERE rc.metadata->>'source' = 'learning_field'
GROUP BY concept_slug, q.entity_text
ORDER BY concept_slug, q.entity_text
"""


def query_families(conn) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(FIELD_FAMILIES_SQL)
        return [dict(r) for r in cur.fetchall()]


def query_clusters(conn, concept_slug: str | None = None) -> list[dict]:
    sql = GOVERNANCE_CLUSTERS_SQL
    params: tuple = ()
    if concept_slug:
        sql = sql.replace(
            "ORDER BY",
            f"HAVING split_part(rc.metadata->>'governance_cluster_key', ':', 2) = %s\nORDER BY"
        )
        # Simpler approach: filter after
        pass
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql)
        rows = [dict(r) for r in cur.fetchall()]
    if concept_slug:
        rows = [r for r in rows if r["concept_slug"] == concept_slug]
    return rows


def query_source_claims(conn, cluster_key: str | None = None) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(SOURCE_CLAIMS_SQL)
        rows = [dict(r) for r in cur.fetchall()]
    if cluster_key:
        rows = [r for r in rows if r["cluster_key"] == cluster_key]
    return rows


def query_questions(conn, concept_slug: str | None = None) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(QUESTIONS_SQL)
        rows = [dict(r) for r in cur.fetchall()]
    if concept_slug:
        rows = [r for r in rows if r["concept_slug"] == concept_slug]
    return rows


def build_full_export(conn) -> dict:
    """Build the complete three-level export."""
    families = query_families(conn)
    clusters = query_clusters(conn)
    sources = query_source_claims(conn)
    questions = query_questions(conn)

    # Index sources by cluster_key
    sources_by_cluster: dict[str, list[dict]] = {}
    for s in sources:
        key = s["cluster_key"]
        sources_by_cluster.setdefault(key, []).append(s)

    # Index questions by concept_slug
    questions_by_concept: dict[str, list[str]] = {}
    for q in questions:
        slug = q["concept_slug"]
        questions_by_concept.setdefault(slug, []).append(q["question"])

    # Index clusters by concept_slug
    clusters_by_concept: dict[str, list[dict]] = {}
    for c in clusters:
        slug = c["concept_slug"]
        clusters_by_concept.setdefault(slug, []).append(c)

    # Assemble three-level structure
    result = {
        "field_families": [],
        "summary": {
            "total_families": len(families),
            "total_clusters": len(clusters),
            "total_source_claims": len(sources),
            "synthesis_ready": sum(1 for f in families if f["synthesis_readiness"].startswith("ready")),
            "cross_project": sum(1 for f in families if f["cross_project"]),
        },
    }

    for fam in families:
        slug = fam["concept_slug"]
        fam_clusters = clusters_by_concept.get(slug, [])
        fam_questions = questions_by_concept.get(slug, [])

        family_entry = {
            "concept_slug": slug,
            "governance_clusters": fam["governance_clusters"],
            "target_docs": fam["target_docs"],
            "total_supports": fam["total_supports"],
            "total_opposes": fam["total_opposes"],
            "distinct_sources": fam["distinct_sources"],
            "cross_project": fam["cross_project"],
            "synthesis_readiness": fam["synthesis_readiness"],
            "target_doc_list": fam["target_doc_list"],
            "questions": fam_questions,
            "clusters": [],
        }

        for cl in fam_clusters:
            cl_sources = sources_by_cluster.get(cl["cluster_key"], [])
            cluster_entry = {
                "cluster_key": cl["cluster_key"],
                "target_doc": cl["target_doc"],
                "review_rid": cl["review_rid"],
                "review_statement": cl["review_statement"],
                "target_section": cl["target_section"],
                "change_slug": cl["change_slug"],
                "support_count": cl["support_count"],
                "oppose_count": cl["oppose_count"],
                "distinct_sources": cl["distinct_sources"],
                "cross_project": cl["cross_project"],
                "source_notes": cl["source_notes"],
                "source_claims": [
                    {
                        "source_rid": s["source_rid"],
                        "statement": s["source_statement"],
                        "stance": s["stance"],
                        "source_document": s["source_document"],
                        "project_uri": s["source_project_uri"],
                    }
                    for s in cl_sources
                ],
            }
            family_entry["clusters"].append(cluster_entry)

        result["field_families"].append(family_entry)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Export learning field convergence data as JSON.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--families", action="store_true", help="Field family overview")
    group.add_argument("--ready", action="store_true", help="Synthesis-ready families only")
    group.add_argument("--cluster", type=str, help="Single cluster detail by key")
    group.add_argument("--all", action="store_true", help="Full three-level export")

    args = parser.parse_args()

    try:
        conn = get_connection()
    except Exception as e:
        print(json.dumps({"error": f"Database connection failed: {e}"}), file=sys.stderr)
        return 1

    try:
        if args.families:
            data = {"field_families": query_families(conn)}
        elif args.ready:
            families = query_families(conn)
            data = {
                "field_families": [f for f in families if f["synthesis_readiness"].startswith("ready")]
            }
        elif args.cluster:
            clusters = query_clusters(conn)
            matching = [c for c in clusters if c["cluster_key"] == args.cluster]
            sources = query_source_claims(conn, cluster_key=args.cluster)
            data = {"cluster_key": args.cluster, "clusters": matching, "source_claims": sources}
        elif args.all:
            data = build_full_export(conn)

        print(json.dumps(data, indent=2, default=str))
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
