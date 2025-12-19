# Phase 1 Implementation - Knowledge Graph Quality Improvement

## Mission

Implement critical quality improvements to the Regen KOI knowledge graph based on completed quality review and extraction improvement investigation. Focus on **immediate, high-impact fixes** that prevent future quality issues.

## Context - What's Been Done

### Completed Analysis (Available Results)

**1. Quality Review** (PROMPT_1):
- **Health Score**: 62/100
- **Critical Issues Identified**:
  - 3,690 entities flagged for quality issues
  - 50+ duplicate entity clusters (e.g., "Regen Network" has 4 variants, 911 total occurrences)
  - 25 entities appearing as both Organization AND Project
  - 26 instances of generic nouns ("User", "farmers", "company")
  - 22% of entities below confidence threshold (3,280 entities)
- **Reports Location**: `/Users/darrenzal/projects/RegenAI/koi-processor/reports/kg_quality_review_20251208/`
- **Fix Scripts Available**: `deduplicate_entities.py`

**2. Extraction Improvement Investigation** (PROMPT_2):
- **Grade**: A (95/100)
- **Key Learnings**: YonEarth system uses modular post-processing pipeline with entity quality filters, deduplication, and content-specific extraction profiles
- **POC Delivered**: `EntityQualityFilter` with 108 passing tests
- **Reports Location**: `/Users/darrenzal/projects/RegenAI/koi-processor/reports/extraction_improvement_20251208/`
- **Implementation Roadmap**: 4 phases defined with priorities

### Current System State

**Database**: PostgreSQL with Apache AGE
- Host: localhost (or 202.61.196.119 for remote)
- Port: 5433
- Database: eliza
- User: postgres
- Password: postgres

**Current Graph**:
- 14,706 entities (Org: 6,922 | Project: 5,154 | Person: 2,630)
- 19,608 statements
- 4,547 extractions from 49,027 memories

**Key Files**:
- `src/knowledge_graph/graph_integration.py` - Current graph construction
- `src/knowledge_graph/improvements/entity_quality_filter.py` - POC filter (ready to use)
- `reports/kg_quality_review_20251208/entity_quality_issues.csv` - Flagged entities

## Your Mission - Phase 1: Critical Quality Fixes

**Duration**: This session (4-6 hours)
**Goal**: Implement and validate EntityQualityFilter + Canonical Registry
**Success Metric**: Demonstrate 30%+ reduction in low-quality entities

---

## Task 1: Validate POC Against Quality Review Data

**Priority**: P0 - Critical (do first)
**Effort**: 30 minutes
**Why**: Prove the POC actually fixes the issues found in quality review

### Implementation

Create validation script:

```python
# File: src/knowledge_graph/improvements/validate_poc.py

import pandas as pd
from pathlib import Path
from entity_quality_filter import EntityQualityFilter, FilterConfig

def validate_against_quality_review():
    """Test POC filter against actual flagged entities from quality review."""

    # Load quality review data
    review_file = Path(__file__).parents[3] / 'reports' / 'kg_quality_review_20251208' / 'entity_quality_issues.csv'
    df = pd.read_csv(review_file)

    # Initialize filter
    filter_obj = EntityQualityFilter(FilterConfig())

    # Test each flagged entity
    results = []
    for _, row in df.iterrows():
        entity_name = row['entity_name']
        entity_type = row['entity_type']
        issue_category = row['issue_category']

        is_valid, reasons = filter_obj.filter_with_reasons(entity_name, entity_type)

        results.append({
            'entity_name': entity_name,
            'entity_type': entity_type,
            'quality_review_issue': issue_category,
            'poc_would_block': not is_valid,
            'poc_block_reasons': reasons
        })

    results_df = pd.DataFrame(results)

    # Calculate metrics
    total_flagged = len(df)
    blocked_by_poc = len(results_df[results_df['poc_would_block']])
    block_rate = blocked_by_poc / total_flagged * 100

    # Generate report
    print(f"\n{'='*60}")
    print("POC Validation Against Quality Review")
    print(f"{'='*60}\n")
    print(f"Total Flagged Entities: {total_flagged:,}")
    print(f"POC Would Block: {blocked_by_poc:,} ({block_rate:.1f}%)")
    print(f"Still Need Manual Review: {total_flagged - blocked_by_poc:,}")
    print()

    # Breakdown by issue type
    print("Breakdown by Issue Category:")
    for category in df['issue_category'].unique():
        category_total = len(df[df['issue_category'] == category])
        category_blocked = len(results_df[
            (results_df['quality_review_issue'] == category) &
            (results_df['poc_would_block'])
        ])
        print(f"  {category}: {category_blocked}/{category_total} ({category_blocked/category_total*100:.1f}%)")

    # Save detailed results
    output_file = Path(__file__).parent / 'poc_validation_results.csv'
    results_df.to_csv(output_file, index=False)
    print(f"\nDetailed results saved to: {output_file}")

    return results_df, block_rate

if __name__ == "__main__":
    validate_against_quality_review()
```

