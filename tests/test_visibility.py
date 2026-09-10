"""Fail-closed semantics for the audience-scoped visibility kernel.

READ-ONLY: these never touch Postgres. ``visible_at`` is driven with a fake async
connection so the tests assert the POLICY (fail-closed / degrade-closed), not any
data-specific outcome — correct, because every live row is currently
``'unclassified'`` and stamping real scopes is a separate, un-run step.

Tests are plain (non-async) functions that drive the coroutine via ``asyncio.run``,
so they need no pytest-asyncio marker under the repo's ``asyncio_mode = strict``.
"""

from __future__ import annotations

import asyncio

import pytest

from api.policy.visibility import (
    scopes_for_audience,
    visibility_predicate,
    visible_at,
)


# ---------------------------------------------------------------------------
# Fake async connection (duck-typed asyncpg: fetchrow / fetch)
# ---------------------------------------------------------------------------


class FakeConn:
    """Minimal async stand-in for an asyncpg connection.

    ``reg`` is the single ``entity_registry`` row returned by ``fetchrow`` (or None
    to simulate an unknown uri). ``rid_rows`` is the ``entity_rid_mappings`` result
    for the concept folder-gate. If ``raises`` is set, every query raises it
    (drives the degrade-closed path).
    """

    def __init__(self, reg=None, rid_rows=None, raises: Exception | None = None):
        self._reg = reg
        self._rid_rows = rid_rows or []
        self._raises = raises

    async def fetchrow(self, sql, *args):
        if self._raises is not None:
            raise self._raises
        return self._reg

    async def fetch(self, sql, *args):
        if self._raises is not None:
            raise self._raises
        return self._rid_rows


def _reg(scope="unclassified", node_private=False, name="Acme Corp",
         etype="Organization", aliases=None):
    return {
        "visibility_scope": scope,
        "node_private": node_private,
        "entity_text": name,
        "entity_type": etype,
        "aliases": aliases or [],
    }


def _run(coro):
    return asyncio.run(coro)


URI = "https://example.org/entity/acme"


# ---------------------------------------------------------------------------
# visible_at — fail-closed core
# ---------------------------------------------------------------------------


def test_unknown_uri_denied_for_all_audiences():
    conn = FakeConn(reg=None)  # no registry row
    for audience in ("public", "team", "confidential"):
        assert _run(visible_at(conn, URI, audience)) is False


def test_empty_uri_denied():
    conn = FakeConn(reg=_reg(scope="public"))
    assert _run(visible_at(conn, "", "confidential")) is False


def test_unclassified_denied_for_team_and_public():
    # Every live row is currently 'unclassified' — it must be invisible to
    # team + public (fail-closed for the not-yet-classified bucket).
    conn = FakeConn(reg=_reg(scope="unclassified"))
    assert _run(visible_at(conn, URI, "team")) is False
    assert _run(visible_at(conn, URI, "public")) is False


def test_exception_path_degrades_closed():
    conn = FakeConn(raises=RuntimeError("db exploded"))
    assert _run(visible_at(conn, URI, "confidential")) is False


def test_unknown_audience_denied():
    # 'unclassified' is a scope, never a valid audience; anything unrecognized denies.
    conn = FakeConn(reg=_reg(scope="public"))
    assert _run(visible_at(conn, URI, "unclassified")) is False
    assert _run(visible_at(conn, URI, "admin")) is False
    assert _run(visible_at(conn, URI, "")) is False


def test_node_private_hard_deny_even_when_scope_public():
    conn = FakeConn(reg=_reg(scope="public", node_private=True))
    for audience in ("public", "team", "confidential"):
        assert _run(visible_at(conn, URI, audience)) is False


# ---------------------------------------------------------------------------
# visible_at — the affirmative paths that MUST hold (policy, not live data)
# ---------------------------------------------------------------------------


def test_confidential_audience_sees_confidential_scope():
    conn = FakeConn(reg=_reg(scope="confidential"))
    assert _run(visible_at(conn, URI, "confidential")) is True


def test_confidential_audience_sees_unclassified_rollout_path():
    # The full-trust internal viewer must still see rows during the rollout, when
    # everything is unclassified — otherwise the internal app goes dark.
    conn = FakeConn(reg=_reg(scope="unclassified"))
    assert _run(visible_at(conn, URI, "confidential")) is True


def test_team_audience_sees_public_and_team_not_confidential():
    assert _run(visible_at(FakeConn(reg=_reg(scope="public")), URI, "team")) is True
    assert _run(visible_at(FakeConn(reg=_reg(scope="team")), URI, "team")) is True
    assert _run(visible_at(FakeConn(reg=_reg(scope="confidential")), URI, "team")) is False


def test_public_audience_sees_only_public():
    assert _run(visible_at(FakeConn(reg=_reg(scope="public")), URI, "public")) is True
    assert _run(visible_at(FakeConn(reg=_reg(scope="team")), URI, "public")) is False


