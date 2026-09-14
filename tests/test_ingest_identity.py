"""Payload-endpoint identity for document ingestion — issues #62, #61, #53.

ISOLATION. Every DB test here runs on ONE asyncpg connection wrapped in a
transaction that is rolled back at teardown, and every HTTP test drives the router
IN PROCESS via ASGITransport. Nothing in this file talks to localhost:8351.

That is not incidental style. tests/conftest.py documents the failure it prevents:
a test that posts over HTTP to the live backend writes through a separate uvicorn
process holding its own pool against the LIVE database, and no environment
variable can redirect that — the ingests landed in personal_koi while the teardown
DELETE went to personal_koi_test, and 225 orphaned rows accumulated over 30 hours.
An in-process app shares this connection, so the rollback is the guarantee rather
than a cleanup that might not have run.

Requires the scratch database to carry migrations 124 and 125:
    psql personal_koi_test -f migrations/124_entity_normalization_compat.sql
    psql personal_koi_test -f migrations/125_extraction_provenance_and_quality.sql
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api import ingest_identity as ident
from api.extraction_quality import assess_extraction
from api.resolution_primitives import (
    normalize_alias,
    normalize_entity_text,
)

DB_URL = os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi_test")
EMBED_DIM = 3072
SVC_TOKEN = "test-identity-service-token"
SVC_AUTH = {"Authorization": f"Bearer {SVC_TOKEN}"}

# The four collapses issue #62 reports, with the Jaro-Winkler scores it recorded.
REPORTED_COLLAPSES = [
    ("Anthropic Claude 3 Opus", "Anthropic Claude 3 Sonnet", 0.9411),
    ("Buehler 2024 fitted exponential degree model",
     "Buehler 2024 fitted power-law degree model", 0.9168),
    ("Buehler 2024 adversarial-X-LoRA generated graph",
     "Buehler 2024 adversarial-X-LoRA augmented graph", 0.9608),
    ("Buehler 2024 global-graph modularity score",
     "Buehler 2024 global-graph community structure", 0.9393),
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ═══════════════════════════════════════════════════════════════════════════
# 0. The defect is live. This is the positive control for the whole change.
# ═══════════════════════════════════════════════════════════════════════════

class TestTheDefectIsReal:
    """If these stop failing-by-collapsing, the premise of #62 has changed.

    Without this, every test below could pass against a system that never had the
    bug, and nothing would say so.
    """

    def test_the_four_reported_collapses_still_reproduce(self):
        """Each pair still clears every guard on the live fuzzy path.

        They survive because `passes_distinctive_token_check` rejects DISJOINT
        distinctive-token sets, and these pairs are not disjoint: they share most
        tokens and differ in exactly the one that distinguishes them
        (opus/sonnet, power-law/exponential). That is why raising a threshold is
        not the fix and pinning is.
        """
        from api.entity_schema import get_schema_for_type
        from api.resolution_primitives import (
            jaro_winkler_similarity,
            passes_distinctive_token_check,
            passes_token_overlap_strict,
        )

        threshold = get_schema_for_type("Concept").similarity_threshold
        for requested, sibling, reported_jw in REPORTED_COLLAPSES:
            a, b = normalize_entity_text(requested), normalize_entity_text(sibling)
            jw = jaro_winkler_similarity(a, b)
            assert round(jw, 4) == reported_jw, (
                f"{requested!r} vs {sibling!r}: JW is now {jw:.4f}, issue #62 recorded "
                f"{reported_jw}. The scoring changed — re-derive the issue's table.")
            assert jw >= threshold, "no longer reaches the fuzzy tier"
            assert passes_token_overlap_strict(a, b, "Concept"), "strict guard now rejects it"
            assert passes_distinctive_token_check(a, b), "distinctive-token guard now rejects it"

    def test_normalizer_drift_still_hides_a_legacy_row(self):
        """#61: the current normalizer cannot find a label an older one stored.

        `GPT-4` is the repaired instance. The assertion is about the FUNCTION, so
        it keeps holding after that row is merged — the class, not the instance.
        """
        assert normalize_entity_text("GPT-4") == "gpt 4"
        assert normalize_entity_text("GPT-4") != "gpt-4", (
            "the legacy stored form and the current form have converged; #61's "
            "premise no longer holds")


# ═══════════════════════════════════════════════════════════════════════════
# 1. Endpoint collection — pure, no DB
# ═══════════════════════════════════════════════════════════════════════════

def _payload(facts, entities=None, type_map=None):
    return {"entities": entities or [], "facts": facts, "type_map": type_map or {}}


class TestCollectEndpoints:

    def test_collects_fact_endpoints_including_those_absent_from_entities(self):
        """A fact endpoint absent from entities[] must still be collected.

        Those are exactly the endpoints that get minted with a DEFAULTED type
        (the reason EpisodeCreateResponse.entities_typed_by_default exists), so
        collecting from facts alone would leave the weakest endpoints unpinned.
        """
        eps = ident.collect_endpoints(
            _payload(
                facts=[{"subject": "Alpha", "object": "Ghost", "predicate": "P",
                        "fact_text": "t"}],
                entities=[{"name": "Alpha", "type": "Concept"}],
            ),
            normalize=normalize_entity_text,
        )
        by_name = {e.name: e for e in eps}
        assert set(by_name) == {"Alpha", "Ghost"}
        assert by_name["Alpha"].type_defaulted is False
        assert by_name["Ghost"].type_defaulted is True
        assert by_name["Ghost"].roles == frozenset({"object"})

    def test_reversed_fact_order_yields_an_identical_endpoint_set(self):
        """#62 acceptance: reversing fact order produces the same endpoint map."""
        facts = [
            {"subject": f"S{i}", "object": f"O{i}", "predicate": "P", "fact_text": f"f{i}"}
            for i in range(25)
        ]
        fwd = ident.collect_endpoints(_payload(facts), normalize=normalize_entity_text)
        rev = ident.collect_endpoints(_payload(list(reversed(facts))),
                                      normalize=normalize_entity_text)
        assert [e.key for e in fwd] == [e.key for e in rev]

    def test_rejects_one_label_claimed_under_two_types(self):
        """Two entities[] entries whose labels normalize the same but disagree on type.

        Guessing which type is meant would be inventing an identity decision, and
        the URI hashes the type, so the guess would be permanent.
        """
        with pytest.raises(ident.IdentityError) as exc:
            ident.collect_endpoints(
                _payload(
                    facts=[{"subject": "Janus", "predicate": "P", "object": None,
                            "object_literal": "x", "fact_text": "t"}],
                    entities=[{"name": "Janus", "type": "Person"},
                              {"name": "janus", "type": "Organization"}],
                ),
                normalize=normalize_entity_text,
            )
        assert "multiple entity types" in str(exc.value)

    def test_normalization_collapses_spelling_variants_into_one_endpoint(self):
        eps = ident.collect_endpoints(
            _payload(facts=[
                {"subject": "Vault-Sync", "predicate": "P", "object": "vault_sync",
                 "fact_text": "t"},
            ]),
            normalize=normalize_entity_text,
        )
        assert len(eps) == 1, f"expected one endpoint, got {[e.key for e in eps]}"
        assert eps[0].normalized == "vault sync"


