# Cross-source discourse links (issue #60, Stage 1)

> **Migrations 133 and 134 are APPLIED on `personal_koi`. Do not reuse either number.**
> - `133_discourse_link_candidates`: applied 2026-10-19 23:37 PDT.
> - `134_discourse_discovery_jobs`: applied 2026-10-20 11:21:29 PDT.
>
> Numbers also taken elsewhere, as of 2026-10-20:
> - 126: shared `regen-prod`;
> - 127: PR #70;
> - 128: branch `div9-landing`;
> - 132: PR #81, applied on `personal_koi`.
>
> Before numbering a migration, check branches, open PRs and `koi_migrations`.

## What it does

A thorough-tier ingestion can queue a job that looks for claims in *other* sources resembling each of the new source's discourse moves. It uses keyword and local-embedding search fused by rank (`hybrid-rrf/v0`). It records the matches as **candidates**.

- A candidate is a proposal to inspect. Similarity establishes no identity, support, contradiction or independent corroboration.
- A candidate becomes a relation only through an explicit, authorized judgment. Judgments are disabled outside scratch databases unless `KOI_DISCOURSE_JUDGMENTS=1`, and the review router is not mounted.
- Every candidate keeps both ends' sources (rid, title, URL), both claims' exact text (by representation hash), and how each claim is bound to its source version.
- A candidate is admissible only when both ends carry an extraction binding recorded with the extraction input. Claims extracted before bindings existed never become admissible retrospectively.

## Ingest a source with discovery

```bash
cd ~/projects/koi-processor-runtime && set -a && source config/personal.env && set +a
venv/bin/python scripts/ingest_document.py --source-path <markdown> --tier thorough --discourse-discovery \
  --source-url <canonical url> --name "<title>" --gate-evidence-out <gate.json>
```

- **The option:** `--discourse-discovery` requires `--tier thorough`. It sets `KOI_DISCOURSE_BINDINGS` and `KOI_DISCOURSE_INGEST_DISCOVERY` for that process only.
- **What it adds:** the extraction records each move's binding to the source version it read, and, once the extraction is complete, inserts one discovery job. The summary prints `discovery job: {...}`. Retrieval never runs inside the ingestion.
- **Without the option:** ingestion is exactly as before. No binding is recorded and no job is queued.
- **Never queued:** sources of excluded corpora (`EXCLUDED_SOURCE_PATTERNS`, the held wiki material) and moves without a binding captured with their input.
- **Held material:** do not ingest it with this option.

## The worker

- **Scheduled:** `scripts/run_discourse_discovery.sh` makes one pass that claims at most one job. LaunchAgent `com.personal-koi.discourse-discovery` (`scripts/com.personal-koi.discourse-discovery.plist`) runs it every 15 minutes, with no KeepAlive and no RunAtLoad.
- **By hand:** `scripts/run_discourse_discovery.sh`.
- **Steps for each job:**
  1. **Check the binding:** source revision, extraction run and input digest, claims, caller context. If any changed, the job becomes `stale`.
  2. **Plan:** in one READ ONLY transaction, with local Ollama embeddings only.
  3. **Apply:** one short transaction, with the writer lock, a 2 s lock timeout and a write-scope check. The candidates, the run row (`ingest:<job>:<attempt>`) and the job's completion commit together.
- **Limits** (`api/discourse_discovery.py` `LIMITS`, stored with each job):
  - 200 moves;
  - 3 attempts, 1 replan;
  - planning 600 s, apply 60 s;
  - lease 900 s;
  - backoff 60, 300 and 1,800 s.

### Seeing pending, failed and stale jobs

- `koi-discourse jobs` (read-only) lists every job with status, attempts, last error, run, run status, outputs (`present` or `rolled_back`) and candidate count. It also lists extractions that recorded bindings but have no job.
- `logs/discourse-discovery-state.json` is rewritten after every scheduled pass. It holds what the pass did, the queue by status, and every failed or stale job with its error under `needs_attention`.
- `logs/discourse-discovery.log` and `.error.log` hold the pass output and launchd errors.

Failed and stale jobs are not retried past their limits. A newer extraction of the same source queues its own job.

## Query (Claude Code, Codex, people)

Install once:

```bash
ln -sfn ~/projects/koi-processor-runtime/scripts/koi-discourse ~/.local/bin/koi-discourse
```

```bash
koi-discourse candidates --query "cognitive glue" --limit 5          # moves matching the text, with their candidates
koi-discourse candidates --move-id <uuid>                            # one move
koi-discourse candidates --run-id ingest:1:1 --limit 20              # what one run found
koi-discourse jobs --source-rid document:<sha256>
```

- **Read-only:** both commands run in one READ ONLY transaction and print JSON. The top-level `label` says what candidates are.
- **Results:** each result has `move` (the claim and its source), `admitted_relations` (reviewed judgments; empty while judgments are disabled) and `candidates`.
- **Each candidate:** `label: candidate (unreviewed)`, the legs that found it, scores, `other` (the other claim and its source), `admissible_now` and its blockers.
- **Reporting:** quote both sources and the label when reporting a candidate. Do not report it as support or agreement.

## Backout

- **Supported backout:** operational rollback of one run. It undoes the run's owned outputs (candidates, vectors, unreferenced claim texts), keeps the job, run and effect rows as history (run status `rolled_back`), and never touches the ingested document or its bindings.
  ```bash
  venv/bin/python -B scripts/discourse_links_canary.py rollback --dsn <dsn> --run-id ingest:<job>:<attempt> --apply
  ```
- **Schema removal:** `migrations/134_discourse_discovery_jobs_down.sql` is destructive and separate. It refuses while ingestion run history exists.

## Files

- **Library:**
  - `api/discourse_links.py`: visibility, representations, bindings, retrieval, plan and apply, review;
  - `api/discourse_discovery.py`: jobs and status.
- **Scripts:**
  - `scripts/discourse_discovery_worker.py`: run, status;
  - `scripts/discourse_links_canary.py`: manifest canary, rollback, `candidates`;
  - `scripts/extract_deep_documents.py`: binding capture and the job hook;
  - `scripts/ingest_document.py`: `--discourse-discovery`.
- **Schema and tests:**
  - `migrations/133_*`, `migrations/134_*`;
  - `tests/test_discourse_links.py`;
  - `tests/test_extractor_entity_link_survivors.py`.
