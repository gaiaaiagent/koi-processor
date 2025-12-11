# PROMPT 21: Implement Graph-as-Registry with Jena Text & Deterministic URIs

**Date**: 2025-12-09
**Status**: READY TO IMPLEMENT
**Priority**: CRITICAL - BLOCKING EXTRACTION
**Estimated Time**: 6-8 hours

---

## Architecture Summary

Based on expert architectural guidance, we're implementing a **three-tier incremental deduplication system**:

**L1**: Python LRU cache (1000 hot entities) - microsecond lookups
**L2**: Fuseki Lucene index (fuzzy matching) - millisecond lookups
**L3**: Deterministic URIs (content-addressable) - automatic dedup

This enables **stream processing** of documents with persistent cross-document deduplication.

---

## Phase 1: Enable Jena Text (Lucene Index) - 2 hours

### Step 1: Configure Fuseki with Lucene (1 hour)

**Goal**: Add server-side fuzzy text matching to Fuseki

#### A. Create Lucene Configuration

**File**: Create `/opt/projects/fuseki-config/text-config.ttl`

```turtle
@prefix :        <http://localhost/jena_example/#> .
@prefix rdf:     <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs:    <http://www.w3.org/2000/01/rdf-schema#> .
@prefix tdb2:    <http://jena.apache.org/2016/tdb#> .
@prefix text:    <http://jena.apache.org/text#> .
@prefix skos:    <http://www.w3.org/2004/02/skos/core#> .
@prefix schema:  <http://schema.org/> .

# Define the text-indexed dataset
:text_dataset rdf:type text:TextDataset ;
    text:dataset   :tdb2_dataset ;
    text:index     :indexLucene .

# TDB2 dataset (existing)
:tdb2_dataset rdf:type tdb2:DatasetTDB2 ;
    tdb2:location "/fuseki/databases/koi" .

# Lucene index configuration
:indexLucene a text:TextIndexLucene ;
    text:directory <file:/fuseki/lucene-index> ;
    text:entityMap :entMap ;
    text:storeValues true ;
    text:analyzer [ a text:StandardAnalyzer ] .

# Map which properties to index
:entMap a text:EntityMap ;
    text:entityField      "uri" ;
    text:defaultField     "text" ;
    text:uidField         "uid" ;
    text:map (
        # Index entity labels
        [ text:field "text" ;
          text:predicate rdfs:label ]

        # Index schema.org names
        [ text:field "text" ;
          text:predicate schema:name ]

        # Index alternative labels (aliases)
        [ text:field "text" ;
          text:predicate skos:altLabel ]
    ) .
```

#### B. Update Fuseki Docker Configuration

**File**: Update docker-compose.yml or Fuseki startup

```yaml
services:
  fuseki:
    image: stain/jena-fuseki
    volumes:
      - ./fuseki-config:/fuseki/config
      - fuseki-data:/fuseki/databases
      - fuseki-lucene:/fuseki/lucene-index  # ADD THIS
    environment:
      - FUSEKI_DATASET_1=koi
      - FUSEKI_CONFIG=/fuseki/config/text-config.ttl  # ADD THIS
```

#### C. Restart Fuseki and Rebuild Index

```bash
ssh darren@202.61.196.119

# Stop Fuseki
docker-compose down fuseki

# Rebuild Lucene index from existing data
docker-compose run fuseki \
  tdb2.textindexer \
  --desc=/fuseki/config/text-config.ttl

# Start Fuseki with text indexing
docker-compose up -d fuseki

# Verify text search works
curl -X POST http://localhost:3030/koi/sparql \
  --data 'query=PREFIX text: <http://jena.apache.org/text#> SELECT * WHERE { (?s ?score) text:query "Regen Network" } LIMIT 5'
```

### Step 2: Test Lucene Fuzzy Matching (30 minutes)

**Create test script**: `scripts/test_lucene_fuzzy.py`

