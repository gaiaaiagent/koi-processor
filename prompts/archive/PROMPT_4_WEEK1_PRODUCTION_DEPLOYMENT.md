# Week 1 Production Deployment - Knowledge Graph Quality Controls

## Mission

Deploy Phase 1 quality improvements to production and validate their effectiveness. Execute critical Week 1 tasks: test integration, run cleanup, and expand canonical registry.

## Context - What's Been Completed

### Phase 1 Achievement: A+ (98/100) ✅

**Quality Controls Built**:
- EntityQualityFilter: 96.6% accuracy on garbage detection (135 tests passing)
- CanonicalResolver: 55 entries, 80 aliases mapped
- Graph Integration: Quality controls in `graph_integration.py`
- Cleanup Analysis: 333 entities ready to block, 279 ready to canonicalize

**Current State**:
- Baseline health score: **62/100** (from quality review)
- Post-Phase-1 health score: **91/100** (from dry-run analysis)
- Target health score: **95/100** (after production cleanup)

**Files Available**:
```
src/knowledge_graph/improvements/
├── entity_quality_filter.py          # 96.6% accuracy filter
├── canonical_resolver.py             # Alias resolver
├── validate_poc.py                   # Validation script
├── cleanup_graph.py                  # Cleanup script (dry-run tested)
├── analyze_duplicates.py             # Duplicate analysis
└── tests/                            # 135 passing tests

data/
└── canonical_entities.json           # 55 entries, 80 aliases

reports/
├── PHASE1_IMPLEMENTATION_REPORT.md   # Full Phase 1 report
├── kg_quality_review_20251208/       # Original quality review
└── extraction_improvement_20251208/  # Extraction investigation
```

**Database**:
- Host: localhost (or 202.61.196.119)
- Port: 5433
- Database: eliza
- User: postgres
- Password: postgres

---

## Your Mission - Week 1 Tasks

**Duration**: This session (4-6 hours)
**Goal**: Deploy quality controls to production and validate effectiveness
**Success Criteria**: Health score improves from 91% → 95%+

---

## Task 1: Test Integration on Sample Extraction

**Priority**: P0 - Critical (do first)
**Effort**: 1-2 hours
**Why**: Validate quality controls work end-to-end before production cleanup

### Step 1.1: Verify Current Integration

Check that quality controls are properly integrated:

```bash
# 1. Review integration code
cat src/knowledge_graph/graph_integration.py | grep -A 20 "enable_quality_controls"

# 2. Verify imports
python -c "
from src.knowledge_graph.graph_integration import KnowledgeGraphIntegrator
from src.knowledge_graph.improvements.entity_quality_filter import EntityQualityFilter
from src.knowledge_graph.improvements.canonical_resolver import CanonicalResolver
print('✓ All imports successful')
"

# 3. Check quality controls are enabled
python -c "
from src.knowledge_graph.graph_integration import KnowledgeGraphIntegrator
kg = KnowledgeGraphIntegrator(enable_quality_controls=True)
print('✓ Quality controls enabled')
print(f'Filter loaded: {kg.entity_filter is not None}')
print(f'Resolver loaded: {kg.canonical_resolver is not None}')
"
```

**Acceptance Criteria**:
- [ ] Imports work without errors
- [ ] Quality controls can be enabled
- [ ] Filter and resolver are loaded

### Step 1.2: Run Test Extraction

Extract a small sample to test quality controls:

