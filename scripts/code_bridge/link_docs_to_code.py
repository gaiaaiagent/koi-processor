#!/usr/bin/env python3
"""
Link doc mentions to code artifacts (koi_doc_code_links).

This script scans docs in koi_memories (natural-language corpus) and creates
high-precision links to code artifacts in koi_code_artifacts. It uses simple
regex-based matching to avoid noisy links.

Strategy (high precision):
  - Link code symbols when the symbol is globally unique OR uniquely matched
    within a detected repo context.
  - Link file paths only when a full org/repo path is present and the file path
    maps to a single artifact.

Usage:
  cd /opt/projects/koi-processor
  PYTHONPATH=src ./.venv/bin/python scripts/code_bridge/link_docs_to_code.py --dry-run
  PYTHONPATH=src ./.venv/bin/python scripts/code_bridge/link_docs_to_code.py

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
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import psycopg2
from psycopg2.extras import RealDictCursor, Json


# Stage 6 corpus filter: natural-language KG only
# - Include all non-repo sources
# - Repo sources (GitHub/GitLab): include ONLY documentation files by file_path
# - Exclude file_path IS NULL rows for repo sources
CORPUS_FILTER_SQL = r"""
  AND (
    (source_sensor NOT ILIKE '%%github%%' AND source_sensor NOT ILIKE '%%gitlab%%')
    OR
    (
      (source_sensor ILIKE '%%github%%' OR source_sensor ILIKE '%%gitlab%%')
      AND (metadata ? 'file_path')
      AND (metadata->>'file_path') IS NOT NULL
      AND (
        (metadata->>'file_path') ~* '[.](md|mdx|rst|txt)$'
        OR (metadata->>'file_path') ~* '(^|/)(readme|license|changelog)([.].*)?$'
        OR (metadata->>'file_path') ILIKE '%%/docs/%%'
      )
      AND (metadata->>'file_path') NOT ILIKE '%%.pb.go'
      AND (metadata->>'file_path') !~* '/(node_modules|vendor|dist|build|generated)/'
      AND (metadata->>'file_path') !~* '/(test|tests|examples)/'
      AND (metadata->>'file_path') !~* '_test[.][^/]+$'
    )
  )
"""


SYMBOL_RE = re.compile(r"\b[A-Z][A-Za-z0-9_]{2,}\b")
GITHUB_URL_RE = re.compile(r"https?://github\.com/[^)\s]+")
FILE_PATH_RE = re.compile(
    r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+\.(?:go|py|ts|tsx|js|proto|rs|java|kt|cs|cpp|h|hpp))\b"
)

SYMBOL_STOPLIST = {
    "Main",
    "Init",
    "Config",
    "Server",
    "Client",
    "Request",
    "Response",
    "Context",
    "Manager",
    "Handler",
    "Provider",
    "Factory",
    "Builder",
    "Service",
    "Module",
    "Runtime",
    "Error",
    "Errors",
    "Result",
    "State",
    "Status",
    "Message",
    "Messages",
    "Keeper",
}


@dataclass(frozen=True)
class Artifact:
    code_uri: str
    kind: str
    repo_key: str
    file_path: str
    symbol: Optional[str]
    language: Optional[str]
    commit_sha: Optional[str]


def _repo_parts(repo_key: str) -> Tuple[str, str]:
    # repo_key: host/org/repo
    parts = repo_key.split("/")
    if len(parts) >= 3:
        return parts[-2], parts[-1]
    if len(parts) == 2:
        return parts[0], parts[1]
    return "", repo_key


def _parse_repo_key_from_github_url(url: str) -> Optional[str]:
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if parsed.netloc.lower() != "github.com":
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    org, repo = parts[0], parts[1]
    return f"github.com/{org}/{repo}"


def _parse_file_path_from_github_url(url: str) -> Optional[str]:
    # https://github.com/org/repo/blob/<sha>/path
    # https://github.com/org/repo/tree/<sha>/path
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if parsed.netloc.lower() != "github.com":
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 5:
        return None
    org, repo, blob_or_tree = parts[0], parts[1], parts[2]
    if blob_or_tree not in {"blob", "tree"}:
        return None
    file_path = "/".join(parts[4:])
    return f"{org}/{repo}/{file_path}"


def _extract_repo_context(text: str) -> Set[str]:
    repos = set()
    for url in GITHUB_URL_RE.findall(text):
        repo_key = _parse_repo_key_from_github_url(url)
        if repo_key:
            repos.add(repo_key)
    return repos


def _extract_file_paths(text: str) -> Set[str]:
    paths = set()
    for path in FILE_PATH_RE.findall(text):
        paths.add(path)
    for url in GITHUB_URL_RE.findall(text):
        path = _parse_file_path_from_github_url(url)
        if path:
            paths.add(path)
    return paths


def _symbol_candidates(text: str, min_len: int) -> Set[str]:
    symbols = set()
    for sym in SYMBOL_RE.findall(text):
        if len(sym) < min_len:
            continue
        if sym in SYMBOL_STOPLIST:
            continue
        symbols.add(sym)
    return symbols


def load_artifacts(conn) -> Tuple[Dict[str, List[Artifact]], Dict[str, Dict[str, List[Artifact]]], Dict[str, List[Artifact]]]:
    symbol_index: Dict[str, List[Artifact]] = defaultdict(list)
    symbol_repo_index: Dict[str, Dict[str, List[Artifact]]] = defaultdict(lambda: defaultdict(list))
    file_path_index: Dict[str, List[Artifact]] = defaultdict(list)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT code_uri, kind, repo_key, file_path, symbol, language, commit_sha
            FROM koi_code_artifacts
            WHERE symbol IS NOT NULL
              AND file_path IS NOT NULL
              AND repo_key IS NOT NULL
            """
        )
        for row in cur.fetchall():
            artifact = Artifact(
                code_uri=row["code_uri"],
                kind=row["kind"],
                repo_key=row["repo_key"],
                file_path=row["file_path"],
                symbol=row["symbol"],
                language=row.get("language"),
                commit_sha=row.get("commit_sha"),
            )
            symbol_index[artifact.symbol].append(artifact)
            symbol_repo_index[artifact.symbol][artifact.repo_key].append(artifact)
            file_path_index[artifact.file_path].append(artifact)

    return symbol_index, symbol_repo_index, file_path_index


