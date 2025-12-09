"""
Analyze Duplicate Clusters Script

Extracts canonical entity candidates from quality review duplicate clusters
and generates the initial canonical registry.

Usage:
    python -m src.knowledge_graph.improvements.analyze_duplicates

Author: Claude Code
Date: 2025-12-08
"""

import json
from pathlib import Path
from typing import Dict, List
from collections import defaultdict
import sys

project_root = Path(__file__).parents[3]


def load_duplicate_clusters() -> List[Dict]:
    """Load duplicate clusters from quality review."""
    clusters_file = project_root / 'reports' / 'kg_quality_review_20251208' / 'duplicate_clusters.json'

    if not clusters_file.exists():
        raise FileNotFoundError(f"Duplicate clusters file not found: {clusters_file}")

    with open(clusters_file) as f:
        return json.load(f)


def analyze_duplicate_clusters() -> List[Dict]:
    """
    Extract canonical candidates from duplicate clusters.

    For each cluster, selects the most common variant as canonical
    and collects all other variants as aliases.
    """

    print("\n" + "=" * 60)
    print("Analyzing Duplicate Clusters")
    print("=" * 60 + "\n")

    clusters = load_duplicate_clusters()
    print(f"Loaded {len(clusters)} duplicate clusters\n")

    canonical_candidates = []

    for cluster in clusters:
        normalized_name = cluster.get('normalized_name', '')
        entity_type = cluster.get('type', 'Unknown')
        variants = cluster.get('variants', [])
        total_occurrences = cluster.get('total_occurrences', 0)

        if not variants:
            continue

        # Choose canonical: prefer Title Case, then most specific variant
        canonical = select_canonical_variant(variants)
        aliases = [v for v in variants if v != canonical]

        canonical_candidates.append({
            'normalized_key': normalized_name.lower().replace(' ', '-'),
            'canonical_name': canonical,
            'aliases': aliases,
            'entity_type': map_entity_type(entity_type),
            'total_occurrences': total_occurrences,
            'variant_count': len(variants)
        })

    # Sort by occurrence count (highest priority)
    canonical_candidates.sort(key=lambda x: x['total_occurrences'], reverse=True)

    # Print top 20
    print("-" * 60)
    print("TOP 20 CANONICAL CANDIDATES")
    print("-" * 60)
    for i, candidate in enumerate(canonical_candidates[:20], 1):
        print(f"{i:2d}. {candidate['canonical_name']:30s} ({candidate['entity_type']})")
        print(f"    Occurrences: {candidate['total_occurrences']:,}")
        print(f"    Aliases: {', '.join(candidate['aliases'][:3])}{'...' if len(candidate['aliases']) > 3 else ''}")
        print()

    # Save candidates for review
    output_file = project_root / 'data' / 'canonical_candidates.json'
    output_file.parent.mkdir(exist_ok=True)

    with open(output_file, 'w') as f:
        json.dump(canonical_candidates, f, indent=2)

    print("-" * 60)
    print(f"Extracted {len(canonical_candidates)} canonical candidates")
    print(f"Saved to: {output_file}")
    print("-" * 60)

    return canonical_candidates


def select_canonical_variant(variants: List[str]) -> str:
    """Select the best canonical variant from a list."""

    if not variants:
        return ""

    # Preference order:
    # 1. Title Case (e.g., "Regen Network")
    # 2. Proper case (first letter capital)
    # 3. Longest variant (most specific)

    title_case = [v for v in variants if v.istitle() or (v[0].isupper() and ' ' in v)]
    if title_case:
        return max(title_case, key=len)

    proper_case = [v for v in variants if v[0].isupper()]
    if proper_case:
        return max(proper_case, key=len)

    # Fall back to longest variant
    return max(variants, key=len)


def map_entity_type(raw_type: str) -> str:
    """Map quality review entity type to standard type."""
    type_map = {
        'Organization': 'ORGANIZATION',
        'Project': 'PROJECT',
        'Person': 'PERSON',
        'Unknown': 'ENTITY'
    }
    return type_map.get(raw_type, 'ENTITY')


def generate_canonical_registry(candidates: List[Dict], min_occurrences: int = 10) -> Dict:
    """
    Generate the canonical_entities.json registry from candidates.

    Args:
        candidates: List of canonical candidates
        min_occurrences: Minimum occurrences to include (default 10)
    """

    registry = {
        "version": "1.0.0",
        "last_updated": "2025-12-08",
        "description": "Canonical entity registry for Regen KOI knowledge graph deduplication",
        "entities": {
            "organizations": {},
            "projects": {},
            "people": {},
            "concepts": {}
        }
    }

    category_map = {
        'ORGANIZATION': 'organizations',
        'PROJECT': 'projects',
        'PERSON': 'people',
        'ENTITY': 'concepts'
    }

    for candidate in candidates:
        if candidate['total_occurrences'] < min_occurrences:
            continue

        category = category_map.get(candidate['entity_type'], 'concepts')
        key = candidate['normalized_key']

        registry['entities'][category][key] = {
            'canonical_name': candidate['canonical_name'],
            'aliases': candidate['aliases'],
            'entity_type': candidate['entity_type'],
            'confidence': 1.0,
            'occurrences': candidate['total_occurrences']
        }

    # Add known Regen entities that might be missing
    registry = add_known_regen_entities(registry)

    return registry


