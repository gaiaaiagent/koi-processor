# Claims Engine V2 — Attestation Layer Design

**Status:** Design Document (Mar 10, 2026)
**Prerequisite:** Claims Engine V1 + V2 Hardening (deployed Mar 9, 2026)
**Author:** Darren Zal

---

## 1. Problem Statement

The Claims Engine V1 records **who** reviewed a claim, but doesn't **prove** it. Identity binding in the current system has a critical asymmetry:

| Field | Table | Binding | Strength |
|-------|-------|---------|----------|
| `claimant_uri` | `claims` | FK → `entity_registry` | **Strong** — validated on insert |
| `created_by` | `claims` | `TEXT`, optional | **Weak** — not even returned in API responses |
| `actor` | `claim_state_log` | `TEXT`, optional | **None** — free-text, zero validation |

When Darren types `"actor": "David Fortson"` on a verify request, that string is stored verbatim with no validation that David Fortson exists, agreed, or was involved. The system is useful workflow infrastructure, but not a trust network.

### What's Missing

1. **Operator identity** — `created_by` exists in the DB (`claims.created_by`, line 55 of `064_claims_engine.sql`) but `ClaimResponse` (lines 49-66 of `claims_router.py`) doesn't include it. API consumers can't see who created a claim.

2. **Reviewer identity** — `actor` in `claim_state_log` is a string Darren typed, not a reference to a resolved entity. There's no way to query "what has David Fortson reviewed?" without full-text searching the audit log.

3. **Attestation as a record** — Reviews are currently side effects of state transitions (`PATCH /claims/{rid}/verify`), not first-class records with their own lifecycle, evidence references, and on-chain anchoring capability.

---

## 2. Four-Role Model

V2 introduces four distinct roles with clear identity semantics:

| Role | Definition | V1 | V2 |
|------|-----------|-----|-----|
| **Claimant** | Entity making the impact assertion | `claimant_uri` → FK to `entity_registry` | No change (already strong) |
| **Subject** | Entity the claim is about | `about_uri` via graph edge (`about` predicate) | No change |
| **Operator** | Agent who entered the claim into the system | `created_by` TEXT (hidden from API) | `operator_uri` → FK to `entity_registry`, returned in responses |
| **Reviewer** | Agent who evaluates claim veracity | `actor` TEXT in `claim_state_log` (free-text) | `reviewer_uri` in `claim_attestations` → FK to `entity_registry` |

**Why four roles, not two:**
- Claimant ≠ Operator. CEC claims something; Darren enters it into the system. Both identities matter for provenance.
- Reviewer ≠ Operator. Dave reviews a claim he didn't create. His judgment is a different speech act than data entry.

---

## 3. Schema Changes

### 3.1 Migration 066: `claim_attestations` Table

```sql
-- Migration 066: Claims Engine V2 — Attestation layer
-- Purely additive — no existing data modified

-- New predicates for attestation relationships
INSERT INTO allowed_predicates (predicate, description, subject_types, object_types) VALUES
  ('attests_claim', 'Reviewer attests to a claim''s veracity', ARRAY['Person', 'Organization'], ARRAY['Claim']),
  ('operates_claim', 'Operator entered a claim into the system', ARRAY['Person', 'Organization'], ARRAY['Claim'])
ON CONFLICT (predicate) DO NOTHING;

-- Add operator_uri to claims (nullable — existing claims get NULL)
ALTER TABLE claims ADD COLUMN IF NOT EXISTS operator_uri TEXT
  REFERENCES entity_registry(fuseki_uri);

-- First-class attestation records
CREATE TABLE claim_attestations (
  id SERIAL PRIMARY KEY,
  attestation_rid TEXT UNIQUE NOT NULL,    -- orn:koi-net.attestation:<hash>
  claim_rid TEXT NOT NULL REFERENCES claims(claim_rid),
  reviewer_uri TEXT NOT NULL REFERENCES entity_registry(fuseki_uri),

  -- Verdict
  verdict TEXT NOT NULL DEFAULT 'pending'
    CHECK (verdict IN ('pending', 'approved', 'rejected', 'needs_info')),
  rationale TEXT,
  evidence_uris TEXT[],                    -- entity URIs the reviewer examined

  -- On-chain attestation (MsgAttest)
  content_hash TEXT,                       -- BLAKE2b-256 of canonical attestation
  graph_iri TEXT,                          -- regen:.rdf IRI for ContentHash.Graph
  attest_tx_hash TEXT,
  attest_timestamp TIMESTAMPTZ,
  attestor_address TEXT,                   -- regen1... on-chain address

  -- Extensible
  metadata JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),

  -- One active attestation per reviewer per claim
  UNIQUE(claim_rid, reviewer_uri)
);

CREATE INDEX idx_attestations_claim ON claim_attestations(claim_rid);
CREATE INDEX idx_attestations_reviewer ON claim_attestations(reviewer_uri);
CREATE INDEX idx_attestations_verdict ON claim_attestations(verdict);
```

