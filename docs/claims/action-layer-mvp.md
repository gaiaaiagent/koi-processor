# Claims Engine — Action Layer MVP

**Status:** Draft for review (Apr 15, 2026)
**Context:** Marie's Apr 2 framing — the current app is closer to a *viewer* than an *engine*. The viewer shows who claimed what and whether it was verified. The engine part is what *happens* as a consequence of a verified claim. This doc proposes the minimal trigger to build first.

---

## The Gap

The Claims Engine V2 today ends at `ledger_anchored`:

```
self_reported → peer_reviewed → verified → ledger_anchored
```

`ledger_anchored` proves that a claim was made and attested. But it doesn't *do* anything downstream. A human still has to take the verified claim and manually decide: issue credits, retire on behalf of a donor, notify a funder, or unlock a tranche. That manual step is the gap the action layer closes.

---

## Proposed First Trigger

**Trigger:** Claim reaches `verified` state (i.e., passes peer review with at least one approved attestation)
**Output:** An `ActionProposal` — a structured suggestion for what to do next, requiring explicit human approval before anything is broadcast

```
claim.state = 'verified'
        │
        ▼
 ActionProposal created
 (type + parameters + rationale)
        │
        ▼
 Human reviews in UI / via API
        │
     approve?
    ┌───┴───┐
   YES      NO
    │        │
    ▼        ▼
broadcast  dismiss
(on-chain  (logged,
or notify) no action)
```

The human approval gate is non-negotiable for Phase 1. The value of the action layer isn't automation — it's structured suggestion. The system proposes; a human decides.

---

## Data Model

### `claim_action_proposals` table

```sql
CREATE TABLE claim_action_proposals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_rid       TEXT NOT NULL REFERENCES claims(rid),
    action_type     TEXT NOT NULL,   -- see action types below
    parameters      JSONB NOT NULL,  -- action-specific payload
    rationale       TEXT,            -- why this action is suggested
    proposed_by     TEXT,            -- 'system' or reviewer_uri
    proposed_at     TIMESTAMPTZ DEFAULT NOW(),
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | dismissed | broadcast
    reviewed_by     TEXT,            -- reviewer_uri
    reviewed_at     TIMESTAMPTZ,
    broadcast_tx    TEXT,            -- ledger tx hash if broadcast
    broadcast_at    TIMESTAMPTZ
);
```

### Action Types (Phase 1)

| `action_type` | What it proposes | Parameters |
|---|---|---|
| `draft_credit_batch` | Submit a credit batch to Regen Registry | `class_id`, `project_id`, `amount`, `unit`, `start_date`, `end_date` |
| `retire_credits` | Retire existing credits on behalf of a beneficiary | `batch_denom`, `amount`, `beneficiary`, `reason` |
| `notify_stakeholder` | Send a structured notification to a downstream party | `recipient_uri`, `message_template`, `context` |

Phase 1 builds `retire_credits` first — it's the SeaTrees use case and already partially scaffolded via the `seatrees_bloom_export.py` path.

---

## API (Phase 1)

### List action proposals for a claim
```
GET /claims/{rid}/actions
→ [ActionProposal, ...]
```

### Propose an action (system or human-initiated)
```
POST /claims/{rid}/actions
Body: { action_type, parameters, rationale }
→ ActionProposal
```

### Approve / dismiss
```
POST /claims/{rid}/actions/{action_id}/approve
POST /claims/{rid}/actions/{action_id}/dismiss
Body: { reviewer_uri, note }
→ ActionProposal (status updated)
```

### Broadcast (only callable after approve)
```
POST /claims/{rid}/actions/{action_id}/broadcast
→ { tx_hash, broadcast_at }
```

---

## Integration Points

- **Trigger**: `claim_state_log` webhook or DB trigger on `state = 'verified'` → auto-generate a `draft_credit_batch` or `retire_credits` proposal if claim has the right metadata (quantity, unit, project_id)
- **Regen Ledger**: `retire_credits` broadcast uses same `regen tx ecocredit retire` path as `seatrees_bloom_export.py`
- **Claims viewer**: Add an "Actions" tab per claim. Pending proposals show a banner: "1 action awaiting approval"

---

## What This Is NOT

- Not autonomous execution. Human approves before anything is broadcast.
- Not full RBAM integration. That's Phase 3 (org-level delegation, DAO DAO). This uses direct Keplr signing.
- Not a generic workflow engine. One trigger type (`verified` → proposal), one approval model (human), Phase 1.

---

## Success Criteria

1. A verified claim auto-generates a `retire_credits` proposal for the right claim types
2. A human can approve via API (UI in Phase 2)
3. Approval broadcasts a `MsgRetire` and writes back the tx hash to the action proposal
4. The proof pack for the claim includes the downstream action as a linked record

---

## Next Step

Team picks one action type to build first at the Apr 15 meeting. Recommended: `retire_credits` (SeaTrees already has the broadcast path; adds most immediate demo value).
