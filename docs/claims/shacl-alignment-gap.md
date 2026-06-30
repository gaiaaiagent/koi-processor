# Claims SHACL validation — alignment gap (provisional, 2026-06-05)

**Status:** infrastructure landed; **validation NOT yet wired into `create_claim`** because the koi-processor claim representation is not aligned with the canonical LinkML `rfs:Claim` shape. This doc records the gap with an executable proof and the two ways to close it.

## What landed on this branch (`darren/claims-shacl-validation`)

- `schema/shacl/claim.shacl.ttl` — SHACL shape generated from regen-data-standards **PR #53** `Claim.yaml` (`linkml` `ShaclGenerator`, 419 triples). *Provisional:* PR #53 is unmerged (`upstream/pr-53-head`); regenerate when it merges.
- `api/shacl_validation.py` — validation helper. Runs `pyshacl` inside `asyncio.to_thread()` (never block the single-worker event loop), **fail-closed** if the shape file is missing, gated by `VALIDATE_CLAIMS_SHACL` (default **false**).
- `requirements.txt` — adds `pyshacl`, `rdflib`.
- `tests/test_shacl_claim_shape.py` — codifies the proof below.

## The gap

koi-processor already emits an `rfs:Claim` JSON-LD (used for content hashing — `claims_router.py` `_canonical_json` ~line 392 and the proof-pack ~line 2492):

```json
{ "@context": "https://framework.regen.network/schema/", "@type": "rfs:Claim",
  "claimant_uri": "...", "claim_type": "ecological", "statement": "...", "about_uri": "..." }
```

The PR #53 LinkML `rfs:Claim` shape is **`sh:closed`** and requires structured slots:
`schema:name`, `rfs:hasClaimType`, `rfs:verificationStatus`, `rfs:hasClaimant` (Entity), `rfs:hasSubject` (Entity), `rfs:hasPrimaryImpact` (Impact). Nested `Entity`/`Impact` have their own required slots + controlled-vocabulary enums.

Same `@type` IRI, **completely different property vocabulary**. So today's claims fail validation 100% (closed-shape violations on every flat key + every required slot missing).

## Executable proof (throwaway venv, pyshacl 0.31 / rdflib 7.6)

```
shape: 419 triples loaded from claim.shacl.ttl
(A) LinkML-slot-aligned claim: conforms=True
(B) today's koi flat-key claim: conforms=False
   Message: Node <urn:claim:koi> is closed. It cannot have value: Literal("Soil carbon increased 2 tC/ha/yr")
   Message: Node <urn:claim:koi> is closed. It cannot have value: Literal("ecological")
   Message: Node <urn:claim:koi> is closed. It cannot have value: Literal("orn:koi-net.entity:demo")
```

(A) passes only when shaped to the LinkML slots with valid enum values (Entity `rfs:type` ∈ {Individual, Organization, Community}; Impact `rfs:hasImpactType` from the ImpactType vocab; `rfs:verificationStatus` ∈ {SelfReported, PeerReviewed, Verified, LedgerAnchored, Withdrawn}). The shape is correct and strict — the toolchain works.

## Two ways to close the gap (pick upstream, with FWG / Marie)

1. **Author a JSON-LD `@context`** mapping koi's flat keys → LinkML slot URIs, and synthesize the required structured nodes (claimant/subject as `Entity`, a primary `Impact`, a `verificationStatus`) at validation time. Lets the existing storage model stand; the mapping is the work. **Problem:** `hasSubject` and `hasPrimaryImpact` are *required* but have **no source field** in `ClaimCreateRequest` — they can't be mapped, only invented. So a pure context mapping is insufficient without model changes.
2. **Extend the claim model** (`ClaimCreateRequest` + storage) to carry the LinkML semantics (typed claimant/subject entities, primary impact, verification status) and emit slot-keyed JSON-LD. The faithful fix; larger; should follow PR #53 merging and coordinate with the data-standards alignment work.

Either way, **SHACL can't be meaningfully enabled until the claim representation aligns.** Until then this stays off-by-default and unwired.

## When ready to wire (exact spot)

In `api/routers/claims_router.py::create_claim`, after the `about_uri` validation block (~line 652, before RID generation ~line 655):

```python
from api.shacl_validation import shacl_enabled, validate_claim_ttl
...
if shacl_enabled():
    conforms, report = await validate_claim_ttl(claim_to_aligned_ttl(body))  # claim_to_aligned_ttl: the gap above
    if not conforms:
        raise HTTPException(status_code=422, detail=f"Claim failed SHACL validation:\n{report}")
```

Do **not** enable `VALIDATE_CLAIMS_SHACL=true` on the live single-worker service until (a) alignment is done and (b) latency is profiled — synchronous validation on the hot path is the documented outage risk (`asyncio.to_thread` mitigates event-loop blocking but pool pressure still applies under load).
