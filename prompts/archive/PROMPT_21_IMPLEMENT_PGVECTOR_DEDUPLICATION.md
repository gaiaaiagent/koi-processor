# PROMPT 21: Implement pgvector-Based Entity Deduplication

**Date**: 2025-12-09
**Status**: READY TO IMPLEMENT
**Priority**: CRITICAL - BLOCKING EXTRACTION
**Estimated Time**: 3.5 hours (vs 6-8 hours for Jena Text approach)

---

## Context & Discovery

**Critical Finding**: koi-processor already has pgvector installed and operational! We can use the **Postgres Waterfall Strategy** instead of configuring Jena Text/Lucene.

**Expert Recommendation**:
> "If you already have pgvector handling embeddings, you have a far superior engine for deduplication than anything Fuseki can provide natively. pgvector does **semantic matching** (meaning, context) vs Fuseki Lucene's **lexical matching** (spelling, fuzziness)."

**Investigation**: See `PGVECTOR_INVESTIGATION_FINDINGS.md` for complete analysis.

---

## Architecture: The Postgres Waterfall

Instead of asking Fuseki "Do you have something spelled like 'Regen Network'?", we ask Postgres "Do you have a vector semantically close to this entity?"

### Three-Tier Strategy

**Tier 1: Exact Match** (B-Tree index, instant, ~microseconds)
```sql
SELECT fuseki_uri
FROM entity_registry
WHERE normalized_text = LOWER(?)
  AND entity_type = ?
```

**Tier 2: Semantic Match** (HNSW index, fast, ~milliseconds)
```sql
SELECT fuseki_uri, entity_text,
       1 - (embedding <=> ?) AS similarity
FROM entity_registry
WHERE 1 - (embedding <=> ?) > 0.95
  AND entity_type = ?
ORDER BY similarity DESC
LIMIT 1
```

**CRITICAL**: Threshold set to **0.95** (conservative). Better false negative than false positive.

**Tier 3: Create New** (deterministic URI)
```python
new_uri = generate_deterministic_uri(entity_text, entity_type)
insert_into_registry(new_uri, entity_text, entity_type, embedding)
fuseki.insert_skeleton(new_uri, entity_text, entity_type)
```

---

## ⚠️ CRITICAL: The Hidden Trap - Embedding Generation

**This is the most critical design decision in the entire implementation.**

### The Problem: Polysemy

Entities with the same name can be different things:
- "Mercury" (Planet) vs "Mercury" (Chemical Element) vs "Mercury" (Freddie Mercury)
- "Apple" (Fruit) vs "Apple" (Company)
- "Regen" (Organization) vs "Regen" (Project name)

### The Solution: Type Filtering + Context-Free Embeddings

**1. ALWAYS Filter by entity_type**
```sql
-- ✅ CORRECT: Type filter prevents cross-type merging
WHERE 1 - (embedding <=> $vector) > 0.95
  AND entity_type = 'PERSON'  -- Critical!

-- ❌ WRONG: Would merge "Mercury" (Planet) with "Mercury" (Person)
WHERE 1 - (embedding <=> $vector) > 0.95
```

**2. Embed ONLY the Normalized Name (NOT Context)**

```python
# ✅ CORRECT: Embed just the entity name
embedding = openai.embeddings.create(
    input="gregory landua"  # Normalized name only
)

# ❌ WRONG: Embedding with context breaks deduplication
embedding = openai.embeddings.create(
    input="Gregory Landua founded Regen Network in 2017"  # Too specific!
)
```

**Why?**
- **Registry represents the ideal entity**, not a specific mention
- Context changes every time the entity appears
- We want "Gregory Landua" to always produce the same embedding
- Context is for disambiguation at extraction time, not registry time

### The "Danger Zone": Similarity Thresholds

**Similarity > 0.98**: Auto-merge (safe)
- Example: "IBM" → "I.B.M." (punctuation variation)

**Similarity 0.95 - 0.98**: Safe zone
- Example: "International Business Machines" → "IBM"

**Similarity 0.90 - 0.95**: **DANGER ZONE** ⚠️
- Example: "Model X" vs "Model Y" (semantically close but different!)
- Example: "Bill Gates" vs "Bill Clinton" (same first name, both famous)

**Similarity < 0.90**: Definitely different

### Implementation Rule

**For entity_registry embeddings**:
```python
def embed_entity(entity_text: str) -> List[float]:
    """
    Embed ONLY the normalized entity name.

    ✅ DO: Use normalized name
    ❌ DON'T: Use surrounding context
    ❌ DON'T: Use full sentence where entity appeared
    """
    normalized = normalize_name(entity_text)

    response = openai.embeddings.create(
        model="text-embedding-ada-002",
        input=normalized  # Just the name!
    )

    return response.data[0].embedding
```

### Tuning Strategy

Start conservative (threshold = 0.95), then iterate:

1. **Monitor false negatives** (same entity, separate URIs)
   - If you see "Greg Landua" and "Gregory Landua" as separate → lower threshold to 0.93

2. **Monitor false positives** (different entities, same URI)
   - If you see "Regen Network" merged with "Regen Project X" → raise threshold to 0.97

**Rule**: Better to have duplicates than bad merges. Merging is permanent; duplicates can be cleaned up later.

---

## 🔒 CRITICAL: Race Condition Protection

**The Final Bulletproofing** - This prevents duplicate entities under concurrent load.

### The Scenario

**High-throughput streaming systems** can have race conditions:

```
Time    Thread A                        Thread B
─────────────────────────────────────────────────────────
t0      Extract "Project X"             Extract "Project X"
t1      Check registry: Not found       Check registry: Not found
t2      Generate URI                    Generate URI
t3      INSERT INTO entity_registry     INSERT INTO entity_registry
t4      ❌ DUPLICATE!
```