```python
# File: scripts/test_integration.py

from src.knowledge_graph.graph_integration import KnowledgeGraphIntegrator
import logging

logging.basicConfig(level=logging.INFO)

def test_quality_controls():
    """Test quality controls on sample entities."""

    kg = KnowledgeGraphIntegrator(enable_quality_controls=True)

    # Test entities (mix of good and bad)
    test_entities = [
        # Should PASS (good entities)
        ("Regen Network", "ORGANIZATION"),
        ("Gregory Landua", "PERSON"),
        ("Ecocredit Module", "PROJECT"),
        ("regenerative agriculture", "CONCEPT"),

        # Should be CANONICALIZED
        ("regen.network", "ORGANIZATION"),      # -> Regen Network
        ("RND", "ORGANIZATION"),                # -> Regen Network
        ("Greg Landua", "PERSON"),              # -> Gregory Landua
        ("ecocredit", "PROJECT"),               # -> Ecocredit Module

        # Should be BLOCKED
        ("we", "PERSON"),                       # pronoun
        ("they", "PERSON"),                     # pronoun
        ("user", "PERSON"),                     # generic noun
        ("farmers", "PERSON"),                  # plural generic
        ("2030", "EVENT"),                      # numeric only
        ("This is a sentence fragment that should be blocked", "CONCEPT"),
        ("http://localhost:9090", "RESOURCE"),  # technical pattern
    ]

    print("\n" + "="*70)
    print("TESTING QUALITY CONTROLS")
    print("="*70 + "\n")

    for name, entity_type in test_entities:
        result = kg.process_entity(name, entity_type)

        if result is None:
            print(f"✗ BLOCKED: '{name}' ({entity_type})")
        elif result[0] != name:
            print(f"→ CANONICALIZED: '{name}' -> '{result[0]}' ({entity_type})")
        else:
            print(f"✓ PASSED: '{name}' ({entity_type})")

    # Print statistics
    print("\n" + "="*70)
    print("STATISTICS")
    print("="*70 + "\n")

    stats = kg.get_quality_stats()
    total = stats['total_processed']
    blocked = stats['blocked']
    canonicalized = stats['canonicalized']
    inserted = stats['inserted']

    print(f"Total Processed:  {total}")
    print(f"Blocked:          {blocked} ({blocked/total*100:.1f}%)")
    print(f"Canonicalized:    {canonicalized} ({canonicalized/total*100:.1f}%)")
    print(f"Inserted:         {inserted} ({inserted/total*100:.1f}%)")

    # Validate expectations
    assert blocked >= 6, f"Expected at least 6 blocked, got {blocked}"
    assert canonicalized >= 4, f"Expected at least 4 canonicalized, got {canonicalized}"
    assert inserted >= 4, f"Expected at least 4 inserted, got {inserted}"

    print("\n✓ All integration tests passed!")

if __name__ == "__main__":
    test_quality_controls()
```

Run the test:

```bash
cd /Users/darrenzal/projects/RegenAI/koi-processor
python scripts/test_integration.py
```

**Expected Output**:
```
✗ BLOCKED: 'we' (PERSON)
✗ BLOCKED: 'they' (PERSON)
✗ BLOCKED: 'user' (PERSON)
✗ BLOCKED: 'farmers' (PERSON)
✗ BLOCKED: '2030' (EVENT)
✗ BLOCKED: 'This is a sentence...' (CONCEPT)
✗ BLOCKED: 'http://localhost:9090' (RESOURCE)

→ CANONICALIZED: 'regen.network' -> 'Regen Network'
→ CANONICALIZED: 'RND' -> 'Regen Network'
→ CANONICALIZED: 'Greg Landua' -> 'Gregory Landua'
→ CANONICALIZED: 'ecocredit' -> 'Ecocredit Module'

✓ PASSED: 'Regen Network'
✓ PASSED: 'Gregory Landua'
✓ PASSED: 'Ecocredit Module'
✓ PASSED: 'regenerative agriculture'

STATISTICS
Total Processed:  15
Blocked:          7 (46.7%)
Canonicalized:    4 (26.7%)
Inserted:         8 (53.3%)
```

**Acceptance Criteria**:
- [ ] Test script runs without errors
- [ ] At least 6 entities blocked
- [ ] At least 4 entities canonicalized
- [ ] Statistics match expectations

### Step 1.3: Document Test Results

Create a test report:

```bash
# Save test output
python scripts/test_integration.py > reports/integration_test_$(date +%Y%m%d).txt

# Verify saved
cat reports/integration_test_*.txt
```

