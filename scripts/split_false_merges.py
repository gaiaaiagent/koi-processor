#!/usr/bin/env python3
"""
Phase B: Split semantic false merges in entity_rid_mappings.

For each collision pair, Entity A keeps the existing canonical URI,
Entity B gets a new URI. Relationships and document links are re-synced.

Usage:
    source config/personal.env
    python scripts/split_false_merges.py [--dry-run]
"""

import asyncio
import json
import os
import sys
import uuid
import re
import logging

import asyncpg
import httpx
import yaml

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# --- Collision inventory ---
# Each tuple: (entity_a_vault_path, entity_b_vault_path)
# Entity A keeps the existing URI; Entity B gets a new URI.
COLLISION_PAIRS = [
    ("People/Benjamin Neal.md", "People/Benjamin Life.md"),
    ("People/Sam Bennett.md", "People/Sam Bennetts.md"),
    ("People/Rebecca Harman.md", "People/Rebecca Saltman.md"),
    ("Organizations/University of Victoria.md", "Organizations/University of British Columbia.md"),
    ("Organizations/Songhees Nation.md", "Organizations/Songhees Catering.md"),
    ("Organizations/Sandown Centre.md", "Organizations/Sandown Farm.md"),
    ("Organizations/Salish Sea Hub.md", "Organizations/Salish Sea Institute.md"),
    ("Organizations/Regenerative Food Systems Investment.md", "Organizations/Regenerative.fi.md"),
    ("Organizations/Regen Foundation.md", "Organizations/RegenAI.md"),
    ("Locations/Esquimalt Harbour.md", "Locations/Esquimalt Lagoon.md"),
    ("Locations/Vancouver Island.md", "Locations/Vancouver.md"),
    ("Concepts/Bioregionalism.md", "Concepts/Bioregional Finance.md"),

    # --- 2026-07-16 round ---------------------------------------------------
    # Surfaced by auditing entity_rid_mappings for canonicals with >1 mapping.
    # Only pairs that are DEMONSTRABLY two different real things are listed;
    # same-thing duplicates (Will Reddick/Will Ruddick, Becca/Rebecca Harman,
    # Claims Engine/Claims Engine V1, and the cross-type Org-vs-Project pairs)
    # are note-duplication, not entity collisions — splitting those would
    # fragment one real entity, so they are deliberately excluded.
    ("Organizations/University of Victoria.md", "Organizations/University of Alberta.md"),           # Edmonton, ualberta.ca
    ("Organizations/First Nations National Guardians Network.md", "Organizations/First Nations Finance Authority.md"),  # FNFA, fnfa.ca
    ("Projects/Regen Ledger.md", "Concepts/REGEN Token.md"),                                          # the chain vs its native token
    ("Projects/KOI.md", "People/Koi.md"),                                                             # KOI infra vs an unrelated person in Brazil
    ("Projects/Claims Engine Demo Checklist.md", "Projects/Claims Engine Prod Runbook.md"),           # two distinct documents
]

VAULT_ROOT = os.path.expanduser("~/Documents/Notes")
API_BASE = "http://localhost:8351"


def doc_rid_to_vault_path(doc_rid: str):
    """Map a document_rid to a vault-relative .md path, or None if not a vault note.

    Step 5 previously hard-coded `doc_rid.replace('orn:obsidian.document:Notes/', '')`.
    Real document_rid prefixes are `vault:` (the dominant form for vault notes),
    plus `orn:`, `document:`, `claude-session:` and `substack-corpus:` — so for
    every `vault:`-prefixed rid the replace was a no-op, the resulting path never
    existed, and the loop `continue`d. Step 5 therefore re-attributed NOTHING,
    silently, for every split run to date (2026-07-16).

    Only vault-backed notes are returned; non-vault sources (sessions, substack)
    have no file to inspect for wikilinks.
    """
    for prefix in ("vault:", "orn:obsidian.document:Notes/", "orn:obsidian.note:Notes/"):
        if doc_rid.startswith(prefix):
            rel = doc_rid[len(prefix):]
            return rel if rel.endswith(".md") else rel + ".md"
    return None


def update_note_canonical_uri(vault_path: str, old_uri: str, new_uri: str) -> bool:
    """Repoint a note's koi.canonical_uri from old_uri to new_uri.

    Step 6 used to only LOG the required change, so a split note kept advertising
    the winner's URI in its own frontmatter (e.g. University of Alberta.md still
    claimed organization-university-of-victoria-...). Rewrites only the exact
    `canonical_uri: <old_uri>` line, and only when it still holds old_uri, so it
    is idempotent and cannot touch anything else in the file.
    """
    path = os.path.join(VAULT_ROOT, vault_path)
    if not os.path.exists(path):
        return False
    with open(path) as f:
        content = f.read()
    needle = f"canonical_uri: {old_uri}"
    if needle not in content:
        return False
    with open(path, "w") as f:
        f.write(content.replace(needle, f"canonical_uri: {new_uri}", 1))
    return True


