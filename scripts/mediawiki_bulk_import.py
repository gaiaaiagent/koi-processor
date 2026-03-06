#!/usr/bin/env python3
"""
MediaWiki bulk importer: reads parsed page JSON + manifest, resolves entities
against the KOI knowledge graph, and promotes edges into entity_relationships.

Usage:
    # Dry run
    python scripts/mediawiki_bulk_import.py \
        --parsed-dir data/mediawiki_parsed/ \
        --manifest data/mediawiki_manifest.jsonl \
        --wiki-url https://salishsearestoration.org \
        --dry-run

    # Pilot (top 50 by promotion priority)
    python scripts/mediawiki_bulk_import.py \
        --parsed-dir data/mediawiki_parsed/ \
        --manifest data/mediawiki_manifest.jsonl \
        --wiki-url https://salishsearestoration.org \
        --limit 50 \
        --run-id pilot-2026-03-06

    # Full import
    python scripts/mediawiki_bulk_import.py \
        --parsed-dir data/mediawiki_parsed/ \
        --manifest data/mediawiki_manifest.jsonl \
        --wiki-url https://salishsearestoration.org \
        --run-id full-2026-03-08

Environment variables:
    DB_HOST     (default: localhost)
    DB_PORT     (default: 5432)
    DB_NAME     (required)
    DB_USER     (default: postgres)
    DB_PASSWORD (default: empty string)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import unicodedata
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

# Ensure the koi-processor root is on sys.path so `api.*` is importable.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _PROJECT_ROOT)

from api.mediawiki_parser import PARSER_VERSION, WikiPageParse, StructuralEdge, EditorialEdge
from api.personal_ingest_api import (
    resolve_entity,
    store_new_entity,
    ExtractedEntity,
    CanonicalEntity,
    normalize_entity_text,
)

logger = logging.getLogger("mediawiki_bulk_import")

BATCH_SIZE = 50
HIGH_DEGREE_THRESHOLD = 50

# ---------------------------------------------------------------------------
# Slug helper (matches mediawiki_parse_dump.py)
# ---------------------------------------------------------------------------

_SLUG_UNSAFE_RE = re.compile(r"[^a-z0-9\s-]")
_SLUG_SPACE_RE = re.compile(r"[\s]+")


def _title_to_slug(title: str, max_len: int = 100) -> str:
    s = unicodedata.normalize("NFC", title.strip().lower())
    s = _SLUG_UNSAFE_RE.sub("", s)
    s = _SLUG_SPACE_RE.sub("-", s).strip("-")
    return s[:max_len] if s else "untitled"


# ---------------------------------------------------------------------------
# Manifest reader
# ---------------------------------------------------------------------------

def read_manifest(manifest_path: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Read the manifest JSONL (already sorted by promotion_priority desc)."""
    rows: List[Dict[str, Any]] = []
    with open(manifest_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    if limit is not None and limit > 0:
        rows = rows[:limit]
    return rows


def load_page_json(parsed_dir: str, title: str, slug_seen: Dict[str, int]) -> Optional[Dict[str, Any]]:
    """Load the per-page JSON file matching a title. Handles slug collisions."""
    slug = _title_to_slug(title)
    if slug in slug_seen:
        slug_seen[slug] += 1
        slug = f"{slug}-{slug_seen[slug]}"
    else:
        slug_seen[slug] = 1

    page_path = os.path.join(parsed_dir, f"{slug}.json")
    if not os.path.exists(page_path):
        logger.warning(f"Page JSON not found: {page_path}")
        return None
    with open(page_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def get_db_connection(args: argparse.Namespace) -> asyncpg.Connection:
    # Prefer POSTGRES_URL (used on production servers)
    postgres_url = os.environ.get("POSTGRES_URL")
    if postgres_url:
        return await asyncpg.connect(postgres_url)

    db_host = os.environ.get("DB_HOST", "localhost")
    db_port = int(os.environ.get("DB_PORT", "5432"))
    db_name = os.environ.get("DB_NAME")
    db_user = os.environ.get("DB_USER", "postgres")
    db_password = os.environ.get("DB_PASSWORD", "")

    if not db_name:
        raise RuntimeError("DB_NAME or POSTGRES_URL environment variable is required")

    return await asyncpg.connect(
        host=db_host,
        port=db_port,
        database=db_name,
        user=db_user,
        password=db_password,
    )


async def verify_schema(conn: asyncpg.Connection) -> None:
    """Verify mediawiki_page_state table exists."""
    exists = await conn.fetchval("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'mediawiki_page_state'
        )
    """)
    if not exists:
        raise RuntimeError(
            "mediawiki_page_state table does not exist. "
            "Run migration 063_mediawiki_import.sql first."
        )


async def upsert_wiki(conn: asyncpg.Connection, wiki_url: str) -> int:
    """Upsert wiki in mediawiki_wikis, return wiki_id."""
    # Derive api_url from wiki_url
    api_url = wiki_url.rstrip("/") + "/api.php"
    row = await conn.fetchrow("""
        INSERT INTO mediawiki_wikis (base_url, api_url, wiki_name)
        VALUES ($1, $2, $3)
        ON CONFLICT (base_url) DO UPDATE SET api_url = EXCLUDED.api_url
        RETURNING id
    """, wiki_url.rstrip("/"), api_url, wiki_url.rstrip("/").split("//")[-1])
    return row["id"]


async def create_import_run(
    conn: asyncpg.Connection,
    run_id: str,
    wiki_id: int,
    mode: str,
    page_limit: Optional[int],
) -> int:
    """Create an import run record, return its DB id."""
    row = await conn.fetchrow("""
        INSERT INTO mediawiki_import_runs (run_id, wiki_id, parse_version, mode, page_limit)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (run_id) DO UPDATE SET
            status = 'running',
            started_at = NOW(),
            pages_processed = 0,
            entities_created = 0,
            entities_matched = 0,
            edges_promoted = 0,
            finished_at = NULL
        RETURNING id
    """, run_id, wiki_id, PARSER_VERSION, mode, page_limit)
    return row["id"]


async def finalize_import_run(
    conn: asyncpg.Connection,
    run_id: str,
    pages_processed: int,
    entities_created: int,
    entities_matched: int,
    edges_promoted: int,
    status: str = "completed",
) -> None:
    await conn.execute("""
        UPDATE mediawiki_import_runs
        SET pages_processed = $2,
            entities_created = $3,
            entities_matched = $4,
            edges_promoted = $5,
            status = $6,
            finished_at = NOW()
        WHERE run_id = $1
    """, run_id, pages_processed, entities_created, entities_matched, edges_promoted, status)


# ---------------------------------------------------------------------------
# Page-level import logic
# ---------------------------------------------------------------------------

async def upsert_page_state(
    conn: asyncpg.Connection,
    wiki_id: int,
    page: Dict[str, Any],
) -> Tuple[int, bool]:
    """Upsert mediawiki_page_state. Returns (page_state_id, was_skipped).

    Skipped = content_hash unchanged.
    """
    existing = await conn.fetchrow("""
        SELECT id, content_hash FROM mediawiki_page_state
        WHERE wiki_id = $1 AND page_id = $2
    """, wiki_id, page["page_id"])

    if existing and existing["content_hash"] == page.get("content_hash"):
        return existing["id"], True

    row = await conn.fetchrow("""
        INSERT INTO mediawiki_page_state (
            wiki_id, page_id, title, normalized_title, source_rid,
            namespace, template_type, bkc_entity_type, page_class,
            is_redirect, redirect_target, content_hash, revision_id,
            word_count, wikilink_count, template_field_count,
            entity_density_score, ingest_confidence, promotion_priority,
            parse_version, status, scanned_at
        ) VALUES (
            $1, $2, $3, $4, $5,
            $6, $7, $8, $9,
            $10, $11, $12, $13,
            $14, $15, $16,
            $17, $18, $19,
            $20, 'pending', NOW()
        )
        ON CONFLICT (wiki_id, page_id) DO UPDATE SET
            title = EXCLUDED.title,
            normalized_title = EXCLUDED.normalized_title,
            source_rid = EXCLUDED.source_rid,
            namespace = EXCLUDED.namespace,
            template_type = EXCLUDED.template_type,
            bkc_entity_type = EXCLUDED.bkc_entity_type,
            page_class = EXCLUDED.page_class,
            is_redirect = EXCLUDED.is_redirect,
            redirect_target = EXCLUDED.redirect_target,
            content_hash = EXCLUDED.content_hash,
            revision_id = EXCLUDED.revision_id,
            word_count = EXCLUDED.word_count,
            wikilink_count = EXCLUDED.wikilink_count,
            template_field_count = EXCLUDED.template_field_count,
            entity_density_score = EXCLUDED.entity_density_score,
            ingest_confidence = EXCLUDED.ingest_confidence,
            promotion_priority = EXCLUDED.promotion_priority,
            parse_version = EXCLUDED.parse_version,
            status = 'pending',
            scanned_at = NOW()
        RETURNING id
    """,
        wiki_id,
        page["page_id"],
        page["title"],
        page.get("normalized_title", ""),
        page.get("source_rid", ""),
        page.get("namespace", 0),
        page.get("template_type"),
        page.get("bkc_entity_type"),
        page.get("page_class", "source_only"),
        page.get("is_redirect", False),
        page.get("redirect_target"),
        page.get("content_hash", ""),
        page.get("revision_id", 0),
        page.get("word_count", 0),
        len(page.get("wikilinks", [])),
        len(page.get("template_fields", {})),
        page.get("entity_density_score", 0.0),
        page.get("ingest_confidence", 0.0),
        page.get("promotion_priority", 0.0),
        page.get("parse_version", PARSER_VERSION),
    )
    return row["id"], False


async def store_page_links(
    conn: asyncpg.Connection,
    wiki_id: int,
    page_state_id: int,
    page: Dict[str, Any],
) -> int:
    """Upsert source-native edges into mediawiki_page_links. Returns count."""
    count = 0
    revision_id = page.get("revision_id", 0)

    for se in page.get("structural_edges", []):
        await conn.execute("""
            INSERT INTO mediawiki_page_links (
                wiki_id, source_page_id, target_title, normalized_target_title,
                predicate, edge_class, field_name, confidence,
                source_section, source_revision_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (wiki_id, source_page_id, target_title, predicate,
                         COALESCE(field_name, ''), COALESCE(source_section, ''))
            DO UPDATE SET
                confidence = EXCLUDED.confidence,
                source_revision_id = EXCLUDED.source_revision_id
        """,
            wiki_id,
            page_state_id,
            se["target_title"],
            se["target_title"].lower().strip(),
            se["predicate"],
            "structural",
            se.get("field_name"),
            se["confidence"],
            se.get("source_section"),
            revision_id,
        )
        count += 1

    for ee in page.get("editorial_edges", []):
        await conn.execute("""
            INSERT INTO mediawiki_page_links (
                wiki_id, source_page_id, target_title, normalized_target_title,
                predicate, edge_class, confidence,
                source_section, source_revision_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (wiki_id, source_page_id, target_title, predicate,
                         COALESCE(field_name, ''), COALESCE(source_section, ''))
            DO UPDATE SET
                confidence = EXCLUDED.confidence,
                source_revision_id = EXCLUDED.source_revision_id
        """,
            wiki_id,
            page_state_id,
            ee["target_title"],
            ee["target_title"].lower().strip(),
            "related_to",
            "editorial",
            ee["confidence"],
            ee.get("source_section"),
            revision_id,
        )
        count += 1

    return count


async def register_redirect_alias(
    conn: asyncpg.Connection,
    redirect_title: str,
    target_title: str,
) -> None:
    """If the redirect target resolves to an existing entity, add this title as alias."""
    normalized_alias = normalize_entity_text(redirect_title)
    normalized_target = normalize_entity_text(target_title)

    target_entity = await conn.fetchrow("""
        SELECT fuseki_uri FROM entity_registry
        WHERE normalized_text = $1
        LIMIT 1
    """, normalized_target)

    if target_entity:
        await conn.execute("""
            UPDATE entity_registry
            SET aliases = (
                SELECT ARRAY(
                    SELECT DISTINCT unnest(
                        array_cat(COALESCE(aliases, '{}'), $1::TEXT[])
                    )
                )
            )
            WHERE fuseki_uri = $2
        """, [normalized_alias], target_entity["fuseki_uri"])
        logger.debug(f"Registered alias '{redirect_title}' -> {target_entity['fuseki_uri']}")


async def process_entity_bearing_page(
    conn: asyncpg.Connection,
    page: Dict[str, Any],
    page_state_id: int,
    wiki_id: int,
    run_id: str,
    log_file,
) -> Dict[str, int]:
    """Process an entity_bearing page: resolve entities, promote edges.

    Returns counters dict with entities_created, entities_matched, edges_promoted.
    """
    counters = {"entities_created": 0, "entities_matched": 0, "edges_promoted": 0}
    title = page["title"]
    source_rid = page.get("source_rid", "")
    bkc_type = page.get("bkc_entity_type", "Concept")

    # Count weak edges (tier 3 structural + editorial) for high-degree quarantine
    weak_edge_count = 0
    for se in page.get("structural_edges", []):
        if se["confidence"] < 0.85:
            weak_edge_count += 1
    weak_edge_count += len(page.get("editorial_edges", []))

    if weak_edge_count > HIGH_DEGREE_THRESHOLD:
        logger.warning(
            f"High-degree quarantine: '{title}' has {weak_edge_count} weak edges, "
            f"setting review_status='needs_review'"
        )
        await conn.execute("""
            UPDATE mediawiki_page_state
            SET review_status = 'needs_review',
                status = 'quarantined',
                last_run_id = $2
            WHERE id = $1
        """, page_state_id, run_id)
        _write_log(log_file, source_rid, title, None, None, None,
                    "quarantined", None, run_id)
        return counters

    # Resolve the page's primary entity
    page_entity = ExtractedEntity(name=title, type=bkc_type)
    canonical, is_new = await resolve_entity(conn, page_entity)

    if is_new:
        await store_new_entity(conn, page_entity, canonical, source_rid, source="mediawiki_import")
        counters["entities_created"] += 1
        _write_log(log_file, source_rid, title, None, canonical.uri, canonical.name,
                    "created", None, run_id)
    else:
        counters["entities_matched"] += 1
        _write_log(log_file, source_rid, title,
                    page.get("source_rid"), canonical.uri, canonical.name,
                    "matched", canonical.confidence, run_id)

    subject_uri = canonical.uri

    # Register aliases from the page
    for alias in page.get("aliases", []):
        normalized_alias = normalize_entity_text(alias)
        if normalized_alias:
            await conn.execute("""
                UPDATE entity_registry
                SET aliases = (
                    SELECT ARRAY(
                        SELECT DISTINCT unnest(
                            array_cat(COALESCE(aliases, '{}'), $1::TEXT[])
                        )
                    )
                )
                WHERE fuseki_uri = $2
            """, [normalized_alias], subject_uri)

    # Upsert document_entity_links for the page entity
    await conn.execute("""
        INSERT INTO document_entity_links (document_rid, entity_uri, context)
        VALUES ($1, $2, $3)
        ON CONFLICT (document_rid, entity_uri) DO NOTHING
    """, source_rid, subject_uri, f"Primary entity from wiki page: {title}")

    # Delete existing mediawiki_import edges for this page (idempotent re-import)
    await conn.execute("""
        DELETE FROM entity_relationships
        WHERE source LIKE 'mediawiki_import:%'
        AND source_rid = $1
    """, source_rid)

    # Resolve structural edges and promote to entity_relationships
    for se in page.get("structural_edges", []):
        target_type = se.get("target_type_hint", "Concept") or "Concept"
        target_entity = ExtractedEntity(name=se["target_title"], type=target_type)
        target_canonical, target_is_new = await resolve_entity(conn, target_entity)

        if target_is_new:
            await store_new_entity(
                conn, target_entity, target_canonical, source_rid,
                source="mediawiki_import"
            )
            counters["entities_created"] += 1

        # Check predicate exists in allowed_predicates
        predicate = se["predicate"]
        predicate_ok = await conn.fetchval("""
            SELECT EXISTS (SELECT 1 FROM allowed_predicates WHERE predicate = $1)
        """, predicate)
        if not predicate_ok:
            logger.debug(f"Skipping edge with unknown predicate: {predicate}")
            continue

        # Skip self-referential edges
        if subject_uri == target_canonical.uri:
            continue

        try:
            await conn.execute("""
                INSERT INTO entity_relationships
                    (subject_uri, predicate, object_uri, confidence, source, source_rid)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (subject_uri, predicate, object_uri) DO UPDATE
                SET confidence = GREATEST(entity_relationships.confidence, EXCLUDED.confidence),
                    source = EXCLUDED.source,
                    source_rid = EXCLUDED.source_rid
            """,
                subject_uri,
                predicate,
                target_canonical.uri,
                se["confidence"],
                f"mediawiki_import:{source_rid}",
                source_rid,
            )
            counters["edges_promoted"] += 1
        except Exception as e:
            logger.debug(f"Edge insert failed ({title} -> {se['target_title']}): {e}")

        # Update link resolution status
        await conn.execute("""
            UPDATE mediawiki_page_links
            SET resolution_status = 'resolved',
                resolved_target_uri = $3,
                target_match_confidence = $4
            WHERE source_page_id = $1
            AND target_title = $2
            AND edge_class = 'structural'
        """, page_state_id, se["target_title"], target_canonical.uri, target_canonical.confidence)

        # Upsert document_entity_links for referenced entity
        await conn.execute("""
            INSERT INTO document_entity_links (document_rid, entity_uri, context)
            VALUES ($1, $2, $3)
            ON CONFLICT (document_rid, entity_uri) DO NOTHING
        """, source_rid, target_canonical.uri,
            f"Referenced via {se['predicate']} from {title}")

    # Resolve editorial edges (lower confidence, promote if target resolves to existing)
    for ee in page.get("editorial_edges", []):
        target_entity = ExtractedEntity(name=ee["target_title"], type="Concept")
        target_canonical, target_is_new = await resolve_entity(conn, target_entity)

        if target_is_new:
            # For editorial edges, still create the entity but with lower confidence
            await store_new_entity(
                conn, target_entity, target_canonical, source_rid,
                source="mediawiki_import"
            )
            counters["entities_created"] += 1

        predicate = "related_to"
        if subject_uri == target_canonical.uri:
            continue

        predicate_ok = await conn.fetchval("""
            SELECT EXISTS (SELECT 1 FROM allowed_predicates WHERE predicate = $1)
        """, predicate)
        if not predicate_ok:
            continue

        try:
            await conn.execute("""
                INSERT INTO entity_relationships
                    (subject_uri, predicate, object_uri, confidence, source, source_rid)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (subject_uri, predicate, object_uri) DO UPDATE
                SET confidence = GREATEST(entity_relationships.confidence, EXCLUDED.confidence),
                    source = EXCLUDED.source,
                    source_rid = EXCLUDED.source_rid
            """,
                subject_uri,
                predicate,
                target_canonical.uri,
                ee["confidence"],
                f"mediawiki_import:{source_rid}",
                source_rid,
            )
            counters["edges_promoted"] += 1
        except Exception as e:
            logger.debug(f"Editorial edge insert failed ({title} -> {ee['target_title']}): {e}")

        # Update link resolution
        await conn.execute("""
            UPDATE mediawiki_page_links
            SET resolution_status = 'resolved',
                resolved_target_uri = $3,
                target_match_confidence = $4
            WHERE source_page_id = $1
            AND target_title = $2
            AND edge_class = 'editorial'
        """, page_state_id, ee["target_title"], target_canonical.uri, target_canonical.confidence)

        await conn.execute("""
            INSERT INTO document_entity_links (document_rid, entity_uri, context)
            VALUES ($1, $2, $3)
            ON CONFLICT (document_rid, entity_uri) DO NOTHING
        """, source_rid, target_canonical.uri,
            f"Editorial link from {title}")

    # Update page state with results
    await conn.execute("""
        UPDATE mediawiki_page_state
        SET status = 'ingested',
            entity_uri = $2,
            entities_created = $3,
            relationships_created = $4,
            ingested_at = NOW(),
            last_run_id = $5
        WHERE id = $1
    """, page_state_id, subject_uri,
        counters["entities_created"], counters["edges_promoted"], run_id)

    return counters


def _write_log(
    log_file,
    source_rid: str,
    source_title: str,
    source_url: Optional[str],
    entity_uri: Optional[str],
    entity_name: Optional[str],
    action: str,
    match_tier: Optional[float],
    run_id: str,
) -> None:
    """Append one record to the import log JSONL."""
    if log_file is None:
        return
    record = {
        "source_rid": source_rid,
        "source_title": source_title,
        "source_url": source_url,
        "entity_uri": entity_uri,
        "entity_name": entity_name,
        "action": action,
        "match_tier": match_tier,
        "run_id": run_id,
    }
    log_file.write(json.dumps(record, ensure_ascii=False) + "\n")
    log_file.flush()


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

def dry_run(manifest: List[Dict[str, Any]], parsed_dir: str) -> None:
    """Print what the import would do without touching the DB."""
    entity_bearing = [m for m in manifest if m.get("page_class") == "entity_bearing"
                      and m.get("ingest_confidence", 0) >= 0.6]
    source_only = [m for m in manifest if m.get("page_class") == "source_only"]
    alias_only = [m for m in manifest if m.get("page_class") == "alias_only"]
    below_threshold = [m for m in manifest if m.get("page_class") == "entity_bearing"
                       and m.get("ingest_confidence", 0) < 0.6]

    print(f"=== DRY RUN ===")
    print(f"Parse version: {PARSER_VERSION}")
    print(f"Total pages in manifest: {len(manifest)}")
    print()
    print(f"Will process:")
    print(f"  entity_bearing (conf >= 0.6):  {len(entity_bearing)}")
    print(f"  entity_bearing (conf < 0.6):   {len(below_threshold)}  (skipped)")
    print(f"  source_only:                   {len(source_only)}  (staged)")
    print(f"  alias_only:                    {len(alias_only)}  (redirect aliases)")
    print()

    # Count edges
    slug_seen: Dict[str, int] = {}
    total_structural = 0
    total_editorial = 0
    quarantine_candidates = 0

    for m in manifest:
        page_data = load_page_json(parsed_dir, m["title"], slug_seen)
        if page_data is None:
            continue
        structural = page_data.get("structural_edges", [])
        editorial = page_data.get("editorial_edges", [])
        total_structural += len(structural)
        total_editorial += len(editorial)

        weak = sum(1 for s in structural if s["confidence"] < 0.85) + len(editorial)
        if weak > HIGH_DEGREE_THRESHOLD:
            quarantine_candidates += 1

    print(f"Candidate edges:")
    print(f"  structural:  {total_structural}")
    print(f"  editorial:   {total_editorial}")
    print(f"  total:       {total_structural + total_editorial}")
    print()
    print(f"High-degree quarantine candidates (>{HIGH_DEGREE_THRESHOLD} weak edges): {quarantine_candidates}")
    print()

    if entity_bearing:
        print(f"Top 10 by promotion priority:")
        for m in entity_bearing[:10]:
            print(f"  {m['promotion_priority']:.2f}  {m['title']}")


# ---------------------------------------------------------------------------
# Main async flow
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bulk import parsed MediaWiki pages into KOI knowledge graph."
    )
    parser.add_argument(
        "--parsed-dir", required=True,
        help="Directory containing per-page JSON files.",
    )
    parser.add_argument(
        "--manifest", required=True,
        help="Path to the manifest JSONL file (sorted by promotion_priority).",
    )
    parser.add_argument(
        "--wiki-url", required=True,
        help="Base URL of the wiki (e.g. https://salishsearestoration.org).",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process only the top N pages from manifest.",
    )
    parser.add_argument(
        "--run-id", default=None,
        help="Import run identifier. Required for non-dry-run.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would happen without touching the DB.",
    )
    parser.add_argument(
        "--log-dir", default="data",
        help="Directory for import log JSONL (default: data/).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    )

    # Read manifest
    manifest = read_manifest(args.manifest, args.limit)
    logger.info(f"Loaded {len(manifest)} pages from manifest (limit={args.limit})")

    if args.dry_run:
        dry_run(manifest, args.parsed_dir)
        return

    if not args.run_id:
        logger.error("--run-id is required for non-dry-run imports")
        sys.exit(1)

    # Connect to DB
    conn = await get_db_connection(args)
    try:
        await verify_schema(conn)
        logger.info("Schema verified: mediawiki_page_state table exists")

        # Upsert wiki
        wiki_id = await upsert_wiki(conn, args.wiki_url)
        logger.info(f"Wiki registered: id={wiki_id}, url={args.wiki_url}")

        # Create import run
        mode = f"limit-{args.limit}" if args.limit else "full"
        run_db_id = await create_import_run(conn, args.run_id, wiki_id, mode, args.limit)
        logger.info(f"Import run created: run_id={args.run_id}, db_id={run_db_id}")

        # Open log file
        os.makedirs(args.log_dir, exist_ok=True)
        log_path = os.path.join(args.log_dir, "mediawiki_import_log.jsonl")
        log_file = open(log_path, "a", encoding="utf-8")

        # Process in batches
        totals = {
            "pages_processed": 0,
            "pages_skipped": 0,
            "entities_created": 0,
            "entities_matched": 0,
            "edges_promoted": 0,
            "errors": 0,
        }

        slug_seen: Dict[str, int] = {}
        batches = [manifest[i:i + BATCH_SIZE] for i in range(0, len(manifest), BATCH_SIZE)]

        for batch_idx, batch in enumerate(batches):
            logger.info(f"Batch {batch_idx + 1}/{len(batches)} ({len(batch)} pages)")

            tx = conn.transaction()
            await tx.start()
            try:
                for manifest_row in batch:
                    title = manifest_row["title"]
                    page_data = load_page_json(args.parsed_dir, title, slug_seen)
                    if page_data is None:
                        logger.warning(f"Skipping '{title}': page JSON not found")
                        totals["errors"] += 1
                        continue

                    page_class = page_data.get("page_class", "source_only")
                    ingest_confidence = page_data.get("ingest_confidence", 0.0)

                    # Upsert page state
                    page_state_id, was_skipped = await upsert_page_state(
                        conn, wiki_id, page_data
                    )

                    if was_skipped:
                        totals["pages_skipped"] += 1
                        logger.debug(f"Skipped (unchanged): {title}")
                        continue

                    # Store source-native links
                    link_count = await store_page_links(conn, wiki_id, page_state_id, page_data)
                    logger.debug(f"Stored {link_count} page links for '{title}'")

                    # Route by page class
                    if page_class == "entity_bearing" and ingest_confidence >= 0.6:
                        page_counters = await process_entity_bearing_page(
                            conn, page_data, page_state_id, wiki_id, args.run_id, log_file,
                        )
                        totals["entities_created"] += page_counters["entities_created"]
                        totals["entities_matched"] += page_counters["entities_matched"]
                        totals["edges_promoted"] += page_counters["edges_promoted"]

                    elif page_class == "source_only":
                        await conn.execute("""
                            UPDATE mediawiki_page_state
                            SET status = 'staged', last_run_id = $2
                            WHERE id = $1
                        """, page_state_id, args.run_id)
                        _write_log(log_file, page_data.get("source_rid", ""),
                                   title, None, None, None, "staged", None, args.run_id)

                    elif page_class == "alias_only":
                        redirect_target = page_data.get("redirect_target")
                        if redirect_target:
                            await register_redirect_alias(conn, title, redirect_target)
                        await conn.execute("""
                            UPDATE mediawiki_page_state
                            SET status = 'skipped', last_run_id = $2
                            WHERE id = $1
                        """, page_state_id, args.run_id)
                        _write_log(log_file, page_data.get("source_rid", ""),
                                   title, None, None, None, "alias_registered", None, args.run_id)

                    else:
                        # entity_bearing but below threshold
                        await conn.execute("""
                            UPDATE mediawiki_page_state
                            SET status = 'staged', last_run_id = $2
                            WHERE id = $1
                        """, page_state_id, args.run_id)

                    totals["pages_processed"] += 1

                await tx.commit()
                logger.info(
                    f"  Batch {batch_idx + 1} committed: "
                    f"+{sum(1 for _ in batch)} pages"
                )

            except Exception as e:
                await tx.rollback()
                totals["errors"] += len(batch)
                logger.error(f"  Batch {batch_idx + 1} ROLLED BACK: {e}")

        log_file.close()

        # Finalize run
        status = "completed" if totals["errors"] == 0 else "completed_with_errors"
        await finalize_import_run(
            conn, args.run_id,
            totals["pages_processed"],
            totals["entities_created"],
            totals["entities_matched"],
            totals["edges_promoted"],
            status,
        )

        # Print summary
        print()
        print(f"=== Import Complete ===")
        print(f"Run ID:            {args.run_id}")
        print(f"Status:            {status}")
        print(f"Pages processed:   {totals['pages_processed']}")
        print(f"Pages skipped:     {totals['pages_skipped']}  (content_hash unchanged)")
        print(f"Entities created:  {totals['entities_created']}")
        print(f"Entities matched:  {totals['entities_matched']}")
        print(f"Edges promoted:    {totals['edges_promoted']}")
        print(f"Errors:            {totals['errors']}")
        print(f"Import log:        {log_path}")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
