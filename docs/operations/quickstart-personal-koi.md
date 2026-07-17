# Personal-KOI Quick Start

The **personal-KOI** surface: a single-user knowledge graph backend (Obsidian vault +
sessions + sensor corpora) served by `api/personal_ingest_api.py` on **port 8351**, backed
by PostgreSQL **`personal_koi`** with pgvector, embedding via **OpenAI
`text-embedding-3-large` (3072-dim)**.

> This is distinct from the RegenAI public-production event-bridge stack (see
> `quickstart.md` / `deployment.md`, which describe branch `stable`, the `eliza` DB, and
> BGE-1024). Don't mix the two.

## Prerequisites

- Python 3.11+, a virtualenv (repo `venv/` or a shared one, e.g. `~/venvs/koi-server`)
- PostgreSQL 14+ with the `pgvector` extension (≥0.8 for 3072-dim `halfvec`/HNSW)
- An `OPENAI_API_KEY` (embeddings; extraction can use a different transport — see below)

## 1. Config

```bash
cp config/personal.env.example config/personal.env   # gitignored; fill in your values
# minimum: POSTGRES_URL=postgresql://<user>@localhost:5432/personal_koi
#          OPENAI_API_KEY=sk-...
#          EMBEDDING_MODEL=text-embedding-3-large   EMBEDDING_DIMENSION=3072
```

Env is loaded with `set -a; source config/personal.env; set +a` (exports to child
processes). See `config/README.md` for the full variable list, incl. the optional
`DOC_EXTRACTOR_*` deep-extraction transport knobs and the `*.example` personal-config
pattern.

## 2. Database

```bash
createdb personal_koi
psql personal_koi -c "CREATE EXTENSION IF NOT EXISTS vector;"
# apply migrations (see migrations/ + migrations/baselines/personal_koi.json)
bash scripts/setup.sh      # or run the migration runner your setup uses
```

## 3. Run the backend

**Dev (foreground):**
```bash
set -a; source config/personal.env; set +a
venv/bin/python -m uvicorn api.personal_ingest_api:app --host 127.0.0.1 --port 8351
```

**Operator (macOS launchd service — the normal path):** the backend runs as
`com.personal.koi-processor` (foreground + KeepAlive), started from the
`koi-processor-service` checkout via `~/.config/personal-koi/start.sh`. Do NOT launch a
second raw `uvicorn` — it races on port 8351. Manage it with:
```bash
~/.config/personal-koi/restart.sh                    # cycle the service (waits for /health)
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.personal.koi-processor.plist  # cold start
```

## 4. Verify

```bash
curl -s http://localhost:8351/health       # {"status":"healthy", "embedding_model":"text-embedding-3-large", "embedding_dimension":3072, ...}
curl -s http://localhost:8351/tasks/stats  # confirms the task router is mounted
```

## 5. Sensors (optional)

Personal-KOI ingests via launchd sensor jobs (`com.personal-koi.*`) that run from the
dedicated **`~/projects/koi-processor-runtime`** clone pinned to `regen-prod` — NOT the dev
checkout. See the repo `CLAUDE.md` DEPLOY TOPOLOGY and "Substack corpus ingestion", and
`docs/integration/personal-sensor.md` for the sensor roster. To update sensor code: commit
to `regen-prod`, then `git -C ~/projects/koi-processor-runtime pull`.

## 6. Deep extraction (entities/facts/discourse)

`scripts/extract_deep_documents.py` builds the graph from ingested docs. Model/transport is
env-tunable (`DOC_EXTRACTOR_TRANSPORT` = `claude_p` default | `api` | `openai`); extraction
is serialized by one global advisory lock, so run it **sequentially, never as parallel
agents**. One-off batches on an alternate model go through `scripts/run_batch_extract.sh`.
See the `CLAUDE.md` "Deep-extraction transport" section + the
`reference_koi_extraction_model_tiering` memory.
