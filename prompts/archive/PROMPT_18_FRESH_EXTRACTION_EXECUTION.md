# PROMPT 18: Fresh Extraction Execution

**Date**: 2025-12-09
**Status**: Ready for execution
**Duration**: 4-6 days
**Agent**: Claude Code (Opus 4.5)

---

## 🎯 Context

**Re-extraction Complete**: 1,016 documents with 97.63% quality deployed to production.

**Fresh Extraction Needed**: 1,065 documents have NEVER had entity extraction.

**Your Mission**: Execute fresh extraction on all remaining documents, validate quality, and deploy to production.

---

## 📋 Prerequisites (Already Complete)

✅ Scripts created and reviewed:
- `query_fresh_extraction_documents.py` - Query documents
- `filter_github_markdown.py` - Filter GitHub content
- `extract_fresh_documents.py` - Run extraction (GPT-5-mini)
- `validate_fresh_extractions.py` - Validate quality
- `deploy_fresh_extractions.py` - Deploy to production

✅ Research complete:
- GitHub sensor differences documented (GITHUB_SENSORS_RESEARCH.md)
- Markdown vs code separation strategy defined

✅ Model configured:
- Using OpenAI GPT-4o-mini for extraction
- Pipeline with 5 modules ready
- Target: 97%+ pass rate
- CAT receipt generation for provenance tracking

✅ Performance optimizations:
- 10x faster API calls (50ms delay vs 500ms)
- 3 retry attempts with exponential backoff
- Graceful fallback on persistent failures
- Progress checkpointing for resume capability

---

## 🚀 Execution Plan

### Step 1: Setup & Verification (30 min)

**Location**: Server `darren@202.61.196.119`
**Directory**: `/opt/projects/koi-processor/scripts/reextraction`

```bash
# SSH to server
ssh darren@202.61.196.119

# Navigate to directory
cd /opt/projects/koi-processor/scripts/reextraction

# Verify scripts exist
ls -lh *.py

# Check environment
python3 --version
pip3 list | grep -E "psycopg2|openai"

# Verify OPENAI_API_KEY is set
echo $OPENAI_API_KEY | head -c 20
```

**Expected**: All 5 scripts present, Python 3.10+, dependencies installed, API key set.

---

### Step 2: Query Documents (15 min)

**Goal**: Identify all 1,065 documents needing fresh extraction.

```bash
# Query all documents (stats only)
python3 query_fresh_extraction_documents.py --stats-only

# Expected output:
# EXTRACTION STATUS BY SOURCE SENSOR
# discourse-sensor: 569 documents
# youtube-sensor: 15 documents
# gitlab-sensor: 30 documents
# github-activity-sensor: 23 documents
# github-sensor (markdown only): 428 documents
# TOTAL: 1,065 documents

# Generate full document list
python3 query_fresh_extraction_documents.py --output fresh_extraction_documents.json

# Verify output
cat fresh_extraction_documents.json | jq '.summary'
```

**Validation**:
- ✅ Total documents: 1,065
- ✅ No overlap with re-extracted documents
- ✅ All sources represented

---

### Step 3: Test Extraction on Sample (1 hour)

**Goal**: Verify extraction works before full run.

```bash
# Test on small Discourse sample (10 documents)
python3 extract_fresh_documents.py \
  --source discourse \
  --batch-size 10 \
  --limit 10 \
  --dry-run

# Expected output:
# - 10 documents processed
# - Entities extracted with confidence scores
# - Pipeline applied (pass/block counts)
# - No database writes (dry-run)
# - CAT receipts will be created automatically (in non-dry-run mode)

# Test actual write (5 documents)
python3 extract_fresh_documents.py \
  --source discourse \
  --batch-size 5 \
  --limit 5

# Verify in database
psql -h localhost -p 5433 -U postgres -d eliza -c "
SELECT COUNT(*)
FROM koi_kg_extractions
WHERE extractor_version LIKE '%fresh%';"

# Expected: 5 extractions

# Verify CAT receipts created
psql -h localhost -p 5433 -U postgres -d eliza -c "
SELECT COUNT(*)
FROM koi_transformation_receipts
WHERE transformation_type = 'kg_extraction_fresh';"

# Expected: 5 CAT receipts
```

**Validation**:
- ✅ Extraction succeeds
- ✅ Entities returned with confidence
- ✅ Pipeline applies correctly
- ✅ Database writes work
- ✅ CAT receipts generated automatically

---

### Step 4: Extract Text Sources (2-3 days)

**Goal**: Extract from Discourse, YouTube, GitLab, GitHub Activity (637 documents).

