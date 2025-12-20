# Duplicate Content Fix Plan

**Issue:** Search results return duplicate content (same WhitePaper.tex appearing 5 times with different RIDs)
**Root Cause:** GitLab sensor creates new RIDs per sensor run for unchanged files
**Impact:** Poor search UX, wasted storage, inflated result counts
**Date:** 2025-12-20

---

## Problem Description

When searching for "carbon credits methodology", the Hybrid RAG system returns 5 results that are all the **same WhitePaper.tex content** from different GitLab sensor runs:

```
Result 1: regen.gitlab:regen-ledger/docs/WhitePaper.tex:chunk0 (run Dec 15)
Result 2: regen.gitlab:regen-ledger/docs/WhitePaper.tex:chunk0 (run Dec 18)
Result 3: regen.gitlab:regen-ledger/docs/WhitePaper.tex:chunk0 (run Dec 19)
Result 4: regen.gitlab:regen-ledger/docs/WhitePaper.tex:chunk0 (run Dec 20a)
Result 5: regen.gitlab:regen-ledger/docs/WhitePaper.tex:chunk0 (run Dec 20b)
```

All 5 have identical content but different RIDs because the sensor creates unique identifiers per run.

---

## Architecture Overview

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  GitLab Sensor  │────▶│  Event Bridge   │────▶│  koi_memories   │
│  (koi-sensors)  │     │  (koi-processor)│     │  (PostgreSQL)   │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                                                        │
                                                        ▼
                                               ┌─────────────────┐
                                               │ koi-query-api   │
                                               │ (Hybrid RAG)    │
                                               └─────────────────┘
                                                        │
                                                        ▼
                                               ┌─────────────────┐
                                               │  Search Results │
                                               │  (duplicates!)  │
                                               └─────────────────┘
```

### Current Deduplication State

| Layer | Current Dedup | Works For | Gap |
|-------|---------------|-----------|-----|
| Sensor | None | - | Creates new RID per run |
| Event Bridge | SHA-256 + URL-based | Web pages | GitLab uses different RID format |
| Storage | `superseded_at` column | Same RID updates | Different RIDs bypass this |
| Query | Source-diversity sampling | Source type balance | Same content, same source type |

---

## Root Cause Analysis

### 1. GitLab Sensor RID Format (Primary Issue)

**Location:** `koi-sensors/sensors/gitlab_sensor.py` (or similar)

The sensor likely generates RIDs that include run-specific information:

```python
# PROBLEM: RID includes timestamp or run ID
rid = f"regen.gitlab:{repo}/{file_path}:{run_id}"
# or
rid = f"regen.gitlab:{repo}/{file_path}:{commit_sha}:{chunk_index}"
```

Each sensor run creates NEW RIDs even when file content hasn't changed.

### 2. Event Bridge Doesn't Catch It

**Location:** `/opt/projects/koi-processor/src/core/koi_event_bridge_v2.py`

The event bridge has content-hash dedup, but it may:
- Only apply to web pages (URL-based)
- Not check content hash across different RID patterns
- Not supersede old entries when RID format differs

### 3. Query Returns All Matches

**Location:** `/opt/projects/koi-processor/koi-query-api.ts`

The entity search and vector search return all matching chunks without content-level deduplication:

```typescript
// Current: Returns all matches
const results = await pool.query(entityQuery, [patterns, limit]);