def generate_uri(entity_type: str, name: str) -> str:
    """Generate a new canonical URI for a split entity."""
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    suffix = uuid.uuid4().hex[:12]
    return f"orn:personal-koi.entity:{entity_type.lower()}-{slug}-{suffix}"


def infer_type(vault_path: str) -> str:
    """Infer entity type from vault path prefix."""
    if vault_path.startswith("People/"):
        return "Person"
    elif vault_path.startswith("Organizations/"):
        return "Organization"
    elif vault_path.startswith("Locations/"):
        return "Location"
    elif vault_path.startswith("Concepts/"):
        return "Concept"
    elif vault_path.startswith("Projects/"):
        return "Project"
    return "Thing"


def parse_frontmatter(filepath: str) -> dict:
    """Parse YAML frontmatter from a markdown file."""
    try:
        with open(filepath, 'r') as f:
            content = f.read()
        if not content.startswith('---'):
            return {}
        end = content.index('---', 3)
        fm = yaml.safe_load(content[3:end]) or {}
        return json_safe(fm)
    except Exception as e:
        logger.warning(f"Failed to parse frontmatter from {filepath}: {e}")
        return {}


def json_safe(obj):
    """Recursively convert date/datetime objects to ISO strings for JSON serialization."""
    import datetime
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [json_safe(v) for v in obj]
    elif isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    return obj


