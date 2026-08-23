# KOI Processor

**Knowledge Organization Infrastructure** — a sensor-to-agent pipeline that ingests
content, resolves and deduplicates entities, builds a knowledge graph, and serves
semantic + graph search to AI agents. PostgreSQL + pgvector is the authoritative store.

> **Two deployment surfaces — don't conflate them.** This repo serves both, and much of
> the historical docs describe the second:
>
> | | **personal-KOI** (primary, this branch) | **RegenAI public production** |
> |---|---|---|
> | Branch | `regen-prod` | `stable` |
> | Backend | `api/personal_ingest_api.py` @ **:8351** | event-bridge stack + `personal_ingest_api` |
> | DB | `personal_koi` (localhost:5432) | `eliza` (host, :5433) |
> | Embeddings | OpenAI `text-embedding-3-large` (**3072-dim**) | BGE `bge-large-en-v1.5` (1024-dim) live via `src/core/bge_server.py` |
> | Host | local (macOS launchd) + NUC | `$KOI_PROD_HOST` |
>
> `$KOI_PROD_HOST` is deliberately not written down in this public repo. Operators: it is in
> your local ops notes / SSH config. Set it in your shell, or use an `ssh` alias.
>
> When an older doc mentions `bge_server.py` / `koi_event_bridge_v2.py`, `eliza`, or ports
> 8090/8100, it's describing **RegenAI prod**. (Verified against live prod 2026-07-16.)

## Quick start

- **personal-KOI:** [`docs/operations/quickstart-personal-koi.md`](docs/operations/quickstart-personal-koi.md)
- **RegenAI event-bridge stack:** [`docs/operations/quickstart.md`](docs/operations/quickstart.md)
- **Deploy topology, service management, transport config:** see `CLAUDE.md` (§DEPLOY TOPOLOGY, §Personal KOI Backend).

Health check (personal-KOI): `curl -s http://localhost:8351/health`

## What's live

The personal-KOI backend (`api/personal_ingest_api.py`) mounts these routers:

- **Entity resolution** (`/entities`) — 4-tier dedup: exact → fuzzy (Jaro-Winkler) → semantic (pgvector) → create. Quality pipeline (confidence filter, canonical resolver, ontology normalizer). See [`docs/architecture/`](docs/architecture/).
- **Documents & ingestion** (`/documents`) — chunk → embed (OpenAI 3072-dim) → store, with parent/child provenance and versioning.
- **Knowledge graph** — `/project`, `/intents`, plus **graph traversal** (neighborhood / shortest-path / directed relationships) via PostgreSQL recursive CTEs (`api/graph_queries.py`).
- **Tasks** (`/tasks`) — always-on task registry (ingest/list/patch/stats).
- **KOI-net federation** (`/koi-net`) — peer onboarding, **vault sync** (E2EE, `VAULT_SYNC_ENABLED=true`), commons intake. See [`docs/operations/peer-onboarding.md`](docs/operations/peer-onboarding.md), [`scripts/federation/README.md`](scripts/federation/README.md).
- **Claims Engine** — impact-claim create / attest / verify / anchor on the Regen ledger. See [`docs/claims-engine-v1.md`](docs/claims-engine-v1.md).
- **Deep extraction** — entities + facts + discourse moves from documents (`scripts/extract_deep_documents.py`); model/transport is env-tunable (`DOC_EXTRACTOR_TRANSPORT` = `claude_p` | `api` | `openai`) with a repair loop and a batch wrapper (`scripts/run_batch_extract.sh`). See `CLAUDE.md` §Deep-extraction transport.
- **Sensors** — personal-KOI ingest jobs (substack, sessions, email, research papers, …) run as launchd jobs from the dedicated `~/projects/koi-processor-runtime` clone. See [`docs/integration/personal-sensor.md`](docs/integration/personal-sensor.md) and `CLAUDE.md` §Substack corpus ingestion.

## Architecture

PostgreSQL (pgvector) is authoritative for entities, documents, chunks, and relationships.
Ingestion resolves/dedupes entities, embeds chunks, and records provenance; retrieval fuses
semantic (vector) + keyword search and can expand along graph edges. Full detail (with the
BGE-vs-OpenAI embedding scoping note) in [`docs/architecture/overview.md`](docs/architecture/overview.md)
and [`docs/architecture/hybrid-rag.md`](docs/architecture/hybrid-rag.md).

```
sensors → ingest/resolve/embed → PostgreSQL (+pgvector)  → hybrid RAG + graph traversal → agents (MCP)
                                        │
                                        └─ Fuseki/SPARQL mirror (RegenAI prod)
```

## Project structure

```
api/            FastAPI backend (personal_ingest_api, routers/, graph_queries, embedding_provider)
scripts/        ingest, extraction, federation, migrations tooling, run_batch_extract.sh
src/core/       RegenAI event-bridge stack (bge_server.py, koi_event_bridge_v2.py) — prod only
config/         personal.env(.example) + *.example personal-config templates (see config/README.md)
migrations/     schema + migrations/baselines/ per-database manifests
docs/           architecture, operations, integration, claims, specs (+ archive/ historical)
tests/          contract + unit + smoke suites
```

## Configuration

Copy `config/personal.env.example` → `config/personal.env` (gitignored) and fill in
`POSTGRES_URL`, `OPENAI_API_KEY`, embedding model/dim. Personal choices (which
publications/feeds/authors to ingest, batch transport) use the committed `*.example` →
gitignored pattern — see [`config/README.md`](config/README.md). Load env with
`set -a; source config/personal.env; set +a`.

## API & schema reference

- REST endpoints: [`docs/operations/api.md`](docs/operations/api.md) (scope-annotated per surface)
- QueryPlan IR spec: [`docs/specs/b9a-query-plan-spec.md`](docs/specs/b9a-query-plan-spec.md)
- Isolated KOI tables + useful queries: [`docs/integration/personal-sensor.md`](docs/integration/personal-sensor.md)
- Changelog: [`docs/CHANGELOG.md`](docs/CHANGELOG.md)

## Experimental / parked (NOT deployed)

These are code-complete experiments behind flags, **not** running on any surface — don't
mistake them for live components:

- **TerminusDB graph mirror** (`TERMINUSDB_ENABLED=false`, no instance running anywhere;
  untouched since Feb 2026). Production graph traversal uses the PostgreSQL CTE path
  (`api/graph_queries.py`), not this mirror. Docs: [`scripts/terminusdb/README.md`](scripts/terminusdb/README.md).
- **Learning Field graph projection** (`scripts/project_bridge_notes.py`) — bridge-note →
  claims/concepts projection; run on demand, not a service.
- **Milestone B content tooling** (podcast/curator/weekly-aggregator under `src/content/`) —
  point-in-time deliverables; see `docs/archive/` for their records.

## Testing

```bash
BASE_URL=http://127.0.0.1:8351 pytest tests/test_contract.py -v -m core   # behavioral contract
pytest tests/                                                             # unit + integration
```

## Related repositories

- [gaiaaiagent/koi-sensors](https://github.com/gaiaaiagent/koi-sensors) — sensor implementations
- [gaiaaiagent/koi-research](https://github.com/gaiaaiagent/koi-research) — research & evals
- [gaiaaiagent/GAIA](https://github.com/gaiaaiagent/GAIA) — Eliza AI agent framework

## License

MIT — see `LICENSE`.

---

*Historical status logs, dated milestone records, and phase-by-phase notes previously in this
README now live in `docs/` (esp. `docs/CHANGELOG.md` and `docs/archive/`). This README is the
concise front door; the repo `CLAUDE.md` is the operator briefing.*
