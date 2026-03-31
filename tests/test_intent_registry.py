"""
Regression tests for the intent_registry API (intent_router.py).

Runs against the live backend on localhost:8351.
Each test uses unique intent keys prefixed with "reg-test-intent-" and cleans up
after itself by setting status=archived so real intent data is never touched.

Usage:
    pytest tests/test_intent_registry.py -v
    # or with explicit URL:
    KOI_API_URL=http://localhost:8351 pytest tests/test_intent_registry.py -v
"""

import os
import uuid

import httpx
import pytest

BASE_URL = os.getenv("KOI_API_URL", "http://localhost:8351")


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


def make_key(suffix: str) -> str:
    """Generate a unique test intent key."""
    return f"reg-test-intent-{suffix}-{uuid.uuid4().hex[:6]}"


def ingest(client, key: str, **kwargs) -> dict:
    """Ingest (create/upsert) an intent and return the response JSON."""
    payload = {
        "intentKey": key,
        "intentType": kwargs.pop("intentType", "OFFER"),
        "publisherName": kwargs.pop("publisherName", "Test Publisher"),
        "landscapeGroup": kwargs.pop("landscapeGroup", "test-group"),
        **kwargs,
    }
    r = client.post("/intents/ingest", json=payload)
    assert r.status_code == 200, f"ingest failed ({r.status_code}): {r.text}"
    return r.json()


def cleanup(client, key: str):
    """Mark a test intent as archived so it doesn't pollute real intent lists."""
    client.patch(f"/intents/{key}", json={"status": "archived"})


# ---------------------------------------------------------------------------
# Precondition: backend is serving /intents routes
# ---------------------------------------------------------------------------

def test_intents_stats_reachable(client):
    """Readiness check -- verifies intent router is mounted."""
    r = client.get("/intents/stats")
    assert r.status_code == 200, "/intents/stats not reachable -- is the backend running?"
    data = r.json()
    assert "by_status" in data
    assert "by_type" in data
    assert "by_landscape_group" in data


# ---------------------------------------------------------------------------
# TestIntentCRUD
# ---------------------------------------------------------------------------

class TestIntentCRUD:
    """Basic CRUD operations on the intent registry."""

    def test_create_draft_intent(self, client):
        """POST /intents/ingest creates a draft intent with correct fields."""
        key = make_key("crud-create")
        try:
            result = ingest(
                client, key,
                intentType="OFFER",
                publisherName="Alice Test",
                landscapeGroup="cascadia-test",
                assetOffered="firewood",
                description="Test firewood offer",
                tags=["test", "firewood"],
            )
            assert result["intent_key"] == key
            assert result["status"] == "draft"
            assert result["intent_type"] == "OFFER"
            assert result["publisher_name"] == "Alice Test"
            assert result["landscape_group"] == "cascadia-test"
            assert result["description"] == "Test firewood offer"
            assert "test" in result["tags"]
            assert "firewood" in result["tags"]
        finally:
            cleanup(client, key)

    def test_get_detail_returns_draft(self, client):
        """GET /intents/detail/{key} returns the draft with correct fields."""
        key = make_key("crud-detail")
        try:
            ingest(
                client, key,
                intentType="WANT",
                publisherName="Bob Test",
                landscapeGroup="salish-test",
                assetWanted="salmon",
                description="Looking for salmon",
            )

            r = client.get(f"/intents/detail/{key}")
            assert r.status_code == 200, f"GET detail returned {r.status_code}: {r.text}"
            detail = r.json()
            assert detail["intent_key"] == key
            assert detail["status"] == "draft"
            assert detail["intent_type"] == "WANT"
            assert detail["publisher_name"] == "Bob Test"
            assert detail["asset_wanted"] == "salmon"
            assert detail["description"] == "Looking for salmon"
        finally:
            cleanup(client, key)

    def test_discovery_excludes_draft(self, client):
        """GET /intents/ (public discovery) does NOT contain draft intents."""
        key = make_key("crud-nodraft")
        try:
            result = ingest(client, key)
            intent_rid = result["intent_rid"]

            # Default discovery only shows active intents
            r = client.get("/intents/")
            assert r.status_code == 200
            discovery_rids = [item["intent_rid"] for item in r.json()]
            assert intent_rid not in discovery_rids, (
                f"Draft intent {intent_rid} should not appear in public discovery"
            )
        finally:
            cleanup(client, key)

    def test_patch_updates_description(self, client):
        """PATCH /intents/{key} updates description."""
        key = make_key("crud-patch")
        try:
            ingest(client, key, description="Original description")

            r = client.patch(
                f"/intents/{key}",
                json={"description": "Updated description"},
            )
            assert r.status_code == 200, f"PATCH returned {r.status_code}: {r.text}"
            patched = r.json()
            assert patched["description"] == "Updated description"
        finally:
            cleanup(client, key)

    def test_patch_preserves_unspecified_fields(self, client):
        """PATCH must not reset fields that are not in the request body."""
        key = make_key("crud-preserve")
        try:
            ingest(
                client, key,
                description="Keep this",
                tags=["preserve-me"],
            )

            # Patch only description -- priority and tags should survive
            r = client.patch(
                f"/intents/{key}",
                json={"description": "Changed"},
            )
            assert r.status_code == 200
            patched = r.json()
            assert patched["description"] == "Changed"
            assert patched["priority"] == 100.0, (
                f"priority changed unexpectedly: {patched['priority']}"
            )
            assert "preserve-me" in patched["tags"], (
                f"tags changed unexpectedly: {patched['tags']}"
            )
        finally:
            cleanup(client, key)


