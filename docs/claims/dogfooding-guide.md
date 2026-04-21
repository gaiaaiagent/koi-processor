# Claims Engine — Team Dogfooding Guide

> Quick-start for the team to try out the Claims Engine via MCP tools or API.

---

## What Is It?

The Claims Engine lets you create, review, and anchor **impact claims** on the Regen Ledger. A claim is a structured assertion like "Organization X restored 500 hectares of mangrove habitat" — with evidence links, peer attestations, and on-chain anchoring for verifiability.

**Current state**: V2 Phase 2 complete. 50+ claims, 3 organizations (CEC, Blue Forest, ZFP), 2 claims anchored on mainnet (`regen-1`).

---

## Via Claude Code MCP (Easiest)

Install the dedicated **regen-claims-mcp** plugin — scoped to the Claims Engine surface only (no personal workflow dependencies):

```bash
git clone https://github.com/gaiaaiagent/regen-claims-mcp.git
cd regen-claims-mcp && npm install && npm run build
```

Add to your Claude Code MCP config (ask Darren or Gregory for the team basic-auth creds):

```json
{
  "mcpServers": {
    "regen-claims": {
      "command": "node",
      "args": ["/absolute/path/to/regen-claims-mcp/dist/index.js"],
      "env": {
        "KOI_API_ENDPOINT": "https://regen.gaiaai.xyz",
        "KOI_BASIC_AUTH_USER": "<team-user>",
        "KOI_BASIC_AUTH_PASS": "<team-pass>"
      }
    }
  }
}
```

Once configured, the following tools are available from any Claude Code session:

### Create a claim
```
Use the create_claim tool:
- claimant_uri: (your entity URI from entity_registry)
- statement: "Plain language impact assertion"
- claim_type: ecological | social | financial | governance
- Optional: about_uri, operator_uri, metadata (JSON with quantity, unit, dates, etc.)
```

### Search claims
```
Use the search_claims tool:
- query: keyword search across statements
- claimant_uri: filter by claimant
- claim_type: filter by type
- verification: filter by level (self_reported, peer_reviewed, verified, ledger_anchored)
```

### Review/attest a claim
```
Use the create_attestation tool:
- claim_rid: the claim's RID (orn:koi-net.claim:...)
- reviewer_uri: your entity URI
- verdict: approved | rejected | needs_info
- rationale: why you're approving/rejecting
- evidence_uris: optional supporting evidence
```

### Anchor on-chain
```
Use the anchor_claim tool:
- claim_rid: the claim's RID
(Requires regen CLI + claims-service key configured)
```

### Download proof pack
```
Use the get_proof_pack tool:
- claim_rid: the claim's RID (must be ledger_anchored)
Returns: JSON verification bundle with content hash, ledger IRI, tx hash
```

---

## Via REST API

Base URL: `http://localhost:8351` (local koi-server)

### List claims
```bash
curl -sL "http://localhost:8351/claims/"
```

### Create a claim
```bash
curl -X POST "http://localhost:8351/claims/" \
  -H "Content-Type: application/json" \
  -d '{
    "claimant_uri": "orn:personal-koi.entity:organization-...",
    "statement": "Your impact claim here",
    "claim_type": "ecological"
  }'
```

### Attest to a claim
```bash
curl -X POST "http://localhost:8351/claims/{rid}/attestations" \
  -H "Content-Type: application/json" \
  -d '{
    "reviewer_uri": "orn:personal-koi.entity:person-...",
    "verdict": "approved",
    "rationale": "Verified against field reports"
  }'
```

### Get proof pack
```bash
curl -sL "http://localhost:8351/claims/{rid}/proof-pack"
```

---

## Key Concepts

| Concept | Description |
|---------|-------------|
| **Claim RID** | Content-addressable ID: `orn:koi-net.claim:<blake2b-hash>` |
| **Verification levels** | `self_reported` → `peer_reviewed` (≥1 attestation) → `verified` (≥2) → `ledger_anchored` |
| **Self-attestation guard** | Reviewer cannot be the claimant |
| **Proof pack** | Downloadable verification bundle with hash + ledger proof |

---

## Full API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/claims/` | GET | List/search claims |
| `/claims/` | POST | Create new claim |
| `/claims/{rid}` | GET | Get claim with evidence |
| `/claims/{rid}/verify` | PATCH | Advance verification level |
| `/claims/{rid}/evidence` | POST | Attach evidence entity |
| `/claims/{rid}/history` | GET | Verification audit log |
| `/claims/extract` | POST | AI extraction from text |
| `/claims/{rid}/prepare-anchor` | POST | Compute content hash |
| `/claims/{rid}/anchor` | POST | Broadcast to Regen Ledger |
| `/claims/{rid}/reconcile` | POST | Check on-chain tx status |
| `/claims/{rid}/proof-pack` | GET | Download verification bundle |
| `/claims/{rid}/attestations` | POST | Create/update attestation |
| `/claims/{rid}/attestations` | GET | List attestations |
| `/claims/{rid}/attestations/{att_rid}` | GET | Get single attestation |
| `/claims/{rid}/attestations/{att_rid}/anchor` | POST | Anchor attestation on-chain |
| `/claims/{rid}/attestations/{att_rid}/reconcile` | POST | Check attestation tx status |

---

*Last updated: March 11, 2026*
