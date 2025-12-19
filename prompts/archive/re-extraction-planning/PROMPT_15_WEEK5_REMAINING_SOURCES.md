# PROMPT 15: Week 5 - Remaining Sources Re-extraction (638 Documents)

**Date**: 2025-12-09
**Phase**: Week 5 - Final Text Sources
**Duration**: 3-4 days
**Agent**: Claude Code (Opus 4.5)

---

## 🎯 Context

**Checkpoint Complete**: 1,015 documents analyzed - STRONG GO

**Weeks 3-4 Results**:
- Documents: 1,015 (49% of extractable corpus)
- Pass rate: 97.63% ✅
- Block rate: 2.86% ✅
- Stability: 0.87% variance (EXCELLENT) ✅
- False positives: 0% ✅

**Ready for**: Final batch of text sources (638 documents)

---

## 🎯 Your Mission

Re-extract the remaining 638 documents across 4 sources with the validated pipeline. This completes all text-based sources before GitHub markdown setup.

**Scope**:
- **Remaining Discourse**: 569 documents (complete all discourse content)
- **YouTube**: 15 documents (video transcripts)
- **GitLab**: 30 documents (repository documentation)
- **GitHub Activity**: 24 documents (issue discussions, PR comments)
- **Total**: 638 documents (~6,500-7,500 entities)

**Tasks**:
1. **Day 1: Discourse Batch 1** (200 docs)
   - Select first 200 remaining discourse documents
   - Extract baseline
   - Re-extract with pipeline
   - Validate results

2. **Day 2: Discourse Batches 2-3** (369 docs)
   - Process remaining discourse documents
   - Complete all discourse re-extraction (980 total)
   - Validate consistency with Week 3

3. **Day 3: YouTube + GitLab** (45 docs)
   - Process all YouTube documents (15)
   - Process all GitLab documents (30)
   - Validate results

4. **Day 4: GitHub Activity + Week 5 Validation** (24 docs + analysis)
   - Process GitHub Activity documents (24)
   - Aggregate all Week 5 results (638 docs)
   - Generate comprehensive report
   - Make GO/NO-GO decision for Week 6

---

## 🖥️ Execution Environment: Server (IMPORTANT)

### ⚠️ Run on Production Server, Not Locally

**Why Server Execution**:
- ✅ **Direct database access** - No network latency (localhost connection)
- ✅ **Much faster** - Production environment, optimized performance
- ✅ **Background processing** - Can disconnect, processing continues
- ✅ **Already deployed** - Code at `/opt/projects/koi-processor`
- ✅ **10-15 hours runtime** - No need to keep laptop on

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

**Step 3: Reconnect to or Create tmux Session**

**If Week 4 session still exists**:
```bash
# List sessions
tmux ls

# If week4-reextraction exists:
tmux attach -t week4-reextraction

# Rename it to week5
# Press: Ctrl-B, then type: :rename-session week5-reextraction
# Press: Enter
```

**If starting fresh**:
```bash
# Create new session for Week 5
tmux new -s week5-reextraction
```

**Step 4: Create Week 5 Results Directory**
```bash
cd /opt/projects/koi-processor/scripts/reextraction
mkdir -p week5_results
cd week5_results
```

---

## 📋 Day 1: Remaining Discourse Batch 1 (200 documents)

### Prerequisites

Before starting, verify:
```bash
# Check you're in tmux
echo $TMUX
# Should show something (not empty)

# Verify database connection
PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza -c "SELECT COUNT(*) FROM koi_memories WHERE source_sensor LIKE 'discourse-sensor%';"
# Should show ~980 total discourse memories

# Count already processed
cd /opt/projects/koi-processor/scripts/reextraction
python3 << 'EOF'
import json
with open('week3_results/discourse_all_results.json') as f:
    week3 = json.load(f)
    week3_docs = week3.get('results', {})
    print(f"Week 3 processed: {len(week3_docs)} discourse documents")
EOF
# Should show: 411

# Check Python environment
cd /opt/projects/koi-processor
python3 -c "from src.knowledge_graph.graph_integration import KnowledgeGraphIntegrator; print('OK')"
# Should print: OK
```

### Step 1.1: Select Remaining Discourse Documents (Batch 1)

