# Interactive Review - Quick Start

## 🚀 Run This Now

Open your terminal and paste these commands:

```bash
# SSH to server
ssh darren@202.61.196.119

# Navigate and setup
cd /opt/projects/koi-processor
source venv/bin/activate
source .env

# Run interactive review
python3 scripts/interactive_merge_review.py --limit 30
```

---

## 🎮 During Review

For each merge you'll see:
```
[1/11]
================================================================================
Cluster 107: BuilderDAO (ORGANIZATION)
================================================================================

Canonical: BuilderDAO (33 mentions)
Variant:   DAO (31 mentions)

Context: Generic vs specific: DAO (generic) vs BuilderDAO (specific DAO)
--------------------------------------------------------------------------------

Action? [ENTER=keep merged, 's'=split, 'q'=quit]:
```

### Your Keyboard Commands

| Key | Action | When to Use |
|-----|--------|-------------|
| **ENTER** | Keep merged | Same thing, just different names |
| **s** | Split into 2 | Different entities (like BuilderDAO ≠ DAO) |
| **q** | Quit review | Done reviewing, save progress |

---

## 📝 Quick Decision Guide

### ✅ Keep Merged (Press ENTER)

- Same entity, different spelling
- Title variants (Dr Stuart Marsh ← Stuart Marsh)
- Abbreviations (SDK ← Software Development Kit)

### ❌ Split (Type 's')

- **Different entities** (BuilderDAO ≠ DAO)
- **Different versions** (Proposal 23 ≠ Proposal 25)
- **Different components** (MCP Server ≠ MCP Client)
- **Different species/types** (eastern pines ≠ western pines)

**When unsure**: Split! (Safer to have distinct entities)

---

## 🎯 Recommendations for the 11 Merges

Based on your domain knowledge:

| # | Merge | My Recommendation | Your Decision |
|---|-------|-------------------|---------------|
| 1 | BuilderDAO ↔ DAO | **SPLIT** (specific vs generic) | |
| 2 | Regen Registry Assistant ↔ program | **SPLIT** (AI agent vs registry) | |
| 3 | Proposal 23 ↔ 25 | **SPLIT** (different proposals) | |
| 4 | eastern ↔ western white pines | **SPLIT** (different species) | |
| 5 | MCP Server ↔ Client | **SPLIT** (different components) | |
| 6 | Cosmos SDK ↔ 0.53 | **KEEP** (version vs general) | |
| 7 | CosmWasm integration ↔ CosmWasm | **KEEP** (same library) | |
| 8 | Regen Ledger Community ↔ Team | **KEEP** (likely same group) | |
| 9 | Phase 1-2 ↔ Phase 2a Complete | **SPLIT** (different phases) | |
| 10 | Phase 7 ↔ 8 Complete | **SPLIT** (different phases) | |
| 11 | UVP 1 ↔ UVP 3 | **SPLIT** (different value props) | |

**Recommended**: Split 8, Keep 3

---

## ⚡ Expected Timeline

- Review 11 merges @ ~30 sec each = **5-6 minutes**
- Script generates SQL automatically
- Total process: **~10 minutes**

---

## 📤 After Review Completes

The script will create:

1. **`review_decisions.json`** - Your decisions
2. **`review_decisions_splits.sql`** - Auto-generated SQL to apply splits

Then just run:
```bash
# Take backup first!
PGPASSWORD=postgres pg_dump -h localhost -p 5433 -U postgres -d eliza \
  -F c -f /tmp/eliza_pre_splits_$(date +%Y%m%d_%H%M%S).backup

# Apply your splits
PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza \
  -f review_decisions_splits.sql

# Validate
PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza -c "
SELECT COUNT(*) AS unique_entities,
       SUM(occurrence_count) AS total_mentions,
       ROUND(((1 - COUNT(*)::float / SUM(occurrence_count)::float) * 100)::numeric, 2) AS dedup_rate
FROM entity_registry;"
```

---

## 🆘 If Something Goes Wrong

**Script error?**
- Check env vars: `echo $POSTGRES_HOST` (should be localhost)
- Check connection: `PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza -c 'SELECT 1'`

**Want to undo?**
- Restore from backup: `pg_restore -c /tmp/eliza_pre_splits_*.backup`

**Changed your mind?**
- Edit `review_decisions.json` before applying
- Re-run review with `--limit` adjusted

---

## 💡 Pro Tips

1. **Read the context** - It explains WHY it's questionable
2. **When in doubt, split** - Easier to merge later than un-merge
3. **Use 'q' if rushed** - Progress is saved, continue later
4. **Review the SQL** - Check `review_decisions_splits.sql` before applying

---

## ✅ Success Criteria

After applying splits:
- **Entities**: 12,995 → ~13,003 (depends on your splits)
- **Dedup rate**: 70.39% → ~70.34% (minimal change)
- **Type collisions**: 0 (none created)
- **Grade**: A (high-confidence, domain-validated)

---

**Ready?** Copy the commands above and paste into your terminal! 🚀

**Estimated time**: 10 minutes total
**Your role**: Review 11 merges, decide keep vs split
**Output**: Custom SQL script based on YOUR decisions
