# PROMPT 21: Resume Fresh Extraction with Deduplication

**Date**: 2025-12-09
**Status**: READY (after PROMPT_20 complete)
**Priority**: HIGH
**Prerequisite**: PROMPT_20 (deduplication porting) must be complete

---

## Context

**Extraction was stopped at 300/4,710 GitHub docs** to implement cross-document deduplication.

**Current state**:
- ✅ Discourse: 839 docs extracted
- ✅ YouTube: 15 docs extracted
- ✅ GitLab: 200 docs extracted
- ✅ GitHub Activity: 51 docs extracted
- 🟡 GitHub Markdown: 300/4,710 docs extracted (PAUSED)

**After PROMPT_20**:
- ✅ FuzzyDeduplicator module integrated
- ✅ Enhanced EntityQualityFilter deployed
- ✅ Pipeline tested and validated
- ✅ Deduplication working (< 5% duplicates)

**This prompt**: Resume GitHub extraction from checkpoint with new dedup-enabled pipeline.

---

## Pre-Resumption Checklist

**CRITICAL**: Verify PROMPT_20 completion before proceeding:

```bash
cd /opt/projects/koi-processor

# 1. Check FuzzyDeduplicator exists
[ -f src/knowledge_graph/postprocessing/modules/fuzzy_deduplicator.py ] && echo "✅ FuzzyDeduplicator found" || echo "❌ Missing!"

# 2. Check pipeline config includes dedup
grep -q "FuzzyDeduplicator" src/knowledge_graph/config/pipeline_config.json && echo "✅ Pipeline configured" || echo "❌ Missing!"

# 3. Check dependencies installed
python3 -c "import fuzzywuzzy; print('✅ fuzzywuzzy installed')" 2>/dev/null || echo "❌ Missing!"

# 4. Run pipeline tests
pytest tests/test_fuzzy_deduplicator.py -v --tb=short

# 5. Check all 121+ tests passing
pytest tests/test_pipeline_*.py -q
```

**If ANY check fails** → DO NOT PROCEED, fix issues first.

---

## Validation: Test Dedup on Previous 300 Docs

**Before resuming extraction**, validate dedup works on the 300 GitHub docs already extracted:

### Step 1: Query Existing Extractions

```bash
ssh darren@202.61.196.119 "cd /opt/projects/koi-processor && PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza -c \"
SELECT
  id,
  memory_rid,
  jsonb_array_length(result->'entities') as entity_count
FROM koi_kg_extractions
WHERE metadata->>'source' = 'github'
ORDER BY created_at DESC
LIMIT 10;
\""
```

### Step 2: Re-process Through New Pipeline

Create validation script: `scripts/validate_dedup_on_existing.py`