### Acceptance Criteria
- [ ] Script runs successfully
- [ ] POC blocks 30%+ of flagged entities
- [ ] Report saved to `poc_validation_results.csv`
- [ ] Metrics documented in validation report

---

## Task 2: Create Canonical Entity Registry

**Priority**: P0 - Critical
**Effort**: 2-3 hours
**Why**: Fixes the 911 "Regen Network" variants and other duplicates

### Implementation

#### Step 2.1: Analyze Current Duplicates

Use quality review duplicate clusters:

```python
# File: src/knowledge_graph/improvements/analyze_duplicates.py

import json
import pandas as pd
from collections import defaultdict

def analyze_duplicate_clusters():
    """Extract top entity variants from quality review for canonical registry."""

    # Load duplicate clusters from quality review
    clusters_file = 'reports/kg_quality_review_20251208/duplicate_clusters.json'
    with open(clusters_file) as f:
        clusters = json.load(f)

    # Extract canonical candidates (most common variant per cluster)
    canonical_candidates = []

    for cluster in clusters:
        variants = cluster['variants']
        occurrence_counts = cluster.get('occurrence_counts', {})

        # Most common variant becomes canonical
        canonical = max(occurrence_counts.items(), key=lambda x: x[1])[0]
        aliases = [v for v in variants if v != canonical]

        canonical_candidates.append({
            'canonical': canonical,
            'aliases': aliases,
            'total_occurrences': sum(occurrence_counts.values()),
            'entity_type': cluster.get('entity_type', 'Unknown')
        })

    # Sort by occurrence count (highest priority)
    canonical_candidates.sort(key=lambda x: x['total_occurrences'], reverse=True)

    # Save for manual review
    with open('data/canonical_candidates.json', 'w') as f:
        json.dump(canonical_candidates[:100], f, indent=2)

    print(f"Extracted {len(canonical_candidates)} canonical entity candidates")
    print(f"Top 100 saved to data/canonical_candidates.json")

    return canonical_candidates

if __name__ == "__main__":
    analyze_duplicate_clusters()
```

#### Step 2.2: Create Canonical Registry

