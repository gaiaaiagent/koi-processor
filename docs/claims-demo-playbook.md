# Claims Engine Demo — Playbook

**URL:** https://regen.gaiaai.xyz/claims
**Last updated:** 2026-03-10

---

## Preflight (5 min before demo)

- [ ] Open https://regen.gaiaai.xyz/claims — confirm **"Connected"** badge (top-right, green dot)
- [ ] Dashboard shows **3 Total Claims** with By Type breakdown (Financial, Ecological, Governance)
- [ ] Click **Claims Browser** tab — 3 rows visible
- [ ] Have this playbook open in a second tab

**If "Connected" is missing or dashboard is empty:**
```bash
ssh darren@202.61.196.119 "nohup /opt/projects/koi-processor/start-claims-api.sh > /tmp/claims-api.log 2>&1 &"
# Wait 10 seconds, reload the page
```

---

## Demo Flow (~10 min)

### 1. Dashboard (2 min)

**Story:** "This is the Claims Engine — it tracks impact claims from organizations, takes them through a verification pipeline, and can anchor them on the Regen Ledger blockchain."

- Point out the **4 summary cards**: Total Claims, Verified, AI-Extracted, On-Chain
- Show **By Type** breakdown — Financial / Ecological / Governance
- Show **By Organization** — 3 orgs with 1 claim each
- Show **Claims Pipeline** visualization: Self-Reported → Peer Reviewed → Verified → On-Chain
- "Right now all 3 are self-reported. The pipeline shows the verification journey each claim takes."

### 2. Claims Browser (3 min)

**Story:** "The browser lets you explore, filter, and drill into claim detail."

- Show the table with all 3 claims
- **Filter by type:** Select "Ecological" from the Type dropdown — only CEC's claim appears
- Reset to "All Types"
- **Click a claim row** to open the slide-over detail panel:
  - Full statement text
  - Organization URI
  - Metadata (structured fields: quantity, unit, year)
  - State history (shows initial `self_reported` entry)
  - Claim RID (unique content-addressed identifier)
- Close the panel

### 3. AI Extraction (3 min) — the wow moment

**Story:** "The engine can extract structured claims from unstructured text using Claude."

- Click **AI Extraction** tab
- Click **"Load Example"** (pre-fills sample text), OR paste:

> Blue Forest Conservation restored 2,400 acres of degraded forest in the North Yuba watershed. Their work sequesters an estimated 18,000 tons of CO2 annually. The project employed 45 local workers during the 2023 restoration season.

- Click **"Extract Claims"** — wait 3-5 seconds for Claude to respond
- Show the extracted candidates:
  - Confidence scores
  - Auto-detected claim types
  - Structured metadata (quantities, units, locations)
- "Each candidate can be reviewed and accepted, or discarded. Nothing enters the system without human approval."

> **Backup:** If extraction hangs or errors, say: "The AI extraction calls Claude's API — sometimes there's latency. Let me show you what extraction produces." Switch to Claims Browser and point to the seeded claims as examples of structured output.

### 4. Create Claim (2 min) — optional

**Story:** "You can also create claims manually, with entity resolution matching organizations from the knowledge graph."

- Click **Create Claim** tab
- Select an organization from the **Claimant Organization** dropdown (e.g., "Community Environmental Council")
- Statement: "CEC reduced methane emissions by 800 tons CO2e through composting in Q4 2025"
- Type: "ecological"
- Click **Create Claim**
- Switch to Dashboard — count should now be 4

> **Backup:** If typeahead doesn't load, skip this section: "Manual creation uses entity resolution to match against 29,000+ entities — I'll show that flow separately."

---

## Key Talking Points

| Concept | Detail |
|---------|--------|
| **Verification pipeline** | Self-reported → peer reviewed → verified → ledger anchored. Each transition logged with actor + reason. |
| **Provenance** | Every claim tracks source document, AI confidence, and content hash. |
| **Blockchain anchoring** | Verified claims anchor on-chain via Regen Ledger MsgAnchor, producing an immutable IRI. |
| **Entity resolution** | Orgs matched against 29K+ entities using fuzzy (Jaro-Winkler) + semantic (embeddings) matching. |
| **Extensible metadata** | JSONB field supports any structured data — quantities, SDG tags, locations, methodologies. |

## Likely Questions

| Question | Answer |
|----------|--------|
| How does peer review work? | V1 is manual state transition via API. V2 will add a review UI — the reconcile endpoint is already built. |
| What if extraction gets it wrong? | Candidates are previewed before creation. Confidence scores flag uncertainty. Nothing auto-commits. |
| Is this connected to the real ledger? | Anchoring code tested on Regen testnet. Mainnet anchoring needs a funded account. |
| How do you clean up demo data? | All demo claims tagged `source_document LIKE 'claims-demo-portal:%'`. One SQL delete cleans up. |
| Can this handle documents at scale? | Extraction is per-document. Batch processing would be a sensor integration (same pattern as GitHub/Discourse sensors). |
| What's the data model? | See [claims-engine-v1.md](claims-engine-v1.md) for full schema and API reference. |

---

## Emergency Fallback

If the production server is unreachable:
1. **Local demo:** `cd ~/projects/RegenAI/koi-server && ~/.config/personal-koi/start.sh` → open `http://localhost:8351/demo`
2. **Architecture walkthrough:** Use [claims-engine-v1.md](claims-engine-v1.md) as reference