### 3.2 Changes to Existing Tables

**`claims` table:**
- Add `operator_uri TEXT REFERENCES entity_registry(fuseki_uri)` (nullable)
- Existing `created_by` preserved as-is (free-text provenance string)

**`claim_state_log` table:**
- No schema changes. Existing free-text `actor` entries preserved.
- New state log entries written by the attestation layer use `reviewer_uri` value as `actor`, providing a backwards-compatible upgrade path.

---

## 4. Attestation RID Strategy

Content-addressable, following the same pattern as claim RIDs:

```
attestation_rid = orn:koi-net.attestation:{sha256(canonical_json)[:16]}
```

Canonical JSON includes:
```json
{
  "claim_rid": "orn:koi-net.claim:abc123...",
  "reviewer_uri": "urn:koi:entity:david-fortson-...",
  "verdict": "approved",
  "rationale": "Reviewed evidence documents...",
  "evidence_uris": ["urn:koi:entity:ev1", "urn:koi:entity:ev2"]
}
```

Same content → same RID → idempotent (UNIQUE constraint on `attestation_rid`).
Verdict change → new canonical JSON → new RID → `INSERT ... ON CONFLICT(claim_rid, reviewer_uri) DO UPDATE`.

---

## 5. API Changes

### 5.1 New Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/claims/{rid}/attestations` | Create or update attestation for a claim |
| `GET` | `/claims/{rid}/attestations` | List all attestations for a claim |
| `GET` | `/claims/{rid}/attestations/{att_rid}` | Get specific attestation |
| `POST` | `/claims/{rid}/attestations/{att_rid}/attest-onchain` | Broadcast `MsgAttest` (longer-term) |

#### `POST /claims/{rid}/attestations`

**Request:**
```json
{
  "reviewer_uri": "urn:koi:entity:david-fortson-...",
  "verdict": "approved",
  "rationale": "Reviewed CEC annual report and satellite imagery...",
  "evidence_uris": ["urn:koi:entity:ev1", "urn:koi:entity:ev2"],
  "metadata": {}
}
```

**Validation:**
- `reviewer_uri` must exist in `entity_registry` (FK check)
- `reviewer_uri` must NOT equal `claimant_uri` on the claim (non-self-attestation guard)
- `claim_rid` must exist
- `verdict` must be one of: `pending`, `approved`, `rejected`, `needs_info`
- `evidence_uris` (if provided) must all exist in `entity_registry`

**Response (201):**
```json
{
  "attestation_rid": "orn:koi-net.attestation:a1b2c3...",
  "claim_rid": "orn:koi-net.claim:xyz789...",
  "reviewer_uri": "urn:koi:entity:david-fortson-...",
  "verdict": "approved",
  "rationale": "...",
  "evidence_uris": ["..."],
  "content_hash": null,
  "graph_iri": null,
  "attest_tx_hash": null,
  "created_at": "2026-03-10T...",
  "updated_at": "2026-03-10T..."
}
```

**Side effects:**
1. Creates `attests_claim` graph edge: `reviewer_uri → claim entity_uri`
2. Creates `claim_state_log` entry with `actor = reviewer_uri` (not free-text)
3. On verdict update (UPSERT), creates new state log entry

#### `GET /claims/{rid}/attestations`

Returns array of all attestations. Supports `?verdict=approved` filter.

#### `GET /claims/{rid}/attestations/{att_rid}`

Returns single attestation with full detail.

### 5.2 Modified Endpoints

#### `POST /claims/` — Add `operator_uri`

