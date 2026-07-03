# Claims Engine Phase 3, Slice 3 — RBAM Integration for Reviewer Attestation Signing

**Status:** Design draft (conversation starter for Thursday meeting with Marie)
**Author:** Darren Zal
**Date:** 2026-04-02
**Prerequisite:** Slice 1 (Graph IRI generation), Slice 2 (MsgAttest broadcast)
**See also:** [V2 Attestation Design](v2-attestations.md), [Identity Integration](identity-integration.md)

---

## 1. Overview — Why RBAM Instead of Raw x/authz

The March 12 call with Marie confirmed `cosmos.authz` as viable for per-reviewer signing (Option B in identity-integration.md). But the Regen Marketplace has since moved to a higher-level abstraction: **DAO DAO + RBAM** (Role-Based Access Management).

**Why RBAM over raw `cosmos.authz`:**

- **Role semantics.** Raw authz grants are address-to-address (`MsgGrant`). RBAM adds role membership (owner, admin, editor, viewer, author), so authorization is "this address has the editor role in this org" rather than "address X granted address Y permission to send MsgAttest."
- **Existing pattern.** The Regen Marketplace already uses this for org management. Reference: `regen-web/web-marketplace/src/legacy-pages/CreateOrganization/hooks/useCreateDao`. We reuse the pattern rather than inventing our own.
- **Authorization filter composition.** RBAM uses typed authorization filters that match specific Cosmos message types. The `can_anchor_attest_data` authorization permits exactly the messages we need.
- **Fee grants are separate.** RBAM handles "who can do what." Fee sponsorship ("who pays gas") is a separate `cosmos.feegrant` concern. Clean separation of authorization from economics.

**The key insight:** RBAM is not required for the pilot. Individual reviewers can sign MsgAttest directly from their own wallets (Keplr) without any DAO or role setup. RBAM becomes relevant when an organization wants to delegate attestation authority to its members.

---

## 2. Two-Tier Reviewer Identity Model

### Tier 1 — Individual Path (Pilot)

Reviewer signs `MsgAttest` directly from their own wallet. No DAO, no RBAM, no delegation.

**Setup:**
1. Reviewer creates/has a Regen address (Keplr wallet)
2. Reviewer registers wallet with KOI: `POST /entity/{uri}/wallet` (migration 071, already deployed)
3. Service account optionally grants fee allowance (see Section 4)

**Signing:**
- Reviewer's Keplr wallet signs `MsgAttest` directly
- `attestor` field = reviewer's own `regen1...` address
- On-chain record: "reviewer X attested to content hash Y"

**Why start here:**
- Zero infrastructure beyond what we have (Keplr + regen CLI)
- Marie confirmed this works (March 12 call)
- Sufficient for dogfooding with 2-5 reviewers
- No dependency on DAO DAO contract deployment

### Tier 2 — Org-Delegation Path (Later)

Reviewer has a role in an org DAO (via RBAM) and signs `MsgAttest` on behalf of the org.

**Setup:**
1. Org creates a DAO via the Marketplace (useCreateDao pattern)
2. DAO assigns roles to members (owner/admin/editor)
3. RBAM contract grants `can_anchor_attest_data` authorization to role holders
4. Reviewer's wallet gains permission to sign `MsgAttest` as a DAO member

**Signing:**
- Reviewer signs via `MsgExec` wrapping `MsgAttest`
- The DAO address is the logical attestor; the reviewer's address is the executor
- On-chain record binds: org DAO + reviewer address + content hash

**When this matters:**
- Org-level attestation authority ("CEC reviewed this claim" not just "Dave reviewed this claim")
- Role revocation without revoking individual authz grants
- Audit trail of which role the reviewer held at attestation time

---

## 3. RBAM Authorization Model

The Marketplace defines a `can_anchor_attest_data` authorization that permits both data anchoring and attestation:

```json
{
  "filter": {
    "$or": [
      { "stargate": { "type_url": "/regen.data.v2.MsgAnchor" } },
      { "stargate": { "type_url": "/regen.data.v2.MsgAttest" } }
    ]
  }
}
```

### Role Inheritance

Marketplace roles and their authorization levels:

| Role | Scope | `can_anchor_attest_data` | Notes |
|------|-------|--------------------------|-------|
| **owner** | org | Yes | Full DAO admin |
| **admin** | org | Yes | Can manage members |
| **editor** | org | Yes | Can modify org content |
| **viewer** | org | No | Read-only |
| **author** | project | Yes | Project-level, not org-level |

Roles with `can_anchor_attest_data` authorization can execute `MsgAnchor` and `MsgAttest` on behalf of the DAO. The RBAM contract checks role membership at execution time -- if a role is revoked, the next MsgExec attempt fails.

### How It Works at the Contract Level

