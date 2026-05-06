#!/usr/bin/env python3
"""
Option C — Targeted re-extraction for github + forum cohorts via OpenAI Batch API.

Phase 3 of overnight Option C (2026-05-06).

Subcommands:
  build   — assemble JSONL of chunks needing extraction
  submit  — upload + submit batch
  poll    — poll status until complete (or expired/failed)
  process — write extraction results into koi_kg_extractions + koi_entity_chunk_links

State at /opt/projects/koi-processor/batch_state/option-c-batch.json.
"""

import sys
import os
import json
import time
import logging
import argparse
from pathlib import Path
from typing import Dict, Any, List

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

load_dotenv(Path(__file__).parent.parent / ".env")

from extraction.prompt_builder import build_extraction_prompt, get_system_message

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/opt/projects/koi-processor/batch_state/option-c-batch.log', mode='a'),
    ],
)
logger = logging.getLogger(__name__)

STATE_FILE = Path('/opt/projects/koi-processor/batch_state/option-c-batch.json')
JSONL_FILE = Path('/opt/projects/koi-processor/batch_state/option-c-batch.jsonl')
RESULT_FILE = Path('/opt/projects/koi-processor/batch_state/option-c-batch-results.jsonl')

MODEL = os.getenv('OPTION_C_MODEL', 'gpt-4.1-nano')


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
            logger.warning("State corrupt, starting fresh")
    return {}


def save_state(s: Dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(s, indent=2, default=str))
    tmp.replace(STATE_FILE)


