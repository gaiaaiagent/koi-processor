# KOI Processor Testing Guide

## Overview

This guide covers testing strategies, test suites, and best practices for the KOI Processor system.

## Test Levels

### 1. Unit Tests
Test individual components in isolation.

### 2. Integration Tests
Test interactions between components.

### 3. End-to-End Tests
Test complete workflows from event to searchable content.

### 4. Performance Tests
Test system behavior under load.

## Running Tests

### Quick Test
```bash
# Run pipeline integration test
python scripts/test_pipeline.py
```

### Full Test Suite
```bash
# Install test dependencies
pip install pytest pytest-asyncio pytest-cov

# Run all tests
pytest tests/ -v

# With coverage
pytest tests/ --cov=. --cov-report=html
```

## Integration Test Suite

### test_pipeline.py

The main integration test that validates:
1. Service availability
2. BGE embedding generation
3. Event processing
4. Database storage
5. Deduplication
6. Version control

**Usage:**
```bash
python scripts/test_pipeline.py
```

**Expected Output:**
```
========================================
KOI Pipeline Integration Test
========================================

[Test 1] Checking services...
✓ Event Bridge is running at http://localhost:8100
✓ BGE Server is running at http://localhost:8090

[Test 2] Testing BGE embedding generation...
✓ BGE server generated 1024-dim embedding

[Test 3] Sending NEW event...
✓ Event processed successfully

[Test 4] Verifying database storage...
✓ Found 3 memories in isolated tables
✓ Found 3 BGE embeddings

[Test 5] Testing deduplication...
✓ Deduplication working: duplicate was rejected

[Test 6] Testing UPDATE event...
✓ UPDATE event processed, new version created

========================================
Test Summary
========================================
✓ PASSED - BGE Embedding
✓ PASSED - NEW Event
✓ PASSED - Database Storage
✓ PASSED - Deduplication
✓ PASSED - UPDATE Event

Total: 5 passed, 0 failed

🎉 All tests passed! Pipeline is working correctly.
```

## Manual Testing

### 1. Test Event Processing

#### NEW Event
```bash
curl -X POST http://localhost:8100/process-koi-event \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "NEW",
    "source_sensor": "manual_test",
    "timestamp": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",
    "bundle": {
      "rid": "test.manual.'$(date +%s)'",
      "cid": "bafytest123",
      "content": {
        "text": "This is a manual test of the KOI pipeline. It contains enough text to be chunked into multiple pieces. Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua."
      },
      "metadata": {
        "title": "Manual Test",
        "author": "Test User"
      }
    }
  }'
```

#### UPDATE Event
```bash
# First create a record
RID="test.update.$(date +%s)"
curl -X POST http://localhost:8100/process-koi-event \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "NEW",
    "source_sensor": "test",
    "bundle": {
      "rid": "'$RID'",
      "content": {"text": "Original content"}
    }
  }'

# Then update it
curl -X POST http://localhost:8100/process-koi-event \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "UPDATE",
    "source_sensor": "test",
    "bundle": {
      "rid": "'$RID'",
      "content": {"text": "Updated content"}
    }
  }'
```

### 2. Test Deduplication

```bash
# Send same event twice
RID="test.dedup.$(date +%s)"
for i in 1 2; do
  curl -X POST http://localhost:8100/process-koi-event \
    -H "Content-Type: application/json" \
    -d '{
      "event_type": "NEW",
      "source_sensor": "test",
      "bundle": {
        "rid": "'$RID'",
        "content": {"text": "Duplicate test content"}
      }
    }'
  echo "\nAttempt $i completed"
  sleep 1
done

# Check database - should only have one entry
psql -d eliza -c "SELECT COUNT(*) FROM koi_memories WHERE rid = '$RID';"
```

### 3. Test Embedding Generation

```bash
# Direct BGE test
curl -X POST http://localhost:8090/encode \
  -H "Content-Type: application/json" \
  -d '{"text": "Test embedding generation"}' | jq '.embedding | length'

# Should output: 1024
```

### 4. Test Chunking

```bash
# Send large content
curl -X POST http://localhost:8100/process-koi-event \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "NEW",
    "source_sensor": "test",
    "bundle": {
      "rid": "test.chunking.'$(date +%s)'",
      "content": {
        "text": "'$(python3 -c "print('Test content. ' * 500)")'"
      }
    }
  }'

# Check chunks created
psql -d eliza -c "
  SELECT rid, char_length(content->>'text') as length 
  FROM koi_memories 
  WHERE rid LIKE 'test.chunking.%' 
  ORDER BY rid;
"
```

## Database Testing