// Missing: Content-based deduplication
```

---

## Fix Plan: 3 Phases

### Phase 1: Query-Level Dedup (IMMEDIATE - Do First) ✅ COMPLETED

**Purpose:** Fix search UX immediately for all historical duplicates
**File:** `/opt/projects/koi-processor/koi-query-api.ts`
**Effort:** 30 minutes
**Risk:** Low
**Status:** IMPLEMENTED 2025-12-20

#### Implementation

**Changes made to `koi-query-api.ts`:**

1. **performEntitySearch** (lines ~291-306): Added `deduplicated` CTE after `combined` CTE
   - Partitions by `md5(content)`
   - Orders by `published_at DESC NULLS LAST, entity_count DESC`
   - Filters to `content_rank = 1`

2. **performSemanticSearch** (lines ~439-475): Wrapped query in `vector_results` CTE, added `deduplicated` CTE
   - Fetches 3x limit initially to account for duplicates
   - Partitions by `md5(content)`
   - Orders by `similarity DESC`

3. **performKeywordSearch** (lines ~614-628): Added `deduplicated` CTE after `combined` CTE
   - Partitions by `md5(content)`
   - Orders by `rank DESC, match_type`

Find the `performEntitySearch` function (around line 200-300) and wrap the final query with content deduplication:

**Current pattern:**
```typescript
const entityQuery = `
  WITH matched_entities AS (...),
  entity_memories AS (...),
  with_source AS (...),
  ...
  SELECT * FROM combined
  ORDER BY max_entity_length DESC
  LIMIT $2
`;
```

**Add content dedup wrapper:**
```typescript
const entityQuery = `
  WITH matched_entities AS (...),
  entity_memories AS (...),
  with_source AS (...),
  combined AS (...),
  -- NEW: Deduplicate by content hash
  deduplicated AS (
    SELECT *,
      ROW_NUMBER() OVER (
        PARTITION BY md5(content)
        ORDER BY published_at DESC NULLS LAST, entity_count DESC
      ) as content_rank
    FROM combined
  )
  SELECT rid, content, source, url, entities_matched, entity_count,
         max_entity_length, published_at
  FROM deduplicated
  WHERE content_rank = 1
  ORDER BY max_entity_length DESC
  LIMIT $2
`;
```

**Also apply to `performSemanticSearch`** (around line 400-500):

```typescript
// Add similar dedup to vector search results
const vectorQuery = `
  WITH vector_results AS (
    -- existing vector search
  ),
  deduplicated AS (
    SELECT *,
      ROW_NUMBER() OVER (
        PARTITION BY md5(content->>'text')
        ORDER BY similarity DESC
      ) as content_rank
    FROM vector_results
  )
  SELECT * FROM deduplicated WHERE content_rank = 1
  LIMIT $2
`;
```

#### Testing Phase 1

```bash
# Start the API
cd /opt/projects/koi-processor && bun koi-query-api.ts

# Test search - should return unique content only
curl -X POST http://localhost:8302/query \
  -H "Content-Type: application/json" \
  -d '{"question": "carbon credits methodology", "limit": 10}'

# Verify: No duplicate WhitePaper.tex entries
```

---

### Phase 2: Storage-Level Dedup (SHORT-TERM) ✅ COMPLETED

**Purpose:** Prevent new duplicates from being stored
**Files:**
- `/opt/projects/koi-processor/src/core/koi_event_bridge_v2.py`
- Database migration
**Effort:** 2 hours
**Risk:** Medium
**Status:** IMPLEMENTED 2025-12-20

**Changes made:**
1. **koi_event_bridge_v2.py** (lines ~543-572): Added global content_hash check
   - Queries for ANY memory with same content_hash (not just same RID)
   - Catches cross-RID duplicates from GitLab sensor
   - Logs "Cross-RID duplicate detected" for visibility

2. **migrations/023_content_hash_dedup_index.sql**: New migration
   - Creates index on content_hash for fast lookups
   - Backfills content_hash for existing records
   - Uses SHA-256 to match Python's hashlib

#### Step 2.1: Add content_hash column

**IMPORTANT:** Use SHA-256 consistently. The Python code uses `hashlib.sha256().hexdigest()`,
so SQL must use the same algorithm via `pgcrypto` extension.

```sql
-- Run on production database (port 5433)

-- 1. Enable pgcrypto for SHA-256 support
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 2. Add content_hash column
ALTER TABLE koi_memories ADD COLUMN IF NOT EXISTS content_hash TEXT;

-- 3. Create index for fast lookups
CREATE INDEX IF NOT EXISTS idx_memories_content_hash
ON koi_memories(content_hash)
WHERE superseded_at IS NULL;

