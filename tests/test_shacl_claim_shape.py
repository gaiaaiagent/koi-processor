"""SHACL claim-shape proof tests.

Codifies the alignment finding in docs/claims/shacl-alignment-gap.md:
  - the generated shape is satisfiable: a claim shaped to the LinkML slot
    vocabulary CONFORMS;
  - today's koi-processor flat-key claim JSON-LD does NOT conform (sh:closed
    violations) — i.e. SHACL cannot be enabled until the claim representation
    is aligned to the LinkML slots.

Requires pyshacl + rdflib (see requirements.txt); skipped if absent.
Run: pytest tests/test_shacl_claim_shape.py -v
"""
from pathlib import Path

import pytest

pytest.importorskip("pyshacl")
pytest.importorskip("rdflib")

import rdflib  # noqa: E402
from pyshacl import validate  # noqa: E402

SHAPE_PATH = Path(__file__).resolve().parent.parent / "schema" / "shacl" / "claim.shacl.ttl"

# A claim shaped to the LinkML slot vocabulary, with valid controlled-vocab values.
ALIGNED_CLAIM_TTL = """
@prefix rfs: <https://framework.regen.network/schema/> .
@prefix rft: <https://framework.regen.network/taxonomy/> .
@prefix schema1: <http://schema.org/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
<urn:claim:good> a rfs:Claim ;
    schema1:name "Soil carbon increased 2 tC/ha/yr" ;
    schema1:description "demo claim" ;
    schema1:url "urn:doc:1"^^xsd:anyURI ;
    rfs:hasClaimType rft:Ecological ;
    rfs:verificationStatus rft:SelfReported ;
    rfs:hasClaimant [ a rfs:Entity ; schema1:name "Demo Org" ; rfs:walletAddress "regen1org" ; rfs:type rfs:Organization ] ;
    rfs:hasSubject  [ a rfs:Entity ; schema1:name "Demo Project" ; rfs:walletAddress "regen1proj" ; rfs:type rfs:Organization ] ;
    rfs:hasPrimaryImpact [ a rfs:Impact ; schema1:name "SOC gain" ; rfs:hasImpactType rft:IncreasedCarbonSequestrationStorage ] .
"""

# Today's koi-processor rfs:Claim JSON-LD, as RDF (flat ad-hoc keys).
KOI_FLAT_CLAIM_TTL = """
@prefix rfs: <https://framework.regen.network/schema/> .
<urn:claim:koi> a rfs:Claim ;
    rfs:claimant_uri "orn:koi-net.entity:demo" ;
    rfs:claim_type "ecological" ;
    rfs:statement "Soil carbon increased 2 tC/ha/yr" ;
    rfs:about_uri "orn:regen.methodology:soc" .
"""


def _validate(ttl: str):
    shape = rdflib.Graph().parse(str(SHAPE_PATH), format="turtle")
    data = rdflib.Graph().parse(data=ttl, format="turtle")
    conforms, _results_graph, results_text = validate(
        data, shacl_graph=shape, inference="rdfs", advanced=True
    )
    return conforms, results_text


def test_shape_file_exists():
    assert SHAPE_PATH.exists(), f"missing generated shape: {SHAPE_PATH}"


def test_aligned_claim_conforms():
    conforms, report = _validate(ALIGNED_CLAIM_TTL)
    assert conforms, f"LinkML-aligned claim should pass SHACL but failed:\n{report}"


def test_koi_flatkey_claim_does_not_conform():
    """Documents the alignment gap: today's claim representation fails (sh:closed)."""
    conforms, report = _validate(KOI_FLAT_CLAIM_TTL)
    assert not conforms, "koi flat-key claim unexpectedly conformed — has the model been aligned?"
    assert "closed" in report.lower(), f"expected sh:closed violations, got:\n{report}"
