# Blocked Entities Manual Review

**Test Set**: 43 documents (~50 target, 43 available)
**Total Blocked**: 27 entities
**Reviewer**: Claude Code (Opus 4.5)
**Date**: 2025-12-09

---

## Review Results

| # | Entity | Type | Reason | Classification | Notes |
|---|--------|------|--------|----------------|-------|
| 1 | A Geek | Person | generic_pattern | TP | Generic reference "A [noun]" |
| 2 | The Ministry for the Future | Project | generic_pattern | **FP** | Book title by Kim Stanley Robinson |
| 3 | researchers | Person | lowercase_person | TP | Generic noun |
| 4 | ryanchristo-Chora_Validator | Person | lowercase_person | **FP** | Username/handle |
| 5 | swidnikk | Person | lowercase_person | **FP** | Username |
| 6 | vitwit | Person | lowercase_person | **FP** | Username/validator name |
| 7 | Enable usdc.noble on Regen Ledger | Project | sentence_like | **FP** | Governance proposal title |
| 8 | Regen Ledger v2.0 | Project | sentence_like | **FP** | Valid project version |
| 9 | Regen Ledger v4.1 | Project | sentence_like | **FP** | Valid project version |
| 10 | Regen Ledger v6.0.0 | Project | sentence_like | **FP** | Valid project version |
| 11 | Stargaze.fi | Organization | sentence_like | **FP** | Valid protocol/domain |
| 12 | USDC.noble | Project | sentence_like | **FP** | Valid token name |
| 13 | USDC.noble | Project | sentence_like | **FP** | Duplicate |
| 14 | Will | Person | sentence_like | **FP** | Person's name (matched "will" verb) |
| 15 | Will-Regen Foundation | Organization | sentence_like | **FP** | Valid organization |
| 16 | tokenomics 2.0 | Project | sentence_like | **FP** | Valid project/concept name |
| 17 | LunarPunkLabs.org | Organization | sentence_like, technical_pattern | TP | URL/domain |
| 18 | app.regen.claim | Project | sentence_like, technical_pattern | TP | Module path |
| 19 | app.regen.evidence | Project | sentence_like, technical_pattern | TP | Module path |
| 20 | app.regen.method | Project | sentence_like, technical_pattern | TP | Module path |
| 21 | app.regen.network | Project | sentence_like, technical_pattern | TP | URL/domain |
| 22 | regen.data.v2 | Project | sentence_like, technical_pattern | TP | Module path |
| 23 | scientists | Person | stop_word, lowercase_person | TP | Generic noun |
| 24 | scientists | Person | stop_word, lowercase_person | TP | Generic noun |
| 25 | currency_allow_list | Project | technical_pattern | TP | Code identifier |
| 26 | x/feegrant | Project | technical_pattern | TP | Cosmos module path |
| 27 | x/marketplace | Project | technical_pattern | TP | Cosmos module path |

---

## Summary

- **Total Blocked**: 27
- **True Positives**: 13 (48.1%)
- **False Positives**: 14 (51.9%)
- **Unclear**: 0

### False Positive Breakdown by Reason

| Reason | False Positives | Issue |
|--------|-----------------|-------|
| sentence_like | 10 | Period pattern catches v2.0, .fi, .noble; verb pattern catches "Will" |
| lowercase_person | 3 | Catches valid usernames (swidnikk, vitwit) |
| generic_pattern | 1 | "The Ministry for the Future" - book title |

---

## Root Cause Analysis

### Issue 1: Sentence-like Pattern (10 FPs)

**Period pattern** `[.!?;]` catches:
- Version numbers: `v2.0`, `v4.1`, `v6.0.0`
- Domain names: `.fi`, `.noble`, `.network`
- Decimal numbers: `2.0`

**Verb pattern** `\b(will|would|could|...)\b` catches:
- Person name "Will" (matched as modal verb)
- "Will-Regen Foundation"

### Issue 2: Lowercase Person (3 FPs)

Valid usernames/handles are being blocked:
- `ryanchristo-Chora_Validator` - GitHub handle
- `swidnikk` - forum username
- `vitwit` - validator name

### Issue 3: Generic Pattern (1 FP)

`^(the |a |an |...)` catches "The Ministry for the Future" which is a valid book title.

---

## Recommended Fixes

### Fix 1: Add Exceptions to Sentence-like Pattern

Modify to NOT match periods followed by numbers (version numbers):
```python
# Current: [.!?;]
# Fix: Don't match "." followed by digit (v2.0) or common TLDs
```

### Fix 2: Add Common Usernames/Handles to Whitelist

Add to whitelist:
- `vitwit`, `swidnikk`, `ryanchristo`, or
- Implement pattern: don't block lowercase if it contains special chars (-, _)

### Fix 3: Add "Will" to Person Whitelist

"Will" is a common first name and should be whitelisted.

### Fix 4: Add Book/Media Titles to Whitelist

- "The Ministry for the Future"
- Or: don't apply generic_pattern to type=PROJECT

---

## GO/NO-GO Decision

**Decision**: NO-GO ❌

**Rationale**:
- Block false positive rate of 51.9% is unacceptable (target: < 5%)
- 14 valid entities incorrectly removed
- Need to fix sentence_like pattern before proceeding

**False Positive Rate**: 51.9% (blocks) / 2.5% (overall entities)

**Ready for Week 2 Pilot?**: NO

**Next Steps**:
1. Fix sentence_like pattern to not match version numbers (v2.0)
2. Add "Will" to person name whitelist
3. Consider adding validator usernames to whitelist
4. Re-run 50-doc test
5. Verify FP rate < 5% before proceeding

---

*Review completed: 2025-12-09*
