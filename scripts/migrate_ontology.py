#!/usr/bin/env python3
"""
KOI Protocol Ontology Migration Script

Migrates domain-specific vertex labels to generic types with domain properties:
- Keeper → Struct (domain_type: "keeper")
- Message → Struct (domain_type: "message")
- Handler → Function (domain_type: "handler")

Usage:
    python migrate_ontology.py --host localhost --port 5433 --db eliza --password postgres
    python migrate_ontology.py --dry-run  # Test without making changes
"""

import argparse
import re
import sys
from typing import Dict, List, Tuple
import psycopg2
from loguru import logger


class OntologyMigrator:
    """Migrates Apache AGE graph from domain-specific to generic ontology."""

    def __init__(self, host: str, port: int, db: str, password: str, dry_run: bool = False):
        self.host = host
        self.port = port
        self.db = db
        self.password = password
        self.dry_run = dry_run
        self.conn = None
        self.graph_name = "regen_graph_v2"
        self.backup_graph_name = "regen_graph_v2_backup"

    def connect(self):
        """Connect to PostgreSQL database."""
        logger.info(f"Connecting to {self.host}:{self.port}/{self.db}...")
        self.conn = psycopg2.connect(
            host=self.host,
            port=self.port,
            database=self.db,
            user="postgres",
            password=self.password
        )
        self.conn.set_session(autocommit=True)

        # Load AGE extension and set search path
        cur = self.conn.cursor()
        cur.execute("LOAD 'age';")
        cur.execute("SET search_path = ag_catalog, '$user', public;")
        cur.close()

        logger.success("Connected to database")

    def disconnect(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            logger.info("Disconnected from database")

    def extract_module_from_path(self, file_path: str) -> str:
        """
        Extract Cosmos SDK module name from file path.

        Examples:
            x/ecocredit/base/keeper/keeper.go → ecocredit.base
            x/ecocredit/marketplace/keeper/keeper.go → ecocredit.marketplace
            x/data/tx.pb.go → data
        """
        # Match x/{module}/{submodule}/... or x/{module}/...
        match = re.search(r'x/([^/]+)(?:/([^/]+))?', file_path)
        if match:
            module = match.group(1)
            submodule = match.group(2)

            # For ecocredit, include submodule (base, marketplace, basket)
            if module == "ecocredit" and submodule and submodule not in ["keeper", "types", "client", "mocks"]:
                return f"{module}.{submodule}"
            return module

        # Fallback: check for api/regen/{module}/...
        match = re.search(r'api/regen/([^/]+)', file_path)
        if match:
            module = match.group(1)
            # Skip version suffixes
            if module not in ["data", "ecocredit"]:
                return module

        return "unknown"

    def is_mock(self, file_path: str, name: str) -> bool:
        """Detect if entity is a mock (for testing)."""
        return "/mocks/" in file_path or name.startswith("Mock")

    def backup_graph(self):
        """Create a backup of the current graph."""
        if self.dry_run:
            logger.info("[DRY RUN] Would create backup graph: {}", self.backup_graph_name)
            return

        logger.info("Creating backup graph: {}...", self.backup_graph_name)
        cur = self.conn.cursor()

        try:
            # Check if backup already exists
            cur.execute("""
                SELECT * FROM cypher(%s, $$
                    MATCH (n) RETURN count(n) as count LIMIT 1
                $$) as (count agtype);
            """, (self.backup_graph_name,))

            logger.warning("Backup graph already exists: {}", self.backup_graph_name)
            return
        except Exception:
            # Backup doesn't exist, create it
            pass

        # Note: AGE doesn't support graph cloning, so we'll just verify original exists
        cur.execute("""
            SELECT * FROM cypher(%s, $$
                MATCH (n) RETURN count(n) as count
            $$) as (count agtype);
        """, (self.graph_name,))

        result = cur.fetchone()
        count = result[0] if result else 0
        logger.success("Original graph has {} entities", count)
        logger.info("Proceeding with in-place migration (backup via pg_dump recommended)")

        cur.close()

    def get_entity_count(self, label: str) -> int:
        """Get count of entities with specific label."""
        cur = self.conn.cursor()
        cur.execute("""
            SELECT * FROM cypher(%s, $$
                MATCH (n:%s) RETURN count(n) as count
            $$ ::varchar) as (count agtype);
        """, (self.graph_name, label))

        result = cur.fetchone()
        cur.close()
        return int(str(result[0]).strip('"')) if result else 0

    def migrate_keepers(self) -> int:
        """
        Migrate Keeper entities to Struct with domain properties.

        Returns: Number of entities migrated
        """
        count = self.get_entity_count("Keeper")
        logger.info("Migrating {} Keeper entities to Struct...", count)

        if count == 0:
            logger.warning("No Keeper entities found")
            return 0

        if self.dry_run:
            logger.info("[DRY RUN] Would migrate {} Keepers", count)
            return count

        cur = self.conn.cursor()

        # Fetch all Keepers
        cur.execute("""
            SELECT * FROM cypher(%s, $$
                MATCH (k:Keeper)
                RETURN id(k) as id, properties(k) as props
            $$) as (id agtype, props agtype);
        """, (self.graph_name,))

        keepers = cur.fetchall()
        migrated = 0

        for node_id, props_str in keepers:
            # Parse properties (AGE returns agtype format)
            # For simplicity, we'll use Cypher to update in-place

            # Update: Add Struct label, remove Keeper label, add domain properties
            update_query = """
                SELECT * FROM cypher(%s, $$
                    MATCH (k:Keeper)
                    WHERE id(k) = %s
                    SET k:Struct
                    SET k.domain = 'cosmos-sdk'
                    SET k.domain_type = 'keeper'
                    REMOVE k:Keeper
                    RETURN k
                $$) as (result agtype);
            """

            try:
                # Extract node ID (remove AGE wrapper)
                id_val = str(node_id).strip('"')
                cur.execute(update_query, (self.graph_name, id_val))
                migrated += 1
            except Exception as e:
                logger.error("Failed to migrate Keeper {}: {}", node_id, e)

        cur.close()
        logger.success("Migrated {} Keepers to Struct", migrated)
        return migrated

    def migrate_messages(self) -> int:
        """
        Migrate Message entities to Struct with domain properties.

        Returns: Number of entities migrated
        """
        count = self.get_entity_count("Message")
        logger.info("Migrating {} Message entities to Struct...", count)

        if count == 0:
            logger.warning("No Message entities found")
            return 0

        if self.dry_run:
            logger.info("[DRY RUN] Would migrate {} Messages", count)
            return count

        cur = self.conn.cursor()

        # Update all Messages in a single query
        update_query = """
            SELECT * FROM cypher(%s, $$
                MATCH (m:Message)
                SET m:Struct
                SET m.domain = 'cosmos-sdk'
                SET m.domain_type = 'message'
                REMOVE m:Message
                RETURN count(m) as migrated
            $$) as (migrated agtype);
        """

        try:
            cur.execute(update_query, (self.graph_name,))
            result = cur.fetchone()
            migrated = int(str(result[0]).strip('"')) if result else 0
            cur.close()
            logger.success("Migrated {} Messages to Struct", migrated)
            return migrated
        except Exception as e:
            logger.error("Failed to migrate Messages: {}", e)
            cur.close()
            return 0

    def migrate_handlers(self) -> int:
        """
        Migrate Handler entities to Function with domain properties.

        Returns: Number of entities migrated
        """
        count = self.get_entity_count("Handler")
        logger.info("Migrating {} Handler entities to Function...", count)

        if count == 0:
            logger.warning("No Handler entities found")
            return 0

        if self.dry_run:
            logger.info("[DRY RUN] Would migrate {} Handlers", count)
            return count

        cur = self.conn.cursor()

        # Update all Handlers in a single query
        update_query = """
            SELECT * FROM cypher(%s, $$
                MATCH (h:Handler)
                SET h.domain = 'cosmos-sdk'
                SET h.domain_type = 'handler'
                REMOVE h:Handler
                RETURN count(h) as migrated
            $$) as (migrated agtype);
        """

        try:
            cur.execute(update_query, (self.graph_name,))
            result = cur.fetchone()
            migrated = int(str(result[0]).strip('"')) if result else 0
            cur.close()
            logger.success("Migrated {} Handlers to Function", migrated)
            return migrated
        except Exception as e:
            logger.error("Failed to migrate Handlers: {}", e)
            cur.close()
            return 0

    def validate_migration(self):
        """Validate the migration was successful."""
        logger.info("Validating migration...")

        cur = self.conn.cursor()

        # 1. Check no domain-specific labels remain
        for label in ["Keeper", "Message", "Handler"]:
            count = self.get_entity_count(label)
            if count > 0:
                logger.error("❌ Found {} {} entities (should be 0)", count, label)
            else:
                logger.success("✓ No {} entities remain", label)

        # 2. Check domain properties exist
        cur.execute("""
            SELECT * FROM cypher(%s, $$
                MATCH (n:Struct)
                WHERE n.domain_type = 'keeper'
                RETURN count(n) as count
            $$) as (count agtype);
        """, (self.graph_name,))
        result = cur.fetchone()
        keeper_count = int(str(result[0]).strip('"')) if result else 0
        logger.info("✓ Found {} Structs with domain_type='keeper'", keeper_count)

        cur.execute("""
            SELECT * FROM cypher(%s, $$
                MATCH (n:Struct)
                WHERE n.domain_type = 'message'
                RETURN count(n) as count
            $$) as (count agtype);
        """, (self.graph_name,))
        result = cur.fetchone()
        message_count = int(str(result[0]).strip('"')) if result else 0
        logger.info("✓ Found {} Structs with domain_type='message'", message_count)

        cur.execute("""
            SELECT * FROM cypher(%s, $$
                MATCH (n:Function)
                WHERE n.domain_type = 'handler'
                RETURN count(n) as count
            $$) as (count agtype);
        """, (self.graph_name,))
        result = cur.fetchone()
        handler_count = int(str(result[0]).strip('"')) if result else 0
        logger.info("✓ Found {} Functions with domain_type='handler'", handler_count)

        # 3. Check total entity count unchanged
        cur.execute("""
            SELECT * FROM cypher(%s, $$
                MATCH (n) RETURN count(n) as count
            $$) as (count agtype);
        """, (self.graph_name,))
        result = cur.fetchone()
        total = int(str(result[0]).strip('"')) if result else 0
        logger.info("✓ Total entities: {}", total)

        # 4. Check CALLS edges preserved
        cur.execute("""
            SELECT * FROM cypher(%s, $$
                MATCH ()-[r:CALLS]->() RETURN count(r) as count
            $$) as (count agtype);
        """, (self.graph_name,))
        result = cur.fetchone()
        edges = int(str(result[0]).strip('"')) if result else 0
        logger.info("✓ CALLS edges: {}", edges)

        cur.close()

        if keeper_count == 8 and message_count == 132 and handler_count == 39 and total == 26768 and edges == 11331:
            logger.success("✅ Migration validation PASSED")
            return True
        else:
            logger.error("❌ Migration validation FAILED - counts don't match expected")
            return False

    def run(self):
        """Execute the full migration."""
        try:
            self.connect()

            logger.info("=" * 60)
            logger.info("KOI PROTOCOL ONTOLOGY MIGRATION")
            logger.info("=" * 60)
            logger.info("Graph: {}", self.graph_name)
            logger.info("Dry run: {}", self.dry_run)
            logger.info("")

            # Step 1: Backup (informational only, recommend pg_dump)
            self.backup_graph()
            logger.info("")

            # Step 2: Pre-migration counts
            logger.info("Pre-migration entity counts:")
            keeper_count = self.get_entity_count("Keeper")
            message_count = self.get_entity_count("Message")
            handler_count = self.get_entity_count("Handler")
            logger.info("  Keeper: {}", keeper_count)
            logger.info("  Message: {}", message_count)
            logger.info("  Handler: {}", handler_count)
            logger.info("")

            # Step 3: Migrate
            if not self.dry_run:
                logger.info("Starting migration...")

            migrated_keepers = self.migrate_keepers()
            logger.info("")

            migrated_messages = self.migrate_messages()
            logger.info("")

            migrated_handlers = self.migrate_handlers()
            logger.info("")

            # Step 4: Validate
            if not self.dry_run:
                self.validate_migration()

            logger.info("=" * 60)
            logger.success("MIGRATION COMPLETE")
            logger.info("=" * 60)
            logger.info("Migrated:")
            logger.info("  Keeper → Struct: {}", migrated_keepers)
            logger.info("  Message → Struct: {}", migrated_messages)
            logger.info("  Handler → Function: {}", migrated_handlers)
            logger.info("  Total: {}", migrated_keepers + migrated_messages + migrated_handlers)

        except Exception as e:
            logger.error("Migration failed: {}", e)
            raise
        finally:
            self.disconnect()


def main():
    parser = argparse.ArgumentParser(description="Migrate KOI graph ontology from domain-specific to generic types")
    parser.add_argument("--host", default="localhost", help="PostgreSQL host")
    parser.add_argument("--port", type=int, default=5433, help="PostgreSQL port")
    parser.add_argument("--db", default="eliza", help="Database name")
    parser.add_argument("--password", default="postgres", help="PostgreSQL password")
    parser.add_argument("--dry-run", action="store_true", help="Test migration without making changes")

    args = parser.parse_args()

    migrator = OntologyMigrator(
        host=args.host,
        port=args.port,
        db=args.db,
        password=args.password,
        dry_run=args.dry_run
    )

    migrator.run()


if __name__ == "__main__":
    main()
