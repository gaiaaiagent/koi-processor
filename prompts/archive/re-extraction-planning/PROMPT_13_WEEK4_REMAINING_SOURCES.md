# PROMPT 13: Week 4 - Remaining Sources Re-extraction (604 Documents)

**Date**: 2025-12-09
**Phase**: Week 4 - Final Re-extraction Batch
**Duration**: 5 days
**Agent**: Claude Code (Opus 4.5)

---

## 🎯 Context

**Week 3 Complete**: 411 Discourse documents - STRONG GO

**Week 3 Results**:
- ✅ **Pass rate**: 98.36% (2,514/2,556 entities)
- ✅ **Block rate**: 2.07% (53/2,556 entities)
- ✅ **False positive rate**: ~0% (spot check verified)
- ✅ **Consistency**: Matches pilot (98.3% vs 98.36%)
- ✅ **All batches validated**: 3 batches across 411 documents

**Ready for**: Final re-extraction of remaining sources (604 documents)

---

## 🎯 Your Mission

Re-extract the remaining 604 documents across 4 sources with the validated pipeline. This completes the full re-extraction of the knowledge graph.

**Scope**:
- **Website**: 454 documents (~9,217 entities)
- **Notion**: 78 documents (~2,823 entities)
- **Podcast**: 66 documents (~411 entities)
- **GitHub**: 6 documents (~39 entities)
- **Total**: 604 documents (~12,490 entities)

**Tasks**:
1. **Day 1: Website Batch 1** (200 docs)
   - Select first 200 website documents
   - Extract baseline
   - Re-extract with pipeline
   - Validate results

2. **Day 2: Website Batches 2-3** (254 docs)
   - Process remaining website documents
   - Complete website re-extraction (454 total)
   - Validate consistency

3. **Day 3: Notion** (78 docs)
   - Process all Notion documents
   - Validate results
   - Compare with Week 3 metrics

4. **Day 4: Podcast + GitHub** (72 docs)
   - Process Podcast documents (66)
   - Process GitHub documents (6)
   - Complete all re-extractions

5. **Day 5: Week 4 Validation**
   - Aggregate all Week 4 results (604 docs)
   - Generate comprehensive report
   - Compare with Week 3 baseline
   - Make final quality assessment

---

## 🖥️ Execution Environment: Server (IMPORTANT)

### ⚠️ Run on Production Server, Not Locally

**Why Server Execution**:
- ✅ **Direct database access** - No network latency (localhost connection)
- ✅ **Much faster** - Production environment, optimized performance
- ✅ **Background processing** - Can disconnect, processing continues
- ✅ **Already deployed** - Code at `/opt/projects/koi-processor`
- ✅ **12-20 hours runtime** - No need to keep laptop on

**Server**: `darren@202.61.196.119`
**Database**: PostgreSQL on port 5433 (localhost)
**Project Path**: `/opt/projects/koi-processor`

### Server Setup

**Step 1: SSH to Server**
```bash
ssh darren@202.61.196.119
```

**Step 2: Navigate to Project**
```bash
cd /opt/projects/koi-processor/scripts/reextraction
```

**Step 3: Start or Reconnect to tmux Session**

**If Week 3 session still exists**:
```bash
# List sessions
tmux ls

# If week3-reextraction exists:
tmux attach -t week3-reextraction

# Rename it to week4
# Press: Ctrl-B, then type: :rename-session week4-reextraction
# Press: Enter
```

**If starting fresh**:
```bash
# Create new session for Week 4
tmux new -s week4-reextraction
```

**Step 4: Create Week 4 Results Directory**
```bash
cd /opt/projects/koi-processor/scripts/reextraction
mkdir -p week4_results
cd week4_results
```

---

## 📋 Day 1: Website Batch 1 (200 documents)

### Prerequisites

Before starting, verify:
```bash
# Check you're in tmux
echo $TMUX
# Should show something (not empty)

# Verify database connection
PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza -c "SELECT COUNT(*) FROM documents WHERE source = 'website';"
# Should show ~454

# Check Python environment
cd /opt/projects/koi-processor
python3 -c "from src.knowledge_graph.graph_integration import KnowledgeGraphIntegrator; print('OK')"
# Should print: OK
```

### Step 1.1: Select Website Documents (Batch 1)

