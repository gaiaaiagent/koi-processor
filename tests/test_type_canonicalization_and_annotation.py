"""P2 tests: entity-type canonicalization + vault-path annotation (2026-07-13).

Two concerns:

  1. canonicalize_entity_type (api/entity_schema.py) — pure, no DB.
  2. _annotate_vault_fields + the /entity/resolve response annotation
     (api/personal_ingest_api.py) — exercised directly against a stub conn and
     end-to-end through a FastAPI TestClient with a monkeypatched _VAULT_ROOT
     and a fake db_pool. ANNOTATION MUST NOT gate resolution.
"""

import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from api.entity_schema import canonicalize_entity_type


# ---------------------------------------------------------------------------
# canonicalize_entity_type
# ---------------------------------------------------------------------------

CANON_CASES = [
    ("schema:SoftwareApplication", "SoftwareApplication"),  # prefix strip, unknown type
    ("bkc:Concept", "Concept"),                              # prefix strip -> known key
    ("schema:Persons", "Person"),                            # plural-tolerant
    ("Projects", "Project"),                                 # plural-tolerant, no prefix
    ("organization", "Organization"),                        # case-insensitive
    ("Person", "Person"),                                    # already canonical
    ("FooBar", "FooBar"),                                    # unknown passthrough
    ("", ""),                                                # empty passthrough
]


@pytest.mark.parametrize("raw,expected", CANON_CASES)
def test_canonicalize_entity_type(raw, expected):
    assert canonicalize_entity_type(raw) == expected


def test_canonicalize_none_passthrough():
    assert canonicalize_entity_type(None) is None


def test_canonicalize_strips_whitespace_and_prefix():
    assert canonicalize_entity_type("  bkc:Concept  ") == "Concept"


# ---------------------------------------------------------------------------
# _annotate_vault_fields — direct, with a stub conn + monkeypatched _VAULT_ROOT
# ---------------------------------------------------------------------------

class _FetchvalConn:
    """Minimal async conn stub: fetchval returns a preset vault_path."""

    def __init__(self, vault_path):
        self._vault_path = vault_path

    async def fetchval(self, query, *args):
        # _annotate_vault_fields only issues the vault_path lookup.
        assert "vault_path" in query and "entity_rid_mappings" in query
        return self._vault_path


@pytest.mark.asyncio
async def test_annotate_mapping_and_note_exists(monkeypatch, tmp_path):
    from api import personal_ingest_api as pia

    # Mount a fake vault and create the note file so _vault_note_exists -> True.
    (tmp_path / "People").mkdir()
    (tmp_path / "People" / "Clare Attwell.md").write_text("# note")
    monkeypatch.setattr(pia, "_VAULT_ROOT", tmp_path)

    conn = _FetchvalConn("People/Clare Attwell.md")
    vpath, vexists, vfolder = await pia._annotate_vault_fields(
        conn, "orn:personal-koi.entity:person-clare", "Person"
    )
    assert vpath == "People/Clare Attwell.md"
    assert vexists is True
    assert vfolder == "People"


@pytest.mark.asyncio
async def test_annotate_mapping_but_note_missing(monkeypatch, tmp_path):
    from api import personal_ingest_api as pia

    monkeypatch.setattr(pia, "_VAULT_ROOT", tmp_path)  # dir exists, note does not
    conn = _FetchvalConn("People/Ghost Person.md")
    vpath, vexists, vfolder = await pia._annotate_vault_fields(
        conn, "orn:personal-koi.entity:person-ghost", "Person"
    )
    assert vpath == "People/Ghost Person.md"
    assert vexists is False  # phantom mapping — file not on disk
    assert vfolder == "People"


@pytest.mark.asyncio
async def test_annotate_no_mapping(monkeypatch, tmp_path):
    from api import personal_ingest_api as pia

    monkeypatch.setattr(pia, "_VAULT_ROOT", tmp_path)
    conn = _FetchvalConn(None)  # no entity_rid_mappings row
    vpath, vexists, vfolder = await pia._annotate_vault_fields(
        conn, "orn:personal-koi.entity:org-acme", "Organization"
    )
    assert vpath is None
    assert vexists is False
    assert vfolder == "Organizations"  # folder still derived from type