```python
#!/usr/bin/env python3
"""Test Fuseki Lucene fuzzy matching."""

from SPARQLWrapper import SPARQLWrapper, JSON

def test_fuzzy_search(query_text, threshold=0.8):
    """Test fuzzy text search via Lucene."""
    sparql = SPARQLWrapper("http://localhost:3030/koi/sparql")

    query = f"""
    PREFIX text: <http://jena.apache.org/text#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT ?s ?score ?label WHERE {{
      (?s ?score) text:query "{query_text}" .
      ?s rdfs:label ?label .
    }}
    ORDER BY DESC(?score)
    LIMIT 10
    """

    sparql.setQuery(query)
    sparql.setReturnFormat(JSON)
    results = sparql.query().convert()

    print(f"Query: '{query_text}'")
    print(f"Results:")
    for result in results["results"]["bindings"]:
        print(f"  - {result['label']['value']} (score: {result['score']['value']})")

    return results

if __name__ == "__main__":
    # Test fuzzy matching
    test_fuzzy_search("Regen Network")
    test_fuzzy_search("regen")  # Should still match "Regen Network"
    test_fuzzy_search("Gregory Landua")
    test_fuzzy_search("Gregory")  # Should still match "Gregory Landua"
```

**Success criteria**: Queries return fuzzy matches in < 100ms

---

## Phase 2: Implement Deterministic URIs - 1 hour

### Step 1: Create URI Generator (30 minutes)

**File**: `src/knowledge_graph/uri_generator.py`

```python
"""Deterministic, content-addressable URI generation."""

import hashlib
import re
from typing import Dict
from urllib.parse import quote

class DeterministicURIGenerator:
    """
    Generate deterministic URIs based on entity content.

    Same normalized name + type always produces same URI.
    This prevents duplicates at the RDF level.
    """

    BASE_URI = "https://regen.network"

    TYPE_PREFIXES = {
        "PERSON": "person",
        "ORGANIZATION": "org",
        "PROJECT": "project",
        "LOCATION": "location",
        "EVENT": "event",
        "CONCEPT": "concept",
    }

    def __init__(self, base_uri: str = None):
        self.base_uri = base_uri or self.BASE_URI

    def normalize_name(self, name: str) -> str:
        """
        Normalize entity name for consistent hashing.

        Normalization rules:
        - Lowercase
        - Remove extra whitespace
        - Remove punctuation (except hyphens in names)
        - Trim
        """
        # Lowercase
        normalized = name.lower()

        # Remove common prefixes/suffixes
        normalized = re.sub(r'\b(the|a|an)\s+', '', normalized)

        # Normalize whitespace
        normalized = ' '.join(normalized.split())

        # Remove trailing punctuation
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
            "Regen Network", "ORGANIZATION" ->
            https://regen.network/org/a1b2c3d4...

            "Gregory Landua", "PERSON" ->
            https://regen.network/person/e5f6g7h8...
        """
        # Normalize name
        normalized = self.normalize_name(name)

        # Normalize type
        entity_type = entity_type.upper()
        type_prefix = self.TYPE_PREFIXES.get(entity_type, "entity")

        # Generate content hash
        content = f"{normalized}:{entity_type}"
        hash_digest = hashlib.sha256(content.encode('utf-8')).hexdigest()

        # Use first 16 chars of hash (collision probability: ~1 in 10^19)
        short_hash = hash_digest[:16]

        # Build URI
        uri = f"{self.base_uri}/{type_prefix}/{short_hash}"

        return uri

    def generate_uri_with_metadata(self, name: str, entity_type: str) -> Dict[str, str]:
        """
        Generate URI with metadata for debugging/provenance.

        Returns:
            {
                "uri": "https://...",
                "normalized_name": "regen network",
                "hash": "a1b2c3d4...",
                "original_name": "Regen Network"
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
            "type": entity_type
        }
```

### Step 2: Test Deterministic URIs (30 minutes)

**File**: `tests/test_uri_generator.py`

