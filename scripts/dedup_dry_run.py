#!/usr/bin/env python3
"""
FIX-006 Dry-Run Merge Report

Scans the existing entity_registry and proposes merges using the new
Tier 1.x/Tier 2 logic (no mutations by default).

Usage:
    cd koi-processor && set -a; source .env; set +a
    python scripts/dedup_dry_run.py --out merges.csv --format csv
    python scripts/dedup_dry_run.py --out merges.jsonl --format jsonl

Output columns:
    winner_uri, winner_name, winner_type, loser_uri, loser_name, loser_type,
    score, method, reason

Author: Claude Code
Date: 2025-12-23
"""

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict

# Add project root to path
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

try:
    import psycopg2
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

try:
    from rapidfuzz import fuzz
    from rapidfuzz.distance import JaroWinkler
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False

# Import our normalization module
try:
    from knowledge_graph.entity_normalizer import normalize_entity_name, is_single_token_name
    HAS_NORMALIZER = True
except ImportError:
    HAS_NORMALIZER = False
    def normalize_entity_name(name, entity_type=None):
        return name.strip().lower()
    def is_single_token_name(name):
        return len(name.split()) == 1


@dataclass
class MergeProposal:
    """Proposed merge between two entities."""
    winner_uri: str
    winner_name: str
    winner_type: str
    loser_uri: str
    loser_name: str
    loser_type: str
    score: float
    method: str
    reason: str


# FIX-006: Per-type fuzzy thresholds
FUZZY_THRESHOLDS = {
    "PERSON": 0.93,         # Raised from 0.88 to reduce false positives
    "ORGANIZATION": 0.85,
    "PROJECT": 0.85,
    "TECHNOLOGY": 0.85,
    "DEFAULT": 0.85,
}


def get_db_config() -> Dict[str, Any]:
    """Get database configuration from environment."""
    return {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": int(os.getenv("POSTGRES_PORT", 5433)),
        "database": os.getenv("POSTGRES_DB", "eliza"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
    }


def load_canonical_mappings() -> Dict[Tuple[str, str], str]:
    """Load canonical mappings from data/canonical_entities.json."""
    try:
        canonical_path = Path(__file__).parents[1] / "data" / "canonical_entities.json"
        if not canonical_path.exists():
            print(f"Warning: Canonical mappings not found: {canonical_path}")
            return {}

        with open(canonical_path, "r") as f:
            data = json.load(f)

        lookup = {}
        for section, entities in data.get("entities", {}).items():
            for _, entry in entities.items():
                canonical_name = entry.get("canonical_name")
                entity_type = entry.get("entity_type")
                aliases = entry.get("aliases", [])
                if not canonical_name or not entity_type:
                    continue

                entity_type_upper = entity_type.upper()
                normalized_canonical = normalize_entity_name(canonical_name, entity_type_upper)
                lookup[(normalized_canonical, entity_type_upper)] = canonical_name

                for alias in aliases:
                    normalized_alias = normalize_entity_name(alias, entity_type_upper)
                    if (normalized_alias, entity_type_upper) not in lookup:
                        lookup[(normalized_alias, entity_type_upper)] = canonical_name

        print(f"Loaded {len(lookup)} canonical mappings")
        return lookup
    except Exception as e:
        print(f"Warning: Failed to load canonical mappings: {e}")
        return {}


def propose_canonical_registry_direct_merges(
    entities: List[Dict[str, Any]],
    canonical_mappings: Dict[Tuple[str, str], str],
) -> List[MergeProposal]:
    """
    Propose merges directly from the curated canonical registry (no similarity threshold).

    This catches cases where the canonical registry says an alias should resolve to a
    canonical entity even when the strings are not very similar, e.g.:
      - Gregory_Regen -> Gregory Landua
      - Gregory | RND -> Gregory Landua
      - regenfoundation -> Regen Foundation

    Same-type only (canonical mapping is keyed by entity_type).
    """
    if not canonical_mappings:
        return []

    # Index entities by (type, normalized_name)
    by_type_and_norm: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for entity in entities:
        etype = (entity.get("type") or "").upper()
        if not etype:
            continue
        norm = normalize_entity_name(entity.get("name", ""), etype)
        by_type_and_norm.setdefault((etype, norm), []).append(entity)

    proposals: List[MergeProposal] = []
    seen_losers: set = set()

    for entity in entities:
        loser_uri = entity.get("uri")
        if not loser_uri or loser_uri in seen_losers:
            continue

        etype = (entity.get("type") or "").upper()
        if not etype:
            continue

        norm_loser = normalize_entity_name(entity.get("name", ""), etype)
        canonical_name = canonical_mappings.get((norm_loser, etype))
        if not canonical_name:
            continue

        norm_canonical = normalize_entity_name(canonical_name, etype)
        candidates = by_type_and_norm.get((etype, norm_canonical), [])
        if not candidates:
            continue

        # Pick winner deterministically: highest occurrence_count.
        winner = max(candidates, key=lambda x: x.get("occurrence_count", 0) or 0)
        winner_uri = winner.get("uri")
        if not winner_uri or winner_uri == loser_uri:
            continue

        proposals.append(
            MergeProposal(
                winner_uri=winner_uri,
                winner_name=winner.get("name", ""),
                winner_type=winner.get("type", ""),
                loser_uri=loser_uri,
                loser_name=entity.get("name", ""),
                loser_type=entity.get("type", ""),
                score=1.0,
                method="tier1_5_canonical",
                reason="canonical_registry_direct",
            )
        )
        seen_losers.add(loser_uri)

    return proposals


def fetch_entities(conn) -> List[Dict]:
    """Fetch all entities from entity_registry."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, fuseki_uri, entity_text, entity_type, normalized_text, occurrence_count
        FROM entity_registry
        ORDER BY occurrence_count DESC
    """)
    rows = cursor.fetchall()
    cursor.close()

    entities = []
    for row in rows:
        entities.append({
            "id": row[0],
            "uri": row[1],
            "name": row[2],
            "type": row[3],
            "normalized": row[4],
            "occurrence_count": row[5],
        })

    return entities