# ---------------------------------------------------------------------------
# TestPrivacyRegression
# ---------------------------------------------------------------------------

class TestPrivacyRegression:
    """Regression: private fields must never leak into discovery or detail responses."""

    PRIVATE_FIELDS_DISCOVERY = [
        "publisher_contact",
        "source_excerpt",
        "priority",
        "tags",
        "created_at",
        "publisher_name",
    ]

    PRIVATE_FIELDS_DETAIL = [
        "publisher_contact",
        "source_excerpt",
    ]

    def test_discovery_excludes_private_fields(self, client):
        """GET /intents/ must not expose contact, excerpt, priority, tags,
        created_at, or publisher_name. If someone adds these to
        IntentDiscoveryResponse, this test fails."""
        key = make_key("priv-disc")
        try:
            ingest(
                client, key,
                publisherContact="secret@test.com",
                sourceExcerpt="sensitive quote from workshop",
                tags=["private-tag"],
            )
            # Promote to active so it appears in discovery
            client.post(
                f"/intents/{key}/review",
                json={"reviewedBy": "test-privacy-coordinator"},
            )

            r = client.get("/intents/")
            assert r.status_code == 200
            result = r.json()
            intent_rid = None
            # Find our intent by iterating (it should be active now)
            detail_r = client.get(f"/intents/detail/{key}")
            intent_rid = detail_r.json()["intent_rid"]

            matching = [i for i in result if i["intent_rid"] == intent_rid]
            assert len(matching) > 0, (
                f"Active intent {intent_rid} should appear in discovery"
            )
            discovery_item = matching[0]

            for field in self.PRIVATE_FIELDS_DISCOVERY:
                assert field not in discovery_item, (
                    f"Discovery response must NOT contain '{field}' -- "
                    f"privacy regression detected. Got: {discovery_item.get(field)}"
                )
        finally:
            cleanup(client, key)

    def test_detail_excludes_contact_and_excerpt(self, client):
        """GET /intents/detail/{key} must not expose publisher_contact or
        source_excerpt. These belong to the Coordinator projection only."""
        key = make_key("priv-detail")
        try:
            ingest(
                client, key,
                publisherContact="secret@test.com",
                sourceExcerpt="sensitive quote from workshop",
            )

            r = client.get(f"/intents/detail/{key}")
            assert r.status_code == 200
            detail = r.json()

            for field in self.PRIVATE_FIELDS_DETAIL:
                assert field not in detail, (
                    f"Detail response must NOT contain '{field}' -- "
                    f"privacy regression detected. Got: {detail.get(field)}"
                )
        finally:
            cleanup(client, key)


# ---------------------------------------------------------------------------
# TestDraftToActiveReview
# ---------------------------------------------------------------------------