**Goal**: Select first 200 remaining discourse documents (not already processed in Week 3)

```bash
cd /opt/projects/koi-processor/scripts/reextraction

# Create script to exclude Week 3 documents
cat > select_remaining_discourse.py << 'EOF'
#!/usr/bin/env python3
"""Select remaining discourse documents (excluding Week 3)."""
import json
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

# Load Week 3 documents to exclude
week3_file = Path('week3_results/discourse_all_documents.json')
with open(week3_file) as f:
    week3_data = json.load(f)
    week3_rids = set(doc['document_rid'] for doc in week3_data['documents'])
    print(f"Week 3 processed: {len(week3_rids)} documents")

# Import selection script
from select_pilot_documents import connect_db, get_documents_by_quality

conn = connect_db()

# Get all discourse documents across quality tiers
all_discourse = []

# High quality (0.85+)
high = get_documents_by_quality(conn, 0.85, 1.0, 1000, source_filter='discourse')
all_discourse.extend(high)

# Medium quality (0.70-0.85)
medium = get_documents_by_quality(conn, 0.70, 0.85, 1000, source_filter='discourse')
all_discourse.extend(medium)

# Low quality (< 0.70) - if any
low = get_documents_by_quality(conn, 0.0, 0.70, 1000, source_filter='discourse')
all_discourse.extend(low)

print(f"Total discourse documents: {len(all_discourse)}")

# Filter out Week 3 documents
remaining = [doc for doc in all_discourse if doc['document_rid'] not in week3_rids]
print(f"Remaining after Week 3: {len(remaining)}")

# Take first N
batch_size = int(sys.argv[1]) if len(sys.argv) > 1 else 200
offset = int(sys.argv[2]) if len(sys.argv) > 2 else 0

batch = remaining[offset:offset + batch_size]
print(f"Selected batch: {len(batch)} documents (offset {offset})")

# Save
output_file = Path(f'week5_results/discourse_remaining_batch_{offset//200 + 1}.json')
output_data = {
    'generated_at': str(Path(__file__).parent),
    'source_filter': 'discourse (remaining)',
    'total_count': len(batch),
    'week3_excluded': len(week3_rids),
    'documents': batch
}

with open(output_file, 'w') as f:
    json.dump(output_data, f, indent=2)

print(f"Saved: {output_file}")
EOF

chmod +x select_remaining_discourse.py

# Run selection
python3 select_remaining_discourse.py 200 0

# Verify output
cat week5_results/discourse_remaining_batch_1.json | python3 -c "import json, sys; d=json.load(sys.stdin); print(f\"Batch 1: {d['total_count']} documents\")"
```

**Expected**: ~200 documents (or fewer if < 569 remaining)

**Output**: `week5_results/discourse_remaining_batch_1.json`

### Step 1.2: Extract Baseline Entities

**Goal**: Extract entities from baseline (current database)

```bash
cd /opt/projects/koi-processor/scripts/reextraction

python3 extract_baseline_entities.py \
  --input week5_results/discourse_remaining_batch_1.json \
  --output week5_results/discourse_remaining_batch_1_baseline.json

# Check results
cat week5_results/discourse_remaining_batch_1_baseline.json | python3 -c "import json, sys; docs=json.load(sys.stdin); print(f\"Docs: {len(docs)}, Entities: {sum(len(d['entities']) for d in docs)}\")"
```

**Expected**:
- ~200 documents
- ~2,000-2,500 entities total (~10-12 per doc)
- Confidence scores 0.70-0.95

**Output**: `week5_results/discourse_remaining_batch_1_baseline.json`

### Step 1.3: Re-extract with Pipeline

**Goal**: Re-extract entities through validated pipeline

```bash
python3 reextract_pilot.py \
  --input week5_results/discourse_remaining_batch_1.json \
  --baseline week5_results/discourse_remaining_batch_1_baseline.json \
  --output week5_results/discourse_remaining_batch_1_results.json \
  --use-pipeline

# Monitor progress (optional)
tail -f week5_results/discourse_remaining_batch_1_results.json
```

**Expected**:
- ~200 documents processed
- ~97-98% pass rate (based on Week 3 discourse: 98.36%)
- ~2-3% block rate
- ~0% false positive rate

