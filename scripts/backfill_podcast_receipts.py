#!/usr/bin/env python3
"""
Backfill missing CAT receipts for podcast memories created before 01:28
"""

import asyncio
import asyncpg
import hashlib
import json
from datetime import datetime, timezone

async def backfill_receipts():
    conn = await asyncpg.connect(
        host='localhost',
        port=5433,
        user='postgres',
        password='postgres',
        database='eliza'
    )

    print('='*70)
    print('BACKFILLING MISSING CAT RECEIPTS')
    print('='*70)
    print()

    # 1. Find memories without koi_to_memory receipts
    print('1. Finding memories without koi_to_memory receipts...')

    memories_without_receipts = await conn.fetch('''
        SELECT
            m.id,
            m.rid,
            m.content_hash,
            m.created_at,
            m.metadata
        FROM koi_memories m
        WHERE m.rid LIKE 'regen.podcast%'
        AND NOT EXISTS (
            SELECT 1 FROM koi_transformation_receipts tr
            WHERE tr.output_rid = m.rid
            AND tr.transformation_type = 'koi_to_memory'
        )
        ORDER BY m.created_at
    ''')

    print(f'   Found {len(memories_without_receipts)} memories missing receipts')
    print()

    # 2. Create koi_to_memory receipts
    print('2. Creating koi_to_memory receipts...')
    created_koi_to_memory = 0

    for memory in memories_without_receipts:
        try:
            # Extract parent RID from chunk RID
            rid = memory['rid']
            if '#chunk' in rid:
                parent_rid = rid.split('#chunk')[0]
            else:
                parent_rid = rid

            # Generate receipt ID
            receipt_content = f"koi_to_memory:{parent_rid}:{rid}:{memory['created_at'].isoformat()}"
            receipt_id = hashlib.sha256(receipt_content.encode()).hexdigest()

            # Create receipt with backdated timestamp
            await conn.execute('''
                INSERT INTO koi_transformation_receipts (
                    receipt_id,
                    transformation_type,
                    input_rid,
                    output_rid,
                    input_cid,
                    output_cid,
                    processor_name,
                    processor_version,
                    chunks_created,
                    embeddings_created,
                    entities_extracted,
                    source_sensor,
                    event_type,
                    metadata,
                    processing_duration_ms,
                    created_at,
                    stored_in_graph
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
                ON CONFLICT (receipt_id) DO NOTHING
            ''',
                receipt_id,
                'koi_to_memory',
                parent_rid,
                rid,
                None,
                memory['content_hash'],
                'KOI Event Bridge v2 (Backfill)',
                '2.0.0',
                1,
                0,
                0,
                'podcast-sensor',
                'UPDATE',
                json.dumps({'backfilled': True, 'original_created_at': memory['created_at'].isoformat()}),
                None,
                memory['created_at'],
                None
            )

            created_koi_to_memory += 1

            if created_koi_to_memory % 100 == 0:
                print(f'   Created {created_koi_to_memory} receipts...')

        except Exception as e:
            print(f'   Error creating receipt for {memory["rid"]}: {e}')
            continue

    print(f'   ✓ Created {created_koi_to_memory} koi_to_memory receipts')
    print()

    # 3. Find embeddings without receipts
    print('3. Finding embeddings without memory_to_bge_embedding receipts...')

    embeddings_without_receipts = await conn.fetch('''
        SELECT
            e.id as embedding_id,
            e.memory_id,
            e.created_at as embedding_created_at,
            m.rid,
            m.content_hash
        FROM koi_embeddings e
        JOIN koi_memories m ON e.memory_id = m.id
        WHERE m.rid LIKE 'regen.podcast%'
        AND NOT EXISTS (
            SELECT 1 FROM koi_transformation_receipts tr
            WHERE tr.output_rid = m.rid
            AND tr.transformation_type = 'memory_to_bge_embedding'
        )
        ORDER BY e.created_at
    ''')

    print(f'   Found {len(embeddings_without_receipts)} embeddings missing receipts')
    print()

    # 4. Create memory_to_bge_embedding receipts
    print('4. Creating memory_to_bge_embedding receipts...')
    created_embedding_receipts = 0

    for emb in embeddings_without_receipts:
        try:
            receipt_content = f"memory_to_bge_embedding:{emb['rid']}:{emb['rid']}:{emb['embedding_created_at'].isoformat()}"
            receipt_id = hashlib.sha256(receipt_content.encode()).hexdigest()

            await conn.execute('''
                INSERT INTO koi_transformation_receipts (
                    receipt_id,
                    transformation_type,
                    input_rid,
                    output_rid,
                    input_cid,
                    output_cid,
                    processor_name,
                    processor_version,
                    chunks_created,
                    embeddings_created,
                    entities_extracted,
                    source_sensor,
                    event_type,
                    metadata,
                    processing_duration_ms,
                    created_at,
                    stored_in_graph
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
                ON CONFLICT (receipt_id) DO NOTHING
            ''',
                receipt_id,
                'memory_to_bge_embedding',
                emb['rid'],
                emb['rid'],
                emb['content_hash'],
                None,
                'BGE Embedding Service (Backfill)',
                '1.0.0',
                0,
                1,
                0,
                'bge-service',
                'UPDATE',
                json.dumps({'backfilled': True, 'original_created_at': emb['embedding_created_at'].isoformat()}),
                None,
                emb['embedding_created_at'],
                None
            )

            created_embedding_receipts += 1

            if created_embedding_receipts % 100 == 0:
                print(f'   Created {created_embedding_receipts} receipts...')

        except Exception as e:
            print(f'   Error creating embedding receipt for {emb["rid"]}: {e}')
            continue

    print(f'   ✓ Created {created_embedding_receipts} memory_to_bge_embedding receipts')
    print()

    # 5. Verify final counts
    print('='*70)
    print('VERIFICATION')
    print('='*70)
    print()

    final_koi_to_memory = await conn.fetchval('''
        SELECT COUNT(*) FROM koi_transformation_receipts
        WHERE transformation_type = 'koi_to_memory'
        AND output_rid LIKE '%podcast%'
    ''')

    final_embeddings = await conn.fetchval('''
        SELECT COUNT(*) FROM koi_transformation_receipts
        WHERE transformation_type = 'memory_to_bge_embedding'
        AND output_rid LIKE '%podcast%'
    ''')

    total_memories = await conn.fetchval(
        "SELECT COUNT(*) FROM koi_memories WHERE rid LIKE 'regen.podcast%'"
    )

    total_embeddings = await conn.fetchval('''
        SELECT COUNT(*) FROM koi_embeddings e
        JOIN koi_memories m ON e.memory_id = m.id
        WHERE m.rid LIKE 'regen.podcast%'
    ''')

    print(f'koi_to_memory receipts: {final_koi_to_memory} / {total_memories}')
    if final_koi_to_memory == total_memories:
        print('   ✓ 100% coverage!')
    else:
        print(f'   ⚠️  Missing {total_memories - final_koi_to_memory} receipts')

    print()
    print(f'memory_to_bge_embedding receipts: {final_embeddings} / {total_embeddings}')
    if final_embeddings == total_embeddings:
        print('   ✓ 100% coverage!')
    else:
        print(f'   ⚠️  Missing {total_embeddings - final_embeddings} receipts')

    print()
    print('='*70)
    print(f'BACKFILL COMPLETE!')
    print(f'Created {created_koi_to_memory} koi_to_memory receipts')
    print(f'Created {created_embedding_receipts} embedding receipts')
    print('='*70)

    await conn.close()

if __name__ == '__main__':
    asyncio.run(backfill_receipts())
