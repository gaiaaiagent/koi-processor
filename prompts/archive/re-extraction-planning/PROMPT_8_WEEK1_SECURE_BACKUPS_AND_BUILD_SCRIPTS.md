# PROMPT 8: Week 1 - Secure Backups & Build Re-extraction Scripts

**Date**: 2025-12-08
**Phase**: Option A Re-extraction - Week 1
**Duration**: 5 days
**Agent**: Claude Code (Opus 4.5)

---

## 🎯 Context

**What's Complete**:
- ✅ Phase 1: Quality controls (99.7% quality score)
- ✅ Phase 2a: Confidence filtering (49 tests)
- ✅ Phase 2b: Pipeline framework (103 tests)
- ✅ Graph integration complete (18 tests)
- ✅ **Total: 121 tests passing** (45 framework + 58 modules + 18 integration)
- ✅ Repository cleanup and push to GitHub
- ✅ Backups created (PostgreSQL 651MB, Fuseki 3.6MB+1.2KB in `/tmp/`)

**Current State**:
- Production server: `darren@202.61.196.119:5433`
- Database: PostgreSQL (eliza)
- Graph store: Apache Jena Fuseki (http://localhost:3030/koi)
- Documents to re-extract: **3,497** (Discourse 1,407, GitHub 172, Web 452, Notion 47, Other 1,419)
- Pipeline operational with 5 modules
- Quality: 99.7%

**The Plan**:
- **Option A**: Incremental re-extraction over 6 weeks
- Week 1 (this prompt): Secure backups + build scripts
- Week 2: Pilot re-extraction (100 documents)
- Weeks 3-6: Full re-extraction + validation

**See**: `OPTION_A_REEXTRACTION_ULTRATHINK_PLAN.md` for complete 6-week plan

---

## 🎯 Your Mission

Build the infrastructure for re-extraction:

1. **Secure backups permanently** (Day 1-2)
   - Move backups from `/tmp/` to permanent storage
   - Verify backup integrity (test restore)
   - Document backup locations

2. **Build 4 re-extraction scripts** (Day 3-4)
   - `select_pilot_documents.py` - Select 100 representative documents
   - `extract_baseline_entities.py` - Extract current entities from graph
   - `reextract_pilot.py` - Re-extract with pipeline enabled
   - `compare_extractions.py` - Compare old vs new extractions

3. **Test scripts locally** (Day 5)
   - Run on 10 test documents (not pilot set)
   - Verify results and fix bugs

---

## 📋 Task 1: Secure Backups (Day 1-2)

### Current Backup Status

**Created**: 2025-12-09 00:27 UTC
**Location**: `/tmp/` (TEMPORARY - need to move!)

| Component | Size | Status | Location |
|-----------|------|--------|----------|
| PostgreSQL (eliza) | 651MB | ✅ Complete | `/tmp/eliza_dump.backup` |
| Fuseki Data Volume | 3.6MB | ✅ Complete | `/tmp/fuseki-data-volume.tar.gz` |
| Fuseki KOI Volume | 1.2KB | ✅ Complete | `/tmp/fuseki-koi-volume.tar.gz` |

### Step 1.1: Choose Storage Location

**Option A: Download to local machine** (RECOMMENDED)
```bash
# Create local backup directory
mkdir -p ~/backups/koi_pre_reextraction_20251208

# Download backups
scp darren@202.61.196.119:/tmp/eliza_dump.backup ~/backups/koi_pre_reextraction_20251208/
scp darren@202.61.196.119:/tmp/fuseki-data-volume.tar.gz ~/backups/koi_pre_reextraction_20251208/
scp darren@202.61.196.119:/tmp/fuseki-koi-volume.tar.gz ~/backups/koi_pre_reextraction_20251208/

# Verify download
ls -lh ~/backups/koi_pre_reextraction_20251208/
```

**Option B: Move to server permanent storage**
```bash
ssh darren@202.61.196.119 "
  # Create backup directory
  sudo mkdir -p /opt/backups/pre_reextraction_20251208

  # Move backups from /tmp/
  sudo mv /tmp/eliza_dump.backup /opt/backups/pre_reextraction_20251208/
  sudo mv /tmp/fuseki-data-volume.tar.gz /opt/backups/pre_reextraction_20251208/
  sudo mv /tmp/fuseki-koi-volume.tar.gz /opt/backups/pre_reextraction_20251208/

  # Set permissions (read-only for safety)
  sudo chmod 440 /opt/backups/pre_reextraction_20251208/*

  # Verify
  ls -lh /opt/backups/pre_reextraction_20251208/
"
```

**Option C: Upload to cloud storage** (if available)
```bash
# AWS S3 example
aws s3 cp /tmp/eliza_dump.backup s3://your-bucket/koi-backups/pre_reextraction_20251208/
aws s3 cp /tmp/fuseki-*.tar.gz s3://your-bucket/koi-backups/pre_reextraction_20251208/
```

### Step 1.2: Verify Backup Integrity

**Test PostgreSQL backup**:
```bash
# Local test (if downloaded)
pg_restore --list ~/backups/koi_pre_reextraction_20251208/eliza_dump.backup | head -20

# Should show:
# - Archive entry count
# - Tables: cat_receipts, documents, chunks, knowledge_graph_entities, etc.
# - No errors
```

**Test Fuseki backups**:
```bash
# View contents
tar -tzf ~/backups/koi_pre_reextraction_20251208/fuseki-data-volume.tar.gz | head -20
tar -tzf ~/backups/koi_pre_reextraction_20251208/fuseki-koi-volume.tar.gz

# Should show:
# - TDB2 database files (.dat, .idn, .info)
# - No corruption messages
```

### Step 1.3: Test Restore Procedure (Dry Run)

**IMPORTANT**: Test on a separate database, NOT production!

```bash
# Create test database
ssh darren@202.61.196.119 "
  PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -c 'CREATE DATABASE eliza_restore_test;'
"

# Test restore (dry run)
ssh darren@202.61.196.119 "
  PGPASSWORD=postgres pg_restore --list /tmp/eliza_dump.backup > /tmp/restore_test.log
  cat /tmp/restore_test.log
"

# If successful, actual restore to test DB:
ssh darren@202.61.196.119 "
  PGPASSWORD=postgres pg_restore -h localhost -p 5433 -U postgres -d eliza_restore_test /tmp/eliza_dump.backup
"

# Verify tables
ssh darren@202.61.196.119 "
  PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza_restore_test -c '\\dt'
"

# Clean up test DB
ssh darren@202.61.196.119 "
  PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -c 'DROP DATABASE eliza_restore_test;'
"
```

### Step 1.4: Document Backup Locations

Create `BACKUPS.md` in the root directory:

```markdown
# Backup Documentation

**Date Created**: 2025-12-08
**Purpose**: Pre-re-extraction baseline

## Backup Locations

| Component | Size | Location | Status |
|-----------|------|----------|--------|
| PostgreSQL (eliza) | 651MB | [location] | ✅ Verified |
| Fuseki Data Volume | 3.6MB | [location] | ✅ Verified |
| Fuseki KOI Volume | 1.2KB | [location] | ✅ Verified |

## Restore Instructions

### PostgreSQL
\`\`\`bash
PGPASSWORD=postgres pg_restore -h localhost -p 5433 -U postgres -d eliza [backup_file]
\`\`\`

### Fuseki
\`\`\`bash
# Stop Fuseki
docker-compose stop fuseki

# Restore data volume
sudo tar xzf [fuseki-data-backup] -C /var/lib/docker/volumes/fuseki-data/_data

# Restore KOI volume
sudo tar xzf [fuseki-koi-backup] -C /var/lib/docker/volumes/fuseki-koi/_data

# Start Fuseki
docker-compose start fuseki
\`\`\`

## Verification

- [ ] PostgreSQL backup tested
- [ ] Fuseki backups tested
- [ ] Restore procedure documented
- [ ] Backup locations secured
```

### Success Criteria (Day 1-2)

- [ ] Backups moved to permanent storage
- [ ] PostgreSQL backup verified (can list contents)
- [ ] Fuseki backups verified (can list files)
- [ ] Test restore successful (on test database)
- [ ] BACKUPS.md created with restore instructions
- [ ] Backup locations documented

---

## 📋 Task 2: Build Re-extraction Scripts (Day 3-4)

### Script 1: `select_pilot_documents.py`

**Purpose**: Select 100 representative documents for pilot re-extraction

**Location**: `koi-processor/scripts/select_pilot_documents.py`

**Requirements**:
- Stratified sampling: 50 high-quality, 30 medium, 20 low-quality
- Based on existing entity confidence scores
- From Discourse sources (easier to validate)
- Output: `pilot_documents.json`

**Implementation**:

```python
#!/usr/bin/env python3
"""
Select 100 representative documents for pilot re-extraction.

Stratified sampling:
- 50 high-quality entities (confidence > 0.85)
- 30 medium-quality (0.70-0.85)
- 20 low-quality (< 0.70)

Output: pilot_documents.json
"""

import json
import sys
from pathlib import Path
from typing import List, Dict

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import psycopg2
from psycopg2.extras import RealDictCursor


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


def get_documents_by_quality(conn, min_conf: float, max_conf: float, limit: int) -> List[Dict]:
    """
    Get documents with entities in the specified confidence range.

    Returns documents from Discourse sources for easier validation.
    """
    cursor = conn.cursor()

    query = """
    WITH doc_avg_confidence AS (
        SELECT
            c.document_id,
            d.source_type,
            d.source_url,
            d.title,
            AVG((e.metadata->>'confidence')::float) as avg_confidence,
            COUNT(e.id) as entity_count
        FROM knowledge_graph_entities e
        JOIN chunks c ON e.chunk_id = c.id
        JOIN documents d ON c.document_id = d.id
        WHERE
            d.source_type IN ('discourse-forum', 'discourse-sensor')
            AND (e.metadata->>'confidence')::float BETWEEN %s AND %s
        GROUP BY c.document_id, d.source_type, d.source_url, d.title
        HAVING COUNT(e.id) >= 3  -- At least 3 entities
    )
    SELECT
        document_id,
        source_type,
        source_url,
        title,
        avg_confidence,
        entity_count
    FROM doc_avg_confidence
    ORDER BY RANDOM()
    LIMIT %s;
    """

    cursor.execute(query, (min_conf, max_conf, limit))
    return cursor.fetchall()


def select_pilot_documents() -> List[Dict]:
    """
    Select 100 representative documents using stratified sampling.

    Returns:
        List of document dictionaries with metadata
    """
    conn = connect_db()

    print("=" * 70)
    print("PILOT DOCUMENT SELECTION")
    print("=" * 70)
    print()

    # Stratified sampling
    print("Selecting documents by quality tier...")

    # High quality (confidence > 0.85)
    print("  - High quality (conf > 0.85): 50 documents")
    high_quality = get_documents_by_quality(conn, 0.85, 1.0, 50)

    # Medium quality (confidence 0.70-0.85)
    print("  - Medium quality (conf 0.70-0.85): 30 documents")
    medium_quality = get_documents_by_quality(conn, 0.70, 0.85, 30)

    # Low quality (confidence < 0.70)
    print("  - Low quality (conf < 0.70): 20 documents")
    low_quality = get_documents_by_quality(conn, 0.0, 0.70, 20)

    conn.close()

    # Combine
    pilot_docs = []
    pilot_docs.extend([{**doc, 'quality_tier': 'high'} for doc in high_quality])
    pilot_docs.extend([{**doc, 'quality_tier': 'medium'} for doc in medium_quality])
    pilot_docs.extend([{**doc, 'quality_tier': 'low'} for doc in low_quality])

    print()
    print(f"Selected {len(pilot_docs)} documents:")
    print(f"  - High: {len(high_quality)}")
    print(f"  - Medium: {len(medium_quality)}")
    print(f"  - Low: {len(low_quality)}")

    return pilot_docs


def save_pilot_documents(documents: List[Dict], output_path: str):
    """Save pilot documents to JSON file."""
    # Convert to serializable format
    serializable_docs = []
    for doc in documents:
        serializable_docs.append({
            'document_id': int(doc['document_id']),
            'source_type': doc['source_type'],
            'source_url': doc['source_url'],
            'title': doc['title'],
            'avg_confidence': float(doc['avg_confidence']),
            'entity_count': int(doc['entity_count']),
            'quality_tier': doc['quality_tier']
        })

    with open(output_path, 'w') as f:
        json.dump(serializable_docs, f, indent=2)

    print()
    print(f"Saved to: {output_path}")


def main():
    """Main execution."""
    output_path = Path(__file__).parent / 'pilot_documents.json'

    try:
        documents = select_pilot_documents()
        save_pilot_documents(documents, str(output_path))

        print()
        print("=" * 70)
        print("SELECTION COMPLETE")
        print("=" * 70)
        print()
        print("Next steps:")
        print("  1. Review pilot_documents.json")
        print("  2. Run extract_baseline_entities.py")

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
```

### Script 2: `extract_baseline_entities.py`

**Purpose**: Extract current entities from knowledge graph for pilot documents

**Location**: `koi-processor/scripts/extract_baseline_entities.py`

**Requirements**:
- Query Fuseki for entities related to pilot documents
- Get entity names, types, confidence scores, relationships
- Output: `baseline_entities.json`

**Implementation**:

```python
#!/usr/bin/env python3
"""
Extract current entities from knowledge graph for pilot documents.

Input: pilot_documents.json
Output: baseline_entities.json
"""

import json
import sys
from pathlib import Path
from typing import List, Dict

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import psycopg2
from psycopg2.extras import RealDictCursor


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


def load_pilot_documents(input_path: str) -> List[Dict]:
    """Load pilot documents from JSON."""
    with open(input_path, 'r') as f:
        return json.load(f)


def extract_entities_for_document(conn, document_id: int) -> List[Dict]:
    """
    Extract all entities for a document from PostgreSQL.

    Note: This gets entities from the database, which were stored during extraction.
    They may or may not be in the Fuseki graph depending on quality filtering.
    """
    cursor = conn.cursor()

    query = """
    SELECT
        e.id,
        e.name,
        e.type,
        e.metadata,
        c.chunk_index,
        c.chunk_text
    FROM knowledge_graph_entities e
    JOIN chunks c ON e.chunk_id = c.id
    WHERE c.document_id = %s
    ORDER BY c.chunk_index, e.id;
    """

    cursor.execute(query, (document_id,))
    return cursor.fetchall()


def extract_baseline_entities(pilot_docs: List[Dict]) -> Dict:
    """
    Extract current entities for all pilot documents.

    Returns:
        Dict with document_id -> entities mapping
    """
    conn = connect_db()

    print("=" * 70)
    print("BASELINE ENTITY EXTRACTION")
    print("=" * 70)
    print()

    baseline = {}
    total_entities = 0

    for i, doc in enumerate(pilot_docs, 1):
        doc_id = doc['document_id']
        print(f"Processing {i}/{len(pilot_docs)}: {doc['title'][:50]}...")

        entities = extract_entities_for_document(conn, doc_id)

        # Convert to serializable format
        entity_list = []
        for e in entities:
            entity_list.append({
                'id': int(e['id']),
                'name': e['name'],
                'type': e['type'],
                'confidence': e['metadata'].get('confidence') if e['metadata'] else None,
                'metadata': e['metadata'],
                'chunk_index': int(e['chunk_index'])
            })

        baseline[str(doc_id)] = {
            'document': doc,
            'entities': entity_list,
            'entity_count': len(entity_list)
        }

        total_entities += len(entity_list)

    conn.close()

    print()
    print(f"Extracted {total_entities} entities from {len(pilot_docs)} documents")
    print(f"Average: {total_entities / len(pilot_docs):.1f} entities per document")

    return baseline


def save_baseline(baseline: Dict, output_path: str):
    """Save baseline entities to JSON."""
    with open(output_path, 'w') as f:
        json.dump(baseline, f, indent=2)

    print()
    print(f"Saved to: {output_path}")


def generate_stats(baseline: Dict):
    """Generate baseline statistics."""
    print()
    print("-" * 70)
    print("BASELINE STATISTICS")
    print("-" * 70)

    total_docs = len(baseline)
    total_entities = sum(doc_data['entity_count'] for doc_data in baseline.values())

    # Entity types
    type_counts = {}
    confidence_scores = []

    for doc_data in baseline.values():
        for entity in doc_data['entities']:
            entity_type = entity['type']
            type_counts[entity_type] = type_counts.get(entity_type, 0) + 1

            if entity['confidence'] is not None:
                confidence_scores.append(entity['confidence'])

    print(f"Documents: {total_docs}")
    print(f"Total entities: {total_entities}")
    print(f"Average per doc: {total_entities / total_docs:.1f}")
    print()
    print("Entity types:")
    for entity_type, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        pct = count / total_entities * 100
        print(f"  {entity_type:20s}: {count:4d} ({pct:5.1f}%)")

    if confidence_scores:
        avg_conf = sum(confidence_scores) / len(confidence_scores)
        min_conf = min(confidence_scores)
        max_conf = max(confidence_scores)
        print()
        print(f"Confidence scores:")
        print(f"  Average: {avg_conf:.3f}")
        print(f"  Min: {min_conf:.3f}")
        print(f"  Max: {max_conf:.3f}")


def main():
    """Main execution."""
    script_dir = Path(__file__).parent
    input_path = script_dir / 'pilot_documents.json'
    output_path = script_dir / 'baseline_entities.json'

    if not input_path.exists():
        print(f"ERROR: {input_path} not found")
        print("Run select_pilot_documents.py first")
        return 1

    try:
        pilot_docs = load_pilot_documents(str(input_path))
        baseline = extract_baseline_entities(pilot_docs)
        save_baseline(baseline, str(output_path))
        generate_stats(baseline)

        print()
        print("=" * 70)
        print("EXTRACTION COMPLETE")
        print("=" * 70)
        print()
        print("Next steps:")
        print("  1. Review baseline_entities.json")
        print("  2. Run reextract_pilot.py")

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
```

### Script 3: `reextract_pilot.py`

**Purpose**: Re-extract pilot documents with pipeline enabled

**Location**: `koi-processor/scripts/reextract_pilot.py`

**Requirements**:
- Fetch original content from CAT receipts
- Run extraction with pipeline enabled
- Compare to baseline (number of entities, types, etc.)
- Log differences (blocked entities, transformed entities, new entities)
- Output: `pilot_results.json`

**Implementation** (outline - agent should complete):

```python
#!/usr/bin/env python3
"""
Re-extract pilot documents with pipeline enabled.

Input: pilot_documents.json, baseline_entities.json
Output: pilot_results.json
"""

import json
import sys
from pathlib import Path
from typing import List, Dict

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

# TODO: Agent should implement:
# 1. Load pilot documents and baseline
# 2. For each document:
#    a. Fetch original content from CAT receipts
#    b. Run extraction with pipeline
#    c. Compare new vs baseline entities
#    d. Log differences (blocked, transformed, new)
# 3. Save results to pilot_results.json
# 4. Generate summary statistics

# Key functions needed:
# - load_pilot_and_baseline()
# - reextract_document(document_id) -> entities
# - compare_extractions(baseline_entities, new_entities) -> diff
# - save_results(results, output_path)
# - generate_summary(results)

def main():
    """Main execution."""
    print("TODO: Implement re-extraction logic")
    print("See OPTION_A_REEXTRACTION_ULTRATHINK_PLAN.md for requirements")
    return 1

if __name__ == '__main__':
    sys.exit(main())
```

### Script 4: `compare_extractions.py`

**Purpose**: Compare old vs new extractions and generate report

**Location**: `koi-processor/scripts/compare_extractions.py`

**Requirements**:
- Load baseline and pilot results
- Compare entity counts (old vs new)
- Analyze blocked entities (by module)
- Compare type distributions
- Compare confidence scores
- Analyze list splitting results
- Calculate quality improvement percentage
- Output: `comparison_report.md`

**Implementation** (outline - agent should complete):

```python
#!/usr/bin/env python3
"""
Compare old vs new extractions.

Input: baseline_entities.json, pilot_results.json
Output: comparison_report.md
"""

import json
import sys
from pathlib import Path
from typing import List, Dict

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

# TODO: Agent should implement:
# 1. Load baseline and pilot results
# 2. Calculate metrics:
#    - Entity count change (old vs new)
#    - Block rate by module
#    - Type normalization stats
#    - List splitting stats
#    - Confidence score changes
#    - Quality improvement percentage
# 3. Generate markdown report with tables and charts
# 4. Include examples of blocked entities
# 5. Include examples of transformed entities

# Key functions needed:
# - load_data(baseline_path, results_path)
# - calculate_metrics(baseline, results) -> metrics
# - generate_report(metrics, output_path)
# - analyze_blocked_entities(results) -> analysis
# - analyze_transformations(results) -> analysis

def main():
    """Main execution."""
    print("TODO: Implement comparison logic")
    print("See OPTION_A_REEXTRACTION_ULTRATHINK_PLAN.md for requirements")
    return 1

if __name__ == '__main__':
    sys.exit(main())
```

### Success Criteria (Day 3-4)

- [ ] `select_pilot_documents.py` created and functional
- [ ] `extract_baseline_entities.py` created and functional
- [ ] `reextract_pilot.py` created (at least outline)
- [ ] `compare_extractions.py` created (at least outline)
- [ ] All scripts have docstrings and comments
- [ ] Scripts follow existing code style
- [ ] Error handling implemented
- [ ] README updated with script usage

---

## 📋 Task 3: Test Scripts Locally (Day 5)

### Test Plan

**Objective**: Validate scripts work correctly before pilot phase

**Test Set**: 10 documents (NOT from pilot set)

### Step 3.1: Select Test Documents

```bash
cd koi-processor/scripts

# Select 10 test documents (modify select_pilot_documents.py temporarily)
# Or manually create test_documents.json with 10 document IDs
```

### Step 3.2: Run Baseline Extraction

```bash
# Extract baseline for test set
python3 extract_baseline_entities.py --input test_documents.json --output test_baseline.json

# Verify output
cat test_baseline.json | jq '. | length'  # Should show 10
cat test_baseline.json | jq '.[0]'  # Show first document
```

### Step 3.3: Run Re-extraction

```bash
# Re-extract test set with pipeline
python3 reextract_pilot.py --input test_documents.json --baseline test_baseline.json --output test_results.json

# Monitor for errors
# Check processing time (should be < 10s per document)
```

### Step 3.4: Run Comparison

```bash
# Generate comparison report
python3 compare_extractions.py --baseline test_baseline.json --results test_results.json --output test_comparison.md

# Review report
cat test_comparison.md
```

### Step 3.5: Manual Validation

**Check**:
- [ ] Entity counts make sense (new count ≈ old count, not 0)
- [ ] Blocked entities are actually low-quality (pronouns, generics, etc.)
- [ ] Type normalization worked (COMPANY → ORGANIZATION)
- [ ] List splitting worked ("A and B" → 2 entities)
- [ ] No crashes or errors
- [ ] Processing time acceptable (< 10s per document)

### Step 3.6: Fix Bugs

**Common issues to check**:
- Database connection errors
- JSON serialization issues
- Missing metadata fields
- Division by zero in statistics
- File path issues

### Success Criteria (Day 5)

- [ ] All scripts run without errors
- [ ] Test set (10 documents) processed successfully
- [ ] Comparison report generated
- [ ] Results validated manually (spot check 3 documents)
- [ ] No major bugs found
- [ ] Scripts ready for pilot phase (Week 2)

---

## 📊 Week 1 Deliverables

By end of Week 1, you should have:

### Files Created

1. **BACKUPS.md** - Backup documentation with restore instructions
2. **scripts/select_pilot_documents.py** - Pilot selection script (COMPLETE)
3. **scripts/extract_baseline_entities.py** - Baseline extraction script (COMPLETE)
4. **scripts/reextract_pilot.py** - Re-extraction script (functional)
5. **scripts/compare_extractions.py** - Comparison script (functional)

### Artifacts

6. **pilot_documents.json** - 100 selected documents (NOT created yet, just script)
7. **test_baseline.json** - Test run baseline
8. **test_results.json** - Test run results
9. **test_comparison.md** - Test run comparison report

### Documentation

10. Update **koi-processor/scripts/README.md** with script usage
11. Update **koi-processor/README.md** with re-extraction section

---

## 🧪 Testing Requirements

### Unit Tests

Create `tests/test_reextraction_scripts.py`:

```python
"""Tests for re-extraction scripts."""

import pytest
from pathlib import Path
import sys

# Add scripts to path
scripts_dir = Path(__file__).parent.parent / 'scripts'
sys.path.insert(0, str(scripts_dir))


def test_select_pilot_documents():
    """Test pilot document selection."""
    # Test database connection
    # Test stratified sampling logic
    # Test output format
    pass


def test_extract_baseline_entities():
    """Test baseline entity extraction."""
    # Test entity extraction for known document
    # Test output format
    # Test statistics calculation
    pass


# Add more tests as needed
```

Run tests:
```bash
cd koi-processor
pytest tests/test_reextraction_scripts.py -v
```

---

## 📝 Documentation Updates

### Update `koi-processor/scripts/README.md`

Add section:

```markdown
## Re-extraction Scripts

Scripts for re-extracting documents with pipeline framework.

### select_pilot_documents.py

Select 100 representative documents for pilot re-extraction.

**Usage**:
\`\`\`bash
cd scripts
python3 select_pilot_documents.py
\`\`\`

**Output**: `pilot_documents.json`

### extract_baseline_entities.py

Extract current entities from knowledge graph for pilot documents.

**Usage**:
\`\`\`bash
python3 extract_baseline_entities.py
\`\`\`

**Input**: `pilot_documents.json`
**Output**: `baseline_entities.json`

### reextract_pilot.py

Re-extract pilot documents with pipeline enabled.

**Usage**:
\`\`\`bash
python3 reextract_pilot.py
\`\`\`

**Input**: `pilot_documents.json`, `baseline_entities.json`
**Output**: `pilot_results.json`

### compare_extractions.py

Compare old vs new extractions and generate report.

**Usage**:
\`\`\`bash
python3 compare_extractions.py
\`\`\`

**Input**: `baseline_entities.json`, `pilot_results.json`
**Output**: `comparison_report.md`
```

---

## ✅ Week 1 Completion Checklist

### Day 1-2: Secure Backups
- [ ] Backups moved to permanent storage (local or server)
- [ ] PostgreSQL backup verified (pg_restore --list)
- [ ] Fuseki backups verified (tar -tzf)
- [ ] Test restore successful (on test database)
- [ ] BACKUPS.md created with restore instructions
- [ ] Backup locations documented and secured

### Day 3-4: Build Scripts
- [ ] `select_pilot_documents.py` created and functional
- [ ] `extract_baseline_entities.py` created and functional
- [ ] `reextract_pilot.py` created (at least skeleton with TODOs)
- [ ] `compare_extractions.py` created (at least skeleton with TODOs)
- [ ] All scripts have proper docstrings
- [ ] Error handling implemented
- [ ] Scripts follow PEP 8 style

### Day 5: Test Scripts
- [ ] Test set (10 documents) selected
- [ ] Baseline extraction successful
- [ ] Re-extraction successful (no crashes)
- [ ] Comparison report generated
- [ ] Results validated manually
- [ ] Major bugs fixed
- [ ] Scripts ready for pilot phase

### Documentation
- [ ] `koi-processor/scripts/README.md` updated
- [ ] `koi-processor/README.md` updated (re-extraction section)
- [ ] Script usage documented
- [ ] Test results documented

### Verification
- [ ] All 121 tests still passing (no regressions)
- [ ] Production environment unchanged (backups only)
- [ ] Scripts tested locally (not on production)

---

## 🚦 Ready for Week 2?

**Before proceeding to Week 2 (Pilot Re-extraction)**, verify:

✅ **Backups secured and verified**
- Backups in permanent storage (not /tmp/)
- Can restore PostgreSQL database
- Can restore Fuseki volumes
- Restore procedure documented

✅ **Scripts built and tested**
- All 4 scripts created
- Scripts run without errors
- Test run successful (10 documents)
- Comparison report makes sense

✅ **Documentation complete**
- BACKUPS.md exists
- Script usage documented
- README updated

✅ **Production unchanged**
- All 121 tests still passing
- No changes to production data
- Backups safe and accessible

**If all checked**, you're ready for **Week 2: Pilot Re-extraction (100 documents)**

---

## 🆘 Common Issues

### Issue 1: Database Connection Error

**Symptom**: `psycopg2.OperationalError: could not connect to server`

**Solution**:
```bash
# Check PostgreSQL is running
ssh darren@202.61.196.119 "docker ps | grep postgres"

# Check port is correct (5433)
ssh darren@202.61.196.119 "PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -l"
```

### Issue 2: Missing CAT Receipts

**Symptom**: `cat_receipts` table empty or missing entries

**Solution**:
```bash
# Check table exists
ssh darren@202.61.196.119 "PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza -c 'SELECT COUNT(*) FROM cat_receipts;'"

# If empty, may need to re-run extraction pipeline
```

### Issue 3: Fuseki Not Accessible

**Symptom**: Cannot query Fuseki graph

**Solution**:
```bash
# Check Fuseki is running
ssh darren@202.61.196.119 "docker ps | grep fuseki"

# Check endpoint
ssh darren@202.61.196.119 "curl -s http://localhost:3030/koi/query --data 'query=SELECT (COUNT(*) as ?count) WHERE { ?s ?p ?o }'"
```

### Issue 4: Script Crashes

**Symptom**: Script exits with traceback

**Solution**:
- Check error message carefully
- Add print statements to debug
- Run with smaller test set (1-2 documents)
- Check database data matches expected format

---

## 📚 References

- **Week 1 Plan**: `OPTION_A_REEXTRACTION_ULTRATHINK_PLAN.md` (Day 1-5)
- **Full Re-extraction Plan**: `RE_EXTRACTION_PLAN.md`
- **Pipeline Framework**: `koi-processor/src/knowledge_graph/README.md`
- **Database Schema**: `koi-processor/docs/ARCHITECTURE.md`
- **CAT System**: CAT receipts track extraction provenance

---

## 🎯 Success Criteria Summary

**Week 1 Complete When**:
- ✅ Backups secured permanently and verified
- ✅ 4 re-extraction scripts created and functional
- ✅ Scripts tested locally (10 documents)
- ✅ Test results validated manually
- ✅ Documentation updated
- ✅ Ready for Week 2 pilot phase

**Next Prompt**: `PROMPT_9_WEEK2_PILOT_REEXTRACTION.md` (100 documents)

---

**Last Updated**: 2025-12-08
**Version**: Week 1 Implementation Guide
**Agent**: Claude Code (Opus 4.5)
**Duration**: 5 days
**Status**: 📋 Ready for handoff
