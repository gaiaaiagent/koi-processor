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

CORAL_RETIREMENT = {
    "amount": "2",
    "batch_denom": "MBS01-002-20251201-20301231-001",
    "timestamp": "2026-08-10T20:53:35Z",
}

UNKNOWN_PROJECT_ID = "MBS01-999"


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
    """The next unknown product must refuse instead of inheriting coral values."""
    with pytest.raises(UnregisteredProjectError) as err:
        build_bloom_row(RETIREMENT, FakeCache(project_id=UNKNOWN_PROJECT_ID))
    assert err.value.project_id == UNKNOWN_PROJECT_ID
    assert "land_size or land_size_per_credit" in err.value.missing


def test_refusal_names_the_project_and_the_batch():
    """The operator must be able to act without reading the source."""
    with pytest.raises(UnregisteredProjectError) as err:
        build_bloom_row(RETIREMENT, FakeCache(project_id=UNKNOWN_PROJECT_ID))
    message = err.value.message()
    assert UNKNOWN_PROJECT_ID in message
    assert RETIREMENT["batch_denom"] in message


def test_refusal_carries_a_paste_ready_remedy():
    with pytest.raises(UnregisteredProjectError) as err:
        build_bloom_row(RETIREMENT, FakeCache(project_id=UNKNOWN_PROJECT_ID))
    remedy = err.value.remedy()
    assert f'"{UNKNOWN_PROJECT_ID}": {{' in remedy
    for field in COMMERCIAL_FIELDS:
        assert field in remedy, f"remedy omits {field}, so a fix from it would be incomplete"
    assert "land_size" in remedy
    assert "land_size_per_credit" in remedy
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


def test_registry_entry_without_a_land_size_mode_is_refused(monkeypatch):
    no_area = {k: v for k, v in PROJECTS["MBS01-002"].items() if k != "land_size"}
    monkeypatch.setitem(PROJECTS, "MBS01-002", no_area)

    with pytest.raises(UnregisteredProjectError) as err:
        build_bloom_row(CORAL_RETIREMENT, FakeCache(project_id="MBS01-002", jurisdiction="CR-P"))
    assert "land_size or land_size_per_credit" in err.value.missing


def test_registry_entry_with_two_land_size_modes_is_refused(monkeypatch):
    ambiguous = dict(PROJECTS["MBS01-002"], land_size_per_credit=0.1)
    monkeypatch.setitem(PROJECTS, "MBS01-002", ambiguous)

    with pytest.raises(UnregisteredProjectError) as err:
        build_bloom_row(CORAL_RETIREMENT, FakeCache(project_id="MBS01-002", jurisdiction="CR-P"))
    assert "exactly one of land_size or land_size_per_credit" in err.value.missing


def test_coral_project_exports_with_seatrees_supplied_values():
    """MBS01-002 must export the values SeaTrees supplied on 2026-08-13."""
    row = build_bloom_row(
        CORAL_RETIREMENT,
        FakeCache(project_id="MBS01-002", jurisdiction="CR-P", admin="wrong-admin"),
    )

    assert row["credit_price"] == 40
    assert row["purchase_amount"] == 80.0
    assert row["project_name"] == "Coral Reef: Golfo Dulce"
    assert row["project_developer"] == (
        "regen1a3g9wfm33l80eek2jwrhnfctr7vslfw7k7y83dvc6guy8cp9444q5d7vx0"
    )
    assert row["project_country"] == "Costa Rica"
    assert row["project_region"] == "Central America"
    assert row["transaction_description"] == "Purchase of Seatrees+ Biodiversity Blocks"
    assert row["credit_scheme"] == "Seatrees+ Biodiversity Blocks"
    assert row["activity_type"] == "Uplift, Stewardship"
    assert row["avg_price_per_hectare_per_year"] == 3200
    assert row["credit_size"] == 0.25
    assert row["credit_length"] == 5
    # Coral's 0.1 ha is the fixed project area, not two credits × credit_size.
    assert row["land_size"] == 0.1
