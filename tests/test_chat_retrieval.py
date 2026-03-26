"""Behavioral tests for B9a retrieval executors.

Tests the extracted executor functions from api/retrieval_executors.py
using mocked asyncpg connections. These replace the earlier source-text
pattern tests and provide a real safety net for the refactor.
"""

import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.schemas.query_plan import EvidenceBundle, RetrievalOp, SourceType
from api.retrieval_executors import (
    entity_lookup,
    relationship_traverse,
    text_search,
    web_source_lookup,
    evidence_bundles_to_legacy_format,
)


# ---------------------------------------------------------------------------
# Helpers: mock DB row factories
# ---------------------------------------------------------------------------

def _make_entity_row(uri="urn:entity:1", text="Salish Sea", etype="Bioregion",
                     similarity=0.9, metadata=None):
    """Build a mock entity_registry row (dict-like)."""
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


def _make_chunk_row(chunk_id=1, doc_rid="doc:123", title="Herring Report",
                    chunk_text="The herring population in the Salish Sea...",
                    rrf_score=0.032, context="Marine ecology context",
                    section_id=None, section_title=None, wiki_url=None):
    return {
        "id": chunk_id,
        "document_rid": doc_rid,
        "title": title,
        "chunk_text": chunk_text,
        "chunk_context": context,
        "rrf_score": rrf_score,
        "section_id": section_id,
        "section_title": section_title,
        "wiki_url": wiki_url,
    }


def _make_web_row(url="https://example.com/salish", title="Salish Sea Info",
                  description="Overview of the Salish Sea bioregion."):
    return {
        "url": url,
        "title": title,
        "description": description,
    }


# ---------------------------------------------------------------------------
# entity_lookup tests
# ---------------------------------------------------------------------------

class TestEntityLookup:

    @pytest.mark.asyncio
    async def test_entity_lookup_returns_evidence_bundles(self):
        """Vector search returns properly typed EvidenceBundles."""
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[
            _make_entity_row(uri="urn:e:1", text="Eelgrass", etype="Concept", similarity=0.92),
            _make_entity_row(uri="urn:e:2", text="Salish Sea", etype="Bioregion", similarity=0.85),
        ])

        bundles = await entity_lookup(
            "What is eelgrass?",
            [0.1] * 1536,  # mock embedding
            conn,
            max_results=5,
        )

        assert len(bundles) == 2
        assert all(isinstance(b, EvidenceBundle) for b in bundles)
        assert bundles[0].source_uri == "urn:e:1"
        assert bundles[0].source_type == SourceType.LOCAL_AUTHORITATIVE
        assert bundles[0].retrieval_op == RetrievalOp.ENTITY_LOOKUP
        assert bundles[0].confidence == 0.92
        assert bundles[0].metadata["entity_type"] == "Concept"
        assert bundles[0].metadata["label"] == "Eelgrass"
        assert "Description of Eelgrass" in bundles[0].text

    @pytest.mark.asyncio
    async def test_entity_lookup_keyword_fallback(self):
        """When vector search raises UndefinedColumnError, falls back to keyword search."""
        import asyncpg.exceptions

        call_count = 0
        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise asyncpg.exceptions.UndefinedColumnError("embedding column missing")
            return [_make_entity_row(uri="urn:e:fallback", text="Herring", similarity=0.5)]

        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=_side_effect)

        bundles = await entity_lookup(
            "What is herring?",
            [0.1] * 1536,
            conn,
            max_results=5,
        )

        assert call_count == 2  # first call failed, second was keyword fallback
        assert len(bundles) == 1
        assert bundles[0].metadata["label"] == "Herring"

    @pytest.mark.asyncio
    async def test_entity_lookup_empty_results(self):
        """No embedding and no keywords -> empty list, no crash."""
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])

        bundles = await entity_lookup("ab", None, conn, max_results=5)

        assert bundles == []


# ---------------------------------------------------------------------------
# relationship_traverse tests
# ---------------------------------------------------------------------------

