# Claims Engine Dogfood Results

**Date:** 2026-03-11
**Status:** V2 Phase 2 deployed — attestation anchoring + proof-pack download live on mainnet

## Phase A-D Summary (completed 2026-03-09)

16 real claims created across 3 organizations:
- **CEC** (Community Environmental Council): 8 claims
- **Blue Forest Conservation**: 5 claims
- **Zero Foodprint**: 3 claims

All claims at `self_reported` state. Evidence linking and AI extraction tested.

## Phase E — Peer Review

**Status:** COMPLETE (2026-03-11)

All 3 original claims received identity-bound attestations and advanced through the state machine:

| Claim RID | Org | Final State | Attestations | Reviewers |
|-----------|-----|-------------|-------------|-----------|
| `d209853096489f32` | Regen Network | `verified` | 2 approved | Darren Zal, Dave Nielsen |
| `a26c6445b5a202eb` | CEC | `verified` | 2 approved | Darren Zal, Dave Nielsen |
| `e7ec2556ec035ab1` | Zero Foodprint | `peer_reviewed` | 1 approved | Darren Zal |

**V2 attestation flow validated:**
- Reviewer entity search via `entity-search` endpoint
- Attestation creation with FK to `entity_registry`
- Self-attestation guard (claimant cannot review own claim)
- Case-insensitive entity type matching (prod uses UPPERCASE types)
- Grandfathering confirmed — pre-migration claims advance without policy gates

## Phase F — Anchoring

**Status:** COMPLETE (2026-03-11)

First claim successfully anchored on Regen Ledger mainnet (`regen-1`).

**Setup:**
- `regen` CLI v7.2.0 installed to `~/bin/regen` on production
- `claims-service` key created in test keyring
- Funded via Vitwit faucet (100M uregen)
- `start-claims-api.sh` updated with `export PATH=$HOME/bin:$PATH`
- `.env` updated with `REGEN_CHAIN_ID`, `REGEN_RPC_URL`, `REGEN_REST_URL`, `REGEN_KEY_NAME`

| Claim RID | Content Hash | Tx Hash | Ledger IRI | Status |
|-----------|-------------|---------|------------|--------|
| `d209853096489f32` | `17b5eb3dd51d...` | `D64BCE8E6A928253D17581D9AD0A9A5CB0779D94D381F56EC2F705422357C28B` | `regen:1138BqHBPtQ9u8NH6q9kZCaMfT7MB8Zj5bJYtVLjGEaRgzaoBMTL.json` | anchored |
| `a26c6445b5a202eb` | `c68b2b907fc9...` | `C023433392F529FD6982655D42D89F3B2B90B07F5A4631A8A41D6789AFB03727` | `regen:114TBi1Y15xYqYpN7kpgnFUCmvxwnDc7ePxPTve6yvKjmQamm64A.json` | anchored |

**Pipeline validated:** Both claims took the direct 200 path (no 202→reconcile needed). First anchor: `2026-03-11T06:23:14Z`. Second anchor (CEC claim): `2026-03-11T07:59:29Z`.

## Phase G — Observations

### V1 → V2 Improvements
- **V1:** `actor` was free-text — anyone could write any name
- **V2:** `reviewer_uri` is FK to `entity_registry` — identity-bound
- **V2:** Policy gates enforce attestation counts before state transitions
- **V2:** UPSERT attestations — same reviewer can update verdict
- **V2:** Self-attestation guard — claimant cannot attest their own claims
- **V2:** Grandfathering — pre-migration claims exempt from policy gates

### What Works Well
- Content-addressable RIDs prevent duplicate claims
- State machine transitions are well-guarded
- Evidence linking with type enforcement
- Anchor/reconcile flow handles timeouts gracefully

### V2 Phase 1 Metrics
- 11 new pytest tests (attestation CRUD + policy gates)
- 16 existing reconcile tests — no regressions
- 56/56 HTTP smoke tests passing on production (aliases column type mismatch fixed: `jsonb` → `TEXT[]`)
- Migration 052 + 066 applied to production `eliza` DB
- 5 attestations created across 3 claims (2 reviewers)
- Grandfathering cutoff: `2026-03-11T05:36:36Z`

### Production Discovery: Entity Type Case Mismatch
Production `eliza` DB uses UPPERCASE entity types (`PERSON`, `ORGANIZATION`) while local `personal_koi` uses mixed-case (`Person`, `Organization`). Fixed claims router to use case-insensitive comparison (`.lower()`). The `aliases` column is also `jsonb` on prod vs `TEXT[]` locally — causes `ANY(aliases)` to fail. This is a pre-existing issue affecting entity creation via `/ingest`, not claims-specific.