```bash
# Create tmux session for long-running extraction
tmux new-session -d -s fresh_extraction

# Discourse (569 documents) - ~4-6 hours
tmux send-keys -t fresh_extraction "cd /opt/projects/koi-processor/scripts/reextraction" Enter
tmux send-keys -t fresh_extraction "python3 extract_fresh_documents.py --source discourse --batch-size 50 2>&1 | tee logs/discourse_extraction.log" Enter

# Monitor progress
tmux attach -t fresh_extraction
# (Ctrl+b, d to detach)

# After Discourse completes, run remaining sources
python3 extract_fresh_documents.py --source youtube --batch-size 10
python3 extract_fresh_documents.py --source gitlab --batch-size 10
python3 extract_fresh_documents.py --source github-activity --batch-size 10

# Check progress
psql -h localhost -p 5433 -U postgres -d eliza -c "
SELECT
  m.source_sensor,
  COUNT(DISTINCT e.id) as extracted_count
FROM koi_kg_extractions e
JOIN koi_memories m ON m.rid = e.memory_rid
WHERE e.extractor_version LIKE '%fresh%'
GROUP BY m.source_sensor
ORDER BY extracted_count DESC;"
```

**Validation checkpoints**:
- After Discourse: ~569 extractions
- After YouTube: ~584 extractions
- After GitLab: ~614 extractions
- After GitHub Activity: ~637 extractions

**Error Handling**:
- API rate limits: Script has 0.5s delay, but monitor for 429 errors
- Extraction failures: Check `fresh_extraction.log` for errors
- Resume: Can re-run with same command (checks for existing extractions)

---

### Step 5: Extract GitHub Markdown (1-2 days)

**Goal**: Extract from 428 GitHub markdown files (skip code).

```bash
# First, verify markdown filtering works
python3 filter_github_markdown.py

# Expected output:
# GitHub Markdown Files:
#   Total found: 2,829
#   Has extractions: 2,401 (code files, already skipped)
#   Needs extraction: 428 (markdown files)
#
#   Files by extension:
#     .md: ~380
#     .mdx: ~20
#     README: ~25
#     .rst: ~3

# Extract markdown files
tmux send-keys -t fresh_extraction "python3 extract_fresh_documents.py --source github-markdown --batch-size 50 2>&1 | tee logs/github_markdown_extraction.log" Enter

# Monitor progress
tail -f logs/github_markdown_extraction.log

# Verify completion
psql -h localhost -p 5433 -U postgres -d eliza -c "
SELECT COUNT(*)
FROM koi_kg_extractions e
JOIN koi_memories m ON m.rid = e.memory_rid
WHERE e.extractor_version LIKE '%fresh%'
  AND m.source_sensor LIKE 'github-sensor%';"

# Expected: ~428 extractions
```

**Validation**:
- ✅ Only markdown files extracted
- ✅ Code files skipped (verified by RID patterns)
- ✅ Total fresh extractions: ~1,065

---

### Step 6: Validate Quality (2 hours)

**Goal**: Verify all extractions meet 97%+ quality target.

```bash
# Run validation on all fresh extractions
python3 validate_fresh_extractions.py \
  --output fresh_validation_report.txt

# Expected output:
# ======================================================================
# FRESH EXTRACTION VALIDATION REPORT
# ======================================================================
#
# SUMMARY
# Documents validated: 1,065
# Total entities: ~11,000-13,000
# Entities passed: ~10,700-12,600 (97%+)
# Entities blocked: ~300-700 (2-3%)
# Pass rate: 97%+
#
# TARGET MET: 97.X% >= 97.0% target
#
# BY SOURCE
# discourse: 97.X% pass rate
# youtube: 97.X% pass rate
# gitlab: 97.X% pass rate
# github-activity: 97.X% pass rate
# github-markdown: 97.X% pass rate

# Review validation report
cat fresh_validation_report.txt

# Check blocked entities
cat fresh_validation_report.json | jq '.block_analysis'
```

**Validation criteria**:
- ✅ Pass rate >= 97%
- ✅ All sources >= 95% pass rate
- ✅ Blocked entities are legitimate (pronouns, generics, URLs)
- ✅ No false positives detected

**If validation fails** (pass rate < 97%):
1. Review blocked entities: Are they legitimate blocks?
2. Check for new patterns: Any unexpected entity types?
3. Adjust pipeline config if needed: `src/knowledge_graph/config/pipeline_config.json`
4. Re-run validation

---

### Step 7: Deploy to Production (1 hour)

**Goal**: Deploy validated extractions to production knowledge graph.

