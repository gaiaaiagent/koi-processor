# PROMPT 10: Fix False Positives & Validate with Larger Test

**Date**: 2025-12-09
**Phase**: Week 1 Extended Validation
**Duration**: Half day (4 hours)
**Agent**: Claude Code (Opus 4.5)

---

## 🎯 Context

**Investigation Complete**: 4 blocked entities analyzed

**Findings**:
- **Overall false positive rate**: 0.9% (1/109 entities) ✅ Good
- **Block false positive rate**: 25% (1/4 blocks) ⚠️ Concerning
- **Issue**: "US" blocked as pronoun (false positive)

**Concern**: If 25% of blocks are false positives, scaling to 100 docs could result in ~10-15 valid entities incorrectly blocked.

**Before Week 2 Pilot, we need to**:
1. Fix the false positive (add country codes to whitelist)
2. Test with larger sample (25-50 documents)
3. Review ALL blocked entities from larger test
4. Validate false positive rate < 5% before scaling

---

## 🎯 Your Mission

Apply the fix and validate with a larger test set to ensure pipeline accuracy before scaling to 100 documents.

**Tasks**:
1. **Add Country Code Whitelist** (30 min)
   - Add comprehensive country code list
   - Add common abbreviations (UN, EU, NASA, etc.)
   - Update EntityQualityFilter
   - Add tests

2. **Run Larger Test** (1 hour)
   - Select 50 documents (stratified sampling)
   - Extract baseline entities
   - Re-extract with pipeline (including fix)
   - Generate comparison report

3. **Review ALL Blocked Entities** (2 hours)
   - Manually review each blocked entity
   - Classify: True Positive vs False Positive
   - Document findings
   - Calculate actual false positive rate

4. **Make GO/NO-GO Decision** (30 min)
   - Analyze results
   - Decide if ready for Week 2 (100 docs)
   - Document recommendation

---

## 📋 Task 1: Add Country Code Whitelist (30 min)

### Step 1.1: Create Comprehensive Whitelist

**File**: `src/knowledge_graph/improvements/entity_quality_filter.py`

Add before the `EntityQualityFilter` class:

```python
# Common abbreviations and entity names that should NOT be blocked
# even though they match pronoun/generic patterns

# ISO 3166-1 alpha-2 country codes (commonly used as entities)
COUNTRY_CODES = {
    # Major countries
    'US', 'UK', 'EU', 'UN', 'CA', 'AU', 'NZ', 'FR', 'DE', 'IT',
    'JP', 'CN', 'IN', 'BR', 'MX', 'ZA', 'KR', 'ES', 'SE', 'NO',
    'DK', 'FI', 'NL', 'BE', 'AT', 'CH', 'IE', 'PT', 'GR', 'PL',
    'CZ', 'HU', 'RO', 'BG', 'HR', 'SK', 'SI', 'EE', 'LV', 'LT',
    'CY', 'MT', 'LU', 'IS', 'LI', 'MC', 'SM', 'VA', 'AD', 'AL',

    # Regions/Organizations
    'EU', 'UN', 'NATO', 'ASEAN', 'OPEC', 'WTO', 'IMF', 'WHO',
    'UNESCO', 'UNICEF', 'UNHCR', 'WFP', 'FAO', 'ILO', 'IAEA',

    # Common abbreviations used as entity names
    'NASA', 'ESA', 'NOAA', 'EPA', 'FDA', 'CDC', 'NIH', 'NSF',
    'DARPA', 'ARPA', 'DOE', 'DOD', 'DHS', 'FBI', 'CIA', 'NSA',

    # Tech/Science
    'MIT', 'IBM', 'HP', 'AMD', 'ARM', 'GPS', 'DNA', 'RNA', 'AI',
    'ML', 'NLP', 'API', 'SDK', 'IDE', 'OS', 'CPU', 'GPU', 'RAM',
}

# Currency codes (commonly mentioned as entities)
CURRENCY_CODES = {
    'USD', 'EUR', 'GBP', 'JPY', 'CNY', 'INR', 'BRL', 'MXN',
    'CAD', 'AUD', 'NZD', 'CHF', 'SEK', 'NOK', 'DKK', 'KRW',
}

# Combine into ENTITY_WHITELIST
ENTITY_WHITELIST = COUNTRY_CODES.union(CURRENCY_CODES)
```

### Step 1.2: Update EntityQualityFilter