# ═══════════════════════════════════════════════════════════════════════════
# 2. Preflight against a real registry — scratch DB, rolled back
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
async def conn():
    c = await asyncpg.connect(DB_URL)
    db = await c.fetchval("SELECT current_database()")
    assert db != "personal_koi", (
        f"refusing to run: connected to {db!r}. These tests write entity rows; the "
        f"live graph is not an acceptable target.")
    tx = c.transaction()
    await tx.start()
    yield c
    await tx.rollback()
    await c.close()


async def _seed_entity(conn, *, uri, text, etype, normalized=None, aliases=None,
                       merged_into=None):
    """Insert a registry row, optionally with a LEGACY normalized_text.

    `normalized` defaults to the CURRENT normalization; passing a different value
    is how #61's drift is reproduced.
    """
    await conn.execute(
        """INSERT INTO entity_registry
             (fuseki_uri, entity_text, entity_type, normalized_text, aliases,
              merged_into, source)
           VALUES ($1,$2,$3,$4,$5,$6,'pytest')""",
        uri, text, etype,
        normalized if normalized is not None else normalize_entity_text(text),
        aliases, merged_into,
    )


def _tag():
    return uuid.uuid4().hex[:10]


async def _preflight(conn, names_types, **kw):
    eps = [
        ident.Endpoint(name=n, entity_type=t, normalized=normalize_entity_text(n))
        for n, t in names_types
    ]
    report = await ident.preflight_endpoints(
        conn, eps, normalize=normalize_entity_text,
        normalize_alias_fn=normalize_alias, **kw)
    return eps, report