```bash
# Dry-run deployment
python3 deploy_fresh_extractions.py --dry-run

# Expected output:
# CURRENT STATE
# Fresh extractions pending: 1,065
# Fresh entities to deploy: ~11,000-13,000
#
# DRY RUN - No changes made
# Would deploy 1,065 extractions
# Would add ~11,000-13,000 entities to knowledge graph

# Run validation before deployment
python3 deploy_fresh_extractions.py \
  --validate-first \
  --deploy \
  --output fresh_deployment_report.txt

# Expected output:
# VALIDATION
# Running validation...
# [validation output]
# Validation passed - Proceeding with deployment
#
# BACKUP
# Backup created: backups/pre_fresh_deployment_YYYYMMDD_HHMMSS.json
#
# DEPLOYMENT
# Updating extractor versions to mark as deployed...
# Updated 1,065 extractions
#
# DEPLOYMENT COMPLETE
# Deployed: 1,065 extractions
# Entities added: ~11,000-13,000

# Verify deployment
psql -h localhost -p 5433 -U postgres -d eliza -c "
SELECT
  extractor_version,
  COUNT(*) as count,
  SUM(jsonb_array_length(COALESCE(entities, '[]'::jsonb))) as total_entities
FROM koi_kg_extractions
GROUP BY extractor_version
ORDER BY count DESC;"

# Expected:
# 1.0.0-reextracted: 1,015 extractions, ~14,690 entities
# 1.0.0-fresh-deployed: 1,065 extractions, ~11,000-13,000 entities
# Total: 2,080 extractions, ~25,000-27,000 entities

# Verify CAT receipts created
psql -h localhost -p 5433 -U postgres -d eliza -c "
SELECT
  transformation_type,
  COUNT(*) as count,
  SUM(entities_extracted) as total_entities
FROM koi_transformation_receipts
WHERE transformation_type = 'kg_extraction_fresh'
GROUP BY transformation_type;"

# Expected:
# kg_extraction_fresh: 1,065 receipts, ~11,000-13,000 entities
```

**Validation**:
- ✅ Backup created successfully
- ✅ 1,065 extractions deployed
- ✅ 1,065 CAT receipts created
- ✅ No errors during deployment
- ✅ Total corpus: 2,080 documents with extractions

---

## 📊 Expected Final Results

### Corpus Coverage

| Phase | Documents | Entities | Status |
|-------|-----------|----------|--------|
| Re-extraction (Weeks 3-5) | 1,016 | 14,690 | Deployed ✅ |
| Fresh Extraction (Week 6) | 1,065 | ~11,000-13,000 | [TO DEPLOY] |
| **TOTAL** | **2,081** | **~25,000-27,000** | **[%]** |

### Quality Metrics

| Metric | Target | Expected |
|--------|--------|----------|
| Pass Rate | 97%+ | 97-98% |
| Block Rate | 2-3% | 2-3% |
| Consistency | Re-extraction ± 1% | YES |
| False Positives | < 5% | < 1% |

### Source Coverage

| Source | Documents | Extracted | Coverage |
|--------|-----------|-----------|----------|
| Discourse | 980 | 980 | 100% |
| Website | 720 | 454 | 63% |
| Notion | 260 | 78 | 30% |
| GitHub (markdown) | ~428 | 428 | 100% |
| GitHub Activity | ~23 | 23 | 100% |
| Podcast | 67 | 66 | 99% |
| YouTube | 15 | 15 | 100% |
| GitLab | 30 | 30 | 100% |
| Medium | 8 | [TBD] | [%] |

**Note**: Website and Notion have lower coverage because many documents are duplicates, administrative, or non-text content.

---

## 🚨 Troubleshooting

### OpenAI API Issues

**Rate Limit (429 error)**:
```bash
# Check rate limit status
curl https://api.openai.com/v1/usage \
  -H "Authorization: Bearer $OPENAI_API_KEY"

# Reduce batch size
python3 extract_fresh_documents.py --source discourse --batch-size 10
```

**Authentication error**:
```bash
# Verify API key
echo $OPENAI_API_KEY | head -c 20

# Re-set if needed
export OPENAI_API_KEY="sk-..."
```

### Database Connection Issues

**Connection refused**:
```bash
# Check PostgreSQL is running
sudo systemctl status postgresql

# Restart if needed
sudo systemctl restart postgresql

# Verify port
psql -h localhost -p 5433 -U postgres -d eliza -c "SELECT 1;"
```

### Extraction Failures

**Check logs**:
```bash
tail -100 fresh_extraction.log
```

**Resume from failure**:
```bash
# Script automatically resumes from checkpoint
python3 extract_fresh_documents.py --source discourse --batch-size 50

# Check checkpoint status
cat .checkpoint_discourse.json

# Force restart from beginning (delete checkpoint)
rm .checkpoint_discourse.json
python3 extract_fresh_documents.py --source discourse --batch-size 50
```

**Retry behavior**:
- Each extraction automatically retries 3 times with exponential backoff (2s, 4s, 6s)
- On persistent failure, extraction continues with empty result (logged as error)
- Checkpoint saves every batch, so interruptions don't lose progress

### Low Pass Rate