**In `__init__` method**:

```python
def __init__(self, config: Optional[FilterConfig] = None):
    """Initialize filter with optional configuration."""
    self.config = config or FilterConfig()

    # Combine built-in whitelist with user config
    self.whitelist = ENTITY_WHITELIST.copy()
    if self.config.whitelist:
        self.whitelist.update(self.config.whitelist)

    # Convert to lowercase for case-insensitive matching
    self.whitelist_lower = {w.lower() for w in self.whitelist}

    # ... rest of init
```

**In `_should_block_with_reason` method** (before pronoun check):

```python
def _should_block_with_reason(self, entity: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Check if entity should be blocked. Returns (should_block, reason)."""
    name = entity.get('name', '').strip()
    name_lower = name.lower()

    # Check whitelist first (case-insensitive)
    if name_lower in self.whitelist_lower:
        return False, None

    # Check pronouns
    if name_lower in self.pronouns:
        return True, f"pronoun: '{name}'"

    # ... rest of checks
```

### Step 1.3: Add Tests

**File**: `tests/test_entity_quality_filter.py`

```python
def test_country_code_whitelist():
    """Test that country codes are not blocked."""
    filter = EntityQualityFilter()

    # Test common country codes
    for code in ['US', 'UK', 'EU', 'UN']:
        should_block, reason = filter._should_block_with_reason({
            'name': code,
            'type': 'ORGANIZATION'
        })
        assert should_block == False, f"{code} should not be blocked"
        assert reason is None

    # Test that lowercase versions of whitelisted items still work
    should_block, reason = filter._should_block_with_reason({
        'name': 'us',  # lowercase
        'type': 'ORGANIZATION'
    })
    assert should_block == False, "Lowercase 'us' should be whitelisted"

def test_abbreviation_whitelist():
    """Test that common abbreviations are not blocked."""
    filter = EntityQualityFilter()

    # Test organizations
    for abbr in ['NASA', 'UNESCO', 'WHO', 'MIT']:
        should_block, reason = filter._should_block_with_reason({
            'name': abbr,
            'type': 'ORGANIZATION'
        })
        assert should_block == False, f"{abbr} should not be blocked"

def test_currency_whitelist():
    """Test that currency codes are not blocked."""
    filter = EntityQualityFilter()

    for currency in ['USD', 'EUR', 'GBP', 'JPY']:
        should_block, reason = filter._should_block_with_reason({
            'name': currency,
            'type': 'CURRENCY'
        })
        assert should_block == False, f"{currency} should not be blocked"

def test_pronoun_still_blocked_if_not_whitelisted():
    """Test that non-whitelisted pronouns are still blocked."""
    filter = EntityQualityFilter()

    # 'we' is a pronoun but NOT in whitelist
    should_block, reason = filter._should_block_with_reason({
        'name': 'we',
        'type': 'PERSON'
    })
    assert should_block == True
    assert 'pronoun' in reason
```

### Step 1.4: Run Tests

```bash
cd koi-processor

# Run new tests
pytest tests/test_entity_quality_filter.py::test_country_code_whitelist -v
pytest tests/test_entity_quality_filter.py::test_abbreviation_whitelist -v
pytest tests/test_entity_quality_filter.py::test_currency_whitelist -v
pytest tests/test_entity_quality_filter.py::test_pronoun_still_blocked_if_not_whitelisted -v

# Run full test suite (should still be 121+ passing)
pytest
```

**Expected**: All new tests pass + existing 121 tests still pass

---

## 📋 Task 2: Run Larger Test (1 hour)

### Step 2.1: Select 50 Documents

```bash
cd scripts/reextraction

# Select 50 documents (stratified: 25 high, 15 medium, 10 low)
python select_pilot_documents.py --count 50 --output pilot_50_documents.json

# Verify selection
cat pilot_50_documents.json | jq '. | length'
# Expected: 50

# Check quality tier distribution
cat pilot_50_documents.json | jq 'group_by(.quality_tier) | map({tier: .[0].quality_tier, count: length})'
# Expected: ~25 high, ~15 medium, ~10 low
```

### Step 2.2: Extract Baseline

```bash
# Extract baseline entities for 50 documents
python extract_baseline_entities.py \
  --input pilot_50_documents.json \
  --output baseline_50_entities.json

# Check entity count
cat baseline_50_entities.json | jq 'reduce .[] as $doc (0; . + $doc.entity_count)'
# Expected: ~500-750 entities (avg ~10-15 per doc)
```

