import importlib.util
import asyncio
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "extract_deep_documents.py"
SCHEMA = ROOT / "scripts" / "schemas" / "deep_extraction_doc_v2.schema.json"


def load_extractor():
    spec = importlib.util.spec_from_file_location("extract_deep_documents", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_and_validate_repairs_missing_fact_text():
    extractor = load_extractor()
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    raw = json.dumps(
        {
            "document": {
                "name": "Harmonicity in Networked Social Information Dynamics",
                "summary": "The paper studies harmonicity in networked social information dynamics.",
                "doc_kind": "paper",
                "chunk_span": [0, 2],
            },
            "entities": [
                {
                    "name": "Discourse Sheaf",
                    "type": "Concept",
                    "first_seen_chunk": 0,
                    "mention_count": 1,
                },
                {
                    "name": "Harmonicity",
                    "type": "Concept",
                    "first_seen_chunk": 1,
                    "mention_count": 1,
                },
            ],
            "facts": [
                {
                    "subject": "Discourse Sheaf",
                    "predicate": "RELATES_TO",
                    "object": "Harmonicity",
                    "object_literal": None,
                    "chunk_range": [0, 1],
                    "confidence": "high",
                }
            ],
            "discourse": [
                {
                    "move_type": "claim",
                    "title": "Discourse sheaves can model harmonicity in social information dynamics.",
                    "detail": None,
                    "status": "asserted",
                    "supports": None,
                    "chunk_range": [0, 2],
                }
            ],
        }
    )

    parsed = extractor.parse_and_validate(raw, schema)

    assert parsed["facts"][0]["fact_text"] == "Discourse Sheaf relates to Harmonicity."


def test_parse_and_validate_drops_extra_fact_fields():
    extractor = load_extractor()
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    raw = json.dumps(
        {
            "document": {
                "name": "Persistence by Parts",
                "summary": "The paper studies distributed persistent homology.",
                "doc_kind": "paper",
                "chunk_span": [0, 2],
            },
            "entities": [
                {
                    "name": "Distributed Persistent Homology",
                    "type": "Concept",
                    "first_seen_chunk": 0,
                    "mention_count": 1,
                }
            ],
            "facts": [
                {
                    "subject": "Distributed Persistent Homology",
                    "predicate": "RELATES_TO",
                    "object": "Feature Detection",
                    "object_literal": None,
                    "fact_text": "Distributed persistent homology relates to feature detection.",
                    "chunk_range": [0, 1],
                    "confidence": "high",
                    "type": "claim",
                }
            ],
            "discourse": [
                {
                    "move_type": "claim",
                    "title": "Distributed persistence can detect multiscale features.",
                    "detail": None,
                    "status": "asserted",
                    "supports": None,
                    "chunk_range": [0, 2],
                }
            ],
        }
    )

    parsed = extractor.parse_and_validate(raw, schema)

    assert "type" not in parsed["facts"][0]


def test_call_extractor_headless_llm_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DOC_EXTRACTOR_ALLOW_HEADLESS_LLM", raising=False)
    monkeypatch.delenv("DOC_EXTRACTOR_CLAUDE_P_FALLBACK", raising=False)
    monkeypatch.delenv("DOC_EXTRACTOR_OPENAI_FALLBACK", raising=False)
    extractor = load_extractor()

    async def run():
        with pytest.raises(extractor.ExtractionError) as exc:
            await extractor.call_extractor("{}", object(), model="claude-sonnet-4-6")
        return exc.value

    error = asyncio.run(run())

    assert error.reason == "headless_llm_disabled"
    assert "agent-first" in error.detail
    assert extractor.CLAUDE_P_FALLBACK is False
    assert extractor.OPENAI_FALLBACK is False


def _enable_headless_with_openai_fallback(monkeypatch):
    """Load the module with headless extraction + OpenAI fallback enabled.

    Constants are read at import time, so env must be set before load_extractor().
    """
    monkeypatch.setenv("DOC_EXTRACTOR_ALLOW_HEADLESS_LLM", "1")
    monkeypatch.setenv("DOC_EXTRACTOR_OPENAI_FALLBACK", "1")
    monkeypatch.delenv("DOC_EXTRACTOR_CLAUDE_P_FALLBACK", raising=False)
    monkeypatch.delenv("DOC_EXTRACTOR_CLAUDE_P_PRIMARY", raising=False)


def test_fallback_reasons_default_widened():
    """Default set widens the historical pair to also cover unusable output."""
    extractor = load_extractor()
    assert extractor.FALLBACK_REASONS == {
        "extract_http_error",
        "no_api_key",
        "extract_truncated",
        "empty_completion",
    }


def test_extract_truncated_falls_through_to_openai(monkeypatch):
    """anthropic raising extract_truncated → openai transport is attempted."""
    _enable_headless_with_openai_fallback(monkeypatch)
    monkeypatch.delenv("DOC_EXTRACTOR_FALLBACK_REASONS", raising=False)
    extractor = load_extractor()

    calls = []

    async def fake_anthropic(prompt, http, *, model, **kwargs):
        calls.append("anthropic")
        raise extractor.ExtractionError("extract_truncated", "hit max_tokens")

    async def fake_openai(prompt, http, *, model=None, **kwargs):
        calls.append("openai")
        return '{"ok": true}'

    monkeypatch.setattr(extractor, "_call_anthropic", fake_anthropic)
    monkeypatch.setattr(extractor, "_call_openai", fake_openai)

    raw, route = asyncio.run(
        extractor.call_extractor("prompt", object(), model="claude-sonnet-4-6")
    )

    assert calls == ["anthropic", "openai"], "openai must be tried after truncation"
    assert raw == '{"ok": true}'
    assert route == "openai_fallback"


def test_empty_completion_falls_through_to_openai(monkeypatch):
    """anthropic raising empty_completion → openai transport is attempted."""
    _enable_headless_with_openai_fallback(monkeypatch)
    monkeypatch.delenv("DOC_EXTRACTOR_FALLBACK_REASONS", raising=False)
    extractor = load_extractor()

    calls = []

    async def fake_anthropic(prompt, http, *, model, **kwargs):
        calls.append("anthropic")
        raise extractor.ExtractionError("empty_completion", "no text content")

    async def fake_openai(prompt, http, *, model=None, **kwargs):
        calls.append("openai")
        return '{"ok": true}'

    monkeypatch.setattr(extractor, "_call_anthropic", fake_anthropic)
    monkeypatch.setattr(extractor, "_call_openai", fake_openai)

    raw, route = asyncio.run(
        extractor.call_extractor("prompt", object(), model="claude-sonnet-4-6")
    )
    assert calls == ["anthropic", "openai"]
    assert route == "openai_fallback"


def test_terminal_parse_error_still_raises(monkeypatch):
    """A terminal reason (extract_parse_error) is not in the set → no fallback."""
    _enable_headless_with_openai_fallback(monkeypatch)
    monkeypatch.delenv("DOC_EXTRACTOR_FALLBACK_REASONS", raising=False)
    extractor = load_extractor()

    calls = []

    async def fake_anthropic(prompt, http, *, model, **kwargs):
        calls.append("anthropic")
        raise extractor.ExtractionError("extract_parse_error", "no JSON object found")

    async def fake_openai(prompt, http, *, model=None, **kwargs):
        calls.append("openai")
        return '{"ok": true}'

    monkeypatch.setattr(extractor, "_call_anthropic", fake_anthropic)
    monkeypatch.setattr(extractor, "_call_openai", fake_openai)

    with pytest.raises(extractor.ExtractionError) as exc:
        asyncio.run(
            extractor.call_extractor("prompt", object(), model="claude-sonnet-4-6")
        )
    assert exc.value.reason == "extract_parse_error"
    assert calls == ["anthropic"], "openai must NOT be tried for a terminal error"


def test_fallback_reasons_env_override_narrows_set(monkeypatch):
    """DOC_EXTRACTOR_FALLBACK_REASONS can narrow the set so extract_truncated raises."""
    _enable_headless_with_openai_fallback(monkeypatch)
    monkeypatch.setenv("DOC_EXTRACTOR_FALLBACK_REASONS", "extract_http_error,no_api_key")
    extractor = load_extractor()
    assert extractor.FALLBACK_REASONS == {"extract_http_error", "no_api_key"}

    calls = []

    async def fake_anthropic(prompt, http, *, model, **kwargs):
        calls.append("anthropic")
        raise extractor.ExtractionError("extract_truncated", "hit max_tokens")

    async def fake_openai(prompt, http, *, model=None, **kwargs):
        calls.append("openai")
        return '{"ok": true}'

    monkeypatch.setattr(extractor, "_call_anthropic", fake_anthropic)
    monkeypatch.setattr(extractor, "_call_openai", fake_openai)

    with pytest.raises(extractor.ExtractionError) as exc:
        asyncio.run(
            extractor.call_extractor("prompt", object(), model="claude-sonnet-4-6")
        )
    assert exc.value.reason == "extract_truncated"
    assert calls == ["anthropic"], "narrowed set must not fall through on truncation"


def test_post_episode_uses_configured_timeout():
    extractor = load_extractor()
    extractor.EPISODE_POST_TIMEOUT = 42.5
    extractor.KOI_INGEST_SERVICE_TOKEN = None

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"episode_id": "episode-1"}

    class FakeHttp:
        def __init__(self):
            self.timeout = None

        async def post(self, *_args, **kwargs):
            self.timeout = kwargs["timeout"]
            return FakeResponse()

    http = FakeHttp()
    result = asyncio.run(extractor.post_episode(http, {"facts": []}))

    assert result == {"episode_id": "episode-1"}
    assert http.timeout == 42.5