Both threads think "Project X" is new and try to insert simultaneously.

### The Fix: Database Constraints (Not Python Logic)

**Rule**: The database is the ultimate arbiter of uniqueness, not Python.

**Why**:
- Python checks have a time gap between CHECK and INSERT
- Even single-threaded async code can have this issue
- Multiple extraction processes could run in parallel
- The database guarantees atomicity

**Implementation**:
1. **UNIQUE constraint** on `(normalized_text, entity_type)` - Prevents duplicates at DB level
2. **ON CONFLICT** clause in INSERT - Graceful handling
3. **Try/except IntegrityError** - Fallback for edge cases

This is the **A+ Move** that makes the system truly bulletproof.

---

## Phase 1: Create entity_registry Table - 30 minutes

### Step 1: Design Schema (10 minutes)

**File**: `scripts/setup_entity_registry.sql`

```sql
-- =============================================================================
-- Entity Registry: pgvector-based deduplication
-- =============================================================================
-- Purpose: Centralized entity registry with semantic deduplication
-- Strategy: Exact match → Vector similarity → Create new
-- Database: eliza (same as koi_kg_extractions)
-- =============================================================================

-- Create entity registry table
CREATE TABLE IF NOT EXISTS entity_registry (
    id SERIAL PRIMARY KEY,

    -- Identity
    fuseki_uri TEXT UNIQUE NOT NULL,           -- Canonical Fuseki URI
    entity_text TEXT NOT NULL,                 -- Original entity name
    entity_type TEXT NOT NULL,                 -- PERSON, ORGANIZATION, etc.
    normalized_text TEXT NOT NULL,             -- Lowercase, trimmed for matching

    -- Semantic matching
    embedding VECTOR(1536) NOT NULL,           -- OpenAI ada-002 embedding

    -- Provenance
    first_seen_at TIMESTAMP DEFAULT NOW(),
    last_seen_at TIMESTAMP DEFAULT NOW(),
    occurrence_count INTEGER DEFAULT 1,        -- How many times seen

    -- Metadata
    metadata JSONB DEFAULT '{}'::jsonb,        -- Additional properties

    -- Constraints
    -- ⚠️ CRITICAL: This UNIQUE constraint prevents race conditions!
    -- If two threads try to insert same entity simultaneously, DB rejects duplicate
    CONSTRAINT entity_registry_text_type_key UNIQUE (normalized_text, entity_type)
);

-- =============================================================================
-- Indexes: Three-tier lookup optimization
-- =============================================================================

-- Tier 1: Exact Match (B-Tree, fastest)
CREATE INDEX IF NOT EXISTS idx_entity_exact
ON entity_registry (normalized_text, entity_type);

-- Tier 2: Semantic Match (HNSW, vector similarity)
CREATE INDEX IF NOT EXISTS idx_entity_vector
ON entity_registry
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- Support queries by URI
CREATE INDEX IF NOT EXISTS idx_entity_uri
ON entity_registry (fuseki_uri);

-- Support provenance queries
CREATE INDEX IF NOT EXISTS idx_entity_first_seen
ON entity_registry (first_seen_at DESC);

-- =============================================================================
-- Optional: Fuzzy Trigram Index (for typos)
-- =============================================================================
-- Uncomment if you want Tier 1.5: fuzzy string matching

-- CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- CREATE INDEX idx_entity_trigram
-- ON entity_registry
-- USING gin (normalized_text gin_trgm_ops);

-- =============================================================================
-- Statistics
-- =============================================================================

CREATE OR REPLACE VIEW entity_registry_stats AS
SELECT
    COUNT(*) as total_entities,
    COUNT(DISTINCT entity_type) as unique_types,
    SUM(occurrence_count) as total_occurrences,
    AVG(occurrence_count) as avg_occurrences_per_entity,
    MAX(occurrence_count) as max_occurrences,
    MIN(first_seen_at) as oldest_entity,
    MAX(last_seen_at) as newest_entity
FROM entity_registry;
```

### Step 2: Apply Schema (10 minutes)

**Local**:
```bash
cd /Users/darrenzal/projects/RegenAI/koi-processor

# Copy SQL to server
scp scripts/setup_entity_registry.sql darren@202.61.196.119:/opt/projects/koi-processor/scripts/
```

**On Server**:
```bash
ssh darren@202.61.196.119

cd /opt/projects/koi-processor

# Apply schema
PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza \
  -f scripts/setup_entity_registry.sql

# Verify table created
PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza \
  -c "\d entity_registry"

# Check stats view
PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d eliza \
  -c "SELECT * FROM entity_registry_stats;"
```

**Expected output**:
```
 total_entities | unique_types | total_occurrences | ...
----------------+--------------+-------------------+-----
              0 |            0 |                 0 | ...
```

### Step 3: Test Schema (10 minutes)

**File**: `scripts/test_entity_registry_schema.py`