def test_denylisted_name_denied_even_when_scope_public():
    conn = FakeConn(reg=_reg(scope="public", name="Hydro One"))
    assert _run(visible_at(conn, URI, "confidential")) is False


def test_denylist_matches_alias_case_insensitively():
    conn = FakeConn(reg=_reg(scope="public", name="A Utility", aliases=["layer b"]))
    assert _run(visible_at(conn, URI, "confidential")) is False


# ---------------------------------------------------------------------------
# visible_at — Concept folder gate
# ---------------------------------------------------------------------------


def test_concept_in_folder_and_allowlist_visible():
    conn = FakeConn(
        reg=_reg(scope="public", name="data sovereignty", etype="schema:Concept"),
        rid_rows=[{"vault_path": "Concepts/data sovereignty.md"}],
    )
    assert _run(visible_at(conn, URI, "team")) is True


def test_concept_not_in_concepts_folder_denied():
    conn = FakeConn(
        reg=_reg(scope="public", name="data sovereignty", etype="schema:Concept"),
        rid_rows=[{"vault_path": "Notes/data sovereignty.md"}],
    )
    assert _run(visible_at(conn, URI, "team")) is False


def test_concept_not_on_allowlist_denied():
    conn = FakeConn(
        reg=_reg(scope="public", name="random idea", etype="Concept"),
        rid_rows=[{"vault_path": "Concepts/random idea.md"}],
    )
    assert _run(visible_at(conn, URI, "team")) is False


def test_concept_with_no_vault_mapping_denied():
    conn = FakeConn(
        reg=_reg(scope="public", name="data sovereignty", etype="Concept"),
        rid_rows=[],  # no vault_path row → cannot confirm folder → deny
    )
    assert _run(visible_at(conn, URI, "team")) is False


# ---------------------------------------------------------------------------
# scopes_for_audience
# ---------------------------------------------------------------------------


def test_scopes_for_audience_mapping():
    assert scopes_for_audience("public") == ("public",)
    assert scopes_for_audience("team") == ("public", "team")
    assert scopes_for_audience("confidential") == (
        "public", "team", "confidential", "unclassified",
    )


def test_scopes_for_audience_unknown_is_empty():
    assert scopes_for_audience("unclassified") == ()
    assert scopes_for_audience("nope") == ()
    assert scopes_for_audience(None) == ()  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# visibility_predicate — reusable, injection-safe SQL fragment
# ---------------------------------------------------------------------------


def test_visibility_predicate_team():
    frag = visibility_predicate("er", scopes_for_audience("team"))
    assert frag == (
        "(NOT COALESCE(er.node_private, false) "
        "AND er.visibility_scope IN ('public', 'team'))"
    )


def test_visibility_predicate_public():
    assert visibility_predicate("er", scopes_for_audience("public")) == (
        "(NOT COALESCE(er.node_private, false) "
        "AND er.visibility_scope IN ('public'))"
    )


def test_visibility_predicate_confidential_all_four():
    frag = visibility_predicate("entity_registry", scopes_for_audience("confidential"))
    assert frag == (
        "(NOT COALESCE(entity_registry.node_private, false) "
        "AND entity_registry.visibility_scope IN "
        "('public', 'team', 'confidential', 'unclassified'))"
    )


def test_visibility_predicate_empty_scopes_matches_nothing():
    assert visibility_predicate("er", ()) == "(false)"
    assert visibility_predicate("er", scopes_for_audience("bogus")) == "(false)"


def test_visibility_predicate_rejects_unsafe_alias():
    for bad in ("er; DROP TABLE entity_registry", "1abc", "er er", "", "e-r"):
        with pytest.raises(ValueError):
            visibility_predicate(bad, ("public",))


def test_visibility_predicate_rejects_unknown_scope():
    with pytest.raises(ValueError):
        visibility_predicate("er", ("public", "secret'; DROP TABLE x;--"))
    with pytest.raises(ValueError):
        visibility_predicate("er", ("public", "node_private"))


def test_visibility_predicate_dedups_and_orders():
    # Duplicate + out-of-order input yields the canonical ordered, de-duped form.
    frag = visibility_predicate("er", ("team", "public", "team"))
    assert frag == (
        "(NOT COALESCE(er.node_private, false) "
        "AND er.visibility_scope IN ('public', 'team'))"
    )


def test_visibility_predicate_retains_node_private_hard_deny():
    # Drop-in replacement for `AND NOT node_private`: the node_private hard-deny
    # MUST survive alongside the audience-scope gate.
    frag = visibility_predicate("er", scopes_for_audience("confidential"))
    assert "NOT COALESCE(er.node_private, false)" in frag
    assert "er.visibility_scope IN" in frag


def test_scopes_for_alias_matches_scopes_for_audience():
    from api.policy.visibility import scopes_for
    for aud in ("public", "team", "confidential", "nope", ""):
        assert scopes_for(aud) == scopes_for_audience(aud)