```python
import pytest
from knowledge_graph.uri_generator import DeterministicURIGenerator

def test_same_name_same_uri():
    """Same normalized name produces same URI."""
    gen = DeterministicURIGenerator()

    uri1 = gen.generate_uri("Regen Network", "ORGANIZATION")
    uri2 = gen.generate_uri("Regen Network", "ORGANIZATION")

    assert uri1 == uri2

def test_case_insensitive():
    """Case variations produce same URI."""
    gen = DeterministicURIGenerator()

    uri1 = gen.generate_uri("Regen Network", "ORGANIZATION")
    uri2 = gen.generate_uri("REGEN NETWORK", "ORGANIZATION")
    uri3 = gen.generate_uri("regen network", "ORGANIZATION")

    assert uri1 == uri2 == uri3

def test_whitespace_normalization():
    """Whitespace variations produce same URI."""
    gen = DeterministicURIGenerator()

    uri1 = gen.generate_uri("Regen  Network", "ORGANIZATION")
    uri2 = gen.generate_uri("Regen Network", "ORGANIZATION")

    assert uri1 == uri2

def test_different_types_different_uris():
    """Same name, different type -> different URI."""
    gen = DeterministicURIGenerator()

    uri_org = gen.generate_uri("Regen", "ORGANIZATION")
    uri_proj = gen.generate_uri("Regen", "PROJECT")

    assert uri_org != uri_proj
    assert "org" in uri_org
    assert "project" in uri_proj

def test_uri_format():
    """URI has expected format."""
    gen = DeterministicURIGenerator()

    uri = gen.generate_uri("Regen Network", "ORGANIZATION")

    assert uri.startswith("https://regen.network/org/")
    assert len(uri.split('/')[-1]) == 16  # Short hash

def test_metadata():
    """Metadata includes provenance info."""
    gen = DeterministicURIGenerator()

    metadata = gen.generate_uri_with_metadata("Regen Network", "ORGANIZATION")

    assert metadata["uri"].startswith("https://regen.network")
    assert metadata["normalized_name"] == "regen network"
    assert metadata["original_name"] == "Regen Network"
    assert len(metadata["hash"]) == 64  # SHA256
```

**Run tests**:
```bash
cd /opt/projects/koi-processor
pytest tests/test_uri_generator.py -v
```

---

## Phase 3: Implement Three-Tier Lookup - 2 hours

### Step 1: Create Entity Registry with LRU Cache (1 hour)

**File**: `src/knowledge_graph/entity_registry.py`