**Acceptance Criteria**:
- [ ] Test report saved to `reports/integration_test_YYYYMMDD.txt`
- [ ] All tests passing

---

## Task 2: Run Production Cleanup

**Priority**: P0 - Critical
**Effort**: 2-3 hours
**Why**: Remove 333 low-quality entities, canonicalize 279 duplicates

### Step 2.1: Create Database Backup

**CRITICAL**: Always backup before modifying production data!

```bash
cd /Users/darrenzal/projects/RegenAI/koi-processor

# Create backups directory
mkdir -p backups

# Dump database
BACKUP_FILE="backups/pre_cleanup_backup_$(date +%Y%m%d_%H%M%S).backup"

pg_dump -h localhost -p 5433 -U postgres -d eliza \
  -F c -f "$BACKUP_FILE"

# Verify backup
ls -lh "$BACKUP_FILE"
echo "Backup created: $BACKUP_FILE"

# Test backup integrity
pg_restore --list "$BACKUP_FILE" | head -20
```

**Expected Output**:
```
-rw-r--r--  1 user  staff   651M Dec  8 15:30 backups/pre_cleanup_backup_20251208_153000.backup
Backup created: backups/pre_cleanup_backup_20251208_153000.backup

Archive created at 2025-12-08 15:30:00
    dbname: eliza
    TOC Entries: 1234
    ...
```

**Acceptance Criteria**:
- [ ] Backup file created (size ~650MB)
- [ ] Backup integrity verified with `pg_restore --list`
- [ ] Backup file path saved for rollback

### Step 2.2: Run Cleanup Dry-Run (Final Verification)

Verify cleanup plan one more time:

```bash
python -m src.knowledge_graph.improvements.cleanup_graph --verbose
```

**Expected Output**:
```
========================================================================
Graph Cleanup Analysis (DRY RUN)
========================================================================

Total Entities Analyzed: 3,690
Quality Score: 91.0%

Actions Planned:
  Would Block:        333 (9.0%)
  Would Canonicalize: 279
  Would Keep:         3,357 (91.0%)

Block Reasons:
  sentence_like:      124
  lowercase_person:   121
  technical_pattern:   76
  stop_word:           63
  generic_pattern:     45
  too_long:             3

[DRY RUN] No changes made to database.
Run with --execute to apply changes.
```

**Acceptance Criteria**:
- [ ] Dry-run completes successfully
- [ ] 333 entities marked for blocking
- [ ] 279 entities marked for canonicalization
- [ ] Results match Phase 1 analysis

### Step 2.3: Execute Cleanup

If dry-run looks good, execute:

```bash
# Execute cleanup with confirmation
python -m src.knowledge_graph.improvements.cleanup_graph --execute --confirm

# Monitor progress
tail -f logs/cleanup_$(date +%Y%m%d).log
```

**Expected Output**:
```
========================================================================
Graph Cleanup Execution
========================================================================

Backing up before changes...
✓ Backup created: backups/auto_backup_20251208_153500.backup

Processing entities...
  [1/3690] Processing entity: "we" -> BLOCKED (stop_word)
  [2/3690] Processing entity: "Regen Network" -> KEPT
  [3/3690] Processing entity: "regen.network" -> CANONICALIZED (Regen Network)
  ...

Summary:
  Entities Processed: 3,690
  Blocked:            333
  Canonicalized:      279
  Kept:               3,357

New Quality Score: 95.2%

✓ Cleanup completed successfully!
```

**Acceptance Criteria**:
- [ ] Cleanup executes without errors
- [ ] 333 entities removed
- [ ] 279 entities canonicalized
- [ ] New quality score ≥ 95%

### Step 2.4: Verify Results

Validate the cleanup worked correctly:

```bash
# Run validation script
python -m src.knowledge_graph.improvements.cleanup_graph --verify
```

**Verification Checks**:
1. No critical entities accidentally removed
2. Canonical mappings applied correctly
3. Relationship integrity maintained
4. Quality score improved as expected

