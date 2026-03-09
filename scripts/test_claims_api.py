#!/usr/bin/env python3
"""Claims Engine V1 — API smoke tests.

Run: python -m scripts.test_claims_api [--base-url http://localhost:8351]

Tests:
1. Create claim → 201 + claim_rid + entity_uri
2. Idempotency: same body → same claim_rid
3. Get claim by RID → includes evidence field
4. List claims → results include created claim
5. Verify claim → state transition + audit log
6. Reject invalid transition → 409
7. Link evidence → evidences_claim edge
8. History → audit trail
9. Prepare anchor → content_hash computed
10. Entity graph integration verified
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error


BASE_URL = "http://localhost:8351"
PASS = 0
FAIL = 0
CREATED_RIDS = []


def _req(method: str, path: str, body=None, expected_status=None):
    """Make HTTP request and return (status, data)."""
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req) as resp:
            status = resp.status
            resp_data = json.loads(resp.read().decode())
            return status, resp_data
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            resp_data = json.loads(e.read().decode())
        except Exception:
            resp_data = {"detail": str(e)}
        return status, resp_data


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        print(f"  FAIL: {name} — {detail}")


def test_health():
    """Test that the server is reachable."""
    print("\n[0] Health check")
    try:
        status, data = _req("GET", "/health")
        check("server reachable", status == 200, f"status={status}")
    except Exception as e:
        check("server reachable", False, str(e))
        print("\n  Server not running. Start with: ~/.config/personal-koi/start.sh")
        sys.exit(1)


def test_setup_claimant():
    """Ensure a test claimant entity exists. Returns its URI."""
    print("\n[0b] Setup: ensure test claimant entity exists")
    # Check if a test entity exists by searching
    status, data = _req("POST", "/entity/resolve", {"label": "Claims Engine Test Org"})
    if status == 200 and data.get("candidates"):
        uri = data["candidates"][0]["uri"]
        check("test claimant found", True)
        return uri

    # Create one via the entity registry
    status, data = _req("POST", "/entities/register", {
        "entities": [{
            "name": "Claims Engine Test Org",
            "type": "Organization",
            "source": "test_claims_api",
        }]
    })
    if status == 200 and data.get("results"):
        uri = data["results"][0].get("uri")
        if uri:
            check("test claimant created", True)
            return uri

    # Fallback: try to find any existing organization
    status, data = _req("POST", "/entity/resolve", {"label": "Regen Network", "type_hint": "Organization"})
    if status == 200 and data.get("candidates"):
        uri = data["candidates"][0]["uri"]
        check("fallback claimant found", True)
        return uri

    check("test claimant setup", False, "Could not create or find a test entity")
    return None


def test_create_claim(claimant_uri: str):
    """Test claim creation."""
    print("\n[1] Create claim")
    body = {
        "claimant_uri": claimant_uri,
        "statement": "Test organization restored 50 hectares of degraded wetland in the Salish Sea bioregion between 2023-2025",
        "claim_type": "ecological",
        "metadata": {
            "quantity": 50,
            "unit": "hectares",
            "start_date": "2023-01-01",
            "end_date": "2025-12-31",
            "subject_location": "Salish Sea",
            "sdg_tags": ["SDG14", "SDG15"],
        },
    }
    status, data = _req("POST", "/claims/", body)
    check("status 201", status == 201, f"status={status} data={data}")
    check("claim_rid returned", "claim_rid" in data, str(data.keys()))
    check("entity_uri returned", "entity_uri" in data, str(data.keys()))
    check("verification=self_reported", data.get("verification") == "self_reported",
          f"got {data.get('verification')}")
    check("metadata preserved", data.get("metadata", {}).get("quantity") == 50,
          f"got {data.get('metadata')}")

    rid = data.get("claim_rid")
    if rid:
        CREATED_RIDS.append(rid)
    return rid, body


def test_idempotency(claimant_uri: str, original_body: dict, original_rid: str):
    """Test that same body → same RID."""
    print("\n[2] Idempotency")
    status, data = _req("POST", "/claims/", original_body)
    check("idempotent create succeeds", status in (200, 201), f"status={status}")
    check("same RID returned", data.get("claim_rid") == original_rid,
          f"expected={original_rid} got={data.get('claim_rid')}")


def test_get_claim(rid: str):
    """Test get claim by RID."""
    print("\n[3] Get claim by RID")
    status, data = _req("GET", f"/claims/{rid}")
    check("status 200", status == 200, f"status={status}")
    check("claim_rid matches", data.get("claim_rid") == rid, f"got {data.get('claim_rid')}")
    check("statement present", bool(data.get("statement")), "empty statement")
    check("evidence field present", "evidence" in data, str(data.keys()))


def test_list_claims():
    """Test list/search claims."""
    print("\n[4] List claims")
    status, data = _req("GET", "/claims/?verification=self_reported&limit=5")
    check("status 200", status == 200, f"status={status}")
    check("returns list", isinstance(data, list), f"type={type(data)}")
    if data:
        check("results have claim_rid", "claim_rid" in data[0], str(data[0].keys()))


def test_verify_claim(rid: str):
    """Test verification state transition."""
    print("\n[5] Verify claim (self_reported → peer_reviewed)")
    status, data = _req("PATCH", f"/claims/{rid}/verify", {
        "new_level": "peer_reviewed",
        "actor": "test_script",
        "reason": "Automated test verification",
    })
    check("status 200", status == 200, f"status={status} data={data}")
    check("verification=peer_reviewed", data.get("verification") == "peer_reviewed",
          f"got {data.get('verification')}")


def test_invalid_transition(rid: str):
    """Test that invalid transitions are rejected."""
    print("\n[6] Reject invalid transition (peer_reviewed → self_reported)")
    status, data = _req("PATCH", f"/claims/{rid}/verify", {
        "new_level": "self_reported",
        "actor": "test_script",
    })
    check("status 409", status == 409, f"status={status} data={data}")


def test_history(rid: str):
    """Test audit history."""
    print("\n[8] Claim history")
    status, data = _req("GET", f"/claims/{rid}/history")
    check("status 200", status == 200, f"status={status}")
    transitions = data.get("transitions", [])
    check("has transitions", len(transitions) >= 2, f"count={len(transitions)}")
    if transitions:
        check("first is creation", transitions[0].get("to_state") == "self_reported",
              f"got {transitions[0]}")


def test_prepare_anchor(rid: str):
    """Test content hash computation."""
    print("\n[9] Prepare anchor")
    status, data = _req("POST", f"/claims/{rid}/prepare-anchor")
    check("status 200", status == 200, f"status={status}")
    check("content_hash present", bool(data.get("content_hash")), str(data))
    check("ready_to_anchor=false", data.get("ready_to_anchor") is False, str(data))


def test_entity_graph(claimant_uri: str, rid: str):
    """Test entity graph integration."""
    print("\n[10] Entity graph integration")
    # Check entity_registry has a Claim entry
    status, data = _req("POST", "/entity/resolve", {"label": rid, "type_hint": "Claim"})
    # May not resolve by RID directly — check via relationships
    status2, data2 = _req("GET", f"/relationships/{urllib.parse.quote(claimant_uri, safe='')}")
    check("relationships endpoint reachable", status2 == 200, f"status={status2}")
    if status2 == 200:
        rels = data2 if isinstance(data2, list) else data2.get("relationships", [])
        makes_claim = [r for r in rels if r.get("predicate") == "makes_claim"]
        check("makes_claim edge exists", len(makes_claim) > 0,
              f"found {len(makes_claim)} makes_claim edges")


def test_not_found():
    """Test 404 for non-existent claim."""
    print("\n[11] Not found")
    status, data = _req("GET", "/claims/orn:koi-net.claim:doesnotexist")
    check("status 404", status == 404, f"status={status}")


def main():
    global BASE_URL
    parser = argparse.ArgumentParser(description="Claims Engine V1 smoke tests")
    parser.add_argument("--base-url", default="http://localhost:8351")
    args = parser.parse_args()
    BASE_URL = args.base_url

    print(f"Claims Engine V1 — Smoke Tests")
    print(f"Base URL: {BASE_URL}")
    print("=" * 50)

    test_health()

    claimant_uri = test_setup_claimant()
    if not claimant_uri:
        print("\nCannot proceed without a claimant entity. Exiting.")
        sys.exit(1)
    print(f"  Using claimant: {claimant_uri}")

    rid, body = test_create_claim(claimant_uri)
    if not rid:
        print("\nClaim creation failed. Exiting.")
        sys.exit(1)

    test_idempotency(claimant_uri, body, rid)
    test_get_claim(rid)
    test_list_claims()
    test_verify_claim(rid)
    test_invalid_transition(rid)
    test_history(rid)
    test_prepare_anchor(rid)

    try:
        import urllib.parse
        test_entity_graph(claimant_uri, rid)
    except ImportError:
        print("\n[10] Skipped (urllib.parse not available)")

    test_not_found()

    print("\n" + "=" * 50)
    print(f"Results: {PASS} passed, {FAIL} failed")
    if FAIL > 0:
        sys.exit(1)
    print("All tests passed!")


if __name__ == "__main__":
    import urllib.parse
    main()