### Step 2.3: Re-extract with Pipeline

```bash
# Re-extract with pipeline (including whitelist fix)
python reextract_pilot.py \
  --input pilot_50_documents.json \
  --baseline baseline_50_entities.json \
  --output pilot_50_results.json

# Monitor progress
# Should complete without errors
```

### Step 2.4: Generate Comparison Report

```bash
# Generate comparison report
python compare_extractions.py \
  --baseline baseline_50_entities.json \
  --results pilot_50_results.json \
  --output comparison_50_report.md

# Review report
cat comparison_50_report.md
```

**Expected Results**:
- Pass rate: ~96-98%
- Block rate: ~2-4%
- Blocked entities: ~10-30
- "US" should now pass (not blocked)

---

## 📋 Task 3: Review ALL Blocked Entities (2 hours)

### Step 3.1: Extract All Blocked Entities

Create a script to extract all blocked entities for manual review.

**File**: `scripts/reextraction/extract_blocked_entities.py`

```python
#!/usr/bin/env python3
"""Extract all blocked entities for manual review."""

import json
import sys
from pathlib import Path

def extract_blocked_entities(results_path: str) -> list:
    """Extract all blocked entities from results."""
    with open(results_path) as f:
        results = json.load(f)

    blocked = []
    for doc_id, doc_data in results.items():
        doc_info = doc_data['document']
        for entity in doc_data['pipeline_results']['blocked']:
            blocked.append({
                'entity_name': entity['name'],
                'entity_type': entity['type'],
                'confidence': entity.get('confidence'),
                'blocked_by': entity.get('blocked_by'),
                'block_reason': entity.get('blocked_reason', 'Unknown'),
                'document_id': doc_id,
                'document_title': doc_info.get('title', 'Unknown'),
                'source_url': doc_info.get('source_url', 'Unknown'),
            })

    return blocked

def main():
    results_path = Path(__file__).parent / 'pilot_50_results.json'
    output_path = Path(__file__).parent / 'blocked_entities_for_review.json'

    blocked = extract_blocked_entities(str(results_path))

    # Sort by block reason for easier review
    blocked.sort(key=lambda x: (x['block_reason'], x['entity_name']))

    # Save to file
    with open(output_path, 'w') as f:
        json.dump(blocked, f, indent=2)

    # Print summary
    print(f"Extracted {len(blocked)} blocked entities")
    print(f"Saved to: {output_path}")

    # Group by reason
    by_reason = {}
    for entity in blocked:
        reason = entity['block_reason'].split(':')[0]
        by_reason[reason] = by_reason.get(reason, 0) + 1

    print("\nBlocked by reason:")
    for reason, count in sorted(by_reason.items(), key=lambda x: -x[1]):
        print(f"  {reason}: {count}")

if __name__ == '__main__':
    main()
```

Run the script:

```bash
python extract_blocked_entities.py
```

### Step 3.2: Create Review Template

Create a spreadsheet or markdown for manual review:

**File**: `scripts/reextraction/BLOCKED_ENTITIES_REVIEW.md`

```markdown
# Blocked Entities Manual Review

**Test Set**: 50 documents
**Total Blocked**: [X] entities
**Reviewer**: [Your name]
**Date**: 2025-12-09

---

## Review Instructions

For each blocked entity below, classify as:
- **TP** (True Positive): Correctly blocked (low-quality, pronoun, generic, etc.)
- **FP** (False Positive): Incorrectly blocked (valid entity)
- **UNCLEAR**: Need more context to decide

---

## Blocked Entities

| # | Entity | Type | Reason | Document | TP/FP/UNCLEAR | Notes |
|---|--------|------|--------|----------|---------------|-------|
| 1 | [name] | [type] | [reason] | [title] | [ ] | |
| 2 | [name] | [type] | [reason] | [title] | [ ] | |
| ... | | | | | | |

---

## Summary

- **Total Blocked**: [X]
- **True Positives**: [X] ([%])
- **False Positives**: [X] ([%])
- **Unclear**: [X] ([%])

### False Positives List

1. [Entity name] - [Why it should not be blocked]
2. ...

### Recommended Actions

- [ ] Add to whitelist: [entities]
- [ ] Remove pattern: [pattern name]
- [ ] Tune threshold: [which threshold]
- [ ] No changes needed

---

## GO/NO-GO Decision

**Decision**: GO / NO-GO

**Rationale**: [Why]

**False Positive Rate**: [X]% (target: < 5%)

**Ready for Week 2 Pilot?**: YES / NO
```