```python
# File: data/canonical_entities.json

{
  "version": "1.0.0",
  "last_updated": "2025-12-08",
  "entities": {
    "organizations": {
      "regen-network": {
        "canonical_name": "Regen Network",
        "canonical_uri": "https://regen.network",
        "aliases": [
          "regen.network",
          "Regen",
          "RND",
          "Regen Network Development",
          "Regen Network Inc",
          "RegenNetwork"
        ],
        "entity_type": "FORMAL_ORGANIZATION",
        "confidence": 1.0,
        "notes": "Primary organization behind Regen Ledger"
      },
      "regen-registry": {
        "canonical_name": "Regen Registry",
        "aliases": [
          "Registry",
          "Regen Registry Program",
          "The Registry"
        ],
        "entity_type": "FORMAL_ORGANIZATION",
        "confidence": 1.0
      },
      "regen-foundation": {
        "canonical_name": "Regen Foundation",
        "aliases": [
          "Foundation",
          "Regen Foundation Inc"
        ],
        "entity_type": "FORMAL_ORGANIZATION",
        "confidence": 1.0
      }
    },
    "projects": {
      "ecocredit": {
        "canonical_name": "Regen Ecocredit Module",
        "aliases": [
          "ecocredit",
          "eco-credit",
          "Ecocredit",
          "ecocredit module",
          "regen ecocredit"
        ],
        "entity_type": "PROJECT",
        "confidence": 1.0,
        "notes": "Cosmos SDK module for ecological credits"
      },
      "regen-ledger": {
        "canonical_name": "Regen Ledger",
        "aliases": [
          "regen ledger",
          "Ledger",
          "Regen blockchain",
          "Regen chain"
        ],
        "entity_type": "PROJECT",
        "confidence": 1.0
      }
    },
    "people": {
      "gregory-landua": {
        "canonical_name": "Gregory Landua",
        "aliases": [
          "Greg Landua",
          "Gregory",
          "Greg"
        ],
        "entity_type": "PERSON",
        "confidence": 1.0,
        "notes": "Co-founder of Regen Network"
      },
      "christian-shearer": {
        "canonical_name": "Christian Shearer",
        "aliases": [
          "Chris Shearer",
          "Christian",
          "Chris"
        ],
        "entity_type": "PERSON",
        "confidence": 1.0
      }
    },
    "concepts": {
      "regenerative-agriculture": {
        "canonical_name": "Regenerative Agriculture",
        "aliases": [
          "regenerative ag",
          "regen ag",
          "regenerative farming",
          "Regenerative agriculture"
        ],
        "entity_type": "CONCEPT",
        "confidence": 1.0
      }
    }
  }
}
```

#### Step 2.3: Implement Canonical Resolver

```python
# File: src/knowledge_graph/improvements/canonical_resolver.py

import json
from pathlib import Path
from typing import Dict, Optional, Tuple

class CanonicalResolver:
    """Resolves entity aliases to canonical forms."""

    def __init__(self, registry_path: Optional[Path] = None):
        if registry_path is None:
            registry_path = Path(__file__).parents[3] / 'data' / 'canonical_entities.json'

        with open(registry_path) as f:
            self.registry = json.load(f)

        # Build reverse lookup: alias -> canonical
        self.alias_to_canonical = {}
        for category, entities in self.registry['entities'].items():
            for entity_id, entity_data in entities.items():
                canonical = entity_data['canonical_name']
                entity_type = entity_data.get('entity_type', 'UNKNOWN')

                # Add canonical name itself
                self.alias_to_canonical[canonical.lower()] = (canonical, entity_type)

                # Add all aliases
                for alias in entity_data.get('aliases', []):
                    self.alias_to_canonical[alias.lower()] = (canonical, entity_type)

    def resolve(self, entity_name: str, entity_type: Optional[str] = None) -> Tuple[str, bool]:
        """
        Resolve entity name to canonical form.

        Returns:
            (canonical_name, was_resolved)
        """
        lookup_key = entity_name.lower()

        if lookup_key in self.alias_to_canonical:
            canonical, canonical_type = self.alias_to_canonical[lookup_key]

            # If type provided, only resolve if types match
            if entity_type and entity_type != canonical_type:
                return entity_name, False

            return canonical, True

        return entity_name, False

    def get_canonical_type(self, entity_name: str) -> Optional[str]:
        """Get canonical entity type for resolved name."""
        lookup_key = entity_name.lower()
        if lookup_key in self.alias_to_canonical:
            _, entity_type = self.alias_to_canonical[lookup_key]
            return entity_type
        return None

# Usage example
if __name__ == "__main__":
    resolver = CanonicalResolver()

    test_cases = [
        "regen.network",
        "RND",
        "Regen Network Development",
        "ecocredit",
        "Greg Landua",
        "regenerative ag"
    ]

    print("Canonical Resolution Test:")
    for name in test_cases:
        canonical, resolved = resolver.resolve(name)
        print(f"  {name} -> {canonical} (resolved: {resolved})")
```

### Acceptance Criteria
- [ ] Registry JSON created with 50+ entries
- [ ] CanonicalResolver class implemented with tests
- [ ] Tested on sample entities from quality review
- [ ] Documentation includes how to add new entries

---

## Task 3: Integrate into graph_integration.py