**Request change:** Add optional `operator_uri` field.
```json
{
  "claimant_uri": "...",
  "statement": "...",
  "operator_uri": "urn:koi:entity:darren-zal-..."
}
```

If provided, validated as FK to `entity_registry`. Creates `operates_claim` graph edge.

#### `GET /claims/{rid}` — Enrich response

Add to `ClaimResponse`:
- `created_by: Optional[str]` — surface the existing hidden field
- `operator_uri: Optional[str]` — new FK field
- `attestation_count: int` — count of attestations (avoids N+1 queries)
- `attestation_summary: Optional[dict]` — e.g., `{"approved": 2, "rejected": 0, "pending": 1}`

#### `PATCH /claims/{rid}/verify` — Attestation policy preconditions

When transitioning to `peer_reviewed` or `verified`, the endpoint checks attestation policy (see Section 6). If preconditions are not met, returns `409 Conflict` with details on what's missing.

---

## 6. Attestation-Derived State (Hybrid Policy)

V2 uses a **hybrid model**: attestations are preconditions for state transitions, but transitions remain explicit. An operator or claimant still calls `PATCH /verify` — the system just enforces that sufficient attestations exist first.

### Policy Rules

| Target State | Precondition | Enforcement |
|-------------|-------------|-------------|
| `peer_reviewed` | ≥ 1 `approved` attestation from a reviewer where `reviewer_uri ≠ claimant_uri` | Hard gate |
| `verified` | ≥ 2 `approved` attestations, OR ≥ 1 from a verified authority (future: authority registry) | Hard gate |
| `ledger_anchored` | `content_hash` computed + anchor broadcast (unchanged from V1) | Unchanged |
| `withdrawn` | No attestation precondition (claimant/operator can always withdraw) | Unchanged |

### Grandfathering

Pre-V2 claims (created before migration 066 is applied) are exempt from attestation preconditions. The exemption is determined by checking `claims.created_at` against the migration timestamp stored in `koi_migrations_registry`.

This means:
- All 49 existing claims retain their current verification states
- Existing `claim_state_log` entries with free-text `actor` are preserved
- The policy gate only activates for claims created after V2 goes live

---

## 7. Graph Integration

Attestations create graph edges like all other KOI entities:

| Predicate | Subject | Object | Created When |
|-----------|---------|--------|-------------|
| `attests_claim` | `reviewer_uri` (Person/Org) | claim `entity_uri` (Claim) | Attestation created |
| `operates_claim` | `operator_uri` (Person/Org) | claim `entity_uri` (Claim) | Claim created with `operator_uri` |

These edges enable graph queries like:
- "What claims has David Fortson reviewed?" → traverse `attests_claim` edges from David's entity
- "Who has reviewed claims about CEC?" → `about` edge to CEC + `attests_claim` reverse traversal
- "What's Darren's operator footprint?" → traverse `operates_claim` edges

---

## 8. On-Chain Attestation via MsgAttest

### V1 Recap: MsgAnchor

V1 uses `MsgAnchor` with `ContentHash.Raw` to timestamp claim data on-chain:
- Proves data existed at a point in time
- Single signer (service account)
- File: `api/ledger_anchor.py` — `broadcast_anchor()` (line 124)

### V2 Addition: MsgAttest

`MsgAttest` adds **veracity attestation** — a signer asserts that a `ContentHash.Graph` is accurate:

```protobuf
message MsgAttest {
  string attestor = 1;        // regen1... address
  repeated ContentHash_Graph content_hashes = 2;
}
```

The difference:
- `MsgAnchor` says: "This data exists" (timestamp)
- `MsgAttest` says: "This data is accurate" (judgment)

### Canonical Serialization

For `ContentHash.Graph`, we need a deterministic serialization of the claim + attestation bundle:

```json
{
  "@context": "https://schema.regen.network/claims/v2",
  "claim": {
    "rid": "orn:koi-net.claim:xyz789...",
    "claimant": "urn:koi:entity:cec-...",
    "statement": "...",
    "type": "ecological",
    "metadata": { ... }
  },
  "attestation": {
    "rid": "orn:koi-net.attestation:a1b2c3...",
    "reviewer": "urn:koi:entity:david-fortson-...",
    "verdict": "approved",
    "rationale": "...",
    "evidence": ["urn:koi:entity:ev1", "..."]
  }
}
```