def find_fuzzy_matches(
    entities: List[Dict],
    canonical_mappings: Dict,
) -> List[MergeProposal]:
    """
    Find fuzzy string matches among entities.

    Groups entities by type, then finds matches within each group.
    """
    proposals = []

    # Group by type
    by_type: Dict[str, List[Dict]] = {}
    for entity in entities:
        etype = entity["type"]
        if etype not in by_type:
            by_type[etype] = []
        by_type[etype].append(entity)

    for entity_type, type_entities in by_type.items():
        print(f"Processing {len(type_entities)} {entity_type} entities...")

        threshold = FUZZY_THRESHOLDS.get(entity_type, FUZZY_THRESHOLDS["DEFAULT"])

        # Track which entities have been marked as losers
        losers = set()

        for i, entity_a in enumerate(type_entities):
            if entity_a["id"] in losers:
                continue

            normalized_a = normalize_entity_name(entity_a["name"], entity_type)

            # Skip single-token PERSONs not in canonical registry
            if entity_type == "PERSON" and is_single_token_name(normalized_a):
                if (normalized_a, entity_type) not in canonical_mappings:
                    continue

            for j in range(i + 1, len(type_entities)):
                entity_b = type_entities[j]
                if entity_b["id"] in losers:
                    continue

                normalized_b = normalize_entity_name(entity_b["name"], entity_type)

                # Skip single-token PERSONs not in canonical registry
                if entity_type == "PERSON" and is_single_token_name(normalized_b):
                    if (normalized_b, entity_type) not in canonical_mappings:
                        continue

                # Length filter (cheap prefilter)
                if abs(len(normalized_a) - len(normalized_b)) > max(len(normalized_a), len(normalized_b)) * 0.5:
                    continue

                # Calculate similarity
                if entity_type == "PERSON":
                    score = JaroWinkler.normalized_similarity(normalized_a, normalized_b)
                else:
                    score = fuzz.token_sort_ratio(normalized_a, normalized_b) / 100.0

                if score >= threshold:
                    # Winner is the one with higher occurrence count
                    if entity_a["occurrence_count"] >= entity_b["occurrence_count"]:
                        winner, loser = entity_a, entity_b
                    else:
                        winner, loser = entity_b, entity_a

                    # Determine reason
                    if normalized_a == normalized_b:
                        reason = "exact_normalized_match"
                        method = "tier1_normalized"
                    elif (normalized_a, entity_type) in canonical_mappings or (normalized_b, entity_type) in canonical_mappings:
                        reason = "canonical_alias_match"
                        method = "tier1_5_canonical"
                    else:
                        reason = f"fuzzy_string_match_{entity_type.lower()}"
                        method = "tier1x_fuzzy"

                    proposals.append(MergeProposal(
                        winner_uri=winner["uri"],
                        winner_name=winner["name"],
                        winner_type=winner["type"],
                        loser_uri=loser["uri"],
                        loser_name=loser["name"],
                        loser_type=loser["type"],
                        score=round(score, 4),
                        method=method,
                        reason=reason,
                    ))
                    losers.add(loser["id"])

    return proposals


