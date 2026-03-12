# Improved KOI Storage Architecture

**Last Updated**: September 27, 2025
**Status**: IMPLEMENTED ✅

## Recent Provenance Enhancements (September 27, 2025)

### Parent-Child Document Relationships
- **Parent Documents**: Topics/threads stored with `is_parent: true` metadata
- **Child Documents**: Individual posts/comments with `parent_rid` reference
- **URL Preservation**: Both parent_url and direct URLs maintained
- **Provenance API**: Enables traversal of document hierarchy

### Metadata Structure
```json
{
  "parent_rid": "topic_123_rid",      // Reference to parent document
  "parent_url": "https://forum.../t/123", // Direct parent URL
  "is_parent": true/false,            // Parent document flag
  "is_child": true/false,             // Child document flag
  "topic_id": "123",                  // Original topic identifier
  "post_number": 5                    // Position in thread
}
```

## Three-Layer Storage Pattern

### Layer 1: Raw Artifacts (`koi_content` table)
**Purpose**: Store immutable raw content as scraped
- Full HTML/JSON from websites, APIs, etc.
- Content hash for deduplication
- Never modified after creation
- Source of truth for reprocessing

**Schema Enhancement Needed**:
```sql
ALTER TABLE koi_content ADD COLUMN raw_content TEXT;
ALTER TABLE koi_content ADD COLUMN content_type VARCHAR(50); -- 'html', 'json', 'text'
ALTER TABLE koi_content ADD COLUMN scraped_at TIMESTAMP;
```

### Layer 2: Processed Documents (`koi_memories` table)
**Purpose**: Store extracted, cleaned documents with metadata
- One entry per document (not chunks!)
- Extracted text, title, dates, author, etc.
- Links back to raw artifact via `source_content_rid`
- Can be reprocessed when extraction improves

**Current Issue**: Storing chunks here instead of documents!

### Layer 3: Search Chunks (`koi_memories_chunks` table - NEW)
**Purpose**: Optimized chunks for semantic search
- Multiple chunks per document
- Each chunk references parent document
- Includes embeddings for similarity search

## Improved Data Flow

```
1. SCRAPE PHASE
   Website HTML → Hash check → Store in koi_content (if new/changed)
                             ↓
2. EXTRACTION PHASE
   koi_content → Extract metadata → Store document in koi_memories
              → Extract dates, title, author
              → Clean text content
                             ↓
3. CHUNKING PHASE
   koi_memories → Smart chunking → Store chunks in koi_memories_chunks
                → Preserve metadata in each chunk
                → Generate embeddings
```

## Benefits of This Architecture

1. **Reprocessability**: Can improve extraction without re-scraping
   ```
   UPDATE koi_memories
   SET published_at = extract_date_v2(koi_content.raw_content)
   FROM koi_content
   WHERE koi_memories.source_content_rid = koi_content.rid;
   ```

2. **Deduplication**: Only scrape when content hash changes
   ```python
   new_hash = hashlib.sha256(html_content.encode()).hexdigest()
   if existing_hash == new_hash:
       skip_scraping()
   ```

3. **Provenance**: Full audit trail from raw → processed → chunks
   ```
   Raw HTML (koi_content)
     → Document (koi_memories)
       → Chunks (koi_memories_chunks)
   ```

4. **Respectful Scraping**: Minimize load on source websites

## Migration Steps

### Step 1: Create chunks table
```sql
CREATE TABLE koi_memories_chunks (
    id SERIAL PRIMARY KEY,
    chunk_rid VARCHAR UNIQUE NOT NULL,
    document_rid VARCHAR NOT NULL REFERENCES koi_memories(rid),
    chunk_index INTEGER NOT NULL,
    content JSONB NOT NULL,
    embedding vector(1024),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW()
);
```

### Step 2: Enhance koi_content for raw storage
```sql
ALTER TABLE koi_content
ADD COLUMN raw_content TEXT,
ADD COLUMN content_type VARCHAR(50),
ADD COLUMN scraped_at TIMESTAMP DEFAULT NOW();
```

### Step 3: Update semantic bridge to:
1. Store raw content in koi_content first
2. Extract and store document in koi_memories (without chunks)
3. Create chunks in new koi_memories_chunks table

### Step 4: Create reprocessing pipeline
```python
def reprocess_content(content_rid):
    # Get raw content
    raw = fetch_from_koi_content(content_rid)

    # Re-extract with improved logic
    metadata = extract_metadata_v2(raw.raw_content)

    # Update document
    update_koi_memories(content_rid, metadata)

    # Regenerate chunks if needed
    regenerate_chunks(content_rid)
```

## Example: Processing a Forum Post

### 1. Scrape (Store Raw)
```json
{
  "rid": "orn:content:forum.regen/abc123",
  "raw_content": "<html>...entire page HTML...</html>",
  "content_hash": "sha256:abcd1234...",
  "content_type": "html",
  "url": "https://forum.regen.network/t/topic/123"
}
```

### 2. Extract (Store Document)
```json
{
  "rid": "orn:doc:forum.regen/abc123",
  "source_content_rid": "orn:content:forum.regen/abc123",
  "content": {
    "text": "Cleaned text content...",
    "title": "Forum Post Title"
  },
  "metadata": {
    "published_at": "2024-03-15T10:30:00Z",
    "author": "community_member",
    "url": "https://forum.regen.network/t/topic/123"
  }
}
```

### 3. Chunk (Store Chunks)
```json
{
  "chunk_rid": "orn:doc:forum.regen/abc123:chunk_0",
  "document_rid": "orn:doc:forum.regen/abc123",
  "chunk_index": 0,
  "content": {
    "text": "First 1000 chars..."
  },
  "metadata": {
    "published_at": "2024-03-15T10:30:00Z",  // Preserved!
    "url": "https://forum.regen.network/t/topic/123"
  }
}
```

## Key Principle: Separation of Concerns

- **koi_content**: Immutable raw artifacts
- **koi_memories**: Mutable processed documents
- **koi_memories_chunks**: Searchable chunks
- **koi_embeddings**: Vector search

Each layer has a single responsibility and can be updated independently!