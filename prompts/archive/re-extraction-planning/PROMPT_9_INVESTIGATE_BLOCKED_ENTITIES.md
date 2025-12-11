# PROMPT 9: Investigate Blocked Entities

**Date**: 2025-12-09
**Phase**: Week 1 Follow-up - Block Analysis
**Duration**: 1-2 days
**Agent**: Claude Code (Opus 4.5)

---

## 🎯 Context

**Week 1 Complete**: Backups secured, scripts built, test run successful (10 documents)

**Test Results**:
- **Pass rate**: 96.3% (105/109 entities)
- **Block rate**: 3.7% (4 entities blocked)
- **Issue**: All 4 blocked entities show "Unknown" reason
- **Blocker**: EntityQualityFilter (100%)

**Before proceeding to full pilot (100 documents), we need to**:
1. Understand WHY entities were blocked
2. Add better block reason tracking
3. Validate blocks are correct (not false positives)
4. Document findings for Week 2 decision

---

## 🎯 Your Mission

Investigate the 4 blocked entities from the test run and improve block reason tracking.

**Tasks**:
1. **Review Blocked Entities** (1-2 hours)
   - Extract the 4 blocked entity names from test results
   - Identify which EntityQualityFilter pattern blocked them
   - Validate blocks are correct (pronouns, generics, URLs, etc.)

2. **Improve Block Tracking** (2-3 hours)
   - Add `block_reason` field to ProcessingContext
   - Update EntityQualityFilter to log specific patterns
   - Update comparison script to show detailed block reasons

3. **Re-run Test** (30 min)
   - Run test with 10 documents again
   - Verify block reasons now show correctly
   - Generate new comparison report

4. **Analysis & Report** (30 min)
   - Document findings
   - Recommend any filter tuning
   - Make GO/NO-GO recommendation for Week 2 pilot

---

## 📋 Task 1: Review Blocked Entities (1-2 hours)

### Step 1.1: Extract Blocked Entity Names

From the test run, find which entities were blocked.

**Location**: `koi-processor/scripts/reextraction/pilot_results.json`

**Look for**:
```json
{
  "document_id": ...,
  "baseline_entities": [...],
  "pipeline_results": {
    "valid": [...],
    "blocked": [
      {
        "name": "???",
        "type": "???",
        "blocked_by": "EntityQualityFilter",
        "reason": "Unknown"
      }
    ]
  }
}
```

**Extract**: The names of all 4 blocked entities

### Step 1.2: Identify Blocking Patterns

For each blocked entity, check which pattern in EntityQualityFilter blocked it.

**File to check**: `src/knowledge_graph/improvements/entity_quality_filter.py`

**Patterns to check**:
1. **Pronouns**: `['i', 'you', 'he', 'she', 'it', 'we', 'they', 'me', ...]`
2. **Generic nouns**: `['user', 'users', 'person', 'people', 'community', ...]`
3. **URL/email**: Regex patterns `http`, `@`, `localhost`, etc.
4. **Sentence-like**: Contains 5+ words
5. **Short low-quality**: Length < 3 or single character

**Method**:
```python
# For each blocked entity name
from knowledge_graph.improvements.entity_quality_filter import EntityQualityFilter

filter = EntityQualityFilter()
entity = {'name': 'BLOCKED_NAME_HERE', 'type': 'TYPE'}

# Check manually which pattern matches
name_lower = entity['name'].lower()

# Check each pattern
if name_lower in filter.pronouns:
    print("BLOCKED: Pronoun")
elif name_lower in filter.generic_nouns:
    print("BLOCKED: Generic noun")
# ... etc
```

### Step 1.3: Validate Blocks Are Correct

For each blocked entity:
- **Are they truly low-quality?** (pronouns, generics, etc.)
- **Or are they false positives?** (valid entities incorrectly blocked)

