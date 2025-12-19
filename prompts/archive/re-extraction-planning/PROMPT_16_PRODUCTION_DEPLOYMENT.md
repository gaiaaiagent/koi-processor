# PROMPT 16: Production Deployment - Apply Validated Pipeline

**Date**: 2025-12-09
**Phase**: Production Deployment
**Duration**: 1-2 days
**Agent**: Claude Code (Opus 4.5)

---

## 🎯 Context

**Re-extraction Validation Complete**: ✅ **SUCCESS**

**Final Validation Results**:
- Documents Re-extracted: 1,016 (100% of documents with existing extractions)
- Pass Rate: 97.63% (exceeds 95% threshold)
- Block Rate: 2.86% (within 2-5% target range)
- False Positive Rate: ~0% (zero FPs identified)
- Pipeline Stability: 0.87% variance across weeks (excellent)

**Quality Improvements Ready to Deploy**:
- Remove 431 low-quality entities (2.86% of baseline)
- Apply canonical resolution (~3,500+ entity name normalizations)
- Normalize all entity types (100% standardization)
- Improve overall graph quality from baseline → 97.63%

**Decision**: **STRONG GO for production deployment**

---

## 🎯 Your Mission

Deploy the validated pipeline results to production, replacing baseline extractions with high-quality pipeline-processed entities. This is the final step of the re-extraction project.

**Scope**:
- Replace 1,016 baseline entity extractions with pipeline results
- Update knowledge graph (Fuseki) with improved entities
- Validate deployment success
- Document changes and provide rollback capability

**Tasks**:
1. **Pre-Deployment Validation** (2-3 hours)
   - Verify backups are current
   - Validate pipeline results integrity
   - Create deployment plan
   - Prepare rollback procedure

2. **Database Deployment** (4-6 hours)
   - Update PostgreSQL with pipeline results
   - Replace baseline extractions
   - Verify data integrity
   - Run validation queries

3. **Knowledge Graph Update** (4-6 hours)
   - Update Fuseki graph with new entities
   - Remove blocked entities
   - Apply canonical resolutions
   - Rebuild entity relationships

4. **Post-Deployment Validation** (2-3 hours)
   - Verify entity counts
   - Validate quality improvements
   - Check graph integrity
   - Test queries and retrieval
   - Generate final report

---

## 📋 Task 1: Pre-Deployment Validation (2-3 hours)

### Step 1.1: Verify Backups

**Goal**: Ensure we can rollback if needed

```bash
ssh darren@202.61.196.119

# Check existing backups from earlier in session
ls -lh /tmp/eliza_dump.backup
ls -lh /tmp/fuseki-*.tar.gz

# If backups are old (> 24 hours), create fresh ones
# PostgreSQL backup
PGPASSWORD=postgres pg_dump -h localhost -p 5433 -U postgres -d eliza \
  -F c -f /tmp/eliza_pre_deployment_$(date +%Y%m%d_%H%M%S).backup

# Fuseki backups
sudo tar czf /tmp/fuseki-koi-pre-deployment-$(date +%Y%m%d_%H%M%S).tar.gz \
  -C /var/lib/docker/volumes/fuseki-koi/_data .

sudo tar czf /tmp/fuseki-data-pre-deployment-$(date +%Y%m%d_%H%M%S).tar.gz \
  -C /var/lib/docker/volumes/fuseki-data/_data .

# Verify backups
ls -lh /tmp/*pre-deployment*.* | tail -5
```

**Expected**: Fresh backups created (PostgreSQL ~650MB, Fuseki ~3-4MB)

### Step 1.2: Validate Pipeline Results Integrity

**Goal**: Ensure all pipeline results are valid before deployment

