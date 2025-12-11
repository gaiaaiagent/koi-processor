# PROMPT 12: Week 3 - Discourse Re-extraction (1,407 Documents)

**Date**: 2025-12-09
**Phase**: Week 3 - First Large-Scale Re-extraction
**Duration**: 5 days
**Agent**: Claude Code (Opus 4.5)

---

## 🎯 Context

**Week 2 Pilot Complete**: 99 documents, 2,350 entities - STRONG GO

**Pilot Results**:
- ✅ **Pass rate**: 98.3% (2,310/2,350 entities)
- ✅ **Block rate**: 2.6% (60/2,350 entities)
- ✅ **False positive rate**: 0% (0/60 blocked)
- ✅ **Tests**: 170 passing
- ✅ **Consistency**: Stable across 10 → 43 → 99 doc tests

**Ready for**: First large-scale re-extraction (1,407 Discourse documents)

---

## 🎯 Your Mission

Re-extract all 1,407 Discourse documents (forum + sensor) with the validated pipeline. This is the first production-scale re-extraction.

**Scope**:
- **Forum posts**: 913 documents
- **Discourse sensor**: 494 documents
- **Total**: 1,407 documents (~14,000-21,000 entities)

**Tasks**:
1. **Day 1: Forum Posts Batch 1** (200 docs)
   - Select first 200 forum documents
   - Extract baseline
   - Re-extract with pipeline
   - Validate results

2. **Day 2: Forum Posts Batches 2-5** (713 docs)
   - Process remaining forum documents in batches
   - Monitor quality after each batch
   - Complete forum re-extraction (913 total)

3. **Day 3: Discourse Sensor Batch 1** (200 docs)
   - Select first 200 sensor documents
   - Extract baseline
   - Re-extract with pipeline
   - Validate results

4. **Day 4: Discourse Sensor Batches 2-3** (294 docs)
   - Process remaining sensor documents
   - Complete sensor re-extraction (494 total)

5. **Day 5: Week 3 Validation**
   - Aggregate all results (1,407 docs)
   - Generate comprehensive report
   - Validate quality metrics
   - Make GO/NO-GO decision for Week 4

---

## 🖥️ Execution Environment: Server (IMPORTANT)

### ⚠️ Run on Production Server, Not Locally

**Why Server Execution**:
- ✅ **Direct database access** - No network latency (localhost connection)
- ✅ **Much faster** - Production environment, optimized performance
- ✅ **Background processing** - Can disconnect, processing continues
- ✅ **Already deployed** - Code at `/opt/projects/koi-processor`
- ✅ **14-24 hours runtime** - No need to keep laptop on

**Why NOT Local**:
- ❌ Network round-trips: 14,000-21,000 entities × latency = VERY SLOW
- ❌ Database on server (`202.61.196.119:5433`) - would need SSH tunnel
- ❌ Need laptop on for 14-24 hours
- ❌ Less reliable (network disconnects)

### Server Setup (Before Day 1)

**Step 1: SSH to Server**
```bash
# Connect to production server
ssh darren@202.61.196.119
```

**Step 2: Navigate to Project**
```bash
# Go to koi-processor directory
cd /opt/projects/koi-processor

# Check current branch
git status
# Should be on: regen-prod
```

**Step 3: Pull Latest Code**
```bash
# Pull latest changes (includes Week 1-2 scripts)
git pull origin regen-prod

# Verify re-extraction scripts are present
ls -la scripts/reextraction/

# Expected files:
# - select_pilot_documents.py
# - extract_baseline_entities.py
# - reextract_pilot.py
# - compare_extractions.py
# - __init__.py
```

**Step 4: Start tmux Session**

**What is tmux?** - Terminal multiplexer that keeps sessions alive when you disconnect

```bash
# Create new tmux session for Week 3
tmux new -s week3-reextraction

# You're now in a tmux session
# Green bar at bottom shows: [week3-reextraction]

# IMPORTANT tmux commands:
# - Ctrl-B, then D  = Detach (disconnect but keep running)
# - Ctrl-D or "exit" = Close session (stops everything)
```