def test_facts_payload_scopes_paper_local_numbered_entities():
    extractor = load_extractor()
    merged = {
        "type_map": {
            "theorem 2": "Concept",
            "lemma 4": "Concept",
            "tarski fixed point theorem": "Concept",
        },
        "facts": [
            {
                "subject": "Theorem 2",
                "predicate": "DERIVES_FROM",
                "object": "Tarski Fixed Point Theorem",
                "object_literal": None,
                "fact_text": "Theorem 2 applies the Tarski Fixed Point Theorem.",
                "chunk_range": [9, 11],
            },
            {
                "subject": "Lemma 4",
                "predicate": "PROVES",
                "object": None,
                "object_literal": "The evaluation map is monotone.",
                "fact_text": "Lemma 4 states that the evaluation map is monotone.",
                "chunk_range": [14, 14],
            },
        ],
    }

    payload = extractor.facts_to_episode_payload(
        merged,
        name="Network Preference Dynamics using Lattice Theory",
        summary="",
        source_document="https://arxiv.org/abs/2310.00179v2",
        group_id="sheaf-explorer",
    )

    assert payload["facts"][0]["subject"] == (
        "Theorem 2 (Network Preference Dynamics using Lattice Theory)"
    )
    assert payload["facts"][0]["object"] == "Tarski Fixed Point Theorem"
    assert payload["facts"][0]["subject_type"] == "Concept"
    assert payload["facts"][1]["subject"] == (
        "Lemma 4 (Network Preference Dynamics using Lattice Theory)"
    )
    assert extractor.scope_paper_local_entity(
        "Hodge-Lawvere Theorem",
        "Toward a Spectral Theory of Cellular Sheaves",
    ) == "Hodge-Lawvere Theorem"


