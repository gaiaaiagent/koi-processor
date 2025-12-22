#!/usr/bin/env python3
"""
Export code entities (from code_entity_provenance) into koi_code_artifacts.

This is the first half of the "bridge" between:
- semantic KG (Postgres tables: entity_registry / koi_relationships / RDF export)
- code graph (tree-sitter → Apache AGE) and its provenance (CAT receipts)

Initial population strategy:
  code_entity_provenance (VIEW) → koi_code_artifacts (TABLE)

Later, you can extend this to also set (age_graph, age_id) by querying AGE and matching
on code_uri (often rid://code/...) or other stable properties.

Usage:
  cd /opt/projects/koi-processor
  PYTHONPATH=src ./.venv/bin/python scripts/code_bridge/export_code_artifacts.py

Environment:
  POSTGRES_HOST (default: localhost)
  POSTGRES_PORT (default: 5433)
  POSTGRES_DB   (default: eliza)
  POSTGRES_USER (default: postgres)
  POSTGRES_PASSWORD (default: postgres)
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import urlparse

import psycopg2
from psycopg2.extras import RealDictCursor, Json


GITHUB_REPO_RE = re.compile(r"^/([^/]+)/([^/]+)/")


@dataclass(frozen=True)
class RepoRef:
    repo_key: str
    org: Optional[str] = None
    repo: Optional[str] = None


def _parse_repo_key_from_url(url: str) -> Optional[RepoRef]:
    try:
        parsed = urlparse(url)
    except Exception:
        return None

    if not parsed.netloc or not parsed.path:
        return None

    match = GITHUB_REPO_RE.match(parsed.path)
    if not match:
        return None

    org, repo = match.group(1), match.group(2)
    host = parsed.netloc.lower()
    return RepoRef(repo_key=f"{host}/{org}/{repo}", org=org, repo=repo)


def _parse_repo_key_from_file_path(file_path: str) -> Optional[RepoRef]:
    # Some pipelines store file_path like "regen-network/regen-ledger/x/ecocredit/..."
    parts = [p for p in (file_path or "").split("/") if p]
    if len(parts) < 2:
        return None
    org, repo = parts[0], parts[1]
    return RepoRef(repo_key=f"github.com/{org}/{repo}", org=org, repo=repo)


def resolve_repo_key(
    github_url: Optional[str],
    repo_fallback: Optional[str],
    file_path: Optional[str],
) -> RepoRef:
    if github_url:
        parsed = _parse_repo_key_from_url(github_url)
        if parsed:
            return parsed

    if file_path:
        parsed = _parse_repo_key_from_file_path(file_path)
        if parsed:
            return parsed

    if repo_fallback:
        # Last resort: preserve something stable, even if not globally unique.
        return RepoRef(repo_key=repo_fallback, repo=repo_fallback)

    return RepoRef(repo_key="unknown")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export code_entity_provenance → koi_code_artifacts")
    parser.add_argument("--limit", type=int, default=0, help="Limit rows processed (0 = no limit)")
    parser.add_argument("--dry-run", action="store_true", help="Print stats only; do not write")
    args = parser.parse_args()

    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5433")),
        database=os.getenv("POSTGRES_DB", "eliza"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )
    conn.autocommit = False

    limit_sql = "LIMIT %(limit)s" if args.limit and args.limit > 0 else ""

    select_sql = f"""
        SELECT DISTINCT ON (entity_rid)
          entity_rid,
          repo,
          file_path,
          entity_name,
          entity_type,
          language,
          commit_sha,
          github_url,
          full_metadata,
          extracted_at
        FROM code_entity_provenance
        WHERE entity_rid IS NOT NULL
          AND entity_type IS NOT NULL
          AND file_path IS NOT NULL
        ORDER BY entity_rid, extracted_at DESC
        {limit_sql}
    """

    upsert_sql = """
        INSERT INTO koi_code_artifacts (
          code_uri,
          kind,
          repo_key,
          file_path,
          symbol,
          language,
          commit_sha,
          metadata,
          updated_at
        )
        VALUES (
          %(code_uri)s,
          %(kind)s,
          %(repo_key)s,
          %(file_path)s,
          %(symbol)s,
          %(language)s,
          %(commit_sha)s,
          %(metadata)s,
          now()
        )
        ON CONFLICT (code_uri) DO UPDATE SET
          kind = EXCLUDED.kind,
          repo_key = EXCLUDED.repo_key,
          file_path = EXCLUDED.file_path,
          symbol = EXCLUDED.symbol,
          language = EXCLUDED.language,
          commit_sha = EXCLUDED.commit_sha,
          metadata = EXCLUDED.metadata,
          updated_at = now()
    """

    processed = 0
    inserted_or_updated = 0

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(select_sql, {"limit": args.limit})
        rows = cur.fetchall()

    for row in rows:
        code_uri = row["entity_rid"]
        kind = row["entity_type"]
        symbol = row.get("entity_name")
        language = row.get("language")
        commit_sha = row.get("commit_sha")
        file_path = row.get("file_path") or ""

        repo_ref = resolve_repo_key(
            github_url=row.get("github_url"),
            repo_fallback=row.get("repo"),
            file_path=file_path,
        )

        metadata = dict(row.get("full_metadata") or {})
        metadata.update(
            {
                "github_url": row.get("github_url"),
                "repo_raw": row.get("repo"),
                "extracted_at": (row.get("extracted_at").isoformat() if row.get("extracted_at") else None),
            }
        )

        payload = {
            "code_uri": code_uri,
            "kind": kind,
            "repo_key": repo_ref.repo_key,
            "file_path": file_path,
            "symbol": symbol,
            "language": language,
            "commit_sha": commit_sha,
            "metadata": Json(metadata),
        }

        processed += 1
        if args.dry_run:
            continue

        with conn.cursor() as cur:
            cur.execute(upsert_sql, payload)
        inserted_or_updated += 1

    if args.dry_run:
        conn.rollback()
        print(f"[export_code_artifacts] dry_run processed={processed} writes=0")
    else:
        conn.commit()
        print(f"[export_code_artifacts] processed={processed} upserted={inserted_or_updated}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

