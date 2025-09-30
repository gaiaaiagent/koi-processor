# Data Provenance System

**Last Updated:** September 30, 2025
**Status:** ✅ Production Ready - 100% Coverage Achieved

---

## Overview

The KOI Data Provenance System provides complete end-to-end traceability for all content in the pipeline, from original source URLs through every transformation step to final agent-accessible knowledge. Every chunk returned by semantic search can be traced back to its complete origin with full transformation history.

**Key Capabilities:**
- **100% URL Coverage**: All web-sourced content maintains source URLs
- **CAT Receipt Tracking**: Cryptographic audit trail for all transformations
- **RID-based Lineage**: Resource Identifiers link content through transformation chains
- **MCP Integration**: Semantic search results include complete provenance metadata
- **Apache Jena Support**: RDF-based provenance queries via SPARQL

---

## System Status (September 30, 2025)

### Coverage Metrics

| Source Type | Total Items | With URLs | Coverage |
|-------------|-------------|-----------|----------|
| **GitHub** | 1,579 | 1,579 | 100.00% |
| **Forums** | 439 | 439 | 100.00% |
| **GitLab** | 400 | 400 | 100.00% |
| **Web Pages** | 301 | 301 | 100.00% |
| **Podcasts** | 116 | 116 | 100.00% |
| **Overall** | **2,835** | **2,835** | **100.00%** |

### CAT Receipt Statistics
- **Total Receipts**: 19,760+ transformations tracked
- **Receipt Types**: 5 (sensor_collection, chunking, llm_extraction, graph_integration, embedding)
- **Embedding Coverage**: 99.95% (2,019/2,020 memories with BGE embeddings)

---

## URL Provenance System

### How URLs Are Preserved

Every sensor extracts and preserves source URLs in document metadata, which flows through the entire pipeline:

```
┌─────────────┐      ┌──────────────┐      ┌─────────────┐      ┌──────────┐
│   Sensor    │ -->  │    Bundle    │ -->  │ Event Bridge│ -->  │ Database │
│  (extract)  │      │  (metadata)  │      │  (process)  │      │ (store)  │
└─────────────┘      └──────────────┘      └─────────────┘      └──────────┘
     url                  url                    url                 url
```

#### 1. **Sensor URL Extraction**

Each sensor extracts URLs from its specific source:

**Website Sensor:**
```python
document = {
    "url": page_url,  # https://regen.network/...
    "source_url": page_url,
    # ... content ...
}
```

**Discourse Forum Sensor:**
```python
document = {
    "url": f"{forum_url}/t/{slug}/{topic_id}/{post_number}",
    # e.g., https://forum.regen.network/t/governance/123/1
}
```

**GitHub Sensor:**
```python
document = {
    "url": f"{repo_url}/blob/{branch}/{filepath}",
    # e.g., https://github.com/regen-network/regen-ledger/blob/main/README.md
}
```

**GitLab Sensor:**
```python
document = {
    "url": f"{repo_url}/-/blob/{branch}/{filepath}",
    # e.g., https://gitlab.com/regen-network/docs/-/blob/main/whitepaper.md
}
```

**Podcast Sensor:**
```python
document = {
    "url": episode_data.get('permalink_url'),
    # e.g., https://soundcloud.com/planetaryregeneration/episode-1
}
```

#### 2. **Bundle Metadata Inclusion**

The `document_to_bundle()` function in `bundle_system.py` ensures URLs are included in bundle metadata:

```python
# CRITICAL: Include URL fields for event bridge URL extraction
# Event bridge checks bundle.manifest.metadata for 'url' and 'source_url'
if document.get("url"):
    bundle_metadata["url"] = document.get("url")
if document.get("source_url"):
    bundle_metadata["source_url"] = document.get("source_url")

# Also check metadata for URL fields as fallback
doc_metadata = document.get("metadata", {})
if not bundle_metadata.get("url") and doc_metadata.get("url"):
    bundle_metadata["url"] = doc_metadata.get("url")
if not bundle_metadata.get("source_url") and doc_metadata.get("source_url"):
    bundle_metadata["source_url"] = doc_metadata.get("source_url")
```

**Location:** `koi-sensors/koi_protocol/core/bundle_system.py` (lines 269-289)

#### 3. **Event Bridge Processing**

Event bridge preserves URLs when chunking documents:

```python
# When creating chunks from parent document
chunk_metadata = {
    "parent_rid": parent_rid,
    "chunk_index": i,
    "chunk_total": total_chunks,
    "url": parent_metadata.get('url'),  # Inherited from parent
    "source_url": parent_metadata.get('source_url'),
    # ... other metadata ...
}
```