1. DAO is created with RBAM as its authorization module
2. RBAM stores role assignments: `(address, role, dao_address)`
3. When a member submits `MsgExec { MsgAttest { ... } }`, the RBAM contract:
   - Checks the sender has a role in the DAO
   - Checks the role includes `can_anchor_attest_data` authorization
   - Checks the inner message type matches the filter (`MsgAnchor` or `MsgAttest`)
   - If all pass, executes the inner message with the DAO as the logical sender

---

## 4. Fee Sponsorship

Fee sponsorship is a **separate concern** from authorization. A reviewer can have RBAM permission to sign `MsgAttest` but still need someone to cover gas fees.

**Mechanism:** `cosmos.feegrant.v1beta1.MsgGrantAllowance`

**Two sponsorship patterns:**

### Pattern A — Service Account Sponsors Individual Reviewers (Pilot)

```
Service account (claims-service) → MsgGrantAllowance → Reviewer wallet
```

- Covers gas for `MsgAttest` and `MsgAnchor` transactions
- Can set spend limits and expiration
- Works for Tier 1 (individual path) immediately

### Pattern B — Org DAO Sponsors Members (Later)

```
Org DAO → MsgGrantAllowance → Member wallet
```

- DAO treasury covers gas for members
- Aligned with RBAM role membership
- Fee grant can be scoped to specific message types

**Implementation note:** The KOI backend already manages the `claims-service` key. For the pilot, we add a `grant_fee_allowance()` helper alongside the existing `broadcast_anchor()` in `api/ledger_anchor.py`. This is a one-time setup per reviewer, not per-transaction.

---

## 5. Integration Points with KOI Claims Engine

### 5.1 Entity Registry — wallet_address (Done)

Migration 071 added `wallet_address TEXT` to `entity_registry` with a unique partial index. The `POST /entity/{uri}/wallet` endpoint validates bech32/EVM addresses and enforces uniqueness.

Current state: `wallet_address` is populated for the service account. Reviewer wallet registration is the first step of onboarding.

### 5.2 broadcast_attest() — Signer Address Parameter

Currently, `broadcast_anchor()` in `api/ledger_anchor.py` hardcodes `--from claims-service` (line 181). For per-reviewer signing:

**Individual path:**
```python
async def broadcast_attest(
    content_hash: str,
    signer_address: str,       # reviewer's regen1... address
    signer_key_name: str = None # if signing via CLI keyring (testing)
) -> dict:
```