**Output**: `week5_results/discourse_remaining_batch_1_results.json`

### Step 1.4: Generate Comparison Report

**Goal**: Validate results match expected metrics

```bash
python3 compare_extractions.py \
  --results week5_results/discourse_remaining_batch_1_results.json \
  --output week5_results/discourse_remaining_batch_1_report.md

# Review report
cat week5_results/discourse_remaining_batch_1_report.md
```

**Validation Checks**:
- [ ] Pass rate ≥ 95% (target: ~98% like Week 3)
- [ ] Block rate 2-5%
- [ ] FP rate < 5% (target: ~0%)
- [ ] No systematic issues in blocked entities

**If Validation Fails**: STOP and investigate before proceeding to Day 2

---

## 📋 Day 2: Remaining Discourse Batches 2-3 (369 documents)

### Step 2.1: Discourse Batch 2 (200 documents)

```bash
cd /opt/projects/koi-processor/scripts/reextraction

# Select batch 2
python3 select_remaining_discourse.py 200 200

# Extract baseline
python3 extract_baseline_entities.py \
  --input week5_results/discourse_remaining_batch_2.json \
  --output week5_results/discourse_remaining_batch_2_baseline.json

# Re-extract with pipeline
python3 reextract_pilot.py \
  --input week5_results/discourse_remaining_batch_2.json \
  --baseline week5_results/discourse_remaining_batch_2_baseline.json \
  --output week5_results/discourse_remaining_batch_2_results.json \
  --use-pipeline

# Generate report
python3 compare_extractions.py \
  --results week5_results/discourse_remaining_batch_2_results.json \
  --output week5_results/discourse_remaining_batch_2_report.md
```

### Step 2.2: Discourse Batch 3 (169 documents, or remaining)

```bash
# Select remaining discourse documents
python3 select_remaining_discourse.py 200 400

# Extract baseline
python3 extract_baseline_entities.py \
  --input week5_results/discourse_remaining_batch_3.json \
  --output week5_results/discourse_remaining_batch_3_baseline.json

# Re-extract with pipeline
python3 reextract_pilot.py \
  --input week5_results/discourse_remaining_batch_3.json \
  --baseline week5_results/discourse_remaining_batch_3_baseline.json \
  --output week5_results/discourse_remaining_batch_3_results.json \
  --use-pipeline

# Generate report
python3 compare_extractions.py \
  --results week5_results/discourse_remaining_batch_3_results.json \
  --output week5_results/discourse_remaining_batch_3_report.md
```

### Step 2.3: Aggregate All Discourse Results

**Goal**: Combine Week 3 + Week 5 discourse batches into complete discourse report

```bash
cd /opt/projects/koi-processor/scripts/reextraction

cat > week5_results/aggregate_all_discourse.py << 'EOF'
#!/usr/bin/env python3
"""Aggregate all discourse results (Week 3 + Week 5)."""
import json
from pathlib import Path

# Load Week 3 discourse results
week3_file = Path('../week3_results/discourse_all_results.json')
with open(week3_file) as f:
    week3_data = json.load(f)
    week3_results = week3_data.get('results', {})
    print(f"Week 3: {len(week3_results)} discourse documents")

# Load Week 5 batches
week5_batches = [
    'discourse_remaining_batch_1_results.json',
    'discourse_remaining_batch_2_results.json',
    'discourse_remaining_batch_3_results.json'
]

week5_results = {}
for batch_file in week5_batches:
    batch_path = Path(batch_file)
    if not batch_path.exists():
        print(f"Skipping {batch_file} (not found)")
        continue

    with open(batch_path) as f:
        batch_data = json.load(f)
        batch_results = batch_data.get('results', {})
        week5_results.update(batch_results)
        print(f"  {batch_file}: {len(batch_results)} documents")

print(f"Week 5: {len(week5_results)} discourse documents")

# Combine
all_discourse = {**week3_results, **week5_results}
print(f"Total discourse: {len(all_discourse)} documents")

# Check for duplicates
expected = len(week3_results) + len(week5_results)
if len(all_discourse) != expected:
    print(f"⚠️  WARNING: Duplicates detected!")
    print(f"  Expected: {expected}")
    print(f"  Actual: {len(all_discourse)}")
    print(f"  Duplicates: {expected - len(all_discourse)}")

# Save
output_file = Path('discourse_complete_results.json')
with open(output_file, 'w') as f:
    json.dump(all_discourse, f, indent=2)

print(f"\nSaved: {output_file}")
print(f"  Size: {output_file.stat().st_size / 1024 / 1024:.1f} MB")
EOF

python3 week5_results/aggregate_all_discourse.py

# Generate complete discourse report
python3 compare_extractions.py \
  --results week5_results/discourse_complete_results.json \
  --output week5_results/discourse_complete_report.md

# View
cat week5_results/discourse_complete_report.md
```