@pytest.mark.anyio
class TestPreflight:

    async def test_finds_a_row_whose_stored_normalization_is_legacy(self, conn):
        """#61 AC1. The row is reachable ONLY by recomputing the stored label.

        The paired control below shows the stock exact lookup missing the same
        row, which is what makes this assertion mean something.
        """
        t = _tag()
        uri = f"orn:personal-koi.entity:concept-gpt4-{t}"
        await _seed_entity(conn, uri=uri, text=f"GPT-4x{t}", etype="Concept",
                           normalized=f"gpt-4x{t}")          # LEGACY spelling

        _eps, report = await _preflight(conn, [(f"GPT-4x{t}", "Concept")])
        f = report.findings[0]
        assert f.state == ident.STATE_LIVE_EXACT, (
            f"expected the legacy row to be found, got {f.state} — this is #61")
        assert f.matched_via == "current_norm"
        assert f.live_uris == [uri]

    async def test_control_the_stock_exact_lookup_misses_that_row(self, conn):
        """The defect, stated as a query. Without this the test above is unmoored."""
        t = _tag()
        await _seed_entity(conn, uri=f"orn:personal-koi.entity:concept-gpt4-{t}",
                           text=f"GPT-4x{t}", etype="Concept", normalized=f"gpt-4x{t}")
        hit = await conn.fetchval(
            "SELECT fuseki_uri FROM entity_registry "
            "WHERE normalized_text=$1 AND entity_type='Concept' AND merged_into IS NULL",
            normalize_entity_text(f"GPT-4x{t}"))
        assert hit is None, (
            "the stock exact lookup now finds the legacy row; #61 may be fixed "
            "elsewhere and this suite's premise needs re-deriving")

    async def test_missing_endpoint_is_missing_not_ambiguous(self, conn):
        _eps, report = await _preflight(conn, [(f"Nothing Like This {_tag()}", "Concept")])
        assert report.findings[0].state == ident.STATE_MISSING

    async def test_two_live_rows_with_one_current_label_block(self, conn):
        """#61 AC5 / #62 requirement 3: a graph-global duplicate set blocks."""
        t = _tag()
        name = f"Duplicated Thing {t}"
        await _seed_entity(conn, uri=f"orn:personal-koi.entity:concept-a-{t}",
                           text=name, etype="Concept")
        await _seed_entity(conn, uri=f"orn:personal-koi.entity:concept-b-{t}",
                           text=name.replace(" ", "-"), etype="Concept",
                           normalized=name.replace(" ", "-").lower())  # legacy form
        _eps, report = await _preflight(conn, [(name, "Concept")])
        f = report.findings[0]
        assert f.state == ident.STATE_GLOBAL_DUPLICATE, f.as_evidence()
        with pytest.raises(ident.IdentityError):
            ident.require_no_blockers(report)

    async def test_a_tombstone_with_no_live_row_blocks(self, conn):
        """The label matches ONLY a merged-away row: identity would depend on
        following a merge chain that can change under the payload."""
        t = _tag()
        name = f"Merged Away {t}"
        survivor = f"orn:personal-koi.entity:concept-live-{t}"
        await _seed_entity(conn, uri=survivor, text=f"Survivor {t}", etype="Concept")
        await _seed_entity(conn, uri=f"orn:personal-koi.entity:concept-dead-{t}",
                           text=name, etype="Concept", merged_into=survivor)
        _eps, report = await _preflight(conn, [(name, "Concept")])
        assert report.findings[0].state == ident.STATE_TOMBSTONE_RISK

    async def test_a_tombstone_ALONGSIDE_a_live_row_does_not_block(self, conn):
        """REGRESSION, found by running the real payload against the real graph.

        The single blocker over Buehler's 115 endpoints was `GPT-4` — issue #61's
        own repaired entity, whose legacy row is tombstoned *because the repair
        worked*. Blocking there makes a correctly-merged graph permanently
        un-ingestable, i.e. punishes the repair.

        The tombstone is unreachable on the pinned path anyway: the binding names
        the live URI, and `_bind_pinned_uri` refuses a merged-away one outright.
        """
        t = _tag()
        name = f"Repaired Label {t}"
        live = f"orn:personal-koi.entity:concept-live-{t}"
        await _seed_entity(conn, uri=live, text=name, etype="Concept")
        await _seed_entity(conn, uri=f"orn:personal-koi.entity:concept-legacy-{t}",
                           text=name, etype="Concept",
                           normalized=name.replace(" ", "-").lower(),
                           merged_into=live)
        _eps, report = await _preflight(conn, [(name, "Concept")])
        f = report.findings[0]
        assert f.state == ident.STATE_LIVE_EXACT, f.as_evidence()
        assert f.live_uris == [live]
        assert f.tombstoned_uris, "the tombstone must still be recorded as evidence"
        ident.require_no_blockers(report)  # must not raise

    async def test_alias_only_blocks_but_an_audited_decision_clears_it(self, conn):
        """#62 requirement 6 — the ONLY sanctioned override, and it is recorded."""
        t = _tag()
        uri = f"orn:personal-koi.entity:concept-canon-{t}"
        alias = f"Alias Spelling {t}"
        await _seed_entity(conn, uri=uri, text=f"Canonical Spelling {t}",
                           etype="Concept", aliases=[normalize_alias(alias)])

        _eps, blocked = await _preflight(conn, [(alias, "Concept")])
        assert blocked.findings[0].state == ident.STATE_ALIAS_ONLY
        with pytest.raises(ident.IdentityError):
            ident.require_no_blockers(blocked)

        _eps, cleared = await _preflight(conn, [(alias, "Concept")],
                                         alias_decisions={alias: uri})
        assert cleared.findings[0].state == ident.STATE_LIVE_EXACT
        assert cleared.findings[0].matched_via == "audited_alias_decision"
        ident.require_no_blockers(cleared)

    async def test_a_wrong_alias_decision_does_not_clear_the_block(self, conn):
        """A decision naming a URI that is not actually a candidate must not win."""
        t = _tag()
        uri = f"orn:personal-koi.entity:concept-canon-{t}"
        alias = f"Alias Spelling {t}"
        await _seed_entity(conn, uri=uri, text=f"Canonical Spelling {t}",
                           etype="Concept", aliases=[normalize_alias(alias)])
        _eps, report = await _preflight(
            conn, [(alias, "Concept")],
            alias_decisions={alias: f"orn:personal-koi.entity:concept-unrelated-{t}"})
        assert report.findings[0].state == ident.STATE_ALIAS_ONLY

    async def test_same_label_different_type_BLOCKS(self, conn):
        """SUPERSEDES an earlier test that asserted this was merely advisory.

        That assertion was wrong, and only real data showed it: an advisory
        cross-type match leaves the endpoint MISSING, and preregistration then
        mints the cross-type twin. See TestCrossTypeConflictBlocks for the
        DWeb Berlin case that forced the change.
        """
        t = _tag()
        name = f"Polysemous {t}"
        await _seed_entity(conn, uri=f"orn:personal-koi.entity:org-{t}",
                           text=name, etype="Organization")
        _eps, report = await _preflight(conn, [(name, "Concept")])
        f = report.findings[0]
        assert f.state == ident.STATE_CROSS_TYPE_CONFLICT
        assert f.cross_type_uris, "the cross-type row must be surfaced as evidence"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Freeze — the bijection assertion
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
class TestFreeze:

    async def test_distinct_endpoints_get_distinct_uris(self, conn):
        t = _tag()
        a, b = f"Claude 3 Opus {t}", f"Claude 3 Sonnet {t}"
        ua = f"orn:personal-koi.entity:concept-opus-{t}"
        ub = f"orn:personal-koi.entity:concept-sonnet-{t}"
        await _seed_entity(conn, uri=ua, text=a, etype="Concept")
        await _seed_entity(conn, uri=ub, text=b, etype="Concept")
        eps, _ = await _preflight(conn, [(a, "Concept"), (b, "Concept")])
        frozen = await ident.freeze_endpoint_map(conn, eps, normalize=normalize_entity_text)
        assert frozen.by_name == {a: ua, b: ub}
        assert frozen.distinct_uris == 2
        assert frozen.type_for(a) == "Concept"

    async def test_two_endpoints_reaching_one_row_via_drift_raise(self, conn):
        """The bijection assertion catches a collapse the DUAL READ itself enables.

        This is the case worth guarding, and it is not hypothetical: the #61
        compatibility read matches a row by EITHER its stored normalized_text or
        the current normalization of its canonical label. A drifted row therefore
        answers to two different keys at once. Here endpoint A matches it via the
        stored value and endpoint B via the recomputed one — two distinct typed
        payload endpoints, one identity.

        Making the exact lookup more forgiving is what creates this, so the
        bijection check has to be the thing that catches it. Without the check,
        widening the read would trade a false SPLIT (#61) for a false MERGE (#62)
        and call it a fix.
        """
        t = _tag()
        uri = f"orn:personal-koi.entity:concept-drifted-{t}"
        # entity_text normalizes to "current spelling <t>"; the STORED value is a
        # different string entirely — the shape 1,268 live rows are in today.
        await _seed_entity(conn, uri=uri, text=f"Current Spelling {t}", etype="Concept",
                           normalized=f"legacy spelling {t}")

        eps = [
            ident.Endpoint(name=f"Current Spelling {t}", entity_type="Concept",
                           normalized=f"current spelling {t}"),
            ident.Endpoint(name=f"Legacy Spelling {t}", entity_type="Concept",
                           normalized=f"legacy spelling {t}"),
        ]
        with pytest.raises(ident.IdentityError) as exc:
            await ident.freeze_endpoint_map(conn, eps, normalize=normalize_entity_text)
        assert "same identity" in str(exc.value)
        assert exc.value.blockers[0]["state"] == "endpoint_collapse"
        assert exc.value.blockers[0]["uri"] == uri

    async def test_an_audited_decision_may_point_two_labels_at_one_uri(self, conn):
        """#62 requirement 6's escape hatch, and the ONLY one.

        An operator who has audited the case may map variants onto one identity.
        The contrast with the test above is the whole point: the same outcome is a
        blocker when nobody decided it and permitted when somebody did.
        """
        t = _tag()
        shared = f"orn:personal-koi.entity:concept-shared-{t}"
        await _seed_entity(conn, uri=shared, text=f"Shared {t}", etype="Concept")
        eps = [
            ident.Endpoint(name=f"First {t}", entity_type="Concept",
                           normalized=normalize_entity_text(f"First {t}")),
            ident.Endpoint(name=f"Second {t}", entity_type="Concept",
                           normalized=normalize_entity_text(f"Second {t}")),
        ]
        frozen = await ident.freeze_endpoint_map(
            conn, eps, normalize=normalize_entity_text,
            alias_decisions={f"First {t}": shared, f"Second {t}": shared})
        assert frozen.by_name == {f"First {t}": shared, f"Second {t}": shared}
        assert all(e["bound_via"] == "audited_alias_decision" for e in frozen.evidence)

    async def test_a_registration_that_returned_a_different_uri_raises(self, conn):
        t = _tag()
        name = f"Drifted {t}"
        real = f"orn:personal-koi.entity:concept-real-{t}"
        await _seed_entity(conn, uri=real, text=name, etype="Concept")
        eps, _ = await _preflight(conn, [(name, "Concept")])
        with pytest.raises(ident.IdentityError) as exc:
            await ident.freeze_endpoint_map(
                conn, eps, normalize=normalize_entity_text,
                expected_uris={name: f"orn:personal-koi.entity:concept-other-{t}"})
        assert "registration_disagreement" in str(exc.value.blockers[0]["state"])


