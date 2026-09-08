# Agent context — ledger anchoring and attestation

Revised 2026-09-08. These notes describe pinned source behavior and requirements for a proposed implementation. They do not establish the code running on a server or ratify an anchoring migration. The related [claim identity proposal](claim-identity-proposal.md) contains the pending fingerprint/profile decisions.

## 1. Authoritative sources

Ledger references below are pinned to [regen-ledger 451c3a3f](https://github.com/regen-network/regen-ledger/tree/451c3a3f4353fc0d5a82383f2616a284baffa96f). KOI references are pinned to [koi-processor c08c0a7e](https://github.com/gaiaaiagent/koi-processor/tree/c08c0a7e3fdde4b6ce5186e2f7d8d11069ff4b95). Refresh a deployment claim from deployment evidence rather than from a branch name.

| Source | What it establishes |
|---|---|
| [v2 tx.proto](https://github.com/regen-network/regen-ledger/blob/451c3a3f4353fc0d5a82383f2616a284baffa96f/proto/regen/data/v2/tx.proto) | MsgAnchor and MsgAttest message fields. |
| [v2 types.proto](https://github.com/regen-network/regen-ledger/blob/451c3a3f4353fc0d5a82383f2616a284baffa96f/proto/regen/data/v2/types.proto) | Raw/Graph formats and algorithm registries. |
| [x/data/types.go](https://github.com/regen-network/regen-ledger/blob/451c3a3f4353fc0d5a82383f2616a284baffa96f/x/data/types.go) | Runtime structural validation. |
| [x/data/iri.go](https://github.com/regen-network/regen-ledger/blob/451c3a3f4353fc0d5a82383f2616a284baffa96f/x/data/iri.go) | Raw/Graph prefix, IRI construction and parsing. |
| [api/ledger_anchor.py](https://github.com/gaiaaiagent/koi-processor/blob/c08c0a7e3fdde4b6ce5186e2f7d8d11069ff4b95/api/ledger_anchor.py) | KOI hashing, IRI conversion and transaction paths at the inspected revision. |
| [claims_router.py](https://github.com/gaiaaiagent/koi-processor/blob/c08c0a7e3fdde4b6ce5186e2f7d8d11069ff4b95/api/routers/claims_router.py) | Claim/attestation service call sites. |

RDF shapes and the standards review remain in [regen-data-standards #61](https://github.com/regen-network/regen-data-standards/pull/61) and [ADR #56](https://github.com/regen-network/regen-data-standards/pull/56). These are review surfaces, not claims that the proposed files already exist on main.

## 2. Ledger constraints

### 2.1 MsgAttest takes Graph hashes

`MsgAttest.content_hashes` is `repeated ContentHash.Graph` in v2, as in v1. A Raw hash cannot be supplied as that Graph field.

Distinguish **attesting a claim’s graph** from **attesting a separate RDF statement about a Raw-anchored claim**. The latter can reference the Raw claim IRI. Creating a new Graph representation is also possible and creates a different IRI. Therefore “a Raw claim can never be attested” is too broad; the exact Raw hash cannot itself serve as the Graph attestation target.

### 2.2 Raw hashes require preserving exact bytes

The Raw format does not prescribe a canonical encoding. To verify a Raw hash, retain the exact bytes hashed. A producer may choose a deterministic serialization, but another serialization of equivalent information need not produce the same hash. Loss of the retained payload prevents checking that content against the commitment.

### 2.3 RDFC-1.0 and URDNA2015 share a registry value

The [v1 registry](https://github.com/regen-network/regen-ledger/blob/451c3a3f4353fc0d5a82383f2616a284baffa96f/proto/regen/data/v1/types.proto) names `URDNA2015 = 1`; v2 names `RDFC_1_0 = 1` and documents clarifications around escaping. The same numeric label does not establish which implementation/spec was executed or certify byte parity. Test candidate implementations on common vectors, including escaping and blank nodes.

### 2.4 Digest registry and hash validation are different checks

The inspected registry names BLAKE2b-256 with value 1. `validateHash` requires a digest length of 20–64 bytes and a nonzero digest-algorithm value. That structural check does not prove the digest uses the named algorithm or enforce BLAKE2b’s 32-byte output length for identifier 1. Clients must validate the chosen suite and length themselves.

### 2.5 The inspected KOI path uses the v2 format

`derive_ledger_iri` constructs Raw with `file_extension: json`, a v2 field; v1 uses a media-type enum. The ledger’s generated `x/data` types also reference v2. Use v2 field definitions for this code path. This establishes format targeting, not a live deployment observation.

### 2.6 Algorithm names are not proved by on-chain structural validation

In v2 the algorithm fields are uint32. `ContentHash_Graph.Validate` rejects canonicalization algorithm zero; `validateHash` rejects digest algorithm zero. Their paths do not validate membership of the named registries or recompute a supplied digest from content. Clients must enforce the intended supported values and verify their implementation. A nonzero but incorrect label must not be treated as conformance.

### 2.7 Raw and Graph have different IRI type prefixes

`IriPrefixRaw = 0` and `IriPrefixGraph = 1` are encoded in the base58check payload. Graph also encodes canonicalization, Merkle-tree and digest values. Converting a Raw representation to Graph creates a different IRI, even if the digest bytes happen to match. Preserve existing identifiers/relationships through any explicitly approved migration.

### 2.8 Decode the IRI; its suffix is not sufficient

Graph IRIs must end `.rdf`. Raw extensions may contain 2–6 lowercase letters or digits, which **also permits `rdf`**. Consequently `.rdf` does not prove Graph type. Use `ParseIRI`, inspect the decoded variant, and apply the variant’s validation rules; parsing by itself is not a content or canonicalization check. A `.json` suffix rules out a valid Graph IRI but does not prove the rest of the IRI is valid.

### 2.9 The inspected code has separate claim and attestation paths

- `compute_content_hash` hashes a claim projection with BLAKE2b-256 over sorted compact Python JSON; `derive_ledger_iri` constructs Raw with extension `json`.
- `build_attestation_jsonld` / `generate_graph_iri` construct the separate graph representation used by `broadcast_attest`.

The first is not RFC 8785 JCS. The existence of the second path does not mean the original Raw claim has a Graph identity, and inspecting these functions does not establish that either transaction was included on chain. Confirm inclusion using transaction/anchor receipts.

### 2.10 The inspected graph canonicalizer names URDNA2015

`generate_graph_iri` invokes `pyld.jsonld.normalize` with `algorithm: URDNA2015` and emits algorithm value 1. The proposed new profile evaluates RDFC-1.0. Resolve spec/version parity using executed vectors before claiming equivalence or deploying a replacement. The pinned requirements.txt does not declare pyld; a future runtime change must establish its dependency provenance in the actual serving environment. This is a source-level observation, not proof that a running service lacks the package.

## 3. Before changing anchored data

- Identify the exact object being anchored or attested and whether the decoded type is Raw or Graph.
- Name the content profile, canonicalization implementation/version, preimage, digest and IRI encoding. Check labels against actual behavior.
- Preserve exact Raw payload bytes and existing identifiers until an explicit migration decision.
- Treat local status and IRI generation separately from chain inclusion evidence.
- Run the selected conformance, negative-input and resource-bound tests; compare canonical bytes before comparing hashes.
- Use the standards review surface for schema changes, and the implementation proposal for service identity/migration. Neither is ratified merely by being documented.

The previous revision coupled a suffix check, a Raw-attestation overstatement and an unratified dual-anchor plan. Those conclusions are superseded by §§2.1, 2.7 and 2.8 above; historical discussion remains in PR history.