Serialized with `json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(',', ':'))` (same approach as V1 `_canonical_claim_json` in `ledger_anchor.py` line 26), then hashed with BLAKE2b-256.

**Note:** This is deterministic JSON-LD, not full RDFC 1.0 (RDF Dataset Canonicalization). Full canonicalization is deferred to V3+ (see Section 11).

### Signing Model

| Phase | Signer | How |
|-------|--------|-----|
| Near-term | Service account (`claims-service` key) | Same as V1 `MsgAnchor` — `regen` CLI with `--keyring-backend test` |
| Longer-term | Per-reviewer delegated key | `cosmos.authz` `MsgGrant` — service account delegates `MsgAttest` permission to reviewer addresses |

The per-reviewer model requires:
1. Reviewer generates or receives a Regen address
2. Service account grants `MsgAttest` authorization via `cosmos.authz`
3. Reviewer signs attestation tx with their own key
4. On-chain record binds: reviewer address → content hash → attestation

---

## 9. Implementation Phases

### Phase 1: Near-Term (~1 sprint)

**Scope:** Foundation — make identity visible and create attestation records.

| Task | Files | Description |
|------|-------|-------------|
| Surface `created_by` in API | `api/routers/claims_router.py` (line 49) | Add `created_by: Optional[str]` to `ClaimResponse` |
| Add `operator_uri` column | Migration 066 | `ALTER TABLE claims ADD COLUMN operator_uri TEXT REFERENCES entity_registry(fuseki_uri)` |
| Create `claim_attestations` table | Migration 066 | Full schema from Section 3.1 |
| Add `attests_claim` + `operates_claim` predicates | Migration 066 | Insert into `allowed_predicates` |
| CRUD endpoints for attestations | `api/routers/claims_router.py` | `POST/GET /claims/{rid}/attestations` |
| Update `ClaimResponse` | `api/routers/claims_router.py` | Add `operator_uri`, `attestation_count`, `attestation_summary` |
| Update `ClaimCreateRequest` | `api/routers/claims_router.py` (line 37) | Add optional `operator_uri` field |

### Phase 2: Mid-Term (~1 sprint)

**Scope:** Policy enforcement and graph integration.

| Task | Files | Description |
|------|-------|-------------|
| Attestation policy preconditions | `api/routers/claims_router.py` | 409 Conflict on verify without sufficient attestations |
| Graph edges for attestations | `api/routers/claims_router.py` | `attests_claim` + `operates_claim` edges on create |
| MCP tools | `regen-koi-mcp` | `create_attestation`, `list_attestations`, `get_attestation` tools |
| Grandfathering logic | `api/routers/claims_router.py` | Exempt pre-V2 claims from policy checks |

### Phase 3: Longer-Term (~2 sprints)

**Scope:** On-chain attestation and advanced identity.

| Task | Files | Description |
|------|-------|-------------|
| `MsgAttest` broadcast | `api/ledger_anchor.py` | New `broadcast_attest()` function alongside existing `broadcast_anchor()` |
| Canonical JSON-LD hashing | `api/ledger_anchor.py` | Claim + attestation bundle serialization |
| `attest-onchain` endpoint | `api/routers/claims_router.py` | `POST /claims/{rid}/attestations/{att_rid}/attest-onchain` |
| Per-reviewer key delegation | New module | `cosmos.authz` MsgGrant workflow |
| Reconcile for attestations | `api/routers/claims_router.py` | Similar to claim reconcile — check `MsgAttest` tx status |

---

## 10. Migration Safety

1. **Migration 066 is purely additive.** No existing data is modified, no columns dropped, no constraints changed on existing tables.

2. **`operator_uri` is nullable.** Existing claims get `NULL`. No backfill required.

3. **`claim_state_log` is unchanged.** Existing free-text `actor` entries are preserved verbatim. New entries written by the attestation layer use `reviewer_uri` as the `actor` value.

4. **Rollback:** Drop `claim_attestations` table, drop `operator_uri` column. No data loss on existing tables.

5. **Grandfathering:** Policy checks compare `claims.created_at` against migration timestamp. Claims created before V2 are exempt from attestation preconditions.

---

## 11. What V2 Does NOT Cover (V3+)