```python
#!/usr/bin/env python3
"""Validate deduplication on existing GitHub extractions."""

import json
import psycopg2
from knowledge_graph.graph_integration import KnowledgeGraphIntegrator

def validate_existing_extractions(limit=300):
    """Re-process existing extractions through new pipeline."""
    conn = psycopg2.connect(
        host="localhost",
        port=5433,
        database="eliza",
        user="postgres",
        password="postgres"
    )
    cursor = conn.cursor()

    # Get existing GitHub extractions
    cursor.execute("""
        SELECT
            id,
            result->'entities' as entities,
            result->'relationships' as relationships
        FROM koi_kg_extractions
        WHERE metadata->>'source' = 'github'
        ORDER BY created_at DESC
        LIMIT %s
    """, (limit,))

    rows = cursor.fetchall()
    print(f"Found {len(rows)} existing GitHub extractions")

    # Initialize pipeline
    kg = KnowledgeGraphIntegrator(store_type="memory", use_pipeline=True)

    stats = {
        "total_entities": 0,
        "passed_entities": 0,
        "blocked_entities": 0,
        "merged_entities": 0,
    }

    entity_names = {}

    for extraction_id, entities_json, rels_json in rows:
        entities = json.loads(entities_json) if entities_json else []
        relationships = json.loads(rels_json) if rels_json else []

        # Process through new pipeline
        processed = kg.process_entities_batch(entities, relationships)

        stats["total_entities"] += len(entities)

        for entity in processed:
            if entity.get("blocked"):
                stats["blocked_entities"] += 1
            else:
                stats["passed_entities"] += 1

                # Track entity name occurrences
                name = entity.get("name")
                if name:
                    entity_names[name] = entity_names.get(name, 0) + 1

    # Check for duplicates
    print(f"\n{'='*60}")
    print("DEDUPLICATION ANALYSIS")
    print(f"{'='*60}")

    print(f"\nTotal entities: {stats['total_entities']}")
    print(f"Passed: {stats['passed_entities']}")
    print(f"Blocked: {stats['blocked_entities']}")
    print(f"Pass rate: {stats['passed_entities']/stats['total_entities']*100:.2f}%")

    # Check for duplicate patterns
    print(f"\nTop entity names (checking for duplicates):")
    sorted_names = sorted(entity_names.items(), key=lambda x: x[1], reverse=True)

    for name, count in sorted_names[:20]:
        print(f"  {name}: {count}")

    # Specific checks
    print(f"\n{'='*60}")
    print("DUPLICATE CHECKS")
    print(f"{'='*60}")

    regen_variants = [n for n, c in entity_names.items() if "regen" in n.lower()]
    print(f"\n'Regen' variants found: {len(regen_variants)}")
    for variant in regen_variants[:10]:
        print(f"  - {variant}: {entity_names[variant]}")

    if len(regen_variants) > 3:
        print("  ⚠️  WARNING: Multiple Regen variants suggest dedup not working!")
    else:
        print("  ✅ Good: Regen variants merged")

    gregory_variants = [n for n, c in entity_names.items() if "gregory" in n.lower()]
    print(f"\n'Gregory' variants found: {len(gregory_variants)}")
    for variant in gregory_variants:
        print(f"  - {variant}: {entity_names[variant]}")

    if len(gregory_variants) > 2:
        print("  ⚠️  WARNING: Multiple Gregory variants suggest dedup not working!")
    else:
        print("  ✅ Good: Gregory variants merged")

    # Check for blocked generics
    blocked_generics = ["user", "unknown", "validator", "community"]
    found_generics = [g for g in blocked_generics if g.lower() in [n.lower() for n in entity_names.keys()]]

    print(f"\nGeneric entities that should be blocked:")
    if found_generics:
        for generic in found_generics:
            print(f"  ❌ '{generic}' found: {entity_names.get(generic, 0)} occurrences")
        print("  ⚠️  WARNING: Quality filter not working!")
    else:
        print("  ✅ Good: No generic entities found")

    cursor.close()
    conn.close()

    return stats

if __name__ == "__main__":
    validate_existing_extractions(300)
```

**Run validation**:
```bash
cd /opt/projects/koi-processor
source venv/bin/activate
source .env
python3 scripts/validate_dedup_on_existing.py
```

**Success criteria**:
- ✅ Pass rate >= 97%
- ✅ < 3 "Regen" variants (merged to "Regen Network")
- ✅ < 2 "Gregory" variants (merged to "Gregory Landua")
- ✅ No "User", "unknown", "Validator", "community" entities

**If validation fails** → DO NOT RESUME, debug dedup module first.

---

## Resume Extraction

### Step 1: Check Checkpoint

```bash
ssh darren@202.61.196.119 "cat /opt/projects/koi-processor/scripts/reextraction/.checkpoint_github-markdown.json"
```

**Expected output**:
```json
{
  "source": "github-markdown",
  "completed": 300,
  "timestamp": "2025-12-09T22:50:14.061586+00:00"
}
```

### Step 2: Resume GitHub Extraction