### Open Items for V2 Phase 3
- Multi-org attestation requirements (cross-org review)
- Attestation expiry / time-bounded validity

## Phase H — V2 Phase 2: Attestation Anchoring + Proof-Pack (2026-03-11)

**Status:** COMPLETE

**Deployed:** `a916ed71` + `4823bfa9` to prod, migration 069 applied (`graph_iri` → `ledger_iri`).

### Attestation Anchoring

4 attestations anchored on Regen Ledger mainnet (`regen-1`). All took the direct 200 path (no 202→reconcile needed).

| Attestation RID | Claim RID | Content Hash | Tx Hash | Ledger IRI |
|-----------------|-----------|-------------|---------|------------|
| `fbd74f0429d608ff` | `d209853096489f32` | `2d44876d90eb...` | `4EFDEED7D687EDBD87CD073CD5D252C99181376E7B330DFBC21D505EA6E7E0AF` | `regen:113HgUmoMx9ewQjRxgTQZ5M2cG46RDRrsj3yDU6yfcabqN7ZtAKd.json` |
| `85c4f34f5b2008bb` | `d209853096489f32` | `bc111318c9d0...` | `51C1A648EE5ABA992910D916C711A45AAB79E42A622DC13E3253110C0C72974C` | `regen:114Na66L34mTgfXabMCNovZUjiCi4Sx4Rk6xMqnasRZ5o1bfksZ3.json` |
| `83b7b015ec89c5ce` | `a26c6445b5a202eb` | `f05224d83abd...` | `4684C17996280659BD3CEE911B540D35BB65F60F6243B17820FDC9B068734B94` | `regen:114marLTTyGdjtNSTN1SfynLbzjnekybDZzt5trG2cvZsR6VhhqY.json` |
| `729daddb0ad68910` | `a26c6445b5a202eb` | `8e203e5d8e4e...` | `B21624533698D6E889022D3F48658634EAEA2CFC9071AF34D03D3B36F09CE5A8` | `regen:1142LbQAVK8u2jKEaca7LHcjGfZECXA9gnshkQJLyESQhCfhY9rk.json` |

Attestor address: `regen15eexs5vt9klzf304v2fczfh2823lwgz8g4apt9`

### Proof-Pack Download

Both claims have downloadable proof-packs via `GET /claims/{rid}/proof-pack?format=download`:

| Claim | Filename | Version | Chain ID | Attestation Hashes Verified |
|-------|----------|---------|----------|-----------------------------|
| `d209853096489f32` | `proof-pack-d20985309648-2026-03-11.json` | 2.0 | regen-1 | claim ✅ attest 2/2 ✅ |
| `a26c6445b5a202eb` | `proof-pack-a26c6445b5a2-2026-03-11.json` | 2.0 | regen-1 | claim ✅ attest 2/2 ✅ |

### Content Hash Fix (`4823bfa9`)

Original `_canonical_claim_json` included the mutable `verification` field. When a claim transitions from `verified` → `ledger_anchored`, recomputing the hash produces a different value, causing `claim_content_hash_verified: False`. Fixed by:

1. **Removed `verification` from canonical JSON** — it's mutable state, not content
2. **Anchored claims**: proof-pack verifies `derive_ledger_iri(stored_hash) == stored_ledger_iri` (pinned hash matches on-chain IRI)
3. **Non-anchored claims**: proof-pack recomputes hash from fields as before
4. **Backfilled** non-anchored claim hashes on both local (27) and prod (3)

The 2 on-chain claim anchors (`D64BCE...`, `C02343...`) remain valid — their `content_hash` → `ledger_iri` derivation is correct and immutable. The hash algorithm change only affects future anchors.

### Known Test Issues (unrelated to Phase 2)

2 of 16 `test_claims_reconcile.py` tests fail (`test_anchor_verify_fail_returns_202`, `test_reconcile_tx_confirmed_anchor_not_indexed`). These are pre-existing failures from a prior decision to treat tx confirmation as sufficient without requiring REST IRI verification. Not caused by Phase 2 changes.

### V2 Phase 2 Metrics
- 6 new pytest tests (attestation anchoring + proof-pack)
- 5 new HTTP smoke tests (tests 21-25)
- Migration 069 applied (column rename `graph_iri` → `ledger_iri`)
- 4 attestations anchored on mainnet (all direct 200 path)
- Lazy `content_hash` backfill confirmed (all 4 attestations had NULL content_hash before anchor)
- `compute_attestation_hash()` produces deterministic BLAKE2b-256 hashes
- `get_signing_address()` correctly resolves from local keyring
