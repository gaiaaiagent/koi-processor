#!/usr/bin/env python3
"""
Backfill entity_registry from koi_entity_chunk_links + canonicalize chunk_link URIs.

Phase 2 of overnight Option C (2026-05-06).

Reads DISTINCT (entity_name_lower, entity_type) pairs from koi_entity_chunk_links,
runs each through the existing EntityResolver waterfall (Tier 1 exact -> 1.5
canonical -> 1.x fuzzy -> 2 semantic -> 3 create), then:

  1. Sets node_private=true on the resolved entity_registry row IF any chunk_link
     for that pair has document_rid -> koi_memories with is_private=true (OR
     aggregate, per handoff hard-stop).
  2. UPDATEs koi_entity_chunk_links.entity_uri to the canonical fuseki_uri for
     pairs where the canonical URI differs from the existing URI.

Resumable via /opt/projects/koi-processor/batch_state/option-b-backfill.json.
Privacy hard-stop wired in: if any private cohort entity ends with node_private=
false in the registry, abort + log.

Usage:
    python3 scripts/backfill_entity_registry_from_chunk_links.py [--dry-run] [--limit N]
"""

import sys
import os
import json
import time
import logging
import argparse
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import psycopg2
from psycopg2.extras import RealDictCursor

load_dotenv(Path(__file__).parent.parent / ".env")

from knowledge_graph.entity_resolver import EntityResolver

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/opt/projects/koi-processor/batch_state/option-b-backfill.log', mode='a'),
    ],
)
logger = logging.getLogger(__name__)

STATE_FILE = Path('/opt/projects/koi-processor/batch_state/option-b-backfill.json')