# ═══════════════════════════════════════════════════════════════════════════
# 4. The endpoint honours pins — in-process ASGI, no live server
# ═══════════════════════════════════════════════════════════════════════════

class _Pool:
    """One connection, quacking like asyncpg.Pool, inside the rolled-back txn."""

    def __init__(self, conn):
        self._conn = conn

    class _CM:
        def __init__(self, c):
            self._c = c

        async def __aenter__(self):
            return self._c

        async def __aexit__(self, *exc):
            return False

    def acquire(self):
        return self._CM(self._conn)


async def _fake_embed(text, **kwargs):
    seed = hash(text)
    return [float(((seed + i) % 97) + 1) / 97.0 for i in range(EMBED_DIM)]


@pytest.fixture
async def api(conn, monkeypatch):
    from api.routers.knowledge_router import create_router

    monkeypatch.setenv("KOI_CLAIMS_SERVICE_TOKEN", SVC_TOKEN)
    app = FastAPI()
    app.include_router(
        create_router(_Pool(conn), generate_document_embedding=_fake_embed),
        prefix="/knowledge")
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test", headers=SVC_AUTH) as client:
        yield client, conn


def _episode(facts, *, tag, create_entities=True):
    return {
        "name": f"Episode {tag}",
        "source_description": "pytest",
        "source_document": f"doc-{tag}",
        "group_id": f"test-{tag}",
        "facts": facts,
        "create_entities": create_entities,
    }