**Expected**:
- Total: ~980 discourse documents (411 from Week 3 + 569 from Week 5)
- Pass rate: 97-98% (consistent with Week 3's 98.36%)

---

## 📋 Day 3: YouTube + GitLab (45 documents)

### Step 3.1: YouTube Documents (15 docs)

```bash
cd /opt/projects/koi-processor/scripts/reextraction

# Select all YouTube documents
python3 select_pilot_documents.py \
  --source youtube \
  --count 15 \
  --output week5_results/youtube_all.json

# Verify count
cat week5_results/youtube_all.json | python3 -c "import json, sys; d=json.load(sys.stdin); print(f\"YouTube: {d['total_count']} documents\")"

# Extract baseline
python3 extract_baseline_entities.py \
  --input week5_results/youtube_all.json \
  --output week5_results/youtube_all_baseline.json

# Re-extract with pipeline
python3 reextract_pilot.py \
  --input week5_results/youtube_all.json \
  --baseline week5_results/youtube_all_baseline.json \
  --output week5_results/youtube_all_results.json \
  --use-pipeline

# Generate report
python3 compare_extractions.py \
  --results week5_results/youtube_all_results.json \
  --output week5_results/youtube_all_report.md

cat week5_results/youtube_all_report.md
```

### Step 3.2: GitLab Documents (30 docs)

```bash
# Select all GitLab documents
python3 select_pilot_documents.py \
  --source gitlab \
  --count 30 \
  --output week5_results/gitlab_all.json

# Verify count
cat week5_results/gitlab_all.json | python3 -c "import json, sys; d=json.load(sys.stdin); print(f\"GitLab: {d['total_count']} documents\")"

# Extract baseline
python3 extract_baseline_entities.py \
  --input week5_results/gitlab_all.json \
  --output week5_results/gitlab_all_baseline.json

# Re-extract with pipeline
python3 reextract_pilot.py \
  --input week5_results/gitlab_all.json \
  --baseline week5_results/gitlab_all_baseline.json \
  --output week5_results/gitlab_all_results.json \
  --use-pipeline

# Generate report
python3 compare_extractions.py \
  --results week5_results/gitlab_all_results.json \
  --output week5_results/gitlab_all_report.md

cat week5_results/gitlab_all_report.md
```

**Expected for YouTube + GitLab**:
- Small sample sizes (15 + 30 = 45 docs)
- Pass rate: 95-99% (based on previous patterns)
- May see some technical patterns blocked (similar to GitHub)

---

## 📋 Day 4: GitHub Activity + Week 5 Validation

### Step 4.1: GitHub Activity Documents (24 docs)

```bash
cd /opt/projects/koi-processor/scripts/reextraction

# Select all GitHub Activity documents
python3 select_pilot_documents.py \
  --source github-activity \
  --count 24 \
  --output week5_results/github_activity_all.json

# Verify count
cat week5_results/github_activity_all.json | python3 -c "import json, sys; d=json.load(sys.stdin); print(f\"GitHub Activity: {d['total_count']} documents\")"

# Extract baseline
python3 extract_baseline_entities.py \
  --input week5_results/github_activity_all.json \
  --output week5_results/github_activity_all_baseline.json

# Re-extract with pipeline
python3 reextract_pilot.py \
  --input week5_results/github_activity_all.json \
  --baseline week5_results/github_activity_all_baseline.json \
  --output week5_results/github_activity_all_results.json \
  --use-pipeline

# Generate report
python3 compare_extractions.py \
  --results week5_results/github_activity_all_results.json \
  --output week5_results/github_activity_all_report.md

cat week5_results/github_activity_all_report.md
```

### Step 4.2: Aggregate All Week 5 Results

**Goal**: Combine all Week 5 sources into single report

```bash
cd /opt/projects/koi-processor/scripts/reextraction

cat > week5_results/aggregate_week5.py << 'EOF'
#!/usr/bin/env python3
"""Aggregate all Week 5 source results."""
import json
from pathlib import Path

sources = [
    'discourse_remaining_batch_1_results.json',
    'discourse_remaining_batch_2_results.json',
    'discourse_remaining_batch_3_results.json',
    'youtube_all_results.json',
    'gitlab_all_results.json',
    'github_activity_all_results.json'
]

all_results = {}
for source_file in sources:
    source_path = Path(source_file)
    if not source_path.exists():
        print(f"⚠️  {source_file} not found, skipping")
        continue

    try:
        with open(source_path) as f:
            data = json.load(f)
            results = data.get('results', {})
            all_results.update(results)
            print(f"✅ {source_file}: {len(results)} documents")
    except Exception as e:
        print(f"❌ Error loading {source_file}: {e}")

print(f"\n📊 Total Week 5: {len(all_results)} documents")

# Save
output_file = Path('week5_all_results.json')
with open(output_file, 'w') as f:
    json.dump(all_results, f, indent=2)

print(f"✅ Saved: {output_file}")
print(f"   Size: {output_file.stat().st_size / 1024 / 1024:.1f} MB")
EOF

python3 week5_results/aggregate_week5.py

# Generate Week 5 comprehensive report
python3 compare_extractions.py \
  --results week5_results/week5_all_results.json \
  --output week5_results/week5_all_report.md

less week5_results/week5_all_report.md
```

### Step 4.3: Compare Weeks 3-4 vs Week 5

**Goal**: Validate Week 5 results are consistent with previous weeks

```bash
cat > week5_results/compare_weeks.py << 'EOF'
#!/usr/bin/env python3
"""Compare Weeks 3-4 vs Week 5 results."""
import json

# Load Weeks 3-4 combined
with open('../weeks_3_4_combined_results.json') as f:
    weeks_3_4 = json.load(f)

# Load Week 5
with open('week5_results/week5_all_results.json') as f:
    week5 = json.load(f)

def analyze(results, name):
    baseline_count = 0
    passed_count = 0
    blocked_count = 0

    for doc_data in results.values():
        baseline_count += len(doc_data.get('baseline_entities', []))
        passed_count += len(doc_data.get('pipeline_results', {}).get('valid', []))
        blocked_count += len(doc_data.get('pipeline_results', {}).get('blocked', []))

    pass_rate = (passed_count / baseline_count * 100) if baseline_count > 0 else 0
    block_rate = (blocked_count / baseline_count * 100) if baseline_count > 0 else 0

    return {
        'name': name,
        'docs': len(results),
        'baseline': baseline_count,
        'passed': passed_count,
        'blocked': blocked_count,
        'pass_rate': pass_rate,
        'block_rate': block_rate
    }

weeks_3_4_metrics = analyze(weeks_3_4, 'Weeks 3-4')
week5_metrics = analyze(week5, 'Week 5')

print("="*70)
print("WEEKS 3-4 vs WEEK 5 COMPARISON")
print("="*70)
print()
print(f"{'Metric':<20} {'Weeks 3-4':<15} {'Week 5':<15} {'Diff':<10}")
print("-"*70)
print(f"{'Documents':<20} {weeks_3_4_metrics['docs']:<15} {week5_metrics['docs']:<15} {week5_metrics['docs'] - weeks_3_4_metrics['docs']:<10}")
print(f"{'Baseline Entities':<20} {weeks_3_4_metrics['baseline']:<15,} {week5_metrics['baseline']:<15,} {week5_metrics['baseline'] - weeks_3_4_metrics['baseline']:<10,}")
print(f"{'Passed':<20} {weeks_3_4_metrics['passed']:<15,} {week5_metrics['passed']:<15,} {week5_metrics['passed'] - weeks_3_4_metrics['passed']:<10,}")
print(f"{'Blocked':<20} {weeks_3_4_metrics['blocked']:<15} {week5_metrics['blocked']:<15} {week5_metrics['blocked'] - weeks_3_4_metrics['blocked']:<10}")
print()
print(f"{'Pass Rate':<20} {weeks_3_4_metrics['pass_rate']:<14.2f}% {week5_metrics['pass_rate']:<14.2f}% {week5_metrics['pass_rate'] - weeks_3_4_metrics['pass_rate']:<9.2f}%")
print(f"{'Block Rate':<20} {weeks_3_4_metrics['block_rate']:<14.2f}% {week5_metrics['block_rate']:<14.2f}% {week5_metrics['block_rate'] - weeks_3_4_metrics['block_rate']:<9.2f}%")
print()

# Validation
pass_diff = abs(week5_metrics['pass_rate'] - weeks_3_4_metrics['pass_rate'])
print("VALIDATION:")
if pass_diff < 2.0:
    print(f"✅ Pass rates consistent (diff: {pass_diff:.2f}%)")
else:
    print(f"⚠️  Pass rates differ (diff: {pass_diff:.2f}%)")

if week5_metrics['pass_rate'] >= 95.0:
    print(f"✅ Week 5 pass rate meets threshold: {week5_metrics['pass_rate']:.2f}%")
else:
    print(f"❌ Week 5 pass rate below threshold: {week5_metrics['pass_rate']:.2f}%")

# Combined totals
total_docs = weeks_3_4_metrics['docs'] + week5_metrics['docs']
total_baseline = weeks_3_4_metrics['baseline'] + week5_metrics['baseline']
total_passed = weeks_3_4_metrics['passed'] + week5_metrics['passed']
total_blocked = weeks_3_4_metrics['blocked'] + week5_metrics['blocked']
combined_pass_rate = (total_passed / total_baseline * 100) if total_baseline > 0 else 0

print()
print("="*70)
print("COMBINED (WEEKS 3-5)")
print("="*70)
print(f"Documents:        {total_docs:,}")
print(f"Baseline Entities: {total_baseline:,}")
print(f"Passed:           {total_passed:,} ({combined_pass_rate:.2f}%)")
print(f"Blocked:          {total_blocked:,}")
print()
print(f"Progress: {total_docs} / 2,081 documents ({total_docs/2081*100:.1f}% of extractable corpus)")
EOF

python3 week5_results/compare_weeks.py
```

### Step 4.4: Create Week 5 Completion Report

```bash
cat > week5_results/WEEK5_COMPLETE.md << 'EOF'
# Week 5 Complete - Remaining Sources Re-extraction

**Date**: 2025-12-09
**Scope**: 638 documents (Discourse, YouTube, GitLab, GitHub Activity)
**Purpose**: Complete all text-based sources
**Execution**: Production server (202.61.196.119)

---

## Executive Summary

**Status**: [SUCCESS/ISSUES]

**Documents Processed**: [ACTUAL_COUNT] / 638
- Remaining Discourse: [COUNT] / 569
- YouTube: [COUNT] / 15
- GitLab: [COUNT] / 30
- GitHub Activity: [COUNT] / 24

**Entities**:
- Baseline: [COUNT]
- Passed: [COUNT]
- Blocked: [COUNT]

**Metrics**:
- Pass Rate: [RATE]%
- Block Rate: [RATE]%
- False Positive Rate: [RATE]%

**Recommendation**: [GO/NO-GO] for Week 6 (GitHub markdown)

---

## Processing Summary by Source

### Remaining Discourse (569 documents)

| Batch | Docs | Baseline | Passed | Blocked | Pass Rate |
|-------|------|----------|--------|---------|-----------|
| 1 | [#] | [#] | [#] | [#] | [%] |
| 2 | [#] | [#] | [#] | [#] | [%] |
| 3 | [#] | [#] | [#] | [#] | [%] |
| **TOTAL** | [#] | [#] | [#] | [#] | [%] |

### YouTube (15 documents)

| Metric | Count |
|--------|-------|
| Documents | 15 |
| Baseline | [#] |
| Passed | [#] |
| Blocked | [#] |
| Pass Rate | [%] |

### GitLab (30 documents)

| Metric | Count |
|--------|-------|
| Documents | 30 |
| Baseline | [#] |
| Passed | [#] |
| Blocked | [#] |
| Pass Rate | [%] |

### GitHub Activity (24 documents)

| Metric | Count |
|--------|-------|
| Documents | 24 |
| Baseline | [#] |
| Passed | [#] |
| Blocked | [#] |
| Pass Rate | [%] |

---

## Weeks 3-4 vs Week 5 Comparison

| Metric | Weeks 3-4 | Week 5 | Consistent? |
|--------|-----------|--------|-------------|
| Pass Rate | 97.63% | [%] | [YES/NO] |
| Block Rate | 2.86% | [%] | [YES/NO] |
| FP Rate | ~0% | [%] | [YES/NO] |

---

## Overall Progress

| Phase | Documents | Baseline | Passed | Blocked | Pass Rate |
|-------|-----------|----------|--------|---------|-----------|
| Weeks 3-4 | 1,015 | 15,046 | 14,690 | 431 | 97.63% |
| Week 5 | 638 | [#] | [#] | [#] | [%] |
| **TOTAL** | **1,653** | [#] | [#] | [#] | [%] |

**Coverage**: 79.4% (1,653 / 2,081 extractable documents)

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

## GO/NO-GO Decision

### [GO/NO-GO] - [Proceed to Week 6 / Investigate Issues]

**Criteria Met**:
- [ ] Pass rate > 95% (actual: [%])
- [ ] Block rate 2-5% (actual: [%])
- [ ] FP rate < 5% (actual: [%])
- [ ] Results consistent with Weeks 3-4 (97.63%)
- [ ] No critical issues found
- [ ] All sources validated

**Confidence**: [HIGH/MEDIUM/LOW]

**Rationale**:
[Explain the decision based on results]

---

## Next Steps

### If GO:
1. Create PROMPT_16 for Week 6 (GitHub markdown setup + extraction)
2. Configure entity extraction for GitHub source
3. Process ~428 GitHub markdown files
4. Complete to 100% coverage

### If NO-GO:
1. Investigate discrepancies
2. Analyze false positives
3. Tune filters if needed
4. Re-run affected sources

---

## Deliverables

✅ **Created**:
1. `discourse_remaining_batch_[1-3]_results.json` - Remaining discourse results
2. `youtube_all_results.json` - YouTube results
3. `gitlab_all_results.json` - GitLab results
4. `github_activity_all_results.json` - GitHub Activity results
5. `week5_all_results.json` - All Week 5 aggregated
6. `discourse_complete_results.json` - Complete discourse (Weeks 3+5)
7. `WEEK5_COMPLETE.md` - This report

✅ **Validated**:
- All 638 documents processed
- Metrics consistent across sources
- No systematic issues
- Ready for Week 6 (GitHub markdown)

---

## Final Recommendation

**Decision**: [GO/NO-GO]

**Confidence**: [HIGH/MEDIUM/LOW]

**Risk Assessment**: [LOW/MEDIUM/HIGH]

**Signed Off**: Claude Code (Opus 4.5)

**Date**: 2025-12-09

---

*Week 5 - Remaining Sources Re-extraction Complete*
*Ready for PROMPT_16: Week 6 - GitHub Markdown Setup & Extraction*
EOF

# Fill in the template with actual results
nano week5_results/WEEK5_COMPLETE.md
```

**Manual Step**: Fill in all `[PLACEHOLDERS]` with actual metrics from your results

---

## ✅ Completion Checklist

### Day 1: Discourse Batch 1
- [ ] 200 remaining discourse documents selected
- [ ] Week 3 documents excluded correctly
- [ ] Baseline extracted
- [ ] Pipeline re-extraction complete
- [ ] Comparison report generated
- [ ] Pass rate ≥ 95%

### Day 2: Discourse Batches 2-3
- [ ] Batch 2 (200 docs) processed and validated
- [ ] Batch 3 (~169 docs) processed and validated
- [ ] All discourse aggregated (Week 3 + Week 5)
- [ ] Complete discourse report generated
- [ ] ~980 total discourse documents complete

### Day 3: YouTube + GitLab
- [ ] 15 YouTube documents processed
- [ ] 30 GitLab documents processed
- [ ] Both sources validated
- [ ] Reports generated

### Day 4: GitHub Activity + Validation
- [ ] 24 GitHub Activity documents processed
- [ ] All Week 5 results aggregated (638 docs)
- [ ] Comprehensive Week 5 report generated
- [ ] Weeks 3-4 vs Week 5 comparison complete
- [ ] GO/NO-GO decision made
- [ ] WEEK5_COMPLETE.md finalized

---

## 📊 Success Criteria

**Week 5 Complete When**:
- ✅ All 638 documents processed
- ✅ Pass rate ≥ 95% across all sources
- ✅ Results consistent with Weeks 3-4 (97.63%)
- ✅ False positive rate < 5%
- ✅ All source reports generated
- ✅ Final decision documented

**Expected Progress After Week 5**: 79.4% (1,653 / 2,081 documents)

**If GO**: Ready for PROMPT_16 (Week 6 - GitHub markdown setup + extraction)

**If NO-GO**: Investigate issues, tune filters, re-run affected sources

---

## 🆘 Common Issues

### Issue 1: Duplicate Documents Between Week 3 and Week 5

**Symptom**: Week 5 selects documents already processed in Week 3

**Solution**:
```bash
# Verify exclusion working
python3 << 'EOF'
import json

with open('week3_results/discourse_all_documents.json') as f:
    week3_docs = json.load(f)['documents']
    week3_rids = set(d['document_rid'] for d in week3_docs)

with open('week5_results/discourse_remaining_batch_1.json') as f:
    week5_docs = json.load(f)['documents']
    week5_rids = set(d['document_rid'] for d in week5_docs)

overlap = week3_rids & week5_rids
print(f"Overlap: {len(overlap)} documents")
if overlap:
    print("Duplicates found!")
    for rid in list(overlap)[:5]:
        print(f"  - {rid}")
else:
    print("✅ No duplicates")
EOF
```

### Issue 2: Pass Rate Drops Significantly

**Example**: Week 5 pass rate is 90% (vs Weeks 3-4's 97.63%)

**Solution**:
1. Check blocked entities in report
2. Look for false positives (valid entities blocked)
3. If found, add to whitelist and re-run
4. If no FPs, blocks may be legitimate (different content quality)

### Issue 3: Source Has Fewer Documents Than Expected

**Example**: YouTube shows only 10 docs instead of 15

**Solution**:
```bash
# Check actual count in database
PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza -c "
  SELECT COUNT(DISTINCT SPLIT_PART(rid, '#', 1))
  FROM koi_memories
  WHERE source_sensor LIKE 'youtube-sensor%';
"

# Adjust expectations accordingly
```

---

## 📚 References

- **Week 3 Results**: `/opt/projects/koi-processor/scripts/reextraction/week3_results/`
- **Week 4 Results**: `/opt/projects/koi-processor/scripts/reextraction/week4_results/`
- **Weeks 3-4 Analysis**: `/opt/projects/koi-processor/scripts/reextraction/weeks_3_4_analysis_report.md`
- **Pipeline Configuration**: `/opt/projects/koi-processor/src/knowledge_graph/config/pipeline_config.json`

---

## 🎯 Deliverables

By end of Week 5:

1. **week5_all_results.json** - All 638 documents aggregated
2. **Individual source reports** - Discourse, YouTube, GitLab, GitHub Activity
3. **discourse_complete_results.json** - Complete discourse (all ~980 docs)
4. **week5_all_report.md** - Comprehensive comparison report
5. **Weeks 3-4 vs Week 5 comparison** - Consistency validation
6. **WEEK5_COMPLETE.md** - Final assessment with GO/NO-GO

---

**Next Prompt**:
- If **GO**: `PROMPT_16_WEEK6_GITHUB_MARKDOWN.md` (428 markdown files + setup)
- If **NO-GO**: Investigate, fix, re-run affected sources

---

**Last Updated**: 2025-12-09
**Version**: Week 5 Re-extraction Task
**Agent**: Claude Code (Opus 4.5)
**Duration**: 3-4 days
**Status**: 📋 Ready for handoff