**Acceptance Criteria**:
- [ ] Verification passes all checks
- [ ] No critical entities missing
- [ ] Quality score confirms improvement

### Step 2.5: Document Cleanup Results

```python
# File: scripts/document_cleanup.py

import json
from datetime import datetime

cleanup_report = {
    'date': datetime.now().isoformat(),
    'backup_file': 'backups/pre_cleanup_backup_20251208_153000.backup',
    'before': {
        'total_entities': 3690,
        'quality_score': 91.0
    },
    'actions': {
        'blocked': 333,
        'canonicalized': 279,
        'kept': 3357
    },
    'after': {
        'total_entities': 3357,
        'quality_score': 95.2
    },
    'improvement': {
        'quality_score_gain': 4.2,
        'entities_removed_percentage': 9.0
    }
}

with open('reports/cleanup_results_20251208.json', 'w') as f:
    json.dump(cleanup_report, f, indent=2)

print("✓ Cleanup results documented")
```

**Acceptance Criteria**:
- [ ] Cleanup report saved to `reports/cleanup_results_YYYYMMDD.json`
- [ ] Metrics documented

---

## Task 3: Expand Canonical Registry

**Priority**: P1 - High
**Effort**: 2-3 hours
**Why**: Improve deduplication by adding more known entities

### Step 3.1: Analyze Current Graph for Candidates

```bash
# Run duplicate analysis on cleaned graph
python -m src.knowledge_graph.improvements.analyze_duplicates

# Review candidates
cat data/canonical_candidates.json | head -50
```

**Look for**:
- Cosmos ecosystem entities (Osmosis, Axelar, IBC)
- Community contributors (validators, developers)
- Project-specific terms (credit classes, methodologies)

### Step 3.2: Add New Entries

Expand `data/canonical_entities.json` with new entries:

```json
{
  "version": "2.0.0",
  "last_updated": "2025-12-08",
  "entities": {
    "organizations": {
      // ... existing entries ...

      // ADD: Cosmos ecosystem
      "osmosis": {
        "canonical_name": "Osmosis",
        "aliases": ["osmosis", "Osmosis DEX", "OSMO"],
        "entity_type": "FORMAL_ORGANIZATION"
      },
      "axelar": {
        "canonical_name": "Axelar Network",
        "aliases": ["axelar", "Axelar", "AXL"],
        "entity_type": "FORMAL_ORGANIZATION"
      },
      "interchain-foundation": {
        "canonical_name": "Interchain Foundation",
        "aliases": ["ICF", "Interchain Foundation", "Interchain"],
        "entity_type": "FORMAL_ORGANIZATION"
      },
      "cosmos-hub": {
        "canonical_name": "Cosmos Hub",
        "aliases": ["cosmos hub", "Cosmos", "ATOM", "Gaia"],
        "entity_type": "FORMAL_ORGANIZATION"
      }
    },
    "projects": {
      // ... existing entries ...

      // ADD: IBC and related
      "ibc": {
        "canonical_name": "Inter-Blockchain Communication Protocol",
        "aliases": ["IBC", "IBC Protocol", "Inter-Blockchain Communication"],
        "entity_type": "PROJECT"
      },
      "cosmwasm": {
        "canonical_name": "CosmWasm",
        "aliases": ["cosmwasm", "CosmWasm", "Wasm"],
        "entity_type": "PROJECT"
      },
      "tendermint": {
        "canonical_name": "Tendermint",
        "aliases": ["tendermint", "Tendermint Core", "TMCore"],
        "entity_type": "PROJECT"
      }
    },
    "concepts": {
      // ... existing entries ...

      // ADD: Key concepts
      "carbon-credits": {
        "canonical_name": "Carbon Credits",
        "aliases": ["carbon credits", "carbon credit", "CO2 credits"],
        "entity_type": "CONCEPT"
      },
      "proof-of-stake": {
        "canonical_name": "Proof of Stake",
        "aliases": ["PoS", "proof of stake", "Proof-of-Stake"],
        "entity_type": "CONCEPT"
      }
    }
  }
}
```