```bash
cd /opt/projects/koi-processor/scripts/reextraction

# Create validation script
cat > validate_pipeline_results.py << 'EOF'
#!/usr/bin/env python3
"""Validate pipeline results before production deployment."""
import json
from pathlib import Path

def validate_results_file(file_path):
    """Validate a single results file."""
    with open(file_path) as f:
        data = json.load(f)

    results = data.get('results', {})

    issues = []
    total_docs = len(results)
    total_baseline = 0
    total_passed = 0
    total_blocked = 0

    for doc_id, doc_data in results.items():
        # Check required fields
        if 'baseline_entities' not in doc_data:
            issues.append(f"{doc_id}: Missing baseline_entities")
        if 'pipeline_results' not in doc_data:
            issues.append(f"{doc_id}: Missing pipeline_results")
        elif 'valid' not in doc_data['pipeline_results']:
            issues.append(f"{doc_id}: Missing pipeline_results.valid")
        elif 'blocked' not in doc_data['pipeline_results']:
            issues.append(f"{doc_id}: Missing pipeline_results.blocked")

        # Count entities
        baseline = len(doc_data.get('baseline_entities', []))
        passed = len(doc_data.get('pipeline_results', {}).get('valid', []))
        blocked = len(doc_data.get('pipeline_results', {}).get('blocked', []))

        total_baseline += baseline
        total_passed += passed
        total_blocked += blocked

        # Check consistency
        if baseline != passed + blocked:
            issues.append(f"{doc_id}: Entity count mismatch (baseline: {baseline}, passed+blocked: {passed + blocked})")

    return {
        'file': file_path.name,
        'docs': total_docs,
        'baseline': total_baseline,
        'passed': total_passed,
        'blocked': total_blocked,
        'issues': issues
    }

# Validate all result files
result_files = [
    Path('week3_results/discourse_all_results.json'),
    Path('week4_results/week4_all_results.json'),
    Path('week5_results/week5_all_results.json')
]

print("="*70)
print("PIPELINE RESULTS VALIDATION")
print("="*70)
print()

all_valid = True
total_issues = []

for file_path in result_files:
    if not file_path.exists():
        print(f"⚠️  {file_path} not found, skipping")
        continue

    print(f"Validating: {file_path}")
    result = validate_results_file(file_path)

    print(f"  Documents: {result['docs']}")
    print(f"  Baseline: {result['baseline']:,}")
    print(f"  Passed: {result['passed']:,}")
    print(f"  Blocked: {result['blocked']:,}")

    if result['issues']:
        print(f"  ❌ Issues found: {len(result['issues'])}")
        all_valid = False
        total_issues.extend(result['issues'])
    else:
        print(f"  ✅ Valid")
    print()

print("="*70)
if all_valid:
    print("✅ ALL PIPELINE RESULTS VALIDATED")
    print("   Ready for production deployment")
else:
    print(f"❌ VALIDATION FAILED: {len(total_issues)} issues found")
    print("\nFirst 10 issues:")
    for issue in total_issues[:10]:
        print(f"  - {issue}")
    if len(total_issues) > 10:
        print(f"  ... and {len(total_issues) - 10} more")
    print("\n⚠️  DO NOT PROCEED WITH DEPLOYMENT")
EOF

python3 validate_pipeline_results.py
```

**Expected**: "✅ ALL PIPELINE RESULTS VALIDATED"

**If Validation Fails**: STOP and fix data integrity issues before proceeding

### Step 1.3: Create Deployment Plan

**Document deployment steps**:

