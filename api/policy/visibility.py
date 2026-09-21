"""Audience-scoped, fail-closed visibility kernel for the read path.

This is the async, asyncpg-facing sibling of the ``is_projectable`` kernel in
``IndigenomicsAI/scripts/otter_notion/entity_projector.py``. It answers one
question — *may this ``audience`` see this entity?* — over the NEW 4-value
``entity_registry.visibility_scope`` axis introduced by migration 107.

It exists so the ~50 ``AND NOT node_private`` read-path sites can migrate, one at
a time, from a single boolean privacy flag to an audience-scoped gate WITHOUT
changing behaviour until entities are actually classified (today every row is
``'unclassified'``).

Two exports:

  * ``async visible_at(conn, uri, audience) -> bool`` — the per-entity decision,
    fail-closed on every ambiguity and degrade-closed on any exception.
  * ``visibility_predicate(alias, scopes) -> str`` — the reusable, injection-safe
    SQL fragment that the read-path query sites splice in place of
    ``AND NOT node_private``. Because it is a DROP-IN replacement for that clause it
    KEEPS the ``node_private`` hard-deny AND adds the audience-scope gate, emitting
    ``(NOT COALESCE(<alias>.node_private, false) AND <alias>.visibility_scope IN (…))``.

Convenience: ``scopes_for(audience)`` (alias of ``scopes_for_audience``) turns an
audience into its allowed-scope set, so a call site can write
``visibility_predicate("er", scopes_for("team"))``.

Config caching: the denylist / concept-allowlist YAML is read ONCE and cached for the
life of the process (operator-owned, reviewed like code). To pick up an edit, restart
the process or call ``_reset_config_cache_for_tests()`` to force a reload.

TWO SEPARATE AXES — never conflate:

  * ``entity_registry.visibility_scope`` — NEW, 4-value AUDIENCE gate
    (``public`` | ``team`` | ``confidential`` | ``unclassified``). THIS module reads
    ONLY this column.
  * ``entity_rid_mappings.visibility_scope`` — PRE-EXISTING, 2-value projection-privacy
    (``public`` | ``node_private``) consumed by the Notion projector. NOT read here.

Fail-closed / degrade-closed contract:

  * unknown uri (no registry row)                → False
  * ``node_private`` true (hard override)         → False
  * name/alias on the confidential denylist       → False
  * scope not in the audience's allowed set       → False
  * ``'unclassified'`` scope                       → visible ONLY to the
    ``confidential`` (full-trust internal) audience; NEVER to ``team`` / ``public``
  * a Concept not in the ``Concepts/`` vault folder AND on the allowlist → False
  * ANY exception anywhere                         → False

No hard dependency on ``asyncpg`` (the connection is duck-typed: it only needs
``await conn.fetchrow(sql, *args)`` / ``await conn.fetch(sql, *args)``), so the
unit tests can drive it with a fake connection and no live DB.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Audience → allowed-scope mapping (the whole visibility policy, in one table)
# ---------------------------------------------------------------------------

# The 4 legal values of entity_registry.visibility_scope (migration 107 CHECK).
VALID_SCOPES: frozenset[str] = frozenset(
    {"public", "team", "confidential", "unclassified"}
)

# The audiences a caller may ask about. NOTE: 'unclassified' is a SCOPE, not an
# audience — it is deliberately absent here, so asking for it fails closed.
VALID_AUDIENCES: frozenset[str] = frozenset({"public", "team", "confidential"})

# What each audience is allowed to see. Monotone: public ⊆ team ⊆ confidential.
#   * public       → only genuinely-public entities.
#   * team         → public + team (NOT unclassified — fail-closed for unclassified).
#   * confidential  → EVERYTHING, including still-unclassified rows. This is the
#     full-trust internal/admin viewer; during the migration-107 rollout every row
#     is 'unclassified', so excluding it here would black out the internal app.
_AUDIENCE_SCOPES: dict[str, tuple[str, ...]] = {
    "public": ("public",),
    "team": ("public", "team"),
    "confidential": ("public", "team", "confidential", "unclassified"),
}

_ALIAS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Bundled config lives beside this module (LOCAL copy — never cross-import the
# IndigenomicsAI otter_notion projection_config.yaml). Override with an env var.
_DEFAULT_CONFIG_PATH = str(Path(__file__).with_name("projection_config.yaml"))


def scopes_for_audience(audience: str) -> tuple[str, ...]:
    """Return the allowed-scope tuple for ``audience``; unknown audience → ``()``.

    Mapping: ``public → {public}``; ``team → {public, team}``; ``confidential → all
    four scopes (incl. 'unclassified')``.

    ROLLOUT NOTE: ``'unclassified'`` rows stay INVISIBLE to the ``team`` and ``public``
    audiences during the migration-107 rollout (their scope sets exclude it). Only the
    full-trust ``confidential`` audience sees unclassified rows. Callers on the
    internal / live read paths therefore pass the ``'confidential'`` (or ``'team'``,
    once rows are classified) audience DELIBERATELY, so that the graph does not go dark
    while every row is still ``'unclassified'``.

    An empty tuple is the fail-closed answer: it makes ``visibility_predicate``
    emit a match-nothing predicate and ``visible_at`` deny.
    """
    if not isinstance(audience, str):
        return ()
    return _AUDIENCE_SCOPES.get(audience, ())


# Short convenience alias so call sites read ``scopes_for("team")``.
scopes_for = scopes_for_audience


# ---------------------------------------------------------------------------
# Reusable SQL fragment (the ~50 read-path sites adopt this)
# ---------------------------------------------------------------------------


def visibility_predicate(alias: str, scopes) -> str:
    """Return an injection-safe SQL boolean gating ``<alias>``'s ``entity_registry`` row.

    This is a DROP-IN replacement for the read path's ``AND NOT node_private`` clause,
    so it RETAINS the ``node_private`` hard-deny and ADDS the audience-scope gate:

        ``(NOT COALESCE(<alias>.node_private, false) AND <alias>.visibility_scope IN (…))``

    ``NOT COALESCE(node_private, false)`` keeps a NULL ``node_private`` visible (matching
    the original ``NOT node_private`` when the column is non-NULL, and failing safe by
    treating NULL as "not private" exactly as the boolean ``NOT`` would after coalesce).

    ``alias`` is the table alias/name the ``entity_registry`` row is exposed under at
    the call site (e.g. ``"er"`` or ``"entity_registry"``). ``scopes`` is the set of
    scope values that audience may see — pass ``scopes_for(audience)``.

    Injection-safety comes from strict whitelisting, NOT string escaping:

      * ``alias`` must match ``^[A-Za-z_][A-Za-z0-9_]*$`` or ``ValueError`` is raised;
      * every value in ``scopes`` must be one of ``VALID_SCOPES`` or ``ValueError``
        is raised.

    Because both inputs are validated against fixed allowlists, the literal values
    spliced into the returned fragment can never carry attacker-controlled text —
    this matches the codebase's existing ``privacy_filter`` idiom (interpolated,
    not ``$n``-parametrized) while staying safe, and sidesteps the fact that each
    call site has a different asyncpg positional-parameter offset.

    Empty ``scopes`` (e.g. an unknown audience) → ``"(false)"`` — matches nothing.
    """
    if not isinstance(alias, str) or not _ALIAS_RE.match(alias):
        raise ValueError(f"unsafe SQL alias: {alias!r}")
    scope_list = list(scopes or [])
    if not scope_list:
        return "(false)"
    bad = [s for s in scope_list if s not in VALID_SCOPES]
    if bad:
        raise ValueError(f"unknown visibility scope(s): {bad!r}")
    # De-dup while preserving a stable, deterministic order.
    ordered = [s for s in ("public", "team", "confidential", "unclassified") if s in set(scope_list)]
    quoted = ", ".join(f"'{s}'" for s in ordered)
    return (
        f"(NOT COALESCE({alias}.node_private, false) "
        f"AND {alias}.visibility_scope IN ({quoted}))"
    )


# ---------------------------------------------------------------------------
# Type normalization + config loading (copied local — no cross-repo import)
# ---------------------------------------------------------------------------


def normalize_type(raw: str | None) -> str:
    """Normalize a KG ``entity_type`` to a canonical label (``schema:Concept`` → ``Concept``).

    Copied from entity_projector.normalize_type to keep this module free of any
    cross-repo import.
    """
    if not raw:
        return "Misc"
    t = str(raw).strip()
    low = t.lower()
    for pref in ("schema:", "bkc:"):
        if low.startswith(pref):
            t = t.split(":", 1)[1]
            low = t.lower()
            break
    if low == "place":
        return "Location"
    return (t[:1].upper() + t[1:]) if t else "Misc"


def _load_config(path: str | None = None) -> tuple[set[str], set[str]]:
    """Load ``(denylist, concept_allowlist)`` — both lowercased. Missing yaml → empty sets.

    LOCAL copy of entity_projector._load_config (do NOT cross-import). Path resolves
    to ``PROJECTION_CONFIG_PATH`` env, then the arg, then the bundled default.
    """
    resolved = os.environ.get("PROJECTION_CONFIG_PATH") or path or _DEFAULT_CONFIG_PATH
    denylist: set[str] = set()
    allow: set[str] = set()
    try:
        import yaml  # lazy

        with open(resolved, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for x in data.get("entity_denylist") or []:
            if str(x).strip():
                denylist.add(str(x).strip().lower())
        for x in data.get("concept_projection_allowlist") or []:
            if str(x).strip():
                allow.add(str(x).strip().lower())
    except FileNotFoundError:
        pass
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("could not load visibility config %s: %s", resolved, exc)
    return denylist, allow


# Config is process-static (operator-owned, reviewed like code). Load once; a test
# can force a reload by clearing the cache via ``_reset_config_cache_for_tests``.
_CONFIG_CACHE: tuple[set[str], set[str]] | None = None


def _get_config() -> tuple[set[str], set[str]]:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        _CONFIG_CACHE = _load_config()
    return _CONFIG_CACHE


def _reset_config_cache_for_tests() -> None:  # pragma: no cover - test hook
    global _CONFIG_CACHE
    _CONFIG_CACHE = None


# ---------------------------------------------------------------------------
# The per-entity gate
# ---------------------------------------------------------------------------


async def visible_at(conn, uri: str, audience: str) -> bool:
    """Return True iff ``audience`` may see the entity identified by ``uri``.

    ``conn`` is an asyncpg connection (or any object exposing ``await conn.fetchrow``
    / ``await conn.fetch``). Reads ONLY ``entity_registry.visibility_scope`` for the
    audience axis; ``node_private`` remains a hard deny; concepts are additionally
    folder-gated via a JOIN to ``entity_rid_mappings.vault_path`` (``entity_registry``
    carries only ``vault_rid``).

    Fail-closed and degrade-closed: any ambiguity or exception → False.
    """
    try:
        allowed = scopes_for_audience(audience)
        if not allowed:  # unknown / 'unclassified' audience → deny everything
            return False
        if not uri:
            return False

        reg = await conn.fetchrow(
            "SELECT visibility_scope, node_private, entity_text, entity_type, aliases "
            "FROM entity_registry WHERE fuseki_uri = $1",
            uri,
        )
        if reg is None:  # unknown uri
            return False

        scope = reg["visibility_scope"]
        node_private = reg["node_private"]
        entity_text = reg["entity_text"]
        entity_type = reg["entity_type"]
        aliases = reg["aliases"]

        # 1. node_private is an unconditional hard deny (independent of scope).
        if node_private:
            return False

        # 2. Confidential denylist on name + aliases (case-insensitive).
        denylist, concept_allowlist = _get_config()
        names_lower = {
            n.strip().lower()
            for n in ({entity_text} | set(aliases or []))
            if n and str(n).strip()
        }
        if names_lower & denylist:
            return False

        # 3. Audience-scope gate. 'unclassified' only clears for the confidential
        #    audience (its allowed set is the only one that contains 'unclassified').
        if scope not in allowed:
            return False

        # 4. Concept folder-gate: a Concept must live in the Concepts/ vault folder
        #    (JOIN to entity_rid_mappings for vault_path) AND be on the allowlist.
        if normalize_type(entity_type) == "Concept":
            if not (names_lower & concept_allowlist):
                return False
            rows = await conn.fetch(
                "SELECT vault_path FROM entity_rid_mappings WHERE canonical_uri = $1",
                uri,
            )
            in_concepts_folder = any(
                (r["vault_path"] or "").startswith("Concepts/") for r in (rows or [])
            )
            if not in_concepts_folder:
                return False

        return True
    except Exception as exc:  # degrade-closed on ANY error
        logger.warning("visible_at(%r, %r) failed: %s", uri, audience, exc)
        return False