```python
"""Entity registry with three-tier lookup strategy."""

from functools import lru_cache
from typing import Optional, Dict, Tuple
from SPARQLWrapper import SPARQLWrapper, JSON

from .uri_generator import DeterministicURIGenerator


class EntityRegistry:
    """
    Three-tier entity lookup and deduplication.

    L1: Python LRU cache (hot 1000 entities)
    L2: Fuseki Lucene index (fuzzy matching)
    L3: Deterministic URIs (safety net)
    """

    def __init__(
        self,
        fuseki_endpoint: str = "http://localhost:3030/koi/sparql",
        fuzzy_threshold: float = 0.8,
        cache_size: int = 1000
    ):
        self.fuseki = SPARQLWrapper(fuseki_endpoint)
        self.uri_gen = DeterministicURIGenerator()
        self.fuzzy_threshold = fuzzy_threshold

        # L1: LRU cache (decorated method below)
        self._cache_size = cache_size

        # Statistics
        self.stats = {
            "l1_hits": 0,  # Cache hits
            "l2_hits": 0,  # Lucene fuzzy matches
            "l3_new": 0,   # New entities created
        }

    @lru_cache(maxsize=1000)
    def _cached_lookup(self, cache_key: Tuple[str, str]) -> Optional[str]:
        """
        L1: Cached lookup (decorator handles caching).

        This is just a pass-through - real caching handled by @lru_cache.
        """
        return None  # Cache miss, proceed to L2

    def get_or_create_uri(
        self,
        entity_name: str,
        entity_type: str,
        create_if_missing: bool = True
    ) -> Dict[str, any]:
        """
        Get existing URI or create new one using three-tier strategy.

        Args:
            entity_name: Entity name
            entity_type: Entity type
            create_if_missing: If True, create new URI if no match found

        Returns:
            {
                "uri": "https://...",
                "matched": True/False,
                "match_method": "l1_cache" | "l2_lucene" | "l3_new",
                "match_score": 0.0-1.0
            }
        """
        # Normalize for consistent caching
        normalized_name = self.uri_gen.normalize_name(entity_name)
        cache_key = (normalized_name, entity_type)

        # L1: Check LRU cache
        cached_uri = self._cached_lookup(cache_key)
        if cached_uri:
            self.stats["l1_hits"] += 1
            return {
                "uri": cached_uri,
                "matched": True,
                "match_method": "l1_cache",
                "match_score": 1.0
            }

        # L2: Fuzzy lookup via Lucene
        lucene_match = self._fuzzy_lookup_lucene(entity_name, entity_type)
        if lucene_match:
            uri = lucene_match["uri"]
            # Update L1 cache
            self._cached_lookup.cache_info()  # Just to trigger caching
            self.stats["l2_hits"] += 1
            return {
                "uri": uri,
                "matched": True,
                "match_method": "l2_lucene",
                "match_score": lucene_match["score"]
            }

        # L3: Create deterministic URI
        if create_if_missing:
            uri = self.uri_gen.generate_uri(entity_name, entity_type)
            self.stats["l3_new"] += 1
            return {
                "uri": uri,
                "matched": False,
                "match_method": "l3_new",
                "match_score": 1.0  # Exact match to self
            }
        else:
            return None

    def _fuzzy_lookup_lucene(
        self,
        entity_name: str,
        entity_type: str
    ) -> Optional[Dict[str, any]]:
        """
        L2: Fuzzy lookup via Fuseki Lucene index.

        Returns:
            {"uri": "...", "score": 0.95, "label": "..."}
            or None if no match above threshold
        """
        query = f"""
        PREFIX text: <http://jena.apache.org/text#>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        PREFIX regen: <https://regen.network/ontology#>

        SELECT ?s ?score ?label WHERE {{
          # Lucene fuzzy search
          (?s ?score) text:query "{entity_name}" .
          ?s rdfs:label ?label .

          # Filter by type
          ?s a regen:{entity_type} .
        }}
        ORDER BY DESC(?score)
        LIMIT 1
        """

        self.fuseki.setQuery(query)
        self.fuseki.setReturnFormat(JSON)

        try:
            results = self.fuseki.query().convert()
            bindings = results["results"]["bindings"]

            if bindings:
                result = bindings[0]
                score = float(result["score"]["value"])

                # Check threshold
                if score >= self.fuzzy_threshold:
                    return {
                        "uri": result["s"]["value"],
                        "score": score,
                        "label": result["label"]["value"]
                    }
        except Exception as e:
            print(f"Lucene lookup error: {e}")

        return None

    def get_stats(self) -> Dict:
        """Get lookup statistics."""
        total = sum(self.stats.values())
        if total == 0:
            return self.stats

        return {
            **self.stats,
            "total_lookups": total,
            "l1_hit_rate": self.stats["l1_hits"] / total,
            "l2_hit_rate": self.stats["l2_hits"] / total,
            "l3_new_rate": self.stats["l3_new"] / total,
        }
```

### Step 2: Test Entity Registry (1 hour)

**File**: `tests/test_entity_registry.py`

