# PROMPT 27: Entity Registry Consolidation

**Created**: 2025-12-10
**Status**: READY FOR EXECUTION
**Estimated Time**: 2-3 hours
**Prerequisites**: PROMPT_26 complete (re-extraction with PROMPT_24 improvements)

---

## Server Connection & Environment

**Production Server**:
- Host: `darren@202.61.196.119`
- Project directory: `/opt/projects/koi-processor`
- Python environment: `/opt/projects/koi-processor/venv`
- Environment variables: `/opt/projects/koi-processor/.env`

**Database**:
- Host: `localhost` (on server)
- Port: `5433`
- User: `postgres`
- Password: `postgres` (or from `.env`)
- Database: `eliza`
- Table: `entity_registry`

**Connect to Server**:
```bash
# SSH into production server
ssh darren@202.61.196.119

# Navigate to project
cd /opt/projects/koi-processor

# Activate Python virtual environment
source venv/bin/activate

# Load environment variables
source .env

# Verify connection
python3 -c "import psycopg2; conn = psycopg2.connect('host=localhost port=5433 dbname=eliza user=postgres password=postgres'); print('✓ Database connected')"
```

**Database Access**:
```bash
# Connect via psql
PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza

# Or use Python connection
python3 -c "
import psycopg2
conn = psycopg2.connect('host=localhost port=5433 dbname=eliza user=postgres password=postgres')
cursor = conn.cursor()
cursor.execute('SELECT COUNT(*) FROM entity_registry')
print(f'Entity count: {cursor.fetchone()[0]}')
"
```

---

## Context

After PROMPT_26 re-extraction of problematic sources (Discourse + GitHub Issues), the entity_registry has:
- **15,239 unique entities** (expected ~7,000-8,000 after consolidation)
- **Type mismatches**: "Regen Network" appears as both PERSON (525) and ORGANIZATION (1,497)
- **Semantic duplicates**: "Regen Network", "regen", "$Regen" are separate entities
- **Root cause**: Backfill script bypassed CanonicalResolver + Tier 2 disabled for speed

## Investigation Summary

### YonEarth Codebase Analysis

Investigated `/Users/darrenzal/projects/RegenAI/yonearth-gaia-chatbot` for entity consolidation patterns:

**Key Modules Found**:
1. **`type_compatibility_validator.py`**: Auto-swaps entities when types don't match relationships
2. **`semantic_deduplicator.py`**: Uses SentenceTransformer (0.87 threshold) for duplicate detection
3. **`entity_resolver.py`**: Resolves name variations with 4-level tie-breaking (allowlist → longest → most frequent → earliest)
4. **`normalize_entity_types.py`**: Simple type mapping dict (e.g., "person" → "PERSON")

**Best Practices Adopted**:
- ✅ Type normalization mapping dictionary (simple, effective)
- ✅ Backup before changes (YonEarth always backs up)
- ✅ Statistics tracking (type changes, merge counts)
- ✅ Deterministic tie-breaking for canonical selection

### KOI-Processor Current State

**Strengths**:
- ✅ Tier 1 (exact) + Tier 2 (semantic) deduplication infrastructure exists
- ✅ CanonicalResolver with canonical_entities.json configuration
- ✅ OpenAI embeddings + pgvector HNSW for semantic matching
- ✅ EntityQualityFilter for pattern-based filtering

**Gaps**:
- ❌ Backfill script bypasses CanonicalResolver pipeline
- ❌ Tier 2 semantic dedup was disabled for speed (OPENAI_API_KEY="" override)
- ❌ No type normalization mapping for legacy data cleanup

---

## Solution: 3-Phase Consolidation

### Phase 1: Investigate Type Mismatches (30 min)

**Goal**: Identify all type collision patterns in entity_registry

**SQL Queries**:

```sql
-- 1. Find entities with same name but different types
SELECT
    entity_text,
    entity_type,
    COUNT(*) as occurrence_count
FROM entity_registry
GROUP BY entity_text, entity_type
HAVING entity_text IN (
    SELECT entity_text
    FROM entity_registry
    GROUP BY entity_text
    HAVING COUNT(DISTINCT entity_type) > 1
)
ORDER BY entity_text, occurrence_count DESC;

-- 2. Count type collisions
SELECT
    entity_text,
    COUNT(DISTINCT entity_type) as type_count,
    STRING_AGG(DISTINCT entity_type, ', ') as types,
    SUM(occurrence_count) as total_occurrences
FROM entity_registry
GROUP BY entity_text
HAVING COUNT(DISTINCT entity_type) > 1
ORDER BY total_occurrences DESC
LIMIT 50;

-- 3. Identify "Regen Network" variants
SELECT
    entity_text,
    entity_type,
    occurrence_count
FROM entity_registry
WHERE entity_text ILIKE '%regen%'
ORDER BY occurrence_count DESC;
```

**Expected Patterns**:
- "Regen Network" (ORGANIZATION, 1,497) vs "Regen Network" (PERSON, 525)
- "regen-network" (PERSON, 338) should be ORGANIZATION
- "$Regen" (PROJECT, 178) might consolidate with "Regen Network" (ORGANIZATION)

**Deliverable**: `entity_type_mismatch_report.txt` with collision patterns

---

### Phase 2: Fix Type Mismatches (45 min)

**Goal**: Apply CanonicalResolver to existing entity_registry data

**Strategy**: Create `scripts/fix_entity_types.py` based on YonEarth's `normalize_entity_types.py`

#### Implementation Plan

**New File**: `scripts/fix_entity_types.py` (~300 lines)

**Key Components**:

1. **Type Normalization Mapping** (from YonEarth):
```python
TYPE_NORMALIZATION = {
    # Organizations (case variants)
    'organization': 'ORGANIZATION',
    'Organization': 'ORGANIZATION',
    'ORGANIZATION': 'ORGANIZATION',
    'company': 'ORGANIZATION',
    'Company': 'ORGANIZATION',

    # People (case variants)
    'person': 'PERSON',
    'Person': 'PERSON',
    'PERSON': 'PERSON',

    # Projects (case variants)
    'project': 'PROJECT',
    'Project': 'PROJECT',
    'PROJECT': 'PROJECT',

    # Technology
    'technology': 'TECHNOLOGY',
    'Technology': 'TECHNOLOGY',
    'TECHNOLOGY': 'TECHNOLOGY',
    'website': 'TECHNOLOGY',

    # Concepts
    'concept': 'CONCEPT',
    'Concept': 'CONCEPT',
    'CONCEPT': 'CONCEPT',

    # Events
    'event': 'EVENT',
    'Event': 'EVENT',
    'EVENT': 'EVENT',

    # Locations
    'location': 'LOCATION',
    'Location': 'LOCATION',
    'LOCATION': 'LOCATION',
}
```

2. **CanonicalResolver Integration**:
```python
from src.knowledge_graph.improvements.canonical_resolver import CanonicalResolver

resolver = CanonicalResolver()

def fix_entity_type(entity_text: str, entity_type: str) -> str:
    """Apply canonical resolution to determine correct type."""

    # Step 1: Normalize case variants
    normalized_type = TYPE_NORMALIZATION.get(entity_type, entity_type.upper())

    # Step 2: Check canonical_entities.json
    canonical_name, _ = resolver.resolve(entity_text, normalized_type)

    # Step 3: Look up canonical type from canonical_entities.json
    canonical_type = resolver.get_canonical_type(canonical_name)

    if canonical_type:
        return canonical_type

    return normalized_type
```