def get_db_config() -> Dict[str, Any]:
    return {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": int(os.getenv("POSTGRES_PORT", 5433)),
        "database": os.getenv("POSTGRES_DB", "eliza"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
    }


def load_state() -> Dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            logger.warning("State file corrupt, starting fresh")
    return {
        'processed_pairs': [],   # list of [name_lower, type] keys already done
        'errored_pairs': [],      # list of {name_lower, type, error}
        'mappings': {},           # "name_lower::type" -> {fuseki_uri, canonical_text, was_new, match_method, score}
        'started_at': None,
        'finished_at': None,
        'tier_stats': {},
    }


def save_state(s: Dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(s, indent=2, default=str))
    tmp.replace(STATE_FILE)


def fetch_pairs_with_privacy(db_config: Dict[str, Any], limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Fetch DISTINCT (entity_name_lower, entity_type) with sample name + privacy aggregate.

    Returns list of {name_lower, name_canonical, type, is_private_or, link_count}.
    """
    logger.info("Fetching distinct (name_lower, type) pairs with privacy aggregate...")
    conn = psycopg2.connect(**db_config)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # The MAX(entity_name) gives a deterministic-ish canonical surface form (lexicographic).
        # is_private_or = true if any chunk_link for this pair traces to a private memory.
        sql = """
            SELECT
              l.entity_name_lower AS name_lower,
              l.entity_type AS type,
              MAX(l.entity_name) AS name_canonical,
              BOOL_OR(COALESCE(m.is_private, FALSE)) AS is_private_or,
              COUNT(*) AS link_count
            FROM koi_entity_chunk_links l
            LEFT JOIN koi_memories m ON m.rid = l.document_rid
            WHERE l.entity_name_lower IS NOT NULL
              AND l.entity_type IS NOT NULL
              AND length(l.entity_name_lower) > 0
            GROUP BY l.entity_name_lower, l.entity_type
            ORDER BY link_count DESC
        """
        if limit:
            sql += f" LIMIT {int(limit)}"
        cur.execute(sql)
        rows = cur.fetchall()
        logger.info(f"Fetched {len(rows)} distinct (name_lower, type) pairs")
        return [dict(r) for r in rows]
    finally:
        cur.close()
        conn.close()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--limit', type=int, default=None)
    p.add_argument('--checkpoint-every', type=int, default=200)
    p.add_argument('--rewrite-only', action='store_true',
                   help='Skip resolver; just rewrite chunk_links using already-saved mappings')
    p.add_argument('--skip-rewrite', action='store_true',
                   help='Run resolver but do not rewrite chunk_links (pause at canonical-mapping stage)')
    args = p.parse_args()

    db_config = get_db_config()
    state = load_state()
    if not state.get('started_at'):
        state['started_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    save_state(state)

    if not args.rewrite_only:
        # Build resolver + persistent privacy-update connection
        if args.dry_run:
            resolver = None
            priv_conn = None
            logger.info("DRY RUN — skipping resolver, will only fetch + log")
        else:
            resolver = EntityResolver(db_config=db_config)
            priv_conn = psycopg2.connect(**db_config)
            priv_conn.autocommit = True
            logger.info("EntityResolver initialized; privacy-update connection ready")

        pairs = fetch_pairs_with_privacy(db_config, args.limit)

        processed_set = set(tuple(p) for p in state['processed_pairs'])
        queue = [r for r in pairs if (r['name_lower'], r['type']) not in processed_set]
        logger.info(f"Total: {len(pairs)}, processed: {len(processed_set)}, queue: {len(queue)}")

        for i, pair in enumerate(queue):
            name_lower = pair['name_lower']
            entity_type = pair['type']
            name_canonical = pair['name_canonical'] or name_lower
            is_private = bool(pair['is_private_or'])
            try:
                if args.dry_run:
                    # Log resolution intent
                    logger.info(f"DRY: would resolve '{name_canonical}' ({entity_type}) "
                                f"private={is_private} link_count={pair['link_count']}")
                    state['processed_pairs'].append([name_lower, entity_type])
                else:
                    res = resolver.get_or_create_entity(
                        entity_text=name_canonical,
                        entity_type=entity_type,
                        metadata={
                            'backfill_source': 'chunk_links_canonicalization_2026-05-06',
                            'name_lower_input': name_lower,
                            'is_private_chunk_link': is_private,
                        },
                    )
                    canonical_uri = res['uri']
                    method = res.get('match_method', 'unknown')
                    score = res.get('match_score', 0.0)

                    state['tier_stats'][method] = state['tier_stats'].get(method, 0) + 1
                    state['mappings'][f"{name_lower}::{entity_type}"] = {
                        'fuseki_uri': canonical_uri,
                        'canonical_text': res.get('entity_text', name_canonical),
                        'was_new': not res.get('matched', False),
                        'match_method': method,
                        'score': float(score),
                        'is_private': is_private,
                    }

                    # Privacy propagation: if private, OR-set node_private=true on the row
                    if is_private and priv_conn is not None:
                        with priv_conn.cursor() as c:
                            c.execute(
                                "UPDATE entity_registry SET node_private = TRUE WHERE fuseki_uri = %s AND node_private = FALSE",
                                (canonical_uri,),
                            )

                    state['processed_pairs'].append([name_lower, entity_type])

                if (i + 1) % args.checkpoint_every == 0:
                    save_state(state)
                    logger.info(f"Progress: {len(state['processed_pairs'])}/{len(pairs)} | tier_stats={state.get('tier_stats')}")
            except Exception as e:
                logger.exception(f"Error on '{name_canonical}' ({entity_type}): {e}")
                state['errored_pairs'].append({
                    'name_lower': name_lower,
                    'type': entity_type,
                    'error': str(e),
                })
                save_state(state)

        save_state(state)
        if priv_conn is not None:
            priv_conn.close()
        logger.info(f"Resolver pass complete. Processed: {len(state['processed_pairs'])}, Errored: {len(state['errored_pairs'])}")
        logger.info(f"Tier stats: {state.get('tier_stats')}")

    if args.dry_run or args.skip_rewrite:
        logger.info("Stopping before chunk_link rewrite")
        return 0

    # ----- Rewrite chunk_links via temp mapping table -----
    logger.info("Building canonical mapping temp table for chunk_link rewrite...")
    conn = psycopg2.connect(**db_config)
    try:
        with conn.cursor() as c:
            c.execute("DROP TABLE IF EXISTS tmp_canonical_mapping;")
            c.execute("""
                CREATE TEMP TABLE tmp_canonical_mapping (
                  name_lower TEXT,
                  entity_type TEXT,
                  fuseki_uri TEXT,
                  PRIMARY KEY (name_lower, entity_type)
                );
            """)
            rows = []
            for key, m in state['mappings'].items():
                name_lower, entity_type = key.split('::', 1)
                rows.append((name_lower, entity_type, m['fuseki_uri']))
            from psycopg2.extras import execute_values
            execute_values(
                c,
                "INSERT INTO tmp_canonical_mapping (name_lower, entity_type, fuseki_uri) VALUES %s ON CONFLICT DO NOTHING",
                rows,
                page_size=1000,
            )
            c.execute("ANALYZE tmp_canonical_mapping;")
            c.execute("SELECT COUNT(*) FROM tmp_canonical_mapping;")
            n = c.fetchone()[0]
            logger.info(f"Inserted {n} canonical mappings into temp table")

            # Update chunk_links: set entity_uri = canonical, only where it differs
            t0 = time.time()
            c.execute("""
                UPDATE koi_entity_chunk_links l
                SET entity_uri = m.fuseki_uri
                FROM tmp_canonical_mapping m
                WHERE l.entity_name_lower = m.name_lower
                  AND l.entity_type = m.entity_type
                  AND (l.entity_uri IS NULL OR l.entity_uri <> m.fuseki_uri);
            """)
            updated = c.rowcount
            conn.commit()
            elapsed = time.time() - t0
            logger.info(f"Rewrote {updated} chunk_link rows in {elapsed:.1f}s")
            state['rewrite_count'] = updated
            state['rewrite_seconds'] = elapsed
    finally:
        conn.close()

    state['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    save_state(state)

    # ----- Privacy hard-stop check -----
    logger.info("Running privacy hard-stop check...")
    conn = psycopg2.connect(**db_config)
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT COUNT(*) FROM (
                  SELECT DISTINCT er.fuseki_uri
                  FROM entity_registry er
                  WHERE er.node_private = FALSE
                    AND EXISTS (
                      SELECT 1 FROM koi_entity_chunk_links l
                      JOIN koi_memories m ON m.rid = l.document_rid
                      WHERE l.entity_uri = er.fuseki_uri
                        AND m.is_private = TRUE
                    )
                ) t;
            """)
            leak_count = c.fetchone()[0]
            if leak_count > 0:
                logger.error(f"PUBLIC_LEAK DETECTED: {leak_count} entity_registry rows have public flag but private chunk_link exists")
                state['public_leak_count'] = leak_count
                save_state(state)
                return 2
            logger.info("Privacy hard-stop PASSED (0 leaks)")
            state['public_leak_count'] = 0
            save_state(state)
    finally:
        conn.close()

    logger.info("Phase 2 backfill complete.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