**Priority**: P0 - Critical
**Effort**: 1-2 hours
**Why**: Make filters active for all future extractions

### Implementation

Modify `src/knowledge_graph/graph_integration.py`:

```python
# Add at top of file
from knowledge_graph.improvements.entity_quality_filter import EntityQualityFilter, FilterConfig
from knowledge_graph.improvements.canonical_resolver import CanonicalResolver

class KnowledgeGraphIntegration:
    def __init__(self):
        # ... existing init code ...

        # Add quality controls
        self.entity_filter = EntityQualityFilter(FilterConfig())
        self.canonical_resolver = CanonicalResolver()
        self.quality_stats = {
            'total_extracted': 0,
            'blocked_by_filter': 0,
            'resolved_to_canonical': 0
        }

    def process_entity(self, entity_name: str, entity_type: str, **kwargs):
        """Process entity with quality controls before graph insertion."""

        self.quality_stats['total_extracted'] += 1

        # Step 1: Quality filter
        is_valid, reasons = self.entity_filter.filter_with_reasons(entity_name, entity_type)
        if not is_valid:
            self.quality_stats['blocked_by_filter'] += 1
            logger.info(f"Blocked entity '{entity_name}': {', '.join(reasons)}")
            return None  # Don't insert

        # Step 2: Canonical resolution
        canonical_name, was_resolved = self.canonical_resolver.resolve(entity_name, entity_type)
        if was_resolved:
            self.quality_stats['resolved_to_canonical'] += 1
            entity_name = canonical_name

        # Step 3: Proceed with existing insertion logic
        return self._insert_entity(entity_name, entity_type, **kwargs)

    def get_quality_report(self) -> dict:
        """Generate quality control metrics report."""
        total = self.quality_stats['total_extracted']
        blocked = self.quality_stats['blocked_by_filter']
        resolved = self.quality_stats['resolved_to_canonical']

        return {
            'total_extracted': total,
            'blocked_by_filter': blocked,
            'blocked_percentage': blocked / total * 100 if total > 0 else 0,
            'resolved_to_canonical': resolved,
            'resolved_percentage': resolved / total * 100 if total > 0 else 0,
            'inserted_to_graph': total - blocked,
            'quality_improvement': (blocked + resolved) / total * 100 if total > 0 else 0
        }
```

### Acceptance Criteria
- [ ] `graph_integration.py` modified with quality controls
- [ ] Integration tested with sample extraction
- [ ] Quality metrics logged and reported
- [ ] No regressions in existing functionality

---

## Task 4: Run Cleanup on Existing Graph

**Priority**: P1 - High
**Effort**: 1-2 hours
**Why**: Improve current graph quality

### Implementation

Create cleanup script:

```python
# File: scripts/cleanup_existing_graph.py

import psycopg2
from knowledge_graph.improvements.entity_quality_filter import EntityQualityFilter, FilterConfig
from knowledge_graph.improvements.canonical_resolver import CanonicalResolver

def cleanup_existing_entities(dry_run=True):
    """Remove low-quality entities and merge duplicates in existing graph."""

    # Connect to database
    conn = psycopg2.connect(
        host="localhost",
        port=5433,
        database="eliza",
        user="postgres",
        password="postgres"
    )

    filter_obj = EntityQualityFilter(FilterConfig())
    resolver = CanonicalResolver()

    cursor = conn.cursor()
    cursor.execute("SET search_path = ag_catalog, '$user', public;")

    # Fetch all entities
    cursor.execute("""
        SELECT * FROM cypher('eliza', $$
            MATCH (n:Entity)
            RETURN id(n), n.name, n.type
        $$) as (id agtype, name agtype, type agtype);
    """)

    entities = cursor.fetchall()

    stats = {
        'total': len(entities),
        'to_remove': 0,
        'to_merge': 0
    }

    removal_candidates = []
    merge_candidates = []

    for entity_id, name, entity_type in entities:
        # Clean agtype formatting
        name = str(name).strip('"')
        entity_type = str(entity_type).strip('"')

        # Check quality filter
        is_valid, reasons = filter_obj.filter_with_reasons(name, entity_type)
        if not is_valid:
            removal_candidates.append((entity_id, name, reasons))
            stats['to_remove'] += 1

        # Check canonical resolution
        canonical, was_resolved = resolver.resolve(name, entity_type)
        if was_resolved:
            merge_candidates.append((entity_id, name, canonical))
            stats['to_merge'] += 1

    # Print report
    print(f"\n{'='*60}")
    print("Graph Cleanup Analysis")
    print(f"{'='*60}\n")
    print(f"Total Entities: {stats['total']:,}")
    print(f"To Remove (low quality): {stats['to_remove']:,} ({stats['to_remove']/stats['total']*100:.1f}%)")
    print(f"To Merge (duplicates): {stats['to_merge']:,} ({stats['to_merge']/stats['total']*100:.1f}%)")
    print(f"\nQuality Improvement: {(stats['to_remove'] + stats['to_merge'])/stats['total']*100:.1f}%")

    if dry_run:
        print("\n[DRY RUN] No changes made to database.")
        print("Run with --execute to apply changes.")
    else:
        # Execute removals and merges
        print("\n[EXECUTING] Applying changes...")
        # ... implementation of actual graph updates ...

    cursor.close()
    conn.close()

    return stats

if __name__ == "__main__":
    import sys
    dry_run = '--execute' not in sys.argv
    cleanup_existing_entities(dry_run=dry_run)
```