3. **SQL Update Logic**:
```python
def consolidate_type_mismatches(conn):
    """Consolidate entities with same name but different types."""

    # Find all type collisions
    cursor = conn.cursor()
    cursor.execute("""
        SELECT entity_text, entity_type, occurrence_count, id
        FROM entity_registry
        WHERE entity_text IN (
            SELECT entity_text
            FROM entity_registry
            GROUP BY entity_text
            HAVING COUNT(DISTINCT entity_type) > 1
        )
        ORDER BY entity_text, occurrence_count DESC
    """)

    collisions = cursor.fetchall()

    # Group by entity_text
    entities = defaultdict(list)
    for entity_text, entity_type, occurrence_count, entity_id in collisions:
        entities[entity_text].append({
            'id': entity_id,
            'type': entity_type,
            'count': occurrence_count
        })

    fixed_count = 0
    stats = defaultdict(int)

    for entity_text, variants in entities.items():
        # Determine canonical type using CanonicalResolver
        canonical_type = fix_entity_type(entity_text, variants[0]['type'])

        # Find which variant should be kept (highest occurrence_count)
        keeper = max(variants, key=lambda v: v['count'])

        if keeper['type'] != canonical_type:
            # Update keeper to canonical type
            cursor.execute("""
                UPDATE entity_registry
                SET entity_type = %s
                WHERE id = %s
            """, (canonical_type, keeper['id']))

            stats[f"{keeper['type']} → {canonical_type}"] += 1
            fixed_count += 1

        # Merge other variants into keeper
        for variant in variants:
            if variant['id'] != keeper['id']:
                # Add variant's occurrences to keeper
                cursor.execute("""
                    UPDATE entity_registry
                    SET occurrence_count = occurrence_count + %s
                    WHERE id = %s
                """, (variant['count'], keeper['id']))

                # Delete variant
                cursor.execute("""
                    DELETE FROM entity_registry
                    WHERE id = %s
                """, (variant['id'],))

                stats[f"Merged {variant['type']} into {canonical_type}"] += 1
                fixed_count += 1

    conn.commit()

    logger.info(f"✓ Fixed {fixed_count} type mismatches")
    logger.info("  Type changes:")
    for change, count in sorted(stats.items(), key=lambda x: -x[1])[:20]:
        logger.info(f"    {change}: {count} entities")

    return fixed_count, stats
```

4. **Backup Before Changes**:
```python
import shutil
from datetime import datetime

def backup_entity_registry(conn):
    """Backup entity_registry to SQL dump before changes."""

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = f"/tmp/entity_registry_backup_{timestamp}.sql"

    # SQL dump
    os.system(f"PGPASSWORD=postgres pg_dump -h localhost -p 5433 -U postgres -d eliza -t entity_registry -f {backup_path}")

    logger.info(f"✓ Backup created: {backup_path}")

    return backup_path
```

**Execution Steps**:
1. Backup entity_registry table
2. Load CanonicalResolver with canonical_entities.json
3. Identify all type collisions
4. Apply canonical type resolution
5. Merge duplicates (sum occurrence_counts, delete variants)
6. Report statistics

**Expected Impact**:
- "Regen Network" (PERSON, 525) → merged into "Regen Network" (ORGANIZATION, 2,022)
- "regen-network" (PERSON, 338) → "Regen Network" (ORGANIZATION)
- Type mismatches: ~50-100 fixes

**Test Plan**:
```python
def test_fix_entity_types():
    """Test type fixing logic."""

    # Test 1: Case normalization
    assert fix_entity_type("Regen Network", "organization") == "ORGANIZATION"

    # Test 2: Canonical lookup
    assert fix_entity_type("Regen Network", "PERSON") == "ORGANIZATION"

    # Test 3: Preserve correct types
    assert fix_entity_type("Gregory Landua", "PERSON") == "PERSON"
```

**Deliverable**: `scripts/fix_entity_types.py` with tests + execution report

---

### Phase 3: Enable Tier 2 Semantic Dedup (2-3 hours)

**Goal**: Re-run backfill with Tier 2 semantic deduplication enabled

**Why Needed**: 15,239 entities should consolidate to ~7,000-8,000 after semantic matching

**Current Bottleneck**:
- Backfill ran with `OPENAI_API_KEY=""` override (Tier 2 disabled for speed)
- Only Tier 1 (exact match) was active
- Result: "Regen Network", "regen", "$Regen" are separate entities

**Solution**: Re-run backfill with Tier 2 enabled

#### Implementation Plan

**Update**: `scripts/backfill_entity_registry.py` (~20 lines)

**Changes**:
1. Remove `OPENAI_API_KEY=""` override (already done in PROMPT_22)
2. Ensure `.env` loading (already fixed in BACKFILL_DOTENV_FIX.md)
3. Add progress tracking for Tier 2 semantic lookups