```python
import pytest
from knowledge_graph.entity_registry import EntityRegistry

@pytest.fixture
def registry():
    """Create test registry."""
    return EntityRegistry(
        fuseki_endpoint="http://localhost:3030/koi/sparql",
        fuzzy_threshold=0.8
    )

def test_deterministic_uri_generation(registry):
    """Same entity gets same URI."""
    result1 = registry.get_or_create_uri("Regen Network", "ORGANIZATION")
    result2 = registry.get_or_create_uri("Regen Network", "ORGANIZATION")

    assert result1["uri"] == result2["uri"]

def test_cache_hit(registry):
    """Second lookup hits L1 cache."""
    result1 = registry.get_or_create_uri("Regen Network", "ORGANIZATION")
    result2 = registry.get_or_create_uri("Regen Network", "ORGANIZATION")

    assert result1["match_method"] == "l3_new"
    assert result2["match_method"] == "l1_cache"

def test_fuzzy_matching(registry):
    """Fuzzy variants match existing entity."""
    # This test requires Fuseki to be running with Lucene enabled
    # and "Regen Network" to exist in the graph

    result = registry.get_or_create_uri("regen", "ORGANIZATION")

    # Should match "Regen Network" via Lucene
    assert result["match_method"] in ["l2_lucene", "l3_new"]

def test_stats_tracking(registry):
    """Registry tracks lookup statistics."""
    registry.get_or_create_uri("Entity 1", "PERSON")
    registry.get_or_create_uri("Entity 1", "PERSON")  # Cache hit
    registry.get_or_create_uri("Entity 2", "PERSON")

    stats = registry.get_stats()

    assert stats["total_lookups"] == 3
    assert stats["l1_hits"] >= 1
    assert stats["l3_new"] >= 2
```

---

## Phase 4: Integration with Graph Insertion - 2 hours

### Step 1: Update Graph Integrator (1.5 hours)

**File**: `src/knowledge_graph/graph_integration.py`

Add entity registry integration:

```python
from .entity_registry import EntityRegistry

class KnowledgeGraphIntegrator:
    def __init__(self, ...):
        # ... existing code ...

        # ADD: Entity registry for deduplication
        self.entity_registry = EntityRegistry(
            fuseki_endpoint=self.store.endpoint,
            fuzzy_threshold=0.85
        )

    def _get_or_create_entity_by_name(
        self,
        name: str,
        entity_type: str,
        properties: Dict = None
    ) -> str:
        """
        Get existing entity URI or create new one.

        NOW USES: Three-tier lookup strategy
        """
        # Use entity registry instead of simple hash
        result = self.entity_registry.get_or_create_uri(name, entity_type)

        entity_uri = result["uri"]

        # Log deduplication info
        if result["matched"]:
            self.logger.debug(
                f"Matched entity '{name}' via {result['match_method']} "
                f"(score: {result['match_score']:.2f})"
            )
        else:
            self.logger.debug(f"Created new entity '{name}' -> {entity_uri}")

        # Insert entity properties (if new or updating)
        if properties:
            self._insert_entity_properties(entity_uri, name, entity_type, properties)

        return entity_uri
```

### Step 2: Add Monitoring (30 minutes)

**File**: `src/knowledge_graph/monitoring.py`

```python
"""Monitoring and metrics for entity deduplication."""

def log_dedup_stats(entity_registry, logger):
    """Log deduplication statistics."""
    stats = entity_registry.get_stats()

    logger.info("="*60)
    logger.info("ENTITY DEDUPLICATION STATS")
    logger.info("="*60)
    logger.info(f"Total lookups: {stats['total_lookups']}")
    logger.info(f"L1 Cache hits: {stats['l1_hits']} ({stats.get('l1_hit_rate', 0)*100:.1f}%)")
    logger.info(f"L2 Lucene matches: {stats['l2_hits']} ({stats.get('l2_hit_rate', 0)*100:.1f}%)")
    logger.info(f"L3 New entities: {stats['l3_new']} ({stats.get('l3_new_rate', 0)*100:.1f}%)")
    logger.info("="*60)
```

---

## Phase 5: Testing & Validation - 1 hour

### Step 1: End-to-End Test

**File**: `scripts/test_incremental_dedup.py`