**Document findings**:
```markdown
## Blocked Entities Analysis

1. **Entity**: "BLOCKED_NAME_1"
   - **Type**: PROJECT/PERSON/etc
   - **Reason**: Pronoun / Generic noun / etc
   - **Valid block?**: YES/NO
   - **Notes**: ...

2. **Entity**: "BLOCKED_NAME_2"
   ...
```

---

## 📋 Task 2: Improve Block Tracking (2-3 hours)

### Step 2.1: Add `block_reason` to ProcessingContext

**File**: `src/knowledge_graph/postprocessing/base.py`

**Current**:
```python
@dataclass
class ProcessingContext:
    entities: List[Dict[str, Any]]
    blocked_entities: List[Dict[str, Any]] = field(default_factory=list)
    # ... other fields
```

**Add**:
```python
@dataclass
class ProcessingContext:
    entities: List[Dict[str, Any]]
    blocked_entities: List[Dict[str, Any]] = field(default_factory=list)
    # NEW: Track detailed block reasons
    block_metadata: Dict[str, Any] = field(default_factory=dict)  # entity_id -> {reason, pattern, module}
    # ... other fields
```

### Step 2.2: Update EntityQualityFilter to Log Reasons

**File**: `src/knowledge_graph/postprocessing/modules/entity_quality_filter.py`

**Current blocking** (example):
```python
def _should_block(self, entity: Dict[str, Any]) -> bool:
    name = entity.get('name', '')
    name_lower = name.lower()

    # Check pronouns
    if name_lower in self.pronouns:
        return True

    # Check generic nouns
    if name_lower in self.generic_nouns:
        return True

    # ... other checks

    return False
```

**Updated** (add reason tracking):
```python
def _should_block(self, entity: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Check if entity should be blocked. Returns (should_block, reason)."""
    name = entity.get('name', '')
    name_lower = name.lower()

    # Check pronouns
    if name_lower in self.pronouns:
        return True, f"pronoun: '{name}'"

    # Check generic nouns
    if name_lower in self.generic_nouns:
        return True, f"generic_noun: '{name}'"

    # Check URL patterns
    if any(pattern in name_lower for pattern in ['http', 'www.', '://', 'localhost']):
        return True, f"url_pattern: '{name}'"

    # Check email
    if '@' in name and '.' in name.split('@')[-1]:
        return True, f"email_pattern: '{name}'"

    # Check sentence-like (5+ words)
    if len(name.split()) >= 5:
        return True, f"sentence_like: {len(name.split())} words"

    # Check short low-quality
    if len(name) < 3 or len(name) == 1:
        return True, f"too_short: {len(name)} chars"

    return False, None

def process(self, context: ProcessingContext) -> ProcessingContext:
    """Process entities through quality filter."""
    valid = []
    blocked = []

    for entity in context.entities:
        should_block, reason = self._should_block(entity)

        if should_block:
            blocked_entity = {**entity, 'blocked_by': self.get_name()}
            if reason:
                blocked_entity['block_reason'] = reason
                # Add to context metadata
                entity_id = f"{entity.get('name')}:{entity.get('type')}"
                context.block_metadata[entity_id] = {
                    'reason': reason,
                    'module': self.get_name(),
                    'pattern': reason.split(':')[0] if ':' in reason else reason
                }
            blocked.append(blocked_entity)
        else:
            valid.append(entity)

    context.entities = valid
    context.blocked_entities.extend(blocked)

    return context
```

### Step 2.3: Update Comparison Script

**File**: `scripts/reextraction/compare_extractions.py`

**Update block analysis** to show detailed reasons:

```python
def analyze_blocks(results: Dict) -> Dict:
    """Analyze blocked entities with detailed reasons."""
    blocks_by_module = {}
    blocks_by_reason = {}
    blocks_by_pattern = {}

    for doc_id, doc_data in results.items():
        blocked = doc_data['pipeline_results']['blocked']

        for entity in blocked:
            module = entity.get('blocked_by', 'Unknown')
            reason = entity.get('block_reason', 'Unknown')
            pattern = reason.split(':')[0] if ':' in reason else reason

            blocks_by_module[module] = blocks_by_module.get(module, 0) + 1
            blocks_by_reason[reason] = blocks_by_reason.get(reason, 0) + 1
            blocks_by_pattern[pattern] = blocks_by_pattern.get(pattern, 0) + 1

    return {
        'by_module': blocks_by_module,
        'by_reason': blocks_by_reason,
        'by_pattern': blocks_by_pattern
    }
```