### Step 3.3: Manual Review Process

For EACH blocked entity:

1. **Read entity name and type**
2. **Check block reason** (pronoun, generic_noun, etc.)
3. **Look at source document** (title, URL) for context
4. **Classify**:
   - **TP**: If it's truly low-quality (pronoun, generic, technical path)
   - **FP**: If it's a valid entity that should not be blocked
   - **UNCLEAR**: If you need to see the actual source text

4. **Document findings** in review template

### Step 3.4: Calculate False Positive Rate

After reviewing all blocked entities:

```
False Positive Rate = (False Positives / Total Blocked) * 100%

Target: < 5%
```

**Decision criteria**:
- **< 5% FP rate**: GO to Week 2 (excellent)
- **5-10% FP rate**: Fix major FPs, re-test 50 docs
- **> 10% FP rate**: NO-GO, need filter tuning

---

## 📋 Task 4: Make GO/NO-GO Decision (30 min)

### Step 4.1: Analyze Results

Create final analysis document:

**File**: `scripts/reextraction/VALIDATION_REPORT_50_DOCS.md`

```markdown
# Validation Report - 50 Document Test

**Date**: 2025-12-09
**Test Set**: 50 documents
**Purpose**: Validate pipeline accuracy before Week 2 pilot (100 docs)

---

## Test Results

### Overall Metrics

- **Documents**: 50
- **Total Baseline Entities**: [X]
- **Entities After Pipeline**: [X]
- **Entities Blocked**: [X]
- **Pass Rate**: [X]%
- **Block Rate**: [X]%

### Block Analysis

- **Total Blocked**: [X]
- **True Positives**: [X] ([%])
- **False Positives**: [X] ([%])
- **False Positive Rate**: [X]%

### By Pattern

| Pattern | Blocked | True Pos | False Pos | Accuracy |
|---------|---------|----------|-----------|----------|
| pronoun | [X] | [X] | [X] | [%] |
| generic_noun | [X] | [X] | [X] | [%] |
| technical_pattern | [X] | [X] | [X] | [%] |
| sentence_like | [X] | [X] | [X] | [%] |
| ... | | | | |

---

## Issues Found

### False Positives

1. **[Entity Name]**
   - Type: [type]
   - Blocked reason: [reason]
   - Why FP: [explanation]
   - Fix: Add to whitelist / Remove pattern / etc.

2. ...

### Patterns Needing Tuning

- [ ] [Pattern name]: [Issue and recommendation]
- [ ] ...

---

## Fixes Applied

### During This Task

1. ✅ Added country code whitelist (COUNTRY_CODES)
2. ✅ Added abbreviation whitelist (NASA, UNESCO, etc.)
3. ✅ Added currency codes (USD, EUR, etc.)
4. ✅ Case-insensitive whitelist matching

### Additional Fixes Needed

- [ ] [Fix description]
- [ ] ...

---

## Comparison to Initial Test

| Metric | 10 Docs | 50 Docs | Change |
|--------|---------|---------|--------|
| Pass Rate | 96.3% | [X]% | [±X]% |
| Block Rate | 3.7% | [X]% | [±X]% |
| FP Rate (blocks) | 25% | [X]% | [±X]% |
| FP Rate (overall) | 0.9% | [X]% | [±X]% |

---

## GO/NO-GO Recommendation

### Option A: GO - Proceed to Week 2 ✅

**Criteria Met**:
- [ ] False positive rate < 5%
- [ ] No major blocking issues
- [ ] "US" false positive fixed
- [ ] Test results consistent with 10-doc test
- [ ] All tests passing (121+)

**Next Step**: PROMPT_11 (Week 2 Full Pilot - 100 documents)

### Option B: NO-GO - More Work Needed ❌

**Issues**:
- [ ] False positive rate > 5%
- [ ] Major blocking patterns need tuning
- [ ] Inconsistent results vs 10-doc test

**Next Step**: Fix issues, re-test 50 docs

---

## Recommendation

**Decision**: GO / NO-GO

**Rationale**: [Detailed explanation]

**Confidence**: HIGH / MEDIUM / LOW

**Risk Assessment**:
- Risk of proceeding: [assessment]
- Mitigation: [if any]

---

*Report generated after manual review of all blocked entities*
```