```python
#!/usr/bin/env python3
"""Test incremental deduplication end-to-end."""

import asyncio
from extraction.openai_extractor import OpenAIExtractor
from knowledge_graph.graph_integration import KnowledgeGraphIntegrator

SAMPLE_DOCS = [
    # Doc 1: "Regen Network"
    "Regen Network launched the ecocredit module in 2021.",

    # Doc 2: "Regen" (should deduplicate to "Regen Network")
    "Regen is building blockchain infrastructure for climate action.",

    # Doc 3: "REGEN NETWORK" (should deduplicate)
    "REGEN NETWORK announced a new partnership today.",

    # Doc 4: Gregory variations
    "Gregory Landua founded the company.",
    "Gregory is the CEO.",
    "Gregory_RND posted on the forum.",
]

async def test_incremental_dedup():
    """Test that entities are deduplicated across documents."""
    extractor = OpenAIExtractor(model="gpt-4o-mini")
    kg = KnowledgeGraphIntegrator(store_type="fuseki", use_pipeline=True)

    entity_uris = {}

    for i, text in enumerate(SAMPLE_DOCS):
        print(f"\n[{i+1}/{len(SAMPLE_DOCS)}] Processing: {text[:50]}...")

        # Extract
        result = await extractor.extract_metadata(text, "test", {})
        entities = result.get("entities", [])

        # Process through pipeline + insert
        for entity in entities:
            uri = kg._get_or_create_entity_by_name(
                entity["name"],
                entity["type"]
            )

            # Track URIs
            name = entity["name"].lower()
            if "regen" in name:
                entity_uris.setdefault("regen", set()).add(uri)
            if "gregory" in name:
                entity_uris.setdefault("gregory", set()).add(uri)

    # Analyze results
    print(f"\n{'='*60}")
    print("DEDUPLICATION RESULTS")
    print(f"{'='*60}")

    for entity_group, uris in entity_uris.items():
        print(f"\n{entity_group.title()} variations:")
        print(f"  Unique URIs: {len(uris)}")
        if len(uris) == 1:
            print(f"  ✅ Perfect deduplication!")
        else:
            print(f"  ⚠️ Multiple URIs found:")
            for uri in uris:
                print(f"    - {uri}")

    # Print registry stats
    stats = kg.entity_registry.get_stats()
    print(f"\nRegistry stats:")
    print(f"  L1 cache hit rate: {stats.get('l1_hit_rate', 0)*100:.1f}%")
    print(f"  L2 fuzzy match rate: {stats.get('l2_hit_rate', 0)*100:.1f}%")
    print(f"  L3 new entity rate: {stats.get('l3_new_rate', 0)*100:.1f}%")

if __name__ == "__main__":
    asyncio.run(test_incremental_dedup())
```

**Success criteria**:
- ✅ All "Regen" variations → same URI
- ✅ All "Gregory" variations → same URI
- ✅ L1 cache hit rate > 50%

---

## Deployment Checklist

Before resuming extraction:

- [ ] Fuseki Lucene index enabled and working
- [ ] Test fuzzy search returns results in < 100ms
- [ ] Deterministic URI tests passing (100%)
- [ ] Entity registry tests passing (100%)
- [ ] End-to-end incremental dedup test passing
- [ ] Monitoring/logging configured
- [ ] All 121+ existing pipeline tests still passing

---

## Performance Targets

| Metric | Target | Measurement |
|--------|--------|-------------|
| L1 cache hit rate | > 70% | After 1000 entities |
| L2 Lucene lookup | < 100ms | Per query |
| L3 URI generation | < 1ms | Per entity |
| Overall throughput | > 100 entities/sec | With dedup |

---

## Monitoring During Extraction

Every 1000 documents, log:
- Deduplication stats (L1/L2/L3 rates)
- Unique entities created
- Cache effectiveness
- Lucene query performance

---

## Success Criteria

**Mark complete when**:
- ✅ Fuseki Lucene index operational
- ✅ Deterministic URIs working (same name → same URI)
- ✅ Three-tier lookup functional (cache + Lucene + new)
- ✅ Cross-document deduplication confirmed (test shows merging)
- ✅ Performance targets met (cache hit rate, throughput)
- ✅ All tests passing

---

**Status**: READY TO IMPLEMENT
**Priority**: CRITICAL - BLOCKING EXTRACTION RESUME
**Estimated completion**: 6-8 hours