class TestRelationshipTraverse:

    @pytest.mark.asyncio
    async def test_relationship_traverse_returns_bundles(self):
        """CTE results become EvidenceBundles with formatted edge text."""
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[
            _make_rel_row(subj_uri="urn:e:1", subj_label="Salish Sea",
                         pred="located_in", obj_uri="urn:e:2",
                         obj_label="Pacific Northwest", depth=1),
            _make_rel_row(subj_uri="urn:e:2", subj_label="Pacific Northwest",
                         pred="contains", obj_uri="urn:e:3",
                         obj_label="Fraser River", depth=2),
        ])

        bundles = await relationship_traverse(["urn:e:1"], conn, max_hops=2)

        assert len(bundles) == 2
        assert all(b.retrieval_op == RetrievalOp.RELATIONSHIP_TRAVERSE for b in bundles)
        assert "Salish Sea --[located_in]--> Pacific Northwest" in bundles[0].text
        assert "Pacific Northwest --[contains]--> Fraser River" in bundles[1].text
        # Closer hops have higher confidence
        assert bundles[0].confidence > bundles[1].confidence
        assert bundles[0].metadata["depth"] == 1
        assert bundles[1].metadata["depth"] == 2

    @pytest.mark.asyncio
    async def test_relationship_traverse_no_entities(self):
        """Empty entity_uris -> immediate empty return, no DB call."""
        conn = AsyncMock()
        conn.fetch = AsyncMock()

        bundles = await relationship_traverse([], conn)

        assert bundles == []
        conn.fetch.assert_not_called()


# ---------------------------------------------------------------------------
# text_search tests
# ---------------------------------------------------------------------------

class TestTextSearch:

    @pytest.mark.asyncio
    async def test_text_search_rrf_fusion(self):
        """RRF fusion combines vector + BM25 results correctly."""
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[
            _make_chunk_row(chunk_id=1, doc_rid="doc:a", title="Doc A",
                           chunk_text="Content A", rrf_score=0.035),
            _make_chunk_row(chunk_id=2, doc_rid="doc:b", title="Doc B",
                           chunk_text="Content B", rrf_score=0.028),
        ])

        bundles = await text_search(
            "herring population",
            [0.1] * 1536,
            conn,
            top_k=8,
        )

        assert len(bundles) == 2
        assert all(b.source_type == SourceType.LOCAL_DOCUMENT for b in bundles)
        assert all(b.retrieval_op == RetrievalOp.TEXT_SEARCH for b in bundles)
        assert bundles[0].source_uri == "doc:a"
        assert bundles[0].metadata["title"] == "Doc A"

    @pytest.mark.asyncio
    async def test_text_search_with_reranking(self):
        """When rerank_fn provided, it filters to top_k."""
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[
            _make_chunk_row(chunk_id=i, doc_rid=f"doc:{i}", title=f"Doc {i}",
                           chunk_text=f"Content {i}", rrf_score=0.03 - i * 0.001)
            for i in range(10)
        ])

        def mock_reranker(query, chunks, top_k=8):
            # Simulate reranker: reverse order, add rerank_score, take top_k
            for i, c in enumerate(chunks[:top_k]):
                c['rerank_score'] = 0.9 - i * 0.05
            return chunks[:top_k]

        bundles = await text_search(
            "herring", [0.1] * 1536, conn,
            top_k=3,
            rerank_fn=mock_reranker,
        )

        # Reranker was called with top_k=3
        assert len(bundles) <= 3

    @pytest.mark.asyncio
    async def test_text_search_bm25_fallback(self):
        """DataError on vector path triggers BM25-only fallback."""
        import asyncpg.exceptions

        call_count = 0
        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise asyncpg.exceptions.DataError("dimension mismatch")
            return [_make_chunk_row(chunk_id=99, doc_rid="doc:bm25",
                                   title="BM25 Result", rrf_score=0.5)]

        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=_side_effect)

        bundles = await text_search(
            "herring", [0.1] * 1536, conn, top_k=8,
        )

        assert call_count == 2  # first failed, second was BM25 fallback
        assert len(bundles) == 1
        assert bundles[0].source_uri == "doc:bm25"

    @pytest.mark.asyncio
    async def test_text_search_code_filtering(self):
        """With include_code=False, code_filter SQL fragment is applied."""
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])

        await text_search("test", [0.1] * 1536, conn, include_code=False)

        # The SQL should contain the code filter
        call_args = conn.fetch.call_args
        sql = call_args[0][0]
        assert "entity_name" in sql  # code filter checks entity_name IS NULL


# ---------------------------------------------------------------------------
# web_source_lookup tests
# ---------------------------------------------------------------------------