**Execution**:
```bash
cd /opt/projects/koi-processor
source venv/bin/activate
source .env

# Run backfill with Tier 2 enabled
python scripts/backfill_entity_registry.py
```

**Expected Timeline**:
- Tier 1 (exact): < 1 second (B-Tree index, 29,577 mentions)
- Tier 2 (semantic): ~2-3 hours (OpenAI embeddings + pgvector HNSW for ~5,000-10,000 lookups)
- **Cost**: ~$10-15 (OpenAI embeddings @ $0.0001/1K tokens × 100K-150K tokens)

**Expected Results**:
- Before: 15,239 unique entities
- After: ~7,000-8,000 unique entities (~50% reduction)
- Dedup rate: ~75-80% (Tier 1) → ~85-90% (Tier 1 + Tier 2)

**Progress Monitoring**:
```python
# Add to backfill script
logger.info(f"Tier 2 semantic lookups: {tier2_count}/{total_mentions} ({tier2_count/total_mentions*100:.1f}%)")
logger.info(f"Embeddings generated: {embeddings_count}")
logger.info(f"Estimated cost: ${embeddings_count * 0.0001:.2f}")
```

**Validation**:
```sql
-- Check consolidation results
SELECT
    COUNT(*) as unique_entities,
    SUM(occurrence_count) as total_mentions
FROM entity_registry;

-- Verify "Regen Network" consolidation
SELECT
    entity_text,
    entity_type,
    occurrence_count
FROM entity_registry
WHERE entity_text ILIKE '%regen%'
ORDER BY occurrence_count DESC;

-- Expected: "Regen Network" (ORGANIZATION, ~2,800-3,000 mentions)
-- "regen", "$Regen" should be merged if semantically similar
```

**Deliverable**: Updated entity_registry with Tier 2 consolidation + validation report

---

## Success Criteria

### Phase 1: Investigation
- ✅ SQL queries executed successfully
- ✅ Type mismatch report generated
- ✅ Top 50 collision patterns identified

### Phase 2: Type Fixes
- ✅ `scripts/fix_entity_types.py` created with tests
- ✅ Backup created before changes
- ✅ CanonicalResolver applied to existing data
- ✅ Type mismatches reduced to 0
- ✅ Statistics report shows all type changes

### Phase 3: Semantic Dedup
- ✅ Backfill script runs with Tier 2 enabled
- ✅ Unique entities reduced from 15,239 → ~7,000-8,000
- ✅ Dedup rate improved from 76.8% → 85-90%
- ✅ "Regen Network" variants consolidated
- ✅ Validation queries confirm expected consolidation

---

## Rollback Plan

**If Phase 2 fails** (type fixes):
1. Restore entity_registry from backup:
   ```bash
   psql -h localhost -p 5433 -U postgres -d eliza -f /tmp/entity_registry_backup_TIMESTAMP.sql
   ```
2. Recovery time: < 5 minutes

**If Phase 3 fails** (semantic dedup):
1. Backfill will halt on error (no data corruption)
2. Fix issue and re-run from checkpoint
3. Worst case: Restore from Phase 2 state

---

## Comparison: YonEarth vs KOI-Processor Approach

| Aspect | YonEarth | KOI-Processor |
|--------|----------|---------------|
| **When** | Post-processing (batch) | Real-time (extraction time) |
| **What** | Relationship-level dedup | Entity-level dedup |
| **Model** | SentenceTransformer (local) | OpenAI embeddings (API) |
| **Threshold** | 0.87 (cosine) | Tier 2 pgvector HNSW |
| **Type Normalization** | TYPE_NORMALIZATION dict | CanonicalResolver + canonical_entities.json |
| **Type Validation** | TypeCompatibilityValidator (auto-swap) | Not implemented (future enhancement) |
| **Entity Resolution** | EntityResolver (4-level tie-breaking) | Tier 1 + Tier 2 waterfall |