def resolve_symbol_links(
    symbol: str,
    symbol_index: Dict[str, List[Artifact]],
    symbol_repo_index: Dict[str, Dict[str, List[Artifact]]],
    repo_context: Set[str],
    max_symbol_cardinality: int,
) -> List[Artifact]:
    artifacts = symbol_index.get(symbol, [])
    if not artifacts:
        return []

    if len(artifacts) == 1:
        return artifacts

    if len(artifacts) > max_symbol_cardinality:
        return []

    if repo_context:
        # Narrow down by repo context
        for repo_key in repo_context:
            candidates = symbol_repo_index.get(symbol, {}).get(repo_key, [])
            if len(candidates) == 1:
                return candidates

    return []


def resolve_file_links(
    file_path: str,
    file_path_index: Dict[str, List[Artifact]],
) -> List[Artifact]:
    artifacts = file_path_index.get(file_path, [])
    if len(artifacts) == 1:
        return artifacts
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Link docs to code artifacts")
    parser.add_argument("--dry-run", action="store_true", help="No DB writes")
    parser.add_argument("--limit", type=int, default=0, help="Max docs to process (0 = all)")
    parser.add_argument("--batch-size", type=int, default=200, help="Docs per batch")
    parser.add_argument("--min-symbol-length", type=int, default=4, help="Min symbol length to consider")
    parser.add_argument("--max-symbol-cardinality", type=int, default=5, help="Max artifacts per symbol allowed")
    args = parser.parse_args()

    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5433")),
        database=os.getenv("POSTGRES_DB", "eliza"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )
    conn.autocommit = False

    symbol_index, symbol_repo_index, file_path_index = load_artifacts(conn)

    total_docs = 0
    total_links = 0
    total_symbols = 0
    total_file_links = 0
    last_id = None

    insert_sql = """
        INSERT INTO koi_doc_code_links
          (memory_rid, code_uri, mention_text, confidence, metadata)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """

    try:
        while True:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                limit = args.batch_size
                if args.limit and total_docs >= args.limit:
                    break

                if args.limit:
                    limit = min(args.batch_size, args.limit - total_docs)

                if last_id is None:
                    cur.execute(
                        f"""
                        SELECT id, rid, source_sensor, content->>'text' AS text
                        FROM koi_memories
                        WHERE superseded_at IS NULL
                          AND content->>'text' IS NOT NULL
                          AND LENGTH(content->>'text') > 50
                          {CORPUS_FILTER_SQL}
                        ORDER BY id ASC
                        LIMIT %s
                        """,
                        (limit,),
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT id, rid, source_sensor, content->>'text' AS text
                        FROM koi_memories
                        WHERE superseded_at IS NULL
                          AND content->>'text' IS NOT NULL
                          AND LENGTH(content->>'text') > 50
                          AND id > %s
                          {CORPUS_FILTER_SQL}
                        ORDER BY id ASC
                        LIMIT %s
                        """,
                        (last_id, limit),
                    )
                docs = cur.fetchall()

            if not docs:
                break

            for doc in docs:
                total_docs += 1
                last_id = doc["id"]
                text = doc.get("text") or ""

                repo_context = _extract_repo_context(text)

                symbols = _symbol_candidates(text, args.min_symbol_length)
                total_symbols += len(symbols)

                file_paths = _extract_file_paths(text)

                link_rows = []

                for symbol in symbols:
                    artifacts = resolve_symbol_links(
                        symbol=symbol,
                        symbol_index=symbol_index,
                        symbol_repo_index=symbol_repo_index,
                        repo_context=repo_context,
                        max_symbol_cardinality=args.max_symbol_cardinality,
                    )
                    if not artifacts:
                        continue

                    for artifact in artifacts:
                        meta = {
                            "match_type": "symbol",
                            "symbol": symbol,
                            "repo_key": artifact.repo_key,
                            "file_path": artifact.file_path,
                            "source_sensor": doc.get("source_sensor"),
                        }
                        link_rows.append(
                            (
                                doc["rid"],
                                artifact.code_uri,
                                symbol,
                                0.85,
                                Json(meta),
                            )
                        )

                for file_path in file_paths:
                    artifacts = resolve_file_links(file_path, file_path_index)
                    if not artifacts:
                        continue
                    total_file_links += 1
                    for artifact in artifacts:
                        meta = {
                            "match_type": "file_path_unique",
                            "file_path": file_path,
                            "repo_key": artifact.repo_key,
                            "source_sensor": doc.get("source_sensor"),
                        }
                        link_rows.append(
                            (
                                doc["rid"],
                                artifact.code_uri,
                                file_path,
                                0.8,
                                Json(meta),
                            )
                        )

                if not link_rows:
                    continue

                total_links += len(link_rows)

                if not args.dry_run:
                    with conn.cursor() as cur:
                        cur.executemany(insert_sql, link_rows)

            if not args.dry_run:
                conn.commit()

    except Exception:
        conn.rollback()
        raise
    finally:
        if args.dry_run:
            conn.rollback()
        conn.close()

    print(f"[link_docs_to_code] docs={total_docs} links={total_links} symbol_mentions={total_symbols} file_links={total_file_links} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