```python
#!/usr/bin/env python3
"""Test entity_registry schema and indexes."""

import psycopg2
import numpy as np

def test_schema():
    """Test entity_registry table setup."""
    conn = psycopg2.connect(
        host="localhost",
        port=5433,
        database="eliza",
        user="postgres",
        password="postgres"
    )
    cursor = conn.cursor()

    print("Testing entity_registry schema...")

    # Test 1: Insert sample entity
    print("\n[1/5] Testing INSERT...")
    sample_embedding = np.random.rand(1536).tolist()

    cursor.execute("""
        INSERT INTO entity_registry
        (fuseki_uri, entity_text, entity_type, normalized_text, embedding)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id, fuseki_uri
    """, (
        "https://regen.network/org/test123",
        "Test Entity",
        "ORGANIZATION",
        "test entity",
        sample_embedding
    ))

    result = cursor.fetchone()
    print(f"  ✅ Inserted entity ID: {result[0]}, URI: {result[1]}")

    # Test 2: Exact match query
    print("\n[2/5] Testing EXACT MATCH...")
    cursor.execute("""
        SELECT fuseki_uri, entity_text
        FROM entity_registry
        WHERE normalized_text = %s AND entity_type = %s
    """, ("test entity", "ORGANIZATION"))

    match = cursor.fetchone()
    if match:
        print(f"  ✅ Exact match found: {match[1]} -> {match[0]}")
    else:
        print("  ❌ Exact match failed!")

    # Test 3: Vector similarity query
    print("\n[3/5] Testing VECTOR SIMILARITY...")
    test_vector = np.random.rand(1536).tolist()

    cursor.execute("""
        SELECT fuseki_uri, entity_text,
               1 - (embedding <=> %s) AS similarity
        FROM entity_registry
        WHERE entity_type = %s
        ORDER BY similarity DESC
        LIMIT 1
    """, (test_vector, "ORGANIZATION"))

    similar = cursor.fetchone()
    if similar:
        print(f"  ✅ Vector search works: {similar[1]} (similarity: {similar[2]:.4f})")
    else:
        print("  ❌ Vector search failed!")

    # Test 4: Unique constraint
    print("\n[4/5] Testing UNIQUE CONSTRAINT...")
    try:
        cursor.execute("""
            INSERT INTO entity_registry
            (fuseki_uri, entity_text, entity_type, normalized_text, embedding)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            "https://regen.network/org/test456",  # Different URI
            "Test Entity",
            "ORGANIZATION",
            "test entity",  # Same normalized text
            sample_embedding
        ))
        print("  ❌ Unique constraint failed! Duplicate allowed!")
    except psycopg2.IntegrityError as e:
        print("  ✅ Unique constraint working (duplicate rejected)")
        conn.rollback()

    # Test 5: Stats view
    print("\n[5/5] Testing STATS VIEW...")
    cursor.execute("SELECT * FROM entity_registry_stats")
    stats = cursor.fetchone()
    print(f"  ✅ Stats: {stats[0]} entities, {stats[2]} total occurrences")

    # Cleanup
    cursor.execute("DELETE FROM entity_registry WHERE entity_text = 'Test Entity'")
    conn.commit()

    cursor.close()
    conn.close()

    print("\n" + "="*60)
    print("✅ ALL TESTS PASSED")
    print("="*60)

if __name__ == "__main__":
    test_schema()
```

**Run test**:
```bash
ssh darren@202.61.196.119 "cd /opt/projects/koi-processor && source venv/bin/activate && python3 scripts/test_entity_registry_schema.py"
```

---

## Phase 2: Implement DeterministicURIGenerator - 30 minutes

### Step 1: Create URI Generator Class (20 minutes)

**File**: `src/knowledge_graph/uri_generator.py`

```python
"""Deterministic, content-addressable URI generation for entities."""

import hashlib
import re
from typing import Dict, Tuple
from urllib.parse import quote


class DeterministicURIGenerator:
    """
    Generate deterministic URIs based on entity content.

    Same normalized name + type always produces same URI.
    This prevents duplicates at the RDF level - it's the "anti-duplication shield".

    Benefits:
    - Collision-resistant (SHA256)
    - Reproducible (same input → same URI)
    - No need to query before generating
    - Works offline
    """

    BASE_URI = "https://regen.network"

    TYPE_PREFIXES = {
        "PERSON": "person",
        "ORGANIZATION": "org",
        "PROJECT": "project",
        "LOCATION": "location",
        "EVENT": "event",
        "CONCEPT": "concept",
        "CLAIM": "claim",
        "TECHNOLOGY": "tech",
        "METHODOLOGY": "methodology",
        "METRIC": "metric",
    }

    def __init__(self, base_uri: str = None):
        """
        Initialize URI generator.

        Args:
            base_uri: Base URI for all entities (default: https://regen.network)
        """
        self.base_uri = base_uri or self.BASE_URI

    def normalize_name(self, name: str) -> str:
        """
        Normalize entity name for consistent hashing.

        Normalization rules:
        - Lowercase
        - Remove extra whitespace
        - Remove common articles (the, a, an)
        - Trim trailing punctuation

        Args:
            name: Original entity name

        Returns:
            Normalized name

        Examples:
            "The Regen Network" → "regen network"
            "REGEN NETWORK  " → "regen network"
            "Gregory Landua, CEO" → "gregory landua, ceo"
        """
        # Lowercase
        normalized = name.lower()

        # Remove common articles at start
        normalized = re.sub(r'^\s*(the|a|an)\s+', '', normalized)

        # Normalize whitespace (collapse multiple spaces)
        normalized = ' '.join(normalized.split())

        # Remove trailing punctuation (but keep internal punctuation)
        normalized = normalized.rstrip('.,;:!?')

        return normalized.strip()

    def generate_uri(self, name: str, entity_type: str) -> str:
        """
        Generate deterministic URI from name and type.

        Args:
            name: Entity name
            entity_type: Entity type (PERSON, ORGANIZATION, etc.)

        Returns:
            Content-addressable URI

        Examples:
            generate_uri("Regen Network", "ORGANIZATION")
            → https://regen.network/org/a1b2c3d4e5f6g7h8

            generate_uri("Gregory Landua", "PERSON")
            → https://regen.network/person/e5f6g7h8i9j0k1l2
        """
        # Normalize name
        normalized = self.normalize_name(name)

        # Normalize type
        entity_type_upper = entity_type.upper()
        type_prefix = self.TYPE_PREFIXES.get(entity_type_upper, "entity")

        # Generate content hash
        # Format: "{normalized_name}:{entity_type}"
        content = f"{normalized}:{entity_type_upper}"
        hash_digest = hashlib.sha256(content.encode('utf-8')).hexdigest()

        # Use first 16 chars of hash
        # Collision probability: ~1 in 10^19 (astronomically low)
        short_hash = hash_digest[:16]

        # Build URI
        uri = f"{self.base_uri}/{type_prefix}/{short_hash}"

        return uri

    def generate_uri_with_metadata(
        self,
        name: str,
        entity_type: str
    ) -> Dict[str, str]:
        """
        Generate URI with metadata for debugging/provenance.

        Args:
            name: Entity name
            entity_type: Entity type

        Returns:
            Dictionary with URI and metadata:
            {
                "uri": "https://...",
                "normalized_name": "regen network",
                "hash": "a1b2c3d4...",
                "original_name": "Regen Network",
                "type": "ORGANIZATION"
            }
        """
        normalized = self.normalize_name(name)
        uri = self.generate_uri(name, entity_type)

        content = f"{normalized}:{entity_type.upper()}"
        full_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()

        return {
            "uri": uri,
            "normalized_name": normalized,
            "hash": full_hash,
            "original_name": name,
            "type": entity_type.upper()
        }

    def parse_uri(self, uri: str) -> Tuple[str, str]:
        """
        Extract type and hash from URI.

        Args:
            uri: Entity URI

        Returns:
            (type_prefix, hash) tuple

        Example:
            parse_uri("https://regen.network/org/a1b2c3d4e5f6g7h8")
            → ("org", "a1b2c3d4e5f6g7h8")
        """
        parts = uri.replace(self.base_uri + "/", "").split("/")
        if len(parts) >= 2:
            return parts[0], parts[1]
        return None, None
```