#### 4. **Database Storage**

URLs are stored in the `koi_memories.metadata` JSONB column:

```sql
SELECT
    rid,
    metadata->>'url' as url,
    metadata->>'source_url' as source_url,
    metadata->>'parent_rid' as parent_rid
FROM koi_memories
WHERE rid = 'orn:web.page:regen.network/abc123#chunk0';
```

**Result:**
```
rid                                          | url                        | source_url                 | parent_rid
---------------------------------------------|----------------------------|----------------------------|--------------------
orn:web.page:regen.network/abc123#chunk0     | https://regen.network/...  | https://regen.network/...  | orn:web.page:...
```

### Tracing Chunks to Source URLs

#### Example: Forum Post Provenance

**Semantic Search Result:**
```json
{
  "rid": "regen.forum-post:forum.regen.network_136_post_1#chunk2",
  "similarity": 0.87,
  "content": "Discussion about governance proposals..."
}
```

**Trace to Source:**
```sql
SELECT
    m.rid as chunk_rid,
    m.metadata->>'url' as source_url,
    m.metadata->>'parent_rid' as parent_document,
    m.source_sensor,
    m.created_at
FROM koi_memories m
WHERE m.rid = 'regen.forum-post:forum.regen.network_136_post_1#chunk2';
```

**Result:**
```
chunk_rid                                              | source_url                                                 | parent_document                           | source_sensor
-------------------------------------------------------|-----------------------------------------------------------|------------------------------------------|------------------
regen.forum-post:forum.regen.network_136_post_1#chunk2 | https://forum.regen.network/t/governance/136/1            | regen.forum-post:forum.regen.network_136  | discourse-sensor
```

**Complete URL**: User can click through to `https://forum.regen.network/t/governance/136/1` to see original context.

#### Example: GitHub File Provenance

**MCP Search Query:** "regen ledger architecture"

**Result with Provenance:**
```json
{
  "rid": "regen.github:github_regen-ledger_main_docs_ARCHITECTURE.md#chunk5",
  "similarity": 0.92,
  "metadata": {
    "url": "https://github.com/regen-network/regen-ledger/blob/main/docs/ARCHITECTURE.md",
    "source_url": "https://github.com/regen-network/regen-ledger/blob/main/docs/ARCHITECTURE.md"
  }
}
```

**Direct Access**: Click URL to view the exact file on GitHub.

### MCP Server Integration

The MCP Knowledge Server (`koi_knowledge_mcp_server.py`) returns URLs with every semantic search result:

```python
@app.post("/search", response_model=KnowledgeResponse)
async def search_knowledge(query: KnowledgeQuery):
    # ... semantic search with BGE embeddings ...

    memories = []
    for row in results:
        memory = {
            "rid": row['rid'],
            "content": row['content'],
            "metadata": row['metadata'],  # Contains url and source_url
            "similarity": row.get('similarity', 0.0)
        }
        memories.append(memory)

    return KnowledgeResponse(
        success=True,
        memories=memories,
        count=len(memories)
    )
```

**API Usage:**
```bash
curl -X POST http://localhost:8200/search \
  -H "Content-Type: application/json" \
  -d '{"query": "carbon credits", "limit": 5}'
```

**Response includes URLs:**
```json
{
  "success": true,
  "memories": [
    {
      "rid": "orn:web.page:regen.network/abc#chunk0",
      "similarity": 0.89,
      "metadata": {
        "url": "https://regen.network/resources/carbon-credits",
        "source_url": "https://regen.network/resources/carbon-credits"
      }
    }
  ]
}
```

---

## CAT Receipts (Content Addressable Transformation)

### What are CAT Receipts?

CAT receipts provide complete provenance tracking for all data transformations in the KOI pipeline. Every time content is processed, transformed, or enriched, a cryptographic receipt is created that forms an immutable audit trail.

CAT receipts are cryptographic records that track:
- **Input**: What content entered the transformation (RID, CID)
- **Output**: What content was produced (RID, CID)
- **Processor**: Which component performed the transformation
- **Metrics**: Processing statistics (chunks, embeddings, duration)
- **Metadata**: Additional context and parameters

### Receipt Types

The KOI pipeline generates several types of CAT receipts:

#### 1. `sensor_collection`
Created when sensors collect content from external sources.
- Input: Source URL or API endpoint
- Output: Document RID
- Tracks: Collection timestamp, sensor type

