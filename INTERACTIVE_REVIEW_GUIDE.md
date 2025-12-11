# Interactive Merge Review Guide

**Created**: 2025-12-10
**Purpose**: Review questionable merges from batch consolidation with domain expert input

---

## Why Interactive Review?

The batch consolidation merged 189 entities using semantic similarity (0.88 threshold). While most merges are correct, some require domain knowledge to validate:

**Examples needing domain knowledge**:
- BuilderDAO vs DAO (specific vs generic?)
- Regen Registry Assistant vs Regen Registry program (agent vs registry?)
- Cosmos SDK vs Cosmos SDK 0.53 (version-specific vs generic?)

**Solution**: Interactive review where YOU decide on questionable merges!

---

## Quick Start

### Step 1: SSH to Server

```bash
ssh darren@202.61.196.119
cd /opt/projects/koi-processor
source venv/bin/activate
source .env
```

### Step 2: Run Interactive Review

```bash
python3 scripts/interactive_merge_review.py --limit 30
```

**This will**:
1. Show you up to 30 questionable merges
2. For each merge, ask: Keep merged or Split?
3. Save your decisions to `review_decisions.json`
4. Generate SQL script `review_decisions_splits.sql`
5. Print summary (how many kept vs split)

---

## How It Works

### For Each Merge

You'll see:
```
================================================================================
Cluster 107: BuilderDAO (ORGANIZATION)
================================================================================

Canonical: BuilderDAO (33 mentions)
Variant:   DAO (31 mentions)

Context: Generic vs specific: DAO (generic) vs BuilderDAO (specific DAO)
--------------------------------------------------------------------------------

Action? [ENTER=keep merged, 's'=split, 'q'=quit]:
```

**Your options**:
- **Press ENTER**: Keep merged (current state)
- **Type 's'**: Split into 2 separate entities
  - You'll be asked for a reason (brief)
  - Example: "BuilderDAO is a specific DAO implementation"
- **Type 'q'**: Quit review (saves progress so far)

---

## Example Session

```
================================================================================
INTERACTIVE MERGE REVIEW
================================================================================

Fetching up to 30 questionable merges...
Found 11 questionable merges to review

For each merge:
  - Press ENTER to keep merged
  - Type 's' to split (you'll be asked for a reason)
  - Type 'q' to quit review

Press ENTER to start review...

[1/11]
================================================================================
Cluster 107: BuilderDAO (ORGANIZATION)
================================================================================

Canonical: BuilderDAO (33 mentions)
Variant:   DAO (31 mentions)

Context: Generic vs specific: DAO (generic) vs BuilderDAO (specific DAO)
--------------------------------------------------------------------------------

Action? [ENTER=keep merged, 's'=split, 'q'=quit]: s
Reason for split (brief): BuilderDAO is a specific DAO, not generic
✓ Will split: BuilderDAO is a specific DAO, not generic

[2/11]
================================================================================
Cluster 1275: Regen Registry Assistant (PROJECT)
================================================================================

Canonical: Regen Registry Assistant (359 mentions)
Variant:   Regen Registry program (355 mentions)

Context: AI agent vs blockchain registry: Different components
--------------------------------------------------------------------------------

Action? [ENTER=keep merged, 's'=split, 'q'=quit]: s
Reason for split (brief): Assistant is AI agent, program is blockchain registry
✓ Will split: Assistant is AI agent, program is blockchain registry

[3/11]
... (9 more merges to review)

================================================================================
REVIEW SUMMARY
================================================================================
Total reviewed: 11
Keep merged:    6
Split:          5

Splits will:
  - Add 5 entities
  - Reduce dedup rate slightly (~0.05%)
  - Improve accuracy (correct semantic distinctions)

✓ Decisions saved to: review_decisions.json
✓ SQL script generated: review_decisions_splits.sql
  Run: psql -f review_decisions_splits.sql
```

---

## After Review

### 1. Check Your Decisions

```bash
# View decisions
cat review_decisions.json | jq '.decisions[] | {canonical: .merge.canonical, variant: .merge.variant, decision: .decision, reason: .reason}'
```

### 2. Preview SQL Script

```bash
# Review what will be split
cat review_decisions_splits.sql
```

### 3. Take Backup (REQUIRED)

```bash
PGPASSWORD=postgres pg_dump -h localhost -p 5433 -U postgres -d eliza \
  -F c -f /tmp/eliza_pre_splits_$(date +%Y%m%d_%H%M%S).backup
```

### 4. Apply Splits

```bash
PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza \
  -f review_decisions_splits.sql
```

### 5. Validate

```bash
# Check final stats
PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza -c "
SELECT COUNT(*) AS unique_entities,
       SUM(occurrence_count) AS total_mentions,
       ROUND(((1 - COUNT(*)::float / SUM(occurrence_count)::float) * 100)::numeric, 2) AS dedup_rate
FROM entity_registry;"
```

**Expected** (if you split 5 merges):
- Entities: 12,995 → 13,000 (+5)
- Dedup rate: 70.39% → 70.35% (minimal change)

