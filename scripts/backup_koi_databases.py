#!/usr/bin/env python3
"""
Backup KOI database tables to JSON format
"""

import asyncio
import asyncpg
import json
from datetime import datetime
import os
from pathlib import Path

async def backup_koi_databases():
    """Backup KOI database tables to JSON files"""

    # Create backups directory
    backup_dir = Path("/opt/projects/koi-processor/backups")
    backup_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Database connection
    conn = await asyncpg.connect(
        host='localhost',
        port=5433,
        user='postgres',
        password='postgres',
        database='eliza'
    )

    try:
        print(f"📦 Starting KOI database backup at {timestamp}")

        # Tables to backup
        tables = ['koi_memories', 'koi_content', 'koi_memory_chunks']

        for table in tables:
            print(f"\n🔄 Backing up {table}...")

            # Get row count
            count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            print(f"   Found {count} records")

            # Fetch all data
            rows = await conn.fetch(f"SELECT * FROM {table}")

            # Convert to JSON-serializable format
            data = []
            for row in rows:
                record = dict(row)
                # Convert datetime objects to ISO format
                for key, value in record.items():
                    if isinstance(value, datetime):
                        record[key] = value.isoformat()
                data.append(record)

            # Save to JSON file
            backup_file = backup_dir / f"{table}_{timestamp}.json"
            with open(backup_file, 'w') as f:
                json.dump(data, f, indent=2, default=str)

            print(f"   ✅ Saved to {backup_file}")

        # Also create a combined SQL backup using pg_dump via subprocess
        print("\n🔄 Creating SQL backup...")
        import subprocess

        sql_file = backup_dir / f"koi_backup_{timestamp}.sql"

        # Use PGPASSWORD environment variable
        env = os.environ.copy()
        env['PGPASSWORD'] = 'postgres'

        # Build pg_dump command
        tables_arg = ' '.join([f'-t {t}' for t in tables])
        cmd = f"pg_dump -h localhost -p 5433 -U postgres -d eliza {tables_arg}"

        result = subprocess.run(
            cmd,
            shell=True,
            env=env,
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            with open(sql_file, 'w') as f:
                f.write(result.stdout)
            print(f"   ✅ SQL backup saved to {sql_file}")
        else:
            print(f"   ⚠️ SQL backup failed: {result.stderr}")

        print(f"\n✅ Backup complete! Files saved in {backup_dir}")

        # Show backup summary
        print("\n📊 Backup Summary:")
        for table in tables:
            count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            print(f"   {table}: {count} records")

        return backup_dir, timestamp

    finally:
        await conn.close()

# ---------------------------------------------------------------------------
# DISARMED 2026-08-21 (Plan A, step 1).
#
# This file previously defined a destructive table-clearing helper, plus an
# entry point that invoked it after a bare input("Continue? (y/n)") prompt and
# was reachable by running this module directly.
#
# Why that was dangerous rather than merely unused: the connection details
# above (localhost:5433, user postgres, database 'eliza') do not resolve on
# this machine, so the natural "fix" is to repoint them at personal_koi:5432.
# Doing so and confirming the prompt would have emptied the live entity
# registry through four ON DELETE CASCADE foreign keys, and there is no PITR
# on this database (wal_level=replica, archive_mode=off) — the loss would be
# unrecoverable beyond the most recent hand-taken dump.
#
# The backup half of this file (backup_koi_databases, above) is left intact
# but is NOT the project's backup mechanism: it targets the wrong database and
# omits entity_registry, entity_relationships, knowledge_facts,
# entity_merge_log and document_entity_links. Use the table-scoped pg_dump
# recorded in ~/.claude/plans/ (Plan A, step 2) instead.
#
# This module is deliberately not runnable: it has no entry-point guard.
# ---------------------------------------------------------------------------