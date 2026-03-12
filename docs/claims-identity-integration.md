# Claims Engine — Identity & Registry Integration

> Prepared for March 12 all-hands with Marie (Regen Ledger).
> Goal: align Claims Engine Phase 3 identity design with Regen Ledger capabilities.

---

## 1. Current Identity Model (What Works)

The Claims Engine uses a **4-role model**: Claimant, Subject (about_uri), Operator, and Reviewer. Identity binding strength varies significantly by role:

| Role | Field | Binding | How it works |
|------|-------|---------|-------------|
| **Claimant** | `claimant_uri` | App-validated | Lookup against `entity_registry` at insert time, but no DB-level FK constraint (`064_claims_engine.sql:28` — `TEXT NOT NULL`, no `REFERENCES`) |
| **Operator** | `operator_uri` | DB foreign key | FK to `entity_registry(fuseki_uri)` (migration 066) |
| **Reviewer** | `reviewer_uri` | DB foreign key | FK to `entity_registry(fuseki_uri)` (migration 066), type-checked to Person/Organization |
| **State actor** | `claim_state_log.actor` | Free text | No validation — state transitions record a free-text string alongside identity-bound attestation records |

### Key integrity features already in place

- **Self-attestation guard**: Reviewer cannot be the claimant (`claims_router.py:1729-1733`)
- **Policy gates**: ≥1 approved attestation for `peer_reviewed`, ≥2 for `verified` (`claims_router.py:356-358`)
- **Content-addressable RIDs**: BLAKE2b-256 hash → `orn:koi-net.claim:*`
- **On-chain anchoring**: `MsgAnchor` on mainnet (`regen-1`) via `regen` CLI
- **Proof-pack**: Verification bundle with hash verification for offline audit

**Important caveat**: Even the strongest bindings (FK to `entity_registry`) are **identity-by-membership**, not cryptographic. No role has signing capability today.

---

## 2. The Gap (What's Missing)

1. **Single shared service account**: All on-chain anchoring uses one key (`claims-service`) resolved via `regen keys show` (`ledger_anchor.py:84-99`). The `MsgAnchor` broadcast hardcodes `--from claims-service` (`ledger_anchor.py:181-189`).

2. **No reviewer/operator signing**: Identity is entity-registry membership (a DB lookup), not cryptographic proof. A reviewer's attestation is recorded in PostgreSQL but never signed by that reviewer's key.

3. **`MsgAnchor` semantics**: Only proves "data existed at time T" — does **not** prove "reviewer X attested to claim Y." The on-chain record is: service account anchored a content hash.

4. **No per-reviewer on-chain identity binding**: The `attestor_address` column exists in `claim_attestations` (migration 066, line 28) but is only populated with the service account address, not the reviewer's own address.

---

## 3. Questions for Marie

1. **Regen Ledger org identity** — What's the current state of on-chain organization identity? Is there an org registry module, or just addresses? Can an organization be represented on-chain beyond a multisig?

2. **`MsgAttest` vs `MsgAnchor`** — ~~Is `MsgAttest` ready for production use?~~ **ANSWERED (pre-meeting, Mar 12):** Yes — Marie confirmed MsgAttest has been used for several years on mainnet. Production-ready, no caveats.

3. **`cosmos.authz` delegation** — ~~Does MsgExec reflect grantee identity on-chain?~~ **ANSWERED (pre-meeting, Mar 12):** Yes — Marie confirmed the grantee's identity is reflected on-chain. [Mintscan proof tx](https://www.mintscan.io/regen/tx/2cab48df2357f8f0ddb815e7dabadfd656708510ae4351d1b8f44eace2986472?height=20268347). This confirms Option B is viable.

4. **DID / identity standards** — Has any DID system been considered for Regen? Or is the practical path: Regen address + entity registry URI mapping?

5. **Key management UX** — For non-crypto-native reviewers (e.g., project developers), what's the lightest-weight path to a signing key? Browser wallet (Keplr)? Custodial service? Something else?

6. **Compatibility** — ~~Any upcoming changes affecting the data module?~~ **ANSWERED (pre-meeting, Mar 12):** No upcoming ledger changes affecting the data module. Safe to build on current APIs.

---

## 4. Design Options (For Discussion — Not Recommendations)