-- 4. Backfill existing records using SHA-256 (matches Python's hashlib.sha256)
-- NOTE: This can be slow on large tables - consider batching
UPDATE koi_memories
SET content_hash = encode(digest(content->>'text', 'sha256'), 'hex')
WHERE content_hash IS NULL
  AND content->>'text' IS NOT NULL;
```

**Hash Algorithm Consistency:**
| Layer | Algorithm | Code |
|-------|-----------|------|
| Python (Event Bridge) | SHA-256 | `hashlib.sha256(text.encode()).hexdigest()` |
| SQL (Backfill) | SHA-256 | `encode(digest(text, 'sha256'), 'hex')` |
| SQL (Query Dedup) | MD5 | `md5(content)` - OK for query-time grouping only |

#### Step 2.2: Update Event Bridge

**File:** `/opt/projects/koi-processor/src/core/koi_event_bridge_v2.py`

Find the memory insertion logic and add content hash check:

```python
import hashlib

async def process_memory_event(self, event: dict) -> None:
    content_text = event.get('content', {}).get('text', '')
    content_hash = hashlib.sha256(content_text.encode()).hexdigest()

    # Check for existing identical content
    existing = await self.pool.fetchrow("""
        SELECT id, rid FROM koi_memories
        WHERE content_hash = $1
          AND superseded_at IS NULL
        LIMIT 1
    """, content_hash)

    if existing:
        # Same content already exists
        if existing['rid'] == event['rid']:
            # Same RID, same content - skip (no change)
            logger.debug(f"Skipping unchanged content: {event['rid']}")
            return
        else:
            # Different RID, same content - log and skip
            logger.info(
                f"Duplicate content detected: {event['rid']} "
                f"matches existing {existing['rid']}"
            )
            return  # Skip duplicate

    # Insert with content hash
    await self.pool.execute("""
        INSERT INTO koi_memories (rid, content, metadata, content_hash, ...)
        VALUES ($1, $2, $3, $4, ...)
    """, event['rid'], event['content'], event['metadata'], content_hash, ...)
```

#### Testing Phase 2

```bash
# Re-run a GitLab sensor job
cd /opt/projects/koi-sensors
python -m sensors.gitlab_sensor --repo regen-ledger --test

# Check logs for "Duplicate content detected" messages
# Verify no new duplicate RIDs created
```

---

### Phase 3: Sensor-Level Fix (ROOT CAUSE)

**Purpose:** Stop duplicates at the source
**Files:**
- `/opt/projects/koi-sensors/sensors/gitlab_sensor.py`
- Possibly other sensors with similar issues
**Effort:** 1 day
**Risk:** Medium-High (affects all future indexing)

#### Step 3.1: Use Canonical RIDs

Change RID generation from run-specific to canonical:

```python
# BEFORE (problematic):
def generate_rid(repo: str, file_path: str, chunk_index: int, run_id: str) -> str:
    return f"regen.gitlab:{repo}/{file_path}:chunk{chunk_index}:{run_id}"

# AFTER (canonical):
def generate_rid(repo: str, file_path: str, chunk_index: int) -> str:
    # RID is deterministic based on content location only
    return f"regen.gitlab:{repo}/{file_path}#chunk{chunk_index}"
```

#### Step 3.2: Include Version in Metadata (Not RID)

```python
def create_memory_event(repo: str, file_path: str, content: str,
                        commit_sha: str, chunk_index: int) -> dict:
    return {
        "rid": f"regen.gitlab:{repo}/{file_path}#chunk{chunk_index}",
        "content": {"text": content},
        "metadata": {
            "source": "gitlab",
            "repo": repo,
            "file_path": file_path,
            "commit_sha": commit_sha,  # Version info in metadata
            "indexed_at": datetime.now().isoformat(),
            "chunk_index": chunk_index
        }
    }
```

#### Step 3.3: Check Content Before Emitting

```python
async def should_emit_event(self, rid: str, content_hash: str) -> bool:
    """Check if this content needs to be indexed."""

    # Query existing memory
    existing = await self.db.fetchrow("""
        SELECT content_hash FROM koi_memories
        WHERE rid = $1 AND superseded_at IS NULL
    """, rid)

    if existing is None:
        return True  # New file, emit

    if existing['content_hash'] != content_hash:
        return True  # Content changed, emit (will supersede old)

    return False  # Same content, skip
```

#### Testing Phase 3

```bash
# Run sensor twice on same repo
python -m sensors.gitlab_sensor --repo regen-ledger

# Wait, run again
python -m sensors.gitlab_sensor --repo regen-ledger

# Verify:
# 1. Second run emits far fewer events (only changed files)
# 2. No duplicate RIDs in database
# 3. Search returns unique content
```

---

## Database Queries for Verification

### Find Duplicate Content

```sql
-- Find content that appears multiple times
SELECT
    md5(content->>'text') as content_hash,
    COUNT(*) as duplicate_count,
    array_agg(rid) as rids
FROM koi_memories
WHERE superseded_at IS NULL
  AND rid LIKE 'regen.gitlab:%'
GROUP BY md5(content->>'text')
HAVING COUNT(*) > 1
ORDER BY duplicate_count DESC
LIMIT 20;
```

### Count Duplicates by Source

```sql
-- How many duplicates per source type?
SELECT
    CASE
        WHEN rid LIKE 'regen.gitlab:%' THEN 'gitlab'
        WHEN rid LIKE 'regen.github:%' THEN 'github'
        WHEN rid LIKE 'orn:web.page:%' THEN 'web'
        ELSE 'other'
    END as source_type,
    COUNT(*) as total_memories,
    COUNT(DISTINCT md5(content->>'text')) as unique_content,
    COUNT(*) - COUNT(DISTINCT md5(content->>'text')) as duplicates
FROM koi_memories
WHERE superseded_at IS NULL
GROUP BY source_type;
```

### Clean Up Historical Duplicates (After Fixes)

```sql
-- Mark old duplicates as superseded (keep newest)
WITH ranked AS (
    SELECT
        id,
        rid,
        ROW_NUMBER() OVER (
            PARTITION BY md5(content->>'text')
            ORDER BY published_at DESC NULLS LAST, created_at DESC
        ) as rank
    FROM koi_memories
    WHERE superseded_at IS NULL
)
UPDATE koi_memories m
SET superseded_at = NOW()
FROM ranked r
WHERE m.id = r.id
  AND r.rank > 1;
```

---

## File Locations Summary

### Local Development (This Machine)

| Component | Path | Purpose |
|-----------|------|---------|
| Query API | `/Users/darrenzal/projects/RegenAI/koi-processor/koi-query-api.ts` | Phase 1: Add content dedup to search |
| Event Bridge | `/Users/darrenzal/projects/RegenAI/koi-processor/src/core/koi_event_bridge_v2.py` | Phase 2: Check content hash on insert |
| GitLab Sensor | `/Users/darrenzal/projects/RegenAI/koi-sensors/sensors/gitlab_sensor.py` | Phase 3: Canonical RIDs |

### Production Server (202.61.196.119)

| Component | Path | Purpose |
|-----------|------|---------|
| Query API | `/opt/projects/koi-processor/koi-query-api.ts` | Phase 1: Add content dedup to search |
| Event Bridge | `/opt/projects/koi-processor/src/core/koi_event_bridge_v2.py` | Phase 2: Check content hash on insert |
| GitLab Sensor | `/opt/projects/koi-sensors/sensors/gitlab_sensor.py` | Phase 3: Canonical RIDs |
| Database | PostgreSQL on port 5433, database `eliza` | Schema changes |

---

## Development Workflow

```
┌─────────────────────────────────────────────────────────────────┐
│  1. DEVELOP LOCALLY                                             │
│     /Users/darrenzal/projects/RegenAI/koi-processor/            │
│     - Make code changes                                         │
│     - Test if local DB available (port 5433)                    │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  2. COMMIT & PUSH                                               │
│     git add -A && git commit -m "fix: add content dedup"        │
│     git push origin regen-prod                                  │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  3. DEPLOY TO PRODUCTION                                        │
│     ssh darren@202.61.196.119                                   │
│     cd /opt/projects/koi-processor                              │
│     git pull origin regen-prod                                  │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  4. RESTART SERVICES                                            │
│     # Restart Hybrid RAG API (runs under PM2 as user shawn)     │
│     sudo -u shawn pm2 restart hybrid-rag-api                    │
│     # Or if running directly:                                   │
│     pkill -f "bun koi-query-api" && bun koi-query-api.ts &      │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  5. VERIFY ON PRODUCTION                                        │
│     curl -X POST https://regen.gaiaai.xyz/api/koi/query \       │
│       -H "Content-Type: application/json" \                     │
│       -d '{"question": "carbon credits", "limit": 10}'          │
└─────────────────────────────────────────────────────────────────┘
```

### Database Access

**Production Database (for SQL changes):**
```bash
ssh darren@202.61.196.119
psql -h localhost -p 5433 -U postgres -d eliza
```

**Local Database (if running):**
```bash
psql -h localhost -p 5433 -U postgres -d eliza
```

---

## Implementation Order

```
┌─────────────────────────────────────────────────────────────────┐
│  Phase 1: Query Dedup (30 min)                                  │
│  - Immediate UX fix                                             │
│  - Fixes historical duplicates                                  │
│  - Safety net for future                                        │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Phase 2: Storage Dedup (2 hours)                               │
│  - Add content_hash column                                      │
│  - Check before insert                                          │
│  - Prevents new duplicates                                      │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Phase 3: Sensor Fix (1 day)                                    │
│  - Canonical RIDs                                               │
│  - Content-change detection                                     │
│  - Root cause elimination                                       │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Cleanup: Historical Dedup                                      │
│  - Run SQL to supersede old duplicates                          │
│  - Verify search quality                                        │
└─────────────────────────────────────────────────────────────────┘
```

---

## Success Criteria

After all phases complete:

1. **Search Quality:** Query for "carbon credits" returns unique content only
2. **No New Duplicates:** Re-running sensors doesn't create duplicate RIDs
3. **Storage Efficiency:** Duplicate count reduced by 80%+
4. **Performance:** Query latency unchanged or improved (fewer results to score)

---

## Related Files & Context

- **Hybrid RAG Architecture:** `/opt/projects/koi-processor/docs/HYBRID_RAG_ARCHITECTURE.md`
- **Search Tool Design:** `/opt/projects/regen-koi-mcp/docs/SEARCH_TOOL_DESIGN.md`
- **Entity-Chunk Links:** 614,021 associations in `koi_entity_chunk_links` table
- **Current Fusion:** Weighted Average (0.6V + 0.2E + 0.2K + 0.15 boost)

---

*Document created: 2025-12-20*
*Author: Claude Code session with @DarrenZal*
*Purpose: Complete fix plan for content deduplication issue*