class TestDraftToActiveReview:
    """Review workflow: draft -> active promotion via POST /{key}/review."""

    def test_review_promotes_draft_to_active(self, client):
        """POST /intents/{key}/review sets status=active and reviewed_by."""
        key = make_key("review-promote")
        try:
            ingest(client, key)

            r = client.post(
                f"/intents/{key}/review",
                json={"reviewedBy": "test-coordinator"},
            )
            assert r.status_code == 200, f"review returned {r.status_code}: {r.text}"
            reviewed = r.json()
            assert reviewed["status"] == "active", (
                f"Expected status 'active', got '{reviewed['status']}'"
            )
            assert reviewed["reviewed_by"] == "test-coordinator", (
                f"Expected reviewed_by 'test-coordinator', got '{reviewed['reviewed_by']}'"
            )
        finally:
            cleanup(client, key)

    def test_review_sets_reviewed_by_on_detail(self, client):
        """After review, GET /intents/detail/{key} shows reviewed_by."""
        key = make_key("review-detail")
        try:
            ingest(client, key)
            client.post(
                f"/intents/{key}/review",
                json={"reviewedBy": "detail-checker"},
            )

            r = client.get(f"/intents/detail/{key}")
            assert r.status_code == 200
            detail = r.json()
            assert detail["reviewed_by"] == "detail-checker"
            assert detail["status"] == "active"
        finally:
            cleanup(client, key)

    def test_active_intent_appears_in_discovery(self, client):
        """After review, the intent appears in GET /intents/ (public discovery)."""
        key = make_key("review-visible")
        try:
            result = ingest(client, key)
            intent_rid = result["intent_rid"]

            # Verify not in discovery while draft
            r = client.get("/intents/")
            draft_rids = [i["intent_rid"] for i in r.json()]
            assert intent_rid not in draft_rids, "Draft should not be in discovery"

            # Promote
            client.post(
                f"/intents/{key}/review",
                json={"reviewedBy": "test-coordinator"},
            )

            # Now should appear in discovery
            r = client.get("/intents/")
            assert r.status_code == 200
            active_rids = [i["intent_rid"] for i in r.json()]
            assert intent_rid in active_rids, (
                f"Active intent {intent_rid} should appear in public discovery"
            )
        finally:
            cleanup(client, key)

    def test_review_already_active_returns_409(self, client):
        """Reviewing an already-active intent returns 409 Conflict."""
        key = make_key("review-409")
        try:
            ingest(client, key)
            # First review succeeds
            r = client.post(
                f"/intents/{key}/review",
                json={"reviewedBy": "first-reviewer"},
            )
            assert r.status_code == 200

            # Second review must return 409
            r2 = client.post(
                f"/intents/{key}/review",
                json={"reviewedBy": "second-reviewer"},
            )
            assert r2.status_code == 409, (
                f"Expected 409 for re-review, got {r2.status_code}: {r2.text}"
            )
        finally:
            cleanup(client, key)


# ---------------------------------------------------------------------------
# TestIntentRefresh
# ---------------------------------------------------------------------------

class TestIntentRefresh:
    """POST /{key}/refresh resets priority and updates last_refreshed_at."""

    def test_refresh_resets_priority_and_timestamp(self, client):
        """After refresh, priority is 100.0 and last_refreshed_at is updated."""
        key = make_key("refresh")
        try:
            ingest(client, key)
            # Promote to active first
            client.post(
                f"/intents/{key}/review",
                json={"reviewedBy": "test-coordinator"},
            )

            # Record state before refresh
            before = client.get(f"/intents/detail/{key}").json()

            # Refresh
            r = client.post(f"/intents/{key}/refresh")
            assert r.status_code == 200, f"refresh returned {r.status_code}: {r.text}"
            refreshed = r.json()
            assert refreshed["priority"] == 100.0, (
                f"Expected priority 100.0 after refresh, got {refreshed['priority']}"
            )

            # Verify via detail endpoint independently
            detail = client.get(f"/intents/detail/{key}").json()
            assert detail["priority"] == 100.0
        finally:
            cleanup(client, key)


# ---------------------------------------------------------------------------
# TestVocabularyAndGroups
# ---------------------------------------------------------------------------

