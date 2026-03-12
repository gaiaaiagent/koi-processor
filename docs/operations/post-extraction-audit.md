# Post-Extraction Audit Runbook

Purpose
- Run a lightweight quality audit after a Stage 6 re-extraction or large batch reprocess.
- Produce repeatable reports and a single summary file for documentation.

When to run
- After full Stage 6 re-extraction
- After large batch reprocesses (e.g., E402, backfills)

Prerequisites
- Production SSH access (for prod DB)
- `.env` configured with `POSTGRES_*` variables
- Python venv available at `.venv/bin/python`

## Quick Run (Recommended)

On production:

```bash
ssh darren@202.61.196.119
cd /opt/projects/koi-processor
set -a; source .env; set +a
./scripts/post_extraction_audit.sh
```

Outputs are saved to:
- `docs/archive/reports/` (KG audit, predicate histogram, alias report)
- A summary markdown file named `post_extraction_audit_<timestamp>.md`

## Manual Steps (Required Follow-ups)

### 1) Review alias duplicates
- Open the latest alias audit CSV in `docs/archive/reports/alias_audit_report_<timestamp>.csv`
- Classify each alias row as `merge`, `defer`, or `ignore`
- Use `scripts/apply_alias_merges.py` if you want to apply safe merges

### 2) Predicate type-constraint violations
Run this SQL to detect any violations of the predicate guard constraints:

```sql
WITH rels AS (
  SELECT r.id, r.predicate,
         s.entity_type AS subject_type,
         o.entity_type AS object_type
  FROM koi_relationships r
  JOIN entity_registry s ON r.subject_entity_id = s.id
  JOIN entity_registry o ON r.object_entity_id = o.id
)
SELECT predicate, subject_type, object_type, COUNT(*) AS cnt
FROM rels
WHERE
  (predicate = 'operates' AND (subject_type IN ('CONCEPT','EVENT') OR object_type IN ('CONCEPT','MATERIAL','LOCATION','EVENT')))
  OR (predicate = 'founded' AND (subject_type NOT IN ('PERSON','ORGANIZATION') OR object_type NOT IN ('ORGANIZATION','PROJECT','TECHNOLOGY')))
  OR (predicate = 'works_at' AND (subject_type NOT IN ('PERSON') OR object_type NOT IN ('ORGANIZATION','PROJECT','VALIDATOR')))
  OR (predicate = 'employs' AND (subject_type NOT IN ('ORGANIZATION') OR object_type NOT IN ('PERSON')))
  OR (predicate = 'member_of' AND (subject_type NOT IN ('PERSON','ORGANIZATION','VALIDATOR') OR object_type NOT IN ('ORGANIZATION','PROJECT')))
  OR (predicate = 'leads' AND (subject_type NOT IN ('PERSON','ORGANIZATION') OR object_type NOT IN ('ORGANIZATION','PROJECT','EVENT','PROCESS')))
  OR (predicate = 'located_in' AND object_type NOT IN ('LOCATION'))
  OR (predicate = 'authored' AND subject_type NOT IN ('PERSON','ORGANIZATION'))
  OR (predicate = 'validates' AND subject_type NOT IN ('VALIDATOR','ORGANIZATION','TECHNOLOGY'))
  OR (predicate = 'delegates' AND object_type NOT IN ('VALIDATOR','PERSON','ORGANIZATION'))
  OR (predicate = 'votes' AND subject_type NOT IN ('PERSON','ORGANIZATION','VALIDATOR'))
GROUP BY predicate, subject_type, object_type
ORDER BY cnt DESC;
```

If any violations appear, consider deleting or correcting them as part of the cycle.

### 3) (Optional) GraphRAG eval
If GraphRAG features are in use:

```bash
PYTHONPATH=src ./.venv/bin/python scripts/eval_graphrag.py --compare-baseline
```

## Documentation
- Attach the summary report to the master cycle doc:
  `docs/archive/knowledge-graph-review-2026-01.md`
- Note any merges or fixes performed after the audit.
