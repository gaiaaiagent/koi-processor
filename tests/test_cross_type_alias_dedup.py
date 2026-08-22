"""
Regression test for cross-type exact/alias dedup in the ingest resolver.

Bug (observed recurring 2026-05/06 for Polis / Pol.is / Talk to the City / Notion):
resolve_entity()'s Tier 1 (exact) and Tier 1.1 (alias) are gated by entity_type.
An LLM extractor that labels e.g. "Polis" as Concept/Project could never match the
existing schema:SoftwareApplication survivor (which carries "polis" as an alias),
so every type-gated tier missed and Tier 3 minted a fresh same-typed duplicate on
each ingest.

Fix: Tier 1.1b type-agnostic exact/alias fallback in resolve_entity(), accepted
ONLY when EXACTLY ONE live (merged_into IS NULL) entity matches by normalized_text
OR alias — otherwise the name is ambiguous across types (genuine polysemy) and we
fall through to fuzzy/semantic/create unchanged.

This is an HTTP integration test against the local personal-koi backend: POST
/entity/resolve runs the SAME resolve_entity() with the given type_hint and does
NOT persist (read-only), so it faithfully reproduces the ingest resolution path
without writing rows. Skipped when the backend is not reachable on :8351.

Author: Claude Code
Date: 2026-06-03
"""
import json
import os
import urllib.request
import pytest

BASE = "http://localhost:8351"


# /health performs an embedding-provider honesty check, so it is legitimately
# slow: measured 0.65s - 3.32s on a healthy backend (2026-08-22). At the previous
# timeout=3 a healthy-but-slow backend was indistinguishable from a dead one, and
# the 5 tests below SKIPPED. That is a false pass, not a neutral outcome: this
# file contributes 4 nodes to the AC5 regression baseline
# (~/koi-backups/red-baseline-B1-*.txt), so a silent skip makes `comm -13` read
# empty and reports "no new failures" for a run that never executed them. The
# stored baseline records that this already happened once, on 2026-08-21.
_HEALTH_TIMEOUT = float(os.getenv("KOI_HEALTH_TIMEOUT", "20"))


def _backend_up() -> bool:
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=_HEALTH_TIMEOUT) as r:
            return json.loads(r.read()).get("status") == "healthy"
    except Exception:
        return False


def _resolve(label: str, type_hint: str) -> dict:
    body = json.dumps({"label": label, "type_hint": type_hint}).encode()
    req = urllib.request.Request(
        BASE + "/entity/resolve",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


_BACKEND_UP = _backend_up()

if not _BACKEND_UP and os.getenv("KOI_REQUIRE_BACKEND") == "1":
    # Opt-in for gate runs: skipping is the failure mode that produced a bad
    # baseline, so a run that declares it needs the backend must go red, not green.
    raise RuntimeError(
        "KOI_REQUIRE_BACKEND=1 but the personal-koi backend is not healthy on "
        f"{BASE} within {_HEALTH_TIMEOUT}s. Refusing to skip silently."
    )

pytestmark = pytest.mark.skipif(
    not _BACKEND_UP, reason="personal-koi backend not reachable on :8351"
)


@pytest.mark.parametrize(
    "label,type_hint,expect_uri_contains",
    [
        ("Polis", "Concept", "softwareapplication-polis"),
        ("Pol.is", "Concept", "softwareapplication-polis"),
        ("Notion", "Concept", "softwareapplication-notion"),
        ("Notion", "Project", "softwareapplication-notion"),
    ],
)
def test_cross_type_input_resolves_to_single_survivor(label, type_hint, expect_uri_contains):
    """A wrong-typed input that matches exactly one live survivor by name/alias
    must resolve to it (is_new=False), not create a new same-typed duplicate."""
    res = _resolve(label, type_hint)
    assert res.get("is_new") is False, (
        f"{label!r}/{type_hint} resolved is_new=True — cross-type dedup regressed; got {res}"
    )
    cands = res.get("candidates") or []
    assert cands, f"no candidate returned for {label!r}/{type_hint}: {res}"
    assert expect_uri_contains in cands[0]["uri"], (
        f"{label!r}/{type_hint} resolved to {cands[0]['uri']!r}, "
        f"expected URI containing {expect_uri_contains!r}"
    )


def test_response_shape_is_stable():
    """Smoke: the resolver always returns a well-formed envelope."""
    res = _resolve("Polis", "Concept")
    assert "is_new" in res and "candidates" in res
