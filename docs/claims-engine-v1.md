# Claims Engine — Current State & Roadmap

**Status:** V1 Complete + V2 Hardening Deployed (Mar 9, 2026)
**Environment:** Live on `regen-upgrade` testnet
**Dogfooding:** 49 claims across 3 organizations (CEC, Blue Forest, ZFP)

---

## What We Have Today

The Claims Engine is a working system for creating, verifying, and anchoring impact claims on the Regen Ledger. It runs as part of the KOI backend API (localhost:8351) and is accessible via REST endpoints and MCP tools in Claude Code.

### What a Claim Looks Like

A claim is a structured impact assertion — "CEC transitioned 22 farms to regenerative practices in Santa Barbara County (2021-2023)." Each claim has:

- **Claimant** — the entity making the assertion (e.g., Community Environmental Council), linked to the knowledge graph
- **Statement** — plain-language impact description
- **Type** — ecological, social, financial, or governance
- **Subject** — what the claim is about (a location, organization, project, etc.)
- **Evidence** — linked evidence entities that support the claim
- **Metadata** — structured fields like quantity, unit, dates, SDG tags, methodology

Claims are first-class entities in the knowledge graph with their own URIs and relationships.

### Verification State Machine

Every claim starts as `self_reported` and advances through review stages:

```
self_reported → peer_reviewed → verified → ledger_anchored (terminal)
     ↓              ↓
  withdrawn      withdrawn
  (terminal)     (terminal)
```

Each state transition is recorded in an append-only audit log (`claim_state_log`) with who did it and why.

### Current Dogfooding Results (Mar 9)

| Organization | Claims | Types | Status |
|-------------|--------|-------|--------|
| Community Environmental Council (CEC) | 8 | ecological, social | Some peer_reviewed |
| Blue Forest Conservation | 5 | ecological, financial | self_reported |
| Zero Foodprint (ZFP) | 3 | ecological | self_reported |
| **Total** | **16+** | — | Mixed states |

Plus additional claims from AI extraction testing, bringing the total to 49.

---

## How Ledger Anchoring Works

Claims at the `verified` state can be permanently anchored on the Regen Ledger blockchain. This creates a tamper-proof timestamp proving the claim data existed at a specific point in time.

### The Anchoring Flow

```
1. Claim reaches "verified" state
   ↓
2. POST /claims/{rid}/prepare-anchor
   → Serializes claim to canonical JSON (sorted keys, deterministic)
   → Computes BLAKE2b-256 content hash
   → Derives IRI (Internationalized Resource Identifier) via regen CLI
   → Returns: content_hash + predicted IRI (no broadcast yet)
   ↓
3. POST /claims/{rid}/anchor
   → Broadcasts MsgAnchor transaction to regen-upgrade testnet
   → Polls for on-chain confirmation (up to 30s)
   → On success: transitions claim to "ledger_anchored" state
   → On timeout: returns HTTP 202 with tx_hash for later reconciliation
   ↓
4. (If timeout) POST /claims/{rid}/reconcile
   → Checks on-chain status of the broadcast transaction
   → Finalizes the state transition when confirmed
```

### What Goes On-Chain

The blockchain transaction (`MsgAnchor`) contains:
- A `ContentHash.Raw` with the BLAKE2b-256 hash of the claim's canonical JSON serialization
- The signing account (`claims-service` key in the local regen keyring)

**What this proves:** The exact claim data (statement, claimant, evidence references, metadata) existed at the block timestamp. Anyone can recompute the hash from the claim data and verify it matches the on-chain anchor.

**What this does NOT prove (yet):** That anyone besides the service account reviewed or agreed with the claim. The reviewer identity (`actor` field) is currently a free-text string — "David Fortson" is just what Darren typed, not a cryptographic attestation from David. This is the gap the V2 attestation layer addresses.

### Technical Details

- **Chain:** `regen-upgrade` testnet (not mainnet)
- **Signing:** Local `regen` CLI binary with `--keyring-backend test` (no Python signing library needed)
- **Verification:** Any anchor can be independently verified via the REST API:
  `GET https://api-regen-upgrade.vitwit.com/regen/data/v2/anchor-by-iri/{iri}`