**Update markdown report** to show:
```markdown
## Block Analysis

### By Module
| Module | Count | Percentage |
|--------|-------|------------|
| EntityQualityFilter | 4 | 100.0% |

### By Pattern
| Pattern | Count | Percentage |
|---------|-------|------------|
| generic_noun | 2 | 50.0% |
| pronoun | 1 | 25.0% |
| too_short | 1 | 25.0% |

### By Reason (Detailed)
| Reason | Count |
|--------|-------|
| generic_noun: 'user' | 1 |
| generic_noun: 'community' | 1 |
| pronoun: 'we' | 1 |
| too_short: 2 chars | 1 |
```

### Step 2.4: Update Tests

Add tests for block reason tracking:

**File**: `tests/test_entity_quality_filter.py`

```python
def test_block_reason_tracking():
    """Test that block reasons are tracked correctly."""
    filter = EntityQualityFilter()

    # Test pronoun blocking
    should_block, reason = filter._should_block({'name': 'we', 'type': 'PERSON'})
    assert should_block == True
    assert 'pronoun' in reason

    # Test generic noun blocking
    should_block, reason = filter._should_block({'name': 'user', 'type': 'PERSON'})
    assert should_block == True
    assert 'generic_noun' in reason

    # Test valid entity
    should_block, reason = filter._should_block({'name': 'Gregory Landua', 'type': 'PERSON'})
    assert should_block == False
    assert reason is None
```

---

## 📋 Task 3: Re-run Test (30 min)

### Step 3.1: Run Test with Updated Code

```bash
cd koi-processor

# Run test suite first (verify no regressions)
pytest tests/test_entity_quality_filter.py -v
# Expected: All tests pass + new block_reason tests

# Run full test suite
pytest
# Expected: 121+ tests passing

# Re-run 10-document test
cd scripts/reextraction
python select_pilot_documents.py --count 10
python extract_baseline_entities.py
python reextract_pilot.py
python compare_extractions.py
```

### Step 3.2: Review New Comparison Report

**Check**: `comparison_report.md` should now show:
- Detailed block reasons (not "Unknown")
- Block patterns (generic_noun, pronoun, etc.)
- Specific entity names and why they were blocked

**Verify**:
- [ ] Block reasons are specific and actionable
- [ ] All 4 blocked entities have clear reasons
- [ ] No "Unknown" reasons remain

---

## 📋 Task 4: Analysis & Report (30 min)

### Step 4.1: Document Findings

Create: `scripts/reextraction/BLOCKED_ENTITIES_ANALYSIS.md`

```markdown
# Blocked Entities Analysis

**Date**: 2025-12-09
**Test Set**: 10 documents
**Total Blocked**: 4 entities (3.7%)

---

## Summary

All 4 blocked entities were correctly identified as low-quality:

1. **Entity 1**: [name]
   - **Type**: [type]
   - **Reason**: [specific reason]
   - **Pattern**: [pattern type]
   - **Valid block?**: YES/NO
   - **Action**: Keep filter / Tune threshold / Remove pattern

2. **Entity 2**: ...
3. **Entity 3**: ...
4. **Entity 4**: ...

---

## Recommendations

Based on analysis of blocked entities:

### Option A: No Changes Needed ✅
- All blocks are valid (no false positives)
- Filters working correctly
- **Proceed to Week 2 pilot (100 documents)**

### Option B: Tune Filters
- Found [X] false positives (valid entities incorrectly blocked)
- Recommend adjusting:
  - [ ] Remove pattern: [pattern name]
  - [ ] Increase threshold: [which threshold]
  - [ ] Whitelist entity: [entity name]
- **Re-run test after tuning**

### Option C: Investigate Further
- Unclear if blocks are correct
- Need manual review of source documents
- **Defer Week 2 pilot until resolved**

---

## Decision

**Recommended**: [Option A/B/C]

**Rationale**: [Why this option]

**Next Steps**: [What to do next]
```

