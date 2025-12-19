# PROMPT 11: Week 2 Full Pilot - 100 Documents

**Date**: 2025-12-09
**Phase**: Week 2 - Full Pilot Re-extraction
**Duration**: 1 day
**Agent**: Claude Code (Opus 4.5)

---

## 🎯 Context

**Validation Complete**: 43-document test exceeded all targets

**Validation Results**:
- ✅ **Pass rate**: 98.0% (544/555 entities)
- ✅ **False positive rate**: 0% (0/12 blocked entities)
- ✅ **Block rate**: 2.2% (healthy, not over-filtering)
- ✅ **Tests**: 290 passing (169 filter + 121 pipeline)
- ✅ **Whitelist**: 287+ entries (comprehensive)

**Ready for**: Full pilot with 100 documents (final validation before 3,497-doc re-extraction)

---

## 🎯 Your Mission

Execute the full Week 2 pilot: re-extract 100 documents with the validated pipeline and make the final GO/NO-GO decision for full re-extraction.

**Tasks**:
1. **Select 100 Documents** (30 min)
   - Stratified sampling: 50 high, 30 medium, 20 low quality
   - From Discourse sources (easier to validate)
   - Output: `pilot_100_documents.json`

2. **Extract Baseline** (1 hour)
   - Extract current entities from knowledge graph
   - ~1,000-1,500 entities expected
   - Output: `baseline_100_entities.json`

3. **Re-extract with Pipeline** (1-2 hours)
   - Process all entities through 5-module pipeline
   - Track blocked entities and transformations
   - Output: `pilot_100_results.json`

4. **Generate Comparison Report** (30 min)
   - Comprehensive analysis of results
   - Compare to validation test (43 docs)
   - Output: `comparison_100_report.md`

5. **Analyze & Validate** (1-2 hours)
   - Review blocked entities (spot check)
   - Validate quality improvement
   - Check for any issues at scale

6. **Make GO/NO-GO Decision** (30 min)
   - Document findings
   - Recommend proceeding to full re-extraction or not
   - Output: `WEEK2_PILOT_COMPLETE.md`

---

## 📋 Task 1: Select 100 Documents (30 min)

### Step 1.1: Run Selection Script

```bash
cd koi-processor/scripts/reextraction

# Select 100 documents with stratified sampling
python select_pilot_documents.py \
  --count 100 \
  --output pilot_100_documents.json

# Verify selection
echo "Total documents:"
cat pilot_100_documents.json | jq '. | length'

# Check quality tier distribution
echo -e "\nQuality tier distribution:"
cat pilot_100_documents.json | jq 'group_by(.quality_tier) | map({tier: .[0].quality_tier, count: length})'

# Expected output:
# [
#   {"tier": "high", "count": 50},
#   {"tier": "medium", "count": 30},
#   {"tier": "low", "count": 20}
# ]
```

### Step 1.2: Verify Document Selection

```bash
# Check source distribution
echo -e "\nSource distribution:"
cat pilot_100_documents.json | jq 'group_by(.source_type) | map({source: .[0].source_type, count: length})'

# Check average confidence by tier
echo -e "\nAverage confidence by tier:"
cat pilot_100_documents.json | jq 'group_by(.quality_tier) | map({tier: .[0].quality_tier, avg_conf: (map(.avg_confidence) | add / length)})'
```

**Expected**:
- 100 documents total
- 50 high quality (avg confidence > 0.85)
- 30 medium quality (avg confidence 0.70-0.85)
- 20 low quality (avg confidence < 0.70)
- Primarily from Discourse (discourse-forum, discourse-sensor)

**Success Criteria**:
- [ ] 100 documents selected
- [ ] Stratification correct (50/30/20)
- [ ] All documents have entity extractions

---

## 📋 Task 2: Extract Baseline (1 hour)

### Step 2.1: Run Baseline Extraction

```bash
# Extract baseline entities from knowledge graph
python extract_baseline_entities.py \
  --input pilot_100_documents.json \
  --output baseline_100_entities.json

# Monitor progress (should process quickly)
```

### Step 2.2: Verify Baseline Extraction

```bash
# Check total entity count
echo "Total baseline entities:"
cat baseline_100_entities.json | jq 'reduce .[] as $doc (0; . + $doc.entity_count)'

# Expected: ~1,000-1,500 entities (avg 10-15 per document)

# Check entity type distribution
echo -e "\nEntity types:"
cat baseline_100_entities.json | jq -r '
  [.[] | .entities[] | .type] |
  group_by(.) |
  map({type: .[0], count: length}) |
  sort_by(-.count)
'

# Check confidence distribution
echo -e "\nConfidence statistics:"
cat baseline_100_entities.json | jq '
  [.[] | .entities[] | .confidence] |
  {
    count: length,
    min: min,
    max: max,
    avg: (add / length)
  }
'
```