@pytest.mark.anyio
class TestPinnedWrites:

    async def test_unpinned_siblings_collapse_control(self, api):
        """CONTROL: without pinning, the two reported siblings become one entity.

        This is the behaviour #62 describes, exercised through the real endpoint.
        If it ever stops collapsing, the pinning tests below are proving nothing
        and this file should say so loudly rather than stay green.
        """
        client, conn = api
        t = _tag()
        a, b = f"Anthropic Claude 3 Opus {t}", f"Anthropic Claude 3 Sonnet {t}"
        r = await client.post("/knowledge/episodes", json=_episode([
            {"subject": a, "subject_type": "Concept", "predicate": "EVALUATED_ON",
             "object_literal": "bench", "fact_text": f"{a} was evaluated."},
            {"subject": b, "subject_type": "Concept", "predicate": "EVALUATED_ON",
             "object_literal": "bench", "fact_text": f"{b} was evaluated."},
        ], tag=t))
        assert r.status_code == 201, r.text
        uris = [row["subject_uri"] for row in await conn.fetch(
            "SELECT subject_uri FROM knowledge_facts WHERE episode_id=$1 ORDER BY created_at",
            uuid.UUID(r.json()["episode_id"]))]
        assert len(set(uris)) == 1, (
            "the siblings no longer collapse unpinned — #62's mechanism has changed "
            "and the pinning tests below need re-grounding")
        assert r.json()["endpoints_pinned"] == 0

    @pytest.mark.parametrize("pair_index", range(len(REPORTED_COLLAPSES)))
    async def test_pinned_siblings_stay_distinct(self, api, pair_index):
        """#62 acceptance: each reported pair keeps two identities when pinned."""
        client, conn = api
        t = _tag()
        left, right, _jw = REPORTED_COLLAPSES[pair_index]
        a, b = f"{left} {t}", f"{right} {t}"
        ua = f"orn:personal-koi.entity:concept-a-{t}"
        ub = f"orn:personal-koi.entity:concept-b-{t}"
        await _seed_entity(conn, uri=ua, text=a, etype="Concept")
        await _seed_entity(conn, uri=ub, text=b, etype="Concept")

        r = await client.post("/knowledge/episodes", json=_episode([
            {"subject": a, "subject_type": "Concept", "subject_uri": ua,
             "predicate": "EVALUATED_ON", "object_literal": "bench",
             "fact_text": f"{a} was evaluated."},
            {"subject": b, "subject_type": "Concept", "subject_uri": ub,
             "predicate": "EVALUATED_ON", "object_literal": "bench",
             "fact_text": f"{b} was evaluated."},
        ], tag=t, create_entities=False))
        assert r.status_code == 201, r.text
        assert r.json()["endpoints_pinned"] == 2
        uris = {row["subject_uri"] for row in await conn.fetch(
            "SELECT subject_uri FROM knowledge_facts WHERE episode_id=$1",
            uuid.UUID(r.json()["episode_id"]))}
        assert uris == {ua, ub}

    async def test_reversed_fact_order_produces_the_same_graph(self, api):
        """#62 acceptance: reversing fact order yields the same fact graph."""
        client, conn = api
        results = []
        for reverse in (False, True):
            t = _tag()
            uris = {}
            facts = []
            for i in range(6):
                name = f"Endpoint {i} {t}"
                uri = f"orn:personal-koi.entity:concept-{i}-{t}"
                await _seed_entity(conn, uri=uri, text=name, etype="Concept")
                uris[name] = uri
                facts.append({
                    "subject": name, "subject_type": "Concept", "subject_uri": uri,
                    "predicate": "HAS_INDEX", "object_literal": str(i),
                    "fact_text": f"{name} has index {i}.",
                })
            r = await client.post("/knowledge/episodes", json=_episode(
                list(reversed(facts)) if reverse else facts, tag=t,
                create_entities=False))
            assert r.status_code == 201, r.text
            rows = await conn.fetch(
                "SELECT subject_uri, predicate, object_literal FROM knowledge_facts "
                "WHERE episode_id=$1", uuid.UUID(r.json()["episode_id"]))
            # Compare the SHAPE, with the per-run tag stripped, so the two runs are
            # comparable without sharing rows.
            results.append(sorted(
                (row["subject_uri"].replace(t, "TAG"), row["predicate"],
                 row["object_literal"]) for row in rows))
        assert results[0] == results[1]

    async def test_more_than_one_batch_keeps_every_endpoint_distinct(self, api):
        """#62 acceptance: 'tests cover imports larger than one fact batch'.

        DOC_EPISODE_BATCH_SIZE defaults to 100, so the Buehler payload's 213 facts
        are written as three sequential same-episode requests. This exercises >100
        endpoints in one episode and asserts the cardinality survives.
        """
        client, conn = api
        t = _tag()
        n = 120
        facts = []
        for i in range(n):
            name = f"Batch Endpoint {i} {t}"
            uri = f"orn:personal-koi.entity:concept-b{i}-{t}"
            await _seed_entity(conn, uri=uri, text=name, etype="Concept")
            facts.append({
                "subject": name, "subject_type": "Concept", "subject_uri": uri,
                "predicate": "HAS_INDEX", "object_literal": str(i),
                "fact_text": f"{name} has index {i}.",
            })
        r = await client.post("/knowledge/episodes",
                              json=_episode(facts, tag=t, create_entities=False))
        assert r.status_code == 201, r.text
        assert r.json()["endpoints_pinned"] == n
        distinct = await conn.fetchval(
            "SELECT count(DISTINCT subject_uri) FROM knowledge_facts WHERE episode_id=$1",
            uuid.UUID(r.json()["episode_id"]))
        assert distinct == n

    async def test_rerun_creates_no_additional_entities_or_facts(self, api):
        """#62 acceptance: re-running the same import is a no-op."""
        client, conn = api
        t = _tag()
        name = f"Idempotent Endpoint {t}"
        uri = f"orn:personal-koi.entity:concept-idem-{t}"
        await _seed_entity(conn, uri=uri, text=name, etype="Concept")
        body = _episode([{
            "subject": name, "subject_type": "Concept", "subject_uri": uri,
            "predicate": "IS_STABLE", "object_literal": "yes",
            "fact_text": f"{name} is stable.",
        }], tag=t, create_entities=False)

        first = await client.post("/knowledge/episodes", json=body)
        assert first.status_code == 201, first.text
        ents_before = await conn.fetchval("SELECT count(*) FROM entity_registry")
        second = await client.post("/knowledge/episodes", json=body)
        assert second.status_code == 201, second.text

        assert second.json()["episode_id"] == first.json()["episode_id"], \
            "episode reuse by (source_document, group_id) did not hold"
        assert await conn.fetchval("SELECT count(*) FROM entity_registry") == ents_before
        assert await conn.fetchval(
            "SELECT count(*) FROM knowledge_facts WHERE episode_id=$1 AND valid_to IS NULL",
            uuid.UUID(first.json()["episode_id"])) == 1

    async def test_an_unknown_pinned_uri_is_422_and_writes_nothing(self, api):
        """A pin that cannot be honoured must NEVER fall back to name resolution."""
        client, conn = api
        t = _tag()
        before = await conn.fetchval("SELECT count(*) FROM knowledge_facts")
        r = await client.post("/knowledge/episodes", json=_episode([{
            "subject": f"Ghost {t}", "subject_type": "Concept",
            "subject_uri": f"orn:personal-koi.entity:concept-does-not-exist-{t}",
            "predicate": "P", "object_literal": "x", "fact_text": "t",
        }], tag=t, create_entities=False))
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["error"] == "pinned_entity_not_found"
        assert await conn.fetchval("SELECT count(*) FROM knowledge_facts") == before, \
            "the failed request left facts behind — the transaction did not roll back"

    async def test_a_tombstoned_pin_is_422_and_is_not_silently_redirected(self, api):
        """The pin is an assertion of identity, not a hint to be followed."""
        client, conn = api
        t = _tag()
        survivor = f"orn:personal-koi.entity:concept-live-{t}"
        dead = f"orn:personal-koi.entity:concept-dead-{t}"
        await _seed_entity(conn, uri=survivor, text=f"Survivor {t}", etype="Concept")
        await _seed_entity(conn, uri=dead, text=f"Dead {t}", etype="Concept",
                           merged_into=survivor)
        r = await client.post("/knowledge/episodes", json=_episode([{
            "subject": f"Dead {t}", "subject_type": "Concept", "subject_uri": dead,
            "predicate": "P", "object_literal": "x", "fact_text": "t",
        }], tag=t, create_entities=False))
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["error"] == "pinned_entity_merged_away"
        assert r.json()["detail"]["merged_into"] == survivor

    async def test_a_type_mismatched_pin_is_422(self, api):
        client, conn = api
        t = _tag()
        uri = f"orn:personal-koi.entity:org-{t}"
        await _seed_entity(conn, uri=uri, text=f"Thing {t}", etype="Organization")
        r = await client.post("/knowledge/episodes", json=_episode([{
            "subject": f"Thing {t}", "subject_type": "Concept", "subject_uri": uri,
            "predicate": "P", "object_literal": "x", "fact_text": "t",
        }], tag=t, create_entities=False))
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["error"] == "pinned_entity_type_mismatch"

    async def test_object_endpoints_are_pinned_too(self, api):
        client, conn = api
        t = _tag()
        us = f"orn:personal-koi.entity:concept-s-{t}"
        uo = f"orn:personal-koi.entity:concept-o-{t}"
        await _seed_entity(conn, uri=us, text=f"Subj {t}", etype="Concept")
        await _seed_entity(conn, uri=uo, text=f"Obj {t}", etype="Concept")
        r = await client.post("/knowledge/episodes", json=_episode([{
            "subject": f"Subj {t}", "subject_type": "Concept", "subject_uri": us,
            "object": f"Obj {t}", "object_type": "Concept", "object_uri": uo,
            "predicate": "RELATES_TO", "fact_text": "t",
        }], tag=t, create_entities=False))
        assert r.status_code == 201, r.text
        assert r.json()["endpoints_pinned"] == 2
        row = await conn.fetchrow(
            "SELECT subject_uri, object_uri FROM knowledge_facts WHERE episode_id=$1",
            uuid.UUID(r.json()["episode_id"]))
        assert (row["subject_uri"], row["object_uri"]) == (us, uo)