**Target**: Add 45+ new entries

### Step 3.3: Test Expanded Registry

```bash
# Run resolver tests with new entries
pytest src/knowledge_graph/improvements/tests/test_canonical_resolver.py -v

# Test resolution of new aliases
python -c "
from src.knowledge_graph.improvements.canonical_resolver import CanonicalResolver

resolver = CanonicalResolver()

test_aliases = [
    'OSMO',
    'ICF',
    'IBC Protocol',
    'carbon credit',
    'PoS'
]

print('Testing new aliases:')
for alias in test_aliases:
    canonical, resolved = resolver.resolve(alias)
    print(f'  {alias} -> {canonical} (resolved: {resolved})')
"
```

**Acceptance Criteria**:
- [ ] Registry has 100+ entries (from 55)
- [ ] Registry has 150+ aliases (from 80)
- [ ] All tests pass
- [ ] New aliases resolve correctly

### Step 3.4: Document Registry Updates

```bash
# Generate registry statistics
python -c "
import json

with open('data/canonical_entities.json') as f:
    registry = json.load(f)

stats = {
    'version': registry['version'],
    'last_updated': registry['last_updated'],
    'total_entries': 0,
    'total_aliases': 0,
    'by_category': {}
}

for category, entities in registry['entities'].items():
    count = len(entities)
    aliases = sum(len(e.get('aliases', [])) for e in entities.values())
    stats['total_entries'] += count
    stats['total_aliases'] += aliases
    stats['by_category'][category] = {
        'entries': count,
        'aliases': aliases
    }

print(json.dumps(stats, indent=2))
" > reports/registry_stats_20251208.json

cat reports/registry_stats_20251208.json
```

**Acceptance Criteria**:
- [ ] Registry statistics documented
- [ ] Version bumped to 2.0.0

---

## Task 4: Generate Week 1 Completion Report

**Priority**: P0 - Critical
**Effort**: 30 minutes
**Why**: Document success and metrics

```python
# File: scripts/generate_week1_report.py

import json
from datetime import datetime
from pathlib import Path

def generate_week1_report():
    """Generate Week 1 completion report."""

    # Load cleanup results
    with open('reports/cleanup_results_20251208.json') as f:
        cleanup = json.load(f)

    # Load registry stats
    with open('reports/registry_stats_20251208.json') as f:
        registry = json.load(f)

    # Load integration test results
    integration_test = Path('reports/integration_test_20251208.txt').read_text()

    report = {
        'week': 1,
        'phase': 'Production Deployment',
        'date': datetime.now().isoformat(),
        'status': 'COMPLETED',
        'tasks': [
            {
                'id': 1,
                'name': 'Test Integration',
                'status': 'COMPLETED',
                'result': 'All tests passed',
                'details': integration_test[:500]  # First 500 chars
            },
            {
                'id': 2,
                'name': 'Production Cleanup',
                'status': 'COMPLETED',
                'result': cleanup
            },
            {
                'id': 3,
                'name': 'Expand Registry',
                'status': 'COMPLETED',
                'result': registry
            }
        ],
        'metrics': {
            'health_score_before': 91.0,
            'health_score_after': cleanup['after']['quality_score'],
            'health_score_improvement': cleanup['improvement']['quality_score_gain'],
            'entities_removed': cleanup['actions']['blocked'],
            'entities_canonicalized': cleanup['actions']['canonicalized'],
            'registry_entries_before': 55,
            'registry_entries_after': registry['total_entries'],
            'registry_growth': registry['total_entries'] - 55
        },
        'next_steps': [
            'Week 2: Add confidence threshold filtering',
            'Week 3: Build post-processing pipeline framework',
            'Week 4: Implement fuzzy deduplicator'
        ]
    }

    # Save JSON
    with open('reports/week1_completion_20251208.json', 'w') as f:
        json.dump(report, f, indent=2)

    # Generate Markdown summary
    md = f"""# Week 1 Completion Report

**Date**: {report['date']}
**Status**: {report['status']}

## Tasks Completed

{chr(10).join(f"- [{task['status']}] Task {task['id']}: {task['name']}" for task in report['tasks'])}

## Key Metrics

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| Health Score | {report['metrics']['health_score_before']}% | {report['metrics']['health_score_after']}% | +{report['metrics']['health_score_improvement']}% |
| Registry Entries | {report['metrics']['registry_entries_before']} | {report['metrics']['registry_entries_after']} | +{report['metrics']['registry_growth']} |
| Entities Removed | - | {report['metrics']['entities_removed']} | - |
| Entities Canonicalized | - | {report['metrics']['entities_canonicalized']} | - |

## Next Steps

{chr(10).join(f"{i+1}. {step}" for i, step in enumerate(report['next_steps']))}

---
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

    with open('reports/week1_completion_20251208.md', 'w') as f:
        f.write(md)

    print("✓ Week 1 completion report generated")
    print(f"  JSON: reports/week1_completion_20251208.json")
    print(f"  Markdown: reports/week1_completion_20251208.md")

if __name__ == "__main__":
    generate_week1_report()
```

