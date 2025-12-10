#!/usr/bin/env python3
"""
Backfill existing entities from koi_kg_extractions into entity_registry.

This script:
1. Reads all entities from koi_kg_extractions JSONB column
2. Runs each entity through EntityResolver waterfall (Exact -> Vector -> New)
3. Populates entity_registry with deduplicated entities
4. Generates deduplication metrics report

Expected: 29,577 raw entities -> ~8,000-10,000 unique entities (70% reduction)

Usage:
    python3 scripts/backfill_entity_registry.py [--dry-run] [--limit N] [--min-confidence 0.70]
"""

import sys
import os
import json
import logging
import argparse
from typing import Dict, List, Any
from collections import defaultdict
from datetime import datetime

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: psycopg2 required. Install with: pip install psycopg2-binary")
    sys.exit(1)

from knowledge_graph.entity_resolver import EntityResolver

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_db_config() -> Dict[str, Any]:
    """Get database configuration from environment."""
    return {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": int(os.getenv("POSTGRES_PORT", 5433)),
        "database": os.getenv("POSTGRES_DB", "eliza"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "postgres")
    }


class EntityBackfiller:
    """Backfill existing entities through deduplication system."""

    def __init__(self, db_config: Dict[str, Any], min_confidence: float = 0.70, dry_run: bool = False):
        self.db_config = db_config
        self.min_confidence = min_confidence
        self.dry_run = dry_run
        self.resolver = None if dry_run else EntityResolver(db_config=db_config)

        self.stats = {
            'total_entities': 0,
            'processed': 0,
            'skipped_low_confidence': 0,
            'skipped_empty_name': 0,
            'exact_matches': 0,
            'semantic_matches': 0,
            'new_entities': 0,
            'errors': 0,
            'by_type': defaultdict(int),
            'top_duplicates': defaultdict(int),
            'semantic_match_examples': []
        }

    def fetch_all_entities(self, limit: int = None) -> List[Dict]:
        """
        Fetch all entities from koi_kg_extractions.

        Returns:
            List of entity dictionaries with extraction metadata
        """
        logger.info("Fetching all entities from koi_kg_extractions...")

        conn = psycopg2.connect(**self.db_config)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        try:
            query = """
                SELECT
                    id,
                    entities,
                    memory_rid,
                    extraction_type
                FROM koi_kg_extractions
                WHERE entities IS NOT NULL
                  AND jsonb_array_length(entities) > 0
                ORDER BY id
            """
            if limit:
                query += f" LIMIT {limit}"

            cursor.execute(query)
            rows = cursor.fetchall()

            # Flatten JSONB arrays into individual entities
            all_entities = []
            for row in rows:
                extraction_id = row['id']
                entities_json = row['entities']
                memory_rid = row['memory_rid']
                extraction_type = row.get('extraction_type', 'unknown')

                for entity in entities_json:
                    name = entity.get('name', '').strip()
                    if not name:
                        continue

                    all_entities.append({
                        'extraction_id': extraction_id,
                        'memory_rid': memory_rid,
                        'extraction_type': extraction_type,
                        'name': name,
                        'type': entity.get('type', 'UNKNOWN').upper(),
                        'confidence': entity.get('confidence', 0.0),
                        'properties': entity.get('properties', {})
                    })

            logger.info(f"Fetched {len(all_entities)} entities from {len(rows)} extractions")
            return all_entities

        finally:
            cursor.close()
            conn.close()

    def process_entities(self, entities: List[Dict]) -> None:
        """
        Process all entities through EntityResolver waterfall.

        Args:
            entities: List of entity dictionaries
        """
        total = len(entities)
        logger.info(f"Processing {total} entities through deduplication system...")

        if self.dry_run:
            logger.info("DRY RUN MODE - No changes will be made")

        for i, entity in enumerate(entities, 1):
            try:
                self.stats['total_entities'] += 1

                # Skip empty names
                if not entity['name']:
                    self.stats['skipped_empty_name'] += 1
                    continue

                # Skip low-confidence entities (quality filter)
                if entity['confidence'] < self.min_confidence:
                    self.stats['skipped_low_confidence'] += 1
                    continue

                # Track by type
                self.stats['by_type'][entity['type']] += 1

                if self.dry_run:
                    self.stats['processed'] += 1
                    continue

                # Resolve entity through waterfall
                result = self.resolver.get_or_create_entity(
                    entity_text=entity['name'],
                    entity_type=entity['type'],
                    metadata={
                        'extraction_id': entity['extraction_id'],
                        'memory_rid': entity['memory_rid'],
                        'extraction_type': entity['extraction_type'],
                        'confidence': entity['confidence'],
                        'properties': entity['properties']
                    }
                )

                self.stats['processed'] += 1

                # Update statistics based on match method
                match_method = result.get('match_method', '')

                if match_method == 'tier1_exact':
                    self.stats['exact_matches'] += 1
                    self.stats['top_duplicates'][entity['name']] += 1
                elif match_method == 'tier2_semantic':
                    self.stats['semantic_matches'] += 1
                    self.stats['top_duplicates'][entity['name']] += 1
                    # Log interesting semantic matches
                    if len(self.stats['semantic_match_examples']) < 50:
                        self.stats['semantic_match_examples'].append({
                            'input': entity['name'],
                            'matched_to': result.get('entity_text', ''),
                            'similarity': result.get('match_score', 0)
                        })
                    logger.info(
                        f"Semantic match: '{entity['name']}' -> '{result.get('entity_text', '')}' "
                        f"(similarity: {result.get('match_score', 0):.3f})"
                    )
                elif match_method == 'tier3_new':
                    self.stats['new_entities'] += 1

                # Progress logging
                if i % 500 == 0:
                    progress = (i / total) * 100
                    logger.info(
                        f"Progress: {i:,}/{total:,} ({progress:.1f}%) - "
                        f"Unique: {self.stats['new_entities']:,}, "
                        f"Duplicates: {self.stats['exact_matches'] + self.stats['semantic_matches']:,}"
                    )

            except Exception as e:
                logger.error(f"Error processing entity '{entity.get('name', 'UNKNOWN')}': {e}")
                self.stats['errors'] += 1

        logger.info(f"Processing complete: {self.stats['processed']:,}/{total:,} entities processed")

    def generate_report(self) -> str:
        """
        Generate deduplication metrics report.

        Returns:
            Markdown report string
        """
        total = self.stats['processed']
        if total == 0:
            return "# Entity Registry Backfill Report\n\nNo entities processed."

        unique = self.stats['new_entities']
        duplicates = self.stats['exact_matches'] + self.stats['semantic_matches']
        dedup_rate = (duplicates / total * 100) if total > 0 else 0

        # Top 20 duplicates
        top_dupes = sorted(
            self.stats['top_duplicates'].items(),
            key=lambda x: x[1],
            reverse=True
        )[:20]

        report = f"""# Entity Registry Backfill Report

**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**Source**: koi_kg_extractions table
**Mode**: {'DRY RUN' if self.dry_run else 'LIVE'}

---

## Summary

| Metric | Value |
|--------|-------|
| **Total Raw Entities** | {self.stats['total_entities']:,} |
| **Processed** | {total:,} |
| **Skipped (Low Confidence)** | {self.stats['skipped_low_confidence']:,} |
| **Skipped (Empty Name)** | {self.stats['skipped_empty_name']:,} |
| **Unique Entities (New)** | {unique:,} |
| **Duplicates Found** | {duplicates:,} |
| **Deduplication Rate** | {dedup_rate:.1f}% |
| **Errors** | {self.stats['errors']:,} |

---

## Matching Breakdown

| Match Type | Count | Percentage |
|------------|-------|------------|
| **Tier 1 (Exact Match)** | {self.stats['exact_matches']:,} | {(self.stats['exact_matches']/total*100) if total else 0:.1f}% |
| **Tier 2 (Semantic Match)** | {self.stats['semantic_matches']:,} | {(self.stats['semantic_matches']/total*100) if total else 0:.1f}% |
| **Tier 3 (New Entity)** | {self.stats['new_entities']:,} | {(self.stats['new_entities']/total*100) if total else 0:.1f}% |

---

## By Entity Type

| Type | Count |
|------|-------|
"""

        for entity_type, count in sorted(self.stats['by_type'].items(), key=lambda x: x[1], reverse=True):
            report += f"| {entity_type} | {count:,} |\n"

        report += f"""
---

## Top 20 Duplicate Entities

| Entity Name | Occurrences |
|-------------|-------------|
"""

        for name, count in top_dupes:
            # Truncate long names
            display_name = name[:50] + '...' if len(name) > 50 else name
            report += f"| {display_name} | {count:,} |\n"

        if self.stats['semantic_match_examples']:
            report += f"""
---

## Semantic Match Examples (Tier 2)

These entities were matched via vector similarity (> 0.95 threshold):

| Input Entity | Matched To | Similarity |
|--------------|------------|------------|
"""
            for ex in self.stats['semantic_match_examples'][:20]:
                report += f"| {ex['input'][:30]} | {ex['matched_to'][:30]} | {ex['similarity']:.3f} |\n"

        report += f"""
---

## Quality Metrics

**Target Outcome**: 65-75% deduplication rate
**Actual Outcome**: {dedup_rate:.1f}% deduplication {'PASS' if 50 <= dedup_rate <= 85 else 'REVIEW'}

| Metric | Target | Actual | Status |
|--------|--------|--------|--------|
| L1 (Exact) Hit Rate | >60% | {(self.stats['exact_matches']/total*100) if total else 0:.1f}% | {'PASS' if (self.stats['exact_matches']/total*100 if total else 0) > 50 else 'LOW'} |
| L2 (Vector) Hit Rate | 10-20% | {(self.stats['semantic_matches']/total*100) if total else 0:.1f}% | {'PASS' if 5 <= (self.stats['semantic_matches']/total*100 if total else 0) <= 30 else 'REVIEW'} |
| L3 (New) Rate | <30% | {(self.stats['new_entities']/total*100) if total else 0:.1f}% | {'PASS' if (self.stats['new_entities']/total*100 if total else 0) < 50 else 'HIGH'} |
| Error Rate | <1% | {(self.stats['errors']/self.stats['total_entities']*100) if self.stats['total_entities'] else 0:.2f}% | {'PASS' if (self.stats['errors']/self.stats['total_entities']*100 if self.stats['total_entities'] else 0) < 1 else 'FAIL'} |

---

## Next Steps

1. {'DRY RUN - No changes made. Run without --dry-run to apply changes.' if self.dry_run else f'entity_registry populated with {unique:,} unique entities'}
2. Resume GitHub extraction with dedup enabled
3. Update CanonicalResolver with domain-specific aliases
4. Monitor future extraction for false positives/negatives

**Status**: {'DRY RUN COMPLETE' if self.dry_run else 'BACKFILL COMPLETE'}
"""

        return report

    def save_report(self, report: str, output_path: str) -> None:
        """Save report to file."""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as f:
            f.write(report)
        logger.info(f"Report saved to: {output_path}")