def add_known_regen_entities(registry: Dict) -> Dict:
    """Add known Regen Network entities to the registry."""

    # Key organizations
    known_orgs = {
        'regen-network': {
            'canonical_name': 'Regen Network',
            'aliases': ['regen.network', 'Regen', 'RND', 'Regen Network Development',
                       'Regen Network Inc', 'RegenNetwork', 'REGEN Network'],
            'canonical_uri': 'https://regen.network',
            'entity_type': 'ORGANIZATION',
            'confidence': 1.0,
            'notes': 'Primary organization behind Regen Ledger and Registry'
        },
        'regen-registry': {
            'canonical_name': 'Regen Registry',
            'aliases': ['Registry', 'Regen Registry Program', 'The Registry', 'REGEN REGISTRY'],
            'entity_type': 'ORGANIZATION',
            'confidence': 1.0,
            'notes': 'Carbon credit registry program'
        },
        'regen-foundation': {
            'canonical_name': 'Regen Foundation',
            'aliases': ['Foundation', 'Regen Foundation Inc', 'regen foundation'],
            'entity_type': 'ORGANIZATION',
            'confidence': 1.0,
            'notes': 'Non-profit foundation supporting Regen Network'
        }
    }

    # Key projects
    known_projects = {
        'regen-ledger': {
            'canonical_name': 'Regen Ledger',
            'aliases': ['regen ledger', 'Ledger', 'Regen blockchain', 'Regen chain', 'REGEN LEDGER'],
            'entity_type': 'PROJECT',
            'confidence': 1.0,
            'notes': 'Cosmos SDK-based blockchain'
        },
        'ecocredit-module': {
            'canonical_name': 'Ecocredit Module',
            'aliases': ['ecocredit', 'eco-credit', 'Ecocredit', 'ecocredit module',
                       'regen ecocredit', 'x/ecocredit'],
            'entity_type': 'PROJECT',
            'confidence': 1.0,
            'notes': 'Cosmos SDK module for ecological credits'
        },
        'regen-token': {
            'canonical_name': 'REGEN Token',
            'aliases': ['$REGEN', 'regen token', 'REGEN', 'Regen Token',
                       '$regen', 'REGEN coin', 'Regen Coin'],
            'entity_type': 'PROJECT',
            'confidence': 1.0,
            'notes': 'Native token of Regen Network'
        }
    }

    # Key people
    known_people = {
        'gregory-landua': {
            'canonical_name': 'Gregory Landua',
            'aliases': ['Greg Landua', 'Gregory', 'greg landua'],
            'entity_type': 'PERSON',
            'confidence': 1.0,
            'notes': 'Co-founder of Regen Network'
        },
        'christian-shearer': {
            'canonical_name': 'Christian Shearer',
            'aliases': ['Chris Shearer', 'Christian', 'christian shearer'],
            'entity_type': 'PERSON',
            'confidence': 1.0,
            'notes': 'Co-founder of Regen Network'
        }
    }

    # Key concepts
    known_concepts = {
        'regenerative-agriculture': {
            'canonical_name': 'Regenerative Agriculture',
            'aliases': ['regenerative ag', 'regen ag', 'regenerative farming',
                       'Regenerative agriculture', 'regen agriculture'],
            'entity_type': 'CONCEPT',
            'confidence': 1.0
        },
        'carbon-credit': {
            'canonical_name': 'Carbon Credit',
            'aliases': ['carbon credits', 'Carbon Credits', 'carbon offset',
                       'carbon offsets', 'eco-credit', 'ecocredit'],
            'entity_type': 'CONCEPT',
            'confidence': 1.0
        }
    }

    # Merge known entities (don't overwrite existing)
    for key, data in known_orgs.items():
        if key not in registry['entities']['organizations']:
            registry['entities']['organizations'][key] = data

    for key, data in known_projects.items():
        if key not in registry['entities']['projects']:
            registry['entities']['projects'][key] = data

    for key, data in known_people.items():
        if key not in registry['entities']['people']:
            registry['entities']['people'][key] = data

    for key, data in known_concepts.items():
        if key not in registry['entities']['concepts']:
            registry['entities']['concepts'][key] = data

    return registry


def main():
    """Main entry point."""
    try:
        # Analyze duplicates
        candidates = analyze_duplicate_clusters()

        # Generate registry
        registry = generate_canonical_registry(candidates, min_occurrences=10)

        # Save registry
        registry_file = project_root / 'data' / 'canonical_entities.json'
        with open(registry_file, 'w') as f:
            json.dump(registry, f, indent=2)

        # Count entries
        total_entries = sum(len(cat) for cat in registry['entities'].values())
        total_aliases = sum(
            len(entry.get('aliases', []))
            for cat in registry['entities'].values()
            for entry in cat.values()
        )

        print("\n" + "=" * 60)
        print("CANONICAL REGISTRY GENERATED")
        print("=" * 60)
        print(f"Total canonical entries: {total_entries}")
        print(f"Total aliases mapped: {total_aliases}")
        print(f"Saved to: {registry_file}")
        print("=" * 60)

        return 0

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