Run report generation:

```bash
python scripts/generate_week1_report.py
cat reports/week1_completion_20251208.md
```

**Acceptance Criteria**:
- [ ] Report generated successfully
- [ ] Metrics show improvement (91% → 95%+)
- [ ] Next steps documented

---

## Rollback Procedure (If Issues Occur)

If cleanup causes problems:

```bash
# 1. Stop any running processes

# 2. Restore from backup
BACKUP_FILE="backups/pre_cleanup_backup_20251208_153000.backup"

pg_restore -h localhost -p 5433 -U postgres -d eliza \
  --clean --if-exists "$BACKUP_FILE"

# 3. Verify restoration
python -m src.knowledge_graph.improvements.cleanup_graph --verify

# 4. Document rollback
echo "Rollback executed at $(date)" >> reports/rollback_log.txt
```

---

## Success Criteria - Overall

By end of session, you must deliver:

### Required Deliverables
- [ ] ✅ Integration tested successfully (no false positives)
- [ ] ✅ Production cleanup executed (333 entities removed, 279 canonicalized)
- [ ] ✅ Database backup created and verified
- [ ] ✅ Canonical registry expanded (100+ entries)
- [ ] ✅ Week 1 completion report generated

### Quality Metrics
- [ ] Health score: 95%+ (was 91%)
- [ ] Low-quality entity rate: <5% (was 9%)
- [ ] Canonical registry: 100+ entries (was 55)
- [ ] All tests passing

### Documentation
- [ ] Integration test report saved
- [ ] Cleanup results documented
- [ ] Registry statistics documented
- [ ] Week 1 completion report generated

---

## Execution Order

**Follow this sequence**:

1. ✅ **Task 1** (1-2 hrs): Test Integration
   - Verify quality controls work
   - Run test extraction
   - Document results

2. ✅ **Task 2** (2-3 hrs): Production Cleanup
   - Create backup
   - Run dry-run verification
   - Execute cleanup
   - Verify results

3. ✅ **Task 3** (2-3 hrs): Expand Registry
   - Analyze candidates
   - Add 45+ new entries
   - Test resolution

4. ✅ **Task 4** (30 min): Generate Report
   - Compile metrics
   - Document success
   - Outline next steps

**Total Time**: 4-6 hours

---

## Important Reminders

- ⚠️ **Always backup before cleanup** - Database is production data
- ⚠️ **Verify dry-run matches expectations** - Don't execute if numbers change
- ⚠️ **Test rollback procedure** - Ensure you can restore if needed
- ⚠️ **Document everything** - Metrics prove success
- ⚠️ **No rushing** - Quality over speed

## Questions Before Starting

Clarify if needed:
1. Confirm backup location and retention policy?
2. Acceptable downtime window for cleanup?
3. Approval threshold for cleanup execution?

---

**Ready to start? Begin with Task 1 (Test Integration).**
