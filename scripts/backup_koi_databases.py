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

async def clear_tables():
    """Clear all KOI tables to start fresh"""

    conn = await asyncpg.connect(
        host='localhost',
        port=5433,
        user='postgres',
        password='postgres',
        database='eliza'
    )

    try:
        print("\n🧹 Clearing KOI tables...")

        # Clear in order to respect foreign key constraints
        tables = ['koi_memory_chunks', 'koi_memories', 'koi_content']

        for table in tables:
            count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            await conn.execute(f"TRUNCATE TABLE {table} CASCADE")
            print(f"   ✅ Cleared {table} ({count} records removed)")

        print("\n✅ All KOI tables cleared and ready for fresh data")

    finally:
        await conn.close()

async def main():
    # First backup
    backup_dir, timestamp = await backup_koi_databases()

    # Ask for confirmation before clearing
    print("\n⚠️ Ready to clear all KOI tables and start fresh.")
    response = input("Continue? (y/n): ")

    if response.lower() == 'y':
        await clear_tables()
    else:
        print("❌ Aborted - databases not cleared")

if __name__ == "__main__":
    asyncio.run(main())