def find_cross_type_collisions(entities: List[Dict]) -> List[MergeProposal]:
    """
    Find entities with the same normalized name but different types.

    These are reported as type_conflict, not auto-merged.
    """
    proposals = []

    # Group by normalized name
    by_normalized: Dict[str, List[Dict]] = {}
    for entity in entities:
        normalized = normalize_entity_name(entity["name"], entity["type"])
        if normalized not in by_normalized:
            by_normalized[normalized] = []
        by_normalized[normalized].append(entity)

    # Find cross-type collisions
    for normalized, group in by_normalized.items():
        if len(group) < 2:
            continue

        types_in_group = set(e["type"] for e in group)
        if len(types_in_group) > 1:
            # Sort by occurrence count
            group_sorted = sorted(group, key=lambda x: x["occurrence_count"], reverse=True)

            # Report all pairs with different types
            for i, entity_a in enumerate(group_sorted):
                for entity_b in group_sorted[i+1:]:
                    if entity_a["type"] != entity_b["type"]:
                        proposals.append(MergeProposal(
                            winner_uri=entity_a["uri"],
                            winner_name=entity_a["name"],
                            winner_type=entity_a["type"],
                            loser_uri=entity_b["uri"],
                            loser_name=entity_b["name"],
                            loser_type=entity_b["type"],
                            score=1.0,
                            method="type_conflict",
                            reason="exact_name_cross_type",
                        ))

    return proposals


def write_csv(proposals: List[MergeProposal], output_path: str):
    """Write proposals to CSV file."""
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "winner_uri", "winner_name", "winner_type",
            "loser_uri", "loser_name", "loser_type",
            "score", "method", "reason"
        ])
        writer.writeheader()
        for proposal in proposals:
            writer.writerow(asdict(proposal))
    print(f"Wrote {len(proposals)} proposals to {output_path}")


def write_jsonl(proposals: List[MergeProposal], output_path: str):
    """Write proposals to JSONL file."""
    with open(output_path, "w", encoding="utf-8") as f:
        for proposal in proposals:
            f.write(json.dumps(asdict(proposal)) + "\n")
    print(f"Wrote {len(proposals)} proposals to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="FIX-006 Dry-Run Merge Report")
    parser.add_argument("--out", required=True, help="Output file path")
    parser.add_argument("--format", choices=["csv", "jsonl"], default="csv", help="Output format")
    parser.add_argument("--include-conflicts", action="store_true", help="Include cross-type conflicts")
    args = parser.parse_args()

    if not HAS_PSYCOPG2:
        print("Error: psycopg2 not installed. Run: pip install psycopg2-binary")
        sys.exit(1)

    if not HAS_RAPIDFUZZ:
        print("Error: rapidfuzz not installed. Run: pip install rapidfuzz")
        sys.exit(1)

    print("FIX-006 Dry-Run Merge Report")
    print("=" * 60)

    # Connect to database
    db_config = get_db_config()
    print(f"Connecting to {db_config['host']}:{db_config['port']}/{db_config['database']}...")

    try:
        conn = psycopg2.connect(**db_config)
    except Exception as e:
        print(f"Error: Failed to connect to database: {e}")
        sys.exit(1)

    # Load canonical mappings
    canonical_mappings = load_canonical_mappings()

    # Fetch entities
    print("Fetching entities from entity_registry...")
    entities = fetch_entities(conn)
    print(f"Found {len(entities)} entities")

    # Propose deterministic canonical merges (curated registry, no similarity threshold)
    print("\nProposing canonical-registry merges (deterministic)...")
    canonical_proposals = propose_canonical_registry_direct_merges(entities, canonical_mappings)
    print(f"Found {len(canonical_proposals)} canonical-registry merge proposals")

    # Find fuzzy matches
    print("\nAnalyzing fuzzy string matches...")
    fuzzy_proposals = find_fuzzy_matches(entities, canonical_mappings)
    print(f"Found {len(fuzzy_proposals)} fuzzy match proposals")

    # Find cross-type collisions
    conflict_proposals = []
    if args.include_conflicts:
        print("\nAnalyzing cross-type collisions...")
        conflict_proposals = find_cross_type_collisions(entities)
        print(f"Found {len(conflict_proposals)} cross-type conflicts")

    # Combine proposals
    combined = canonical_proposals + fuzzy_proposals
    # Deduplicate merge proposals by loser_uri (keep first); keep all type_conflict rows.
    seen_loser_uris = set()
    merge_proposals: List[MergeProposal] = []
    for proposal in combined:
        if proposal.loser_uri in seen_loser_uris:
            continue
        seen_loser_uris.add(proposal.loser_uri)
        merge_proposals.append(proposal)

    all_proposals = merge_proposals + conflict_proposals

    # Summary by method
    print("\n" + "=" * 60)
    print("Summary by method:")
    method_counts = {}
    for p in all_proposals:
        method_counts[p.method] = method_counts.get(p.method, 0) + 1
    for method, count in sorted(method_counts.items()):
        print(f"  {method}: {count}")

    # Summary by type
    print("\nSummary by type:")
    type_counts = {}
    for p in all_proposals:
        type_counts[p.winner_type] = type_counts.get(p.winner_type, 0) + 1
    for etype, count in sorted(type_counts.items()):
        print(f"  {etype}: {count}")

    # Write output
    print("\n" + "=" * 60)
    if args.format == "csv":
        write_csv(all_proposals, args.out)
    else:
        write_jsonl(all_proposals, args.out)

    conn.close()
    print("\nDry-run complete. Review proposals before applying merges.")


if __name__ == "__main__":
    main()