**Expected Output**:
```
Total baseline entities: 1200 (example)

Entity types:
{"type": "ORGANIZATION", "count": 450}
{"type": "PROJECT", "count": 380}
{"type": "PERSON", "count": 250}
{"type": "CONCEPT", "count": 120}

Confidence statistics:
{
  "count": 1200,
  "min": 0.75,
  "max": 0.95,
  "avg": 0.865
}
```

**Success Criteria**:
- [ ] Baseline extraction complete
- [ ] 1,000-1,500 entities extracted
- [ ] Entities distributed across 100 documents
- [ ] Confidence scores reasonable (avg ~0.86)

---

## 📋 Task 3: Re-extract with Pipeline (1-2 hours)

### Step 3.1: Run Pipeline Re-extraction

```bash
# Re-extract with pipeline (all 5 modules)
python reextract_pilot.py \
  --input pilot_100_documents.json \
  --baseline baseline_100_entities.json \
  --output pilot_100_results.json

# This will process ~1,000-1,500 entities through the pipeline
# Expected time: 1-2 hours depending on entity count
```

**Monitor Progress**:
The script should show progress updates:
```
Processing document 1/100: [title]
  Baseline: 15 entities
  Pipeline: 14 valid, 1 blocked
Processing document 2/100: [title]
  Baseline: 12 entities
  Pipeline: 12 valid, 0 blocked
...
```

### Step 3.2: Verify Pipeline Execution

```bash
# Check that results file was created
ls -lh pilot_100_results.json

# Quick stats
echo "Documents processed:"
cat pilot_100_results.json | jq '. | length'

echo -e "\nTotal entities:"
cat pilot_100_results.json | jq '
  reduce .[] as $doc (
    {baseline: 0, passed: 0, blocked: 0};
    {
      baseline: (.baseline + $doc.baseline_count),
      passed: (.passed + ($doc.pipeline_results.valid | length)),
      blocked: (.blocked + ($doc.pipeline_results.blocked | length))
    }
  )
'
```

**Expected Output**:
```
Documents processed: 100

Total entities:
{
  "baseline": 1200,
  "passed": 1176,
  "blocked": 24
}
```

**Success Criteria**:
- [ ] All 100 documents processed
- [ ] No errors during pipeline execution
- [ ] Results file created successfully
- [ ] Pass rate > 95%

---

## 📋 Task 4: Generate Comparison Report (30 min)

### Step 4.1: Run Comparison Script

```bash
# Generate comprehensive comparison report
python compare_extractions.py \
  --baseline baseline_100_entities.json \
  --results pilot_100_results.json \
  --output comparison_100_report.md

# Review report
cat comparison_100_report.md
```

### Step 4.2: Key Metrics to Check

The report should show:

**1. Overall Statistics**
- Documents processed: 100
- Total baseline entities: ~1,000-1,500
- Entities after pipeline: ~1,100-1,470
- Pass rate: ~97-98%
- Block rate: ~2-3%

**2. Block Analysis**
- By module (should be mostly EntityQualityFilter)
- By pattern (generic_noun, technical_pattern, etc.)
- By reason (specific entity names)

**3. Transformations**
- Canonical resolutions: ~100-200
- Type normalizations: ~1,000+
- List splits: ~10-50

**4. Quality Tier Analysis**
- High quality: ~99% pass rate
- Medium quality: ~97% pass rate
- Low quality: ~95% pass rate

### Step 4.3: Compare to Validation Test

Create comparison table:

| Metric | 43 Docs (Validation) | 100 Docs (Pilot) | Change |
|--------|----------------------|------------------|--------|
| Pass Rate | 98.0% | [X]% | [±X]% |
| Block Rate | 2.2% | [X]% | [±X]% |
| FP Rate | 0% | [X]% | [±X]% |
| Avg Confidence (passed) | 0.879 | [X] | [±X] |

**Success Criteria**:
- [ ] Comparison report generated
- [ ] Pass rate consistent with validation (97-98%)
- [ ] Block rate consistent (2-3%)
- [ ] No major regressions

---

## 📋 Task 5: Analyze & Validate (1-2 hours)

### Step 5.1: Review Blocked Entities (Spot Check)

You don't need to review ALL blocked entities (would take too long), but spot check a representative sample.

