"""SeaTrees Bloom export: registered projects export unchanged, unregistered ones refuse.

Background: every commercial column in this export (price, scheme, credit
geometry) is product-specific and cannot be read from the chain. They were
previously global constants, so a retirement for any project other than
MBS01-001 silently inherited the mangrove product's values, including the
purchase_amount money column. See INC-20260812-001.

The first test is the make-before-break guard: the existing product's output
must be byte-identical after the change.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from scripts.seatrees_bloom_export import (  # noqa: E402
    COMMERCIAL_FIELDS,
    PROJECTS,
    MetadataCache,
    UnregisteredProjectError,
    build_bloom_row,
)


RETIREMENT = {
    "amount": "1000",
    "batch_denom": "MBS01-001-20240601-20340531-001",
    "timestamp": "2026-08-02T12:00:00Z",
}


class FakeCache(MetadataCache):
    """MetadataCache with the two chain lookups stubbed, so no network is used."""

    def __init__(self, project_id="MBS01-001", jurisdiction="KE", admin="regen1adminaddr"):
        self._project_id = project_id
        self._jurisdiction = jurisdiction
        self._admin = admin

    def get_batch_info(self, batch_denom):
        return {"project_id": self._project_id}

    def get_project_info(self, project_id):
        return {"jurisdiction": self._jurisdiction, "admin": self._admin, "metadata": "regen:123.rdf"}


# ── Make-before-break: the shipped product must not change ───────────

def test_registered_project_row_is_unchanged():
    """Golden row for MBS01-001, transcribed from behaviour before the change.

    If this fails, the fix altered output SeaTrees already receives.
    """
    row = build_bloom_row(RETIREMENT, FakeCache())

    assert row == {
        "date (RETIREMENT)": "2026-08-02",
        "purchase_type": "",
        "purchase_amount": 3000.0,
        "number_of_credits": 1000.0,
        "credit_price": 3,
        "transaction_description": "Purchase of Seatrees+ Biodiversity Blocks",
        "project_name": "Mangrove Forest: Marereni",
        "project_developer": "regen1adminaddr",
        "project_country": "Kenya",
        "project_region": "East Africa",
        "credit_scheme": "Seatrees+ Biodiversity Blocks",
        "activity_type": "Uplift, Stewardship",
        "avg_price_per_hectare_per_year": 3000,
        "credit_size": 0.0001,
        "credit_length": 10,
        "land_size": 0.1,
        "buyer_name": "",
        "buyer_email": "",
        "buyer_company": "",
        "buyer_country": "",
        "buyer_type": "",
        "buyer_channel": "",
        "buyer_notes": "",
    }


def test_blank_registered_developer_still_falls_back_to_admin():
    """Long-standing behaviour for MBS01-001, which registers developer as ""."""
    row = build_bloom_row(RETIREMENT, FakeCache(admin="regen1someoneelse"))
    assert row["project_developer"] == "regen1someoneelse"


# ── The reported failure: a second product must not inherit the first ─

def test_unregistered_project_is_refused():
    """The coral case. Previously this emitted price 3 and a blank name."""
    with pytest.raises(UnregisteredProjectError) as err:
        build_bloom_row(RETIREMENT, FakeCache(project_id="MBS01-002"))
    assert err.value.project_id == "MBS01-002"


def test_refusal_names_the_project_and_the_batch():
    """The operator must be able to act without reading the source."""
    with pytest.raises(UnregisteredProjectError) as err:
        build_bloom_row(RETIREMENT, FakeCache(project_id="MBS01-002"))
    message = err.value.message()
    assert "MBS01-002" in message
    assert RETIREMENT["batch_denom"] in message


def test_refusal_carries_a_paste_ready_remedy():
    with pytest.raises(UnregisteredProjectError) as err:
        build_bloom_row(RETIREMENT, FakeCache(project_id="MBS01-002"))
    remedy = err.value.remedy()
    assert '"MBS01-002": {' in remedy
    for field in COMMERCIAL_FIELDS:
        assert field in remedy, f"remedy omits {field}, so a fix from it would be incomplete"
    assert "Do not guess" in remedy


def test_batch_with_no_resolvable_project_is_refused():
    """A batch lookup that returns no project id used to yield blank columns."""
    cache = FakeCache(project_id="")
    with pytest.raises(UnregisteredProjectError):
        build_bloom_row(RETIREMENT, cache)


# ── A half-filled registry entry is as dangerous as none ─────────────

@pytest.mark.parametrize("missing_field", COMMERCIAL_FIELDS)
def test_registry_entry_missing_any_commercial_field_is_refused(monkeypatch, missing_field):
    """Registering a project without a price must not fall back to another
    product's price. Every commercial field is mandatory."""
    partial = {k: v for k, v in PROJECTS["MBS01-001"].items() if k != missing_field}
    monkeypatch.setitem(PROJECTS, "MBS01-002", partial)

    with pytest.raises(UnregisteredProjectError) as err:
        build_bloom_row(RETIREMENT, FakeCache(project_id="MBS01-002"))
    assert missing_field in err.value.missing


def test_registry_entry_without_a_name_is_refused(monkeypatch):
    """A blank name was the second reported symptom."""
    nameless = dict(PROJECTS["MBS01-001"], name="")
    monkeypatch.setitem(PROJECTS, "MBS01-002", nameless)

    with pytest.raises(UnregisteredProjectError) as err:
        build_bloom_row(RETIREMENT, FakeCache(project_id="MBS01-002"))
    assert "name" in err.value.missing


def test_a_fully_registered_second_product_exports_with_its_own_price(monkeypatch):
    """The fix must not make new products impossible, only unregistered ones."""
    coral = {
        "name": "Coral Reef: Example",
        "developer": "Example Developer",
        "credit_price": 40,
        "transaction_description": "Purchase of Coral Blocks",
        "credit_scheme": "Coral Blocks",
        "activity_type": "Uplift, Stewardship",
        "avg_price_per_hectare_per_year": 5000,
        "credit_size": 0.0002,
        "credit_length": 15,
    }
    monkeypatch.setitem(PROJECTS, "MBS01-002", coral)

    row = build_bloom_row(RETIREMENT, FakeCache(project_id="MBS01-002"))
    assert row["credit_price"] == 40
    assert row["purchase_amount"] == 40000.0
    assert row["project_name"] == "Coral Reef: Example"
    assert row["project_developer"] == "Example Developer"
    assert row["land_size"] == 0.2