# ═══════════════════════════════════════════════════════════════════════════
# 5. Post-write verification
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
class TestVerifyPersistedGraph:

    async def test_an_endpoint_outside_the_frozen_map_is_caught(self, api):
        client, conn = api
        t = _tag()
        pinned = f"orn:personal-koi.entity:concept-pinned-{t}"
        stray = f"orn:personal-koi.entity:concept-stray-{t}"
        await _seed_entity(conn, uri=pinned, text=f"Pinned {t}", etype="Concept")
        await _seed_entity(conn, uri=stray, text=f"Stray {t}", etype="Concept")
        r = await client.post("/knowledge/episodes", json=_episode([
            {"subject": f"Pinned {t}", "subject_type": "Concept", "subject_uri": pinned,
             "predicate": "P", "object_literal": "a", "fact_text": "one"},
            {"subject": f"Stray {t}", "subject_type": "Concept", "subject_uri": stray,
             "predicate": "P", "object_literal": "b", "fact_text": "two"},
        ], tag=t, create_entities=False))
        assert r.status_code == 201

        # A map that knows about only ONE of the two persisted endpoints.
        frozen = ident.FrozenMap(by_name={f"Pinned {t}": pinned}, by_key={},
                                 by_name_type={f"Pinned {t}": "Concept"}, evidence=[])
        report = await ident.verify_persisted_graph(
            conn, uuid.UUID(r.json()["episode_id"]), frozen)
        assert not report.ok
        assert report.offending[0]["persisted_uri"] == stray
        with pytest.raises(ident.IdentityError):
            ident.require_binding_verified(report)

    async def test_a_fully_pinned_episode_verifies_clean(self, api):
        client, conn = api
        t = _tag()
        uri = f"orn:personal-koi.entity:concept-ok-{t}"
        name = f"Ok {t}"
        await _seed_entity(conn, uri=uri, text=name, etype="Concept")
        r = await client.post("/knowledge/episodes", json=_episode([{
            "subject": name, "subject_type": "Concept", "subject_uri": uri,
            "predicate": "P", "object_literal": "a", "fact_text": "one",
        }], tag=t, create_entities=False))
        assert r.status_code == 201
        frozen = ident.FrozenMap(by_name={name: uri}, by_key={},
                                 by_name_type={name: "Concept"}, evidence=[])
        report = await ident.verify_persisted_graph(
            conn, uuid.UUID(r.json()["episode_id"]), frozen)
        assert report.ok
        assert report.checked_facts == 1
        ident.require_binding_verified(report)  # must not raise


# ═══════════════════════════════════════════════════════════════════════════
# 6. Semantic quality is a SEPARATE verdict — issue #64
# ═══════════════════════════════════════════════════════════════════════════

class _W:
    def __init__(self, index, chunk_indices):
        self.index = index
        self.chunk_indices = chunk_indices
        self.chunk_index_base = min(chunk_indices)


def _doc(n_windows=5):
    chunks = {i: f"chunk {i} alpha{i} beta{i} gamma{i} content words here" for i in range(n_windows)}
    windows = [_W(i, [i]) for i in range(n_windows)]
    return chunks, windows