async def split_one(conn, pair, dry_run=False):
    """Split a single collision pair."""
    entity_a_path, entity_b_path = pair

    # Get current shared URI and entity B details
    row = await conn.fetchrow("""
        SELECT canonical_uri, name, entity_type
        FROM entity_rid_mappings
        WHERE vault_path = $1
    """, entity_b_path)

    if not row:
        logger.warning(f"  SKIP: No mapping found for {entity_b_path}")
        return None

    old_uri = row['canonical_uri']
    entity_b_name = row['name']
    entity_b_type = row['entity_type'] or infer_type(entity_b_path)
    normalized = entity_b_name.lower().strip()

    # Idempotency guard: check if Entity B already has its own URI
    # (i.e., its URI differs from Entity A's URI — already split)
    entity_a_row = await conn.fetchrow("""
        SELECT canonical_uri FROM entity_rid_mappings WHERE vault_path = $1
    """, entity_a_path)
    if entity_a_row and old_uri != entity_a_row['canonical_uri']:
        logger.info(f"  SKIP (already split): {entity_b_path} has URI {old_uri}")
        return None

    new_uri = generate_uri(entity_b_type, entity_b_name)

    logger.info(f"  Splitting {entity_b_path}")
    logger.info(f"    Old URI: {old_uri}")
    logger.info(f"    New URI: {new_uri}")

    if dry_run:
        return (old_uri, new_uri)

    # --- Phase B-I: Commit split mappings (single transaction) ---
    async with conn.transaction():
        # Step 1: Create new entity in registry
        await conn.execute("""
            INSERT INTO entity_registry (fuseki_uri, entity_text, entity_type, normalized_text, source, created_at)
            VALUES ($1, $2, $3, $4, 'vault_split', NOW())
            ON CONFLICT (fuseki_uri) DO NOTHING
        """, new_uri, entity_b_name, entity_b_type, normalized)

        # Step 2: Update RID mapping
        await conn.execute("""
            UPDATE entity_rid_mappings
            SET canonical_uri = $1
            WHERE vault_path = $2
        """, new_uri, entity_b_path)

    logger.info(f"    Phase B-I committed (registry + mapping)")

    # --- Phase B-II: Re-sync relationships + fix links ---

    # Step 3: Find affected source files
    affected = await conn.fetch("""
        SELECT DISTINCT r.source_rid, m.canonical_uri
        FROM entity_relationships r
        JOIN entity_rid_mappings m ON m.vault_path = r.source_rid
        WHERE r.subject_uri = $1 OR r.object_uri = $1
    """, old_uri)

    orphans = await conn.fetch("""
        SELECT DISTINCT r.source_rid
        FROM entity_relationships r
        LEFT JOIN entity_rid_mappings m ON m.vault_path = r.source_rid
        WHERE (r.subject_uri = $1 OR r.object_uri = $1)
          AND m.id IS NULL
    """, old_uri)

    # Delete orphan edges
    for orphan in orphans:
        deleted = await conn.execute("""
            DELETE FROM entity_relationships
            WHERE source_rid = $1
              AND (subject_uri = $2 OR object_uri = $2)
        """, orphan['source_rid'], old_uri)
        logger.info(f"    Deleted orphan edges for {orphan['source_rid']}: {deleted}")

    # Step 4: Re-sync relationships from affected source files
    async with httpx.AsyncClient(timeout=30.0) as client:
        for record in affected:
            source_path = record['source_rid']
            entity_uri = record['canonical_uri']
            filepath = os.path.join(VAULT_ROOT, source_path)

            if not os.path.exists(filepath):
                # Source file gone — delete stale edges
                await conn.execute("""
                    DELETE FROM entity_relationships WHERE source_rid = $1
                """, source_path)
                logger.info(f"    Deleted stale edges for missing file: {source_path}")
                continue

            frontmatter = parse_frontmatter(filepath)
            if not frontmatter:
                continue

            try:
                resp = await client.post(f"{API_BASE}/sync-relationships", json={
                    "vault_path": source_path,
                    "entity_uri": entity_uri,
                    "frontmatter": frontmatter,
                })
                if resp.status_code == 200:
                    logger.info(f"    Re-synced relationships for {source_path}")
                else:
                    logger.warning(f"    Sync failed for {source_path}: {resp.status_code} {resp.text}")
            except Exception as e:
                logger.warning(f"    Sync error for {source_path}: {e}")

    # Step 5: Fix document_entity_links
    doc_links = await conn.fetch("""
        SELECT document_rid, context FROM document_entity_links
        WHERE entity_uri = $1
    """, old_uri)

    # Determine which docs reference Entity B by checking wikilinks
    entity_b_wikilink = entity_b_path.replace('.md', '')
    for link in doc_links:
        doc_rid = link['document_rid']
        doc_vault_path = doc_rid_to_vault_path(doc_rid)
        if not doc_vault_path:
            continue  # non-vault source (session/substack) — no file to inspect
        doc_filepath = os.path.join(VAULT_ROOT, doc_vault_path)

        if not os.path.exists(doc_filepath):
            continue

        try:
            with open(doc_filepath, 'r') as f:
                content = f.read()
        except Exception:
            continue

        # Check if document references Entity B (by wikilink or aliased wikilink)
        # Match [[Path]] and [[Path|Alias]]
        if f"[[{entity_b_wikilink}]]" in content or f"[[{entity_b_wikilink}|" in content:
            # Move this link to the new URI
            await conn.execute("""
                DELETE FROM document_entity_links
                WHERE document_rid = $1 AND entity_uri = $2
            """, doc_rid, old_uri)
            await conn.execute("""
                INSERT INTO document_entity_links (document_rid, entity_uri, mention_count, context)
                VALUES ($1, $2, 1, 'split from false merge')
                ON CONFLICT (document_rid, entity_uri) DO NOTHING
            """, doc_rid, new_uri)
            logger.info(f"    Moved doc link: {doc_rid} → new URI")

    return (old_uri, new_uri)


async def main():
    dry_run = "--dry-run" in sys.argv

    db_url = os.environ.get("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")
    conn = await asyncpg.connect(db_url)

    try:
        results = {}
        for pair in COLLISION_PAIRS:
            logger.info(f"\n--- Splitting: {pair[0]} / {pair[1]} ---")
            split = await split_one(conn, pair, dry_run=dry_run)
            if split:
                results[pair[1]] = split  # (old_uri, new_uri) — Step 6 needs both

        logger.info(f"\n{'=' * 60}")
        logger.info(f"Split complete. {len(results)} entities split.")
        if dry_run:
            logger.info("(DRY RUN — no changes made)")

        # Step 6: repoint each split note's koi.canonical_uri. Previously this
        # only logged, leaving split notes advertising the winner's URI forever.
        logger.info(f"\n--- Vault frontmatter updates (Step 6) ---")
        for vault_path, (old_uri, new_uri) in results.items():
            if dry_run:
                logger.info(f"  [dry-run] {vault_path}: koi.canonical_uri → {new_uri}")
            elif update_note_canonical_uri(vault_path, old_uri, new_uri):
                logger.info(f"  {vault_path}: koi.canonical_uri → {new_uri}")
            else:
                logger.info(f"  {vault_path}: no koi.canonical_uri to update (skipped)")

        # Return results for programmatic use
        return results
    finally:
        await conn.close()


if __name__ == "__main__":
    results = asyncio.run(main())
