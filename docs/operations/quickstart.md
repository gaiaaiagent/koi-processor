# KOI Processor Quick Start Guide

> ⚠️ **SCOPE (verified against live prod 2026-07-16):** This quickstart is the **RegenAI
> public-production event-bridge stack** (branch `stable`, host 202.61.196.119, `eliza` DB).
> That stack is genuinely live — but the script **paths have moved**: `bge_server.py` and
> `koi_event_bridge_v2.py` are now at **`src/core/`**, not repo root, and
> `scripts/test_pipeline.py` / `diagnose.py` / `batch_import.py` **no longer exist** (fix
> those before running). Ports/DB shown here (8090/8100, `eliza`, BGE-1024) are the RegenAI
> surface.
>
> **This does NOT describe personal-KOI.** For the primary local personal-KOI surface the
> backend is `api/personal_ingest_api.py` on **port 8351**, DB **`personal_koi`**, embeddings
> **OpenAI `text-embedding-3-large` (3072-dim)**, started via
> `~/.config/personal-koi/start.sh` / `restart.sh` (launchd) — see the repo `CLAUDE.md`
> "Personal KOI Backend" + DEPLOY TOPOLOGY. A personal-KOI quickstart is still TODO.

## 🚀 5-Minute Setup

### Prerequisites Check
```bash
python3 --version  # Need 3.8+
psql --version     # Need PostgreSQL 14+
git --version      # Need git
```

### One-Line Install
```bash
git clone https://github.com/yourusername/koi-processor.git && \
cd koi-processor && \
bash scripts/setup.sh
```

## 📋 Essential Commands

### Start Everything
```bash
# Terminal 1: BGE Server
python bge_server.py

# Terminal 2: Event Bridge
python koi_event_bridge_v2.py

# Terminal 3: Test Pipeline
python scripts/test_pipeline.py
```

### Database Setup
```bash
# Create database
createdb eliza

# Run migrations
psql -d eliza < migrations/001_create_transformation_receipts.sql
psql -d eliza < migrations/002_create_agent_knowledge_permissions.sql
psql -d eliza < migrations/003_create_isolated_koi_tables.sql
```

### Environment Configuration
```bash
# Copy template
cp .env.example .env

# Edit configuration
nano .env

# Key settings to change:
# POSTGRES_URL=postgresql://user:pass@localhost:5432/eliza
# USE_ISOLATED_TABLES=true
```

## 🧪 Testing

### Quick Test
```bash
# Send test event
curl -X POST http://localhost:8100/process-koi-event \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "NEW",
    "source_sensor": "test",
    "timestamp": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",
    "bundle": {
      "rid": "test.doc.'$(date +%s)'",
      "content": {"text": "Test content"}
    }
  }'
```

### Full Test Suite
```bash
python scripts/test_pipeline.py
```

## 🔍 Common Operations

### Check Services
```bash
# Event Bridge health
curl http://localhost:8100/

# BGE Server test
curl -X POST http://localhost:8090/encode \
  -H "Content-Type: application/json" \
  -d '{"text": "test"}'

# Database connection
psql -d eliza -c "SELECT version();"
```

### View Logs
```bash
# Event Bridge logs (if using systemd)
journalctl -u koi-bridge -f

# Or direct output
python koi_event_bridge_v2.py 2>&1 | tee bridge.log
```

### Database Queries
```sql
-- Check recent memories
SELECT rid, version, created_at 
FROM koi_memories 
ORDER BY created_at DESC 
LIMIT 10;

-- Count embeddings
SELECT COUNT(*) 
FROM koi_embeddings 
WHERE dim_1024 IS NOT NULL;

-- Find duplicates
SELECT rid, COUNT(*) as count 
FROM koi_memories 
GROUP BY rid 
HAVING COUNT(*) > 1;
```

## 🐛 Troubleshooting

### Service Won't Start
```bash
# Check port availability
lsof -i :8100  # Event Bridge port
lsof -i :8090  # BGE Server port

# Kill existing process
kill -9 $(lsof -t -i:8100)
```

### Database Connection Failed
```bash
# Test connection
psql postgresql://user:pass@localhost:5432/eliza

# Check PostgreSQL status
systemctl status postgresql

# View PostgreSQL logs
tail -f /var/log/postgresql/postgresql-14-main.log
```

### Embeddings Not Generated
```bash
# Test BGE server directly
python -c "
import requests
r = requests.post('http://localhost:8090/encode', 
                  json={'text': 'test'})
print(r.status_code, len(r.json().get('embedding', [])))
"
```

## 🚢 Production Deployment

### Using Docker
```bash
# Build and run
docker-compose up -d

# View logs
docker-compose logs -f event-bridge

# Stop services
docker-compose down
```

### Using Systemd
```bash
# Copy service files
sudo cp systemd/*.service /etc/systemd/system/

# Enable and start
sudo systemctl enable koi-bge koi-bridge
sudo systemctl start koi-bge koi-bridge

# Check status
sudo systemctl status koi-bridge
```

### Health Monitoring
```bash
# Simple health check
while true; do
  curl -s http://localhost:8100/ > /dev/null && echo "✓ Bridge OK" || echo "✗ Bridge Down"
  curl -s -X POST http://localhost:8090/encode -d '{"text":"test"}' > /dev/null && echo "✓ BGE OK" || echo "✗ BGE Down"
  sleep 5
done
```

## 📊 Performance Tuning

### Database Optimization
```sql
-- Add indexes
CREATE INDEX idx_koi_memories_rid ON koi_memories(rid);
CREATE INDEX idx_koi_memories_created ON koi_memories(created_at DESC);

-- Vacuum and analyze
VACUUM ANALYZE koi_memories;
VACUUM ANALYZE koi_embeddings;
```

### Application Tuning
```bash
# Increase workers (in .env)
UVICORN_WORKERS=4

# Adjust chunk size
CHUNK_SIZE=800
CHUNK_OVERLAP=150

# Enable connection pooling
CONNECTION_POOL_SIZE=20
```

## 📚 API Quick Reference

### Process Event
```http
POST /process-koi-event
{
  "event_type": "NEW|UPDATE|DELETE",
  "source_sensor": "sensor_id",
  "bundle": {
    "rid": "unique.id",
    "content": {"text": "..."}
  }
}
```

### Search (via MCP)
```http
GET /search?query=semantic+search&limit=10
```

### Health Check
```http
GET /health
Response: {"status": "healthy", "version": "2.0.0"}
```

## 🔗 Useful Links

- [Full Documentation](README.md)
- [Architecture Guide](../architecture/overview.md)
- [Deployment Guide](deployment.md)
- [API Reference](README.md#api-reference)
- [Troubleshooting](README.md#troubleshooting)

## 💡 Tips & Tricks

1. **Development Mode**: Set `DEV_MODE=true` in .env for auto-reload
2. **Mock BGE**: Use `USE_MOCK_BGE=true` for testing without GPU
3. **Verbose Logging**: Set `LOG_LEVEL=DEBUG` for detailed output
4. **Skip Dedup**: Set `USE_ISOLATED_TABLES=false` to use legacy tables
5. **Batch Import**: Use `scripts/batch_import.py` for bulk data

## 🆘 Get Help

```bash
# Check version
python koi_event_bridge_v2.py --version

# View configuration
python -c "from dotenv import load_dotenv; import os; load_dotenv(); print(os.environ.get('POSTGRES_URL'))"

# Run diagnostics
python scripts/diagnose.py
```

---

**Need more help?** Check the [README](README.md) or open an issue on GitHub.