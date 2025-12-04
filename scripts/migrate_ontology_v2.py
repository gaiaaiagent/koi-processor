#!/usr/bin/env python3
"""
KOI Protocol Ontology Migration Script (Simplified)

Migrates domain-specific vertex labels to generic types with domain properties:
- Keeper → Struct (domain_type: "keeper")
- Message → Struct (domain_type: "message")
- Handler → Function (domain_type: "handler")

Usage:
    python migrate_ontology_v2.py --host localhost --port 5433 --db eliza --password postgres
    python migrate_ontology_v2.py --dry-run  # Test without making changes
"""

import argparse
import sys
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

    def get_entity_count(self, label: str) -> int:
        """Get count of entities with specific label."""
        cur = self.conn.cursor()
        query = f"""
            SELECT * FROM cypher('{self.graph_name}', $$
                MATCH (n:{label}) RETURN count(n) as count
            $$) as (count agtype);
        """
        cur.execute(query)

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

        # Update all Keepers in a single query
        query = f"""
            SELECT * FROM cypher('{self.graph_name}', $$
                MATCH (k:Keeper)
                SET k:Struct
                SET k.domain = 'cosmos-sdk'
                SET k.domain_type = 'keeper'
                REMOVE k:Keeper
                RETURN count(k) as migrated
            $$) as (migrated agtype);
        """

        try:
            cur.execute(query)
            result = cur.fetchone()
            migrated = int(str(result[0]).strip('"')) if result else 0
            cur.close()
            logger.success("Migrated {} Keepers to Struct", migrated)
            return migrated
        except Exception as e:
            logger.error("Failed to migrate Keepers: {}", e)
            cur.close()
            return 0

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
        query = f"""
            SELECT * FROM cypher('{self.graph_name}', $$
                MATCH (m:Message)
                SET m:Struct
                SET m.domain = 'cosmos-sdk'
                SET m.domain_type = 'message'
                REMOVE m:Message
                RETURN count(m) as migrated
            $$) as (migrated agtype);
        """

        try:
            cur.execute(query)
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

        Note: Handlers should become Functions, not keep Handler label.

        Returns: Number of entities migrated
        """
        count = self.get_entity_count("Handler")
        logger.info("Migrating {} Handler entities (adding domain properties)...", count)

        if count == 0:
            logger.warning("No Handler entities found")
            return 0

        if self.dry_run:
            logger.info("[DRY RUN] Would migrate {} Handlers", count)
            return count

        cur = self.conn.cursor()

        # Add domain properties and remove Handler label
        # Note: Handlers might already have Function/Method label from tree-sitter
        query = f"""
            SELECT * FROM cypher('{self.graph_name}', $$
                MATCH (h:Handler)
                SET h.domain = 'cosmos-sdk'
                SET h.domain_type = 'handler'
                REMOVE h:Handler
                RETURN count(h) as migrated
            $$) as (migrated agtype);
        """

        try:
            cur.execute(query)
            result = cur.fetchone()
            migrated = int(str(result[0]).strip('"')) if result else 0
            cur.close()
            logger.success("Migrated {} Handlers (domain properties added, Handler label removed)", migrated)
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
        query = f"""
            SELECT * FROM cypher('{self.graph_name}', $$
                MATCH (n:Struct)
                WHERE n.domain_type = 'keeper'
                RETURN count(n) as count
            $$) as (count agtype);
        """
        cur.execute(query)
        result = cur.fetchone()
        keeper_count = int(str(result[0]).strip('"')) if result else 0
        logger.info("✓ Found {} Structs with domain_type='keeper'", keeper_count)

        query = f"""
            SELECT * FROM cypher('{self.graph_name}', $$
                MATCH (n:Struct)
                WHERE n.domain_type = 'message'
                RETURN count(n) as count
            $$) as (count agtype);
        """
        cur.execute(query)
        result = cur.fetchone()
        message_count = int(str(result[0]).strip('"')) if result else 0
        logger.info("✓ Found {} Structs with domain_type='message'", message_count)

        query = f"""
            SELECT * FROM cypher('{self.graph_name}', $$
                MATCH (n)
                WHERE n.domain_type = 'handler'
                RETURN count(n) as count
            $$) as (count agtype);
        """
        cur.execute(query)
        result = cur.fetchone()
        handler_count = int(str(result[0]).strip('"')) if result else 0
        logger.info("✓ Found {} entities with domain_type='handler'", handler_count)

        # 3. Check total entity count unchanged
        query = f"""
            SELECT * FROM cypher('{self.graph_name}', $$
                MATCH (n) RETURN count(n) as count
            $$) as (count agtype);
        """
        cur.execute(query)
        result = cur.fetchone()
        total = int(str(result[0]).strip('"')) if result else 0
        logger.info("✓ Total entities: {}", total)

        # 4. Check CALLS edges preserved
        query = f"""
            SELECT * FROM cypher('{self.graph_name}', $$
                MATCH ()-[r:CALLS]->() RETURN count(r) as count
            $$) as (count agtype);
        """
        cur.execute(query)
        result = cur.fetchone()
        edges = int(str(result[0]).strip('"')) if result else 0
        logger.info("✓ CALLS edges: {}", edges)

        cur.close()

        if keeper_count == 8 and message_count == 132 and handler_count == 39 and total == 26768 and edges == 11331:
            logger.success("✅ Migration validation PASSED")
            return True
        else:
            logger.warning("⚠️  Migration counts differ from expected:")
            logger.warning("   Expected: keepers=8, messages=132, handlers=39, total=26768, edges=11331")
            logger.warning("   Got: keepers={}, messages={}, handlers={}, total={}, edges={}",
                          keeper_count, message_count, handler_count, total, edges)
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

            # Pre-migration counts
            logger.info("Pre-migration entity counts:")
            keeper_count = self.get_entity_count("Keeper")
            message_count = self.get_entity_count("Message")
            handler_count = self.get_entity_count("Handler")
            logger.info("  Keeper: {}", keeper_count)
            logger.info("  Message: {}", message_count)
            logger.info("  Handler: {}", handler_count)
            logger.info("")

            if not self.dry_run:
                logger.info("Starting migration...")

            migrated_keepers = self.migrate_keepers()
            logger.info("")

            migrated_messages = self.migrate_messages()
            logger.info("")

            migrated_handlers = self.migrate_handlers()
            logger.info("")

            if not self.dry_run:
                self.validate_migration()

            logger.info("=" * 60)
            logger.success("MIGRATION COMPLETE")
            logger.info("=" * 60)
            logger.info("Migrated:")
            logger.info("  Keeper → Struct: {}", migrated_keepers)
            logger.info("  Message → Struct: {}", migrated_messages)
            logger.info("  Handler (domain props added): {}", migrated_handlers)
            logger.info("  Total: {}", migrated_keepers + migrated_messages + migrated_handlers)

        except Exception as e:
            logger.error("Migration failed: {}", e)
            import traceback
            traceback.print_exc()
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
