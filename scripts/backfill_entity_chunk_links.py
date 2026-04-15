#!/usr/bin/env python3
'''Backfill koi_entity_chunk_links for recent passA extractions.'''
import asyncio, asyncpg, json, os, sys
from datetime import datetime, timedelta, timezone

DB_URL = os.environ.get('POSTGRES_URL', 'postgresql://postgres:postgres@localhost:5433/eliza')

async def backfill(hours_back: int = 48, dry_run: bool = False):
    conn = await asyncpg.connect(DB_URL)
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        total = await conn.fetchval('''SELECT COUNT(*) FROM koi_kg_extractions
            WHERE extraction_type='passA' AND jsonb_array_length(entities) > 0
              AND created_at > $1''', cutoff)
        print(f'passA extractions since {cutoff.isoformat()}: {total}')
        if dry_run:
            return

        total_links = 0; processed = 0; skipped = 0
        t0 = datetime.now()
        batch = 100; offset = 0
        while True:
            docs = await conn.fetch('''SELECT memory_rid, entities FROM koi_kg_extractions
                WHERE extraction_type='passA' AND jsonb_array_length(entities) > 0
                  AND created_at > $1
                ORDER BY created_at DESC LIMIT $2 OFFSET $3''', cutoff, batch, offset)
            if not docs: break
            for doc in docs:
                rid = doc['memory_rid']; ents = doc['entities']
                if isinstance(ents, str): ents = json.loads(ents)
                if not ents: continue
                exists = await conn.fetchval('''SELECT 1 FROM koi_entity_chunk_links
                    WHERE document_rid LIKE $1 LIMIT 1''', rid + '#chunk%')
                if exists:
                    skipped += 1; continue
                chunks = await conn.fetch('''SELECT id, rid FROM koi_memories
                    WHERE rid LIKE $1 AND superseded_at IS NULL''', rid + '#chunk%')
                if not chunks: continue
                rows = []
                for c in chunks:
                    cuuid = str(c['id']); crid = c['rid']; cidx = None
                    if '#chunk' in crid:
                        try: cidx = int(crid.split('#chunk')[1])
                        except: pass
                    for e in ents:
                        n = (e.get('name') or '').strip()
                        if not n: continue
                        rows.append((n, n.lower(), e.get('type','Unknown'),
                            e.get('rid'), cuuid, cidx, crid, float(e.get('confidence', 0.8))))
                if rows:
                    await conn.executemany('''INSERT INTO koi_entity_chunk_links
                        (entity_name, entity_name_lower, entity_type, entity_uri,
                         chunk_rid, chunk_index, document_rid, confidence)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)''', rows)
                    total_links += len(rows)
                processed += 1
            offset += batch
            el = (datetime.now()-t0).total_seconds()
            print(f'  {processed} processed, {skipped} skipped, {total_links} links, {el:.1f}s')
        el = (datetime.now()-t0).total_seconds()
        print(f'\nDone: {processed} processed, {skipped} skipped, {total_links} links in {el:.1f}s')
    finally:
        await conn.close()

if __name__ == '__main__':
    dry = '--dry-run' in sys.argv
    hours = 720 if '--all' in sys.argv else 48
    asyncio.run(backfill(hours_back=hours, dry_run=dry))