#### 2. `chunking`
Created when documents are split into memory chunks.
- Input: Document RID
- Output: Chunk RIDs
- Tracks: Number of chunks created, chunk strategy

#### 3. `llm_extraction`
Created when LLMs extract structured data.
- Input: Document RID
- Output: Extracted entities/relations
- Tracks: Model used, confidence scores

#### 4. `graph_integration`
Created when data is integrated into knowledge graph.
- Input: Extracted data
- Output: RDF triples
- Tracks: Triples added, integration time

#### 5. `embedding`
Created when text chunks are converted to BGE embeddings.
- Input: Memory chunk RID
- Output: Embedding vector
- Tracks: Model (bge-large-en-v1.5), dimensions (1024)

### Database Schema

CAT receipts are stored in the `cat_receipts` table:

```sql
CREATE TABLE cat_receipts (
    rid TEXT PRIMARY KEY,                   -- Receipt RID (orn:cat:...)
    type TEXT NOT NULL,                     -- Receipt type (chunking, embedding, etc.)
    timestamp TIMESTAMP WITH TIME ZONE,     -- Creation timestamp
    parent_rid TEXT,                        -- Parent receipt (for chaining)
    content_cid TEXT,                       -- Content ID
    transformation JSONB NOT NULL,          -- Transformation details
    metadata JSONB DEFAULT '{}',            -- Additional metadata
    hash TEXT NOT NULL,                     -- Receipt hash
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for efficient queries
CREATE INDEX idx_cat_parent_rid ON cat_receipts(parent_rid);
CREATE INDEX idx_cat_type ON cat_receipts(type);
CREATE INDEX idx_cat_timestamp ON cat_receipts(timestamp);
CREATE INDEX idx_cat_content_cid ON cat_receipts(content_cid);

-- Self-referencing foreign key for receipt chains
ALTER TABLE cat_receipts
    ADD CONSTRAINT cat_receipts_parent_rid_fkey
    FOREIGN KEY (parent_rid) REFERENCES cat_receipts(rid);
```

### Querying CAT Receipts

#### View Recent Receipts
```sql
SELECT
    type,
    rid,
    parent_rid,
    transformation,
    created_at
FROM cat_receipts
ORDER BY created_at DESC
LIMIT 10;
```

#### Get Complete Provenance Chain

Trace a chunk back through all transformations:

```sql
WITH RECURSIVE provenance_chain AS (
    -- Start with target chunk
    SELECT
        rid,
        type,
        parent_rid,
        transformation,
        0 as depth
    FROM cat_receipts
    WHERE rid LIKE '%chunk0%'  -- Your target chunk

    UNION ALL

    -- Follow parent chain
    SELECT
        c.rid,
        c.type,
        c.parent_rid,
        c.transformation,
        p.depth + 1
    FROM cat_receipts c
    JOIN provenance_chain p ON c.rid = p.parent_rid
    WHERE p.depth < 10  -- Prevent infinite loops
)
SELECT * FROM provenance_chain
ORDER BY depth;
```

#### Find Processing Statistics
```sql
SELECT
    type,
    COUNT(*) as receipt_count,
    DATE(created_at) as date
FROM cat_receipts
WHERE created_at > NOW() - INTERVAL '7 days'
GROUP BY type, DATE(created_at)
ORDER BY date DESC, type;
```

**Example Output:**
```
type              | receipt_count | date
------------------|---------------|------------
embedding         | 847          | 2025-09-30
chunking          | 234          | 2025-09-30
sensor_collection | 198          | 2025-09-30
llm_extraction    | 187          | 2025-09-30
graph_integration | 176          | 2025-09-30
```

### Receipt Generation

CAT receipts are automatically generated by the Event Bridge v2:

```python
async def create_cat_receipt(transformation_type, input_rid, output_rid, processor, metadata):
    """Create a CAT receipt for transformation tracking"""

    # Generate receipt hash
    timestamp = datetime.now(timezone.utc).isoformat()
    receipt_content = f"{transformation_type}:{input_rid}:{output_rid}:{timestamp}"
    receipt_hash = hashlib.sha256(receipt_content.encode()).hexdigest()

    # Create receipt RID
    receipt_rid = f"orn:cat:{transformation_type}:{receipt_hash[:16]}"

    # Store in PostgreSQL
    await conn.execute("""
        INSERT INTO cat_receipts (rid, type, parent_rid, transformation, hash, timestamp)
        VALUES ($1, $2, $3, $4, $5, $6)
    """, receipt_rid, transformation_type, parent_rid, transformation_json, receipt_hash, timestamp)

    # Store in Jena (optional RDF provenance)
    if jena_enabled:
        await jena.store_transformation(
            transformation_type, input_rid, output_rid, processor, metadata
        )

    return receipt_rid
```