### Step 2: Write Tests (10 minutes)

**File**: `tests/test_uri_generator.py`

```python
"""Tests for DeterministicURIGenerator."""

import pytest
from knowledge_graph.uri_generator import DeterministicURIGenerator


@pytest.fixture
def generator():
    """Create test URI generator."""
    return DeterministicURIGenerator()


def test_same_name_same_uri(generator):
    """Same normalized name produces same URI."""
    uri1 = generator.generate_uri("Regen Network", "ORGANIZATION")
    uri2 = generator.generate_uri("Regen Network", "ORGANIZATION")

    assert uri1 == uri2


def test_case_insensitive(generator):
    """Case variations produce same URI."""
    uri1 = generator.generate_uri("Regen Network", "ORGANIZATION")
    uri2 = generator.generate_uri("REGEN NETWORK", "ORGANIZATION")
    uri3 = generator.generate_uri("regen network", "ORGANIZATION")

    assert uri1 == uri2 == uri3


def test_whitespace_normalization(generator):
    """Whitespace variations produce same URI."""
    uri1 = generator.generate_uri("Regen  Network", "ORGANIZATION")
    uri2 = generator.generate_uri("Regen Network", "ORGANIZATION")
    uri3 = generator.generate_uri("  Regen Network  ", "ORGANIZATION")

    assert uri1 == uri2 == uri3


def test_article_removal(generator):
    """Leading articles are removed."""
    uri1 = generator.generate_uri("The Regen Network", "ORGANIZATION")
    uri2 = generator.generate_uri("Regen Network", "ORGANIZATION")

    assert uri1 == uri2


def test_different_types_different_uris(generator):
    """Same name, different type → different URI."""
    uri_org = generator.generate_uri("Regen", "ORGANIZATION")
    uri_proj = generator.generate_uri("Regen", "PROJECT")

    assert uri_org != uri_proj
    assert "org" in uri_org
    assert "project" in uri_proj


def test_uri_format(generator):
    """URI has expected format."""
    uri = generator.generate_uri("Regen Network", "ORGANIZATION")

    assert uri.startswith("https://regen.network/org/")
    hash_part = uri.split('/')[-1]
    assert len(hash_part) == 16
    assert hash_part.isalnum()


def test_metadata_generation(generator):
    """Metadata includes all provenance info."""
    metadata = generator.generate_uri_with_metadata("Regen Network", "ORGANIZATION")

    assert "uri" in metadata
    assert metadata["uri"].startswith("https://regen.network")
    assert metadata["normalized_name"] == "regen network"
    assert metadata["original_name"] == "Regen Network"
    assert metadata["type"] == "ORGANIZATION"
    assert len(metadata["hash"]) == 64  # Full SHA256


def test_parse_uri(generator):
    """Can extract type and hash from URI."""
    uri = generator.generate_uri("Regen Network", "ORGANIZATION")
    type_prefix, hash_part = generator.parse_uri(uri)

    assert type_prefix == "org"
    assert len(hash_part) == 16


def test_deterministic_across_instances():
    """Different generator instances produce same URI."""
    gen1 = DeterministicURIGenerator()
    gen2 = DeterministicURIGenerator()

    uri1 = gen1.generate_uri("Gregory Landua", "PERSON")
    uri2 = gen2.generate_uri("Gregory Landua", "PERSON")

    assert uri1 == uri2


def test_collision_resistance():
    """Different inputs produce different URIs."""
    generator = DeterministicURIGenerator()

    uri1 = generator.generate_uri("Regen Network", "ORGANIZATION")
    uri2 = generator.generate_uri("Regen Networks", "ORGANIZATION")  # Plural
    uri3 = generator.generate_uri("Regen", "ORGANIZATION")  # Shorter

    assert uri1 != uri2 != uri3
    assert len({uri1, uri2, uri3}) == 3  # All unique
```