### Acceptance Criteria
- [ ] Script runs in dry-run mode successfully
- [ ] Reports entities to remove and merge
- [ ] Generates before/after quality metrics
- [ ] Can be executed with `--execute` flag to apply changes

---

## Task 5: Generate Final Report

**Priority**: P0 - Critical
**Effort**: 30 minutes
**Why**: Document success and next steps

### Implementation

```python
# File: scripts/generate_phase1_report.py

import json
from datetime import datetime
from pathlib import Path

def generate_phase1_report():
    """Generate comprehensive Phase 1 implementation report."""

    report = {
        'phase': 1,
        'title': 'Critical Quality Fixes',
        'date': datetime.now().isoformat(),
        'status': 'COMPLETED',
        'tasks': [
            {
                'id': 1,
                'name': 'POC Validation',
                'status': 'COMPLETED',
                'metrics': load_poc_validation_metrics()
            },
            {
                'id': 2,
                'name': 'Canonical Registry',
                'status': 'COMPLETED',
                'metrics': {
                    'total_entries': count_canonical_entries(),
                    'aliases_mapped': count_aliases()
                }
            },
            {
                'id': 3,
                'name': 'Integration',
                'status': 'COMPLETED',
                'metrics': load_integration_metrics()
            },
            {
                'id': 4,
                'name': 'Graph Cleanup',
                'status': 'COMPLETED',
                'metrics': load_cleanup_metrics()
            }
        ],
        'overall_impact': {
            'quality_improvement': 'X%',  # Calculate from actual metrics
            'entities_blocked': 'X',
            'entities_merged': 'X',
            'new_health_score': 'X/100'
        },
        'next_steps': [
            'Phase 2: Build post-processing pipeline framework',
            'Phase 2: Implement fuzzy deduplicator',
            'Phase 3: Create Discourse extraction profile'
        ]
    }

    # Save JSON report
    output_file = Path(__file__).parents[1] / 'reports' / f"phase1_implementation_{datetime.now().strftime('%Y%m%d')}.json"
    with open(output_file, 'w') as f:
        json.dump(report, f, indent=2)

    # Generate Markdown summary
    md_report = f"""# Phase 1 Implementation Report

## Summary

**Date**: {report['date']}
**Status**: {report['status']}

## Tasks Completed

{chr(10).join(f"- [{task['status']}] Task {task['id']}: {task['name']}" for task in report['tasks'])}

## Overall Impact

- Quality Improvement: {report['overall_impact']['quality_improvement']}
- Entities Blocked: {report['overall_impact']['entities_blocked']}
- Entities Merged: {report['overall_impact']['entities_merged']}
- New Health Score: {report['overall_impact']['new_health_score']} (was 62/100)

## Next Steps

{chr(10).join(f"{i+1}. {step}" for i, step in enumerate(report['next_steps']))}

---

Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

    md_file = output_file.with_suffix('.md')
    with open(md_file, 'w') as f:
        f.write(md_report)

    print(f"Report saved to: {output_file}")
    print(f"Markdown summary: {md_file}")

if __name__ == "__main__":
    generate_phase1_report()
```