**Goal**: Select first 200 website documents

```bash
cd /opt/projects/koi-processor/scripts/reextraction

# Run selection script for website
python3 select_pilot_documents.py \
  --source website \
  --count 200 \
  --output week4_results/website_batch_1.json

# Verify output
cat week4_results/website_batch_1.json | jq '. | length'
# Expected: 200
```

**Output**: `week4_results/website_batch_1.json`

### Step 1.2: Extract Baseline Entities

**Goal**: Extract entities from baseline (current database)

```bash
python3 extract_baseline_entities.py \
  --input week4_results/website_batch_1.json \
  --output week4_results/website_batch_1_baseline.json

# Check results
cat week4_results/website_batch_1_baseline.json | jq '.[] | {doc_id: .document_id, entity_count: (.entities | length)}' | head -5
```

**Expected**:
- ~200 documents
- ~4,000-4,500 entities total (~20-22 per doc)
- Confidence scores 0.70-0.95

**Output**: `week4_results/website_batch_1_baseline.json`

### Step 1.3: Re-extract with Pipeline

**Goal**: Re-extract entities through validated pipeline

```bash
python3 reextract_pilot.py \
  --input week4_results/website_batch_1.json \
  --baseline week4_results/website_batch_1_baseline.json \
  --output week4_results/website_batch_1_results.json \
  --use-pipeline

# Monitor progress
tail -f week4_results/website_batch_1_results.json
```

**Expected**:
- ~200 documents processed
- ~95-98% pass rate (based on Week 3 baseline)
- ~2-5% block rate
- ~0% false positive rate

**Output**: `week4_results/website_batch_1_results.json`

### Step 1.4: Generate Comparison Report

**Goal**: Validate results match expected metrics

```bash
python3 compare_extractions.py \
  --results week4_results/website_batch_1_results.json \
  --output week4_results/website_batch_1_report.md

# Review report
cat week4_results/website_batch_1_report.md
```

**Validation Checks**:
- [ ] Pass rate ≥ 95% (target: ~98%)
- [ ] Block rate 2-5%
- [ ] FP rate < 5% (target: ~0%)
- [ ] No systematic issues in blocked entities

**If Validation Fails**: STOP and investigate before proceeding to Day 2

---

## 📋 Day 2: Website Batches 2-3 (254 documents)

### Step 2.1: Website Batch 2 (200 documents)

```bash
cd /opt/projects/koi-processor/scripts/reextraction

# Select documents
python3 select_pilot_documents.py \
  --source website \
  --count 200 \
  --offset 200 \
  --output week4_results/website_batch_2.json

# Extract baseline
python3 extract_baseline_entities.py \
  --input week4_results/website_batch_2.json \
  --output week4_results/website_batch_2_baseline.json

# Re-extract with pipeline
python3 reextract_pilot.py \
  --input week4_results/website_batch_2.json \
  --baseline week4_results/website_batch_2_baseline.json \
  --output week4_results/website_batch_2_results.json \
  --use-pipeline

# Generate report
python3 compare_extractions.py \
  --results week4_results/website_batch_2_results.json \
  --output week4_results/website_batch_2_report.md

# Validate
cat week4_results/website_batch_2_report.md
```

**Validation**: Same checks as Batch 1

### Step 2.2: Website Batch 3 (54 documents)

```bash
# Select remaining website documents
python3 select_pilot_documents.py \
  --source website \
  --count 54 \
  --offset 400 \
  --output week4_results/website_batch_3.json

# Extract baseline
python3 extract_baseline_entities.py \
  --input week4_results/website_batch_3.json \
  --output week4_results/website_batch_3_baseline.json

# Re-extract with pipeline
python3 reextract_pilot.py \
  --input week4_results/website_batch_3.json \
  --baseline week4_results/website_batch_3_baseline.json \
  --output week4_results/website_batch_3_results.json \
  --use-pipeline

# Generate report
python3 compare_extractions.py \
  --results week4_results/website_batch_3_results.json \
  --output week4_results/website_batch_3_report.md
```

### Step 2.3: Aggregate Website Results

**Goal**: Combine all website batches into single report

