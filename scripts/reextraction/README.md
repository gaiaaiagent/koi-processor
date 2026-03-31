# Re-extraction Scripts

Scripts for Phase 3 re-extraction of documents with the quality pipeline.

## Overview

These scripts support the incremental re-extraction process:

1. **select_pilot_documents.py** - Select representative documents for pilot
2. **extract_baseline_entities.py** - Extract current entities from knowledge graph
3. **reextract_pilot.py** - Re-process entities through the pipeline
4. **compare_extractions.py** - Generate comparison reports

## Prerequisites

- SSH tunnel to production database:
  ```bash
  ssh -L 5433:localhost:5433 darren@202.61.196.119
  ```

- Python dependencies:
  ```bash
  pip install psycopg2-binary
  ```

## Usage

### 1. Select Pilot Documents

Select documents for pilot re-extraction using stratified sampling:

```bash
# Select 100 documents (default)
python scripts/reextraction/select_pilot_documents.py

# Select 10 documents for testing
python scripts/reextraction/select_pilot_documents.py --count 10

# Filter by source type
python scripts/reextraction/select_pilot_documents.py --source discourse
```

**Options**:
- `--count, -c` - Total documents to select (default: 100)
- `--source, -s` - Filter by source type (e.g., discourse, github, podcast)
- `--output, -o` - Output file path (default: pilot_documents.json)
- `--host` - Database host (default: localhost)
- `--port` - Database port (default: 5433)

**Output**: `pilot_documents.json`

**Sampling distribution**:
- 50% high-quality (confidence > 0.85)
- 30% medium-quality (confidence 0.70-0.85)
- 20% low-quality (confidence < 0.70)

### 2. Extract Baseline Entities

Extract current entities from the knowledge graph for selected documents:

```bash
python scripts/reextraction/extract_baseline_entities.py
```

**Options**:
- `--input, -i` - Input file path (default: pilot_documents.json)
- `--output, -o` - Output file path (default: baseline_entities.json)
- `--host` - Database host (default: localhost)
- `--port` - Database port (default: 5433)

**Input**: `pilot_documents.json`
**Output**: `baseline_entities.json`

### 3. Re-process with Pipeline

Re-process baseline entities through the quality pipeline:

```bash
python scripts/reextraction/reextract_pilot.py
```

**Options**:
- `--baseline, -b` - Baseline file path (default: baseline_entities.json)
- `--output, -o` - Output file path (default: pilot_results.json)
- `--config, -c` - Pipeline config file path

**Input**: `pilot_documents.json`, `baseline_entities.json`
**Output**: `pilot_results.json`

**Pipeline modules applied**:
1. ConfidenceFilter - Blocks low-confidence entities (< 0.70)
2. CanonicalResolver - Normalizes known entity aliases
3. EntityQualityFilter - Blocks pronouns, generics, URLs, patterns
4. ListSplitter - Splits list-like entities ("A and B")
5. OntologyNormalizer - Standardizes entity types

### 4. Generate Comparison Report

Compare baseline and pipeline-processed entities:

```bash
python scripts/reextraction/compare_extractions.py

# Also output metrics as JSON
python scripts/reextraction/compare_extractions.py --json
```

**Options**:
- `--baseline, -b` - Baseline file path (default: baseline_entities.json)
- `--results, -r` - Results file path (default: pilot_results.json)
- `--output, -o` - Output report path (default: comparison_report.md)
- `--json, -j` - Also output metrics as JSON

**Input**: `baseline_entities.json`, `pilot_results.json`
**Output**: `comparison_report.md`

## Full Workflow

```bash
# 1. Establish SSH tunnel (in separate terminal)
ssh -L 5433:localhost:5433 darren@202.61.196.119

# 2. Navigate to project
cd /Users/darrenzal/projects/RegenAI/koi-processor

# 3. Select documents (10 for testing, 100 for pilot)
python scripts/reextraction/select_pilot_documents.py --count 10

# 4. Extract baseline entities
python scripts/reextraction/extract_baseline_entities.py

# 5. Re-process through pipeline
python scripts/reextraction/reextract_pilot.py

# 6. Generate comparison report
python scripts/reextraction/compare_extractions.py

# 7. Review report
cat scripts/reextraction/comparison_report.md
```

## Output Files

After running all scripts:

| File | Description |
|------|-------------|
| `pilot_documents.json` | Selected documents with metadata |
| `baseline_entities.json` | Current entities from knowledge graph |
| `pilot_results.json` | Pipeline processing results |
| `comparison_report.md` | Detailed comparison analysis |

## Metrics Tracked

The comparison report includes:

- **Block Analysis**: Entities blocked by each pipeline module
- **Transformations**: Canonical resolutions, list splits, type normalizations
- **Type Analysis**: Entity type distribution before/after
- **Confidence Analysis**: Confidence score statistics
- **Tier Analysis**: Results by quality tier (high/medium/low)

## Notes

- Scripts do NOT perform LLM re-extraction - they process existing entities
- For true re-extraction with LLM, see the full re-extraction plan
- Test with 10 documents before running on full pilot set (100)
- Always maintain SSH tunnel while running scripts

## 2026-03-24 Model Bakeoff Notes

Two separate bakeoffs were run for the upcoming B8 contextual retrieval work:

1. **Proxy extraction bakeoff**
   - Report: `BAKEOFF_REPORT_20260324_205823.md`
   - Compared general extraction quality across `gpt-4o-mini`, `gemini-2.5-flash-lite`, `deepseek-chat`, and `gemini-3.1-flash-lite-preview`
   - Result: `gemini-3.1-flash-lite-preview` looked strongest on that proxy task, but the task shape did not match B8

2. **Direct contextual retrieval bakeoff**
   - Script: `contextual_bakeoff.py`
   - Report: `CONTEXTUAL_BAKEOFF_20260324_151212.md`
   - Raw results: `CONTEXTUAL_BAKEOFF_20260324_151212.json`
   - Method: reconstruct parent documents from chunked `koi_memories`, run the actual B8 prompt (`document + chunk -> 1-2 sentence context`), then use a blind `gemini-2.5-flash-lite` judge
   - Result: `gpt-4o-mini` beat `gemini-3.1-flash-lite-preview` `21-3` on 24 samples

**Decision for B8:** use `gpt-4o-mini` as the default context-generation model.

Why:
- The direct bakeoff matches the real contextual retrieval task
- `gpt-4o-mini` produced more specific, document-anchored snippets
- It was also cheaper in the direct run (`$0.0174` total generation cost vs `$0.0343` for Gemini)

Known operational note:
- Gemini sync works, but Gemini Batch is still unreliable in this environment, so it is not the default path for offline B8 generation

## Related Documentation

- `BACKUPS.md` - Backup and restore procedures
- `RE_EXTRACTION_PLAN.md` - Full re-extraction strategy
- `OPTION_A_REEXTRACTION_ULTRATHINK_PLAN.md` - Detailed 6-week plan