```bash
ssh darren@202.61.196.119 "cd /opt/projects/koi-processor && \
  source venv/bin/activate && \
  source .env && \
  nohup python3 scripts/reextraction/extract_fresh_documents.py \
    --source github-markdown \
    --batch-size 50 \
    > logs/github_extraction_resume.log 2>&1 &"
```

**Monitor progress**:
```bash
# Check process running
ssh darren@202.61.196.119 "ps aux | grep extract_fresh_documents"

# Watch progress (updated every 60s)
watch -n 60 'ssh darren@202.61.196.119 "tail -20 /opt/projects/koi-processor/logs/github_extraction_resume.log"'

# Check database progress
ssh darren@202.61.196.119 "PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza -c \"
SELECT
  metadata->>'source' as source,
  COUNT(*) as docs,
  SUM(jsonb_array_length(result->'entities')) as entities
FROM koi_kg_extractions
WHERE metadata->>'source' = 'github'
GROUP BY metadata->>'source';
\""
```

### Step 3: Monitor Deduplication Metrics

**Create monitoring script**: `scripts/monitor_dedup_during_extraction.py`

```python
#!/usr/bin/env python3
"""Monitor deduplication metrics during extraction."""

import time
import psycopg2
from collections import Counter

def monitor_dedup(interval=300):  # 5 minutes
    """Monitor dedup metrics every N seconds."""
    while True:
        conn = psycopg2.connect(
            host="localhost",
            port=5433,
            database="eliza",
            user="postgres",
            password="postgres"
        )
        cursor = conn.cursor()

        # Get entity counts
        cursor.execute("""
            SELECT
                jsonb_array_elements(result->'entities')->>'name' as name
            FROM koi_kg_extractions
            WHERE metadata->>'source' = 'github'
        """)

        names = [row[0] for row in cursor.fetchall()]
        name_counts = Counter(names)

        # Check for duplicates
        print(f"\n{'='*60}")
        print(f"DEDUP MONITORING - {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")

        print(f"\nTotal unique entities: {len(name_counts)}")
        print(f"Total entity occurrences: {sum(name_counts.values())}")

        # Top entities
        print(f"\nTop 10 entities:")
        for name, count in name_counts.most_common(10):
            print(f"  {name}: {count}")

        # Duplicate checks
        regen_variants = {n: c for n, c in name_counts.items() if "regen" in n.lower()}
        print(f"\n'Regen' variants: {len(regen_variants)}")
        if len(regen_variants) > 3:
            print("  ⚠️  WARNING: Multiple variants detected!")
            for variant, count in sorted(regen_variants.items(), key=lambda x: x[1], reverse=True):
                print(f"    - {variant}: {count}")
        else:
            print("  ✅ Good: Dedup working")

        cursor.close()
        conn.close()

        time.sleep(interval)

if __name__ == "__main__":
    monitor_dedup(interval=300)  # Check every 5 minutes
```

**Run monitoring** (in separate tmux session):
```bash
ssh darren@202.61.196.119
cd /opt/projects/koi-processor
source venv/bin/activate
source .env

# Start monitoring in tmux
tmux new-session -d -s dedup_monitor "python3 scripts/monitor_dedup_during_extraction.py"

# Attach to see output
tmux attach -t dedup_monitor
```

---

## Expected Timeline

**Remaining work**: 4,410 GitHub docs (4,710 - 300 completed)

**Rate**: ~6-7 docs/min (OpenAI API bottleneck)

**Time**: 4,410 docs ÷ 6.5 docs/min = **~680 minutes = 11.3 hours**

**Expected completion**: ~10-12 hours from resume

---

## Quality Checks During Extraction

**Every 1,000 documents**, check deduplication quality:

```bash
ssh darren@202.61.196.119 "cd /opt/projects/koi-processor && PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza -c \"
SELECT
  jsonb_array_elements(result->'entities')->>'name' as name,
  COUNT(*) as occurrences
FROM koi_kg_extractions
WHERE metadata->>'source' = 'github'
GROUP BY name
ORDER BY COUNT(*) DESC
LIMIT 30;
\""
```