```bash
cd /opt/projects/koi-processor/scripts/reextraction

# Create aggregation script
cat > week4_results/aggregate_website.py << 'EOF'
#!/usr/bin/env python3
"""Aggregate website batch results."""
import json
import sys

batches = [
    'week4_results/website_batch_1_results.json',
    'week4_results/website_batch_2_results.json',
    'week4_results/website_batch_3_results.json'
]

all_results = {}
for batch_file in batches:
    with open(batch_file) as f:
        batch_data = json.load(f)
        all_results.update(batch_data)

with open('week4_results/website_all_results.json', 'w') as f:
    json.dump(all_results, f, indent=2)

print(f"Aggregated {len(all_results)} documents")
EOF

python3 week4_results/aggregate_website.py

# Generate aggregate report
python3 compare_extractions.py \
  --results week4_results/website_all_results.json \
  --output week4_results/website_all_report.md

# Review
cat week4_results/website_all_report.md
```

**Expected**:
- 454 documents processed
- ~9,000-9,500 entities
- 95-98% pass rate
- Consistent with Week 3 (98.36%)

---

## 📋 Day 3: Notion (78 documents)

### Step 3.1: Select All Notion Documents

```bash
cd /opt/projects/koi-processor/scripts/reextraction

# Select all Notion documents (single batch)
python3 select_pilot_documents.py \
  --source notion \
  --count 78 \
  --output week4_results/notion_all.json

# Verify count
cat week4_results/notion_all.json | jq '. | length'
# Expected: 78 (or actual count from database)
```

### Step 3.2: Extract Baseline

```bash
python3 extract_baseline_entities.py \
  --input week4_results/notion_all.json \
  --output week4_results/notion_all_baseline.json

# Check entity count
cat week4_results/notion_all_baseline.json | jq '[.[] | .entities | length] | add'
# Expected: ~2,800-3,000 entities
```

### Step 3.3: Re-extract with Pipeline

```bash
python3 reextract_pilot.py \
  --input week4_results/notion_all.json \
  --baseline week4_results/notion_all_baseline.json \
  --output week4_results/notion_all_results.json \
  --use-pipeline
```

### Step 3.4: Generate Report

```bash
python3 compare_extractions.py \
  --results week4_results/notion_all_results.json \
  --output week4_results/notion_all_report.md

# Review
cat week4_results/notion_all_report.md
```

**Validation**:
- [ ] Pass rate ≥ 95%
- [ ] Block rate 2-5%
- [ ] FP rate < 5%
- [ ] Results consistent with Week 3

---

## 📋 Day 4: Podcast + GitHub (72 documents)

### Step 4.1: Podcast Documents (66 docs)

```bash
cd /opt/projects/koi-processor/scripts/reextraction

# Select all Podcast documents
python3 select_pilot_documents.py \
  --source podcast \
  --count 66 \
  --output week4_results/podcast_all.json

# Extract baseline
python3 extract_baseline_entities.py \
  --input week4_results/podcast_all.json \
  --output week4_results/podcast_all_baseline.json

# Re-extract with pipeline
python3 reextract_pilot.py \
  --input week4_results/podcast_all.json \
  --baseline week4_results/podcast_all_baseline.json \
  --output week4_results/podcast_all_results.json \
  --use-pipeline

# Generate report
python3 compare_extractions.py \
  --results week4_results/podcast_all_results.json \
  --output week4_results/podcast_all_report.md
```

### Step 4.2: GitHub Documents (6 docs)

```bash
# Select all GitHub documents
python3 select_pilot_documents.py \
  --source github \
  --count 6 \
  --output week4_results/github_all.json

# Extract baseline
python3 extract_baseline_entities.py \
  --input week4_results/github_all.json \
  --output week4_results/github_all_baseline.json

# Re-extract with pipeline
python3 reextract_pilot.py \
  --input week4_results/github_all.json \
  --baseline week4_results/github_all_baseline.json \
  --output week4_results/github_all_results.json \
  --use-pipeline

# Generate report
python3 compare_extractions.py \
  --results week4_results/github_all_results.json \
  --output week4_results/github_all_report.md
```

### Step 4.3: Aggregate Podcast + GitHub