### Verify Schema
```sql
-- Check tables exist
SELECT table_name 
FROM information_schema.tables 
WHERE table_schema = 'public' 
AND table_name IN ('koi_memories', 'koi_embeddings');

-- Check indexes
SELECT indexname, indexdef 
FROM pg_indexes 
WHERE tablename IN ('koi_memories', 'koi_embeddings');

-- Check constraints
SELECT conname, contype, conrelid::regclass 
FROM pg_constraint 
WHERE conrelid IN ('koi_memories'::regclass, 'koi_embeddings'::regclass);
```

### Test Queries
```sql
-- Test deduplication query
EXPLAIN ANALYZE
SELECT id, version 
FROM koi_memories 
WHERE rid = 'test.rid.123' 
ORDER BY version DESC 
LIMIT 1;

-- Test vector search
EXPLAIN ANALYZE
SELECT km.rid, km.content->>'text' as text,
       ke.dim_1024 <-> (SELECT dim_1024 FROM koi_embeddings LIMIT 1) as distance
FROM koi_memories km
JOIN koi_embeddings ke ON km.id = ke.memory_id
WHERE km.superseded_at IS NULL
ORDER BY distance
LIMIT 10;
```

## Performance Testing

### 1. Load Test Script

Create `tests/load_test.py`:
```python
import asyncio
import time
import httpx
from datetime import datetime

async def send_event(client, i):
    """Send a single event"""
    event = {
        "event_type": "NEW",
        "source_sensor": "load_test",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "bundle": {
            "rid": f"test.load.{i}.{time.time()}",
            "content": {
                "text": f"Load test event {i}. " * 50
            }
        }
    }
    
    start = time.time()
    response = await client.post(
        "http://localhost:8100/process-koi-event",
        json=event
    )
    duration = time.time() - start
    
    return {
        "status": response.status_code,
        "duration": duration,
        "event_id": i
    }

async def load_test(num_events=100, concurrent=10):
    """Run load test"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        print(f"Sending {num_events} events with {concurrent} concurrent...")
        
        tasks = []
        for i in range(num_events):
            task = send_event(client, i)
            tasks.append(task)
            
            if len(tasks) >= concurrent:
                results = await asyncio.gather(*tasks)
                tasks = []
                
                # Print progress
                successful = sum(1 for r in results if r["status"] == 200)
                avg_duration = sum(r["duration"] for r in results) / len(results)
                print(f"Batch complete: {successful}/{len(results)} successful, "
                      f"avg time: {avg_duration:.2f}s")
        
        # Process remaining
        if tasks:
            await asyncio.gather(*tasks)
        
        print("Load test complete!")

if __name__ == "__main__":
    asyncio.run(load_test(num_events=100, concurrent=10))
```

### 2. Memory Test
```bash
# Monitor memory usage during test
python scripts/test_pipeline.py &
PID=$!

while kill -0 $PID 2>/dev/null; do
    ps -o pid,vsz,rss,comm -p $PID
    sleep 1
done
```

### 3. Concurrent Test
```bash
# Test concurrent event processing
for i in {1..10}; do
  curl -X POST http://localhost:8100/process-koi-event \
    -H "Content-Type: application/json" \
    -d '{
      "event_type": "NEW",
      "source_sensor": "concurrent_'$i'",
      "bundle": {
        "rid": "test.concurrent.'$i'.'$(date +%s)'",
        "content": {"text": "Concurrent test '$i'"}
      }
    }' &
done

wait
echo "All concurrent requests completed"
```

## Error Testing

### 1. Invalid Event
```bash
# Missing required field
curl -X POST http://localhost:8100/process-koi-event \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "NEW",
    "source_sensor": "test"
  }'
# Should return 422 Unprocessable Entity
```

### 2. Database Connection Failure
```bash
# Stop PostgreSQL
sudo systemctl stop postgresql

# Try to process event
curl -X POST http://localhost:8100/process-koi-event \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "NEW",
    "source_sensor": "test",
    "bundle": {
      "rid": "test.dbfail.'$(date +%s)'",
      "content": {"text": "Test"}
    }
  }'

# Should handle gracefully with error response

# Restart PostgreSQL
sudo systemctl start postgresql
```

### 3. BGE Server Failure
```bash
# Stop BGE server
pkill -f bge_server.py

# Process event (should still store, but without embeddings)
curl -X POST http://localhost:8100/process-koi-event \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "NEW",
    "source_sensor": "test",
    "bundle": {
      "rid": "test.bge.fail.'$(date +%s)'",
      "content": {"text": "Test without BGE"}
    }
  }'

# Restart BGE server
python bge_server.py &
```

## Debugging Tests

### Enable Debug Logging
```bash
# In .env
LOG_LEVEL=DEBUG

# Or via environment
LOG_LEVEL=DEBUG python koi_event_bridge_v2.py
```