---

## Known Questionable Merges (Pre-Loaded)

The script includes 11 known questionable merges:

1. **BuilderDAO ↔ DAO** - Specific vs generic DAO
2. **Regen Registry Assistant ↔ Regen Registry program** - AI agent vs blockchain
3. **Proposal 23 ↔ Proposal 25** - Different proposal numbers
4. **eastern white pines ↔ western white pines** - Different species
5. **MCP Server ↔ MCP Client** - Different components + wrong type
6. **Cosmos SDK ↔ Cosmos SDK 0.53** - Generic vs version-specific
7. **CosmWasm integration ↔ CosmWasm** - Integration vs library
8. **Regen Ledger Community ↔ Regen Ledger Team** - Different groups?
9. **Phase 1-2 Complete ↔ Phase 2a Complete** - Different phases
10. **Phase 7 Complete ↔ Phase 8 Complete** - Different phases
11. **Unique Value Proposition 1 ↔ 3** - Different UVPs

**You decide** which ones to split based on domain knowledge!

---

## Tips for Review

### Keep Merged When:
- ✅ Same thing with different names (e.g., "Dr Stuart Marsh" ← "Stuart Marsh")
- ✅ Spelling variants (e.g., "CosmosSDK" ← "Cosmos SDK")
- ✅ Generic term + specific are used interchangeably in docs

### Split When:
- ❌ Different entities (e.g., "eastern pines" vs "western pines")
- ❌ Specific instance vs generic class (e.g., "BuilderDAO" vs "DAO")
- ❌ Different versions/numbers (e.g., "Proposal 23" vs "Proposal 25")
- ❌ Different components (e.g., "MCP Server" vs "MCP Client")

### When Unsure:
- Search the docs: Look for how the terms are used
- Conservative approach: **Split** (you can always merge later)
- Aggressive approach: **Keep merged** (easier for now)

**My recommendation**: When in doubt, **Split** - it's safer to have distinct entities than incorrect merges.

---

## Alternative: Use Pre-Made Cleanup Script

If you already know what needs to be split based on domain knowledge, use:

```bash
# This splits the 5 definite false positives you identified:
# - Proposal 23/25
# - eastern/western white pines
# - MCP Server/Client
# - BuilderDAO/DAO
# - Regen Registry Assistant/program

PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza \
  -f scripts/cleanup_all_false_positives.sql
```

**Result**:
- Entities: 12,995 → 13,001 (+6)
- Dedup rate: 70.39% → 70.36%
- All 5 false positives fixed

---

## Expected Timeline

| Step | Time | Description |
|------|------|-------------|
| Setup | 1 min | SSH + activate env |
| Review | 5-10 min | Interactive review (30 merges @ 20 sec each) |
| Backup | 1 min | Pre-split backup |
| Apply | 30 sec | Run SQL script |
| Validate | 30 sec | Check stats |
| **Total** | **7-13 min** | **Complete process** |

---

## Output Files

After running interactive review, you'll have:

1. **`review_decisions.json`** - Your decisions in JSON format
   ```json
   {
     "total_reviewed": 11,
     "splits": 5,
     "keeps": 6,
     "decisions": [...]
   }
   ```

2. **`review_decisions_splits.sql`** - Generated SQL script to apply splits
   ```sql
   -- Split Script Generated from Interactive Review
   -- Splits: 5

   BEGIN;

   -- Split 1: BuilderDAO ↔ DAO
   -- Reason: BuilderDAO is a specific DAO
   ...

   COMMIT;
   ```

---

## Rollback

If you made a mistake and want to undo:

```bash
# Restore from pre-splits backup
PGPASSWORD=postgres pg_restore -h localhost -p 5433 -U postgres -d eliza \
  -c /tmp/eliza_pre_splits_*.backup
```

---

## Next Steps After Splits

1. ✅ Validate final stats (entities, dedup rate)
2. ✅ Check type collisions (should be 0)
3. ✅ Take production-ready backup
4. ✅ Tag release (`v1.0-batch-consolidation-complete`)
5. ✅ Deploy to production
6. 🎯 Plan PROMPT_29B (Grade A+ via prevention)

---

## Questions?

**Q**: What if I change my mind during review?
**A**: Type 'q' to quit - your decisions so far are saved. You can review the JSON file and edit it before applying.

**Q**: Can I review more than 30 merges?
**A**: Yes! Use `--limit 50` or any number. But 30 is usually enough to catch major issues.

**Q**: What if the script crashes?
**A**: Progress is saved to `review_decisions.json` after each merge. Just re-run and skip the ones you already reviewed.

**Q**: Can I add my own questionable merges to review?
**A**: Yes! Edit `scripts/interactive_merge_review.py` and add to the `known_questionable` list (around line 90).

---

**Status**: Ready for interactive review!
**Time**: ~10 minutes for 30 merges
**Output**: `review_decisions.json` + `review_decisions_splits.sql`
**Risk**: LOW (backup before applying, fully reversible)
