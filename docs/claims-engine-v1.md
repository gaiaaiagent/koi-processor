# Claims Engine V1 — Implementation Reference

**Status:** In Progress
**Target:** Internal dogfooding by Mar 10-11, 2026

## Scope

Lightweight, ledger-anchored impact claim system. V1 covers:
- Lean core schema with extensible JSONB metadata
- CRUD + verification state machine
- Entity graph integration (claims as first-class entities)
- AI-powered claim extraction from documents
- Content hashing for ledger anchoring (broadcast stubbed)
- MCP tools for Claude Code integration

## Schema Design

Three authoritative sources:
1. **Smith/Bennetts** — Claims are "self-stable assertions" with structure (evidence + verification)
2. **Impact Registry using DLT** — `impactClaim = {claimStatement, claimEvidence[], claimAuthor}`
3. **Dave's "atomic primitive"** — "Name it, vault it, map it, value it"

### Core Table: `claims`

| Column | Type | Purpose |
|--------|------|---------|
| claim_rid | TEXT UNIQUE | Content-addressable RID: `orn:koi-net.claim:<hash>` |
| entity_uri | TEXT | entity_registry.fuseki_uri (claim as graph entity) |
| claimant_uri | TEXT NOT NULL | Who makes the claim |
| statement | TEXT NOT NULL | Plain-language impact assertion |
| claim_type | TEXT | ecological, social, financial, governance |
| verification | TEXT | self_reported → peer_reviewed → verified → ledger_anchored |
| source_document | TEXT | Provenance: document RID or path |
| ai_confidence | FLOAT | NULL if manually created |
| content_hash | TEXT | BLAKE2b-256 for ledger anchoring |
| supersedes_rid | TEXT | Previous version (append-only versioning) |
| metadata | JSONB | Extensible: quantity, dates, SDGs, methodology, etc. |

### Predicates

| Predicate | Subject → Object | Purpose |
|-----------|-----------------|---------|
| makes_claim | Person/Org → Claim | Claimant relationship |
| evidences_claim | Evidence → Claim | Evidence attachment |
| supersedes_claim | Claim → Claim | Version chain |
| about | Claim → Location/Org/etc | Claim subject (reuses existing) |

## API Contract

**Base:** `POST/GET /claims/...` on personal KOI API (localhost:8351)

| Method | Path | Purpose |
|--------|------|---------|
| POST | /claims/ | Create claim (entity reg + graph edges + SQL) |
| GET | /claims/{rid} | Get claim with linked evidence |
| GET | /claims/ | List/search with filters |
| PATCH | /claims/{rid}/verify | Advance verification level |
| POST | /claims/{rid}/evidence | Attach evidence entity |
| GET | /claims/{rid}/history | Verification audit log |
| POST | /claims/extract | AI extraction from document text |
| POST | /claims/{rid}/prepare-anchor | Compute content hash (broadcast stubbed) |

## Verification State Machine

```
self_reported → peer_reviewed → verified → ledger_anchored (terminal)
     ↓              ↓
  withdrawn      withdrawn
  (terminal)     (terminal)
```

## RID Strategy

Content-addressable, append-only:
- `claim_rid = orn:koi-net.claim:{sha256(canonical_json(about_uri, claimant, claim_type, metadata, statement))[:16]}`
- `about_uri` is part of the identity — same statement about different entities produces distinct RIDs
- `about_uri` is validated (must exist in entity_registry) before entering the hash
- Same content → same RID → idempotent (concurrent duplicates caught via unique constraint)
- Any field change → new RID → new row + `supersedes_rid` link

## MCP Tools (personal-koi-mcp)

| Tool | Backend Call |
|------|-------------|
| create_claim | POST /claims/ |
| search_claims | GET /claims/ |
| get_claim | GET /claims/{rid} |
| verify_claim | PATCH /claims/{rid}/verify |
| extract_claims | POST /claims/extract |
| link_evidence | POST /claims/{rid}/evidence |

## Build Status

- [x] Phase 0: Implementation doc
- [x] Phase 1: Schema + migration (064_claims_engine.sql)
- [x] Phase 1b: Router + graph integration (claims_router.py)
- [x] Phase 2: Claim extraction pipeline (claim_extractor.py)
- [x] Phase 3: MCP tools (personal-koi-mcp)
- [x] Phase 4: Ledger anchoring stub (ledger_anchor.py)
- [ ] Phase 5: Testing + dogfooding

## What V1 Does NOT Cover

- User wallet integration (V2: MsgAttest multi-party)
- Dashboard UI
- COMET Planner PDF parsing
- Credit issuance (x/ecocredit)
- Multi-tenant / multi-org
- URDNA2015 graph canonicalization
- Mixed evidence types (document RIDs, external URLs)