class TestWebSourceLookup:

    @pytest.mark.asyncio
    async def test_web_source_lookup_returns_evidence_bundles(self):
        """Web submissions become EvidenceBundles with source_type=LOCAL_WEB."""
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[
            _make_web_row(url="https://example.com/a", title="Page A",
                         description="Description A"),
        ])

        bundles = await web_source_lookup(["urn:e:1"], conn)

        assert len(bundles) == 1
        assert bundles[0].source_type == SourceType.LOCAL_WEB
        assert bundles[0].source_uri == "https://example.com/a"
        assert bundles[0].metadata["title"] == "Page A"
        assert bundles[0].confidence == 0.8

    @pytest.mark.asyncio
    async def test_web_source_lookup_graceful_missing_table(self):
        """UndefinedTableError -> empty list, no crash."""
        import asyncpg.exceptions

        conn = AsyncMock()
        conn.fetch = AsyncMock(
            side_effect=asyncpg.exceptions.UndefinedTableError("web_submissions does not exist")
        )

        bundles = await web_source_lookup(["urn:e:1"], conn)

        assert bundles == []


# ---------------------------------------------------------------------------
# Legacy adapter tests
# ---------------------------------------------------------------------------

class TestLegacyAdapter:

    def test_evidence_bundles_to_legacy_format(self):
        """Flat bundle list partitions correctly into legacy 4-tuple."""
        bundles = [
            EvidenceBundle(
                source_uri="urn:e:1",
                source_type=SourceType.LOCAL_AUTHORITATIVE,
                retrieval_op=RetrievalOp.ENTITY_LOOKUP,
                confidence=0.9,
                text="Description of Eelgrass",
                metadata={"entity_type": "Concept", "label": "Eelgrass", "fuseki_uri": "urn:e:1"},
            ),
            EvidenceBundle(
                source_uri="urn:e:1:located_in:urn:e:2",
                source_type=SourceType.LOCAL_AUTHORITATIVE,
                retrieval_op=RetrievalOp.RELATIONSHIP_TRAVERSE,
                confidence=1.0,
                text="Eelgrass --[located_in]--> Salish Sea",
                metadata={"subject_uri": "urn:e:1", "predicate": "located_in", "object_uri": "urn:e:2",
                          "subject_label": "Eelgrass", "object_label": "Salish Sea", "depth": 1},
            ),
            EvidenceBundle(
                source_uri="doc:123",
                source_type=SourceType.LOCAL_DOCUMENT,
                retrieval_op=RetrievalOp.TEXT_SEARCH,
                confidence=0.035,
                text="Eelgrass beds are critical habitat...",
                metadata={"title": "Marine Ecology", "context": "Habitat section",
                          "section_id": "s1", "section_title": "Habitats", "wiki_url": "https://wiki/eelgrass"},
            ),
            EvidenceBundle(
                source_uri="https://example.com/eelgrass",
                source_type=SourceType.LOCAL_WEB,
                retrieval_op=RetrievalOp.ENTITY_LOOKUP,
                confidence=0.8,
                text="Comprehensive eelgrass resource",
                metadata={"url": "https://example.com/eelgrass", "title": "Eelgrass Guide",
                          "summary": "Comprehensive eelgrass resource"},
            ),
        ]

        sources, rels, docs, web = evidence_bundles_to_legacy_format(bundles)

        # Entity sources
        entity_sources = [s for s in sources if s['entity_type'] not in ('Document', 'WebSource')]
        assert len(entity_sources) == 1
        assert entity_sources[0]['label'] == "Eelgrass"
        assert entity_sources[0]['score'] == 0.9

        # Relationships
        assert len(rels) == 1
        assert "Eelgrass --[located_in]--> Salish Sea" in rels[0]

        # Doc chunks
        assert len(docs) == 1
        assert docs[0]['rid'] == "doc:123"
        assert docs[0]['title'] == "Marine Ecology"
        assert docs[0]['wiki_url'] == "https://wiki/eelgrass"

        # Web sources
        assert len(web) == 1
        assert web[0]['url'] == "https://example.com/eelgrass"
        assert web[0]['title'] == "Eelgrass Guide"

        # Web also added to sources
        web_in_sources = [s for s in sources if s['entity_type'] == 'WebSource']
        assert len(web_in_sources) == 1

        # Doc also added to sources
        doc_in_sources = [s for s in sources if s['entity_type'] == 'Document']
        assert len(doc_in_sources) == 1


# ---------------------------------------------------------------------------
# Orchestration test: chat_endpoint wires executors correctly
# ---------------------------------------------------------------------------