def fetch_chunks_to_extract(db_config: Dict[str, Any], limit: int = None) -> List[Dict[str, Any]]:
    """Pull chunks (memory rows with #chunkN rid shape) in github + forum cohorts
    that have NO matching koi_entity_chunk_links row."""
    conn = psycopg2.connect(**db_config)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        sql = """
            SELECT
              m.id::text AS memory_uuid,
              m.rid AS chunk_rid,
              m.content,
              m.metadata,
              CASE
                WHEN m.rid LIKE 'regen.github:%' THEN 'github'
                WHEN m.rid LIKE 'regen.forum%' OR m.rid LIKE 'orn:web.page:forum%' THEN 'forum'
                ELSE 'other'
              END AS cohort
            FROM koi_memories m
            WHERE (m.rid LIKE 'regen.github:%'
                   OR m.rid LIKE 'regen.forum%'
                   OR m.rid LIKE 'orn:web.page:forum%')
              AND m.rid LIKE '%#chunk%'
              AND m.superseded_at IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM koi_entity_chunk_links l
                WHERE l.chunk_rid = m.id::text
                   OR l.chunk_rid = m.rid
              )
            ORDER BY m.id
        """
        if limit:
            sql += f" LIMIT {int(limit)}"
        cur.execute(sql)
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def cmd_build(args):
    state = load_state()
    chunks = fetch_chunks_to_extract(get_db_config(), args.limit)
    cohort_breakdown = {}
    for c in chunks:
        cohort_breakdown[c['cohort']] = cohort_breakdown.get(c['cohort'], 0) + 1
    logger.info(f"Found {len(chunks)} chunks needing extraction. Cohorts: {cohort_breakdown}")

    JSONL_FILE.parent.mkdir(parents=True, exist_ok=True)
    sys_msg = get_system_message()
    n_written = 0
    with open(JSONL_FILE, 'w') as f:
        for c in chunks:
            content = c['content']
            if isinstance(content, dict):
                text = content.get('text', '')
            elif isinstance(content, str):
                # Sometimes jsonb stored as string
                try:
                    parsed = json.loads(content)
                    text = parsed.get('text', '') if isinstance(parsed, dict) else content
                except (json.JSONDecodeError, TypeError):
                    text = content
            else:
                text = str(content) if content else ''
            text = text.strip()
            if not text or len(text) < 50:
                continue
            md = c.get('metadata') or {}
            if isinstance(md, str):
                try:
                    md = json.loads(md)
                except json.JSONDecodeError:
                    md = {}
            user_prompt = build_extraction_prompt(
                content=text,
                source_type=c['cohort'],
                metadata=md,
                max_content_length=3000,
            )
            request = {
                "custom_id": c['memory_uuid'],
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": sys_msg},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 4096,
                    "response_format": {"type": "json_object"},
                },
            }
            f.write(json.dumps(request) + '\n')
            n_written += 1
    state['build'] = {
        'jsonl_file': str(JSONL_FILE),
        'rows_written': n_written,
        'rows_total_chunks': len(chunks),
        'cohort_breakdown': cohort_breakdown,
        'model': MODEL,
        'built_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    save_state(state)
    logger.info(f"Wrote {n_written} requests to {JSONL_FILE}. JSONL size: {JSONL_FILE.stat().st_size} bytes")


def cmd_submit(args):
    from openai import OpenAI
    client = OpenAI()
    state = load_state()
    if not JSONL_FILE.exists():
        logger.error(f"Run 'build' first; no file at {JSONL_FILE}")
        return 1
    logger.info(f"Uploading {JSONL_FILE} to OpenAI...")
    f = client.files.create(file=open(JSONL_FILE, 'rb'), purpose='batch')
    logger.info(f"File ID: {f.id}, status: {f.status}")
    batch = client.batches.create(
        input_file_id=f.id,
        endpoint='/v1/chat/completions',
        completion_window='24h',
        metadata={
            'session': 'option-c-overnight-2026-05-06',
            'cohorts': 'github+forum',
            'model': MODEL,
        },
    )
    state['submit'] = {
        'file_id': f.id,
        'batch_id': batch.id,
        'status': batch.status,
        'submitted_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    save_state(state)
    logger.info(f"Batch ID: {batch.id}, status: {batch.status}")
    return 0


def cmd_poll(args):
    from openai import OpenAI
    client = OpenAI()
    state = load_state()
    batch_id = state.get('submit', {}).get('batch_id')
    if not batch_id:
        logger.error("No batch_id in state; submit first")
        return 1
    deadline = time.time() + args.max_wait_hours * 3600
    while time.time() < deadline:
        b = client.batches.retrieve(batch_id)
        rc = b.request_counts
        elapsed_min = (time.time() - time.mktime(time.strptime(state['submit']['submitted_at'], '%Y-%m-%dT%H:%M:%SZ'))) / 60.0
        logger.info(
            f"Status={b.status} elapsed={elapsed_min:.1f}min "
            f"completed={rc.completed if rc else '-'} failed={rc.failed if rc else '-'} total={rc.total if rc else '-'}"
        )
        state['poll'] = {
            'last_status': b.status,
            'last_check': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'completed': rc.completed if rc else None,
            'failed': rc.failed if rc else None,
            'total': rc.total if rc else None,
            'output_file_id': b.output_file_id,
            'error_file_id': b.error_file_id,
        }
        save_state(state)
        if b.status in ('completed', 'failed', 'expired', 'cancelled'):
            logger.info(f"Terminal state: {b.status}")
            if b.status == 'completed' and b.output_file_id:
                logger.info(f"Downloading output {b.output_file_id} -> {RESULT_FILE}")
                resp = client.files.content(b.output_file_id)
                RESULT_FILE.write_bytes(resp.content)
                logger.info(f"Wrote {RESULT_FILE.stat().st_size} bytes")
            return 0
        time.sleep(args.poll_interval)
    logger.warning(f"Hit max_wait of {args.max_wait_hours}h; abandoning poll loop")
    return 2


def parse_extraction_json(s: str) -> Dict[str, Any]:
    """Parse model output JSON, tolerant of small format issues."""
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # Try removing leading/trailing markdown fences
        s2 = s.strip()
        if s2.startswith('```'):
            s2 = s2.split('\n', 1)[1] if '\n' in s2 else s2
        if s2.endswith('```'):
            s2 = s2.rsplit('\n', 1)[0]
        try:
            return json.loads(s2)
        except json.JSONDecodeError:
            return {}


def cmd_process(args):
    """Read extraction results, write to koi_kg_extractions + koi_entity_chunk_links."""
    state = load_state()
    if not RESULT_FILE.exists():
        logger.error(f"No results file at {RESULT_FILE}; run poll first")
        return 1

    db_config = get_db_config()
    conn = psycopg2.connect(**db_config)
    conn.autocommit = False

    try:
        # Cache: chunk_rid (memory_uuid) -> {parent_rid, chunk_index, source_rid}
        # Collect all custom_ids from the batch result file first
        custom_ids = []
        with open(RESULT_FILE) as f:
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get('custom_id'):
                    custom_ids.append(row['custom_id'])
        logger.info(f"Result file has {len(custom_ids)} custom_ids")

        # Bulk lookup metadata for these UUIDs
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id::text AS memory_uuid, rid AS chunk_rid_full,
                   metadata->>'parent_rid' AS parent_rid,
                   COALESCE((metadata->>'chunk_index')::int, NULL) AS chunk_idx,
                   metadata->>'source_type' AS source_type
            FROM koi_memories
            WHERE id::text = ANY(%s)
        """, (custom_ids,))
        meta_map = {r['memory_uuid']: dict(r) for r in cur.fetchall()}
        cur.close()
        logger.info(f"Loaded metadata for {len(meta_map)} chunks")

        n_extractions_written = 0
        n_link_rows_written = 0
        n_skipped_empty = 0
        n_parse_errors = 0
        n_total = 0

        with open(RESULT_FILE) as f:
            for line in f:
                if not line.strip():
                    continue
                n_total += 1
                if args.limit and n_total > args.limit:
                    break
                row = json.loads(line)
                memory_uuid = row.get('custom_id')
                if memory_uuid not in meta_map:
                    n_skipped_empty += 1
                    continue
                meta = meta_map[memory_uuid]
                parent_rid = meta.get('parent_rid')
                chunk_idx = meta.get('chunk_idx')
                cohort = meta.get('source_type') or 'unknown'

                if row.get('error'):
                    n_parse_errors += 1
                    continue

                resp_body = (row.get('response') or {}).get('body') or {}
                choices = resp_body.get('choices') or []
                if not choices:
                    n_parse_errors += 1
                    continue
                content = choices[0].get('message', {}).get('content', '')
                ext = parse_extraction_json(content)
                if not ext:
                    n_parse_errors += 1
                    continue

                entities = ext.get('entities') or []
                if not isinstance(entities, list):
                    entities = []
                relationships = ext.get('relationships') or []
                statements = ext.get('statements') or []
                summary = ext.get('summary') or ''

                # Insert into koi_kg_extractions (per chunk)
                with conn.cursor() as c:
                    c.execute(
                        """
                        INSERT INTO koi_kg_extractions
                          (memory_rid, extraction_rid, extraction_type, entities, statements,
                           relations, confidence_score, extractor_version)
                        VALUES (%s, %s, 'passA', %s::jsonb, %s::jsonb, %s::jsonb, %s, %s)
                        """,
                        (
                            meta['chunk_rid_full'],
                            f"{meta['chunk_rid_full']}#extraction-option-c",
                            json.dumps(entities),
                            json.dumps(statements),
                            json.dumps(relationships),
                            float(ext.get('confidence', 0.85)) if isinstance(ext.get('confidence', 0.85), (int, float)) else 0.85,
                            f'option-c-batch-{MODEL}',
                        ),
                    )
                n_extractions_written += 1

                # Insert chunk_links (per entity, this chunk)
                link_rows = []
                for ent in entities:
                    if not isinstance(ent, dict):
                        continue
                    name = (ent.get('name') or '').strip()
                    if not name:
                        continue
                    link_rows.append((
                        name,
                        name.lower(),
                        ent.get('type', 'Unknown'),
                        ent.get('rid') or ent.get('uri'),
                        memory_uuid,            # chunk_rid (uuid of memory row)
                        chunk_idx,
                        parent_rid or meta['chunk_rid_full'],
                        float(ent.get('confidence', 0.8)) if isinstance(ent.get('confidence', 0.8), (int, float)) else 0.8,
                    ))
                if link_rows:
                    with conn.cursor() as c:
                        execute_values(
                            c,
                            """
                            INSERT INTO koi_entity_chunk_links
                              (entity_name, entity_name_lower, entity_type, entity_uri,
                               chunk_rid, chunk_index, document_rid, confidence)
                            VALUES %s
                            ON CONFLICT (entity_name_lower, chunk_rid, char_offset) DO NOTHING
                            """,
                            link_rows,
                            page_size=200,
                        )
                    n_link_rows_written += len(link_rows)

                if n_total % 200 == 0:
                    conn.commit()
                    logger.info(f"Processed {n_total}: extractions={n_extractions_written}, links={n_link_rows_written}")
        conn.commit()
        logger.info(
            f"DONE: total={n_total}, extractions={n_extractions_written}, "
            f"links={n_link_rows_written}, skipped={n_skipped_empty}, parse_errors={n_parse_errors}"
        )
        state['process'] = {
            'finished_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'total_processed': n_total,
            'extractions_written': n_extractions_written,
            'links_written': n_link_rows_written,
            'skipped_empty': n_skipped_empty,
            'parse_errors': n_parse_errors,
        }
        save_state(state)
        return 0
    finally:
        conn.close()


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)

    pb = sub.add_parser('build')
    pb.add_argument('--limit', type=int, default=None)

    ps = sub.add_parser('submit')

    pp = sub.add_parser('poll')
    pp.add_argument('--poll-interval', type=int, default=300, help='seconds between polls')
    pp.add_argument('--max-wait-hours', type=float, default=12.0)

    pr = sub.add_parser('process')
    pr.add_argument('--limit', type=int, default=None)

    args = p.parse_args()
    if args.cmd == 'build':
        return cmd_build(args)
    if args.cmd == 'submit':
        return cmd_submit(args)
    if args.cmd == 'poll':
        return cmd_poll(args)
    if args.cmd == 'process':
        return cmd_process(args)


if __name__ == '__main__':
    sys.exit(main())