- **Ghost anchor protection (V2 Hardening):** If the broadcast succeeds but confirmation times out, the claim stays at `verified` (not falsely marked `ledger_anchored`). The `tx_hash` is preserved so `/reconcile` can finalize later.

### Environment Variables (in `personal.env`)

```
REGEN_CHAIN_ID=regen-upgrade
REGEN_RPC_URL=https://rpc-regen-upgrade.vitwit.com/
REGEN_REST_URL=https://api-regen-upgrade.vitwit.com/
REGEN_KEY_NAME=claims-service
```

No mnemonic or private key in env — key material lives in the regen keyring.

---

## How the Team Can Start Issuing Claims

### Option A: Via Claude Code (Recommended)

If you have the `personal-koi` MCP server configured, you can create and manage claims directly in conversation:

**1. Create a claim:**
```
Use the create_claim tool:
- claimant_uri: (the entity URI of the organization making the claim)
- statement: "Blue Forest Conservation restored 1,200 acres of forest in the Sierra Nevada (2022-2024)"
- claim_type: ecological
- about_uri: (entity URI of the location/project, if it exists in the knowledge graph)
- metadata: { "quantity": 1200, "unit": "acres", "start_date": "2022-01-01", "end_date": "2024-12-31" }
```

**2. Find existing entities first:**
```
Use the search tool to find the claimant's entity URI:
- Search for "Blue Forest Conservation" to get its fuseki_uri
- Search for the location/project to get its fuseki_uri for about_uri
```

**3. Link evidence:**
```
Use the link_evidence tool:
- claim RID (returned from create_claim)
- evidence_uri: (entity URI of an Evidence entity in the knowledge graph)
```

**4. Advance verification:**
```
Use the verify_claim tool:
- claim RID
- new_level: "peer_reviewed"
- actor: "Your Name"
- reason: "Reviewed annual report and satellite imagery"
```

### Option B: Via REST API (curl)

**1. Check the server is running:**
```bash
curl http://localhost:8351/health
```

**2. Find a claimant entity:**
```bash
# Search for the organization
curl "http://localhost:8351/search?q=Blue+Forest+Conservation&limit=3"
```

**3. Create a claim:**
```bash
curl -X POST http://localhost:8351/claims/ \
  -H "Content-Type: application/json" \
  -d '{
    "claimant_uri": "urn:koi:entity:blue-forest-conservation-...",
    "statement": "Restored 1,200 acres of forest in the Sierra Nevada (2022-2024)",
    "claim_type": "ecological",
    "about_uri": "urn:koi:entity:sierra-nevada-...",
    "metadata": {
      "quantity": 1200,
      "unit": "acres",
      "start_date": "2022-01-01",
      "end_date": "2024-12-31",
      "sdg_tags": ["SDG15"],
      "methodology": "Annual report + satellite imagery"
    }
  }'
```

**4. List existing claims:**
```bash
# All claims
curl "http://localhost:8351/claims/"

# Filter by claimant
curl "http://localhost:8351/claims/?claimant_uri=urn:koi:entity:blue-forest-..."

# Filter by type
curl "http://localhost:8351/claims/?claim_type=ecological"

# Filter by verification state
curl "http://localhost:8351/claims/?verification=self_reported"
```

**5. View a specific claim with evidence:**
```bash
curl "http://localhost:8351/claims/orn:koi-net.claim:abc123..."
```

**6. Link evidence:**
```bash
curl -X POST "http://localhost:8351/claims/orn:koi-net.claim:abc123.../evidence" \
  -H "Content-Type: application/json" \
  -d '{"evidence_uri": "urn:koi:entity:some-evidence-doc-...", "actor": "Your Name"}'
```

