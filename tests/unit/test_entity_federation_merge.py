"""Entity federation must never blank a peer's locally-computed data.

Regression cover for the 2026-08-03 defect: both `/register-entity` emit sites
inlined a payload literal that hardcoded ``"aliases": []`` / ``"metadata": {}``,
and the subscriber's UPSERT did ``SET metadata = EXCLUDED.metadata``. The result
was not merely "federation teaches the peer nothing" — an inbound event actively
ERASED metadata the peer had computed locally. Measured at the time of the fix:
644 rows on the MacBook node and 964 on the NUC had ``first_seen_rid IS NOT NULL``
(proof ``store_new_entity`` ran locally and wrote a populated metadata JSON) yet
held ``metadata = '{}'``.

Style follows tests/test_alias_normalization.py: AST guards + a fake-conn unit
test, no live DB and no app import (importing personal_ingest_api triggers
FastAPI app side effects). The actual merge SEMANTICS are proven against a live
database by the deploy-time integration check, since expressing
``jsonb ||`` / ``array_agg(DISTINCT ...)`` behaviour in a fake is worthless.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

INGEST_PY = ROOT / "api" / "personal_ingest_api.py"
HANDLERS_PY = ROOT / "api" / "domain_event_handlers.py"


# --- Fake conn --------------------------------------------------------------

class _RecordingConn:
    """Minimal asyncpg-connection stand-in that records executed SQL."""

    def __init__(self, row=None):
        self.executed: list[tuple[str, tuple]] = []
        self._row = row

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    async def fetchrow(self, sql, *args):
        return self._row

    @property
    def upsert_sql(self) -> str:
        for sql, _ in self.executed:
            if "INSERT INTO entity_registry" in sql:
                return " ".join(sql.split())
        raise AssertionError("no entity_registry INSERT was executed")

    @property
    def upsert_args(self) -> tuple:
        for sql, args in self.executed:
            if "INSERT INTO entity_registry" in sql:
                return args
        raise AssertionError("no entity_registry INSERT was executed")


async def _apply(payload):
    from api.domain_event_handlers import _apply_entity

    conn = _RecordingConn()
    await _apply_entity(conn, payload.get("fuseki_uri", "rid:x"), "UPDATE", payload, "peer")
    return conn


# --- The core invariant -----------------------------------------------------

@pytest.mark.asyncio
async def test_upsert_never_replaces_metadata_wholesale():
    """The exact clause that caused the data loss must not come back."""
    conn = await _apply({
        "fuseki_uri": "orn:personal-koi.entity:person-x-1",
        "entity_text": "X",
        "entity_type": "Person",
        "metadata": {},
        "aliases": [],
    })
    sql = conn.upsert_sql
    assert "metadata = EXCLUDED.metadata" not in sql, (
        "regression: an inbound event can blank a peer's metadata again"
    )
    assert "aliases = EXCLUDED.aliases" not in sql, (
        "regression: an inbound event can shrink a peer's alias list again"
    )
    # jsonb concat, so an empty incoming object is a no-op.
    assert "entity_registry.metadata" in sql and "|| COALESCE(EXCLUDED.metadata" in sql


@pytest.mark.asyncio
async def test_upsert_merges_the_optional_fields_non_destructively():
    conn = await _apply({
        "fuseki_uri": "orn:personal-koi.entity:person-x-1",
        "entity_text": "X",
        "entity_type": "Person",
    })
    sql = conn.upsert_sql
    # alias set-union: the array can never shrink
    assert "array_agg(DISTINCT a)" in sql
    # description: keep local unless the incoming value is non-empty
    assert "description = COALESCE(NULLIF(EXCLUDED.description, ''), entity_registry.description)" in sql
    # phonetic_code drives Tier-1.x phonetic matching -> fill-if-missing only
    assert "phonetic_code = COALESCE(entity_registry.phonetic_code, EXCLUDED.phonetic_code)" in sql


@pytest.mark.asyncio
async def test_identity_fields_still_replace_so_renames_converge():
    conn = await _apply({"fuseki_uri": "u", "entity_text": "New", "entity_type": "Person"})
    sql = conn.upsert_sql
    for field in ("entity_text", "entity_type", "normalized_text"):
        assert f"{field} = EXCLUDED.{field}" in sql


# --- Embedding: not emitted, but honoured if a peer sends one ---------------

@pytest.mark.asyncio
async def test_embedding_column_omitted_when_payload_has_no_vector():
    conn = await _apply({"fuseki_uri": "u", "entity_text": "X", "entity_type": "Person"})
    sql = conn.upsert_sql
    assert "embedding" not in sql
    assert len(conn.upsert_args) == 8  # the 8 non-vector columns


@pytest.mark.asyncio
async def test_inbound_vector_is_honoured_and_never_overwrites_a_local_one():
    conn = await _apply({
        "fuseki_uri": "u",
        "entity_text": "X",
        "entity_type": "Person",
        "embedding_column": "embedding_3072",
        "embedding_value": [0.1, 0.2, 0.3],
    })
    sql = conn.upsert_sql
    assert "embedding_3072" in sql and "::vector" in sql
    assert "embedding_3072 = COALESCE(entity_registry.embedding_3072, EXCLUDED.embedding_3072)" in sql
    assert conn.upsert_args[-1] == "[0.1,0.2,0.3]"


@pytest.mark.asyncio
async def test_unknown_embedding_column_is_ignored_not_injected():
    """Guard against SQL injection through the payload-supplied column name."""
    conn = await _apply({
        "fuseki_uri": "u",
        "entity_text": "X",
        "entity_type": "Person",
        "embedding_column": "embedding_3072; DROP TABLE entity_registry --",
        "embedding_value": [0.1],
    })
    assert "DROP TABLE" not in conn.upsert_sql


@pytest.mark.asyncio
async def test_rejects_unsupported_embedding_format():
    from api.domain_event_handlers import _apply_entity

    with pytest.raises(ValueError):
        await _apply_entity(
            _RecordingConn(), "u", "UPDATE",
            {"fuseki_uri": "u", "entity_text": "X", "embedding_format": "base64"},
            "peer",
        )


@pytest.mark.asyncio
async def test_string_metadata_is_parsed_not_stringified():
    conn = await _apply({
        "fuseki_uri": "u", "entity_text": "X", "entity_type": "Person",
        "metadata": '{"context": "festival vendor"}',
    })
    # arg 6 (0-indexed 5) is the metadata JSON
    assert "festival vendor" in conn.upsert_args[5]


@pytest.mark.asyncio
async def test_malformed_metadata_degrades_to_empty_not_crash():
    conn = await _apply({
        "fuseki_uri": "u", "entity_text": "X", "entity_type": "Person",
        "metadata": "not json at all",
    })
    assert conn.upsert_args[5] == "{}"


# --- Producer side ----------------------------------------------------------

@pytest.mark.asyncio
async def test_payload_builder_emits_real_registry_values():
    from api.personal_ingest_api import _build_entity_federation_payload

    row = {
        "fuseki_uri": "orn:personal-koi.entity:person-ash-946fb5407d72",
        "entity_text": "Ash",
        "entity_type": "Person",
        "normalized_text": "ash",
        "aliases": ["ash the coffee guy"],
        "metadata": {"context": "Turkish coffee vendor", "confidence": 1.0},
        "description": "Serves Turkish coffee at festivals.",
        "phonetic_code": "AX",
    }
    payload = await _build_entity_federation_payload(_RecordingConn(row), row["fuseki_uri"])

    assert payload["aliases"] == ["ash the coffee guy"], "the empty-literal defect is back"
    assert payload["metadata"]["context"] == "Turkish coffee vendor"
    assert payload["description"] == "Serves Turkish coffee at festivals."
    assert payload["phonetic_code"] == "AX"
    # Vector deliberately excluded — see the helper's docstring for the
    # 22k-events/30d * 60kB payload-size arithmetic.
    assert "embedding_value" not in payload


@pytest.mark.asyncio
async def test_payload_builder_falls_back_when_row_vanished():
    """A merge between write and emit must not crash the request path."""
    from api.personal_ingest_api import _build_entity_federation_payload

    payload = await _build_entity_federation_payload(
        _RecordingConn(None), "u", fallback_name="Ash", fallback_type="Person"
    )
    assert payload["entity_text"] == "Ash"
    assert payload["entity_type"] == "Person"
    assert payload["normalized_text"] == "ash"


@pytest.mark.asyncio
async def test_payload_builder_survives_a_failing_lookup():
    from api.personal_ingest_api import _build_entity_federation_payload

    class _Boom(_RecordingConn):
        async def fetchrow(self, sql, *args):
            raise RuntimeError("db gone")

    payload = await _build_entity_federation_payload(
        _Boom(), "u", fallback_name="Ash", fallback_type="Person"
    )
    assert payload["entity_text"] == "Ash"


# --- AST guards: no app import ---------------------------------------------

def _calls_in(path: Path, func_name: str):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == func_name:
            return node
    raise AssertionError(f"{func_name} not found in {path}")


def test_no_emit_site_hardcodes_an_empty_entity_payload():
    """The literal that caused the incident must not reappear at any emit site."""
    tree = ast.parse(INGEST_PY.read_text())
    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "emit_domain_event":
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        if node.args[0].value != "entity":
            continue
        # 4th positional arg is the payload; a dict literal here is the bug.
        if len(node.args) >= 4 and isinstance(node.args[3], ast.Dict):
            keys = [k.value for k in node.args[3].keys if isinstance(k, ast.Constant)]
            offenders.append((node.lineno, keys))
    assert not offenders, (
        f"entity federation emitted from an inline dict literal at {offenders}; "
        "use _build_entity_federation_payload so real aliases/metadata are sent"
    )


def test_payload_builder_uses_the_module_json_alias():
    """This module aliases stdlib json as `json_module_global` and never binds
    bare `json`; `json.loads` here is a runtime NameError that py_compile and
    import-free unit tests both miss."""
    src = ast.get_source_segment(
        INGEST_PY.read_text(),
        _calls_in(INGEST_PY, "_build_entity_federation_payload"),
    )
    assert "json_module_global.loads" in src
    assert "\n    json.loads" not in src and " json.loads" not in src.replace(
        "json_module_global.loads", ""
    )