```bash
# Combine podcast and github results
cat > week4_results/aggregate_other.py << 'EOF'
#!/usr/bin/env python3
"""Aggregate podcast and github results."""
import json

sources = [
    'week4_results/podcast_all_results.json',
    'week4_results/github_all_results.json'
]

all_results = {}
for source_file in sources:
    with open(source_file) as f:
        data = json.load(f)
        all_results.update(data)

with open('week4_results/other_all_results.json', 'w') as f:
    json.dump(all_results, f, indent=2)

print(f"Aggregated {len(all_results)} documents")
EOF

python3 week4_results/aggregate_other.py

# Generate report
python3 compare_extractions.py \
  --results week4_results/other_all_results.json \
  --output week4_results/other_all_report.md
```

---

## 📋 Day 5: Week 4 Validation

### Step 5.1: Aggregate All Week 4 Results

**Goal**: Combine all sources (website, notion, podcast, github) into single report

```bash
cd /opt/projects/koi-processor/scripts/reextraction

# Aggregate all Week 4 results
cat > week4_results/aggregate_week4.py << 'EOF'
#!/usr/bin/env python3
"""Aggregate all Week 4 source results."""
import json

sources = [
    'week4_results/website_all_results.json',
    'week4_results/notion_all_results.json',
    'week4_results/podcast_all_results.json',
    'week4_results/github_all_results.json'
]

all_results = {}
for source_file in sources:
    try:
        with open(source_file) as f:
            data = json.load(f)
            all_results.update(data)
            print(f"Added {len(data)} docs from {source_file}")
    except FileNotFoundError:
        print(f"WARNING: {source_file} not found, skipping")

with open('week4_results/week4_all_results.json', 'w') as f:
    json.dump(all_results, f, indent=2)

print(f"\nTotal: {len(all_results)} documents aggregated")
EOF

python3 week4_results/aggregate_week4.py
# Expected: ~604 documents
```

### Step 5.2: Generate Week 4 Comprehensive Report

```bash
python3 compare_extractions.py \
  --results week4_results/week4_all_results.json \
  --output week4_results/week4_all_report.md

# Review
less week4_results/week4_all_report.md
```

### Step 5.3: Compare Week 3 vs Week 4

**Goal**: Validate Week 4 results are consistent with Week 3 baseline

```bash
cat > week4_results/compare_weeks.py << 'EOF'
#!/usr/bin/env python3
"""Compare Week 3 vs Week 4 results."""
import json

# Load results
with open('../week3_results/discourse_all_results.json') as f:
    week3 = json.load(f)

with open('week4_results/week4_all_results.json') as f:
    week4 = json.load(f)

def analyze(results):
    baseline_count = 0
    passed_count = 0
    blocked_count = 0

    for doc_id, doc_data in results.items():
        baseline_count += len(doc_data['baseline_entities'])
        passed_count += len(doc_data['pipeline_results']['valid'])
        blocked_count += len(doc_data['pipeline_results']['blocked'])

    pass_rate = (passed_count / baseline_count * 100) if baseline_count > 0 else 0
    block_rate = (blocked_count / baseline_count * 100) if baseline_count > 0 else 0

    return {
        'docs': len(results),
        'baseline': baseline_count,
        'passed': passed_count,
        'blocked': blocked_count,
        'pass_rate': pass_rate,
        'block_rate': block_rate
    }

week3_metrics = analyze(week3)
week4_metrics = analyze(week4)

print("=" * 60)
print("WEEK 3 vs WEEK 4 COMPARISON")
print("=" * 60)
print()
print(f"{'Metric':<20} {'Week 3':<15} {'Week 4':<15} {'Diff':<10}")
print("-" * 60)
print(f"{'Documents':<20} {week3_metrics['docs']:<15} {week4_metrics['docs']:<15} {week4_metrics['docs'] - week3_metrics['docs']:<10}")
print(f"{'Baseline Entities':<20} {week3_metrics['baseline']:<15} {week4_metrics['baseline']:<15} {week4_metrics['baseline'] - week3_metrics['baseline']:<10}")
print(f"{'Passed':<20} {week3_metrics['passed']:<15} {week4_metrics['passed']:<15} {week4_metrics['passed'] - week3_metrics['passed']:<10}")
print(f"{'Blocked':<20} {week3_metrics['blocked']:<15} {week4_metrics['blocked']:<15} {week4_metrics['blocked'] - week3_metrics['blocked']:<10}")
print(f"{'Pass Rate':<20} {week3_metrics['pass_rate']:.2f}%{'':<10} {week4_metrics['pass_rate']:.2f}%{'':<10} {week4_metrics['pass_rate'] - week3_metrics['pass_rate']:.2f}%")
print(f"{'Block Rate':<20} {week3_metrics['block_rate']:.2f}%{'':<10} {week4_metrics['block_rate']:.2f}%{'':<10} {week4_metrics['block_rate'] - week3_metrics['block_rate']:.2f}%")
print()

# Validation
print("VALIDATION:")
pass_diff = abs(week4_metrics['pass_rate'] - week3_metrics['pass_rate'])
if pass_diff < 2.0:
    print(f"✅ Pass rates consistent (diff: {pass_diff:.2f}%)")
else:
    print(f"⚠️  Pass rates differ significantly (diff: {pass_diff:.2f}%)")

if week4_metrics['pass_rate'] >= 95.0:
    print(f"✅ Week 4 pass rate meets threshold (95%+)")
else:
    print(f"❌ Week 4 pass rate below threshold: {week4_metrics['pass_rate']:.2f}%")

print()
EOF

python3 week4_results/compare_weeks.py
```