```bash
# Extract blocked entities
python extract_blocked_entities.py \
  --input pilot_100_results.json \
  --output blocked_100_entities.json

# Count by pattern
cat blocked_100_entities.json | jq 'group_by(.block_reason | split(":")[0]) | map({pattern: .[0].block_reason | split(":")[0], count: length})'
```

**Spot Check Strategy**:
1. Review top 3 patterns (most common)
2. Sample 5 entities from each pattern
3. Verify they're correctly blocked
4. Document any false positives

**Example**:
```
Top patterns:
1. generic_noun: 12 entities
   Sample: "researchers", "community", "users", "scientists", "people"
   Review: ✅ All correctly blocked (generic, not specific)

2. technical_pattern: 8 entities
   Sample: "app.regen.data", "x/ecocredit", "LunarPunkLabs.org"
   Review: ✅ All correctly blocked (module paths, domains)

3. sentence_like: 4 entities
   Sample: "carbon sequestration project phase 2"
   Review: ✅ Correctly blocked (description, not entity name)
```

### Step 5.2: Validate Transformations

Check that transformations are working correctly:

```bash
# Check canonical resolutions
cat pilot_100_results.json | jq -r '
  [.[] | .pipeline_results.valid[] |
   select(.metadata.canonicalized == true) |
   {original: .metadata.original_name, resolved: .name}
  ] | .[0:10]
'

# Expected: Original names resolved to canonical forms
# Example:
# {"original": "Regen Net", "resolved": "Regen Network"}
# {"original": "Greg Landua", "resolved": "Gregory Landua"}

# Check type normalizations
cat pilot_100_results.json | jq -r '
  [.[] | .pipeline_results.valid[] |
   select(.metadata.original_type != null) |
   {entity: .name, original: .metadata.original_type, normalized: .type}
  ] | .[0:10]
'

# Expected: Types normalized to standard forms
# Example:
# {"entity": "Regen Network", "original": "Organization", "normalized": "ORGANIZATION"}
```

### Step 5.3: Quality Improvement Analysis

Calculate actual quality improvement:

**Before Pipeline** (baseline):
- Total entities: ~1,200
- Low-quality entities: ~24 (2%)

**After Pipeline**:
- Valid entities: ~1,176
- Blocked entities: ~24 (2%)
- False positives: ~0 (< 1%)

**Quality Improvement**:
- Removed 100% of low-quality entities
- False positive rate < 1%
- Net improvement: ~2% cleaner data

**Success Criteria**:
- [ ] Spot check shows valid blocks
- [ ] Transformations working correctly
- [ ] Quality improvement measurable
- [ ] No systematic issues found

---

## 📋 Task 6: Make GO/NO-GO Decision (30 min)

### Step 6.1: Create Completion Report

**File**: `scripts/reextraction/WEEK2_PILOT_COMPLETE.md`

