#!/usr/bin/env python3
"""
Create Apache AGE property indexes for stub nodes.

Targets:
  - :Stub(entity_id)
  - :Stub:Doc(rid)
  - :Stub:CodeArtifact(code_uri)

Usage:
  cd /opt/projects/koi-processor
  PYTHONPATH=src ./.venv/bin/python scripts/code_bridge/create_stub_indexes.py

Environment:
  POSTGRES_HOST (default: localhost)
  POSTGRES_PORT (default: 5433)
  POSTGRES_DB   (default: eliza)
  POSTGRES_USER (default: postgres)
  POSTGRES_PASSWORD (default: postgres)
  AGE_GRAPH     (default: regen_graph_v2)
"""

from __future__ import annotations

import os
import psycopg2


INDEX_QUERIES = [
    "CREATE INDEX ON :Stub(entity_id)",
    "CREATE INDEX ON :Stub:Doc(rid)",
    "CREATE INDEX ON :Stub:CodeArtifact(code_uri)",
]


def main() -> int:
    graph = os.getenv("AGE_GRAPH", "regen_graph_v2")

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

    created = 0
    skipped = 0

    for stmt in INDEX_QUERIES:
        try:
            cur.execute(
                f"""
                SELECT * FROM cypher('{graph}', $$
                    {stmt}
                $$) AS (result agtype);
                """
            )
            created += 1
            print(f"[AGE] Created index: {stmt}")
        except Exception as exc:
            # AGE raises on duplicate index creation; treat as non-fatal.
            skipped += 1
            print(f"[AGE] Skipped index (likely exists): {stmt} ({exc})")

    cur.close()
    conn.close()
    print(f"[AGE] Indexes created={created} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