**Look for**:
- ✅ Single "Regen Network" entry (no "Regen", "REGEN" variants)
- ✅ Single "Gregory Landua" entry (no "Gregory", "Gregory_RND" variants)
- ✅ No "User", "unknown", "Validator" entries
- ✅ Duplicate rate < 5%

**If duplicates appear** → STOP extraction, debug dedup module.

---

## Post-Extraction Validation

**After all 4,710 docs extracted**, run full validation:

```bash
cd /opt/projects/koi-processor
source venv/bin/activate
source .env

# Run validation script
python3 scripts/reextraction/validate_fresh_extractions.py
```

**Expected results**:
- Total entities: ~45,000-50,000 (vs ~60,000+ without dedup)
- Pass rate: 97%+
- Duplicate rate: < 5%
- Quality: Excellent

---

## Troubleshooting

### Issue: Duplicates Still Appearing

**Diagnosis**:
```bash
# Check if FuzzyDeduplicator is running
cd /opt/projects/koi-processor
python3 -c "
from knowledge_graph.graph_integration import KnowledgeGraphIntegrator
kg = KnowledgeGraphIntegrator(store_type='memory', use_pipeline=True)
print('Pipeline modules:', [m.__class__.__name__ for m in kg.pipeline.modules])
"
```

**Should output**:
```
Pipeline modules: ['ConfidenceFilter', 'CanonicalResolver', 'FuzzyDeduplicator', 'EntityQualityFilter', 'ListSplitter', 'OntologyNormalizer']
```

**If FuzzyDeduplicator missing** → Check pipeline_config.json

### Issue: Extraction Failing

**Check logs**:
```bash
ssh darren@202.61.196.119 "tail -100 /opt/projects/koi-processor/logs/github_extraction_resume.log"
```

**Common issues**:
- Import error: `ModuleNotFoundError: No module named 'fuzzywuzzy'`
  - Fix: `pip install fuzzywuzzy python-Levenshtein`
- Pipeline error: Module not registered
  - Fix: Check `__init__.py` includes FuzzyDeduplicator

### Issue: Performance Degradation

**Check if dedup adds overhead**:
```bash
# Monitor extraction rate
ssh darren@202.61.196.119 "tail -f /opt/projects/koi-processor/logs/github_extraction_resume.log | grep 'docs/min'"
```

**Expected**: 6-7 docs/min (same as before)
**If slower**: FuzzyDeduplicator may need optimization

---

## Success Criteria

**Mark complete when**:
- ✅ All 4,710 GitHub docs extracted
- ✅ Pass rate >= 97%
- ✅ Duplicate rate < 5%
- ✅ No "User", "unknown", "Validator" entities in final data
- ✅ "Regen Network" / "Regen" / "REGEN" merged to 1 entity
- ✅ "Gregory Landua" / "Gregory" / "Gregory_RND" merged to 1 entity

---

## Next Steps After Completion

1. Run final validation: `scripts/reextraction/validate_fresh_extractions.py`
2. Deploy to production: `scripts/reextraction/deploy_fresh_extractions.py`
3. Generate final report: Update FRESH_EXTRACTION_REPORT_TEMPLATE.md
4. Update CLAUDE.md with Phase 3 completion status

---

## Reference

**Checkpoint location**: `/opt/projects/koi-processor/scripts/reextraction/.checkpoint_github-markdown.json`

**Extraction script**: `/opt/projects/koi-processor/scripts/reextraction/extract_fresh_documents.py`

**Pipeline config**: `/opt/projects/koi-processor/src/knowledge_graph/config/pipeline_config.json`

---

**Status**: READY TO START (after PROMPT_20)
**Priority**: HIGH
**Estimated completion**: 11-12 hours extraction time