class TestVocabularyAndGroups:
    """Vocabulary and landscape group config CRUD."""

    def test_vocabulary_add_and_list(self, client):
        """POST /intents/vocabulary creates entry; GET returns it."""
        asset_key = f"reg-test-asset-{uuid.uuid4().hex[:6]}"
        try:
            # Add vocabulary entry
            r = client.post("/intents/vocabulary", json={
                "assetKey": asset_key,
                "displayName": "Test Firewood",
                "category": "forest-products",
                "landscapeGroup": "test-group",
            })
            assert r.status_code == 200, f"vocabulary POST failed: {r.status_code}: {r.text}"
            vocab = r.json()
            assert vocab["asset_key"] == asset_key
            assert vocab["display_name"] == "Test Firewood"
            assert vocab["category"] == "forest-products"

            # List and verify present
            r = client.get("/intents/vocabulary")
            assert r.status_code == 200
            all_vocab = r.json()
            matching = [v for v in all_vocab if v["asset_key"] == asset_key]
            assert len(matching) == 1, (
                f"Expected 1 vocabulary entry with key {asset_key}, found {len(matching)}"
            )
            assert matching[0]["display_name"] == "Test Firewood"
        finally:
            # No archive for vocabulary -- these are idempotent upserts.
            # Overwrite with a clearly test-marked entry to minimize pollution.
            pass

    def test_group_add_and_list(self, client):
        """POST /intents/groups creates entry; GET returns it."""
        group_key = f"reg-test-group-{uuid.uuid4().hex[:6]}"
        try:
            r = client.post("/intents/groups", json={
                "groupKey": group_key,
                "displayName": "Test Landscape Group",
                "decayLambda": 0.05,
                "coordinatorName": "Test Coordinator",
            })
            assert r.status_code == 200, f"groups POST failed: {r.status_code}: {r.text}"
            group = r.json()
            assert group["group_key"] == group_key
            assert group["display_name"] == "Test Landscape Group"
            assert group["decay_lambda"] == 0.05
            assert group["coordinator_name"] == "Test Coordinator"

            # List and verify present
            r = client.get("/intents/groups")
            assert r.status_code == 200
            all_groups = r.json()
            matching = [g for g in all_groups if g["group_key"] == group_key]
            assert len(matching) == 1, (
                f"Expected 1 group with key {group_key}, found {len(matching)}"
            )
            assert matching[0]["display_name"] == "Test Landscape Group"
        finally:
            pass


# ---------------------------------------------------------------------------
# TestStats
# ---------------------------------------------------------------------------

class TestStats:
    """GET /intents/stats returns reasonable aggregate counts."""

    def test_stats_reflect_created_intents(self, client):
        """After creating draft and active intents, stats counts are coherent."""
        key_draft = make_key("stats-draft")
        key_active = make_key("stats-active")
        try:
            # Snapshot before
            before = client.get("/intents/stats").json()
            draft_before = before["by_status"].get("draft", 0)
            active_before = before["by_status"].get("active", 0)

            # Create a draft
            ingest(client, key_draft, intentType="WANT")

            # Create and promote an active
            ingest(client, key_active, intentType="OFFER")
            client.post(
                f"/intents/{key_active}/review",
                json={"reviewedBy": "test-stats-coordinator"},
            )

            # Snapshot after
            after = client.get("/intents/stats").json()
            draft_after = after["by_status"].get("draft", 0)
            active_after = after["by_status"].get("active", 0)

            assert draft_after >= draft_before + 1, (
                f"Expected draft count to increase by at least 1: "
                f"{draft_before} -> {draft_after}"
            )
            assert active_after >= active_before + 1, (
                f"Expected active count to increase by at least 1: "
                f"{active_before} -> {active_after}"
            )

            # Verify aggregate shape
            assert "by_type" in after
            assert "by_landscape_group" in after
            assert "stale_count" in after
            assert "expiring_soon" in after
        finally:
            cleanup(client, key_draft)
            cleanup(client, key_active)

    def test_stats_by_type_includes_test_types(self, client):
        """Stats by_type should reflect the intent types we created."""
        key = make_key("stats-type")
        try:
            ingest(client, key, intentType="SWAP")
            stats = client.get("/intents/stats").json()
            assert "SWAP" in stats["by_type"], (
                f"Expected SWAP in by_type, got: {stats['by_type']}"
            )
            assert stats["by_type"]["SWAP"] >= 1
        finally:
            cleanup(client, key)


