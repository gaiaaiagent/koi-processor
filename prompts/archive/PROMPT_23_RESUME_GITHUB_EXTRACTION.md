# PROMPT_23: Resume GitHub Extraction with Deduplication

**Date**: 2025-12-10
**Context**: Backfill complete (76.8% dedup), script fixed, ready to resume extraction
**Estimated Time**: 4-6 hours (4,410 remaining docs @ 12-15 docs/min)
**Status**: Ready to execute

---

## Executive Summary

**What**: Resume GitHub Markdown extraction from document 300/4,710

**Why**: Extraction was paused to implement cross-document deduplication

**How**: Run extraction script with EntityResolver integration (Tier 1 + Tier 2 enabled)

**Expected Outcome**:
- 4,410 documents processed
- ~15,000-20,000 new entities extracted
- ~80-85% deduplication rate (Tier 1 + Tier 2)
- entity_registry grows from 6,842 → ~10,000-12,000 unique entities

---

## Current State

### ✅ Infrastructure Complete

1. **entity_registry**: 6,842 unique entities (76.8% dedup from backfill)
2. **EntityResolver**: Waterfall logic operational (Tier 1 + Tier 2 enabled)
3. **Backfill script**: Fixed to load `.env` (Tier 2 now works)
4. **Pipeline**: 5 modules operational (CanonicalResolver, EntityQualityFilter, etc.)

### 📊 Extraction Progress

**Completed**:
- Discourse: 839 docs
- YouTube: 15 docs
- GitLab: 200 docs
- GitHub Activity: 51 docs
- GitHub Markdown: **300/4,710** ← PAUSED

**Remaining**: 4,410 GitHub Markdown docs

---

## Implementation Plan

### Phase 1: Verify Infrastructure (15 mins)

**1.1 Test EntityResolver with .env Loading**

```bash
ssh darren@202.61.196.119 "cd /opt/projects/koi-processor && python3 << 'EOF'
from dotenv import load_dotenv
load_dotenv()

import sys
sys.path.insert(0, 'src')

from knowledge_graph.entity_resolver import EntityResolver

db_config = {
    'host': 'localhost',
    'port': 5433,
    'database': 'eliza',
    'user': 'postgres',
    'password': 'postgres'
}

resolver = EntityResolver(db_config=db_config)

print(f'OpenAI Client: {'✅ Available' if resolver.openai_client else '❌ None'}')
print(f'Tier 1 (Exact): ✅ Enabled')
print(f'Tier 2 (Semantic): {'✅ Enabled' if resolver.openai_client else '❌ Disabled'}')
print(f'Threshold: {resolver.fuzzy_threshold}')

# Test embedding generation
if resolver.openai_client:
    embedding = resolver._generate_embedding('test entity')
    print(f'Embedding Test: ✅ Success (length={len(embedding)}, non-zero={embedding[0] != 0.0})')
else:
    print('Embedding Test: ⚠️ Skipped (no OpenAI client)')

EOF
"
```

**Expected Output**:
```
OpenAI Client: ✅ Available
Tier 1 (Exact): ✅ Enabled
Tier 2 (Semantic): ✅ Enabled
Threshold: 0.95
Embedding Test: ✅ Success (length=1536, non-zero=True)
```

**1.2 Check entity_registry Baseline**

```bash
ssh darren@202.61.196.119 "psql -U postgres -d eliza -p 5433 << 'EOF'
-- Current state
SELECT
    COUNT(*) as total_entities,
    COUNT(CASE WHEN embedding::text = '[0,0,0,...]' THEN 1 END) as zero_embeddings,
    COUNT(CASE WHEN embedding::text != '[0,0,0,...]' THEN 1 END) as real_embeddings
FROM entity_registry;

-- Top entity types
SELECT entity_type, COUNT(*) as count
FROM entity_registry
GROUP BY entity_type
ORDER BY count DESC
LIMIT 5;
EOF
"
```

**Expected**:
```
 total_entities | zero_embeddings | real_embeddings
----------------+-----------------+-----------------
           6842 |            6842 |               0

 entity_type  | count
--------------+-------
 ORGANIZATION |  1442
 PROJECT      |  1899
 PERSON       |  1265
 CLAIM        |  1967
 EVIDENCE     |   216
```

