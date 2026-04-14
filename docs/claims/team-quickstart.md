# Claims Engine — Team Quickstart

> For Dave, Becca, and anyone on the team who wants to try the Claims Engine without setting up MCP or running curl commands.

**Live URL:** https://regen.gaiaai.xyz/claims

That's it — no login, no setup. Open the link in a browser and you're looking at the live system.

---

## What You're Looking At

The Claims Engine is our platform for recording, reviewing, and anchoring **impact claims** on the Regen Ledger. A claim is a structured assertion like "Organization X restored 500 hectares of mangrove habitat" — with evidence, peer attestations, and optional on-chain anchoring for verifiability.

**Current state:** V2 live on mainnet. Real claims from CEC, Blue Forest, and Zero Foodprint are in the system. A few are ledger-anchored; most are at earlier verification levels.

---

## The Four Tabs

When you open the page, you'll see four tabs along the top:

### 1. Dashboard
Counts and status. Shows total claims, how many are verified, how many are ledger-anchored. Good 10-second overview of system state.

### 2. Claims Browser
The main browsing surface. Filter by claimant (CEC, Blue Forest, ZFP), claim type (ecological / social / financial / governance), or verification level (self-reported → peer-reviewed → verified → ledger-anchored). Click any claim to open a detail panel with evidence, attestations, and (if anchored) a proof pack download.

### 3. AI Extraction
Paste a chunk of text — a grant report, a project update, a methodology doc — and the system will extract candidate claims from it. Useful for seeing how AI-drafted claims compare to human-written ones.

### 4. Create Claim
Manual claim entry. Pick a claimant, write the statement, choose type. This is where you'd go if you wanted to submit your own test claim.

---

## What to Try (30 minutes)

If you have a half hour to dogfood, do these in order:

1. **Dashboard** — read the totals. Does anything surprise you?
2. **Claims Browser** — open 3 claims across different orgs. Note what's clear and what's confusing in the detail panels.
3. **Filter by "ledger-anchored"** — open one of the anchored claims, download the proof pack, and look at what's inside the JSON bundle.
4. **AI Extraction** — paste a paragraph from a real project report you've seen. Note whether the extracted claims match what you'd have written.
5. **Create Claim** — create one test claim end-to-end. Pick any claimant; use "TEST — Dogfooding" in the statement so it's easy to find later.

Bonus: try to break it. Weird input, empty fields, massive text dumps in AI Extraction. We want to see where it falls over.

---

## What We Want Feedback On

- **Clarity.** Does the language make sense? Do you know what a "claim" is after using it for 10 minutes?
- **Flow.** Is the path from create → review → anchor obvious, or do you have to hunt?
- **Trust signals.** When you see "ledger-anchored," do you feel like you understand what that means? Does the proof pack help or confuse?
- **Gaps.** What's a feature or view you expected to find but couldn't?

---

## How to Report Issues or Feedback

Two options — pick whichever is less friction for you:

- **Slack:** drop in `#gaia-symbiocine-rnd` with a screenshot and one sentence of context
- **GitHub:** open an issue at https://github.com/gaiaaiagent/koi-processor/issues (tag `dogfooding`)

For anything urgent or confusing, just DM Darren.

---

## Known Gaps (so you don't report what we already know)

- Creating a claim from the UI does **not** currently let you attach evidence in the same step — evidence attachment is API-only for now
- On-chain anchoring from the UI requires the `claims-service` key; if you try to anchor and it fails silently, that's why
- The "AI Extraction" tab uses gpt-4o-mini; extractions are suggestions, not finished claims — you still have to edit/approve

---

*Last updated: 2026-04-14*