```markdown
# Week 2 Pilot Complete - 100 Document Re-extraction

**Date**: 2025-12-09
**Test Set**: 100 documents
**Purpose**: Final validation before full re-extraction (3,497 docs)

---

## Executive Summary

**Pilot Status**: ✅ SUCCESS / ⚠️ ISSUES / ❌ FAILED

**Key Metrics**:
- Documents: 100
- Baseline entities: [X]
- Valid entities: [X]
- Blocked entities: [X]
- Pass rate: [X]%
- Block rate: [X]%
- False positive rate: [X]%

**Recommendation**: GO / NO-GO for full re-extraction

---

## Test Results

### Overall Metrics

| Metric | Value | Target | Status |
|--------|-------|--------|--------|
| Pass Rate | [X]% | > 95% | ✅/❌ |
| Block Rate | [X]% | 2-5% | ✅/❌ |
| FP Rate | [X]% | < 5% | ✅/❌ |
| Processing Time | [X] min | < 120 min | ✅/❌ |

### Comparison to Validation Test

| Metric | 43 Docs | 100 Docs | Consistent? |
|--------|---------|----------|-------------|
| Pass Rate | 98.0% | [X]% | ✅/❌ |
| Block Rate | 2.2% | [X]% | ✅/❌ |
| FP Rate | 0% | [X]% | ✅/❌ |

### Quality Tier Performance

| Tier | Documents | Baseline | Passed | Blocked | Pass Rate |
|------|-----------|----------|--------|---------|-----------|
| High | 50 | [X] | [X] | [X] | [X]% |
| Medium | 30 | [X] | [X] | [X] | [X]% |
| Low | 20 | [X] | [X] | [X] | [X]% |

---

## Block Analysis

### By Pattern

| Pattern | Count | % of Blocks | Sample Entities |
|---------|-------|-------------|-----------------|
| generic_noun | [X] | [X]% | researchers, scientists, users |
| technical_pattern | [X] | [X]% | app.regen.*, x/ecocredit |
| sentence_like | [X] | [X]% | [examples] |
| ... | | | |

### Spot Check Results

**Sample Size**: [X] entities reviewed (from [X] total blocked)

**Classification**:
- True Positives: [X] ([X]%)
- False Positives: [X] ([X]%)
- Unclear: [X] ([X]%)

**False Positives Found** (if any):
1. [Entity name] - [Why it should not be blocked]
2. ...

---

## Transformations

| Type | Count | Examples |
|------|-------|----------|
| Canonical Resolutions | [X] | Regen Net → Regen Network |
| Type Normalizations | [X] | Organization → ORGANIZATION |
| List Splits | [X] | "A and B" → 2 entities |

---

## Issues Found

### Critical Issues (blocking GO decision)
- [ ] None / [List critical issues]

### Minor Issues (can fix later)
- [ ] None / [List minor issues]

### False Positives (if any)
- [ ] None / [List false positives]

---

## GO/NO-GO Decision

### Option A: GO - Proceed to Full Re-extraction ✅

**Criteria Met**:
- [ ] Pass rate > 95%
- [ ] Block rate 2-5%
- [ ] FP rate < 5%
- [ ] Results consistent with validation test
- [ ] No critical issues found
- [ ] All 290 tests still passing

**Recommendation**: Proceed to Weeks 3-6 (full re-extraction of 3,497 documents)

**Confidence**: HIGH / MEDIUM / LOW

**Expected Timeline**:
- Week 3: Discourse (1,407 documents)
- Week 4: Remaining sources (2,090 documents)
- Week 5: Validation & analysis
- Week 6: Optimization & cleanup

### Option B: NO-GO - More Work Needed ❌

**Issues Preventing GO**:
- [ ] Pass rate < 95%
- [ ] FP rate > 5%
- [ ] Major inconsistencies vs validation
- [ ] Critical issues found

**Required Actions**:
1. [Action needed]
2. [Action needed]
3. Re-run 100-doc pilot after fixes

---

## Risk Assessment

### Risks of Proceeding

1. **False Positives at Scale**
   - Current FP rate: [X]%
   - At 3,497 docs: ~[X] false positives expected
   - Mitigation: [strategy]

2. **Performance Issues**
   - Current processing time: [X] min for 100 docs
   - At 3,497 docs: ~[X] hours expected
   - Mitigation: Batch processing, optimize if needed

3. **Unknown Edge Cases**
   - Sample size: 100 docs (2.9% of total)
   - Potential unknown issues in remaining 97.1%
   - Mitigation: Monitor Week 3 closely, pause if issues

### Confidence Assessment

**Statistical Confidence**: HIGH / MEDIUM / LOW

**Reasoning**:
- Sample size: 100 docs, ~1,200 entities
- Results consistent with 43-doc validation
- 0 false positives in validation, [X] in pilot
- All edge cases from validation still handled correctly

---

## Next Steps

### If GO

**Immediate** (Week 3, Day 1):
1. Hand off to PROMPT_12 (Week 3: Discourse Re-extraction)
2. Prepare for processing 1,407 documents
3. Set up monitoring for large-scale run

**Week 3 Plan**:
- Day 1-2: Forum posts (913 documents)
- Day 3-4: Discourse sensor (494 documents)
- Day 5: Verify & report

### If NO-GO

**Immediate**:
1. Document all issues found
2. Prioritize fixes
3. Apply fixes to pipeline/filters
4. Re-run 100-doc pilot
5. Validate fixes work

---

## Deliverables

✅ **Created**:
1. pilot_100_documents.json - Selected documents
2. baseline_100_entities.json - Baseline extractions
3. pilot_100_results.json - Pipeline results
4. comparison_100_report.md - Comparison analysis
5. blocked_100_entities.json - Blocked entity list
6. WEEK2_PILOT_COMPLETE.md - This report

✅ **Validated**:
- All 290 tests passing
- Pipeline operational at scale
- No regressions found

---

## Final Recommendation

**Decision**: GO / NO-GO

**Recommendation**: [Clear statement with rationale]

**Confidence Level**: HIGH / MEDIUM / LOW

**Signed Off By**: [Agent/Team]

**Date**: 2025-12-09

---

*Week 2 Pilot - Full 100-document validation complete*
```