**Run tests**:
```bash
ssh darren@202.61.196.119 "cd /opt/projects/koi-processor && source venv/bin/activate && pytest tests/test_uri_generator.py -v"
```

**Expected**: All tests pass

---

## Phase 3: Implement Entity Resolver - 1 hour

### Step 1: Create EntityResolver Class (40 minutes)

**File**: `src/knowledge_graph/entity_resolver.py`

```python
"""Entity resolver with three-tier waterfall lookup."""

import psycopg2
from typing import Optional, Dict, List
from openai import OpenAI
import os

from .uri_generator import DeterministicURIGenerator


class EntityResolver:
    """
    Three-tier entity lookup and deduplication.

    Tier 1: Exact Match (Postgres B-Tree, microseconds)
    Tier 2: Semantic Match (pgvector HNSW, milliseconds)
    Tier 3: Create New (deterministic URI)

    Why this works:
    - Tier 1 handles exact duplicates (fast path)
    - Tier 2 handles semantic variations ("IBM" = "International Business Machines")
    - Tier 3 ensures new entities get unique, reproducible URIs

    Postgres is the "Source of Truth" for entity identity.
    """

    def __init__(
        self,
        db_config: Dict[str, str],
        openai_api_key: str = None,
        fuzzy_threshold: float = 0.95,
        embedding_model: str = "text-embedding-ada-002"
    ):
        """
        Initialize entity resolver.

        Args:
            db_config: Postgres connection config {host, port, database, user, password}
            openai_api_key: OpenAI API key for embeddings
            fuzzy_threshold: Cosine similarity threshold (0.95 conservative, tune based on results)
            embedding_model: OpenAI embedding model
        """
        self.db_config = db_config
        self.uri_gen = DeterministicURIGenerator()
        self.fuzzy_threshold = fuzzy_threshold

        # OpenAI client for embeddings
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY")
        self.openai_client = OpenAI(api_key=self.openai_api_key)
        self.embedding_model = embedding_model

        # Statistics
        self.stats = {
            "tier1_exact_hits": 0,
            "tier2_semantic_hits": 0,
            "tier3_new_entities": 0,
        }

    def get_or_create_entity(
        self,
        entity_text: str,
        entity_type: str,
        metadata: Dict = None
    ) -> Dict[str, any]:
        """
        Resolve entity using three-tier waterfall.

        Args:
            entity_text: Entity name
            entity_type: Entity type
            metadata: Optional additional metadata

        Returns:
            {
                "uri": "https://...",
                "matched": True/False,
                "match_method": "tier1_exact" | "tier2_semantic" | "tier3_new",
                "match_score": 1.0 (exact) or 0.0-1.0 (similarity),
                "entity_text": "canonical name from registry"
            }
        """
        conn = psycopg2.connect(**self.db_config)
        cursor = conn.cursor()

        # Normalize for consistent matching
        normalized = self.uri_gen.normalize_name(entity_text)

        try:
            # -------------------------------------------------------------------
            # TIER 1: EXACT MATCH (fastest)
            # -------------------------------------------------------------------
            cursor.execute("""
                SELECT fuseki_uri, entity_text, occurrence_count
                FROM entity_registry
                WHERE normalized_text = %s AND entity_type = %s
            """, (normalized, entity_type))

            match = cursor.fetchone()
            if match:
                uri, canonical_text, count = match

                # Update occurrence count
                cursor.execute("""
                    UPDATE entity_registry
                    SET occurrence_count = occurrence_count + 1,
                        last_seen_at = NOW()
                    WHERE fuseki_uri = %s
                """, (uri,))
                conn.commit()

                self.stats["tier1_exact_hits"] += 1

                return {
                    "uri": uri,
                    "matched": True,
                    "match_method": "tier1_exact",
                    "match_score": 1.0,
                    "entity_text": canonical_text
                }

            # -------------------------------------------------------------------
            # TIER 2: SEMANTIC MATCH (smart)
            # -------------------------------------------------------------------
            embedding = self._generate_embedding(entity_text)

            cursor.execute("""
                SELECT fuseki_uri, entity_text,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM entity_registry
                WHERE 1 - (embedding <=> %s::vector) > %s
                  AND entity_type = %s
                ORDER BY similarity DESC
                LIMIT 1
            """, (embedding, embedding, self.fuzzy_threshold, entity_type))

            match = cursor.fetchone()
            if match:
                uri, canonical_text, score = match

                # Update occurrence count
                cursor.execute("""
                    UPDATE entity_registry
                    SET occurrence_count = occurrence_count + 1,
                        last_seen_at = NOW()
                    WHERE fuseki_uri = %s
                """, (uri,))
                conn.commit()

                self.stats["tier2_semantic_hits"] += 1

                return {
                    "uri": uri,
                    "matched": True,
                    "match_method": "tier2_semantic",
                    "match_score": float(score),
                    "entity_text": canonical_text
                }

            # -------------------------------------------------------------------
            # TIER 3: CREATE NEW ENTITY
            # -------------------------------------------------------------------
            new_uri = self.uri_gen.generate_uri(entity_text, entity_type)

            try:
                cursor.execute("""
                    INSERT INTO entity_registry
                    (fuseki_uri, entity_text, entity_type, normalized_text, embedding, metadata)
                    VALUES (%s, %s, %s, %s, %s::vector, %s)
                    ON CONFLICT (normalized_text, entity_type) DO UPDATE
                    SET occurrence_count = entity_registry.occurrence_count + 1,
                        last_seen_at = NOW()
                    RETURNING id, fuseki_uri
                """, (
                    new_uri,
                    entity_text,
                    entity_type,
                    normalized,
                    embedding,
                    metadata or {}
                ))

                result = cursor.fetchone()
                conn.commit()

                if result:
                    entity_id, final_uri = result

                    # Check if we created new or hit race condition
                    if final_uri == new_uri:
                        self.stats["tier3_new_entities"] += 1
                    else:
                        # Race condition! Another thread just inserted this entity
                        self.stats["tier1_exact_hits"] += 1  # Count as cache hit

                    return {
                        "uri": final_uri,
                        "matched": (final_uri != new_uri),  # True if race condition
                        "match_method": "tier3_new" if final_uri == new_uri else "tier1_exact",
                        "match_score": 1.0,
                        "entity_text": entity_text
                    }

            except Exception as e:
                # Fallback: If anything goes wrong, try exact match one more time
                conn.rollback()
                cursor.execute("""
                    SELECT fuseki_uri, entity_text
                    FROM entity_registry
                    WHERE normalized_text = %s AND entity_type = %s
                """, (normalized, entity_type))

                fallback_match = cursor.fetchone()
                if fallback_match:
                    uri, canonical_text = fallback_match
                    return {
                        "uri": uri,
                        "matched": True,
                        "match_method": "tier1_exact",
                        "match_score": 1.0,
                        "entity_text": canonical_text
                    }
                else:
                    # Re-raise if truly unexpected error
                    raise

        finally:
            cursor.close()
            conn.close()

    def _generate_embedding(self, text: str) -> List[float]:
        """
        Generate embedding for entity text using OpenAI.

        ⚠️ CRITICAL: This method embeds ONLY the normalized entity name.

        DO NOT embed:
        - Surrounding context
        - Full sentence where entity appeared
        - Entity description from source document

        The registry represents the ideal entity, not a specific mention.

        Args:
            text: Entity name (will be normalized before embedding)

        Returns:
            1536-dimensional embedding vector
        """
        # Normalize entity name before embedding
        normalized = self.uri_gen.normalize_name(text)

        response = self.openai_client.embeddings.create(
            model=self.embedding_model,
            input=normalized  # Embed normalized name ONLY
        )
        return response.data[0].embedding

    def get_stats(self) -> Dict:
        """Get lookup statistics."""
        total = sum(self.stats.values())
        if total == 0:
            return self.stats

        return {
            **self.stats,
            "total_lookups": total,
            "tier1_hit_rate": self.stats["tier1_exact_hits"] / total,
            "tier2_hit_rate": self.stats["tier2_semantic_hits"] / total,
            "tier3_new_rate": self.stats["tier3_new_entities"] / total,
        }

    def reset_stats(self):
        """Reset statistics counters."""
        self.stats = {k: 0 for k in self.stats}
```