**Expected Output**:
```
===========================================================
WEEK 3 vs WEEK 4 COMPARISON
===========================================================

Metric               Week 3          Week 4          Diff
------------------------------------------------------------
Documents            411             604             193
Baseline Entities    2,556           ~12,490         ~9,934
Passed               2,514           ~12,240         ~9,726
Blocked              53              ~250            ~197
Pass Rate            98.36%          98.0%           -0.36%
Block Rate           2.07%           2.0%            -0.07%

VALIDATION:
✅ Pass rates consistent (diff: 0.36%)
✅ Week 4 pass rate meets threshold (95%+)
```

### Step 5.4: Final Quality Assessment

**Create Final Report**: `week4_results/WEEK4_COMPLETE.md`

```bash
cat > week4_results/WEEK4_COMPLETE.md << 'EOF'
# Week 4 Complete - Remaining Sources Re-extraction

**Date**: 2025-12-09
**Scope**: 604 documents (Website, Notion, Podcast, GitHub)
**Purpose**: Final re-extraction batch
**Execution**: Production server (202.61.196.119)

---

## Executive Summary

**Status**: [SUCCESS/ISSUES]

**Documents Processed**: [ACTUAL_COUNT] / 604
- Website: [COUNT] / 454
- Notion: [COUNT] / 78
- Podcast: [COUNT] / 66
- GitHub: [COUNT] / 6

**Entities**:
- Baseline: [COUNT]
- Passed: [COUNT]
- Blocked: [COUNT]

**Metrics**:
- Pass Rate: [RATE]%
- Block Rate: [RATE]%
- False Positive Rate: [RATE]%

**Recommendation**: [GO/NO-GO] for final analysis

---

## Processing Summary by Source

### Website (454 documents)

| Batch | Docs | Baseline | Passed | Blocked | Pass Rate |
|-------|------|----------|--------|---------|-----------|
| 1 | 200 | [#] | [#] | [#] | [%] |
| 2 | 200 | [#] | [#] | [#] | [%] |
| 3 | 54 | [#] | [#] | [#] | [%] |
| **TOTAL** | **454** | [#] | [#] | [#] | [%] |

### Notion (78 documents)

| Metric | Count |
|--------|-------|
| Documents | 78 |
| Baseline | [#] |
| Passed | [#] |
| Blocked | [#] |
| Pass Rate | [%] |

### Podcast (66 documents)

| Metric | Count |
|--------|-------|
| Documents | 66 |
| Baseline | [#] |
| Passed | [#] |
| Blocked | [#] |
| Pass Rate | [%] |

### GitHub (6 documents)

| Metric | Count |
|--------|-------|
| Documents | 6 |
| Baseline | [#] |
| Passed | [#] |
| Blocked | [#] |
| Pass Rate | [%] |

---

## Week 3 vs Week 4 Comparison

| Metric | Week 3 | Week 4 | Consistent? |
|--------|--------|--------|-------------|
| Pass Rate | 98.36% | [%] | [YES/NO] |
| Block Rate | 2.07% | [%] | [YES/NO] |
| FP Rate | ~0% | [%] | [YES/NO] |

---

## Block Analysis

### By Module

| Module | Count | % of Blocks |
|--------|-------|-------------|
| EntityQualityFilter | [#] | [%] |
| ConfidenceFilter | [#] | [%] |

### Sample Blocked Entities

[List representative sample of blocked entities with reasons]

---

## Final Assessment

### Overall Re-extraction Results

| Phase | Documents | Baseline | Passed | Blocked | Pass Rate |
|-------|-----------|----------|--------|---------|-----------|
| Week 3 (Discourse) | 411 | 2,556 | 2,514 | 53 | 98.36% |
| Week 4 (Remaining) | 604 | [#] | [#] | [#] | [%] |
| **TOTAL** | **1,015** | [#] | [#] | [#] | [%] |

### Quality Improvement

| Metric | Before Pipeline | After Pipeline | Improvement |
|--------|-----------------|----------------|-------------|
| Low-quality entities | [#] | 0 | 100% removed |
| Total entities | [#] | [#] | [%] reduction |
| Canonical resolutions | - | [#] | Applied |
| Type normalizations | - | [#] | Applied |

---

## GO/NO-GO Decision

### [GO/NO-GO] - [Proceed to Final Analysis / Investigate Issues]

**Criteria Met**:
- [ ] Pass rate > 95% (actual: [%])
- [ ] Block rate 2-5% (actual: [%])
- [ ] FP rate < 5% (actual: [%])
- [ ] Results consistent with Week 3 (98.36%)
- [ ] No critical issues found
- [ ] All batches validated

**Confidence**: [HIGH/MEDIUM/LOW]

**Rationale**:
[Explain the decision based on results]

---

## Next Steps

### If GO:
1. Create PROMPT_14 for final analysis and reporting
2. Aggregate all results (Weeks 3-4)
3. Generate comprehensive quality report
4. Document findings and recommendations
5. Plan production update

### If NO-GO:
1. Investigate discrepancies
2. Analyze false positives
3. Tune filters if needed
4. Re-run affected batches

---

## Deliverables

✅ **Created**:
1. `website_batch_[1-3].json` - Website batch inputs
2. `website_batch_[1-3]_baseline.json` - Baseline extractions
3. `website_batch_[1-3]_results.json` - Pipeline results
4. `website_all_results.json` - Aggregated website results
5. `notion_all_results.json` - Notion results
6. `podcast_all_results.json` - Podcast results
7. `github_all_results.json` - GitHub results
8. `week4_all_results.json` - All Week 4 aggregated
9. `WEEK4_COMPLETE.md` - This report

✅ **Validated**:
- All 604 documents processed
- Metrics consistent across sources
- No systematic issues
- Ready for final analysis

---

## Final Recommendation

**Decision**: [GO/NO-GO]

**Confidence**: [HIGH/MEDIUM/LOW]

**Risk Assessment**: [LOW/MEDIUM/HIGH]

**Signed Off**: Claude Code (Opus 4.5)

**Date**: 2025-12-09

---

*Week 4 - Remaining Sources Re-extraction Complete*
*Ready for PROMPT_14: Final Analysis & Reporting*
EOF

# Fill in the template with actual results
nano week4_results/WEEK4_COMPLETE.md
```