class TestChatEndpointOrchestration:
    """Verify that chat_endpoint calls the extracted executors with correct
    args and feeds evidence_bundles_to_legacy_format correctly."""

    @pytest.mark.asyncio
    async def test_chat_endpoint_calls_executors_and_adapter(self):
        """Patch all 4 executors + adapter, verify chat_endpoint calls them
        with expected arguments and produces a response."""
        from api.schemas.query_plan import EvidenceBundle, RetrievalOp, SourceType

        # Build mock return bundles
        entity_bundle = EvidenceBundle(
            source_uri="urn:e:1", source_type=SourceType.LOCAL_AUTHORITATIVE,
            retrieval_op=RetrievalOp.ENTITY_LOOKUP, confidence=0.9,
            text="Mock entity", metadata={"entity_type": "Concept", "label": "Eelgrass", "fuseki_uri": "urn:e:1"},
        )
        rel_bundle = EvidenceBundle(
            source_uri="urn:e:1:rel:urn:e:2", source_type=SourceType.LOCAL_AUTHORITATIVE,
            retrieval_op=RetrievalOp.RELATIONSHIP_TRAVERSE, confidence=1.0,
            text="Eelgrass --[located_in]--> Salish Sea",
            metadata={"subject_uri": "urn:e:1", "predicate": "located_in", "object_uri": "urn:e:2",
                      "subject_label": "Eelgrass", "object_label": "Salish Sea", "depth": 1},
        )
        text_bundle = EvidenceBundle(
            source_uri="doc:1", source_type=SourceType.LOCAL_DOCUMENT,
            retrieval_op=RetrievalOp.TEXT_SEARCH, confidence=0.03,
            text="Eelgrass beds...", metadata={"title": "Marine Doc", "context": "", "section_id": None,
                                                "section_title": None, "wiki_url": None},
        )
        web_bundle = EvidenceBundle(
            source_uri="https://example.com", source_type=SourceType.LOCAL_WEB,
            retrieval_op=RetrievalOp.ENTITY_LOOKUP, confidence=0.8,
            text="Web desc", metadata={"url": "https://example.com", "title": "Web Page", "summary": "Web desc"},
        )

        mock_entity = AsyncMock(return_value=[entity_bundle])
        mock_rel = AsyncMock(return_value=[rel_bundle])
        mock_text = AsyncMock(return_value=[text_bundle])
        mock_web = AsyncMock(return_value=[web_bundle])

        # Mock the LLM (wrap in asyncio.to_thread mock)
        mock_openai = MagicMock()
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock(message=MagicMock(content="Mock answer about eelgrass"))]
        mock_openai.chat.completions.create.return_value = mock_completion

        # Mock DB pool with proper async context manager
        mock_conn = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.acquire.return_value = mock_cm

        # Mock generate_embedding
        mock_embed = AsyncMock(return_value=[0.1] * 1536)

        # Mock _try_structured_graph_query
        mock_graph_query = AsyncMock(return_value="")

        # Mock asyncio.to_thread so the OpenAI call doesn't need a real thread
        async def _fake_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with patch("api.personal_ingest_api.db_pool", mock_pool), \
             patch("api.personal_ingest_api.openai_client", mock_openai), \
             patch("api.personal_ingest_api.OPENAI_API_KEY", "fake-key"), \
             patch("api.personal_ingest_api.generate_embedding", mock_embed), \
             patch("api.personal_ingest_api._try_structured_graph_query", mock_graph_query), \
             patch("asyncio.to_thread", side_effect=_fake_to_thread), \
             patch("api.retrieval_executors.entity_lookup", mock_entity), \
             patch("api.retrieval_executors.relationship_traverse", mock_rel), \
             patch("api.retrieval_executors.text_search", mock_text), \
             patch("api.retrieval_executors.web_source_lookup", mock_web):

            # Import after patching
            from api.personal_ingest_api import chat_endpoint, ChatRequest

            request = ChatRequest(query="What is eelgrass?", max_context_entities=5)
            response = await chat_endpoint(request)

        # Verify all 4 executors were called
        mock_entity.assert_called_once()
        mock_rel.assert_called_once()
        mock_text.assert_called_once()
        mock_web.assert_called_once()

        # Verify entity_lookup received correct args
        entity_call = mock_entity.call_args
        assert entity_call[0][0] == "What is eelgrass?"  # query
        assert entity_call[1]['max_results'] == 5

        # Verify relationship_traverse received entity URIs from entity_lookup
        rel_call = mock_rel.call_args
        assert rel_call[0][0] == ["urn:e:1"]  # entity_uris from entity_bundle

        # Verify web_source_lookup received same entity URIs
        web_call = mock_web.call_args
        assert web_call[0][0] == ["urn:e:1"]

        # Verify response has expected structure
        assert "answer" in response
        assert "sources" in response
        assert "intent" in response
        assert response["answer"] == "Mock answer about eelgrass"
        assert len(response["sources"]) > 0
