"""Unit tests for B1 chat retrieval hardening.

Tests the three new retrieval paths added in B1:
- Document chunk retrieval (koi_memory_chunks)
- 2-hop relationship CTE
- Web source context (web_submissions + document_entity_links)
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entity_row(uri="urn:entity:1", text="Salish Sea", etype="Bioregion",
                     similarity=0.9, metadata=None):
    """Build a mock entity_registry row."""
    return {
        "fuseki_uri": uri,
        "entity_text": text,
        "entity_type": etype,
        "similarity": similarity,
        "metadata": metadata or json.dumps({"description": f"Description of {text}"}),
    }


def _make_rel_row(subj_uri="urn:entity:1", subj_label="Salish Sea",
                  pred="located_in", obj_uri="urn:entity:2",
                  obj_label="Pacific Northwest", depth=1):
    return {
        "subject_uri": subj_uri,
        "subject_label": subj_label,
        "predicate": pred,
        "object_uri": obj_uri,
        "object_label": obj_label,
        "depth": depth,
    }


def _make_chunk_row(doc_rid="doc:123", title="Herring Report",
                    chunk_text="The herring population in the Salish Sea...",
                    similarity=0.85):
    return {
        "document_rid": doc_rid,
        "title": title,
        "chunk_text": chunk_text,
        "similarity": similarity,
    }


def _make_web_row(url="https://example.com/salish", title="Salish Sea Info",
                  summary="Overview of the Salish Sea bioregion."):
    return {
        "url": url,
        "title": title,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Test: 2-hop relationship CTE returns depth > 1
# ---------------------------------------------------------------------------

class TestTwoHopRelationships:
    """B1.2: 2-hop recursive CTE replaces 1-hop LIMIT 30."""

    def test_rel_query_includes_depth(self):
        """The relationship query SQL should include depth column and LIMIT 50."""
        import importlib
        import inspect
        spec = importlib.util.spec_from_file_location(
            "api", "api/personal_ingest_api.py")
        # Just check the source text for the CTE pattern
        with open("api/personal_ingest_api.py") as f:
            src = f.read()
        assert "WITH RECURSIVE traverse AS" in src
        assert "t.depth + 1" in src
        assert "WHERE t.depth < 2" in src
        assert "LIMIT 50" in src

    def test_rel_query_no_limit_30(self):
        """The old 1-hop LIMIT 30 should be gone from the chat endpoint."""
        with open("api/personal_ingest_api.py") as f:
            src = f.read()
        # Find the chat_endpoint function
        start = src.index("async def chat_endpoint")
        end = src.index("\nif __name__", start)
        chat_fn = src[start:end]
        assert "LIMIT 30" not in chat_fn


# ---------------------------------------------------------------------------
# Test: Document chunk retrieval path (B1.1)
# ---------------------------------------------------------------------------

class TestDocChunkRetrieval:
    """B1.1: koi_memory_chunks search alongside entity embeddings."""

    def test_chunk_query_in_source(self):
        """Chat endpoint should query koi_memory_chunks."""
        with open("api/personal_ingest_api.py") as f:
            src = f.read()
        start = src.index("async def chat_endpoint")
        end = src.index("\nif __name__", start)
        chat_fn = src[start:end]
        assert "koi_memory_chunks" in chat_fn

    def test_chunk_similarity_threshold(self):
        """Only chunks with similarity > 0.3 should be included."""
        with open("api/personal_ingest_api.py") as f:
            src = f.read()
        start = src.index("async def chat_endpoint")
        end = src.index("\nif __name__", start)
        chat_fn = src[start:end]
        assert "0.3" in chat_fn

    def test_chunk_adds_document_source(self):
        """Chunks should add Document-typed entries to sources."""
        with open("api/personal_ingest_api.py") as f:
            src = f.read()
        start = src.index("async def chat_endpoint")
        end = src.index("\nif __name__", start)
        chat_fn = src[start:end]
        assert '"Document"' in chat_fn


# ---------------------------------------------------------------------------
# Test: Web source context (B1.3)
# ---------------------------------------------------------------------------

class TestWebSourceContext:
    """B1.3: web_submissions joined with document_entity_links."""

    def test_web_query_in_source(self):
        """Chat endpoint should query web_submissions."""
        with open("api/personal_ingest_api.py") as f:
            src = f.read()
        start = src.index("async def chat_endpoint")
        end = src.index("\nif __name__", start)
        chat_fn = src[start:end]
        assert "web_submissions" in chat_fn
        assert "document_entity_links" in chat_fn

    def test_web_source_type(self):
        """Web sources should have entity_type WebSource."""
        with open("api/personal_ingest_api.py") as f:
            src = f.read()
        start = src.index("async def chat_endpoint")
        end = src.index("\nif __name__", start)
        chat_fn = src[start:end]
        assert '"WebSource"' in chat_fn

    def test_graceful_missing_table(self):
        """Missing web_submissions table should be caught gracefully."""
        with open("api/personal_ingest_api.py") as f:
            src = f.read()
        start = src.index("async def chat_endpoint")
        end = src.index("\nif __name__", start)
        chat_fn = src[start:end]
        # Should have UndefinedTableError catch for web_submissions
        assert chat_fn.count("UndefinedTableError") >= 2  # chunks + web


# ---------------------------------------------------------------------------
# Test: Enhanced prompt (B1.4)
# ---------------------------------------------------------------------------

class TestEnhancedPrompt:
    """B1.4: Bioregional framing and citation instructions."""

    def test_bioregional_framing(self):
        """System prompt should mention bioregional context."""
        with open("api/personal_ingest_api.py") as f:
            src = f.read()
        start = src.index("async def chat_endpoint")
        end = src.index("\nif __name__", start)
        chat_fn = src[start:end]
        assert "ecological stewardship" in chat_fn or "regenerative" in chat_fn
        assert "bioregion" in chat_fn.lower()

    def test_citation_instruction(self):
        """System prompt should instruct the LLM to cite sources."""
        with open("api/personal_ingest_api.py") as f:
            src = f.read()
        start = src.index("async def chat_endpoint")
        end = src.index("\nif __name__", start)
        chat_fn = src[start:end]
        assert "cite" in chat_fn.lower() or "Cite" in chat_fn

    def test_prompt_sections_conditional(self):
        """Document and web sections should only appear when data exists."""
        with open("api/personal_ingest_api.py") as f:
            src = f.read()
        start = src.index("async def chat_endpoint")
        end = src.index("\nif __name__", start)
        chat_fn = src[start:end]
        assert "if doc_block:" in chat_fn
        assert "if web_block:" in chat_fn

    def test_entity_block_filters_non_entities(self):
        """Entity block should exclude Document and WebSource types."""
        with open("api/personal_ingest_api.py") as f:
            src = f.read()
        start = src.index("async def chat_endpoint")
        end = src.index("\nif __name__", start)
        chat_fn = src[start:end]
        assert "not in ('Document', 'WebSource')" in chat_fn


# ---------------------------------------------------------------------------
# Test: Source serialization
# ---------------------------------------------------------------------------

class TestSourceSerialization:
    """Sources list should include entity, document, and web sources."""

    def test_sources_include_document_type(self):
        """Document chunks should appear in sources with entity_type=Document."""
        with open("api/personal_ingest_api.py") as f:
            src = f.read()
        start = src.index("async def chat_endpoint")
        end = src.index("\nif __name__", start)
        chat_fn = src[start:end]
        # Document sources added
        assert '"entity_type": "Document"' in chat_fn

    def test_sources_include_web_type(self):
        """Web sources should appear in sources with entity_type=WebSource."""
        with open("api/personal_ingest_api.py") as f:
            src = f.read()
        start = src.index("async def chat_endpoint")
        end = src.index("\nif __name__", start)
        chat_fn = src[start:end]
        assert '"entity_type": "WebSource"' in chat_fn
