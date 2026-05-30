# Translation memo: Regen AI Claims Engine ↔ Waka

*Follow-up to the Apr 22 Signal thread. Written for Austin, Sam, Shawn. Translation artifact, not a spec. No commitment implied. Read whenever useful.*

**Author:** Darren Zal · **Status:** draft

---

## Why this memo exists

After the Apr 22 message, I noticed I'd given you a lot of architecture and not much of a way in. This is the way in: a short translation of how I currently understand the overlap between what Austin has shared as Waka and what we've shipped (Regen AI Claims Engine), structured as **what I think I see + what I might be wrong about**.

Each section ends with 2–3 questions where I'd genuinely value your push-back. It's not a spec. I haven't pre-decided a schema, a payload format, or an integration plan. If we never go beyond async exchange of artifacts, that's still useful to me. If a call emerges organically out of this, even better — but no commitment implied either way.

(A longer technical compare/contrast essay sits in our private docs repo; happy to share on request, but the questions below stand on their own.)

---

## 1. Shared pipeline

Both systems do roughly the same shape: take raw evidence, run it through some witness/attestation process, anchor a portable artifact, and let downstream systems read it. The pipeline shape — *evidence → witness → anchor → portable proof* — appears to be shared.

Where it gets interesting is **what each step is allowed to be plural**. We've made each step linear and sequential (one verification level rolls forward into the next). Waka has made witness *compositional* — multiple witness records can stack on the same claim, and the claim doesn't collapse them into a single state.

**Questions:**
- Does the "same pipeline shape" frame ring true to you, or are we missing a step / muddling distinct concerns?
- Is there a stage where you've found compositionality essential — where any collapse to linearity would break the model?

---

## 2. Different centers of gravity

Reading Waka's docs and our own backlog side by side, I read the divergence as **where weight gets put**, not as fundamentally different things:

- **Regen AI** puts weight on *internal adoption*: three surfaces (browser/MCP/CLI) on one OAuth, narrow @regen.network identity, single anchoring substrate. Bias: get internal teammates using claims as part of their daily workflow this quarter.
- **Waka** puts weight on *institutional memory across protocols*: any wallet / any DID, multi-substrate anchoring (IPFS+Filecoin, AT Proto PDS, EAS, Regen `x/data`, Hypercerts), explicit 50-year durability framing. Bias: claims sovereignty across organizations and generations.

Both seem rational given their goals. They look complementary at the edges — narrow + frictionless inside an org composing with wide + sovereign across orgs.

On our end, the broader operating fabric we're starting to call **RegenOS** — with the Claims Engine sitting inside it as a *verification rail* — is internal framing for our own team. The closest shorthand I've used internally for what we should *learn from* Waka is **peer perception/witness infrastructure**. That's my shorthand, not a placement inside RegenOS. Flag if even the shorthand is the wrong shape.

**Questions:**
- Is "different centers of gravity" an accurate summary, or does it understate / overstate where you sit?
- Is the multi-substrate anchoring posture a hard architectural commitment for Waka, or is there an outer ring of substrates that are "supported but not load-bearing"?
- If we wanted to make our system meaningfully Waka-readable at the boundary without sacrificing internal-adoption ergonomics, where would you draw the seam?

---

## 3. Witness-record open questions

This is where I want to learn the most.

What I currently *think* about our linear lifecycle (`self_reported → peer_reviewed → verified → ledger_anchored`):

- It's easy for users to reason about and easy to filter on in SQL.
- It collapses *method* into *state*. A claim in `verified` doesn't self-describe how it got there — was it a peer review? a schema check? an AI classification? The level alone doesn't say.
- That collapse may be fine for our internal use (we know our methods), but it's lossy for any cross-org consumer who'd want to reason about *kind* of witness and *confidence per-method*.

What I think Waka does differently: witness records are *typed* and *stack*. A single claim accretes evidence of method, signer, confidence, signature — and nothing forces a reduction.

**Questions:**
- What's the minimum-viable witness record shape you'd consider stable enough for cross-system reuse? (If I wanted to read Waka witness records as input to a Regen consumer, which fields would be truly load-bearing vs. nice-to-have?)
- How do you handle the relationship between *individual witness records* and a *claim-level summary* a downstream system might need (e.g. "is this ready to issue against?")? Do you compute a roll-up, or always require consumers to interpret the bundle themselves?
- Is PGS (Participatory Guarantee System) quorum modeled as a single witness record with N signatories, or as N records with a separate quorum-resolution layer?