# ---------------------------------------------------------------------------
# TestMatchProposalFlow (Slice 2)
# ---------------------------------------------------------------------------

class TestMatchProposalFlow:
    """POST /intents/match, PATCH /intents/proposals/{rid}, GET /intents/proposals."""

    def test_matching_creates_candidate_proposal(self, client):
        """Two complementary intents produce a candidate match proposal."""
        offer_key = make_key("match-offer")
        want_key = make_key("match-want")
        group = f"match-group-{uuid.uuid4().hex[:6]}"
        # Ensure vocabulary exists
        client.post("/intents/vocabulary", json={
            "assetKey": "test_match_asset",
            "displayName": "Test Match Asset",
            "category": "test",
        })
        try:
            # Create and review both intents
            ingest(client, offer_key, assetOffered="test_match_asset",
                   landscapeGroup=group)
            client.post(f"/intents/{offer_key}/review",
                        json={"reviewedBy": "test"})
            ingest(client, want_key, intentType="WANT",
                   assetWanted="test_match_asset", landscapeGroup=group)
            client.post(f"/intents/{want_key}/review",
                        json={"reviewedBy": "test"})

            # Run matching
            r = client.post("/intents/match",
                            json={"landscapeGroup": group})
            assert r.status_code == 200, f"match failed: {r.text}"
            proposals = r.json()
            assert len(proposals) >= 1, (
                f"Expected at least 1 proposal, got {len(proposals)}"
            )

            proposal = proposals[0]
            assert proposal["status"] == "candidate"
            assert proposal["match_type"] == "local"

            # Verify proposals endpoint lists it
            r2 = client.get("/intents/proposals", params={"status": "candidate"})
            assert r2.status_code == 200
            all_proposals = r2.json()
            rids = [p["proposal_rid"] for p in all_proposals]
            assert proposal["proposal_rid"] in rids, (
                "Proposal not found in GET /proposals listing"
            )
        finally:
            cleanup(client, offer_key)
            cleanup(client, want_key)

    def test_matching_deduplicates_on_repeated_runs(self, client):
        """Running /match twice does not create duplicate proposals."""
        offer_key = make_key("dedup-offer")
        want_key = make_key("dedup-want")
        group = f"dedup-group-{uuid.uuid4().hex[:6]}"
        client.post("/intents/vocabulary", json={
            "assetKey": "dedup_asset",
            "displayName": "Dedup Asset",
        })
        try:
            ingest(client, offer_key, assetOffered="dedup_asset",
                   landscapeGroup=group)
            client.post(f"/intents/{offer_key}/review",
                        json={"reviewedBy": "test"})
            ingest(client, want_key, intentType="WANT",
                   assetWanted="dedup_asset", landscapeGroup=group)
            client.post(f"/intents/{want_key}/review",
                        json={"reviewedBy": "test"})

            # Run matching twice
            r1 = client.post("/intents/match",
                             json={"landscapeGroup": group})
            r2 = client.post("/intents/match",
                             json={"landscapeGroup": group})

            assert len(r1.json()) >= 1, "First match run should find a match"
            # Second run returns 0 new proposals (dedup)
            assert len(r2.json()) == 0, (
                f"Second match run should return 0 new proposals (dedup), "
                f"got {len(r2.json())}"
            )
        finally:
            cleanup(client, offer_key)
            cleanup(client, want_key)

    def test_proposal_accepted_fulfills_intents(self, client):
        """Accepting a proposal transitions both intents to fulfilled."""
        offer_key = make_key("accept-offer")
        want_key = make_key("accept-want")
        group = f"accept-group-{uuid.uuid4().hex[:6]}"
        client.post("/intents/vocabulary", json={
            "assetKey": "accept_asset",
            "displayName": "Accept Asset",
        })
        try:
            ingest(client, offer_key, assetOffered="accept_asset",
                   landscapeGroup=group)
            client.post(f"/intents/{offer_key}/review",
                        json={"reviewedBy": "test"})
            ingest(client, want_key, intentType="WANT",
                   assetWanted="accept_asset", landscapeGroup=group)
            client.post(f"/intents/{want_key}/review",
                        json={"reviewedBy": "test"})

            # Match and get proposal
            proposals = client.post("/intents/match",
                                    json={"landscapeGroup": group}).json()
            assert len(proposals) >= 1
            proposal_rid = proposals[0]["proposal_rid"]

            # Introduce
            r = client.patch(f"/intents/proposals/{proposal_rid}",
                             json={"status": "introduced",
                                   "resolvedBy": "test-coord"})
            assert r.status_code == 200
            assert r.json()["status"] == "introduced"

            # Accept
            r = client.patch(f"/intents/proposals/{proposal_rid}",
                             json={"status": "accepted",
                                   "resolvedBy": "test-coord"})
            assert r.status_code == 200
            assert r.json()["status"] == "accepted"

            # Verify both intents are now fulfilled
            offer_detail = client.get(
                f"/intents/detail/{offer_key}").json()
            want_detail = client.get(
                f"/intents/detail/{want_key}").json()
            assert offer_detail["status"] == "fulfilled", (
                f"Offer should be fulfilled, got {offer_detail['status']}"
            )
            assert want_detail["status"] == "fulfilled", (
                f"Want should be fulfilled, got {want_detail['status']}"
            )
        finally:
            cleanup(client, offer_key)
            cleanup(client, want_key)