### Acceptance Criteria
- [ ] JSON report generated with metrics
- [ ] Markdown summary created
- [ ] Reports saved to `reports/phase1_implementation_YYYYMMDD.*`
- [ ] Metrics show measurable quality improvement

---

## Success Criteria - Overall

By end of this session, you must deliver:

### Required Deliverables
- [ ] ✅ POC validation shows 30%+ of flagged entities would be blocked
- [ ] ✅ Canonical registry with 50+ entries created
- [ ] ✅ CanonicalResolver implemented with tests
- [ ] ✅ Integration into `graph_integration.py` complete
- [ ] ✅ Cleanup script tested on existing graph (dry-run)
- [ ] ✅ Phase 1 report generated with metrics

### Quality Metrics
- [ ] New graph health score: 70+/100 (target 8+ point improvement)
- [ ] Low-quality entity rate: <10% (was 22%)
- [ ] Duplicate entity clusters: <25 (was 50+)
- [ ] Generic noun instances: <10 (was 26)

### Code Quality
- [ ] All new code has unit tests (>80% coverage)
- [ ] Integration tests pass
- [ ] No regressions in existing functionality
- [ ] Code documented with docstrings

---

## Technical Requirements

**Environment**:
- Python 3.10+
- PostgreSQL client with Apache AGE
- Access to quality review reports
- Access to POC code

**Key Files** (already exist):
```
koi-processor/
├── src/knowledge_graph/
│   ├── graph_integration.py              # Modify this
│   └── improvements/                     # POC code here
│       ├── entity_quality_filter.py      # Use this
│       └── tests/
├── reports/
│   ├── kg_quality_review_20251208/       # Reference this
│   │   ├── entity_quality_issues.csv
│   │   └── duplicate_clusters.json
│   └── extraction_improvement_20251208/  # Reference this
│       └── 04_IMPLEMENTATION_ROADMAP.md
├── data/                                  # Create registry here
│   └── canonical_entities.json           # Create this
└── scripts/                               # Add cleanup scripts here
```

**Database Access**:
```python
import psycopg2

conn = psycopg2.connect(
    host="localhost",
    port=5433,
    database="eliza",
    user="postgres",
    password="postgres"
)
```

---

## Execution Order

**Follow this sequence**:

1. ✅ **Task 1** (30 min): Validate POC against quality review
   - Proves POC works on real data
   - Establishes baseline metrics

2. ✅ **Task 2** (2-3 hrs): Create canonical registry
   - Analyze duplicates from quality review
   - Build registry with 50+ entries
   - Implement CanonicalResolver

3. ✅ **Task 3** (1-2 hrs): Integrate into graph_integration.py
   - Add quality filter + canonical resolver
   - Test integration

4. ✅ **Task 4** (1-2 hrs): Run cleanup on existing graph
   - Dry-run analysis
   - Generate cleanup report

5. ✅ **Task 5** (30 min): Generate final report
   - Compile all metrics
   - Document success
   - Outline Phase 2 next steps

**Total Time**: 4-6 hours

---

## Important Notes

- **Don't modify production graph without dry-run first**
- **Save all metrics** for before/after comparison
- **Document any issues or blockers** encountered
- **If a task fails**, document why and continue with others
- **Test everything** before marking complete

## Questions for Clarification

Before starting:
1. Should cleanup script be run on production immediately or wait for review?
2. Are there specific entities that must NOT be merged/removed?
3. Should we notify users before making graph changes?

## Getting Help

If you encounter issues:
- Quality review reports are in `reports/kg_quality_review_20251208/`
- POC code is in `src/knowledge_graph/improvements/`
- Implementation roadmap is in `reports/extraction_improvement_20251208/04_IMPLEMENTATION_ROADMAP.md`
- Test against quality review data first to validate approach

---

**Ready to start? Begin with Task 1 (POC Validation).**