class TestSemanticQuality:

    def test_a_thin_extraction_passes_structurally_but_fails_semantically(self):
        """#64 acceptance, exactly as worded.

        Structurally this run is complete: it produced facts, they have real
        endpoints, there are no duplicates. Every floor in the completion gate's
        catalog is a `>= 1`, and this clears them. It is still a bad extraction —
        it read one window of five.
        """
        chunks, windows = _doc(5)
        thin = {"facts": [{
            "subject": "alpha0", "object": "beta0", "predicate": "P",
            "fact_text": "alpha0 P beta0", "chunk_range": [0, 0],
        }]}

        # Structural floors the real gate applies, evaluated on this run.
        assert len(thin["facts"]) >= 1          # facts_available
        assert all(f.get("chunk_range") for f in thin["facts"])

        report = assess_extraction(merged=thin, chunks_by_index=chunks,
                                   windows=windows, tier="standard")
        assert report.status == "fail"
        assert "window_coverage" in report.as_dict()["failed"]

    def test_padding_with_invented_facts_makes_the_verdict_worse(self):
        """#64 requirement 5: thresholds must not reward hallucinated volume."""
        chunks, windows = _doc(3)
        real = [{"subject": f"alpha{i}", "object": f"beta{i}", "predicate": f"P{i}",
                 "fact_text": "t", "chunk_range": [i, i]} for i in range(3)]
        honest = assess_extraction(merged={"facts": real}, chunks_by_index=chunks,
                                   windows=windows, tier="standard")
        padded_facts = real + [
            {"subject": f"invented{i}", "object": f"fabricated{i}", "predicate": "P0",
             "fact_text": "t", "chunk_range": [0, 0]} for i in range(20)]
        padded = assess_extraction(merged={"facts": padded_facts}, chunks_by_index=chunks,
                                   windows=windows, tier="standard")

        def val(rep, name):
            return next(d.value for d in rep.dimensions if d.name == name)

        assert val(padded, "citation_support") < val(honest, "citation_support")
        # Concentration RISES when one predicate is repeated for volume (higher is
        # worse for this dimension), and chunk_coverage does not move at all,
        # because the invented facts all cite a chunk that was already covered.
        assert val(padded, "predicate_concentration") > val(honest, "predicate_concentration")
        assert val(padded, "chunk_coverage") <= val(honest, "chunk_coverage")
        assert len(padded_facts) > len(real), "the padded run has MORE facts"

    def test_a_well_covered_extraction_passes(self):
        """#64 acceptance: a well-covered fixture passes with span evidence."""
        chunks, windows = _doc(4)
        facts = [{"subject": f"alpha{i}", "object": f"beta{i}", "predicate": f"PRED_{i}",
                  "fact_text": f"alpha{i} relates to beta{i}", "chunk_range": [i, i]}
                 for i in range(4)]
        report = assess_extraction(
            merged={"facts": facts}, chunks_by_index=chunks, windows=windows,
            tier="standard",
            identity_evidence={"mode": "strict",
                               "verification": {"binding_verified": True,
                                                "checked_facts": 4, "offending_facts": 0}})
        assert report.status == "pass", report.as_dict()

    def test_an_unmeasurable_dimension_is_review_never_pass(self):
        """'could not check' must not read the same as 'checked and fine'."""
        chunks, windows = _doc(2)
        report = assess_extraction(
            merged={"facts": [{"subject": "alpha0", "object": "beta0", "predicate": "P",
                               "fact_text": "t", "chunk_range": [0, 0]},
                              {"subject": "alpha1", "object": "beta1", "predicate": "Q",
                               "fact_text": "t", "chunk_range": [1, 1]}]},
            chunks_by_index=chunks, windows=windows, tier="standard")
        integrity = next(d for d in report.dimensions if d.name == "endpoint_integrity")
        assert integrity.value is None
        assert integrity.status == "review"
        assert report.status == "review"

    def test_a_fabricated_citation_range_is_caught(self):
        chunks, windows = _doc(3)
        facts = [{"subject": f"alpha{i}", "object": f"beta{i}", "predicate": f"P{i}",
                  "fact_text": "t", "chunk_range": [i, i]} for i in range(3)]
        facts.append({"subject": "ghost", "object": "phantom", "predicate": "P9",
                      "fact_text": "t", "chunk_range": [900, 900]})
        report = assess_extraction(merged={"facts": facts}, chunks_by_index=chunks,
                                   windows=windows, tier="standard")
        in_range = next(d for d in report.dimensions if d.name == "citation_in_range")
        assert in_range.value == 0.75
        assert in_range.status == "fail"


# ═══════════════════════════════════════════════════════════════════════════
# 7. Producer provenance — issue #64
# ═══════════════════════════════════════════════════════════════════════════

def _edd():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "edd_under_test", "scripts/extract_deep_documents.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestProducerProvenance:

    def test_an_openai_run_never_reports_an_anthropic_model(self):
        """#64 acceptance, in the exact words of the issue.

        `_call_openai` ignores its `model` argument and sends OPENAI_MODEL, while
        the caller passed ANTHROPIC_MODEL. The receipt must name what did the work.
        """
        edd = _edd()
        receipt = edd._producer_for("openai", "claude-sonnet-5")
        assert receipt["model"] == edd.OPENAI_MODEL
        assert receipt["model"] != "claude-sonnet-5"
        assert receipt["provider"] == "openai_compatible"

    def test_each_transport_names_its_own_provider(self):
        edd = _edd()
        assert edd._producer_for("api", "m")["provider"] == "anthropic"
        assert edd._producer_for("claude_p", "m")["provider"] == "claude_subscription"

    def test_the_receipt_records_the_endpoint_host_and_never_the_key(self):
        edd = _edd()
        r = edd._producer_for("openai", "m")
        blob = repr(r)
        assert "endpoint_host" in r
        assert "Bearer" not in blob and "api_key" not in blob.lower()

    def test_a_provider_4xx_becomes_a_dead_letterable_extraction_error(self):
        """A non-retryable provider status must NOT abort the whole document.

        `raise_for_status()` raises httpx.HTTPStatusError, which is not an
        ExtractionError — so before this was handled it escaped the transport
        fallback, the repair loop AND the per-window dead-letter, and one 401 or
        413 cost every window already extracted. The reason must be
        `extract_http_error` specifically, because that is what makes it eligible
        for transport fallback.
        """
        import asyncio

        import httpx

        edd = _edd()

        class _Resp:
            status_code = 413
            text = "payload too large"

            def raise_for_status(self):
                raise httpx.HTTPStatusError(
                    "413", request=httpx.Request("POST", "http://x"), response=self)

            def json(self):  # pragma: no cover - never reached
                return {}

        class _Client:
            async def post(self, *a, **kw):
                return _Resp()

        with pytest.raises(edd.ExtractionError) as exc:
            asyncio.run(edd._call_openai("prompt", _Client()))
        assert exc.value.reason == "extract_http_error"
        assert exc.value.reason in edd.TRANSPORT_FALLBACK_REASONS

    def test_a_thorough_extraction_is_not_penalised_for_being_thorough(self):
        """REGRESSION. The first version of this layer scored the real fixtures
        BACKWARDS: the thin 25-fact Buehler run PASSED and the curated 213-fact
        re-run went to REVIEW.

        The cause was a "distinct predicates / facts" dimension. Any
        distinct-over-total ratio FALLS as an extraction legitimately adds facts,
        so it penalised exactly the behaviour it was meant to reward — an inert
        threshold pointing the wrong way, which is the failure #64 requirement 5
        names from the other direction.

        This asserts the shape that broke: same document, same coverage, one
        extraction simply more thorough than the other. The thorough one must not
        score worse on any dimension.
        """
        chunks, windows = _doc(6)
        sparse = [{"subject": f"alpha{i}", "object": f"beta{i}", "predicate": "P",
                   "fact_text": "t", "chunk_range": [i, i]} for i in range(6)]
        # Same 6 chunks, but each one yields several distinct, supported relations.
        thorough = []
        for i in range(6):
            for pred in ("P", "Q", "R", "S"):
                thorough.append({"subject": f"alpha{i}", "object": f"beta{i}",
                                 "predicate": pred, "fact_text": "t",
                                 "chunk_range": [i, i]})

        rs = assess_extraction(merged={"facts": sparse}, chunks_by_index=chunks,
                               windows=windows, tier="standard")
        rt = assess_extraction(merged={"facts": thorough}, chunks_by_index=chunks,
                               windows=windows, tier="standard")

        rank = {"pass": 3, "review": 2, "fail": 1, "not_evaluated": 0}
        by_name = {d.name: d for d in rs.dimensions}
        for d in rt.dimensions:
            assert rank[d.status] >= rank[by_name[d.name].status], (
                f"the thorough extraction scored WORSE on {d.name}: "
                f"{by_name[d.name].status} -> {d.status}")
        assert len(thorough) > len(sparse)