For Keplr-based signing, the actual transaction construction happens client-side (browser). The backend provides:
- The content hash to attest
- The Graph IRI (from Slice 1's IRI generation)
- The unsigned `MsgAttest` payload for Keplr to sign

**Org path:**
```python
async def broadcast_attest_as_dao(
    content_hash: str,
    dao_address: str,           # DAO's regen1... address
    executor_address: str,      # reviewer's regen1... address
) -> dict:
```

The executor signs `MsgExec { inner: [MsgAttest { attestor: dao_address, ... }] }`. The RBAM contract validates role membership.

### 5.3 Graph IRI Generation (Slice 1 Dependency)

Slice 1 produces the `ContentHash.Graph` IRI for attestation payloads:

```
Attestation JSON-LD → URDNA2015 canonicalization → BLAKE2b-256 → base58check → regen:*.rdf IRI
```

This IRI is what `MsgAttest.content_hashes` references. Without Slice 1, there is no content hash for RBAM-authorized signers to attest to.

### 5.4 Attestation Record Binding

When an attestation is anchored on-chain:

| Field | Individual Path | Org Path |
|-------|----------------|----------|
| `claim_attestations.attestor_address` | Reviewer's `regen1...` | DAO's `regen1...` |
| `claim_attestations.attest_tx_hash` | Tx hash | Tx hash |
| `claim_attestations.reviewer_uri` | Reviewer entity URI | Reviewer entity URI |
| `claim_attestations.metadata` | `{}` | `{"dao_address": "regen1...", "role": "editor"}` |

For the org path, the `reviewer_uri` still points to the individual reviewer (the person who made the judgment). The DAO address is the on-chain attestor (the organization endorsing the judgment). Both are recorded.

---

## 6. Sequence Diagrams

### Individual Reviewer (Tier 1 — Pilot)

```
Reviewer              KOI Backend              Regen Ledger
   |                      |                         |
   |  1. Register wallet  |                         |
   |  POST /entity/{uri}/ |                         |
   |  wallet              |                         |
   |--------------------->|                         |
   |                      |                         |
   |  2. Create attestation                         |
   |  POST /claims/{rid}/ |                         |
   |  attestations        |                         |
   |--------------------->|                         |
   |      attestation_rid |                         |
   |<---------------------|                         |
   |                      |                         |
   |  3. Request unsigned |                         |
   |  MsgAttest payload   |                         |
   |  POST /claims/{rid}/ |                         |
   |  attestations/{att}/ |                         |
   |  prepare-attest      |                         |
   |--------------------->|                         |
   |   { msg, graph_iri } |                         |
   |<---------------------|                         |
   |                      |                         |
   |  4. Sign with Keplr  |                         |
   |  (client-side)       |                         |
   |  ~~~~~~~~~~~~~~~~~~~~>                         |
   |                      |                         |
   |  5. Broadcast signed |                         |
   |  tx                  |                         |
   |--------------------->|   MsgAttest             |
   |                      |------------------------>|
   |                      |   tx_hash               |
   |                      |<------------------------|
   |                      |                         |
   |  6. Record on-chain  |                         |
   |  binding             |                         |
   |   { tx_hash, iri }   |                         |
   |<---------------------|                         |
```

### Org Reviewer via RBAM (Tier 2 — Later)

```
Reviewer              KOI Backend              RBAM Contract     Regen Ledger
   |                      |                         |                  |
   |  1. Register wallet  |                         |                  |
   |--------------------->|                         |                  |
   |                      |                         |                  |
   |  2. Create           |                         |                  |
   |  attestation         |                         |                  |
   |--------------------->|                         |                  |
   |                      |                         |                  |
   |  3. Request unsigned |                         |                  |
   |  MsgExec payload     |                         |                  |
   |  (wrapping MsgAttest |                         |                  |
   |   with DAO address)  |                         |                  |
   |--------------------->|                         |                  |
   |   { msg, dao_addr }  |                         |                  |
   |<---------------------|                         |                  |
   |                      |                         |                  |
   |  4. Sign MsgExec     |                         |                  |
   |  with Keplr          |                         |                  |
   |  ~~~~~~~~~~~~~~~~~~~~>                         |                  |
   |                      |                         |                  |
   |  5. Broadcast        |                         |                  |
   |--------------------->|   MsgExec{MsgAttest}    |                  |
   |                      |------------------------>|                  |
   |                      |   check role membership |                  |
   |                      |   check authorization   |                  |
   |                      |   filter matches        |                  |
   |                      |   MsgAttest type_url    |                  |
   |                      |                         |  execute         |
   |                      |                         |  MsgAttest       |
   |                      |                         |----------------->|
   |                      |                         |   tx_hash        |
   |                      |                         |<-----------------|
   |                      |   tx_hash               |                  |
   |                      |<------------------------|                  |
   |                      |                         |                  |
   |  6. Record on-chain  |                         |                  |
   |  binding (DAO +      |                         |                  |
   |  reviewer)           |                         |                  |
   |<---------------------|                         |                  |
```

---

## 7. Open Questions for Marie

1. **RBAM contract addresses** — What are the deployed RBAM contract addresses on `regen-1` mainnet and `regen-redwood-1` (or current testnet)? We need these to construct `MsgExec` payloads.

2. **Minimal DAO for testing** — Is there a lightweight way to create a DAO for development/testing without going through the full Marketplace org creation flow? We need a DAO with 2-3 roles assigned for integration testing.

3. **Querying RBAM role membership** — Can we query the RBAM contract from the KOI backend to verify a reviewer's role before constructing the MsgExec payload? Specifically: given a `(wallet_address, dao_address)` pair, can we check if the address holds a role with `can_anchor_attest_data` authorization? This would let us fail fast with a clear error rather than submitting a transaction that the contract rejects.

4. **Recommended testnet** — What's the current recommended testnet for Phase 3 development? `regen-redwood-1`? Is there a faucet? Our mainnet-is-testnet approach works for MsgAnchor (cheap) but RBAM contract interactions may have different cost profiles.

5. **DAO DAO version compatibility** — Which version of DAO DAO is the Marketplace using? Are there breaking changes between versions we should pin against?

6. **Role assignment API** — Is there a REST/RPC endpoint to assign roles, or is it contract-execute only? For the pilot, we may want to script role assignment rather than going through the Marketplace UI.

---

## 8. References

- **useCreateDao hook (Marketplace reference):** `regen-web/web-marketplace/src/legacy-pages/CreateOrganization/hooks/useCreateDao`
- **DAO DAO documentation:** https://docs.daodao.zone/
- **Cosmos feegrant module:** https://docs.cosmos.network/main/build/modules/feegrant
- **cosmos.authz module:** https://docs.cosmos.network/main/build/modules/authz
- **Regen data module (MsgAttest):** https://buf.build/regen/regen-ledger/docs/main:regen.data.v2
- **Mintscan proof tx (authz grantee visibility):** https://www.mintscan.io/regen/tx/2cab48df2357f8f0ddb815e7dabadfd656708510ae4351d1b8f44eace2986472?height=20268347
- **KOI wallet registration:** Migration 071 (`migrations/071_wallet_address.sql`)
- **V2 attestation layer design:** [`docs/claims/v2-attestations.md`](v2-attestations.md) -- Section 8 covers MsgAttest signing model
- **Identity integration (post-call outcomes):** [`docs/claims/identity-integration.md`](identity-integration.md) -- Section 6 covers March 12 confirmed answers

---

*Draft: April 2, 2026 -- for Thursday discussion with Marie, not a final spec.*
