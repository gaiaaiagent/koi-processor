# Team Commitment Stewardship — A koi-repo response on tasks as witnessed commitments

| **Field** | **Value** |
| --- | --- |
| Author | Darren Zal ([darren@regen.network](mailto:darren@regen.network)) |
| Date | 2026-04-30 |
| Document type | koi-repo response (companion to `regenos-charter-response-darren.md`) |
| Status | Draft v1 — peer-reviewed via 3-round Codex `/review-plan` (gpt-5.5, x-high), ready for circulation |
| Responding to | [RegenOS Architecture Decisions Charter v1.0 (Open Questions)](https://www.notion.so/regennetwork/RegenOS-Architecture-Decisions-Charter-v1-0-Open-Questions-34f25b77eda18131b5f8eacae84f2024) (Gregory Landua, 2026-04-25, last edited 2026-04-28) |
| Suggested permanent location | `~/projects/RegenAI/koi-processor/docs/claims/team-commitment-stewardship.md` |
| Companion | [`regenos-charter-response-darren.md`](./regenos-charter-response-darren.md) — initial koi-repo response to the same Charter |
| Audience | Gregory Landua (primary); RND PBC + close collaborators (Marie or equivalent); Regen AI core team |

---

## 0. Frame

This memo is a koi-repo response companion to the *RegenOS Architecture Decisions Charter v1.0 (Open Questions)* (Gregory Landua, 2026-04-25, last edited 2026-04-28) and to my own initial response (`regenos-charter-response-darren.md`, 2026-04-30). It does not propose a new charter decision; it threads through three existing ones — **D1** (compilation agent locus, ratifying the operational hybrid), **D5** (KOI as federation rail), and **D6** (claims-engine + provenance) — and proposes one architectural extension that composes across all three: **promoting tasks to first-class witnessed-commitment objects with provenance, and adding a KOI-mediated team task pool with consent-tier discipline applied inward.**

The memo's load-bearing claim is that *team attention is the modal commons of organizational coordination* — borrowing Will Ruddick's framing of care as the modal commons (cf. *Beyond the Claims Engine* §1, citing Ruddick's *Journal of a Grassroots Economist*) — and that the existing meeting-processing pipeline is already operating an implicit, single-node version of this commons. Naming it explicitly, with provenance and consent disciplines, is the small step that makes it sustainable as the team grows beyond one node.

**Topology in scope.** Three tiers (per `koi-processor/CLAUDE.md` deploy table):

| Tier | Where | Trust | What lives here |
|---|---|---|---|
| **Sovereign (per-member personal-koi)** | Each teammate's local backend (port 8351, `regen-prod` branch); each teammate runs their own | Self-sovereign | Private vault state, transcripts, default `consent_tier: private` task records |
| **Shared (Regen AI production backend)** | `darren@202.61.196.119`, `stable` branch, EU-jurisdictioned (Netcup, Nürnberg) | Multi-member federation | Team-tier task proposals, disposition pass results, attested commitments |
| **Federated (NUC peer of laptop)** | Darren's NUC, Dobby `deploy.sh` rsync from laptop working tree | Same trust as laptop personal-koi | Backup peer / always-on for Darren only |

Cross-member task submission flows: per-member personal-koi (sovereign) → opt-in submission to Regen AI production backend (shared) → other members poll and review. The shared backend is *never* the source of authority for any one member's own tasks — it's the meeting-point for cross-member coordination, no more.

## 1. Problem: task creation provenance is flat, and the task pool is single-node

The 2026-04-30 Regen AI standup processed cleanly through the existing pipeline (`meeting-notes` → `process-note --apply --propagate` → `meeting-tasks --apply --backend`). 9 task files written; 9 backend records created; 6 prior-meeting tasks correctly aged out. The pipeline works.

But two structural gaps surface on review:

**Gap 1 — Task provenance is flat.** Some tasks were *explicitly named* in the meeting ("Marie: set up the Regen OS software tool registry repository on Regen Network"). Others were *agent-inferred* from transcript context ("Darren Zal: Draft a one- or two-page memo for Conservation International... targeting completion by Monday or Tuesday" — the assistant resolved the date, inferred the priority). Others were *cross-meeting context-derived* (the body annotation pointing at next Tuesday came from prior standup state). The system writes them to `Tasks/*.md` and `task_registry` indistinguishably. Once written, you can't tell which were explicit-in-meeting commitments vs. agent inferences vs. context-derived. This matters because the *speech-act class* of a task is operationally significant: an explicit-in-meeting commitment carries different binding force than an agent inference, and a teammate reviewing a task should be able to tell at a glance which they're approving.

**Gap 2 — Task creation is single-node.** Each teammate's local meeting-processing pipeline produces tasks for self *and* tasks for others, but those for-others tasks live only in the local-node's vault until manually shared (Telegram, email, or "I'll send it to you"). There is no team task pool, no dedup pass, no "I caught a task you missed" surface, no way to *negotiate* the canonical task list across the team's distributed sense-making. The pipeline scales to N=1 cleanly; it does not scale to N=2 without manual coordination.

Both gaps are addressable as a single architectural extension that composes with what's already shipped.

## 2. The speech-act triad applied to tasks

The "Beyond the Claims Engine" essay (Darren, 2026-04-23, §1) names three speech-acts the existing claims-engine framing collapses:

> **Witness** — *"this happened, and I saw it."* Past-oriented. Records what was.
> **Attestation** — *"this is so, and I vouch for it."* Present-oriented. Vouches for what is.
> **Promise** (or *commitment*) — *"this could be, and I pledge toward it."* Future-oriented. Pledges what could become.

A task lifecycle maps onto this triad cleanly:

| Phase | Speech-act | What happens |
|---|---|---|
| **Spawn** | Witness | A meeting transcript / chat message / agent inference produces a candidate task. Provenance: source RID + transcript span + extractor agent ID + explicit-vs-inferred flag + confidence. |
| **Triage** | Attestation | Team-member(s) review the candidate, vouch for it ("this is real, this should exist"), and resolve owner / due date / scope. |
| **Acceptance** | Promise | Owner accepts the assignment ("I pledge to do this by Y"). The task transitions from `proposed` to `open`. |
| **Stewardship** | (cycle) | Care → commitment → coordination → learning → wiser care, per Ruddick. Status updates, blockedBy/blocks links, periodic re-triage. |
| **Completion / cancellation / supersession** | Closure | Done, cancelled-as-superseded (the disposition we used today for 4 stale tasks), or rolled into a successor task. |

The current pipeline conflates Spawn and Acceptance: a task written to `Tasks/*.md` with `status: inbox` carries both the witness-claim ("the agent saw this in the transcript") and an implicit promise ("Darren is the owner") — but no attestation step where Darren actually vouched for it. In single-node operation that's fine, because Darren is the only attester; in multi-node the ambiguity becomes a consent failure.

The temporal-register question from "Beyond the Claims Engine" §2 ("given this is true, what do we do?" / "what was the action, if this turned out to be true?" / "what should we do to make this true?") also applies. Most existing meeting-task systems (including ours) optimize for the first question — they capture *present-register* action items. They occasionally retrospect (second question, e.g. our cancel-as-superseded pass today). They rarely capture **prospective commitments** (third question — "we want X to exist, and here's a pledge toward bringing it into being") as first-class objects. The team-task-pool is the natural surface where prospective commitments could live.

## 3. Schema sketch: provenance and consent on `task_registry`

Two non-breaking additions to the existing `task_registry` table (`koi-processor/migrations/056_task_registry.sql`). **Applies symmetrically to both tiers** — per-member personal-koi (regen-prod branch) and Regen AI production backend (stable branch). Migration ships against `regen-prod` first, then promotes via the cherry-pick → `stable` flow per the standard koi-processor branch topology.

**Schema finding (verified 2026-04-30 via `psql personal_koi -c "\d task_registry"`).** The current schema has 25 columns including a generic `metadata jsonb DEFAULT '{}'::jsonb` field. Two viable implementation shapes:

- **Shape A — dedicated columns** (sketch below): `provenance JSONB` + `consent_tier TEXT` as named first-class columns. Pros: semantically explicit, indexable independently, query-legible. Cons: schema migration with two new columns + two new indexes.
- **Shape B — namespace under existing `metadata`**: store as `metadata.provenance` and `metadata.consent_tier` keys; check constraint enforces tier vocabulary via JSON path. Pros: zero column adds, only a `CHECK` constraint and partial GIN index. Cons: less legible in `\d` output; more complex query patterns; can't NOT-NULL the consent_tier without backfill.

Recommend Shape A for legibility; the implementer picks. Both compose with KOI's existing consent_tier propagation pattern (BKC: `community_only` / `private` entities are excluded from federation, no `koi_rid` issued).

```sql
-- Shape A (Sketch, not migration.)
ALTER TABLE task_registry
  ADD COLUMN provenance JSONB,
  ADD COLUMN consent_tier TEXT NOT NULL DEFAULT 'private'
    CHECK (consent_tier IN ('private', 'team', 'org', 'public'));

-- Existing rows default to private; backward compatible.
CREATE INDEX idx_task_registry_consent_tier ON task_registry(consent_tier);
CREATE INDEX idx_task_registry_provenance_gin ON task_registry USING GIN (provenance);
```

`provenance` JSONB shape:

```json
{
  "speech_act": "witness | attestation | promise",
  "source_rid": "vault:Meetings/Regen AI/2026-04-30 Regen AI Meeting",
  "source_span": { "type": "transcript", "start_offset": 1234, "end_offset": 1456 },
  "extractor": {
    "agent_id": "claude-opus-4-7@darren-laptop",
    "session_id": "uuid-...",
    "skill_version": "meeting-tasks@2026-04-30"
  },
  "explicit": false,
  "confidence": 0.85,
  "inference_chain": [
    "Body annotation 'targeting completion by Monday or Tuesday' resolved to 2026-05-04",
    "Owner Darren Zal pre-resolved from frontmatter attendees array"
  ],
  "attestations": [
    {
      "by": "[[People/Darren Zal]]",
      "at": "2026-04-30T17:45:00-07:00",
      "action": "accept",
      "via": "process-note --apply"
    }
  ]
}
```

The `attestations` array is append-only; each attestation event (accept, reject, redirect, supersede) appends a record. The `consent_tier` column governs federation visibility; `private` (default) never leaves the local node.

Three things this schema buys:
1. **Auditability** — for any task, you can trace back to the witness event and the attestation chain. This is what makes the pipeline *legible* to a Marie-tier reader and to the team itself.
2. **Anchorability** — the task object carries the metadata needed for `x/data MsgAnchor` (consistent with D6's claims-engine + regen-signing rail). Anchoring tasks is parking-lot, but the schema doesn't preclude it.
3. **Disposition vocabulary** — the `accept / reject / redirect / supersede` action enum gives the disposition pass (§4 below) a stable shape.

## 4. Flow sketch: KOI-mediated submission with a disposition pass

The flow is parallel to `vault_ingest_extraction`'s Tier-1/1.1/1.5/2/3 entity-resolution ladder, repurposed for tasks. **Sovereign-tier (per-member personal-koi) → shared-tier (Regen AI production backend) is the only direction tasks cross consent boundaries; the reverse (shared → sovereign) is a poll-driven read-only surface.**

```
[Each member's local personal-koi running their meeting-processing pipeline]
[Sovereign tier: port 8351, regen-prod branch, member-private]
    │
    ├── Generates tasks with full provenance block
    │   (speech_act, source_rid, extractor, explicit, confidence)
    │
    ├── consent_tier defaults to 'private' (local vault only — never federated)
    │
    └── Member opts in: bumps consent_tier to 'team' for selected tasks
            │
            ▼
[POST /tasks/propose to Regen AI production backend]
[Shared tier: darren@202.61.196.119, stable branch, multi-member]
    │
    ├── Authenticated via existing ~/.koi-auth.json token (per 2026-04-21 auth work)
    ├── Each proposal carries the provenance block + a 'proposed_owner' field
    │   + the submitting member's URI (cryptographic provenance, not impersonatable)
    │
    └── Lands in 'proposals' inbox on the shared backend (separate table or status=proposed)
            │
            ▼
[Disposition pass — parallel to entity resolution, runs on shared tier]
    │
    ├── Tier 1 (exact): same taskKey already exists → mark proposal as 'duplicate'
    │
    ├── Tier 1.5 (contextual): different taskKey, but same proposed_owner +
    │   project + title-similarity > 0.85 → flag as 'likely-duplicate'
    │
    ├── Tier 2 (semantic): embedding similarity > 0.92 across proposals from
    │   different submitters in same time window → flag as 'convergent'
    │
    ├── Tier 3 (unique): no matches → 'novel proposal' surface
    │
    └── Outputs surface table:
        - Convergent: 5 members proposed it, accept by quorum
        - Likely-duplicate: 2 members proposed minor variants, manual merge
        - Novel: 1 member proposed it, route to proposed_owner for attestation
        - Duplicate: already exists, ignored
            │
            ▼
[Proposed_owner polls shared tier via simple CLI / MCP tool]
[Reads from Regen AI production backend; writes attestation back via PATCH]
    │
    └── accept / reject / redirect / redact → appends attestation record on shared tier
        │   (attestation is signed by owner's auth token; non-impersonatable)
        │
        ├── accept → status: open, consent_tier per owner's preference;
        │            owner's local personal-koi pulls the now-attested task into
        │            sovereign-tier vault on next poll cycle
        ├── reject → status: cancelled on shared tier, with reason in provenance
        ├── redirect → forks proposal to new owner, original archived on shared tier
        └── redact → status: redacted, content removed from shared tier; sovereign
                     tier untouched (the redaction is a federation-visibility signal)
```

The disposition pass produces three valuable surfaces:

1. **Convergent surface** — multiple members independently proposed the same task. Strong signal it's real; quorum auto-accept (with named threshold, e.g. ≥2 members for team-tier).
2. **Likely-duplicate surface** — entity resolution for tasks. Mirrors the existing `vault_ingest_extraction` Tier-1.5 contextual matching pattern. Manual merge or auto-merge with confidence threshold.
3. **Novel-proposal surface** — *"someone caught a task I missed."* The genuinely interesting surface. This is where collective sense-making delivers value beyond what any single node produces.

Implementation surface area is small: a new endpoint (`POST /tasks/propose`), a new table or status enum (`proposed`), a disposition cron or on-demand worker, and CLI/MCP tools for accept/reject/redirect. All of it composes with existing patterns; nothing new architecturally.

## 5. Consent disciplines: four sovereignty invariants applied inward

The charter response §2.2 names four sovereignty invariants for KOI federation (sovereign authority over own knowledge, opt-in sharing, self-sovereign identity, local-first operation) and four power-capture mechanisms to reject (reproductive-labour invisibilisation, protocol lock-in, gatekeeper-role accrual, data asymmetry). Both already cited in BKC's federation-overview docs.

This memo's contribution is to apply both *inward* — to teammate attentional commitments, not just outward to indigenous knowledge stewardship.

**Sovereignty invariants applied to teammate tasks:**

1. **Sovereign authority over own commitments.** No task assigned to a teammate becomes binding without their explicit attestation. The proposal layer is non-binding; the attestation is what binds.
2. **Opt-in sharing.** Tasks default to `consent_tier: private` (local vault only). The submitter must explicitly bump to `team` to enter the pool. The proposed_owner can decline ever entering pool-attestation flow at all.
3. **Self-sovereign identity.** Each member's submitter agent runs under their own credentials (existing `~/.koi-auth.json` token model). Provenance records who proposed what; no impersonation.
4. **Local-first operation.** The local pipeline works fully without ever submitting to the pool. Pool participation is additive.

**Power-capture mechanisms to reject (inward application):**

1. **Reproductive-labour invisibilisation** — the agent that does the inference is doing work. The provenance block makes that work visible (extractor agent ID, inference chain). Without provenance, the human reviewer thinks "the system noticed this" when in fact the agent did substantive work that should be attributable.
2. **Protocol lock-in** — pool submissions use HTTP + JSON over an authenticated endpoint, not a proprietary RPC. Schema is open. Members can self-host; team can change backends.
3. **Gatekeeper-role accrual** — the agent doing inference is a gatekeeper for whose attention gets named. If only one member's agent runs the pipeline, that member becomes the de facto convener of team attention. Mitigation: every member runs their own; the *convergent surface* is the cross-check.
4. **Data asymmetry** — raw transcripts are not pooled. Only proposed tasks (with provenance pointing to source RIDs) cross consent-tier boundaries. The transcript stays sovereign-per-node.

**CARE/OCAP/FPIC extended inward.** Borrowing the BKC consent envelope discipline (charter response §2.3): the pool must support a **redaction / non-anchor disposition** as first-class. A member who attests "actually, don't track this — sensitive HR conversation, off-the-record" must produce a binding redaction signal that propagates. The schema's `attestations` array supports this via a `redact` action; the disposition pass must honor redactions before any team-tier surfacing.

## 6. Tasks → specs → roadmap arc: the modal commons of attention

Will Ruddick's framing in *Beyond the Claims Engine* §1, threaded through "Attention turned into care; care into commitment; commitment into coordination; coordination into learning; learning back into wiser care," names what the meeting-task pipeline already half-does. Today's status-machine collapses all phases into `inbox / open / waiting / done / cancelled`. The lifecycle is real; the schema doesn't reflect it.

A second move worth naming: **tasks aren't isolated to-dos; they ladder into specs, which ladder into the spec-DAG, which roots in the roadmap.** The spec-DAG infrastructure already exists (`koi-processor/scripts/ingest_spec_dag.py`, with cross-project edges across spore / IC / PM / DW / SSD / FG / FC). Tasks that explicitly cite a SpecDoc RID in their provenance block participate in the roadmap arc; tasks that don't are floating commitments without strategic anchor.

Concrete: the 9 tasks created today all relate to *Regen OS* and *Conservation International proposal* — both candidates for SpecDoc registration if not already registered. Adding a `spec_rid` field to the provenance block (linking task to spec) makes the *commitment-pool of attention* legible: you can ask the system "show me all current commitments to Regen OS spec evolution" and get a coherent answer.

Kirsanow's frame from "Funding at the Frontier" §3 sharpens this further: investment lives at the **frontier** of the discourse graph. By analogy, attention should accrue at the frontier of the team's spec-DAG — the unverified-but-load-bearing edges, the bridges between sparse clusters. Tasks at the frontier are leverage tasks. Tasks at saturated areas are diminishing-returns tasks. The pool, with provenance pointing at SpecDoc RIDs, makes that distinction tractable.

This connects but doesn't conflate with funding-at-the-frontier mechanics. Internal-team attention allocation is a smaller game than public-goods funding; same shape, different scale. The arc is: explicit attentional commitments → composed into specs → composed into the spec-DAG → roots in the roadmap. The pool is the ledger of in-flight commitments at the bottom of that ladder.

## 7. v0 demo proposal: next Regen AI standup

The cheapest way to ground the architecture is a **shared-inbox demo**, no schema migration required. Runbook (target: 2026-05-12 standup if 05-05 is too tight):

1. **Pre-meeting (1–2 days before):** Each opted-in member runs their own local meeting-processing pipeline against the previous standup transcript (or a shared sample meeting) and tags 3–5 tasks they'd want to share. Output: `Tasks/*.md` files with manually-attached `pool_demo: true` flag.
2. **Submission:** Members manually post their candidate tasks (title + proposed owner + due date + speech-act class self-labeled) to a shared Slack channel or Notion page. (No backend extension required for demo.)
3. **Disposition pass — manual:** Darren or facilitator runs through the candidates by hand, classifying as: Convergent (multiple members proposed it), Likely-duplicate (variants), Novel (one member). Document the classification in the standup notes.
4. **Attestation:** Each proposed_owner reviews their proposed tasks; accepts, rejects, or redirects. Document attestations in the standup notes.
5. **Retro (post-meeting):** Compare per-member task lists pre-pool vs. post-pool. Surface insights:
   - How many tasks were convergent? (Sense-making convergence signal.)
   - How many were novel from a single member? (Catch-rate signal.)
   - How many got rejected? (Inference-error signal.)
   - Did anyone feel surveilled or over-assigned? (Consent-discipline signal.)

**Explicit consent gating:** participants opt in by the day before; non-participants are not assigned proposed tasks; transcripts stay local; only candidate tasks are pooled. A teammate who declines the demo entirely is not analyzed.

If retro is positive: write up the demo as evidence supporting the v1 architecture, with retro insights folded into Charter v2 inputs.

If retro is mixed: surface the friction points, refine the consent disciplines, decide whether to iterate or pause.

## 8. Composition with Charter D1 / D5 / D6

This memo extends three existing decisions; it does not introduce a new one.

**D1 (compilation agent locus).** Charter response §2.1 ratifies the operational hybrid (vault Markdown + structured Postgres, vault is human-editable projection, backend is operational source of truth). Tasks are already an example of that hybrid (`Tasks/*.md` + `task_registry`). This memo extends the hybrid by adding *task creation events* — proposals with provenance — as a structured first-class object, with the vault projection deliberately lagging until owner attestation. The hybrid pattern composes: vault sees only attested tasks; backend sees the full proposal-and-disposition history.

**D5 (KOI federation).** Charter response §2.2 confirms KOI as consent-mediated sovereignty rail. This memo extends the federation surface from knowledge objects (RIDs) and governance objects (SpecDoc RIDs) to *commitment objects* (Task RIDs with provenance). The consent_tier discipline applies symmetrically. The "governance-object federation via SpecDoc RIDs is supported but unproven at multi-org scale" caveat applies to commitment-objects too; v0 demo is intra-team only. The sovereign-tier-to-shared-tier flow (per-member personal-koi → Regen AI production backend) is exactly the "consent-mediated sovereignty, not RPC" pattern named in the charter response §2.2; the architecture explicitly avoids using any one member's personal-koi as the team backend (gatekeeper-role-accrual rejection).

**D6 (claims-engine + provenance).** Charter response §2.3 confirms claims-engine + regen-signing as canonical pair, with consent envelope in payload, vocabulary-version contract, and standards-alignment posture (C2PA + W3C VC). This memo extends the claims-engine vocabulary by mapping the witness/attestation/promise triad onto task lifecycle. The provenance schema is anchorable; the disposition vocabulary is a candidate addition to the v1 claims-engine API contract.

Three non-collisions worth naming:
- **Does not adjudicate meta-canon** (per charter response §2.3 caution). Tasks are not ADRs; the disposition pass does not retroactively re-adjudicate decisions, only resolves task-level proposals.
- **Does not require D8 adoption-matrix re-classification.** The architecture composes existing patterns (entity resolution, KOI federation, claims-engine vocab); no new external upstream dependencies introduced.
- **Does not pre-empt D7 hackathon co-design.** The pool is intra-team; cross-team or cross-org task federation is parking-lot. (Parachute Vault adoption decision unaffected.)

## 9. Open questions and where I defer

**Open — operational:**
- Should the disposition pass run as a cron (every 6 hours, end-of-day) or on-demand (member triggers via `/tasks/dispose` MCP call)? Tradeoff: cron surfaces convergence faster; on-demand respects local-first cleanly. Probably both with sensible defaults.
- What's the convergence threshold? ≥2 members for team-tier auto-accept seems right for a 5–6 person team but is unprincipled for larger groups. Defer to BKC's federation discipline for principled threshold rules.
- How do consent_tier transitions work? Once a task is `team`-tier, can the original submitter unilaterally bump to `private` (effectively withdrawing the proposal)? Probably yes pre-attestation, no post-attestation (binding withdrawn unilaterally is consent violation in the other direction).

**Open — architectural:**
- Should `inference_chain` in the provenance block be human-readable narrative (current sketch) or structured (e.g., a sequence of typed inference steps)? Charter response §2.4 vocabulary-version contract argues for structured; ergonomics argue for narrative. Hybrid: structured with a `narrative` field.
- Does the claims-engine API contract need to know about tasks at all, or is the task pool a separate-but-aligned vocabulary? Soft preference for separate (don't overload claims-engine), but with explicit alignment in disposition action enum.

**Defer to Greg / decision session:**
- Whether this memo gets folded into Charter v2 directly, runs as a parallel v1.x extension, or stays as a koi-repo response artifact with no formal charter status.
- Anchor cadence for this memo (v1 proposal-state vs v2 post-team-feedback). Default v2 unless Greg's ledger discipline argues for v1.
- Whether the v0 demo runs at 2026-05-05 or 2026-05-12. 05-12 is realistic; 05-05 too tight for opt-in collection.

## References

- Beyond the Claims Engine — Toward the Stewardship of Commitments (Darren Zal, 2026-04-23): `~/projects/RegenAI/docs/essays/beyond-claims-engine.md`. Speech-act triad in §1; three temporal-register questions in §2; commons-of-care framing throughout.
- Funding at the Frontier — Graph-native economics for the stewardship of commitments (Darren Zal, 2026-04-23): `~/projects/RegenAI/docs/essays/funding-at-the-frontier.md`. Kirsanow frontier-of-graph in §3.
- RegenOS Architecture Decisions Charter v1.0 — Open Questions (Gregory Landua, 2026-04-25, last edited 2026-04-28): `https://www.notion.so/regennetwork/RegenOS-Architecture-Decisions-Charter-v1-0-Open-Questions-34f25b77eda18131b5f8eacae84f2024`. Eight architecture questions D1–D8; convergence framing in §2.
- Response to RegenOS Architecture Decisions Charter v1.0 (Darren Zal, 2026-04-30): `~/projects/RegenAI/koi-processor/docs/claims/regenos-charter-response-darren.md` (the promoted permanent location; was at `/tmp/` during drafting and promoted as Implementation Step 3 of the plan that produced this memo). Sovereignty invariants in §2.2; consent envelope in §2.3; standards-alignment in §2.3.
- Forum: Agentic Organization Design and Collaboration (Gregory Landua, ongoing): `https://forum.regen.network/t/agentic-organization-design-and-collaboration/610`. Three-stack convergence framing; Knowledge Commoning Swarm pitch.
- Will Ruddick, *Journal of a Grassroots Economist* (Grassroots Economics, ongoing): commitment-pools as living registries of care.
- Will Ruddick, *Commitment-pool route graphs for finance and mutual aid* (SSRN, 2026-04): formal commitment-tuple primitive.
- BioregionKnowledgeCommons / bioregional-coordination — `docs/foundations/rights-licensing-consent-policy-slots.md`, `data-classification-matrix-v0.1.md`, `federation-overview.md`. Consent-tier discipline; CARE/OCAP/FPIC framework.
- `koi-processor/migrations/056_task_registry.sql` (existing): task_registry schema.
- `koi-processor/api/routers/task_router.py` (existing): task ingest / patch / stats endpoints.
- `~/projects/darren-workflow/skills/meeting-tasks/SKILL.md` (existing): meeting-tasks skill spec.
- `koi-processor/scripts/ingest_spec_dag.py` (existing): SpecDoc RID federation pattern.
- C2PA Content Credentials: `https://spec.c2pa.org` / NSA-CISA Jan 2025 guidance.
- W3C Verifiable Credentials 2.0: `https://www.w3.org/TR/vc-data-model-2.0/`.

---