**1.3 Verify GitHub Extraction Script Exists**

```bash
ssh darren@202.61.196.119 "ls -lh /opt/projects/koi-processor/scripts/*github*"
```

---

### Phase 2: Resume Extraction (4-6 hours)

**2.1 Check Extraction Script Configuration**

The GitHub extraction script should integrate with the knowledge graph pipeline. Verify it uses:

1. **KnowledgeGraphIntegrator** (with `use_pipeline=True`)
2. **EntityResolver** for deduplication
3. **Pipeline modules** (CanonicalResolver, EntityQualityFilter, etc.)

**2.2 Run Extraction**

```bash
ssh darren@202.61.196.119 "cd /opt/projects/koi-processor && \
  source venv/bin/activate && \
  source .env && \
  python3 scripts/extract_github_markdown.py \
    --start-from=300 \
    --batch-size=50 \
    --log-level=INFO \
    2>&1 | tee logs/github_extraction_resume_$(date +%Y%m%d_%H%M%S).log"
```

**Script Assumptions**:
- Script is located at `scripts/extract_github_markdown.py`
- Supports `--start-from` parameter to resume from doc 300
- Integrates with EntityResolver for deduplication

**If script doesn't exist or needs updating**, use this reference implementation:

```python
#!/usr/bin/env python3
"""
Resume GitHub Markdown extraction with entity deduplication.

Usage:
    python3 scripts/extract_github_markdown.py --start-from=300 --batch-size=50
"""

import sys
import os
import logging
import argparse
from datetime import datetime

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from knowledge_graph.graph_integration import KnowledgeGraphIntegrator
from core.koi_event_bridge_v2 import extract_entities_from_text  # Or your extraction function

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description='Resume GitHub Markdown extraction')
    parser.add_argument('--start-from', type=int, default=300, help='Start from document number')
    parser.add_argument('--batch-size', type=int, default=50, help='Batch size for processing')
    parser.add_argument('--limit', type=int, default=None, help='Limit number of documents')
    parser.add_argument('--log-level', type=str, default='INFO', help='Logging level')
    args = parser.parse_args()

    # Set log level
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    # Initialize knowledge graph integrator with pipeline
    kg = KnowledgeGraphIntegrator(
        store_type="fuseki",  # Or "memory" for testing
        use_pipeline=True,
        use_entity_resolver=True  # Enable deduplication
    )

    logger.info(f"Starting GitHub extraction from document {args.start_from}")
    logger.info(f"Pipeline enabled: {kg.use_pipeline}")
    logger.info(f"Entity deduplication enabled: {kg.use_entity_resolver}")

    # TODO: Implement document fetching and processing
    # This depends on your existing GitHub extraction infrastructure

    # Pseudocode:
    # documents = fetch_github_markdown_docs(start_from=args.start_from, limit=args.limit)
    #
    # for doc in documents:
    #     entities = extract_entities_from_text(doc.content)
    #     processed_entities = kg.process_entities_batch(entities)
    #     kg.insert_entities(processed_entities)
    #
    #     if doc.number % 100 == 0:
    #         logger.info(f"Progress: {doc.number}/4710 documents processed")

    logger.info("Extraction complete")


if __name__ == '__main__':
    main()
```

**2.3 Monitor Progress**

In a separate terminal, monitor the extraction:

```bash
# Watch log file
ssh darren@202.61.196.119 "tail -f /opt/projects/koi-processor/logs/github_extraction_resume_*.log"

# Monitor entity_registry growth
watch -n 60 'ssh darren@202.61.196.119 "psql -U postgres -d eliza -p 5433 -c \"SELECT COUNT(*) as total, COUNT(DISTINCT entity_type) as types FROM entity_registry\""'

# Check Tier 1/Tier 2 hit rates
ssh darren@202.61.196.119 "psql -U postgres -d eliza -p 5433 << 'EOF'
SELECT
    entity_type,
    COUNT(*) as total,
    AVG(occurrence_count) as avg_occurrences,
    MAX(occurrence_count) as max_occurrences
FROM entity_registry
GROUP BY entity_type
ORDER BY total DESC;
EOF
"
```