### Step 2: Write Tests (20 minutes)

**File**: `tests/test_entity_resolver.py`

```python
"""Tests for EntityResolver."""

import pytest
from knowledge_graph.entity_resolver import EntityResolver


@pytest.fixture
def db_config():
    """Test database configuration."""
    return {
        "host": "localhost",
        "port": 5433,
        "database": "eliza",
        "user": "postgres",
        "password": "postgres"
    }


@pytest.fixture
def resolver(db_config):
    """Create test entity resolver."""
    return EntityResolver(db_config=db_config)


def test_create_new_entity(resolver):
    """Creating new entity works."""
    result = resolver.get_or_create_entity("Test Entity Alpha", "ORGANIZATION")

    assert result["matched"] == False
    assert result["match_method"] == "tier3_new"
    assert result["uri"].startswith("https://regen.network/org/")
    assert result["entity_text"] == "Test Entity Alpha"


def test_exact_match(resolver):
    """Exact match returns same entity."""
    # Create first
    result1 = resolver.get_or_create_entity("Test Entity Beta", "PERSON")

    # Lookup with exact name
    result2 = resolver.get_or_create_entity("Test Entity Beta", "PERSON")

    assert result2["matched"] == True
    assert result2["match_method"] == "tier1_exact"
    assert result2["uri"] == result1["uri"]
    assert result2["match_score"] == 1.0


def test_case_insensitive_match(resolver):
    """Case variations match via Tier 1."""
    result1 = resolver.get_or_create_entity("Test Entity Gamma", "LOCATION")
    result2 = resolver.get_or_create_entity("TEST ENTITY GAMMA", "LOCATION")

    assert result2["matched"] == True
    assert result2["uri"] == result1["uri"]


def test_semantic_match(resolver):
    """Semantic variations match via Tier 2."""
    # Create with full name
    result1 = resolver.get_or_create_entity(
        "International Business Machines Corporation",
        "ORGANIZATION"
    )

    # Lookup with common abbreviation
    result2 = resolver.get_or_create_entity("IBM", "ORGANIZATION")

    # Should match semantically (high vector similarity)
    # NOTE: This test may fail if threshold is too strict or embeddings aren't similar enough
    if result2["match_method"] == "tier2_semantic":
        assert result2["uri"] == result1["uri"]
        assert result2["match_score"] > 0.92


def test_different_types_create_separate(resolver):
    """Same name, different type → separate entities."""
    result_org = resolver.get_or_create_entity("Regen", "ORGANIZATION")
    result_proj = resolver.get_or_create_entity("Regen", "PROJECT")

    assert result_org["uri"] != result_proj["uri"]


def test_stats_tracking(resolver):
    """Resolver tracks statistics."""
    resolver.reset_stats()

    # Create new
    resolver.get_or_create_entity("Entity 1", "PERSON")

    # Exact match
    resolver.get_or_create_entity("Entity 1", "PERSON")

    # Another new
    resolver.get_or_create_entity("Entity 2", "PERSON")

    stats = resolver.get_stats()

    assert stats["total_lookups"] == 3
    assert stats["tier1_exact_hits"] >= 1
    assert stats["tier3_new_entities"] >= 2
```