### Step 4.2: Document Decision

Based on the analysis, make a clear GO/NO-GO recommendation:

**GO if**:
- ✅ False positive rate < 5%
- ✅ Whitelist fix resolved "US" issue
- ✅ No new major blocking issues found
- ✅ Results consistent with 10-doc test
- ✅ All 121+ tests still passing

**NO-GO if**:
- ❌ False positive rate > 5%
- ❌ Found systemic blocking issues
- ❌ Multiple valid entities being blocked
- ❌ Needs significant filter tuning

---

## ✅ Completion Checklist

### Task 1: Add Whitelist
- [ ] Created COUNTRY_CODES, CURRENCY_CODES sets
- [ ] Updated EntityQualityFilter with whitelist
- [ ] Added 4+ new tests for whitelist
- [ ] All tests passing (125+)

### Task 2: Larger Test
- [ ] Selected 50 documents (stratified)
- [ ] Extracted baseline (~500-750 entities)
- [ ] Re-extracted with pipeline
- [ ] Generated comparison report
- [ ] "US" now passes (not blocked)

### Task 3: Review Blocked Entities
- [ ] Extracted all blocked entities to JSON
- [ ] Created review template
- [ ] Manually reviewed EVERY blocked entity
- [ ] Classified as TP/FP/UNCLEAR
- [ ] Calculated false positive rate
- [ ] Documented false positives

### Task 4: Decision
- [ ] Created VALIDATION_REPORT_50_DOCS.md
- [ ] Analyzed results vs 10-doc test
- [ ] Documented all issues found
- [ ] Made clear GO/NO-GO recommendation
- [ ] Documented rationale and confidence

---

## 📊 Success Criteria

**Investigation Complete When**:
- ✅ Whitelist implemented and tested
- ✅ 50-document test complete
- ✅ All blocked entities manually reviewed
- ✅ False positive rate calculated
- ✅ GO/NO-GO decision documented
- ✅ All tests passing (125+)

**If GO**: Ready for PROMPT_11 (Week 2 Full Pilot - 100 documents)

**If NO-GO**: Document issues, apply fixes, re-test

---

## 🆘 Common Issues

### Issue 1: Whitelist Not Working

**Check**:
1. Is `whitelist_lower` being used for case-insensitive matching?
2. Is whitelist check BEFORE pronoun check in `_should_block_with_reason`?
3. Are both "US" and "us" in whitelist_lower?

### Issue 2: Still Finding Many False Positives

**Solution**:
1. Group false positives by pattern
2. Identify systemic issues (e.g., all 2-letter acronyms blocked)
3. Add category to whitelist (e.g., all ISO country codes)
4. Re-test

### Issue 3: Test Taking Too Long

**Optimization**:
- Process in batches
- Use multiprocessing for entity processing
- Skip detailed logging for 50-doc test

---

## 📚 References

- **EntityQualityFilter**: `src/knowledge_graph/postprocessing/modules/entity_quality_filter.py`
- **10-doc results**: `scripts/reextraction/BLOCKED_ENTITIES_ANALYSIS.md`
- **ISO Country Codes**: https://en.wikipedia.org/wiki/ISO_3166-1_alpha-2
- **Test Results**: `scripts/reextraction/pilot_50_results.json`

---

## 🎯 Deliverables

By end of validation:

1. **Updated EntityQualityFilter** - With comprehensive whitelist
2. **50-document test results** - pilot_50_results.json, comparison_50_report.md
3. **Manual review** - BLOCKED_ENTITIES_REVIEW.md (all entities classified)
4. **Validation report** - VALIDATION_REPORT_50_DOCS.md
5. **GO/NO-GO decision** - Clear recommendation with rationale
6. **All tests passing** - 125+ tests

---

**Next Prompt**:
- If **GO** (FP rate < 5%): `PROMPT_11_WEEK2_PILOT_REEXTRACTION.md` (100 documents)
- If **NO-GO** (FP rate > 5%): Document issues, apply fixes, re-run this task

---

**Last Updated**: 2025-12-09
**Version**: Validation Task
**Agent**: Claude Code (Opus 4.5)
**Duration**: Half day (4 hours)
**Status**: 📋 Ready for handoff