**Step 5: Navigate to Scripts Directory**
```bash
# Inside tmux session
cd /opt/projects/koi-processor/scripts/reextraction

# Create results directory
mkdir -p week3_results

# Check Python environment
which python3
# Should be: /usr/bin/python3 or similar

# Verify database connection
PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza -c "SELECT COUNT(*) FROM documents;"
# Should show document count without errors
```

### Disconnecting and Reconnecting

**To Disconnect (keeps processing running)**:
```bash
# Press: Ctrl-B, then press: D
# You'll see: [detached (from session week3-reextraction)]

# Can now close SSH, processing continues!
exit
```

**To Reconnect**:
```bash
# SSH back to server
ssh darren@202.61.196.119

# List tmux sessions
tmux ls
# Should show: week3-reextraction: 1 windows (created ...)

# Reconnect to session
tmux attach -t week3-reextraction

# You're back! Processing status visible
```

### Monitoring Progress

**Option 1: Stay attached to tmux**
- See output in real-time
- Can scroll up (Ctrl-B, then [, then arrow keys)
- Exit scroll mode: Press Q

**Option 2: Add logging to scripts**
```bash
# Run scripts with output redirection
python reextract_pilot.py ... 2>&1 | tee batch_1.log

# In another tmux window (Ctrl-B, then C = new window)
tail -f batch_1.log

# Switch between windows: Ctrl-B, then 0/1/2
```

**Option 3: Check results directory**
```bash
# In another SSH session (don't disconnect tmux)
ssh darren@202.61.196.119
cd /opt/projects/koi-processor/scripts/reextraction/week3_results
ls -lh
# See files being created
```

### After Completion

**Download Results (Optional)**
```bash
# From your local machine
scp -r darren@202.61.196.119:/opt/projects/koi-processor/scripts/reextraction/week3_results ~/Downloads/

# Or just specific files
scp darren@202.61.196.119:/opt/projects/koi-processor/scripts/reextraction/week3_results/WEEK3_DISCOURSE_COMPLETE.md ~/Downloads/
```

**Close tmux Session (when all done)**
```bash
# Inside tmux session
exit

# Or from outside
tmux kill-session -t week3-reextraction
```

---

## 📋 Batch Processing Strategy

### Why Batches?

- **Manageable**: 200 docs per batch (~2,000 entities)
- **Monitorable**: Check quality after each batch
- **Pausable**: Can stop if issues found
- **Recoverable**: Smaller rollback units

### Batch Schedule

| Day | Batch | Source | Docs | Cumulative | Est. Time |
|-----|-------|--------|------|------------|-----------|
| 1 | 1 | Forum | 200 | 200 | 2-3 hrs |
| 2 | 2 | Forum | 200 | 400 | 2-3 hrs |
| 2 | 3 | Forum | 200 | 600 | 2-3 hrs |
| 2 | 4 | Forum | 200 | 800 | 2-3 hrs |
| 2 | 5 | Forum | 113 | 913 | 1-2 hrs |
| 3 | 6 | Sensor | 200 | 1,113 | 2-3 hrs |
| 3 | 7 | Sensor | 200 | 1,313 | 2-3 hrs |
| 4 | 8 | Sensor | 94 | 1,407 | 1-2 hrs |

**Total estimated time**: 14-24 hours processing time across 5 days

---

## 📋 Day 1: Forum Posts Batch 1 (200 docs)

**Prerequisites**:
- ✅ SSH'd to server (`darren@202.61.196.119`)
- ✅ In tmux session (`week3-reextraction`)
- ✅ In directory: `/opt/projects/koi-processor/scripts/reextraction`
- ✅ `week3_results/` directory created

### Step 1.1: Select First Batch

```bash
# You should already be in: /opt/projects/koi-processor/scripts/reextraction
# If not: cd /opt/projects/koi-processor/scripts/reextraction

# Select ALL Discourse forum documents (we'll process in batches)
python select_pilot_documents.py \
  --source discourse-forum \
  --output week3_results/forum_all_documents.json

# Verify count
cat week3_results/forum_all_documents.json | jq '. | length'
# Expected: 913

# Split into batches of 200
python -c "
import json
with open('week3_results/forum_all_documents.json') as f:
    docs = json.load(f)

# Create batches
batch_size = 200
for i in range(0, len(docs), batch_size):
    batch_num = (i // batch_size) + 1
    batch = docs[i:i+batch_size]

    with open(f'week3_results/forum_batch_{batch_num}.json', 'w') as f:
        json.dump(batch, f, indent=2)

    print(f'Batch {batch_num}: {len(batch)} documents')
"

# Verify batches
ls -lh week3_results/forum_batch_*.json
```

**Expected output**:
```
Batch 1: 200 documents
Batch 2: 200 documents
Batch 3: 200 documents
Batch 4: 200 documents
Batch 5: 113 documents

-rw-r--r-- forum_batch_1.json
-rw-r--r-- forum_batch_2.json
-rw-r--r-- forum_batch_3.json
-rw-r--r-- forum_batch_4.json
-rw-r--r-- forum_batch_5.json
```

### Step 1.2: Process Batch 1

```bash
# Extract baseline for batch 1
echo "Processing Batch 1 (200 documents)..."
python extract_baseline_entities.py \
  --input week3_results/forum_batch_1.json \
  --output week3_results/forum_batch_1_baseline.json

# Check entity count
echo "Baseline entities:"
cat week3_results/forum_batch_1_baseline.json | jq 'reduce .[] as $doc (0; . + $doc.entity_count)'
# Expected: ~2,000-3,000 entities

# Re-extract with pipeline
python reextract_pilot.py \
  --input week3_results/forum_batch_1.json \
  --baseline week3_results/forum_batch_1_baseline.json \
  --output week3_results/forum_batch_1_results.json

# Generate comparison report
python compare_extractions.py \
  --baseline week3_results/forum_batch_1_baseline.json \
  --results week3_results/forum_batch_1_results.json \
  --output week3_results/forum_batch_1_report.md
```

### Step 1.3: Validate Batch 1 Results

```bash
# Check key metrics
cat week3_results/forum_batch_1_report.md | grep -A 5 "Executive Summary"

# Expected:
# - Pass Rate: ~98%
# - Block Rate: ~2-3%
# - False Positive Rate: < 1%
```

**Validation checklist**:
- [ ] Pass rate > 95%
- [ ] Block rate 2-5%
- [ ] No unexpected blocking patterns
- [ ] Results consistent with pilot (98.3%)

**If issues found**:
1. Review blocked entities (spot check 10-20)
2. Identify any false positives
3. Document issues
4. **STOP and consult** before proceeding to Batch 2

**If validation passes**:
- [ ] Document Batch 1 metrics
- [ ] Proceed to Day 2 (Batches 2-5)

---

## 📋 Day 2: Forum Posts Batches 2-5 (713 docs)

### Step 2.1: Process Batch 2 (200 docs)

```bash
echo "=== Processing Batch 2 (200 documents) ==="

# Baseline
python extract_baseline_entities.py \
  --input week3_results/forum_batch_2.json \
  --output week3_results/forum_batch_2_baseline.json

# Re-extract
python reextract_pilot.py \
  --input week3_results/forum_batch_2.json \
  --baseline week3_results/forum_batch_2_baseline.json \
  --output week3_results/forum_batch_2_results.json

# Compare
python compare_extractions.py \
  --baseline week3_results/forum_batch_2_baseline.json \
  --results week3_results/forum_batch_2_results.json \
  --output week3_results/forum_batch_2_report.md

# Quick validation
cat week3_results/forum_batch_2_report.md | grep "Pass Rate"
```

**Quick validation** (after each batch):
- Check pass rate is still ~98%
- Check no major increase in blocks
- Spot check 5-10 blocked entities

### Step 2.2: Process Batch 3 (200 docs)

Repeat same process as Batch 2:

```bash
echo "=== Processing Batch 3 (200 documents) ==="
# [Same commands as Batch 2, replace '2' with '3']
```

### Step 2.3: Process Batch 4 (200 docs)

```bash
echo "=== Processing Batch 4 (200 documents) ==="
# [Same commands as Batch 2, replace '2' with '4']
```

### Step 2.4: Process Batch 5 (113 docs - final forum batch)

```bash
echo "=== Processing Batch 5 (113 documents - FINAL FORUM BATCH) ==="
# [Same commands as Batch 2, replace '2' with '5']
```

### Step 2.5: Aggregate Forum Results

```bash
# Create aggregation script
cat > week3_results/aggregate_forum_results.py << 'EOF'
#!/usr/bin/env python3
"""Aggregate all forum batch results."""

import json
from pathlib import Path

results_dir = Path('week3_results')
batches = [1, 2, 3, 4, 5]

# Aggregate all results
aggregated = {}
total_baseline = 0
total_passed = 0
total_blocked = 0

for batch_num in batches:
    results_file = results_dir / f'forum_batch_{batch_num}_results.json'

    with open(results_file) as f:
        batch_results = json.load(f)

    # Merge into aggregated
    aggregated.update(batch_results)

    # Count entities
    for doc_data in batch_results.values():
        total_baseline += doc_data['baseline_count']
        total_passed += len(doc_data['pipeline_results']['valid'])
        total_blocked += len(doc_data['pipeline_results']['blocked'])

# Save aggregated results
output_file = results_dir / 'forum_all_results.json'
with open(output_file, 'w') as f:
    json.dump(aggregated, f, indent=2)

print(f"Forum Re-extraction Complete:")
print(f"  Documents: {len(aggregated)}")
print(f"  Baseline entities: {total_baseline}")
print(f"  Passed: {total_passed}")
print(f"  Blocked: {total_blocked}")
print(f"  Pass rate: {total_passed/total_baseline*100:.1f}%")
print(f"  Block rate: {total_blocked/total_baseline*100:.1f}%")
print(f"\nSaved to: {output_file}")
EOF

chmod +x week3_results/aggregate_forum_results.py
python week3_results/aggregate_forum_results.py
```

**Expected output**:
```
Forum Re-extraction Complete:
  Documents: 913
  Baseline entities: 9,130-13,695 (avg 10-15 per doc)
  Passed: 8,955-13,453
  Blocked: 175-242
  Pass rate: 98.0-98.5%
  Block rate: 1.5-2.5%
```

### Step 2.6: Day 2 Validation

**Checklist**:
- [ ] All 5 forum batches completed
- [ ] 913 documents processed total
- [ ] Pass rate still ~98%
- [ ] No systematic issues found
- [ ] Aggregated results saved

**If validation passes**: Proceed to Day 3 (Discourse Sensor)

---

## 📋 Day 3: Discourse Sensor Batch 1 (200 docs)

### Step 3.1: Select Sensor Batches

```bash
cd week3_results

# Select ALL Discourse sensor documents
python ../select_pilot_documents.py \
  --source discourse-sensor \
  --output sensor_all_documents.json

# Verify count
cat sensor_all_documents.json | jq '. | length'
# Expected: 494

# Split into batches
python -c "
import json
with open('sensor_all_documents.json') as f:
    docs = json.load(f)

batch_size = 200
for i in range(0, len(docs), batch_size):
    batch_num = (i // batch_size) + 1
    batch = docs[i:i+batch_size]

    with open(f'sensor_batch_{batch_num}.json', 'w') as f:
        json.dump(batch, f, indent=2)

    print(f'Batch {batch_num}: {len(batch)} documents')
"
```

**Expected output**:
```
Batch 1: 200 documents
Batch 2: 200 documents
Batch 3: 94 documents
```

### Step 3.2: Process Sensor Batch 1

```bash
echo "=== Processing Sensor Batch 1 (200 documents) ==="

# Baseline
python ../extract_baseline_entities.py \
  --input sensor_batch_1.json \
  --output sensor_batch_1_baseline.json

# Re-extract
python ../reextract_pilot.py \
  --input sensor_batch_1.json \
  --baseline sensor_batch_1_baseline.json \
  --output sensor_batch_1_results.json

# Compare
python ../compare_extractions.py \
  --baseline sensor_batch_1_baseline.json \
  --results sensor_batch_1_results.json \
  --output sensor_batch_1_report.md

# Quick validation
cat sensor_batch_1_report.md | grep -A 3 "Executive Summary"
```

**Validation**: Same criteria as forum batches (pass rate > 95%)

---

## 📋 Day 4: Discourse Sensor Batches 2-3 (294 docs)

### Step 4.1: Process Sensor Batch 2 (200 docs)

```bash
echo "=== Processing Sensor Batch 2 (200 documents) ==="
# [Same process as Sensor Batch 1, replace '1' with '2']
```

### Step 4.2: Process Sensor Batch 3 (94 docs - final batch)

```bash
echo "=== Processing Sensor Batch 3 (94 documents - FINAL SENSOR BATCH) ==="
# [Same process as Sensor Batch 1, replace '1' with '3']
```

### Step 4.3: Aggregate Sensor Results

```bash
cat > aggregate_sensor_results.py << 'EOF'
#!/usr/bin/env python3
"""Aggregate all sensor batch results."""

import json
from pathlib import Path

results_dir = Path('.')
batches = [1, 2, 3]

aggregated = {}
total_baseline = 0
total_passed = 0
total_blocked = 0

for batch_num in batches:
    results_file = results_dir / f'sensor_batch_{batch_num}_results.json'

    with open(results_file) as f:
        batch_results = json.load(f)

    aggregated.update(batch_results)

    for doc_data in batch_results.values():
        total_baseline += doc_data['baseline_count']
        total_passed += len(doc_data['pipeline_results']['valid'])
        total_blocked += len(doc_data['pipeline_results']['blocked'])

output_file = results_dir / 'sensor_all_results.json'
with open(output_file, 'w') as f:
    json.dump(aggregated, f, indent=2)

print(f"Sensor Re-extraction Complete:")
print(f"  Documents: {len(aggregated)}")
print(f"  Baseline entities: {total_baseline}")
print(f"  Passed: {total_passed}")
print(f"  Blocked: {total_blocked}")
print(f"  Pass rate: {total_passed/total_baseline*100:.1f}%")
print(f"  Block rate: {total_blocked/total_baseline*100:.1f}%")
EOF

chmod +x aggregate_sensor_results.py
python aggregate_sensor_results.py
```

---

## 📋 Day 5: Week 3 Validation & Reporting

### Step 5.1: Aggregate ALL Week 3 Results

```bash
cd week3_results

cat > aggregate_week3_results.py << 'EOF'
#!/usr/bin/env python3
"""Aggregate all Week 3 results (forum + sensor)."""

import json
from pathlib import Path

results_dir = Path('.')

# Load forum and sensor results
with open('forum_all_results.json') as f:
    forum_results = json.load(f)

with open('sensor_all_results.json') as f:
    sensor_results = json.load(f)

# Merge
week3_results = {**forum_results, **sensor_results}

# Calculate totals
total_docs = len(week3_results)
total_baseline = 0
total_passed = 0
total_blocked = 0

for doc_data in week3_results.values():
    total_baseline += doc_data['baseline_count']
    total_passed += len(doc_data['pipeline_results']['valid'])
    total_blocked += len(doc_data['pipeline_results']['blocked'])

# Save
output_file = 'week3_all_results.json'
with open(output_file, 'w') as f:
    json.dump(week3_results, f, indent=2)

print("=" * 70)
print("WEEK 3 RE-EXTRACTION COMPLETE")
print("=" * 70)
print(f"\nDocuments processed: {total_docs}")
print(f"  - Forum: {len(forum_results)}")
print(f"  - Sensor: {len(sensor_results)}")
print(f"\nEntities:")
print(f"  - Baseline: {total_baseline:,}")
print(f"  - Passed: {total_passed:,}")
print(f"  - Blocked: {total_blocked:,}")
print(f"\nMetrics:")
print(f"  - Pass rate: {total_passed/total_baseline*100:.2f}%")
print(f"  - Block rate: {total_blocked/total_baseline*100:.2f}%")
print(f"\nSaved to: {output_file}")
print("=" * 70)
EOF

python aggregate_week3_results.py
```

### Step 5.2: Generate Final Comparison Report

```bash
# Generate comprehensive Week 3 report
python ../compare_extractions.py \
  --baseline <(cat forum_all_results.json sensor_all_results.json | jq -s 'add') \
  --results week3_all_results.json \
  --output WEEK3_COMPARISON_REPORT.md

# Note: You may need to create a combined baseline file
# Alternatively, update compare_extractions.py to handle this
```

### Step 5.3: Spot Check Blocked Entities

```bash
# Extract all blocked entities
python ../extract_blocked_entities.py \
  --input week3_all_results.json \
  --output week3_blocked_entities.json

# Count by pattern
cat week3_blocked_entities.json | jq 'group_by(.block_reason | split(":")[0]) | map({pattern: .[0].block_reason | split(":")[0], count: length}) | sort_by(-.count)'

# Spot check: Review top 10 from each pattern
# Verify they're correct blocks (true positives)
```

**Spot check sample**:
- Review 10 from technical_pattern
- Review 10 from generic_noun/lowercase_person
- Review 10 from sentence_like
- Total: ~30 entities reviewed out of ~350-550 blocked

### Step 5.4: Create Week 3 Completion Report

**File**: `week3_results/WEEK3_DISCOURSE_COMPLETE.md`

```markdown
# Week 3 Complete - Discourse Re-extraction

**Date**: 2025-12-09
**Scope**: 1,407 Discourse documents (913 forum + 494 sensor)
**Purpose**: First large-scale re-extraction

---

## Executive Summary

**Status**: ✅ SUCCESS / ⚠️ ISSUES / ❌ FAILED

**Documents Processed**: 1,407 (100%)
- Forum: 913
- Sensor: 494

**Entities**:
- Baseline: [X,XXX]
- Passed: [X,XXX]
- Blocked: [XXX]

**Metrics**:
- Pass Rate: [XX.X]%
- Block Rate: [X.X]%
- False Positive Rate: [X.X]%

**Recommendation**: GO / NO-GO for Week 4

---

## Processing Summary

### By Batch

| Day | Batch | Source | Docs | Baseline | Passed | Blocked | Pass Rate |
|-----|-------|--------|------|----------|--------|---------|-----------|
| 1 | 1 | Forum | 200 | [X] | [X] | [X] | [XX]% |
| 2 | 2 | Forum | 200 | [X] | [X] | [X] | [XX]% |
| 2 | 3 | Forum | 200 | [X] | [X] | [X] | [XX]% |
| 2 | 4 | Forum | 200 | [X] | [X] | [X] | [XX]% |
| 2 | 5 | Forum | 113 | [X] | [X] | [X] | [XX]% |
| 3 | 6 | Sensor | 200 | [X] | [X] | [X] | [XX]% |
| 3 | 7 | Sensor | 200 | [X] | [X] | [X] | [XX]% |
| 4 | 8 | Sensor | 94 | [X] | [X] | [X] | [XX]% |
| **Total** | **8** | **Both** | **1,407** | **[X,XXX]** | **[X,XXX]** | **[XXX]** | **[XX.X]%** |

### Consistency Check

| Metric | Pilot (99 docs) | Week 3 (1,407 docs) | Consistent? |
|--------|-----------------|---------------------|-------------|
| Pass Rate | 98.3% | [XX.X]% | ✅/❌ |
| Block Rate | 2.6% | [X.X]% | ✅/❌ |
| FP Rate | 0% | [X.X]% | ✅/❌ |

---

## Block Analysis

### By Pattern

| Pattern | Count | % of Blocks | True Pos | False Pos |
|---------|-------|-------------|----------|-----------|
| technical_pattern | [X] | [XX]% | [X] | [X] |
| lowercase_person | [X] | [XX]% | [X] | [X] |
| generic_pattern | [X] | [XX]% | [X] | [X] |
| confidence_too_low | [X] | [XX]% | [X] | [X] |
| ... | | | | |

### Spot Check Results

**Sample Size**: 30 entities (from [XXX] total blocked)

**Classification**:
- True Positives: [XX] ([XX]%)
- False Positives: [X] ([X]%)

**False Positives Found** (if any):
1. [Entity name] - [Why it should not be blocked]
2. ...

---

## Quality Improvement

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Low-quality entities | [XXX] | [~0] | [XX]% removed |
| Type inconsistency | [XX]% | [~5]% | [XX]% improvement |
| Canonical coverage | [XX]% | [~100]% | [XX]% improvement |

---

## Issues Found

### Critical Issues
- [ ] None / [List critical issues]

### Minor Issues
- [ ] None / [List minor issues]

### False Positives
- [ ] None / [List false positives]

---

## GO/NO-GO Decision

### Option A: GO - Proceed to Week 4 ✅

**Criteria Met**:
- [ ] Pass rate > 95%
- [ ] Block rate 2-5%
- [ ] FP rate < 5%
- [ ] Results consistent with pilot
- [ ] No critical issues

**Recommendation**: Proceed to Week 4 (Remaining sources - 2,090 documents)

**Confidence**: HIGH / MEDIUM / LOW

### Option B: NO-GO - Issues Need Resolution ❌

**Issues**:
- [ ] Pass rate < 95%
- [ ] FP rate > 5%
- [ ] Major inconsistencies
- [ ] Critical blocking issues

**Required Actions**:
1. [Action needed]
2. [Action needed]
3. Re-run affected batches

---

## Week 4 Readiness

**If GO**:
- [ ] All 1,407 Discourse documents processed
- [ ] Quality metrics validated
- [ ] No systematic issues
- [ ] Ready for GitHub (172 docs), Web (452 docs), Notion (47 docs), Other (1,419 docs)

**Week 4 Plan**:
- Day 1: GitHub (172 docs)
- Day 2: Web sources (452 docs)
- Day 3: Notion (47 docs)
- Day 4-5: Other (1,419 docs)

**Expected Week 4 metrics**:
- Total docs: 2,090
- Total entities: ~20,000-31,350
- Pass rate: ~98%
- Processing time: ~20-30 hours across 5 days

---

## Deliverables

✅ **Created**:
1. 8 batch result files (forum_batch_*, sensor_batch_*)
2. forum_all_results.json - All 913 forum documents
3. sensor_all_results.json - All 494 sensor documents
4. week3_all_results.json - Combined 1,407 documents
5. week3_blocked_entities.json - All blocked entities
6. WEEK3_COMPARISON_REPORT.md - Detailed analysis
7. WEEK3_DISCOURSE_COMPLETE.md - This report

✅ **Validated**:
- Batch-by-batch processing successful
- Metrics consistent across batches
- No major issues at scale
- All 170 tests still passing

---

## Final Recommendation

**Decision**: GO / NO-GO

**Rationale**: [Clear explanation]

**Confidence**: HIGH / MEDIUM / LOW

**Risk Assessment**: [Analysis]

**Signed Off**: [Agent/Team]

**Date**: 2025-12-09

---

*Week 3 - First large-scale re-extraction complete*
```

---

## ✅ Completion Checklist

### Day 1: Forum Batch 1
- [ ] 200 documents selected
- [ ] Baseline extracted
- [ ] Re-extracted with pipeline
- [ ] Results validated (pass rate > 95%)

### Day 2: Forum Batches 2-5
- [ ] Batch 2 complete (200 docs)
- [ ] Batch 3 complete (200 docs)
- [ ] Batch 4 complete (200 docs)
- [ ] Batch 5 complete (113 docs)
- [ ] Forum results aggregated (913 docs total)
- [ ] Forum metrics validated

### Day 3: Sensor Batch 1
- [ ] 200 documents selected
- [ ] Baseline extracted
- [ ] Re-extracted with pipeline
- [ ] Results validated

### Day 4: Sensor Batches 2-3
- [ ] Batch 2 complete (200 docs)
- [ ] Batch 3 complete (94 docs)
- [ ] Sensor results aggregated (494 docs total)
- [ ] Sensor metrics validated

### Day 5: Week 3 Validation
- [ ] All results aggregated (1,407 docs)
- [ ] Comprehensive comparison report generated
- [ ] Spot check completed (30+ entities)
- [ ] WEEK3_DISCOURSE_COMPLETE.md created
- [ ] GO/NO-GO decision documented
- [ ] All 170 tests still passing

---

## 📊 Success Criteria

**Week 3 Successful When**:
- ✅ All 1,407 documents processed
- ✅ Pass rate > 95% overall
- ✅ Block rate 2-5%
- ✅ FP rate < 5%
- ✅ Results consistent with pilot (98.3%)
- ✅ No critical issues found
- ✅ Batch-by-batch validation passed

**If GO**: Ready for PROMPT_13 (Week 4: Remaining Sources - 2,090 documents)

**If NO-GO**: Document issues, fix, re-run affected batches

---

## 🆘 Common Issues

### Issue 1: Batch Processing Slower Than Expected

**If taking > 3 hours per batch**:
- Check system resources (CPU, memory)
- Consider processing overnight
- Not a blocker, just slower

### Issue 2: Pass Rate Drops Below 95%

**Action**:
1. Stop processing new batches
2. Review blocked entities from failing batch
3. Identify cause (new patterns, edge cases)
4. Fix if needed, re-run batch

### Issue 3: Memory Issues with Large Batches

**If script crashes**:
- Reduce batch size to 100 docs
- Process in smaller chunks
- Clear memory between batches

### Issue 4: Inconsistent Results Across Batches

**If pass rate varies > 5% between batches**:
- Review batch selection (quality distribution)
- Check for source-specific issues
- May indicate batches have different characteristics

---

## 📚 References

- **Pilot Results**: `scripts/reextraction/WEEK2_PILOT_COMPLETE.md`
- **Validation Results**: `scripts/reextraction/VALIDATION_REPORT_50_DOCS.md`
- **Scripts**: `scripts/reextraction/` directory
- **Tests**: 170 passing tests

---

## 🎯 Deliverables

By end of Week 3:

1. **8 batch results** - Individual batch processing results
2. **forum_all_results.json** - 913 forum documents
3. **sensor_all_results.json** - 494 sensor documents
4. **week3_all_results.json** - Combined 1,407 documents
5. **week3_blocked_entities.json** - All blocked entities
6. **WEEK3_COMPARISON_REPORT.md** - Comprehensive analysis
7. **WEEK3_DISCOURSE_COMPLETE.md** - Final report with GO/NO-GO
8. **All tests passing** - 170 tests, no regressions

---

**Next Prompt**:
- If **GO**: `PROMPT_13_WEEK4_REMAINING_SOURCES.md` (2,090 documents)
- If **NO-GO**: Document issues, fix, re-run affected batches

---

**Last Updated**: 2025-12-09
**Version**: Week 3 Full Re-extraction
**Agent**: Claude Code (Opus 4.5)
**Duration**: 5 days
**Status**: 📋 Ready for handoff