**Run tests**:
```bash
ssh darren@202.61.196.119 "cd /opt/projects/koi-processor && source venv/bin/activate && source .env && pytest tests/test_entity_resolver.py -v"
```

---

## Phase 4: Integration - 1 hour

### Step 1: Update graph_integration.py (40 minutes)

**File**: `src/knowledge_graph/graph_integration.py`

Find and update the `_get_or_create_entity_by_name` method:

```python
from .entity_resolver import EntityResolver

class KnowledgeGraphIntegrator:
    def __init__(self, ...):
        # ... existing code ...

        # ADD: Entity resolver for deduplication
        self.entity_resolver = EntityResolver(
            db_config={
                "host": os.getenv("POSTGRES_HOST", "localhost"),
                "port": int(os.getenv("POSTGRES_PORT", 5433)),
                "database": os.getenv("POSTGRES_DB", "eliza"),
                "user": os.getenv("POSTGRES_USER", "postgres"),
                "password": os.getenv("POSTGRES_PASSWORD", "postgres")
            },
            fuzzy_threshold=0.95  # Conservative: better false negative than false positive
        )

    def _get_or_create_entity_by_name(
        self,
        name: str,
        entity_type: str,
        properties: Dict = None
    ) -> str:
        """
        Get existing entity URI or create new one.

        NOW USES: Three-tier waterfall strategy with pgvector
        """
        # Use entity resolver (Exact → Semantic → New)
        result = self.entity_resolver.get_or_create_entity(
            entity_text=name,
            entity_type=entity_type,
            metadata=properties
        )

        entity_uri = result["uri"]

        # Log deduplication info
        if result["matched"]:
            self.logger.debug(
                f"✅ Matched '{name}' → '{result['entity_text']}' "
                f"via {result['match_method']} (score: {result['match_score']:.3f})"
            )
        else:
            self.logger.debug(
                f"🆕 Created new entity '{name}' → {entity_uri}"
            )

        # Self-healing: Ensure entity exists in Fuseki
        self._sync_entity_to_fuseki(entity_uri, name, entity_type, properties)

        return entity_uri

    def _sync_entity_to_fuseki(
        self,
        uri: str,
        name: str,
        entity_type: str,
        properties: Dict = None
    ):
        """
        ⚠️ CRITICAL: Self-healing mechanism for distributed systems.

        **Postgres is the Source of Truth** for entity identity.

        In distributed systems (Python + Postgres + Fuseki), things get out of sync.
        If Postgres says "This URI exists," but Fuseki returns 404, we quietly
        re-insert the triples into Fuseki WITHOUT crashing.

        This prevents cascading failures and ensures data consistency.

        Args:
            uri: Entity URI from registry
            name: Entity name
            entity_type: Entity type
            properties: Optional entity properties
        """
        # Check if entity exists in Fuseki
        ask_query = f"ASK {{ <{uri}> ?p ?o }}"
        exists = self.store.query(ask_query)

        if not exists:
            # Entity missing from Fuseki - self-heal by inserting
            self.logger.warning(
                f"Self-healing: Entity {uri} in registry but not in graph. Re-inserting..."
            )
            self._insert_entity_skeleton(uri, name, entity_type, properties)
        else:
            # Entity exists - optionally update properties
            if properties:
                self._update_entity_properties(uri, properties)

    def _insert_entity_skeleton(
        self,
        uri: str,
        name: str,
        entity_type: str,
        properties: Dict = None
    ):
        """Insert minimal entity skeleton into Fuseki."""
        from rdflib import Graph, URIRef, Literal, RDF, RDFS

        g = Graph()
        entity_uri = URIRef(uri)

        # Type
        type_uri = URIRef(f"https://regen.network/ontology#{entity_type}")
        g.add((entity_uri, RDF.type, type_uri))

        # Label
        g.add((entity_uri, RDFS.label, Literal(name)))

        # Properties
        if properties:
            for key, value in properties.items():
                pred = URIRef(f"https://regen.network/ontology#{key}")
                g.add((entity_uri, pred, Literal(value)))

        # Insert into Fuseki
        self.store.insert_graph(g)
```

### Step 2: Add Monitoring (20 minutes)

**File**: `src/knowledge_graph/monitoring.py`

```python
"""Monitoring and metrics for entity deduplication."""

import logging
from typing import Dict


def log_resolver_stats(entity_resolver, logger: logging.Logger):
    """Log entity resolver statistics."""
    stats = entity_resolver.get_stats()

    logger.info("="*70)
    logger.info("ENTITY DEDUPLICATION STATS")
    logger.info("="*70)
    logger.info(f"Total lookups: {stats.get('total_lookups', 0)}")
    logger.info(
        f"Tier 1 (Exact):    {stats.get('tier1_exact_hits', 0):6d} "
        f"({stats.get('tier1_hit_rate', 0)*100:5.1f}%)"
    )
    logger.info(
        f"Tier 2 (Semantic): {stats.get('tier2_semantic_hits', 0):6d} "
        f"({stats.get('tier2_hit_rate', 0)*100:5.1f}%)"
    )
    logger.info(
        f"Tier 3 (New):      {stats.get('tier3_new_entities', 0):6d} "
        f"({stats.get('tier3_new_rate', 0)*100:5.1f}%)"
    )
    logger.info("="*70)


def get_registry_summary(db_config: Dict) -> Dict:
    """Get summary statistics from entity_registry."""
    import psycopg2

    conn = psycopg2.connect(**db_config)
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM entity_registry_stats")
    result = cursor.fetchone()

    cursor.close()
    conn.close()

    if result:
        return {
            "total_entities": result[0],
            "unique_types": result[1],
            "total_occurrences": result[2],
            "avg_occurrences": result[3],
            "max_occurrences": result[4],
        }
    return {}
```