def test_title_anchored_entities_pre_register_named_contributions():
    extractor = load_extractor()
    merged = {
        "type_map": {
            "torsor cnns": "Concept",
            "frustration": "Concept",
            "graphs": "Concept",
        },
        "entities": [
            {"name": "Torsor CNNs", "type": "Concept"},
            {"name": "frustration", "type": "Concept"},
            {"name": "graphs", "type": "Concept"},
            {"name": "edge potential", "type": "Concept"},
        ],
    }

    anchored = extractor.title_anchored_entities(
        merged,
        "Learning from Frustration: Torsor CNNs on Graphs",
    )

    assert anchored == {"Torsor CNNs": "Concept"}


def test_scientific_exact_entities_pre_register_known_collision_terms():
    extractor = load_extractor()
    merged = {
        "type_map": {
            "cell complex": "Concept",
            "cylindrical coordination space": "Concept",
            "globally non-interfering communication": "Concept",
            "knowledge graph completion": "Concept",
            "knowledge graph embedding": "Concept",
            "knowledge sheaf": "Concept",
            "locally non-interfering communication constraints": "Concept",
            "non-cylindrical coordination space": "Concept",
            "configuration spaces": "Concept",
            "configuration spaces of trees": "Concept",
            "configuration space c^n(upsilon)": "Concept",
            "configuration space c k^n": "Concept",
            "non-trivial cycles": "Concept",
            "sensor relation": "Concept",
            "sensor supports": "Concept",
            "sensor cover u": "Concept",
            "strong rips complex rs": "Concept",
            "weak fence subcomplex fw": "Concept",
            "pairwise diagonal delta": "Concept",
            "plex": "Project",
        },
        "entities": [
            {"name": "cell complex", "type": "Concept"},
            {"name": "cylindrical coordination space", "type": "Concept"},
            {"name": "globally non-interfering communication", "type": "Concept"},
            {"name": "knowledge graph completion", "type": "Concept"},
            {"name": "knowledge graph embedding", "type": "Concept"},
            {"name": "knowledge sheaf", "type": "Concept"},
            {"name": "locally non-interfering communication constraints", "type": "Concept"},
            {"name": "non-cylindrical coordination space", "type": "Concept"},
            {"name": "configuration spaces", "type": "Concept"},
            {"name": "configuration spaces of trees", "type": "Concept"},
            {"name": "configuration space C^N(Upsilon)", "type": "Concept"},
            {"name": "configuration space C_K^N", "type": "Concept"},
            {"name": "non-trivial cycles", "type": "Concept"},
            {"name": "sensor relation", "type": "Concept"},
            {"name": "sensor supports", "type": "Concept"},
            {"name": "Sensor cover U", "type": "Concept"},
            {"name": "Strong Rips complex Rs", "type": "Concept"},
            {"name": "Weak fence subcomplex Fw", "type": "Concept"},
            {"name": "pairwise diagonal Delta", "type": "Concept"},
            {"name": "Plex", "type": "Project"},
        ],
    }

    exact = extractor.scientific_exact_entities(merged)

    assert exact == {
        "cell complex": "Concept",
        "cylindrical coordination space": "Concept",
        "globally non-interfering communication": "Concept",
        "configuration space C^N(Upsilon)": "Concept",
        "configuration space C_K^N": "Concept",
        "configuration spaces of trees": "Concept",
        "knowledge graph completion": "Concept",
        "knowledge sheaf": "Concept",
        "locally non-interfering communication constraints": "Concept",
        "non-cylindrical coordination space": "Concept",
        "non-trivial cycles": "Concept",
        "sensor relation": "Concept",
        "sensor supports": "Concept",
        "pairwise diagonal Delta": "Concept",
        "Sensor cover U": "Concept",
        "Strong Rips complex Rs": "Concept",
        "Weak fence subcomplex Fw": "Concept",
    }


def test_extracted_exact_entities_skip_numbered_paper_local_artifacts():
    extractor = load_extractor()
    merged = {
        "type_map": {
            "vin de silva": "Person",
            "cech complex": "Concept",
            "theorem 3": "Concept",
            "unknown typed term": "NotAType",
        },
        "entities": [
            {"name": "Vin de Silva", "type": "Person"},
            {"name": "Cech Complex", "type": "Concept"},
            {"name": "Theorem 3", "type": "Concept"},
            {"name": "Unknown Typed Term", "type": "NotAType"},
        ],
    }

    exact = extractor.extracted_exact_entities(merged)

    assert exact == {
        "Cech Complex": "Concept",
        "Unknown Typed Term": "Concept",
        "Vin de Silva": "Person",
    }