```bash
cat > DEPLOYMENT_PLAN.md << 'EOF'
# Production Deployment Plan

**Date**: 2025-12-09
**Scope**: Deploy validated pipeline results to production
**Documents**: 1,016 re-extracted documents
**Expected Duration**: 8-12 hours

---

## Pre-Deployment Checklist

- [ ] Backups verified (PostgreSQL + Fuseki)
- [ ] Pipeline results validated (1,016 docs, 97.63% pass rate)
- [ ] Rollback procedure documented
- [ ] Deployment window scheduled (low-traffic period recommended)
- [ ] Team notified (if applicable)

---

## Deployment Steps

### Phase 1: Database Update (4-6 hours)

1. Start transaction (for rollback capability)
2. Update `koi_kg_extractions` table:
   - Replace baseline entities with pipeline results
   - Mark updated extractions with pipeline version
3. Commit transaction
4. Validate database integrity

### Phase 2: Knowledge Graph Update (4-6 hours)

1. Backup current graph state
2. Remove blocked entities from graph
3. Update valid entities with canonical names
4. Rebuild entity relationships
5. Validate graph consistency

### Phase 3: Post-Deployment Validation (2-3 hours)

1. Verify entity counts
2. Check quality improvements
3. Test sample queries
4. Generate deployment report

---

## Rollback Procedure

If critical issues discovered:

1. **Database Rollback**:
   ```bash
   PGPASSWORD=postgres pg_restore -h localhost -p 5433 -U postgres \
     -d eliza --clean /tmp/eliza_pre_deployment_YYYYMMDD_HHMMSS.backup
   ```

2. **Fuseki Rollback**:
   ```bash
   docker stop fuseki
   sudo rm -rf /var/lib/docker/volumes/fuseki-koi/_data/*
   sudo tar xzf /tmp/fuseki-koi-pre-deployment-YYYYMMDD_HHMMSS.tar.gz \
     -C /var/lib/docker/volumes/fuseki-koi/_data
   docker start fuseki
   ```

3. **Verify rollback success**

---

## Success Criteria

- ✅ All 1,016 documents updated
- ✅ 431 low-quality entities removed
- ✅ Canonical resolution applied
- ✅ Entity type normalization complete
- ✅ Graph integrity validated
- ✅ No query performance degradation

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Data integrity issues | Low | High | Pre-deployment validation |
| Deployment failure | Low | Medium | Transaction-based updates |
| Performance degradation | Very Low | Medium | Post-deployment testing |
| Rollback needed | Very Low | Medium | Current backups + procedure |

**Overall Risk**: LOW
EOF

cat DEPLOYMENT_PLAN.md
```

---

## 📋 Task 2: Database Deployment (4-6 hours)

### Step 2.1: Create Deployment Script

**Goal**: Update PostgreSQL with pipeline results

```bash
cd /opt/projects/koi-processor/scripts/reextraction

cat > deploy_to_production.py << 'EOF'
#!/usr/bin/env python3
"""Deploy validated pipeline results to production database."""
import json
import sys
from pathlib import Path
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

def connect_db():
    """Connect to PostgreSQL database."""
    return psycopg2.connect(
        host="localhost",
        port=5433,
        database="eliza",
        user="postgres",
        password="postgres",
        cursor_factory=RealDictCursor
    )

def load_all_results():
    """Load all pipeline results."""
    result_files = [
        Path('week3_results/discourse_all_results.json'),
        Path('week4_results/week4_all_results.json'),
        Path('week5_results/week5_all_results.json')
    ]

    all_results = {}
    for file_path in result_files:
        if not file_path.exists():
            continue

        with open(file_path) as f:
            data = json.load(f)
            results = data.get('results', {})
            all_results.update(results)
            print(f"Loaded {file_path.name}: {len(results)} documents")

    return all_results

def get_extraction_id_for_memory(conn, memory_rid):
    """Get extraction ID for a memory RID."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id FROM koi_kg_extractions
        WHERE memory_rid = %s
        ORDER BY created_at DESC
        LIMIT 1
    """, (memory_rid,))

    result = cursor.fetchone()
    return result['id'] if result else None

def update_extraction(conn, extraction_id, pipeline_entities, metadata):
    """Update an extraction with pipeline results."""
    cursor = conn.cursor()

    # Convert entities to JSON
    entities_json = json.dumps(pipeline_entities)

    # Update extraction
    cursor.execute("""
        UPDATE koi_kg_extractions
        SET
            entities = %s::jsonb,
            metadata = metadata || %s::jsonb,
            updated_at = %s
        WHERE id = %s
    """, (
        entities_json,
        json.dumps(metadata),
        datetime.utcnow(),
        extraction_id
    ))

    return cursor.rowcount

def main():
    """Main deployment function."""
    print("="*70)
    print("PRODUCTION DEPLOYMENT")
    print("="*70)
    print()

    # Load pipeline results
    print("Loading pipeline results...")
    all_results = load_all_results()
    print(f"Total documents: {len(all_results)}")
    print()

    # Connect to database
    print("Connecting to database...")
    conn = connect_db()

    # Start transaction (for rollback capability)
    print("Starting transaction...")
    conn.autocommit = False

    try:
        updated_count = 0
        skipped_count = 0
        error_count = 0

        print("\nUpdating extractions...")
        for i, (doc_id, doc_data) in enumerate(all_results.items(), 1):
            # Get extraction ID
            extraction_id = get_extraction_id_for_memory(conn, doc_id)

            if not extraction_id:
                print(f"  ⚠️  No extraction found for {doc_id}, skipping")
                skipped_count += 1
                continue

            # Get pipeline results
            pipeline_results = doc_data.get('pipeline_results', {})
            valid_entities = pipeline_results.get('valid', [])
            blocked_entities = pipeline_results.get('blocked', [])

            # Create metadata
            metadata = {
                'pipeline_version': '1.0',
                'pipeline_pass_rate': len(valid_entities) / (len(valid_entities) + len(blocked_entities)) if (len(valid_entities) + len(blocked_entities)) > 0 else 0,
                'entities_blocked': len(blocked_entities),
                'deployed_at': datetime.utcnow().isoformat()
            }

            # Update extraction
            rows_updated = update_extraction(conn, extraction_id, valid_entities, metadata)

            if rows_updated > 0:
                updated_count += 1
            else:
                error_count += 1
                print(f"  ❌ Failed to update extraction {extraction_id}")

            # Progress update every 100 docs
            if i % 100 == 0:
                print(f"  Progress: {i}/{len(all_results)} documents ({i/len(all_results)*100:.1f}%)")

        # Summary
        print()
        print("="*70)
        print("DEPLOYMENT SUMMARY")
        print("="*70)
        print(f"Updated:  {updated_count}")
        print(f"Skipped:  {skipped_count}")
        print(f"Errors:   {error_count}")
        print()

        # Validate
        if error_count > 0:
            print("❌ ERRORS DETECTED - Rolling back transaction")
            conn.rollback()
            return False

        # Confirm before commit
        print("Ready to commit changes to production.")
        print(f"This will update {updated_count} extractions.")
        print()
        response = input("Proceed with commit? (yes/no): ")

        if response.lower() == 'yes':
            print("\nCommitting transaction...")
            conn.commit()
            print("✅ DEPLOYMENT SUCCESSFUL")
            return True
        else:
            print("\n❌ Deployment cancelled by user - Rolling back")
            conn.rollback()
            return False

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        print("Rolling back transaction...")
        conn.rollback()
        raise

    finally:
        conn.close()

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
EOF

chmod +x deploy_to_production.py
```

### Step 2.2: Run Deployment (DRY RUN first)

**Start with dry run to verify**:

```bash
# Dry run - modify script to skip commit
# (Add --dry-run flag support if desired)

# Then run actual deployment
python3 deploy_to_production.py
```

**Expected**:
- Updated: ~1,016 extractions
- Skipped: 0 (all should have extraction IDs)
- Errors: 0

**User must confirm before commit**

### Step 2.3: Validate Database Update

```bash
# Verify entity counts after deployment
PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza << 'SQL'
-- Count extractions with pipeline metadata
SELECT COUNT(*) as pipeline_extractions
FROM koi_kg_extractions
WHERE metadata->>'pipeline_version' = '1.0';

-- Sample pipeline results
SELECT
    memory_rid,
    jsonb_array_length(entities) as entity_count,
    metadata->>'pipeline_pass_rate' as pass_rate,
    metadata->>'entities_blocked' as blocked_count,
    metadata->>'deployed_at' as deployed_at
FROM koi_kg_extractions
WHERE metadata->>'pipeline_version' = '1.0'
LIMIT 10;
SQL
```

**Expected**: ~1,016 extractions with pipeline metadata

---

## 📋 Task 3: Knowledge Graph Update (4-6 hours)

### Step 3.1: Backup Current Graph State

```bash
# Create timestamped backup
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
docker exec fuseki tdbquery --loc=/fuseki/databases/koi \
  --query='SELECT (COUNT(*) as ?count) WHERE { ?s ?p ?o }' > /tmp/fuseki_triple_count_${TIMESTAMP}.txt

echo "Current triple count saved to /tmp/fuseki_triple_count_${TIMESTAMP}.txt"
```

### Step 3.2: Update Knowledge Graph

**Create graph update script**:

```bash
cd /opt/projects/koi-processor/scripts/reextraction

cat > update_knowledge_graph.py << 'EOF'
#!/usr/bin/env python3
"""Update knowledge graph with pipeline results."""
import json
import sys
from pathlib import Path
from SPARQLWrapper import SPARQLWrapper, POST, JSON
from rdflib import Graph, Namespace, URIRef, Literal
from rdflib.namespace import RDF, RDFS

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

# Fuseki endpoint
FUSEKI_ENDPOINT = "http://localhost:3030/koi"
sparql = SPARQLWrapper(f"{FUSEKI_ENDPOINT}/update")

# Namespaces
REGEN = Namespace("https://regen.network/")
KG = Namespace("https://koi.regen.network/")

def load_all_results():
    """Load all pipeline results."""
    result_files = [
        Path('week3_results/discourse_all_results.json'),
        Path('week4_results/week4_all_results.json'),
        Path('week5_results/week5_all_results.json')
    ]

    all_results = {}
    for file_path in result_files:
        if not file_path.exists():
            continue

        with open(file_path) as f:
            data = json.load(f)
            results = data.get('results', {})
            all_results.update(results)

    return all_results

def remove_blocked_entities(doc_id, blocked_entities):
    """Remove blocked entities from the graph."""
    # Build DELETE query
    delete_clauses = []

    for entity in blocked_entities:
        entity_name = entity.get('name', '')
        entity_type = entity.get('type', '')

        # Create entity URI
        entity_uri = f"<{REGEN}{entity_type}/{entity_name}>"

        delete_clauses.append(f"""
            {entity_uri} ?p ?o .
            ?s ?p2 {entity_uri} .
        """)

    if not delete_clauses:
        return 0

    delete_query = f"""
    PREFIX regen: <{REGEN}>
    PREFIX kg: <{KG}>

    DELETE {{
        {' '.join(delete_clauses)}
    }}
    WHERE {{
        {{
            {' UNION '.join(f'{{ {clause} }}' for clause in delete_clauses)}
        }}
    }}
    """

    sparql.setQuery(delete_query)
    sparql.setMethod(POST)

    try:
        sparql.query()
        return len(blocked_entities)
    except Exception as e:
        print(f"    Error removing entities: {e}")
        return 0

def update_entity_in_graph(entity, doc_id):
    """Update or insert an entity in the graph."""
    # This is a simplified version
    # Full implementation would handle all entity properties

    entity_name = entity.get('name', '')
    entity_type = entity.get('type', '')
    confidence = entity.get('confidence', 0.0)

    # Create entity URI
    entity_uri = URIRef(f"{REGEN}{entity_type}/{entity_name}")

    # Build INSERT query
    insert_query = f"""
    PREFIX regen: <{REGEN}>
    PREFIX kg: <{KG}>
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    INSERT DATA {{
        <{entity_uri}> rdf:type kg:{entity_type} ;
                       rdfs:label "{entity_name}" ;
                       kg:confidence {confidence} ;
                       kg:extractedFrom <{doc_id}> ;
                       kg:pipelineProcessed true .
    }}
    """

    sparql.setQuery(insert_query)
    sparql.setMethod(POST)

    try:
        sparql.query()
        return True
    except Exception as e:
        print(f"    Error updating entity {entity_name}: {e}")
        return False

def main():
    """Main graph update function."""
    print("="*70)
    print("KNOWLEDGE GRAPH UPDATE")
    print("="*70)
    print()

    # Load pipeline results
    print("Loading pipeline results...")
    all_results = load_all_results()
    print(f"Total documents: {len(all_results)}")
    print()

    removed_count = 0
    updated_count = 0
    error_count = 0

    print("Updating knowledge graph...")
    for i, (doc_id, doc_data) in enumerate(all_results.items(), 1):
        pipeline_results = doc_data.get('pipeline_results', {})
        valid_entities = pipeline_results.get('valid', [])
        blocked_entities = pipeline_results.get('blocked', [])

        # Remove blocked entities
        removed = remove_blocked_entities(doc_id, blocked_entities)
        removed_count += removed

        # Update valid entities
        for entity in valid_entities:
            success = update_entity_in_graph(entity, doc_id)
            if success:
                updated_count += 1
            else:
                error_count += 1

        # Progress every 100 docs
        if i % 100 == 0:
            print(f"  Progress: {i}/{len(all_results)} documents ({i/len(all_results)*100:.1f}%)")

    # Summary
    print()
    print("="*70)
    print("GRAPH UPDATE SUMMARY")
    print("="*70)
    print(f"Entities removed: {removed_count}")
    print(f"Entities updated: {updated_count}")
    print(f"Errors:           {error_count}")
    print()

    if error_count > 0:
        print("⚠️  Some errors occurred during update")
        return False
    else:
        print("✅ GRAPH UPDATE SUCCESSFUL")
        return True

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
EOF

chmod +x update_knowledge_graph.py

# Run graph update
python3 update_knowledge_graph.py
```

**Note**: This is a simplified version. Full implementation would handle:
- All entity properties and relationships
- Canonical name resolution
- Entity type normalization
- Relationship updates

### Step 3.3: Validate Graph Update

```bash
# Count triples after update
docker exec fuseki tdbquery --loc=/fuseki/databases/koi \
  --query='SELECT (COUNT(*) as ?count) WHERE { ?s ?p ?o }'

# Compare with baseline
echo "Triple count difference should show ~431 fewer triples (blocked entities removed)"
```

---

## 📋 Task 4: Post-Deployment Validation (2-3 hours)

### Step 4.1: Verify Entity Counts

```bash
cd /opt/projects/koi-processor/scripts/reextraction

cat > validate_deployment.py << 'EOF'
#!/usr/bin/env python3
"""Validate production deployment."""
import psycopg2
from psycopg2.extras import RealDictCursor

def connect_db():
    return psycopg2.connect(
        host="localhost",
        port=5433,
        database="eliza",
        user="postgres",
        password="postgres",
        cursor_factory=RealDictCursor
    )

conn = connect_db()
cursor = conn.cursor()

print("="*70)
print("POST-DEPLOYMENT VALIDATION")
print("="*70)
print()

# Check pipeline extractions
cursor.execute("""
    SELECT COUNT(*) as count
    FROM koi_kg_extractions
    WHERE metadata->>'pipeline_version' = '1.0'
""")
pipeline_count = cursor.fetchone()['count']
print(f"Extractions with pipeline: {pipeline_count}")
print(f"Expected: 1,016")
print(f"Match: {'✅' if pipeline_count >= 1015 else '❌'}")
print()

# Check entity totals
cursor.execute("""
    SELECT
        SUM(jsonb_array_length(entities)) as total_entities,
        AVG(CAST(metadata->>'pipeline_pass_rate' as FLOAT)) as avg_pass_rate,
        SUM(CAST(metadata->>'entities_blocked' as INTEGER)) as total_blocked
    FROM koi_kg_extractions
    WHERE metadata->>'pipeline_version' = '1.0'
""")
stats = cursor.fetchone()
print(f"Total entities: {stats['total_entities']:,}")
print(f"Average pass rate: {stats['avg_pass_rate']*100:.2f}%")
print(f"Total blocked: {stats['total_blocked']}")
print()

# Expected values
expected_entities = 14690  # From Week 3-5 analysis
expected_blocked = 431

entity_match = abs(stats['total_entities'] - expected_entities) < 10
blocked_match = abs(stats['total_blocked'] - expected_blocked) < 10

print("Validation:")
print(f"  Entities match expected: {'✅' if entity_match else '❌'} ({stats['total_entities']} vs {expected_entities})")
print(f"  Blocked match expected: {'✅' if blocked_match else '❌'} ({stats['total_blocked']} vs {expected_blocked})")
print(f"  Pass rate acceptable: {'✅' if stats['avg_pass_rate'] >= 0.95 else '❌'} ({stats['avg_pass_rate']*100:.2f}%)")

if entity_match and blocked_match and stats['avg_pass_rate'] >= 0.95:
    print("\n✅ DEPLOYMENT VALIDATION SUCCESSFUL")
else:
    print("\n⚠️  VALIDATION CONCERNS - Review metrics above")

conn.close()
EOF

python3 validate_deployment.py
```

### Step 4.2: Test Sample Queries

```bash
# Test entity retrieval
PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza << 'SQL'
-- Sample entities from pipeline
SELECT
    e.memory_rid,
    jsonb_array_length(e.entities) as entity_count,
    e.entities->0->>'name' as first_entity,
    e.entities->0->>'type' as entity_type,
    e.metadata->>'pipeline_pass_rate' as pass_rate
FROM koi_kg_extractions e
WHERE e.metadata->>'pipeline_version' = '1.0'
LIMIT 10;
SQL

# Test knowledge graph queries
docker exec fuseki tdbquery --loc=/fuseki/databases/koi \
  --query='PREFIX kg: <https://koi.regen.network/>
           SELECT ?entity ?type
           WHERE {
             ?entity a ?type ;
                     kg:pipelineProcessed true .
           }
           LIMIT 10'
```

### Step 4.3: Generate Deployment Report

```bash
cat > DEPLOYMENT_REPORT.md << 'EOF'
# Production Deployment Report

**Date**: 2025-12-09
**Deployment Duration**: [ACTUAL_DURATION]
**Status**: [SUCCESS/ISSUES]

---

## Deployment Summary

**Scope**:
- Documents Re-extracted: 1,016
- Entities Updated: 14,690
- Entities Blocked: 431
- Quality Improvement: 2.86% low-quality removed

**Results**:
- Database Updates: [SUCCESS/ISSUES]
- Knowledge Graph Updates: [SUCCESS/ISSUES]
- Post-Deployment Validation: [PASS/FAIL]

---

## Quality Improvements

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Entity Count | 15,046 | 14,690 | -431 (-2.86%) |
| Low-Quality Entities | 431 | 0 | -431 (-100%) |
| Canonical Resolution | No | Yes | Applied |
| Type Normalization | Partial | 100% | Complete |

---

## Validation Results

### Database Validation

- ✅ 1,016 extractions updated with pipeline version
- ✅ Average pass rate: 97.63%
- ✅ Entity counts match expected values
- ✅ No data integrity issues

### Knowledge Graph Validation

- ✅ Blocked entities removed
- ✅ Valid entities updated
- ✅ Triple count adjusted correctly
- ✅ Query performance maintained

### Sample Query Tests

- ✅ Entity retrieval working
- ✅ Type-based queries functional
- ✅ Relationship traversal operational

---

## Issues Encountered

[List any issues and their resolutions]

---

## Rollback Capability

Backups available at:
- PostgreSQL: `/tmp/eliza_pre_deployment_YYYYMMDD_HHMMSS.backup` (651MB)
- Fuseki: `/tmp/fuseki-*-pre-deployment-YYYYMMDD_HHMMSS.tar.gz` (~4MB)

Rollback procedure documented in DEPLOYMENT_PLAN.md

---

## Next Steps

1. Monitor production for 24-48 hours
2. Collect user feedback on quality improvements
3. Plan fresh extraction for 637 never-processed documents (optional)
4. Archive re-extraction project documentation

---

## Recommendations

**Immediate**:
- ✅ Deployment successful, no immediate action needed
- Monitor query performance and user feedback

**Future Enhancements**:
1. Fresh extraction for 637 documents without baselines
   - Use OpenAI (GPT-4.1-mini or GPT-5-mini) for entity extraction
   - Pass through validated pipeline
   - Add to knowledge graph

2. Pipeline improvements:
   - Consider adding more canonical resolutions
   - Monitor for new low-quality patterns
   - Tune confidence thresholds if needed

3. Ongoing maintenance:
   - Re-run pipeline on new extractions
   - Periodic quality audits
   - Update whitelist as needed

---

**Deployment Status**: ✅ COMPLETE

**Signed Off**: Claude Code (Opus 4.5)

**Date**: 2025-12-09
EOF

nano DEPLOYMENT_REPORT.md
```

**Manual Step**: Fill in actual deployment metrics and any issues encountered

---

## ✅ Completion Checklist

### Pre-Deployment
- [ ] Backups verified (PostgreSQL + Fuseki)
- [ ] Pipeline results validated (1,016 docs)
- [ ] Deployment plan reviewed
- [ ] Rollback procedure documented

### Database Deployment
- [ ] Deployment script created
- [ ] Production database updated (1,016 extractions)
- [ ] Transaction committed successfully
- [ ] Database integrity validated

### Knowledge Graph Update
- [ ] Graph state backed up
- [ ] Blocked entities removed (431)
- [ ] Valid entities updated (14,690)
- [ ] Graph consistency validated

### Post-Deployment
- [ ] Entity counts verified
- [ ] Sample queries tested
- [ ] Quality improvements confirmed
- [ ] Deployment report generated

---

## 📊 Success Criteria

**Deployment Successful When**:
- ✅ All 1,016 extractions updated in database
- ✅ 431 blocked entities removed from graph
- ✅ 14,690 valid entities updated
- ✅ Average pass rate 97.63%
- ✅ No data integrity issues
- ✅ Query functionality maintained
- ✅ Validation tests pass

---

## 🆘 Common Issues

### Issue 1: Some Extractions Not Found

**Symptom**: Deployment reports skipped extractions

**Solution**:
1. Verify memory RIDs match database
2. Check for RID format differences
3. May need to adjust RID matching logic

### Issue 2: Graph Update Errors

**Symptom**: SPARQL errors during graph update

**Solution**:
1. Check Fuseki service status
2. Verify endpoint connectivity
3. Review SPARQL query syntax
4. May need to batch updates

### Issue 3: Validation Counts Don't Match

**Symptom**: Entity counts differ from expected

**Solution**:
1. Re-run aggregation scripts to verify expected counts
2. Check for missing result files
3. Investigate any data integrity issues
4. May need partial re-deployment

---

## 📚 References

- **Week 3 Results**: `/opt/projects/koi-processor/scripts/reextraction/week3_results/`
- **Week 4 Results**: `/opt/projects/koi-processor/scripts/reextraction/week4_results/`
- **Week 5 Results**: `/opt/projects/koi-processor/scripts/reextraction/week5_results/`
- **Analysis Report**: `/opt/projects/koi-processor/scripts/reextraction/weeks_3_4_analysis_report.md`
- **Pipeline Config**: `/opt/projects/koi-processor/src/knowledge_graph/config/pipeline_config.json`

---

## 🎯 Project Completion

This completes the **Re-extraction Validation Project**:
- ✅ Phase 1: Quality filters and pipeline framework
- ✅ Phase 2: Comprehensive testing and validation
- ✅ Phase 3: Production deployment

**Final Results**:
- Documents: 1,016 (100% of extractable)
- Pass Rate: 97.63%
- Quality Improvement: 2.86% reduction in low-quality entities
- Pipeline: Production-ready and deployed

---

**Status**: 📋 Ready for execution
**Duration**: 1-2 days
**Risk**: LOW (backups + rollback capability)

---

**Agent**: Execute deployment carefully. Verify each step. Use transaction-based updates. Confirm before committing changes. Document any issues encountered.

Good luck! 🚀
