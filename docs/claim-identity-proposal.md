# Claim identity, canonicalization and anchoring — implementation proposal

**Status: Proposed, 2026-09-08.** No production behavior changes. WP0 reuse/approval and the relevant WP1 RDF choices remain prerequisites for adopting a new service identity. This companion to [standards ADR #56](https://github.com/regen-network/regen-data-standards/pull/56) relocates the service material formerly mixed into that ADR; it does not ratify it.

## Source boundaries

| Inspected source | Established behavior | Limit |
|---|---|---|
| [KOI anchor code at c08c0a7e](https://github.com/gaiaaiagent/koi-processor/blob/c08c0a7e3fdde4b6ce5186e2f7d8d11069ff4b95/api/ledger_anchor.py) | Claim payloads use sorted compact Python JSON and BLAKE2b-256 Raw anchors. Attestation records use a separate JSON-LD/URDNA2015 Graph path. | This is a pinned source snapshot, not verification of a running deployment. Deterministic Python JSON is not RFC 8785 JCS. |
| [JC main crate at 8cbf0e5d](https://github.com/ybird-labs/claims/tree/8cbf0e5d1c46fb6a6d829037a76709815e7caff0/crates/claims/src/domain) | Models Claim IRI + content + assertor + assertion time; fingerprint type records a supplied digest and names an RDFC-1.0/SHA-256 suite. | A suite label and trusted canonical-text constructor are not an implemented external admission/canonicalization pipeline. |
| [JC design and spike at 217fafdd](https://github.com/ybird-labs/claims/tree/217fafdd9685fb427ed74f440f9fa65a475f4885) | Design §§3, 5, 7, 19 include declared schema references in identity; the isolated spike calls Sophia RDFC-1.0 and hashes suite + sorted schema IRIs + separator + canonical N-Quads with SHA-256. ClaimIRI derives from that fingerprint. | `crates/` is unchanged from main. The spike is disposable and outside root CI; design §20 leaves the exact profile and encoding open. This is a prototype, not the adopted Regen protocol. |

Asserted attribution/time **in the hashed RDF** contribute to identity. Witnessed submission metadata outside the value does not. “Same content regardless of assertor” only holds when the full value, including asserted attribution, is unchanged. Correcting an identity-bearing timestamp produces new content and a new identity; a correction relation can retain history.

## Proposed implementation decisions

### Canonicalization and profile (former D2–D3)

Evaluate RDFC-1.0 over an agreed RDF dataset profile for semantic comparison and for the ledger Graph path. The ledger’s registry names RDFC-1.0; it does not ratify the team’s profile or force internal claim identity to equal an anchor digest. See [ledger constraints](ledger-anchoring-agent-context.md) for the exact Graph/Raw and algorithm-validation limits.

Pin schema/context revisions. Use the standards namespaces and generated context rather than inventing a parallel namespace or treating deterministic JSON-LD expansion as an unanswered choice. Define type-aware normalization: date-only fields stay dates; timestamp equivalence, decimal precision, IRI mapping and list/set representation require agreed vectors. Preserve material distinctions in the claim-type content.

### Complete fingerprint suite (former D5; historical question 9)

Decide and version **all** of: content profile, declared schema set, canonicalization, preimage framing, digest and IRI encoding. The newer spike’s actual framing is:

```text
suite label + newline
sorted duplicate-free schema IRIs, each prefixed by "schema " and suffixed by newline
"---" + newline
canonical N-Quads bytes
```

This describes the inspected spike, not a newly selected wire contract. Hashing only the same N-Quads cannot match this suite if the schema declarations/framing differ. SHA-256 claim identity and a BLAKE2b-256 ledger Graph digest can serve different documented purposes; do not relabel one as the other or assume their equality. Also do not label a digest over extra envelope bytes as a canonical-RDF graph digest without specifying what graph those bytes represent.

### Admission and immutability (former D4, D6)

The external service boundary must parse and validate input, canonicalize and recompute identity before persistence; trusted domain constructors are internal APIs. Specify which validations gate admission versus create later judgments: JC’s newer design admits structurally valid claims at L0 and evaluates declared-schema conformance at L1. Adoption requires reconciling that distinction with the intended Regen service.

For untrusted RDF, propose an isolated canonicalization worker with measured resource caps and a hard timeout, rejected input leaving no admitted claim. Select limits from representative fixtures before rollout. Require accepted content and stored identity to remain consistent under mutation attempts, through deep immutability or equivalent enforcement. These are acceptance requirements for a future implementation, not assertions that this docs PR implements them.

### Anchor migration and scheme metadata (former D8–D9)

Preserve existing Raw identifiers and their exact payload bytes until the consumer inventory and migration decision are recorded. A new Graph anchor has a different IRI; it can coexist with, reference, or replace use of a Raw anchor under an explicit migration. A Graph attestation may also describe a Raw-anchored claim. Neither path is automatically chosen here.

Recheck actual deployed code, persisted hashes/IRIs, anchor receipts and external consumers before choosing cutover or transition. The [older census narrative](https://github.com/DarrenZal/regen-data-standards/blob/52f61e911ccfb818d39083f66264a281edea92fe/docs/adr/0001-claim-substance-canonicalization.md) is historical context, not a current proof that migration is free. A local anchored status or an IRI conversion is not a chain receipt.

Record the suite/profile/schema revision alongside future stored fingerprints, and distinguish claim identity, exact-material hash and ledger anchor. Select concrete persistence fields in the implementing change after the suite is agreed; this proposal introduces none.

## Required vectors before adoption

These are test specifications, not passing claims or golden digests for an unselected suite.

| Scenario | Expected result |
|---|---|
| JSON ordering/whitespace and compact/expanded identifiers under the same pinned context | Same canonical RDF; same identity within one selected suite. Exact submitted-byte hashes may differ. |
| Same RDF, changed declared schema reference | Different spike identity; required production behavior depends on the recorded schema-in-preimage decision. |
| Schema declarations reordered or duplicated | Same spike schema-set identity after its set normalization. Admission must explicitly accept/normalize or reject duplicates. |
| Attribution/assertion time changed inside content | Changed canonical value and fingerprint; audit-only metadata change leaves claim identity unchanged. |
| Material quantity/unit/credit-class/methodology change | Distinct value; no collision caused by omitting those selected content fields. |
| Date-only / equivalent timestamp / decimal and set/list cases | Expected normalization comes from the accepted profile; preserve ordered lists and meaningful precision. |
| Malformed/empty content, missing required declarations, poison RDF, mutation after acceptance | Reject at the designated boundary without persisting an invalid admitted claim; enforce resource and identity-consistency requirements. |
| Same claim submitted again | Agreed replay behavior; under the newer design, one claim with another submission audit record. |
| Raw versus Graph digest/IRI | Decode and validate the type; never infer it from `.rdf` alone or pass Raw as a Graph hash. |
| Two selected implementations | Compare canonical bytes, complete preimage bytes, digest and derived IRI under identical suite/profile revisions. |

The [OutputRecord fixture work #62](https://github.com/regen-network/regen-data-standards/issues/62) supplies conversion evidence. It does not by itself prove identity parity, replay semantics, consent enforcement or ledger inclusion. Cross-engine claims require the corresponding executed harness and fixed expected results.
