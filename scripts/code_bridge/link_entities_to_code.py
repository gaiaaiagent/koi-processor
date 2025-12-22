#!/usr/bin/env python3
"""
Link semantic entities to code artifacts by writing code_uri into entity_registry.metadata.

This is the entity-level bridge that runs AFTER Stage 6, once MODULE/KEEPER/API_MESSAGE
entities are available.

High-precision approach:
  - Prefer exact or normalized symbol matches
  - For MODULE, use module-path inference from code artifact file_path
  - Skip ambiguous matches

Usage:
  cd /opt/projects/koi-processor
  PYTHONPATH=src ./.venv/bin/python scripts/code_bridge/link_entities_to_code.py --dry-run
  PYTHONPATH=src ./.venv/bin/python scripts/code_bridge/link_entities_to_code.py --types MODULE,KEEPER,API_MESSAGE

Environment:
  POSTGRES_HOST (default: localhost)
  POSTGRES_PORT (default: 5433)
  POSTGRES_DB   (default: eliza)
  POSTGRES_USER (default: postgres)
  POSTGRES_PASSWORD (default: postgres)
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor


TYPE_DEFAULTS = ("MODULE", "KEEPER", "API_MESSAGE")
TYPE_TO_KINDS = {
    "MODULE": {"Keeper", "Message", "Struct", "Interface", "Function"},
    "KEEPER": {"Keeper"},
    "API_MESSAGE": {"Message", "Struct"},
}

MODULE_STOPLIST = {
    "module",
    "modules",
    "core",
    "base",
    "types",
    "client",
    "keeper",
    "msg",
    "messages",
    "api",
    "proto",
}


@dataclass(frozen=True)
class Artifact:
    code_uri: str
    kind: str
    repo_key: str
    file_path: str
    symbol: Optional[str]
    commit_sha: Optional[str]


def normalize_symbol(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def normalize_entity_name(text: str, entity_type: str) -> str:
    if not text:
        return ""
    s = text.lower().strip()
    s = re.sub(r"\s*(module|keeper|message|handler|service)$", "", s, flags=re.I)
    s = re.sub(r"[^a-z0-9]", "", s)
    if entity_type == "MODULE" and s in MODULE_STOPLIST:
        return ""
    return s


def extract_module_slug(file_path: str) -> Optional[str]:
    if not file_path:
        return None
    match = re.search(r"/x/([^/]+)/", file_path)
    if match:
        return match.group(1).lower()
    match = re.search(r"/api/regen/([^/]+)/", file_path)
    if match:
        return match.group(1).lower()
    return None


def load_aliases(path: Optional[str]) -> Dict[str, str]:
    if not path:
        return {}
    with open(path, "r") as f:
        data = json.load(f)
    return {normalize_symbol(k): normalize_symbol(v) for k, v in data.items()}


def load_artifacts(conn) -> Tuple[Dict[str, List[Artifact]], Dict[str, List[Artifact]]]:
    symbol_index: Dict[str, List[Artifact]] = defaultdict(list)
    module_index: Dict[str, List[Artifact]] = defaultdict(list)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT code_uri, kind, repo_key, file_path, symbol, commit_sha
            FROM koi_code_artifacts
            WHERE file_path IS NOT NULL
              AND repo_key IS NOT NULL
            """
        )
        for row in cur.fetchall():
            artifact = Artifact(
                code_uri=row["code_uri"],
                kind=row["kind"],
                repo_key=row["repo_key"],
                file_path=row["file_path"],
                symbol=row.get("symbol"),
                commit_sha=row.get("commit_sha"),
            )
            if artifact.symbol:
                key = normalize_symbol(artifact.symbol)
                if key:
                    symbol_index[key].append(artifact)
            slug = extract_module_slug(artifact.file_path)
            if slug:
                module_index[slug].append(artifact)

    return symbol_index, module_index


def choose_artifact_by_kind(artifacts: List[Artifact], allowed_kinds: Optional[set]) -> Optional[Artifact]:
    if not artifacts:
        return None
    if allowed_kinds:
        artifacts = [a for a in artifacts if a.kind in allowed_kinds]
    if not artifacts:
        return None
    # Prefer Keeper/Message over generic kinds
    priority = {"Keeper": 0, "Message": 1, "Struct": 2, "Interface": 3, "Function": 4}
    artifacts.sort(key=lambda a: priority.get(a.kind, 99))
    return artifacts[0]


def resolve_symbol_match(
    entity_name: str,
    symbol_index: Dict[str, List[Artifact]],
    allowed_kinds: Optional[set],
    max_cardinality: int,
) -> Tuple[Optional[Artifact], float, str]:
    key = normalize_symbol(entity_name)
    if not key:
        return None, 0.0, "no_symbol"
    artifacts = symbol_index.get(key, [])
    if not artifacts:
        return None, 0.0, "no_match"
    if len(artifacts) == 1:
        return choose_artifact_by_kind(artifacts, allowed_kinds), 1.0, "symbol_exact"
    if len(artifacts) > max_cardinality:
        return None, 0.0, "symbol_ambiguous"
    # Try to resolve by kind
    candidate = choose_artifact_by_kind(artifacts, allowed_kinds)
    if candidate:
        return candidate, 0.8, "symbol_normalized"
    return None, 0.0, "symbol_ambiguous"