### Database Query Logging
```sql
-- Enable query logging
ALTER SYSTEM SET log_statement = 'all';
SELECT pg_reload_conf();

-- View logs
tail -f /var/log/postgresql/postgresql-14-main.log
```

### Network Debugging
```bash
# Monitor HTTP traffic
tcpdump -i lo -A -s 0 'port 8100'

# Or use mitmproxy
mitmdump -p 8101 --mode reverse:http://localhost:8100
```

## Test Data Management

### Generate Test Data
```python
# generate_test_data.py
import json
from datetime import datetime, timedelta

def generate_events(count=100):
    """Generate test events"""
    events = []
    base_time = datetime.utcnow()
    
    for i in range(count):
        event = {
            "event_type": "NEW",
            "source_sensor": f"generator_{i % 5}",
            "timestamp": (base_time - timedelta(hours=i)).isoformat() + "Z",
            "bundle": {
                "rid": f"test.generated.{i}.{base_time.timestamp()}",
                "content": {
                    "text": f"Generated content {i}. " * 20,
                    "title": f"Test Document {i}"
                },
                "metadata": {
                    "index": i,
                    "batch": "test_batch_001"
                }
            }
        }
        events.append(event)
    
    with open("test_events.json", "w") as f:
        json.dump(events, f, indent=2)
    
    print(f"Generated {count} test events in test_events.json")

if __name__ == "__main__":
    generate_events(100)
```

### Clean Test Data
```sql
-- Remove all test data
DELETE FROM koi_embeddings 
WHERE memory_id IN (
    SELECT id FROM koi_memories 
    WHERE rid LIKE 'test.%'
);

DELETE FROM koi_memories 
WHERE rid LIKE 'test.%';

-- Verify cleanup
SELECT COUNT(*) as test_records 
FROM koi_memories 
WHERE rid LIKE 'test.%';
```

## Continuous Integration

### GitHub Actions Workflow
```yaml
# .github/workflows/test.yml
name: Test KOI Processor

on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]

jobs:
  test:
    runs-on: ubuntu-latest
    
    services:
      postgres:
        image: pgvector/pgvector:pg14
        env:
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: eliza
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
        ports:
          - 5432:5432
    
    steps:
    - uses: actions/checkout@v2
    
    - name: Set up Python
      uses: actions/setup-python@v2
      with:
        python-version: '3.8'
    
    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -r requirements.txt
        pip install pytest pytest-asyncio pytest-cov
    
    - name: Run migrations
      run: |
        PGPASSWORD=postgres psql -h localhost -U postgres -d eliza < migrations/001_create_transformation_receipts.sql
        PGPASSWORD=postgres psql -h localhost -U postgres -d eliza < migrations/002_create_agent_knowledge_permissions.sql
        PGPASSWORD=postgres psql -h localhost -U postgres -d eliza < migrations/003_create_isolated_koi_tables.sql
    
    - name: Start services
      run: |
        python bge_server.py &
        python koi_event_bridge_v2.py &
        sleep 5
    
    - name: Run tests
      run: |
        python scripts/test_pipeline.py
        pytest tests/ -v --cov=. --cov-report=xml
    
    - name: Upload coverage
      uses: codecov/codecov-action@v2
```

## Best Practices

1. **Always test deduplication** after schema changes
2. **Monitor memory usage** during load tests
3. **Test with production-like data** volumes
4. **Verify indexes** are being used efficiently
5. **Test error scenarios** and recovery
6. **Document test failures** with reproduction steps
7. **Keep test data isolated** from production

## Troubleshooting Test Failures

### Service Not Available
```bash
# Check if services are running
ps aux | grep -E "(bge_server|event_bridge)"

# Check ports
netstat -tulpn | grep -E "(8090|8100)"

# Restart services
pkill -f "bge_server.py"
pkill -f "koi_event_bridge_v2.py"
python bge_server.py &
python koi_event_bridge_v2.py &
```

### Database Issues
```bash
# Check PostgreSQL status
systemctl status postgresql

# Test connection
psql -d eliza -c "SELECT 1;"

# Check for locks
psql -d eliza -c "
  SELECT pid, state, query 
  FROM pg_stat_activity 
  WHERE state != 'idle';
"
```

### Embedding Failures
```bash
# Test BGE server directly
python -c "
import requests
r = requests.post('http://localhost:8090/encode', json={'text': 'test'})
print('Status:', r.status_code)
print('Embedding length:', len(r.json().get('embedding', [])))
"
```

---

For more information, see the [README](README.md) and [ARCHITECTURE](ARCHITECTURE.md) documentation.