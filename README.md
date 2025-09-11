# KOI Processor v2

🚀 **Production-Ready Knowledge Organization Infrastructure Pipeline**

A comprehensive sensor-to-agent pipeline that processes real-time content from KOI sensors, generates embeddings, handles deduplication and versioning, and provides immediate semantic search capabilities for AI agents.

## 📋 Table of Contents
- [Overview](#overview)
- [Architecture](#architecture)
- [Key Features](#key-features)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [API Documentation](#api-documentation)
- [Database Schema](#database-schema)
- [Testing](#testing)
- [Deployment](#deployment)
- [Troubleshooting](#troubleshooting)

## Overview

The KOI Processor is the central processing hub of the Knowledge Organization Infrastructure (KOI) ecosystem. It receives events from distributed sensors, processes content into searchable embeddings, and makes knowledge immediately available to AI agents through semantic search.

### What's New in v2
- ✅ **RID-based Deduplication**: Prevents duplicate content ingestion
- ✅ **Version Control**: Tracks content updates with full audit trail
- ✅ **Isolated Tables**: Separates sensor data from scraped content
- ✅ **Production Embeddings**: Model-agnostic embedding server (currently BGE-large-en-v1.5)
- ✅ **MCP Integration**: Semantic search via Model Context Protocol
- 🚧 **Daily Content Curator** (Planned): Processor component for content curation and X bot integration

## Architecture

```
DATA INGESTION PIPELINE:
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│ KOI Sensors │────▶│ Coordinator  │────▶│ Event Bridge │
│  (Various)  │     │  (Port 8200) │     │  (Port 8100) │
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
- **Separated tables**: `koi_memories` for sensor data, `memories` for legacy
- **No contamination**: Clean separation of data sources
- **Migration support**: Gradual transition from legacy systems

## Installation

### Prerequisites
- Python 3.8+
- PostgreSQL 14+ with pgvector extension
- Bun (for TypeScript MCP server)
- Apache Jena Fuseki 4.x
- 4GB+ RAM recommended

### Step 1: Clone Repository
```bash
git clone https://github.com/yourusername/koi-processor.git
cd koi-processor
```

### Step 2: Python Environment
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Step 3: Database Setup
```bash
# Create database
createdb -U postgres eliza

# Enable pgvector extension
psql -U postgres -d eliza -c "CREATE EXTENSION IF NOT EXISTS vector;"

# Run migrations
psql -U postgres -d eliza < migrations/001_create_transformation_receipts.sql
psql -U postgres -d eliza < migrations/002_create_agent_knowledge_permissions.sql
psql -U postgres -d eliza < migrations/003_create_isolated_koi_tables.sql
```

### Step 4: Embedding Server Setup
```bash
# Option 1: Use the mock embedding server (for testing)
python bge_server.py  # Note: filename kept for compatibility

# Option 2: Use real embedding model (requires GPU)
# Currently configured for BAAI/bge-large-en-v1.5
# See bge_server_real.py for Hugging Face implementation
```

### Step 5: Apache Jena Fuseki Setup
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

## Planned Components

### Daily Content Curator
**Status**: Architecture Defined (Session 7 of Milestone B)

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