**7. Advance verification:**
```bash
curl -X PATCH "http://localhost:8351/claims/orn:koi-net.claim:abc123.../verify" \
  -H "Content-Type: application/json" \
  -d '{"new_level": "peer_reviewed", "actor": "Dave Fortson", "reason": "Reviewed supporting documents"}'
```

### Option C: AI Extraction from Documents

The engine can automatically extract claims from document text using an LLM:

```bash
curl -X POST http://localhost:8351/claims/extract \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Community Environmental Council helped 22 farms transition to regenerative practices...",
    "source_document": "CEC-annual-report-2023.pdf",
    "claimant_uri": "urn:koi:entity:cec-..."
  }'
```

This returns a list of extracted claims with `ai_confidence` scores. Each extracted claim is automatically created in the system.

### What You'll Need

1. **KOI backend running** — `~/.config/personal-koi/start.sh` (or confirm with `curl http://localhost:8351/health`)
2. **Claimant entity must exist** in the knowledge graph — search first to find the URI, or create via the entity resolution pipeline
3. **Evidence entities** (optional but recommended) — documents, reports, or data sources already indexed in KOI
4. **For anchoring:** `regen` CLI binary installed and `claims-service` key in the keyring

### Tips for Good Claims

- **Be specific:** "Restored 1,200 acres" is better than "Did restoration work"
- **Include dates:** Use `start_date` and `end_date` in metadata for temporal bounds
- **Link evidence:** Claims without evidence are just assertions — link supporting documents
- **Use `about_uri`:** Connecting a claim to a specific place or project enables graph queries like "all claims about the Sierra Nevada"
- **SDG tags:** Adding `sdg_tags` in metadata enables filtering by Sustainable Development Goals

---

## API Reference

| Method | Path | Purpose |
|--------|------|---------|
| POST | /claims/ | Create claim (entity reg + graph edges + SQL) |
| GET | /claims/{rid} | Get claim with linked evidence |
| GET | /claims/ | List/search with filters |
| PATCH | /claims/{rid}/verify | Advance verification level |
| POST | /claims/{rid}/evidence | Attach evidence entity |
| GET | /claims/{rid}/history | Verification audit log |
| POST | /claims/extract | AI extraction from document text |
| POST | /claims/{rid}/prepare-anchor | Compute content hash + predict IRI |
| POST | /claims/{rid}/anchor | Anchor verified claim on Regen Ledger testnet |
| POST | /claims/{rid}/reconcile | Check on-chain status of timed-out broadcast |

### MCP Tools (personal-koi-mcp)

| Tool | Backend Call |
|------|-------------|
| create_claim | POST /claims/ |
| search_claims | GET /claims/ |
| get_claim | GET /claims/{rid} |
| verify_claim | PATCH /claims/{rid}/verify |
| extract_claims | POST /claims/extract |
| link_evidence | POST /claims/{rid}/evidence |
| anchor_claim | POST /claims/{rid}/anchor |
| reconcile_claim | POST /claims/{rid}/reconcile |

---

## Schema Design

### Core Table: `claims`

| Column | Type | Purpose |
|--------|------|---------|
| claim_rid | TEXT UNIQUE | Content-addressable RID: `orn:koi-net.claim:<hash>` |
| entity_uri | TEXT | entity_registry.fuseki_uri (claim as graph entity) |
| claimant_uri | TEXT NOT NULL | Who makes the claim (FK to entity_registry) |
| statement | TEXT NOT NULL | Plain-language impact assertion |
| claim_type | TEXT | ecological, social, financial, governance |
| verification | TEXT | self_reported → peer_reviewed → verified → ledger_anchored |
| source_document | TEXT | Provenance: document RID or path |
| ai_confidence | FLOAT | NULL if manually created |
| content_hash | TEXT | BLAKE2b-256 for ledger anchoring |
| tx_hash | TEXT | Transaction hash from broadcast (for reconciliation) |
| supersedes_rid | TEXT | Previous version (append-only versioning) |
| metadata | JSONB | Extensible: quantity, dates, SDGs, methodology, etc. |
| created_by | TEXT | Who entered the claim into the system (free-text) |

### RID Strategy

