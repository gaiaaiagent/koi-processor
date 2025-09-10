# KOI Processor v2

🚀 **Production-Ready Knowledge Organization Infrastructure Pipeline**

A comprehensive sensor-to-agent pipeline that processes real-time content from KOI sensors, generates BGE embeddings, handles deduplication and versioning, and provides immediate semantic search capabilities for AI agents.

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
- ✅ **BGE-large-en-v1.5**: Production-grade 1024-dimensional embeddings
- ✅ **MCP Integration**: Semantic search via Model Context Protocol

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ KOI Sensors │────▶│ Coordinator  │────▶│ Event Bridge │────▶│ BGE Server   │
│  (Various)  │     │  (Port 8200) │     │  (Port 8100) │     │  (Port 8090) │
└─────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
                                                   │                    │
                                                   ▼                    ▼
                                          ┌──────────────┐     ┌──────────────┐
                                          │ PostgreSQL   │     │ MCP Server   │
                                          │  (pgvector)  │────▶│  (Search)    │
                                          └──────────────┘     └──────────────┘
                                                   │
                                                   ▼
                                          ┌──────────────┐
                                          │ Eliza Agents │
                                          │    (RAG)     │
                                          └──────────────┘
```

### Component Description

1. **KOI Sensors**: Monitor websites, documents, and other sources
2. **KOI Coordinator** (`port 8200`): Routes events to processing pipeline
3. **KOI Event Bridge v2** (`port 8100`): Handles deduplication, versioning, chunking
4. **BGE Server** (`port 8090`): Generates BAAI/bge-large-en-v1.5 embeddings
5. **PostgreSQL**: Stores content and vectors with pgvector extension
6. **MCP Server**: Provides semantic search API for agents

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
- **BGE embeddings**: State-of-the-art 1024-dimensional vectors
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

### Step 4: BGE Server Setup
```bash
# Option 1: Use the mock BGE server (for testing)
python bge_server.py

# Option 2: Use real BGE model (requires GPU)
# See bge_server_real.py for Hugging Face implementation
```

### Step 5: MCP Server Setup
```bash
cd bge-mcp-ts
bun install
bun run bge-server.ts
```

## Configuration

### Environment Variables
Create a `.env` file in the project root:

```bash
# Database
POSTGRES_URL=postgresql://postgres:postgres@localhost:5433/eliza

# BGE Server
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
- **8090**: BGE Embedding Server
- **8100**: KOI Event Bridge
- **8200**: KOI Coordinator
- **3000**: MCP Server (optional)

## Usage

### Starting Services

#### 1. Start BGE Server
```bash
python bge_server.py
# Server will run on http://localhost:8090
```

#### 2. Start Event Bridge v2
```bash
USE_ISOLATED_TABLES=true python koi_event_bridge_v2.py
# Server will run on http://localhost:8100
```

#### 3. Start MCP Server (optional)
```bash
cd bge-mcp-ts
bun run bge-server.ts
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

# BGE server test
curl -X POST http://localhost:8090/encode \
  -H "Content-Type: application/json" \
  -d '{"text": "test embedding"}'
```

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

### BGE Server API

#### `POST /encode` - Generate Embedding
Generates BGE embedding for text.

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
    dim_768 vector(768),   -- For embeddinggemma
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
3. **Use real BGE model** instead of mock server
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

#### "BGE server not responding"
- Check if BGE server is running: `curl http://localhost:8090/encode -d '{"text":"test"}'`
- Verify BGE_API_URL environment variable
- Check firewall rules for port 8090

#### "Duplicate key violation"
- This means deduplication is working!
- Use UPDATE event type for changed content
- Check RID uniqueness before sending NEW events

#### "No BGE embeddings created"
- Verify pgvector extension: `\dx` in psql
- Check embedding dimension matches (1024 for BGE)
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