**Manual Step**: Fill in all `[PLACEHOLDERS]` with actual metrics from your results

### Step 5.5: Make GO/NO-GO Decision

**GO Criteria**:
- ✅ Pass rate ≥ 95% (target: ~98%)
- ✅ Block rate 2-5%
- ✅ FP rate < 5% (target: ~0%)
- ✅ Results consistent with Week 3 (98.36%)
- ✅ All sources processed successfully

**Decision Matrix**:

| Scenario | Action |
|----------|--------|
| All criteria met | **GO** - Proceed to final analysis (PROMPT_14) |
| Pass rate 90-95% | **CONDITIONAL GO** - Review blocks, proceed with caution |
| Pass rate < 90% | **NO-GO** - Investigate, tune filters, re-run |
| High FP rate (>5%) | **NO-GO** - Add whitelist entries, re-run affected batches |

---

## ✅ Completion Checklist

### Day 1: Website Batch 1
- [ ] 200 website documents selected
- [ ] Baseline extracted
- [ ] Pipeline re-extraction complete
- [ ] Comparison report generated
- [ ] Pass rate ≥ 95%

### Day 2: Website Batches 2-3
- [ ] Batch 2 (200 docs) processed and validated
- [ ] Batch 3 (54 docs) processed and validated
- [ ] Website aggregate report generated
- [ ] 454 website documents complete
- [ ] Metrics consistent across batches

