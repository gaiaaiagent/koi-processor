# KOI Processor v2

🚀 **Production-Ready Knowledge Organization Infrastructure Pipeline**

✅ **Provenance System Enhanced (Sept 27, 2025)**: Complete parent-child document relationships with full URL preservation through provenance chain.

✅ **Text Extraction Fixed (Sept 14, 2025)**: Pipeline now processing 100% clean, uncorrupted text after fixing sensor extraction issues.

A comprehensive sensor-to-agent pipeline that processes real-time content from KOI sensors, generates embeddings, handles deduplication and versioning, and provides immediate semantic search capabilities for AI agents.

## 📋 Table of Contents
- [Overview](#overview)
- [Architecture](#architecture)
- [Key Features](#key-features)
- [Phase 1 TerminusDB Integration](#phase-1-terminusdb-integration)
- [Federation Bootstrap (Blank Host)](#federation-bootstrap-blank-host)
- [KOI-net Vault Sync Roadmap](#koi-net-vault-sync-roadmap)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [API Documentation](#api-documentation)
- [Database Schema](#database-schema)
- [Personal Sensor Integration](#personal-sensor-integration)
- [Testing](#testing)
- [Deployment](#deployment)
- [Troubleshooting](#troubleshooting)

## Overview

The KOI Processor is the central processing hub of the Knowledge Organization Infrastructure (KOI) ecosystem. It receives events from distributed sensors, processes content into searchable embeddings, and makes knowledge immediately available to AI agents through semantic search.

## Phase 1 TerminusDB Integration

Phase 1 adds an optional TerminusDB graph mirror behind an outbox pattern:

- PostgreSQL remains authoritative for entity resolution and ingestion.
- Writes enqueue outbox rows in the same PG transaction.
- A background worker drains outbox rows to TerminusDB with retries/backoff.
- API graph endpoints expose conflict inspection and sync health.

Implementation docs and runbook:

- [`scripts/terminusdb/README.md`](scripts/terminusdb/README.md)
- [`scripts/terminusdb/smoke_phase1.sh`](scripts/terminusdb/smoke_phase1.sh)
- [`migrations/048_terminusdb_outbox.sql`](migrations/048_terminusdb_outbox.sql)

## Graph Traversal Endpoints (Phase A)

PostgreSQL recursive CTE-based graph traversal for the personal knowledge graph. Enables indirect connection discovery beyond point lookups.

### Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /relationships/{entity_uri}` | 1-hop relationships (now with `direction` param: `both`/`incoming`/`outgoing`) |
| `GET /graph/neighborhood/{entity_uri}` | Multi-hop neighborhood traversal (BFS, max depth 4) |
| `GET /graph/shortest-path?source=...&target=...` | Shortest path between two entities (BFS, max depth 8) |

### Safety Caps

| Cap | Default | Max |
|-----|---------|-----|
| `max_depth` (neighborhood) | 2 | 4 |
| `max_depth` (shortest-path) | 6 | 8 |
| `max_nodes` | 200 | 500 |
| `max_edges` | 1000 | 2000 |
| Query timeout | 5s | — |

### Example Usage

```bash
# Neighborhood (2-hop)
curl -s 'localhost:8351/graph/neighborhood/orn:personal-koi.entity:person-darren-zal-42986b9bf8c0?max_depth=2'

# Shortest path
curl -s 'localhost:8351/graph/shortest-path?source=orn:...&target=orn:...'

# Directed relationships
curl -s 'localhost:8351/relationships/orn:...?direction=outgoing'
```

Auth: Restricted to localhost and WireGuard mesh (10.100.0.0/24). Tests: `tests/test_graph_traversal.py` (21 isolated fixture tests), `tests/test_graph_traversal_smoke.py` (12 live-DB smoke tests).

## Federation Bootstrap (Blank Host)

Federation onboarding scripts now support near one-command bootstrap for new peer machines (Ubuntu/macOS), including prerequisites, DB/venv setup, join-request generation, and validation.

Runbook:

- [`scripts/federation/README.md`](scripts/federation/README.md)

Primary bootstrap command (peer host):

```bash
cd scripts/federation
./bootstrap-node.sh --yes <node-name> <wireguard-ip> [koi-processor-path]
```

Post-setup validation:

```bash
./validate-node.sh --expect-wg-ip <wireguard-ip> --strict
```

Federation smoke validation (bidirectional share):

```bash
# Node A -> Node B
curl -sS -X POST http://<node-a-wg-ip>:8351/koi-net/share \
  -H 'Content-Type: application/json' \
  -d '{
    "document_rid": "orn:personal-koi.testdoc:smoke-a-to-b-<ts>",
    "recipient": "<peer-alias-on-node-a>",
    "message": "smoke a->b",
    "contents": {"title":"smoke","body":"federation test"}
  }'

# Verify on Node B (received inbox)
curl -sS "http://<node-b-wg-ip>:8351/koi-net/shared-with-me?from_peer=<peer-alias-on-node-b>&limit=10"
```

Note: `since` on `/koi-net/shared-with-me` is ISO-8601 datetime (example: `2026-02-25T20:22:00Z`).

## KOI-net Vault Sync

Bidirectional markdown-folder sync between KOI-net peers. Phase Sync-1 validated (two-peer 15/15). Phase Sync-1.5 (hardening) implementation complete, 72h soak in progress.

Canonical roadmap: [`docs/planning/KOI_NET_VAULT_SYNC_ROADMAP.md`](docs/planning/KOI_NET_VAULT_SYNC_ROADMAP.md)

### Operational Flags

| Var | Default | Purpose |
|-----|---------|---------|
| `VAULT_SYNC_ENABLED` | false | Enable vault sync subsystem |
| `VAULT_SYNC_FOLDER` | Shared | Subfolder within vault to sync |
| `VAULT_SYNC_WATCHER` | true | File watcher for low-latency change detection (fails open) |
| `VAULT_SYNC_REPAIR_ENABLED` | false | Gate for reconcile repair mode (keep disabled until post-soak) |
| `VAULT_SYNC_MAX_FILES_PER_SCAN` | 100 | Backpressure: file cap per cycle |
| `VAULT_SYNC_MAX_BYTES_PER_SCAN` | 10MB | Backpressure: byte cap per cycle |
| `VAULT_SYNC_MAX_EVENTS_PER_SCAN` | 200 | Backpressure: event cap per cycle |
| `VAULT_SYNC_DELETE_EVENT_RESERVE` | 50 | Min budget reserved for delete events |
| `VAULT_SYNC_WATCHER_DEBOUNCE_MS` | 500 | Debounce window for editor saves |

### Default-safe production settings

```bash
VAULT_SYNC_ENABLED=true
VAULT_SYNC_FOLDER=Shared
VAULT_SYNC_REPAIR_ENABLED=false  # enable after soak
# All other flags at defaults
```

### Key endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/koi-net/vault-sync/status` | GET | Dashboard: metrics, pending events, rejected counts |
| `/koi-net/vault-sync/trigger` | POST | Force immediate scan cycle |
| `/koi-net/vault-sync/configure` | POST | Set sync peer |
| `/koi-net/vault-sync/reconcile` | POST | Drift detection (`{"mode":"detect"}`) or repair |

All vault-sync endpoints require localhost + admin token.

## Domain Event Federation

Full database replication between KOI-net peers via protocol-native FUN events. Mutation endpoints emit domain events; receiving nodes apply them with UPSERT semantics for near-realtime (~60s) delta sync.

Supported domains: `entity`, `task`, `claim`, `attestation`, `commitment`, `commitment_pool`.

Key files:
- `api/federation_events.py` — Producer singleton (`FederationEvents.emit()`)
- `api/domain_event_handlers.py` — Consumer UPSERT handlers (entity, task, claim, attestation, commitment, pool)
- `api/event_queue.py` — Event queue with `_koi_domain` bypass for `rid_types` filter
- `tests/test_federation_bridge.py` — 24 automated tests

Docs: [`scripts/federation/README.md`](scripts/federation/README.md) (Domain Event Bridge section)

## Claims Engine

Lightweight, ledger-anchored impact claim system with Regen Ledger testnet integration.

### Features

- Verification state machine: `self_reported → peer_reviewed → verified → ledger_anchored`
- Content-addressable RIDs (BLAKE2b-256 hash)
- Entity graph integration (claims as first-class KOI entities)
- AI-powered claim extraction from documents
- Live ledger anchoring via `regen` CLI with async 202 pending responses
- Reconciliation endpoint for blockchain drift detection

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | /claims/ | Create claim |
| GET | /claims/{rid} | Get claim with evidence |
| GET | /claims/ | Search/filter claims |
| PATCH | /claims/{rid}/verify | Advance verification level |
| POST | /claims/{rid}/evidence | Attach evidence entity |
| POST | /claims/{rid}/anchor | Anchor on Regen Ledger (200 success or 202 pending) |
| POST | /claims/{rid}/reconcile | Check on-chain status of pending broadcast |

### Testing

```bash
# Monkeypatched pytest (no live network needed)
pytest tests/test_claims_reconcile.py -v

# HTTP smoke suite (requires running server)
python -m scripts.test_claims_api --base-url http://localhost:8351
```

Docs: [`docs/claims-engine-v1.md`](docs/claims-engine-v1.md)

### What's New in v2
- ✅ **RID-based Deduplication**: Prevents duplicate content ingestion
- ✅ **Version Control**: Tracks content updates with full audit trail
- ✅ **CAT Receipts**: Complete provenance tracking for all transformations
- ✅ **Isolated Tables**: Separates sensor data from scraped content
- ✅ **Parent-Child Documents**: Hierarchical relationships for forum topics and posts
- ✅ **Provenance API**: Navigate document lineage via `/api/koi/graph/provenance/{rid}`
- ✅ **Enhanced Curators**: Daily/Weekly content curation with proper URL attribution
- ✅ **Production Embeddings**: Model-agnostic embedding server (currently BGE-large-en-v1.5)
- ✅ **MCP Integration**: Semantic search via Model Context Protocol
- ✅ **Content Operations Dashboard**: Web-based monitoring for Daily Bot and Weekly Digest
- ✅ **Daily Content Curator**: LLM-enhanced daily X posts with comprehensive ledger integration
- ✅ **Weekly Aggregator**: AI-powered weekly digest with on-chain activity tracking

- ✅ **Code Graph Service**: Automatic code entity extraction into Apache AGE graph
- ✅ **Knowledge Graph Quality Improvement** (Dec 2025): Modular post-processing pipeline with regression suite and 99.7% quality (up from 62%)

### 🎯 Knowledge Graph Quality (Dec 2025)

**Status**: ✅ PRODUCTION DEPLOYMENT v1.1 | Quality: 99.7% | Tests: KG regression suite passing | Dedup: 70.10%

Comprehensive three-phase quality improvement project completed successfully:

**Phase 1-2: Quality Filters & Pipeline** ✅
- **62% → 99.7% quality** improvement
- **Modular Pipeline Framework**: 6 operational modules (ConfidenceFilter, DocumentLevelDeduplicator, CanonicalResolver, OntologyNormalizer, ListSplitter, EntityQualityFilter)
- **Regression Suite**: Targeted tests for KG stability
- **Production Deployment**: Zero errors, < 1% performance overhead

**Phase 3: Cross-Document Deduplication** ✅ COMPLETE
- **Entity Deduplication System** with three-tier waterfall:
  - Tier 1 (Exact): B-Tree index match (~microseconds) - 58.0% hit rate
  - Tier 2 (Semantic): pgvector HNSW + OpenAI embeddings (~milliseconds) - 10.6% hit rate
  - Tier 3 (New): Insert new entities - 31.4% new entities
- **Production Stats** (as of 2025-12-11, pre-Stage 6 re-extraction):
  - **12,985 unique entities** from 43,430 raw entity mentions
  - **70.10% deduplication rate** (target: 65-75%)
  - **64,925 RDF triples** (Fuseki knowledge graph deployed)
  - **Zero type collisions** (all type mismatches resolved)
  - **Zero placeholder entities** (all "Unknown"/"Anonymous" removed)
  - **Zero errors** in production
- **Quality Improvements**:
  - JIRA IDs: 509 → 0 (100% eliminated)
  - Template text: 444 → 0 (100% eliminated)
  - Chunk repetition: 95% reduction
  - Type consolidation: 678 entities merged
- **Code Quality**: A+ grade (expert reviewed)

### Stage 6 Re-Extraction + Code Bridge (Dec 2025)

**Stage 6** rebuilds the semantic KG from a **docs-only corpus** (Notion + Discourse + Website + GitHub/GitLab markdown), using **Gemini** extraction and PostgreSQL as the authoritative store. Fuseki is rebuilt from PostgreSQL after completion.

**Code Bridge** enables joinable semantics and code structure:
- `koi_code_artifacts`: canonical code entities (exported from code graph provenance)
- `koi_doc_code_links`: doc → code links (MENTIONS edges preserved)
- `entity_registry.metadata.code_uri`: entity-level code links (post-Stage 6)
- AGE stub sync for single-query access to semantic anchors

Key scripts:
- `scripts/reextraction/stage6_canary_gemini.py`
- `scripts/reextraction/stage6_full_reextract_gemini.py`
- `scripts/code_bridge/export_code_artifacts.py`
- `scripts/code_bridge/link_docs_to_code.py`
- `scripts/code_bridge/link_entities_to_code.py`
- `scripts/code_bridge/sync_stubs_to_age.py`

**Quick Start**:
```python
from knowledge_graph.graph_integration import KnowledgeGraphIntegrator

# With pipeline and deduplication
kg = KnowledgeGraphIntegrator(
    use_pipeline=True,
    use_entity_resolver=True  # Enable deduplication
)
valid_entities = kg.process_entities_batch(entities)
```

**Documentation**:
- [PRODUCTION_DEPLOYMENT_SUMMARY.md](PRODUCTION_DEPLOYMENT_SUMMARY.md) - Production deployment status & rollback procedures
- [prompts/ALL_PROMPTS_SUMMARY.md](prompts/ALL_PROMPTS_SUMMARY.md) - Complete project workflow
- [CLAUDE.md](CLAUDE.md) - Current project context

---

## Learning Field Graph Projection

Projects bridge notes from [Spore](https://github.com/DarrenZal/spore) and [Intelligence Commons](https://github.com/DarrenZal/intelligence-commons) into the KOI knowledge graph as an argumentative learning layer. Built on top of the Claims Engine.

**Two claim layers:**
- **Source claims** — extracted from bridge note Claim Registers, linked to Concepts via `about`
- **Review claims** — proposed canon changes, linked to source claims via `supports`/`opposes`

**Scripts:**
- `scripts/project_bridge_notes.py` — Projection pipeline (`--dry-run` or `--apply`)
- `scripts/verify_learning_field.sql` — Verification checklist (`psql personal_koi -f scripts/verify_learning_field.sql`)

**Design:** Idempotent (re-runs produce 0 duplicates). Claim versioning via `supersedes_rid` when bridge notes are edited. Disposition-based stance detection ("no change" → opposes). Cross-project concept dedup via entity resolution. Full provenance (`metadata.source = 'learning_field'`).

See [the shared learning field plan](https://github.com/DarrenZal/spore/blob/main/.claude/plans/shared-learning-field-for-spore-and-ic.md) for architecture details.

---

## Project Structure

```
koi-processor/
├── src/                    # Source code
│   ├── core/              # Core KOI processing
│   │   ├── koi_event_bridge_v2.py
│   │   ├── koi_types.py
│   │   ├── koi_permissions_api.py
│   │   └── bge_server.py
│   ├── content/           # Content generation & monitoring
│   │   ├── content_dashboard.py
│   │   ├── daily_curator_llm.py
│   │   ├── weekly_curator_llm.py
│   │   └── quality_control.py
│   ├── audio/             # Podcast generation
│   │   ├── audio_pipeline_enhanced.py
│   │   └── podcast_integration.py
│   ├── knowledge_graph/   # Knowledge graph & entity processing
│   │   ├── postprocessing/
│   │   │   ├── pipeline.py          # Pipeline framework
│   │   │   └── modules/             # Processing modules
│   │   ├── entity_resolver.py       # Entity deduplication (3-tier)
│   │   ├── uri_generator.py         # Deterministic URI generation
│   │   └── graph_integration.py     # Fuseki integration
│   ├── services/          # External service integrations
│   │   ├── regen_ledger.py
│   │   └── regen_ledger_comprehensive.py
│   └── utils/             # Utilities & helpers
├── scripts/               # Operational scripts
│   ├── setup.sh          # One-command setup
│   ├── run_migrations.sh # Database migrations
│   ├── run_daily_curator.py
│   └── code_bridge/      # Code/semantic bridge scripts
├── migrations/            # Database migrations
├── docs/                  # Documentation
├── tests/                 # Test suite (use targeted KG regression subset)
├── prompts/               # Project planning & documentation
│   ├── PROMPT_1-23_*.md  # Active prompts
│   └── archive/          # Superseded prompts
├── config/                # Configuration files
├── static/                # Dashboard static files
├── templates/             # Dashboard templates
└── requirements.txt       # Python dependencies
```

## Architecture

```
DATA INGESTION PIPELINE:
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│ KOI Sensors │────▶│ Coordinator  │────▶│ Event Bridge │
│  (Various)  │     │  (Port 8005) │     │  (Port 8100) │
└─────────────┘     └──────────────┘     └──────────────┘
                                                   │
                                    ┌──────────────┴──────────────┐
                                    │                             │
                                    ▼                             ▼
                            ┌──────────────┐            ┌──────────────────┐
                            │  Embedding   │            │ Entity Extractor │
                            │   Server     │            │  (JSON-LD/RDF)   │
                            │  (Port 8090) │            │    [PLANNED]     │
                            └──────────────┘            └──────────────────┘
                                    │                              │
                                    ▼                              ▼
                            ┌──────────────┐            ┌──────────────────┐
                            │ PostgreSQL   │            │  Apache Jena     │
                            │  (pgvector)  │            │    Fuseki        │
                            │ • Embeddings │            │  (Port 3030)     │
                            └──────────────┘            │ • RDF Triples    │
                                                        │ • Ontologies     │
                                                        └──────────────────┘

QUERY/ACCESS LAYER:
                            ┌──────────────────────────────────────┐
                            │      Hybrid RAG API (Port 8301)      │
                            │  • Reciprocal Rank Fusion (RRF)      │
                            │  • BGE Semantic Search               │
                            │  • Adaptive Extraction Triggers      │
                            └──────────────────────────────────────┘
                                              │
                            ┌─────────────────┴──────────────────┐
                            │                                     │
                            ▼                                     ▼
                   ┌──────────────────┐             ┌──────────────────┐
                   │  MCP Knowledge   │             │   GAIA React     │
                   │  Server          │             │   Frontend       │
                   │  (Port 8200)     │             │ (Port 3000 web)  │
                   └──────────────────┘             └──────────────────┘

QUERY/ACCESS LAYER:
                            ┌───────────────────────────────────┐
                            │         PostgreSQL                │
                            │  • koi_memories (KOI knowledge)   │
                            │  • koi_embeddings (pgvector)      │
                            │  • memories (agent state)         │
                            │  • conversations (agent history)  │
                            └───────────────────────────────────┘
                                               │
                                               ▼
                            ┌─────────────────────────────────┐
                            │         Eliza Agents            │
                            │        (5 AI Agents)            │
                            │                                 │
                            │ • Direct SQL for state          │
                            │ • MCP tools for external data   │
                            └─────────────────────────────────┘
                                     ▲             ▲
                                     │             │
                        ┌────────────┴───┐    ┌────┴──────────┐
                        │ Knowledge MCP  │    │  Regen MCP    │
                        │     Server     │    │    Server     │
                        │                │    │               │
                        │ Routes to:     │    │ Connects to:  │
                        │ • PostgreSQL   │    │ • Regen       │
                        │   (pgvector)   │    │   Ledger      │
                        │ • Apache Jena  │    │ • Blockchain  │
                        │   Fuseki       │    │   data        │
                        └────────────────┘    └───────────────┘
                                ▲                     ▲
                                │                     │
                        ┌───────┴────────┐            │
                        │                │            │
                  PostgreSQL      Apache Jena    Regen Ledger
                  (pgvector)        Fuseki       (Blockchain)
```

### Component Description

#### Data Ingestion Pipeline:
1. **KOI Sensors**: Monitor websites, documents, and other sources
2. **KOI Coordinator** (`port 8200`): Routes events to processing pipeline
3. **KOI Event Bridge v2** (`port 8100`): Distributes content to processors
   - Handles deduplication, versioning, chunking
   - Routes to both embedding and entity extraction paths

4. **Embedding Server** (`port 8090`): Generates semantic embeddings
   - Currently using BAAI/bge-large-en-v1.5 (1024 dimensions)
   - Model-agnostic API allows swapping to other models
   - Stores embeddings in PostgreSQL pgvector

5. **Entity Extractor** (PLANNED): Extracts structured data
   - Processes content into JSON-LD/RDF format
   - Extracts entities, relationships, and ontological information
   - Uses unified metabolic ontology (36 classes)
   - Loads RDF triples directly into Apache Jena

#### Storage Layer:
6. **PostgreSQL**: Dual-purpose database
   - Stores KOI knowledge (koi_memories, koi_embeddings with pgvector)
   - Stores agent state (memories, conversations, relationships)

7. **Apache Jena Fuseki** (`port 3030`): SPARQL triplestore
   - Stores RDF triples and OWL ontologies
   - Populated by Entity Extractor (when implemented)
   - Handles complex ontological/semantic reasoning queries

#### Query/Access Layer:
8. **Knowledge MCP Server**: KOI knowledge query API for agents
   - Routes semantic searches to PostgreSQL pgvector
   - Routes ontological queries to Apache Jena Fuseki
   - Provides unified knowledge interface via stdio transport

9. **Regen MCP Server**: Blockchain data API for agents
   - Connects to Regen Ledger blockchain
   - Provides access to on-chain data (carbon credits, ecological state, etc.)
   - Handles blockchain queries and transactions
   - Separate from knowledge infrastructure

10. **Eliza Agents**: Three connection patterns
    - **Direct PostgreSQL**: For agent state, conversations, memories
    - **Via Knowledge MCP**: For KOI knowledge queries (embeddings and ontologies)
    - **Via Regen MCP**: For blockchain/ledger queries

## Key Features

### 🔄 Deduplication & Versioning
- **RID-based tracking**: Each document has a unique Resource Identifier
- **Version control**: UPDATE events create new versions, preserving history
- **Audit trail**: Complete provenance tracking with CAT receipts

### 🧬 Smart Processing
- **Intelligent chunking**: 1000 chars with 200 char overlap
- **Multi-format support**: Handles JSON, HTML, plain text
- **Event types**: NEW, UPDATE, FORGET with appropriate handling

### 🔍 Semantic Search
- **Production embeddings**: State-of-the-art semantic vectors
- **MCP integration**: Standard protocol for agent tool use
- **Permission filtering**: Agent-specific content access control

### 📊 Isolated Storage
- **Dual-table pattern**: `koi_memories` for source documents, `memories` for chunked content
- **No contamination**: Clean separation of data sources
- **Migration support**: Gradual transition from legacy systems
- **Full documentation**: See [STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md) for details

### 🗓️ RDF Date Enrichment (publishedAt)
- Export publication dates from DB to JSON mapping:
  - `node scripts/export_published_map.js` (uses `POSTGRES_URL`, writes to `src/core/published_map.json` by default)
- Refine RDF graph with `regx:publishedAt` and load into Jena:
  - `bash scripts/refine_with_published.sh` (uses `CONSOLIDATION_PATH`, `PUBLISHED_MAP_PATH`, `JENA_DATA_ENDPOINT`)
- Enables SPARQL date gating in MCP when present.

## Quick Start

### Prerequisites
- Python 3.8+
- PostgreSQL with pgvector extension (or Docker)
- 4GB+ RAM recommended

### Installation

## Setup

### Quick Start (No Google Drive)
```bash
./scripts/setup.sh
```

### With Google Drive Integration
1. Run setup:
   ```bash
   ./scripts/setup.sh
   ```

2. Configure OAuth (see [config/README.md](config/README.md)):
   ```bash
   cp /path/to/client_secret.json config/client_secret.json
   ```

3. Service account setup (for automated ingestion):
   - Share Google Drive folders with: `rag-ingestion-bot@koi-sensor.iam.gserviceaccount.com`
   - Grant "Viewer" access

## OAuth Endpoints

Once configured, users authenticate at:
- Auth URL: `https://your-domain.com/api/koi/auth/initiate`
- Callback: `https://your-domain.com/api/koi/auth/callback`
- Status: `https://your-domain.com/api/koi/auth/status`

1. **Clone and setup**:
```bash
cd /opt/projects/koi-processor
git pull origin regen-prod
bash scripts/setup.sh
```

2. **Configure environment**:
```bash
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY (required for podcast generation)
```

3. **Run migrations**:
```bash
bash scripts/run_migrations_with_backup.sh
```

4. **Start services**:
```bash
# Start core KOI services
bash scripts/start_all_services.sh

# Start Hybrid RAG API (required for semantic search)
cd /opt/projects/koi-processor && bun koi-query-api.ts &

# Start MCP Knowledge Server (for agent access)
source venv/bin/activate && python3 src/core/koi_knowledge_mcp_server.py &
```

**Service Ports:**
- Port 8005: KOI Coordinator
- Port 8090: BGE Embedding Server
- Port 8100: Event Bridge
- Port 8200: MCP Knowledge Server
- Port 8301: Hybrid RAG API (semantic search)
- Port 8400: Content Dashboard

5. **Access dashboard**:
Open http://localhost:8400 in your browser

### Web Access Setup (HTTPS)

To set up HTTPS access at `https://regen.gaiaai.xyz/digests`:

```bash
# Run the nginx setup script
sudo bash /opt/projects/koi-processor/setup_nginx_digests.sh
```

This will:
- Install nginx (if needed)
- Configure SSL with Let's Encrypt
- Set up proxy from https://regen.gaiaai.xyz/digests to localhost:8400
- Enable WebSocket support for real-time updates

After setup, the dashboard will be accessible at: **https://regen.gaiaai.xyz/digests**

```bash
# Clone the repository
git clone https://github.com/yourusername/koi-processor.git
cd koi-processor

# Run the setup script
chmod +x scripts/setup.sh
./scripts/setup.sh
```

The setup script will:
- ✅ Check Python version
- ✅ Create virtual environment
- ✅ Install all dependencies
- ✅ Set up PostgreSQL (optionally with Docker)
- ✅ Run database migrations
- ✅ Create configuration files
- ✅ Set up the monitoring dashboard

## Running the System

### 1. Start the Monitoring Dashboard
```bash
source venv/bin/activate
python src/content/content_dashboard.py
# Open http://localhost:8400 in your browser
```

### 2. Run Daily Content Curator
```bash
source venv/bin/activate
python scripts/run_daily_curator.py
```

### 3. Check System Status
```bash
source venv/bin/activate
python scripts/run_daily_curator.py status
```

## Advanced Setup

### Manual Installation

If you prefer to set up components manually:

#### Database
```bash
# Create database
createdb -U postgres eliza

# Enable pgvector extension  
psql -U postgres -d eliza -c "CREATE EXTENSION IF NOT EXISTS vector;"

# Run migrations individually
psql -U postgres -d eliza < migrations/001_create_transformation_receipts.sql
psql -U postgres -d eliza < migrations/002_create_agent_knowledge_permissions.sql
psql -U postgres -d eliza < migrations/003_create_isolated_koi_tables.sql
psql -U postgres -d eliza < migrations/004_add_publication_dates.sql
psql -U postgres -d eliza < migrations/005_create_dashboard_tables.sql
```

#### Embedding Server (Optional)
```bash
# For testing/development
python bge_server.py

# For production (requires GPU)
# See bge_server_real.py for Hugging Face implementation
```

#### Apache Jena Fuseki (Optional)
```bash
# Download and extract Fuseki
wget https://dlcdn.apache.org/jena/binaries/apache-jena-fuseki-4.10.0.tar.gz
tar -xzf apache-jena-fuseki-4.10.0.tar.gz
cd apache-jena-fuseki-4.10.0

# Start Fuseki server
./fuseki-server --loc=/path/to/data --port=3030 /koi

# Or use Docker
docker run -p 3030:3030 -e ADMIN_PASSWORD=admin stain/jena-fuseki
```

### Step 6: Knowledge MCP Server Setup
```bash
cd bge-mcp-ts
bun install
bun run bge-server.ts
```

### Step 7: Regen MCP Server Setup (Optional)
```bash
# See separate Regen MCP repository for blockchain integration
# https://github.com/yourusername/regen-mcp-server
```

## Configuration

### Environment Variables
Create a `.env` file in the project root:

```bash
# Database
POSTGRES_URL=postgresql://postgres:postgres@localhost:5433/eliza

# Embedding Server
BGE_API_URL=http://localhost:8090/encode

# Event Bridge Configuration
USE_ISOLATED_TABLES=true  # Use new deduplication tables
KOI_COORDINATOR_URL=http://localhost:8200

# MCP Server (optional)
MCP_SERVER_PORT=3000

# Logging
LOG_LEVEL=INFO
```

### Service Ports
- **8090**: Embedding Server
- **8100**: KOI Event Bridge
- **8200**: KOI Coordinator
- **3000**: MCP Server (stdio transport)
- **3030**: Apache Jena Fuseki SPARQL endpoint

## Usage

### Starting Services

#### 1. Start Embedding Server
```bash
python bge_server.py  # Currently using BGE model
# Server will run on http://localhost:8090
```

#### 2. Start Event Bridge v2
```bash
USE_ISOLATED_TABLES=true python koi_event_bridge_v2.py
# Server will run on http://localhost:8100
```

#### 3. Start Apache Jena Fuseki
```bash
./fuseki-server --loc=/path/to/data --port=3030 /koi
# SPARQL endpoint will be at http://localhost:3030/koi
```

#### 4. Start Knowledge MCP Server
```bash
cd bge-mcp-ts
bun run bge-server.ts
# Knowledge MCP server handles query routing to PostgreSQL and Apache Jena
```

#### 5. Start Regen MCP Server (if needed)
```bash
# See Regen MCP repository for setup
# Provides blockchain data access to agents
```

### Sending Events

#### NEW Event (First time content)
```bash
curl -X POST http://localhost:8100/process-koi-event \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "NEW",
    "source_sensor": "website_monitor",
    "timestamp": "2025-09-09T12:00:00Z",
    "bundle": {
      "rid": "sensor.website.example.com.page1",
      "cid": "bafyreiabc123...",
      "content": {
        "text": "This is the content to be processed..."
      },
      "metadata": {
        "title": "Example Page",
        "url": "https://example.com/page1"
      },
      "manifest": {
        "version": "1.0.0"
      }
    }
  }'
```

#### UPDATE Event (Content changed)
```bash
curl -X POST http://localhost:8100/process-koi-event \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "UPDATE",
    "source_sensor": "website_monitor",
    "timestamp": "2025-09-09T13:00:00Z",
    "bundle": {
      "rid": "sensor.website.example.com.page1",
      "cid": "bafyreiabc456...",
      "content": {
        "text": "This is the UPDATED content..."
      },
      "metadata": {
        "title": "Example Page (Updated)"
      },
      "manifest": {
        "version": "1.0.0"
      }
    }
  }'
```

### Checking Status
```bash
# Event Bridge health
curl http://localhost:8100/

# Pipeline statistics
curl http://localhost:8100/stats

# Embedding server test
curl -X POST http://localhost:8090/encode \
  -H "Content-Type: application/json" \
  -d '{"text": "test embedding"}'

# Apache Jena SPARQL test
curl http://localhost:3030/koi/sparql \
  -H "Content-Type: application/sparql-query" \
  -d "SELECT * WHERE { ?s ?p ?o } LIMIT 10"
```

### Agent Query Flow

The dual MCP Server architecture provides specialized query interfaces:

#### Knowledge MCP Server:
1. **Semantic Search** (via PostgreSQL pgvector):
   - Agent sends: `{"tool": "bge_search", "query": "regenerative agriculture"}`
   - Routes to PostgreSQL for embedding similarity search
   - Returns relevant documents with similarity scores

2. **Ontological Query** (via Apache Jena):
   - Agent sends: `{"tool": "sparql_query", "query": "SELECT ?entity WHERE..."}`
   - Routes to Apache Jena Fuseki
   - Returns RDF triples and relationships

3. **Hybrid Query**:
   - Combines results from both systems
   - Semantic context from embeddings + ontological relationships

#### Regen MCP Server:
4. **Blockchain Query**:
   - Agent sends: `{"tool": "ledger_query", "query": "carbon_credits"}`
   - Connects to Regen Ledger
   - Returns on-chain data (credits, attestations, ecological state)

## API Documentation

### Event Bridge API

#### `GET /` - Health Check
Returns service status and configuration.

**Response:**
```json
{
  "service": "KOI Event Bridge v2",
  "status": "operational",
  "version": "2.0.0",
  "features": [...],
  "isolated_tables": true
}
```

#### `POST /process-koi-event` - Process Event
Processes a KOI event with deduplication and versioning.

**Request Body:**
```json
{
  "event_type": "NEW|UPDATE|FORGET",
  "source_sensor": "string",
  "timestamp": "ISO 8601",
  "bundle": {
    "rid": "unique resource identifier",
    "cid": "content identifier",
    "content": {},
    "metadata": {},
    "manifest": {}
  }
}
```

**Response:**
```json
{
  "success": true,
  "rid": "string",
  "cid": "string",
  "chunks_created": 1,
  "embeddings_created": 1,
  "version": 1,
  "previous_version_id": null,
  "error": null
}
```

#### `GET /stats` - Pipeline Statistics
Returns current pipeline metrics.

### Embedding Server API

#### `POST /encode` - Generate Embedding
Generates semantic embedding for text (currently using BGE model).

**Request:**
```json
{
  "text": "content to embed"
}
```

**Response:**
```json
{
  "embedding": [0.123, -0.456, ...] // 1024 dimensions
}
```

## Database Schema

## Personal Sensor Integration

For local personal KOI stacks, see [`docs/PERSONAL_SENSOR_INTEGRATION.md`](docs/PERSONAL_SENSOR_INTEGRATION.md).

Key points:
- Canonical source sensor IDs: `email-sensor`, `claude-sessions-sensor`
- Required migration for email metadata: `migrations/033_email_sensor_tables.sql`
- Session endpoints: `/search-sessions`, `/session-stats`, `/session-tools`, `/session-files`

### Isolated KOI Tables

#### `koi_memories`
```sql
CREATE TABLE koi_memories (
    id UUID PRIMARY KEY,
    rid VARCHAR(500) NOT NULL,
    cid VARCHAR(500),
    version INTEGER DEFAULT 1,
    previous_version_id UUID,
    event_type VARCHAR(20),
    source_sensor VARCHAR(200),
    content JSONB,
    metadata JSONB,
    superseded_at TIMESTAMP,
    created_at TIMESTAMP,
    UNIQUE(rid, version)
);
```

#### `koi_embeddings`
```sql
CREATE TABLE koi_embeddings (
    id SERIAL PRIMARY KEY,
    memory_id UUID REFERENCES koi_memories(id),
    dim_768 vector(768),   -- For alternative models
    dim_1024 vector(1024), -- For BGE
    dim_1536 vector(1536), -- For OpenAI
    created_at TIMESTAMP,
    UNIQUE(memory_id)
);
```

### Useful Queries

```sql
-- Get latest version of all documents
SELECT * FROM current_koi_memories;

-- Get version history for a RID
SELECT * FROM get_koi_memory_history('sensor.website.example.com.page1');

-- Pipeline statistics
SELECT * FROM koi_pipeline_stats;

-- Check for duplicates
SELECT rid, COUNT(*) 
FROM koi_memories 
WHERE superseded_at IS NULL 
GROUP BY rid 
HAVING COUNT(*) > 1;
```

## Milestone B Components

### ✅ Podcast Publishing System (Session 14 - COMPLETE)
**Status**: Fully Implemented and Tested

The podcast publishing system generates weekly audio digests from aggregated content using automated audio generation (Podcastfy) or manual export (NotebookLM).

**Key Components**:
- `podcast_publisher.py` - RSS 2.0 feed generation with iTunes extensions
- `podcastfy_generator.py` - Automated audio generation (no manual steps)
- `podcast_integration.py` - Complete pipeline orchestration
- `PODCAST_HOSTING_GUIDE.md` - Comprehensive documentation

**Features**:
- Automated audio generation from weekly digests
- RSS feed with full podcast metadata
- Google Drive backup integration (optional)
- Episode management and versioning
- Configurable voices and conversation styles

### ✅ Weekly Aggregator (Session 8 - COMPLETE)
**Status**: Fully Implemented

Aggregates content from past 7 days and generates comprehensive weekly digests.

### ✅ Daily Content Curator (Sessions 7, 9-13 - COMPLETE)
**Status**: Fully Implemented

The Daily Content Curator will be a specialized processor component that aggregates and curates content for daily X posts and weekly digests.

**Architecture Decision**: 
- **Component Type**: Processor/Aggregator (NOT a KOI node)
- **Location**: `/koi-processor/daily_curator.py`
- **Integration**: Queries KOI infrastructure rather than acting as a sensor

**Key Features**:
- Query PostgreSQL for recent koi_memories (24-48 hours)
- Embedding similarity search for trending topic identification
- Stats aggregation from ledger sensor data
- Thread generation (3-5 posts with headline, stat, links, CTA)
- Style guide compliance checking
- JSON output for X bot consumption

**Data Flow**:
```
KOI Sensors → Event Bridge → PostgreSQL
                                ↓
                        Daily Content Curator
                                ↓
                        X Bot / Weekly Digest
```

## Testing

### Unit Tests
```bash
python -m pytest tests/
```

### Integration Test
```bash
# Start all services
./scripts/start_services.sh

# Run integration tests
python tests/test_integration.py
```

### Manual Testing
```bash
# Send test event
python scripts/send_test_event.py

# Check if processed
psql -U postgres -d eliza -c "SELECT * FROM koi_pipeline_stats;"
```

## Deployment

### Production Configuration

1. **Use environment variables** for all configuration
2. **Enable SSL** for PostgreSQL connections
3. **Use real embedding model** instead of mock server
4. **Set up monitoring** (Prometheus metrics available at `/metrics`)
5. **Configure log rotation** for production logs

### Docker Deployment
```bash
# Build image
docker build -t koi-processor .

# Run with environment file
docker run --env-file .env.production koi-processor
```

### Systemd Service
```ini
[Unit]
Description=KOI Event Bridge v2
After=network.target postgresql.service

[Service]
Type=simple
User=koi
WorkingDirectory=/opt/koi-processor
Environment="USE_ISOLATED_TABLES=true"
ExecStart=/usr/bin/python3 koi_event_bridge_v2.py
Restart=always

[Install]
WantedBy=multi-user.target
```

## Troubleshooting

### Common Issues

#### "Embedding server not responding"
- Check if embedding server is running: `curl http://localhost:8090/encode -d '{"text":"test"}'`
- Verify BGE_API_URL environment variable
- Check firewall rules for port 8090

#### "Duplicate key violation"
- This means deduplication is working!
- Use UPDATE event type for changed content
- Check RID uniqueness before sending NEW events

#### "No embeddings created"
- Verify pgvector extension: `\dx` in psql
- Check embedding dimension matches model output
- Review Event Bridge logs for errors

#### "Memory/CPU usage high"
- Adjust chunk size and overlap in configuration
- Implement rate limiting for sensor events
- Consider horizontal scaling with multiple Event Bridge instances

### Debug Mode
```bash
# Enable debug logging
LOG_LEVEL=DEBUG python koi_event_bridge_v2.py

# Check specific component
python -c "from koi_event_bridge_v2 import test_connection; test_connection()"
```

## Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open Pull Request

## Related Repositories

- **[koi-sensors](https://github.com/yourusername/koi-sensors)** - Sensor implementations
- **[koi-research](https://github.com/yourusername/koi-research)** - Research and documentation
- **[GAIA](https://github.com/yourusername/GAIA)** - Eliza AI agent framework

## License

MIT License - see LICENSE file for details

---

**Built with 💚 for the regenerative future**
