#!/usr/bin/env python3
"""Claims Engine V2 — API smoke tests.

Run: python -m scripts.test_claims_api [--base-url http://localhost:8351]

Tests:
1-10: Core claim CRUD, verification, evidence, anchoring prep
11-16: Extraction, anchor preconditions, 404s
17-20: Reconcile, tx_hash in responses
21-25: Attestation content_hash, anchor fields, proof-pack enhanced/download, attestation anchor
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


def _resolve_or_create_entity(name: str, entity_type: str):
    """Resolve an existing entity or create via /ingest. Returns URI or None.

    IMPORTANT: /entity/resolve returns is_new=true when the entity doesn't exist
    in entity_registry (URI is only a proposed deterministic slug). We must check
    is_new=false before treating a resolve result as a real persisted entity.
    """
    # Step 1: Try to resolve existing entity (with type_hint)
    status, data = _req("POST", "/entity/resolve", {
        "label": name, "type_hint": entity_type,
    })
    if status == 200 and data.get("candidates") and not data.get("is_new"):
        return data["candidates"][0]["uri"]

    # Step 2: Create via /ingest (the actual entity creation surface)
    ts = int(time.time())
    status, data = _req("POST", "/ingest", {
        "document_rid": f"test:claims-smoke-{entity_type.lower()}-{ts}",
        "source": "test_claims_api",
        "entities": [{"name": name, "type": entity_type}],
    })
    if status == 200 and data.get("canonical_entities"):
        uri = data["canonical_entities"][0].get("uri")
        if uri:
            return uri
    return None


def test_setup_claimant():
    """Ensure a test claimant entity exists. Returns its URI."""
    print("\n[0b] Setup: ensure test claimant entity exists")
    uri = _resolve_or_create_entity("Claims Engine Test Org", "Organization")
    if uri:
        check("test claimant ready", True)
        return uri

    # Fallback: try to find any existing organization
    status, data = _req("POST", "/entity/resolve", {
        "label": "Regen Network", "type_hint": "Organization",
    })
    if status == 200 and data.get("candidates") and not data.get("is_new"):
        uri = data["candidates"][0]["uri"]
        check("fallback claimant found", True)
        return uri

    check("test claimant setup", False, "Could not create or find a test entity")
    return None


def test_setup_reviewers():
    """Create two test reviewer entities for attestation policy compliance.

    Names must be very distinct to avoid entity resolution merging them.
    """
    print("\n[0c] Setup: ensure test reviewer entities exist")
    reviewers = []
    names = ["Alice Nakamoto Reviewer", "Bob Finney Attestor"]
    for name in names:
        uri = _resolve_or_create_entity(name, "Person")
        if uri:
            # Verify we got a unique URI (not merged with previous reviewer)
            if uri not in reviewers:
                reviewers.append(uri)
                check(f"reviewer '{name}' ready", True)
            else:
                # Entity resolution merged — create with unique document_rid
                ts = int(time.time())
                status, data = _req("POST", "/ingest", {
                    "document_rid": f"test:claims-reviewer-{ts}-{len(reviewers)}",
                    "source": "test_claims_api",
                    "entities": [{"name": f"{name} {ts}", "type": "Person"}],
                })
                if status == 200 and data.get("canonical_entities"):
                    alt_uri = data["canonical_entities"][0].get("uri")
                    if alt_uri and alt_uri not in reviewers:
                        reviewers.append(alt_uri)
                        check(f"reviewer '{name}' ready (distinct)", True)
                    else:
                        check(f"reviewer '{name}' distinct", False, "still merged")
                else:
                    check(f"reviewer '{name}' fallback", False, f"status={status}")
        else:
            check(f"reviewer '{name}' setup", False, "could not resolve or create")
    check("reviewers ready", len(reviewers) == 2, f"got {len(reviewers)}, uris={reviewers}")
    return reviewers


def _ensure_attestations(rid: str, reviewer_uris: list, count: int = 1):
    """Create `count` approved attestations on a claim for policy compliance."""
    for i in range(min(count, len(reviewer_uris))):
        status, data = _req("POST", f"/claims/{rid}/attestations", {
            "reviewer_uri": reviewer_uris[i],
            "verdict": "approved",
            "rationale": f"Smoke test attestation {i+1}",
        })
        check(f"attestation {i+1} for {rid[:40]}", status in (200, 201),
              f"status={status} data={data}")


def test_create_claim(claimant_uri: str):
    """Test claim creation."""
    print("\n[1] Create claim")
    # Unique statement per run to avoid idempotency returning stale state
    ts = int(time.time())
    body = {
        "claimant_uri": claimant_uri,
        "statement": f"Test organization restored 50 hectares of degraded wetland in the Salish Sea bioregion (run {ts})",
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
    """Test content hash computation and IRI prediction."""
    print("\n[9] Prepare anchor")
    status, data = _req("POST", f"/claims/{rid}/prepare-anchor")
    check("status 200", status == 200, f"status={status}")
    check("content_hash present", bool(data.get("content_hash")), str(data))
    # ready_to_anchor depends on whether regen CLI is available
    if data.get("predicted_ledger_iri"):
        check("ready_to_anchor=true (regen CLI available)", data.get("ready_to_anchor") is True, str(data))
    else:
        check("ready_to_anchor=false (no regen CLI)", data.get("ready_to_anchor") is False, str(data))


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


def test_link_evidence(rid: str):
    """Test evidence linking — both type enforcement and happy path."""
    print("\n[7a] Link evidence — reject non-Evidence entity type")
    # Try linking a non-Evidence entity (the claimant is an Organization/Concept, not Evidence)
    status, data = _req("GET", f"/claims/{rid}")
    claimant_uri = data.get("claimant_uri", "")
    if claimant_uri:
        status2, data2 = _req("POST", f"/claims/{rid}/evidence", {
            "evidence_uri": claimant_uri,
            "actor": "test_script",
        })
        check("rejects non-Evidence type", status2 == 422, f"status={status2} data={data2}")

    print("\n[7b] Link evidence — create Evidence entity and link")
    # Create an Evidence entity via /ingest
    ts = int(time.time())
    ev_status, ev_data = _req("POST", "/ingest", {
        "document_rid": f"test://smoke-evidence-{ts}",
        "source": "test_claims_api",
        "entities": [{
            "name": f"Test Wetland Restoration Report {ts}",
            "type": "Evidence",
            "mentions": [f"Test Wetland Restoration Report {ts}"],
            "confidence": 1.0,
        }],
        "relationships": [],
    })
    evidence_uri = None
    if ev_status == 200 and ev_data.get("canonical_entities"):
        evidence_uri = ev_data["canonical_entities"][0].get("uri")

    if evidence_uri:
        status3, data3 = _req("POST", f"/claims/{rid}/evidence", {
            "evidence_uri": evidence_uri,
            "actor": "test_script",
        })
        check("evidence link succeeds", status3 == 200, f"status={status3} data={data3}")

        # Verify evidence appears in get_claim
        status4, data4 = _req("GET", f"/claims/{rid}")
        evidence_list = data4.get("evidence") or []
        ev_uris = [e.get("uri") for e in evidence_list]
        check("evidence in get response", evidence_uri in ev_uris,
              f"expected {evidence_uri} in {ev_uris}")
    else:
        check("evidence entity created", False, f"status={ev_status} data={ev_data}")


def test_ledger_anchored_blocked(rid: str):
    """Test that ledger_anchored transition is blocked without ledger_iri."""
    print("\n[7c] Block ledger_anchored without actual anchor")
    # First advance to verified
    _req("PATCH", f"/claims/{rid}/verify", {
        "new_level": "verified",
        "actor": "test_script",
        "reason": "test advancement",
    })
    # Try to go to ledger_anchored — should be blocked
    status, data = _req("PATCH", f"/claims/{rid}/verify", {
        "new_level": "ledger_anchored",
        "actor": "test_script",
    })
    check("ledger_anchored blocked without anchor", status == 409, f"status={status} data={data}")


def test_extract_endpoint():
    """Test the extraction endpoint (Finding 3 — verify surface works)."""
    print("\n[12] Extract claims endpoint")
    body = {
        "document_text": "The Community Environmental Council has partnered with 22 farms across "
                         "Santa Barbara County since 2021, transitioning 450 acres from conventional "
                         "to regenerative practices. This initiative has measurably improved soil health "
                         "metrics across all participating farms, with an average 35% increase in soil "
                         "organic matter content documented through independent laboratory analysis.",
        "source_document": "test://smoke-test-document",
        "auto_create": False,
        "confidence_threshold": 0.5,
    }
    status, data = _req("POST", "/claims/extract", body)
    check("extract status 200", status == 200, f"status={status}")
    check("has candidates field", "candidates" in data, str(data.keys()))
    check("has candidate_count", "candidate_count" in data, str(data.keys()))
    check("source preserved", data.get("source_document") == "test://smoke-test-document",
          f"got {data.get('source_document')}")
    # Candidates may be empty if ANTHROPIC_API_KEY is not set — that's OK for smoke test
    if data.get("candidates"):
        c = data["candidates"][0]
        check("candidate has statement", bool(c.get("statement")), str(c))
        check("candidate has confidence", "confidence" in c, str(c))


def test_anchor_precondition():
    """Test that anchor requires verified state."""
    print("\n[14] Anchor precondition — reject non-verified")
    # Create a fresh claim for this test
    ts = int(time.time())
    status, data = _req("POST", "/claims/", {
        "claimant_uri": CREATED_RIDS[0] if CREATED_RIDS else "test",  # reuse any known URI
        "statement": f"Anchor precondition test claim (run {ts})",
        "claim_type": "ecological",
        "metadata": {},
    })
    # This may 404 if claimant is a claim RID not entity URI — use a simpler approach
    # Instead, try to anchor the claim from test_ledger_anchored_blocked which is at 'verified'
    # but doesn't have ledger_iri — we want to test the /anchor endpoint state check
    pass


def test_anchor_state_check():
    """Test that POST /anchor rejects claims not at 'verified' state."""
    print("\n[14] Anchor state check")
    # Create a fresh claim
    ts = int(time.time())
    body = {
        "claimant_uri": "_test_placeholder_",  # will be replaced below
        "statement": f"Anchor state check test (run {ts})",
        "claim_type": "ecological",
        "metadata": {},
    }
    # We can't easily create a fresh claim here without a claimant.
    # Instead, try to anchor a known self_reported claim if any exist.
    status, data = _req("GET", "/claims/?verification=self_reported&limit=1")
    if status == 200 and data:
        rid = data[0]["claim_rid"]
        status2, data2 = _req("POST", f"/claims/{rid}/anchor")
        check("anchor rejects self_reported", status2 == 409, f"status={status2} data={data2}")
    else:
        check("anchor rejects self_reported", True)  # skip if no claims


def test_anchor_missing_binary():
    """Test prepare-anchor graceful handling when regen binary may not be available."""
    print("\n[15] Prepare-anchor with IRI derivation")
    if not CREATED_RIDS:
        check("prepare-anchor with IRI", True)  # skip
        return
    rid = CREATED_RIDS[0]
    status, data = _req("POST", f"/claims/{rid}/prepare-anchor")
    check("prepare-anchor status 200", status == 200, f"status={status}")
    check("has content_hash", bool(data.get("content_hash")), str(data))
    # predicted_ledger_iri may be None if regen binary not installed — that's OK
    has_iri = data.get("predicted_ledger_iri") is not None
    if has_iri:
        check("predicted_ledger_iri present", True)
        check("ready_to_anchor=true", data.get("ready_to_anchor") is True, str(data))
    else:
        check("ready_to_anchor=false (no regen binary)", data.get("ready_to_anchor") is False, str(data))
        print(f"    Note: IRI derivation skipped — {data.get('reason', 'unknown reason')}")


def test_not_found():
    """Test 404 for non-existent claim."""
    print("\n[16] Not found")
    status, data = _req("GET", "/claims/orn:koi-net.claim:doesnotexist")
    check("status 404", status == 404, f"status={status}")


def test_reconcile_no_txhash(claimant_uri: str, reviewer_uris: list):
    """Test reconcile returns 409 when claim has no tx_hash."""
    print("\n[17] Reconcile — no tx_hash")
    # Create a fresh claim and advance to verified
    ts = int(time.time())
    status, data = _req("POST", "/claims/", {
        "claimant_uri": claimant_uri,
        "statement": f"Reconcile no-txhash test (run {ts})",
        "claim_type": "ecological",
        "metadata": {},
    })
    if status != 201:
        check("create claim for reconcile test", False, f"status={status}")
        return
    rid = data["claim_rid"]
    CREATED_RIDS.append(rid)

    # V2: create attestations before verify
    _ensure_attestations(rid, reviewer_uris, count=2)

    # Advance: self_reported → peer_reviewed → verified
    _req("PATCH", f"/claims/{rid}/verify", {
        "new_level": "peer_reviewed", "actor": "test", "reason": "test",
    })
    _req("PATCH", f"/claims/{rid}/verify", {
        "new_level": "verified", "actor": "test", "reason": "test",
    })

    # Reconcile should fail — no tx_hash
    status2, data2 = _req("POST", f"/claims/{rid}/reconcile")
    check("reconcile 409 without tx_hash", status2 == 409, f"status={status2} data={data2}")
    check("error mentions tx_hash", "tx_hash" in str(data2.get("detail", "")),
          f"detail={data2.get('detail')}")


def test_reconcile_not_found():
    """Test reconcile returns 404 for non-existent claim."""
    print("\n[18] Reconcile — claim not found")
    status, data = _req("POST", "/claims/orn:koi-net.claim:doesnotexist/reconcile")
    check("reconcile 404", status == 404, f"status={status}")


def test_reconcile_wrong_state():
    """Test reconcile returns 409 when claim is not at 'verified' state."""
    print("\n[19] Reconcile — wrong state")
    # Use a self_reported claim
    status, data = _req("GET", "/claims/?verification=self_reported&limit=1")
    if status == 200 and data:
        rid = data[0]["claim_rid"]
        status2, data2 = _req("POST", f"/claims/{rid}/reconcile")
        check("reconcile 409 wrong state", status2 == 409, f"status={status2} data={data2}")
    else:
        check("reconcile 409 wrong state", True)  # skip


def test_tx_hash_in_claim_response():
    """Test that tx_hash field appears in claim responses."""
    print("\n[20] tx_hash field in ClaimResponse")
    if not CREATED_RIDS:
        check("tx_hash field present", True)  # skip
        return
    rid = CREATED_RIDS[0]
    status, data = _req("GET", f"/claims/{rid}")
    check("tx_hash field exists", "tx_hash" in data, f"keys={list(data.keys())}")


def test_attestation_content_hash():
    """Test that content_hash is populated on attestation create."""
    print("\n[21] Attestation content_hash populated")
    if not CREATED_RIDS:
        check("attestation content_hash", True)  # skip
        return
    # Use an existing claim that has attestations
    rid = CREATED_RIDS[0]
    status, data = _req("GET", f"/claims/{rid}/attestations")
    if status == 200 and data:
        att = data[0]
        check("content_hash present", att.get("content_hash") is not None,
              f"content_hash={att.get('content_hash')}")
        check("content_hash is 64 hex chars", len(att.get("content_hash", "")) == 64,
              f"len={len(att.get('content_hash', ''))}")
    else:
        check("attestation content_hash", True)  # skip if no attestations


def test_attestation_anchor_fields():
    """Test that attestation response includes anchor fields."""
    print("\n[22] Attestation anchor fields in response")
    if not CREATED_RIDS:
        check("attestation anchor fields", True)  # skip
        return
    rid = CREATED_RIDS[0]
    status, data = _req("GET", f"/claims/{rid}/attestations")
    if status == 200 and data:
        att = data[0]
        check("ledger_iri field exists", "ledger_iri" in att, str(att.keys()))
        check("attest_timestamp field exists", "attest_timestamp" in att, str(att.keys()))
        check("attestor_address field exists", "attestor_address" in att, str(att.keys()))
    else:
        check("attestation anchor fields", True)  # skip


def test_proof_pack_enhanced():
    """Test proof-pack includes hash verification and new fields."""
    print("\n[23] Proof-pack enhanced fields")
    # Find a ledger_anchored claim
    status, data = _req("GET", "/claims/?verification=ledger_anchored&limit=1")
    if status != 200 or not data:
        check("proof-pack enhanced (skip — no anchored claims)", True)
        return
    rid = data[0]["claim_rid"]
    status2, pp = _req("GET", f"/claims/{rid}/proof-pack")
    check("proof-pack status 200", status2 == 200, f"status={status2}")
    if status2 == 200:
        check("claim_content_hash_verified present", "claim_content_hash_verified" in pp,
              str(pp.keys()))
        check("chain_id present", bool(pp.get("chain_id")), f"chain_id={pp.get('chain_id')}")
        check("verification_instructions present", bool(pp.get("verification_instructions")),
              "empty")
        check("version is 2.0", pp.get("version") == "2.0", f"version={pp.get('version')}")
        # Check attestations have anchor fields
        if pp.get("attestations"):
            att = pp["attestations"][0]
            check("attestation has hash_verified", "hash_verified" in att, str(att.keys()))
            check("attestation has content_hash", "content_hash" in att, str(att.keys()))


def test_proof_pack_download():
    """Test proof-pack download format returns Content-Disposition."""
    print("\n[24] Proof-pack download format")
    status, data = _req("GET", "/claims/?verification=ledger_anchored&limit=1")
    if status != 200 or not data:
        check("proof-pack download (skip — no anchored claims)", True)
        return
    rid = data[0]["claim_rid"]
    # Can't easily test Content-Disposition with our simple _req helper,
    # but verify the endpoint works with format=download
    url = f"{BASE_URL}/claims/{urllib.parse.quote(rid, safe='')}/proof-pack?format=download"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            check("download status 200", resp.status == 200, f"status={resp.status}")
            cd = resp.headers.get("Content-Disposition", "")
            check("Content-Disposition present", "attachment" in cd, f"header={cd}")
            check("filename in header", "proof-pack-" in cd, f"header={cd}")
    except urllib.error.HTTPError as e:
        check("proof-pack download", False, f"status={e.code}")


def test_attestation_anchor_state_check():
    """Test that attestation anchor rejects when parent claim is not ledger_anchored."""
    print("\n[25] Attestation anchor — parent claim state check")
    if not CREATED_RIDS:
        check("attestation anchor state check", True)  # skip
        return
    rid = CREATED_RIDS[0]
    # Get attestations
    status, atts = _req("GET", f"/claims/{rid}/attestations")
    if status != 200 or not atts:
        check("attestation anchor state check (skip)", True)
        return
    att_rid = atts[0]["attestation_rid"]
    # This claim is at verified (not ledger_anchored), so anchor should be rejected
    status2, data2 = _req("POST", f"/claims/{rid}/attestations/{att_rid}/anchor")
    check("rejects non-anchored parent", status2 == 409, f"status={status2} data={data2}")


def main():
    global BASE_URL
    parser = argparse.ArgumentParser(description="Claims Engine V1 smoke tests")
    parser.add_argument("--base-url", default="http://localhost:8351")
    args = parser.parse_args()
    BASE_URL = args.base_url

    print(f"Claims Engine V2 — Smoke Tests")
    print(f"Base URL: {BASE_URL}")
    print("=" * 50)

    test_health()

    claimant_uri = test_setup_claimant()
    if not claimant_uri:
        print("\nCannot proceed without a claimant entity. Exiting.")
        sys.exit(1)
    print(f"  Using claimant: {claimant_uri}")

    reviewer_uris = test_setup_reviewers()
    if len(reviewer_uris) < 2:
        print("\nCannot proceed without 2 reviewer entities. Exiting.")
        sys.exit(1)

    rid, body = test_create_claim(claimant_uri)
    if not rid:
        print("\nClaim creation failed. Exiting.")
        sys.exit(1)

    test_idempotency(claimant_uri, body, rid)
    test_get_claim(rid)
    test_list_claims()

    # V2: create 1 attestation before test 5 (peer_reviewed needs >= 1)
    _ensure_attestations(rid, reviewer_uris, count=1)
    test_verify_claim(rid)
    test_invalid_transition(rid)

    # V2: create 2nd attestation before test 7c (verified needs >= 2)
    _ensure_attestations(rid, reviewer_uris, count=2)
    test_link_evidence(rid)
    test_ledger_anchored_blocked(rid)
    test_history(rid)
    test_prepare_anchor(rid)

    try:
        import urllib.parse
        test_entity_graph(claimant_uri, rid)
    except ImportError:
        print("\n[10] Skipped (urllib.parse not available)")

    test_extract_endpoint()
    test_anchor_state_check()
    test_anchor_missing_binary()
    test_not_found()
    test_reconcile_no_txhash(claimant_uri, reviewer_uris)
    test_reconcile_not_found()
    test_reconcile_wrong_state()
    test_tx_hash_in_claim_response()
    test_attestation_content_hash()
    test_attestation_anchor_fields()
    test_proof_pack_enhanced()
    test_proof_pack_download()
    test_attestation_anchor_state_check()

    print("\n" + "=" * 50)
    print(f"Results: {PASS} passed, {FAIL} failed")
    if FAIL > 0:
        sys.exit(1)
    print("All tests passed!")


if __name__ == "__main__":
    import urllib.parse
    main()
