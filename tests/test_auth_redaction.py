"""Malformed-header redaction: no request header content may reach cred_kind.

Written 2026-09-08 after the Codex lane reproduced a defect in which a bare
`Authorization: <token>` (no scheme) had the whole token lowercased into
cred_kind and written to the durable log. Synthetic credentials only.

These are *property* tests, not example tests: the canary is a distinctive
synthetic secret, and every assertion asks whether it escaped ANYWHERE in the
emitted record -- not merely whether one field looks right.
"""
import json
import pytest
from api import auth_observability as ao

CANARY = "SuperSecretCanary1234567890AbCdEf"


def _record(auth_value=None, **extra):
    h = dict(extra)
    if auth_value is not None:
        h["authorization"] = auth_value
    return ao.decide("GET", "/stats", h)


MALFORMED = [
    pytest.param(CANARY, id="bare-token-no-scheme"),          # THE reported defect
    pytest.param(CANARY.lower(), id="bare-token-lowercase"),
    pytest.param(f"Basic {CANARY}", id="basic-scheme"),
    pytest.param(f"Token {CANARY}", id="token-scheme"),
    pytest.param(f"BEARER{CANARY}", id="bearer-no-space"),
    pytest.param(f"bearer  {CANARY}", id="bearer-double-space"),
    pytest.param(f"Bearer\t{CANARY}", id="bearer-tab-not-space"),
    pytest.param(" ", id="whitespace-only"),
    pytest.param(f"{CANARY} {CANARY}", id="token-then-token"),
    pytest.param(f"Negotiate {CANARY}==", id="negotiate-b64ish"),
]


@pytest.mark.parametrize("auth", MALFORMED)
def test_canary_never_appears_anywhere_in_the_record(auth):
    """The strongest form: serialize the WHOLE record and search it."""
    blob = json.dumps(_record(auth))
    assert CANARY not in blob
    assert CANARY.lower() not in blob      # the exact defect: lowercased secret
    assert CANARY.upper() not in blob


@pytest.mark.parametrize("auth", MALFORMED)
def test_cred_kind_is_always_a_fixed_label(auth):
    assert _record(auth)["cred_kind"] in ao.CRED_KINDS


def test_positive_control_the_canary_is_detectable():
    """If this fails, every assertion above is vacuous."""
    blob = json.dumps({"cred_kind": "non_bearer:" + CANARY.lower()})
    assert CANARY.lower() in blob          # the pre-fix behaviour WOULD be caught


def test_the_exact_reported_defect_is_gone():
    rec = _record(CANARY)
    assert not rec["cred_kind"].startswith("non_bearer:")
    assert rec["cred_kind"] == ao.KIND_MALFORMED_NO_SCHEME


# A whitespace-only header carries no caller identity, so there is nothing to
# fingerprint and an empty cred_fp is correct. Excluded deliberately -- the first
# version of this test asserted a fingerprint for it and failed, which was the
# test being wrong, not the code.
DISTINGUISHABLE = [p for p in MALFORMED if p.id != "whitespace-only"]


@pytest.mark.parametrize("auth", DISTINGUISHABLE)
def test_malformed_callers_remain_distinguishable(auth):
    """Redaction must not cost observability: distinct malformed headers must
    still yield distinct fingerprints, or the log cannot tell callers apart."""
    fp = _record(auth)["cred_fp"]
    assert fp and CANARY.lower() not in fp.lower()
    assert len(fp) <= 16


def test_whitespace_only_header_has_no_fingerprint():
    """Explicitly pinned: empty content yields no fingerprint, and is still
    labelled with a fixed kind rather than anything derived from the header."""
    rec = _record(" ")
    assert rec["cred_fp"] == ""
    assert rec["cred_kind"] in ao.CRED_KINDS


def test_two_different_malformed_headers_get_different_fingerprints():
    a = _record(CANARY)["cred_fp"]
    b = _record(CANARY + "X")["cred_fp"]
    assert a and b and a != b


def test_same_header_is_stable():
    assert _record(CANARY)["cred_fp"] == _record(CANARY)["cred_fp"]


def test_x_api_key_value_never_leaks():
    blob = json.dumps(ao.decide("GET", "/stats", {"x-api-key": CANARY}))
    assert CANARY not in blob and CANARY.lower() not in blob


def test_wellformed_bearer_still_classified():
    assert _record(f"Bearer {CANARY}")["cred_kind"] == ao.KIND_BEARER


def test_no_header_is_none():
    assert _record()["cred_kind"] == ao.KIND_NONE


def test_exempt_path_emits_no_credential_fields():
    rec = ao.decide("POST", "/koi-net/events/poll", {"authorization": CANARY})
    assert rec["verdict"] == "exempt"
    assert CANARY not in json.dumps(rec) and CANARY.lower() not in json.dumps(rec)