@pytest.mark.asyncio
async def test_annotate_vault_folder_by_type(monkeypatch, tmp_path):
    from api import personal_ingest_api as pia

    monkeypatch.setattr(pia, "_VAULT_ROOT", tmp_path)
    conn = _FetchvalConn(None)
    for etype, folder in [("Person", "People"), ("Organization", "Organizations"),
                          ("Project", "Projects"), ("Concept", "Concepts")]:
        _, _, vfolder = await pia._annotate_vault_fields(conn, "orn:x", etype)
        assert vfolder == folder, f"{etype} -> {vfolder} (want {folder})"


# ---------------------------------------------------------------------------
# End-to-end: /entity/resolve GET annotates the winning candidate.
# Fake db_pool whose conn answers exactly the queries the handler issues for an
# exact-match (Tier 1) resolution. Proves the wiring, and that annotation is
# additive (does not gate) — the candidate still resolves.
# ---------------------------------------------------------------------------

class _ResolveConn:
    def __init__(self, uri, name, etype, vault_path):
        self._uri, self._name, self._etype = uri, name, etype
        self._vault_path = vault_path

    async def fetchrow(self, query, *args):
        # Tier 1 exact match on normalized_text (+ entity_type).
        if "FROM entity_registry" in query and "normalized_text = $1" in query:
            return {
                "id": 1,
                "fuseki_uri": self._uri,
                "entity_text": self._name,
                "entity_type": self._etype,
                "normalized_text": args[0],
            }
        return None

    async def fetchval(self, query, *args):
        # Order matters: the annotation query filters "!= 'node_private'", so
        # match the vault_path lookup before the is-private probe.
        if "vault_path" in query and "entity_rid_mappings" in query:
            return self._vault_path
        if "SELECT node_private" in query:
            return False
        return None

    async def fetch(self, query, *args):
        return []  # no siblings


class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _AcquireCtx(self._conn)


def test_resolve_get_includes_vault_annotation(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from api import personal_ingest_api as pia

    (tmp_path / "People").mkdir()
    (tmp_path / "People" / "Clare Attwell.md").write_text("# note")
    monkeypatch.setattr(pia, "_VAULT_ROOT", tmp_path)

    conn = _ResolveConn(
        uri="orn:personal-koi.entity:person-clare-attwell-abc123",
        name="Clare Attwell",
        etype="Person",
        vault_path="People/Clare Attwell.md",
    )
    monkeypatch.setattr(pia, "db_pool", _FakePool(conn))

    client = TestClient(pia.app)
    resp = client.get("/entity/resolve", params={"label": "Clare Attwell", "type_hint": "Person"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_new"] is False
    cand = body["candidates"][0]
    # Resolution still happened (annotation did not gate it) ...
    assert cand["uri"] == "orn:personal-koi.entity:person-clare-attwell-abc123"
    # ... and the annotation fields are present + correct.
    assert cand["vault_path"] == "People/Clare Attwell.md"
    assert cand["vault_note_exists"] is True
    assert cand["vault_folder"] == "People"


def test_resolve_get_annotation_null_mapping_does_not_gate(monkeypatch, tmp_path):
    """No entity_rid_mappings row -> null vault_path, but resolution unaffected."""
    from fastapi.testclient import TestClient
    from api import personal_ingest_api as pia

    monkeypatch.setattr(pia, "_VAULT_ROOT", tmp_path)
    conn = _ResolveConn(
        uri="orn:personal-koi.entity:org-acme-xyz",
        name="Acme",
        etype="Organization",
        vault_path=None,
    )
    monkeypatch.setattr(pia, "db_pool", _FakePool(conn))

    client = TestClient(pia.app)
    resp = client.get("/entity/resolve", params={"label": "Acme", "type_hint": "Organization"})
    assert resp.status_code == 200, resp.text
    cand = resp.json()["candidates"][0]
    assert cand["uri"] == "orn:personal-koi.entity:org-acme-xyz"  # still resolved
    assert cand["vault_path"] is None
    assert cand["vault_note_exists"] is False
    assert cand["vault_folder"] == "Organizations"