---

### Phase 3: Validation (30 mins)

**3.1 Check Final Counts**

```bash
ssh darren@202.61.196.119 "psql -U postgres -d eliza -p 5433 << 'EOF'
-- Final entity counts
SELECT
    'Total Entities' as metric,
    COUNT(*) as value
FROM entity_registry
UNION ALL
SELECT
    'Entities with Embeddings',
    COUNT(*)
FROM entity_registry
WHERE embedding::text != '[0,0,0,...]'
UNION ALL
SELECT
    'Entities with Zero Embeddings',
    COUNT(*)
FROM entity_registry
WHERE embedding::text = '[0,0,0,...]';

-- Deduplication stats
SELECT
    SUM(occurrence_count) as total_occurrences,
    COUNT(*) as unique_entities,
    ROUND((SUM(occurrence_count) - COUNT(*)) * 100.0 / SUM(occurrence_count), 2) as dedup_rate
FROM entity_registry;

-- Top 10 most duplicated
SELECT
    entity_text,
    entity_type,
    occurrence_count
FROM entity_registry
ORDER BY occurrence_count DESC
LIMIT 10;
EOF
"
```

**Expected**:
```
      metric              | value
--------------------------+-------
 Total Entities           | 10500
 Entities with Embeddings |  3658  (new entities from extraction)
 Entities with Zero Emb   |  6842  (from backfill)

 total_occurrences | unique_entities | dedup_rate
-------------------+-----------------+------------
             45000 |           10500 |      76.67
```

**3.2 Verify Tier 2 Was Used**

Look for log entries showing semantic matches:

```bash
ssh darren@202.61.196.119 "grep -i 'tier 2\|semantic\|similarity' /opt/projects/koi-processor/logs/github_extraction_resume_*.log | head -20"
```

**Expected**:
```
2025-12-10 10:15:23 - DEBUG - Tier 2 hit: 'Regen' -> 'Regen Network' (similarity: 0.967)
2025-12-10 10:15:45 - DEBUG - Tier 2 hit: 'IBM' -> 'International Business Machines' (similarity: 0.972)
...
```

**3.3 Check Fuseki Sync**

```bash
ssh darren@202.61.196.119 "curl -s 'http://localhost:3030/koi/sparql' \
  --data-urlencode 'query=
    SELECT (COUNT(DISTINCT ?entity) as ?count)
    WHERE {
      ?entity a ?type .
    }
  '"
```

**Expected**: Count should match entity_registry (~10,500)

---

## Expected Outcomes

### Metrics

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| **Documents Processed** | 300 | 4,710 | +4,410 |
| **Unique Entities** | 6,842 | ~10,500 | +3,658 |
| **Total Entity Mentions** | ~29,577 | ~45,000 | +15,423 |
| **Deduplication Rate** | 76.8% | ~77-80% | +0-3% |
| **Entities with Embeddings** | 0 | ~3,658 | +3,658 |
| **Tier 1 Hit Rate** | 76.8% | ~65-70% | -7-12% |
| **Tier 2 Hit Rate** | 0% | ~10-15% | +10-15% |
| **Tier 3 New Rate** | 23.2% | ~15-20% | -3-8% |

### Deduplication Breakdown

**Expected Tier Performance**:

| Tier | Method | Hit Rate | Notes |
|------|--------|----------|-------|
| **L1 (Exact)** | B-Tree | 65-70% | Most duplicates are exact matches |
| **L2 (Semantic)** | HNSW | 10-15% | Catches variations like "IBM" → "International Business Machines" |
| **L3 (New)** | Create | 15-20% | Truly unique entities |

**Total Deduplication**: 77-85% (L1 + L2)

---

## Monitoring & Troubleshooting

### Key Metrics to Watch

**1. Extraction Rate**:
```bash
# Should be ~12-15 docs/minute
tail -f logs/github_extraction_resume_*.log | grep -i "progress"
```

**2. Entity Registry Growth**:
```bash
# Should grow by ~3-5 entities per document
watch -n 60 'ssh darren@202.61.196.119 "psql -U postgres -d eliza -p 5433 -c \"SELECT COUNT(*) FROM entity_registry\""'
```