def resolve_module_match(
    entity_name: str,
    module_index: Dict[str, List[Artifact]],
    allowed_kinds: Optional[set],
) -> Tuple[Optional[Artifact], float, str]:
    slug = normalize_entity_name(entity_name, "MODULE")
    if not slug:
        return None, 0.0, "no_module_slug"
    candidates = module_index.get(slug, [])
    if not candidates:
        return None, 0.0, "module_no_match"
    # Prefer a deterministic artifact within the module
    candidate = choose_artifact_by_kind(candidates, allowed_kinds)
    if candidate:
        return candidate, 0.7, "module_path"
    return None, 0.0, "module_ambiguous"


def update_entity_metadata(cur, entity_id: int, artifact: Artifact, confidence: float, method: str, run_id: str):
    cur.execute(
        """
        UPDATE entity_registry
        SET metadata = jsonb_set(
            jsonb_set(
                jsonb_set(
                    jsonb_set(
                        jsonb_set(metadata, '{code_uri}', to_jsonb(%s::text), true),
                        '{link_confidence}', to_jsonb(%s::float), true
                    ),
                    '{link_method}', to_jsonb(%s::text), true
                ),
                '{link_run_id}', to_jsonb(%s::text), true
            ),
            '{code_repo_key}', to_jsonb(%s::text), true
        )
        WHERE id = %s
        """,
        (
            artifact.code_uri,
            confidence,
            method,
            run_id,
            artifact.repo_key,
            entity_id,
        ),
    )
    # Optional extras
    cur.execute(
        """
        UPDATE entity_registry
        SET metadata = jsonb_set(
            jsonb_set(
                jsonb_set(metadata, '{code_file_path}', to_jsonb(%s::text), true),
                '{code_symbol}', to_jsonb(%s::text), true
            ),
            '{code_kind}', to_jsonb(%s::text), true
        )
        WHERE id = %s
        """,
        (
            artifact.file_path,
            artifact.symbol or "",
            artifact.kind,
            entity_id,
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Link entities to code artifacts")
    parser.add_argument("--types", type=str, default=",".join(TYPE_DEFAULTS), help="Comma-separated entity types")
    parser.add_argument("--min-occurrence", type=int, default=1, help="Minimum occurrence_count")
    parser.add_argument("--max-symbol-cardinality", type=int, default=5, help="Max artifacts per symbol")
    parser.add_argument("--limit", type=int, default=0, help="Limit entities processed")
    parser.add_argument("--dry-run", action="store_true", help="No DB writes")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing code_uri")
    parser.add_argument("--alias-file", type=str, default="", help="JSON alias map for module normalization")
    args = parser.parse_args()

    types = [t.strip().upper() for t in args.types.split(",") if t.strip()]
    aliases = load_aliases(args.alias_file)

    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5433")),
        database=os.getenv("POSTGRES_DB", "eliza"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )
    conn.autocommit = False

    symbol_index, module_index = load_artifacts(conn)

    run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    totals = Counter()
    by_type = Counter()
    by_method = Counter()

    query = """
        SELECT id, entity_text, entity_type, occurrence_count, metadata
        FROM entity_registry
        WHERE entity_type = ANY(%s)
          AND occurrence_count >= %s
    """
    if not args.overwrite:
        query += " AND (metadata->>'code_uri') IS NULL"
    if args.limit and args.limit > 0:
        query += " LIMIT %s"

    params = [types, args.min_occurrence]
    if args.limit and args.limit > 0:
        params.append(args.limit)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query, params)
        rows = cur.fetchall()

    for row in rows:
        entity_id = row["id"]
        name = row["entity_text"] or ""
        etype = row["entity_type"]
        allowed_kinds = TYPE_TO_KINDS.get(etype)

        artifact = None
        confidence = 0.0
        method = "no_match"

        # Tier 1: symbol match
        artifact, confidence, method = resolve_symbol_match(
            entity_name=name,
            symbol_index=symbol_index,
            allowed_kinds=allowed_kinds,
            max_cardinality=args.max_symbol_cardinality,
        )

        # Tier 2: module path inference
        if not artifact and etype == "MODULE":
            artifact, confidence, method = resolve_module_match(
                entity_name=name,
                module_index=module_index,
                allowed_kinds=allowed_kinds,
            )

        # Tier 3: alias normalization (MODULE only)
        if not artifact and etype == "MODULE" and aliases:
            normalized = normalize_entity_name(name, "MODULE")
            alias = aliases.get(normalized)
            if alias:
                alias_candidates = module_index.get(alias, [])
                candidate = choose_artifact_by_kind(alias_candidates, allowed_kinds)
                if candidate:
                    artifact = candidate
                    confidence = 0.6
                    method = "alias_match"

        totals["processed"] += 1

        if not artifact:
            totals["unlinked"] += 1
            by_method[method] += 1
            continue

        totals["linked"] += 1
        by_type[etype] += 1
        by_method[method] += 1

        if args.dry_run:
            continue

        with conn.cursor() as cur:
            update_entity_metadata(cur, entity_id, artifact, confidence, method, run_id)

    if args.dry_run:
        conn.rollback()
    else:
        conn.commit()

    conn.close()

    print("[link_entities_to_code] run_id={}".format(run_id))
    print("[link_entities_to_code] processed={} linked={} unlinked={}".format(
        totals["processed"], totals["linked"], totals["unlinked"]
    ))
    if by_type:
        print("[link_entities_to_code] linked_by_type={}".format(dict(by_type)))
    if by_method:
        print("[link_entities_to_code] method_counts={}".format(dict(by_method)))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
