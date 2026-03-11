# Claims Engine Dogfood Results

**Date:** 2026-03-10 (deployed to production 2026-03-11)
**Status:** V2 Phase 1 deployed to production — Phases A-G complete, first claim anchored on-chain

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

### Open Items for V2 Phase 2
- ~~Regen CLI installation~~ — ✅ completed (v7.2.0, `~/bin/regen`)
- Attestation anchoring (individual attestation tx_hash)
- Multi-org attestation requirements (cross-org review)
- Attestation expiry / time-bounded validity
- Proof pack export (bundle claim + attestations + evidence for verification)
- ~~Fix production `aliases` column type (`jsonb` → `TEXT[]`)~~ — ✅ fixed (migration 036 applied, event bridge updated, views recreated)