### Step 6.2: Make Final Decision

Based on all evidence:

**GO if**:
- ✅ Pass rate > 95%
- ✅ Block rate 2-5%
- ✅ FP rate < 5%
- ✅ Results consistent with validation
- ✅ No critical issues
- ✅ Spot check validates blocks are correct

**NO-GO if**:
- ❌ Pass rate < 95%
- ❌ FP rate > 5%
- ❌ Major inconsistencies
- ❌ Critical issues found
- ❌ Systematic blocking problems

**Document reasoning clearly** - this decision determines if we proceed to re-extract 3,497 documents (Weeks 3-6).

---

## ✅ Completion Checklist

### Task 1: Select Documents
- [ ] 100 documents selected
- [ ] Stratification correct (50/30/20)
- [ ] From Discourse sources
- [ ] pilot_100_documents.json created

### Task 2: Extract Baseline
- [ ] Baseline extraction complete
- [ ] 1,000-1,500 entities extracted
- [ ] baseline_100_entities.json created
- [ ] Stats look reasonable

### Task 3: Re-extract with Pipeline
- [ ] All 100 documents processed
- [ ] No errors during execution
- [ ] pilot_100_results.json created
- [ ] Pass rate > 95%

### Task 4: Comparison Report
- [ ] comparison_100_report.md generated
- [ ] Metrics consistent with validation
- [ ] No major regressions

### Task 5: Analyze & Validate
- [ ] Spot check completed (sample of blocks)
- [ ] Transformations validated
- [ ] Quality improvement measured
- [ ] No systematic issues found

### Task 6: GO/NO-GO Decision
- [ ] WEEK2_PILOT_COMPLETE.md created
- [ ] Clear GO/NO-GO recommendation
- [ ] Confidence level documented
- [ ] Next steps outlined

---

## 📊 Success Criteria

**Week 2 Pilot Successful When**:
- ✅ All 100 documents processed
- ✅ Pass rate > 95%
- ✅ FP rate < 5%
- ✅ Results consistent with 43-doc validation
- ✅ No critical issues found
- ✅ Clear GO/NO-GO decision made
- ✅ All 290 tests still passing

**If GO**: Ready for PROMPT_12 (Week 3: Discourse Re-extraction - 1,407 documents)

**If NO-GO**: Document issues, fix, re-test

---

## 🆘 Common Issues

### Issue 1: Processing Taking Too Long

**If > 2 hours**:
- Check system resources
- Batch processing may help
- Consider parallelization

**Not a blocker** - just slower than expected

### Issue 2: Pass Rate Lower Than Expected

**If < 95%**:
- Review blocked entities
- Check for new patterns not seen in validation
- May need filter tuning

**Decision**: If 90-95%, consider GO with monitoring. If < 90%, NO-GO and investigate.

### Issue 3: Higher FP Rate

**If > 5%**:
- Review false positives
- Identify patterns
- May need whitelist additions
- NO-GO until fixed

### Issue 4: Memory Issues

**If script crashes**:
- Process in batches (25 docs at a time)
- Increase available memory
- Optimize data structures

---

## 📚 References

- **Validation Results**: `scripts/reextraction/VALIDATION_REPORT_50_DOCS.md`
- **Blocked Entity Analysis**: `scripts/reextraction/BLOCKED_ENTITIES_ANALYSIS.md`
- **Scripts**: `scripts/reextraction/` directory
- **Pipeline Config**: `src/knowledge_graph/config/pipeline_config.json`
- **Tests**: 290 passing tests in `tests/`

---

## 🎯 Deliverables

By end of Week 2 pilot:

1. **pilot_100_documents.json** - 100 selected documents
2. **baseline_100_entities.json** - Baseline extractions
3. **pilot_100_results.json** - Pipeline results
4. **comparison_100_report.md** - Comprehensive analysis
5. **blocked_100_entities.json** - Blocked entity list
6. **WEEK2_PILOT_COMPLETE.md** - Final report with GO/NO-GO decision
7. **All tests passing** - 290 tests, no regressions

---

**Next Prompt**:
- If **GO**: `PROMPT_12_WEEK3_DISCOURSE_REEXTRACTION.md` (1,407 documents)
- If **NO-GO**: Document issues, fix, re-run Week 2 pilot

---

**Last Updated**: 2025-12-09
**Version**: Week 2 Full Pilot
**Agent**: Claude Code (Opus 4.5)
**Duration**: 1 day (6-8 hours)
**Status**: 📋 Ready for handoff