### Step 4.2: Make GO/NO-GO Recommendation

**GO if**:
- All 4 blocked entities are truly low-quality
- Block reasons are clear and correct
- No false positives found
- Test results validate pipeline behavior

**NO-GO if**:
- Found false positives (valid entities blocked)
- Block reasons unclear or incorrect
- Filters need tuning
- Concerns about pipeline accuracy

---

## ✅ Completion Checklist

### Task 1: Review Blocked Entities
- [ ] Extracted 4 blocked entity names
- [ ] Identified blocking patterns for each
- [ ] Validated blocks are correct (or not)
- [ ] Documented findings

### Task 2: Improve Block Tracking
- [ ] Added `block_metadata` to ProcessingContext
- [ ] Updated EntityQualityFilter with `block_reason`
- [ ] Updated comparison script to show reasons
- [ ] Added tests for block reason tracking
- [ ] All tests passing (121+)

### Task 3: Re-run Test
- [ ] Re-ran 10-document test
- [ ] New comparison report generated
- [ ] Block reasons now show correctly (not "Unknown")
- [ ] Results validated

### Task 4: Analysis & Report
- [ ] Created BLOCKED_ENTITIES_ANALYSIS.md
- [ ] Documented all 4 blocked entities
- [ ] Made recommendations
- [ ] GO/NO-GO decision documented

---

## 📊 Success Criteria

**Investigation Complete When**:
- ✅ All 4 blocked entities analyzed
- ✅ Block reasons tracked and logged correctly
- ✅ Test re-run successful with detailed reasons
- ✅ GO/NO-GO recommendation made
- ✅ Analysis documented for Week 2 decision

**If GO**: Ready for PROMPT_10 (Week 2 Full Pilot - 100 documents)

**If NO-GO**: Need filter tuning, then re-test

---

## 🆘 Common Issues

### Issue 1: Can't Find Blocked Entities in Results

**Solution**:
```bash
cd scripts/reextraction
# Search for blocked entities in results
cat pilot_results.json | jq '.[] | .pipeline_results.blocked[] | .name'
```

### Issue 2: Block Reason Still Shows "Unknown"

**Check**:
1. Did you update `_should_block()` to return `(bool, str)`?
2. Did you update `process()` to use the returned reason?
3. Did you add `block_reason` to blocked entity dict?

### Issue 3: Tests Failing After Changes

**Solution**:
1. Update test expectations (method now returns tuple)
2. Mock the new block_metadata field
3. Check for backward compatibility issues

---

## 📚 References

- **EntityQualityFilter**: `src/knowledge_graph/postprocessing/modules/entity_quality_filter.py`
- **ProcessingContext**: `src/knowledge_graph/postprocessing/base.py`
- **Comparison Script**: `scripts/reextraction/compare_extractions.py`
- **Test Results**: `scripts/reextraction/pilot_results.json`
- **Original Test Report**: `scripts/reextraction/comparison_report.md`

---

## 🎯 Deliverables

By end of investigation:

1. **BLOCKED_ENTITIES_ANALYSIS.md** - Analysis report
2. **Updated code** - Block reason tracking implemented
3. **New comparison_report.md** - With detailed block reasons
4. **GO/NO-GO decision** - For Week 2 pilot
5. **All tests passing** - No regressions

---

**Next Prompt**:
- If **GO**: `PROMPT_10_WEEK2_PILOT_REEXTRACTION.md` (100 documents)
- If **NO-GO**: Tune filters and re-run this investigation

---

**Last Updated**: 2025-12-09
**Version**: Investigation Task
**Agent**: Claude Code (Opus 4.5)
**Duration**: 1-2 days
**Status**: 📋 Ready for handoff