**Best of Both Worlds**:
- ✅ Adopt YonEarth's type normalization dict (simple, effective)
- ✅ Keep KOI-Processor's real-time dedup (better UX)
- ✅ Consider future: TypeCompatibilityValidator for relationship validation

---

## Files to Create/Modify

### New Files:
1. `scripts/fix_entity_types.py` - Type mismatch consolidation (~300 lines)
2. `tests/test_fix_entity_types.py` - Unit tests (~100 lines)
3. `entity_type_mismatch_report.txt` - Investigation results
4. `type_fix_execution_report.txt` - Phase 2 statistics
5. `tier2_backfill_report.txt` - Phase 3 results

### Modified Files:
6. `scripts/backfill_entity_registry.py` - Ensure Tier 2 enabled (already done in PROMPT_22)
7. `src/knowledge_graph/improvements/canonical_resolver.py` - Add `get_canonical_type()` method (~10 lines)

---

## Handoff Instructions

**For Agent Executing This Prompt**:

1. **Connect to Production Server**:
   ```bash
   # SSH into production server
   ssh darren@202.61.196.119

   # Navigate to project directory
   cd /opt/projects/koi-processor

   # Activate Python environment
   source venv/bin/activate

   # Load environment variables
   source .env

   # Verify database connection
   python3 -c "import psycopg2; conn = psycopg2.connect('host=localhost port=5433 dbname=eliza user=postgres password=postgres'); print('✓ Database connected')"
   ```

2. **Read Context**:
   - Review PROMPT_26 results (`reextraction_report_20251210.md`)
   - Review CanonicalResolver (`src/knowledge_graph/improvements/canonical_resolver.py`)
   - Review canonical_entities.json (`data/canonical_entities.json`)

3. **Execute Phase 1** (Investigation):
   - Run SQL queries against production entity_registry
   - Generate `entity_type_mismatch_report.txt`
   - Confirm patterns match expectations

4. **Execute Phase 2** (Type Fixes):
   - Create `scripts/fix_entity_types.py` based on this plan
   - Write tests (`tests/test_fix_entity_types.py`)
   - Create backup
   - Run type consolidation
   - Generate statistics report

5. **Execute Phase 3** (Semantic Dedup):
   - Verify `.env` has `OPENAI_API_KEY`
   - Run backfill with Tier 2 enabled
   - Monitor progress (2-3 hours)
   - Run validation queries
   - Generate final report

6. **Report Results**:
   - Entity count: Before → After
   - Dedup rate: Before → After
   - Top consolidated entities (with occurrence counts)
   - Cost breakdown (OpenAI API usage)
   - Any issues encountered

---

## Expected Final State

**Entity Registry**:
- Unique entities: ~7,000-8,000 (from 15,239)
- Total mentions: ~43,928 (unchanged)
- Dedup rate: ~85-90% (from 76.8%)

**Type Mismatches**:
- "Regen Network" (ORGANIZATION only, ~2,800-3,000 mentions)
- "regen-network" → merged into "Regen Network"
- All type collisions resolved

**Semantic Duplicates**:
- "Regen Network" + "regen" + "$Regen" → consolidated based on semantic similarity
- Context-aware consolidation (e.g., "$REGEN" token vs "Regen Network" org might stay separate if semantically distinct)

**Quality Metrics**:
- Zero type collisions (same entity_text with different entity_types)
- Tier 1 + Tier 2 dedup active for all future extractions
- CanonicalResolver applied to both new and existing entities

---

## Next Steps After Completion

1. **Monitor Production**:
   - Run validation queries daily for 1 week
   - Check for new type mismatches
   - Verify Tier 2 semantic dedup working on fresh extractions

2. **Future Enhancements** (Optional):
   - Implement TypeCompatibilityValidator (from YonEarth) for relationship validation
   - Add EntityResolver (from YonEarth) for name variant resolution
   - Build admin UI for canonical_entities.json management

3. **Documentation**:
   - Update CLAUDE.md with Phase 3 completion
   - Document type normalization mapping in README
   - Add consolidation metrics to weekly reports

---

**Status**: READY FOR EXECUTION
**Priority**: HIGH (fixes critical data quality issues)
**Risk**: LOW (backups + rollback plan in place)