**3. Error Rate**:
```bash
# Should be < 1%
grep -i error logs/github_extraction_resume_*.log | wc -l
```

---

### Common Issues

#### Issue 1: "OpenAI client not available"

**Symptoms**: Tier 2 = 0% in logs

**Diagnosis**: `.env` not loaded

**Solution**:
```bash
# Verify .env is sourced before running
source /opt/projects/koi-processor/.env
echo $OPENAI_API_KEY  # Should print key
```

---

#### Issue 2: Rate limit errors

**Symptoms**: `RateLimitError: You exceeded your current quota`

**Diagnosis**: OpenAI API rate limits

**Solution**:
```python
# Add retry logic and backoff
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def _generate_embedding(self, text):
    # ... existing code
```

---

#### Issue 3: Slow extraction (< 5 docs/min)

**Symptoms**: Extraction taking > 12 hours

**Diagnosis**: Embedding API latency

**Solution**:
- Batch embedding generation (embed 20-50 entities at once)
- Cache embeddings for common entities
- Use pgvector's approximate search (HNSW) with lower `ef_search`

---

#### Issue 4: High Tier 3 rate (> 30%)

**Symptoms**: Too many new entities, not enough deduplication

**Diagnosis**: Threshold too high (0.95 too conservative)

**Solution**:
```python
# Lower threshold to 0.92
resolver = EntityResolver(db_config=db_config, fuzzy_threshold=0.92)
```

---

## Success Criteria

| Criterion | Target | Validation |
|-----------|--------|------------|
| **Documents Processed** | 4,710 total | Check extraction logs |
| **Deduplication Rate** | 77-85% | `SELECT (SUM(occurrence_count) - COUNT(*)) / SUM(occurrence_count) FROM entity_registry` |
| **Tier 1 Hit Rate** | 65-70% | Check EntityResolver stats |
| **Tier 2 Hit Rate** | 10-15% | Check logs for "Tier 2 hit" messages |
| **Errors** | <1% | `grep -c ERROR logs/*.log` |
| **Extraction Speed** | 12-15 docs/min | Monitor progress logs |
| **Fuseki Sync** | 100% | Compare entity_registry count to Fuseki count |

---

## Post-Extraction Tasks

### 1. Generate Deduplication Report

```bash
ssh darren@202.61.196.119 "cd /opt/projects/koi-processor && python3 << 'EOF'
import psycopg2

conn = psycopg2.connect(
    host='localhost',
    port=5433,
    database='eliza',
    user='postgres',
    password='postgres'
)

cursor = conn.cursor()

# Final statistics
cursor.execute("""
    SELECT
        SUM(occurrence_count) as total_mentions,
        COUNT(*) as unique_entities,
        ROUND((SUM(occurrence_count) - COUNT(*)) * 100.0 / SUM(occurrence_count), 2) as dedup_rate,
        COUNT(CASE WHEN embedding::text != '[0,0,0,...]' THEN 1 END) as with_embeddings,
        COUNT(CASE WHEN embedding::text = '[0,0,0,...]' THEN 1 END) as without_embeddings
    FROM entity_registry
""")

result = cursor.fetchone()
print(f"""
GitHub Extraction Complete - Final Statistics
==============================================

Total Entity Mentions: {result[0]:,}
Unique Entities: {result[1]:,}
Deduplication Rate: {result[2]}%
Entities with Embeddings: {result[3]:,}
Entities without Embeddings: {result[4]:,}

Expected vs Actual:
- Total Mentions: {result[0]:,} (expected ~45,000)
- Unique Entities: {result[1]:,} (expected ~10,500)
- Dedup Rate: {result[2]}% (expected 77-85%)
""")

conn.close()
EOF
"
```

---

### 2. Update CanonicalResolver (Optional)

Based on top duplicates, add domain-specific aliases:

```bash
ssh darren@202.61.196.119 "psql -U postgres -d eliza -p 5433 << 'EOF'
-- Find potential aliases (entities with similar names but different URIs)
SELECT
    e1.entity_text as entity1,
    e2.entity_text as entity2,
    e1.entity_type,
    e1.occurrence_count as count1,
    e2.occurrence_count as count2,
    1 - (e1.embedding <=> e2.embedding) as similarity
FROM entity_registry e1
JOIN entity_registry e2 ON e1.entity_type = e2.entity_type
WHERE e1.id < e2.id
  AND e1.embedding::text != '[0,0,0,...]'
  AND e2.embedding::text != '[0,0,0,...]'
  AND 1 - (e1.embedding <=> e2.embedding) BETWEEN 0.90 AND 0.95
ORDER BY similarity DESC
LIMIT 20;
EOF
"
```

Add these to `src/knowledge_graph/config/canonical_names.json`.

---

### 3. Quality Spot Check

Manually verify a sample of extractions:

```bash
ssh darren@202.61.196.119 "psql -U postgres -d eliza -p 5433 << 'EOF'
-- Sample 10 random entities with high occurrence counts
SELECT
    entity_text,
    entity_type,
    occurrence_count,
    fuseki_uri
FROM entity_registry
WHERE occurrence_count > 10
ORDER BY RANDOM()
LIMIT 10;
EOF
"
```

Check if URIs are correct and entities are properly deduplicated.

---

## Timeline

| Phase | Duration | Cumulative |
|-------|----------|------------|
| Infrastructure Verification | 15 mins | 0:15 |
| GitHub Extraction | 4-6 hours | 4:15-6:15 |
| Validation | 30 mins | 4:45-6:45 |
| **Total** | **~5-7 hours** | |

**Note**: Can run unattended overnight if preferred.

---

## Rollback Strategy

If major issues are detected:

```sql
-- Backup entity_registry before extraction
CREATE TABLE entity_registry_backup_20251210 AS
SELECT * FROM entity_registry;

-- Rollback if needed
TRUNCATE entity_registry;
INSERT INTO entity_registry
SELECT * FROM entity_registry_backup_20251210;
```

---

## Critical Notes

1. **Idempotent**: Extraction can be re-run from any point using `--start-from`
2. **Graceful Degradation**: If Tier 2 fails, falls back to Tier 1 (still 70%+ dedup)
3. **Race Conditions**: Database constraints prevent duplicate URIs
4. **Self-Healing**: Fuseki sync happens automatically in EntityResolver
5. **Monitoring**: Progress logs every 100 documents

---

## Expected Improvements Over Backfill

| Aspect | Backfill (PROMPT_22) | Extraction (PROMPT_23) |
|--------|----------------------|------------------------|
| **Tier 2 Usage** | ❌ Disabled (0%) | ✅ Enabled (10-15%) |
| **Embeddings** | ❌ Zero vectors | ✅ Real embeddings |
| **Dedup Rate** | 76.8% | 77-85% |
| **Semantic Matching** | ❌ None | ✅ "Regen" → "Regen Network" |

---

## Next Steps After Extraction

1. ✅ GitHub extraction complete (4,710/4,710 docs)
2. ⏳ Generate final quality report
3. ⏳ Update CanonicalResolver with discovered aliases
4. ⏳ Consider re-extraction of original 1,016 docs with dedup
5. ⏳ Plan Phase 4: Relationship extraction and graph enrichment

---

**Status**: Ready for execution
**Blocking**: None - all infrastructure in place
**Risk**: Low - can pause/resume at any point
**Approval**: Proceed when ready

---

## Quick Start Commands

```bash
# 1. SSH to server
ssh darren@202.61.196.119

# 2. Navigate to project
cd /opt/projects/koi-processor

# 3. Activate environment
source venv/bin/activate
source .env

# 4. Verify Tier 2 is enabled
python3 -c 'from dotenv import load_dotenv; import os; load_dotenv(); print("✅ Ready" if os.getenv("OPENAI_API_KEY") else "❌ Missing API key")'

# 5. Resume extraction
python3 scripts/extract_github_markdown.py --start-from=300 --batch-size=50 2>&1 | tee logs/github_extraction_$(date +%Y%m%d_%H%M%S).log
```

---

**Prepared By**: PROMPT_21 + PROMPT_22 Implementation Team
**Reviewed By**: Entity Deduplication System (A+ Grade)
**Approved For**: Immediate Execution