### Receipt ID Format

Receipt RIDs follow the pattern: `orn:cat:{type}:{hash}`

**Examples:**
- `orn:cat:chunking:1c42bf1279456c53` - Chunking receipt
- `orn:cat:embedding:3cd42b9a95db3448` - Embedding receipt
- `orn:cat:graph_integration:6fb4be3d94a56541` - Graph integration receipt

---

## Apache Jena Integration

### SPARQL Provenance Queries

The system integrates with Apache Jena Fuseki for RDF-based provenance queries:

**Endpoint**: `http://localhost:3030/koi/sparql`

#### Query Complete Provenance

```sparql
PREFIX koi: <https://regen.network/koi#>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?artifact ?derivedFrom ?activity ?agent ?time ?type
WHERE {
    <urn:rid:your-chunk-rid> prov:wasDerivedFrom* ?artifact .
    OPTIONAL { ?artifact prov:wasDerivedFrom ?derivedFrom }
    OPTIONAL { ?artifact prov:wasGeneratedBy ?activity }
    OPTIONAL { ?activity prov:wasAssociatedWith ?agent }
    OPTIONAL { ?activity prov:startedAtTime ?time }
    OPTIONAL { ?activity koi:transformationType ?type }
}
ORDER BY DESC(?time)
```

#### Get All CAT Receipts for a RID

```sparql
PREFIX koi: <https://regen.network/koi#>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?receipt ?hash ?input ?output ?type ?time
WHERE {
    ?receipt a koi:CATReceipt ;
            koi:receiptHash ?hash ;
            koi:transformationType ?type ;
            prov:generatedAtTime ?time .

    OPTIONAL { ?receipt koi:inputRID ?input }
    OPTIONAL { ?receipt koi:outputRID ?output }

    FILTER(?input = "your-rid" || ?output = "your-rid")
}
ORDER BY DESC(?time)
```

---

## End-to-End Provenance Example

### Scenario: Finding the Source of a Search Result

**User Query**: "What are carbon credits?"

**1. Semantic Search via MCP:**
```bash
curl -X POST http://localhost:8200/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "carbon credits methodology",
    "limit": 3
  }'
```

**2. Search Result:**
```json
{
  "memories": [
    {
      "rid": "orn:web.page:regen.network/methodologies/abc123#chunk2",
      "similarity": 0.93,
      "content": {
        "text": "Carbon credits represent verified reductions..."
      },
      "metadata": {
        "url": "https://regen.network/methodologies/carbon-credits",
        "source_url": "https://regen.network/methodologies/carbon-credits",
        "parent_rid": "orn:web.page:regen.network/methodologies/abc123",
        "chunk_index": 2,
        "chunk_total": 5
      }
    }
  ]
}
```

**3. Trace Complete Provenance:**

```sql
-- Get chunk details and URL
SELECT
    m.rid,
    m.metadata->>'url' as source_url,
    m.metadata->>'parent_rid' as parent_document,
    m.source_sensor,
    m.created_at,
    (SELECT COUNT(*) > 0 FROM koi_embeddings e
     WHERE e.memory_id = m.id AND e.dim_1024 IS NOT NULL) as has_embedding
FROM koi_memories m
WHERE m.rid = 'orn:web.page:regen.network/methodologies/abc123#chunk2';
```

**4. Get CAT Receipt Chain:**

```sql
SELECT
    type,
    transformation,
    timestamp
FROM cat_receipts
WHERE rid LIKE '%abc123%'
ORDER BY timestamp;
```

**5. Full Transformation History:**

```
1. sensor_collection (website-sensor)
   https://regen.network/methodologies/carbon-credits
   → orn:web.page:regen.network/methodologies/abc123

2. chunking (event-bridge)
   orn:web.page:regen.network/methodologies/abc123
   → orn:web.page:regen.network/methodologies/abc123#chunk{0-4}

3. embedding (bge-server)
   orn:web.page:regen.network/methodologies/abc123#chunk2
   → 1024-dimensional vector

4. User queries via MCP
   → Semantic search returns chunk with source URL
   → User clicks through to https://regen.network/methodologies/carbon-credits
```

**Result**: Complete traceability from semantic search result → chunk → document → source URL → original web page.