class TestDeclaredButUnusedEntities:

    def test_a_declared_entity_no_fact_references_is_not_an_endpoint(self):
        """REGRESSION, found by running the gate on three freshly-ingested posts.

        The extractor declares many more entities than its facts reference — 26
        declared vs 14 used on one real document. Treating the union as endpoints
        would pre-register a dozen rows per document that no fact points at:
        registry inflation introduced by the very thing meant to make identity
        safer, and something the unpinned path never did.

        A declared-but-unreferenced entity is a candidate the extractor mentioned,
        not an identity the payload asserts.
        """
        eps = ident.collect_endpoints(
            _payload(
                facts=[{"subject": "Used", "object": None, "object_literal": "x",
                        "predicate": "P", "fact_text": "t"}],
                entities=[{"name": "Used", "type": "Concept"},
                          {"name": "Declared But Unused", "type": "Person"},
                          {"name": "Also Unused", "type": "Organization"}],
            ),
            normalize=normalize_entity_text,
        )
        assert {e.name for e in eps} == {"Used"}

    def test_entities_still_supply_the_type_for_a_fact_endpoint(self):
        """entities[] keeps its one job: saying what type an endpoint is."""
        eps = ident.collect_endpoints(
            _payload(
                facts=[{"subject": "Ada Lovelace", "object": None,
                        "object_literal": "x", "predicate": "P", "fact_text": "t"}],
                entities=[{"name": "Ada Lovelace", "type": "Person"}],
            ),
            normalize=normalize_entity_text,
        )
        assert len(eps) == 1
        assert eps[0].entity_type == "Person"
        assert eps[0].type_defaulted is False


@pytest.mark.anyio
class TestCrossTypeConflictBlocks:
    """REGRESSION for the DWeb Berlin shape (operator decision, 2026-09-14).

    The extractor declared `DWeb Berlin` an Organization while the graph held it
    as a Project. The first version classified that MISSING with a cross-type
    ADVISORY, so `preregister_missing` would have minted a SECOND `DWeb Berlin` —
    an Organization beside the Project. The contract exists to stop one payload
    endpoint becoming two identities; creating the cross-type twin is that same
    failure wearing a different hat.
    """

    async def test_a_cross_type_label_blocks_instead_of_minting_a_twin(self, conn):
        t = _tag()
        name = f"Cross Typed {t}"
        await _seed_entity(conn, uri=f"orn:personal-koi.entity:project-{t}",
                           text=name, etype="Project")
        _eps, report = await _preflight(conn, [(name, "Organization")])
        f = report.findings[0]
        assert f.state == ident.STATE_CROSS_TYPE_CONFLICT, f.as_evidence()
        assert f.cross_type_uris
        with pytest.raises(ident.IdentityError):
            ident.require_no_blockers(report)

    async def test_an_audited_type_decision_clears_it(self, conn):
        t = _tag()
        name = f"Cross Typed {t}"
        await _seed_entity(conn, uri=f"orn:personal-koi.entity:project-{t}",
                           text=name, etype="Project")
        _eps, report = await _preflight(conn, [(name, "Organization")],
                                        type_decisions={name: "Organization"})
        assert report.findings[0].state == ident.STATE_MISSING
        ident.require_no_blockers(report)

    async def test_preregistration_refuses_the_twin_even_if_the_gate_was_skipped(self, conn):
        """preregister_missing is callable directly, and minting is irreversible
        once the type is hashed into a URI. It refuses rather than trusting the
        caller ran the gate."""
        t = _tag()
        name = f"Cross Typed {t}"
        await _seed_entity(conn, uri=f"orn:personal-koi.entity:project-{t}",
                           text=name, etype="Project")
        _eps, report = await _preflight(conn, [(name, "Organization")],
                                        type_decisions={name: "Organization"})
        assert report.findings[0].state == ident.STATE_MISSING   # gate cleared...
        report.findings[0].state = ident.STATE_MISSING           # ...and still blocked below

        class _NeverCalled:
            async def post(self, *a, **k):
                raise AssertionError("preregistration must refuse BEFORE any HTTP call")

        with pytest.raises(ident.IdentityError) as exc:
            await ident.preregister_missing(_NeverCalled(), report)
        assert exc.value.blockers[0]["state"] == ident.STATE_CROSS_TYPE_CONFLICT