| Item | Why Deferred |
|------|-------------|
| Full RDFC 1.0 canonicalization | Current deterministic JSON-LD is sufficient; RDFC 1.0 adds complexity with no immediate benefit |
| Per-reviewer on-chain key management | Requires UX for key generation, custody, and `cosmos.authz` grants — different product surface |
| Cross-org reviewer trust policies | Need real multi-org usage data before designing trust boundaries |
| Multi-party attestation thresholds | Simple count-based policy is adequate for dogfooding; weighted/quorum models need research |
| Reviewer reputation/authority scoring | Depends on attestation volume that doesn't exist yet |
| Dashboard UI for attestation workflows | Backend-first; UI follows when the data model stabilizes |

---

## 12. Key Files Reference

| File | Current Role | V2 Changes |
|------|-------------|------------|
| `api/routers/claims_router.py` | All claim endpoints, Pydantic models | Add attestation endpoints, update `ClaimResponse` + `ClaimCreateRequest` |
| `api/ledger_anchor.py` | `MsgAnchor` broadcast + reconcile helpers | Add `MsgAttest` broadcast (Phase 3) |
| `migrations/064_claims_engine.sql` | Core `claims` + `claim_state_log` tables | No changes |
| `migrations/065_claims_tx_hash.sql` | `tx_hash` column on `claims` | No changes |
| `migrations/066_attestations.sql` | *(new)* | `claim_attestations` table, `operator_uri` column, new predicates |
| `docs/claims-engine-v1.md` | V1 implementation reference | Add "V2 Design" cross-reference |

---

## Appendix A: Example Attestation Lifecycle

```
1. Darren creates claim (operator)
   POST /claims/
   { claimant_uri: "CEC", statement: "...", operator_uri: "Darren" }

2. Dave reviews and attests (reviewer)
   POST /claims/{rid}/attestations
   { reviewer_uri: "Dave", verdict: "approved", rationale: "..." }
   → attests_claim edge created
   → claim_state_log entry: actor="Dave's entity URI"

3. Darren advances state (operator action, policy-gated)
   PATCH /claims/{rid}/verify
   { new_level: "peer_reviewed", actor: "Darren" }
   → System checks: ≥1 approved attestation from non-claimant? ✓
   → State transitions to peer_reviewed

4. Second reviewer attests
   POST /claims/{rid}/attestations
   { reviewer_uri: "Samu", verdict: "approved", rationale: "..." }

5. Advance to verified
   PATCH /claims/{rid}/verify
   { new_level: "verified" }
   → System checks: ≥2 approved attestations? ✓ (Dave + Samu)
   → State transitions to verified

6. Anchor on-chain (unchanged from V1)
   POST /claims/{rid}/anchor
   → MsgAnchor broadcast, ledger_anchored state

7. (Future) Attest on-chain
   POST /claims/{rid}/attestations/{att_rid}/attest-onchain
   → MsgAttest broadcast with ContentHash.Graph
```

---

## Appendix B: Comparison with Alternative Approaches

### Alternative: "Claims about Claims"

One approach would be to model reviews as claims about claims — a meta-claim like "Dave claims that CEC's carbon claim is accurate." This was rejected because:

1. **Different semantics.** A claim is an impact assertion ("we restored 200 hectares"). An attestation is a judgment about an assertion ("I reviewed the evidence and agree"). Conflating them muddies the data model.

2. **Different lifecycle.** Claims have a verification state machine. Attestations have a simpler verdict enum. Forcing attestations through the claim lifecycle adds unnecessary complexity.

3. **Different schema needs.** Attestations need `reviewer_uri`, `verdict`, `rationale`, `evidence_uris`. Claims need `claimant_uri`, `statement`, `claim_type`, `metadata`. Overloading one table serves neither well.

4. **Query complexity.** "Find all reviews of this claim" becomes a self-join with predicate filtering instead of a simple FK lookup.

### Alternative: Extend `claim_state_log`

Another approach: enrich the existing audit log with reviewer identity and evidence references. Rejected because:

1. `claim_state_log` is append-only by design — updating a review verdict would require a new log entry, not an update, making "current verdict" queries complex.
2. Reviews have their own lifecycle (pending → approved/rejected) that doesn't map to state transitions.
3. On-chain attestation needs a stable record to hash, not a series of log entries.

---

*Last updated: March 10, 2026*