### Day 3: Notion
- [ ] 78 Notion documents processed
- [ ] Baseline extracted
- [ ] Pipeline re-extraction complete
- [ ] Comparison report generated
- [ ] Results consistent with Week 3

### Day 4: Podcast + GitHub
- [ ] 66 Podcast documents processed
- [ ] 6 GitHub documents processed
- [ ] Both sources validated
- [ ] Aggregate report generated

### Day 5: Week 4 Validation
- [ ] All Week 4 results aggregated (604 docs)
- [ ] Comprehensive report generated
- [ ] Week 3 vs Week 4 comparison complete
- [ ] Final quality assessment documented
- [ ] GO/NO-GO decision made
- [ ] WEEK4_COMPLETE.md finalized

---

## 📊 Success Criteria

**Week 4 Complete When**:
- ✅ All 604 documents processed (Website, Notion, Podcast, GitHub)
- ✅ Pass rate ≥ 95% across all sources
- ✅ Results consistent with Week 3 (98.36%)
- ✅ False positive rate < 5%
- ✅ All batch reports generated
- ✅ Final decision documented

**If GO**: Ready for PROMPT_14 (Final Analysis & Reporting)

**If NO-GO**: Investigate issues, tune filters, re-run affected batches

---

## 🆘 Common Issues

### Issue 1: Source Has Fewer Documents Than Expected

**Example**: Database shows 430 website docs instead of 454

**Solution**:
```bash
# Check actual count
PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d elisa -c "SELECT source, COUNT(*) FROM documents GROUP BY source;"

# Adjust batch sizes accordingly
# Use actual count in select_pilot_documents.py
```

### Issue 2: Pass Rate Drops Significantly

**Example**: Website pass rate is 85% (vs Week 3's 98.36%)

**Solution**:
1. Check blocked entities in report
2. Look for false positives (valid entities blocked)
3. If found, add to whitelist and re-run
4. If no FPs, blocks may be legitimate (different content quality)

### Issue 3: Pipeline Errors During Re-extraction

**Error**: `ModuleNotFoundError` or `DatabaseError`

**Solution**:
```bash
# Check Python environment
cd /opt/projects/koi-processor
python3 -c "from src.knowledge_graph.graph_integration import KnowledgeGraphIntegrator; print('OK')"

# Check database connection
PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza -c "SELECT 1;"

# If issues persist, check logs
tail -100 /opt/projects/koi-processor/logs/*.log
```

### Issue 4: tmux Session Disconnected

**Solution**:
```bash
# Reconnect to session
ssh darren@202.61.196.119
tmux attach -t week4-reextraction

# If session doesn't exist, check if it was renamed
tmux ls

# Attach to correct session
tmux attach -t [session-name]
```

---

## 📚 References

- **Week 3 Results**: `/opt/projects/koi-processor/scripts/reextraction/week3_results/`
- **Re-extraction Scripts**: `/opt/projects/koi-processor/scripts/reextraction/`
- **Pipeline Modules**: `/opt/projects/koi-processor/src/knowledge_graph/postprocessing/modules/`
- **Configuration**: `/opt/projects/koi-processor/src/knowledge_graph/config/pipeline_config.json`

---

## 🎯 Deliverables

By end of Week 4:

1. **week4_all_results.json** - All 604 documents aggregated
2. **Individual source reports** - Website, Notion, Podcast, GitHub
3. **week4_all_report.md** - Comprehensive comparison report
4. **Week 3 vs Week 4 comparison** - Consistency validation
5. **WEEK4_COMPLETE.md** - Final assessment with GO/NO-GO
6. **All tests still passing** - No regressions

---

**Next Prompt**:
- If **GO**: `PROMPT_14_FINAL_ANALYSIS.md` (Aggregate, analyze, report)
- If **NO-GO**: Investigate, fix, re-run affected batches

---

**Last Updated**: 2025-12-09
**Version**: Week 4 Re-extraction Task
**Agent**: Claude Code (Opus 4.5)
**Duration**: 5 days
**Status**: 📋 Ready for handoff