---

## 4. Regen `x/data` payload convention questions

We currently use Regen `x/data` for both anchoring and attestation — some paths are `MsgAnchor` (BLAKE2b-256 over a payload), and reviewer attestations are graph-native (`MsgAttest` / JSON-LD with URDNA2015 canonicalization, per the work we've been doing with Marie). Waka also lists Regen `x/data` as one of its anchoring substrates, so mechanically we're already in the same protocol graph.

The unresolved interop question is the **payload/profile convention behind the IRI** — what the bytes referenced by the anchor are actually shaped like, and which fields each side considers stable. Without a shared profile, neither side can parse the other's claims without bespoke work.

What I think we currently anchor: a mix of (a) hashed payloads tied to KOI RIDs, and (b) graph-native JSON-LD attestations. Probably none of this is yet documented as a profile.
What I think Waka currently anchors: something closer to ATProto Hypercerts lexicon shape (please correct if wrong).

**Questions:**
- Do you have a documented payload convention for what's at the other end of an `x/data` IRI when Waka anchors? If yes, where can I read it?
- If we each wrote a small profile note ("if you see one of *my* anchors, here's what the payload looks like and which fields are stable") and exchanged them, would that already be useful, or is there a more upstream convergence layer we should both adopt?
- Is ATProto Hypercerts lexicon stable enough yet that one could write to it and not have it shift, or is it still under enough development that we'd be following a moving target?

---

## 5. What Regen AI might offer Waka

Speaking only to surfaces — not asking you to adopt anything.

- **Three-surface OAuth**: one token unlocks a browser portal, an MCP server (Claude Code / Desktop / Cursor), and a CLI with a SKILL.md (zero upfront context cost, agent-native). The MCP and CLI shapes might be of interest if Waka eventually wants programmatic surface beyond the web app.
- **KOI integration**: claim → entity-link extraction across a knowledge graph, with semantic + fuzzy + exact entity resolution. Useful if Waka ever wants AI-assisted draft claims from documents.
- **Per-user audit trail**: every write attributed to a Google email in `claim_state_log`. Less philosophically rich than DID-signed records, but cleaner for some org-internal compliance / dispute-resolution use cases.
- **Regen-native MsgAnchor lifecycle**: hardened reconcile path, 202-pending semantics for indexing lag, proof-pack with IRI verification.

**Questions:**
- Of those, which (if any) are useful to Waka's roadmap? And which would feel like premature or unwelcome coupling?
- Is there appetite from Waka's side for an MCP/CLI surface, or is the web-first posture intentional and load-bearing?

---

## 6. What Waka might teach Regen AI

I think we have at least four learnable things in your direction:

1. **Compositional witness records as an extension layer** beneath our linear lifecycle — keep the level for user ergonomics, add witness records underneath for method/confidence preservation.
2. **Protocol pluralism at identity** — if we ever admit non-@regen.network stewards (community auditors, partner orgs), our narrow-OAuth posture breaks down. Waka's DID-method-pluralism architecture handles that natively.
3. **ATProto Hypercerts lexicon as an export shape** — even if our internal storage stays Regen-native, an export profile that maps our claims into Hypercerts activity shape would let downstream evaluation consumers read us without bespoke adapters.
4. **50-year durability framing** as a posture-shaping constraint — most of our infra decisions implicitly assume 5–10 year horizons. Naming the longer horizon out loud probably changes some of our anchoring + storage choices.

**Questions:**
- Are we reading Waka's contribution accurately? Anything we've named as learnable that you'd push back on as the wrong takeaway?
- If we did adopt one of these (say, witness records as an extension layer), is there a way to do it that keeps us legible to Waka without overcoupling — a "minimal adoption shape"?
- What's something Waka has tried-and-discarded that we should know about before walking the same path?

---

## How we might use this

Worth saying explicitly: **I'm not asking for a yes or a no on anything.** Three plausible next moves, unranked, and any of them work for me:

- Read-only: this sits in your inbox and informs your model of where Regen AI is. This is enough.
- Async push-back: someone replies in Signal with corrections or additions to the questions above. Also enough.
- A 30-min call walking through specific questions, if and when that's useful for you. Welcome but not necessary.

Whatever feels right for your bandwidth and process. No deadline, no commitment implied.

— Darren