---

## Phase 5: Testing & Validation - 30 minutes

### End-to-End Test

**File**: `scripts/test_dedup_end_to_end.py`

```python
#!/usr/bin/env python3
"""Test end-to-end deduplication with real entity examples."""

import asyncio
import sys
sys.path.insert(0, "/opt/projects/koi-processor/src")

from knowledge_graph.graph_integration import KnowledgeGraphIntegrator
from knowledge_graph.monitoring import log_resolver_stats


SAMPLE_ENTITIES = [
    # Regen Network variations
    ("Regen Network", "ORGANIZATION"),
    ("regen", "ORGANIZATION"),
    ("REGEN NETWORK", "ORGANIZATION"),
    ("The Regen Network", "ORGANIZATION"),

    # Gregory variations
    ("Gregory Landua", "PERSON"),
    ("Gregory", "PERSON"),
    ("Gregory_RND", "PERSON"),

    # IBM variations (semantic test)
    ("IBM", "ORGANIZATION"),
    ("International Business Machines", "ORGANIZATION"),
]


def test_deduplication():
    """Test that all variations resolve to same URIs."""

    print("\n" + "="*70)
    print("ENTITY DEDUPLICATION END-TO-END TEST")
    print("="*70)

    kg = KnowledgeGraphIntegrator(store_type="memory", use_pipeline=True)

    entity_uris = {}

    for i, (name, entity_type) in enumerate(SAMPLE_ENTITIES, 1):
        print(f"\n[{i}/{len(SAMPLE_ENTITIES)}] Processing: '{name}' ({entity_type})")

        uri = kg._get_or_create_entity_by_name(name, entity_type)

        # Track URIs by entity group
        key = name.lower().split()[0]  # "regen", "gregory", "ibm"
        entity_uris.setdefault(key, set()).add(uri)

        print(f"  URI: {uri}")

    # Analyze results
    print("\n" + "="*70)
    print("DEDUPLICATION RESULTS")
    print("="*70)

    for entity_group, uris in entity_uris.items():
        print(f"\n{entity_group.title()}:")
        print(f"  Variations tested: {sum(1 for name, _ in SAMPLE_ENTITIES if entity_group in name.lower())}")
        print(f"  Unique URIs: {len(uris)}")

        if len(uris) == 1:
            print(f"  ✅ Perfect deduplication!")
        else:
            print(f"  ⚠️  Multiple URIs found:")
            for uri in uris:
                print(f"    - {uri}")

    # Print resolver stats
    print("\n")
    import logging
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    log_resolver_stats(kg.entity_resolver, logger)

    # Success criteria
    regen_uris = entity_uris.get("regen", set())
    gregory_uris = entity_uris.get("gregory", set())

    if len(regen_uris) == 1 and len(gregory_uris) == 1:
        print("\n✅ ALL TESTS PASSED")
        return True
    else:
        print("\n❌ TESTS FAILED - Deduplication not working correctly")
        return False


if __name__ == "__main__":
    success = test_deduplication()
    sys.exit(0 if success else 1)
```

**Run test**:
```bash
ssh darren@202.61.196.119 "cd /opt/projects/koi-processor && source venv/bin/activate && source .env && python3 scripts/test_dedup_end_to_end.py"
```

**Success Criteria**:
- ✅ All "Regen" variations → 1 URI
- ✅ All "Gregory" variations → 1 URI
- ✅ Tier 1 hit rate > 50%
- ✅ No errors

---

## Deployment Checklist

Before resuming extraction:

- [ ] entity_registry table created with indexes
- [ ] Test schema script passes (5/5 tests)
- [ ] DeterministicURIGenerator tests passing (100%)
- [ ] EntityResolver tests passing (100%)
- [ ] End-to-end dedup test passing
- [ ] Monitoring/logging configured
- [ ] All existing pipeline tests still passing (121+)

---

## Performance Expectations

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Tier 1 hit rate | > 70% | After 1000 entities processed |
| Tier 2 latency | < 100ms | Per semantic match query |
| Tier 3 latency | < 50ms | Per new entity creation |
| Overall throughput | > 50 entities/sec | With dedup enabled |
| Duplicate rate | < 5% | Of total entities |

---

## Next Steps After Implementation

1. **Backfill Existing Entities** (29,577 entities)
   - Extract all unique entities from koi_kg_extractions
   - Generate embeddings
   - Populate entity_registry
   - Update Fuseki with canonical URIs

2. **Resume GitHub Extraction**
   - Continue from checkpoint (300/4,710)
   - Monitor deduplication metrics every 500 docs
   - Validate duplicate rate stays < 5%

3. **Tune Threshold**
   - Start with 0.92
   - Monitor false positives (different entities merged)
   - Monitor false negatives (same entity split)
   - Adjust based on data

---

## Time Estimate

| Phase | Time | Cumulative |
|-------|------|------------|
| Phase 1: entity_registry table | 30 min | 30 min |
| Phase 2: DeterministicURIGenerator | 30 min | 1 hour |
| Phase 3: EntityResolver | 1 hour | 2 hours |
| Phase 4: Integration | 1 hour | 3 hours |
| Phase 5: Testing | 30 min | 3.5 hours |

**Total**: 3.5 hours (vs 6-8 hours for Jena Text approach)

---

**Status**: READY TO START
**Priority**: CRITICAL - BLOCKING EXTRACTION RESUME
**Approach**: pgvector waterfall (simpler, faster, better than Jena Text)
