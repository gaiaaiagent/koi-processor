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

2. **`MsgAttest` vs `MsgAnchor`** — `MsgAttest` with `ContentHash.Graph` would semantically mean "this data is accurate" vs "this data exists." Is `MsgAttest` ready for production use on `regen-1`? Any gotchas (gas, indexing, query support)?

3. **`cosmos.authz` delegation** — For per-reviewer signing: service account grants `MsgAttest` permission to reviewer addresses. Has this pattern been used on Regen Ledger? `authz` lets grantee execute via `MsgExec` on behalf of granter — does this give us per-reviewer identity binding, or is the on-chain signer still the granter's context?

4. **DID / identity standards** — Has any DID system been considered for Regen? Or is the practical path: Regen address + entity registry URI mapping?

5. **Key management UX** — For non-crypto-native reviewers (e.g., project developers), what's the lightest-weight path to a signing key? Browser wallet (Keplr)? Custodial service? Something else?

6. **Compatibility** — Any upcoming Regen Ledger changes (Cosmos SDK upgrade, module additions, ICS integration) that would affect the data module or identity?

---

## 4. Design Options (For Discussion — Not Recommendations)

### Option A — Service account + URI mapping
Keep single signer. Map `attestor_address` → entity registry URI in our DB. On-chain record says "the service anchored this," not "the reviewer signed this."
- **Pro**: Zero UX change, deployable today
- **Con**: Weakest identity binding — relies entirely on trust in the service operator

### Option B — `cosmos.authz` delegation
Reviewer gets own Regen address. Service account grants them `MsgAttest` permission via `MsgGrant`.
- **Pro**: Leverages existing Cosmos infrastructure
- **Con**: Need to verify with Marie — `authz` uses `MsgExec` wrapper, so the on-chain execution context may still be the granter. If so, this doesn't provide the per-reviewer identity binding we want.

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

## 6. Post-Call Outcomes (To Be Filled After March 12)

**Owner**: Darren

After the Thursday all-hands with Marie, update this section with:
- Which design option(s) Marie recommends or flags issues with
- Current state of Regen Ledger identity / org modules
- `MsgAttest` production readiness status
- `cosmos.authz` behavior confirmation (granter vs grantee context)
- Any SDK upgrade timeline that affects planning
- Agreed next steps and ownership

---

*Prepared: March 11, 2026*