Content-addressable, append-only:
- `claim_rid = orn:koi-net.claim:{sha256(canonical_json(about_uri, claimant, claim_type, metadata, statement))[:16]}`
- Same content → same RID → idempotent
- Any field change → new RID → new row + `supersedes_rid` link

### Graph Predicates

| Predicate | Subject → Object | Purpose |
|-----------|-----------------|---------|
| makes_claim | Person/Org → Claim | Claimant relationship |
| evidences_claim | Evidence → Claim | Evidence attachment |
| supersedes_claim | Claim → Claim | Version chain |
| about | Claim → Location/Org/etc | Claim subject |

---

## Roadmap: What's Coming

### V2: Attestation Layer (Design Complete — see [V2 Design Doc](claims-engine-v2-attestations.md))

The key gap in V1: **the system records who reviewed a claim, but doesn't prove it.** The `actor` field on verification transitions is free-text — "David Fortson" is just a string someone typed, not an identity-bound attestation.

V2 introduces:

1. **Four-role identity model** — Claimant (strong, exists today), Subject (exists today), Operator (who entered the claim — currently hidden `created_by` field, V2 promotes to FK), Reviewer (who evaluated it — V2 creates first-class attestation records with FK to entity_registry)

2. **`claim_attestations` table** — First-class review records with verdict (approved/rejected/needs_info), rationale, evidence references, and their own on-chain anchoring capability

3. **Attestation policy** — State transitions become policy-gated: `peer_reviewed` requires ≥1 approved attestation from a non-claimant reviewer; `verified` requires ≥2 approved attestations

4. **`MsgAttest` on-chain** — V1 uses `MsgAnchor` (proves data existed). V2 adds `MsgAttest` with `ContentHash.Graph` (proves a reviewer attested to data veracity). Different semantic: timestamping vs. judgment.

### Implementation Phases

| Phase | Scope | Status |
|-------|-------|--------|
| **Near-term** | Surface `created_by` in API responses, add `operator_uri` column, create `claim_attestations` table + CRUD endpoints | Design complete |
| **Mid-term** | Attestation policy preconditions on verify, graph edges for attestations, MCP tools | Designed |
| **Longer-term** | `MsgAttest` on-chain, canonical JSON-LD hashing, per-reviewer key delegation via `cosmos.authz` | Designed |

### Beyond V2

- Dashboard UI for claim/attestation workflows
- COMET Planner PDF parsing for automated claim extraction
- Credit issuance integration (x/ecocredit)
- Multi-tenant / multi-org access control
- Full RDF Dataset Canonicalization (RDFC 1.0)

---

## Build History

- [x] Phase 0: Implementation doc
- [x] Phase 1: Schema + migration (064_claims_engine.sql)
- [x] Phase 1b: Router + graph integration (claims_router.py)
- [x] Phase 2: Claim extraction pipeline (claim_extractor.py)
- [x] Phase 3: MCP tools (personal-koi-mcp)
- [x] Phase 4: Ledger anchoring — live testnet via regen CLI (ledger_anchor.py)
- [x] Phase 5: Testing (46 smoke tests + 16 pytest passing)
- [x] Phase 6: V2 Hardening — ghost anchor fix, reconcile endpoint, 202 pending responses
- [x] Phase 7: V2 Design Doc — attestation layer architecture

## Key Files

| File | Purpose |
|------|---------|
| `api/routers/claims_router.py` | All claim endpoints + Pydantic models |
| `api/ledger_anchor.py` | Content hashing, IRI derivation, MsgAnchor broadcast |
| `migrations/064_claims_engine.sql` | Core schema (claims + claim_state_log tables) |
| `migrations/065_claims_tx_hash.sql` | tx_hash column for reconciliation |
| `tests/test_claims_reconcile.py` | 16 pytest tests (in-process ASGI) |
| `scripts/test_claims_api.py` | 20 HTTP smoke tests |
| `docs/claims-engine-v2-attestations.md` | V2 attestation layer design |

---

*Last updated: March 10, 2026*