---

## Monitoring and Verification

### Check URL Coverage

```sql
SELECT
    CASE
        WHEN rid LIKE 'orn:web.page:%' THEN 'web-page'
        WHEN rid LIKE 'regen.github:%' THEN 'github'
        WHEN rid LIKE 'regen.gitlab:%' THEN 'gitlab'
        WHEN rid LIKE 'regen.forum-%' THEN 'forum'
        WHEN rid LIKE 'regen.podcast:%' THEN 'podcast'
        ELSE 'other'
    END as source_type,
    COUNT(*) as total,
    SUM(CASE WHEN metadata->>'url' IS NOT NULL THEN 1 ELSE 0 END) as with_url,
    ROUND(100.0 * SUM(CASE WHEN metadata->>'url' IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 2) as coverage_pct
FROM koi_memories
GROUP BY source_type
ORDER BY total DESC;
```

### Verify CAT Receipt Generation

```bash
# Check receipt generation rate
psql $DATABASE_URL -c "
SELECT
    type,
    COUNT(*) as receipts_created,
    MAX(created_at) as last_created
FROM cat_receipts
WHERE created_at > NOW() - INTERVAL '1 day'
GROUP BY type;
"
```

### Test Provenance Trace

```python
#!/usr/bin/env python3
"""Test provenance tracing from chunk to source"""
import asyncio
import asyncpg

async def trace_chunk(chunk_rid):
    conn = await asyncpg.connect("postgresql://postgres:postgres@localhost:5433/eliza")

    # Get chunk with URL
    chunk = await conn.fetchrow("""
        SELECT
            m.rid,
            m.metadata->>'url' as url,
            m.metadata->>'parent_rid' as parent_rid,
            m.source_sensor,
            m.created_at
        FROM koi_memories m
        WHERE m.rid = $1
    """, chunk_rid)

    print(f"Chunk: {chunk['rid']}")
    print(f"Source URL: {chunk['url']}")
    print(f"Parent: {chunk['parent_rid']}")
    print(f"Sensor: {chunk['source_sensor']}")
    print(f"Created: {chunk['created_at']}")

    # Get CAT receipts
    receipts = await conn.fetch("""
        SELECT type, transformation, timestamp
        FROM cat_receipts
        WHERE rid LIKE $1
        ORDER BY timestamp
    """, f"%{chunk['parent_rid']}%")

    print(f"\nTransformation Chain ({len(receipts)} steps):")
    for i, r in enumerate(receipts, 1):
        print(f"  {i}. {r['type']} @ {r['timestamp']}")

    await conn.close()

asyncio.run(trace_chunk("orn:web.page:regen.network/abc#chunk0"))
```

---

## Troubleshooting

### Missing URLs

**Symptom**: Chunks don't have URLs in metadata

**Check**:
```sql
SELECT COUNT(*) FROM koi_memories WHERE metadata->>'url' IS NULL;
```

**Fix**: Re-scrape content with updated sensors that include URL extraction.

### Missing CAT Receipts

**Symptom**: No receipts created for recent transformations

**Check**:
```sql
SELECT COUNT(*) FROM cat_receipts WHERE created_at > NOW() - INTERVAL '1 hour';
```

**Fix**: Verify Event Bridge is running and creating receipts:
```bash
curl http://localhost:8100/stats
```

### Broken Provenance Chains

**Symptom**: Parent RIDs don't match existing records

**Check**:
```sql
SELECT DISTINCT parent_rid
FROM cat_receipts
WHERE parent_rid IS NOT NULL
  AND parent_rid NOT IN (SELECT rid FROM cat_receipts);
```

**Fix**: Usually indicates receipts were created in wrong order. Event Bridge should create sensor_collection receipt before chunking receipt.

---

## Future Enhancements

- [ ] Real-time provenance visualization dashboard
- [ ] Export provenance chains as JSON-LD
- [ ] Cross-system provenance verification
- [ ] Blockchain anchoring for receipt immutability
- [ ] Provenance-based content filtering in MCP queries
- [ ] Automated provenance integrity checks

---

## References

- **KOI Protocol Spec**: Complete specification of RID, CID, and Bundle systems
- **Event Bridge v2**: Implementation of CAT receipt generation
- **Apache Jena Integration**: `src/provenance/jena_integration.py`
- **MCP Server**: `koi_knowledge_mcp_server.py` for semantic search with provenance
- **Bundle System**: `koi_protocol/core/bundle_system.py` for URL metadata preservation
