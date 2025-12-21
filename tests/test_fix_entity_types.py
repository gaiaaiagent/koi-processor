"""Unit tests for scripts.fix_entity_types."""

import sys

sys.path.insert(0, ".")

from scripts.fix_entity_types import TypeFixer, Variant, normalize_type


class DummyResolver:
    """Stubbed CanonicalResolver for tests."""

    def __init__(self, mapping):
        # mapping: entity_name -> {"canonical": str, "type": str}
        self.mapping = mapping

    def resolve(self, entity_name, entity_type=None, allow_type_mismatch=False):
        if entity_name in self.mapping:
            return self.mapping[entity_name]["canonical"], True
        return entity_name, False

    def get_canonical_type(self, entity_name):
        entry = self.mapping.get(entity_name)
        return entry["type"] if entry else None


def make_variant(entity_id: int, text: str, etype: str, count: int) -> Variant:
    """Helper to build a Variant quickly."""
    return Variant(
        id=entity_id,
        entity_text=text,
        entity_type=etype,
        normalized_text=text.lower(),
        occurrence_count=count,
        fuseki_uri=f"https://example.org/{entity_id}",
    )


def test_normalize_type_mapping():
    """Lower-case synonyms are normalized to expected types."""
    assert normalize_type("organization") == "ORGANIZATION"
    assert normalize_type("Person") == "PERSON"
    assert normalize_type("technology") == "TECHNOLOGY"
    assert normalize_type("unknown") == "UNKNOWN"


def test_resolve_type_prefers_canonical_mapping():
    """CanonicalResolver mapping wins over raw normalization."""
    fixer = TypeFixer(db_config={}, dry_run=True)
    fixer.resolver = DummyResolver(
        {
            "Regen Network": {"canonical": "Regen Network", "type": "ORGANIZATION"},
        }
    )

    resolved_type = fixer.resolve_type("Regen Network", "PERSON")
    assert resolved_type == "ORGANIZATION"


def test_build_plan_prefers_existing_canonical_variant():
    """Keeper is chosen from the variant that already matches canonical type."""
    fixer = TypeFixer(db_config={}, dry_run=True)
    fixer.resolver = DummyResolver(
        {"Regen Network": {"canonical": "Regen Network", "type": "ORGANIZATION"}}
    )

    variants = [
        make_variant(1, "Regen Network", "PERSON", 525),
        make_variant(2, "Regen Network", "ORGANIZATION", 1497),
    ]

    plan = fixer.build_plan("Regen Network", variants)
    assert plan.canonical_type == "ORGANIZATION"
    assert plan.keeper.id == 2  # already canonical + highest count
    assert plan.merges == [(1, 2, 525, "PERSON")]
    assert plan.type_changes == []


def test_build_plan_updates_keeper_when_canonical_missing():
    """If no canonical type variant exists, the keeper is updated to canonical."""
    fixer = TypeFixer(db_config={}, dry_run=True)
    fixer.resolver = DummyResolver(
        {"regen": {"canonical": "regen", "type": "PROJECT"}}
    )

    variants = [
        make_variant(10, "regen", "TOKEN", 300),
        make_variant(11, "regen", "PROJECT", 50),
    ]

    plan = fixer.build_plan("regen", variants)
    # Keeper is the canonical type variant even if its count is lower
    assert plan.keeper.id == 11
    assert plan.canonical_type == "PROJECT"
    # Non-canonical variant is merged into the canonical keeper
    assert plan.type_changes == []
    assert plan.merges == [(10, 11, 300, "TOKEN")]
