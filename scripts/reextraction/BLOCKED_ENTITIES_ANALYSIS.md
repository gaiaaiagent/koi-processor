# Blocked Entities Analysis

**Date**: 2025-12-09
**Test Set**: 10 documents
**Total Blocked**: 4 entities (3.7%)

---

## Summary

The test run processed 109 entities from 10 documents. Of these:
- **105 entities passed** (96.3%)
- **4 entities blocked** (3.7%)

All blocks were performed by the **EntityQualityFilter** module.

---

## Blocked Entities Detail

### 1. "US" (Organization)

| Field | Value |
|-------|-------|
| **Name** | US |
| **Type** | Organization |
| **Confidence** | 0.85 |
| **Block Reason** | `stop_word` |
| **Valid Block?** | **NO - FALSE POSITIVE** |

**Analysis**: "US" is being blocked because when lowercased, it matches the pronoun "us" (as in "we/us/our"). However, "US" is a valid abbreviation for "United States" and should be allowed.

**Recommendation**: Add "US" to the whitelist in FilterConfig to prevent this false positive.

---

### 2. "app.regen.claim" (Project)

| Field | Value |
|-------|-------|
| **Name** | app.regen.claim |
| **Type** | Project |
| **Confidence** | 0.75 |
| **Block Reason** | `sentence_like, technical_pattern` |
| **Valid Block?** | **YES - CORRECT** |

**Analysis**: This is a namespace/schema path (like `app.regen.claim`) used in Regen's data structures. It matches:
- `technical_pattern`: The regex `^[a-z]+\.[a-z]+\.[a-z\-]+` matches package paths
- `sentence_like`: Contains periods which trigger sentence detection

This is NOT a proper entity name - it's a technical identifier that was incorrectly extracted as a Project entity.

---

### 3. "app.regen.evidence" (Project)

| Field | Value |
|-------|-------|
| **Name** | app.regen.evidence |
| **Type** | Project |
| **Confidence** | 0.75 |
| **Block Reason** | `sentence_like, technical_pattern` |
| **Valid Block?** | **YES - CORRECT** |

**Analysis**: Same as above - a namespace/schema path, not a valid entity.

---

### 4. "app.regen.method" (Project)

| Field | Value |
|-------|-------|
| **Name** | app.regen.method |
| **Type** | Project |
| **Confidence** | 0.75 |
| **Block Reason** | `sentence_like, technical_pattern` |
| **Valid Block?** | **YES - CORRECT** |

**Analysis**: Same as above - a namespace/schema path, not a valid entity.

---

## Block Pattern Summary

| Pattern | Count | Percentage | Description |
|---------|-------|------------|-------------|
| technical_pattern | 3 | 42.9% | Package paths (app.regen.*) |
| sentence_like | 3 | 42.9% | Contains periods/punctuation |
| stop_word | 1 | 14.3% | Matches pronoun "us" |

---

## Recommendations

### Option A: Minor Tuning (Recommended)

**Issue**: 1 false positive out of 4 blocks (25% false positive rate among blocks)

**Action**: Add "US" to whitelist to fix the false positive

```python
config = FilterConfig(
    whitelist={'us', 'US'}  # Country abbreviation
)
```

**Impact**:
- False positive rate drops to 0%
- 3 true blocks remain (technical patterns correctly blocked)
- No loss of quality protection

### Option B: No Changes

**Rationale**:
- 3 out of 4 blocks are correct (75%)
- "US" as an entity is ambiguous (could be misextracted pronoun)
- Conservative approach maintains quality

**Risk**: Some valid country references may be blocked

### Option C: Investigate Further

**When to use**:
- If more false positives are found in larger test sets
- If blocking patterns need refinement

---

## Decision

**Recommended**: **Option A - Minor Tuning**

**Rationale**:
1. The false positive ("US") is a clear error that should be fixed
2. The other 3 blocks are correctly identifying technical patterns
3. Adding "US" to the whitelist is a simple, low-risk change
4. The overall block rate (3.7%) is healthy and indicates the filter is working

---

## GO/NO-GO Recommendation

### **GO - Proceed to Week 2 Pilot (100 documents)**

**Justification**:

1. **96.3% pass rate** - Pipeline is correctly allowing valid entities
2. **3.7% block rate** - Filter is catching low-quality entities without over-filtering
3. **Block reasons are clear** - No "Unknown" reasons after fixes
4. **Only 1 false positive** - Easily fixable with whitelist addition
5. **Technical patterns correctly blocked** - `app.regen.*` namespace paths should not be entities

**Before proceeding**:
1. Add "US" to the whitelist configuration
2. Re-run test to verify 0 false positives
3. Document the whitelist addition

**Expected Week 2 results**:
- Similar ~96% pass rate on larger dataset
- Block reasons will be tracked and analyzable
- Any new false positives can be added to whitelist

---

## Test Results Summary

| Metric | Value |
|--------|-------|
| Documents | 10 |
| Total Entities | 109 |
| Passed | 105 (96.3%) |
| Blocked | 4 (3.7%) |
| True Positives (correct blocks) | 3 (75%) |
| False Positives (incorrect blocks) | 1 (25%) |
| Block reasons tracked | YES |
| "Unknown" reasons | 0 |

---

## Files Modified

1. `scripts/reextraction/reextract_pilot.py` - Fixed typo: `block_reason` -> `blocked_reason`
2. `scripts/reextraction/compare_extractions.py` - Added "By Pattern" section to report
3. `src/knowledge_graph/improvements/tests/test_entity_quality_filter.py` - Added tests for `filter_with_reasons`

---

## Next Steps

1. **Immediate**: Add "US" to whitelist
2. **Week 2**: Run 100-document pilot with updated configuration
3. **Week 2**: Review larger sample for additional false positives
4. **Week 3+**: Full re-extraction of 3,497 documents

---

**Last Updated**: 2025-12-09
**Analysis By**: Claude Code (Opus 4.5)
**Status**: GO for Week 2 Pilot
