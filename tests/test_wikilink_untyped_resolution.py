"""The untyped wikilink tier must be able to run, and nested vault folders must keep their type.

THE DEFECT THIS PINS
--------------------
`batch_resolve_entities` resolves wikilink targets in three tiers. The middle tier — for
targets with no type hint — ordered by `occurrence_count`, a column that exists in
migrations/020_entity_registry.sql (RegenAI-era) and in ZERO tables of `personal_koi`.
So that tier did not merely rank badly: it raised `UndefinedColumnError` every time it was
reached, before any relationship was written and before the Step 3b vault-path fallback
that resolves exactly these targets correctly.

`POST /register-entity` catches the exception, logs a warning and returns success, so a
note whose frontmatter contained one untyped target silently lost ALL of its relationships.

Reached how: `parse_wikilink` does `folder, name = path.rsplit('/', 1)`, so a nested path
`Meetings/BKC COP/2026-02-09 BKC COP Meeting` yields the folder key
`"meetings/bkc cop"`, which is not in `folder_type_map` -> type_hint None. Nested is the
vault's actual convention, so essentially every meeting wikilink took the broken tier.
Measured 2026-08-23: 1,984 of 6,112 vault notes with a sync target carried at least one
untyped target, and `sourced_from` had 0 edges from a nested path against 1,944 declared
in the vault.

WHY NOT "just delete the ORDER BY"
----------------------------------
`DISTINCT ON` without a total order picks an arbitrary row per name, so the tier would
become nondeterministic instead of broken. The replacement must both exist and rank:
`(merged_into IS NOT NULL), id` prefers a live row over a tombstone and then breaks ties
stably. The pre-existing comment claiming this tier "actively PREFERS the tombstone,
which typically has the higher count" described behaviour that never once executed.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import asyncpg
import pytest

from api.vault_parser import FIELD_TO_PREDICATE, parse_wikilink

REPO = Path(__file__).resolve().parents[1]
VAULT_PARSER = REPO / "api" / "vault_parser.py"


def _source() -> str:
    return VAULT_PARSER.read_text()


# --------------------------------------------------------------------------------------
# The column must exist. This is the whole defect, stated as a property of the schema.
# --------------------------------------------------------------------------------------

async def _columns(table: str) -> set[str]:
    conn = await asyncpg.connect(os.environ["POSTGRES_URL"])
    try:
        rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = $1",
            table,
        )
        return {r["column_name"] for r in rows}
    finally:
        await conn.close()


def test_untyped_tier_orders_by_a_column_that_exists() -> None:
    """The ORDER BY of the untyped tier may only name real entity_registry columns."""
    src = _source()
    m = re.search(
        r"SELECT DISTINCT ON \(normalized_text\).*?ORDER BY ([^\n]+)\n",
        src,
        re.DOTALL,
    )
    assert m, "the untyped tier's DISTINCT ON query vanished — re-anchor this test"
    order_by = m.group(1)

    cols = asyncio.run(_columns("entity_registry"))
    assert cols, "information_schema returned nothing; a silent skip is what we are preventing"

    referenced = set(re.findall(r"[a-z_][a-z0-9_]*", order_by)) - {
        "desc", "asc", "nulls", "last", "first", "is", "not", "null", "and", "or"
    }
    missing = sorted(referenced - cols)
    assert not missing, (
        f"ORDER BY names column(s) absent from entity_registry: {missing}. "
        "This raises UndefinedColumnError and the caller swallows it."
    )


def test_untyped_tier_query_actually_executes() -> None:
    """Run the tier's real SQL. Before the fix this raises UndefinedColumnError."""
    src = _source()
    m = re.search(
        r'(SELECT DISTINCT ON \(normalized_text\).*?)\n\s*"""',
        src,
        re.DOTALL,
    )
    assert m, "could not extract the untyped-tier SQL — re-anchor this test"
    sql = m.group(1)

    async def run() -> None:
        conn = await asyncpg.connect(os.environ["POSTGRES_URL"])
        try:
            await conn.fetch(sql, ["a name that resolves to nothing"])
        finally:
            await conn.close()

    asyncio.run(run())


def test_untyped_tier_is_deterministic() -> None:
    """DISTINCT ON without a total order returns an arbitrary row; forbid that regression."""
    src = _source()
    m = re.search(
        r"SELECT DISTINCT ON \(normalized_text\).*?ORDER BY ([^\n]+)\n", src, re.DOTALL
    )
    assert m, "the untyped tier's DISTINCT ON query vanished — re-anchor this test"
    order_by = m.group(1).strip()
    assert "normalized_text" in order_by, "DISTINCT ON key must lead the ORDER BY"
    tail = order_by.split("normalized_text", 1)[1]
    assert tail.strip(" ,"), (
        "ORDER BY has no tiebreak after normalized_text: DISTINCT ON would pick "
        "an arbitrary row per name"
    )
    assert "id" in tail, "no stable unique tiebreak (expected id) in the ORDER BY"


def test_occurrence_count_never_returns_to_vault_parser() -> None:
    """A RegenAI-era column. Re-introducing it re-breaks the tier silently."""
    offenders = [
        f"{VAULT_PARSER.name}:{i}: {line.strip()}"
        for i, line in enumerate(_source().split("\n"), 1)
        if "occurrence_count" in line and not line.lstrip().startswith("#")
    ]
    assert not offenders, "occurrence_count is back in executable code:\n" + "\n".join(offenders)


# --------------------------------------------------------------------------------------
# Nested vault folders must keep their type hint (fix (a)).
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected_type",
    [
        ("[[Meetings/2026-06-30 IndigenomicsAI Meeting]]", "Meeting"),
        ("[[Meetings/IndigenomicsAI/2026-06-30 IndigenomicsAI Meeting]]", "Meeting"),
        ("[[Meetings/BKC COP/2026-02-09 BKC COP Meeting]]", "Meeting"),
        ("[[Meetings/The Salish Sea Dreaming/2026-03-13 Meeting]]", "Meeting"),
        ("[[People/Shawn Anderson]]", "Person"),
        ("[[People/Cascadia/Shawn Anderson]]", "Person"),
        ("[[Organizations/Regen Network]]", "Organization"),
    ],
)
def test_nested_folders_keep_their_type_hint(raw: str, expected_type: str) -> None:
    _, hint = parse_wikilink(raw)
    assert hint == expected_type, (
        f"{raw} lost its type hint. rsplit('/', 1) makes the folder key the whole prefix, "
        "so a nested path never matches folder_type_map and falls into the untyped tier."
    )


def test_bare_wikilink_still_has_no_type_hint() -> None:
    """The untyped tier must stay reachable — this is not a licence to type everything."""
    assert parse_wikilink("[[Regen Network]]") == ("Regen Network", None)


def test_nested_meeting_wikilink_survives_the_full_target_build() -> None:
    """End-to-end on the sourceNote field: the shape that dominated the vault."""
    predicate, direction, default_hint = FIELD_TO_PREDICATE["sourcenote"]
    assert default_hint is None, "sourceNote relies on the folder prefix for its type"
    name, hint = parse_wikilink("[[Meetings/BKC COP/2026-02-09 BKC COP Meeting]]")
    assert (hint or default_hint) == "Meeting", (
        "sourceNote wikilinks to nested meeting folders resolve untyped, which is how "
        "1,944 declared sourced_from edges became 0 in the graph"
    )