**Review blocked entities**:
```bash
cat fresh_validation_report.json | jq '.block_analysis.by_reason' | head -20
```

**Adjust pipeline** (if needed):
```bash
vim /opt/projects/koi-processor/src/knowledge_graph/config/pipeline_config.json

# Lower confidence threshold (if too aggressive)
# Add exceptions to quality filter (if false positives)
```

---

## ✅ Success Criteria

**Extraction Complete When**:
- ✅ All 1,065 documents extracted
- ✅ Pass rate >= 97%
- ✅ All sources processed
- ✅ No critical errors in logs
- ✅ Validation passed

**Deployment Complete When**:
- ✅ Backup created
- ✅ 1,065 extractions deployed
- ✅ 1,065 CAT receipts created
- ✅ ~11,000-13,000 entities added
- ✅ No deployment errors
- ✅ Final report generated

**Project Complete When**:
- ✅ Total corpus: 2,081 documents
- ✅ Total entities: ~25,000-27,000
- ✅ Quality: 97%+ maintained
- ✅ GitHub sensors understood
- ✅ All documentation complete

---

## 📂 Output Files

**Location**: `/opt/projects/koi-processor/scripts/reextraction/`

**Generated files**:
- `fresh_extraction_documents.json` - Document list (1,065 docs)
- `fresh_extraction_results_*.json` - Extraction results by source
- `fresh_validation_report.txt` - Validation report
- `fresh_validation_report.json` - Validation data
- `fresh_deployment_report.txt` - Deployment report
- `backups/pre_fresh_deployment_*.json` - Pre-deployment backup
- `logs/discourse_extraction.log` - Discourse extraction log
- `logs/github_markdown_extraction.log` - GitHub markdown extraction log

---

## 📋 Quick Start

```bash
# SSH to server
ssh darren@202.61.196.119

# Navigate to directory
cd /opt/projects/koi-processor/scripts/reextraction

# Create logs directory
mkdir -p logs backups

# Step 1: Query documents
python3 query_fresh_extraction_documents.py --stats-only

# Step 2: Test extraction (5 docs)
python3 extract_fresh_documents.py --source discourse --batch-size 5 --limit 5

# Step 3: Full extraction (in tmux)
tmux new-session -d -s fresh_extraction
tmux send-keys -t fresh_extraction "cd /opt/projects/koi-processor/scripts/reextraction" Enter
tmux send-keys -t fresh_extraction "python3 extract_fresh_documents.py --source discourse --batch-size 50 2>&1 | tee logs/discourse_extraction.log" Enter

# Monitor
tmux attach -t fresh_extraction

# Step 4: Continue with other sources
python3 extract_fresh_documents.py --source youtube --batch-size 10
python3 extract_fresh_documents.py --source gitlab --batch-size 10
python3 extract_fresh_documents.py --source github-activity --batch-size 10
python3 extract_fresh_documents.py --source github-markdown --batch-size 50

# Step 5: Validate
python3 validate_fresh_extractions.py --output fresh_validation_report.txt

# Step 6: Deploy
python3 deploy_fresh_extractions.py --validate-first --deploy --output fresh_deployment_report.txt

# Step 7: Celebrate!
cat fresh_deployment_report.txt
```

---

## 🎉 Next Steps After Completion

1. **Generate final project report**
   - Combine re-extraction + fresh extraction results
   - Document quality improvements (62% → 97%+)
   - Summarize GitHub sensor research findings

2. **Knowledge graph verification**
   - Query Fuseki for entity counts
   - Verify entity types distribution
   - Check for orphaned entities

3. **Production monitoring**
   - Set up quality monitoring
   - Create dashboards for entity counts
   - Document maintenance procedures

4. **Documentation updates**
   - Update CLAUDE.md with final results
   - Create maintenance guide
   - Archive all prompts and reports

---

**Agent**: Your task is to execute fresh extraction on 1,065 documents, validate quality (97%+ target), and deploy to production. Follow the step-by-step plan above, validate at each checkpoint, and generate comprehensive reports.

The scripts are production-ready. Use tmux for long-running extractions, monitor logs for errors, and validate before deploying.

**Expected Timeline** (with 10x performance improvements):
- Day 1: Setup, test, complete Discourse extraction (2-3 hours) ⚡️
- Day 2: All remaining text sources + GitHub markdown (2-3 hours) ⚡️
- Day 3: Validation and quality review (2-3 hours)
- Day 4: Deployment and final reporting (2-3 hours)

**Performance Improvements**:
- ⚡️ **10x faster** extraction (50ms vs 500ms delay)
- 🔄 **Automatic retry** with exponential backoff (99%+ reliability)
- 💾 **Progress checkpointing** every batch (resume from interruption)
- 🛡️ **Graceful fallback** (continues on persistent failures)
- 📊 **Real-time progress** saving and logging

Good luck! 🚀