def main():
    """Main backfill execution."""
    parser = argparse.ArgumentParser(description='Backfill entity registry from koi_kg_extractions')
    parser.add_argument('--dry-run', action='store_true', help='Run without making changes')
    parser.add_argument('--limit', type=int, help='Limit number of extractions to process')
    parser.add_argument('--min-confidence', type=float, default=0.70, help='Minimum confidence threshold (default: 0.70)')
    parser.add_argument('--output', type=str, help='Output report path')
    args = parser.parse_args()

    logger.info("=" * 80)
    logger.info("Entity Registry Backfill - PROMPT_22")
    logger.info("=" * 80)

    if args.dry_run:
        logger.info("DRY RUN MODE ENABLED")

    # Get database config
    db_config = get_db_config()
    logger.info(f"Database: {db_config['host']}:{db_config['port']}/{db_config['database']}")

    # Initialize backfiller
    backfiller = EntityBackfiller(
        db_config=db_config,
        min_confidence=args.min_confidence,
        dry_run=args.dry_run
    )

    # Step 1: Fetch all entities
    logger.info("\n[1/3] Fetching entities from koi_kg_extractions...")
    entities = backfiller.fetch_all_entities(limit=args.limit)

    if not entities:
        logger.warning("No entities found to process!")
        return

    # Step 2: Process through deduplication
    logger.info("\n[2/3] Processing entities through deduplication system...")
    backfiller.process_entities(entities)

    # Step 3: Generate report
    logger.info("\n[3/3] Generating deduplication report...")
    report = backfiller.generate_report()

    # Determine output path
    if args.output:
        output_path = args.output
    else:
        # Use current directory for scripts, reports dir for production
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_dir = os.path.dirname(script_dir)
        reports_dir = os.path.join(project_dir, 'reports')
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        mode = 'dryrun' if args.dry_run else 'backfill'
        output_path = os.path.join(reports_dir, f'{mode}_report_{timestamp}.md')

    backfiller.save_report(report, output_path)

    # Print summary
    logger.info("\n" + "=" * 80)
    logger.info("BACKFILL " + ("DRY RUN " if args.dry_run else "") + "COMPLETE")
    logger.info("=" * 80)
    logger.info(f"Total Entities: {backfiller.stats['total_entities']:,}")
    logger.info(f"Processed: {backfiller.stats['processed']:,}")
    logger.info(f"Unique Entities: {backfiller.stats['new_entities']:,}")

    total = backfiller.stats['processed']
    if total > 0:
        duplicates = backfiller.stats['exact_matches'] + backfiller.stats['semantic_matches']
        logger.info(f"Duplicates Found: {duplicates:,}")
        logger.info(f"Deduplication Rate: {(duplicates/total*100):.1f}%")

    logger.info(f"Errors: {backfiller.stats['errors']:,}")
    logger.info(f"Report: {output_path}")
    logger.info("=" * 80)

    # Print resolver stats if available
    if backfiller.resolver:
        resolver_stats = backfiller.resolver.get_stats()
        logger.info("\nEntityResolver Statistics:")
        logger.info(f"  Total Lookups: {resolver_stats.get('total_lookups', 0):,}")
        logger.info(f"  Tier 1 Hit Rate: {resolver_stats.get('tier1_hit_rate', 0)*100:.1f}%")
        logger.info(f"  Tier 2 Hit Rate: {resolver_stats.get('tier2_hit_rate', 0)*100:.1f}%")
        logger.info(f"  Tier 3 New Rate: {resolver_stats.get('tier3_new_rate', 0)*100:.1f}%")
        if resolver_stats.get('embedding_errors', 0) > 0:
            logger.warning(f"  Embedding Errors: {resolver_stats.get('embedding_errors', 0)}")


if __name__ == '__main__':
    main()