### Option A — Service account + URI mapping
Keep single signer. Map `attestor_address` → entity registry URI in our DB. On-chain record says "the service anchored this," not "the reviewer signed this."
- **Pro**: Zero UX change, deployable today
- **Con**: Weakest identity binding — relies entirely on trust in the service operator

### Option B — `cosmos.authz` delegation (CONFIRMED VIABLE)
Reviewer gets own Regen address. Service account grants them `MsgAttest` permission via `MsgGrant`. When we submit via `MsgExec`, the grantee's identity is reflected on-chain.
- **Pro**: Leverages existing Cosmos infrastructure, grantee identity confirmed on-chain ([Mintscan proof](https://www.mintscan.io/regen/tx/2cab48df2357f8f0ddb815e7dabadfd656708510ae4351d1b8f44eace2986472?height=20268347))
- **Con**: Reviewer still needs a Regen address and gas — key management UX TBD
- **Status**: Marie confirmed viability pre-meeting Mar 12. Remaining question: lightest key management path for non-crypto-native reviewers.

### Option C — Per-reviewer direct signing
Each reviewer has their own key (Keplr, CLI, or custodial). They sign `MsgAttest` directly with their own address.
- **Pro**: Strongest identity binding — on-chain record is: "reviewer X's address attested to content hash Y"
- **Con**: Heaviest UX lift (key generation, gas funding, key management)

### Option D — Hybrid off-chain/on-chain
WebCrypto or OAuth-backed signing for off-chain attestation records. Service account handles on-chain anchoring separately.
- **Pro**: Separates "identity proof" (off-chain, fast) from "timestamping" (on-chain, durable)
- **Con**: Two different trust models to explain

**Key question for Marie**: Which option(s) are compatible with Regen Ledger's current and planned identity infrastructure?

---

## 5. Reference — Current Schema

### `claims` table (migration 064)

| Column | Type | Identity role |
|--------|------|--------------|
| `claimant_uri` | `TEXT NOT NULL` | App-validated against entity_registry |
| `operator_uri` | `TEXT REFERENCES entity_registry(fuseki_uri)` | DB foreign key (migration 066) |
| `content_hash` | `TEXT` | BLAKE2b-256 of canonical JSON |
| `ledger_iri` | `TEXT` | IRI from MsgAnchor response |
| `tx_hash` | `TEXT` | Broadcast transaction hash (migration 065) |

### `claim_attestations` table (migration 066)

| Column | Type | Identity role |
|--------|------|--------------|
| `reviewer_uri` | `TEXT NOT NULL REFERENCES entity_registry(fuseki_uri)` | DB foreign key |
| `attestor_address` | `TEXT` | Regen address (currently service account only) |
| `content_hash` | `TEXT` | BLAKE2b-256 of attestation bundle |
| `attest_tx_hash` | `TEXT` | Transaction hash for attestation anchor |
| `verdict` | `TEXT` | pending/approved/rejected/needs_info |

### `claim_state_log` table (migration 064)

| Column | Type | Identity role |
|--------|------|--------------|
| `actor` | `TEXT` | Free-text, no validation |

### Related docs

- [Claims Engine V2 Attestation Design](claims-engine-v2-attestations.md) — §8 covers MsgAttest signing model
- [Claims Engine V1](claims-engine-v1.md) — Core architecture and dogfooding results

---

## 6. Post-Call Outcomes

**Owner**: Darren

### Pre-meeting answers (Marie, Mar 12 overnight)

Marie answered 3 of 6 questions before the meeting via Dave:

| Question | Answer |
|----------|--------|
| MsgAttest production-ready? | Yes — used for several years on mainnet |
| cosmos.authz grantee identity? | Yes — grantee reflected on-chain ([Mintscan proof](https://www.mintscan.io/regen/tx/2cab48df2357f8f0ddb815e7dabadfd656708510ae4351d1b8f44eace2986472?height=20268347)) |
| Upcoming ledger changes? | None affecting the data module |

**Impact:** Option B (authz delegation) confirmed viable. Discussion shifts from "is this possible?" to "how do we implement it?"

### Still open for meeting discussion

- Key management UX for non-crypto-native reviewers
- authz grant flow implementation details
- DID / identity standards direction
- Org identity on-chain (beyond multisig)

### Meeting outcomes (fill after call)

- *TBD*

---

*Prepared: March 11, 2026*