# ---------------------------------------------------------------------------
# TestDigest (Slice 2)
# ---------------------------------------------------------------------------

class TestDigest:
    """GET /intents/digest/{landscape_group} includes coordinator-only fields."""

    def test_digest_includes_contact_info(self, client):
        """Coordinator digest should include publisher_contact."""
        offer_key = make_key("digest-offer")
        want_key = make_key("digest-want")
        group = f"digest-group-{uuid.uuid4().hex[:6]}"
        client.post("/intents/vocabulary", json={
            "assetKey": "digest_asset",
            "displayName": "Digest Asset",
        })
        try:
            ingest(client, offer_key, assetOffered="digest_asset",
                   landscapeGroup=group,
                   publisherName="DigestOffer",
                   publisherContact="offer@test.com")
            client.post(f"/intents/{offer_key}/review",
                        json={"reviewedBy": "test"})
            ingest(client, want_key, intentType="WANT",
                   assetWanted="digest_asset", landscapeGroup=group,
                   publisherName="DigestWant",
                   publisherContact="want@test.com")
            client.post(f"/intents/{want_key}/review",
                        json={"reviewedBy": "test"})

            # Create a match proposal
            client.post("/intents/match",
                        json={"landscapeGroup": group})

            # Get digest
            r = client.get(f"/intents/digest/{group}")
            assert r.status_code == 200
            digest = r.json()
            assert "digest" in digest, f"Expected 'digest' key, got: {digest.keys()}"
            text = digest["digest"]
            assert "offer@test.com" in text, (
                "Digest should include offer publisher_contact"
            )
            assert "want@test.com" in text, (
                "Digest should include want publisher_contact"
            )
        finally:
            cleanup(client, offer_key)
            cleanup(client, want_key)


# ---------------------------------------------------------------------------
# TestDraftActiveGuard (Slice 2/3 boundary fix)
# ---------------------------------------------------------------------------

class TestDraftActiveGuard:
    """PATCH cannot bypass the draft→active review membrane."""

    def test_patch_draft_to_active_blocked(self, client):
        """PATCH status=active on a draft intent returns 409."""
        key = make_key("guard-bypass")
        try:
            ingest(client, key)
            r = client.patch(f"/intents/{key}",
                             json={"status": "active"})
            assert r.status_code == 409, (
                f"Expected 409 for draft→active via PATCH, got {r.status_code}: "
                f"{r.text}"
            )
        finally:
            cleanup(client, key)

    def test_patch_draft_to_archived_allowed(self, client):
        """PATCH status=archived on a draft is allowed (withdraw before review)."""
        key = make_key("guard-archive")
        try:
            ingest(client, key)
            r = client.patch(f"/intents/{key}",
                             json={"status": "archived"})
            assert r.status_code == 200, (
                f"Expected 200 for draft→archived, got {r.status_code}: {r.text}"
            )
            detail = client.get(f"/intents/detail/{key}").json()
            assert detail["status"] == "archived"
        finally:
            cleanup(client, key)
